"""屏幕 1：总览 —— 用图说话：圆环、条形、色块卡。

数据：status.get + software.list(全部/在用/私自) + cleanup.plan（可释放内存）。
"""
from __future__ import annotations

import tkinter as tk

from common import C_MUTE, BaseScreen, fmt_mb, items_of
from appguard.models import ROLE_ZH
from theme import (C_SURFACE, C_TEXT, F_BODY, F_H2, F_SMALL,
                   Card, RoundedButton, px)
from widgets import (BarList, C_AMBER, C_BLUE, C_GRAY, C_GREEN, C_GREEN_SOFT,
                     C_PAGE, DonutChart, IconTile, ROLE_COLORS, ScrollFrame,
                     StatTile, bind_tooltip)


class OverviewScreen(BaseScreen):
    TITLE = "总览"

    def build(self) -> None:
        self._scroll = ScrollFrame(self, bg=C_PAGE)
        self._scroll.pack(fill="both", expand=True)
        body = self._scroll.interior
        body.configure(bg=C_PAGE)

        # 三个大数字（图标 + 数字 + 迷你柱）
        stats = tk.Frame(body, bg=C_PAGE)
        stats.pack(fill="x", padx=18, pady=(18, 10))
        self.tile_in_use = StatTile(stats, "apps", "在用软件", C_GREEN, C_GREEN_SOFT)
        self.tile_private = StatTile(stats, "bolt", "私自进程", C_AMBER, "#332D5A")
        self.tile_freed = StatTile(stats, "clean", "可释放", C_BLUE, "#2A2F3A")
        for index, tile in enumerate((self.tile_in_use, self.tile_private,
                                      self.tile_freed)):
            tile.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 12, 0))
            stats.grid_columnconfigure(index, weight=1)
        self.var_in_use = self.tile_in_use.var
        self.var_private = self.tile_private.var
        self.var_freed = self.tile_freed.var

        actions = tk.Frame(body, bg=C_PAGE)
        actions.pack(fill="x", padx=18, pady=(0, 12))
        self.btn_clean = RoundedButton(actions, text="清理建议", kind="primary",
                                       height=38, padx=20, command=self.app.goto_cleanup)
        self.btn_clean.pack(side="left")
        RoundedButton(actions, text="↻", height=38, min_width=48,
                      command=self.reload_manual).pack(side="left", padx=8)
        self.lbl_hint = tk.Label(actions, text="", fg=C_MUTE, bg=C_PAGE,
                                 font=F_SMALL)
        self.lbl_hint.pack(side="left", padx=8)

        middle = tk.Frame(body, bg=C_PAGE)
        middle.pack(fill="x", padx=18)
        for index in range(2):
            middle.grid_columnconfigure(index, weight=1)

        donut_card = Card(middle, radius=2)
        donut_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        donut_card.configure(height=px(292))
        head = tk.Frame(donut_card.body, bg=C_SURFACE)
        head.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(head, text="软件构成", bg=C_SURFACE, fg=C_TEXT, font=F_H2,
                 anchor="w").pack(side="left")
        self.donut = DonutChart(donut_card.body, size=px(186), thickness=px(24))
        self.donut.pack(side="left", padx=(14, 0), pady=8)
        self.legend = tk.Frame(donut_card.body, bg=C_SURFACE)
        self.legend.pack(side="left", fill="both", expand=True, padx=10, pady=18)

        bars_card = Card(middle, radius=2)
        bars_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        bars_card.configure(height=px(292))
        head2 = tk.Frame(bars_card.body, bg=C_SURFACE)
        head2.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(head2, text="内存 TOP", bg=C_SURFACE, fg=C_TEXT, font=F_H2,
                 anchor="w").pack(side="left")
        self.bars = BarList(bars_card.body, height=px(220), row_h=px(28))
        self.bars.pack(fill="both", expand=True, padx=12, pady=(4, 8))

        bottom = tk.Frame(body, bg=C_PAGE)
        bottom.pack(fill="both", expand=True, padx=18, pady=(12, 18))
        for index in range(2):
            bottom.grid_columnconfigure(index, weight=1)

        private_card = Card(bottom, radius=2)
        private_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        private_card.configure(height=px(250))
        ph = tk.Frame(private_card.body, bg=C_SURFACE)
        ph.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(ph, text="私自组件", bg=C_SURFACE, fg=C_TEXT, font=F_H2,
                 anchor="w").pack(side="left")
        self.private_grid = tk.Frame(private_card.body, bg=C_SURFACE)
        self.private_grid.pack(fill="both", expand=True, padx=10, pady=8)

        use_card = Card(bottom, radius=2)
        use_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        use_card.configure(height=px(250))
        uh = tk.Frame(use_card.body, bg=C_SURFACE)
        uh.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(uh, text="在用", bg=C_SURFACE, fg=C_TEXT, font=F_H2,
                 anchor="w").pack(side="left")
        self.use_list = tk.Frame(use_card.body, bg=C_SURFACE)
        self.use_list.pack(fill="both", expand=True, padx=12, pady=6)

        role_card = Card(body, radius=2)
        role_card.pack(fill="x", padx=18, pady=(0, 18))
        role_card.configure(height=px(176))
        rh = tk.Frame(role_card.body, bg=C_SURFACE)
        rh.pack(fill="x", padx=14, pady=(8, 0))
        tk.Label(rh, text="角色分布", bg=C_SURFACE, fg=C_TEXT, font=F_H2,
                 anchor="w").pack(side="left")
        self.role_bars = BarList(role_card.body, height=px(130), row_h=px(24))
        self.role_bars.pack(fill="both", expand=True, padx=12, pady=(2, 8))

        self._freed_computed = False
        self._apply_empty()

    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        if not self.app.connected:
            return
        self.run_bg(lambda: (self.rpc.call("status.get"),
                             items_of(self.rpc.call("software.list", limit=0)),
                             items_of(self.rpc.call("software.list", in_use=True)),
                             items_of(self.rpc.call("software.list", in_use=False,
                                                    has_private=True))),
                    self._apply)

    def reload_manual(self) -> None:
        if not self.app.connected:
            return
        self.lbl_hint.config(text="统计中…")
        self.run_bg(lambda: (self.rpc.call("status.get"),
                             items_of(self.rpc.call("software.list", limit=0)),
                             items_of(self.rpc.call("software.list", in_use=True)),
                             items_of(self.rpc.call("software.list", in_use=False,
                                                    has_private=True)),
                             self.rpc.call("cleanup.plan", scope="all")),
                    self._apply)

    def _apply(self, payload) -> None:
        status, all_sw, in_use, private = payload[0], payload[1], payload[2], payload[3]
        plan = payload[4] if len(payload) > 4 else None
        self.app.update_status(status)
        counts = status.get("counts") or {}
        self.tile_in_use.set_value(str(counts.get("in_use", len(in_use))),
                                   [len(s.get("processes") or []) for s in in_use][:10])
        self.tile_private.set_value(str(counts.get("private", 0)),
                                    [int(s.get("private_background") or 0)
                                     for s in private][:10])
        if plan is not None:
            summary = plan.get("summary") or {}
            freed = summary.get("default_memory_mb", summary.get("memory_mb"))
            self.tile_freed.set_value(fmt_mb(freed))
            self.lbl_hint.config(text="")
        else:
            self.lbl_hint.config(text="")

        # 圆环：软件构成（互斥分组：在用 → 未在用但有私自 → 其他）
        total = len(all_sw)
        in_use_count = sum(1 for s in all_sw if s.get("in_use"))
        private_count = sum(1 for s in all_sw
                            if not s.get("in_use")
                            and int(s.get("private_background") or 0) > 0)
        other = max(0, total - in_use_count - private_count)
        self.donut.set_data(
            [("在用", in_use_count, C_GREEN), ("私自", private_count, C_AMBER),
             ("其他", other, C_GRAY)],
            center_top=str(total), center_bottom="软件")
        self._render_legend([("在用", in_use_count, C_GREEN),
                             ("私自", private_count, C_AMBER),
                             ("其他", other, C_GRAY)])

        # 内存 TOP
        top = sorted(all_sw, key=lambda s: -(float(s.get("memory_mb") or 0)))[:7]
        bars = []
        for item in top:
            if item.get("in_use"):
                color = C_GREEN
            elif int(item.get("private_background") or 0) > 0:
                color = C_AMBER
            else:
                color = C_GRAY
            bars.append((str(item.get("name") or "-"),
                         float(item.get("memory_mb") or 0), color))
        self.bars.set_data(bars)

        self._render_private(private)
        self._render_in_use(in_use)
        if plan is not None:
            from collections import Counter
            counts = Counter(str(item.get("role") or "unknown")
                             for item in plan.get("items") or [])
            self.role_bars.set_data([
                (ROLE_ZH.get(role, role), value, ROLE_COLORS.get(role, C_GRAY))
                for role, value in counts.most_common(6)
            ])

    def _render_legend(self, segments) -> None:
        for child in self.legend.winfo_children():
            child.destroy()
        for label, value, color in segments:
            row = tk.Frame(self.legend, bg=C_SURFACE)
            row.pack(fill="x", pady=5)
            dot = tk.Canvas(row, width=12, height=12, bg=C_SURFACE,
                            highlightthickness=0, bd=0)
            dot.pack(side="left")
            dot.create_oval(1, 1, 11, 11, fill=color, outline=color)
            tk.Label(row, text=label, bg=C_SURFACE, fg=C_MUTE,
                     font=F_SMALL).pack(side="left", padx=6)
            tk.Label(row, text=str(value), bg=C_SURFACE, fg=C_TEXT,
                     font=(F_SMALL[0], 10, "bold")).pack(side="right")

    def _render_private(self, private) -> None:
        for child in self.private_grid.winfo_children():
            child.destroy()
        items = sorted(private, key=lambda s: -(int(s.get("private_background") or 0)))[:4]
        if not items:
            tk.Label(self.private_grid, text="—", bg=C_SURFACE, fg=C_MUTE,
                     font=F_BODY).pack(pady=20)
            return
        for index, item in enumerate(items):
            tile = tk.Frame(self.private_grid, bg=C_SURFACE, cursor="hand2")
            tile.pack(fill="x", pady=3)
            icon = IconTile(tile, str(item.get("name") or "?"), C_AMBER, "#332D5A",
                            size=px(38))
            icon.pack(side="left")
            labels = tk.Frame(tile, bg=C_SURFACE)
            labels.pack(side="left", padx=8, fill="x", expand=True)
            name = str(item.get("name") or item.get("id") or "-")
            tk.Label(labels, text=name if len(name) <= 16 else name[:15] + "…",
                     bg=C_SURFACE, fg=C_TEXT, font=F_BODY, anchor="w").pack(fill="x")
            tk.Label(labels, text=f"{item.get('private_background', 0)} 个后台组件",
                     bg=C_SURFACE, fg=C_AMBER, font=F_SMALL,
                     anchor="w").pack(fill="x")
            if item.get("in_use_reason"):
                bind_tooltip(tile, str(item["in_use_reason"]))
            for widget in (tile, icon, labels):
                widget.bind("<Button-1>", lambda _e, sid=item.get("id"):
                            self.app.goto_cleanup_for(sid), add="+")

    def _render_in_use(self, in_use) -> None:
        for child in self.use_list.winfo_children():
            child.destroy()
        if not in_use:
            tk.Label(self.use_list, text="—", bg=C_SURFACE, fg=C_MUTE,
                     font=F_BODY).pack(pady=20)
            return
        for item in in_use[:6]:
            row = tk.Frame(self.use_list, bg=C_SURFACE, cursor="arrow")
            row.pack(fill="x", pady=2)
            dot = tk.Canvas(row, width=10, height=10, bg=C_SURFACE,
                            highlightthickness=0, bd=0)
            dot.pack(side="left")
            dot.create_oval(1, 1, 9, 9, fill=C_GREEN, outline=C_GREEN)
            name = str(item.get("name") or item.get("id") or "-")
            tk.Label(row, text=name if len(name) <= 22 else name[:21] + "…",
                     bg=C_SURFACE, fg=C_TEXT, font=F_BODY,
                     anchor="w").pack(side="left", padx=6, fill="x", expand=True)
            count = len(item.get("processes") or [])
            tk.Label(row, text=f"{count}", bg=C_SURFACE, fg=C_MUTE,
                     font=F_SMALL).pack(side="right")
            bind_tooltip(row, str(item.get("in_use_reason") or ""))

    def _apply_empty(self) -> None:
        self.var_in_use.set("—")
        self.var_private.set("—")
        self.var_freed.set("—")
        self.donut.set_data([], center_top="—", center_bottom="软件")
        self.bars.set_data([])
        self.role_bars.set_data([])
        for child in self.legend.winfo_children():
            child.destroy()
        for child in self.private_grid.winfo_children():
            child.destroy()
        for child in self.use_list.winfo_children():
            child.destroy()

    def on_shown(self) -> None:
        if self.app.connected and not self._freed_computed:
            self._freed_computed = True
            self.reload_manual()

    def clear(self) -> None:
        self._freed_computed = False
        self._apply_empty()

    def set_clean_enabled(self, enabled: bool, reason: str = "") -> None:
        self.btn_clean.state(["!disabled"] if enabled else ["disabled"])
        self.lbl_hint.config(text=reason)

    def on_event(self, envelope: dict) -> None:
        if envelope.get("event") in ("snapshot.updated", "software.started",
                                     "software.stopped"):
            self.reload()
