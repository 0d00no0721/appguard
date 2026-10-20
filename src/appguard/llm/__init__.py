"""AppGuard LLM 解释功能（可选、默认关闭）。"""
from __future__ import annotations

from .config import (DEFAULT_CONFIG, default_config_path, default_local_dir,
                     default_secret_path, get_api_key, has_api_key,
                     load_config, public_config, save_config, set_api_key,
                     clear_api_key, require_ready)
from .errors import LLM_ERROR_CODES, LlmError
from .service import LlmService

__all__ = [
    "DEFAULT_CONFIG", "LLM_ERROR_CODES", "LlmError", "LlmService",
    "clear_api_key", "default_config_path", "default_local_dir",
    "default_secret_path", "get_api_key", "has_api_key", "load_config",
    "public_config", "require_ready", "save_config", "set_api_key",
]
