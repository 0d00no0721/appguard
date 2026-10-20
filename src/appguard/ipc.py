"""IPC 协议层：NDJSON 信封 + 方法分发 + 事件总线 + 命名管道/stdio 服务端 + 客户端。

协议细节见《接口规范.md》§2：一条消息 = 一行 JSON；有 `id` 是应答，无 `id` 且有 `event` 是推送。
事件与审计**共用同一个单调序号空间**（审计写的每一行也占一个号），这样一次清理可以用
`audit_seq_from/to` 精确定位到事件流与日志里的同一批记录。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Callable

import win32api
import win32con
import win32file
import win32pipe
import win32security

API_VERSION = 1
MAX_FRAME = 1024 * 1024
PIPE_NAME = r"\\.\pipe\AppGuard.v1"
ENDPOINT_FILENAME = "endpoint.json"

# --------------------------------------------------------------------------- #
# 错误码（与接口规范附录 A 一一对应）
# --------------------------------------------------------------------------- #
E_API_VERSION = "E_API_VERSION"
E_NO_HANDSHAKE = "E_NO_HANDSHAKE"
E_FRAME_TOO_LARGE = "E_FRAME_TOO_LARGE"
E_BAD_REQUEST = "E_BAD_REQUEST"
E_UNKNOWN_METHOD = "E_UNKNOWN_METHOD"
E_NOT_FOUND = "E_NOT_FOUND"
E_PERMISSION = "E_PERMISSION"
E_DEGRADED = "E_DEGRADED"
E_DRY_RUN = "E_DRY_RUN"
E_PLAN_EXPIRED = "E_PLAN_EXPIRED"
E_PLAN_STALE = "E_PLAN_STALE"
E_PROTECTED = "E_PROTECTED"
E_BUSY = "E_BUSY"
E_TIMEOUT = "E_TIMEOUT"
E_KILL_DENIED = "E_KILL_DENIED"
E_SERVICE_FAILED = "E_SERVICE_FAILED"
E_INTERNAL = "E_INTERNAL"


class IpcError(Exception):
    def __init__(self, code: str, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def to_error(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


# --------------------------------------------------------------------------- #
# 信封
# --------------------------------------------------------------------------- #
def response_ok(request_id: Any, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": request_id, "ok": True, "result": result or {}}


def response_err(request_id: Any, error: IpcError | dict[str, Any]) -> dict[str, Any]:
    payload = error.to_error() if isinstance(error, IpcError) else error
    return {"id": request_id, "ok": False, "error": payload}


def event_envelope(name: str, seq: int, data: dict[str, Any]) -> dict[str, Any]:
    return {"event": name, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seq": seq, "data": data}


def encode(message: dict[str, Any]) -> bytes:
    return (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")


def decode(line: bytes | str) -> dict[str, Any]:
    text = line.decode("utf-8", "replace") if isinstance(line, bytes) else line
    text = text.strip()
    if not text:
        raise IpcError(E_BAD_REQUEST, "空消息")
    if len(text) > MAX_FRAME:
        raise IpcError(E_FRAME_TOO_LARGE, "消息超过 1 MiB 上限")
    try:
        message = json.loads(text)
    except Exception as exc:                                    # noqa: BLE001
        raise IpcError(E_BAD_REQUEST, f"JSON 解析失败：{exc}") from exc
    if not isinstance(message, dict):
        raise IpcError(E_BAD_REQUEST, "消息必须是 JSON 对象")
    return message


# --------------------------------------------------------------------------- #
# 事件总线
# --------------------------------------------------------------------------- #
class EventBus:
    """事件发布 + 环形缓冲回放（接口规范 §2.6）。"""

    def __init__(self, seq_source: Callable[[], int], capacity: int = 1000) -> None:
        self._next_seq = seq_source
        self._capacity = capacity
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._subscribers: dict[str, set[str]] = {}      # conn_id → {event|"*"}

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._buffer[-1]["seq"] if self._buffer else 0

    def publish(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        envelope = event_envelope(name, self._next_seq(), data)
        with self._lock:
            self._buffer.append(envelope)
            if len(self._buffer) > self._capacity:
                del self._buffer[: len(self._buffer) - self._capacity]
            targets = [conn for conn, events in self._subscribers.items()
                       if "*" in events or name in events]
        for conn in targets:
            self._deliver(conn, envelope)
        return envelope

    def _deliver(self, conn_id: str, envelope: dict[str, Any]) -> None:
        """默认实现：把事件挂到该连接的待发队列；由服务端循环取走。"""

    def subscribe(self, conn_id: str, events: list[str] | None = None,
                  since_seq: int | None = None) -> dict[str, Any]:
        with self._lock:
            self._subscribers.setdefault(conn_id, set()).update(events or ["*"])
            replay = []
            if since_seq is not None:
                wanted = set(events or ["*"])
                replay = [e for e in self._buffer
                          if e["seq"] > since_seq and ("*" in wanted or e["event"] in wanted)]
        return {"subscribed": sorted(self._subscribers.get(conn_id, set())),
                "replayed": len(replay), "events": replay}

    def unsubscribe(self, conn_id: str, events: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            if not events:
                self._subscribers.pop(conn_id, None)
            else:
                current = self._subscribers.get(conn_id)
                if current:
                    current.difference_update(events)
        return {"subscribed": sorted(self._subscribers.get(conn_id, set()))}

    def drop(self, conn_id: str) -> None:
        with self._lock:
            self._subscribers.pop(conn_id, None)


# --------------------------------------------------------------------------- #
# 方法分发
# --------------------------------------------------------------------------- #
class Dispatcher:
    def __init__(self) -> None:
        self._methods: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self.features: list[str] = []

    def method(self, name: str):
        def wrapper(func):
            self._methods[name] = func
            return func
        return wrapper

    def register(self, name: str, func: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._methods[name] = func

    @property
    def method_names(self) -> list[str]:
        return sorted(self._methods)

    def dispatch(self, request: dict[str, Any], *, conn_id: str = "local") -> dict[str, Any]:
        request_id = request.get("id")
        method = request.get("method")
        params = dict(request.get("params") or {})
        # 连接级注入：`subscribe`/`unsubscribe` 必须知道"事件挂到哪条连接上"。
        # ⚠️ 早期版本没注入 ⇒ 订阅被登记到不存在的 "stdio" 名下 ⇒ **事件永远推不到客户端**。
        # 2026-10-17 由 UI 侧实测暴露（订阅后静默 12 秒收到 0 条推送，而守护每 5 秒都在发 heartbeat），
        # 后端复现确认后修复。
        # **服务端权威**：客户端传什么都不算，一律以真实连接 id 覆盖（防止替别的连接订阅）。
        params["conn_id"] = conn_id
        if not isinstance(method, str) or not method:
            return response_err(request_id, IpcError(E_BAD_REQUEST, "缺少 method"))
        handler = self._methods.get(method)
        if handler is None:
            return response_err(request_id, IpcError(
                E_UNKNOWN_METHOD, f"未知方法 {method}", {"supported": self.method_names}))
        try:
            result = handler(params)
            return response_ok(request_id, result if isinstance(result, dict) else {"value": result})
        except IpcError as exc:
            return response_err(request_id, exc)
        except Exception as exc:                                # noqa: BLE001
            return response_err(request_id, IpcError(
                E_INTERNAL, f"{type(exc).__name__}: {exc}", {"method": method}))


# --------------------------------------------------------------------------- #
# 服务端：stdio
# --------------------------------------------------------------------------- #
def serve_stdio(dispatcher: Dispatcher, *, conn_id: str = "stdio",
                stdin=None, stdout=None) -> None:
    """在 stdin/stdout 上跑协议（开发/测试用）。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    while True:
        line = stdin.readline()
        if not line:
            break
        try:
            request = decode(line)
        except IpcError as exc:
            stdout.write(json.dumps(response_err(None, exc), ensure_ascii=False) + "\n")
            stdout.flush()
            continue
        response = dispatcher.dispatch(request, conn_id=conn_id)
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()


