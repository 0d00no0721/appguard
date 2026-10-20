"""LLM 配置与 DPAPI 密钥存储。

非敏感配置：%LOCALAPPDATA%\\AppGuard\\llm.json
API Key     ：%LOCALAPPDATA%\\AppGuard\\llm-secret.bin（DPAPI，当前用户）
"""
from __future__ import annotations

import copy
import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from typing import Any
from urllib.parse import urlparse

from .errors import LlmError

DEFAULT_EXTENSIONS = [".txt", ".log", ".ini", ".cfg", ".conf", ".json", ".yaml",
                      ".yml", ".toml", ".xml", ".md", ".csv"]
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "provider": "openai-compatible",
    "base_url": "",
    "model": "",
    "temperature": 0.2,
    "max_tokens": 800,
    "timeout_s": 45,
    "file_access": {
        "enabled": True,
        "max_files": 10,
        "max_file_kb": 64,
        "max_total_kb": 256,
        "extensions": list(DEFAULT_EXTENSIONS),
    },
}
PROVIDERS = {"openai-compatible"}
ENTROPY = b"AppGuard.LLM.v1"


def default_local_dir() -> str:
    override = os.environ.get("APPGUARD_LLM_DIR")
    if override:
        return os.path.abspath(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "AppGuard")


def default_config_path() -> str:
    return os.path.join(default_local_dir(), "llm.json")


def default_secret_path() -> str:
    return os.path.join(default_local_dir(), "llm-secret.bin")


def _atomic_write(path: str, data: bytes, mode: int | None = None) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".llm-", suffix=".writing", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _validate_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LlmError("E_LLM_BAD_CONFIG", "Base URL 不能为空")
    if len(text) > 500:
        raise LlmError("E_LLM_BAD_CONFIG", "Base URL 过长")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise LlmError("E_LLM_BAD_CONFIG", "Base URL 只支持 http/https")
    if parsed.username or parsed.password:
        raise LlmError("E_LLM_UNSAFE_URL", "Base URL 不能包含用户名/密码")
    if not parsed.hostname:
        raise LlmError("E_LLM_BAD_CONFIG", "Base URL 缺少主机名")
    if parsed.scheme == "http" and parsed.hostname.lower() not in {
            "127.0.0.1", "localhost", "::1"}:
        raise LlmError("E_LLM_UNSAFE_URL",
                       "远程 LLM 必须使用 HTTPS；只有 localhost 允许 HTTP")
    return text.rstrip("/")


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        for key in ("enabled", "provider", "base_url", "model", "temperature",
                    "max_tokens", "timeout_s"):
            if key in raw:
                cfg[key] = raw[key]
        files = raw.get("file_access")
        if isinstance(files, dict):
            for key in ("enabled", "max_files", "max_file_kb", "max_total_kb",
                        "extensions"):
                if key in files:
                    cfg["file_access"][key] = files[key]
    cfg["enabled"] = bool(cfg.get("enabled"))
    provider = str(cfg.get("provider") or "openai-compatible")
    if provider not in PROVIDERS:
        raise LlmError("E_LLM_BAD_CONFIG", f"不支持的 provider：{provider}")
    cfg["provider"] = provider
    cfg["base_url"] = str(cfg.get("base_url") or "").strip()
    if cfg["base_url"]:
        cfg["base_url"] = _validate_url(cfg["base_url"])
    cfg["model"] = str(cfg.get("model") or "").strip()
    try:
        cfg["temperature"] = float(cfg.get("temperature", 0.2))
    except (TypeError, ValueError) as exc:
        raise LlmError("E_LLM_BAD_CONFIG", "temperature 必须是数字") from exc
    if not -0.1 <= cfg["temperature"] <= 2.0:
        raise LlmError("E_LLM_BAD_CONFIG", "temperature 超出范围")
    try:
        cfg["max_tokens"] = int(cfg.get("max_tokens", 800))
        cfg["timeout_s"] = float(cfg.get("timeout_s", 45))
    except (TypeError, ValueError) as exc:
        raise LlmError("E_LLM_BAD_CONFIG", "max_tokens/timeout_s 必须是数字") from exc
    if not 64 <= cfg["max_tokens"] <= 4096:
        raise LlmError("E_LLM_BAD_CONFIG", "max_tokens 必须在 64~4096")
    if not 5 <= cfg["timeout_s"] <= 120:
        raise LlmError("E_LLM_BAD_CONFIG", "timeout_s 必须在 5~120")
    files = cfg["file_access"]
    files["enabled"] = bool(files.get("enabled", True))
    try:
        files["max_files"] = int(files.get("max_files", 10))
        files["max_file_kb"] = int(files.get("max_file_kb", 64))
        files["max_total_kb"] = int(files.get("max_total_kb", 256))
    except (TypeError, ValueError) as exc:
        raise LlmError("E_LLM_BAD_CONFIG", "文件限额必须是数字") from exc
    if not 1 <= files["max_files"] <= 20:
        raise LlmError("E_LLM_BAD_CONFIG", "max_files 必须在 1~20")
    if not 1 <= files["max_file_kb"] <= 256:
        raise LlmError("E_LLM_BAD_CONFIG", "max_file_kb 必须在 1~256")
    if not 1 <= files["max_total_kb"] <= 1024:
        raise LlmError("E_LLM_BAD_CONFIG", "max_total_kb 必须在 1~1024")
    if files["max_total_kb"] < files["max_file_kb"]:
        raise LlmError("E_LLM_BAD_CONFIG", "max_total_kb 不能小于 max_file_kb")
    exts = files.get("extensions") or DEFAULT_EXTENSIONS
    if not isinstance(exts, list):
        raise LlmError("E_LLM_BAD_CONFIG", "extensions 必须是数组")
    normalized = []
    for ext in exts:
        text = str(ext or "").strip().lower()
        if not text:
            continue
        if not text.startswith("."):
            text = "." + text
        if len(text) > 12 or "/" in text or "\\" in text:
            raise LlmError("E_LLM_BAD_CONFIG", f"非法扩展名：{ext}")
        normalized.append(text)
    files["extensions"] = sorted(set(normalized)) or list(DEFAULT_EXTENSIONS)
    return cfg


