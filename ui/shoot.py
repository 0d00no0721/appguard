"""给设计验收用：启动 UI、依次切换屏幕并截图到 ui/screenshots/。

前置：守护已运行（默认管道）。用法：`python ui\\shoot.py`
"""
from __future__ import annotations

import os
import sys
import time
from tkinter import messagebox

sys.path.insert(0, "ui")

from PIL import ImageGrab  # noqa: E402

from appguard_ui import AppGuardUI  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")


def log(*args):
    print(*args, flush=True)


# 截图时不要把 RPC 错误弹成模态对话框（会卡住 mainloop）
for name in ("showerror", "showwarning", "showinfo"):
    setattr(messagebox, name, lambda *a, **k: log("messagebox", a[:2]))


def _capture(root, name: str) -> None:
    root.lift()
    root.attributes("-topmost", True)
    root.update_idletasks()
    time.sleep(0.35)
    root.update_idletasks()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{name}.png")
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
    log(path)


def main() -> int:
    log("creating ui")
    app = AppGuardUI(".appguard-dev")
    root = app.root
    steps = [("overview", "01-overview"), ("software", "02-software"),
             ("processes", "03-processes"), ("cleanup", "04-cleanup"),
             ("audit", "05-audit"), ("settings", "06-settings")]
    state = {"index": 0, "waits": 0}

    def schedule_next(delay: int) -> None:
        root.after(delay, next_step)

    def wait_connect() -> None:
        state["waits"] += 1
        if app.connected:
            log("connected")
            root.after(900, next_step)
            return
        if state["waits"] > 60:
            log("not connected; capturing empty state")
            _capture(root, "00-empty")
            app.ui_quit()
            return
        root.after(200, wait_connect)

    def next_step() -> None:
        if state["index"] >= len(steps):
            app.ui_quit()
            return
        key, name = steps[state["index"]]
        log("screen", key)
        app.show_screen(key)
        if key == "cleanup":
            root.after(400, lambda: app.screens["cleanup"].generate_plan())
            root.after(3200, lambda: finish(key, name))
        else:
            root.after(2000, lambda: finish(key, name))

    def finish(key: str, name: str) -> None:
        log("capture", key)
        _capture(root, name)
        state["index"] += 1
        schedule_next(250)

    root.after(500, wait_connect)
    root.after(45000, lambda: (log("hard timeout"), app.ui_quit()))
    root.mainloop()
    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
