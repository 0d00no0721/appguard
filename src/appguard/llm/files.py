"""文件上传校验、文本判断与脱敏。

v1 的文件内容来自浏览器的 `<input type="file">`，网关不按路径读取磁盘。
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from .errors import LlmError

_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)"
               r"(\s*[:=]\s*)([^\s\"'<>]{1,256})"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
               re.S),
]
_MAX_QUESTION = 400


def redact_text(text: str) -> str:
    out = str(text or "")
    out = _SECRET_PATTERNS[0].sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", out)
    out = _SECRET_PATTERNS[1].sub("Bearer [REDACTED]", out)
    out = _SECRET_PATTERNS[2].sub("-----BEGIN PRIVATE KEY-----[REDACTED]"
                                  "-----END PRIVATE KEY-----", out)
    return out


def sanitize_command_line(cmdline: str | None) -> str:
    if not cmdline:
        return ""
    text = redact_text(str(cmdline))
    # 常见 token 作为独立参数出现的情况。
    text = re.sub(r"(?i)(--?(?:token|key|password|secret)\s+)(\S+)",
                  lambda m: m.group(1) + "[REDACTED]", text)
    return text[:1200]


def sanitize_question(question: str | None) -> str:
    text = redact_text(str(question or "")).strip()
    if len(text) > _MAX_QUESTION:
        text = text[:_MAX_QUESTION]
    return text


def _safe_name(name: Any) -> str:
    text = str(name or "").replace("\\", "/").split("/")[-1].strip()
    if not text or len(text) > 255:
        raise LlmError("E_LLM_FILE", "文件名非法")
    if any(ch in text for ch in "\r\n\t"):
        raise LlmError("E_LLM_FILE", "文件名包含控制字符")
    return text


def _looks_binary(text: str) -> bool:
    if "\x00" in text:
        return True
    sample = text[:4096]
    if not sample:
        return False
    controls = sum(1 for ch in sample if ord(ch) < 9 or 13 < ord(ch) < 32)
    return controls / max(1, len(sample)) > 0.02


def validate_uploads(files: Any, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    access = cfg.get("file_access") or {}
    if not access.get("enabled", True):
        raise LlmError("E_LLM_FILE", "文件附件功能已关闭")
    if files is None:
        files = []
    if not isinstance(files, list):
        raise LlmError("E_LLM_FILE", "files 必须是数组")
    max_files = int(access.get("max_files") or 10)
    max_file = int(access.get("max_file_kb") or 64) * 1024
    max_total = int(access.get("max_total_kb") or 256) * 1024
    extensions = set(access.get("extensions") or [])
    if len(files) > max_files:
        raise LlmError("E_LLM_FILE_TOO_LARGE", f"最多只能附加 {max_files} 个文件")
    out: list[dict[str, Any]] = []
    total = 0
    for item in files:
        if not isinstance(item, dict):
            raise LlmError("E_LLM_FILE", "文件项必须是对象")
        name = _safe_name(item.get("name"))
        ext = os.path.splitext(name)[1].lower()
        if extensions and ext not in extensions:
            raise LlmError("E_LLM_FILE", f"不允许的文件类型：{name}")
        text = item.get("text")
        if not isinstance(text, str):
            raise LlmError("E_LLM_FILE", f"文件内容必须是文本：{name}")
        if _looks_binary(text):
            raise LlmError("E_LLM_FILE", f"看起来是二进制文件：{name}")
        raw = text.encode("utf-8")
        if len(raw) > max_file:
            raise LlmError("E_LLM_FILE_TOO_LARGE",
                           f"文件过大：{name}（上限 {max_file // 1024} KB）")
        total += len(raw)
        if total > max_total:
            raise LlmError("E_LLM_FILE_TOO_LARGE",
                           f"附件总大小超过 {max_total // 1024} KB")
        digest = hashlib.sha256(raw).hexdigest()
        expected = str(item.get("sha256") or "").strip().lower()
        if expected and expected != digest:
            raise LlmError("E_LLM_FILE", f"文件哈希不匹配：{name}")
        out.append({"name": name, "size": len(raw), "sha256": digest,
                    "text": redact_text(text)})
    return out
