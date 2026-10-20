"""守护层回归：策略持久化/脏数据、采集失败降级、计划表回收、IPC 契约校验。

这些断言全部来自 2026-10-17 的代码复查（`tools/code_review.py` + `tools/code_probe.py`），
把当时发现的缺陷锁死，防止回归。
"""

from __future__ import annotations

import json
import os

from harness import check, eq, section, work_dir
import appguard.inventory as inv
from appguard.config import load_config
from appguard.daemon import Daemon


def _clean(work: str) -> None:
    for name in ("state.json", "config.toml", "audit.jsonl"):
        path = os.path.join(work, name)
        if os.path.exists(path):
            os.remove(path)


def run() -> None:
    work = work_dir("daemon-state")

    section("策略持久化与恢复（曾经只写不读 ⇒ 重启静默丢失）")
    _clean(work)
    first = Daemon(load_config(data_dir=work))
    first.prepare()
    eq("初始 dry_run = true", first.cfg.policy.dry_run, True)
    response = first.dispatcher.dispatch({"id": 1, "method": "policy.set",
                                          "params": {"dry_run": False, "min_idle_s": 300}})
    check("policy.set 成功", response["ok"] and response["result"]["dry_run"] is False)
    check("state.json 已落盘", os.path.exists(os.path.join(work, "state.json")))

    second = Daemon(load_config(data_dir=work))
    second.prepare()
    eq("重启后 dry_run 仍是 false", second.cfg.policy.dry_run, False)
    eq("重启后 min_idle_s 仍是 300", second.cfg.policy.min_idle_s, 300)
    check("恢复动作留有审计",
          len(second.auditor.read(event="policy.restored", limit=0)) >= 1)

    section("脏 state.json 被丢弃且不崩（曾能把采集线程拖死）")
    with open(os.path.join(work, "state.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"policy": {"dry_run": "yes", "min_idle_s": "abc", "protect": ["nope"],
                              "never_touch_roles": ["不存在的角色"]}}, handle)
    third = Daemon(load_config(data_dir=work))
    third.prepare()
    eq("非法 dry_run 未生效", third.cfg.policy.dry_run, True)
    eq("非法 min_idle_s 未生效", third.cfg.policy.min_idle_s, 120)
    eq("非法 protect 未生效", third.cfg.policy.protect, [])
    note = third._policy_restore_note or {}
    check("丢弃记录已留痕", bool(note.get("dropped")), str(note))

    section("采集失败时沿用上一份快照（接口曾会整体 500）")
    fourth = Daemon(load_config(data_dir=work))
    fourth.prepare()
    snapshot = fourth.snapshot()
    check("首次采集成功", len(snapshot.processes) > 0)

    original = inv.collect

    def _boom(**_kwargs):
        raise RuntimeError("模拟采集失败")

    inv.collect = _boom                                     # type: ignore[assignment]
    try:
        again = fourth.snapshot(refresh=True)
        check("失败时返回上一份快照（不是抛异常）", again is snapshot)
        health, reason = fourth.health
        eq("health 降级", health, "degraded")
        check("降级原因写明采集异常", "模拟采集失败" in (reason or ""), str(reason))
        eq("失败已写审计",
           len(fourth.auditor.read(event="collect.error", limit=0)) >= 1, True)
        status = fourth.dispatcher.dispatch({"id": 2, "method": "status.get"})
        check("status.get 仍可用且暴露告警",
              status["ok"] and status["result"]["warnings"]["collect"])
    finally:
        inv.collect = original                              # type: ignore[assignment]
    fourth.snapshot(refresh=True)
    eq("恢复后 health 回到正常路径（非管理员 → degraded 但原因是权限）",
       "采集异常" in (fourth.health[1] or ""), False)

    section("计划表回收（曾经只增不减）")
    for _ in range(40):
        fourth.dispatcher.dispatch({"id": 3, "method": "cleanup.plan", "params": {}})
    check("计划表有上限", len(fourth._plans) <= 32, f"{len(fourth._plans)} 条")

    section("IPC 契约校验（拼错不能让清理静默空转）")
    plan_id = fourth.dispatcher.dispatch({"id": 4, "method": "cleanup.plan"})["result"]["plan_id"]

    def code_of(params: dict, method: str = "cleanup.execute") -> str | None:
        response = fourth.dispatcher.dispatch({"id": 5, "method": method, "params": params})
        return None if response["ok"] else response["error"]["code"]

    eq("include_risk 拼错 → E_BAD_REQUEST",
       code_of({"plan_id": plan_id, "include_risk": ["bogus"]}), "E_BAD_REQUEST")
    eq("include_risk 传 protected → E_BAD_REQUEST",
       code_of({"plan_id": plan_id, "include_risk": ["protected"]}), "E_BAD_REQUEST")
    eq("scope=software 缺 software_ids → E_BAD_REQUEST",
       code_of({"scope": "software"}, "cleanup.plan"), "E_BAD_REQUEST")
    eq("scope 乱写 → E_BAD_REQUEST",
       code_of({"scope": "乱写"}, "cleanup.plan"), "E_BAD_REQUEST")
    eq("policy.set min_idle_s 越界 → E_BAD_REQUEST",
       code_of({"min_idle_s": 0}, "policy.set"), "E_BAD_REQUEST")
    eq("policy.set protect 非法前缀 → E_BAD_REQUEST",
       code_of({"protect": ["whatever"]}, "policy.set"), "E_BAD_REQUEST")
    eq("audit.export 非法格式 → E_BAD_REQUEST",
       code_of({"path": os.path.join(work, "a.txt"), "format": "xml"}, "audit.export"),
       "E_BAD_REQUEST")

    section("订阅必须登记到真实连接 id（曾经登记到 stdio ⇒ 事件推不出去）")
    trace = Daemon(load_config(data_dir=work))
    trace.prepare()
    trace.dispatcher.dispatch({"id": 7, "method": "subscribe", "params": {"events": ["x"]}},
                              conn_id="tcp-abc")
    check("订阅者以连接 id 登记", "tcp-abc" in trace.bus._subscribers,
          str(list(trace.bus._subscribers)))
    check("没有制造幽灵订阅者 stdio", "stdio" not in trace.bus._subscribers,
          str(list(trace.bus._subscribers)))
    trace.dispatcher.dispatch({"id": 8, "method": "unsubscribe", "params": {}}, conn_id="tcp-abc")
    check("退订后清理干净", "tcp-abc" not in trace.bus._subscribers)

    section("知识库加载告警可见（外部文件坏了不能静默）")
    kb_dir = os.path.join(work, "kb")
    os.makedirs(kb_dir, exist_ok=True)
    with open(os.path.join(kb_dir, "known_processes.json"), "w", encoding="utf-8",
              newline="\n") as handle:
        handle.write("{ 这不是 JSON ")
    fifth = Daemon(load_config(data_dir=work))
    fifth.prepare()
    check("坏知识库有告警", any("知识库" in w for w in fifth._kb_warnings),
          str(fifth._kb_warnings))
    rules = fifth.dispatcher.dispatch({"id": 6, "method": "classify.rules"})
    check("告警通过 classify.rules 暴露",
          rules["ok"] and rules["result"]["warnings"])
    section("守护单实例标记（daemon.pid）")
    pid_work = work_dir("daemon-pid")
    pid_daemon = Daemon(load_config(data_dir=pid_work), pipe_name="AppGuard.test.pid")
    pid_daemon.start(with_server=False)
    pid_path = os.path.join(pid_work, "daemon.pid")
    check("daemon.pid 已写入", os.path.exists(pid_path))
    with open(pid_path, encoding="utf-8") as handle:
        pid_payload = json.load(handle)
    eq("daemon.pid 记录当前进程", int(pid_payload.get("pid") or 0), os.getpid())
    eq("daemon.pid 记录管道名", pid_payload.get("pipe"), "AppGuard.test.pid")
    pid_daemon.stop()
    check("stop 后 daemon.pid 清理", not os.path.exists(pid_path))
