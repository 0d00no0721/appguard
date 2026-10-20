"""底层 Windows 访问层（进程 / 窗口 / 服务 / 注册表自启 / 文件信息 / 签名）。

设计原则：
  * 全部函数**只读**，不改变系统状态（服务启停与杀进程在 service_ctl / cleaner 里）。
  * 任何一个进程取不到信息都不能让整轮采集失败 —— 一律 try/except 降级为 None。
  * 依赖：stdlib + ctypes + pywin32（本机已装）。**不依赖 psutil / wmi 包**。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Iterable

import win32api
import win32com.client
import win32con
import win32service
import pythoncom

from .models import AutostartEntry, ServiceRuntime

# --------------------------------------------------------------------------- #
# DLL 与常量
# --------------------------------------------------------------------------- #
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259
TOKEN_QUERY = 0x0008
MAX_PATH_LONG = 32768

_SERVICE_STATE = {1: "stopped", 2: "start_pending", 3: "stop_pending", 4: "running",
                  5: "continue_pending", 6: "pause_pending", 7: "paused"}
_SERVICE_START = {0: "boot", 1: "system", 2: "auto", 3: "manual", 4: "disabled"}
_INTEGRITY = {0x0000: "untrusted", 0x1000: "low", 0x2000: "medium", 0x3000: "high",
              0x4000: "system", 0x5000: "protected"}


def _extract_state(status) -> int | None:
    """从 QueryServiceStatus 的返回里取 CurrentState。

    pywin32 返回的是元组 `(ServiceType, CurrentState, ControlsAccepted, ...)` ——
    **状态在索引 1，索引 0 是服务类型（例如 272）**；dict 形式则直接用键。
    这里用"值必须落在合法状态集合内"来兼容两种次序，避免再次读错。
    """
    if isinstance(status, dict):
        state = status.get("CurrentState")
        return state if state in _SERVICE_STATE else None
    if isinstance(status, (tuple, list)):
        for cand in ((status[1] if len(status) > 1 else None), (status[0] if status else None)):
            if isinstance(cand, int) and cand in _SERVICE_STATE:
                return cand
    return None


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wt.USHORT), ("MaximumLength", wt.USHORT), ("Buffer", ctypes.c_void_p)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


# --- 显式声明函数签名：64 位下漏声明 restype 会把指针截断成 int ---
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
k32.ProcessIdToSessionId.argtypes = [wt.DWORD, ctypes.POINTER(wt.DWORD)]
k32.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(FILETIME)] * 4
k32.GetTickCount64.restype = ctypes.c_ulonglong
psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wt.DWORD]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
advapi32.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD,
                                         ctypes.POINTER(wt.DWORD)]
advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wt.DWORD)
advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wt.DWORD]
k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]


def is_process_alive(pid: int) -> bool:
    """进程是否仍在运行（用退出码判定，避免把"已死未回收"当成活着）。"""
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wt.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def terminate_process(pid: int) -> tuple[bool, str | None]:
    """原生结束单个进程。

    为什么不直接用 `taskkill`：它要额外起一个进程，且实测在受限环境下会返回
    `ERROR: Access denied`（**即使目标是我们自己创建的子进程**）。
    原生 `TerminateProcess` 只依赖我们自己的句柄，同样权限下更可靠、也更快。
    """
    h = k32.OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False, f"OpenProcess 失败（Win32 错误 {ctypes.get_last_error()}）"
    try:
        if k32.TerminateProcess(h, 1):
            return True, None
        return False, f"TerminateProcess 失败（Win32 错误 {ctypes.get_last_error()}）"
    finally:
        k32.CloseHandle(h)


def wait_process_gone(pid: int, timeout: float = 2.0) -> bool:
    """等进程真正退出（`TerminateProcess` 是异步的，立即复查会误判）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_process_alive(pid):
            return True
        time.sleep(0.05)
    return not is_process_alive(pid)


