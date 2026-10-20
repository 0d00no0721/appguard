"""系统托盘：构成主义图标 + 状态角标（红/黑/灰）。

状态映射：未运行=灰；降级=红；有待清理=红；正常=黑。
pystray 不可用时退化为「最小化到任务栏」，由 appguard_ui 处理。
"""
from __future__ import annotations

import os
import threading

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except Exception:  # noqa: BLE001
    pystray = None
    Image = ImageDraw = None
    TRAY_AVAILABLE = False

STATE_COLORS = {
    "lost": ("#A0A0A0", "未连接"),
    "degraded": ("#6A5CFF", "降级"),
    "in_use": ("#5BC0EB", "有待清理"),
    "ok": ("#5BC0EB", "正常"),
}

_ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets",
                      "appguard-64.png")


def _base_image():
    try:
        return Image.open(_ASSET).convert("RGBA").resize((64, 64), Image.LANCZOS)
    except Exception:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((2, 2, 62, 62), radius=14, fill="#303030",
                               outline="#454545", width=2)
        draw.rounded_rectangle((12, 12, 56, 24), radius=6, fill="#6A5CFF")
        draw.rounded_rectangle((18, 28, 56, 40), radius=6, fill="#2E86C1")
        draw.ellipse((40, 44, 54, 58), fill="#5BC0EB")
        return img


def _make_image(color: str):
    img = _base_image()
    draw = ImageDraw.Draw(img)
    # 右下角状态方块（构成主义硬边，不用圆形胶囊）
    draw.rectangle((46, 46, 62, 62), fill=color, outline="#303030", width=2)
    return img


class TrayIcon:
    """pystray 封装：update(state) 切图标；菜单回调全部交回 app（主线程再调度）。"""

    def __init__(self, app) -> None:
        self.app = app
        self.state = "lost"
        self._icon = None
        self._images = {name: _make_image(color)
                        for name, (color, _t) in STATE_COLORS.items()} \
            if TRAY_AVAILABLE else {}

    @property
    def available(self) -> bool:
        return TRAY_AVAILABLE

    def start(self) -> None:
        if not TRAY_AVAILABLE:
            return
        menu = pystray.Menu(
            pystray.MenuItem("打开主界面", self._wrap(self.app.ui_show),
                             default=True),
            pystray.MenuItem("查看日志", self._wrap(self.app.ui_show_audit)),
            pystray.MenuItem("设置", self._wrap(self.app.ui_show_settings)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._wrap(self.app.ui_quit)),
        )
        self._icon = pystray.Icon("AppGuard", self._images["lost"],
                                  "AppGuard 应用守护（未连接）", menu)
        threading.Thread(target=self._icon.run, name="guardui-tray",
                         daemon=True).start()

    def update(self, state: str) -> None:
        self.state = state
        if not TRAY_AVAILABLE or self._icon is None:
            return
        color, text = STATE_COLORS.get(state, STATE_COLORS["lost"])
        self._icon.icon = self._images[state]
        self._icon.title = f"AppGuard 应用守护（{text}）"

    def stop(self) -> None:
        if TRAY_AVAILABLE and self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass

    def _wrap(self, fn):
        def handler(_icon=None, _item=None) -> None:
            self.app.root.after(0, fn)
        return handler
