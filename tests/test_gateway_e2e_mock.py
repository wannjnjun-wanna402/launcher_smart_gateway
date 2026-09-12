#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端模拟测试 (E2E Mock Testing)：
验证通过 HTTP 请求调用 qwen_tool_proxy.py 的 /v1/chat/completions 和 /v1/messages，
检查转发到后端的真实 payload 是否完整携带了自适应动态采样与思考参数。
"""

import os
import sys
import json
import time
import socket
import threading
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# 捕获后端接收到的真实 payload
captured_backend_requests = []

class MockLlamaServerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        payload = json.loads(body.decode("utf-8")) if body else {}
        captured_backend_requests.append(payload)

        # 返回符合 OpenAI 规范的响应
        resp_data = {
            "id": "chatcmpl-mock-123",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": payload.get("model", "mock-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Mock response from backend",
                        "reasoning_content": "Mock reasoning"
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "total_tokens": 70
            }
        }
        resp_bytes = json.dumps(resp_data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def do_GET(self):
        # 兼容健康检查 /props 或 /health
        resp_data = {"status": "ok", "default_generation_settings": {"params": {}}}
        resp_bytes = json.dumps(resp_data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def log_message(self, format, *args):
        pass  # 静默日志


def run_e2e_test():
    # 寻找空闲端口
    def get_free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]

    mock_backend_port = get_free_port()
    proxy_port = get_free_port()

    # 1. 启动模拟的 llama-server 后端
    backend_server = HTTPServer(('127.0.0.1', mock_backend_port), MockLlamaServerHandler)
    backend_thread = threading.Thread(target=backend_server.serve_forever, daemon=True)
    backend_thread.start()

    # 2. 导入代理网关并在测试端口启动
    from qwen_tool_proxy import ThreadedHTTPServer, TransparentProxyHandler, backend_manager

    proxy_server = ThreadedHTTPServer(('127.0.0.1', proxy_port), TransparentProxyHandler)
    proxy_server.target_host = "127.0.0.1"
    proxy_server.target_port = mock_backend_port
    proxy_server.api_key = "llamacpp"
    backend_manager.port = mock_backend_port
    backend_manager.api_key = "llamacpp"
    proxy_thread = threading.Thread(target=proxy_server.serve_forever, daemon=True)
    proxy_thread.start()

    time.sleep(0.3)

    print(f"🚀 Mock 后端运行在 :{mock_backend_port}，网关代理运行在 :{proxy_port}")

    # -------------------------------------------------------------
    # 场景 A: 客户端发起代码编写任务 (OpenAI 协议，温度为默认 0.7)
    # -------------------------------------------------------------
    req_payload_code = {
        "model": "Qwen3.8-27B",
        "messages": [{"role": "user", "content": "帮我用 Python 写一个双向链表实现，支持 insert 和 delete"}],
        "temperature": 0.7  # 客户端默认值
    }
    req_bytes = json.dumps(req_payload_code).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
        data=req_bytes,
        headers={"Content-Type": "application/json", "Authorization": "Bearer llamacpp"}
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        self_resp = json.loads(resp.read().decode("utf-8"))
        assert "choices" in self_resp

    # 检验后端收到的 payload
    captured_code = captured_backend_requests[-1]
    print("\n[E2E 验证 1 - 编程代码任务自适应参数装配]")
    print(f"  • 后端收到 temperature: {captured_code.get('temperature')} (预期: 0.10)")
    print(f"  • 后端收到 min_p: {captured_code.get('min_p')} (预期: 0.05)")
    print(f"  • 后端收到 top_p: {captured_code.get('top_p')} (预期: 0.85)")
    print(f"  • 后端收到 dry_multiplier: {captured_code.get('dry_multiplier')} (预期: 0.80)")
    print(f"  • 后端收到 reasoning_effort: {captured_code.get('reasoning_effort')} (预期: xhigh)")
    print(f"  • chat_template_kwargs: {captured_code.get('chat_template_kwargs')}")

    assert captured_code.get("temperature") == 0.10
    assert captured_code.get("min_p") == 0.05
    assert captured_code.get("dry_multiplier") == 0.80
    assert captured_code.get("reasoning_effort") == "xhigh"
    assert captured_code.get("chat_template_kwargs", {}).get("reasoning_effort") == "xhigh"

    # -------------------------------------------------------------
    # 场景 B: 客户端通过 Anthropic /v1/messages 协议发起写诗请求
    # -------------------------------------------------------------
    req_payload_anthropic = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "写一首赞美春天的现代抒情诗歌，词藻优美动人"}],
        "max_tokens": 1000
    }
    req_bytes = json.dumps(req_payload_anthropic).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{proxy_port}/v1/messages",
        data=req_bytes,
        headers={"Content-Type": "application/json", "x-api-key": "llamacpp"}
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        anthropic_resp = json.loads(resp.read().decode("utf-8"))
        assert "content" in anthropic_resp

    captured_creative = captured_backend_requests[-1]
    print("\n[E2E 验证 2 - Anthropic 协议文学创作自适应参数装配]")
    print(f"  • 后端收到 temperature: {captured_creative.get('temperature')} (预期: 0.88)")
    print(f"  • 后端收到 min_p: {captured_creative.get('min_p')} (预期: 0.03)")
    print(f"  • 后端收到 top_p: {captured_creative.get('top_p')} (预期: 0.98)")
    print(f"  • 后端收到 dry_multiplier: {captured_creative.get('dry_multiplier')} (预期: 0.50)")
    print(f"  • 后端收到 reasoning_effort: {captured_creative.get('reasoning_effort')} (预期: low)")

    assert captured_creative.get("temperature") == 0.88
    assert captured_creative.get("min_p") == 0.03
    assert captured_creative.get("dry_multiplier") == 0.50
    assert captured_creative.get("reasoning_effort") == "low"

    # -------------------------------------------------------------
    # 场景 C: 工具调用任务 (包含 tools)
    # -------------------------------------------------------------
    req_payload_tool = {
        "model": "Qwen3.8-27B",
        "messages": [{"role": "user", "content": "帮我查一下上海今天的天气"}],
        "tools": [{"type": "function", "function": {"name": "query_weather", "description": "查天气", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]
    }
    req_bytes = json.dumps(req_payload_tool).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
        data=req_bytes,
        headers={"Content-Type": "application/json", "Authorization": "Bearer llamacpp"}
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        tool_resp = json.loads(resp.read().decode("utf-8"))
        assert "choices" in tool_resp

    captured_tool = captured_backend_requests[-1]
    print("\n[E2E 验证 3 - 工具调用任务自适应参数装配]")
    print(f"  • 后端收到 temperature: {captured_tool.get('temperature')} (预期: 0.05)")
    print(f"  • 后端收到 min_p: {captured_tool.get('min_p')} (预期: 0.02)")
    print(f"  • 后端收到 reasoning_effort: {captured_tool.get('reasoning_effort')} (预期: none)")
    print(f"  • 后端收到 enable_thinking: {captured_tool.get('enable_thinking')} (预期: False)")

    assert captured_tool.get("temperature") == 0.05
    assert captured_tool.get("min_p") == 0.02
    assert captured_tool.get("reasoning_effort") == "none"
    assert captured_tool.get("enable_thinking") is False

    print("\n🎉 全部 E2E 端到端集成测试通过！自适应采样与思考参数装配 100% 正确！")

    backend_server.shutdown()
    proxy_server.shutdown()


if __name__ == "__main__":
    run_e2e_test()