def terminate_tree(pid: int) -> tuple[bool, list[str]]:
    """结束进程树：**后序遍历，先子后父**。返回 (是否全部成功, 失败清单)。"""
    children: dict[int, list[int]] = {}
    for row in list_raw_processes():
        children.setdefault(row["ppid"], []).append(row["pid"])
    order: list[int] = []
    stack = [pid]
    while stack:
        current = stack.pop()
        order.append(current)
        stack.extend(children.get(current, []))
    failures: list[str] = []
    for target in reversed(order):
        ok, err = terminate_process(target)
        if not ok and not wait_process_gone(target):
            failures.append(f"{target}: {err}")
    return (not failures), failures

ntdll = ctypes.WinDLL("ntdll")
ntdll.NtQueryInformationProcess.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wt.ULONG, ctypes.POINTER(wt.ULONG)]
ntdll.NtQueryInformationProcess.restype = ctypes.c_long
PROCESS_COMMAND_LINE_INFORMATION = 60


def process_cmdline_nt(pid: int) -> str | None:
    """取命令行：`NtQueryInformationProcess(ProcessCommandLineInformation=60)`。

    只用 PROCESS_QUERY_LIMITED_INFORMATION，**不读目标进程内存、不需要 WMI**；
    实测在 WMI/CIM 被环境阻断时依然可取到其它进程的完整命令行。
    """
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        need = wt.ULONG(0)
        ntdll.NtQueryInformationProcess(h, PROCESS_COMMAND_LINE_INFORMATION, None, 0,
                                        ctypes.byref(need))
        if need.value == 0:
            return None
        buf = ctypes.create_string_buffer(need.value)
        status = ntdll.NtQueryInformationProcess(h, PROCESS_COMMAND_LINE_INFORMATION, buf,
                                                 need.value, ctypes.byref(need))
        if status != 0:
            return None
        us = ctypes.cast(buf, ctypes.POINTER(UNICODE_STRING)).contents
        if not us.Buffer or us.Length == 0:
            return None
        return ctypes.wstring_at(us.Buffer, us.Length // 2) or None
    except Exception:
        return None
    finally:
        k32.CloseHandle(h)


def _ft_to_dt(ft: FILETIME) -> datetime | None:
    ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    if ticks <= 0:
        return None
    return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ticks // 10)


def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 进程
# --------------------------------------------------------------------------- #
def list_raw_processes() -> list[dict[str, Any]]:
    """toolhelp32 全量快照：pid / ppid / name / threads。"""
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        raise OSError(f"CreateToolhelp32Snapshot failed: {ctypes.get_last_error()}")
    out: list[dict[str, Any]] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append({
                "pid": int(entry.th32ProcessID),
                "ppid": int(entry.th32ParentProcessID),
                "name": entry.szExeFile,
                "threads": int(entry.cntThreads),
            })
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def process_details(pid: int, want_integrity: bool = False,
                    want_cmdline: bool = False) -> dict[str, Any]:
    """单进程细节：exe / 会话 / 启动时间 / CPU / 内存 / 完整性 / 命令行。取不到就为 None。"""
    info: dict[str, Any] = {"exe": None, "cmdline": None, "session_id": None, "started_at": None,
                            "cpu_seconds": 0.0, "memory_mb": 0.0, "integrity": None}
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        h = k32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return info
    try:
        buf = ctypes.create_unicode_buffer(MAX_PATH_LONG)
        size = wt.DWORD(MAX_PATH_LONG)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            info["exe"] = buf.value or None

        sid = wt.DWORD()
        if k32.ProcessIdToSessionId(pid, ctypes.byref(sid)):
            info["session_id"] = int(sid.value)

        creation, exit_t, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if k32.GetProcessTimes(h, ctypes.byref(creation), ctypes.byref(exit_t),
                               ctypes.byref(kernel), ctypes.byref(user)):
            dt = _ft_to_dt(creation)
            if dt:
                info["started_at"] = dt.astimezone().isoformat(timespec="seconds")
            k = (kernel.dwHighDateTime << 32) | kernel.dwLowDateTime
            u = (user.dwHighDateTime << 32) | user.dwLowDateTime
            info["cpu_seconds"] = round((k + u) / 1e7, 3)

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(counters), counters.cb):
            info["memory_mb"] = round(counters.WorkingSetSize / 1048576, 2)

        if want_integrity:
            info["integrity"] = _integrity_of(h)
    except Exception:
        pass
    finally:
        k32.CloseHandle(h)
    if want_cmdline:
        info["cmdline"] = process_cmdline_nt(pid)
    return info


