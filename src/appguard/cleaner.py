"""L3 执行：按计划结束进程 / 停止第三方服务（dry-run、优雅关闭、PID 复用防护）。

安全约束（写死在实现里）：
  1. 只能执行 `evaluator.build_plan` 产生的计划，且必须通过 `evaluator.validate`
  2. `policy.dry_run = true` 时**绝不真杀、不发 WM_CLOSE、不停服务**，只写审计
  3. 用户空闲（`GetLastInputInfo` 超阈值）且 `allow_cleanup_while_idle=false` 时拒绝执行
  4. 动手前复核 pid 对应的进程名，防止 PID 复用误杀
  5. **动作单位是进程**：默认只结束计划里被选中的 PID，不连带子进程
  6. 有窗口的进程先发 WM_CLOSE（等 `close_wait_s` 秒），仍活着才强杀
  7. 第三方服务的动作是 `stop_service`（不改服务启动类型，只停运行状态）
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

from . import evaluator
from . import winapi
from .audit import Auditor
from .inventory import Snapshot
from .models import CleanupPlan, CleanupResult, KillResult, Policy, RiskLevel

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class PlanError(Exception):
    """带接口规范错误码的计划执行错误。"""

    def __init__(self, code: str, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def to_error(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def kill_process(pid: int, *, tree: bool = False) -> tuple[bool, str | None, str]:
    """结束进程（可选连带子进程）。

    主通道是**原生 `TerminateProcess`**（我们自己的句柄，受限环境下也有效）；
    失败时才回落 `taskkill`。返回 (是否成功, 错误, 所用方法)。
    """
    if tree:
        ok, failures = winapi.terminate_tree(pid)
        if ok:
            return True, None, "native_tree"
        error = "; ".join(failures[:3]) or "未知失败"
    else:
        ok, error = winapi.terminate_process(pid)
        error = error or ""
        if ok:
            return True, None, "native"
    # 兜底：taskkill（需要额外进程，权限不足时会 Access denied）
    ok2, err2 = taskkill(pid, tree=tree)
    if ok2:
        return True, None, "taskkill"
    return False, error or err2, "failed"


def taskkill(pid: int, *, tree: bool = False, timeout: int = 15) -> tuple[bool, str | None]:
    """兜底通道：调用系统 taskkill。返回 (是否成功, 错误信息)。"""
    args = ["taskkill", "/PID", str(pid), "/F"]
    if tree:
        args.insert(1, "/T")
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, creationflags=_CREATE_NO_WINDOW)
    except Exception as exc:                                     # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode == 0:
        return True, None
    message = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    return False, (message[-1] if message else f"taskkill 退出码 {proc.returncode}")


def execute(plan: CleanupPlan, snapshot: Snapshot, *, policy: Policy, auditor: Auditor,
            kill_tree: bool = False, include_risk: tuple[str, ...] = (RiskLevel.SAFE.value,),
            selected_pids: list[int] | None = None, force: bool = False,
            graceful: bool = True, close_wait_s: int = 5,
            service_wait_s: int = 15) -> CleanupResult:
    """执行清理计划。

    ``snapshot`` 必须是**当前快照**（用于计划失效校验），不是计划生成时的那份。
    ``selected_pids`` 非 None 时，只执行计划里这些 PID（行级勾选）。
    """
    idle_s = winapi.last_input_seconds()
    if (not policy.allow_cleanup_while_idle and idle_s > policy.min_idle_s
            and not policy.dry_run and not force):
        raise PlanError("E_BUSY", f"你已离开 {int(idle_s)} 秒，为避免误关已拒绝执行清理"
                                  f"（如需允许请设置 policy.allow_cleanup_while_idle = true）")

    wanted = {r.value if isinstance(r, RiskLevel) else str(r) for r in include_risk}
    if selected_pids is not None:
        # 行级勾选是显式意图：不再被 include_risk 的默认值（通常只有 safe）二次过滤。
        targets = list(plan.items)
    else:
        targets = [item for item in plan.items if item.risk.value in wanted]

    if selected_pids is not None:
        wanted_pids = {int(x) for x in selected_pids}
        known = {item.pid for item in plan.items}
        missing = sorted(wanted_pids - known)
        if missing:
            raise PlanError("E_BAD_REQUEST",
                            f"计划里没有这些进程：{missing}（请重新生成计划）")
        targets = [item for item in targets if item.pid in wanted_pids]
        if not targets:
            raise PlanError("E_BAD_REQUEST", "没有选中任何计划项")

    # 只校验“本次真正要动”的目标：真实桌面每几秒都有别的进程来去，
    # 全池指纹会让计划刚生成就过期。
    error = evaluator.validate(plan, snapshot,
                               selected_pids=[item.pid for item in targets])
    if error and not force:
        raise PlanError(error, "计划已过期或目标集合已变化，请重新生成计划")

    seq_from = auditor.last_seq + 1
    result = CleanupResult(plan_id=plan.plan_id, dry_run=policy.dry_run)
    auditor.write("cleanup.start", action="cleanup", result="begin", dry_run=policy.dry_run,
                  plan_id=plan.plan_id, planned=len(plan.items), selected=len(targets),
                  selected_pids=sorted(item.pid for item in targets),
                  include_risk=sorted(wanted), idle_seconds=round(idle_s, 1))

    for item in targets:
        if policy.dry_run:
            outcome = KillResult(pid=item.pid, name=item.name, ok=True, error=None,
                                 tree=kill_tree, action=item.action,
                                 services=list(item.services), mode="dry_run")
            result.killed.append(outcome)
            result.freed_mb += item.memory_mb
            auditor.write("cleanup.dry_run", pid=item.pid, name=item.name,
                          software_id=item.software_id, role=item.role.value,
                          origin=item.origin.value, rule=f"action:{item.action}",
                          action=item.action, result="skipped", dry_run=True,
                          services=item.services, reason=item.reason)
            continue

        if item.action == "stop_service":
            ok, err = _stop_services(item, timeout=service_wait_s)
            outcome = KillResult(pid=item.pid, name=item.name, ok=ok, error=err,
                                 tree=False, action="stop_service",
                                 services=list(item.services), mode="service")
            (result.killed if ok else result.failed).append(outcome)
            if ok:
                result.freed_mb += item.memory_mb
            auditor.write("service.stopped" if ok else "service.stop_failed",
                          pid=item.pid, name=item.name, software_id=item.software_id,
                          role=item.role.value, origin=item.origin.value,
                          rule=f"action:{item.action}", action="stop_service",
                          result="ok" if ok else "failed", dry_run=False,
                          services=item.services, reason=item.reason, error=err)
            continue

        mode = "force"
        ok = False
        err: str | None = None
        if graceful and item.windows:
            # 先礼后兵：发 WM_CLOSE，等它自己退出；仍在就强杀。
            winapi.close_windows(item.pid, timeout=close_wait_s)
            if not winapi.is_process_alive(item.pid):
                ok, err, mode = True, None, "graceful"
            else:
                ok, err = _kill_with_guard(item, kill_tree=kill_tree)
                mode = "force_after_close"
        else:
            ok, err = _kill_with_guard(item, kill_tree=kill_tree)

        outcome = KillResult(pid=item.pid, name=item.name, ok=ok, error=err,
                             tree=kill_tree, action="kill", mode=mode)
        (result.killed if ok else result.failed).append(outcome)
        if ok:
            result.freed_mb += item.memory_mb
        auditor.write("process.blocked" if ok else "process.kill_failed",
                      pid=item.pid, name=item.name, software_id=item.software_id,
                      role=item.role.value, origin=item.origin.value,
                      rule=f"action:{item.action}", action="kill",
                      result="ok" if ok else "failed", dry_run=False,
                      mode=mode, reason=item.reason, error=err, tree=kill_tree)

    result.freed_mb = round(result.freed_mb, 1)
    result.audit_seq_from = seq_from
    result.audit_seq_to = auditor.last_seq
    auditor.write("cleanup.executed", action="cleanup", result="ok", dry_run=policy.dry_run,
                  plan_id=plan.plan_id, killed=len(result.killed), failed=len(result.failed),
                  freed_mb=result.freed_mb, seq_from=seq_from, seq_to=result.audit_seq_to,
                  stop_services=sum(1 for r in result.killed + result.failed
                                    if r.action == "stop_service"))
    return result


def _stop_services(item, *, timeout: int) -> tuple[bool, str | None]:
    """停止计划项对应的一个/多个服务；任一失败即整体失败，但会尝试完全部。"""
    errors: list[str] = []
    for name in item.services:
        ok, err = winapi.stop_service(name, timeout=timeout)
        if not ok:
            errors.append(f"{name}: {err}")
    if errors:
        return False, "; ".join(errors)
    return True, None


def _kill_with_guard(item, *, kill_tree: bool) -> tuple[bool, str | None]:
    """PID 复用防护：先复核进程名，再动手。"""
    details = winapi.process_details(item.pid)
    exe = details.get("exe")
    if not exe:
        return False, "进程已不存在或无法访问（可能已退出）"
    current_name = os.path.basename(exe)
    if current_name.lower() != (item.name or "").lower():
        return False, f"PID 已复用（当前是 {current_name}，计划里是 {item.name}），已跳过"
    ok, error, _method = kill_process(item.pid, tree=kill_tree)
    if ok and winapi.wait_process_gone(item.pid, timeout=2.0):
        return True, None
    if ok:
        return False, "进程仍存活（结束请求已接受但未生效）"
    return False, f"{error or '结束失败'}（方法：{_method}）"


def history(auditor: Auditor, limit: int = 20) -> list[dict]:
    """从审计里回放清理历史（供 `cleanup.history`）。"""
    rows = auditor.read(event="cleanup.executed", limit=limit)
    return [{"plan_id": (r.detail or {}).get("plan_id"), "ts": r.ts,
             "killed": (r.detail or {}).get("killed", 0),
             "failed": (r.detail or {}).get("failed", 0),
             "freed_mb": (r.detail or {}).get("freed_mb", 0.0),
             "stop_services": (r.detail or {}).get("stop_services", 0),
             "dry_run": r.dry_run} for r in reversed(rows)]
