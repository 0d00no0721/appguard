"""合成测试数据：把 2026-10-17「雷电模拟器弹窗」事故复刻成可回归的场景。

为什么需要它：验证环境（DSH 沙箱）只能看到 27 个进程，**覆盖不到归属聚合与"服务拉起"判定**，
而那两块恰好是踩坑最多的地方（`InstallLocation=E:\\` 吞并整盘、服务用 CreateProcessAsUser
在用户会话里拉起进程被误判为用户子进程）。这些必须用合成数据回归。
"""

from __future__ import annotations

import os

from appguard.models import AutostartEntry, ProcessInfo, ServiceRuntime

LD_DIR = r"E:\leidian\LDPlayer9"


def proc(pid: int, name: str, exe: str | None, *, ppid: int = 0, cmdline: str | None = None,
         session: int = 1, windows: list[str] | None = None, company: str | None = None,
         product: str | None = None, desc: str | None = None, memory: float = 10.0,
         started_at: str = "2026-10-17T06:00:00+08:00") -> ProcessInfo:
    return ProcessInfo(pid=pid, ppid=ppid, name=name, exe=exe, cmdline=cmdline,
                       session_id=session, windows=list(windows or []), memory_mb=memory,
                       file_company=company, file_product=product, file_description=desc,
                       started_at=started_at, cpu_seconds=1.0)


def ldplayer_scenario():
    """用户没在用雷电，但它的推广组件被后台服务定时拉起。

    关键特征（与真机取证一致）：
      * 启动项里**没有任何雷电条目** —— 所以"任务管理器→启动"页里关不掉它
      * ld29297.exe 的父进程是 ldplayerservice.exe（服务的可执行文件），但会话号是 1
      * 卸载项的 InstallLocation 正好是安装目录
      * 另有一个"陷阱"卸载项 InstallLocation = `E:\\`（复现真实踩过的坑）
    """
    processes = [
        proc(600, "services.exe", r"C:\Windows\System32\services.exe", ppid=4, session=0,
             company="Microsoft Corporation", desc="Services and Controller app", memory=8),
        proc(700, "svchost.exe", r"C:\Windows\System32\svchost.exe", ppid=600, session=0,
             cmdline=r"C:\Windows\System32\svchost.exe -k netsvcs -p", memory=40),
        proc(1000, "explorer.exe", r"C:\Windows\explorer.exe", ppid=600,
             windows=["Program Manager"], memory=120),
        # —— 用户正在用的浏览器（前台）——
        proc(2000, "chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             ppid=1000, windows=["GitHub — Google Chrome"], memory=400,
             company="Google LLC", product="Google Chrome", started_at="2026-10-17T07:00:00+08:00"),
        proc(2001, "chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             ppid=2000, cmdline='"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"'
                               " --type=renderer",
             memory=90, company="Google LLC", product="Google Chrome"),
        # —— 雷电：服务 + 它拉起的推广组件（用户没在用）——
        proc(3000, "ldplayerservice.exe", os.path.join(LD_DIR, "ldplayerservice.exe"),
             ppid=600, session=0, memory=6, company="ChangZhi Co.,Ltd."),
        proc(3001, "ld29297.exe", os.path.join(LD_DIR, "ld29297.exe"), ppid=3000,
             cmdline=f'"{os.path.join(LD_DIR, "ld29297.exe")}" --fromserver', memory=12.4),
        proc(3002, "ldplayerhelper.exe", os.path.join(LD_DIR, "ldplayerhelper.exe"), ppid=3001,
             memory=5.0),
        proc(3003, "ldplayerpartner.exe", os.path.join(LD_DIR, "ldplayerpartner.exe"), ppid=3000,
             memory=7.5),
        # —— 另一个未在用软件的主程序残留 ——
        proc(4000, "steam.exe", r"C:\Program Files (x86)\Steam\steam.exe", ppid=1000,
             memory=60, company="Valve Corporation", product="Steam"),
    ]
    services = [
        ServiceRuntime(name="LDPlayerSvr", display_name="LeiDian", state="running",
                       start_type="auto", binary_path=f'"{os.path.join(LD_DIR, "ldplayerservice.exe")}"',
                       account="LocalSystem"),
        ServiceRuntime(name="LDRemoteSvc", display_name="雷电远程", state="stopped",
                       start_type="manual", binary_path=r'"E:\leidian\remote\bin\ldremoteservice.exe"',
                       account="LocalSystem"),
        ServiceRuntime(name="wuauserv", display_name="Windows Update", state="running",
                       start_type="manual", binary_path=r"%systemroot%\system32\svchost.exe -k netsvcs -p"),
    ]
    autostart = [
        AutostartEntry(entry_id="run:HKLM\\Run:SecurityHealth", kind="registry_run",
                       name="SecurityHealth", command=r"%windir%\system32\SecurityHealthSystray.exe",
                       location="HKLM\\Run"),
    ]
    uninstall = [
        {"DisplayName": "雷电模拟器", "Publisher": "ChangZhi Co.,Ltd.", "DisplayVersion": "9.5.9.0",
         "InstallLocation": LD_DIR, "UninstallString": rf'"{os.path.join(LD_DIR, "dnuninst.exe")}"'},
        # ↓ 真实踩过的坑：某软件把安装目录写成盘符根，会把整盘程序都吞并
        {"DisplayName": "ZCode 3.11.2", "Publisher": "ZCode", "InstallLocation": "E:\\",
         "UninstallString": r'"E:\ZCode\uninst.exe"'},
        {"DisplayName": "Google Chrome", "Publisher": "Google LLC",
         "InstallLocation": r"C:\Program Files\Google\Chrome\Application"},
    ]
    return processes, services, autostart, uninstall


def build(*, processes=None, services=None, autostart=None, uninstall=None, **kwargs):
    """按雷电场景构建快照（默认 fg_pid=chrome 主进程）。

    可传入自定义 processes/services/autostart/uninstall（例如加一个广告窗口）。
    """
    from appguard import inventory
    default_processes, default_services, default_autostart, default_uninstall = \
        ldplayer_scenario()
    return inventory.build_snapshot(
        processes if processes is not None else default_processes,
        services if services is not None else default_services,
        autostart if autostart is not None else default_autostart,
        uninstall if uninstall is not None else default_uninstall,
        fg_pid=kwargs.pop("fg_pid", 2000),
        diagnostics={"synthetic": True}, **kwargs)


def find_software(snapshot, name_fragment: str):
    return next((s for s in snapshot.software if name_fragment in (s.name or "")), None)


def find_process(snapshot, name: str):
    return next((p for p in snapshot.processes if p.name == name), None)
