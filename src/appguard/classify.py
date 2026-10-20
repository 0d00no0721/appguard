"""L2 分类引擎：判定每个进程「是谁的、干嘛的、谁拉起来的」，并生成中文解释。

规则优先级（命中即停止降级，但**全部命中项都会记进 reasons**）：
  1. 知识库精确匹配（进程名）—— 最高置信度
  2. 路径特征（系统目录 / WindowsApps）
  3. 名称 / 命令行 / 文件描述 里的角色关键词
  4. 兜底：有窗口 ⇒ 主程序；否则 unknown

`explanation` 是**面向用户的中文句子**，UI 直接展示，不要重写（保证口径唯一）。
"""

from __future__ import annotations

import json
import os

from .models import ROLE_ZH, Activity, Classification, Origin, ProcRole, ProcessInfo, RiskLevel

# --------------------------------------------------------------------------- #
# 知识库：进程名(小写) → (角色, 说明)
# 说明会拼进中文解释里，直接面向用户，写得越具体越好。
# 可用外部 JSON 扩展（同结构），见 load_kb()。
# --------------------------------------------------------------------------- #
DEFAULT_KB: dict[str, tuple[str, str]] = {
    # —— 雷电模拟器（本项目取证对象）——
    "dnplayer.exe": (ProcRole.MAIN.value, "雷电模拟器主程序，你实际操作的模拟器窗口"),
    "dnmultiplayer.exe": (ProcRole.LAUNCHER.value, "雷电多开器，用来批量管理模拟器实例"),
    "dnmultiplayerex.exe": (ProcRole.LAUNCHER.value, "雷电多开器（独立版）"),
    "ld29297.exe": (ProcRole.AD.value, "雷电的推广/推送组件，由后台服务定时拉起并可能弹广告"),
    "ldplayerhelper.exe": (ProcRole.HELPER.value, "雷电辅助进程，配合主程序做窗口/输入处理"),
    "ldplayerpartner.exe": (ProcRole.AD.value, "雷电合作方推广模块，负责推荐位与活动页"),
    "ldqrcodehelper.exe": (ProcRole.HELPER.value, "雷电扫码登录辅助进程"),
    "ldplayerservice.exe": (ProcRole.SERVICE.value, "雷电后台服务（LDPlayerSvr），负责驱动与实例管理"),
    "ldremoteservice.exe": (ProcRole.SERVICE.value, "雷电远程服务，提供远程控制/投屏能力"),
    "dnremote.exe": (ProcRole.HELPER.value, "雷电远程控制组件"),
    "ldrecord.exe": (ProcRole.HELPER.value, "雷电录屏组件"),
    "dnconsole.exe": (ProcRole.HELPER.value, "雷电命令行控制工具"),
    "ldconsole.exe": (ProcRole.HELPER.value, "雷电命令行控制工具"),
    # —— 腾讯系 ——
    "qq.exe": (ProcRole.MAIN.value, "QQ 主程序"),
    "qqprotect.exe": (ProcRole.DAEMON.value, "QQ 安全防护进程，常驻后台"),
    "txplatform.exe": (ProcRole.HELPER.value, "QQ 平台支持进程"),
    "wechat.exe": (ProcRole.MAIN.value, "微信主程序"),
    "wechatappex.exe": (ProcRole.HELPER.value, "微信小程序/内置浏览器进程"),
    "wechatupdate.exe": (ProcRole.UPDATER.value, "微信更新程序"),
    "tencentdl.exe": (ProcRole.UPDATER.value, "腾讯下载/更新组件"),
    "txgameassistant.exe": (ProcRole.DAEMON.value, "腾讯手游助手（模拟器）"),
    "qqbrowser.exe": (ProcRole.MAIN.value, "QQ 浏览器"),
    "tim.exe": (ProcRole.MAIN.value, "TIM 主程序"),
    # —— 办公 / 输入法 ——
    "wps.exe": (ProcRole.MAIN.value, "WPS 文字"),
    "et.exe": (ProcRole.MAIN.value, "WPS 表格"),
    "wpp.exe": (ProcRole.MAIN.value, "WPS 演示"),
    "wpsupdate.exe": (ProcRole.UPDATER.value, "WPS 更新程序"),
    "wpscloudsvr.exe": (ProcRole.DAEMON.value, "WPS 云文档同步服务（常驻）"),
    "wpscenter.exe": (ProcRole.HELPER.value, "WPS 中心/推荐模块"),
    "ksomisc.exe": (ProcRole.HELPER.value, "WPS 配置修复工具"),
    "sgtool.exe": (ProcRole.HELPER.value, "搜狗输入法辅助/推广组件"),
    "sogouinput.exe": (ProcRole.MAIN.value, "搜狗输入法"),
    "sogoucloud.exe": (ProcRole.DAEMON.value, "搜狗输入法云服务"),
    "chsime.exe": (ProcRole.SYSTEM.value, "微软拼音输入法"),
    "winword.exe": (ProcRole.MAIN.value, "Microsoft Word"),
    "excel.exe": (ProcRole.MAIN.value, "Microsoft Excel"),
    "powerpnt.exe": (ProcRole.MAIN.value, "Microsoft PowerPoint"),
    "officeclicktorun.exe": (ProcRole.SERVICE.value, "Office 即点即用服务（更新与流式加载）"),
    "msoia.exe": (ProcRole.HELPER.value, "Office 智能服务组件"),
    "typora.exe": (ProcRole.MAIN.value, "Typora Markdown 编辑器"),
    "notepad.exe": (ProcRole.MAIN.value, "记事本"),
    "notepad++.exe": (ProcRole.MAIN.value, "Notepad++"),
    # —— 开发 ——
    "code.exe": (ProcRole.MAIN.value, "Visual Studio Code"),
    "devenv.exe": (ProcRole.MAIN.value, "Visual Studio"),
    "pycharm64.exe": (ProcRole.MAIN.value, "PyCharm"),
    "idea64.exe": (ProcRole.MAIN.value, "IntelliJ IDEA"),
    "python.exe": (ProcRole.HELPER.value, "Python 解释器进程"),
    "pythonw.exe": (ProcRole.HELPER.value, "Python 解释器进程（无控制台）"),
    "pwsh.exe": (ProcRole.MAIN.value, "PowerShell 7 终端"),
    "powershell.exe": (ProcRole.MAIN.value, "Windows PowerShell 终端"),
    "cmd.exe": (ProcRole.SYSTEM.value, "Windows 命令提示符"),
    "conhost.exe": (ProcRole.SYSTEM.value, "控制台窗口宿主"),
    "node.exe": (ProcRole.HELPER.value, "Node.js 运行时进程"),
    "java.exe": (ProcRole.HELPER.value, "Java 运行时进程"),
    "javaw.exe": (ProcRole.HELPER.value, "Java 运行时进程（无控制台）"),
    "git.exe": (ProcRole.HELPER.value, "Git 命令行"),
    "windowsterminal.exe": (ProcRole.MAIN.value, "Windows 终端"),
    # —— 浏览器 / 通信 ——
    "chrome.exe": (ProcRole.MAIN.value, "Google Chrome（含其渲染子进程）"),
    "msedge.exe": (ProcRole.MAIN.value, "Microsoft Edge（含其渲染子进程）"),
    "firefox.exe": (ProcRole.MAIN.value, "Mozilla Firefox"),
    "microsoftedgeupdate.exe": (ProcRole.UPDATER.value, "Edge 更新程序（后台常驻）"),
    "googleupdate.exe": (ProcRole.UPDATER.value, "Chrome 更新程序（后台常驻）"),
    "googlecrashhandler.exe": (ProcRole.CRASH.value, "Chrome 崩溃收集器"),
    "googlecrashhandler64.exe": (ProcRole.CRASH.value, "Chrome 崩溃收集器（64 位）"),
    "feishu.exe": (ProcRole.MAIN.value, "飞书"),
    "dingtalk.exe": (ProcRole.MAIN.value, "钉钉"),
    "dingtalkupdater.exe": (ProcRole.UPDATER.value, "钉钉更新程序"),
    "zoom.exe": (ProcRole.MAIN.value, "Zoom 会议"),
    "ms-teams.exe": (ProcRole.MAIN.value, "Microsoft Teams"),
    "telegram.exe": (ProcRole.MAIN.value, "Telegram"),
    "discord.exe": (ProcRole.MAIN.value, "Discord"),
    "aweSun.exe".lower(): (ProcRole.MAIN.value, "向日葵远程控制（AweSun）"),
    "sunloginclient.exe": (ProcRole.MAIN.value, "向日葵远程控制"),
    "todesk.exe": (ProcRole.MAIN.value, "ToDesk 远程控制"),
    "anydesk.exe": (ProcRole.MAIN.value, "AnyDesk 远程控制"),
    # —— 网盘 / 下载 ——
    "baidunetdisk.exe": (ProcRole.MAIN.value, "百度网盘"),
    "yundetect.exe": (ProcRole.DAEMON.value, "百度网盘检测服务（常驻后台）"),
    "baidunetdiskhost.exe": (ProcRole.HELPER.value, "百度网盘辅助进程"),
    "adrive.exe": (ProcRole.MAIN.value, "阿里云盘"),
    "thunder.exe": (ProcRole.MAIN.value, "迅雷"),
    "thunderplatform.exe": (ProcRole.DAEMON.value, "迅雷平台服务（常驻后台）"),
    "xldlservice.exe": (ProcRole.SERVICE.value, "迅雷下载服务"),
    "onedrive.exe": (ProcRole.DAEMON.value, "OneDrive 同步客户端"),
    "dropbox.exe": (ProcRole.DAEMON.value, "Dropbox 同步客户端"),
    # —— 安全 / 系统 ——
    "360tray.exe": (ProcRole.TRAY.value, "360 安全卫士托盘进程"),
    "360safe.exe": (ProcRole.MAIN.value, "360 安全卫士主程序"),
    "zhudongfangyu.exe": (ProcRole.DAEMON.value, "360 主动防御服务"),
    "qhsafeTray.exe".lower(): (ProcRole.TRAY.value, "360 安全卫士托盘"),
    "huorong.exe": (ProcRole.TRAY.value, "火绒安全托盘"),
    "usysdiag.exe": (ProcRole.SERVICE.value, "火绒安全服务组件"),
    "msmpeng.exe": (ProcRole.SYSTEM.value, "Microsoft Defender 反恶意软件引擎"),
    "securityhealthsystray.exe": (ProcRole.SYSTEM.value, "Windows 安全中心托盘"),
    "securityhealthservice.exe": (ProcRole.SYSTEM.value, "Windows 安全中心服务"),
    # —— 硬件 / 驱动配套 ——
    "nvcontainer.exe": (ProcRole.DAEMON.value, "NVIDIA 驱动容器进程（控制面板/服务宿主）"),
    "nvdisplay.container.exe": (ProcRole.DAEMON.value, "NVIDIA 显示驱动服务"),
    "nvsphelper64.exe": (ProcRole.HELPER.value, "NVIDIA ShadowPlay 辅助进程"),
    "rtkauduservice64.exe": (ProcRole.SERVICE.value, "Realtek 音频服务"),
    "igfxem.exe": (ProcRole.HELPER.value, "Intel 显卡辅助进程"),
    "coodesker-x64.exe": (ProcRole.DAEMON.value, "Coodesker 桌面整理工具"),
    # —— 游戏 / 平台 ——
    "steam.exe": (ProcRole.MAIN.value, "Steam 客户端"),
    "steamwebhelper.exe": (ProcRole.HELPER.value, "Steam 内置浏览器（子进程）"),
    "steamservice.exe": (ProcRole.SERVICE.value, "Steam 客户端服务"),
    "epicgameslauncher.exe": (ProcRole.MAIN.value, "Epic 游戏商城"),
    "wegame.exe": (ProcRole.MAIN.value, "WeGame"),
    # —— 其他效率工具 ——
    "everything.exe": (ProcRole.DAEMON.value, "Everything 文件搜索（常驻托盘）"),
    "snipaste.exe": (ProcRole.TRAY.value, "Snipaste 截图工具"),
    "listary.exe": (ProcRole.DAEMON.value, "Listary 快速搜索"),
    "ditto.exe": (ProcRole.TRAY.value, "Ditto 剪贴板工具"),
    "mstsc.exe": (ProcRole.MAIN.value, "Windows 远程桌面"),
    "explorer.exe": (ProcRole.SYSTEM.value, "Windows 资源管理器（桌面与文件窗口）"),
    "dwm.exe": (ProcRole.SYSTEM.value, "桌面窗口管理器（合成器）"),
    "svchost.exe": (ProcRole.SERVICE.value, "Windows 服务宿主（多个系统服务共用）"),
    "csrss.exe": (ProcRole.SYSTEM.value, "Windows 客户端/服务器运行时（关键系统进程）"),
    "wininit.exe": (ProcRole.SYSTEM.value, "Windows 启动初始化（关键系统进程）"),
    "winlogon.exe": (ProcRole.SYSTEM.value, "Windows 登录管理（关键系统进程）"),
    "services.exe": (ProcRole.SYSTEM.value, "服务控制管理器（关键系统进程）"),
    "lsass.exe": (ProcRole.SYSTEM.value, "本地安全认证（关键系统进程）"),
    "smss.exe": (ProcRole.SYSTEM.value, "会话管理器（关键系统进程）"),
    "searchindexer.exe": (ProcRole.SYSTEM.value, "Windows 搜索索引器"),
    "searchhost.exe": (ProcRole.SYSTEM.value, "Windows 搜索界面"),
    "startmenuexperiencehost.exe": (ProcRole.SYSTEM.value, "开始菜单"),
    "textinputhost.exe": (ProcRole.SYSTEM.value, "输入法/触摸键盘宿主"),
    "runtimebroker.exe": (ProcRole.SYSTEM.value, "UWP 应用权限代理"),
    "conhost.exe": (ProcRole.SYSTEM.value, "控制台窗口宿主"),
    "idle": (ProcRole.SYSTEM.value, "系统空闲进程（不是真实进程）"),
    "registry": (ProcRole.SYSTEM.value, "注册表虚拟进程"),
    "memory compression": (ProcRole.SYSTEM.value, "内存压缩"),
}