def _integrity_of(handle: int) -> str | None:
    token = wt.HANDLE()
    if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        TOKEN_INTEGRITY_LEVEL = 25
        need = wt.DWORD()
        advapi32.GetTokenInformation(token, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(need))
        if not need.value:
            return None
        buf = ctypes.create_string_buffer(need.value)
        if not advapi32.GetTokenInformation(token, TOKEN_INTEGRITY_LEVEL, buf, need.value,
                                            ctypes.byref(need)):
            return None
        # TOKEN_MANDATORY_LABEL 的第一个字段是 SID_AND_ATTRIBUTES{Sid, Attributes}
        sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        if not sid_ptr:
            return None
        count_ptr = advapi32.GetSidSubAuthorityCount(sid_ptr)
        if not count_ptr:
            return None
        n = int(count_ptr[0])
        if n <= 0:
            return None
        rid_ptr = advapi32.GetSidSubAuthority(sid_ptr, n - 1)
        if not rid_ptr:
            return None
        rid = int(rid_ptr[0])
        return _INTEGRITY.get(rid & 0xF000, "unknown")
    except Exception:
        return None
    finally:
        k32.CloseHandle(token)


def wmi_process_info() -> dict[int, dict[str, Any]]:
    """命令行与可执行路径。

    两条通道：① pywin32 WMI（快）② PowerShell CIM（慢但更耐受限环境）。
    都失败返回空字典 —— 命令行是可选字段，缺失不影响其它采集。
    """
    out = _wmi_via_com()
    if not out:
        out = _wmi_via_powershell()
    return out


def _wmi_via_com() -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        svc = locator.ConnectServer(".", "root\\cimv2")
        svc.Security_.ImpersonationLevel = 3
        for p in svc.ExecQuery("SELECT ProcessId, CommandLine, ExecutablePath FROM Win32_Process"):
            try:
                out[int(p.ProcessId)] = {"cmdline": p.CommandLine, "exe": p.ExecutablePath}
            except Exception:
                continue
    except Exception:
        return {}
    return out


_PS_PROC_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue';"
    "Get-CimInstance Win32_Process | "
    "ForEach-Object { [pscustomobject]@{pid=$_.ProcessId; cmd=$_.CommandLine; exe=$_.ExecutablePath} } | "
    "ConvertTo-Json -Compress -Depth 3"
)


