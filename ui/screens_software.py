"""屏幕 2：软件清单 —— 卡片墙；一张卡=一个软件 + 状态/私自/内存的图形。

点击卡片看明细（进程/服务/自启）；有私自组件时可直接进入该软件的清理预览。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from common import (C_MUTE, KIND_ZH, BaseScreen, activity_zh, fmt_mb, items_of,
                    origin_zh, role_zh)
from theme import C_SURFACE, C_TEXT, F_BODY, F_SMALL, Card, RoundedButton, px
from widgets import (C_AMBER, C_BLUE, C_GREEN, C_PAGE, Chip, IconTile,
                     ScrollFrame, bind_tooltip, _KIND_TINTS)


class SoftwareScreen(BaseScreen):
    TITLE = "软件清单"

    def build(self) -> None:
        bar = tk.Frame(self, bg=C_PAGE)
        bar.pack(fill="x", padx=18, pady=(16, 6))
        self.chips: dict[str, Chip] = {}
        for key, label, color in (("all", "全部", C_BLUE), ("in_use", "在用", C_GREEN),
                                  ("private", "私自", C_AMBER)):
            chip = Chip(bar, label, command=lambda k=key: self._set_filter(k),
                        selected=(key == "all"), color=color, bg=C_PAGE)
            chip.pack(side="left", padx=(0, 6))
            self.chips[key] = chip
        self.var_count = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.var_count, bg=C_PAGE, fg=C_MUTE,
                 font=F_SMALL).pack(side="left", padx=10)
        RoundedButton(bar, text="↻", height=30, min_width=42,
                      command=self.reload).pack(side="right")

        self._scroll = ScrollFrame(self, bg=C_PAGE)
        self._scroll.pack(fill="both", expand=True, padx=18, pady=(2, 14))
        self.grid = self._scroll.interior
        for column in range(3):
            self.grid.grid_columnconfigure(column, weight=1)

        self._all: list[dict] = []
        self._filter = "all"
        self._tiles: dict[str, "SoftwareTile"] = {}
        self._cache: dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    def _set_filter(self, key: str) -> None:
        self._filter = key
        for name, chip in self.chips.items():
            chip.set_selected(name == key)
        self._render()

    def reload(self) -> None:
        if not self.app.connected:
            return
        self.run_bg(lambda: items_of(self.rpc.call("software.list", limit=0)),
                    self._apply)

    def _apply(self, items) -> None:
        self._all = list(items)
        self._cache = {str(sw.get("id")): sw for sw in self._all}
        self._render()

    def _render(self) -> None:
        for child in self.grid.winfo_children():
            child.destroy()
        self._tiles = {}
        if self._filter == "in_use":
            items = [sw for sw in self._all if sw.get("in_use")]
        elif self._filter == "private":
            items = [sw for sw in self._all if int(sw.get("private_background") or 0) > 0]
        else:
            items = list(self._all)
        self.var_count.set(f"{len(items)} / {len(self._all)}")
        for index, sw in enumerate(items):
            tile = SoftwareTile(self.grid, sw, self._open_detail_id)
            tile.grid(row=index // 3, column=index % 3, sticky="nsew",
                      padx=(0 if index % 3 == 0 else 8, 0), pady=(0, 8))
            self._tiles[str(sw.get("id"))] = tile
        if not items:
            tk.Label(self.grid, text="—", bg=C_PAGE, fg=C_MUTE,
                     font=F_BODY).grid(row=0, column=0, columnspan=3, pady=30)

    # ------------------------------------------------------------------ #
    def _open_detail(self, _event=None) -> None:
        """兼容自检：没有事件时打开第一张卡。"""
        if not self._tiles:
            return
        first = next(iter(self._tiles))
        self._open_detail_id(first)

    def _open_detail_id(self, software_id: str) -> None:
        if not self.app.connected or not software_id:
            return
        self.run_bg(lambda: self.rpc.call("software.get", id=software_id),
                    lambda detail: SoftwareDetailWindow(self.winfo_toplevel(),
                                                        self.app, detail))

    def clear(self) -> None:
        for child in self.grid.winfo_children():
            child.destroy()
        self._tiles = {}
        self._all = []
        self._cache = {}
        self.var_count.set("")


class SoftwareTile(Card):
    """一张软件卡：色块图标 + 名称 + 状态 + 私自角标 + 类型条。"""

    def __init__(self, master, software: dict, on_open):
        super().__init__(master, radius=2, auto_height=False)
        self.software = software
        self._on_open = on_open
        self.configure(height=px(116), cursor="hand2")
        body = self.body
        row = tk.Frame(body, bg=C_SURFACE)
        row.pack(fill="both", expand=True, padx=10, pady=10)
        kind = str(software.get("kind") or "app")
        color, tint = _KIND_TINTS.get(kind, (C_BLUE, "#2A2F3A"))
        name = str(software.get("name") or software.get("id") or "?")
        self.icon = IconTile(row, name, color, tint, size=px(46))
        self.icon.pack(side="left", anchor="n")
        info = tk.Frame(row, bg=C_SURFACE)
        info.pack(side="left", fill="both", expand=True, padx=10)
        top = tk.Frame(info, bg=C_SURFACE)
        top.pack(fill="x")
        title = name if len(name) <= 22 else name[:21] + "…"
        tk.Label(top, text=title, bg=C_SURFACE, fg=C_TEXT, font=F_BODY,
                 anchor="w").pack(side="left")
        if software.get("in_use"):
            tk.Label(top, text="●", bg=C_SURFACE, fg=C_GREEN,
                     font=(F_SMALL[0], 9, "bold")).pack(side="right")
        publisher = str(software.get("publisher") or KIND_ZH.get(kind, kind))
        tk.Label(info, text=publisher if len(publisher) <= 24 else publisher[:23] + "…",
                 bg=C_SURFACE, fg=C_MUTE, font=F_SMALL, anchor="w").pack(fill="x")
        self.bar = tk.Canvas(info, height=px(12), bg=C_SURFACE, highlightthickness=0, bd=0)
        self.bar.pack(fill="x", pady=(4, 0))
        self.bar.bind("<Configure>", lambda _e: self._draw_bar())
        meta = tk.Frame(info, bg=C_SURFACE)
        meta.pack(fill="x")
        procs = len(software.get("processes") or [])
        services = len(software.get("services") or [])
        tk.Label(meta, text=f"{procs}P · {services}S · {fmt_mb(software.get('memory_mb'))}M",
                 bg=C_SURFACE, fg=C_MUTE, font=(F_SMALL[0], 7),
                 anchor="w").pack(side="left")
        if int(software.get("private_background") or 0):
            tk.Label(meta, text=f"● {software.get('private_background')}",
                     bg=C_SURFACE, fg=C_AMBER, font=(F_SMALL[0], 7, "bold"),
                     anchor="e").pack(side="right")
        for widget in (self, body, row, info, top):
            widget.bind("<Button-1>", self._click, add="+")
        _bind_click(self, self._click)
        bind_tooltip(self, f"{name} · 点击查看进程/服务/自启")

    def _click(self, _event=None) -> None:
        self._on_open(str(self.software.get("id") or ""))

    def _draw_bar(self) -> None:
        self.bar.delete("all")
        w = self.bar.winfo_width()
        if w <= 4:
            return
        procs = int(len(self.software.get("processes") or []))
        private = int(self.software.get("private_background") or 0)
        segments = max(1, min(18, procs))
        gap = 2
        bw = max(2, (w - gap * (segments - 1)) / segments)
        for i in range(segments):
            color = C_AMBER if i < private else (C_GREEN if self.software.get("in_use")
                                                else "#3B3B3B")
            self.bar.create_rectangle(i * (bw + gap), px(2),
                                      i * (bw + gap) + bw, px(10),
                                      fill=color, outline="")


class SoftwareDetailWindow(tk.Toplevel):
    """software.get 明细：基本信息 + 进程 / 服务 / 自启三个页签。"""

    def __init__(self, master, app, detail: dict) -> None:
        super().__init__(master)
        self.app = app
        self.detail = detail
        self.title(f"AppGuard · {detail.get('name') or detail.get('id')}")
        self.geometry("900x560")
        self.configure(bg=C_PAGE)
        self._build()

    def _build(self) -> None:
        d = self.detail
        head = tk.Frame(self, bg=C_SURFACE)
        head.pack(fill="x")
        kind = str(d.get("kind") or "app")
        color, tint = _KIND_TINTS.get(kind, (C_BLUE, "#2A2F3A"))
        IconTile(head, str(d.get("name") or "?"), color, tint, size=px(52)).pack(
            side="left", padx=14, pady=12)
        info = tk.Frame(head, bg=C_SURFACE)
        info.pack(side="left", fill="x", expand=True, pady=10)
        tk.Label(info, text=str(d.get("name") or "-"), bg=C_SURFACE, fg=C_TEXT,
                 font=("Microsoft YaHei UI", 14, "bold"), anchor="w").pack(fill="x")
        bits = [d.get("publisher") or "-", d.get("version") or "-",
                KIND_ZH.get(kind, kind)]
        tk.Label(info, text=" · ".join(str(x) for x in bits), bg=C_SURFACE,
                 fg=C_MUTE, font=F_SMALL, anchor="w").pack(fill="x")
        reason = str(d.get("in_use_reason") or "未在使用")
        tk.Label(info, text=("● 在用　" if d.get("in_use") else "○ 未用　") + reason,
                 bg=C_SURFACE, fg=(C_GREEN if d.get("in_use") else C_MUTE),
                 font=F_SMALL, anchor="w").pack(fill="x")
        right = tk.Frame(head, bg=C_SURFACE)
        right.pack(side="right", padx=14, pady=12)
        private_n = int(d.get("private_background") or 0)
        is_microsoft = any(((proc.get("classification") or {}).get("microsoft"))
                           for proc in d.get("processes") or [])
        if private_n:
            RoundedButton(right, text=f"清理 {private_n} 个私自进程", kind="primary",
                          height=32, command=self._cleanup_software).pack(pady=2)
        elif is_microsoft:
            tk.Label(right, text="微软组件 · 保留", bg=C_SURFACE, fg=C_GREEN,
                     font=F_SMALL).pack(pady=2)
        tk.Label(right, text=f"{fmt_mb(d.get('memory_mb'))} MB · "
                             f"{len(d.get('processes') or [])} 进程",
                 bg=C_SURFACE, fg=C_MUTE, font=F_SMALL).pack()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=14, pady=12)

        tab_p = ttk.Frame(nb)
        nb.add(tab_p, text=f"进程 {len(d.get('processes') or [])}")
        tree_p, bar_p = make_tree(tab_p, [
            ("pid", "PID", 70), ("name", "进程", 190), ("role", "角色", 90),
            ("activity", "活跃", 90), ("origin", "来源", 110),
            ("mark", "标记", 70), ("mem", "内存", 80),
        ], height=12)
        tree_p.pack(side="left", fill="both", expand=True)
        bar_p.pack(side="right", fill="y")
        for proc in d.get("processes") or []:
            cls = proc.get("classification") or {}
            mark = "微软" if cls.get("microsoft") else ("私自" if cls.get("private") else "")
            tree_p.insert("", "end", values=(
                proc.get("pid"), proc.get("name"), role_zh(cls.get("role")),
                activity_zh(cls.get("activity")), origin_zh(cls.get("origin")),
                mark, fmt_mb(proc.get("memory_mb"))))

        services = d.get("services") or []
        tab_s = ttk.Frame(nb)
        nb.add(tab_s, text=f"服务 {len(services)}")
        tree_s, bar_s = make_tree(tab_s, [
            ("name", "服务名", 200), ("state", "状态", 90),
            ("start", "启动", 90), ("path", "路径", 420),
        ], height=12)
        tree_s.pack(side="left", fill="both", expand=True)
        bar_s.pack(side="right", fill="y")
        for svc in services:
            if isinstance(svc, dict):
                tree_s.insert("", "end", values=(
                    svc.get("name"), svc.get("state"), svc.get("start_type"),
                    svc.get("binary_path") or "-"))
            else:
                tree_s.insert("", "end", values=(svc, "-", "-", "-"))

        autostart = d.get("autostart") or []
        tab_a = ttk.Frame(nb)
        nb.add(tab_a, text=f"自启 {len(autostart)}")
        tree_a, bar_a = make_tree(tab_a, [
            ("kind", "类型", 110), ("name", "名称", 180), ("cmd", "命令", 420),
            ("enabled", "启用", 60),
        ], height=10)
        tree_a.pack(side="left", fill="both", expand=True)
        bar_a.pack(side="right", fill="y")
        for entry in autostart:
            tree_a.insert("", "end", values=(
                entry.get("kind"), entry.get("name"), entry.get("command"),
                "是" if entry.get("enabled") else "否"))
        foot = tk.Frame(tab_a)
        foot.pack(fill="x", pady=4)
        can_disable = bool((self.app.hello.get("capabilities") or {})
                           .get("can_disable_autostart"))
        self.btn_disable = RoundedButton(foot, text="禁用选中自启项", height=30,
                                         command=lambda: self._disable(tree_a))
        self.btn_disable.pack(side="left")
        note = tk.Label(foot, fg=C_MUTE, anchor="w", bg=C_SURFACE, font=F_SMALL)
        note.pack(side="left", padx=8)
        if not can_disable:
            self.btn_disable.state(["disabled"])
            note.config(text="当前守护未提供禁用自启能力")

    def _cleanup_software(self) -> None:
        software_id = self.detail.get("id")
        if not software_id:
            return
        self.app.goto_cleanup_for(software_id)
        self.destroy()

    def _disable(self, tree: ttk.Treeview) -> None:
        sel = tree.selection()
        if not sel:
            return
        entry_id = sel[0]
        entry = next((e for e in (self.detail.get("autostart") or [])
                      if e.get("entry_id") == entry_id), None)
        if entry and not entry.get("can_disable"):
            self.app.notify("该自启项标记为不可禁用", level="warn")
            return
        from common import bg
        bg(self, lambda: self.app.rpc.call("autostart.disable", entry_id=entry_id),
           lambda _r: self.app.notify(f"已禁用自启项：{entry_id}"),
           lambda err: self.app.notify(str(err), level="error"))


def make_tree(parent, columns, height=12, selectmode="browse"):
    from common import make_tree as _make
    return _make(parent, columns, height=height, selectmode=selectmode)


def _bind_click(widget, callback) -> None:
    for child in widget.winfo_children():
        child.bind("<Button-1>", callback, add="+")
        _bind_click(child, callback)
