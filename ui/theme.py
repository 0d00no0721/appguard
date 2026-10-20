"""UI 视觉主题：配色 / 字体 / 圆角控件（纯 Canvas 手绘，无外部依赖）。

- apply_theme(root)：ttk 全局风格（clam 基底）+ tk 经典控件默认色
- RoundedButton：圆角按钮（primary/normal/danger），接口对齐 ttk.Button
  （state() / cget("text") / config(text=…) / invoke()，自检脚本直接依赖）
- NavButton：左侧导航项（选中高亮 + 左侧指示条 + hover）
- Pill：圆角徽标（演练模式 / 健康状态）
- Card / SectionCard：圆角卡片容器（内容放 .body）
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# --------------------------------------------------------------------------- #
# 配色
# --------------------------------------------------------------------------- #
C_BG = "#2B2B2B"            # 深色底
C_SURFACE = "#353535"       # 卡片
C_BORDER = "#454545"        # 细分隔线
C_TEXT = "#FFFFFF"
C_MUTE = "#A0A0A0"
C_ACCENT = "#6A5CFF"        # 紫罗兰
C_ACCENT_HOVER = "#7C70FF"
C_ACCENT_PRESS = "#5849E0"
C_ACCENT_SOFT = "#3B3568"
C_OK = "#5BC0EB"
C_WARN = "#5BC0EB"
C_BAD = "#FF7A8A"
C_BANNER_BG = "#3B3152"
C_BANNER_FG = "#E8E4FF"
C_DRYRUN_BG = "#6A5CFF"
C_DRYRUN_FG = "#FFFFFF"
C_OK_SOFT = "#22323A"
C_OK_FG = "#5BC0EB"
C_DISABLED_BG = "#3A3A3A"
C_DISABLED_FG = "#777777"
C_HEADING_BG = "#262626"
C_HOVER_BG = "#414141"

FONT_FAMILY = "Microsoft YaHei UI"
DISPLAY_FAMILY = "Bahnschrift SemiBold"
F_TITLE = (FONT_FAMILY, 15, "bold")
F_H2 = (FONT_FAMILY, 11, "bold")
F_BODY = (FONT_FAMILY, 10)
F_SMALL = (FONT_FAMILY, 9)
F_NUM = (DISPLAY_FAMILY, 26, "bold")
F_DISPLAY = (DISPLAY_FAMILY, 13, "bold")
F_MICRO = ("Bahnschrift", 8, "bold")

# DPI 缩放：字体由 Tk scaling 处理；固定像素高度/图表尺寸用 px() 统一放大。
SCALE = 1.0


def set_scale(value: float) -> None:
    global SCALE
    SCALE = max(1.0, min(2.5, float(value)))


def px(value: float) -> int:
    return int(round(float(value) * SCALE))


def _bg_of(widget) -> str:
    """向上找到最近一个支持 bg 的祖先的背景色（ttk 控件无 bg，跳过）。"""
    w = widget
    while w is not None:
        try:
            bg = w.cget("bg")
            if bg:
                return str(bg)
        except tk.TclError:
            pass
        w = getattr(w, "master", None)
    return C_BG


def round_rect(cv: tk.Canvas, x1, y1, x2, y2, r, **kw) -> int:
    """在 Canvas 上画精确圆角矩形（边角按弧采样，边缘平直）。"""
    r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1]
    for cx, cy, a0 in ((x2 - r, y1 + r, -90), (x2 - r, y2 - r, 0),
                       (x1 + r, y2 - r, 90), (x1 + r, y1 + r, 180)):
        for i in range(1, 7):
            a = math.radians(a0 + i * 15)
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
    pts += [x1, y1 + r]
    return cv.create_polygon(pts, **kw)


def _tk_font(spec):
    return tkfont.Font(family=spec[0], size=spec[1],
                       weight=spec[2] if len(spec) > 2 else "normal")


# --------------------------------------------------------------------------- #
# ttk / tk 全局风格
# --------------------------------------------------------------------------- #
def apply_theme(root: tk.Tk) -> None:
    root.configure(bg=C_BG)
    root.option_add("*Label.Background", C_BG)
    root.option_add("*Frame.Background", C_BG)
    root.option_add("*Listbox.HighlightBackground", C_BORDER)
    root.option_add("*Listbox.HighlightColor", C_ACCENT)
    root.option_add("*TCombobox*Listbox.background", C_SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", C_TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", C_ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", font=F_BODY, background=C_BG, foreground=C_TEXT)
    style.configure("TFrame", background=C_BG)
    style.configure("Card.TFrame", background=C_SURFACE)
    style.configure("TLabel", background=C_BG, foreground=C_TEXT)
    style.configure("Card.TLabel", background=C_SURFACE, foreground=C_TEXT)
    style.configure("Mute.TLabel", background=C_BG, foreground=C_MUTE)
    style.configure("Card.Mute.TLabel", background=C_SURFACE, foreground=C_MUTE)
    style.configure("TCheckbutton", background=C_SURFACE, foreground=C_TEXT)
    style.map("TCheckbutton", background=[("active", C_SURFACE)])
    style.configure("TEntry", fieldbackground=C_SURFACE, foreground=C_TEXT,
                    bordercolor=C_BORDER, padding=3, insertcolor=C_TEXT)
    style.configure("TCombobox", fieldbackground=C_SURFACE, background=C_SURFACE,
                    foreground=C_TEXT, bordercolor=C_BORDER, padding=3,
                    arrowcolor=C_MUTE)
    style.map("TCombobox", fieldbackground=[("readonly", C_SURFACE)],
              foreground=[("readonly", C_TEXT)])
    style.configure("TSpinbox", fieldbackground=C_SURFACE, foreground=C_TEXT,
                    bordercolor=C_BORDER, arrowcolor=C_MUTE, padding=3)
    style.configure("Treeview", background=C_SURFACE, fieldbackground=C_SURFACE,
                    foreground=C_TEXT, rowheight=px(26), bordercolor=C_BORDER,
                    font=F_BODY)
    style.configure("Treeview.Heading", background=C_HEADING_BG,
                    foreground=C_SURFACE, font=(FONT_FAMILY, 9, "bold"),
                    relief="flat", bordercolor=C_BORDER, padding=(6, 5))
    style.map("Treeview",
              background=[("selected", C_ACCENT)],
              foreground=[("selected", C_SURFACE)])
    style.configure("Vertical.TScrollbar", background=C_BORDER,
                    troughcolor=C_SURFACE, bordercolor=C_BORDER,
                    arrowcolor=C_MUTE, relief="flat")
    style.map("Vertical.TScrollbar", background=[("active", C_ACCENT)])
    style.configure("TNotebook", background=C_BG, bordercolor=C_BORDER)
    style.configure("TNotebook.Tab", background="#3A3A3A", foreground=C_MUTE,
                    padding=(12, 5), font=F_SMALL)
    style.map("TNotebook.Tab",
              background=[("selected", C_ACCENT)],
              foreground=[("selected", "#FFFFFF")])
    style.configure("TLabelframe", background=C_BG, bordercolor=C_BORDER)
    style.configure("TLabelframe.Label", background=C_BG, foreground=C_TEXT,
                    font=F_SMALL)


# --------------------------------------------------------------------------- #
# 圆角按钮（接口对齐 ttk.Button 的子集：state/cget/config/invoke）
# --------------------------------------------------------------------------- #
_BTN_KINDS = {
    "primary": {"bg": C_ACCENT, "bg_hover": C_ACCENT_HOVER,
                "bg_press": C_ACCENT_PRESS, "fg": "#FFFFFF", "border": C_ACCENT,
                "dis_bg": C_DISABLED_BG, "dis_fg": C_DISABLED_FG},
    "danger": {"bg": C_BAD, "bg_hover": "#FF8F9C", "bg_press": "#E56777",
               "fg": "#2B2B2B", "border": C_BAD,
               "dis_bg": C_DISABLED_BG, "dis_fg": C_DISABLED_FG},
    "normal": {"bg": "#3A3A3A", "bg_hover": C_HOVER_BG, "bg_press": "#4B4B4B",
               "fg": C_TEXT, "border": C_BORDER,
               "dis_bg": C_DISABLED_BG, "dis_fg": C_DISABLED_FG},
}


class RoundedButton(tk.Canvas):
    def __init__(self, master, text="", command=None, kind="normal",
                 padx=16, height=32, radius=10, font=F_BODY, min_width=0):
        self._text = text
        self._command = command
        self._kind = kind if kind in _BTN_KINDS else "normal"
        self._state: set[str] = set()
        self._hover = False
        self._press = False
        self._font = font
        self._metric = _tk_font(font)
        self._padx = padx
        self._radius = radius
        self._min_width = min_width
        super().__init__(master, bg=_bg_of(master), highlightthickness=0, bd=0,
                         height=height, cursor="hand2")
        self._fit_width()
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    # -- ttk.Button 兼容接口 ------------------------------------------------ #
    def state(self, spec=None):
        if spec is None:
            return tuple(sorted(self._state))
        for token in spec:
            token = str(token)
            if token.startswith("!"):
                self._state.discard(token[1:])
            else:
                self._state.add(token)
        self._draw()
        return tuple(sorted(self._state))

    def instate(self, spec):  # 兼容 ttk 的另一个常用查询
        return all((s[1:] not in self._state) if s.startswith("!")
                    else (s in self._state) for s in spec)

    def cget(self, key):
        if key == "text":
            return self._text
        return super().cget(key)

    def config(self, cnf=None, **kw):  # noqa: A003 - 与 tk 接口同名
        if "text" in kw:
            self._text = kw.pop("text")
            self._fit_width()
        if "command" in kw:
            self._command = kw.pop("command")
        if kw:
            super().configure(**kw)
        self._draw()

    configure = config

    def invoke(self):
        if "disabled" not in self._state and self._command:
            return self._command()
        return None

    # -- 交互与绘制 ---------------------------------------------------------- #
    @property
    def _disabled(self) -> bool:
        return "disabled" in self._state

    def _fit_width(self) -> None:
        width = max(self._min_width, self._metric.measure(self._text)
                    + self._padx * 2 + 4)
        super().configure(width=width)

    def _on_enter(self, _e) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _e) -> None:
        self._hover = self._press = False
        self._draw()

    def _on_press(self, _e) -> None:
        if not self._disabled:
            self._press = True
            self._draw()

    def _on_release(self, e) -> None:
        was = self._press
        self._press = False
        self._draw()
        if was and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.invoke()

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        p = _BTN_KINDS[self._kind]
        if self._disabled:
            bg, fg, border = p["dis_bg"], p["dis_fg"], None
        elif self._press:
            bg, fg, border = p["bg_press"], p["fg"], p["border"]
        elif self._hover:
            bg, fg, border = p["bg_hover"], p["fg"], p["border"]
        else:
            bg, fg, border = p["bg"], p["fg"], p["border"]
        if self._kind == "normal" and self._hover and not self._disabled:
            fg = C_TEXT
            border = C_BORDER
        # 直接调 Canvas.configure：避免走覆写的 config（它会重绘 → 无穷递归）
        tk.Canvas.configure(self, cursor="arrow" if self._disabled else "hand2")
        round_rect(self, 1, 1, w - 2, h - 2, self._radius,
                   fill=bg, outline=border or bg, width=1)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


# --------------------------------------------------------------------------- #
# 导航项
# --------------------------------------------------------------------------- #
class NavButton(tk.Canvas):
    def __init__(self, master, text="", command=None, height=38):
        self._text = text
        self._command = command
        self._selected = False
        self._hover = False
        super().__init__(master, bg=_bg_of(master), highlightthickness=0, bd=0,
                         height=height, cursor="hand2")
        self._font = _tk_font(F_BODY)
        self._font_sel = _tk_font((FONT_FAMILY, 10, "bold"))
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._on_click)

    def set_selected(self, on: bool) -> None:
        self._selected = bool(on)
        self._draw()

    def _on_enter(self, _e) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _e) -> None:
        self._hover = False
        self._draw()

    def _on_click(self, e) -> None:
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            if self._command:
                self._command()

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        if self._selected:
            round_rect(self, 6, 3, w - 8, h - 3, 8,
                       fill=C_ACCENT_SOFT, outline=C_ACCENT_SOFT)
            round_rect(self, 10, h / 2 - 9, 14, h / 2 + 9, 2,
                       fill=C_ACCENT, outline=C_ACCENT)
            self.create_text(24, h / 2, text=self._text, anchor="w",
                             fill=C_ACCENT_HOVER, font=self._font_sel)
        else:
            if self._hover:
                round_rect(self, 6, 3, w - 8, h - 3, 8,
                           fill=C_HOVER_BG, outline=C_HOVER_BG)
            self.create_text(24, h / 2, text=self._text, anchor="w",
                             fill="#4b5563", font=self._font)


# --------------------------------------------------------------------------- #
# 圆角徽标
# --------------------------------------------------------------------------- #
class Pill(tk.Canvas):
    def __init__(self, master, text="", bg=C_DRYRUN_BG, fg=C_DRYRUN_FG,
                 font=F_SMALL, height=None):
        self._text = text
        self._fill = bg
        self._fg = fg
        self._font = font
        self._metric = _tk_font(font)
        height = px(24) if height is None else height
        super().__init__(master, bg=_bg_of(master), highlightthickness=0, bd=0,
                         height=height)
        self._fit()
        self.bind("<Configure>", lambda _e: self._draw())

    def cget(self, key):
        if key == "text":
            return self._text
        return super().cget(key)

    def config(self, cnf=None, **kw):  # noqa: A003
        if "text" in kw:
            self._text = kw.pop("text")
        if "bg" in kw:
            self._fill = kw.pop("bg")
        if "fg" in kw:
            self._fg = kw.pop("fg")
        if kw:
            super().configure(**kw)
        self._fit()
        self._draw()

    configure = config

    def _fit(self) -> None:
        super().configure(width=self._metric.measure(self._text) + px(22))

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        round_rect(self, 1, 1, w - 2, h - 2, 10,
                   fill=self._fill, outline=C_BORDER, width=1)
        self.create_text(w / 2, h / 2, text=self._text, fill=self._fg,
                         font=self._font)


# --------------------------------------------------------------------------- #
# 圆角卡片
# --------------------------------------------------------------------------- #
class Card(tk.Canvas):
    """硬边卡片容器。内容一律放进 .body（tk.Frame）。"""

    def __init__(self, master, radius=16, fill=C_SURFACE, outline=C_BORDER,
                 auto_height=False):
        super().__init__(master, bg=_bg_of(master), highlightthickness=0, bd=0)
        self._radius = radius
        self._fill = fill
        self._outline = outline
        self._auto_height = auto_height
        self._inset = max(2, int(radius * 0.34))
        self.body = tk.Frame(self, bg=fill)
        self._win = self.create_window(self._inset, self._inset, anchor="nw",
                                       window=self.body)
        self.bind("<Configure>", self._redraw)
        if auto_height:
            self.body.bind("<Configure>", self._sync_height)

    def _redraw(self, _e=None) -> None:
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.delete("cardbg")
        round_rect(self, 1, 1, w - 2, h - 2, self._radius,
                   fill=self._fill, outline=self._outline, width=1, tags="cardbg")
        self.tag_lower("cardbg")
        self.itemconfigure(self._win, width=w - 2 * self._inset,
                           height=h - 2 * self._inset)

    def _sync_height(self, _e=None) -> None:
        need = self.body.winfo_reqheight() + 2 * self._inset
        if need > 1 and self.winfo_height() != need:
            super().configure(height=need)


class SectionCard(Card):
    """带标题行的卡片；标题下方才是真正的内容区（仍用 .body）。"""

    def __init__(self, master, title="", radius=16, auto_height=False):
        super().__init__(master, radius=radius, auto_height=auto_height)
        head = tk.Frame(self.body, bg=C_SURFACE)
        head.pack(fill="x")
        tk.Frame(head, bg=C_ACCENT, width=6, height=16).pack(
            side="left", padx=(10, 6), pady=(8, 0))
        tk.Label(head, text=title, font=F_H2, fg=C_TEXT, bg=C_SURFACE,
                 anchor="w").pack(side="left", pady=(8, 0))
        self.header = head
        tk.Frame(self.body, bg=C_BORDER, height=1).pack(fill="x", padx=10)
        inner = tk.Frame(self.body, bg=C_SURFACE)
        inner.pack(fill="both", expand=True)
        self.body = inner
