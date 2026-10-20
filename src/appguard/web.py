"""本地 HTTP / SSE 网关：浏览器 UI 的后端接口 + 静态页面（普通用户进程）。

设计：
  * 为**浏览器前端**提供 JSON API、SSE 事件流与 `web/` 静态文件；
  * 网关本身**不提升权限**：通过命名管道/回环 TCP 调用已运行的守护；
  * 只绑 127.0.0.1，随机 token，校验 Host/Origin，拒绝跨站请求；
  * SSE 用 `subscribe(since_seq)` 轮询回放（0.8s），没有连接时不轮询。

接口（v0.4）：
  GET  /                       前端页面（web/index.html；资源缺失时回退为 API 发现 JSON）
  GET  /api/health             网关 + 守护存活 + 前端资源状态
  GET  /api/events?token=&since_seq=   SSE 事件流（EventSource；token 可走 query）
  POST /api/<method>           JSON body 即 params；Authorization: Bearer <token>
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from . import ipc
from .llm import LLM_ERROR_CODES, LlmError, LlmService

VERSION = "guardweb/0.4.0"
MAX_BODY = 1 * 1024 * 1024
SSE_POLL_SECONDS = 0.8
SSE_HEARTBEAT_SECONDS = 15.0
FRONTEND_FILES = ("index.html", "app.css", "app.js")
DEFAULT_WEB_ROOT = os.environ.get("APPGUARD_WEB_ROOT") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "web"))

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

HTML_CSP = ("default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; frame-ancestors 'none'")

# 浏览器能调用的方法白名单。刻意排除：
#   daemon.shutdown（浏览器不应该关守护）、audit.export（任意路径写盘）、
#   subscribe/unsubscribe（由 SSE 网关自己管理）、hello（网关已握手）。
WEB_METHODS = {
    "ping",
    "status.get",
    "snapshot.get",
    "software.list",
    "software.get",
    "process.list",
    "process.get",
    "process.explain",
    "classify.rules",
    "classify.reload",
    "cleanup.plan",
    "cleanup.execute",
    "cleanup.history",
    "policy.get",
    "policy.set",
    "protect.add",
    "protect.remove",
    "audit.tail",
    "audit.query",
    "kb.stats",
    "kb.review",
    "kb.apply",
    "kb.rollback",
    "daemon.reload",
}

# 这些方法由网关本地处理（不经过守护）：
#   * 浏览器只把 API Key 发给本地网关，网关用 DPAPI 保存；
#   * LLM/文件校验都在普通用户网关内完成。
LOCAL_METHODS = {
    "llm.config.get",
    "llm.config.set",
    "llm.config.test",
    "llm.config.clear_key",
    "llm.prepare",
    "llm.explain",
}

log = logging.getLogger("appguard.web")


def default_endpoint_path() -> str:
    """token 属于用户级秘密，放在 LocalAppData，而不是所有用户可读的 ProgramData。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "AppGuard", "web-endpoint.json")


class Backend(Protocol):
    def call(self, method: str, params: dict[str, Any], *,
             timeout_s: float | None = None) -> dict[str, Any]: ...
    def probe(self) -> bool: ...
    def subscribe(self, conn_id: str, since_seq: int) -> dict[str, Any]: ...
    def unsubscribe(self, conn_id: str) -> None: ...


