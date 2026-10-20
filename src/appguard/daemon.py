"""守护进程装配：采集线程 + 方法分发 + 事件广播 + 单写者队列。

对外只暴露《接口规范.md》§4 的方法；UI 不做任何特权操作，一切经这里。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from . import cleaner, evaluator, inventory, kb as kbmod
from .audit import Auditor
from .classify import DEFAULT_KB, ROLE_ZH, load_kb
from .config import Config
from .ipc import (API_VERSION, E_API_VERSION, E_BAD_REQUEST, E_BUSY, E_NOT_FOUND,
                  E_PERMISSION, E_PLAN_EXPIRED, E_PLAN_STALE, Dispatcher, EventBus,
                  IpcError, PIPE_NAME, PipeServer)
from .models import Health, RiskLevel

SERVER_VERSION = "guardd/0.3.0"

_VALID_ROLES = {"main", "launcher", "updater", "telemetry", "crash", "ad", "tray", "helper",
                "daemon", "service", "installer", "system", "unknown"}
_POLICY_INT_RANGES = {"min_idle_s": (5, 86400)}
_PLAN_KEEP = 32                      # 计划表上限（防止长时间运行内存增长）


def _coerce_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise IpcError("E_BAD_REQUEST", f"{key} 需要布尔值，收到 {value!r}")


def _coerce_int(value: Any, key: str, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise IpcError("E_BAD_REQUEST", f"{key} 需要整数，收到布尔值 {value!r}")
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value.strip())
    if not isinstance(value, int):
        raise IpcError("E_BAD_REQUEST", f"{key} 需要整数，收到 {value!r}")
    if not (low <= value <= high):
        raise IpcError("E_BAD_REQUEST", f"{key} 需要在 {low}~{high} 之间，收到 {value}")
    return value


def _coerce_patterns(value: Any, key: str, *, allowed: set[str] | None,
                     require_prefix: bool) -> list[str]:
    if isinstance(value, str):
        raise IpcError("E_BAD_REQUEST", f"{key} 需要字符串数组，收到字符串 {value!r}")
    if not isinstance(value, (list, tuple)):
        raise IpcError("E_BAD_REQUEST", f"{key} 需要字符串数组，收到 {type(value).__name__}")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise IpcError("E_BAD_REQUEST", f"{key} 的元素必须是非空字符串，收到 {item!r}")
        item = item.strip()
        if require_prefix and not item.startswith(("pid:", "sw:", "exe:")):
            raise IpcError("E_BAD_REQUEST", f"{key} 的元素需要 pid:/sw:/exe: 前缀：{item!r}")
        if allowed is not None and item not in allowed:
            raise IpcError("E_BAD_REQUEST",
                           f"{key} 含未知取值 {item!r}，可用：{sorted(allowed)}")
        out.append(item)
    return out


def _int_param(params: dict[str, Any], key: str, default: int, *,
               minimum: int = 0, maximum: int | None = None) -> int:
    """分页/条数参数：缺省取默认值；显式 0 保留 0（=不限量）；负数/非整数报错。"""
    value = params.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        raise IpcError(E_BAD_REQUEST, f"{key} 需要整数，收到布尔值")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise IpcError(E_BAD_REQUEST, f"{key} 需要整数，收到 {value!r}") from None
    if number < minimum:
        raise IpcError(E_BAD_REQUEST, f"{key} 不能小于 {minimum}，收到 {number}")
    if maximum is not None and number > maximum:
        raise IpcError(E_BAD_REQUEST, f"{key} 不能大于 {maximum}，收到 {number}")
    return number


class Daemon:
    def __init__(self, cfg: Config, *, pipe_name: str = PIPE_NAME) -> None:
        self.cfg = cfg
        self.pipe_name = pipe_name
        self.auditor = Auditor(cfg.audit_path)
        self.bus = EventBus(lambda: self.auditor.next_seq())
        self.dispatcher = Dispatcher()
        self.dispatcher.features = ["snapshot", "classify", "cleanup", "select", "policy",
                                    "audit", "subscribe", "export", "kb"]
        self._lock = threading.RLock()          # 单写者：plan/execute/policy 串行
        self._snapshot: inventory.Snapshot | None = None
        self._snapshot_at = 0.0
        self._plans: dict[str, Any] = {}
        self._stop = threading.Event()
        self._stopped = False
        self._started_at = time.time()
        self._collector: threading.Thread | None = None
        self._server: PipeServer | None = None
        self._tcp: Any = None
        self._transport: str | None = None
        self._collect_error: str | None = None
        self._collect_error_logged: str | None = None
        self._state_error: str | None = None
        self._state = self._load_state()
        self._policy_restore_note: dict[str, Any] | None = None
        self._restore_policy()
        # 外部知识库（`<data_dir>/kb/known_processes.json`）此前只写在文档里、从未被加载。
        self._kb_warnings: list[str] = []
        self._kb = load_kb(cfg.kb_path, self._kb_warnings)
        for warning in self._kb_warnings:
            self.auditor.write("kb.load", action="load",
                               result="ok" if "已合并" in warning else "warn", message=warning)
        self._pending = kbmod.PendingStore(
            os.path.join(cfg.data_dir, "kb", "pending.jsonl"),
            max_entries=cfg.kb.max_pending)
        self._pending_at = 0.0
        self._register()

    def _restore_policy(self) -> None:
        """从 state.json 恢复上次 `policy.set` 的结果。

        ⚠️ 早期版本只写不读 ⇒ `policy.set dry_run=false` 重启后**静默丢失**。
        恢复时逐个字段走同一套类型校验，非法值丢弃并留痕（不让脏 state 把守护带崩）。
        """
        raw = (self._state or {}).get("policy")
        if not isinstance(raw, dict):
            return
        applied: list[str] = []
        dropped: list[str] = []
        for key, value in raw.items():
            try:
                if key in ("dry_run", "allow_cleanup_while_idle"):
                    setattr(self.cfg.policy, key, _coerce_bool(value, key))
                elif key in _POLICY_INT_RANGES:
                    low, high = _POLICY_INT_RANGES[key]
                    setattr(self.cfg.policy, key, _coerce_int(value, key, low, high))
                elif key == "never_touch_roles":
                    roles = _coerce_patterns(value, key, allowed=_VALID_ROLES,
                                             require_prefix=False)
                    if "service" in roles:
                        roles = [r for r in roles if r != "service"]
                        dropped.append("never_touch_roles: service 已迁移"
                                       "（微软/关键进程规则接管）")
                    self.cfg.policy.never_touch_roles = roles
                elif key in ("protect", "allow"):
                    setattr(self.cfg.policy, key,
                            _coerce_patterns(value, key, allowed=None, require_prefix=True))
                else:
                    continue
                applied.append(key)
            except IpcError as exc:
                dropped.append(f"{key}: {exc.message}")
        if applied or dropped:
            self._policy_restore_note = {"applied": sorted(applied), "dropped": dropped}
            self.auditor.write("policy.restored", action="restore",
                               result="ok" if not dropped else "partial",
                               applied=sorted(applied), dropped=dropped)

    # ------------------------------------------------------------------ #
    # 知识库
    # ------------------------------------------------------------------ #
    def _kb_version(self) -> str:
        return kbmod.version_of(self._kb)

    def _reload_kb(self) -> dict[str, Any]:
        warnings: list[str] = []
        self._kb = load_kb(self.cfg.kb_path, warnings)
        self._kb_warnings = warnings
        payload = {"kb_size": len(self._kb), "kb_version": self._kb_version(),
                   "warnings": warnings, "kb_path": self.cfg.kb_path}
        self.auditor.write("kb.reloaded", action="reload", result="ok",
                           kb_size=payload["kb_size"], kb_version=payload["kb_version"],
                           warnings=warnings)
        return payload

    def _maybe_record_pending(self, snapshot: inventory.Snapshot) -> None:
        """把未识别/低置信度进程写成知识库待办证据（节流，不刷屏）。"""
        if not self.cfg.kb.pending_enabled:
            return
        now = time.time()
        if now - self._pending_at < max(10, self.cfg.kb.review_interval_s):
            return
        self._pending_at = now
        name_by_id = {s.id: s.name for s in snapshot.software}
        entries = []
        for proc in snapshot.processes:
            if not kbmod.is_suspect(proc):
                continue
            cls = proc.classification
            names = (inventory.service_names_for(proc, snapshot.services)
                     if cls.role.value == "service" else [])
            entries.append(kbmod.evidence_of(
                proc, name_by_id.get(cls.software_id or ""), names))
        if not entries:
            return
        stats = self._pending.record_many(entries)
        if stats["added"] or stats["updated"]:
            self.auditor.write("kb.pending", action="record", result="ok", **stats)

    # ------------------------------------------------------------------ #
    # 状态与降级
    # ------------------------------------------------------------------ #
    def _load_state(self) -> dict[str, Any]:
        try:
            with open(self.cfg.state_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def _save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.cfg.state_path), exist_ok=True)
            payload = {
                "policy": self.cfg.policy.to_dict(),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            with open(self.cfg.state_path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            self._state_error = None
        except Exception as exc:                                # noqa: BLE001
            # 持久化失败必须留痕：否则用户以为策略存下来了，重启后却回到旧值。
            self._state_error = f"{type(exc).__name__}: {exc}"
            self.auditor.write("state.save_failed", action="save", result="failed",
                               error=self._state_error)

    @property
    def health(self) -> tuple[str, str | None]:
        if self._collect_error:
            return Health.DEGRADED.value, f"采集异常（沿用上一份快照）：{self._collect_error}"
        if not self._admin:
            return Health.DEGRADED.value, (
                "未以管理员身份运行：进程/服务信息不完整（受限环境可能只见数十个进程），"
                "且无法结束 SYSTEM 权限的进程")
        return Health.OK.value, None

    _admin: bool = False

    # ------------------------------------------------------------------ #
    # 采集
    # ------------------------------------------------------------------ #
    def snapshot(self, *, refresh: bool = False) -> inventory.Snapshot:
        ttl = max(1, self.cfg.collect.refresh_interval_s)
        with self._lock:
            fresh = self._snapshot is not None and (time.time() - self._snapshot_at) < ttl
            if self._snapshot is not None and fresh and not refresh:
                return self._snapshot
            old = self._snapshot
            try:
                new = inventory.collect(min_idle_s=self.cfg.policy.min_idle_s,
                                        with_signature=self.cfg.collect.with_signature,
                                        kb=self._kb)
            except Exception as exc:                            # noqa: BLE001
                # 采集失败不能把整个守护拖死：记一次证据、退回上一份好快照。
                self._collect_error = f"{type(exc).__name__}: {exc}"
                if self._collect_error != self._collect_error_logged:
                    self._collect_error_logged = self._collect_error
                    self.auditor.write("collect.error", action="collect", result="failed",
                                       error=self._collect_error)
                if old is not None:
                    return old
                raise
            self._collect_error = None
            self._collect_error_logged = None
            self._snapshot, self._snapshot_at = new, time.time()
        try:
            self._maybe_record_pending(new)
        except Exception as exc:                                # noqa: BLE001
            self.auditor.write("kb.pending_failed", action="record", result="failed",
                               error=f"{type(exc).__name__}: {exc}")
        self._publish_diff(old, new)
        return new

    def _publish_diff(self, old: inventory.Snapshot | None, new: inventory.Snapshot) -> None:
        if old is None:
            self.bus.publish("snapshot.updated", {
                "ts": new.ts, "processes": len(new.processes), "software": len(new.software)})
            return
        old_pids = {p.pid: p for p in old.processes}
        new_pids = {p.pid: p for p in new.processes}
        for pid in sorted(set(new_pids) - set(old_pids)):
            proc = new_pids[pid]
            self.bus.publish("process.started", {
                "pid": pid, "name": proc.name, "software_id": proc.classification.software_id,
                "role": proc.classification.role.value, "origin": proc.classification.origin.value})
        for pid in sorted(set(old_pids) - set(new_pids)):
            proc = old_pids[pid]
            self.bus.publish("process.exited", {
                "pid": pid, "name": proc.name, "software_id": proc.classification.software_id})
        old_use = {s.id: s.in_use for s in old.software}
        for item in new.software:
            was = old_use.get(item.id)
            if was is None or was == item.in_use:
                continue
            self.bus.publish("software.started" if item.in_use else "software.stopped",
                             {"software_id": item.id, "name": item.name,
                              "reason": item.in_use_reason})
        self.bus.publish("snapshot.updated", {
            "ts": new.ts, "processes": len(new.processes), "software": len(new.software),
            "candidates": len(inventory.candidates_for_cleanup(new))})

    def _collector_loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                self.snapshot(refresh=True)
                self.bus.publish("heartbeat", {"health": self.health[0],
                                               "uptime_s": int(time.time() - self._started_at)})
                failures = 0
            except Exception as exc:                            # noqa: BLE001
                failures += 1
                self._collect_error = f"{type(exc).__name__}: {exc}"
                self.auditor.write("collect.error", action="collect", result="failed",
                                   error=self._collect_error, consecutive=failures)
                self.bus.publish("notice", {"level": "warn", "code": "collect.error",
                                            "message": f"采集失败（连续 {failures} 次）："
                                                       f"{self._collect_error}"})
                # 失败时退避，避免把日志刷爆
                self._stop.wait(min(30, 2 ** min(failures, 5)))
                continue
            self._stop.wait(max(2, self.cfg.collect.refresh_interval_s))

    # ------------------------------------------------------------------ #
    # 方法注册
    # ------------------------------------------------------------------ #
    def _register(self) -> None:
        d = self.dispatcher
        d.register("hello", self._m_hello)
        d.register("ping", lambda p: {"pong": True})
        d.register("status.get", self._m_status)
        d.register("snapshot.get", self._m_snapshot)
        d.register("software.list", self._m_software_list)
        d.register("software.get", self._m_software_get)
        d.register("process.list", self._m_process_list)
        d.register("process.get", self._m_process_get)
        d.register("process.explain", self._m_process_explain)
        d.register("classify.rules", self._m_classify_rules)
        d.register("classify.reload", self._m_classify_reload)
        d.register("cleanup.plan", self._m_plan)
        d.register("cleanup.execute", self._m_execute)
        d.register("cleanup.history", self._m_history)
        d.register("policy.get", self._m_policy_get)
        d.register("policy.set", self._m_policy_set)
        d.register("protect.add", self._m_protect_add)
        d.register("protect.remove", self._m_protect_remove)
        d.register("audit.query", self._m_audit_query)
        d.register("audit.tail", self._m_audit_tail)
        d.register("audit.export", self._m_audit_export)
        d.register("subscribe", self._m_subscribe)
        d.register("unsubscribe", self._m_unsubscribe)
        d.register("kb.stats", self._m_kb_stats)
        d.register("kb.review", self._m_kb_review)
        d.register("kb.apply", self._m_kb_apply)
        d.register("kb.rollback", self._m_kb_rollback)
        d.register("daemon.reload", self._m_reload)
        d.register("daemon.shutdown", self._m_shutdown)

    # ------------------------------------------------------------------ #
    def _m_hello(self, params: dict[str, Any]) -> dict[str, Any]:
        version = params.get("api_version")
        if version is not None:
            try:
                version_int = int(version)
            except (TypeError, ValueError):
                raise IpcError(E_API_VERSION, f"api_version 非法：{version!r}",
                               {"supported": [API_VERSION]}) from None
            if version_int != API_VERSION:
                raise IpcError(E_API_VERSION,
                               f"协议版本不匹配：客户端 {version}，服务端 {API_VERSION}",
                               {"supported": [API_VERSION]})
        health, reason = self.health
        return {"api_version": API_VERSION, "server": SERVER_VERSION, "pid": os.getpid(),
                "event_seq": self.auditor.last_seq,
                "elevated": self._admin, "health": health, "degraded_reason": reason,
                "dry_run": self.cfg.policy.dry_run, "features": self.dispatcher.features,
                "capabilities": {"can_kill": self._admin,
                                 "can_control_services": self._admin,
                                 "can_stop_service": self._admin,
                                 "can_disable_autostart": False}}

    def _m_status(self, params: dict[str, Any]) -> dict[str, Any]:
        snap = self.snapshot()
        health, reason = self.health
        return {"health": health, "degraded_reason": reason, "elevated": self._admin,
                "dry_run": self.cfg.policy.dry_run,
                "uptime_s": int(time.time() - self._started_at),
                "counts": {"software": len(snap.software), "processes": len(snap.processes),
                           "in_use": sum(1 for s in snap.software if s.in_use),
                           "private_background": sum(s.private_background for s in snap.software),
                           "private": sum(1 for p in snap.processes
                                          if p.classification.private),
                           "candidates": len(inventory.candidates_for_cleanup(snap))},
                "event_seq": self.auditor.last_seq,
                "policy": self.cfg.policy.to_dict(),
                "ipc": self._ipc_info(),
                "warnings": {"kb": list(self._kb_warnings), "state": self._state_error,
                             "collect": self._collect_error},
                "diagnostics": snap.diagnostics}

    def _m_snapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        snap = self.snapshot(refresh=bool(params.get("refresh")))
        payload = snap.to_dict()
        payload["summary"] = {
            "software": len(snap.software), "processes": len(snap.processes),
            "services": len(snap.services), "autostart": len(snap.autostart),
            "in_use": sum(1 for s in snap.software if s.in_use),
            "private": sum(1 for p in snap.processes if p.classification.private),
        }
        return payload

    def _m_software_list(self, params: dict[str, Any]) -> dict[str, Any]:
        snap = self.snapshot()
        kind = params.get("kind")
        limit = _int_param(params, "limit", 0)
        items = inventory.filter_software(
            snap, in_use=params.get("in_use"), has_private=params.get("has_private"),
            kind=kind, limit=limit or None)
        return {"items": [s.to_dict() for s in items], "total": len(items)}

    def _m_software_get(self, params: dict[str, Any]) -> dict[str, Any]:
        snap = self.snapshot()
        target = next((s for s in snap.software if s.id == params.get("id")), None)
        if target is None:
            raise IpcError(E_NOT_FOUND, f"没有这个软件：{params.get('id')}")
        procs = [p.to_dict() for p in snap.processes if p.pid in set(target.processes)]
        services = [s.to_dict() for s in snap.services if s.name in set(target.services)]
        autostart = [a.to_dict() for a in snap.autostart if a.entry_id in set(target.autostart)]
        payload = target.to_dict()            # 规范：SoftwareItem 字段在顶层
        payload["process_pids"] = list(target.processes)
        payload["processes"] = procs
        payload["services"] = services
        payload["autostart"] = autostart
        return payload

    def _m_process_list(self, params: dict[str, Any]) -> dict[str, Any]:
        snap = self.snapshot()
        rows = list(snap.processes)
        filters = params.get("filter") or {}
        if filters.get("software_id"):
            rows = [p for p in rows if p.classification.software_id == filters["software_id"]]
        for key in ("role", "activity", "origin"):
            if filters.get(key):
                rows = [p for p in rows if getattr(p.classification, key).value == filters[key]]
        sort = params.get("sort") or "memory"
        keys = {"memory": lambda p: -(p.memory_mb or 0), "cpu": lambda p: -(p.cpu_seconds or 0),
                "pid": lambda p: p.pid, "name": lambda p: (p.name or "").lower()}
        rows.sort(key=keys.get(sort, keys["memory"]))
        offset = _int_param(params, "offset", 0)
        limit = _int_param(params, "limit", 200)
        # 0 = 不限量（负数是非法参数，不再被切片语义糊弄）
        window = rows[offset:] if limit == 0 else rows[offset: offset + limit]
        return {"items": [p.to_dict() for p in window], "total": len(rows)}

    def _m_process_get(self, params: dict[str, Any]) -> dict[str, Any]:
        snap = self.snapshot()
        proc = inventory.find_process(snap, int(params.get("pid") or 0))
        if proc is None:
            raise IpcError(E_NOT_FOUND, f"找不到进程 {params.get('pid')}")
        return proc.to_dict()                  # 规范：ProcessInfo 字段在顶层

    def _m_process_explain(self, params: dict[str, Any]) -> dict[str, Any]:
        snap = self.snapshot()
        pids = [int(p) for p in (params.get("pids") or [])]
        items = []
        for pid in pids:
            proc = inventory.find_process(snap, pid)
            if proc is None:
                items.append({"pid": pid, "name": None, "classification": None,
                              "error": "E_NOT_FOUND"})
                continue
            items.append({"pid": pid, "name": proc.name,
                          "classification": proc.classification.to_dict()})
        return {"items": items}

    def _m_classify_rules(self, params: dict[str, Any]) -> dict[str, Any]:
        rules = [{"id": f"kb:{name}", "match": name, "role": role, "note": note}
                 for name, (role, note) in sorted(self._kb.items())]
        return {"kb_version": self._kb_version(), "kb_size": len(rules),
                "kb_path": self.cfg.kb_path, "rules": rules,
                "builtin_size": len(DEFAULT_KB), "roles": ROLE_ZH,
                "warnings": list(self._kb_warnings)}

    def _m_classify_reload(self, params: dict[str, Any]) -> dict[str, Any]:
        payload = self._reload_kb()
        self.bus.publish("kb.reloaded", {"kb_version": payload["kb_version"],
                                         "kb_size": payload["kb_size"]})
        return payload

    # ------------------------------------------------------------------ #
    def _m_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        scope = params.get("scope") or "all"
        if scope not in ("all", "software", "pids"):
            raise IpcError("E_BAD_REQUEST", f"scope 只能是 all/software/pids，收到 {scope!r}")
        if scope == "software" and not params.get("software_ids"):
            raise IpcError("E_BAD_REQUEST", "scope=software 时必须给 software_ids")
        if scope == "pids" and not params.get("pids"):
            raise IpcError("E_BAD_REQUEST", "scope=pids 时必须给 pids")
        with self._lock:
            snap = self.snapshot(refresh=bool(params.get("refresh")))
            plan = evaluator.build_plan(
                snap, self.cfg.policy, scope=scope,
                software_ids=params.get("software_ids"), pids=params.get("pids"),
                kill_tree=self.cfg.cleanup.kill_tree, plan_ttl_s=self.cfg.cleanup.plan_ttl_s,
                max_items=self.cfg.cleanup.max_kill_batch)
            self._plans[plan.plan_id] = (plan, snap, time.time())
            self._prune_plans()
            self.auditor.write("cleanup.planned", action="plan", result="ok",
                               dry_run=self.cfg.policy.dry_run, plan_id=plan.plan_id,
                               **{k: v for k, v in plan.summary.items()})
        return plan.to_dict()

    def _prune_plans(self) -> None:
        """回收过期/过多的计划（接口规范给了 TTL；这里同时兜住"只生成不执行"的滥用）。"""
        ttl = max(self.cfg.cleanup.plan_ttl_s, 60)
        now = time.time()
        for plan_id in [pid for pid, (_p, _s, ts) in self._plans.items() if now - ts > ttl]:
            self._plans.pop(plan_id, None)
        if len(self._plans) > _PLAN_KEEP:
            oldest = sorted(self._plans.items(), key=lambda kv: kv[1][2])
            for plan_id, _ in oldest[: len(self._plans) - _PLAN_KEEP]:
                self._plans.pop(plan_id, None)

    def _m_execute(self, params: dict[str, Any]) -> dict[str, Any]:
        plan_id = params.get("plan_id")
        stored = self._plans.get(plan_id)
        if stored is None:
            raise IpcError(E_NOT_FOUND, f"没有这个计划：{plan_id}（请先调用 cleanup.plan）")
        plan, _snap_at_plan, _created = stored
        include = params.get("include_risk") or [RiskLevel.SAFE.value]
        if isinstance(include, str):
            include = [include]
        unknown = [r for r in include if r not in ("safe", "caution")]
        if unknown:
            # `protected` 永远不可执行；拼错的风险等级会让清理**静默空转**，必须报错。
            raise IpcError(E_BAD_REQUEST,
                           f"include_risk 只能是 safe/caution，收到 {unknown}")
        selected_pids = None
        if params.get("pids") is not None:
            raw = params.get("pids")
            if not isinstance(raw, (list, tuple)):
                raise IpcError(E_BAD_REQUEST, "pids 必须是整数数组")
            try:
                selected_pids = [int(pid) for pid in raw]
            except (TypeError, ValueError):
                raise IpcError(E_BAD_REQUEST, f"pids 必须是整数数组，收到 {raw!r}") from None
            if not selected_pids:
                raise IpcError(E_BAD_REQUEST, "没有选中任何进程")
        if not self._admin and not self.cfg.policy.dry_run:
            raise IpcError(E_PERMISSION, "未以管理员运行，无法真实结束进程（可先开启 dry_run）")
        with self._lock:
            try:
                # ⚠️ 必须用**当前快照**校验计划；用计划时快照会让 E_PLAN_STALE 永不触发。
                live = self.snapshot(refresh=True)
                result = cleaner.execute(
                    plan, live, policy=self.cfg.policy, auditor=self.auditor,
                    kill_tree=self.cfg.cleanup.kill_tree, include_risk=tuple(include),
                    selected_pids=selected_pids,
                    graceful=self.cfg.cleanup.graceful_close,
                    close_wait_s=self.cfg.cleanup.close_wait_s,
                    service_wait_s=self.cfg.cleanup.service_wait_s)
            except cleaner.PlanError as exc:
                code = (E_PLAN_EXPIRED if exc.code == "E_PLAN_EXPIRED" else
                        E_PLAN_STALE if exc.code == "E_PLAN_STALE" else
                        E_BAD_REQUEST if exc.code == "E_BAD_REQUEST" else E_BUSY)
                raise IpcError(code, exc.message, exc.detail) from exc
        for item in result.killed + result.failed:
            if item.action == "stop_service":
                event = "service.stopped" if item.ok else "service.stop_failed"
            else:
                event = "process.blocked" if item.ok else "process.kill_failed"
            self.bus.publish(event, {
                "pid": item.pid, "name": item.name, "action": item.action,
                "services": item.services, "mode": item.mode,
                "result": "ok" if item.ok else "failed", "dry_run": result.dry_run})
        self.bus.publish("cleanup.executed", {
            "plan_id": plan.plan_id, "killed": len(result.killed), "failed": len(result.failed),
            "stop_services": sum(1 for r in result.killed + result.failed
                                 if r.action == "stop_service"),
            "freed_mb": result.freed_mb, "dry_run": result.dry_run})
        self._plans.pop(plan.plan_id, None)
        return result.to_dict()

    def _m_policy_get(self, params: dict[str, Any]) -> dict[str, Any]:
        # 规范 §4.4：policy.get 的结果就是策略字段本身（不再套 {"policy": ...}）。
        return self.cfg.policy.to_dict()

    def _m_policy_set(self, params: dict[str, Any]) -> dict[str, Any]:
        """改策略：**先全部校验、再原子应用**。

        ⚠️ 早期版本直接 `setattr`，`dry_run="yes"`、`min_idle_s="abc"` 这类脏值会被写进策略，
        随后采集线程在 `_in_use_of` 里 `now - started <= min_idle_s` 抛 TypeError 而**静默崩溃**
        ——等于通过 IPC 就能把守护搞瘫。现在类型不对直接 E_BAD_REQUEST 且不改动任何字段。
        """
        parsed: dict[str, Any] = {}
        for key in ("dry_run", "allow_cleanup_while_idle"):
            if params.get(key) is not None:
                parsed[key] = _coerce_bool(params[key], key)
        for key, (low, high) in _POLICY_INT_RANGES.items():
            if params.get(key) is not None:
                parsed[key] = _coerce_int(params[key], key, low, high)
        if params.get("never_touch_roles") is not None:
            parsed["never_touch_roles"] = _coerce_patterns(
                params["never_touch_roles"], "never_touch_roles", allowed=_VALID_ROLES,
                require_prefix=False)
        for key in ("protect", "allow"):
            if params.get(key) is not None:
                parsed[key] = _coerce_patterns(params[key], key,
                                               allowed=None, require_prefix=True)
        with self._lock:
            for key, value in parsed.items():
                setattr(self.cfg.policy, key, value)
            self._save_state()
        payload = self.cfg.policy.to_dict()
        payload["changed"] = sorted(parsed)
        self.bus.publish("policy.changed", {"policy": self.cfg.policy.to_dict()})
        return payload

    @staticmethod
    def _protect_pattern(params: dict[str, Any]) -> str:
        """兼容两种写法：`{pattern:"pid:123"}` 与规范 §4.4 的 `{pid|software_id|exe}`。"""
        pattern = params.get("pattern")
        if pattern:
            value = str(pattern).strip()
            if not value.startswith(("pid:", "sw:", "exe:")):
                raise IpcError(E_BAD_REQUEST, "pattern 必须是 pid:/sw:/exe: 前缀")
            return value
        if params.get("pid") is not None:
            try:
                return f"pid:{int(params['pid'])}"
            except (TypeError, ValueError):
                raise IpcError(E_BAD_REQUEST,
                               f"pid 必须是整数，收到 {params['pid']!r}") from None
        if params.get("software_id"):
            return f"sw:{str(params['software_id']).strip()}"
        if params.get("exe"):
            return f"exe:{str(params['exe']).strip()}"
        raise IpcError(E_BAD_REQUEST, "需要 pattern，或 pid / software_id / exe 之一")

    def _m_protect_add(self, params: dict[str, Any]) -> dict[str, Any]:
        pattern = self._protect_pattern(params)
        with self._lock:
            if pattern not in self.cfg.policy.protect:
                self.cfg.policy.protect.append(pattern)
            self._save_state()
        return {"protect": list(self.cfg.policy.protect), "added": pattern}

    def _m_protect_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        pattern = self._protect_pattern(params)
        with self._lock:
            self.cfg.policy.protect = [p for p in self.cfg.policy.protect if p != pattern]
            self._save_state()
        return {"protect": list(self.cfg.policy.protect), "removed": pattern}

    def _m_history(self, params: dict[str, Any]) -> dict[str, Any]:
        limit = _int_param(params, "limit", 20)
        rows = cleaner.history(self.auditor, int(limit))
        return {"items": rows, "total": len(rows)}

    def _m_audit_query(self, params: dict[str, Any]) -> dict[str, Any]:
        limit = _int_param(params, "limit", 200)
        rows = self.auditor.read(since=params.get("since"), until=params.get("until"),
                                 software_id=params.get("software_id"),
                                 event=params.get("event"), limit=limit,
                                 offset=_int_param(params, "offset", 0))
        return {"rows": [r.to_dict() for r in rows], "total": len(rows)}

    def _m_audit_tail(self, params: dict[str, Any]) -> dict[str, Any]:
        rows = self.auditor.tail(_int_param(params, "n", 100, minimum=1))
        return {"rows": [r.to_dict() for r in rows]}

    def _m_audit_export(self, params: dict[str, Any]) -> dict[str, Any]:
        path = params.get("path")
        fmt = params.get("format") or "jsonl"
        if not path:
            raise IpcError(E_BAD_REQUEST, "缺少 path")
        if fmt not in ("jsonl", "csv"):
            raise IpcError(E_BAD_REQUEST, f"不支持的导出格式 {fmt!r}（可选 jsonl / csv）")
        # 提权守护不能被当作“任意文件写入器”：拒绝系统目录/Program Files。
        target = os.path.abspath(str(path))
        forbidden = []
        for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            base = os.environ.get(var)
            if base:
                forbidden.append(os.path.normcase(os.path.abspath(base)))
        normalized = os.path.normcase(target)
        if any(normalized == base or normalized.startswith(base + os.sep)
               for base in forbidden):
            raise IpcError(E_BAD_REQUEST, f"拒绝导出到系统目录：{target}")
        try:
            count = self.auditor.export(target, fmt=fmt)
        except OSError as exc:
            raise IpcError(E_BAD_REQUEST, f"无法写入 {target}：{exc}") from exc
        return {"path": target, "rows": count, "format": fmt}

    # ------------------------------------------------------------------ #
    # 知识库闭环
    # ------------------------------------------------------------------ #
    def _m_kb_stats(self, params: dict[str, Any]) -> dict[str, Any]:
        stats = self._pending.stats()
        stats.update({"kb_size": len(self._kb), "kb_version": self._kb_version(),
                      "kb_path": self.cfg.kb_path,
                      "pending_enabled": self.cfg.kb.pending_enabled,
                      "review_interval_s": self.cfg.kb.review_interval_s})
        return stats

    def _m_kb_review(self, params: dict[str, Any]) -> dict[str, Any]:
        result = kbmod.build_review(self.cfg.data_dir, self.cfg.kb_path,
                                    pending_path=self._pending.path)
        self.auditor.write("kb.review", action="review", result="ok",
                           count=result.get("count"), dir=result.get("dir"))
        return result

    def _m_kb_apply(self, params: dict[str, Any]) -> dict[str, Any]:
        patch = params.get("patch")
        if patch is None and params.get("path"):
            try:
                patch = kbmod.load_patch(str(params["path"]))
            except Exception as exc:                            # noqa: BLE001
                raise IpcError(E_BAD_REQUEST, f"读取补丁失败：{exc}") from exc
        if not isinstance(patch, dict):
            raise IpcError(E_BAD_REQUEST, "缺少 patch（JSON 对象）或 path")
        dry_run = bool(params.get("dry_run", True))
        try:
            result = kbmod.apply_patch(self.cfg.kb_path, patch, dry_run=dry_run)
        except ValueError as exc:
            raise IpcError(E_BAD_REQUEST, str(exc)) from exc
        if result.get("applied"):
            reloaded = self._reload_kb()
            result["kb_version"] = reloaded["kb_version"]
            result["kb_size"] = reloaded["kb_size"]
            self.auditor.write("kb.applied", action="apply", result="ok",
                               diff=result.get("diff"), backup=result.get("backup"),
                               kb_version=reloaded["kb_version"])
            self.bus.publish("kb.applied", {"diff": result.get("diff"),
                                            "kb_version": reloaded["kb_version"]})
        return result

    def _m_kb_rollback(self, params: dict[str, Any]) -> dict[str, Any]:
        backup = str(params.get("backup") or "").strip()
        if not backup:
            raise IpcError(E_BAD_REQUEST, "缺少 backup（知识库备份路径）")
        real = os.path.abspath(backup)
        kb_dir = os.path.dirname(os.path.abspath(self.cfg.kb_path))
        kb_name = os.path.basename(self.cfg.kb_path)
        if (os.path.dirname(real) != kb_dir
                or not os.path.basename(real).startswith(kb_name + ".bak-")):
            raise IpcError(E_BAD_REQUEST, "只允许恢复本知识库的 .bak-* 备份")
        try:
            result = kbmod.rollback(self.cfg.kb_path, real)
        except OSError as exc:
            raise IpcError(E_BAD_REQUEST, f"恢复失败：{exc}") from exc
        reloaded = self._reload_kb()
        result.update({"kb_version": reloaded["kb_version"],
                       "kb_size": reloaded["kb_size"]})
        self.auditor.write("kb.rolled_back", action="rollback", result="ok",
                           backup=real, kb_version=reloaded["kb_version"])
        return result

    def _m_subscribe(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.bus.subscribe(params.get("conn_id") or "stdio",
                                  events=params.get("events"),
                                  since_seq=params.get("since_seq"))

    def _m_unsubscribe(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.bus.unsubscribe(params.get("conn_id") or "stdio", events=params.get("events"))

    def _m_reload(self, params: dict[str, Any]) -> dict[str, Any]:
        from .config import load_config
        new_cfg = load_config(self.cfg.config_path, data_dir=self.cfg.data_dir,
                              create_if_missing=False)
        self.cfg.policy = new_cfg.policy
        self.cfg.cleanup = new_cfg.cleanup
        self.cfg.collect = new_cfg.collect
        self.cfg.kb = new_cfg.kb
        kb_payload = self._reload_kb()
        self._snapshot, self._snapshot_at = None, 0.0
        self.bus.publish("config.reloaded", {"config_path": self.cfg.config_path})
        return {"reloaded": True, "config_path": self.cfg.config_path,
                "kb_size": kb_payload["kb_size"], "kb_version": kb_payload["kb_version"],
                "warnings": new_cfg.warnings + kb_payload["warnings"]}

    def _m_shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        self.stop()
        return {"bye": True}

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _ipc_info(self) -> dict[str, Any] | None:
        if self._transport == "pipe" and self._server:
            return {"transport": "pipe", "pipe": self.pipe_name,
                    "security": self._server.security_note, "error": self._server.last_error}
        if self._transport == "tcp" and self._tcp:
            return {"transport": "tcp", "host": self._tcp.host, "port": self._tcp.port,
                    "error": self._tcp.last_error}
        return None

    # ------------------------------------------------------------------ #
    # 单实例标记：pipe 在受限环境可能不可连，用 pid 文件给启动器一个旁路判据
    # ------------------------------------------------------------------ #
    def _pid_path(self) -> str:
        return os.path.join(self.cfg.data_dir, "daemon.pid")

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                                    ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == 259          # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:                                       # noqa: BLE001
            return False

    def _read_pid(self) -> int:
        try:
            with open(self._pid_path(), "r", encoding="utf-8") as handle:
                return int((json.load(handle) or {}).get("pid") or 0)
        except Exception:                                       # noqa: BLE001
            return 0

    def _write_pid(self) -> None:
        try:
            path = self._pid_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".writing"
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                json.dump({"pid": os.getpid(), "pipe": self.pipe_name,
                           "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
                          handle, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass

    def _clear_pid(self) -> None:
        try:
            if self._read_pid() == os.getpid():
                os.remove(self._pid_path())
        except OSError:
            pass

    def prepare(self) -> None:
        """只探测能力，不启动线程（供 CLI 进程内直跑用）。"""
        from . import winapi
        self._admin = winapi.is_admin()

    def start(self, *, with_server: bool = True, transport: str = "auto") -> None:
        from . import winapi
        from .ipc import (TcpServer, clear_endpoint, probe_pipe, write_endpoint)
        self._admin = winapi.is_admin()
        existing = self._read_pid()
        if existing and existing != os.getpid() and self._pid_alive(existing):
            raise RuntimeError(f"已有守护在运行（pid {existing}，{self.cfg.data_dir}）；"
                               "请先执行 guardctl stop 或直接使用它")
        health, reason = self.health
        self._write_pid()
        self.auditor.write("daemon.start", action="start", result="ok",
                           elevated=self._admin, health=health, degraded_reason=reason,
                           config_path=self.cfg.config_path)
        self._collector = threading.Thread(target=self._collector_loop, daemon=True)
        self._collector.start()
        if not with_server:
            return

        # ① 命名管道（首选）
        if transport in ("auto", "pipe"):
            self._server = PipeServer(self.dispatcher, self.bus, pipe_name=self.pipe_name)
            threading.Thread(target=self._server.serve_forever, daemon=True).start()
            ready = self._server.ready.wait(timeout=4.0)
            ok, why = (probe_pipe(self.pipe_name) if ready
                       else (False, self._server.last_error or "超时未就绪"))
            if ok:
                self._transport = "pipe"
                self.auditor.write("daemon.pipe_ready", action="listen", result="ok",
                                   pipe=self.pipe_name, security=self._server.security_note)
            else:
                self._server.stop()
                self._server = None
                self.auditor.write("daemon.pipe_error", action="listen", result="failed",
                                   pipe=self.pipe_name, error=why)
                if transport == "pipe":
                    raise RuntimeError(f"命名管道不可用：{why}")

        # ② 回环 TCP（管道不可用时的等价通道）
        if self._transport is None:
            self._tcp = TcpServer(self.dispatcher, self.bus)
            threading.Thread(target=self._tcp.serve_forever, daemon=True).start()
            if not self._tcp.ready.wait(timeout=4.0):
                raise RuntimeError(f"TCP 回退通道也启动失败：{self._tcp.last_error}")
            self._transport = "tcp"
            write_endpoint(self.cfg.data_dir, self._tcp.endpoint())
            self.auditor.write("daemon.tcp_ready", action="listen", result="ok",
                               host=self._tcp.host, port=self._tcp.port)

    def stop(self) -> None:
        from .ipc import clear_endpoint
        if self._stopped:
            return                                  # 幂等：shutdown 与 finally 都会调
        self._stopped = True
        self._stop.set()
        if self._server:
            self._server.stop()
        if self._tcp:
            self._tcp.stop()
            clear_endpoint(self.cfg.data_dir)
        self.auditor.write("daemon.stop", action="stop", result="ok")
        self._clear_pid()

    def wait(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.3)

    # ------------------------------------------------------------------ #
    def run_stdio(self) -> None:
        """stdio 模式下不需要管道服务端。"""
        from .ipc import serve_stdio
        self.start(with_server=False)
        try:
            serve_stdio(self.dispatcher)
        finally:
            self.stop()