def _wmi_via_powershell(timeout: int = 30) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _PS_PROC_SCRIPT],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(proc.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        for row in data:
            try:
                out[int(row["pid"])] = {"cmdline": row.get("cmd"), "exe": row.get("exe")}
            except Exception:
                continue
    except Exception:
        return {}
    return out


# --------------------------------------------------------------------------- #
# 窗口 / 输入
# --------------------------------------------------------------------------- #
_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
WM_CLOSE = 0x0010


def visible_window_records() -> dict[int, list[tuple[int, str]]]:
    """pid → [(hwnd, 标题)]，只收可见且有标题的顶层窗口。"""
    result: dict[int, list[tuple[int, str]]] = {}

    def _cb(hwnd, _lparam):
        try:
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = (buf.value or "").strip()
                    if title:
                        pid = wt.DWORD()
                        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        if pid.value:
                            result.setdefault(int(pid.value), []).append(
                                (int(hwnd), title))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    except Exception:
        pass
    return result


def visible_windows() -> dict[int, list[str]]:
    """pid → 可见且有标题的窗口标题列表。"""
    return {pid: [title for _hwnd, title in rows]
            for pid, rows in visible_window_records().items()}


def close_windows(pid: int, timeout: float = 5.0) -> tuple[bool, int]:
    """向 pid 的可见窗口发 WM_CLOSE（先礼后兵）。

    返回 ``(是否已关闭/进程已退出, 剩余窗口数)``。
    * 没有窗口 → ``(False, 0)``，调用方直接强杀；
    * 进程在超时内退出或窗口全部消失 → ``True``；
    * 超时仍有窗口 → ``False``，调用方再强杀。
    """
    rows = visible_window_records().get(pid, [])
    if not rows:
        return False, 0
    for hwnd, _title in rows:
        try:
            user32.PostMessageW(wt.HWND(hwnd), WM_CLOSE, 0, 0)
        except Exception:
            pass
    deadline = time.time() + max(0.2, timeout)
    remaining = len(rows)
    while time.time() < deadline:
        if not is_process_alive(pid):
            return True, 0
        remaining = len(visible_window_records().get(pid, []))
        if remaining == 0:
            return True, 0
        time.sleep(0.15)
    if not is_process_alive(pid):
        return True, 0
    return False, len(visible_window_records().get(pid, []))


def foreground_pid() -> int | None:
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) or None
    except Exception:
        return None


def last_input_seconds() -> float:
    """距上次键鼠输入经过的秒数（判断用户是否离开）。"""
    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return max(0.0, (k32.GetTickCount() - info.dwTime) / 1000.0)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# 文件信息 / 签名
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=4096)
def file_version_info(path: str | None) -> dict[str, str | None]:
    out: dict[str, str | None] = {"company": None, "product": None,
                                  "description": None, "version": None}
    if not path or not os.path.exists(path):
        return out
    try:
        fixed = win32api.GetFileVersionInfo(path, "\\")
        ms, ls = fixed["FileVersionMS"], fixed["FileVersionLS"]
        out["version"] = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        pass
    try:
        lang, cp = win32api.GetFileVersionInfo(path, r"\VarFileInfo\Translation")[0]
        prefix = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\"
        for key, field in (("CompanyName", "company"), ("ProductName", "product"),
                           ("FileDescription", "description")):
            try:
                out[field] = win32api.GetFileVersionInfo(path, prefix + key)
            except Exception:
                pass
    except Exception:
        pass
    return out


_SIG_CACHE: dict[str, tuple[bool | None, str | None]] = {}

_PS_SIG_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$out=@();"
    "foreach($p in $input){"
    "  if([string]::IsNullOrWhiteSpace($p)){continue}"
    "  $s=Get-AuthenticodeSignature -LiteralPath $p;"
    "  $sub=$null; if($s.SignerCertificate){$sub=[string]$s.SignerCertificate.Subject}"
    "  $out+=[pscustomobject]@{p=$p;c=[int]$s.Status;sub=$sub}"
    "};"
    "ConvertTo-Json -InputObject @($out) -Compress -Depth 3"
)

# SignatureStatus 枚举：0=Valid 1=UnknownError 2=NotSigned 3=HashMismatch
# 4=NotTrusted 5=NotSupportedFileFormat 6=Incompatible
# 用数字而不是字符串 —— 中文系统下 $s.Status 是本地化文本（"有效"），比字符串必错。
_SIG_STATUS = {0: True, 2: False}


def authenticode_many(paths: Iterable[str], timeout: int = 45) -> dict[str, tuple[bool | None, str | None]]:
    """批量校验 Authenticode 签名。返回 {path: (signed|None, signer|None)}。

    用一次性 PowerShell 调用完成（避免每个文件一次进程启动）。失败一律返回 None。
    """
    todo = [p for p in dict.fromkeys(paths) if p and p not in _SIG_CACHE and os.path.exists(p)]
    if todo:
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", _PS_SIG_SCRIPT],
                input="\n".join(todo), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            data = json.loads(proc.stdout or "[]")
            if isinstance(data, dict):
                data = [data]
            for row in data:
                try:
                    code = int(row.get("c"))
                except Exception:
                    code = None
                signed = _SIG_STATUS.get(code) if code is not None else None
                sub = row.get("sub")
                signer = None
                if sub:
                    for part in str(sub).split(","):
                        if part.strip().upper().startswith("CN="):
                            signer = part.strip()[3:]
                            break
                    signer = signer or str(sub)[:80]
                _SIG_CACHE[row["p"]] = (signed, signer)
        except Exception:
            pass
    return {p: _SIG_CACHE.get(p, (None, None)) for p in paths if p}


