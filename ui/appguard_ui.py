"""AppGuard 桌面 UI（tkinter）—— 只通过 IPC 契约驱动后端。

用法：
    python ui\\appguard_ui.py [--data-dir "%ProgramData%\\AppGuard"]

红线落实点：
- 没有任何「结束任意进程」入口；清理只在 CleanupScreen 走 plan → execute
- 「在用」完全使用后端 in_use / in_use_reason，UI 不重算
- explanation / reason / in_use_reason / degraded_reason 原文展示
- 守护不可达：清空所有数据屏 + 空状态页（不展示历史数据）
- health=degraded：横幅 + 禁用一键清理
- dry_run=true：全局角标 + 清理按钮改「试运行」
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
import tkinter as tk
from tkinter import messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from client import (E_IPC, LOST_AFTER, EventStream,  # noqa: E402
                    GuardError, RpcClient)
from common import (C_BAD, C_MUTE, C_WARN, bg)  # noqa: E402
from screens_audit import AuditScreen  # noqa: E402
from screens_cleanup import CleanupScreen  # noqa: E402
from screens_overview import OverviewScreen  # noqa: E402
from screens_processes import ProcessScreen  # noqa: E402
from screens_settings import SettingsScreen  # noqa: E402
from screens_software import SoftwareScreen  # noqa: E402
from theme import (C_ACCENT, C_BANNER_BG, C_BANNER_FG, C_BORDER,  # noqa: E402
                   C_DRYRUN_BG, C_DRYRUN_FG, C_OK_FG, C_OK_SOFT,
                   C_SURFACE, C_TEXT, F_BODY, F_SMALL, F_TITLE,
                   FONT_FAMILY, Pill, RoundedButton, apply_theme, px, round_rect,
                   set_scale)
from widgets import (C_PAGE, C_SIDEBAR, NavIcon, bind_tooltip,  # noqa: E402
                     draw_icon)
from tray import TRAY_AVAILABLE, TrayIcon  # noqa: E402

DEFAULT_DATA_DIR = os.path.expandvars(r"%ProgramData%\AppGuard")
RECONNECT_EVERY_MS = 5000
WATCHDOG_MS = 1000

_SCREEN_ICONS = {
    "overview": "grid", "software": "apps", "processes": "nodes",
    "cleanup": "clean", "audit": "clock", "settings": "gear",
}


def _enable_dpi_awareness() -> None:
    """必须在 Tk 创建之前调用；否则 150% 缩放下整窗被 DWM 位图拉伸 ⇒ 糊。"""
    if sys.platform != "win32":
        return
    try:
        # PER_MONITOR_AWARE_V2（Win10 1703+）
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()           # SYSTEM_DPI_AWARE
    except Exception:
        pass


def _system_dpi() -> int:
    if sys.platform != "win32":
        return 96
    try:
        return int(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))   # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return dpi or 96
    except Exception:
        return 96


class AppGuardUI:
    def __init__(self, data_dir: str) -> None:
        _enable_dpi_awareness()
        self.data_dir = data_dir
        self.rpc = RpcClient(data_dir)
        self.events: EventStream | None = None
        self.hello: dict = {}
        self.status: dict = {}
        self.degraded = False
        self.dry_run = True
        self._lost = True
        self._connecting = False
        self._tray_hint_shown = False

        self.root = tk.Tk()
        self.dpi = _system_dpi()
        self.scale = max(1.0, min(2.5, self.dpi / 96.0))
        set_scale(self.scale)
        # Tk 的 scaling = 每磅像素数；让正文字号跟随系统 DPI，不糊也不小。
        try:
            self.root.tk.call("tk", "scaling", self.dpi / 72.0)
        except Exception:
            pass
        self.root.title("AppGuard 应用守护")
        self.root.geometry(f"{int(1280 * self.scale)}x{int(820 * self.scale)}")
        self.root.minsize(int(1120 * self.scale), int(720 * self.scale))
        apply_theme(self.root)
        self.root.option_add("*Font", "{Microsoft YaHei UI} 10")
        self._set_icon()

        self._build_chrome()
        self._build_screens()

        self.tray = TrayIcon(self)
        self.tray.start()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._set_chrome_lost("正在连接守护…")
        self.root.after(300, self._supervise)
        self.root.after(WATCHDOG_MS, self._watchdog)

    # ------------------------------------------------------------------ #
    # 界面骨架
    # ------------------------------------------------------------------ #
    def _set_icon(self) -> None:
        assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        self._icon_img = None
        for name in ("appguard-256.png", "appguard.png"):
            path = os.path.join(assets, name)
            if not os.path.exists(path):
                continue
            try:
                self._icon_img = tk.PhotoImage(file=path)
                self.root.iconphoto(True, self._icon_img)
                break
            except Exception:
                continue
        try:
            self.root.iconbitmap(default=os.path.join(assets, "appguard.ico"))
        except Exception:
            pass

    def _build_chrome(self) -> None:
        shell = tk.Frame(self.root, bg=C_PAGE)
        shell.pack(fill="both", expand=True)

        # 深色图标侧栏：不占文字宽度，悬浮才显示名称
        self._nav = tk.Frame(shell, bg=C_SIDEBAR, width=px(76))
        self._nav.pack(side="left", fill="y")
        self._nav.pack_propagate(False)
        assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        self._logo_img = None
        try:
            self._logo_img = tk.PhotoImage(file=os.path.join(assets, "appguard-64.png"))
            tk.Label(self._nav, image=self._logo_img, bg=C_SIDEBAR,
                     bd=0).pack(pady=(px(12), px(8)))
        except Exception:
            logo = tk.Canvas(self._nav, width=px(52), height=px(52), bg=C_SIDEBAR,
                             highlightthickness=0, bd=0)
            logo.pack(pady=(px(14), px(10)))
            round_rect(logo, px(4), px(4), px(48), px(48), px(14),
                       fill=C_ACCENT, outline=C_ACCENT)
            draw_icon(logo, "shield", px(26), px(26), px(20), C_SURFACE, 2)
        self._nav_items = tk.Frame(self._nav, bg=C_SIDEBAR)
        self._nav_items.pack(fill="x", pady=(4, 0))
        self._nav_buttons: dict[str, NavIcon] = {}

        workspace = tk.Frame(shell, bg=C_PAGE)
        workspace.pack(side="left", fill="both", expand=True)

        topbar = tk.Frame(workspace, bg=C_SURFACE, height=px(64))
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        left = tk.Frame(topbar, bg=C_SURFACE)
        left.pack(side="left", padx=px(20), pady=px(10))
        tk.Frame(left, bg=C_ACCENT, width=px(8), height=px(34)).pack(
            side="left", padx=(0, px(10)))
        title_box = tk.Frame(left, bg=C_SURFACE)
        title_box.pack(side="left")
        self.lbl_screen_title = tk.Label(title_box, text="总览", font=F_TITLE,
                                         bg=C_SURFACE, fg=C_TEXT, anchor="w")
        self.lbl_screen_title.pack(anchor="w")
        tk.Label(title_box, text="APPGUARD / PROCESS LEDGER",
                 font=(FONT_FAMILY, 7, "bold"),
                 bg=C_SURFACE, fg=C_MUTE, anchor="w").pack(anchor="w")

        self.badge_dry = Pill(topbar, text="● 演练", bg=C_DRYRUN_BG,
                              fg=C_DRYRUN_FG)
        self.badge_health = Pill(topbar, text="● 未连接", bg="#3B3B3B",
                                 fg=C_TEXT)
        self.badge_health.pack(side="right", padx=12, pady=19)
        self.badge_transport = tk.Label(topbar, text="", fg=C_MUTE,
                                        bg=C_SURFACE, font=F_SMALL)
        self.badge_transport.pack(side="right")

        # 降级横幅（显著提示「当前没有真正在守护」）
        self.banner = tk.Frame(workspace, bg=C_BANNER_BG)
        tk.Frame(self.banner, bg=C_BANNER_FG, width=4).pack(side="left", fill="y")
        self.banner_text = tk.Label(self.banner, text="", bg=C_BANNER_BG,
                                    fg=C_BANNER_FG, anchor="w", justify="left",
                                    font=F_BODY)
        self.banner_text.pack(fill="x", padx=10, pady=7)

        statusbar = tk.Frame(workspace, bg=C_SURFACE, height=px(26))
        statusbar.pack(fill="x", side="bottom")
        statusbar.pack_propagate(False)
        tk.Frame(workspace, bg=C_BORDER, height=1).pack(fill="x", side="bottom")
        self.var_status = tk.StringVar(value="")
        self.lbl_status = tk.Label(statusbar, textvariable=self.var_status,
                                   fg=C_MUTE, bg=C_SURFACE, anchor="w",
                                   font=F_SMALL)
        self.lbl_status.pack(side="left", padx=16, pady=3)
        self.var_seq = tk.StringVar(value="")
        tk.Label(statusbar, textvariable=self.var_seq, fg=C_MUTE,
                 bg=C_SURFACE, font=F_SMALL).pack(side="right", padx=16)

        self._main = tk.Frame(workspace, bg=C_PAGE)
        self._main.pack(fill="both", expand=True)
        self.content = self._main

    def _build_screens(self) -> None:
        self.screens: dict[str, object] = {
            "overview": OverviewScreen(self.content, self),
            "software": SoftwareScreen(self.content, self),
            "processes": ProcessScreen(self.content, self),
            "cleanup": CleanupScreen(self.content, self),
            "audit": AuditScreen(self.content, self),
            "settings": SettingsScreen(self.content, self),
        }
        for key, screen in self.screens.items():
            screen.place(relwidth=1, relheight=1)
            btn = NavIcon(self._nav_items, icon=_SCREEN_ICONS.get(key, "grid"),
                          label=screen.TITLE,
                          command=lambda k=key: self.show_screen(k))
            btn.pack(pady=2)
            self._nav_buttons[key] = btn
        self._build_empty_screen()
        self.current_key = "overview"
        self._nav_buttons["overview"].set_selected(True)

    def _build_empty_screen(self) -> None:
        """守护未运行的空状态页（红线 4：不展示任何历史数据）。"""
        from theme import Card
        frame = tk.Frame(self.content, bg=C_PAGE)
        frame.place(relwidth=1, relheight=1)
        card = Card(frame, radius=2, auto_height=True)
        card.place(relx=0.5, rely=0.46, anchor="center", width=620)
        body = card.body
        icon = tk.Canvas(body, width=72, height=72, bg=C_SURFACE,
                         highlightthickness=0, bd=0)
        icon.pack(pady=(24, 4))
        icon.create_oval(4, 4, 68, 68, fill="#332D5A", outline="#332D5A")
        draw_icon(icon, "shield", 36, 36, 28, C_ACCENT, 2)
        tk.Label(body, text="核心未连接", font=("Microsoft YaHei UI", 18, "bold"),
                 bg=C_SURFACE, fg=C_TEXT).pack(pady=(4, 2))
        tk.Label(body, justify="left", fg=C_MUTE, bg=C_SURFACE, font=F_BODY,
                 text=("界面不会自行采集或展示历史数据。\n"
                       "请先启动守护，再回到这里。")).pack(pady=2, padx=36)
        tk.Label(body, justify="left", fg=C_MUTE, bg=C_SURFACE, font=F_SMALL,
                 text=("python tools\\guardctl.py serve --transport auto\n"
                       f"数据目录：{self.data_dir}")).pack(pady=(2, 4), padx=36)
        self.var_retry = tk.StringVar(value="正在尝试连接…")
        tk.Label(body, textvariable=self.var_retry, fg=C_MUTE, bg=C_SURFACE,
                 font=F_SMALL).pack(pady=2)
        RoundedButton(body, text="立即重试", kind="primary",
                      command=self._try_connect).pack(pady=(6, 24))
        self.empty_screen = frame

    # ------------------------------------------------------------------ #
    # 屏幕切换
    # ------------------------------------------------------------------ #
    def show_screen(self, key: str) -> None:
        self.current_key = key
        for nav_key, btn in self._nav_buttons.items():
            btn.set_selected(nav_key == key)
        if self._lost:
            self.empty_screen.tkraise()
            self.lbl_screen_title.config(text="未连接")
            return
        screen = self.screens[key]
        self.lbl_screen_title.config(text=screen.TITLE)
        screen.tkraise()
        screen.reload()
        if hasattr(screen, "on_shown"):
            screen.on_shown()

    def goto_cleanup(self) -> None:
        self.show_screen("cleanup")
        self.screens["cleanup"].generate_plan()

    def goto_cleanup_for(self, software_id: str) -> None:
        """从软件明细进入：只预览该软件的私自/可选进程。"""
        self.show_screen("cleanup")
        self.screens["cleanup"].generate_plan(scope="software",
                                              software_ids=[software_id])

    # ------------------------------------------------------------------ #
    # 连接监管
    # ------------------------------------------------------------------ #
    @property
    def connected(self) -> bool:
        return not self._lost and self.rpc.connected

    def _supervise(self) -> None:
        if self._lost and not self._connecting:
            self._try_connect()
        self.root.after(RECONNECT_EVERY_MS, self._supervise)

    def _try_connect(self) -> None:
        if self._connecting:
            return
        self._connecting = True
        self.var_retry.set("正在连接…")
        bg(self.root, self.rpc.connect, self._on_connected, self._on_connect_failed)

    def _on_connected(self, hello: dict) -> None:
        self._connecting = False
        self.hello = hello or {}
        self._lost = False
        self.update_status({k: self.hello.get(k) for k in
                            ("health", "degraded_reason", "dry_run")})
        since = self.events.last_seq if self.events else 0
        self.events = EventStream(self.data_dir, self._on_event_threadsafe,
                                  self._on_stream_dead)
        self.events.start(since_seq=since)
        pid = self.hello.get("pid")
        self.badge_transport.config(
            text=f"{str(self.rpc.transport).upper()} · PID {pid}")
        self.notify(f"已连接守护（{self.rpc.transport}，PID {pid}）")
        self.show_screen(self.current_key)
        self._refresh_all_counts()

    def _on_connect_failed(self, err: GuardError) -> None:
        self._connecting = False
        self._set_chrome_lost(f"连接失败：{err.message}")

    def _set_chrome_lost(self, reason: str) -> None:
        self._lost = True
        self.hello = {}
        self.status = {}
        for screen in self.screens.values():
            screen.clear()
        self.empty_screen.tkraise()
        self.badge_health.config(text="● 未连接", bg="#3B3B3B", fg=C_TEXT)
        self.badge_transport.config(text="")
        self.badge_dry.pack_forget()
        self.banner.pack_forget()
        self.var_status.set(reason)
        self.var_retry.set("每隔几秒自动重试连接")
        self.tray.update("lost")
        self.screens["overview"].set_clean_enabled(False, "守护未运行")

    def _on_stream_dead(self, reason: str) -> None:
        self.root.after(0, lambda: self._set_chrome_lost(f"事件连接断开：{reason}"))

    def _watchdog(self) -> None:
        """契约 §2.6：超过 15 秒没收到任何消息 ⇒ 守护失联。"""
        try:
            if not self._lost and self.events is not None:
                silence = time.time() - self.events.last_msg_at
                if silence > LOST_AFTER:
                    self.rpc.close()
                    self._set_chrome_lost(f"超过 {int(LOST_AFTER)} 秒未收到守护消息"
                                          "（判定守护失联）")
        finally:
            self.root.after(WATCHDOG_MS, self._watchdog)

    # ------------------------------------------------------------------ #
    # 事件
    # ------------------------------------------------------------------ #
    def _on_event_threadsafe(self, envelope: dict) -> None:
        self.root.after(0, lambda: self._dispatch_event(envelope))

    def _dispatch_event(self, envelope: dict) -> None:
        if self._lost:
            return
        seq = envelope.get("seq")
        name = envelope.get("event")
        if seq:
            self.var_seq.set(f"事件 seq {seq}")
        if name == "heartbeat":
            data = envelope.get("data") or {}
            if data.get("health"):
                self.update_status({"health": data.get("health")})
        elif name == "policy.changed":
            policy = (envelope.get("data") or {}).get("policy") or {}
            self.update_policy(policy)
        elif name == "notice":
            data = envelope.get("data") or {}
            self.notify(data.get("message") or "", level=data.get("level") or "info")
        elif name == "process.blocked":
            data = envelope.get("data") or {}
            action = "演练拦截" if data.get("dry_run") else "已结束"
            self.notify(f"{action} {data.get('name')}（{data.get('explanation') or ''}）")
        screen = self.screens.get(self.current_key)
        if screen is not None:
            screen.on_event(envelope)

    # ------------------------------------------------------------------ #
    # 全局状态（header / banner / tray / 清理按钮）
    # ------------------------------------------------------------------ #
    def update_status(self, status: dict) -> None:
        if not status:
            return
        self.status.update(status)
        health = str(self.status.get("health") or "").lower()
        self.degraded = health == "degraded"
        self.dry_run = bool(self.status.get("dry_run", self.dry_run))
        self._refresh_chrome()

    def update_policy(self, policy: dict) -> None:
        if "dry_run" in policy:
            self.dry_run = bool(policy.get("dry_run"))
            self.status["dry_run"] = self.dry_run
            self._refresh_chrome()

    def _refresh_chrome(self) -> None:
        if self._lost:
            return
        # dry_run 全局角标（红线 6）
        if self.dry_run:
            self.badge_dry.pack(side="right", padx=4)
        else:
            self.badge_dry.pack_forget()
        # 健康徽标 + 降级横幅（红线 5）
        if self.degraded:
            self.badge_health.config(text="降级", bg=C_BANNER_BG, fg=C_BANNER_FG)
            reason = self.status.get("degraded_reason") or "守护处于降级模式"
            short = ("⚠ 降级 · 非管理员：只能查看/演练，真实结束进程不可用"
                     if "未以管理员" in reason else f"⚠ 降级：{reason[:54]}")
            self.banner_text.config(text=short)
            bind_tooltip(self.banner_text, reason)
            self.banner.pack(fill="x", before=self._main)
        else:
            self.badge_health.config(text="守护正常", bg=C_OK_SOFT, fg=C_OK_FG)
            self.banner.pack_forget()
        # 清理入口开关
        overview = self.screens["overview"]
        if self.degraded:
            overview.set_clean_enabled(False, "降级模式下禁止一键清理")
        else:
            overview.set_clean_enabled(True)
        cleanup = self.screens["cleanup"]
        cleanup._sync_execute()
        # 托盘四态
        if self.degraded:
            self.tray.update("degraded")
        else:
            counts = self.status.get("counts") or {}
            self.tray.update("in_use" if counts.get("private") else "ok")

    def _refresh_all_counts(self) -> None:
        if not self.connected:
            return
        bg(self.root, lambda: self.rpc.call("status.get"), self.update_status,
           lambda err: self._maybe_lost(err))

    # ------------------------------------------------------------------ #
    # 错误与通知
    # ------------------------------------------------------------------ #
    def on_screen_error(self, screen, err: GuardError) -> None:
        if err.code == E_IPC:
            self._maybe_lost(err)
            return
        if err.code == "E_API_VERSION":
            messagebox.showerror("版本不匹配",
                                 f"{err.message}\n\n请升级 UI 或守护到同一 API 版本。",
                                 parent=self.root)
            return
        if err.code in ("E_PERMISSION", "E_DEGRADED"):
            messagebox.showwarning("权限不足",
                                   f"{err.message}\n\n请以管理员身份运行 AppGuard 后再试。",
                                   parent=self.root)
            return
        if err.code == "E_TIMEOUT":
            self.notify(f"守护响应超时：{err.message}", level="warn")
            return
        messagebox.showerror("操作失败", str(err), parent=self.root)

    def _maybe_lost(self, err: GuardError) -> None:
        if err.code == E_IPC:
            self._set_chrome_lost(f"守护不可达：{err.message}")
        else:
            self.notify(str(err), level="error")

    def notify(self, text: str, level: str = "info") -> None:
        color = {"info": C_MUTE, "warn": C_WARN, "error": C_BAD}.get(level,
                                                                     C_MUTE)
        self.var_status.set(text)
        self.lbl_status.config(fg=color)

    # ------------------------------------------------------------------ #
    # 托盘动作
    # ------------------------------------------------------------------ #
    def ui_show(self) -> None:
        self.root.deiconify()
        self.root.lift()

    def ui_enter_use(self) -> None:
        """「进入使用态」：回到主界面总览（在用判定仍完全由后端 §5.1 给出）。"""
        self.ui_show()
        self.show_screen("overview")

    def ui_pause_guard(self) -> None:
        """临时放行 10 分钟（可选能力；守护不支持时如实提示）。"""
        if not self.connected:
            self.notify("守护未运行，无法临时放行", level="warn")
            return
        bg(self.root, lambda: self.rpc.call("guard.pause", seconds=600),
           lambda _r: self.notify("已临时放行 10 分钟（该期间不清理后台组件）"),
           lambda err: self.notify("当前守护不支持临时放行" if err.code == "E_UNKNOWN_METHOD"
                                   else str(err), level="warn"))

    def ui_show_audit(self) -> None:
        self.ui_show()
        self.show_screen("audit")

    def ui_show_settings(self) -> None:
        self.ui_show()
        self.show_screen("settings")

    def ui_quit(self) -> None:
        self.tray.stop()
        if self.events:
            self.events.stop()
        self.rpc.close()
        self.root.destroy()

    def _on_close(self) -> None:
        if TRAY_AVAILABLE and self.tray.available:
            self.root.withdraw()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.notify("已最小化到系统托盘（右键图标可退出）")
        else:
            self.root.iconify()  # 无托盘时退化为最小化到任务栏

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="AppGuard 桌面 UI")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="数据目录（默认 %%ProgramData%%\\AppGuard，"
                             "需与守护一致）")
    args = parser.parse_args()
    app = AppGuardUI(args.data_dir)
    app.run()


if __name__ == "__main__":
    main()
