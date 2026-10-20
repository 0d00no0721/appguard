"""L3 评估：候选集 → 风险分级 → 清理计划。

**这是《接口规范.md》§5.2「私自开启」判定的唯一实现**，其它模块不得另写一套口径。

2026-10-18 口径（用户确认）：
  * `in_use` 只是标签，**不再是候选池闸门**；
  * 微软组件永久保护；关键系统进程 / 守护自身永久保护；
  * 更新/推广/遥测/崩溃收集 —— 任何软件（包括正在用的）都算私自，默认勾选；
  * 托盘/后台常驻/辅助/第三方服务 —— 软件未在用 + 来源是自启/服务链才算私自，默认勾选；
  * 正在用软件的后台常驻/辅助/服务 —— 展示但默认不勾，避免拆掉正在用的运行时；
  * 动作单位是进程（kill），第三方服务是唯一的服务级动作（stop_service）。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

from .classify import CLEAR_PRIVATE_ROLES, ORIGIN_PHRASE
from .inventory import Snapshot, plan_pool, service_names_for
from .models import (CleanupItem, CleanupPlan, Policy, ProcRole, ProcessInfo,
                     RiskLevel, SkippedItem)

_ERR_EXPIRED = "E_PLAN_EXPIRED"
_ERR_STALE = "E_PLAN_STALE"
_BACKGROUND_ROLES = {ProcRole.DAEMON, ProcRole.TRAY, ProcRole.HELPER}
_MAIN_ROLES = {ProcRole.MAIN, ProcRole.LAUNCHER, ProcRole.INSTALLER}

# 结束它们=蓝屏/注销/系统不可用。微软组件本来也会被微软规则保护，这里再挡一层。
CRITICAL_NAMES = {
    "system", "registry", "memory compression", "idle",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "lsaiso.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def self_chain(processes: list[ProcessInfo],
               extra_names: tuple[str, ...] = ("appguard", "app_guard", "guardctl")) -> set[int]:
    """守护自身 + 其父链 + 命令行里提到本项目模块的进程 —— 永不清理。"""
    by_pid = {p.pid: p for p in processes}
    protected = {os.getpid()}
    current = by_pid.get(os.getpid())
    while current and current.ppid and current.ppid not in protected:
        protected.add(current.ppid)
        current = by_pid.get(current.ppid)
    for proc in processes:
        cmd = (proc.cmdline or "").lower()
        if any(name in cmd for name in extra_names):
            protected.add(proc.pid)
    return protected


def matches_pattern(pattern: str, proc: ProcessInfo, software_id: str | None) -> bool:
    """支持 `pid:1234` / `sw:<软件id>` / `exe:<完整路径>` 三种写法。"""
    try:
        kind, _, value = pattern.partition(":")
        if kind == "pid":
            return proc.pid == int(value)
        if kind == "sw":
            return software_id is not None and software_id == value
        if kind == "exe":
            return bool(proc.exe) and os.path.normcase(proc.exe) == os.path.normcase(value)
    except Exception:
        return False
    return False


def _critical(proc: ProcessInfo) -> bool:
    return (proc.name or "").lower() in CRITICAL_NAMES


def grade(proc: ProcessInfo, policy: Policy, *, self_pids: set[int],
          in_use: bool = False, service_names: list[str] | None = None
          ) -> tuple[RiskLevel, str, str | None]:
    """返回 (风险, 中文理由, 跳过原因)。跳过原因为 None 表示可进入计划。

    ``in_use`` 只影响“默认是否勾选”（safe vs caution），不再影响能否进入候选。
    """
    cls = proc.classification
    if proc.pid in self_pids:
        return RiskLevel.PROTECTED, "守护自身或其父进程", "是守护自身/父进程"
    if not cls.software_id:
        return RiskLevel.PROTECTED, "无法归属到某个软件", "无法归属到某个软件"

    # ---- 永久保护：微软 / 系统角色 / 关键进程 / 关键进程名 -------------------
    if cls.microsoft:
        return RiskLevel.PROTECTED, "微软组件，按你的设置永久保留", "微软组件，永久保留"
    if cls.role is ProcRole.SYSTEM:
        return RiskLevel.PROTECTED, f"{cls.role_zh}属于系统组件", "系统组件永久保留"
    if _critical(proc):
        return (RiskLevel.PROTECTED, "关键系统进程，结束会导致蓝屏/注销/系统不可用",
                "关键系统进程，禁止结束")

    # ---- 策略：never_touch_roles / 保护名单 ---------------------------------
    allowed = any(matches_pattern(p, proc, cls.software_id) for p in policy.allow)
    if cls.role.value in policy.never_touch_roles and not allowed:
        return (RiskLevel.PROTECTED, f"{cls.role_zh}属于永不清理的角色",
                f"角色受保护（{cls.role.value}）")
    for pattern in policy.protect:
        if matches_pattern(pattern, proc, cls.software_id):
            return RiskLevel.PROTECTED, "命中你的保护名单", f"保护名单：{pattern}"

    # ---- 第三方服务：动作是 stop_service，不是 kill --------------------------
    if cls.role is ProcRole.SERVICE:
        names = service_names or []
        if not names:
            return (RiskLevel.PROTECTED,
                    "第三方服务，但找不到它登记的服务名（无法安全停止）",
                    "找不到对应服务名")
        if in_use:
            return (RiskLevel.CAUTION,
                    f"第三方服务（{', '.join(names)}），你正在用这个软件，默认不勾",
                    None)
        return (RiskLevel.SAFE,
                f"第三方服务（{', '.join(names)}）在后台常驻，而该软件你并没有在用",
                None)

    # ---- 明确私自：更新/推广/遥测/崩溃收集（任何软件、任何使用状态） --------
    if cls.role in CLEAR_PRIVATE_ROLES:
        suffix = "（你在用这个软件也仍属私自组件）" if in_use else "，而该软件你并没有在用"
        return (RiskLevel.SAFE,
                f"{cls.role_zh}{ORIGIN_PHRASE.get(cls.origin, '来源不明')}{suffix}", None)

    # ---- 后台常驻 / 托盘 / 辅助：未在用 + 自启/服务链才算私自 -----------------
    if cls.role in _BACKGROUND_ROLES:
        if cls.private:
            return (RiskLevel.SAFE,
                    f"{cls.role_zh}{ORIGIN_PHRASE.get(cls.origin, '来源不明')}，"
                    "而该软件你并没有在用", None)
        reason = (f"{cls.role_zh}，但你正在用这个软件" if in_use
                  else f"{cls.role_zh}，来源不像是软件自行拉起的")
        return RiskLevel.CAUTION, reason, None

    # ---- 主程序 / 启动器 / 安装器 / 未知：可选但默认不勾 ---------------------
    if cls.role in (ProcRole.MAIN, ProcRole.LAUNCHER) and in_use:
        return (RiskLevel.PROTECTED,
                "你正在使用这个软件，主程序/启动器不作为清理目标",
                "你正在使用这个软件")
    if cls.role is ProcRole.MAIN:
        return RiskLevel.CAUTION, "主程序仍在后台驻留（不是私自开启，默认不勾）", None
    if cls.role in _MAIN_ROLES:
        return RiskLevel.CAUTION, f"{cls.role_zh}仍在后台（默认不勾）", None
    return RiskLevel.CAUTION, "无法确认它的职能（默认不勾，可手动选择）", None


def fingerprint(snapshot: Snapshot) -> str:
    """计划指纹：候选池的任何变化都会让它失效（接口规范 §2.7）。"""
    pids = sorted(p.pid for p in plan_pool(snapshot))
    return hashlib.sha1(",".join(map(str, pids)).encode()).hexdigest()[:16]


def build_plan(snapshot: Snapshot, policy: Policy, *, scope: str = "all",
               software_ids: list[str] | None = None, pids: list[int] | None = None,
               kill_tree: bool = False, plan_ttl_s: int = 120,
               max_items: int = 200) -> CleanupPlan:
    """生成清理计划。

    计划项包含全部“未使用的软件 + 可手动选择的进程”；``default_checked``
    才是 UI 默认勾选的口径。只由 ``grade()`` 判为 PROTECTED 的对象进入
    ``protected_skipped``，永远不可执行。
    """
    now = _now()
    in_use_by_id = {s.id: s.in_use for s in snapshot.software}
    name_by_id = {s.id: s.name for s in snapshot.software}
    self_pids = self_chain(snapshot.processes)

    pool: list[ProcessInfo] = list(plan_pool(snapshot))

    if scope == "software" and software_ids:
        wanted = set(software_ids)
        pool = [p for p in pool if p.classification.software_id in wanted]
    elif scope == "pids" and pids:
        wanted_pids = set(pids)
        pool = [p for p in pool if p.pid in wanted_pids]

    items: list[CleanupItem] = []
    skipped: list[SkippedItem] = []
    seen: set[int] = set()
    for proc in pool:
        if proc.pid in seen:
            continue
        seen.add(proc.pid)
        cls = proc.classification
        sw_id = cls.software_id or ""
        in_use = in_use_by_id.get(sw_id, False)
        service_names = service_names_for(proc, snapshot.services) \
            if cls.role is ProcRole.SERVICE else []
        risk, reason, skip_why = grade(proc, policy, self_pids=self_pids,
                                       in_use=in_use, service_names=service_names)
        if skip_why:
            skipped.append(SkippedItem(pid=proc.pid, name=proc.name, why=skip_why))
            continue
        if len(items) >= max_items:
            skipped.append(SkippedItem(pid=proc.pid, name=proc.name, why="超过单次计划上限"))
            continue
        action = "stop_service" if cls.role is ProcRole.SERVICE else "kill"
        items.append(CleanupItem(
            pid=proc.pid, name=proc.name, software_id=cls.software_id,
            software=name_by_id.get(sw_id), role=cls.role, origin=cls.origin,
            risk=risk, memory_mb=proc.memory_mb or 0.0,
            reason=f"{name_by_id.get(sw_id, '某个软件')}：{reason}",
            restart_cmd=proc.cmdline or None,
            action=action,
            default_checked=risk is RiskLevel.SAFE,
            services=service_names if action == "stop_service" else [],
            windows=list(proc.windows),
            private=cls.private,
            microsoft=cls.microsoft,
        ))

    items.sort(key=lambda i: (not i.default_checked, -i.memory_mb))
    summary = {
        "total": len(items),
        "safe": sum(1 for i in items if i.risk is RiskLevel.SAFE),
        "caution": sum(1 for i in items if i.risk is RiskLevel.CAUTION),
        "protected": len(skipped),
        "default_checked": sum(1 for i in items if i.default_checked),
        "stop_services": sum(1 for i in items if i.action == "stop_service"),
        "memory_mb": round(sum(i.memory_mb for i in items), 1),
        "default_memory_mb": round(sum(i.memory_mb for i in items if i.default_checked), 1),
        "scope": scope,
    }
    plan_id = f"p-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    return CleanupPlan(
        plan_id=plan_id, ts=now.isoformat(timespec="seconds"), scope=scope, items=items,
        protected_skipped=skipped, summary=summary,
        expires_at=(now + timedelta(seconds=plan_ttl_s)).isoformat(timespec="seconds"),
        fingerprint=fingerprint(snapshot))


def validate(plan: CleanupPlan, snapshot: Snapshot, *, now: datetime | None = None,
             selected_pids: list[int] | None = None) -> str | None:
    """校验计划是否仍可执行；返回错误码或 None。

    过期时间**解析不了就算过期**（fail-closed）。

    ``selected_pids``（行级选择/本次实际目标）存在时，只校验**这些目标**
    是否仍在当前候选池里：别的进程来去不应让计划失效（真实桌面每几秒都
    有进程增减，全池指纹会让计划几乎无法执行）。不传时保留全池指纹语义，
    供直接调用/测试使用。
    """
    moment = now or _now()
    if plan.expires_at:
        try:
            if moment > datetime.fromisoformat(plan.expires_at):
                return _ERR_EXPIRED
        except Exception:                                        # noqa: BLE001
            return _ERR_EXPIRED
    if selected_pids is not None:
        pool = {p.pid for p in plan_pool(snapshot)}
        missing = [pid for pid in selected_pids if pid not in pool]
        if missing:
            return _ERR_STALE
        return None
    if plan.fingerprint and plan.fingerprint != fingerprint(snapshot):
        return _ERR_STALE
    return None