# 角色关键词：**只在进程名里**匹配。
# 教训：命令行与文件描述里有太多误伤源 —— Electron 应用子进程带 `--service-sandbox`，
# 一度把 DeepSeek Harness / EduWork 判成"系统服务"。
NAME_KEYWORDS: list[tuple[str, ProcRole]] = [
    ("update", ProcRole.UPDATER),
    ("upgrade", ProcRole.UPDATER),
    ("patch", ProcRole.UPDATER),
    ("telemetry", ProcRole.TELEMETRY),
    ("statistic", ProcRole.TELEMETRY),
    ("report", ProcRole.TELEMETRY),
    ("crashpad", ProcRole.CRASH),
    ("crashhandler", ProcRole.CRASH),
    ("crashreport", ProcRole.CRASH),
    ("werfault", ProcRole.CRASH),
    ("push", ProcRole.AD),
    ("promotion", ProcRole.AD),
    ("recommend", ProcRole.AD),
    ("advert", ProcRole.AD),
    ("market", ProcRole.AD),
    ("unins", ProcRole.INSTALLER),
    ("setup", ProcRole.INSTALLER),
    ("installer", ProcRole.INSTALLER),
    ("launcher", ProcRole.LAUNCHER),
    ("helper", ProcRole.HELPER),
    ("assist", ProcRole.HELPER),
    ("plugin", ProcRole.HELPER),
    ("tray", ProcRole.TRAY),
    ("daemon", ProcRole.DAEMON),
    ("service", ProcRole.SERVICE),
]

