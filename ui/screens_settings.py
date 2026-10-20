"""屏幕 6：设置 —— policy.get/set + protect 管理 + 知识库只读展示。

红线：dry_run 改动必须二次确认；UI 不写 config.toml（那是人编辑的文件）。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from common import C_MUTE, BaseScreen, make_tree
from theme import (C_ACCENT, C_BORDER, C_SURFACE, C_TEXT, F_BODY, F_SMALL,
                   RoundedButton, SectionCard)
from widgets import ToggleSwitch


class SettingsScreen(BaseScreen):
    TITLE = "设置"

    def build(self) -> None:
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=16, pady=14)

        # ---- 策略 -------------------------------------------------------- #
        policy = SectionCard(body, "清理策略（policy.get / policy.set）",
                             auto_height=True)
        policy.pack(fill="x", pady=(0, 8))
        self.var_dry = tk.BooleanVar(value=True)
        row_dry = tk.Frame(policy.body, bg=C_SURFACE)
        row_dry.pack(fill="x", padx=8, pady=(8, 2))
        ToggleSwitch(row_dry, self.var_dry, self._toggle_dry).pack(side="left")
        tk.Label(row_dry, text="演练模式 dry_run（只记录，不真正结束进程）",
                 bg=C_SURFACE, fg=C_TEXT, font=F_BODY).pack(side="left", padx=8)
        self.chk_dry = row_dry

        row = tk.Frame(policy.body, bg=C_SURFACE)
        row.pack(fill="x", padx=8, pady=2)
        tk.Label(row, text="空闲判定窗口 min_idle_s（秒）：", bg=C_SURFACE,
                 fg=C_TEXT, font=F_BODY).pack(side="left")
        self.var_idle = tk.StringVar(value="120")
        ttk.Spinbox(row, from_=30, to=3600, textvariable=self.var_idle,
                    width=8).pack(side="left")
        RoundedButton(row, text="保存", height=30,
                      command=self._save_idle).pack(side="left", padx=8)

        self.var_idle_clean = tk.BooleanVar(value=False)
        row_idle = tk.Frame(policy.body, bg=C_SURFACE)
        row_idle.pack(fill="x", padx=8, pady=2)
        ToggleSwitch(row_idle, self.var_idle_clean,
                     self._toggle_idle_clean).pack(side="left")
        tk.Label(row_idle, text="用户离开（全局空闲）时也允许清理",
                 bg=C_SURFACE, fg=C_TEXT, font=F_BODY).pack(side="left", padx=8)
        self.var_never = tk.StringVar(value="")
        tk.Label(policy.body, textvariable=self.var_never, fg=C_MUTE,
                 bg=C_SURFACE, font=F_SMALL,
                 anchor="w", justify="left").pack(fill="x", padx=8,
                                                  pady=(2, 8))

        # ---- 保护名单 ---------------------------------------------------- #
        protect = SectionCard(body, "保护名单（命中者永不出现在清理计划里）",
                              auto_height=True)
        protect.pack(fill="x", pady=(0, 8))
        self.listbox = tk.Listbox(protect.body, height=4, relief="flat",
                                  highlightthickness=1,
                                  highlightbackground=C_BORDER,
                                  bg=C_SURFACE, fg=C_TEXT, font=F_BODY,
                                  activestyle="none",
                                  selectbackground=C_ACCENT,
                                  selectforeground=C_SURFACE)
        self.listbox.pack(fill="x", padx=8, pady=(6, 2))
        row2 = tk.Frame(protect.body, bg=C_SURFACE)
        row2.pack(fill="x", padx=8, pady=(0, 6))
        self.var_entry = tk.StringVar()
        entry = ttk.Entry(row2, textvariable=self.var_entry, width=52)
        entry.pack(side="left")
        entry.insert(0, "")
        RoundedButton(row2, text="添加保护", height=30,
                      command=self._protect_add).pack(side="left", padx=6)
        RoundedButton(row2, text="移除选中", height=30,
                      command=self._protect_remove).pack(side="left")
        tk.Label(protect.body, text="前缀：pid:1234（按进程） / sw:软件ID（按软件） / "
                               "exe:C:\\路径\\xx.exe（按可执行文件）",
                 fg=C_MUTE, bg=C_SURFACE, font=F_SMALL,
                 anchor="w").pack(fill="x", padx=8, pady=(0, 8))

        # ---- 知识库 ------------------------------------------------------ #
        kb = SectionCard(body, "分类知识库（只读）", auto_height=True)
        kb.pack(fill="x")
        row3 = tk.Frame(kb.body, bg=C_SURFACE)
        row3.pack(fill="x", padx=8, pady=(4, 8))
        RoundedButton(row3, text="查看「我们认识哪些进程」", height=30,
                      command=self._show_rules).pack(side="left")
        RoundedButton(row3, text="重载配置与知识库", height=30,
                      command=self._reload_backend).pack(side="left", padx=8)
        self.var_kb = tk.StringVar(value="")
        tk.Label(row3, textvariable=self.var_kb, fg=C_MUTE, bg=C_SURFACE,
                 font=F_SMALL).pack(side="left", padx=8)

    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        if not self.app.connected:
            return
        self.run_bg(lambda: self.rpc.call("policy.get"), self._apply_policy)

    def _apply_policy(self, policy: dict) -> None:
        self.var_dry.set(bool(policy.get("dry_run", True)))
        self.var_idle.set(str(policy.get("min_idle_s", 120)))
        self.var_idle_clean.set(bool(policy.get("allow_cleanup_while_idle", False)))
        roles = "、".join(policy.get("never_touch_roles") or [])
        self.var_never.set(f"永不清理的角色（后端口径，只读）：{roles}")
        self.listbox.delete(0, "end")
        for item in policy.get("protect") or []:
            self.listbox.insert("end", _protect_label(item))
        self.app.update_policy(policy)

    def clear(self) -> None:
        self.listbox.delete(0, "end")
        self.var_never.set("")

    # ---- 策略改动 -------------------------------------------------------- #
    def _toggle_dry(self) -> None:
        want = self.var_dry.get()
        if not want:
            ok = messagebox.askyesno(
                "关闭演练模式？",
                "关闭后，清理将真实结束进程。\n\n"
                "确定要关闭演练模式（dry_run=false）吗？",
                icon="warning", parent=self)
            if not ok:
                self.var_dry.set(True)
                return
        self.run_bg(lambda: self.rpc.call("policy.set", dry_run=want),
                    self._apply_policy)

    def _toggle_idle_clean(self) -> None:
        want = self.var_idle_clean.get()
        self.run_bg(lambda: self.rpc.call("policy.set",
                                          allow_cleanup_while_idle=want),
                    self._apply_policy)

    def _save_idle(self) -> None:
        try:
            value = int(self.var_idle.get())
        except ValueError:
            self.app.notify("min_idle_s 必须是整数秒", level="warn")
            return
        self.run_bg(lambda: self.rpc.call("policy.set", min_idle_s=value),
                    self._apply_policy)

    # ---- 保护名单 -------------------------------------------------------- #
    def _protect_add(self) -> None:
        raw = self.var_entry.get().strip()
        if not raw:
            return
        prefix, sep, value = raw.partition(":")
        if not sep or not value:
            self.app.notify("格式：pid:1234 / sw:软件ID / exe:完整路径", level="warn")
            return
        prefix = prefix.lower()
        if prefix == "pid":
            try:
                params = {"pid": int(value)}
            except ValueError:
                self.app.notify("pid: 后面必须是数字 PID", level="warn")
                return
        elif prefix == "sw":
            params = {"software_id": value}
        elif prefix == "exe":
            params = {"exe": value}
        else:
            self.app.notify("未知前缀，只支持 pid: / sw: / exe:", level="warn")
            return
        self.run_bg(lambda: self.rpc.call("protect.add", **params),
                    lambda _r: (self.var_entry.set(""), self.reload()))

    def _protect_remove(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        raw = self.listbox.get(sel[0])
        prefix, sep, value = raw.partition(":")
        params = None
        if prefix == "pid" and value.isdigit():
            params = {"pid": int(value)}
        elif prefix == "sw":
            params = {"software_id": value}
        elif prefix == "exe":
            params = {"exe": value}
        if not params:
            self.app.notify("无法解析该保护条目", level="warn")
            return
        self.run_bg(lambda: self.rpc.call("protect.remove", **params),
                    lambda _r: self.reload())

    # ---- 知识库 ---------------------------------------------------------- #
    def _show_rules(self) -> None:
        if not self.app.connected:
            return
        self.run_bg(lambda: self.rpc.call("classify.rules"), self._open_rules)

    def _open_rules(self, payload: dict) -> None:
        self.var_kb.set(f"知识库版本 {payload.get('kb_version')}")
        win = tk.Toplevel(self)
        win.title(f"分类知识库（{payload.get('kb_path') or ''}）")
        win.geometry("900x520")
        win.configure(bg=C_SURFACE)
        tree, bar = make_tree(win, [
            ("id", "规则 ID", 200), ("match", "匹配", 220),
            ("role", "角色", 90), ("note", "说明", 340)], height=20)
        tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        from common import role_zh
        for rule in payload.get("rules") or []:
            match = rule.get("match")
            tree.insert("", "end", values=(
                rule.get("id"),
                match if isinstance(match, str) else str(match),
                role_zh(rule.get("role")),
                rule.get("note") or ""))

    def _reload_backend(self) -> None:
        if not self.app.connected:
            return
        def work():
            out = []
            for method in ("daemon.reload", "classify.reload"):
                try:
                    self.rpc.call(method)
                    out.append(f"{method} 成功")
                except Exception as exc:  # noqa: BLE001
                    out.append(f"{method} 失败：{exc}")
            return "；".join(out)
        self.run_bg(work, lambda msg: self.app.notify(msg))


def _protect_label(item) -> str:
    """protect 条目 → 可解析回 protect.remove 参数的文本。"""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        if item.get("pid") is not None:
            return f"pid:{item['pid']}"
        if item.get("software_id"):
            return f"sw:{item['software_id']}"
        if item.get("exe"):
            return f"exe:{item['exe']}"
    return str(item)
