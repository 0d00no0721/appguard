"""LLM 功能的统一错误类型。"""
from __future__ import annotations


class LlmError(Exception):
    def __init__(self, code: str, message: str, detail: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


LLM_ERROR_CODES = {
    "E_LLM_DISABLED": 403,
    "E_LLM_NOT_CONFIGURED": 409,
    "E_LLM_BAD_CONFIG": 400,
    "E_LLM_KEY": 500,
    "E_LLM_AUTH": 401,
    "E_LLM_RATE": 429,
    "E_LLM_TIMEOUT": 504,
    "E_LLM_NETWORK": 502,
    "E_LLM_UPSTREAM": 502,
    "E_LLM_HTTP": 502,
    "E_LLM_RESPONSE": 502,
    "E_LLM_CONSENT": 410,
    "E_LLM_BUSY": 409,
    "E_LLM_NOT_FOUND": 404,
    "E_LLM_FILE": 400,
    "E_LLM_FILE_TOO_LARGE": 413,
    "E_LLM_UNSAFE_URL": 400,
}