# --------------------------------------------------------------------------- #
# 服务
# --------------------------------------------------------------------------- #
_SERVICE_KEY = r"SYSTEM\CurrentControlSet\Services"


def enum_services() -> list[ServiceRuntime]:
    """Win32 服务清单：**注册表取配置 + SCM 逐个取状态**。

    刻意不使用 `pywin32.EnumServicesStatusEx` —— 该绑定在受限环境下会抛
    `TypeError: Objects of type 'int' can not be converted to Unicode`，且无可用的降级参数。
    注册表路径在普通用户权限下即可读，覆盖面与 `sc query type= service` 一致。
    """
    items = _services_from_registry()
    hscm = None
    try:
        hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    except Exception:                                           # noqa: BLE001
        hscm = None
    try:
        for item in items:
            _fill_service_state(hscm, item)
    finally:
        if hscm:
            try:
                win32service.CloseServiceHandle(hscm)
            except Exception:                                   # noqa: BLE001
                pass
    return items


def stop_service(name: str, timeout: float = 15.0) -> tuple[bool, str | None]:
    """停止一个 Windows 服务并等它到 STOPPED。

    ⚠️ 这是本工具**唯一**的系统级动作（只改变运行状态，不改启动类型/注册表）。
    第三方服务是“定时拉起推广组件”的源头；只杀进程而不停服务，30 分钟后还会回来。
    """
    hscm = None
    hs = None
    try:
        try:
            hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        except Exception as exc:                                # noqa: BLE001
            return False, f"打开 SCM 失败：{exc}"
        try:
            hs = win32service.OpenService(
                hscm, name, win32service.SERVICE_STOP | win32service.SERVICE_QUERY_STATUS)
        except Exception as exc:                                # noqa: BLE001
            return False, f"打开服务 {name} 失败：{exc}"
        try:
            win32service.ControlService(hs, win32service.SERVICE_CONTROL_STOP)
        except Exception as exc:                                # noqa: BLE001
            # 1062 = 服务本来就没在运行，视为成功；其余错误上报。
            if getattr(exc, "winerror", None) != 1062:
                # 先看状态，可能已经停了
                state = _service_state_of(hs)
                if state != 1:
                    return False, f"停止服务 {name} 失败：{exc}"
        deadline = time.time() + max(1.0, timeout)
        while time.time() < deadline:
            state = _service_state_of(hs)
            if state == 1:
                return True, None
            time.sleep(0.2)
        return False, f"服务 {name} 在 {timeout:.0f} 秒内未进入 STOPPED"
    finally:
        for handle, closer in ((hs, win32service.CloseServiceHandle),
                               (hscm, win32service.CloseServiceHandle)):
            if handle:
                try:
                    closer(handle)
                except Exception:                               # noqa: BLE001
                    pass


def _service_state_of(handle) -> int | None:
    try:
        return _extract_state(win32service.QueryServiceStatus(handle))
    except Exception:                                           # noqa: BLE001
        return None


