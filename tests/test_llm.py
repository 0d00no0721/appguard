"""LLM 配置 / 文件校验 / 提示词 / Provider / 网关本地方法测试。"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from appguard.llm import config as llm_config
from appguard.llm import files as llm_files
from appguard.llm.errors import LlmError
from appguard.llm.prompt import build_messages, process_context
from appguard.llm.service import LlmService
from appguard.web import LOCAL_METHODS, WebBridge
from harness import check, eq, section, work_dir


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, method, params, *, timeout_s=None):
        self.calls.append((method, params))
        if method == "process.get":
            return {
                "name": "updater.exe", "pid": 4242, "ppid": 1,
                "parent_name": "services.exe", "exe": r"C:\Apps\Updater\updater.exe",
                "cmdline": r"updater.exe --token=SECRET123 --mode=auto",
                "user": "testuser", "integrity": "medium",
                "started_at": "2026-10-20T10:00:00+0800",
                "memory_mb": 12.5, "threads": 4,
                "file_company": "Example", "file_product": "Updater",
                "file_description": "Updater agent", "signed": True,
                "signer": "Example Ltd", "services": ["UpdaterSvc"],
                "windows": ["private window title"],
                "classification": {
                    "role": "updater", "origin": "service", "activity": "background",
                    "risk": "safe", "private": True, "microsoft": False,
                    "confidence": 0.9, "explanation": "更新器，后台运行",
                    "software_id": "sw-updater", "software_name": "Example Updater",
                    "reasons": ["名字像更新器"],
                },
            }
        if method == "software.get":
            return {
                "id": "sw-updater", "name": "Example Updater", "publisher": "Example",
                "version": "1.0", "install_dir": r"C:\Apps\Updater",
                "services": [{"name": "UpdaterSvc", "display_name": "Updater Service",
                              "state": "running", "start_type": "auto",
                              "binary_path": r"C:\Apps\Updater\svc.exe",
                              "account": "LocalSystem", "pid": 99,
                              "software_id": "sw-updater"}],
            }
        raise AssertionError("unexpected backend call: " + method)


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockLLM"

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.server.last_body = self.rfile.read(length)
        self.server.last_auth = self.headers.get("Authorization")
        if "/bad" in self.path:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"bad key"}')
            return
        payload = {"model": "mock-1",
                   "choices": [{"message": {"content": "这是 AI 解释"}}],
                   "usage": {"prompt_tokens": 9, "completion_tokens": 4,
                             "total_tokens": 13}}
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        return


def _mock_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def run() -> None:
    root = work_dir("llm")
    cfg_path = os.path.join(root, "llm.json")
    secret_path = os.path.join(root, "llm-secret.bin")
    audit_path = os.path.join(root, "llm-audit.jsonl")
    for path in (cfg_path, secret_path, audit_path):
        try:
            os.remove(path)
        except OSError:
            pass

    section("DPAPI / 配置")
    llm_config.set_api_key("sk-test-xyz", secret_path)
    eq("DPAPI 可以解密", llm_config.get_api_key(secret_path), "sk-test-xyz")
    check("DPAPI 文件存在", os.path.exists(secret_path))
    llm_config.clear_api_key(secret_path)
    check("清除后没有 Key", not llm_config.has_api_key(secret_path))

    cfg = llm_config.normalize_config({
        "enabled": True, "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    })
    eq("默认文件总上限", cfg["file_access"]["max_total_kb"], 256)
    try:
        llm_config.normalize_config({"base_url": "http://example.com/v1"})
        check("远程 HTTP 被拒绝", False)
    except LlmError as exc:
        eq("远程 HTTP 错误码", exc.code, "E_LLM_UNSAFE_URL")
    try:
        llm_config.normalize_config({"base_url": "https://x/v1", "max_tokens": 99999})
        check("max_tokens 上限", False)
    except LlmError:
        check("max_tokens 上限", True)

    section("文件校验 / 脱敏")
    access = cfg["file_access"]
    files = llm_files.validate_uploads([
        {"name": "config.ini", "text": "password=abc123\ntoken:SECRET\nmode=auto"}
    ], {"file_access": access})
    eq("文件数量", len(files), 1)
    check("文件大小", files[0]["size"] > 0)
    check("敏感值被脱敏", "abc123" not in files[0]["text"] and "[REDACTED]" in files[0]["text"])
    try:
        llm_files.validate_uploads([{"name": "x.exe", "text": "hi"}],
                                   {"file_access": access})
        check("扩展名被拒绝", False)
    except LlmError as exc:
        eq("扩展名错误码", exc.code, "E_LLM_FILE")
    try:
        llm_files.validate_uploads([{"name": "x.txt", "text": "a\x00b"}],
                                   {"file_access": access})
        check("二进制被拒绝", False)
    except LlmError:
        check("二进制被拒绝", True)

    section("Prompt / 上下文")
    proc = FakeBackend().call("process.get", {})
    ctx = process_context(proc)
    eq("窗口标题不进入上下文", "windows" in ctx, False)
    check("命令行 Token 被脱敏", "SECRET123" not in ctx["cmdline"])
    messages = build_messages(ctx, files, "它是什么？")
    check("系统提示强调不可信", "不可信" in messages[0]["content"])
    check("文件内容被标记", "FILE 1" in messages[1]["content"])
    check("用户问题进入 prompt", "它是什么" in messages[1]["content"])

    section("Provider HTTP")
    server = _mock_server()
    old_no_proxy = os.environ.get("NO_PROXY")
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/v1"
        out = _call_provider(base, "sk-test")
        eq("Provider 返回文本", out["text"], "这是 AI 解释")
        eq("Authorization 头", server.last_auth, "Bearer sk-test")
        check("请求包含 model", b"mock-test" in server.last_body)
        try:
            _call_provider(base + "/bad", "bad")
            check("401 被转换", False)
        except LlmError as exc:
            eq("401 错误码", exc.code, "E_LLM_AUTH")
    finally:
        server.shutdown()
        server.server_close()
        if old_no_proxy is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = old_no_proxy

    section("网关本地方法 / consent")
    backend = FakeBackend()
    svc = LlmService(backend, config_path=cfg_path, secret_path=secret_path,
                     audit_path=audit_path)
    saved = svc.set_config({
        "enabled": True, "base_url": "http://127.0.0.1:9/v1",
        "model": "mock-test", "file_access": {"enabled": True},
    })
    check("保存后 has_key 为 false", saved["has_key"] is False)
    bridge = WebBridge(backend, token="t", endpoint_path=os.path.join(root, "ep.json"),
                       static_root=os.path.join(os.path.dirname(os.path.dirname(__file__)), "web"),
                       llm=svc)
    local = bridge.local_call("llm.config.get", {})
    eq("config.get 不回传 Key", "api_key" in local, False)
    prep = bridge.local_call("llm.prepare", {"kind": "process", "pid": 4242})
    check("prepare 返回 consent", bool(prep.get("consent_id")))
    eq("prepare 的进程 PID", prep["preview"]["pid"], 4242)
    check("process.get 被调用", ("process.get", {"pid": 4242}) in backend.calls)
    check("llm 方法在本地白名单", "llm.explain" in LOCAL_METHODS)

    svc_prep = bridge.local_call("llm.prepare", {
        "kind": "service", "service_name": "UpdaterSvc",
        "software_id": "sw-updater"})
    eq("service prepare 类型", svc_prep["preview"]["kind"], "service")
    eq("service prepare 名称", svc_prep["preview"]["name"], "Updater Service")
    check("software.get 被调用", ("software.get", {"id": "sw-updater"}) in backend.calls)

    # 用 monkeypatch 替换网络调用，验证 consent/文件/审计。
    import appguard.llm.service as service_mod
    original_chat = service_mod.chat
    service_mod.chat = lambda cfg, key, messages, **kwargs: {
        "text": "AI 结果", "model": "mock", "usage": {"total_tokens": 7}}
    try:
        result = svc.explain(prep["consent_id"], [
            {"name": "config.ini", "size": 20, "text": "password=abc\nmode=auto"}
        ], "解释")
        eq("explain 文本", result["text"], "AI 结果")
        eq("explain 文件数", len(result["files"]), 1)
        check("审计文件存在", os.path.exists(audit_path))
        audit = open(audit_path, encoding="utf-8").read()
        check("审计不含文件内容", "password=abc" not in audit)
        check("审计不含 Key", "sk-test" not in audit)
        try:
            svc.explain(prep["consent_id"], [], "")
            check("consent 不能复用", False)
        except LlmError as exc:
            eq("consent 复用错误码", exc.code, "E_LLM_CONSENT")
    finally:
        service_mod.chat = original_chat


def _call_provider(base: str, key: str):
    from appguard.llm import providers
    return providers.chat(
        {"base_url": base, "model": "mock-test", "max_tokens": 64,
         "temperature": 0.2, "timeout_s": 5},
        key,
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
    )
