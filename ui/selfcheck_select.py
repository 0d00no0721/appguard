"""UI 冒烟 4：行级勾选 → 只执行选中的 PID（演练；非管理员也可跑）。

前置：守护已运行（`python tools\\guardctl.py serve --transport auto`）。
用法：`python ui\\selfcheck_select.py`
"""
import sys
import traceback

sys.path.insert(0, "ui")
from appguard_ui import AppGuardUI

results = []


def check(name, ok, extra=""):
    results.append((name, bool(ok), extra))
    print(("PASS" if ok else "FAIL"), name, extra, flush=True)


def _guard(fn):
    try:
        fn()
    except Exception:
        traceback.print_exc()
        check(fn.__name__, False, "异常")


def main():
    app = AppGuardUI(".appguard-dev")
    globals()["_APP"] = app
    root = app.root
    state = {}

    def t1():
        check("已连接守护", app.connected, f"transport={app.rpc.transport}")
        app.show_screen("cleanup")
        cc = app.screens["cleanup"]
        state["cc"] = cc
        cc.generate_plan()

    def t2():
        cc = state["cc"]
        check("预览已生成", cc._plan is not None)
        items = [item for item in cc._items.values() if item.get("default_checked")]
        check("有默认勾选项", len(items) >= 2, f"{len(items)} 项")
        state["expected"] = items[:2]
        cc._checked = {item["pid"] for item in state["expected"]}
        cc._refresh_checks()
        check("只勾选了两个", len(cc._checked) == 2, str(sorted(cc._checked)))
        # 测试目的：验证行级选择与 execute 链路；绕过“非管理员 degraded”对按钮的禁用。
        app.degraded = False
        cc._sync_execute()
        import unittest.mock as mock
        with mock.patch("screens_cleanup.messagebox.askyesno", return_value=True):
            cc.execute()

    def t3():
        cc = state["cc"]
        text = cc.lbl_result.cget("text")
        check("执行有结果", "成功" in text, text[:80])
        check("只处理两个", "成功 2 个" in text, text[:80])
        check("演练模式明说未真正结束", "未真正结束" in text, text[:80])
        app.ui_quit()

    delay = 0
    for ms, fn in ((1500, t1), (2500, t2), (4000, t3)):
        delay += ms
        root.after(delay, lambda f=fn: _guard(f))
    root.mainloop()
    failed = [r for r in results if not r[1]]
    print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
    for name, _ok, extra in failed:
        print("  FAIL:", name, extra)


if __name__ == "__main__":
    main()