class IpcBackend:
    """生产网关：普通用户进程，通过现有 IPC 连接管理员/普通守护。"""

    def __init__(self, data_dir: str, pipe_name: str = ipc.PIPE_NAME,
                 *, timeout: float = 10.0) -> None:
        self.data_dir = data_dir
        self.pipe_name = pipe_name
        self.timeout = timeout
        self._lock = threading.RLock()
        self._client = None
        self._events: dict[str, Any] = {}

    def _connect(self):
        if self._client is None:
            self._client = ipc.make_client(self.data_dir, self.pipe_name,
                                           timeout=self.timeout)
        return self._client

    def _drop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def call(self, method: str, params: dict[str, Any], *,
             timeout_s: float | None = None) -> dict[str, Any]:
        # 长动作（执行/计划/知识库/全量快照）用独立 IPC 连接，避免阻塞 SSE 与健康检查。
        if (method in {"cleanup.execute", "cleanup.plan", "kb.review", "kb.apply",
                       "kb.rollback", "snapshot.get"}
                or (timeout_s is not None and timeout_s > 20)):
            return self._call_isolated(method, params, timeout_s)
        with self._lock:
            try:
                client = self._connect()
            except Exception as exc:                            # noqa: BLE001
                raise ipc.IpcError(ipc.E_IPC, f"守护不可达：{exc}") from exc
            old = getattr(client, "timeout", self.timeout)
            if timeout_s:
                client.timeout = max(1.0, min(600.0, float(timeout_s)))
            try:
                return client.call(method, **params)
            except ipc.IpcError as exc:
                if exc.code in (ipc.E_IPC, ipc.E_TIMEOUT):
                    self._drop()
                raise
            finally:
                if self._client is not None and hasattr(self._client, "timeout"):
                    self._client.timeout = old

    def _call_isolated(self, method: str, params: dict[str, Any],
                       timeout_s: float | None) -> dict[str, Any]:
        try:
            client = ipc.make_client(self.data_dir, self.pipe_name,
                                     timeout=self.timeout)
        except Exception as exc:                                # noqa: BLE001
            raise ipc.IpcError(ipc.E_IPC, f"守护不可达：{exc}") from exc
        try:
            if timeout_s:
                client.timeout = max(1.0, min(600.0, float(timeout_s)))
            return client.call(method, **params)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def probe(self) -> bool:
        try:
            self.call("ping", {}, timeout_s=1.5)
            return True
        except Exception:
            return False

    def subscribe(self, conn_id: str, since_seq: int) -> dict[str, Any]:
        with self._lock:
            client = self._events.get(conn_id)
            if client is None:
                try:
                    client = ipc.make_client(self.data_dir, self.pipe_name,
                                             timeout=max(self.timeout, 30.0))
                except Exception as exc:                        # noqa: BLE001
                    raise ipc.IpcError(ipc.E_IPC, f"事件通道不可达：{exc}") from exc
                self._events[conn_id] = client
            return client.call("subscribe", since_seq=since_seq)

    def unsubscribe(self, conn_id: str) -> None:
        with self._lock:
            client = self._events.pop(conn_id, None)
        if client is None:
            return
        try:
            client.call("unsubscribe")
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


class DispatcherBackend:
    """开发/测试：进程内直跑，不依赖管道。"""

    def __init__(self, daemon) -> None:
        self.daemon = daemon
        self._lock = threading.RLock()

    def call(self, method: str, params: dict[str, Any], *,
             timeout_s: float | None = None) -> dict[str, Any]:
        with self._lock:
            response = self.daemon.dispatcher.dispatch(
                {"id": 1, "method": method, "params": params})
        if response.get("ok"):
            return response.get("result") or {}
        error = response.get("error") or {}
        raise ipc.IpcError(error.get("code") or ipc.E_INTERNAL,
                           error.get("message") or "未知错误", error.get("detail"))

    def probe(self) -> bool:
        return True

    def subscribe(self, conn_id: str, since_seq: int) -> dict[str, Any]:
        return self.daemon.bus.subscribe(conn_id, since_seq=since_seq)

    def unsubscribe(self, conn_id: str) -> None:
        try:
            self.daemon.bus.unsubscribe(conn_id)
        except Exception:
            pass


def _http_status(code: str) -> int:
    if code in LLM_ERROR_CODES:
        return LLM_ERROR_CODES[code]
    if code in (ipc.E_BAD_REQUEST, getattr(ipc, "E_FRAME_TOO_LARGE", "")):
        return 400
    if code in (ipc.E_PERMISSION, ipc.E_DEGRADED, ipc.E_PROTECTED):
        return 403
    if code in (ipc.E_NOT_FOUND, ipc.E_UNKNOWN_METHOD):
        return 404
    if code in (ipc.E_BUSY,):
        return 409
    if code in (ipc.E_PLAN_EXPIRED, ipc.E_PLAN_STALE):
        return 410
    if code == ipc.E_TIMEOUT:
        return 504
    if code == ipc.E_IPC:
        return 503
    return 500


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """客户端中途关闭（SSE/浏览器刷新）是正常现象，不要打 traceback。"""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError,
                            BrokenPipeError, OSError)):
            return
        super().handle_error(request, client_address)


