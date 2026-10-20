"""winapi 层冒烟测试：验证进程/窗口/服务/自启/卸载信息采集是否可用。

用法：  python tools/smoke_winapi.py
输出全部为 ASCII 标签，避免控制台编码问题。
"""

from __future__ import annotations

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from appguard import winapi  # noqa: E402


def timed(label, fn):
    t0 = time.time()
    try:
        value = fn()
        err = None
    except Exception as exc:  # noqa: BLE001
        value, err = None, f"{type(exc).__name__}: {exc}"
    ms = (time.time() - t0) * 1000
    print(f"[{ms:7.0f} ms] {label:<34} {'ERR ' + err if err else ''}")
    return value


def main() -> int:
    print("=== AppGuard winapi smoke ===")
    print("admin            :", winapi.is_admin())
    print("uptime_hours     :", round(winapi.uptime_seconds() / 3600, 1))

    raw = timed("list_raw_processes", winapi.list_raw_processes) or []
    print("    processes    :", len(raw))
    if not raw:
        return 1

    # 细节采集：取内存最大的 40 个做样本，另外全量只取 exe/session
    t0 = time.time()
    detailed = []
    for row in raw:
        d = winapi.process_details(row["pid"], want_integrity=True, want_cmdline=True)
        row.update(d)
        detailed.append(row)
    print(f"[{(time.time() - t0) * 1000:7.0f} ms] process_details x{len(raw)}")
    with_exe = sum(1 for r in detailed if r.get("exe"))
    print("    with_exe     :", with_exe, f"({with_exe * 100 // max(1, len(detailed))}%)")
    with_int = sum(1 for r in detailed if r.get("integrity"))
    print("    with_integr  :", with_int)
    with_cmd = sum(1 for r in detailed if r.get("cmdline"))
    print("    with_cmdline :", with_cmd, f"({with_cmd * 100 // max(1, len(detailed))}%)")

    wmi = timed("wmi_process_info", winapi.wmi_process_info) or {}
    print("    wmi rows     :", len(wmi))
    if wmi:
        sample_pid = next(iter(wmi))
        print("    sample cmd   :", (wmi[sample_pid].get("cmdline") or "")[:90])

    wins = timed("visible_windows", winapi.visible_windows) or {}
    print("    pids w/ win  :", len(wins), "total titles:", sum(len(v) for v in wins.values()))
    fg = winapi.foreground_pid()
    fg_name = next((r["name"] for r in detailed if r["pid"] == fg), None)
    print("    foreground   :", fg, fg_name)
    print("    idle_seconds :", round(winapi.last_input_seconds(), 1))

    exe_sample = [r["exe"] for r in detailed if r.get("exe")][:3]
    for path in exe_sample:
        vi = winapi.file_version_info(path)
        print("    verinfo      :", os.path.basename(path), "|", (vi.get("company") or "-")[:26],
              "|", (vi.get("description") or "-")[:34])
    sig = timed(f"authenticode_many x{len(exe_sample)}", lambda: winapi.authenticode_many(exe_sample))
    for path, (ok, signer) in (sig or {}).items():
        print("    signature    :", os.path.basename(path), "signed =", ok, "|", (signer or "-")[:40])

    svcs = timed("enum_services", winapi.enum_services) or []
    print("    services     :", len(svcs))
    for s in svcs[:3]:
        print("       -", s.name, "|", s.state, "|", s.start_type, "|", (s.display_name or "")[:30])
    for probe in ("LDPlayerSvr", "LDRemoteSvc", "Schedule", "Winmgmt"):
        hit = next((s for s in svcs if s.name.lower() == probe.lower()), None)
        if hit:
            print(f"    probe {probe:<12}: state={hit.state:<8} start={hit.start_type:<8} "
                  f"path={(hit.binary_path or '')[:60]}")

    run = timed("enum_registry_run", winapi.enum_registry_run) or []
    print("    run keys     :", len(run))
    for e in run[:3]:
        print("       -", e.name, "=", (e.command or "")[:60])

    folder = timed("enum_startup_folders", winapi.enum_startup_folders) or []
    print("    startup dir  :", len(folder))

    tasks = timed("enum_scheduled_tasks", winapi.enum_scheduled_tasks) or []
    print("    tasks        :", len(tasks))
    for t in tasks[:3]:
        print("       -", t.name, "|", (t.command or "")[:60])

    unins = timed("enum_uninstall_entries", winapi.enum_uninstall_entries) or []
    print("    uninstall    :", len(unins))
    for u in unins[:3]:
        print("       -", (u.get("DisplayName") or "")[:40], "|", (u.get("Publisher") or "-")[:26])

    print("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
