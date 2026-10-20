"""UI 冒烟 3：冷启动无守护 → 空状态；守护上线 → 自动连接；守护断开 → 失联。

时间线（由外部编排启停守护）：
  t≈3s   断言：空状态页、无数据
  t≈5s   外部启动守护
  t≈12s  断言：已自动连接
  t≈14s  外部停止守护
  t≈32s  断言：15 秒内切回失联空状态
"""
import sys
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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
    root = app.root

    def t1_empty():
        check("冷启动为失联状态", app._lost)
        check("空状态页可见", app.empty_screen.winfo_ismapped() == 1)
        ov = app.screens["overview"]
        check("不展示历史数据", len(ov.tree_in_use.get_children()) == 0
              and ov.var_in_use.get() == "—")

    def t2_connected():
        check("守护上线后自动连接", app.connected, f"lost={app._lost}")

    def t3_lost_again():
        check("守护断开后 15 秒内失联", app._lost)
        check("失联后回到空状态页", app.empty_screen.winfo_ismapped() == 1)

    def t_end():
        failed = [r for r in results if not r[1]]
        print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==", flush=True)
        app.ui_quit()

    for ms, fn in [(3000, t1_empty), (12000, t2_connected), (32000, t3_lost_again),
                   (33500, t_end)]:
        root.after(ms, lambda f=fn: _guard(f))
    root.mainloop()


if __name__ == "__main__":
    main()