def require_ready(cfg: dict[str, Any], has_key: bool) -> None:
    if not cfg.get("enabled"):
        raise LlmError("E_LLM_DISABLED", "AI 解释未启用")
    if not cfg.get("base_url") or not cfg.get("model"):
        raise LlmError("E_LLM_NOT_CONFIGURED", "请先在设置里配置 Base URL 和模型")
    if not has_key and not _is_local_url(cfg.get("base_url", "")):
        raise LlmError("E_LLM_NOT_CONFIGURED", "还没有配置 API Key")


def _is_local_url(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").lower()
    except Exception:                                           # noqa: BLE001
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def load_config(path: str | None = None) -> dict[str, Any]:
    path = path or default_config_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    return normalize_config(data)


def save_config(cfg: dict[str, Any], path: str | None = None) -> dict[str, Any]:
    normalized = normalize_config(cfg)
    path = path or default_config_path()
    raw = json.dumps(normalized, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write(path, raw, mode=0o600)
    return normalized


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise LlmError("E_LLM_KEY", "DPAPI 只在 Windows 上可用")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    entropy_buf = ctypes.create_string_buffer(ENTROPY, len(ENTROPY))
    entropy = _Blob(len(ENTROPY), ctypes.cast(entropy_buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = _Blob()
    if protect:
        fn = crypt32.CryptProtectData
        fn.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR,
                       ctypes.POINTER(_Blob), wintypes.LPVOID, wintypes.LPVOID,
                       wintypes.DWORD, ctypes.POINTER(_Blob)]
        fn.restype = wintypes.BOOL
        ok = fn(ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None,
                0x1, ctypes.byref(blob_out))          # CRYPTPROTECT_UI_FORBIDDEN
    else:
        fn = crypt32.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR),
                       ctypes.POINTER(_Blob), wintypes.LPVOID, wintypes.LPVOID,
                       wintypes.DWORD, ctypes.POINTER(_Blob)]
        fn.restype = wintypes.BOOL
        description = wintypes.LPWSTR()
        ok = fn(ctypes.byref(blob_in), ctypes.byref(description),
                ctypes.byref(entropy), None, None, 0x1, ctypes.byref(blob_out))
        if description:
            try:
                kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))
            except Exception:                               # noqa: BLE001
                pass
    if not ok:
        raise LlmError("E_LLM_KEY", "DPAPI 操作失败（错误 %s）" % ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        try:
            kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))
        except Exception:                                   # noqa: BLE001
            pass


def set_api_key(key: str, path: str | None = None) -> None:
    text = str(key or "").strip()
    if not text:
        raise LlmError("E_LLM_KEY", "API Key 为空")
    encrypted = _dpapi(text.encode("utf-8"), True)
    _atomic_write(path or default_secret_path(), encrypted, mode=0o600)


def get_api_key(path: str | None = None) -> str:
    path = path or default_secret_path()
    try:
        with open(path, "rb") as handle:
            encrypted = handle.read()
    except OSError:
        return ""
    if not encrypted:
        return ""
    try:
        return _dpapi(encrypted, False).decode("utf-8")
    except LlmError:
        return ""


def has_api_key(path: str | None = None) -> bool:
    return bool(get_api_key(path))


def clear_api_key(path: str | None = None) -> None:
    path = path or default_secret_path()
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise LlmError("E_LLM_KEY", f"删除 API Key 失败：{exc}") from exc


def public_config(cfg: dict[str, Any], secret_path: str | None = None) -> dict[str, Any]:
    out = copy.deepcopy(cfg)
    out["has_key"] = has_api_key(secret_path)
    out["provider"] = cfg.get("provider") or "openai-compatible"
    return out
