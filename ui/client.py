"""AppGuard UI 的 IPC 客户端封装。

本模块是 UI 与后端之间的唯一接触点：只允许使用 `appguard.ipc` 暴露的
`make_client` / `IpcError` / `encode` / `decode`。任何特权操作都不在 UI 侧。

关于事件推送的两个契约偏差（2026-10-17 实测，详见 ui/README.md「已知限制」）：
1. 守护端只在「收到下一条请求」之前冲刷事件待发队列，客户端静默等待时
   事件不会主动到达；
2. 更严重：守护的 subscribe 处理器从「业务参数」里取 conn_id，而分发层
   从不把连接级 conn_id 注入参数 —— 事件实际挂到一个永远不存在的
   订阅者名下，管道/TCP 客户端都收不到任何实时推送。
因此本模块不依赖推送，改用「轮询回放」：每 4 秒对事件连接调一次
`subscribe {since_seq}`，响应里带回 seq 更大的全部缓冲事件（文档化语义），
随后立即 `unsubscribe`（避免守护端待发队列对不存在的订阅者无限增长）。
每次响应到达都刷新「最后消息时间」，15 秒无消息判定失联（契约 §2.6）。
"""
from __future__ import annotations

import os
import sys
import threading
import time

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.normpath(_SRC))

from appguard import ipc  # noqa: E402  契约允许的唯一入口

E_IPC = "E_IPC"  # 客户端侧伪错误码：守护不可达（契约附录 A 之外，仅 UI 内部使用）

CLIENT_NAME = "guardui/0.1.0"
PING_INTERVAL = 4.0          # 事件连接上的 ping 周期（兼作事件冲刷，见模块 docstring）
LOST_AFTER = 15.0            # 契约 §2.6：超过 15 秒无任何消息即判定守护失联


class GuardError(Exception):
    """UI 侧统一错误。分支判断一律用 .code，.message 可直接展示。"""

    def __init__(self, code: str, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def __str__(self) -> str:  # 便于直接弹窗
        return f"[{self.code}] {self.message}"


def _wrap_ipc_error(exc: Exception) -> GuardError:
    if isinstance(exc, GuardError):
        return exc
    if isinstance(exc, ipc.IpcError):
        return GuardError(exc.code, exc.message, exc.detail)
    return GuardError(E_IPC, f"守护不可达：{exc}")


class RpcClient:
    """普通 RPC 连接（单连接 + 锁，调用串行化）。"""

    def __init__(self, data_dir: str, *, timeout: float = 8.0) -> None:
        self.data_dir = data_dir
        self.timeout = timeout
        self._client = None
        self._lock = threading.Lock()
        self.hello: dict | None = None
        self.transport: str = "unknown"

    # ------------------------------------------------------------------ #
    def connect(self) -> dict:
        """建立连接并握手，返回 hello 结果。失败抛 GuardError。"""
        self.close()
        try:
            client = ipc.make_client(self.data_dir, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            raise _wrap_ipc_error(exc) from exc
        # make_client 内部已握手一次（以 guardctl 名义）；再以 UI 身份握手，
        # 让审计与能力发现都记录到正确的客户端名。
        try:
            hello = client.call("hello", api_version=1, client=CLIENT_NAME)
        except Exception as exc:  # noqa: BLE001
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
            raise _wrap_ipc_error(exc) from exc
        self._client = client
        self.hello = hello
        self.transport = "tcp" if type(client).__name__ == "TcpClient" else "pipe"
        return hello

    @property
    def connected(self) -> bool:
        return self._client is not None

    def call(self, method: str, **params) -> dict:
        """同步调用，线程安全。连接断开时自动重连一次。

        `timeout_ms` 是契约层的可选参数：这里同时用它调整**本次调用**的
        客户端读超时（清理大批量进程/停服务可能远超默认 8 秒）。
        """
        timeout_s = None
        if params.get("timeout_ms") is not None:
            try:
                timeout_s = max(1.0, float(params["timeout_ms"]) / 1000.0)
            except (TypeError, ValueError):
                timeout_s = None
        with self._lock:
            if self._client is None:
                self.connect()

            def _invoke() -> dict:
                if timeout_s is not None and hasattr(self._client, "timeout"):
                    self._client.timeout = timeout_s
                return self._client.call(method, **params)

            try:
                return _invoke()
            except Exception as exc:  # noqa: BLE001
                err = _wrap_ipc_error(exc)
                # 清理执行不是幂等操作：超时后不要盲目重试（可能已经杀完/停完，
                # 重试只会拿到 E_NOT_FOUND，还会让用户以为没执行）。
                if method == "cleanup.execute" and err.code == ipc.E_TIMEOUT:
                    raise err
                if err.code in (E_IPC, ipc.E_TIMEOUT):
                    # 守护可能重启过：重连一次再试
                    self.connect()
                    try:
                        return _invoke()
                    except Exception as exc2:  # noqa: BLE001
                        raise _wrap_ipc_error(exc2) from exc2
                raise err
            finally:
                if self._client is not None and hasattr(self._client, "timeout"):
                    self._client.timeout = self.timeout

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


class EventStream:
    """事件订阅连接：subscribe 回放 + 实时事件 + ping 保活/冲刷。

    on_event(envelope)  每个事件（含回放）回调一次，在后台线程里触发。
    on_dead(reason)     连接读失败时回调一次（之后由外部决定重连）。

    实现为「轮询回放」：subscribe(since_seq) 拿增量 → unsubscribe（防守护端
    队列泄漏）→ 睡 4 秒再循环。单连接、纯 call()、两条传输通道行为一致。
    """

    def __init__(self, data_dir: str, on_event, on_dead, *, timeout: float = 30.0) -> None:
        self.data_dir = data_dir
        self._on_event = on_event
        self._on_dead = on_dead
        self._timeout = timeout
        self.last_msg_at = 0.0
        self.last_seq = 0
        self._stop = threading.Event()
        self._client = None
        self._reader: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    def start(self, since_seq: int = 0) -> None:
        self.stop()
        self._stop.clear()
        self._reader = threading.Thread(target=self._run, args=(since_seq,),
                                        name="guardui-events", daemon=True)
        self._reader.start()

    def stop(self) -> None:
        self._stop.set()
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()  # 让阻塞中的 read 立即失败退出
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    def _run(self, since_seq: int) -> None:
        try:
            self.last_seq = max(self.last_seq, since_seq)
            fresh = self.last_seq == 0  # 全新连接：首轮回放只对齐序号，不投递
            client = ipc.make_client(self.data_dir, timeout=self._timeout)
            client.call("hello", api_version=1, client=CLIENT_NAME)
            self._client = client
            while not self._stop.is_set():
                result = client.call("subscribe", since_seq=self.last_seq)
                self.last_msg_at = time.time()  # 响应本身就是存活证据
                try:
                    client.call("unsubscribe")
                except Exception:  # noqa: BLE001  退订失败不影响主流程
                    pass
                for envelope in result.get("events") or []:
                    if fresh:
                        seq = envelope.get("seq")
                        if isinstance(seq, int) and seq > self.last_seq:
                            self.last_seq = seq
                    else:
                        self._track(envelope)
                fresh = False
                self._stop.wait(PING_INTERVAL)
        except Exception as exc:  # noqa: BLE001
            if not self._stop.is_set():
                self._on_dead(str(exc))

    def _track(self, message: dict) -> None:
        self.last_msg_at = time.time()
        seq = message.get("seq")
        if isinstance(seq, int) and seq > self.last_seq:
            self.last_seq = seq
        self._on_event(message)
