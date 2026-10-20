"""知识库闭环：疑难进程证据 → review 待办包 → 补丁校验/入库。

设计边界（硬约束，写进实现）：
  * 模型/人只能改「这个进程是干嘛的」（role + note）；
  * **不能**改 policy / protect / 动作 / 默认勾选；
  * `apply` 永远不是自动的：必须显式调用（CLI/UI），默认先 dry-run；
  * 入库前备份，入库后由调用方写审计。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from typing import Any, Iterable

from .models import ProcRole, ProcessInfo

_VALID_ROLES = {role.value for role in ProcRole}
_FORBIDDEN_KEYS = {
    "action", "actions", "policy", "protect", "default_checked", "risk",
    "kill", "stop_service", "check", "selected", "enabled", "path", "exe",
}
_MAX_PATCH_ENTRIES = 500
_MAX_NOTE = 160
_NAME_RE = re.compile(r"^[^\\/:*?\"<>|\r\n]{1,100}$")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _key(entry: dict[str, Any]) -> tuple[str, str]:
    return (str(entry.get("name") or "").lower(),
            str(entry.get("exe") or "").lower())


def version_of(kb: dict[str, Any]) -> str:
    """知识库版本：内容哈希（与条数、顺序无关）。"""
    try:
        payload = json.dumps({str(k).lower(): list(v) if isinstance(v, (list, tuple)) else v
                              for k, v in kb.items()},
                             ensure_ascii=False, sort_keys=True, default=str)
    except Exception:                                           # noqa: BLE001
        payload = str(len(kb))
    return "kb-" + hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:12]


def is_suspect(proc: ProcessInfo) -> bool:
    """疑难件：未知角色、低置信度、非微软。"""
    cls = proc.classification
    if cls.microsoft:
        return False
    return cls.role is ProcRole.UNKNOWN or cls.confidence < 0.6


def evidence_of(proc: ProcessInfo, software_name: str | None,
                service_names: Iterable[str] = ()) -> dict[str, Any]:
    cls = proc.classification
    return {
        "ts": _now_iso(),
        "name": proc.name,
        "exe": proc.exe,
        "company": proc.file_company,
        "product": proc.file_product,
        "description": proc.file_description,
        "version": proc.file_version,
        "cmdline": proc.cmdline,
        "parent": proc.parent_name,
        "ppid": proc.ppid,
        "session": proc.session_id,
        "windows": list(proc.windows or []),
        "signed": proc.signed,
        "signer": proc.signer,
        "software": software_name,
        "services": sorted(set(service_names)),
        "current_role": cls.role.value,
        "confidence": round(float(cls.confidence), 2),
        "reasons": list(cls.reasons),
        "hit_count": 1,
        "first_seen": _now_iso(),
        "last_seen": _now_iso(),
    }


class PendingStore:
    """`<data_dir>/kb/pending.jsonl`：按 (name, exe) 去重、累计 hit_count、封顶。"""

    def __init__(self, path: str, *, max_entries: int = 500) -> None:
        self.path = os.path.abspath(path)
        self.max_entries = max(10, int(max_entries))
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    # ------------------------------------------------------------------ #
    def load(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        out: list[dict[str, Any]] = []
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:                           # noqa: BLE001
                        continue
                    if isinstance(row, dict) and row.get("name"):
                        out.append(row)
        except Exception:                                       # noqa: BLE001
            return []
        out.sort(key=lambda r: (-int(r.get("hit_count") or 0),
                                str(r.get("last_seen") or "")))
        return out[: self.max_entries]

    def stats(self) -> dict[str, Any]:
        rows = self.load()
        return {"count": len(rows), "path": self.path,
                "top": [{"name": r.get("name"), "role": r.get("current_role"),
                         "hit_count": r.get("hit_count")} for r in rows[:10]]}

    def record_many(self, entries: Iterable[dict[str, Any]]) -> dict[str, int]:
        """合并写入；返回 {added, updated, total}。"""
        rows = {_key(r): r for r in self.load()}
        added = updated = 0
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            key = _key(entry)
            old = rows.get(key)
            if old is None:
                entry = dict(entry)
                entry["hit_count"] = int(entry.get("hit_count") or 1)
                entry.setdefault("first_seen", entry.get("ts") or _now_iso())
                entry["last_seen"] = _now_iso()
                rows[key] = entry
                added += 1
            else:
                old["hit_count"] = int(old.get("hit_count") or 1) + 1
                old["last_seen"] = _now_iso()
                # 证据以最新一次为准，但保留首次时间
                for field in ("cmdline", "windows", "services", "current_role",
                              "confidence", "reasons", "parent", "ppid", "session",
                              "company", "product", "description", "signed", "signer"):
                    if field in entry:
                        old[field] = entry[field]
                updated += 1
        # 封顶：hit_count 高的优先，其次最近出现
        ordered = sorted(rows.values(),
                         key=lambda r: (-int(r.get("hit_count") or 0),
                                        str(r.get("last_seen") or "")),
                         reverse=False)[: self.max_entries]
        self._write(ordered)
        return {"added": added, "updated": updated, "total": len(ordered)}

    def clear(self) -> None:
        self._write([])

    def _write(self, rows: list[dict[str, Any]]) -> None:
        tmp = self.path + ".writing"
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)


def build_review(data_dir: str, kb_path: str, *, pending_path: str | None = None,
                 stamp: str | None = None) -> dict[str, Any]:
    """把 pending 证据写成一份可交给 agent/人的待办包。"""
    pending = pending_path or os.path.join(data_dir, "kb", "pending.jsonl")
    store = PendingStore(pending)
    rows = store.load()
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(data_dir, "kb", f"review-{stamp}")
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "pending.json")
    report_path = os.path.join(out_dir, "report.md")
    with open(json_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"generated_at": _now_iso(), "kb_path": kb_path,
                   "count": len(rows), "items": rows}, handle, ensure_ascii=False, indent=2)

    lines = [
        "# 知识库待办包（AppGuard）",
        "",
        f"> 生成时间：{_now_iso()}　|　条目：{len(rows)}",
        f"> 知识库：`{kb_path}`",
        "",
        "## 交给 agent 的任务",
        "",
        "只回答每个进程 **是干什么的**（`role` + 一句 ≤160 字中文说明），"
        "不要改策略、不要决定关谁。输出 JSON 补丁后用 `guardctl kb apply` 入库。",
        "",
        "```json",
        '{"进程名.exe": {"role": "updater", "note": "某软件的自动更新组件"}}',
        "```",
        "",
        f"合法 role：{', '.join(sorted(_VALID_ROLES))}",
        "",
        "## 待办",
        "",
        "| 进程 | 当前判定 | 置信度 | hit | 厂商/产品 | 命令行 | 父进程 | 服务 | 窗口 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        def esc(value: Any) -> str:
            text = str(value if value is not None else "-")
            return text.replace("|", "\\|")[:120]
        lines.append("| {name} | {role} | {conf} | {hit} | {vendor} | {cmd} | {parent} | "
                     "{svc} | {win} |".format(
                         name=esc(row.get("name")), role=esc(row.get("current_role")),
                         conf=esc(row.get("confidence")), hit=esc(row.get("hit_count")),
                         vendor=esc(" / ".join(str(x) for x in (
                             row.get("company"), row.get("product")) if x)),
                         cmd=esc(row.get("cmdline")), parent=esc(row.get("parent")),
                         svc=esc(", ".join(row.get("services") or [])),
                         win=esc("; ".join(row.get("windows") or []))))
    lines += ["", "## 原始证据", "", f"`{json_path}`", ""]
    with open(report_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
    return {"dir": out_dir, "report": report_path, "pending": json_path,
            "count": len(rows)}


def load_patch(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("补丁必须是 JSON 对象：{进程名: {role, note}}")
    return data


def validate_patch(patch: dict[str, Any]) -> tuple[dict[str, dict[str, str]], list[str]]:
    """严格校验补丁；返回 (规范化条目, 错误列表)。任何错误都拒绝整包。"""
    errors: list[str] = []
    if not isinstance(patch, dict):
        return {}, ["补丁必须是 JSON 对象"]
    entries: dict[str, dict[str, str]] = {}
    count = 0
    for raw_name, raw_value in patch.items():
        name = str(raw_name).strip()
        if name.startswith("_"):
            continue                                   # 允许 _meta 之类的元数据
        count += 1
        if not name or not _NAME_RE.match(name):
            errors.append(f"非法进程名：{raw_name!r}（不允许路径/通配符/分隔符）")
            continue
        if name.lower() in _FORBIDDEN_KEYS:
            errors.append(f"非法进程名：{name!r}")
            continue
        role: Any = None
        note: Any = None
        if isinstance(raw_value, dict):
            bad = [k for k in raw_value if str(k).lower() not in ("role", "note")]
            if bad:
                errors.append(f"{name}: 补丁只允许 role/note，发现 {bad}")
                continue
            role, note = raw_value.get("role"), raw_value.get("note")
        elif isinstance(raw_value, (list, tuple)) and len(raw_value) >= 2:
            role, note = raw_value[0], raw_value[1]
        else:
            errors.append(f"{name}: 值必须是 {{role, note}} 或 [role, note]")
            continue
        role = str(role or "").strip().lower()
        if role not in _VALID_ROLES:
            errors.append(f"{name}: 非法 role {role!r}（可用：{sorted(_VALID_ROLES)}）")
            continue
        note = str(note or "").strip()
        if not note:
            errors.append(f"{name}: note 不能为空")
            continue
        if len(note) > _MAX_NOTE:
            errors.append(f"{name}: note 超过 {_MAX_NOTE} 字")
            continue
        entries[name.lower()] = {"role": role, "note": note}
    if count > _MAX_PATCH_ENTRIES:
        errors.append(f"补丁条目 {count} 超过上限 {_MAX_PATCH_ENTRIES}")
    return entries, errors


def apply_patch(kb_path: str, patch: dict[str, Any], *, dry_run: bool = True,
                backup: bool = True) -> dict[str, Any]:
    """校验并合并补丁。返回 diff；``dry_run=False`` 才写盘（写前备份）。"""
    entries, errors = validate_patch(patch)
    if errors:
        raise ValueError("补丁校验失败：" + "；".join(errors[:12]))
    existing: dict[str, Any] = {}
    if os.path.exists(kb_path):
        with open(kb_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"现有知识库不是 JSON 对象：{kb_path}")
        existing = data
    added, updated, unchanged = [], [], []
    merged: dict[str, Any] = {}
    for name, value in existing.items():
        role, note = _normalize_value(value)
        merged[str(name)] = {"role": role, "note": note}
    for name, value in entries.items():
        old = merged.get(name)
        if old is None:
            added.append(name)
        elif old.get("role") != value["role"] or old.get("note") != value["note"]:
            updated.append(name)
        else:
            unchanged.append(name)
        merged[name] = value
    diff = {"added": sorted(added), "updated": sorted(updated),
            "unchanged": sorted(unchanged), "total": len(merged)}
    if dry_run:
        return {"dry_run": True, "applied": False, "path": kb_path, "diff": diff}
    os.makedirs(os.path.dirname(os.path.abspath(kb_path)), exist_ok=True)
    backup_path = None
    if backup and os.path.exists(kb_path):
        backup_path = f"{kb_path}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(kb_path, backup_path)
    tmp = kb_path + ".writing"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(merged, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, kb_path)
    return {"dry_run": False, "applied": True, "path": kb_path,
            "backup": backup_path, "diff": diff}


def _normalize_value(value: Any) -> tuple[str, str]:
    if isinstance(value, dict):
        return str(value.get("role") or "unknown"), str(value.get("note") or "")
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return str(value[0]), str(value[1])
    return "unknown", ""


def rollback(kb_path: str, backup_path: str) -> dict[str, Any]:
    """从备份恢复知识库（供误 apply 时使用）。"""
    if not os.path.exists(backup_path):
        raise FileNotFoundError(backup_path)
    shutil.copy2(backup_path, kb_path)
    return {"restored": True, "path": kb_path, "from": backup_path}
