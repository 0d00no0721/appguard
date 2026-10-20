"""IPC 契约回归：policy 扁平返回、protect 两种写法、software/process 扁平、
classify.rules 版本、limit=0、计划失效、按 PID 选择。"""

from __future__ import annotations

import os
import shutil

import fixtures as F
from harness import check, eq, section, work_dir

from appguard import inventory
from appguard.config import load_config
from appguard.daemon import Daemon
from appguard.models import Classification, ProcessInfo


def _clean(work: str) -> None:
    for name in ("state.json", "config.toml", "audit.jsonl", "endpoint.json"):
        path = os.path.join(work, name)
        if os.path.exists(path):
            os.remove(path)


def run() -> None:
    work = work_dir("contract")
    _clean(work)
    daemon = Daemon(load_config(data_dir=work))
    daemon.prepare()

    snap = F.build()
    current = {"snap": snap}
    original_collect = inventory.collect
    inventory.collect = lambda **_kwargs: current["snap"]   # type: ignore[assignment]

    def call(method: str, **params) -> dict:
        response = daemon.dispatcher.dispatch(
            {"id": 1, "method": method, "params": params})
        if not response.get("ok"):
            raise AssertionError(f"{method} 失败：{response.get('error')}")
        return response["result"]

    def error(method: str, **params) -> str | None:
        response = daemon.dispatcher.dispatch(
            {"id": 1, "method": method, "params": params})
        return None if response.get("ok") else response["error"]["code"]

    try:
        section("policy.get / policy.set 是扁平结构（规范 §4.4）")
        policy = call("policy.get")
        check("policy.get 顶层就是 dry_run", "dry_run" in policy and "policy" not in policy,
              str(sorted(policy)))
        set_result = call("policy.set", dry_run=False, min_idle_s=300)
        check("policy.set 顶层就是 dry_run", "dry_run" in set_result
              and "policy" not in set_result, str(sorted(set_result)))
        eq("设置生效", set_result["dry_run"], False)
        eq("policy.get 同步", call("policy.get")["min_idle_s"], 300)
        call("policy.set", dry_run=True)

        section("protect.add / protect.remove 兼容规范写法与 pattern")
        added = call("protect.add", pid=3001)
        eq("pid 写法被规范化", added["protect"], ["pid:3001"])
        eq("重复添加不重复", call("protect.add", pattern="pid:3001")["protect"], ["pid:3001"])
        eq("按 pid 移除", call("protect.remove", pid=3001)["protect"], [])
        call("protect.add", software_id="sw-abc")
        call("protect.add", exe=r"C:\x\x.exe")
        eq("三种前缀都支持", call("policy.get")["protect"],
           ["sw:sw-abc", "exe:C:\\x\\x.exe"])
        eq("pattern 写法移除", call("protect.remove",
                                   pattern="sw:sw-abc")["protect"], ["exe:C:\\x\\x.exe"])
        call("policy.set", protect=[])

        section("software.get / process.get / snapshot.get 扁平结构")
        software = call("software.list")["items"][0]
        detail = call("software.get", id=software["id"])
        eq("software.get 顶层就是软件名", detail.get("name"), software["name"])
        check("进程明细在 processes（含 classification）",
              bool(detail.get("processes")) and "classification" in detail["processes"][0])
        eq("原始 PID 列表保留在 process_pids", detail.get("process_pids"),
           software["processes"])
        proc = call("process.get", pid=3001)
        eq("process.get 顶层就是 pid", proc.get("pid"), 3001)
        snap_payload = call("snapshot.get", refresh=True)
        check("snapshot.get 带 summary", "summary" in snap_payload
              and snap_payload["summary"]["processes"] > 0)

        section("classify.rules 带 id 与 kb_version")
        rules = call("classify.rules")
        check("kb_version 存在且形如 kb-*", str(rules.get("kb_version", "")).startswith("kb-"),
              str(rules.get("kb_version")))
        check("每条规则都有 id/match/role/note",
              all({"id", "match", "role", "note"} <= set(r) for r in rules["rules"]))
        reloaded = call("classify.reload")
        check("classify.reload 可用", "kb_version" in reloaded)

        section("limit=0 = 不限量（不是 200）")
        big = F.build()
        for index in range(300):
            big.processes.append(ProcessInfo(
                pid=900000 + index, name=f"p{index}.exe", exe=fr"C:\x\p{index}.exe",
                classification=Classification(software_id="sw-bulk")))
        current["snap"] = big
        daemon._snapshot_at = 0.0
        full = call("process.list", limit=0, refresh=True)
        eq("limit=0 返回全部", len(full["items"]), 310)
        eq("total 正确", full["total"], 310)
        eq("负数被拒", error("process.list", limit=-1), "E_BAD_REQUEST")
        current["snap"] = snap
        daemon._snapshot_at = 0.0

        section("计划失效在 IPC 路径上真的会触发（E_PLAN_STALE）")
        plan = call("cleanup.plan", refresh=True)
        plan_id = plan["plan_id"]
        victim_pid = next(i["pid"] for i in plan["items"] if i["default_checked"])
        mutated = F.build()
        mutated.processes = [p for p in mutated.processes if p.pid != victim_pid]
        current["snap"] = mutated
        eq("目标进程退出 → E_PLAN_STALE", error("cleanup.execute", plan_id=plan_id),
           "E_PLAN_STALE")
        current["snap"] = snap

        section("按 PID 选择只执行选中项")
        plan = call("cleanup.plan", refresh=True)
        selected = [i["pid"] for i in plan["items"] if i["default_checked"]][:2]
        check("计划里有至少两个默认勾选项", len(selected) == 2, str(selected))
        result = call("cleanup.execute", plan_id=plan["plan_id"], pids=selected)
        eq("只处理选中的两个", len(result["killed"]), 2)
        eq("PID 与选择一致", sorted(r["pid"] for r in result["killed"]), sorted(selected))
        check("dry-run 不真杀", result["dry_run"] is True)
        plan2 = call("cleanup.plan", refresh=True)
        eq("计划外 PID 被拒", error("cleanup.execute", plan_id=plan2["plan_id"],
                                   pids=[424242]), "E_BAD_REQUEST")
        eq("空选择被拒", error("cleanup.execute", plan_id=plan2["plan_id"], pids=[]),
           "E_BAD_REQUEST")
    finally:
        inventory.collect = original_collect                  # type: ignore[assignment]
        shutil.rmtree(os.path.join(work, "kb"), ignore_errors=True)