# 命令行里的**强特征**（只放真正有判别力的，宁可少不可滥）
CMD_RULES: list[tuple[str, ProcRole]] = [
    ("--fromserver", ProcRole.AD),           # 雷电的推广组件就长这样（本项目取证对象）
    ("--crashpad-handler", ProcRole.CRASH),
    ("--backgroundupdate", ProcRole.UPDATER),
    ("--update", ProcRole.UPDATER),
    ("--telemetry", ProcRole.TELEMETRY),
    ("--uninstall", ProcRole.INSTALLER),
]

# 文件描述里的**强特征词**：只有这些才允许用 desc 判角色。
# （泛词如 "service"/"helper" 出现在描述里会把普通主程序误判，实测踩过。）
STRONG_DESC_RULES: list[tuple[str, ProcRole]] = [
    ("updater", ProcRole.UPDATER),
    ("update", ProcRole.UPDATER),
    ("自动更新", ProcRole.UPDATER),
    ("crash report", ProcRole.CRASH),
    ("crashpad", ProcRole.CRASH),
    ("崩溃", ProcRole.CRASH),
    ("telemetry", ProcRole.TELEMETRY),
    ("telemetry", ProcRole.TELEMETRY),
]

SYSTEM_NAMES = {
    "system", "registry", "memory compression", "idle", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "fontdrvhost.exe",
    "dwm.exe", "svchost.exe", "sihost.exe", "taskhostw.exe", "explorer.exe",
    "ctfmon.exe", "conhost.exe", "runtimebroker.exe", "searchindexer.exe",
    "searchhost.exe", "startmenuexperiencehost.exe", "textinputhost.exe",
    "shellexperiencehost.exe", "applicationframehost.exe", "dllhost.exe",
    "wmiprvse.exe", "spoolsv.exe", "audiodg.exe", "lsaiso.exe",
    "backgroundtaskhost.exe", "securityhealthsystray.exe", "securityhealthservice.exe",
    "msmpeng.exe", "nissrv.exe", "widgets.exe", "widgetservice.exe",
}

