"""配置解析：`config.toml` → `Config`。

原则：
  * **配置文件由人编辑，核心只读**（程序要写的状态放 `state.json`）。
  * 缺失字段一律取默认值；解析失败要 fail-loud（报错并拒绝启动），不静默兜底。
  * `--data-dir` 用于开发/测试隔离（默认 `%ProgramData%\\AppGuard`）。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from typing import Any

from .models import Policy

DEFAULT_CONFIG_TEMPLATE = """\
# AppGuard 配置（核心只读；程序不会改写本文件）
[policy]
dry_run                    = true    # ★ 首次务必保持 true：只记录不动作
min_idle_s                 = 120     # "刚启动就算在用"的时间窗
allow_cleanup_while_idle   = false   # 用户离开时是否允许清理（默认否）
# 只保护系统角色；微软组件由核心规则保护，第三方服务可以按需停止。
never_touch_roles          = ["system"]
protect                    = []      # 永不清理：pid:1234 / sw:<id> / exe:<完整路径>
allow                      = []      # 例外放行：即使角色在 never_touch_roles 里也允许清理

[cleanup]
kill_tree      = false    # ★ 按进程执行：默认只结束勾选的 PID，不连带子进程
plan_ttl_s     = 120      # 清理计划有效期（秒）；过期需重新生成
max_kill_batch = 200      # 单次计划最多处理多少个进程（安全阀）
graceful_close = true     # 有窗口的进程先发 WM_CLOSE，等 close_wait_s 秒
close_wait_s   = 5
service_wait_s = 15

[collect]
refresh_interval_s = 5
with_signature     = false   # 是否校验签名（较慢，只在前 N 个进程上做）

[kb]
pending_enabled  = true   # 把未识别/低置信度进程写成待办证据
review_interval_s = 60    # 采集线程最多每 N 秒落一次待办
max_pending      = 500
auto_apply       = false  # 永不自动入库；apply 必须显式执行

