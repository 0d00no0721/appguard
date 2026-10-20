"""审计日志：JSONL 追加写 + 单调序号 + 轮转 + 查询。

字段与《接口规范.md》§3 的 `AuditRow` 一致；`seq` 在守护进程生命周期内单调递增，
IPC 事件也复用同一个序号空间（`audit_seq_from/to` 用于把一次清理关联到日志区间）。
"""

from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import AuditRow


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


class Auditor:
    """线程安全的审计写入器。"""

    def __init__(self, path: str, *, max_mb: float = 32.0) -> None:
        self.path = os.path.abspath(path)
        self.max_bytes = int(max_mb * 1024 * 1024)
        self._lock = threading.RLock()
        self._seq = 0
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._seq = self._scan_last_seq()

    # ---------------------------------------------------------------- #
    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _scan_last_seq(self) -> int:
        """启动时恢复序号：**只读文件尾部 64 KB**。

        ⚠️ 早期实现注释写着"只读尾部"，代码却遍历了整个文件 —— 32 MB 上限下启动要几秒。
        这里改成真的扫尾部；并在尾部没有完整行时向前多读一块。
        """
        if not os.path.exists(self.path):
            return 0
        try:
            size = os.path.getsize(self.path)
            block = min(size, 64 * 1024)
            with open(self.path, "rb") as handle:
                handle.seek(size - block)
                chunk = handle.read(block).decode("utf-8", "replace")
            lines = [ln for ln in chunk.splitlines() if ln.strip()]
            if size > block and lines:
                lines = lines[1:]                   # 首行多半被截断，丢掉
            last = 0
            for line in reversed(lines):
                try:
                    last = int(json.loads(line).get("seq") or 0)
                    break
                except Exception:                   # noqa: BLE001
                    continue
            return last
        except Exception:                           # noqa: BLE001
            return 0

    # ---------------------------------------------------------------- #
    def write(self, event: str, *, pid: int | None = None, name: str | None = None,
              software_id: str | None = None, role: str | None = None,
              origin: str | None = None, rule: str | None = None,
              action: str | None = None, result: str | None = None,
              dry_run: bool = False, **detail: Any) -> AuditRow:
        row = AuditRow(seq=self.next_seq(), ts=_now_iso(), event=event, pid=pid, name=name,
                       software_id=software_id, role=role, origin=origin, rule=rule,
                       action=action, result=result, dry_run=dry_run, detail=detail or {})
        line = json.dumps(row.to_dict(), ensure_ascii=False)
        with self._lock:
            self._rotate_if_needed()
            with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
        return row

    def _rotate_if_needed(self) -> None:
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > self.max_bytes:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                os.replace(self.path, f"{self.path}.{stamp}")
        except Exception:
            pass

    # ---------------------------------------------------------------- #
    def read(self, *, since: str | None = None, until: str | None = None,
             software_id: str | None = None, event: str | None = None,
             limit: int = 200, offset: int = 0) -> list[AuditRow]:
        rows: list[AuditRow] = []
        if not os.path.exists(self.path):
            return rows
        with open(self.path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except Exception:
                    continue
                if since and str(raw.get("ts") or "") < since:
                    continue
                if until and str(raw.get("ts") or "") > until:
                    continue
                if software_id and raw.get("software_id") != software_id:
                    continue
                if event and raw.get("event") != event:
                    continue
                rows.append(_row_from(raw))
        rows.sort(key=lambda r: r.seq)
        return rows[offset: offset + limit] if limit else rows[offset:]

    def tail(self, n: int = 100) -> list[AuditRow]:
        if n <= 0 or not os.path.exists(self.path):
            return []
        lines: list[str] = []
        try:
            with open(self.path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                block = min(handle.tell(), max(4096, n * 512))
                handle.seek(-block, os.SEEK_END)
                lines = handle.read().decode("utf-8", "replace").splitlines()
        except Exception:
            return []
        rows: list[AuditRow] = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(_row_from(json.loads(line)))
            except Exception:
                continue
        return rows

    def export(self, path: str, *, fmt: str = "jsonl",
               rows: Iterable[AuditRow] | None = None) -> int:
        data = list(rows) if rows is not None else self.read(limit=0)
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        if fmt == "csv":
            fields = ["seq", "ts", "event", "pid", "name", "software_id", "role", "origin",
                      "rule", "action", "result", "dry_run"]
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields + ["detail"])
                writer.writeheader()
                for row in data:
                    item = row.to_dict()
                    item["detail"] = json.dumps(item.get("detail") or {}, ensure_ascii=False)
                    writer.writerow({k: item.get(k) for k in fields + ["detail"]})
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                for row in data:
                    handle.write(json.dumps(row.to_dict(), ensure_ascii=False) + "\n")
        return len(data)


def _row_from(raw: dict[str, Any]) -> AuditRow:
    return AuditRow(
        seq=int(raw.get("seq") or 0), ts=str(raw.get("ts") or ""), event=str(raw.get("event") or ""),
        pid=raw.get("pid"), name=raw.get("name"), software_id=raw.get("software_id"),
        role=raw.get("role"), origin=raw.get("origin"), rule=raw.get("rule"),
        action=raw.get("action"), result=raw.get("result"), dry_run=bool(raw.get("dry_run")),
        detail=raw.get("detail") or {})
