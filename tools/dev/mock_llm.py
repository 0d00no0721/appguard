#!/usr/bin/env python3
"""开发用 mock OpenAI-compatible 服务：返回固定解释，供 LLM UI/网关测试。"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self.server.last_request = body
        payload = {
            "model": "mock-llm",
            "choices": [{"message": {"content":
                "这是一个 mock 解释：\n1. 它是什么：测试更新器。\n"
                "2. 为什么运行：后台更新。\n3. 风险：低。\n4. 建议确认：可结束。"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 24, "total_tokens": 36},
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file", default="")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    port = int(server.server_address[1])
    if args.port_file:
        with open(args.port_file, "w", encoding="utf-8") as handle:
            handle.write(str(port))
    print("mock-llm on 127.0.0.1:%d" % port, flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