[targets]
# 兼容设计文档里的"守护目标"写法（可选，L3 之外的增强能力）
# [[targets.item]]
# id = "ldplayer9"
# name = "雷电模拟器9"
# main = ["dnplayer.exe"]
# services = ["LDPlayerSvr", "LDRemoteSvc"]
# watch = ["ld29297.exe", "ldplayerhelper.exe"]
"""


@dataclass
class CleanupSettings:
    kill_tree: bool = False
    plan_ttl_s: int = 120
    max_kill_batch: int = 200
    graceful_close: bool = True
    close_wait_s: int = 5
    service_wait_s: int = 15


@dataclass
class CollectSettings:
    refresh_interval_s: int = 5
    with_signature: bool = False


@dataclass
class KbSettings:
    pending_enabled: bool = True
    review_interval_s: int = 60
    max_pending: int = 500
    auto_apply: bool = False


@dataclass
class Config:
    data_dir: str
    config_path: str
    policy: Policy = field(default_factory=Policy)
    cleanup: CleanupSettings = field(default_factory=CleanupSettings)
    collect: CollectSettings = field(default_factory=CollectSettings)
    kb: KbSettings = field(default_factory=KbSettings)
    targets: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def audit_path(self) -> str:
        return os.path.join(self.data_dir, "audit.jsonl")

    @property
    def state_path(self) -> str:
        return os.path.join(self.data_dir, "state.json")

    @property
    def kb_path(self) -> str:
        return os.path.join(self.data_dir, "kb", "known_processes.json")


def default_data_dir() -> str:
    base = os.environ.get("ProgramData") or os.path.expanduser("~")
    return os.path.join(base, "AppGuard")


def resolve_data_dir(explicit: str | None = None) -> str:
    """数据目录优先级：显式参数 → 环境变量 → `%ProgramData%\\AppGuard`。"""
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get("APPGUARD_DATA_DIR")
    if env:
        return os.path.abspath(env)
    return default_data_dir()


def ensure_data_dir(data_dir: str) -> str:
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(os.path.join(data_dir, "kb"), exist_ok=True)
    return data_dir


def write_default_config(path: str, *, overwrite: bool = False) -> bool:
    """写一份带注释的默认配置。已存在且未指定 overwrite 时不动。"""
    if os.path.exists(path) and not overwrite:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(DEFAULT_CONFIG_TEMPLATE)
    return True


class ConfigError(Exception):
    pass


def load_config(config_path: str | None = None, *, data_dir: str | None = None,
                create_if_missing: bool = True) -> Config:
    directory = ensure_data_dir(resolve_data_dir(data_dir))
    path = os.path.abspath(config_path) if config_path else os.path.join(directory, "config.toml")

    if create_if_missing and not os.path.exists(path):
        write_default_config(path)

    warnings: list[str] = []
    raw: dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path, "rb") as handle:
                raw = tomllib.load(handle)
        except Exception as exc:                       # fail-loud
            raise ConfigError(f"配置文件解析失败：{path}：{exc}") from exc

    policy_raw = raw.get("policy", {}) or {}
    cleanup_raw = raw.get("cleanup", {}) or {}
    collect_raw = raw.get("collect", {}) or {}

    policy = Policy(
        dry_run=bool(policy_raw.get("dry_run", True)),
        min_idle_s=int(policy_raw.get("min_idle_s", 120)),
        allow_cleanup_while_idle=bool(policy_raw.get("allow_cleanup_while_idle", False)),
        protect=[str(x) for x in (policy_raw.get("protect") or [])],
        allow=[str(x) for x in (policy_raw.get("allow") or [])],
        never_touch_roles=[str(x) for x in (policy_raw.get("never_touch_roles")
                                            or ["system"])],
    )
    _validate_policy(policy, warnings)

    kill_tree_raw = bool(cleanup_raw.get("kill_tree", False))
    if kill_tree_raw:
        warnings.append("[cleanup].kill_tree=true 已忽略：按进程执行只结束勾选的 PID，"
                        "不连带子进程（子进程会在列表里单独出现）")
    cleanup = CleanupSettings(
        kill_tree=False,
        plan_ttl_s=max(10, int(cleanup_raw.get("plan_ttl_s", 120))),
        max_kill_batch=max(1, int(cleanup_raw.get("max_kill_batch", 200))),
        graceful_close=bool(cleanup_raw.get("graceful_close", True)),
        close_wait_s=max(1, int(cleanup_raw.get("close_wait_s", 5))),
        service_wait_s=max(1, int(cleanup_raw.get("service_wait_s", 15))),
    )
    collect = CollectSettings(
        refresh_interval_s=max(1, int(collect_raw.get("refresh_interval_s", 5))),
        with_signature=bool(collect_raw.get("with_signature", False)),
    )
    kb_raw = raw.get("kb", {}) or {}
    kb = KbSettings(
        pending_enabled=bool(kb_raw.get("pending_enabled", True)),
        review_interval_s=max(10, int(kb_raw.get("review_interval_s", 60))),
        max_pending=max(10, int(kb_raw.get("max_pending", 500))),
        auto_apply=bool(kb_raw.get("auto_apply", False)),
    )
    if kb.auto_apply:
        warnings.append("[kb].auto_apply=true 已忽略：知识库补丁必须显式 apply（安全边界）")
        kb.auto_apply = False

    targets_raw = raw.get("targets", {}) or {}
    targets: list[dict[str, Any]] = []
    if isinstance(targets_raw, dict):
        items = targets_raw.get("item") or []
        targets = [t for t in items if isinstance(t, dict)]
        if not items and targets_raw:
            warnings.append("[targets] 段存在但没有 [[targets.item]] 条目，已忽略")
    elif isinstance(targets_raw, list):
        targets = [t for t in targets_raw if isinstance(t, dict)]

    return Config(data_dir=directory, config_path=path, policy=policy, cleanup=cleanup,
                  collect=collect, kb=kb, targets=targets, warnings=warnings)


def _validate_policy(policy: Policy, warnings: list[str]) -> None:
    valid_roles = {"main", "launcher", "updater", "telemetry", "crash", "ad", "tray",
                   "helper", "daemon", "service", "installer", "system", "unknown"}
    bad = [r for r in policy.never_touch_roles if r not in valid_roles]
    if bad:
        warnings.append(f"never_touch_roles 含未知角色，已忽略：{bad}")
        policy.never_touch_roles = [r for r in policy.never_touch_roles if r in valid_roles]
    # 迁移：service 不再是一刀切保护的角色。微软服务由微软规则保护，
    # 第三方服务要能被“停止服务”；旧配置里的 service 会挡住本次改造。
    if "service" in policy.never_touch_roles:
        policy.never_touch_roles = [r for r in policy.never_touch_roles if r != "service"]
        warnings.append(
            "never_touch_roles 里的 service 已移除：微软组件由微软规则保护，"
            "第三方服务可按需停止（如需保护具体服务，请用 protect=exe:...)")
    if not policy.dry_run:
        warnings.append("policy.dry_run = false：清理会真实结束进程，请确认已过观察期")
    for pattern in policy.protect:
        if not pattern.startswith(("pid:", "sw:", "exe:")):
            warnings.append(f"protect 项格式应为 pid:/sw:/exe: 前缀，已忽略：{pattern}")
    policy.protect = [p for p in policy.protect if p.startswith(("pid:", "sw:", "exe:"))]
    policy.allow = [p for p in policy.allow if p.startswith(("pid:", "sw:", "exe:"))]
