"""UI 冒烟 2：健康守护下的 dry-run 全流程（预览 → 执行 → 文案）。"""
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
    globals()["_A"] = app
    root = app.root
    state = {}

    def t1():
        check("守护健康(ok)", app.connected and not app.degraded,
              f"health={app.status.get('health')}")
        check("无降级横幅", not app.banner.winfo_ismapped())
        check("dry_run 角标显示", app.badge_dry.winfo_ismapped() == 1)
        ov = app.screens["overview"]
        check("一键清理可用", "disabled" not in ov.btn_clean.state())

    def t2():
        app.goto_cleanup()   # 总览主按钮路径：跳清理屏并自动生成预览
        state["cc"] = app.screens["cleanup"]

    def t3():
        cc = state["cc"]
        check("预览已生成", cc._plan is not None)
        check("执行按钮文案为试运行", "试运行" in cc.btn_execute.cget("text"),
              cc.btn_execute.cget("text"))
        if cc._plan:
            import unittest.mock as mock
            cc._checked = {pid for pid, item in cc._items.items()
                           if item.get("default_checked")}
            cc._refresh_checks()
            check("默认勾选非空", bool(cc._checked), str(len(cc._checked)))
            check("执行按钮可用", "disabled" not in cc.btn_execute.state())
            with mock.patch("screens_cleanup.messagebox.askyesno", return_value=True):
                cc.execute()

    def t4():
        cc = state["cc"]
        text = cc.lbl_result.cget("text")
        check("执行完成有汇总", "成功" in text, text[:80])
        check("明说未真正结束任何进程", "未真正结束任何进程" in text, text[:80])

    def t5():
        # 审计应能回答「它动了什么」：最近行含 cleanup 事件
        app.show_screen("audit")
        au = app.screens["audit"]
        au.reload()
        root.after(1500, lambda: check(
            "审计含 cleanup 记录",
            any("清理" in str(au.tree.item(r)["values"][2]) or "结束" in str(au.tree.item(r)["values"][2])
                or "演练" in str(au.tree.item(r)["values"][7])
                for r in au.tree.get_children())))

    def t6():
        # 设置：dry_run 二次确认（取消 → 不变）
        app.show_screen("settings")
        st = app.screens["settings"]
        st.reload()
        def do_toggle():
            import unittest.mock as mock
            before = st.var_dry.get()
            st.var_dry.set(not before)
            with mock.patch("screens_settings.messagebox.askyesno", return_value=False):
                st._toggle_dry()
            check("二次确认取消后 dry_run 不变", st.var_dry.get() == before)
        root.after(1000, do_toggle)

    def t7():
        failed = [r for r in results if not r[1]]
        print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
        for name, ok, extra in failed:
            print("  FAIL:", name, extra)
        app.ui_quit()

    schedule = [(2000, t1), (500, t2), (2000, t3), (4000, t4), (1000, t5),
                (3000, t6), (3000, t7)]
    delay = 0
    for ms, fn in schedule:
        delay += ms
        root.after(delay, lambda f=fn: _guard(f))
    root.mainloop()


if __name__ == "__main__":
    main()
