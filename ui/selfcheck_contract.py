"""UI 冒烟 5：设置页契约（policy 扁平返回 / 保护名单增删）。

前置：守护已运行。用法：`python ui\\selfcheck_contract.py`
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
        app.show_screen("settings")
        st = app.screens["settings"]
        state["st"] = st
        st.reload()

    def t2():
        st = state["st"]
        check("策略真实值已载入（扁平结构）", st.var_dry.get() == app.dry_run,
              f"ui={st.var_dry.get()} app={app.dry_run}")
        check("never_touch_roles 已展示", "system" in st.var_never.get(), st.var_never.get())
        st.var_entry.set("pid:1234")
        st._protect_add()

    def t3():
        st = state["st"]
        values = list(st.listbox.get(0, "end"))
        check("保护名单已添加", "pid:1234" in values, str(values))
        if "pid:1234" in values:
            index = values.index("pid:1234")
            st.listbox.selection_clear(0, "end")
            st.listbox.selection_set(index)
            st._protect_remove()

    def t4():
        st = state["st"]
        values = list(st.listbox.get(0, "end"))
        check("保护名单已移除", "pid:1234" not in values, str(values))
        check("配置重载入口可用", callable(getattr(st, "_reload_backend", None)))
        app.ui_quit()

    delay = 0
    for ms, fn in ((1500, t1), (1800, t2), (1800, t3), (2200, t4)):
        delay += ms
        root.after(delay, lambda f=fn: _guard(f))
    root.mainloop()
    failed = [r for r in results if not r[1]]
    print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
    for name, _ok, extra in failed:
        print("  FAIL:", name, extra)


if __name__ == "__main__":
    main()