# --------------------------------------------------------------------------- #
# 服务端：命名管道
# --------------------------------------------------------------------------- #
def _sddl_attributes(sddl: str):
    try:
        descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            sddl, win32security.SDDL_REVISION_1)
        attributes = win32security.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        return attributes
    except Exception:
        return None


def pipe_attribute_candidates() -> list[tuple[str, Any]]:
    """按优先级给出候选安全描述符。

    ⚠️ 实测：带**强制标签**（`S:(ML;;NW;;;ME)`）的 SDDL 需要 `SeSecurityPrivilege`，
    非管理员进程调用 `CreateNamedPipe` 会直接失败（Win32 1314「客户端没有所需的特权」）。
    因此必须降级：带标签 → 只有 DACL → 默认。
    """
    candidates: list[tuple[str, Any]] = []
    try:
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                               win32con.TOKEN_QUERY)
        sid, _attrs = win32security.GetTokenInformation(token, win32security.TokenUser)
        user = win32security.ConvertSidToStringSid(sid)
    except Exception:
        return [("默认安全描述符", None)]
    labeled = _sddl_attributes(f"D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;{user})S:(ML;;NW;;;ME)")
    if labeled is not None:
        candidates.append(("含强制标签（中等完整性客户端可连）", labeled))
    plain = _sddl_attributes(f"D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;{user})")
    if plain is not None:
        candidates.append(("仅 DACL（未持有 SeSecurityPrivilege 时的降级）", plain))
    candidates.append(("默认安全描述符", None))
    return candidates