_SYSTEM_PATH_PREFIXES = ("c:\\windows\\", "c:\\program files\\windowsapps\\")

# 后台类职能：它们本来就不该在没有用户意图的时候长期跑。
# `CLEAR_PRIVATE_ROLES` 是其中“即使在用该软件时也明确属于私自/无关”的那部分
# （更新、推广、遥测、崩溃收集）；其余后台角色（托盘/常驻/辅助）要用
# “软件未在用 + 来源是自启/服务”来判定，避免把正在用的运行时拆掉。
BACKGROUND_ROLES = {
    ProcRole.UPDATER, ProcRole.TELEMETRY, ProcRole.CRASH, ProcRole.AD,
    ProcRole.DAEMON, ProcRole.TRAY, ProcRole.HELPER,
}
CLEAR_PRIVATE_ROLES = {ProcRole.UPDATER, ProcRole.TELEMETRY, ProcRole.CRASH, ProcRole.AD}
BACKGROUND_ORIGINS = {Origin.AUTOSTART, Origin.SERVICE, Origin.CHILD_OF_SERVICE}

_ORIGIN_TAIL = {
    Origin.USER: "由你直接启动",
    Origin.CHILD_OF_USER: "由你正在使用的程序启动",
    Origin.AUTOSTART: "由开机自启项在后台拉起",
    Origin.SERVICE: "由后台服务拉起",
    Origin.CHILD_OF_SERVICE: "由后台服务的子进程拉起",
    Origin.UNKNOWN: "来源不明",
}

