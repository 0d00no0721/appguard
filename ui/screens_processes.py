"""屏幕 3：进程分类表 —— 「角色 + 来源」是本屏的灵魂。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from appguard.models import ACTIVITY_ZH, ORIGIN_ZH, ROLE_ZH
from common import (C_MUTE, BaseScreen, activity_zh, classification_of, clear_tree,
                    fmt_mb, items_of, make_tree, origin_zh, risk_zh, role_zh)
from theme import (C_SURFACE, C_TEXT, F_BODY, F_SMALL, Card, RoundedButton,
                   SectionCard, px)
from widgets import (C_BLUE, C_GRAY, C_PAGE, ROLE_COLORS, ProcessTreemap)

_SORTS = [("memory", "内存"), ("cpu", "CPU"), ("pid", "PID"), ("name", "名称")]


class ProcessScreen(BaseScreen):
    TITLE = "进程分类"

    def build(self) -> None:
        bar = tk.Frame(self, bg=C_PAGE)
        bar.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(bar, text="排序", bg=C_PAGE, fg=C_MUTE, font=F_SMALL).pack(side="left")
        self.var_sort = tk.StringVar(value="内存")
        ttk.Combobox(bar, textvariable=self.var_sort, state="readonly", width=6,
                     values=[label for _v, label in _SORTS]).pack(side="left", padx=4)

        tk.Label(bar, text="角色", bg=C_PAGE, fg=C_MUTE, font=F_SMALL).pack(side="left", padx=(10, 0))
        self.var_role = tk.StringVar(value="全部")
        ttk.Combobox(bar, textvariable=self.var_role, state="readonly", width=8,
                     values=["全部", *ROLE_ZH.values()]).pack(side="left", padx=4)
        tk.Label(bar, text="来源", bg=C_PAGE, fg=C_MUTE, font=F_SMALL).pack(side="left", padx=(10, 0))
        self.var_origin = tk.StringVar(value="全部")
        ttk.Combobox(bar, textvariable=self.var_origin, state="readonly", width=10,
                     values=["全部", *ORIGIN_ZH.values()]).pack(side="left", padx=4)
        tk.Label(bar, text="活跃", bg=C_PAGE, fg=C_MUTE, font=F_SMALL).pack(side="left", padx=(10, 0))
        self.var_activity = tk.StringVar(value="全部")
        ttk.Combobox(bar, textvariable=self.var_activity, state="readonly", width=8,
                     values=["全部", *ACTIVITY_ZH.values()]).pack(side="left", padx=4)
        self.var_limit = tk.StringVar(value="300")
        ttk.Entry(bar, textvariable=self.var_limit, width=5).pack(side="left", padx=(10, 4))
        RoundedButton(bar, text="↻", height=30, min_width=42,
                      command=self.reload).pack(side="right")

        map_card = Card(self, radius=2)
        map_card.pack(fill="x", padx=18, pady=(4, 8))
        map_card.configure(height=px(300))
        mh = tk.Frame(map_card.body, bg=C_SURFACE)
        mh.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(mh, text="进程地图", bg=C_SURFACE, fg=C_TEXT, font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        tk.Label(mh, text="面积 = 内存 · 色块 = 进程 · 颜色 = 角色", bg=C_SURFACE,
                 fg=C_MUTE, font=(F_SMALL[0], 7)).pack(side="left", padx=8)
        self.treemap = ProcessTreemap(map_card.body, height=px(258))
        self.treemap.pack(fill="both", expand=True, padx=8, pady=(2, 8))

        card = Card(self, radius=2)
        card.pack(fill="both", expand=True, padx=18)
        self.tree, scroll = make_tree(card.body, [
            ("pid", "PID", 70), ("name", "进程", 180), ("role", "角色", 85),
            ("activity", "活跃度", 85), ("origin", "来源", 105),
            ("mark", "标记", 70), ("mem", "内存 MB", 75),
            ("sw", "归属软件", 160), ("risk", "风险", 75),
        ], height=9)
        self.tree.pack(side="left", fill="both", expand=True,
                       padx=(6, 0), pady=6)
        scroll.pack(side="left", fill="y", pady=6, padx=(0, 6))
        self.tree.bind("<<TreeviewSelect>>", self._show_explain)

        # 展开解释面板：explanation 整句直接展示，不重写（红线 3）
        panel = SectionCard(self, auto_height=True)
        panel.pack(fill="x", padx=18, pady=(6, 14))
        self.lbl_explain = tk.Label(panel.body, text="选中一行查看解释",
                                    anchor="w", justify="left", wraplength=1020,
                                    fg=C_TEXT, bg=C_SURFACE, font=F_BODY)
        self.lbl_explain.pack(fill="x", padx=10, pady=(6, 0))
        self.lbl_reasons = tk.Label(panel.body, text="", anchor="w",
                                    justify="left", wraplength=1020,
                                    fg=C_MUTE, bg=C_SURFACE, font=F_BODY)
        self.lbl_reasons.pack(fill="x", padx=10, pady=(0, 8))

        self._by_pid: dict[int, dict] = {}
        self._sw_names: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        if not self.app.connected:
            return
        sort = next(v for v, label in _SORTS if label == self.var_sort.get())
        filters = {}
        self._fill_filter(filters, "role", self.var_role.get(), ROLE_ZH)
        self._fill_filter(filters, "origin", self.var_origin.get(), ORIGIN_ZH)
        self._fill_filter(filters, "activity", self.var_activity.get(), ACTIVITY_ZH)
        try:
            limit = max(1, min(2000, int(self.var_limit.get())))
        except ValueError:
            limit = 300

        def work():
            procs = items_of(self.rpc.call("process.list", sort=sort,
                                           filter=filters or None, limit=limit))
            # 归属软件名映射（只读缓存）
            names = dict(self._sw_names)
            missing = {classification_of(p).get("software_id") for p in procs}
            missing.discard(None)
            if not names or any(s not in names for s in missing):
                try:
                    for sw in items_of(self.rpc.call("software.list")):
                        names[sw.get("id")] = sw.get("name") or sw.get("id")
                except Exception:  # noqa: BLE001 名字映射失败不阻塞主表
                    pass
            return procs, names

        self.run_bg(work, self._apply)

    @staticmethod
    def _fill_filter(filters: dict, key: str, label: str, mapping: dict) -> None:
        if label and label != "全部":
            for value, text in mapping.items():
                if text == label:
                    filters[key] = value
                    return

    def _apply(self, payload) -> None:
        procs, self._sw_names = payload
        clear_tree(self.tree)
        self._by_pid = {}
        groups: dict[str, dict] = {}
        for proc in procs:
            cls = classification_of(proc)
            pid = proc.get("pid")
            self._by_pid[pid] = proc
            mark = "微软" if cls.get("microsoft") else ("私自" if cls.get("private") else "")
            self.tree.insert("", "end", iid=str(pid), values=(
                pid, proc.get("name"), role_zh(cls.get("role")),
                activity_zh(cls.get("activity")), origin_zh(cls.get("origin")),
                mark, fmt_mb(proc.get("memory_mb")),
                self._sw_names.get(cls.get("software_id"), cls.get("software_id") or "-"),
                risk_zh(cls.get("risk")),
            ))
            sid = str(cls.get("software_id") or "unknown")
            group = groups.setdefault(sid, {
                "name": self._sw_names.get(sid, sid), "color": C_BLUE,
                "memory": 0.0, "private": 0, "items": [],
            })
            group["memory"] += float(proc.get("memory_mb") or 0)
            if cls.get("private"):
                group["private"] += 1
            role = str(cls.get("role") or "unknown")
            group["items"].append({"pid": pid, "name": proc.get("name"),
                                   "role": role,
                                   "memory": float(proc.get("memory_mb") or 0),
                                   "color": ROLE_COLORS.get(role, C_GRAY)})
        self.treemap.set_data(list(groups.values()))

    def clear(self) -> None:
        clear_tree(self.tree)
        self._by_pid = {}
        self.treemap.set_data([])
        self.lbl_explain.config(text="选中一行查看解释")
        self.lbl_reasons.config(text="")

    # ------------------------------------------------------------------ #
    def _show_explain(self, _event) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        proc = self._by_pid.get(int(sel[0]))
        if proc is None:
            return
        cls = classification_of(proc)
        # 先展示快照里自带的解释；再调 process.explain 拿最新口径
        self._render_explain(proc.get("name"), cls)
        if self.app.connected:
            pid = proc.get("pid")
            self.run_bg(lambda: self.rpc.call("process.explain", pids=[pid]),
                        self._apply_explain)

    def _apply_explain(self, payload) -> None:
        for item in payload.get("items") or []:
            sel = self.tree.selection()
            if sel and str(item.get("pid")) == sel[0]:
                self._render_explain(item.get("name"),
                                     item.get("classification") or {})

    def _render_explain(self, name, cls: dict) -> None:
        explanation = cls.get("explanation") or "（暂无解释）"
        self.lbl_explain.config(text=f"{name}：{explanation}")
        reasons = cls.get("reasons") or []
        conf = cls.get("confidence")
        tail = f"判定依据：{'、'.join(reasons)}" if reasons else ""
        if conf is not None:
            tail += f"　（置信度 {conf}）"
        self.lbl_reasons.config(text=tail)

    def on_event(self, envelope: dict) -> None:
        if envelope.get("event") in ("process.started", "process.exited",
                                     "snapshot.updated"):
            self.reload()