class PipeServer:
    """命名管道服务端：每连接一线程，NDJSON 双向。"""
    def __init__(self, dispatcher: Dispatcher, bus: EventBus, *,
                 pipe_name: str = PIPE_NAME) -> None:
        self.dispatcher = dispatcher
        self.bus = bus
        self.pipe_name = pipe_name
        self._stop = threading.Event()
        self._outbox: dict[str, list[dict[str, Any]]] = {}
        self._outbox_lock = threading.RLock()
        self._wakes: dict[str, threading.Event] = {}          # conn_id → 写线程唤醒事件
        self.ready = threading.Event()
        self.last_error: str | None = None
        self.security_note: str | None = None
        self._attributes: list[tuple[str, Any]] | None = None
        bus._deliver = self._deliver          # 事件投递到各连接的待发队列

    # ---------------------------------------------------------------- #
    def _deliver(self, conn_id: str, envelope: dict[str, Any]) -> None:
        with self._outbox_lock:
            self._outbox.setdefault(conn_id, []).append(envelope)
        wake = self._wakes.get(conn_id)
        if wake is not None:
            wake.set()                     # 唤醒该连接的写线程，立刻把事件推出去

    def _take(self, conn_id: str) -> list[dict[str, Any]]:
        with self._outbox_lock:
            pending = self._outbox.get(conn_id) or []
            self._outbox[conn_id] = []
            return pending

    # ---------------------------------------------------------------- #
    def serve_forever(self) -> None:
        if self._attributes is None:
            self._attributes = pipe_attribute_candidates()
        attempts = 0
        while not self._stop.is_set():
            handle, note = self._create_pipe()
            if handle is None:
                attempts += 1
                # 不再静默：第一次失败就写清楚原因（含每个候选的报错）
                if attempts == 1:
                    message = f"命名管道创建失败（{self.pipe_name}）：{self.last_error}"
                    print(f"[AppGuard] {message}", file=sys.stderr, flush=True)
                self._stop.wait(min(5.0, 0.5 * attempts))
                continue
            attempts = 0
            self.security_note = note
            self.ready.set()
            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except Exception as exc:                            # noqa: BLE001
                self.last_error = f"ConnectNamedPipe 失败：{exc}"
                try:
                    win32file.CloseHandle(handle)
                except Exception:
                    pass
                continue
            thread = threading.Thread(target=self._serve_client, args=(handle,), daemon=True)
            thread.start()
            # 不再保存线程引用：`_threads` 只增不减会随连接数缓慢泄漏（线程本身是 daemon，
            # 退出时随进程结束）。需要观测时看 `ready` / `last_error` 即可。

    def _create_pipe(self):
        """依次尝试候选安全描述符，返回 (handle, note)。"""
        for note, attributes in self._attributes or []:
            try:
                handle = win32pipe.CreateNamedPipe(
                    self.pipe_name,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    MAX_FRAME, MAX_FRAME, 0, attributes)
                return handle, note
            except Exception as exc:                            # noqa: BLE001
                self.last_error = f"[{note}] {exc}"
                continue
        return None, None

    def stop(self) -> None:
        self._stop.set()

    def _serve_client(self, handle) -> None:
        """一条连接两个线程：**写线程**负责异步推事件，**读线程**负责处理请求并回应。

        ⚠️ 早期版本只有一个线程：先冲 outbox、再阻塞 `ReadFile` ⇒ 客户端一闲下来就再也收不到
        事件（必须"先发一个请求"才会顺带收到）。这正是"订阅形同虚设"的另一半原因。
        """
        conn_id = f"pipe-{threading.get_ident()}"
        wake = threading.Event()
        write_lock = threading.Lock()
        stop_writer = threading.Event()
        self._wakes[conn_id] = wake

        def _write(payload: bytes) -> bool:
            with write_lock:
                try:
                    win32file.WriteFile(handle, payload)
                    return True
                except Exception:                               # noqa: BLE001
                    return False

        def writer() -> None:
            while not stop_writer.is_set():
                wake.wait(0.5)
                wake.clear()
                for envelope in self._take(conn_id):
                    if not _write(encode(envelope)):
                        return
                if self._stop.is_set():
                    return

        writer_thread = threading.Thread(target=writer, daemon=True)
        writer_thread.start()
        try:
            buffer = b""
            while not self._stop.is_set():
                # ⚠️ **必须用 PeekNamedPipe 轮询，不能用阻塞 ReadFile**：
                # Windows 的**同步**管道句柄同一时刻只容忍一个未完成 I/O —— 读线程一旦阻塞在
                # ReadFile 上，写线程的 WriteFile 就会被卡住直到那次读完成，
                # 于是"事件推送"永远发不出去（2026-10-17 实测：订阅后 12 秒 0 条推送）。
                try:
                    _err, available, _left = win32pipe.PeekNamedPipe(handle, 0)
                except Exception:                               # noqa: BLE001
                    return
                if available <= 0:
                    time.sleep(0.03)                            # 30 ms 轮询：把句柄让给写线程
                    continue
                try:
                    _rc, data = win32file.ReadFile(handle, min(available, 65536))
                except Exception:                               # noqa: BLE001
                    return
                if not data:
                    return
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        request = decode(line)
                    except IpcError as exc:
                        if not _write(encode(response_err(None, exc))):
                            return
                        continue
                    response = self.dispatcher.dispatch(request, conn_id=conn_id)
                    if not _write(encode(response)):
                        return
        finally:
            stop_writer.set()
            wake.set()
            self._wakes.pop(conn_id, None)
            self.bus.drop(conn_id)
            try:
                win32pipe.DisconnectNamedPipe(handle)
            except Exception:                                   # noqa: BLE001
                pass
            try:
                win32file.CloseHandle(handle)
            except Exception:                                   # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class IpcClient:
    """命名管道客户端（CLI/UI 用）。"""

    def __init__(self, pipe_name: str = PIPE_NAME, *, timeout: float = 8.0) -> None:
        self.pipe_name = pipe_name
        self.timeout = timeout
        self._handle = None
        self._next_id = 0

    def connect(self) -> "IpcClient":
        deadline = time.time() + self.timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                self._handle = win32file.CreateFile(
                    self.pipe_name, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0, None, win32file.OPEN_EXISTING, 0, None)
                return self
            except Exception as exc:                            # noqa: BLE001
                last_error = exc
                time.sleep(0.1)
        raise IpcError(E_TIMEOUT, f"连接守护失败：{self.pipe_name}（{last_error}）")

    def close(self) -> None:
        if self._handle:
            try:
                win32file.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None

    def call(self, method: str, **params) -> dict[str, Any]:
        if not self._handle:
            self.connect()
        self._next_id += 1
        request_id = self._next_id
        win32file.WriteFile(self._handle, encode(
            {"id": request_id, "method": method, "params": params}))
        deadline = time.time() + self.timeout
        buffer = b""
        while time.time() < deadline:
            try:
                _rc, data = win32file.ReadFile(self._handle, 65536)
            except Exception as exc:                            # noqa: BLE001
                raise IpcError(E_TIMEOUT, f"读取响应失败：{exc}") from exc
            if not data:
                raise IpcError(E_TIMEOUT, "守护关闭了连接")
            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                message = decode(line)
                if message.get("id") != request_id:               # 事件或别的应答
                    continue
                if message.get("ok"):
                    return message.get("result") or {}
                raise IpcError(**{k: message["error"].get(k) for k in ("code", "message")},
                               detail=message["error"].get("detail"))
        raise IpcError(E_TIMEOUT, f"{method} 超时")


