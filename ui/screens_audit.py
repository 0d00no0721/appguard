"""屏幕 5：审计 —— 「它到底动了什么」的答案。"""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import filedialog, ttk

from common import (ACTION_ZH, AUDIT_EVENT_ZH, C_MUTE, RESULT_ZH, BaseScreen,
                    clear_tree, fmt_time, make_tree)
from theme import C_BG, C_SURFACE, F_SMALL, Card, RoundedButton


class AuditScreen(BaseScreen):
    TITLE = "审计"

    def build(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=16, pady=(14, 6))
        ttk.Label(bar, text="事件：").pack(side="left")
        self.var_event = tk.StringVar(value="全部")
        ttk.Combobox(bar, textvariable=self.var_event, state="readonly", width=14,
                     values=["全部", *sorted(AUDIT_EVENT_ZH)]).pack(side="left")
        ttk.Label(bar, text="　软件 ID：").pack(side="left")
        self.var_sw = tk.StringVar()
        ttk.Entry(bar, textvariable=self.var_sw, width=14).pack(side="left")
        ttk.Label(bar, text="　条数：").pack(side="left")
        self.var_limit = tk.StringVar(value="100")
        ttk.Entry(bar, textvariable=self.var_limit, width=6).pack(side="left")
        RoundedButton(bar, text="查询", height=30,
                      command=self.reload).pack(side="left", padx=8)
        RoundedButton(bar, text="导出…", height=30,
                      command=self._export).pack(side="left")
        tk.Label(bar, text="双击行看完整记录", fg=C_MUTE, bg=C_BG,
                 font=F_SMALL).pack(side="left", padx=8)

        card = Card(self, radius=2)
        card.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self.tree, scroll = make_tree(card.body, [
            ("seq", "序号", 60), ("ts", "时间", 90), ("event", "事件", 120),
            ("pid", "PID", 70), ("name", "进程", 150), ("action", "动作", 110),
            ("result", "结果", 90), ("dry", "演练", 60),
        ], height=18)
        self.tree.pack(side="left", fill="both", expand=True,
                       padx=(6, 0), pady=6)
        scroll.pack(side="left", fill="y", pady=6, padx=(0, 6))
        self.tree.tag_configure("dry", foreground=C_MUTE)
        self.tree.tag_configure("fail", foreground="#8C1414")
        self.tree.bind("<Double-1>", self._show_row)
        self._rows: dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        if not self.app.connected:
            return
        event = self.var_event.get()
        sw = self.var_sw.get().strip()
        try:
            limit = max(1, min(1000, int(self.var_limit.get())))
        except ValueError:
            limit = 100

        def work():
            if event == "全部" and not sw:
                return (self.rpc.call("audit.tail", n=limit) or {}).get("rows") or []
            return (self.rpc.call("audit.query", event=None if event == "全部" else event,
                                  software_id=sw or None, limit=limit) or {}
                    ).get("rows") or []

        self.run_bg(work, self._apply)

    def _apply(self, rows) -> None:
        clear_tree(self.tree)
        self._rows = {}
        for row in rows:
            key = f"r{row.get('seq', 0)}-{row.get('ts', '')}"
            self._rows[key] = row
            event = row.get("event") or ""
            tags = []
            if row.get("dry_run"):
                tags.append("dry")
            if row.get("result") not in (None, "ok", "success", "done"):
                tags.append("fail")
            self.tree.insert("", "end", iid=key, tags=tags, values=(
                row.get("seq"), fmt_time(row.get("ts")),
                AUDIT_EVENT_ZH.get(event, event),
                row.get("pid") or "", row.get("name") or "",
                ACTION_ZH.get(row.get("action"), row.get("action") or ""),
                RESULT_ZH.get(row.get("result"), row.get("result") or ""),
                "演练" if row.get("dry_run") else ""))

    def clear(self) -> None:
        clear_tree(self.tree)
        self._rows = {}

    # ------------------------------------------------------------------ #
    def _show_row(self, _event) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        row = self._rows.get(sel[0])
        if row is None:
            return
        win = tk.Toplevel(self)
        win.title(f"审计记录 #{row.get('seq')}")
        win.geometry("640x420")
        win.configure(bg=C_SURFACE)
        text = tk.Text(win, wrap="word", relief="flat", padx=8, pady=8)
        text.pack(fill="both", expand=True)
        text.insert("1.0", json.dumps(row, ensure_ascii=False, indent=2))
        text.config(state="disabled")

    def _export(self) -> None:
        if not self.app.connected:
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".jsonl",
            filetypes=[("JSONL", "*.jsonl"), ("CSV", "*.csv")],
            title="导出审计日志")
        if not path:
            return
        fmt = "csv" if path.lower().endswith(".csv") else "jsonl"
        self.run_bg(lambda: self.rpc.call("audit.export", path=path, format=fmt),
                    lambda r: self.app.notify(
                        f"已导出 {r.get('rows')} 条到 {r.get('path')}"))

    def on_event(self, envelope: dict) -> None:
        if envelope.get("event") in ("cleanup.executed", "process.blocked",
                                     "process.kill_failed", "service.stopped",
                                     "service.stop_failed"):
            self.reload()
