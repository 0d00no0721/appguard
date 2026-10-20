"""真实击杀的受控验证（不碰系统进程，只杀本脚本自己起的进程）。

验证三件事：
  1. `taskkill /T /F` 路径真的能结束进程
  2. **PID 复用防护**：进程名与计划不符时拒绝动手
  3. 审计里留下 process.blocked / kill_failed 记录

用法：python tools/smoke_kill.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from appguard import cleaner, winapi  # noqa: E402
from appguard.audit import Auditor  # noqa: E402
from appguard.models import CleanupItem, Origin, ProcRole, RiskLevel  # noqa: E402

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def alive(pid: int) -> bool:
    """按 pid 判活 —— 注意这会被 **PID 复用**骗到（沙箱里 python.exe 很多，pid 回收极快）。"""
    return bool(winapi.process_details(pid).get("exe"))


def exited(proc: subprocess.Popen, timeout: float = 3.0) -> bool:
    """按 Popen 句柄判活 —— 权威且不受 PID 复用影响。

    用**阻塞 wait** 而不是 `poll()`：`TerminateProcess` 是异步的，进程"已不可 OpenProcess"
    与"内核对象已置信号"之间存在极短窗口，`poll()` 会在此窗口内误报"仍在运行"。
    """
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def spawn_victim() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(180)"],
        creationflags=_CREATE_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    auditor = Auditor(os.path.join(ROOT, ".appguard-dev", "audit.jsonl"))
    failures = 0

    # --- 1) 正常击杀 ---
    victim = spawn_victim()
    time.sleep(0.6)
    print(f"起了一个测试进程 pid={victim.pid}，存活={alive(victim.pid)}")
    item = CleanupItem(pid=victim.pid, name=os.path.basename(sys.executable),
                       software_id="sw-test", software="测试进程", role=ProcRole.HELPER,
                       origin=Origin.UNKNOWN, risk=RiskLevel.CAUTION, reason="受控验证")
    ok, err = cleaner._kill_with_guard(item, kill_tree=True)
    gone = winapi.wait_process_gone(victim.pid, timeout=2.0)
    dead = exited(victim)
    print(f"  击杀结果 ok={ok} err={err} 句柄判定已退出={dead} wait_gone={gone} "
          f"（按 pid 复查={not alive(victim.pid)}）")
    auditor.write("process.blocked" if ok and dead else "process.kill_failed",
                  pid=victim.pid, name=item.name, software_id="sw-test", role="helper",
                  rule="test:controlled", action="kill", result="ok" if ok and dead else "failed",
                  reason="受控验证（本脚本自起进程）", error=err)
    if not (ok and dead):
        failures += 1
        print("  ✗ 期望被结束")

    # --- 2) PID 复用防护：故意给错进程名 ---
    victim2 = spawn_victim()
    time.sleep(0.6)
    wrong = CleanupItem(pid=victim2.pid, name="definitely_not_this.exe", software_id="sw-test",
                        software="测试进程", role=ProcRole.HELPER, origin=Origin.UNKNOWN,
                        risk=RiskLevel.CAUTION, reason="PID 复用防护验证")
    ok2, err2 = cleaner._kill_with_guard(wrong, kill_tree=True)
    time.sleep(0.3)
    print(f"\nPID 复用防护：ok={ok2} err={err2} 未退出={not exited(victim2)}")
    if ok2 or exited(victim2):
        failures += 1
        print("  ✗ 期望被拒绝且进程存活")
    else:
        print("  ✓ 已拒绝，未误杀")
    cleaner.kill_process(victim2.pid, tree=True)
    winapi.wait_process_gone(victim2.pid, timeout=2.0)
    print(f"  清理测试进程 pid={victim2.pid} → 句柄判定已退出={exited(victim2)}")

    # --- 3) 审计留痕 ---
    rows = [r for r in auditor.tail(20) if r.rule == "test:controlled"]
    print(f"\n审计留痕：{len(rows)} 条")
    for row in rows:
        print(f"  #{row.seq} {row.event} pid={row.pid} result={row.result}")

    print("\n结论：" + ("全部通过 ✓" if failures == 0 else f"{failures} 项失败 ✗"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