# --------------------------------------------------------------------------- #
# 端点发现（TCP 回退通道）
# --------------------------------------------------------------------------- #
def write_endpoint(data_dir: str, payload: dict[str, Any]) -> str:
    path = os.path.join(data_dir, ENDPOINT_FILENAME)
    os.makedirs(data_dir, exist_ok=True)
    payload = dict(payload)
    payload["pid"] = os.getpid()
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def read_endpoint(data_dir: str) -> dict[str, Any] | None:
    try:
        with open(os.path.join(data_dir, ENDPOINT_FILENAME), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def clear_endpoint(data_dir: str) -> None:
    try:
        os.remove(os.path.join(data_dir, ENDPOINT_FILENAME))
    except Exception:
        pass


def probe_pipe(pipe_name: str = PIPE_NAME, timeout: float = 1.5) -> tuple[bool, str | None]:
    """自检：能否以客户端身份连上刚建好的管道。

    ⚠️ 实测：受限环境（DSH 沙箱、部分 EDR）**允许创建管道却拒绝客户端连接**
    （`CreateFile → 拒绝访问(5)`，即使同一进程、同一用户）。这个探针既能在生产上
    提前发现安全描述符写错，也能驱动自动降级到回环 TCP。
    """
    deadline = time.time() + timeout
    last: str | None = None
    while time.time() < deadline:
        try:
            handle = win32file.CreateFile(
                pipe_name, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None, win32file.OPEN_EXISTING, 0, None)
            win32file.CloseHandle(handle)
            return True, None
        except Exception as exc:                                # noqa: BLE001
            last = str(exc)
            time.sleep(0.1)
    return False, last


# --------------------------------------------------------------------------- #
# 服务端：回环 TCP（管道不可用时的等价通道，见接口规范 §2.3）
# --------------------------------------------------------------------------- #
import secrets                                              # noqa: E402
import socketserver                                         # noqa: E402


class TcpServer:
    """127.0.0.1 上的 NDJSON 服务端；端口随机、令牌鉴权、仅本机。"""

    def __init__(self, dispatcher: Dispatcher, bus: EventBus, *,
                 host: str = "127.0.0.1", port: int = 0, token: str | None = None) -> None:
        self.dispatcher = dispatcher
        self.bus = bus
        self.host = host
        self.port = port
        self.token = token or secrets.token_hex(16)
        self.ready = threading.Event()
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._outbox: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.RLock()
        self._server: Any = None
        self._authorized: set[str] = set()          # 跨线程读写，统一在 _lock 下操作
        self._wakes: dict[str, threading.Event] = {}          # conn_id → 写线程唤醒事件
        bus._deliver = self._deliver

    def _is_authorized(self, conn_id: str) -> bool:
        with self._lock:
            return conn_id in self._authorized

    def _authorize(self, conn_id: str, allowed: bool = True) -> None:
        with self._lock:
            if allowed:
                self._authorized.add(conn_id)
            else:
                self._authorized.discard(conn_id)

    def _deliver(self, conn_id: str, envelope: dict[str, Any]) -> None:
        with self._lock:
            self._outbox.setdefault(conn_id, []).append(envelope)
        wake = self._wakes.get(conn_id)
        if wake is not None:
            wake.set()                     # 唤醒该连接的写线程，立刻把事件推出去

    def _take(self, conn_id: str) -> list[dict[str, Any]]:
        with self._lock:
            pending = self._outbox.get(conn_id) or []
            self._outbox[conn_id] = []
            return pending

    def endpoint(self) -> dict[str, Any]:
        return {"transport": "tcp", "host": self.host, "port": self.port, "token": self.token}

    def serve_forever(self) -> None:
        server = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self_inner) -> None:                     # noqa: N805
                """一条连接两个线程：写线程异步推事件，读线程处理请求并回应。"""
                conn_id = f"tcp-{self_inner.client_address[1]}-{threading.get_ident()}"
                wake = threading.Event()
                write_lock = threading.Lock()
                stop_writer = threading.Event()
                server._wakes[conn_id] = wake

                def _write(payload: bytes) -> bool:
                    with write_lock:
                        try:
                            self_inner.wfile.write(payload)
                            self_inner.wfile.flush()
                            return True
                        except Exception:                       # noqa: BLE001
                            return False

                def writer() -> None:
                    while not stop_writer.is_set():
                        wake.wait(0.5)
                        wake.clear()
                        for envelope in server._take(conn_id):
                            if not _write(encode(envelope)):
                                return
                        if server._stop.is_set():
                            return

                threading.Thread(target=writer, daemon=True).start()
                try:
                    while not server._stop.is_set():
                        line = self_inner.rfile.readline(MAX_FRAME + 1)
                        if not line:
                            return
                        try:
                            request = decode(line)
                        except IpcError as exc:
                            if not _write(encode(response_err(None, exc))):
                                return
                            continue
                        method = request.get("method")
                        params = request.get("params") or {}
                        if method == "hello":
                            if server.token and params.get("token") != server.token:
                                _write(encode(response_err(
                                    request.get("id"), IpcError(E_PERMISSION,
                                                                "令牌不匹配（见 endpoint.json）"))))
                                return
                            server._authorize(conn_id)
                        elif not server._is_authorized(conn_id):
                            _write(encode(response_err(
                                request.get("id"), IpcError(E_NO_HANDSHAKE, "请先发送 hello"))))
                            continue
                        response = server.dispatcher.dispatch(request, conn_id=conn_id)
                        if not _write(encode(response)):
                            return
                except Exception:                               # noqa: BLE001
                    return
                finally:
                    stop_writer.set()
                    wake.set()
                    server._wakes.pop(conn_id, None)
                    server._authorize(conn_id, allowed=False)
                    server.bus.drop(conn_id)

        try:
            self._server = socketserver.ThreadingTCPServer(
                (self.host, self.port), Handler, bind_and_activate=False)
            self._server.allow_reuse_address = True
            self._server.daemon_threads = True
            self._server.server_bind()
            self._server.server_activate()
            self.port = int(self._server.server_address[1])
            self.ready.set()
            self._server.serve_forever(poll_interval=0.3)
        except Exception as exc:                                # noqa: BLE001
            self.last_error = str(exc)
            print(f"[AppGuard] TCP 服务端启动失败：{exc}", file=sys.stderr, flush=True)

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._server:
                self._server.shutdown()
                self._server.server_close()
        except Exception:
            pass


