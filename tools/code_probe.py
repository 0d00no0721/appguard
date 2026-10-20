"""动态探针：不信静态检查，直接拿畸形/边界输入去撞实现。

用法：python tools/code_probe.py
"""

from __future__ import annotations

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from appguard import evaluator, ipc  # noqa: E402
from appguard.config import load_config  # noqa: E402
from appguard.daemon import Daemon  # noqa: E402
from appguard.models import Policy  # noqa: E402

WORK = os.path.join(ROOT, "tests", ".work", "probe")
os.makedirs(WORK, exist_ok=True)
FAILS: list[str] = []


def probe(label: str, fn) -> None:
    try:
        result = fn()
        print(f"  [OK  ] {label}" + (f"   → {result}" if result is not None else ""))
    except Exception as exc:                                    # noqa: BLE001
        print(f"  [FAIL] {label}   → {type(exc).__name__}: {exc}")
        FAILS.append(label)


def main() -> int:
    cfg = load_config(data_dir=WORK)
    daemon = Daemon(cfg)
    daemon.prepare()
    d = daemon.dispatcher

    print("=== 1) 每个 IPC 方法都能被调用（畸形参数不应崩进程）===")
    hello = d.dispatch({"id": 1, "method": "hello", "params": {"api_version": 1}})
    print(f"  hello ok={hello['ok']}")

    for method in d.method_names:
        if method in ("daemon.shutdown", "daemon.reload"):
            continue
        response = d.dispatch({"id": 2, "method": method, "params": {}})
        code = (response.get("error") or {}).get("code")
        note = "ok" if response.get("ok") else f"err={code}"
        # 缺参数应当返回结构化错误，而不是 E_INTERNAL
        bad = (not response.get("ok")) and code in (None, ipc.E_INTERNAL)
        mark = "FAIL" if bad else "OK  "
        if bad:
            FAILS.append(f"{method} 空参数 → {note}")
        print(f"  [{mark}] {method:<20} {note}")

    print("\n=== 2) 参数类型/取值校验（脏值必须被拒，且不改动任何字段）===")

    def rejected(method: str, params: dict, code: str = "E_BAD_REQUEST"):
        """脏值必须返回结构化错误；顺便断言策略没被改动。"""
        before = d.dispatch({"id": 100, "method": "policy.get"})["result"]
        response = d.dispatch({"id": 101, "method": method, "params": params})
        after = d.dispatch({"id": 102, "method": "policy.get"})["result"]
        if response.get("ok"):
            raise AssertionError(f"脏值未被拒：{params} → {response['result']}")
        got = response["error"]["code"]
        if got != code:
            raise AssertionError(f"错误码 {got}，期望 {code}")
        if before != after:
            raise AssertionError(f"拒绝时仍改动了策略：{before} → {after}")
        return got

    probe("policy.set dry_run='yes'", lambda: rejected("policy.set", {"dry_run": "yes"}))
    probe("policy.set min_idle_s='abc'", lambda: rejected("policy.set", {"min_idle_s": "abc"}))
    probe("policy.set min_idle_s=0（越界）", lambda: rejected("policy.set", {"min_idle_s": 0}))
    probe("policy.set never_touch_roles='x'",
          lambda: rejected("policy.set", {"never_touch_roles": "x"}))
    probe("policy.set never_touch_roles=['不存在的角色']",
          lambda: rejected("policy.set", {"never_touch_roles": ["不存在的角色"]}))
    probe("policy.set protect=['whatever']",
          lambda: rejected("policy.set", {"protect": ["whatever"]}))
    probe("policy.set dry_run='false'（可接受的字符串）",
          lambda: d.dispatch({"id": 103, "method": "policy.set",
                              "params": {"dry_run": "false"}})["result"]["dry_run"])
    d.dispatch({"id": 104, "method": "policy.set", "params": {"dry_run": True}})   # 复位

    probe("protect.add 非法前缀", lambda: d.dispatch(
        {"id": 6, "method": "protect.add", "params": {"pattern": "whatever"}})["error"]["code"])
    probe("cleanup.execute 不存在的 plan", lambda: d.dispatch(
        {"id": 7, "method": "cleanup.execute", "params": {"plan_id": "nope"}})["error"]["code"])
    probe("audit.query limit=-5 被拒", lambda: d.dispatch(
        {"id": 8, "method": "audit.query",
         "params": {"limit": -5}})["error"]["code"])
    probe("audit.export 非法路径", lambda: d.dispatch(
        {"id": 9, "method": "audit.export",
         "params": {"path": "Z:\\nope\\x.jsonl"}}).get("error", {}).get("code"))
    probe("audit.export 非法格式", lambda: d.dispatch(
        {"id": 9, "method": "audit.export",
         "params": {"path": os.path.join(WORK, "a.txt"), "format": "xml"}}).get("error", {}).get("code"))
    probe("process.explain 不存在的 pid", lambda: d.dispatch(
        {"id": 10, "method": "process.explain",
         "params": {"pids": [999999]}})["result"]["items"][0]["error"])

    print("\n=== 2b) 计划参数契约（拼错的风险等级不能静默空转）===")
    good_plan = d.dispatch({"id": 20, "method": "cleanup.plan", "params": {}})["result"]["plan_id"]
    probe("cleanup.execute include_risk=['bogus']",
          lambda: rejected("cleanup.execute", {"plan_id": good_plan, "include_risk": ["bogus"]}))
    probe("cleanup.execute include_risk=['protected']",
          lambda: rejected("cleanup.execute",
                           {"plan_id": good_plan, "include_risk": ["protected"]}))
    probe("cleanup.plan scope='software' 缺 software_ids",
          lambda: rejected("cleanup.plan", {"scope": "software"}))
    probe("cleanup.plan scope='pids' 缺 pids",
          lambda: rejected("cleanup.plan", {"scope": "pids"}))
    probe("cleanup.plan scope='乱写'",
          lambda: rejected("cleanup.plan", {"scope": "乱写"}))
    probe("process.list limit=0（应视为不限量）",
          lambda: len(d.dispatch({"id": 21, "method": "process.list",
                                  "params": {"limit": 0}})["result"]["items"]) > 10)

    print("\n=== 3) 计划生命周期与内存 ===")
    sizes = []

    def make_plans() -> str:
        for _ in range(60):
            d.dispatch({"id": 11, "method": "cleanup.plan", "params": {}})
        sizes.append(len(daemon._plans))
        return f"生成 60 个计划后 _plans 大小 = {sizes[-1]}（上限 32）"

    probe("连续生成计划", make_plans)
    if not sizes or sizes[0] > 32:
        print(f"  [FAIL] 计划表未回收（{sizes[0] if sizes else '?'} 条）")
        FAILS.append("计划表无限增长")
    else:
        print(f"  [OK  ] 计划表有上限/回收（{sizes[0]} 条）")

    print("\n=== 4) 事件总线订阅者回收 ===")
    bus = daemon.bus
    for i in range(50):
        bus.subscribe(f"ghost-{i}", events=["x"], since_seq=0)
    probe("清理前订阅者数", lambda: len(bus._subscribers))
    for i in range(50):
        bus.drop(f"ghost-{i}")
    probe("drop 后订阅者数", lambda: len(bus._subscribers))

    print("\n=== 5) 审计轮转与超大查询 ===")
    probe("tail(0)", lambda: len(daemon.auditor.tail(0)))
    probe("query limit=0（全量）", lambda: len(daemon.auditor.read(limit=0)) >= 0)
    probe("query offset 超过总数", lambda: len(daemon.auditor.read(offset=10 ** 6, limit=10)))

    print("\n=== 6) 分类器边界输入 ===")
    from appguard.classify import classify_process
    from appguard.models import ProcessInfo

    probe("空名进程", lambda: classify_process(ProcessInfo(pid=1, name="")).role.value)
    probe("exe 为 None", lambda: classify_process(ProcessInfo(pid=2, name="x.exe", exe=None)).role.value)
    probe("超长命令行", lambda: classify_process(
        ProcessInfo(pid=3, name="a.exe", cmdline="x" * 100000)).origin.value)

    print("\n=== 7) 计划校验对畸形 expires_at 的态度（应 fail-closed）===")
    snap = daemon.snapshot(refresh=True)
    plan = evaluator.build_plan(snap, Policy(dry_run=True), plan_ttl_s=120)
    plan.expires_at = "不是时间"
    verdict = evaluator.validate(plan, snap)
    if verdict == "E_PLAN_EXPIRED":
        print("  [OK  ] 无法解析的过期时间 → 视为已过期（fail-closed）")
    else:
        print(f"  [FAIL] 无法解析的过期时间被判为 {verdict!r} —— 安全阀失效")
        FAILS.append("畸形 expires_at 未 fail-closed")

    print("\n=== 8) 文件描述符/线程是否泄漏 ===")
    import threading
    base_threads = threading.active_count()
    for _ in range(5):
        srv = ipc.TcpServer(d, daemon.bus)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        srv.ready.wait(3)
        client = ipc.TcpClient("127.0.0.1", srv.port, srv.token).connect()
        client.handshake()
        client.close()
        srv.stop()
    time.sleep(1.0)
    after = threading.active_count()
    print(f"  线程数 {base_threads} → {after}")
    if after > base_threads + 8:
        print("  [FAIL] 线程疑似泄漏")
        FAILS.append("TCP 线程泄漏")
    else:
        print("  [OK  ] 线程数可控")

    print("\n" + "=" * 66)
    if FAILS:
        print(f"探针发现 {len(FAILS)} 个问题：")
        for item in FAILS:
            print("  -", item)
    else:
        print("全部探针通过")
    print("=" * 66)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
