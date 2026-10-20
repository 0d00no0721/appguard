"""数据结构与枚举。

本文件是《接口规范.md》§3 的代码化：**改动必须同步文档**。
所有对象都能 `to_dict()` 成可直接 JSON 序列化的形式（枚举自动降为字符串）。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# --------------------------------------------------------------------------- #
# 枚举
# --------------------------------------------------------------------------- #
class ProcRole(str, Enum):
    """进程在所属软件里扮演的角色。"""

    MAIN = "main"
    LAUNCHER = "launcher"
    UPDATER = "updater"
    TELEMETRY = "telemetry"
    CRASH = "crash"
    AD = "ad"
    TRAY = "tray"
    HELPER = "helper"
    DAEMON = "daemon"
    SERVICE = "service"
    INSTALLER = "installer"
    SYSTEM = "system"
    UNKNOWN = "unknown"


ROLE_ZH: dict[str, str] = {
    ProcRole.MAIN.value: "主程序",
    ProcRole.LAUNCHER.value: "启动器",
    ProcRole.UPDATER.value: "更新器",
    ProcRole.TELEMETRY.value: "遥测上报",
    ProcRole.CRASH.value: "崩溃收集",
    ProcRole.AD.value: "推广/推送",
    ProcRole.TRAY.value: "托盘常驻",
    ProcRole.HELPER.value: "辅助进程",
    ProcRole.DAEMON.value: "后台常驻",
    ProcRole.SERVICE.value: "系统服务",
    ProcRole.INSTALLER.value: "安装/卸载",
    ProcRole.SYSTEM.value: "系统进程",
    ProcRole.UNKNOWN.value: "未知",
}


class Activity(str, Enum):
    """进程当前的活跃度（L1 的核心观测量）。"""

    FOREGROUND = "foreground"
    VISIBLE = "visible"
    TRAY = "tray"
    BACKGROUND = "background"
    SERVICE_SESSION = "service_session"


ACTIVITY_ZH: dict[str, str] = {
    Activity.FOREGROUND.value: "前台使用中",
    Activity.VISIBLE.value: "有可见窗口",
    Activity.TRAY.value: "仅托盘",
    Activity.BACKGROUND.value: "后台运行",
    Activity.SERVICE_SESSION.value: "服务会话",
}


class Origin(str, Enum):
    """进程是被谁拉起来的。"""

    USER = "user"
    CHILD_OF_USER = "child_of_user"
    AUTOSTART = "autostart"
    SERVICE = "service"
    CHILD_OF_SERVICE = "child_of_service"
    UNKNOWN = "unknown"


ORIGIN_ZH: dict[str, str] = {
    Origin.USER.value: "用户启动",
    Origin.CHILD_OF_USER.value: "用户程序的子进程",
    Origin.AUTOSTART.value: "开机自启",
    Origin.SERVICE.value: "服务拉起",
    Origin.CHILD_OF_SERVICE.value: "服务的子进程",
    Origin.UNKNOWN.value: "来源未知",
}


class RiskLevel(str, Enum):
    SAFE = "safe"
    CAUTION = "caution"
    PROTECTED = "protected"


RISK_ZH: dict[str, str] = {
    RiskLevel.SAFE.value: "可安全结束",
    RiskLevel.CAUTION.value: "需确认",
    RiskLevel.PROTECTED.value: "受保护",
}


class SoftwareKind(str, Enum):
    APP = "app"
    GAME = "game"
    RUNTIME = "runtime"
    DRIVER = "driver"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class Health(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"


# --------------------------------------------------------------------------- #
# 序列化基类
# --------------------------------------------------------------------------- #
class JsonMixin:
    """dataclass → dict（枚举转值、跳过 None 可选字段由调用方决定）。"""

    def to_dict(self) -> dict[str, Any]:
        return _plain(dataclasses.asdict(self))  # type: ignore[arg-type]


def _plain(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
# 进程与分类
# --------------------------------------------------------------------------- #
@dataclass
class Classification(JsonMixin):
    """单个进程的分类结果（L2 的产物）。"""

    role: ProcRole = ProcRole.UNKNOWN
    activity: Activity = Activity.BACKGROUND
    origin: Origin = Origin.UNKNOWN
    risk: RiskLevel = RiskLevel.CAUTION
    software_id: str | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    explanation: str = ""
    # 归属软件的厂商属性（由 inventory 填）：微软组件永久保护。
    microsoft: bool = False
    # 后台组件：后台类职能、没有窗口、非微软。仅描述“它是什么”。
    background: bool = False
    # 私自开启：后台组件中，**软件自己拉起的**那部分；或在用软件里明确属于
    # 更新/推广/遥测/崩溃收集的组件。UI 用它显示“私自开启”标记。
    private: bool = False

    # 中文标签：`role_zh` / `origin_zh` 已用于 evaluator 的理由文案与验收脚本，
    # `activity_zh` 与前两者构成同一套标签 API（UI 直接用，避免各写一份对照表）。
    @property
    def role_zh(self) -> str:
        return ROLE_ZH.get(self.role.value, self.role.value)

    @property
    def activity_zh(self) -> str:
        return ACTIVITY_ZH.get(self.activity.value, self.activity.value)

    @property
    def origin_zh(self) -> str:
        return ORIGIN_ZH.get(self.origin.value, self.origin.value)


@dataclass
class ProcessInfo(JsonMixin):
    """一个进程的完整画像。"""

    pid: int
    ppid: int | None = None
    name: str = ""
    exe: str | None = None
    cmdline: str | None = None
    parent_name: str | None = None
    session_id: int | None = None
    user: str | None = None
    integrity: str | None = None
    started_at: str | None = None
    cpu_seconds: float = 0.0
    memory_mb: float = 0.0
    threads: int = 0
    windows: list[str] = field(default_factory=list)
    foreground: bool = False
    file_company: str | None = None
    file_product: str | None = None
    file_description: str | None = None
    file_version: str | None = None
    signed: bool | None = None
    signer: str | None = None
    classification: Classification = field(default_factory=Classification)


@dataclass
class ServiceRuntime(JsonMixin):
    name: str
    display_name: str = ""
    state: str = "unknown"          # running/stopped/pending/...
    start_type: str = "unknown"     # auto/manual/disabled/boot/system
    binary_path: str | None = None
    pid: int | None = None
    account: str | None = None
    software_id: str | None = None


@dataclass
class AutostartEntry(JsonMixin):
    entry_id: str
    kind: str                       # registry_run/startup_folder/scheduled_task/service
    name: str
    command: str | None = None
    location: str | None = None
    enabled: bool = True
    can_disable: bool = False
    software_id: str | None = None


# --------------------------------------------------------------------------- #
# 软件清单
# --------------------------------------------------------------------------- #
@dataclass
class SoftwareItem(JsonMixin):
    """L1 的核心产物：一个"软件"及其全部运行痕迹。"""

    id: str
    name: str
    publisher: str | None = None
    version: str | None = None
    kind: SoftwareKind = SoftwareKind.UNKNOWN
    install_dir: str | None = None
    uninstall_string: str | None = None
    processes: list[int] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    autostart: list[str] = field(default_factory=list)   # entry_id 列表
    in_use: bool = False
    in_use_reason: str = ""
    last_active_at: str | None = None
    private_background: int = 0
    memory_mb: float = 0.0


# --------------------------------------------------------------------------- #
# 清理（L3）
# --------------------------------------------------------------------------- #
@dataclass
class CleanupItem(JsonMixin):
    pid: int
    name: str
    software_id: str | None
    software: str | None
    role: ProcRole
    origin: Origin
    risk: RiskLevel
    memory_mb: float = 0.0
    reason: str = ""
    restart_cmd: str | None = None
    # 动作：kill=结束进程；stop_service=停止它对应的第三方服务（服务不是进程，
    # 但它是这些后台组件的源头，必须允许单独停止）。
    action: str = "kill"
    # 默认是否勾选（= risk is SAFE）。UI 用它初始化行级勾选。
    default_checked: bool = True
    # 该进程对应的服务名（仅 action=stop_service 时有值）。
    services: list[str] = field(default_factory=list)
    # 生成计划时的可见窗口标题（执行时用于“先礼后兵”）。
    windows: list[str] = field(default_factory=list)
    private: bool = False
    microsoft: bool = False


@dataclass
class SkippedItem(JsonMixin):
    pid: int
    name: str
    why: str


@dataclass
class CleanupPlan(JsonMixin):
    plan_id: str
    ts: str
    scope: str = "all"
    items: list[CleanupItem] = field(default_factory=list)
    protected_skipped: list[SkippedItem] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    expires_at: str | None = None
    fingerprint: str = ""           # 计划生成时的 pid 集合指纹，用于 E_PLAN_STALE


@dataclass
class KillResult(JsonMixin):
    pid: int
    name: str
    ok: bool
    error: str | None = None
    tree: bool = False
    action: str = "kill"            # kill / stop_service
    services: list[str] = field(default_factory=list)
    mode: str | None = None         # graceful / force / dry_run / native / taskkill


@dataclass
class CleanupResult(JsonMixin):
    plan_id: str
    dry_run: bool = False
    killed: list[KillResult] = field(default_factory=list)
    failed: list[KillResult] = field(default_factory=list)
    freed_mb: float = 0.0
    audit_seq_from: int | None = None
    audit_seq_to: int | None = None


# --------------------------------------------------------------------------- #
# 策略与审计
# --------------------------------------------------------------------------- #
@dataclass
class Policy(JsonMixin):
    dry_run: bool = True
    min_idle_s: int = 120
    allow_cleanup_while_idle: bool = False
    protect: list[str] = field(default_factory=list)          # pid:<n> / sw:<id> / exe:<path>
    allow: list[str] = field(default_factory=list)
    # 只保护“系统”角色；**服务角色不再一刀切保护** —— 微软服务由微软规则保护，
    # 第三方服务是本次改造要能停止的对象（见 docs / 接口规范 §5.2）。
    never_touch_roles: list[str] = field(
        default_factory=lambda: [ProcRole.SYSTEM.value]
    )


@dataclass
class AuditRow(JsonMixin):
    seq: int
    ts: str
    event: str
    pid: int | None = None
    name: str | None = None
    software_id: str | None = None
    role: str | None = None
    origin: str | None = None
    rule: str | None = None
    action: str | None = None
    result: str | None = None
    dry_run: bool = False
    detail: dict[str, Any] = field(default_factory=dict)
