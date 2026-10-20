"""IPC 协议契约测试：信封 / 错误码 / 分发 / 版本协商 / 令牌 / 事件回放。"""

from __future__ import annotations

import itertools
import threading

from harness import check, eq, section, work_dir

from appguard import ipc
from appguard.config import load_config
from appguard.daemon import Daemon
from appguard.models import Policy


def run() -> None:
    section("信封与分帧")
    eq("成功信封", ipc.response_ok(7, {"a": 1}), {"id": 7, "ok": True, "result": {"a": 1}})
    err = ipc.response_err(8, ipc.IpcError(ipc.E_NOT_FOUND, "没有"))
    eq("失败信封 code", err["error"]["code"], ipc.E_NOT_FOUND)
    ev = ipc.event_envelope("heartbeat", 5, {"x": 1})
    check("事件信封无 id、有 seq", "id" not in ev and ev["seq"] == 5)
    eq("编码是一行 JSON", ipc.encode({"a": 1})[-1:], b"\n")
    eq("解码往返", ipc.decode(ipc.encode({"方法": "值"})), {"方法": "值"})
    for bad, code in ((b"", ipc.E_BAD_REQUEST), (b"not-json", ipc.E_BAD_REQUEST),
                      (b"[1,2]", ipc.E_BAD_REQUEST)):
        try:
            ipc.decode(bad)
            check(f"非法输入应被拒：{bad!r}", False, "没有抛错")
        except ipc.IpcError as exc:
            eq(f"非法输入错误码：{bad!r}", exc.code, code)

    section("方法分发")
    dispatcher = ipc.Dispatcher()
    dispatcher.register("ping", lambda p: {"pong": True})

    def _boom(_params):
        raise ValueError("内部炸了")

    dispatcher.register("boom", _boom)
    eq("未知方法", dispatcher.dispatch({"id": 1, "method": "nope"})["error"]["code"],
       ipc.E_UNKNOWN_METHOD)
    eq("缺 method", dispatcher.dispatch({"id": 2})["error"]["code"], ipc.E_BAD_REQUEST)
    eq("普通异常 → E_INTERNAL",
       dispatcher.dispatch({"id": 3, "method": "boom"})["error"]["code"], ipc.E_INTERNAL)
    ok = dispatcher.dispatch({"id": 4, "method": "ping"})
    check("正常调用回原 id", ok["id"] == 4 and ok["result"]["pong"] is True)

    section("守护：版本协商与状态")
    tmp = work_dir("ipc-config")
    cfg = load_config(data_dir=tmp)
    daemon = Daemon(cfg)
    daemon.prepare()
    bad = daemon.dispatcher.dispatch({"id": 1, "method": "hello",
                                      "params": {"api_version": 99}})
    eq("版本不匹配 → E_API_VERSION", bad["error"]["code"], ipc.E_API_VERSION)
    good = daemon.dispatcher.dispatch({"id": 2, "method": "hello",
                                       "params": {"api_version": 1}})
    check("版本匹配握手成功", good["ok"] and good["result"]["api_version"] == 1)
    check("握手返回能力清单", "features" in good["result"]
          and "capabilities" in good["result"])
    policy = daemon.dispatcher.dispatch({"id": 3, "method": "policy.set",
                                         "params": {"dry_run": False}})
    check("写方法可用（非管理员进程内直跑）",
          policy["ok"] and policy["result"]["dry_run"] is False)
    status = daemon.dispatcher.dispatch({"id": 4, "method": "status.get"})
    check("status.get 可调用", status["ok"] and "counts" in status["result"])
    check("status.get 暴露 event_seq（SSE 初始游标）",
          isinstance(status["result"].get("event_seq"), int))

    section("事件总线：回放与退订")
    counter = itertools.count(1)                  # 生产环境的审计序号从 1 开始，测试保持一致
    bus = ipc.EventBus(lambda: next(counter))
    bus.publish("a", {"n": 1})
    bus.publish("b", {"n": 2})
    bus.publish("a", {"n": 3})
    replay = bus.subscribe("c1", events=["a"], since_seq=0)
    eq("按事件名过滤回放", replay["replayed"], 2)
    eq("回放顺序正确", [e["data"]["n"] for e in replay["events"]], [1, 3])
    tail = bus.subscribe("c2", since_seq=2)
    eq("按序号回放", [e["seq"] for e in tail["events"]], [3])
    eq("退订", bus.unsubscribe("c1")["subscribed"], [])

    section("回环 TCP 通道与令牌")
    tcp_dispatcher = ipc.Dispatcher()
    tcp_dispatcher.register("hello", lambda p: {"api_version": 1, "server": "test"})
    tcp_dispatcher.register("ping", lambda p: {"pong": True})
    server = ipc.TcpServer(tcp_dispatcher, ipc.EventBus(lambda: 1))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    check("TCP 服务端就绪", server.ready.wait(3.0), server.last_error or "")

    good_client = ipc.TcpClient("127.0.0.1", server.port, server.token).connect()
    eq("正确令牌握手", good_client.handshake()["api_version"], 1)
    eq("握手后可调用", good_client.call("ping")["pong"], True)
    good_client.close()

    bad_client = ipc.TcpClient("127.0.0.1", server.port, "wrong-token").connect()
    try:
        bad_client.handshake()
        check("错误令牌应被拒", False, "握手居然成功了")
    except ipc.IpcError as exc:
        eq("错误令牌 → E_PERMISSION", exc.code, ipc.E_PERMISSION)
    bad_client.close()

    no_handshake = ipc.TcpClient("127.0.0.1", server.port, server.token).connect()
    try:
        no_handshake.call("ping")
        check("未握手调用应被拒", False, "居然成功了")
    except ipc.IpcError as exc:
        eq("未握手 → E_NO_HANDSHAKE", exc.code, ipc.E_NO_HANDSHAKE)
    no_handshake.close()
    server.stop()

    section("异步推送：订阅后**不发请求**也应收到事件")
    push_bus = ipc.EventBus(lambda: next(counter))
    push_dispatcher = ipc.Dispatcher()
    push_dispatcher.register("hello", lambda p: {"api_version": 1})
    # 注意：替身必须真的在事件总线上注册，否则测的是"没人订阅"而不是"推不出去"
    push_dispatcher.register("subscribe", lambda p: dict(
        push_bus.subscribe(p["conn_id"], events=p.get("events"), since_seq=p.get("since_seq")),
        conn_id=p.get("conn_id")))
    push_server = ipc.TcpServer(push_dispatcher, push_bus)
    threading.Thread(target=push_server.serve_forever, daemon=True).start()
    check("推送用 TCP 服务端就绪", push_server.ready.wait(3.0), push_server.last_error or "")

    subscriber = ipc.TcpClient("127.0.0.1", push_server.port, push_server.token).connect()
    subscriber.handshake()
    subscribed = subscriber.call("subscribe", events=["snapshot.updated"], since_seq=0)
    check("连接 id 已注入且不是 stdio",
          str(subscribed.get("conn_id", "")).startswith("tcp-"), str(subscribed))
    check("客户端伪造 conn_id 无效（服务端权威）",
          str(subscriber.call("subscribe", events=["x"], conn_id="fake-id").get("conn_id"))
          .startswith("tcp-"))

    push_bus.publish("snapshot.updated", {"n": 1})
    subscriber._sock.settimeout(4.0)                 # 只读，不发任何请求
    line = subscriber._file.readline()
    envelope = ipc.decode(line) if line else {}
    eq("静默等待也能收到异步事件", envelope.get("event"), "snapshot.updated")
    eq("事件内容完整", (envelope.get("data") or {}).get("n"), 1)
    subscriber.close()
    push_server.stop()

    section("端点发现（endpoint.json）")
    tmp = work_dir("ipc-endpoint")
    ipc.write_endpoint(tmp, {"transport": "tcp", "host": "127.0.0.1", "port": 1, "token": "t"})
    endpoint = ipc.read_endpoint(tmp)
    eq("端点可读", endpoint["transport"], "tcp")
    check("端点带 pid 与更新时间", "pid" in endpoint and "updated_at" in endpoint)
    ipc.clear_endpoint(tmp)
    eq("端点可清除", ipc.read_endpoint(tmp), None)

    section("配置默认值")
    policy = Policy()
    eq("默认 dry_run 打开", policy.dry_run, True)
    check("默认只保护系统角色（服务由微软/关键进程规则处理）",
          set(policy.never_touch_roles) == {"system"})
    eq("默认时间窗 120 秒", policy.min_idle_s, 120)
    check("默认不允许用户离开时清理", policy.allow_cleanup_while_idle is False)