def _services_from_registry() -> list[ServiceRuntime]:
    out: list[ServiceRuntime] = []
    try:
        root = win32api.RegOpenKeyEx(win32con.HKEY_LOCAL_MACHINE, _SERVICE_KEY, 0, win32con.KEY_READ)
    except Exception:
        return out
    try:
        idx = 0
        while True:
            try:
                name = win32api.RegEnumKey(root, idx)
            except Exception:
                break
            idx += 1
            try:
                key = win32api.RegOpenKeyEx(root, name, 0, win32con.KEY_READ)
            except Exception:
                continue
            try:
                def _val(field: str, default=None):
                    try:
                        return win32api.RegQueryValueEx(key, field)[0]
                    except Exception:
                        return default

                stype = _val("Type", 0x10)
                image = _val("ImagePath")
                # 只保留真的 Win32 服务：必须有可执行路径，且 Type 含 0x10/0x20
                # （注册表里还混着性能库等伪服务，它们没有 ImagePath）
                if not image:
                    continue
                if not isinstance(stype, int) or (stype & 0x30) not in (0x10, 0x20):
                    continue
                display = _val("DisplayName") or ""
                if isinstance(display, str) and display.startswith("@"):
                    display = ""      # 资源引用（@%SystemRoot%\...;-100），交由 UI 用 name 兜底
                out.append(ServiceRuntime(
                    name=name,
                    display_name=display or name,
                    start_type=_SERVICE_START.get(_val("Start"), "unknown"),
                    binary_path=str(image),
                    account=_val("ObjectName"),
                ))
            finally:
                win32api.RegCloseKey(key)
    finally:
        win32api.RegCloseKey(root)
    return out


