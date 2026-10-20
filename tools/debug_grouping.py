"""调试：软件归属（安装目录）解析。用于排查"某进程被归到别的软件"。

用法：python tools/debug_grouping.py
"""

from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from appguard import inventory as inv  # noqa: E402
from appguard import winapi  # noqa: E402


def main() -> int:
    exes: dict[int, str] = {}
    for row in winapi.list_raw_processes():
        details = winapi.process_details(row["pid"])
        if details.get("exe"):
            exes[row["pid"]] = details["exe"]

    entries = winapi.enum_uninstall_entries()
    pairs = [(inv._entry_dir(e), e) for e in entries]

    print("--- 可疑：卸载项推出的目录很短（可能是通用父目录） ---")
    for directory, entry in pairs:
        if directory and len(directory) < 45:
            print("   %-38s -> %s" % ((entry.get("DisplayName") or "")[:38], directory))

    print("\n--- 每个 exe 目录 → 被选中的祖先目录 ---")
    by_dir: dict[str, list[str]] = {}
    for exe in exes.values():
        by_dir.setdefault(os.path.dirname(exe), []).append(os.path.basename(exe))
    for exe_dir, names in sorted(by_dir.items(), key=lambda kv: kv[0].lower()):
        ancestors = sorted(
            [d for d, _e in pairs if d and inv._is_ancestor(d, exe_dir)],
            key=len, reverse=True)
        winner = ancestors[0] if ancestors else exe_dir
        flag = "  <-- 可疑" if ancestors and len(ancestors[0]) < len(exe_dir) - 12 else ""
        print(f"   {exe_dir}")
        print(f"      exes      : {', '.join(names[:6])}")
        print(f"      ancestors : {ancestors[:3] if ancestors else '-'}")
        print(f"      winner    : {winner}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
