"""判定口径回归：in_use 只当标签、微软/关键进程保护、服务动作、默认勾选。"""

from __future__ import annotations

import fixtures as F
from harness import check, eq, section

from appguard import evaluator
from appguard.models import Policy, ProcRole


def run() -> None:
    procs, services, autostart, uninstall = F.ldplayer_scenario()
    # 正在用的 Chrome 里塞三样东西：
    #   更新器（明确私自）、辅助（在用软件，默认不勾）、微软组件（永久保护）
    procs += [
        F.proc(2010, "msedge.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
               ppid=1000, company="Microsoft Corporation", product="Microsoft Edge"),
        F.proc(2011, "svchost.exe", r"C:\Fake\svchost.exe", ppid=600, company=None),
        F.proc(2012, "evilhelper.exe",
               r"C:\Program Files\Google\Chrome\Application\evilhelper.exe",
               ppid=2000, company="Google LLC"),
        F.proc(2013, "evilupdater.exe",
               r"C:\Program Files\Google\Chrome\Application\evilupdater.exe",
               ppid=2000, company="Google LLC"),
    ]
    snap = F.build(processes=procs, services=services, autostart=autostart,
                   uninstall=uninstall, fg_pid=2000)
    plan = evaluator.build_plan(snap, Policy())
    by_name = {i.name: i for i in plan.items}
    skipped = {s.name: s.why for s in plan.protected_skipped}

    section("在用软件里也能关“明确私自”的组件")
    check("Chrome 在用", F.find_software(snap, "Google Chrome").in_use is True)
    check("Chrome 的更新器仍在计划里", "evilupdater.exe" in by_name)
    eq("更新器默认勾选（即使软件在用）", by_name["evilupdater.exe"].default_checked, True)
    eq("更新器风险 safe", by_name["evilupdater.exe"].risk.value, "safe")

    section("在用软件的普通后台组件默认不勾")
    check("在用软件的辅助组件在计划里（可手动选）", "evilhelper.exe" in by_name)
    eq("但默认不勾", by_name["evilhelper.exe"].default_checked, False)
    check("理由写明你在用", "正在用" in by_name["evilhelper.exe"].reason,
          by_name["evilhelper.exe"].reason)

    section("微软 / 关键系统进程永久保护")
    check("微软组件被跳过", "msedge.exe" in skipped and "微软" in skipped["msedge.exe"],
          str(skipped.get("msedge.exe")))
    check("关键系统进程被跳过", "svchost.exe" in skipped and "关键" in skipped["svchost.exe"],
          str(skipped.get("svchost.exe")))
    check("两者都不在计划项里", "msedge.exe" not in by_name and "svchost.exe" not in by_name)

    section("第三方服务的默认勾选随“在用”变化")
    svc = F.find_process(snap, "ldplayerservice.exe")
    policy = Policy()
    self_pids: set[int] = set()
    risk_off, _reason_off, skip_off = evaluator.grade(
        svc, policy, self_pids=self_pids, in_use=False, service_names=["LDPlayerSvr"])
    risk_on, reason_on, skip_on = evaluator.grade(
        svc, policy, self_pids=self_pids, in_use=True, service_names=["LDPlayerSvr"])
    eq("软件未在用 → 服务 safe（默认勾）", risk_off.value, "safe")
    eq("软件未在用 → 不跳过", skip_off, None)
    eq("软件在用 → 服务 caution（默认不勾）", risk_on.value, "caution")
    eq("软件在用 → 不跳过（可手动选）", skip_on, None)
    check("理由提示正在用", "正在用" in reason_on, reason_on)

    section("守护自身/父链永久保护")
    procs2, svc2, auto2, un2 = F.ldplayer_scenario()
    procs2.append(F.proc(7777, "mytool.exe", r"E:\tools\mytool.exe",
                         cmdline=r"python E:\小工具\app_guard\tools\guardctl.py"))
    snap2 = F.build(processes=procs2, services=svc2, autostart=auto2, uninstall=un2)
    plan2 = evaluator.build_plan(snap2, Policy())
    why = {s.name: s.why for s in plan2.protected_skipped}
    check("命令行提到 appguard 的进程被保护", "mytool.exe" in why
          and "守护自身" in why["mytool.exe"], str(why.get("mytool.exe")))

    section("主程序/启动器：在用时不作为清理目标")
    check("在用 Chrome 的主进程被跳过", "chrome.exe" in skipped and "正在使用" in skipped["chrome.exe"],
          str(skipped.get("chrome.exe")))
    eq("角色仍是 main", F.find_process(snap, "chrome.exe").classification.role, ProcRole.MAIN)