def _fill_service_state(hscm, item: ServiceRuntime) -> None:
    if not hscm:
        return
    hs = None
    try:
        hs = win32service.OpenService(hscm, item.name, win32service.SERVICE_QUERY_STATUS)
        state = _extract_state(win32service.QueryServiceStatus(hs))
        if state is not None:
            item.state = _SERVICE_STATE[state]
    except Exception:
        pass
    finally:
        if hs:
            try:
                win32service.CloseServiceHandle(hs)
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# 自启项（只读枚举）
# --------------------------------------------------------------------------- #
_RUN_KEYS = [
    (win32con.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKLM\\Run"),
    (win32con.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
     "HKLM\\RunOnce"),
    (win32con.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
     "HKLM\\Wow6432\\Run"),
    (win32con.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKCU\\Run"),
    (win32con.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
     "HKCU\\RunOnce"),
]


def enum_registry_run() -> list[AutostartEntry]:
    out: list[AutostartEntry] = []
    for hive, subkey, label in _RUN_KEYS:
        try:
            key = win32api.RegOpenKeyEx(hive, subkey, 0, win32con.KEY_READ)
        except Exception:
            continue
        try:
            i = 0
            while True:
                try:
                    name, value, _typ = win32api.RegEnumValue(key, i)
                except Exception:
                    break
                i += 1
                out.append(AutostartEntry(
                    entry_id=f"run:{label}:{name}", kind="registry_run", name=name,
                    command=str(value), location=label, enabled=True, can_disable=False))
        finally:
            win32api.RegCloseKey(key)
    return out


def enum_startup_folders() -> list[AutostartEntry]:
    out: list[AutostartEntry] = []
    folders = [
        (os.path.join(os.environ.get("APPDATA", ""),
                      r"Microsoft\Windows\Start Menu\Programs\Startup"), "用户启动夹"),
        (os.path.join(os.environ.get("ProgramData", ""),
                      r"Microsoft\Windows\Start Menu\Programs\Startup"), "公共启动夹"),
    ]
    for folder, label in folders:
        if not os.path.isdir(folder):
            continue
        for entry in os.scandir(folder):
            out.append(AutostartEntry(
                entry_id=f"folder:{label}:{entry.name}", kind="startup_folder",
                name=entry.name, command=entry.path, location=label,
                enabled=True, can_disable=False))
    return out


def enum_scheduled_tasks(limit: int = 400) -> list[AutostartEntry]:
    """计划任务：COM（快）→ PowerShell `Get-ScheduledTask`（耐受限环境）。"""
    out = _tasks_via_com(limit)
    if not out:
        out = _tasks_via_powershell(limit)
    return out


def _tasks_via_com(limit: int = 400) -> list[AutostartEntry]:
    out: list[AutostartEntry] = []
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        svc = win32com.client.Dispatch("Schedule.Service")
        svc.Connect()
        queue = [svc.GetFolder("\\")]
        seen = 0
        while queue and seen < limit:
            folder = queue.pop()
            try:
                for sub in folder.GetFolders(0):
                    queue.append(sub)
            except Exception:
                pass
            try:
                tasks = folder.GetTasks(1)
            except Exception:
                tasks = []
            for task in tasks:
                seen += 1
                if seen >= limit:
                    break
                try:
                    actions = []
                    for action in task.Definition.Actions:
                        path = getattr(action, "Path", None)
                        args = getattr(action, "Arguments", None)
                        if path:
                            actions.append(f"{path} {args}".strip())
                    path_name = task.Path
                    out.append(AutostartEntry(
                        entry_id=f"task:{path_name}", kind="scheduled_task",
                        name=path_name.rsplit("\\", 1)[-1],
                        command=" | ".join(actions) or None,
                        location=path_name.rsplit("\\", 1)[0] or "\\",
                        enabled=bool(task.Enabled), can_disable=False))
                except Exception:
                    continue
    except Exception:
        return []
    return out


_PS_TASKS_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue';"
    "Get-ScheduledTask | ForEach-Object {"
    "  $a=@($_.Actions);"
    "  [pscustomobject]@{"
    "    n=$_.TaskName; p=$_.TaskPath; s=[string]$_.State;"
    "    e=(($a|ForEach-Object{$_.Execute}) -join ' | ');"
    "    g=(($a|ForEach-Object{$_.Arguments}) -join ' ')"
    "  }"
    "} | ConvertTo-Json -Compress -Depth 3"
)


def _tasks_via_powershell(limit: int = 400, timeout: int = 60) -> list[AutostartEntry]:
    out: list[AutostartEntry] = []
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _PS_TASKS_SCRIPT],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(proc.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        for row in data[:limit]:
            path_name = f"{row.get('p') or '\\'}{row.get('n') or ''}"
            exec_path = (row.get("e") or "").strip()
            args = (row.get("g") or "").strip()
            out.append(AutostartEntry(
                entry_id=f"task:{path_name}", kind="scheduled_task",
                name=row.get("n") or path_name,
                command=(f"{exec_path} {args}".strip() or None),
                location=row.get("p") or "\\",
                enabled=(str(row.get("s") or "").lower() != "disabled"),
                can_disable=False))
    except Exception:
        return []
    return out


# --------------------------------------------------------------------------- #
# 已安装软件（卸载信息）
# --------------------------------------------------------------------------- #
_UNINSTALL_KEYS = [
    (win32con.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (win32con.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (win32con.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]

_UNINSTALL_FIELDS = ("DisplayName", "Publisher", "DisplayVersion", "InstallLocation",
                     "UninstallString", "DisplayIcon", "EstimatedSize")


def enum_uninstall_entries() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for hive, subkey in _UNINSTALL_KEYS:
        try:
            root = win32api.RegOpenKeyEx(hive, subkey, 0, win32con.KEY_READ)
        except Exception:
            continue
        try:
            idx = 0
            while True:
                try:
                    sub = win32api.RegEnumKey(root, idx)
                except Exception:
                    break
                idx += 1
                try:
                    key = win32api.RegOpenKeyEx(root, sub, 0, win32con.KEY_READ)
                except Exception:
                    continue
                row: dict[str, Any] = {"key": sub}
                for field in _UNINSTALL_FIELDS:
                    try:
                        row[field] = win32api.RegQueryValueEx(key, field)[0]
                    except Exception:
                        row[field] = None
                win32api.RegCloseKey(key)
                if row.get("DisplayName"):
                    out.append(row)
        finally:
            win32api.RegCloseKey(root)
    return out


def uptime_seconds() -> float:
    return k32.GetTickCount64() / 1000.0


def sleep(seconds: float) -> None:
    time.sleep(seconds)
