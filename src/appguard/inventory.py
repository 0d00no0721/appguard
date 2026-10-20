"""L1：进程表采集 + 软件清单聚合。

产出 `Snapshot`：进程（已分类）、服务、自启项、软件（按安装目录聚合）。
这是"我现在在用哪些软件"与"哪个进程是干嘛的"的数据源。

聚合口径（保持可预测、与用户直觉一致）：
  软件根目录 = **卸载信息里的安装目录**（若它是进程 exe 的祖先目录）
              否则 = **进程 exe 所在目录**
  ⇒ 同一个安装目录下的 dnplayer.exe / ld29297.exe / ldplayerservice.exe 自然归为同一个软件。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import classify as classifier
from . import winapi
from .models import (AutostartEntry, Origin, ProcRole, ProcessInfo, RiskLevel,
                     ServiceRuntime, SoftwareItem, SoftwareKind)

_NOW = lambda: datetime.now(timezone.utc).astimezone()  # noqa: E731

_CONTAINER_DIRS = {
    "program files", "program files (x86)", "programdata", "windows", "appdata", "local",
    "roaming", "programs", "common files", "windowsapps", "system32", "syswow64",
}

_RUNTIME_HINTS = ("python", "java", "node", "dotnet", "runtime", "vcredist", "directx")

# 这些"产品名"是框架/壳，不能当软件名用（Electron 应用会自称 Electron）
_GENERIC_PRODUCTS = {"electron", "chromium", "cef", "qt", "nw.js", "node.js", "unknown",
                     "application", "microsoft® windows® operating system"}


@dataclass
class Snapshot:
    ts: str
    processes: list[ProcessInfo] = field(default_factory=list)
    services: list[ServiceRuntime] = field(default_factory=list)
    autostart: list[AutostartEntry] = field(default_factory=list)
    software: list[SoftwareItem] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    uninstall: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "diagnostics": self.diagnostics,
            "software": [s.to_dict() for s in self.software],
            "processes": [p.to_dict() for p in self.processes],
            "services": [s.to_dict() for s in self.services],
            "autostart": [a.to_dict() for a in self.autostart],
        }


# --------------------------------------------------------------------------- #
# 路径与命名工具
# --------------------------------------------------------------------------- #
def _norm(path: str | None) -> str | None:
    if not path:
        return None
    text = path.strip().strip('"')
    # 服务的 ImagePath 经常是 `%SystemRoot%\System32\...` 或 `\SystemRoot\...`
    # （未展开的环境变量会让“微软组件”判定失败、软件名退化成 System32）。
    text = os.path.expandvars(text)
    low = text.lower()
    if low.startswith("\\??\\"):
        text = text[4:]
        low = text.lower()
    if low.startswith("\\systemroot\\"):
        text = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                            text[len("\\SystemRoot\\"):])
    return os.path.normpath(text)


def _is_ancestor(candidate: str, target: str) -> bool:
    try:
        cand = os.path.normcase(os.path.normpath(candidate)).rstrip("\\") + "\\"
        targ = os.path.normcase(os.path.normpath(target)).rstrip("\\") + "\\"
        return targ.startswith(cand) and targ != cand
    except Exception:
        return False


def _covers(directory: str, exe_dir: str) -> bool:
    """`directory` 是否覆盖 `exe_dir` —— **含相等**。

    ⚠️ 必须含相等：程序**直接装在安装目录根下**是最常见的形态
    （`E:\\leidian\\LDPlayer9\\dnplayer.exe` 的 exe 目录就是安装目录本身）。
    早期用严格祖先判断 ⇒ 匹配失败 ⇒ 软件名退化成目录名（雷电模拟器 → "LDPlayer9"）。
    """
    try:
        cand = os.path.normcase(os.path.normpath(directory)).rstrip("\\")
        targ = os.path.normcase(os.path.normpath(exe_dir)).rstrip("\\")
        return targ == cand or targ.startswith(cand + "\\")
    except Exception:
        return False


def _is_plausible_root(directory: str | None) -> bool:
    """判断一个"安装目录"是否可信。

    实测教训：部分卸载项的 `InstallLocation` 是 **盘符根（`E:\\`）或容器目录
    （`AppData\\Local\\Programs`）**，若直接采信会把整块盘的程序都归到同一个软件名下。
    因此拒绝：盘符根、已知容器目录名、Windows 目录、用户目录本身。
    """
    if not directory:
        return False
    norm = os.path.normpath(directory)
    if re.fullmatch(r"[A-Za-z]:\\?", norm):
        return False
    base = os.path.basename(norm).lower()
    if base in _CONTAINER_DIRS:
        return False
    lowered = os.path.normcase(norm)
    system_root = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
    if lowered.startswith(system_root):
        return False
    for profile_var in ("USERPROFILE", "PUBLIC"):
        profile = os.environ.get(profile_var)
        if profile and lowered == os.path.normcase(os.path.normpath(profile)):
            return False
    return True


def exe_from_command(command: str | None) -> str | None:
    """从命令行/注册表值里抽出可执行文件路径（支持带引号与带参数）。"""
    if not command:
        return None
    text = str(command).strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 1:
            return _norm(text[1:end])
    match = re.match(r"^([A-Za-z]:\\[^<>|?*\r\n]+?\.(?:exe|com|bat|cmd))", text, re.IGNORECASE)
    if match:
        return _norm(match.group(1))
    first = text.split(" ")[0].strip().strip('"')
    return _norm(first) if first else None


def _entry_dir(entry: dict[str, Any]) -> str | None:
    """卸载信息里推断出的安装目录。"""
    for key in ("InstallLocation",):
        path = _norm(entry.get(key))
        if path and os.path.isdir(path):
            return path
    icon = exe_from_command(entry.get("DisplayIcon"))
    if icon:
        return os.path.dirname(icon)
    uninstall = exe_from_command(entry.get("UninstallString"))
    if uninstall:
        return os.path.dirname(uninstall)
    return None


def _software_id(root: str | None, name: str) -> str:
    basis = (root or name or "unknown").lower()
    return "sw-" + hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:10]


def _kind_of(root: str | None, name: str, exe: str | None) -> SoftwareKind:
    low = f"{root or ''} {name or ''} {exe or ''}".lower()
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    if root and os.path.normcase(root).startswith(os.path.normcase(system_root)):
        return SoftwareKind.SYSTEM
    if any(hint in low for hint in _RUNTIME_HINTS):
        return SoftwareKind.RUNTIME
    return SoftwareKind.APP


def _is_microsoft(proc: ProcessInfo) -> bool:
    """微软组件判定：路径在 Windows/WindowsApps 下，或厂商/签名者是 Microsoft。

    ⚠️ 用户的明确规则：**微软的全部保留并标出来**。因此这里只做“识别”，
    具体保护由 evaluator 统一执行（微软组件永不进清理候选）。
    """
    exe = os.path.normcase(proc.exe or "")
    system_root = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
    if exe and (exe.startswith(system_root + "\\") or "\\windowsapps\\" in exe):
        return True
    for value in (proc.signer, proc.file_company):
        if value and "microsoft" in str(value).lower():
            return True
    return False


def service_map(services: list[ServiceRuntime]) -> dict[str, list[str]]:
    """可执行文件路径（normcase）→ 服务名列表。

    跳过 `svchost.exe`：一个 svchost 承载几十个系统服务，按 exe 归因只会得到噪声；
    微软组件本来也受保护。
    """
    out: dict[str, list[str]] = {}
    for svc in services:
        path = exe_from_command(svc.binary_path)
        if not path or os.path.basename(path).lower() == "svchost.exe":
            continue
        out.setdefault(os.path.normcase(path), []).append(svc.name)
    return out


def service_names_for(proc: ProcessInfo, services: list[ServiceRuntime]) -> list[str]:
    """进程 → 它对应的服务名（先按完整路径，再按文件名兜底）。"""
    exe = os.path.normcase(proc.exe or "")
    if not exe:
        return []
    names: list[str] = []
    by_exe = service_map(services)
    names.extend(by_exe.get(exe, []))
    if not names:
        base = os.path.basename(exe)
        for svc in services:
            path = exe_from_command(svc.binary_path)
            if path and os.path.basename(path).lower() == base:
                names.append(svc.name)
    return sorted(set(names))


# --------------------------------------------------------------------------- #
# 采集
# --------------------------------------------------------------------------- #
def collect(*, with_signature: bool = False, kb: dict | None = None,
            want_cmdline: bool = True, want_integrity: bool = True,
            min_idle_s: int = 120) -> Snapshot:
    """采集一次完整快照（进程已分类、软件已聚合）。"""
    ts = _NOW().isoformat(timespec="seconds")

    # 1) 进程
    raw = winapi.list_raw_processes()
    windows = winapi.visible_windows()
    fg_pid = winapi.foreground_pid()
    idle_s = winapi.last_input_seconds()

    processes: list[ProcessInfo] = []
    for row in raw:
        pid = row["pid"]
        if pid in (0, 4) or (row.get("name") or "").lower() in (
                "[system process]", "system idle process", "idle", "system"):
            continue                       # 内核伪进程，不参与软件聚合与清理
        details = winapi.process_details(pid, want_integrity=want_integrity,
                                         want_cmdline=want_cmdline)
        exe = details.get("exe")
        vi = winapi.file_version_info(exe) if exe else {}
        processes.append(ProcessInfo(
            pid=pid,
            ppid=row.get("ppid"),
            name=row.get("name") or "",
            exe=exe,
            cmdline=details.get("cmdline"),
            session_id=details.get("session_id"),
            integrity=details.get("integrity"),
            started_at=details.get("started_at"),
            cpu_seconds=details.get("cpu_seconds") or 0.0,
            memory_mb=details.get("memory_mb") or 0.0,
            threads=row.get("threads") or 0,
            windows=windows.get(pid, []),
            foreground=(pid == fg_pid),
            file_company=vi.get("company"),
            file_product=vi.get("product"),
            file_description=vi.get("description"),
            file_version=vi.get("version"),
        ))
    by_pid = {p.pid: p for p in processes}
    for proc in processes:
        if proc.ppid and proc.ppid in by_pid:
            proc.parent_name = by_pid[proc.ppid].name

    # 2) 服务 / 自启 / 卸载信息
    services = winapi.enum_services()
    autostart = (winapi.enum_registry_run() + winapi.enum_startup_folders()
                 + winapi.enum_scheduled_tasks())
    uninstall = winapi.enum_uninstall_entries()

    diagnostics = {
        "admin": winapi.is_admin(),
        "process_count": len(processes),
        "with_exe": sum(1 for p in processes if p.exe),
        "with_cmdline": sum(1 for p in processes if p.cmdline),
        "with_window": sum(1 for p in processes if p.windows),
        "foreground_pid": fg_pid,
        "idle_seconds": round(idle_s, 1),
        "service_count": len(services),
        "autostart_count": len(autostart),
        "uninstall_count": len(uninstall),
    }

    return build_snapshot(processes, services, autostart, uninstall, fg_pid=fg_pid,
                          diagnostics=diagnostics, kb=kb, min_idle_s=min_idle_s,
                          with_signature=with_signature)


def build_snapshot(processes: list[ProcessInfo], services: list[ServiceRuntime],
                   autostart: list[AutostartEntry], uninstall: list[dict[str, Any]], *,
                   fg_pid: int | None = None, diagnostics: dict[str, Any] | None = None,
                   kb: dict | None = None, min_idle_s: int = 120,
                   with_signature: bool = False) -> Snapshot:
    """L1/L2 的可测试主体：给定原始素材 → 归属聚合 + 分类 + 在用判定。

    与 `collect()` 分开的原因：**验证环境（受限沙箱）只能看到 27 个进程，覆盖不了聚合规则**，
    所以必须能拿合成数据回归（见 `tests/fixtures.py` 的雷电场景）。
    """
    ts = _NOW().isoformat(timespec="seconds")
    by_pid = {p.pid: p for p in processes}
    for proc in processes:
        if proc.ppid and proc.ppid in by_pid and not proc.parent_name:
            proc.parent_name = by_pid[proc.ppid].name
    diagnostics = diagnostics or {}

    # 2.5) 用**服务登记的可执行路径**补全拿不到 exe 的进程。
    #      受限权限下大量服务进程 OpenProcess 被拒（exe=None），但它们往往在服务表里有路径。
    service_exe_by_name: dict[str, str] = {}
    for svc in services:
        path = exe_from_command(svc.binary_path)
        if path:
            service_exe_by_name.setdefault(os.path.basename(path).lower(), path)
    for proc in processes:
        if not proc.exe and proc.name:
            guess = service_exe_by_name.get(proc.name.lower())
            if guess:
                proc.exe = guess
                # 服务进程常常 OpenProcess 被拒（拿不到版本信息）；用服务登记路径
                # 补全 exe 后重新读厂商/产品，否则微软组件会被误判成“第三方”。
                if not proc.file_company:
                    info = winapi.file_version_info(proc.exe)
                    proc.file_company = info.get("company")
                    proc.file_product = proc.file_product or info.get("product")
                    proc.file_description = proc.file_description or info.get("description")
                    proc.file_version = proc.file_version or info.get("version")

    # 3) 归属：先建"目录 → 软件"映射
    uninstall_dirs: list[tuple[str, dict[str, Any]]] = []
    for entry in uninstall:
        directory = _entry_dir(entry)
        if directory:
            uninstall_dirs.append((directory, entry))
    uninstall_dirs.sort(key=lambda item: len(item[0]), reverse=True)   # 最长前缀优先

    root_cache: dict[str, tuple[str, dict[str, Any] | None]] = {}

    def resolve_root(exe: str | None) -> tuple[str | None, dict[str, Any] | None]:
        if not exe:
            return None, None
        exe_dir = os.path.dirname(exe)
        if exe_dir in root_cache:
            return root_cache[exe_dir]
        chosen_dir, chosen_entry = exe_dir, None
        for directory, entry in uninstall_dirs:
            if not _covers(directory, exe_dir):
                continue
            if not _is_plausible_root(directory):
                continue
            remainder = exe_dir[len(os.path.normpath(directory)):]
            if remainder.count("\\") > 4:                  # 差太远就不是同一个软件
                continue
            chosen_dir, chosen_entry = directory, entry
            break
        result = (chosen_dir, chosen_entry)
        root_cache[exe_dir] = result
        return result

    # 4) 自启 / 服务的 exe 集合（用于来源判定）
    autostart_exes: set[str] = set()
    for entry in autostart:
        path = exe_from_command(entry.command)
        if path:
            autostart_exes.add(os.path.normcase(path))
    service_exes: set[str] = set()
    for svc in services:
        path = exe_from_command(svc.binary_path)
        if path and not path.lower().endswith("svchost.exe"):
            service_exes.add(os.path.normcase(path))

    # 5) 聚合软件
    groups: dict[str, dict[str, Any]] = {}
    pid_to_group: dict[int, str] = {}
    for proc in processes:
        root, entry = resolve_root(proc.exe)
        system_named = (proc.name or "").lower() in classifier.SYSTEM_NAMES
        if root:
            key = os.path.normcase(root)
        elif system_named:
            # 系统进程且路径读不到 → 合并成一组。
            # 否则会出现"svchost.exe 是一个装了 88 个进程的软件"这种荒唐结果。
            key = "sys:unknown-path"
        else:
            key = f"name:{proc.name.lower()}"
        group = groups.get(key)
        if group is None:
            product = proc.file_product
            if product and product.strip().lower() in _GENERIC_PRODUCTS:
                product = None                      # 框架名不算软件名
            desc = proc.file_description
            if desc and desc.strip().lower() in _GENERIC_PRODUCTS:
                desc = None
            name = ((entry or {}).get("DisplayName") or product or desc
                    or (os.path.basename(root) if root else proc.name))
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            kind_override: SoftwareKind | None = None
            if key == "sys:unknown-path":
                name = "系统组件（路径不可读）"
                kind_override = SoftwareKind.SYSTEM
            elif root and os.path.normcase(root).startswith(os.path.normcase(system_root)):
                name = "Windows 系统组件"           # 否则会按文件描述叫成"控制台窗口主机"
                kind_override = SoftwareKind.SYSTEM
            group = groups[key] = {
                "id": _software_id(root, name or proc.name),
                "name": str(name or proc.name),
                "publisher": (entry or {}).get("Publisher") or proc.file_company,
                "version": (entry or {}).get("DisplayVersion") or proc.file_version,
                "kind": kind_override or _kind_of(root, str(name or ""), proc.exe),
                "install_dir": root,
                "uninstall_string": (entry or {}).get("UninstallString"),
                "processes": [],
                "services": [],
                "autostart": [],
                "memory_mb": 0.0,
            }
        group["processes"].append(proc.pid)
        group["memory_mb"] += proc.memory_mb or 0.0
        pid_to_group[proc.pid] = group["id"]

    # 服务归组
    for svc in services:
        path = exe_from_command(svc.binary_path)
        if not path:
            continue
        root, _entry = resolve_root(path)
        key = os.path.normcase(root) if root else f"name:{os.path.basename(path).lower()}"
        group = groups.get(key)
        if group is None:
            continue
        group["services"].append(svc.name)
        svc.software_id = group["id"]

    # 自启归组
    for entry in autostart:
        path = exe_from_command(entry.command)
        if not path:
            continue
        root, _e = resolve_root(path)
        key = os.path.normcase(root) if root else f"name:{os.path.basename(path).lower()}"
        group = groups.get(key)
        if group is None:
            continue
        group["autostart"].append(entry.entry_id)
        entry.software_id = group["id"]

    software: list[SoftwareItem] = []
    for group in groups.values():
        item = SoftwareItem(
            id=group["id"], name=group["name"], publisher=group["publisher"],
            version=group["version"], kind=group["kind"], install_dir=group["install_dir"],
            uninstall_string=group["uninstall_string"], processes=sorted(group["processes"]),
            services=sorted(set(group["services"])), autostart=sorted(set(group["autostart"])),
            memory_mb=round(group["memory_mb"], 1),
        )
        software.append(item)

    # 6) 分类（此时已知归属软件名）
    #    ⚠️ 归属必须**按 pid 精确查表**。早期版本用 `root → id` 映射，
    #    而拿不到 exe 的进程 root 为 None ⇒ 全都落到同一个空键上 ⇒ 被随机分派到别的软件，
    #    于是出现「SearchProtocolHost.exe 的后台常驻（NVDisplay.Container.exe）」这种张冠李戴。
    name_by_id = {s.id: s.name for s in software}
    for proc in processes:
        sw_id = pid_to_group.get(proc.pid)
        parent = by_pid.get(proc.ppid) if proc.ppid else None
        parent_is_service = bool(parent and parent.exe
                                 and os.path.normcase(parent.exe) in service_exes)
        proc.classification = classifier.classify_process(
            proc, parent=parent, software_name=name_by_id.get(sw_id or ""),
            fg_pid=fg_pid,
            autostart_hit=bool(proc.exe and os.path.normcase(proc.exe) in autostart_exes),
            service_hit=bool(proc.exe and os.path.normcase(proc.exe) in service_exes),
            parent_is_service=parent_is_service,
            kb=kb)
        proc.classification.software_id = sw_id

    # 6.5) 主程序兜底：每个软件里"最像主程序"的那个进程，若仍无法定角色，判为主程序。
    #      否则 Electron 应用（无 KB 条目）会全部落在 unknown，覆盖率与可读性都差。
    proc_by_pid = {p.pid: p for p in processes}
    for item in software:
        members = [proc_by_pid[pid] for pid in item.processes if pid in proc_by_pid]
        unknown = [p for p in members if p.classification.role is ProcRole.UNKNOWN]
        if not unknown:
            continue

        def _primary_score(proc: ProcessInfo) -> tuple[int, float]:
            base = os.path.splitext(os.path.basename(proc.exe or proc.name))[0].lower()
            software_key = (item.name or "").lower().replace(" ", "")
            name_hit = 1 if base and base.replace("-", "") in software_key else 0
            return name_hit, (proc.memory_mb or 0.0)

        primary = max(unknown, key=_primary_score)
        primary.classification.role = ProcRole.MAIN
        primary.classification.confidence = max(primary.classification.confidence, 0.45)
        primary.classification.reasons.append("primary-process")
        if primary.classification.risk is RiskLevel.SAFE or not primary.windows:
            primary.classification.risk = RiskLevel.CAUTION
        primary.classification.explanation = classifier.explain(primary, primary.classification,
                                                                item.name)

    # 6.6) 服务链传播：服务用 CreateProcessAsUser 拉起的进程，其子进程也要继承
    #      “服务拉起”的来源。否则辅助进程会被判成“用户子进程”，永远不可能是私自开启。
    def _chain_from_service(proc: ProcessInfo) -> bool:
        current, seen = proc, set()
        for _ in range(24):
            if current.pid in seen:
                break
            seen.add(current.pid)
            parent = by_pid.get(current.ppid) if current.ppid else None
            if parent is None:
                break
            if parent.classification.origin is Origin.SERVICE:
                return True
            if parent.exe and os.path.normcase(parent.exe) in service_exes:
                return True
            current = parent
        return False

    for proc in processes:
        cls = proc.classification
        old_origin = cls.origin
        if old_origin in (Origin.CHILD_OF_USER, Origin.UNKNOWN, Origin.AUTOSTART) \
                and _chain_from_service(proc):
            cls.origin = Origin.CHILD_OF_SERVICE
            cls.reasons.append("ancestor-service")
            old_tail = classifier._ORIGIN_TAIL.get(old_origin)
            new_tail = classifier._ORIGIN_TAIL.get(Origin.CHILD_OF_SERVICE)
            if old_tail and new_tail and old_tail in cls.explanation:
                cls.explanation = cls.explanation.replace(old_tail, new_tail)

    # 6.7) 签名（可选，但微软判定要用；必须在填 microsoft/private 之前）
    if with_signature:
        paths = [p.exe for p in processes if p.exe][:60]
        signatures = winapi.authenticode_many(paths)
        for proc in processes:
            if proc.exe and proc.exe in signatures:
                proc.signed, proc.signer = signatures[proc.exe]

    # 7) 在用判定 + 后台/私自标记
    #    private  = “软件自己拉起的后台组件”（更新/推广/遥测/崩溃 → 任何时候；
    #               托盘/常驻/辅助/第三方服务 → 软件未在用且来源是自启/服务链）
    #    background 描述“它是后台组件”，private 是 UI 上的“私自开启”标记。
    for item in software:
        members = [proc_by_pid[pid] for pid in item.processes if pid in proc_by_pid]
        item.in_use, item.in_use_reason, item.last_active_at = _in_use_of(
            members, min_idle_s=min_idle_s)
        for proc in members:
            cls = proc.classification
            cls.microsoft = _is_microsoft(proc)
            if cls.microsoft:
                cls.risk = RiskLevel.PROTECTED
                cls.background = False
                cls.private = False
                continue
            if cls.role is ProcRole.SYSTEM:
                cls.background = False
                cls.private = False
                continue
            if cls.role is ProcRole.SERVICE:
                # 第三方服务：它就是“软件自己常驻”的那部分；能不能停由 evaluator 决定。
                cls.background = True
                cls.private = True
                continue
            if cls.role in classifier.CLEAR_PRIVATE_ROLES:
                # 更新器/推广/遥测/崩溃收集：即使你正在用这个软件，也明确是私自组件。
                cls.background = True
                cls.private = True
                continue
            if cls.role in (ProcRole.DAEMON, ProcRole.TRAY, ProcRole.HELPER):
                cls.background = True
                cls.private = bool(
                    not proc.windows
                    and not item.in_use
                    and cls.origin in (Origin.AUTOSTART, Origin.SERVICE,
                                       Origin.CHILD_OF_SERVICE, Origin.UNKNOWN))
                continue
            cls.background = False
            cls.private = False
        item.private_background = sum(1 for p in members if p.classification.private)

    software.sort(key=lambda s: (not s.in_use, -s.private_background, -s.memory_mb, s.name.lower()))

    return Snapshot(ts=ts, processes=processes, services=services, autostart=autostart,
                    software=software, diagnostics=diagnostics, uninstall=uninstall)


_NON_USER_WINDOW_ROLES = {ProcRole.AD, ProcRole.UPDATER, ProcRole.TELEMETRY,
                          ProcRole.CRASH, ProcRole.INSTALLER}


def _in_use_of(members: list[ProcessInfo], min_idle_s: int = 120) -> tuple[bool, str, str | None]:
    """接口规范 §5.1 的判定实现。

    ⚠️ 教训：早期版本把"任何 origin=user 的进程"都算在用 ⇒ 一切常驻进程都变"在用"，
    清理候选恒为 0。**「用户启动」必须叠加时间窗**（只在最近 `min_idle_s` 秒内才算）。

    ⚠️ 2026-10-18：**广告/更新器窗口不能算“你在用”**。否则雷电弹广告时，
    工具会认为“你正在用雷电”，从而拒绝清理——正是本项目要解决的那个场景。
    现在“在用”只认主程序/启动器/辅助（非推广类）的窗口。
    """
    try:
        min_idle_s = max(0, int(min_idle_s))       # 防御脏配置（曾因字符串比较崩过采集线程）
    except (TypeError, ValueError):
        min_idle_s = 120
    now = _NOW()
    usable = [p for p in members
              if p.windows and p.classification.role not in _NON_USER_WINDOW_ROLES]
    for proc in usable:
        if proc.foreground:
            return True, f"前台窗口：{proc.windows[0]}", now.isoformat(timespec="seconds")
    for proc in usable:
        return True, f"有可见窗口：{proc.windows[0]}", now.isoformat(timespec="seconds")
    for proc in members:
        if proc.classification.origin is not Origin.USER or not proc.started_at:
            continue
        try:
            started = datetime.fromisoformat(proc.started_at)
        except Exception:
            continue
        if (now - started).total_seconds() <= min_idle_s:
            return True, f"你 {int((now - started).total_seconds())} 秒前刚启动它", proc.started_at
    background_windows = [p for p in members if p.windows]
    if background_windows:
        return False, (f"只有后台/推广窗口（{background_windows[0].windows[0]}），"
                       "不算你在用"), None
    return False, "没有任何窗口，也不在前台", None


# --------------------------------------------------------------------------- #
# 查询辅助（供 daemon / CLI 使用）
# --------------------------------------------------------------------------- #
def filter_software(snapshot: Snapshot, *, in_use: bool | None = None,
                    has_private: bool | None = None, kind: str | None = None,
                    limit: int | None = None) -> list[SoftwareItem]:
    items = snapshot.software
    if in_use is not None:
        items = [s for s in items if s.in_use == in_use]
    if has_private:
        items = [s for s in items if s.private_background > 0]
    if kind:
        items = [s for s in items if s.kind.value == kind]
    return items[:limit] if limit else items


def find_process(snapshot: Snapshot, pid: int) -> ProcessInfo | None:
    return next((p for p in snapshot.processes if p.pid == pid), None)


def plan_pool(snapshot: Snapshot) -> list[ProcessInfo]:
    """**计划候选池**：归属于某个非系统软件的全部进程。

    这是 L3 的唯一候选定义，`evaluator.build_plan` 与计划指纹都用它 ——
    两处必须同源，否则计划生成后就立刻失效。

    ⚠️ 2026-10-18：**`in_use` 不再是候选池的闸门**。工具是“账本 + 手动选择”，
    “软件在用”只影响默认勾选，不影响“能不能看到/能不能选”。
    池里保留主程序/服务/系统角色，由 `evaluator.grade()` 决定
    protected（微软/关键系统/自身）还是可执行（kill / stop_service）。
    """
    kind_by_id = {s.id: s.kind for s in snapshot.software}
    pool: list[ProcessInfo] = []
    for proc in snapshot.processes:
        software_id = proc.classification.software_id
        if not software_id:
            continue                              # 归属不明：不猜
        if kind_by_id.get(software_id) is SoftwareKind.SYSTEM:
            continue                              # 不把 Windows 组件算进来
        pool.append(proc)
    return pool


def candidates_for_cleanup(snapshot: Snapshot) -> list[ProcessInfo]:
    """“私自开启”计数口径：候选池里被标为 `private` 的进程。

    仅用于展示与统计（`private_background` / 总览数字）；**计划生成请用 `plan_pool`**。
    """
    out = [p for p in plan_pool(snapshot) if p.classification.private]
    out.sort(key=lambda p: -(p.memory_mb or 0))
    return out
