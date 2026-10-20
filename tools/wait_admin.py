#!/usr/bin/env python3
"""Wait until the running guard reports elevated=true (used by install.bat).

Exit codes: 0 = an elevated guard is reachable; 1 = timeout / not elevated.
Messages are ASCII on purpose (installer console encoding).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
DEFAULT_DATA = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "AppGuard")
DEFAULT_PIPE = r"\\.\pipe\AppGuard.v1"


def _ensure_src() -> None:
    if SRC not in sys.path:
        sys.path.insert(0, SRC)


def _status(data_dir: str, pipe_name: str) -> dict | None:
    _ensure_src()
    from appguard import ipc
    client = None
    try:
        client = ipc.make_client(data_dir, pipe_name, timeout=2.0, prefer="auto")
        return client.call("status.get")
    except Exception:                                           # noqa: BLE001
        return None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:                               # noqa: BLE001
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wait for elevated AppGuard daemon")
    parser.add_argument("--data-dir", default=DEFAULT_DATA)
    parser.add_argument("--pipe-name", default=DEFAULT_PIPE)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args(argv)

    deadline = time.time() + max(2.0, args.timeout)
    last_state = "unreachable"
    warned = False
    while time.time() < deadline:
        status = _status(args.data_dir, args.pipe_name)
        if status is not None:
            if status.get("elevated"):
                health = status.get("health") or "?"
                print("[ok] elevated guard is running (health=" + str(health) + ")")
                return 0
            last_state = "running as normal user (elevated=false)"
            if not warned:
                print("[..] guard reachable but not elevated; waiting for admin task ...")
                warned = True
        time.sleep(0.8)
    print("[x] timed out waiting for an elevated guard: " + last_state)
    print("    check: schtasks /query /tn AppGuard")
    print("    log  : %LOCALAPPDATA%\\AppGuard\\launcher.log")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
