"""L1/L2 冒烟：软件清单聚合 + 进程分类解释。

用法：python tools/smoke_l1l2.py [--json out.json]
"""

from __future__ import annotations

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from appguard import inventory  # noqa: E402
from appguard.models import ACTIVITY_ZH, ORIGIN_ZH, ROLE_ZH  # noqa: E402


def main() -> int:
    snap = inventory.collect(with_signature=False)
    diag = snap.diagnostics
    print("=== diagnostics ===")
    for key, value in diag.items():
        print(f"  {key:<18} {value}")

    print("\n=== L1 软件清单（在用优先，前 25 个）===")
    print(f"{'状态':<4} {'私自':>4} {'内存MB':>9}  {'软件':<30} {'发布者':<16} {'进程':>4} {'服务':>4} {'自启':>4}")
    for sw in snap.software[:25]:
        state = "在用" if sw.in_use else "未用"
        print(f"{state:<4} {sw.private_background:>4} {sw.memory_mb:>9.1f}  {(sw.name or '')[:30]:<30} "
              f"{(sw.publisher or '-')[:16]:<16} {len(sw.processes):>4} {len(sw.services):>4} "
              f"{len(sw.autostart):>4}")
        if sw.in_use:
            print(f"        ↳ 在用依据：{sw.in_use_reason}")
    print(f"  ... 共 {len(snap.software)} 个软件")

    print("\n=== L2 进程分类（按内存前 30）===")
    for proc in sorted(snap.processes, key=lambda p: -(p.memory_mb or 0))[:30]:
        cls = proc.classification
        print(f"{proc.pid:>7} {proc.name[:26]:<26} {ROLE_ZH.get(cls.role.value, cls.role.value):<8} "
              f"{ACTIVITY_ZH.get(cls.activity.value, ''):<10} {ORIGIN_ZH.get(cls.origin.value, ''):<14} "
              f"{proc.memory_mb:>8.1f}MB  conf={cls.confidence:.2f}")
        print(f"        ↳ {cls.explanation}")

    print("\n=== L3 清理候选（未在用软件的私自后台组件）===")
    candidates = inventory.candidates_for_cleanup(snap)
    for proc in candidates:
        cls = proc.classification
        print(f"  {proc.pid:>7} {proc.name[:26]:<26} {cls.role.value:<9} {proc.memory_mb:>8.1f}MB  "
              f"[{cls.origin.value}]")
    print(f"  合计 {len(candidates)} 个候选，"
          f"{sum(p.memory_mb or 0 for p in candidates):.1f} MB")

    if "--json" in sys.argv:
        path = sys.argv[sys.argv.index("--json") + 1]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(snap.to_dict(), handle, ensure_ascii=False, indent=2)
        print(f"\n快照已写入 {path}")

    # 分类覆盖率自检（这是接口规范 C4 的骨架）
    total = len(snap.processes)
    known = sum(1 for p in snap.processes if p.classification.role.value != "unknown")
    print(f"\n分类覆盖率：{known}/{total} = {known * 100 // max(1, total)}%（role != unknown）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
