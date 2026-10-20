"""屏幕 4：清理 —— 视觉优先：汇总圆环 + 按软件分组的勾选卡片。

* 一张卡 = 一个进程/服务，点击左侧圆圈勾选；
* 颜色：绿=已选 / 琥珀=私自 / 蓝=服务 / 灰=受保护；
* 默认勾选来自后端 `default_checked`；执行只发送 `pids`。
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import messagebox

from client import GuardError
from common import C_BAD, C_MUTE, C_OK, C_WARN, BaseScreen, fmt_mb
from theme import C_SURFACE, C_TEXT, F_BODY, F_SMALL, Card, RoundedButton, px
from widgets import (C_AMBER, C_BLUE, C_GRAY, C_GREEN, C_PAGE,
                     CheckDot, DonutChart, ScrollFrame, bind_tooltip, draw_icon)

_CHECKED = "☑"
_UNCHECKED = "☐"


class CleanupScreen(BaseScreen):
    TITLE = "清理"

    def build(self) -> None:
        self._plan: dict | None = None
        self._items: dict[int, dict] = {}
        self._checked: set[int] = set()
        self._cards: dict[int, "ProcessCard"] = {}
        self._protected_count = 0
        self._expires_ts = 0.0
        self._ticking = False
        self._scope = "all"
        self._software_ids: list[str] = []

        top = tk.Frame(self, bg=C_PAGE)
        top.pack(fill="x", padx=18, pady=(16, 4))
        self.btn_plan = RoundedButton(top, text="生成预览", kind="primary",
                                      height=34, command=self.generate_plan)
        self.btn_plan.pack(side="left")
        self.btn_default = RoundedButton(top, text="全选建议", height=30,
                                         command=self._check_defaults)
        self.btn_default.pack(side="left", padx=(8, 0))
        self.btn_clear_sel = RoundedButton(top, text="清空", height=30,
                                           command=self._clear_checked)
        self.btn_clear_sel.pack(side="left", padx=(6, 0))
        self.var_countdown = tk.StringVar(value="")
        tk.Label(top, textvariable=self.var_countdown, fg=C_MUTE, bg=C_PAGE,
                 font=F_SMALL).pack(side="left", padx=12)
        self.var_summary = tk.StringVar(value="")
        tk.Label(top, textvariable=self.var_summary, fg=C_MUTE, bg=C_PAGE,
                 font=F_SMALL).pack(side="right")

        summary = Card(self, radius=2)
        summary.pack(fill="x", padx=18, pady=(4, 8))
        summary.configure(height=px(150))
        self.donut = DonutChart(summary.body, size=px(118), thickness=px(16))
        self.donut.pack(side="left", padx=(14, 8), pady=10)
        self.summary_text = tk.Frame(summary.body, bg=C_SURFACE)
        self.summary_text.pack(side="left", fill="both", expand=True, pady=14)
        self.var_sel = tk.StringVar(value="—")
        tk.Label(self.summary_text, textvariable=self.var_sel, bg=C_SURFACE,
                 fg=C_TEXT, font=("Microsoft YaHei UI", 18, "bold"),
                 anchor="w").pack(fill="x")
        self.var_sel_sub = tk.StringVar(value="")
        tk.Label(self.summary_text, textvariable=self.var_sel_sub, bg=C_SURFACE,
                 fg=C_MUTE, font=F_SMALL, anchor="w").pack(fill="x")
        self.var_actions = tk.StringVar(value="")
        tk.Label(self.summary_text, textvariable=self.var_actions, bg=C_SURFACE,
                 fg=C_MUTE, font=F_SMALL, anchor="w").pack(fill="x", pady=(4, 0))

        self._scroll = ScrollFrame(self, bg=C_PAGE)
        self._scroll.pack(fill="both", expand=True, padx=18, pady=(0, 4))
        self.groups_frame = self._scroll.interior
        for column in range(2):
            self.groups_frame.grid_columnconfigure(column, weight=1)

        self.protected_box = tk.Frame(self, bg=C_PAGE)
        self.protected_box.pack(fill="x", padx=18, pady=(0, 2))

        foot = tk.Frame(self, bg=C_PAGE)
        foot.pack(fill="x", padx=18, pady=(2, 12))
        self.btn_execute = RoundedButton(foot, text="执行选中", kind="primary",
                                         height=38, command=self.execute)
        self.btn_execute.pack(side="left")
        self.btn_execute.state(["disabled"])
        self.lbl_result = tk.Label(foot, text="", anchor="w", justify="left",
                                   bg=C_PAGE, font=F_BODY)
        self.lbl_result.pack(side="left", padx=12)

    # ------------------------------------------------------------------ #
    def generate_plan(self, scope: str = "all", software_ids: list[str] | None = None) -> None:
        if not self.app.connected:
            return
        self._scope, self._software_ids = scope, list(software_ids or [])
        self.btn_plan.state(["disabled"])
        self.var_summary.set("生成中…")
        params = {"scope": scope}
        if scope == "software" and self._software_ids:
            params["software_ids"] = self._software_ids
        self.run_bg(lambda: self.rpc.call("cleanup.plan", **params),
                    self._apply_plan, self._plan_error)

    def _apply_plan(self, plan: dict) -> None:
        self.btn_plan.state(["!disabled"])
        self._plan = plan
        self._items = {int(item["pid"]): item for item in plan.get("items") or []}
        self._checked = {pid for pid, item in self._items.items()
                         if item.get("default_checked")}
        self._protected_count = len(plan.get("protected_skipped") or [])
        summary = plan.get("summary") or {}
        self.var_summary.set(
            f"{summary.get('total', 0)} 项 · 默认 {summary.get('default_checked', 0)} · "
            f"服务 {summary.get('stop_services', 0)}")
        self._render()
        self._expires_ts = _parse_iso(plan.get("expires_at"))
        self._ticking = True
        self._tick()
        self._sync_execute()

    def _plan_error(self, err: GuardError) -> None:
        self.btn_plan.state(["!disabled"])
        self.var_summary.set("生成失败")
        self.app.on_screen_error(self, err)

    # ------------------------------------------------------------------ #
    def _render(self) -> None:
        for child in self.groups_frame.winfo_children():
            child.destroy()
        for child in self.protected_box.winfo_children():
            child.destroy()
        self._cards = {}
        groups: dict[str, list[dict]] = {}
        order: list[str] = []
        for item in self._items.values():
            key = str(item.get("software_id") or item.get("software") or "?")
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(item)
        row = 0
        for key in order:
            items = groups[key]
            header = tk.Frame(self.groups_frame, bg=C_PAGE)
            header.grid(row=row, column=0, columnspan=2, sticky="ew",
                        pady=(8, 3))
            dot = tk.Canvas(header, width=10, height=10, bg=C_PAGE,
                            highlightthickness=0, bd=0)
            dot.pack(side="left", padx=(2, 6))
            dot.create_oval(1, 1, 9, 9, fill=C_AMBER if any(
                i.get("private") for i in items) else C_BLUE, outline="")
            tk.Label(header, text=str(items[0].get("software") or key),
                     bg=C_PAGE, fg=C_TEXT, font=(F_SMALL[0], 9, "bold"),
                     anchor="w").pack(side="left")
            tk.Label(header, text=f"{len(items)}", bg=C_PAGE, fg=C_MUTE,
                     font=F_SMALL).pack(side="left", padx=6)
            row += 1
            for index, item in enumerate(items):
                card = ProcessCard(self.groups_frame, item,
                                   checked=int(item["pid"]) in self._checked,
                                   on_toggle=self._toggle)
                if len(items) == 1:
                    card.grid(row=row, column=0, columnspan=2, sticky="ew",
                              pady=(0, 8))
                else:
                    span = 2 if (index == len(items) - 1 and len(items) % 2) else 1
                    card.grid(row=row + index // 2, column=index % 2,
                              columnspan=span, sticky="ew",
                              padx=(0 if index % 2 == 0 else 8, 0), pady=(0, 8))
                self._cards[int(item["pid"])] = card
            row += 1 if len(items) == 1 else (len(items) + 1) // 2
        self._render_protected(self._plan or {})
        if not self._items:
            tk.Label(self.groups_frame, text="没有需要处理的进程", bg=C_PAGE,
                     fg=C_MUTE, font=F_BODY).grid(row=0, column=0, columnspan=2,
                                                  pady=30)

    def _render_protected(self, plan: dict) -> None:
        skipped = plan.get("protected_skipped") or []
        if not skipped:
            return
        box = tk.Frame(self.protected_box, bg=C_PAGE)
        box.pack(fill="x")
        tk.Label(box, text=f"受保护 {len(skipped)}", bg=C_PAGE, fg=C_MUTE,
                 font=F_SMALL).pack(side="left", padx=(2, 6))
        for item in skipped[:8]:
            chip = tk.Frame(box, bg="#3B3B3B", bd=0)
            chip.pack(side="left", padx=2)
            icon = tk.Canvas(chip, width=14, height=14, bg="#3B3B3B",
                             highlightthickness=0, bd=0)
            icon.pack(side="left", padx=(6, 2))
            draw_icon(icon, "lock", 7, 7, 10, C_MUTE, 1)
            tk.Label(chip, text=str(item.get("name") or "-"), bg="#3B3B3B",
                     fg=C_MUTE, font=(F_SMALL[0], 7)).pack(side="left",
                                                              padx=(0, 6))
            bind_tooltip(chip, str(item.get("why") or "受保护"))
        if len(skipped) > 8:
            tk.Label(box, text=f"+{len(skipped) - 8}", bg=C_PAGE, fg=C_MUTE,
                     font=F_SMALL).pack(side="left", padx=4)

    # ------------------------------------------------------------------ #
    def _toggle(self, pid: int, checked: bool) -> None:
        if checked:
            self._checked.add(pid)
        else:
            self._checked.discard(pid)
        self._sync_execute()

    def _check_defaults(self) -> None:
        self._checked = {pid for pid, item in self._items.items()
                         if item.get("default_checked")}
        self._refresh_checks()

    def _clear_checked(self) -> None:
        self._checked.clear()
        self._refresh_checks()

    def _refresh_checks(self) -> None:
        for pid, card in self._cards.items():
            card.set_checked(pid in self._checked)
        self._sync_execute()

    def _selected(self) -> list[dict]:
        return [self._items[pid] for pid in sorted(self._checked)
                if pid in self._items]

    def _sync_execute(self) -> None:
        total = len(self._items)
        selected = self._selected()
        services = sum(1 for item in selected if item.get("action") == "stop_service")
        default_mem = sum(float(item.get("memory_mb") or 0) for item in selected)
        self.donut.set_data(
            [("选中", len(selected), C_GREEN),
             ("未选", max(0, total - len(selected)), "#474747")],
            center_top=str(len(selected)), center_bottom=f"共 {total}")
        self.var_sel.set(f"已选 {len(selected)} / {total}")
        self.var_sel_sub.set(f"预计释放 {fmt_mb(default_mem)} MB")
        self.var_actions.set(
            (f"停止服务 {services} · " if services else "")
            + f"受保护 {self._protected_count}")
        if self._plan is None or self.app.degraded or not self.app.connected:
            self.btn_execute.state(["disabled"])
            return
        if not selected:
            self.btn_execute.state(["disabled"])
            self.btn_execute.config(text="执行选中")
            return
        if self._expires_ts and datetime.now().timestamp() > self._expires_ts:
            self.btn_execute.state(["disabled"])
            return
        self.btn_execute.state(["!disabled"])
        suffix = f"（{len(selected)}" + (f" + {services} 服务" if services else "") + "）"
        self.btn_execute.config(text=("试运行" if self.app.dry_run else "执行") + suffix)

    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        if not self._ticking or not self.winfo_exists():
            return
        remain = int(self._expires_ts - datetime.now().timestamp())
        if remain <= 0:
            self.var_countdown.set("已过期 · 重新生成")
            self.btn_execute.state(["disabled"])
            return
        self.var_countdown.set(f"{remain}s")
        self.after(1000, self._tick)

    # ------------------------------------------------------------------ #
    def execute(self) -> None:
        selected = self._selected()
        if self._plan is None or not selected:
            return
        plan_id = self._plan.get("plan_id")
        kills = [i for i in selected if i.get("action") != "stop_service"]
        services = [s for i in selected if i.get("action") == "stop_service"
                    for s in (i.get("services") or [])]
        lines = [f"将处理 {len(selected)} 项"]
        if kills:
            lines.append(f"结束进程 {len(kills)} 个（只动勾选的 PID）")
        if services:
            lines.append(f"停止服务 {', '.join(services)}")
            lines.append("只停运行状态，不改启动类型")
        if self.app.dry_run:
            lines.append("演练模式：不会真的执行")
        if not messagebox.askyesno("确认清理", "\n".join(lines), parent=self):
            return
        self.btn_execute.state(["disabled"])
        self.lbl_result.config(text="执行中…", fg=C_MUTE)
        pids = sorted(self._checked)
        self.run_bg(lambda: self.rpc.call("cleanup.execute", plan_id=plan_id,
                                          pids=pids, timeout_ms=600000),
                    self._apply_result, self._execute_error)

    def _apply_result(self, result: dict) -> None:
        killed = result.get("killed") or []
        failed = result.get("failed") or []
        lines = []
        if result.get("dry_run"):
            lines.append("演练完成：未真正结束进程/停止服务")
        lines.append(f"成功 {len(killed)} · 失败 {len(failed)} · "
                     f"{fmt_mb(result.get('freed_mb'))} MB")
        names = "、".join(f"{k.get('name')}" for k in killed[:6])
        if names:
            lines.append("已处理：" + names + (" 等" if len(killed) > 6 else ""))
        for item in failed:
            lines.append(f"失败：{item.get('name')} — {item.get('error') or '未知'}")
        self.lbl_result.config(text="\n".join(lines), fg=C_OK if not failed else C_WARN)
        self._plan = None
        self._checked.clear()
        self.var_countdown.set("已完成 · 可重新生成")
        self._ticking = False

    def _execute_error(self, err: GuardError) -> None:
        if err.code in ("E_PLAN_EXPIRED", "E_PLAN_STALE"):
            messagebox.showinfo("计划已失效",
                                f"{err.message}\n\n已重新生成。", parent=self)
            self._plan = None
            self.generate_plan(self._scope, self._software_ids)
            return
        if err.code in ("E_DEGRADED", "E_PERMISSION"):
            messagebox.showwarning("权限不足",
                                   f"{err.message}\n\n请以管理员身份运行后再试。",
                                   parent=self)
            self._sync_execute()
            return
        self.lbl_result.config(text=f"执行失败：{err.message}", fg=C_BAD)
        self._sync_execute()

    # ------------------------------------------------------------------ #
    def on_event(self, envelope: dict) -> None:
        if envelope.get("event") == "plan.invalidated":
            data = envelope.get("data") or {}
            if self._plan and data.get("plan_id") == self._plan.get("plan_id"):
                self.generate_plan(self._scope, self._software_ids)

    def clear(self) -> None:
        self._plan = None
        self._items = {}
        self._checked.clear()
        self._cards = {}
        self._protected_count = 0
        self._scope, self._software_ids = "all", []
        self._ticking = False
        self.var_summary.set("")
        self.var_countdown.set("")
        self.var_sel.set("—")
        self.var_sel_sub.set("")
        self.var_actions.set("")
        self.donut.set_data([], center_top="—", center_bottom="")
        self.lbl_result.config(text="")
        for child in self.groups_frame.winfo_children():
            child.destroy()
        for child in self.protected_box.winfo_children():
            child.destroy()
        self.btn_execute.state(["disabled"])

    def on_shown(self) -> None:
        self._sync_execute()


class ProcessCard(Card):
    """一个进程/服务的勾选卡片。"""

    def __init__(self, master, item: dict, *, checked: bool, on_toggle):
        super().__init__(master, radius=2)
        self.item = item
        self._on_toggle = on_toggle
        self.configure(height=px(96), cursor="hand2")
        body = self.body
        action = str(item.get("action") or "kill")
        color = C_BLUE if action == "stop_service" else (
            C_AMBER if item.get("private") else C_GRAY)
        stripe = tk.Canvas(body, width=px(4), height=px(64), bg=C_SURFACE,
                           highlightthickness=0, bd=0)
        stripe.pack(side="left", padx=(6, 0), pady=8)
        stripe.create_rectangle(0, 0, px(4), px(64), fill=color, outline=color)
        self.check = CheckDot(body, checked=checked, command=self._toggled,
                              size=px(24), bg=C_SURFACE)
        self.check.pack(side="left", padx=(8, 4))
        text = tk.Frame(body, bg=C_SURFACE)
        text.pack(side="left", fill="both", expand=True, pady=6)
        top = tk.Frame(text, bg=C_SURFACE)
        top.pack(fill="x")
        name = str(item.get("name") or "-")
        tk.Label(top, text=name if len(name) <= 24 else name[:23] + "…",
                 bg=C_SURFACE, fg=C_TEXT, font=(F_SMALL[0], 9, "bold"),
                 anchor="w").pack(side="left")
        tk.Label(top, text=f"{fmt_mb(item.get('memory_mb'))}M", bg=C_SURFACE,
                 fg=C_MUTE, font=(F_SMALL[0], 7)).pack(side="right")
        meta = tk.Frame(text, bg=C_SURFACE)
        meta.pack(fill="x", pady=(2, 0))
        icon = tk.Canvas(meta, width=14, height=14, bg=C_SURFACE,
                         highlightthickness=0, bd=0)
        icon.pack(side="left")
        draw_icon(icon, "plug" if action == "stop_service" else "clean",
                  7, 7, 10, color, 1)
        tk.Label(meta, text=("停止服务" if action == "stop_service" else "结束"),
                 bg=C_SURFACE, fg=color, font=(F_SMALL[0], 7, "bold")).pack(
                     side="left", padx=(2, 8))
        tk.Label(meta, text=str(item.get("role") or "-"), bg=C_SURFACE,
                 fg=C_MUTE, font=(F_SMALL[0], 7)).pack(side="left")
        reason = str(item.get("reason") or "")
        short = reason if len(reason) <= 46 else reason[:45] + "…"
        tk.Label(text, text=short, bg=C_SURFACE, fg=C_MUTE,
                 font=(F_SMALL[0], 7), anchor="w").pack(fill="x")
        bind_tooltip(self, reason or name)
        _bind_click(self, lambda _e: self.check._click(), skip=self.check)

    def set_checked(self, value: bool) -> None:
        self.check.set_checked(value)

    def _toggled(self, value: bool) -> None:
        self._on_toggle(int(self.item["pid"]), value)


def _bind_click(widget, callback, *, skip=None) -> None:
    if widget is skip:
        return
    widget.bind("<Button-1>", callback, add="+")
    for child in widget.winfo_children():
        _bind_click(child, callback, skip=skip)


def _parse_iso(text) -> float:
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(str(text)).timestamp()
    except ValueError:
        return 0.0
