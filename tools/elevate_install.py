#!/usr/bin/env python3
"""以管理员身份运行 install.bat（UAC）。

由 AppGuard-admin.bat 调用；不自己实现提权流程，只调用 ShellExecuteW("runas")。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.dirname(os.path.abspath(__file__))
INSTALL = os.path.join(TOOLS, "install.bat")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AppGuard install.bat elevated")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not os.path.isfile(INSTALL):
        print("[x] install.bat not found: " + INSTALL, file=sys.stderr)
        return 1
    if args.dry_run:
        print("ShellExecuteW runas: cmd.exe /c \"\"" + INSTALL + "\"\"  (cwd=" + ROOT + ")")
        return 0
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.ShellExecuteW.argtypes = [
            wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        params = '/c ""' + INSTALL + '""'
        result = shell32.ShellExecuteW(None, "runas", "cmd.exe", params, ROOT, 1)
        code = int(result or 0)
        if code <= 32:
            print("[x] ShellExecuteW failed: " + str(code), file=sys.stderr)
            return 1
        print("[ok] requested elevation; complete the UAC prompt.")
        return 0
    except Exception as exc:                                    # noqa: BLE001
        print("[x] elevation failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