# 对外复用：evaluator 的理由文案与 classify 的解释必须同一套措辞。
ORIGIN_PHRASE = _ORIGIN_TAIL

_ACTIVITY_TAIL = {
    Activity.FOREGROUND: "当前在前台，你正在用它",
    Activity.VISIBLE: "当前有可见窗口",
    Activity.TRAY: "当前只缩在托盘里",
    Activity.BACKGROUND: "当前没有窗口，在后台跑",
    Activity.SERVICE_SESSION: "运行在服务会话里（不显示界面）",
}


def load_kb(extra_path: str | None = None,
            warnings: list[str] | None = None) -> dict[str, tuple[str, str]]:
    """知识库 = 内置 + 外部 JSON（同结构 `{name: [role, note]}`）。

    外部文件坏了**不能让守护起不来**，但也**不能静默忽略** —— 用户改完看不到效果会困惑。
    因此把失败原因收集到 `warnings` 里，由调用方（daemon）暴露到 `classify.rules` 与 `status.get`。
    """
    kb = dict(DEFAULT_KB)
    if not extra_path:
        return kb
    if not os.path.isfile(extra_path):
        return kb
    try:
        with open(extra_path, "r", encoding="utf-8") as handle:
            extra = json.load(handle)
    except Exception as exc:                                    # noqa: BLE001
        if warnings is not None:
            warnings.append(f"外部知识库读取/解析失败（{extra_path}）：{exc}")
        return kb
    if not isinstance(extra, dict):
        if warnings is not None:
            warnings.append(f"外部知识库格式不对（应为 JSON 对象）：{extra_path}")
        return kb
    added = 0
    skipped: list[str] = []
    for name, value in extra.items():
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            kb[name.lower()] = (str(value[0]), str(value[1]))
            added += 1
        elif isinstance(value, dict) and "role" in value:
            kb[name.lower()] = (str(value["role"]), str(value.get("note", "")))
            added += 1
        else:
            skipped.append(str(name))
    if skipped and warnings is not None:
        warnings.append(f"外部知识库有 {len(skipped)} 条格式不合法已跳过：{skipped[:5]}")
    if added and warnings is not None:
        warnings.append(f"外部知识库已合并 {added} 条（{extra_path}）")
    return kb


