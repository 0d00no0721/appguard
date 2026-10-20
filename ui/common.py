"""UI 公共工具：中文枚举、格式化、后台任务、通用控件。"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.normpath(_SRC))

# 契约允许 import 的另一半：中文标签字典（§6.2）
from appguard.models import ACTIVITY_ZH, ORIGIN_ZH, RISK_ZH, ROLE_ZH  # noqa: E402

from client import GuardError  # noqa: E402
from theme import (C_BAD, C_BANNER_BG, C_BANNER_FG, C_DRYRUN_BG,  # noqa: E402
                   C_DRYRUN_FG, C_MUTE, C_OK, C_WARN)

__all__ = ["C_OK", "C_WARN", "C_BAD", "C_MUTE", "C_BANNER_BG", "C_BANNER_FG",
           "C_DRYRUN_BG", "C_DRYRUN_FG"]

KIND_ZH = {"app": "应用", "game": "游戏", "runtime": "运行时",
           "driver": "驱动", "system": "系统", "unknown": "未知"}

AUDIT_EVENT_ZH = {
    "daemon.start": "守护启动", "daemon.stop": "守护停止",
    "daemon.pipe_ready": "管道就绪", "daemon.pipe_error": "管道错误",
    "daemon.tcp_ready": "TCP 就绪", "cleanup.planned": "生成计划",
    "cleanup.start": "开始清理", "cleanup.dry_run": "演练清理",
    "cleanup.executed": "清理完成", "process.blocked": "结束进程",
    "process.kill_failed": "结束失败", "service.stopped": "停止服务",
    "service.stop_failed": "停止服务失败", "config.reloaded": "重载配置",
    "kb.pending": "知识库待办", "kb.review": "知识库待办包",
    "kb.applied": "知识库入库", "kb.reloaded": "知识库重载",
    "kb.load": "知识库加载", "policy.restored": "策略恢复",
    "collect.error": "采集异常", "state.save_failed": "状态保存失败",
}

ACTION_ZH = {"kill": "结束进程", "stop_service": "停止服务",
             "cleanup": "清理", "plan": "计划", "start": "启动",
             "stop": "停止", "restore": "恢复", "reload": "重载",
             "apply": "入库", "record": "记录", "review": "待办"}
RESULT_ZH = {"ok": "成功", "success": "成功", "done": "完成",
             "skipped": "跳过", "failed": "失败", "begin": "开始",
             "partial": "部分", "warn": "警告"}


def zh(mapping: dict, value) -> str:
    """枚举 → 中文；未知值按契约前向兼容处理为「未知(原值)」。"""
    if value is None:
        return "-"
    text = mapping.get(value)
    return text if text else f"未知({value})"


def role_zh(v) -> str:
    return zh(ROLE_ZH, v)


def activity_zh(v) -> str:
    return zh(ACTIVITY_ZH, v)


def origin_zh(v) -> str:
    return zh(ORIGIN_ZH, v)


def risk_zh(v) -> str:
    return zh(RISK_ZH, v)


def fmt_mb(value) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "-"


def fmt_time(iso) -> str:
    """ISO8601 → HH:MM:SS；失败原样截断显示。"""
    if not iso:
        return "-"
    text = str(iso)
    return text[11:19] if len(text) >= 19 else text


def items_of(payload):
    """列表型返回兼容：{items,total} 或直接 list。"""
    if isinstance(payload, dict):
        return payload.get("items") or []
    if isinstance(payload, list):
        return payload
    return []


def classification_of(proc: dict) -> dict:
    return proc.get("classification") or {}


# --------------------------------------------------------------------------- #
# 后台任务：所有 IPC 调用一律走后台线程，结果经 root.after 回主线程
# --------------------------------------------------------------------------- #
def bg(root: tk.Misc, fn, on_ok=None, on_err=None) -> None:
    """在后台线程跑 fn()；成功回 on_ok(result)，失败回 on_err(GuardError)。"""
    def runner() -> None:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            err = exc if isinstance(exc, GuardError) else GuardError("E_INTERNAL", str(exc))
            if on_err:
                root.after(0, lambda: on_err(err))
            return
        if on_ok:
            root.after(0, lambda: on_ok(result))

    threading.Thread(target=runner, daemon=True).start()


# --------------------------------------------------------------------------- #
# 通用控件
# --------------------------------------------------------------------------- #
def make_tree(parent, columns: list[tuple[str, str, int]], height: int = 12,
              selectmode: str = "browse") -> tuple[ttk.Treeview, ttk.Scrollbar]:
    """columns: [(列id, 标题, 宽度)]。返回 (tree, scrollbar)。"""
    tree = ttk.Treeview(parent, columns=[c[0] for c in columns], show="headings",
                        height=height, selectmode=selectmode)
    for col_id, title, width in columns:
        tree.heading(col_id, text=title)
        tree.column(col_id, width=width, anchor="w", stretch=(col_id != columns[0][0]))
    bar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=bar.set)
    return tree, bar


def clear_tree(tree: ttk.Treeview) -> None:
    tree.delete(*tree.get_children())


class BaseScreen(ttk.Frame):
    """所有屏幕的基类：app 持有连接与全局状态。"""

    TITLE = "未命名"

    def __init__(self, master, app) -> None:
        super().__init__(master)
        self.app = app
        self.build()

    # 子类实现 ------------------------------------------------------------- #
    def build(self) -> None:
        raise NotImplementedError

    def reload(self) -> None:
        """进入屏幕或收到刷新信号时调用（内部自行走 bg）。"""

    def clear(self) -> None:
        """守护失联时清空一切已展示数据（红线 4）。"""

    # 便利 ----------------------------------------------------------------- #
    @property
    def rpc(self):
        return self.app.rpc

    def run_bg(self, fn, on_ok=None, on_err=None) -> None:
        bg(self.winfo_toplevel(), fn, on_ok,
           on_err or (lambda err: self.app.on_screen_error(self, err)))

    def on_event(self, envelope: dict) -> None:
        """事件推送（主线程）。默认忽略。"""
