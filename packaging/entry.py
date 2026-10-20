#!/usr/bin/env python3
"""AppGuard 冻结入口（PyInstaller onedir）。

对外只暴露一个 AppGuard.exe，多模式：
    AppGuard.exe                    托盘启动器（默认）
    AppGuard.exe daemon             守护（登录任务用；等价 guardctl serve）
    AppGuard.exe web ...            Web 网关
    AppGuard.exe cli ...            诊断 CLI
    AppGuard.exe install --quiet    安装器调用：初始化 + 注册/启动登录任务
    AppGuard.exe uninstall --quiet  卸载器调用：停守护 + 删除登录任务
    AppGuard.exe shortcut           创建桌面快捷方式
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = (Path(sys.executable).resolve().parent if FROZEN
        else Path(__file__).resolve().parent.parent)
MEIPASS = Path(getattr(sys, "_MEIPASS", ROOT))
WEB_ROOT = MEIPASS / "web"
SRC = ROOT / "src"
TOOLS = ROOT / "tools"

os.environ.setdefault("APPGUARD_ROOT", str(ROOT))
os.environ.setdefault("APPGUARD_WEB_ROOT", str(WEB_ROOT))
os.environ.setdefault("APPGUARD_FROZEN", "1" if FROZEN else "0")
if not FROZEN:
    for path in (str(SRC), str(TOOLS)):
        if path not in sys.path:
            sys.path.insert(0, path)

VERSION = "0.4.0"
TASK_NAME = "AppGuard"
PIPE_NAME = r"\\.\pipe\AppGuard.v1"
DEFAULT_DATA = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "AppGuard")
LOCAL_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AppGuard")
LOG_PATH = os.path.join(LOCAL_DIR, "appguard.log")
CREATE_NO_WINDOW = 0x08000000
_LOG_FILE = None


def _setup_stdio() -> None:
    global _LOG_FILE
    if not FROZEN and sys.stdout is not None and sys.stderr is not None:
        return
    try:
        os.makedirs(LOCAL_DIR, exist_ok=True)
        stream = open(LOG_PATH, "a", encoding="utf-8", newline="\n")
        _LOG_FILE = stream
        if sys.stdout is None or FROZEN:
            sys.stdout = stream
        if sys.stderr is None or FROZEN:
            sys.stderr = stream
    except OSError:
        pass


def _log(message: str) -> None:
    print(message, flush=True)
    if _LOG_FILE is not None:
        return
    try:
        os.makedirs(LOCAL_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(time.strftime("%Y-%m-%dT%H:%M:%S%z") + " " + message + "\n")
    except OSError:
        pass


def _run_cli(args: list[str]) -> int:
    from appguard.cli import main
    return int(main(args))


def _run(args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, errors="replace", timeout=timeout,
                          creationflags=CREATE_NO_WINDOW)


def _daemon_alive() -> bool:
    from appguard_web import daemon_alive
    try:
        return bool(daemon_alive(DEFAULT_DATA, PIPE_NAME)[0])
    except Exception:                                           # noqa: BLE001
        return False


def _wait_daemon(timeout_s: float = 30.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _daemon_alive():
            return True
        time.sleep(0.5)
    return False


def cmd_install(argv: list[str]) -> int:
    _log("install: init data dir")
    code = _run_cli(["init"])
    if code != 0:
        _log(f"install: init failed ({code})")
        return code

    _log("install: stop an old guard")
    try:
        _run_cli(["stop"])
    except Exception as exc:                                    # noqa: BLE001
        _log(f"install: stop failed (ignored): {exc}")
    time.sleep(1.0)

    task_cmd = '"' + sys.executable + '" daemon'
    create = _run(["schtasks", "/create", "/tn", TASK_NAME, "/tr", task_cmd,
                   "/sc", "onlogon", "/rl", "highest", "/f"], timeout=30.0)
    if create.returncode != 0:
        _log("install: schtasks create failed: " +
             (create.stderr or create.stdout or "").strip()[:400])
        return 1

    run = _run(["schtasks", "/run", "/tn", TASK_NAME], timeout=30.0)
    if run.returncode != 0:
        _log("install: schtasks run failed: " +
             (run.stderr or run.stdout or "").strip()[:400])
        return 1

    if not _wait_daemon(30.0):
        _log("install: task started but elevated guard did not become reachable")
        return 1
    _log("install: elevated guard is running")
    return 0


def cmd_uninstall(argv: list[str]) -> int:
    _log("uninstall: stop guard")
    try:
        _run_cli(["stop"])
    except Exception as exc:                                    # noqa: BLE001
        _log(f"uninstall: stop failed (ignored): {exc}")
    time.sleep(0.8)

    _run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], timeout=20.0)

    endpoint_path = os.path.join(LOCAL_DIR, "web-endpoint.json")
    try:
        with open(endpoint_path, "r", encoding="utf-8") as handle:
            endpoint = json.load(handle)
        pid = int(endpoint.get("pid") or 0)
        if pid > 0 and pid != os.getpid():
            os.kill(pid, signal.SIGTERM)
    except Exception:                                           # noqa: BLE001
        pass
    try:
        if os.path.exists(endpoint_path):
            os.remove(endpoint_path)
    except OSError:
        pass
    _log("uninstall: done")
    return 0


def cmd_shortcut(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="AppGuard.exe shortcut")
    parser.add_argument("--output", default="")
    parser.add_argument("--name", default="AppGuard")
    args = parser.parse_args(argv)
    try:
        import win32com.client
    except Exception as exc:                                    # noqa: BLE001
        _log("shortcut: pywin32 unavailable: " + str(exc))
        return 1
    if args.output:
        output = os.path.abspath(args.output)
    else:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        output = os.path.join(desktop, args.name + ".lnk")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(output)
    shortcut.TargetPath = sys.executable
    shortcut.Arguments = ""
    shortcut.WorkingDirectory = str(ROOT)
    icon = ROOT / "appguard.ico"
    if icon.is_file():
        shortcut.IconLocation = str(icon)
    shortcut.Description = "AppGuard - process ledger"
    shortcut.Save()
    _log("shortcut: " + output)
    return 0


def _split_global(rest: list[str]) -> tuple[list[str], list[str]]:
    """把 CLI 全局参数挪到子命令前（daemon/web 模式需要）。"""
    globals_: list[str] = []
    remaining: list[str] = []
    takes_value = {"--data-dir", "--pipe-name", "--config"}
    flags = {"--local", "--require-daemon", "--pipe", "--json", "--quiet"}
    i = 0
    while i < len(rest):
        token = rest[i]
        if token in takes_value and i + 1 < len(rest):
            globals_.extend([token, rest[i + 1]])
            i += 2
            continue
        if token in flags:
            globals_.append(token)
            i += 1
            continue
        remaining.append(token)
        i += 1
    return globals_, remaining


def main(argv: list[str] | None = None) -> int:
    _setup_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--version", "-V"):
        _log(VERSION)
        return 0

    if not argv or argv[0].startswith("-"):
        from appguard_launcher import main as launcher_main
        return int(launcher_main(argv))

    mode, rest = argv[0], argv[1:]
    if mode == "daemon":
        global_opts, serve_args = _split_global(rest)
        return _run_cli(global_opts + ["serve", "--transport", "auto"] + serve_args)
    if mode == "web":
        global_opts, web_args = _split_global(rest)
        return _run_cli(global_opts + ["web"] + web_args)
    if mode == "cli":
        return _run_cli(rest)
    if mode == "install":
        return cmd_install(rest)
    if mode == "uninstall":
        return cmd_uninstall(rest)
    if mode == "shortcut":
        return cmd_shortcut(rest)
    if mode in ("--help", "-h"):
        _log("usage: AppGuard.exe [daemon|web|cli|install|uninstall|shortcut]")
        return 0

    # 兼容 `AppGuard.exe status/stop/...`：直接当 CLI 命令。
    return _run_cli(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:                                       # noqa: BLE001
        import traceback
        try:
            traceback.print_exc()
        except Exception:                                       # noqa: BLE001
            pass
        raise SystemExit(1)
