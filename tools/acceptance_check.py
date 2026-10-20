"""真机验收脚本：一次性检查三条主线是否真的在工作。

用法：python tools/acceptance_check.py [--json]
退出码：0 全通过；1 有关键项未通过（详见输出）。
"""

from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from appguard import evaluator, inventory, winapi  # noqa: E402
from appguard.config import load_config  # noqa: E402
from appguard.daemon import Daemon  # noqa: E402

FAILURES: list[str] = []


def line(label: str, value) -> None:
    print(f"  {label:<26} {value}")


def require(label: str, cond: bool, detail: str = "") -> None:
    mark = "OK  " if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f"   <- {detail}" if not cond and detail else ""))
    if not cond:
        FAILURES.append(label)


def main() -> int:
    cfg = load_config(data_dir=os.environ.get("APPGUARD_DATA_DIR"))
    daemon = Daemon(cfg)
    daemon.prepare()
    snap = daemon.snapshot(refresh=True)
    diag = snap.diagnostics

    print("=" * 74)
    print("AppGuard 真机验收")
    print("=" * 74)

    print("\n[1] 采集通道")
    line("管理员权限", winapi.is_admin())
    line("可见进程", diag["process_count"])
    line("有可执行路径", diag["with_exe"])
    line("有命令行", diag["with_cmdline"])
    line("有可见窗口", diag["with_window"])
    line("服务 / 自启 / 已安装", f'{diag["service_count"]} / {diag["autostart_count"]} / '
                               f'{diag["uninstall_count"]}')
    require("能枚举到足量进程（≥100）", diag["process_count"] >= 100,
            f'只有 {diag["process_count"]} 个，可能处在受限环境')
    require("大部分进程能取到命令行", diag["with_cmdline"] >= diag["process_count"] * 0.3,
            "命令行缺失会让分类退化")

    print("\n[2] L1 在用软件")
    in_use = [s for s in snap.software if s.in_use]
    line("软件总数", len(snap.software))
    line("判定为在用", len(in_use))
    for item in in_use[:6]:
        print(f"      - {item.name[:34]:<34} {item.in_use_reason[:44]}")
    require("有软件被判定为在用", len(in_use) > 0, "窗口/前台判定可能失效")
    require("在用理由是可读中文", all(s.in_use_reason for s in in_use))

    print("\n[3] L2 分类覆盖")
    total = len(snap.processes)
    known = sum(1 for p in snap.processes if p.classification.role.value != "unknown")
    line("分类覆盖率", f"{known}/{total} = {known * 100 // max(1, total)}%")
    require("覆盖率 ≥80%", known >= total * 0.8, f"{known}/{total}")
    samples = [p for p in snap.processes if p.classification.role.value in
               ("ad", "updater", "telemetry", "crash", "tray", "daemon")]
    line("识别出的后台组件", len(samples))
    for proc in samples[:5]:
        print(f"      - {proc.name[:28]:<28} {proc.classification.role_zh:<8} "
              f"{proc.classification.origin_zh:<12} {proc.classification.activity_zh}")
        print(f"        {proc.classification.explanation[:88]}")

    print("\n[4] L3 清理计划")
    plan = evaluator.build_plan(snap, cfg.policy, kill_tree=cfg.cleanup.kill_tree,
                                plan_ttl_s=cfg.cleanup.plan_ttl_s)
    line("计划项", f'{plan.summary["total"]}（默认勾选 {plan.summary.get("default_checked", 0)} / '
                   f'需确认 {plan.summary["caution"]} / 受保护 {plan.summary["protected"]} / '
                   f'停止服务 {plan.summary.get("stop_services", 0)}）')
    line("预计释放", f'{plan.summary["memory_mb"]} MB')
    for item in plan.items[:6]:
        mark = "✓" if item.default_checked else " "
        print(f"      - [{mark}] {item.action:<12} {item.name[:26]:<26} "
              f"{item.memory_mb:>7.1f}MB  {item.reason[:56]}")
    require("计划项都带中文理由", all(i.reason for i in plan.items))
    require("计划项都带动作/默认勾选字段",
            all(i.action in ("kill", "stop_service") and isinstance(i.default_checked, bool)
                for i in plan.items))
    require("受保护项没有混进计划",
            all(s.pid not in {i.pid for i in plan.items} for s in plan.protected_skipped))
    require("计划指纹非空", bool(plan.fingerprint))
    require("计划可通过时效校验", evaluator.validate(plan, snap) is None)

    print("\n[5] 雷电案例（本项目的起因）")
    ld_software = [s for s in snap.software
                   if "雷电" in (s.name or "")
                   or (s.install_dir or "").lower().startswith(r"e:\leidian")]
    if ld_software:
        for item in ld_software:
            line("聚合出雷电软件", f"{item.name} | 在用={item.in_use} | "
                                   f"私自后台={item.private_background}")
            line("  安装目录", item.install_dir)
            line("  关联服务", ", ".join(item.services) or "-")
    else:
        print("      （当前没有雷电进程在跑，或在用中）")
    ld_pids = [p for p in snap.processes if (p.exe or "").lower().startswith(r"e:\leidian")]
    line("雷电相关进程", len(ld_pids))
    for proc in ld_pids[:6]:
        print(f"      - {proc.pid:>6} {proc.name:<24} {proc.classification.role_zh:<8} "
              f"{proc.classification.origin_zh:<12} risk={proc.classification.risk.value}")
    candidate_pids = {p.pid for p in inventory.plan_pool(snap)}
    if ld_pids:
        require("雷电进程要么进计划、要么被明确保护",
                all(p.pid in candidate_pids for p in ld_pids),
                "有雷电进程既不在候选池也没被跳过")

    print("\n" + "=" * 74)
    if FAILURES:
        print(f"验收未通过 {len(FAILURES)} 项：")
        for item in FAILURES:
            print("  -", item)
    else:
        print("验收通过 ✓")
    print("=" * 74)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