def _looks_like_system_path(exe: str) -> bool:
    low = (exe or "").lower()
    return any(low.startswith(p) for p in _SYSTEM_PATH_PREFIXES)


def _activity_of(proc: ProcessInfo, fg_pid: int | None) -> Activity:
    if proc.session_id == 0:
        return Activity.SERVICE_SESSION
    if fg_pid and proc.pid == fg_pid:
        return Activity.FOREGROUND
    if proc.windows:
        return Activity.VISIBLE
    return Activity.BACKGROUND


def _origin_of(proc: ProcessInfo, parent: ProcessInfo | None,
               autostart_hit: bool, service_hit: bool, parent_is_service: bool = False) -> Origin:
    if service_hit or proc.session_id == 0:
        return Origin.SERVICE
    # 父进程本身是某个 Windows 服务的可执行文件 ⇒ 这就是"服务拉起"。
    # 雷电站例：ldplayerservice.exe（LDPlayerSvr）用 CreateProcessAsUser 在用户会话里
    # 拉起 ld29297.exe —— 只看会话号会误判成"用户子进程"。
    if parent_is_service:
        return Origin.SERVICE
    parent_name = (proc.parent_name or (parent.name if parent else "") or "").lower()
    if autostart_hit:
        return Origin.AUTOSTART
    if parent_name in ("services.exe", "wininit.exe"):
        return Origin.SERVICE
    if parent_name.startswith("svchost") or parent_name in ("taskhostw.exe", "taskeng.exe"):
        return Origin.CHILD_OF_SERVICE
    if parent_name in ("explorer.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
                       "windowsterminal.exe", "wt.exe"):
        return Origin.USER
    if parent_name in ("runtimebroker.exe", "sihost.exe", "startmenuexperiencehost.exe"):
        return Origin.AUTOSTART
    if parent is None:
        return Origin.UNKNOWN
    return Origin.CHILD_OF_SERVICE if parent.session_id == 0 else Origin.CHILD_OF_USER