class WebBridge:
    def __init__(self, backend: Backend, *, host: str = "127.0.0.1", port: int = 0,
                 token: str | None = None, endpoint_path: str | None = None,
                 max_body: int = MAX_BODY, static_root: str | None = None,
                 llm: LlmService | None = None) -> None:
        self.backend = backend
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(32)
        self.endpoint_path = endpoint_path or default_endpoint_path()
        self.max_body = max_body
        self.static_root = os.path.abspath(static_root or DEFAULT_WEB_ROOT)
        self.llm = llm or LlmService(backend)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._started_at = time.time()

    def local_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """网关本地方法（LLM 配置/解释），不经过守护 IPC。"""
        if method == "llm.config.get":
            return self.llm.public_config()
        if method == "llm.config.set":
            return self.llm.set_config(
                params.get("config") if isinstance(params.get("config"), dict) else {},
                api_key=params.get("api_key"),
                clear_key=bool(params.get("clear_key")))
        if method == "llm.config.test":
            patch = params.get("config") if isinstance(params.get("config"), dict) else None
            return self.llm.test(patch, params.get("api_key"))
        if method == "llm.config.clear_key":
            return self.llm.clear_key()
        if method == "llm.prepare":
            return self.llm.prepare(str(params.get("kind") or "process"),
                                    pid=params.get("pid"),
                                    service_name=params.get("service_name"),
                                    software_id=params.get("software_id"))
        if method == "llm.explain":
            return self.llm.explain(str(params.get("consent_id") or ""),
                                    params.get("files"),
                                    params.get("question"))
        raise ipc.IpcError(ipc.E_UNKNOWN_METHOD, f"网关本地不支持：{method}")

    # ------------------------------------------------------------------ #
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    @property
    def url(self) -> str:
        return f"{self.base_url}#token={self.token}"

    def _allowed_hosts(self) -> set[str]:
        return {f"{self.host}:{self.port}", f"localhost:{self.port}"}

    def _allowed_origins(self) -> set[str]:
        return {f"http://{self.host}:{self.port}",
                f"http://localhost:{self.port}"}

    def start(self) -> "WebBridge":
        bridge = self
        handler = _make_handler(bridge)
        server = _QuietThreadingHTTPServer((self.host, self.port), handler)
        server.bridge = bridge             # type: ignore[attr-defined]
        self._server = server
        self.port = int(server.server_address[1])
        self._thread = threading.Thread(target=server.serve_forever,
                                        name="guardweb-http", daemon=True)
        self._thread.start()
        self._write_endpoint()
        return self

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        self._remove_endpoint()

    def _write_endpoint(self) -> None:
        path = os.path.abspath(self.endpoint_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "transport": "http",
            "version": VERSION,
            "host": self.host,
            "port": self.port,
            "url": self.url,
            "token": self.token,
            "pid": os.getpid(),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        tmp = path + ".writing"
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass

    def _remove_endpoint(self) -> None:
        try:
            path = os.path.abspath(self.endpoint_path)
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if int(payload.get("pid") or 0) == os.getpid():
                os.remove(path)
        except Exception:
            pass

    def frontend_status(self) -> dict[str, Any]:
        """静态页面是否齐全：index.html + app.css + app.js。"""
        files = {name: os.path.isfile(os.path.join(self.static_root, name))
                 for name in FRONTEND_FILES}
        if all(files.values()):
            status = "ready"
        elif any(files.values()):
            status = "partial"
        else:
            status = "missing"
        return {"status": status, "files": files,
                "missing": [name for name, ok in files.items() if not ok],
                "root": self.static_root}

    def health(self) -> dict[str, Any]:
        frontend = self.frontend_status()
        return {
            "ok": True,
            "name": "AppGuard Web Gateway",
            "version": VERSION,
            "pid": os.getpid(),
            "uptime_s": int(time.time() - self._started_at),
            "daemon": bool(self.backend.probe()),
            "frontend": frontend["status"],
            "frontend_files": frontend["files"],
            "frontend_root": frontend["root"],
        }

    def static_file(self, relative: str) -> str | None:
        """把 URL 路径映射到 web/ 目录内；拒绝 ..、绝对路径、越界。"""
        root = os.path.realpath(self.static_root)
        candidate = os.path.realpath(os.path.join(root, relative.lstrip("/")))
        if candidate != root and not candidate.startswith(root + os.sep):
            return None
        return candidate if os.path.isfile(candidate) else None


def _make_handler(bridge: WebBridge):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "AppGuardWeb/0.4"

        # ---- 安全与响应 -------------------------------------------------- #
        def _security_headers(self, *, html: bool = False) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             HTML_CSP if html else
                             "default-src 'none'; frame-ancestors 'none'")

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._security_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass

        def _static(self, relative: str) -> bool:
            path = bridge.static_file(relative)
            if not path:
                return False
            ext = os.path.splitext(path)[1].lower()
            try:
                with open(path, "rb") as handle:
                    body = handle.read()
            except Exception:
                return False
            self.send_response(200)
            self.send_header("Content-Type",
                             CONTENT_TYPES.get(ext, "application/octet-stream"))
            self._security_headers(html=(ext == ".html"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass
            return True

        def _error(self, status: int, code: str, message: str,
                   detail: dict | None = None) -> None:
            # 提前拒绝 body/Content-Type 时，客户端可能还有未读 body；关闭连接避免串包。
            if status in (400, 401, 403, 405, 413, 415, 500):
                self.close_connection = True
            self._json(status, {"ok": False,
                                "error": {"code": code, "message": message,
                                          "detail": detail or {}}})

        # ---- 鉴权 / 同源 ------------------------------------------------ #
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            return host in {h.lower() for h in bridge._allowed_hosts()}

        def _origin_ok(self) -> bool:
            origin = (self.headers.get("Origin") or "").strip().lower()
            if origin and origin not in {o.lower() for o in bridge._allowed_origins()}:
                return False
            site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
            if site and site not in ("same-origin", "none"):
                return False
            return True

        def _token(self, *, allow_query: bool = False) -> str | None:
            auth = self.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                return auth.split(" ", 1)[1].strip()
            if allow_query:
                query = parse_qs(urlparse(self.path).query)
                values = query.get("token") or []
                if values:
                    return values[0]
            return None

        def _authorized(self, *, allow_query: bool = False) -> bool:
            token = self._token(allow_query=allow_query)
            return bool(token) and hmac.compare_digest(token, bridge.token)

        def _guard(self, *, allow_query: bool = False) -> bool:
            if not self._host_ok():
                self._error(403, ipc.E_PERMISSION, "Host 不在允许列表（只接受本机回环）")
                return False
            if not self._origin_ok():
                self._error(403, ipc.E_PERMISSION, "拒绝跨站来源")
                return False
            if not self._authorized(allow_query=allow_query):
                self._error(401, ipc.E_PERMISSION, "缺少或错误的访问令牌")
                return False
            return True

        # ---- GET -------------------------------------------------------- #
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/":
                frontend = bridge.frontend_status()
                if frontend["status"] == "ready" and self._static("index.html"):
                    return
                self._json(200, {
                    "ok": True,
                    "name": "AppGuard local API",
                    "version": VERSION,
                    "api": "/api/<method>",
                    "events": "/api/events",
                    "health": "/api/health",
                    "frontend": frontend["status"],
                    "missing": frontend["missing"],
                    "frontend_root": frontend["root"],
                })
                return
            if path == "/api/health":
                if not self._guard():
                    return
                self._json(200, bridge.health())
                return
            if path == "/api/events":
                if not self._guard(allow_query=True):
                    return
                self._sse()
                return
            if not path.startswith("/api/"):
                rel = "assets/appguard.ico" if path == "/favicon.ico" \
                    else path.lstrip("/")
                if self._static(rel):
                    return
            self._error(404, ipc.E_NOT_FOUND, "没有这个路径")

        def _drain(self, length: int) -> None:
            """拒绝请求前把 body 读掉，避免 Windows 上 close 触发 RST 让客户端看不到 4xx。"""
            try:
                remaining = min(max(0, length), 64 * 1024)
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 8192))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except Exception:
                pass

        # ---- POST ------------------------------------------------------- #
        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if not path.startswith("/api/") or path == "/api/":
                self._error(404, ipc.E_NOT_FOUND, "没有这个路径")
                return
            method = path[len("/api/"):]
            if method not in WEB_METHODS and method not in LOCAL_METHODS:
                self._error(404, ipc.E_UNKNOWN_METHOD, f"Web 网关不提供方法：{method}")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._error(400, ipc.E_BAD_REQUEST, "Content-Length 非法")
                return
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self._drain(length)
                self._error(415, ipc.E_BAD_REQUEST, "Content-Type 必须是 application/json")
                return
            if length > bridge.max_body:
                self._drain(length)
                self._error(413, ipc.E_BAD_REQUEST, "请求体过大")
                return
            try:
                raw = self.rfile.read(length) if length else b"{}"
            except Exception:
                self._error(400, ipc.E_BAD_REQUEST, "读取请求体失败")
                return
            if not self._guard():
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception as exc:                            # noqa: BLE001
                self._error(400, ipc.E_BAD_REQUEST, f"JSON 解析失败：{exc}")
                return
            if not isinstance(payload, dict):
                self._error(400, ipc.E_BAD_REQUEST, "请求体必须是 JSON 对象")
                return
            params = payload.get("params") if isinstance(payload.get("params"), dict) \
                else payload
            timeout_s = None
            if params.get("timeout_ms") is not None:
                try:
                    timeout_s = float(params["timeout_ms"]) / 1000.0
                except (TypeError, ValueError):
                    timeout_s = None
            try:
                if method in LOCAL_METHODS:
                    result = bridge.local_call(method, params)
                else:
                    result = bridge.backend.call(method, params, timeout_s=timeout_s)
                self._json(200, {"ok": True, "result": result})
            except LlmError as exc:
                self._error(_http_status(exc.code), exc.code, exc.message, exc.detail)
            except ipc.IpcError as exc:
                self._error(_http_status(exc.code), exc.code, exc.message, exc.detail)
            except Exception as exc:                            # noqa: BLE001
                log.exception("web api failure: %s", method)
                self._error(500, ipc.E_INTERNAL, f"{type(exc).__name__}: {exc}")

        def do_OPTIONS(self) -> None:  # noqa: N802
            # 不提供 CORS：跨站请求不应该到达这里。
            self._error(405, ipc.E_BAD_REQUEST, "不支持 OPTIONS（同源页面直接请求）")

        # ---- SSE -------------------------------------------------------- #
        def _sse(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            try:
                since = int((query.get("since_seq") or ["0"])[0])
            except ValueError:
                since = 0
            conn_id = f"web-{uuid.uuid4().hex}"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.end_headers()
            try:
                self.wfile.write("retry: 2000\n\n".encode())
                self.wfile.flush()
            except Exception:
                return
            last_ping = time.time()
            try:
                while True:
                    try:
                        batch = bridge.backend.subscribe(conn_id, since)
                    except Exception as exc:                    # noqa: BLE001
                        data = json.dumps({"message": str(exc)}, ensure_ascii=False)
                        self.wfile.write(
                            f"event: error\ndata: {data}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        break
                    for envelope in batch.get("events") or []:
                        seq = envelope.get("seq")
                        if isinstance(seq, int) and seq > since:
                            since = seq
                        data = json.dumps(envelope, ensure_ascii=False)
                        self.wfile.write(
                            f"event: {envelope.get('event', 'message')}\n"
                            f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    if time.time() - last_ping >= SSE_HEARTBEAT_SECONDS:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_ping = time.time()
                    time.sleep(SSE_POLL_SECONDS)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                bridge.backend.unsubscribe(conn_id)

        def log_message(self, fmt: str, *args) -> None:
            # self.path 可能带 SSE 的 ?token=，日志只记录路径，绝不记录 query。
            safe = urlparse(self.path).path
            log.debug("%s - %s %s", self.address_string(), self.command, safe)

    return Handler
