#!/usr/bin/env python3
"""AppGuard 单入口启动器（托盘 + 服务 + Edge 窗口）。

一个进程负责：
  1. 确保守护/网关可用（复用 appguard_web.py 的探测/启动逻辑）；
  2. 打开/聚焦 Edge 的 AppGuard 应用窗口；
  3. 常驻系统托盘；
  4. 监视 AppGuard 窗口关闭 —— 按设置「最小化到托盘」或「退出并停止服务」；
  5. 托盘菜单提供：打开界面、以管理员安装/重启、创建快捷方式、退出。

用户只需要双击 AppGuard.bat（或未来打包后的 AppGuard.exe）。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
import time
from ctypes import wintypes

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("APPGUARD_ROOT") or os.path.dirname(TOOLS)
SRC = os.path.join(ROOT, "src")
GUARDCTL = os.path.join(TOOLS, "guardctl.py")
ELEVATE = os.path.join(TOOLS, "elevate_install.py")
MAKE_SHORTCUT = os.path.join(TOOLS, "make_shortcut.py")
FROZEN = bool(getattr(sys, "frozen", False))
WEB_ROOT = os.environ.get("APPGUARD_WEB_ROOT") or os.path.join(ROOT, "web")
DEFAULT_DATA = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "AppGuard")
PIPE_NAME = r"\\.\pipe\AppGuard.v1"
LOCAL_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AppGuard")
PREF_PATH = os.path.join(LOCAL_DIR, "launcher.json")
LOG_PATH = os.environ.get("APPGUARD_LOG_PATH") or os.path.join(LOCAL_DIR, "launcher.log")
MUTEX_NAME = "Local\\AppGuard.TrayLauncher.v1"
CREATE_NO_WINDOW = 0x08000000
WM_CLOSE = 0x0010
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import appguard_web as web  # noqa: E402


def log(message: str) -> None:
    line = time.strftime("%Y-%m-%dT%H:%M:%S%z") + " " + str(message)
    try:
        os.makedirs(LOCAL_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def load_prefs() -> dict:
    try:
        with open(PREF_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            data.setdefault("close_action", "tray")
            return data
    except Exception:                                           # noqa: BLE001
        pass
    return {"close_action": "tray"}


def save_prefs(prefs: dict) -> None:
    try:
        os.makedirs(LOCAL_DIR, exist_ok=True)
        tmp = PREF_PATH + ".writing"
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(prefs, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, PREF_PATH)
    except OSError as exc:
        log("save prefs failed: " + str(exc))


def _user32():
    user32 = ctypes.windll.user32
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    return user32


def window_list() -> list[tuple[int, str]]:
    try:
        user32 = _user32()
        found: list[tuple[int, str]] = []

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if title.startswith("AppGuard ·") or title.startswith("AppGuard -"):
                found.append((int(hwnd), title))
            return True

        user32.EnumWindows(WNDENUMPROC(callback), 0)
        return found
    except Exception:                                           # noqa: BLE001
        return []


def close_app_windows() -> int:
    count = 0
    try:
        user32 = _user32()
        for hwnd, _title in window_list():
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            count += 1
    except Exception:                                           # noqa: BLE001
        pass
    return count


def focus_app_windows() -> bool:
    try:
        user32 = _user32()
        for hwnd, _title in window_list():
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        return bool(window_list())
    except Exception:                                           # noqa: BLE001
        return False


def _terminate_pid(pid: int, label: str) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
        log(f"terminated {label} pid={pid}")
    except ProcessLookupError:
        pass
    except Exception as exc:                                    # noqa: BLE001
        log(f"terminate {label} pid={pid} failed: {exc}")


def stop_app(state: "LauncherState") -> None:
    """停止网关与守护（只在退出/关闭窗口选择 quit 时调用）。"""
    endpoint = state.endpoint or web.read_endpoint(state.endpoint_path)
    if endpoint:
        _terminate_pid(int(endpoint.get("pid") or 0), "gateway")
        try:
            if os.path.exists(state.endpoint_path):
                os.remove(state.endpoint_path)
        except OSError:
            pass
    try:
        subprocess.run(
            web.cli_argv(state.data_dir, "stop"),
            cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, creationflags=CREATE_NO_WINDOW)
        log("guardctl stop requested")
    except Exception as exc:                                    # noqa: BLE001
        log("guardctl stop failed: " + str(exc))


class LauncherState:
    def __init__(self, args):
        self.args = args
        self.data_dir = args.data_dir
        self.pipe_name = web.normalize_pipe_name(args.pipe_name or PIPE_NAME)
        self.endpoint_path = args.endpoint or web.default_endpoint(self.data_dir)
        self.endpoint: dict | None = None
        self.url = ""
        self.browser = web.find_browser(args.browser) if args.browser != "none" else None
        self.close_action = args.close_action or "tray"
        self.window_seen = False
        self.window_open = False
        self.suppress_close_until = 0.0
        self.admin_pending_until = 0.0
        self.stop_event = threading.Event()
        self.start_lock = threading.Lock()
        self.started = False
        self.icon = None

    def ensure_services(self) -> bool:
        with self.start_lock:
            if self.stop_event.is_set():
                return False
            endpoint = web.read_endpoint(self.endpoint_path)
            health = None
            if endpoint:
                try:
                    health = web.gateway_health(endpoint)
                except Exception:                               # noqa: BLE001
                    health = None
            if not (health and health.get("daemon")):
                web.ensure_daemon(self.data_dir, self.pipe_name, self.args.timeout,
                                  use_task=not getattr(self.args, "ignore_task", False))
            try:
                endpoint, gateway_state = web.ensure_gateway(
                    self.data_dir, self.pipe_name, self.endpoint_path,
                    self.args.port, self.args.timeout)
            except Exception as exc:                            # noqa: BLE001
                log("gateway start failed: " + str(exc))
                return False
            self.endpoint = endpoint
            self.url = str(endpoint.get("url") or "")
            if "#token=" not in self.url:
                self.url = self.url.split("#", 1)[0] + "#token=" + str(endpoint.get("token") or "")
            self.started = True
            log("services ready: " + self.url.split("#", 1)[0] + " gateway=" + gateway_state)
            return True

    def open_ui(self) -> None:
        if not self.ensure_services():
            return
        try:
            action = web.open_window(self.url, self.browser, False)
            log("open window: " + self.url.split("#", 1)[0] +
                " browser=" + str(self.browser) + " action=" + str(action))
        except Exception as exc:                                # noqa: BLE001
            log("open window failed: " + str(exc))

    def request_admin(self) -> None:
        if FROZEN:
            if web.scheduled_task_exists() and web.run_scheduled_task():
                self.admin_pending_until = time.time() + 120
                log("scheduled task restart requested (frozen)")
            else:
                log("no scheduled task; reinstall or run the installer")
                if self.icon is not None:
                    try:
                        self.icon.notify("没有找到 AppGuard 登录任务，请重新运行安装包",
                                         "AppGuard")
                    except Exception:                           # noqa: BLE001
                        pass
            return
        try:
            subprocess.Popen([web.console_python(), ELEVATE],
                             cwd=ROOT, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=CREATE_NO_WINDOW)
            self.admin_pending_until = time.time() + 120
            log("admin elevation requested")
        except Exception as exc:                                # noqa: BLE001
            log("admin elevation failed: " + str(exc))

    def set_close_action(self, action: str) -> None:
        self.close_action = action
        prefs = load_prefs()
        prefs["close_action"] = action
        save_prefs(prefs)
        log("close_action=" + action)
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:                                   # noqa: BLE001
                pass


def monitor(state: LauncherState) -> None:
    """监视 AppGuard 窗口关闭 & 管理员守护切换。"""
    while not state.stop_event.is_set():
        now = time.time()
        windows = window_list()
        exists = bool(windows)
        if exists:
            state.window_seen = True
            state.window_open = True
        elif state.window_seen and state.window_open and now >= state.suppress_close_until:
            state.window_open = False
            if state.close_action == "quit":
                log("AppGuard window closed -> quit")
                close_app_windows()
                stop_app(state)
                if state.icon is not None:
                    try:
                        state.icon.stop()
                    except Exception:                           # noqa: BLE001
                        pass
                return
            log("AppGuard window closed -> tray")
            if state.icon is not None:
                try:
                    state.icon.notify("AppGuard 已最小化到托盘", "AppGuard")
                except Exception:                               # noqa: BLE001
                    pass

        # 管理员安装后自动刷新窗口（让 UI 重新读取 elevated/dry_run）。
        if state.endpoint and state.admin_pending_until:
            try:
                status = web.gateway_status(state.endpoint)
            except Exception:                                   # noqa: BLE001
                status = None
            if status is not None and status.get("elevated"):
                if state.admin_pending_until and now <= state.admin_pending_until:
                    state.admin_pending_until = 0.0
                    log("elevated guard detected; refreshing UI")
                    state.suppress_close_until = time.time() + 6
                    close_app_windows()
                    time.sleep(0.8)
                    state.open_ui()
            elif state.admin_pending_until and now > state.admin_pending_until:
                state.admin_pending_until = 0.0
                if state.icon is not None:
                    try:
                        state.icon.notify("未检测到管理员守护；请确认 UAC 是否被取消", "AppGuard")
                    except Exception:                           # noqa: BLE001
                        pass
        state.stop_event.wait(1.0)


def build_tray(state: LauncherState):
    from win_tray import TrayIcon

    icon_path = os.path.join(WEB_ROOT, "assets", "appguard.ico")

    def on_open():
        threading.Thread(target=state.open_ui, daemon=True).start()

    def on_exit_keep():
        log("tray exit (keep services)")
        state.stop_event.set()
        if state.icon is not None:
            state.icon.stop()

    def on_exit_stop():
        log("tray exit (stop services)")
        state.stop_event.set()
        close_app_windows()
        stop_app(state)
        if state.icon is not None:
            state.icon.stop()

    def on_command(cmd: int) -> None:
        if cmd == 1001:
            on_open()
        elif cmd == 1002:
            state.set_close_action("tray")
        elif cmd == 1003:
            state.set_close_action("quit")
        elif cmd == 1004:
            state.request_admin()
        elif cmd == 1005:
            try:
                argv = ([sys.executable, "shortcut"] if FROZEN
                        else [web.console_python(), MAKE_SHORTCUT])
                subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=CREATE_NO_WINDOW)
            except Exception as exc:                            # noqa: BLE001
                log("shortcut failed: " + str(exc))
        elif cmd == 1006:
            try:
                os.startfile(LOCAL_DIR)
            except Exception as exc:                            # noqa: BLE001
                log("open logs failed: " + str(exc))
        elif cmd == 1007:
            on_exit_keep()
        elif cmd == 1008:
            threading.Thread(target=on_exit_stop, daemon=True).start()

    def build_menu():
        return [
            ("item", 1001, "打开界面", False, True),
            ("sep",),
            ("sub", "关闭窗口时", [
                ("item", 1002, "最小化到托盘", state.close_action == "tray", True),
                ("item", 1003, "退出 AppGuard", state.close_action == "quit", True),
            ]),
            ("sep",),
            ("item", 1004, "以管理员安装/重启（UAC）", False, True),
            ("item", 1005, "创建桌面快捷方式", False, True),
            ("item", 1006, "打开日志目录", False, True),
            ("sep",),
            ("item", 1007, "退出（保留后台守护）", False, True),
            ("item", 1008, "退出并停止 AppGuard", False, True),
        ]

    state.icon = TrayIcon("AppGuard · 进程账本", icon_path, build_menu,
                          on_command, on_double_click=on_open)
    log("tray icon created")
    return state.icon


def run_without_tray(state: LauncherState) -> int:
    if not state.ensure_services():
        return 1
    if not state.args.no_open:
        state.open_ui()
    try:
        monitor(state)
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AppGuard 单入口托盘启动器")
    parser.add_argument("--data-dir", default=DEFAULT_DATA)
    parser.add_argument("--pipe-name", default="")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--browser", choices=["auto", "edge", "chrome", "none"], default="auto")
    parser.add_argument("--close-action", choices=["tray", "quit"], default="")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--no-tray", action="store_true", help="不创建托盘（自动化/调试）")
    parser.add_argument("--ignore-task", action="store_true", help="调试：不启动登录任务，直接起普通守护")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    # 单实例：已有托盘实例时，只打开/聚焦窗口。
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        already = kernel32.GetLastError() == 183
    except Exception:                                           # noqa: BLE001
        mutex = None
        already = False
    if already:
        endpoint_path = args.endpoint or web.default_endpoint(args.data_dir)
        endpoint = web.read_endpoint(endpoint_path)
        if endpoint:
            try:
                url = str(endpoint.get("url") or "")
                if "#token=" not in url:
                    url = url.split("#", 1)[0] + "#token=" + str(endpoint.get("token") or "")
                web.open_window(url, web.find_browser(args.browser) if args.browser != "none" else None, False)
                log("second instance: focused/opened existing window")
            except Exception as exc:                            # noqa: BLE001
                log("second instance open failed: " + str(exc))
        return 0

    prefs = load_prefs()
    if args.close_action:
        prefs["close_action"] = args.close_action
        save_prefs(prefs)
    state = LauncherState(args)
    state.close_action = prefs.get("close_action", "tray")
    log("launcher start: data_dir=" + state.data_dir + " close_action=" + state.close_action)

    try:
        if args.no_tray:
            return run_without_tray(state)
        icon = build_tray(state)

        def setup(_icon):
            log("tray setup start")
            try:
                _icon.visible = True
            except Exception:                                   # noqa: BLE001
                pass
            threading.Thread(target=state.open_ui, daemon=True).start()
            threading.Thread(target=monitor, args=(state,), daemon=True).start()
            log("tray setup threads started")

        icon.run(setup=setup)
        state.stop_event.set()
        return 0
    finally:
        if mutex is not None:
            try:
                ctypes.windll.kernel32.CloseHandle(mutex)
            except Exception:                                   # noqa: BLE001
                pass


if __name__ == "__main__":
    raise SystemExit(main())