def _risk_of(role: ProcRole, proc: ProcessInfo, activity: Activity) -> RiskLevel:
    """粗粒度风险提示；L3 的 evaluator 会在此之上做最终分级。

    服务角色不在这里一刀切 `protected`：第三方服务可以按需 `stop_service`，
    微软服务由 inventory 的 `microsoft` 标记 + evaluator 保护。
    """
    if role is ProcRole.SYSTEM:
        return RiskLevel.PROTECTED
    if activity in (Activity.FOREGROUND, Activity.VISIBLE):
        return RiskLevel.CAUTION
    if role is ProcRole.SERVICE:
        return RiskLevel.CAUTION
    if role in CLEAR_PRIVATE_ROLES:
        return RiskLevel.SAFE
    return RiskLevel.CAUTION


def _build_explanation(proc: ProcessInfo, cls_role: ProcRole, activity: Activity,
                       origin: Origin, software_name: str | None, note: str | None) -> str:
    owner = f"{software_name} 的" if software_name else ""
    head = f"{owner}{ROLE_ZH.get(cls_role.value, cls_role.value)}（{proc.name}）"
    if note:
        head += f"：{note}"
    tails = [_ORIGIN_TAIL.get(origin, ""), _ACTIVITY_TAIL.get(activity, "")]
    tail = "；".join(t for t in tails if t)
    return f"{head}。{tail}。" if tail else f"{head}。"


def explain(proc: ProcessInfo, cls: Classification, software_name: str | None = None,
            note: str | None = None) -> str:
    """按当前分类结果重建中文解释（供 inventory 在调整角色后刷新文案）。"""
    return _build_explanation(proc, cls.role, cls.activity, cls.origin, software_name, note)


def classify_process(proc: ProcessInfo, *, parent: ProcessInfo | None = None,
                     software_name: str | None = None, fg_pid: int | None = None,
                     autostart_hit: bool = False, service_hit: bool = False,
                     parent_is_service: bool = False,
                     kb: dict[str, tuple[str, str]] | None = None) -> Classification:
    """给一个进程定角色 / 活跃度 / 来源，并给出中文解释。"""
    kb = kb if kb is not None else DEFAULT_KB
    name = (proc.name or "").lower()
    exe = (proc.exe or "").lower()
    cmd = (proc.cmdline or "").lower()
    desc = (proc.file_description or "").lower()

    reasons: list[str] = []
    role: ProcRole | None = None
    note: str | None = None
    confidence = 0.0

    entry = kb.get(name)
    if entry:
        try:
            role = ProcRole(entry[0])
        except ValueError:
            role = ProcRole.UNKNOWN
        note = entry[1] or None
        confidence = 0.92
        reasons.append(f"kb:{name}")

    if role is None and (name in SYSTEM_NAMES or _looks_like_system_path(exe)):
        role = ProcRole.SYSTEM
        confidence = 0.7
        reasons.append("system-path" if _looks_like_system_path(exe) else "system-name")

    if role is None:
        for keyword, mapped in NAME_KEYWORDS:
            if keyword in name:
                role = mapped
                confidence = 0.55
                reasons.append(f"kw:{keyword}")
                break
    if role is None:
        for keyword, mapped in CMD_RULES:
            if keyword in cmd:
                role = mapped
                confidence = 0.6
                reasons.append(f"cmd:{keyword}")
                break
    if role is None:
        for keyword, mapped in STRONG_DESC_RULES:
            if keyword in desc:
                role = mapped
                confidence = 0.5
                reasons.append(f"desc:{keyword}")
                break

    activity = _activity_of(proc, fg_pid)

    if role is None:
        if activity in (Activity.FOREGROUND, Activity.VISIBLE):
            role = ProcRole.MAIN
            confidence = 0.6
            reasons.append("has-window")
        else:
            role = ProcRole.UNKNOWN
            confidence = 0.3
            reasons.append("fallback")

    origin = _origin_of(proc, parent, autostart_hit, service_hit, parent_is_service)
    risk = _risk_of(role, proc, activity)
    explanation = _build_explanation(proc, role, activity, origin, software_name, note)

    return Classification(role=role, activity=activity, origin=origin, risk=risk,
                          confidence=confidence, reasons=reasons, explanation=explanation)
