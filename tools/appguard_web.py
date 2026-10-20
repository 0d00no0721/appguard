#!/usr/bin/env python3
"""AppGuard 浏览器启动器：守护 + 本地网关 + 复用已开着的 Edge/Chrome。

用法（正常双击 `AppGuard.bat`）：
    pythonw tools\\appguard_web.py
    python  tools\\appguard_web.py --data-dir .appguard-dev --dry-run

行为：
  1. 守护不可达时：若已安装 `AppGuard` 登录任务，先尝试启动该任务（管理员守护）；
     只有没有任务且没有存活守护时，才以普通用户后台启动（**只读/演练模式**）；
  2. 网关不可用时，在后台启动 `guardctl web`，端点写入用户目录；
  3. 若已有 `AppGuard · …` 窗口，直接聚焦，不再开第二个；
  4. 否则用 `msedge --app=<url>` 打开无地址栏窗口（复用现有 Edge 配置/进程），
     找不到 Edge 时回退 Chrome，再回退默认浏览器。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = os.environ.get("APPGUARD_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
GUARDCTL = os.path.join(ROOT, "tools", "guardctl.py")
DEFAULT_DATA = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "AppGuard")
LOG_PATH = os.environ.get("APPGUARD_LOG_PATH") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "AppGuard", "launcher.log")

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
MUTEX_NAME = "Local\\AppGuard.WebLauncher.v1"
PIPE_NAME = r"\\.\pipe\AppGuard.v1"
TASK_NAME = "AppGuard"


def normalize_pipe_name(name: str) -> str:
    """CLI 的 --pipe-name 可以是 `AppGuard.v1` 或完整 `\\\\.\\pipe\\...`。"""
    text = str(name or "").strip()
    if not text:
        return PIPE_NAME
    if text.startswith("\\\\"):
        return text
    return "\\\\.\\pipe\\" + text.lstrip("\\")


def console_python() -> str:
    """pythonw 下 sys.stdout 为 None；frozen 下子进程就是 AppGuard.exe。"""
    if FROZEN:
        return sys.executable
    exe = sys.executable or "python"
    if os.path.basename(exe).lower().startswith("pythonw"):
        candidate = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(candidate):
            return candidate
    return exe


def _ensure_src() -> None:
    if FROZEN:
        return
    if SRC not in sys.path:
        sys.path.insert(0, SRC)


def log(message: str) -> None:
    line = time.strftime("%Y-%m-%dT%H:%M:%S%z") + " " + str(message)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    try:
        print(message, flush=True)
    except Exception:                                           # noqa: BLE001
        pass


def _run_hidden(argv: list[str], timeout_s: float = 8.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, errors="replace",
                          timeout=timeout_s, creationflags=CREATE_NO_WINDOW)


def scheduled_task_exists() -> bool:
    try:
        result = _run_hidden(["schtasks", "/query", "/tn", TASK_NAME])
        return result.returncode == 0
    except Exception as exc:                                    # noqa: BLE001
        log(f"scheduled task query failed: {exc}")
        return False


def run_scheduled_task() -> bool:
    try:
        result = _run_hidden(["schtasks", "/run", "/tn", TASK_NAME], timeout_s=12.0)
        if result.returncode != 0:
            log("scheduled task start failed: " +
                (result.stderr or result.stdout or "").strip()[:300])
            return False
        log("scheduled task start requested")
        return True
    except Exception as exc:                                    # noqa: BLE001
        log(f"scheduled task start raised: {exc}")
        return False


def default_endpoint(data_dir: str) -> str:
    _ensure_src()
    from appguard import web
    if os.path.abspath(data_dir).lower() == os.path.abspath(DEFAULT_DATA).lower():
        return web.default_endpoint_path()
    return os.path.join(data_dir, "web-endpoint.json")


def daemon_alive(data_dir: str, pipe_name: str) -> tuple[bool, str]:
    """用最便宜的 ping 探测守护；连接是短超时，失败重试 3 次。"""
    _ensure_src()
    from appguard import ipc
    detail = ""
    for _attempt in range(3):
        client = None
        try:
            client = ipc.make_client(data_dir, pipe_name, timeout=2.0, prefer="auto")
            client.call("ping")
            return True, "ok"
        except Exception as exc:                                # noqa: BLE001
            detail = str(exc)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:                               # noqa: BLE001
                    pass
        time.sleep(0.4)
    return False, detail


def pipe_present(pipe_name: str) -> bool:
    """管道文件存在 = 守护大概率还活着；此时不要重复启动第二个守护。"""
    try:
        path = pipe_name if pipe_name.startswith("\\\\") else "\\\\.\\pipe\\" + pipe_name
        return os.path.exists(path)
    except Exception:                                           # noqa: BLE001
        return False


def daemon_process_alive(data_dir: str) -> bool:
    """读 daemon.pid；进程仍活着就绝不重复启动。"""
    path = os.path.join(data_dir, "daemon.pid")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            pid = int((json.load(handle) or {}).get("pid") or 0)
    except Exception:                                           # noqa: BLE001
        return False
    if pid <= 0:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                                ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259            # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:                                           # noqa: BLE001
        return False


def spawn_background(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        args, cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS)


def guard_argv(data_dir: str, pipe_name: str | None, mode: str = "serve") -> list[str]:
    if FROZEN:
        argv = [sys.executable, "daemon" if mode == "serve" else mode]
        if os.path.abspath(data_dir).lower() != os.path.abspath(DEFAULT_DATA).lower():
            argv += ["--data-dir", data_dir]
        if pipe_name:
            argv += ["--pipe-name", pipe_name]
        return argv
    argv = [console_python(), GUARDCTL]
    if os.path.abspath(data_dir).lower() != os.path.abspath(DEFAULT_DATA).lower():
        argv += ["--data-dir", data_dir]
    if pipe_name:
        argv += ["--pipe-name", pipe_name]
    argv.append("serve" if mode == "serve" else mode)
    return argv


def cli_argv(data_dir: str, *args: str) -> list[str]:
    """构造 CLI 子进程参数（frozen 下走 AppGuard.exe cli ...）。"""
    if FROZEN:
        return [sys.executable, "cli"] + list(args)
    argv = [console_python(), GUARDCTL]
    if os.path.abspath(data_dir).lower() != os.path.abspath(DEFAULT_DATA).lower():
        argv += ["--data-dir", data_dir]
    return argv + list(args)


def wait_for(predicate, timeout_s: float, interval_s: float = 0.4):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except Exception as exc:                                # noqa: BLE001
            last = exc
        time.sleep(interval_s)
    if last is not None:
        raise last
    return None


def read_endpoint(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:                                           # noqa: BLE001
        return None


def gateway_health(endpoint: dict) -> dict | None:
    url = str(endpoint.get("url") or "")
    token = str(endpoint.get("token") or "")
    base = url.split("#", 1)[0].rstrip("/")
    if not base or not token:
        return None
    request = urllib.request.Request(
        base + "/api/health", headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=2.0) as response:
        payload = json.load(response)
    return payload if isinstance(payload, dict) else None


def gateway_status(endpoint: dict) -> dict | None:
    url = str(endpoint.get("url") or "")
    token = str(endpoint.get("token") or "")
    base = url.split("#", 1)[0].rstrip("/")
    if not base or not token:
        return None
    data = json.dumps({}).encode("utf-8")
    request = urllib.request.Request(
        base + "/api/status.get", data=data, method="POST",
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=4.0) as response:
        payload = json.load(response)
    result = payload.get("result") if isinstance(payload, dict) else None
    return result if isinstance(result, dict) else None


def ensure_daemon(data_dir: str, pipe_name: str, timeout_s: float,
                  use_task: bool = True) -> tuple[bool, str]:
    ok, detail = daemon_alive(data_dir, pipe_name)
    if ok:
        log("daemon reachable; reuse")
        return True, "reused"
    if daemon_process_alive(data_dir):
        log("daemon.pid is alive but IPC probe failed; keep process (view-only reconnect)")
        return False, "process-present"
    if pipe_present(pipe_name):
        log("daemon pipe exists but IPC probe failed; keep process (view-only reconnect)")
        return False, "pipe-present"
    if use_task and scheduled_task_exists():
        log("scheduled task found; trying to start the elevated guard")
        if run_scheduled_task():
            try:
                wait_for(lambda: daemon_alive(data_dir, pipe_name)[0], timeout_s)
                log("elevated guard started via scheduled task")
                return True, "via-task"
            except Exception as exc:                            # noqa: BLE001
                log(f"elevated guard did not become reachable: {exc}")
                return False, "task-failed"
        return False, "task-failed"
    log("no guard and no scheduled task; starting a normal-user guard (view-only)")
    spawn_background(guard_argv(data_dir, pipe_name, "serve") + ["--transport", "auto"])
    try:
        wait_for(lambda: daemon_alive(data_dir, pipe_name)[0], timeout_s)
        return True, "started"
    except Exception as exc:                                    # noqa: BLE001
        log(f"normal guard start failed: {exc}")
        return False, str(exc)


def ensure_gateway(data_dir: str, pipe_name: str, endpoint_path: str,
                   port: int, timeout_s: float) -> tuple[dict, str]:
    endpoint = read_endpoint(endpoint_path)
    if endpoint:
        try:
            health = gateway_health(endpoint)
            if health is not None:
                return endpoint, "reused"
        except Exception:                                       # noqa: BLE001
            pass
    log("Web 网关未运行，正在后台启动 …")
    argv = guard_argv(data_dir, pipe_name, "web") + [
        "--port", str(port), "--endpoint", endpoint_path]
    spawn_background(argv)

    def ready() -> dict | None:
        data = read_endpoint(endpoint_path)
        if not data:
            return None
        try:
            gateway_health(data)
        except Exception:                                       # noqa: BLE001
            return None
        return data

    endpoint = wait_for(ready, timeout_s)
    if not endpoint:
        raise RuntimeError(f"Web 网关未在 {timeout_s:.0f}s 内就绪：{endpoint_path}")
    return endpoint, "started"


def find_browser(prefer: str) -> str | None:
    candidates: list[str] = []
    if prefer in ("auto", "edge"):
        candidates += [
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                         "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                         "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(
                            hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe") as key:
                        candidates.append(str(winreg.QueryValueEx(key, "")[0]))
                except OSError:
                    pass
        except Exception:                                       # noqa: BLE001
            pass
    if prefer in ("auto", "chrome"):
        candidates += [
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                         "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                         "Google", "Chrome", "Application", "chrome.exe"),
        ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def focus_existing() -> bool:
    """找到已打开的 AppGuard 窗口并前置。"""
    try:
        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]

        matches: list[int] = []

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            if title.startswith("AppGuard ·") or title.startswith("AppGuard -"):
                matches.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(WNDENUMPROC(callback), 0)
        if matches:
            hwnd = matches[0]
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
    except Exception:                                           # noqa: BLE001
        return False
    return False


def open_window(url: str, browser: str | None, dry_run: bool) -> str:
    if focus_existing():
        return "focused"
    if browser:
        params = ('--app="' + url + '" --no-first-run --no-default-browser-check '
                  '--window-size=1440,960')
        if dry_run:
            log("（dry-run）" + browser + " " + params)
            return "dry-run"
        try:
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            shell32.ShellExecuteW.argtypes = [
                wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
            shell32.ShellExecuteW.restype = ctypes.c_void_p
            code = int(shell32.ShellExecuteW(None, "open", browser,
                                             params, None, 1) or 0)
            if code > 32:
                return "browser"
            log(f"ShellExecute browser failed: {code}")
        except Exception as exc:                                # noqa: BLE001
            log(f"ShellExecute browser raised: {exc}")
        argv = [browser, "--app=" + url, "--no-first-run",
                "--no-default-browser-check", "--window-size=1440,960"]
        subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True,
                         creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS)
        return "browser"
    if dry_run:
        log("（dry-run）默认浏览器：" + url)
        return "dry-run"
    import webbrowser
    webbrowser.open(url)
    return "default-browser"


def acquire_mutex():
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        already = kernel32.GetLastError() == 183
        return handle, already
    except Exception:                                           # noqa: BLE001
        return None, False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AppGuard 浏览器启动器")
    parser.add_argument("--data-dir", default=DEFAULT_DATA)
    parser.add_argument("--pipe-name", default="")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--port", type=int, default=0, help="0=随机端口")
    parser.add_argument("--browser", choices=["auto", "edge", "chrome", "none"], default="auto")
    parser.add_argument("--no-open", action="store_true", help="只确保服务，不打开浏览器")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")
    parser.add_argument("--require-admin", action="store_true",
                        help="守护不是管理员就返回 2（不打开窗口）")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    _ensure_src()
    pipe_name = normalize_pipe_name(args.pipe_name or PIPE_NAME)
    endpoint_path = args.endpoint or default_endpoint(args.data_dir)
    if not args.quiet:
        log("launcher start: data_dir=" + args.data_dir + " endpoint=" + endpoint_path)
    mutex, already = acquire_mutex()
    if already and not args.quiet:
        log("检测到另一个启动器实例；将复用已运行的网关/窗口。")

    endpoint = read_endpoint(endpoint_path)
    health = None
    if endpoint:
        try:
            health = gateway_health(endpoint)
        except Exception:                                       # noqa: BLE001
            health = None
    if health and health.get("daemon"):
        ok, daemon_state = True, "via-gateway"
    else:
        ok, daemon_state = ensure_daemon(args.data_dir, pipe_name, args.timeout)
    endpoint, gateway_state = ensure_gateway(
        args.data_dir, pipe_name, endpoint_path, args.port, args.timeout)
    url = str(endpoint.get("url") or "")
    if "#token=" not in url:
        url = url.split("#", 1)[0] + "#token=" + str(endpoint.get("token") or "")
    status = None
    try:
        status = gateway_status(endpoint)
    except Exception as exc:                                    # noqa: BLE001
        log(f"gateway status failed: {exc}")
    elevated = bool(status.get("elevated")) if status else False
    if args.require_admin and not elevated:
        log("require-admin: the reachable guard is not elevated; aborting")
        if mutex is not None:
            try:
                ctypes.windll.kernel32.CloseHandle(mutex)
            except Exception:                                   # noqa: BLE001
                pass
        return 2
    browser = find_browser(args.browser) if args.browser != "none" else None
    action = "none"
    if not args.no_open:
        action = open_window(url, browser, args.dry_run)
    frontend = ""
    try:
        health = gateway_health(endpoint) or {}
        frontend = str(health.get("frontend") or "")
    except Exception:                                           # noqa: BLE001
        pass
    payload = {
        "ok": True,
        "url": url,
        "endpoint": endpoint_path,
        "daemon": "ok" if ok else daemon_state,
        "elevated": elevated,
        "gateway": gateway_state,
        "browser": browser or "",
        "action": action,
        "frontend": frontend,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif not args.quiet:
        log(f"AppGuard 已就绪：{url.split('#', 1)[0]}")
        log(f"  守护 {daemon_state} · 网关 {gateway_state} · 动作 {action}")
        if browser:
            log(f"  浏览器：{browser}")
    if mutex is not None:
        try:
            ctypes.windll.kernel32.CloseHandle(mutex)
        except Exception:                                       # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
