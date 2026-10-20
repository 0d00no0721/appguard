"""L3 冒烟：清理计划 → 风险分级 → dry-run 执行 → 审计，并自检计划失效语义。

用法：python tools/smoke_l3.py
开发数据目录：<项目>/.appguard-dev（不碰 %ProgramData%）
"""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from appguard import cleaner, config, evaluator, inventory  # noqa: E402
from appguard.audit import Auditor  # noqa: E402
from appguard.models import Classification, Origin, ProcessInfo, ProcRole, RiskLevel  # noqa: E402


def main() -> int:
    data_dir = os.path.join(ROOT, ".appguard-dev")
    cfg = config.load_config(data_dir=data_dir)
    print("data_dir :", cfg.data_dir)
    print("config   :", cfg.config_path)
    print("dry_run  :", cfg.policy.dry_run, "| min_idle_s:", cfg.policy.min_idle_s,
          "| never_touch:", cfg.policy.never_touch_roles)
    for warning in cfg.warnings:
        print("  [warn]", warning)

    snap = inventory.collect(with_signature=False)
    print(f"\n软件 {len(snap.software)} 个 / 进程 {len(snap.processes)} 个")

    plan = evaluator.build_plan(snap, cfg.policy, kill_tree=cfg.cleanup.kill_tree,
                                plan_ttl_s=cfg.cleanup.plan_ttl_s,
                                max_items=cfg.cleanup.max_kill_batch)
    print(f"\n=== 清理计划 {plan.plan_id}（指纹 {plan.fingerprint}，{plan.expires_at} 过期）===")
    print("summary  :", json.dumps(plan.summary, ensure_ascii=False))
    if plan.items:
        for item in plan.items:
            print(f"  [{item.risk.value:<8}] {item.pid:>7} {item.name[:26]:<26} "
                  f"{item.memory_mb:>7.1f}MB  {item.software}")
            print(f"              理由：{item.reason}")
    else:
        print("  （没有可清理项）")
    print(f"  受保护/跳过 {len(plan.protected_skipped)} 项：")
    for skip in plan.protected_skipped[:12]:
        print(f"      {skip.pid:>7} {skip.name[:26]:<26} {skip.why}")

    auditor = Auditor(cfg.audit_path)
    print(f"\n=== 执行（dry_run={cfg.policy.dry_run}，默认只勾 safe）===")
    result = cleaner.execute(plan, snap, policy=cfg.policy, auditor=auditor,
                             kill_tree=cfg.cleanup.kill_tree)
    print(f"  结束 {len(result.killed)} 个 / 失败 {len(result.failed)} 个 / "
          f"释放 {result.freed_mb} MB")
    print(f"  审计序号区间：{result.audit_seq_from} → {result.audit_seq_to}")

    print(f"\n=== 执行（dry_run={cfg.policy.dry_run}，safe + caution 全选，验证执行链路）===")
    result2 = cleaner.execute(plan, snap, policy=cfg.policy, auditor=auditor,
                              kill_tree=cfg.cleanup.kill_tree,
                              include_risk=("safe", "caution"))
    print(f"  结束 {len(result2.killed)} 个 / 失败 {len(result2.failed)} 个 / "
          f"释放 {result2.freed_mb} MB")
    if result2.failed:
        for item in result2.failed[:5]:
            print(f"    失败 {item.pid} {item.name}：{item.error}")

    # ---------- 计划失效语义自检（接口规范 C7）----------
    print("\n=== 计划时效自检 ===")
    print("  正常计划      :", evaluator.validate(plan, snap), "（期望 None）")

    mutated = deepcopy(snap)
    fake = ProcessInfo(pid=999999, name="fake_updater.exe", exe=r"C:\fake\fake_updater.exe")
    fake.classification = Classification(role=ProcRole.UPDATER, origin=Origin.SERVICE,
                                         software_id="sw-not-in-use", risk=RiskLevel.SAFE)
    mutated.processes.append(fake)
    print("  目标集合变化后:", evaluator.validate(plan, mutated), "（期望 E_PLAN_STALE）")

    expired = deepcopy(plan)
    expired.expires_at = "2000-01-01T00:00:00+08:00"
    print("  过期计划      :", evaluator.validate(expired, snap), "（期望 E_PLAN_EXPIRED）")

    try:
        cleaner.execute(expired, snap, policy=cfg.policy, auditor=auditor)
        print("  执行过期计划  : 未被拦截 ✗")
    except cleaner.PlanError as exc:
        print("  执行过期计划  : 被拦截 →", exc.code, "✓")

    print("\n=== 审计尾部 ===")
    for row in auditor.tail(8):
        flag = " [dry-run]" if row.dry_run else ""
        print(f"  #{row.seq:<4} {row.ts[11:23]} {row.event:<20} pid={row.pid} "
              f"{(row.name or '')[:22]:<22} {row.action or ''} {row.result or ''}{flag}")

    print("\n=== 清理历史（从审计回放）===")
    for entry in cleaner.history(auditor, limit=3):
        print("  ", json.dumps(entry, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
