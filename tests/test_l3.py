"""L3 回归（合成数据）：风险分级 / 保护名单 / 计划时效 / 按 PID 选择 / dry-run。"""

from __future__ import annotations

import copy
import os

import fixtures as F
from harness import check, eq, section, work_dir

from appguard import cleaner, evaluator
from appguard.audit import Auditor
from appguard.models import Classification, Origin, Policy, ProcessInfo, ProcRole, RiskLevel


def run() -> None:
    snap = F.build()
    policy = Policy()                      # dry_run=True, never_touch=[system]

    section("计划与风险分级")
    plan = evaluator.build_plan(snap, policy, plan_ttl_s=120)
    by_name = {i.name: i for i in plan.items}
    check("推广组件进入计划", "ld29297.exe" in by_name)
    eq("推广组件风险 = safe", by_name["ld29297.exe"].risk, RiskLevel.SAFE)
    check("推广组件默认勾选", by_name["ld29297.exe"].default_checked is True)
    check("理由包含软件名与来源",
          "雷电" in by_name["ld29297.exe"].reason and "服务" in by_name["ld29297.exe"].reason,
          by_name["ld29297.exe"].reason)
    check("第三方服务进入计划且动作是停止服务",
          by_name.get("ldplayerservice.exe") is not None
          and by_name["ldplayerservice.exe"].action == "stop_service")
    eq("第三方服务的服务名", by_name["ldplayerservice.exe"].services, ["LDPlayerSvr"])
    eq("第三方服务默认勾选（软件未在用）",
       by_name["ldplayerservice.exe"].default_checked, True)
    check("在用软件的进程不在计划里",
          not any(i.name == "chrome.exe" for i in plan.items))
    check("未在用软件的主程序作为 caution 残留出现",
          by_name.get("steam.exe") is not None and by_name["steam.exe"].risk is RiskLevel.CAUTION)
    eq("汇总计数自洽", plan.summary["total"], len(plan.items))
    eq("默认勾选数 = safe 数", plan.summary["default_checked"], plan.summary["safe"])

    section("保护名单 / 例外放行")
    guarded = Policy(protect=[f"exe:{os.path.join(F.LD_DIR, 'ld29297.exe')}"])
    plan2 = evaluator.build_plan(snap, guarded)
    check("protect 命中后从计划消失", "ld29297.exe" not in {i.name for i in plan2.items})
    check("且出现在 protected_skipped", any(s.pid == 3001 for s in plan2.protected_skipped))

    wild = Policy(never_touch_roles=["system", "service", "ad"], allow=["pid:3001"])
    plan3 = evaluator.build_plan(snap, wild)
    check("allow 让被禁角色也允许清理", "ld29297.exe" in {i.name for i in plan3.items})
    plan4 = evaluator.build_plan(snap, Policy(never_touch_roles=["system", "service", "ad"]))
    check("没有 allow 时 ad 角色被跳过", "ld29297.exe" not in {i.name for i in plan4.items})
    check("显式把 service 放进 never_touch 时服务被保护",
          "ldplayerservice.exe" not in {i.name for i in plan4.items})

    section("计划时效与失效（接口规范 C7）")
    eq("刚生成的计划可执行", evaluator.validate(plan, snap), None)
    mutated = copy.deepcopy(snap)
    fake = ProcessInfo(pid=999999, name="fake_updater.exe", exe=r"C:\fake\fake_updater.exe")
    fake.classification = Classification(role=ProcRole.UPDATER, origin=Origin.SERVICE,
                                         software_id="sw-not-in-use", risk=RiskLevel.SAFE)
    mutated.processes.append(fake)
    eq("目标集合变化 → E_PLAN_STALE", evaluator.validate(plan, mutated), "E_PLAN_STALE")
    expired = copy.deepcopy(plan)
    expired.expires_at = "2000-01-01T00:00:00+08:00"
    eq("过期 → E_PLAN_EXPIRED", evaluator.validate(expired, snap), "E_PLAN_EXPIRED")

    section("dry-run 执行")
    work = work_dir("l3")
    audit_path = os.path.join(work, "audit.jsonl")
    if os.path.exists(audit_path):
        os.remove(audit_path)                     # 保证计数断言不受历史运行影响
    auditor = Auditor(audit_path)
    safe_only = cleaner.execute(plan, snap, policy=Policy(dry_run=True), auditor=auditor,
                                include_risk=("safe",))
    eq("只清 safe：数量 = 计划里的 safe 数",
       len(safe_only.killed), plan.summary["safe"])
    check("dry-run 标记为真", safe_only.dry_run is True)
    check("dry-run 释放量>0", safe_only.freed_mb > 0)
    seq_mark = auditor.last_seq                      # 只统计第二次执行的审计行
    all_risks = cleaner.execute(plan, snap, policy=Policy(dry_run=True), auditor=auditor,
                                include_risk=("safe", "caution"))
    eq("含 caution：数量 = 全部计划项", len(all_risks.killed), len(plan.items))
    rows = [r for r in auditor.read(event="cleanup.dry_run", limit=0) if r.seq > seq_mark]
    eq("审计里每个目标一条 dry_run 记录", len(rows), len(all_risks.killed))
    check("审计序号区间连续",
          all_risks.audit_seq_from is not None
          and all_risks.audit_seq_to >= all_risks.audit_seq_from)

    section("按 PID 选择（行级勾选）")
    one = cleaner.execute(plan, snap, policy=Policy(dry_run=True), auditor=auditor,
                          selected_pids=[3001])
    eq("只选中一个 PID 时只处理一个", len(one.killed), 1)
    eq("选中的 PID 正确", one.killed[0].pid, 3001)
    service = cleaner.execute(plan, snap, policy=Policy(dry_run=True), auditor=auditor,
                              selected_pids=[3000])
    eq("选中服务项时动作是 stop_service", service.killed[0].action, "stop_service")
    caution = cleaner.execute(plan, snap, policy=Policy(dry_run=True), auditor=auditor,
                              selected_pids=[4000])
    eq("显式选中 caution 项也能执行", caution.killed[0].pid, 4000)
    eq("caution 项动作是 kill", caution.killed[0].action, "kill")
    try:
        cleaner.execute(plan, snap, policy=Policy(dry_run=True), auditor=auditor,
                        selected_pids=[424242])
        check("计划外 PID 应被拒绝", False, "没有抛错")
    except cleaner.PlanError as exc:
        eq("计划外 PID 错误码", exc.code, "E_BAD_REQUEST")

    section("过期计划拒绝执行")
    try:
        cleaner.execute(expired, snap, policy=Policy(dry_run=True), auditor=auditor)
        check("过期计划应被拦截", False, "没有抛错")
    except cleaner.PlanError as exc:
        eq("拦截错误码", exc.code, "E_PLAN_EXPIRED")
