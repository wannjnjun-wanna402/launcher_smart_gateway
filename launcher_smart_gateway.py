#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 智能任务自适应网关 · Python 原生驱动引擎 (专为 AI 工具调用与自动化编排打造)
========================================================================
- 彻底告别 Windows CMD 编码陷阱与引号截断
- 纯 Python 原生进程守护与 8081 智能自适应调度
- 原生支持 Anthropic Messages API / OpenAI Completions / MCP Tool Calling
"""

import os
import sys
import time
import subprocess
import urllib.request
import json

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = r"E:\models"
SERVER_EXE = os.path.join(ROOT_DIR, "llama-server.exe")
TEMPLATE_FILE = os.path.join(ROOT_DIR, "chat_template_qwen_fixed.jinja")
LOG_DIR = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

today = time.strftime("%Y%m%d")
PROXY_LOG = os.path.join(LOG_DIR, f"8081_proxy_{today}.log")
DAILY_LOG = os.path.join(LOG_DIR, f"8083_llama_{today}.log")

def is_port_listening(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0

def ensure_gateway():
    if not is_port_listening(8081):
        print(f"[{time.strftime('%H:%M:%S')}] 🚀 正在拉起 8081 智能自适应调度网关...")
        subprocess.Popen([
            sys.executable,
            os.path.join(ROOT_DIR, "qwen_tool_proxy.py"),
            "--listen", "8081",
            "--target", "8083",
            "--api-key", "llamacpp"
        ], cwd=ROOT_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)

def stop_llama_servers():
    print(f"[{time.strftime('%H:%M:%S')}] 🧹 回收旧进程与显存...")
    subprocess.run(['powershell', '-Command', 'Get-Process | Where-Object { $_.ProcessName -match "llama" } | Stop-Process -Force'], capture_output=True)
    time.sleep(1)

def run_main_27b():
    ensure_gateway()
    stop_llama_servers()

    model_file = os.path.join(MODELS_DIR, "Qwen3.8-27B-Abliterated-Q6_K.gguf")
    if not os.path.exists(model_file):
        print(f"❌ 找不到模型: {model_file}")
        return

    print("=" * 80)
    print("  👑 AI 智能自适应网关 · Qwen3.8-27B 旗舰统一矩阵 (Python 原生驱动)")
    print("=" * 80)
    print("  ├─ 🌐 统一接口: http://127.0.0.1:8081/v1 (全应用接入点)")
    print("  ├─ 📊 算力大屏: http://127.0.0.1:8081/dashboard")
    print("  ├─ ⚡ 运行基准: 27B 双槽MTP (36.7 t/s) · 4.5秒三态无感热切 · 0秒动态思考调控")
    print("  └─ 💾 今日日志: " + DAILY_LOG)
    print("=" * 80)
    print("  ⏳ 正在向 Tesla V100 注入显存，预计耗时约 4~5 秒...
")

    cmd = [
        SERVER_EXE,
        "-m", model_file,
        "-ngl", "99",
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        "-c", "147456",
        "-b", "2048",
        "--ubatch-size", "2048",
        "-t", "6",
        "--parallel", "2",
        "--kv-unified",
        "--cache-reuse", "512",
        "--flash-attn", "on",
        "--ctx-checkpoints", "4",
        "--spec-type", "draft-mtp",
        "--spec-draft-n-max", "2",
        "--spec-draft-n-min", "1",
        "--reasoning", "auto",
        "--reasoning-budget", "2048",
        "--reasoning-effort", "medium",
        "--reasoning-format", "deepseek",
        "--reasoning-preserve",
        "--no-warmup",
        "--temp", "0.3",
        "--top-p", "0.95",
        "--top-k", "20",
        "--min-p", "0.05",
        "--dry-multiplier", "0.0",
        "--repeat-penalty", "1.05",
        "--presence-penalty", "0.0",
        "--jinja",
        "--chat-template-file", TEMPLATE_FILE,
        "--alias", "Qwen3.8-27B-A-Q6_K",
        "--port", "8083",
        "--host", "127.0.0.1",
        "--log-file", DAILY_LOG
    ]

    p = subprocess.Popen(cmd, cwd=ROOT_DIR)
    try:
        p.wait()
    except KeyboardInterrupt:
        print("
正在停止服务...")
        p.terminate()

if __name__ == "__main__":
    run_main_27b()
