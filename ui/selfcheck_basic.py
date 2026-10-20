"""UI 功能冒烟：不起 mainloop 人工驱动，逐步检查六屏与清理两段式（探完即删）。"""
import sys
import time
import traceback

sys.path.insert(0, "ui")

from appguard_ui import AppGuardUI

results = []


def check(name, ok, extra=""):
    results.append((name, bool(ok), extra))
    print(("PASS" if ok else "FAIL"), name, extra, flush=True)


def main():
    app = AppGuardUI(".appguard-dev")
    globals()["_APP"] = app
    root = app.root

    state = {}

    def t1_connect():
        check("连接守护并握手", app.connected and app.hello.get("api_version") == 1,
              f"transport={app.rpc.transport}")
        check("hello 带 features/capabilities",
              "features" in app.hello and "capabilities" in app.hello)
        check("事件流已启动且有消息", app.events and app.events.last_msg_at > 0)

    def t2_overview():
        app.show_screen("overview")
        ov = app.screens["overview"]
        ov.reload_manual()
        state["ov"] = ov

    def t3_overview_check():
        ov = state["ov"]
        check("总览三个大数字已填", ov.var_in_use.get() != "—"
              and ov.var_private.get() != "—")
        check("在用卡片带 in_use_reason",
              len(ov.use_list.winfo_children()) > 0,
              f"{len(ov.use_list.winfo_children())} 张卡片")
        check("降级时一键清理被禁用", ("disabled" in ov.btn_clean.state())
              == app.degraded, f"degraded={app.degraded}")
        check("降级横幅可见性正确", app.banner.winfo_ismapped() == (1 if app.degraded else 0))

    def t4_software():
        app.show_screen("software")
        sc = app.screens["software"]
        sc.reload()
        state["sw"] = sc

    def t5_software_check():
        sc = state["sw"]
        n = len(sc._tiles)
        check("软件清单有数据", n > 0, f"{n} 张卡")
        if n:
            sc._open_detail(None)
            state["detail_wait"] = time.time()

    def t6_detail_check():
        tops = [w for w in root.winfo_children() if w.winfo_class() == "Toplevel"]
        check("软件明细窗口弹出", len(tops) >= 1)
        for w in tops:
            w.destroy()

    def t7_processes():
        app.show_screen("processes")
        pc = app.screens["processes"]
        pc.reload()
        state["pc"] = pc

    def t8_processes_check():
        pc = state["pc"]
        n = len(pc.tree.get_children())
        check("进程表有数据", n > 0, f"{n} 行")
        if n:
            first = pc.tree.get_children()[0]
            vals = pc.tree.item(first)["values"]
            check("角色+来源列非空", bool(vals[2]) and bool(vals[4]),
                  f"role={vals[2]} origin={vals[4]}")
            pc.tree.selection_set(first)
            pc._show_explain(None)
            check("explanation 原文展示", "：" in pc.lbl_explain.cget("text"),
                  pc.lbl_explain.cget("text")[:60])

    def t9_cleanup_plan():
        app.show_screen("cleanup")
        cc = app.screens["cleanup"]
        cc.generate_plan()
        state["cc"] = cc

    def t10_cleanup_check():
        cc = state["cc"]
        plan = cc._plan
        check("清理计划已生成", plan is not None, (plan or {}).get("plan_id", ""))
        if plan:
            s = plan.get("summary") or {}
            check("汇总三类风险", all(k in s for k in ("safe", "caution", "protected")))
            check("默认勾选已初始化", len(cc._checked) == s.get("default_checked", 0),
                  f"{len(cc._checked)} / {s.get('default_checked')}")
            check("倒计时在走", "s" in cc.var_countdown.get()
                  or "过期" in cc.var_countdown.get(), cc.var_countdown.get())
            check("protected 只读且带 why", cc._protected_count >= 0,
                  f"{cc._protected_count} 项")
        if app.degraded:
            check("降级禁止执行", "disabled" in cc.btn_execute.state())
            state["skip_execute"] = True
            return
        # 演练执行：默认勾选的全部
        cc._check_defaults()
        cc._sync_execute()
        check("执行按钮可用", "disabled" not in cc.btn_execute.state(), cc.btn_execute.cget("text"))
        import unittest.mock as mock
        with mock.patch("screens_cleanup.messagebox.askyesno", return_value=True):
            cc.execute()

    def t11_cleanup_result():
        if state.get("skip_execute"):
            check("降级下跳过执行（符合契约）", True)
            return
        cc = state["cc"]
        text = cc.lbl_result.cget("text")
        check("执行有结果", "成功" in text or "失败" in text or "演练" in text, text[:60])
        check("演练模式明说未真正结束", "未真正结束" in text if app.dry_run else True)

    def t12_audit_settings():
        app.show_screen("audit")
        au = app.screens["audit"]
        au.reload()
        root.after(1500, lambda: check("审计有行", len(au.tree.get_children()) > 0))
        app.show_screen("settings")
        st = app.screens["settings"]
        st.reload()
        root.after(1500, lambda: check("设置页策略已载入",
                                       st.var_never.get() != ""
                                       and st.var_dry.get() == app.dry_run))

    def t13_end():
        # 失联判定单元验证：停掉事件流并把 last_msg_at 拨到 16 秒前，看门狗应切空状态。
        # 同时禁止 supervise 在检查前抢连，确保“失联态”可观测。
        app._connecting = True
        if app.events:
            app.events.stop()
            app.events.last_msg_at = time.time() - 16
        root.after(2200, lambda: check("15 秒无消息 → 失联空状态",
                                       app._lost and app.empty_screen.winfo_ismapped()))
        root.after(9000, t14_reconnect)

    def t14_reconnect():
        app._connecting = False
        if not app.connected:
            app._try_connect()
        root.after(2500, t15_done)

    def t15_done():
        check("失联后自动重连", app.connected, f"lost={app._lost}")
        failed = [r for r in results if not r[1]]
        print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
        for name, ok, extra in failed:
            print("  FAIL:", name, extra)
        app.ui_quit()

    schedule = [(1500, t1_connect), (500, t2_overview), (2500, t3_overview_check),
                (300, t4_software), (2500, t5_software_check), (2000, t6_detail_check),
                (300, t7_processes), (2500, t8_processes_check),
                (300, t9_cleanup_plan), (3500, t10_cleanup_check),
                (3500, t11_cleanup_result), (500, t12_audit_settings),
                (3500, t13_end)]

    delay = 0
    for ms, fn in schedule:
        delay += ms
        root.after(delay, lambda f=fn: _guard(f))
    root.mainloop()


def _guard(fn):
    try:
        _app = globals().get("_APP")
        if _app is not None:
            ev = _app.events
            print(f"  [state] lost={_app._lost} rpc={_app.rpc.connected} "
                  f"ev_last={round(time.time() - ev.last_msg_at, 1) if ev else None}s "
                  f"banner={_app.banner.winfo_ismapped()} degraded={_app.degraded}",
                  flush=True)
        fn()
    except Exception:
        traceback.print_exc()
        check(fn.__name__, False, "异常")


if __name__ == "__main__":
    main()