class TcpClient:
    """回环 TCP 客户端（与 IpcClient 同接口）。"""

    def __init__(self, host: str, port: int, token: str | None = None,
                 *, timeout: float = 8.0) -> None:
        self.host, self.port, self.token, self.timeout = host, port, token, timeout
        self._sock = None
        self._file = None
        self._next_id = 0

    def connect(self) -> "TcpClient":
        import socket
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.settimeout(self.timeout)
        self._file = self._sock.makefile("rwb")
        return self

    def handshake(self, client: str = "guardctl/0.1.0") -> dict[str, Any]:
        return self.call("hello", api_version=API_VERSION, client=client, token=self.token)

    def close(self) -> None:
        for closer in (self._file, self._sock):
            try:
                if closer:
                    closer.close()
            except Exception:
                pass

    def call(self, method: str, **params) -> dict[str, Any]:
        if not self._sock:
            self.connect()
        if self.token is not None and "token" not in params and method == "hello":
            params["token"] = self.token
        self._next_id += 1
        request_id = self._next_id
        self._file.write(encode({"id": request_id, "method": method, "params": params}))
        self._file.flush()
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            line = self._file.readline()
            if not line:
                raise IpcError(E_TIMEOUT, "守护关闭了连接")
            message = decode(line)
            if message.get("id") != request_id:
                continue
            if message.get("ok"):
                return message.get("result") or {}
            error = message.get("error") or {}
            raise IpcError(error.get("code") or E_INTERNAL,
                           error.get("message") or "未知错误", error.get("detail"))
        raise IpcError(E_TIMEOUT, f"{method} 超时")


def make_client(data_dir: str | None = None, pipe_name: str = PIPE_NAME,
                *, timeout: float = 8.0, prefer: str = "auto"):
    """按 `endpoint.json` / 管道顺序建立连接，返回带 `.call()` 的客户端。"""
    if prefer in ("auto", "tcp") and data_dir:
        endpoint = read_endpoint(data_dir)
        if endpoint and endpoint.get("transport") == "tcp":
            client = TcpClient(endpoint.get("host", "127.0.0.1"), int(endpoint["port"]),
                               endpoint.get("token"), timeout=timeout)
            try:
                client.connect()
                client.handshake()
                return client
            except Exception:
                client.close()
                if prefer == "tcp":
                    raise
    client = IpcClient(pipe_name, timeout=timeout)
    client.connect()
    client.call("hello", api_version=API_VERSION, client="guardctl/0.1.0")
    return client
