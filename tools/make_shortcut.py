#!/usr/bin/env python3
"""给 AppGuard 创建桌面快捷方式（Windows .lnk，pythonw 启动、无控制台）。"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "tools", "appguard_launcher.py")
ICON = os.path.join(ROOT, "appguard.ico")


def default_desktop() -> str:
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_DESKTOPDIRECTORY = 0x0010
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0:
            return buf.value
    except Exception:                                           # noqa: BLE001
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def pythonw_path() -> str:
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return candidate if os.path.isfile(candidate) else sys.executable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="创建 AppGuard 桌面快捷方式")
    parser.add_argument("--output", default="", help=".lnk 完整路径（默认桌面 AppGuard.lnk）")
    parser.add_argument("--name", default="AppGuard", help="快捷方式名称")
    args = parser.parse_args(argv)

    output = args.output or os.path.join(default_desktop(), args.name + ".lnk")
    output = os.path.abspath(output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    if not os.path.isfile(LAUNCHER):
        print("[x] launcher not found: " + LAUNCHER, file=sys.stderr)
        return 1
    try:
        import win32com.client
    except Exception as exc:                                    # noqa: BLE001
        print("[x] pywin32 is required: " + str(exc), file=sys.stderr)
        return 1
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(output)
    shortcut.TargetPath = pythonw_path()
    shortcut.Arguments = '"' + LAUNCHER + '"'
    shortcut.WorkingDirectory = ROOT
    shortcut.IconLocation = ICON
    shortcut.Description = "AppGuard - process ledger"
    shortcut.Save()
    if not os.path.isfile(output):
        print("[x] shortcut was not created: " + output, file=sys.stderr)
        return 1
    print("[ok] shortcut: " + output)
    print("     target: " + shortcut.TargetPath + " " + shortcut.Arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
