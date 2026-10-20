"""Web 网关契约测试：HTTP/JSON、token、Host/Origin、SSE、错误映射。

不启动真实守护：使用内存 FakeBackend，保证在受限环境也能跑。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from harness import check, eq, section, work_dir

from appguard import ipc, web


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.unsubscribed: list[str] = []

    def call(self, method, params, *, timeout_s=None):
        self.calls.append((method, params))
        flag = params.get("fail")
        if flag == "bad":
            raise ipc.IpcError(ipc.E_BAD_REQUEST, "坏参数")
        if flag == "denied":
            raise ipc.IpcError(ipc.E_PERMISSION, "没权限")
        if flag == "expired":
            raise ipc.IpcError(ipc.E_PLAN_EXPIRED, "计划过期")
        if method == "status.get":
            return {"health": "ok", "counts": {"software": 2, "processes": 9}}
        return {"method": method, "params": params, "timeout_s": timeout_s}

    def probe(self) -> bool:
        return True

    def subscribe(self, conn_id, since_seq):
        return {"subscribed": ["*"], "replayed": 1,
                "events": [{"event": "heartbeat", "seq": since_seq + 1,
                            "ts": "2026-10-18T00:00:00+08:00",
                            "data": {"ping": True}}]}

    def unsubscribe(self, conn_id):
        self.unsubscribed.append(conn_id)


def run() -> None:
    work = work_dir("web")
    endpoint = os.path.join(work, "web-endpoint.json")
    static_dir = os.path.join(work, "static")
    os.makedirs(os.path.join(static_dir, "assets"), exist_ok=True)
    with open(os.path.join(static_dir, "index.html"), "w", encoding="utf-8") as handle:
        handle.write("<!doctype html><title>AppGuard</title><div id=app>PAGE-OK</div>")
    with open(os.path.join(static_dir, "app.css"), "w", encoding="utf-8") as handle:
        handle.write("body{background:#1e1e21}")
    with open(os.path.join(static_dir, "app.js"), "w", encoding="utf-8") as handle:
        handle.write("console.log('appguard')")
    with open(os.path.join(static_dir, "assets", "logo.svg"), "w", encoding="utf-8") as handle:
        handle.write("<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    if os.path.exists(endpoint):
        os.remove(endpoint)
    fake = FakeBackend()
    bridge = web.WebBridge(fake, port=0, token="test-token",
                           endpoint_path=endpoint, max_body=1024,
                           static_root=static_dir)
    bridge.start()
    base = bridge.base_url
    token_headers = {"Authorization": "Bearer test-token",
                     "Origin": f"http://127.0.0.1:{bridge.port}",
                     "Content-Type": "application/json"}

    def request(method: str, path: str, body=None, headers=None):
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(base + path.lstrip("/"), data=data,
                                     method=method, headers=headers or {})
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    try:
        section("发现页与健康检查")
        status, body, headers = request("GET", "/")
        eq("GET / 200", status, 200)
        check("GET / 返回前端页面", b"PAGE-OK" in body)
        check("页面 Content-Type 是 HTML",
              headers.get("Content-Type", "").startswith("text/html"))
        check("页面 CSP 禁止内联脚本",
              "script-src 'self'" in headers.get("Content-Security-Policy", ""))
        status, body, headers = request("GET", "/assets/logo.svg")
        eq("静态资源 200", status, 200)
        check("静态资源 SVG MIME", headers.get("Content-Type", "").startswith("image/svg"))
        status, body, headers = request("GET", "/app.css")
        eq("样式表 200", status, 200)
        check("样式表 MIME", headers.get("Content-Type", "").startswith("text/css"))
        status, body, headers = request("GET", "/app.js")
        eq("脚本 200", status, 200)
        check("脚本 MIME", headers.get("Content-Type", "").startswith("application/javascript"))
        eq("静态目录穿越被拒绝", request("GET", "/%2e%2e/secret.txt")[0], 404)
        status, _body, _headers = request("GET", "/api/health")
        eq("无 token 401", status, 401)
        status, body, headers = request("GET", "/api/health", headers=token_headers)
        eq("带 token 200", status, 200)
        payload = json.loads(body)
        check("健康检查报告守护存活", payload.get("daemon") is True)
        eq("健康检查报告前端就绪", payload.get("frontend"), "ready")
        eq("安全头 X-Frame-Options", headers.get("X-Frame-Options"), "DENY")
        check("安全头 CSP", "frame-ancestors" in headers.get("Content-Security-Policy", ""))

        section("POST /api/<method> 与 token / Host / Origin")
        status, body, _headers = request("POST", "/api/status.get", {},
                                         {**token_headers, "Origin": "http://evil.test"})
        eq("错误 Origin 403", status, 403)
        status, _body, _headers = request("POST", "/api/status.get", {},
                                          {**token_headers, "Host": "evil.test"})
        eq("错误 Host 403", status, 403)
        status, _body, _headers = request("POST", "/api/status.get", {},
                                          {**token_headers,
                                           "Sec-Fetch-Site": "cross-site"})
        eq("跨站 sec-fetch-site 403", status, 403)
        status, body, _headers = request("POST", "/api/status.get",
                                         {"limit": 0}, token_headers)
        eq("正常调用 200", status, 200)
        result = json.loads(body).get("result") or {}
        eq("返回 status", result.get("health"), "ok")
        eq("参数原样透传", fake.calls[-1][1].get("limit"), 0)

        section("方法白名单 / 请求校验")
        eq("未知方法 404", request("POST", "/api/nope", {}, token_headers)[0], 404)
        eq("禁止 daemon.shutdown",
           request("POST", "/api/daemon.shutdown", {}, token_headers)[0], 404)
        eq("错误 Content-Type 415",
           request("POST", "/api/status.get", {}, {k: v for k, v in token_headers.items()
                                                   if k != "Content-Type"})[0], 415)
        big = {"x": "a" * 2000}
        eq("超大 body 413", request("POST", "/api/status.get", big, token_headers)[0], 413)

        section("IPC 错误 → HTTP 状态")
        eq("E_BAD_REQUEST → 400",
           request("POST", "/api/status.get", {"fail": "bad"}, token_headers)[0], 400)
        eq("E_PERMISSION → 403",
           request("POST", "/api/status.get", {"fail": "denied"}, token_headers)[0], 403)
        eq("E_PLAN_EXPIRED → 410",
           request("POST", "/api/status.get", {"fail": "expired"}, token_headers)[0], 410)

        section("SSE 事件流")
        sse_url = base + "api/events?token=test-token&since_seq=0"
        with urllib.request.urlopen(sse_url, timeout=5) as resp:
            check("SSE Content-Type",
                  resp.headers.get("Content-Type", "").startswith("text/event-stream"))
            first = resp.readline().decode("utf-8", "replace").strip()
            second = resp.readline().decode("utf-8", "replace").strip()
            third = resp.readline().decode("utf-8", "replace").strip()
            fourth = resp.readline().decode("utf-8", "replace").strip()
        eq("SSE retry", first, "retry: 2000")
        eq("SSE 空行分隔", second, "")
        eq("SSE event 行", third, "event: heartbeat")
        check("SSE data 行是 JSON", fourth.startswith("data: ") and
              json.loads(fourth[6:]).get("event") == "heartbeat")

        section("前端资源缺失时的降级")
        os.remove(os.path.join(static_dir, "app.js"))
        status, body, _headers = request("GET", "/api/health", headers=token_headers)
        eq("health 报告 partial", json.loads(body).get("frontend"), "partial")
        status, body, _headers = request("GET", "/")
        eq("资源缺失时 / 仍返回 200", status, 200)
        payload = json.loads(body)
        eq("回退为 API 发现 JSON", payload.get("frontend"), "partial")
        check("列出缺失文件", "app.js" in (payload.get("missing") or []))
        check("不返回半成品页面", b"PAGE-OK" not in body)

        section("端点文件")
        check("端点文件已写入", os.path.exists(endpoint))
        with open(endpoint, encoding="utf-8") as handle:
            data = json.load(handle)
        eq("端点端口正确", int(data.get("port")), bridge.port)
        eq("端点 token 正确", data.get("token"), "test-token")
        check("端点 URL 带 fragment token", "#token=" in data.get("url", ""))
    finally:
        bridge.stop()
        check("停止后端点文件被清理", not os.path.exists(endpoint))
