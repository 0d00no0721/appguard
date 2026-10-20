"""网关内的 LLM 编排：配置、consent、文件校验、调用与审计。"""
from __future__ import annotations

import copy
import json
import os
import secrets
import threading
import time
from typing import Any

from . import config as cfgmod
from .errors import LlmError
from .files import sanitize_question, validate_uploads
from .prompt import build_messages, preview_context, process_context, service_context
from .providers import chat, test_connection

CONSENT_TTL_S = 120.0
RATE_WINDOW_S = 60.0
RATE_MAX = 6
AUDIT_MAX_BYTES = 5 * 1024 * 1024


class LlmService:
    def __init__(self, backend, *, local_dir: str | None = None,
                 config_path: str | None = None,
                 secret_path: str | None = None,
                 audit_path: str | None = None) -> None:
        self.backend = backend
        local = local_dir or cfgmod.default_local_dir()
        self.config_path = config_path or os.path.join(local, "llm.json")
        self.secret_path = secret_path or os.path.join(local, "llm-secret.bin")
        self.audit_path = audit_path or os.path.join(local, "llm-audit.jsonl")
        self._consents: dict[str, dict[str, Any]] = {}
        self._consent_lock = threading.Lock()
        self._call_lock = threading.Lock()
        self._rates: list[float] = []
        self._rate_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def _load(self) -> dict[str, Any]:
        return cfgmod.load_config(self.config_path)

    def _key(self) -> str:
        return cfgmod.get_api_key(self.secret_path)

    def public_config(self) -> dict[str, Any]:
        return cfgmod.public_config(self._load(), self.secret_path)

    def set_config(self, patch: dict[str, Any], *, api_key: str | None = None,
                   clear_key: bool = False) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise LlmError("E_LLM_BAD_CONFIG", "config 必须是对象")
        current = self._load()
        merged = copy.deepcopy(current)
        for key in ("enabled", "provider", "base_url", "model", "temperature",
                    "max_tokens", "timeout_s"):
            if key in patch:
                merged[key] = patch[key]
        if isinstance(patch.get("file_access"), dict):
            merged.setdefault("file_access", {})
            merged["file_access"].update(patch["file_access"])
        normalized = cfgmod.save_config(merged, self.config_path)
        if clear_key:
            cfgmod.clear_api_key(self.secret_path)
        elif api_key is not None and str(api_key).strip():
            cfgmod.set_api_key(str(api_key), self.secret_path)
        return cfgmod.public_config(normalized, self.secret_path)

    def clear_key(self) -> dict[str, Any]:
        cfgmod.clear_api_key(self.secret_path)
        return self.public_config()

    def test(self, patch: dict[str, Any] | None = None,
             api_key: str | None = None) -> dict[str, Any]:
        current = self._load()
        if isinstance(patch, dict):
            for key in ("provider", "base_url", "model", "temperature",
                        "max_tokens", "timeout_s"):
                if key in patch:
                    current[key] = patch[key]
        current = cfgmod.normalize_config(current)
        if not current.get("base_url") or not current.get("model"):
            raise LlmError("E_LLM_BAD_CONFIG", "请先填写 Base URL 和模型")
        key = str(api_key).strip() if api_key and str(api_key).strip() else self._key()
        if not key and not cfgmod._is_local_url(current.get("base_url", "")):
            raise LlmError("E_LLM_NOT_CONFIGURED", "还没有配置 API Key")
        return test_connection(current, key)

    # ------------------------------------------------------------------ #
    def _backend_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.backend.call(method, params, timeout_s=8.0)

    def _get_process(self, pid: int) -> dict[str, Any]:
        if pid <= 0:
            raise LlmError("E_LLM_BAD_CONFIG", "PID 非法")
        try:
            return self._backend_call("process.get", {"pid": pid})
        except Exception as exc:                            # noqa: BLE001
            if getattr(exc, "code", "") == "E_NOT_FOUND":
                raise LlmError("E_LLM_NOT_FOUND", f"找不到进程 {pid}") from exc
            raise

    def _get_service(self, service_name: str,
                     software_id: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        if not software_id:
            raise LlmError("E_LLM_BAD_CONFIG", "解释服务需要 software_id")
        try:
            sw = self._backend_call("software.get", {"id": software_id})
        except Exception as exc:                            # noqa: BLE001
            if getattr(exc, "code", "") == "E_NOT_FOUND":
                raise LlmError("E_LLM_NOT_FOUND", "找不到软件") from exc
            raise
        for svc in sw.get("services") or []:
            if str(svc.get("name") or "").lower() == service_name.lower():
                return svc, sw
        raise LlmError("E_LLM_NOT_FOUND", f"找不到服务 {service_name}")

    def _cleanup_consents(self) -> None:
        now = time.time()
        with self._consent_lock:
            for cid in list(self._consents):
                if self._consents[cid]["expires"] < now or self._consents[cid]["used"]:
                    self._consents.pop(cid, None)

    def _new_consent(self, context: dict[str, Any]) -> str:
        self._cleanup_consents()
        with self._consent_lock:
            if len(self._consents) >= 50:
                oldest = min(self._consents, key=lambda k: self._consents[k]["created"])
                self._consents.pop(oldest, None)
            cid = secrets.token_urlsafe(24)
            self._consents[cid] = {
                "context": context,
                "created": time.time(),
                "expires": time.time() + CONSENT_TTL_S,
                "used": False,
            }
            return cid

    # ------------------------------------------------------------------ #
    def prepare(self, kind: str, *, pid: int | None = None,
                service_name: str | None = None,
                software_id: str | None = None) -> dict[str, Any]:
        cfg = self._load()
        key = self._key()
        cfgmod.require_ready(cfg, bool(key))
        if kind == "process":
            proc = self._get_process(int(pid or 0))
            context = process_context(proc)
        elif kind == "service":
            if not service_name:
                raise LlmError("E_LLM_BAD_CONFIG", "缺少 service_name")
            svc, sw = self._get_service(str(service_name), software_id)
            context = service_context(svc, sw)
        else:
            raise LlmError("E_LLM_BAD_CONFIG", "kind 只能是 process/service")
        cid = self._new_consent(context)
        access = cfg.get("file_access") or {}
        return {
            "consent_id": cid,
            "expires_s": int(CONSENT_TTL_S),
            "provider": cfg.get("provider"),
            "model": cfg.get("model"),
            "base_url": cfg.get("base_url"),
            "has_key": bool(key),
            "preview": preview_context(context),
            "files_allowed": bool(access.get("enabled", True)),
            "limits": {
                "max_files": access.get("max_files"),
                "max_file_kb": access.get("max_file_kb"),
                "max_total_kb": access.get("max_total_kb"),
                "extensions": access.get("extensions"),
            },
        }

    def _check_rate(self) -> None:
        now = time.time()
        with self._rate_lock:
            self._rates = [t for t in self._rates if now - t < RATE_WINDOW_S]
            if len(self._rates) >= RATE_MAX:
                raise LlmError("E_LLM_RATE", "AI 请求过于频繁，请稍后再试")
            self._rates.append(now)

    def explain(self, consent_id: str, files: Any = None,
                question: str | None = None) -> dict[str, Any]:
        cfg = self._load()
        key = self._key()
        cfgmod.require_ready(cfg, bool(key))
        if not self._call_lock.acquire(blocking=False):
            raise LlmError("E_LLM_BUSY", "另一个 AI 解释正在进行，请稍后")
        try:
            self._check_rate()
            now = time.time()
            with self._consent_lock:
                consent = self._consents.get(str(consent_id or ""))
                if consent is None or consent["expires"] < now or consent["used"]:
                    raise LlmError("E_LLM_CONSENT", "确认已过期，请重新点击 AI 解释")
                consent["used"] = True
                context = consent["context"]
            selected = validate_uploads(files, cfg)
            messages = build_messages(context, selected, sanitize_question(question))
            started = time.time()
            try:
                result = chat(cfg, key, messages)
            except LlmError as exc:
                self._audit("error", context, selected, cfg, exc=exc)
                raise
            elapsed = int((time.time() - started) * 1000)
            response = {
                "text": result.get("text", ""),
                "provider": cfg.get("provider"),
                "model": result.get("model") or cfg.get("model"),
                "usage": result.get("usage") or {},
                "elapsed_ms": elapsed,
                "files": [{"name": f["name"], "size": f["size"], "sha256": f["sha256"]}
                          for f in selected],
            }
            self._audit("ok", context, selected, cfg, response=response)
            return response
        finally:
            self._call_lock.release()

    # ------------------------------------------------------------------ #
    def _audit(self, status: str, context: dict[str, Any],
               files: list[dict[str, Any]], cfg: dict[str, Any],
               *, response: dict[str, Any] | None = None,
               exc: LlmError | None = None) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "status": status,
            "kind": context.get("kind"),
            "name": context.get("name"),
            "pid": context.get("pid"),
            "provider": cfg.get("provider"),
            "model": (response or {}).get("model") or cfg.get("model"),
            "files": [{"name": f.get("name"), "size": f.get("size"),
                       "sha256": f.get("sha256")} for f in files],
            "usage": (response or {}).get("usage") or {},
            "elapsed_ms": (response or {}).get("elapsed_ms"),
        }
        if exc is not None:
            record["error"] = {"code": exc.code, "message": exc.message}
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.audit_path)), exist_ok=True)
            try:
                if os.path.getsize(self.audit_path) > AUDIT_MAX_BYTES:
                    os.replace(self.audit_path, self.audit_path + ".1")
            except OSError:
                pass
            with open(self.audit_path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
