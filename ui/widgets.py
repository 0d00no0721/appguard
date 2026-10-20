"""视觉组件：图标 / 圆环 / 条形图 / 滚动容器 / 勾选点 / 开关 / 侧栏图标。

设计目标（2026-10-18 重设计）：
* **多用图**：总览用圆环 + 条形图，进程页用 treemap，清理页用卡片/勾选点；
* **少用文字**：状态用颜色/图形/短标签表达，解释放悬浮提示或下方明细；
* 仍然是 tkinter 零依赖，所有图形用 Canvas 现画。
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from theme import (C_ACCENT, C_BORDER, C_MUTE, C_SURFACE, C_TEXT, DISPLAY_FAMILY,
                   F_BODY, F_SMALL, FONT_FAMILY, px, round_rect)

# 深色科技语义色：紫罗兰 / 蓝 / 亮蓝 + 灰
C_GREEN = "#5BC0EB"          # “在用” = 亮蓝
C_GREEN_SOFT = "#1F3A45"
C_AMBER = "#6A5CFF"          # “私自” = 紫罗兰
C_AMBER_SOFT = "#332D5A"
C_BLUE = "#2E86C1"           # 服务 = 天蓝
C_BLUE_SOFT = "#1F3340"
C_VIOLET = "#6A5CFF"
C_VIOLET_SOFT = "#332D5A"
C_RED = "#FF7A8A"
C_RED_SOFT = "#4A2A33"
C_GRAY = "#A0A0A0"
C_GRAY_SOFT = "#3B3B3B"
C_INK = "#FFFFFF"
C_SIDEBAR = "#232323"
C_SIDEBAR_2 = "#3B3568"
C_SIDEBAR_MUTE = "#A0A0A0"
C_PAGE = "#2B2B2B"
C_TRACK = "#474747"

ROLE_COLORS = {
    "main": C_GREEN, "launcher": C_GREEN, "service": C_BLUE,
    "updater": C_AMBER, "ad": C_AMBER, "telemetry": C_AMBER,
    "crash": C_AMBER, "daemon": C_GRAY, "tray": C_GRAY,
    "helper": C_BLUE, "installer": C_AMBER, "system": "#7A7A7A",
    "unknown": "#666666",
}

_KIND_TINTS = {
    "app": (C_GREEN, C_GREEN_SOFT), "game": (C_AMBER, C_AMBER_SOFT),
    "runtime": (C_BLUE, C_BLUE_SOFT), "driver": (C_AMBER, C_AMBER_SOFT),
    "system": (C_GRAY, C_GRAY_SOFT), "unknown": (C_GRAY, C_GRAY_SOFT),
}


# --------------------------------------------------------------------------- #
# 悬浮提示（少用文字：解释放提示里）
# --------------------------------------------------------------------------- #
def bind_tooltip(widget: tk.Misc, text: str, delay: int = 450) -> None:
    if not text:
        return
    state = {"tip": None, "job": None}

    def hide(_e=None) -> None:
        if state["job"]:
            try:
                widget.after_cancel(state["job"])
            except Exception:
                pass
            state["job"] = None
        if state["tip"] is not None:
            state["tip"].destroy()
            state["tip"] = None

    def show() -> None:
        if state["tip"] is not None:
            return
        x = widget.winfo_rootx() + 12
        y = widget.winfo_rooty() + widget.winfo_height() + 6
        state["tip"] = tk.Toplevel(widget)
        state["tip"].wm_overrideredirect(True)
        state["tip"].wm_geometry(f"+{x}+{y}")
        tk.Label(state["tip"], text=text, bg="#111827", fg="#f8fafc",
                 font=F_SMALL, padx=10, pady=5, justify="left",
                 wraplength=360).pack()

    def enter(_e=None) -> None:
        state["job"] = widget.after(delay, show)

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", hide, add="+")


# --------------------------------------------------------------------------- #
# 图标：用最少的线画可辨认的符号
# --------------------------------------------------------------------------- #
def draw_icon(cv: tk.Canvas, name: str, x: float, y: float, size: float,
              color: str, width: int = 2) -> None:
    s = float(size)
    h = s / 2
    b = max(1.5, width)
    if name == "grid":
        for dx in (0, h + 1):
            for dy in (0, h + 1):
                cv.create_rectangle(x - h + dx, y - h + dy, x - 2 + dx, y - 2 + dy,
                                    outline=color, width=b)
    elif name == "apps":
        cv.create_rectangle(x - h, y - h, x - 1, y - 1, outline=color, width=b)
        cv.create_rectangle(x + 1, y - h, x + h, y - 1, outline=color, width=b)
        cv.create_rectangle(x - h, y + 1, x - 1, y + h, outline=color, width=b)
        cv.create_oval(x + 2, y + 2, x + h, y + h, outline=color, width=b)
    elif name == "nodes":
        cv.create_oval(x - h, y - h, x - h + 7, y - h + 7, outline=color, width=b)
        cv.create_oval(x + h - 7, y - h, x + h, y - h + 7, outline=color, width=b)
        cv.create_oval(x - 4, y + h - 7, x + 3, y + h, outline=color, width=b)
        cv.create_line(x - h + 7, y - h + 4, x - 4, y + h - 5, fill=color, width=b)
        cv.create_line(x + h - 7, y - h + 4, x + 3, y + h - 5, fill=color, width=b)
        cv.create_line(x - h + 5, y - h + 3, x + h - 5, y - h + 3, fill=color, width=b)
    elif name == "clean":
        # 剪刀
        cv.create_oval(x - h, y - h, x - h + 8, y - h + 8, outline=color, width=b)
        cv.create_oval(x - h, y + h - 8, x - h + 8, y + h, outline=color, width=b)
        cv.create_line(x - h + 7, y - h + 6, x + h, y + h, fill=color, width=b)
        cv.create_line(x - h + 7, y + h - 6, x + h, y - h, fill=color, width=b)
    elif name == "clock":
        cv.create_oval(x - h, y - h, x + h, y + h, outline=color, width=b)
        cv.create_line(x, y, x, y - h + 4, fill=color, width=b)
        cv.create_line(x, y, x + h - 5, y + 2, fill=color, width=b)
    elif name == "gear":
        cv.create_oval(x - h + 4, y - h + 4, x + h - 4, y + h - 4,
                       outline=color, width=b)
        cv.create_oval(x - 3, y - 3, x + 3, y + 3, outline=color, width=b)
        for i in range(8):
            a = math.radians(i * 45)
            cv.create_line(x + math.cos(a) * (h - 3), y + math.sin(a) * (h - 3),
                           x + math.cos(a) * h, y + math.sin(a) * h, fill=color, width=b)
    elif name == "shield":
        pts = [x, y - h, x + h, y - h + 4, x + h - 1, y + 3,
               x, y + h, x - h + 1, y + 3, x - h, y - h + 4]
        cv.create_polygon(pts, outline=color, fill="", width=b)
    elif name == "check":
        cv.create_line(x - h + 2, y, x - 2, y + h - 3, x + h - 1, y - h + 2,
                       fill=color, width=max(2, b))
    elif name == "play":
        cv.create_polygon(x - h + 2, y - h, x + h, y, x - h + 2, y + h,
                          fill=color, outline=color)
    elif name == "plug":
        cv.create_line(x - 4, y - h, x - 4, y - 4, fill=color, width=b)
        cv.create_line(x + 4, y - h, x + 4, y - 4, fill=color, width=b)
        cv.create_rectangle(x - h + 2, y - 5, x + h - 2, y + 2, outline=color, width=b)
        cv.create_line(x, y + 2, x, y + h, fill=color, width=b)
    elif name == "lock":
        cv.create_rectangle(x - h + 2, y - 1, x + h - 2, y + h, outline=color, width=b)
        cv.create_arc(x - h + 5, y - h, x + h - 5, y + 2, start=0, extent=180,
                      style="arc", outline=color, width=b)
    elif name == "refresh":
        cv.create_arc(x - h, y - h, x + h, y + h, start=35, extent=290,
                      style="arc", outline=color, width=b)
        cv.create_polygon(x + h - 1, y - h + 1, x + h - 8, y - h + 2,
                          x + h - 2, y - h + 9, fill=color, outline=color)
    elif name == "search":
        cv.create_oval(x - h, y - h, x + 4, y + 4, outline=color, width=b)
        cv.create_line(x + 3, y + 3, x + h, y + h, fill=color, width=b)
    elif name == "bolt":
        cv.create_polygon(x + 1, y - h, x - h + 3, y + 1, x - 1, y + 1,
                          x - 2, y + h, x + h - 2, y - 2, x + 1, y - 2,
                          fill=color, outline=color)
    elif name == "close":
        cv.create_line(x - h + 3, y - h + 3, x + h - 3, y + h - 3, fill=color, width=b)
        cv.create_line(x - h + 3, y + h - 3, x + h - 3, y - h + 3, fill=color, width=b)
    elif name == "arrow":
        cv.create_line(x - h, y, x + h, y, fill=color, width=b)
        cv.create_line(x + h - 6, y - 5, x + h, y, x + h - 6, y + 5, fill=color, width=b)
    else:
        cv.create_oval(x - h + 2, y - h + 2, x + h - 2, y + h - 2,
                       outline=color, width=b)


# --------------------------------------------------------------------------- #
# 圆环图
# --------------------------------------------------------------------------- #
class DonutChart(tk.Canvas):
    def __init__(self, master, size=190, thickness=24, bg=C_SURFACE):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0)
        self._size = size
        self._thickness = thickness
        self._bg = bg
        self._segments: list[tuple[str, float, str]] = []
        self._center = ("", "")
        self.bind("<Configure>", lambda _e: self._draw())

    def set_data(self, segments: list[tuple[str, float, str]], *,
                 center_top: str = "", center_bottom: str = "") -> None:
        self._segments = [(str(label), max(0.0, float(value)), color)
                          for label, value, color in segments if value > 0]
        self._center = (str(center_top), str(center_bottom))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            return
        pad = self._thickness / 2 + 4
        box = (pad, pad, w - pad, h - pad)
        self.create_oval(*box, outline=C_TRACK, width=self._thickness)
        total = sum(v for _l, v, _c in self._segments)
        if total > 0:
            start = 90.0
            for _label, value, color in self._segments:
                extent = -360.0 * value / total
                self.create_arc(*box, start=start, extent=extent, style="arc",
                                outline=color, width=self._thickness)
                start += extent
        top, bottom = self._center
        if top:
            self.create_text(w / 2, h / 2 - (8 if bottom else 0), text=top,
                             fill=C_TEXT, font=(DISPLAY_FAMILY, 22, "bold"))
        if bottom:
            self.create_text(w / 2, h / 2 + 16, text=bottom, fill=C_MUTE,
                             font=F_SMALL)


# --------------------------------------------------------------------------- #
# 水平条形图
# --------------------------------------------------------------------------- #
class BarList(tk.Canvas):
    def __init__(self, master, height=190, bg=C_SURFACE, row_h=28):
        super().__init__(master, height=height, bg=bg, highlightthickness=0, bd=0)
        self._bg = bg
        self._row_h = row_h
        self._items: list[tuple[str, float, str]] = []
        self.bind("<Configure>", lambda _e: self._draw())

    def set_data(self, items: list[tuple[str, float, str]]) -> None:
        self._items = [(str(label), max(0.0, float(value)), color)
                       for label, value, color in items]
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        if w <= 10:
            return
        if not self._items:
            self.create_text(w / 2, 30, text="—", fill=C_MUTE, font=F_BODY)
            return
        top = max(v for _l, v, _c in self._items) or 1.0
        label_w = min(px(160), max(px(90), int(w * 0.28)))
        value_w = px(58)
        for index, (label, value, color) in enumerate(self._items):
            y = px(10) + index * self._row_h
            text = label if len(label) <= 12 else label[:11] + "…"
            self.create_text(2, y + 7, text=text, anchor="w", fill=C_TEXT,
                             font=F_SMALL)
            x0 = label_w
            x1 = w - value_w
            self.create_line(x0, y + 7, x1, y + 7, fill=C_TRACK, width=8,
                             capstyle="round")
            end = x0 + (x1 - x0) * (value / top)
            if end > x0 + 2:
                self.create_line(x0, y + 7, end, y + 7, fill=color, width=8,
                                 capstyle="round")
            self.create_text(w - 2, y + 7, text=_short_num(value), anchor="e",
                             fill=C_MUTE, font=F_SMALL)


def _short_num(value: float) -> str:
    if value >= 1024:
        return f"{value / 1024:.1f}G"
    return f"{value:.0f}M"


# --------------------------------------------------------------------------- #
# 小圆环 / 迷你柱
# --------------------------------------------------------------------------- #
class MiniRing(tk.Canvas):
    def __init__(self, master, size=52, thickness=7, bg=C_SURFACE):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0)
        self._size = size
        self._thickness = thickness
        self._ratio = 0.0
        self._text = ""
        self._color = C_BLUE
        self.bind("<Configure>", lambda _e: self._draw())

    def set_ratio(self, ratio: float, text: str = "", color: str = C_BLUE) -> None:
        self._ratio = max(0.0, min(1.0, float(ratio)))
        self._text = str(text)
        self._color = color
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        pad = self._thickness / 2 + 2
        box = (pad, pad, self._size - pad, self._size - pad)
        self.create_oval(*box, outline=C_TRACK, width=self._thickness)
        if self._ratio > 0:
            self.create_arc(*box, start=90, extent=-359.9 * self._ratio,
                            style="arc", outline=self._color, width=self._thickness)
        if self._text:
            self.create_text(self._size / 2, self._size / 2, text=self._text,
                             fill=C_TEXT, font=(FONT_FAMILY, 9, "bold"))


class MiniBars(tk.Canvas):
    def __init__(self, master, width=90, height=34, bg=C_SURFACE):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self._values: list[float] = []
        self._width, self._height = width, height
        self.bind("<Configure>", lambda _e: self._draw())

    def set_values(self, values: list[float], color: str = C_BLUE) -> None:
        self._values = [max(0.0, float(v)) for v in values][-12:]
        self._color = color
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if not self._values or w <= 2:
            return
        top = max(self._values) or 1.0
        gap = 2
        bw = max(2, (w - gap * (len(self._values) - 1)) / len(self._values))
        for i, value in enumerate(self._values):
            bh = max(2, (h - 4) * value / top)
            x0 = i * (bw + gap)
            self.create_rectangle(x0, h - bh, x0 + bw, h, fill=self._color,
                                  outline="")


# --------------------------------------------------------------------------- #
# 滚动容器
# --------------------------------------------------------------------------- #
class ScrollFrame(tk.Frame):
    """Canvas + 内部 Frame；内容放 ``.interior``。"""

    def __init__(self, master, bg=C_PAGE):
        super().__init__(master, bg=bg)
        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._bar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._bar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._bar.pack(side="right", fill="y")
        self.interior = tk.Frame(self._canvas, bg=bg)
        self._window = self._canvas.create_window((0, 0), window=self.interior,
                                                  anchor="nw")
        self.interior.bind("<Configure>", self._sync)
        self._canvas.bind("<Configure>", self._sync_width)
        self._canvas.bind("<Enter>", lambda _e: self._bind_wheel(True))
        self._canvas.bind("<Leave>", lambda _e: self._bind_wheel(False))

    def _sync(self, _e=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _sync_width(self, event) -> None:
        self._canvas.itemconfigure(self._window, width=event.width)
        self._sync()

    def _bind_wheel(self, on: bool) -> None:
        if on:
            self._canvas.bind_all("<MouseWheel>", self._wheel)
        else:
            self._canvas.unbind_all("<MouseWheel>")

    def _wheel(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")


# --------------------------------------------------------------------------- #
# 小部件
# --------------------------------------------------------------------------- #
class Chip(tk.Canvas):
    def __init__(self, master, text: str, command=None, *, selected=False,
                 count: int | None = None, color=C_BLUE, bg=C_SURFACE,
                 height=28, font=F_SMALL):
        self._text = text
        self._command = command
        self._selected = selected
        self._count = count
        self._color = color
        self._bg = bg
        self._font = font
        self._metric = tkfont.Font(family=FONT_FAMILY, size=9)
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         height=height, cursor="hand2")
        self._fit()
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", lambda _e: self._click())

    def _fit(self) -> None:
        text = self._text + (f"  {self._count}" if self._count is not None else "")
        super().configure(width=self._metric.measure(text) + 28)

    def set_selected(self, on: bool) -> None:
        self._selected = bool(on)
        self._draw()

    def _click(self) -> None:
        if self._command:
            self._command()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2:
            return
        fill = C_ACCENT if self._selected else "#3A3A3A"
        fg = "#FFFFFF" if self._selected else C_TEXT
        outline = C_BORDER
        round_rect(self, 1, 1, w - 2, h - 2, 2, fill=fill, outline=outline, width=1)
        text = self._text + (f"  {self._count}" if self._count is not None else "")
        self.create_text(w / 2, h / 2, text=text, fill=fg, font=self._font)


class CheckDot(tk.Canvas):
    def __init__(self, master, checked=True, command=None, size=26, bg=C_SURFACE):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._checked = bool(checked)
        self._command = command
        self._size = size
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", lambda _e: self._draw())

    @property
    def checked(self) -> bool:
        return self._checked

    def set_checked(self, value: bool) -> None:
        value = bool(value)
        if value != self._checked:
            self._checked = value
            self._draw()

    def _click(self, _e=None) -> None:
        self._checked = not self._checked
        self._draw()
        if self._command:
            self._command(self._checked)

    def _draw(self) -> None:
        self.delete("all")
        s = self._size
        if self._checked:
            self.create_oval(2, 2, s - 2, s - 2, fill=C_ACCENT,
                             outline=C_BORDER, width=1)
            draw_icon(self, "check", s / 2, s / 2, s * 0.52, "#FFFFFF", 3)
        else:
            self.create_oval(3, 3, s - 3, s - 3, fill=C_SURFACE,
                             outline=C_BORDER, width=2)


class ToggleSwitch(tk.Canvas):
    def __init__(self, master, variable: tk.BooleanVar, command=None,
                 width=48, height=26, bg=C_SURFACE):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._var = variable
        self._command = command
        self._toggle_w, self._toggle_h = width, height
        self._var.trace_add("write", lambda *_a: self._draw())
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", lambda _e: self._draw())

    def _click(self, _e=None) -> None:
        self._var.set(not bool(self._var.get()))
        if self._command:
            self._command()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self._toggle_w, self._toggle_h
        on = bool(self._var.get())
        track = C_ACCENT if on else "#4A4A4A"
        round_rect(self, 1, 2, w - 1, h - 2, (h - 4) / 2, fill=track, outline=C_BORDER, width=1)
        knob = h - 8
        x = (w - knob - 4) if on else 4
        self.create_oval(x, 4, x + knob, h - 4, fill="#FFFFFF", outline=C_BORDER, width=1)


class NavIcon(tk.Canvas):
    """侧栏图标项：图标 + 悬浮提示（不占文字宽度）。"""

    def __init__(self, master, icon: str, label: str, command=None):
        super().__init__(master, width=px(64), height=px(48), bg=C_SIDEBAR,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._icon = icon
        self._label = label
        self._command = command
        self._selected = False
        self._hover = False
        self._tip = None
        self._tip_job = None
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", lambda _e: self._command and self._command())

    def set_selected(self, on: bool) -> None:
        self._selected = bool(on)
        self._draw()

    def _enter(self, _e) -> None:
        self._hover = True
        self._draw()
        self._tip_job = self.after(450, self._show_tip)

    def _leave(self, _e) -> None:
        self._hover = False
        self._draw()
        if self._tip_job:
            self.after_cancel(self._tip_job)
            self._tip_job = None
        self._hide_tip()

    def _show_tip(self) -> None:
        if self._tip is not None:
            return
        x = self.winfo_rootx() + self.winfo_width() + 6
        y = self.winfo_rooty() + 12
        self._tip = tk.Toplevel(self)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip, text=self._label, bg="#111827", fg="#f8fafc",
                 font=F_SMALL, padx=8, pady=4).pack()

    def _hide_tip(self) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2:
            return
        if self._selected or self._hover:
            round_rect(self, px(10), px(6), w - px(10), h - px(6), px(2),
                       fill=C_ACCENT if self._selected else C_SIDEBAR_2, outline="")
        color = "#FFFFFF" if self._selected else ("#D0D0D0" if self._hover
                                                  else C_SIDEBAR_MUTE)
        draw_icon(self, self._icon, w / 2, h / 2, px(20), color, 2)


class IconTile(tk.Canvas):
    """软件/进程的色块图标（首字母/图标 + 可选角标）。"""

    def __init__(self, master, text: str, color: str, tint: str, size=44,
                 icon: str | None = None, badge: int | None = None, bg=C_SURFACE):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0)
        self._text = text[:2].upper()
        self._color = color
        self._tint = tint
        self._size = size
        self._icon = icon
        self._badge = badge
        self.bind("<Configure>", lambda _e: self._draw())

    def _draw(self) -> None:
        self.delete("all")
        s = self._size
        round_rect(self, 1, 1, s - 1, s - 1, 14, fill=self._tint,
                   outline=C_BORDER, width=1)
        if self._icon:
            draw_icon(self, self._icon, s / 2, s / 2, s * 0.48, self._color, 2)
        else:
            self.create_text(s / 2, s / 2, text=self._text, fill=self._color,
                             font=(FONT_FAMILY, int(s * 0.28), "bold"))
        if self._badge:
            self.create_oval(s - 20, -2, s + 2, 20, fill=C_RED, outline=C_SURFACE,
                             width=2)
            self.create_text(s - 9, 9, text=str(self._badge), fill="#ffffff",
                             font=(FONT_FAMILY, 7, "bold"))


# --------------------------------------------------------------------------- #
# 进程 treemap
# --------------------------------------------------------------------------- #
class ProcessTreemap(tk.Canvas):
    """按软件分组的 treemap：面积=内存，内部小方块=进程（颜色=角色）。

    点击小方块回调 ``on_pick(pid)``；悬浮在顶部显示短标签。
    """

    def __init__(self, master, height=300, bg=C_SURFACE, on_pick=None):
        super().__init__(master, height=height, bg=bg, highlightthickness=0, bd=0)
        self._bg = bg
        self._on_pick = on_pick
        self._groups: list[dict] = []
        self.bind("<Configure>", lambda _e: self._draw())

    def set_data(self, groups: list[dict]) -> None:
        """groups: [{name,color,memory,private,items:[{pid,name,role,memory,color}]}]"""
        self._groups = sorted(groups, key=lambda g: -float(g.get("memory") or 0))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 10 or h <= 10:
            return
        groups = self._groups[:16]
        if not groups:
            self.create_text(w / 2, h / 2, text="—", fill=C_MUTE, font=F_BODY)
            return
        weights = [max(1.0, float(g.get("memory") or 0)) for g in groups]
        rects = _layout(weights, px(4), px(4), w - px(8), h - px(8))
        for group, (x, y, rw, rh) in zip(groups, rects):
            if rw < 4 or rh < 4:
                continue
            color = group.get("color") or C_BLUE
            self.create_rectangle(x + 1, y + 1, x + rw - 1, y + rh - 1,
                                  fill=_tint(color), outline="#ffffff", width=2)
            name = str(group.get("name") or "?")
            if rw > px(54) and rh > px(20):
                label = name if len(name) <= 13 else name[:12] + "…"
                self.create_text(x + px(6), y + px(5), text=label, anchor="nw",
                                 fill="#E6E6E6", font=(FONT_FAMILY, 8, "bold"))
            items = group.get("items") or []
            if items:
                cell = px(9)
                top = y + px(22) if rh > px(30) else y + px(4)
                cols = max(1, int((rw - px(8)) // cell))
                rows = max(1, int((y + rh - top - px(4)) // cell))
                for index, proc in enumerate(items[:cols * rows]):
                    dx = x + px(5) + (index % cols) * cell
                    dy = top + (index // cols) * cell
                    pcolor = proc.get("color") or C_GRAY
                    self.create_rectangle(dx, dy, dx + cell - px(2),
                                          dy + cell - px(2), fill=pcolor,
                                          outline="")
            if group.get("private") and rw > px(70):
                self.create_text(x + rw - px(6), y + px(5),
                                 text=f"● {group['private']}", anchor="ne",
                                 fill=C_AMBER, font=(FONT_FAMILY, 8, "bold"))

def _tint(color: str) -> str:
    palette = {
        C_BLUE: "#22333D", C_GREEN: "#1F3A45", C_AMBER: "#2E2A4A",
        C_VIOLET: "#2E2A4A", C_RED: "#3A2A30", C_GRAY: "#333333",
        C_INK: "#2E2E2E",
    }
    return palette.get(color, "#2E2E2E")


def _layout(values: list[float], x: float, y: float, w: float, h: float):
    """二分 treemap：按权重切分较长边，保证矩形铺满且面积近似。"""
    if not values:
        return []
    if len(values) == 1:
        return [(x, y, w, h)]
    total = sum(values) or 1.0
    half = total / 2.0
    cumulative = 0.0
    split = 1
    for index, value in enumerate(values):
        cumulative += value
        if cumulative >= half:
            split = index + 1
            break
    split = max(1, min(len(values) - 1, split))
    left, right = values[:split], values[split:]
    fraction = sum(left) / total
    if w >= h:
        w1 = w * fraction
        return (_layout(left, x, y, w1, h)
                + _layout(right, x + w1, y, w - w1, h))
    h1 = h * fraction
    return (_layout(left, x, y, w, h1)
            + _layout(right, x, y + h1, w, h - h1))


class StatTile(tk.Frame):
    """顶部大数字卡：图标 + 数字 + 短标签 + 迷你图。"""

    def __init__(self, master, icon: str, caption: str, color: str,
                 tint: str, bg=C_SURFACE):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        self._color, self._tint = color, tint
        self._icon_name = icon
        self.var = tk.StringVar(value="—")
        self.configure(highlightbackground=C_BORDER, highlightthickness=1)
        head = tk.Frame(self, bg=bg)
        head.pack(fill="x", padx=14, pady=(12, 0))
        self._icon = tk.Canvas(head, width=px(34), height=px(34), bg=bg,
                               highlightthickness=0, bd=0)
        self._icon.pack(side="left")
        self._icon.bind("<Configure>", lambda _e: self._draw_icon())
        tk.Label(head, text=caption, bg=bg, fg=C_MUTE, font=F_SMALL,
                 anchor="w").pack(side="left", padx=6)
        body = tk.Frame(self, bg=bg)
        body.pack(fill="x", padx=14, pady=(2, 12))
        tk.Label(body, textvariable=self.var, bg=bg, fg=C_TEXT,
                 font=(DISPLAY_FAMILY, 25, "bold")).pack(side="left")
        self._mini = MiniBars(body, width=80, height=30, bg=bg)
        self._mini.pack(side="right", pady=(6, 0))

    def set_value(self, value: str, values: list[float] | None = None) -> None:
        self.var.set(str(value))
        if values:
            self._mini.set_values(values, self._color)

    def _draw_icon(self) -> None:
        self._icon.delete("all")
        size = px(34)
        round_rect(self._icon, px(2), px(2), size - px(2), size - px(2), px(10),
                   fill=self._tint, outline=self._tint)
        draw_icon(self._icon, self._icon_name, size / 2, size / 2, px(15),
                  self._color, 2)
