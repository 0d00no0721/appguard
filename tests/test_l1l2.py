"""L1/L2 回归（合成雷电场景）：归属聚合 + 分类 + 在用判定 + 私自标记。

这些断言在真机上跑不了（受限环境只看得见少量进程），但正是踩坑最多的地方。
"""

from __future__ import annotations

import os

import fixtures as F
from harness import check, eq, section

from appguard.models import Activity, Origin, ProcRole, RiskLevel


def run() -> None:
    snap = F.build()

    section("归属聚合")
    ld = F.find_software(snap, "雷电模拟器")
    check("找到「雷电模拟器」这个软件", ld is not None)
    if ld is None:
        return
    eq("安装目录 = 卸载项的 InstallLocation", os.path.normcase(ld.install_dir or ""),
       os.path.normcase(F.LD_DIR))
    eq("发布者取自卸载项", ld.publisher, "ChangZhi Co.,Ltd.")
    eq("版本取自卸载项", ld.version, "9.5.9.0")
    members = sorted(p.name for p in snap.processes if p.pid in set(ld.processes))
    eq("四个雷电进程全部归到同一软件", members,
       ["ld29297.exe", "ldplayerhelper.exe", "ldplayerpartner.exe", "ldplayerservice.exe"])
    check("没有被 InstallLocation=E:\\ 的陷阱项吞并", ld.name != "ZCode 3.11.2")
    eq("服务 LDPlayerSvr 挂到该软件", "LDPlayerSvr" in ld.services, True)

    chrome = F.find_software(snap, "Google Chrome")
    check("Chrome 归到自己名下而不是雷电", chrome is not None and "Chrome" in (chrome.name or ""))
    if chrome:
        eq("Chrome 只含 chrome.exe", sorted({p.name for p in snap.processes
                                              if p.pid in set(chrome.processes)}), ["chrome.exe"])

    section("分类（L2）")
    ad = F.find_process(snap, "ld29297.exe")
    eq("角色 = 推广/推送", ad.classification.role, ProcRole.AD)
    eq("来源 = 服务拉起（父进程是服务的可执行文件）", ad.classification.origin, Origin.SERVICE)
    eq("活跃度 = 后台", ad.classification.activity, Activity.BACKGROUND)
    eq("风险 = 可安全结束（默认勾选）", ad.classification.risk, RiskLevel.SAFE)
    check("标记为私自开启", ad.classification.private is True)
    check("不是微软组件", ad.classification.microsoft is False)
    check("解释是给人看的中文整句",
          "推广" in ad.classification.explanation and "服务" in ad.classification.explanation,
          ad.classification.explanation)

    svc = F.find_process(snap, "ldplayerservice.exe")
    eq("服务进程角色 = 系统服务", svc.classification.role, ProcRole.SERVICE)
    check("第三方服务标记为私自（动作会是 stop_service）", svc.classification.private is True)
    check("服务进程来源 = 服务", svc.classification.origin in (Origin.SERVICE, Origin.CHILD_OF_SERVICE))

    helper = F.find_process(snap, "ldplayerhelper.exe")
    eq("辅助进程角色 = 辅助", helper.classification.role, ProcRole.HELPER)
    eq("辅助进程继承服务链来源", helper.classification.origin, Origin.CHILD_OF_SERVICE)
    check("辅助进程标记为私自", helper.classification.private is True)

    partner = F.find_process(snap, "ldplayerpartner.exe")
    eq("合作方模块角色 = 推广/推送", partner.classification.role, ProcRole.AD)
    eq("合作方模块来源 = 服务拉起", partner.classification.origin, Origin.SERVICE)

    section("在用判定（L1）")
    eq("雷电未在用", ld.in_use, False)
    check("未在用理由是人话", "没有" in ld.in_use_reason, ld.in_use_reason)
    eq("私自后台计数 = 4（3 个组件 + 1 个服务）", ld.private_background, 4)
    if chrome:
        eq("Chrome 在用（有前台窗口）", chrome.in_use, True)
        check("在用依据写出窗口标题", "GitHub" in chrome.in_use_reason, chrome.in_use_reason)

    section("广告窗口不算“你在用”")
    procs, services, autostart, uninstall = F.ldplayer_scenario()
    for proc in procs:
        if proc.name == "ld29297.exe":
            proc.windows = ["雷电模拟器 - 活动推荐"]
    ad_snap = F.build(processes=procs, services=services, autostart=autostart,
                      uninstall=uninstall, fg_pid=3001)
    ad_sw = F.find_software(ad_snap, "雷电模拟器")
    eq("只有推广窗口时 in_use=False", ad_sw.in_use, False)
    check("理由说明是推广窗口", "推广" in ad_sw.in_use_reason, ad_sw.in_use_reason)
    eq("推广组件仍被标为私自", F.find_process(ad_snap, "ld29297.exe").classification.private, True)

    section("清理候选（L3 输入）")
    from appguard import inventory
    candidates = inventory.candidates_for_cleanup(snap)
    names = sorted(p.name for p in candidates)
    eq("候选 = 四个雷电私自组件（含第三方服务）", names,
       ["ld29297.exe", "ldplayerhelper.exe", "ldplayerpartner.exe", "ldplayerservice.exe"])
    check("在用的 Chrome 不在候选里", all(p.name != "chrome.exe" for p in candidates))
