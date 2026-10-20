"""OpenAI-compatible LLM 客户端（urllib，无第三方依赖）。"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any

from .errors import LlmError

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise urllib.error.HTTPError(req.full_url, code,
                                     "LLM endpoint redirect is not allowed", headers, fp)


def chat_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise LlmError("E_LLM_BAD_CONFIG", "Base URL 为空")
    if base.lower().endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json",
               "Accept": "application/json",
               "User-Agent": "AppGuard/0.4 LLM"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    return headers


def chat(config: dict[str, Any], api_key: str, messages: list[dict[str, str]],
         *, timeout_s: float | None = None) -> dict[str, Any]:
    url = chat_url(str(config.get("base_url") or ""))
    body = {
        "model": str(config.get("model") or ""),
        "messages": messages,
        "temperature": float(config.get("temperature", 0.2)),
        "max_tokens": int(config.get("max_tokens", 800)),
        "stream": False,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers=_headers(api_key))
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirect())
    timeout = float(timeout_s or config.get("timeout_s") or 45)
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(800).decode("utf-8", "replace")
        except Exception:                                   # noqa: BLE001
            pass
        if exc.code in (401, 403):
            raise LlmError("E_LLM_AUTH", "LLM 认证失败，请检查 API Key",
                           {"status": exc.code}) from exc
        if exc.code == 429:
            raise LlmError("E_LLM_RATE", "LLM 请求过于频繁/额度不足",
                           {"status": exc.code}) from exc
        if exc.code >= 500:
            raise LlmError("E_LLM_UPSTREAM", f"LLM 服务端错误（HTTP {exc.code}）",
                           {"status": exc.code, "body": detail}) from exc
        raise LlmError("E_LLM_HTTP", f"LLM 请求失败（HTTP {exc.code}）",
                       {"status": exc.code, "body": detail}) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if "timed out" in str(reason).lower():
            raise LlmError("E_LLM_TIMEOUT", "LLM 请求超时") from exc
        raise LlmError("E_LLM_NETWORK", f"无法连接 LLM：{reason}") from exc
    except TimeoutError as exc:
        raise LlmError("E_LLM_TIMEOUT", "LLM 请求超时") from exc
    except LlmError:
        raise
    except Exception as exc:                                # noqa: BLE001
        raise LlmError("E_LLM_NETWORK", f"LLM 请求失败：{exc}") from exc

    if len(raw) > MAX_RESPONSE_BYTES:
        raise LlmError("E_LLM_RESPONSE", "LLM 响应过大")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:                                # noqa: BLE001
        raise LlmError("E_LLM_RESPONSE", "LLM 返回了无效 JSON") from exc
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise LlmError("E_LLM_RESPONSE", "LLM 响应缺少 choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        content = "".join(str(part.get("text") or "") if isinstance(part, dict) else str(part)
                          for part in content)
    if not isinstance(content, str) or not content.strip():
        raise LlmError("E_LLM_RESPONSE", "LLM 返回了空内容")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return {
        "text": content.strip(),
        "model": str(payload.get("model") or config.get("model") or ""),
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }


def test_connection(config: dict[str, Any], api_key: str) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": "Reply with the single word: ok"},
        {"role": "user", "content": "ping"},
    ]
    test_cfg = dict(config)
    test_cfg["max_tokens"] = 16
    result = chat(test_cfg, api_key, messages,
                  timeout_s=min(15.0, float(config.get("timeout_s") or 15)))
    return {"ok": True, "model": result.get("model"),
            "reply": result.get("text", "")[:80]}
