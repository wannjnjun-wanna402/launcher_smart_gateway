#!/usr/bin/env python3
"""
Hermes llama.cpp 智能模型切换代理 v1.0
==========================================
功能：
  - 单端口运行，向 Hermes 暴露 3 个模型 (35B-MTP / 8B-VL / 27B-MTP)
  - 根据请求自动切换模型（杀掉旧进程，拉起新进程）
  - 支持流式 SSE 输出
  - 支持视觉模型（图片输入）
  - 支持工具调用 (--jinja)
  - 启动后无需任何人工干预

用法：
  python hermes_llama_proxy.py

Hermes 配置：
  model:
    provider: custom
    base_url: http://localhost:8082
    api_key: hermes-llamacpp
    default: hermes-35b-mtp
"""

import subprocess
import time
import json
import os
import sys
import signal
import threading
import queue
import re
import io
import atexit
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from qwen_tool_proxy import sanitize_payload

# ============================================================
#  配置
# ============================================================
LLAMA_DIR      = Path(r"E:\llama-win-cuda-12.4-x64")
MODELS_DIR     = Path(r"E:\models")
LLAMA_SERVER   = str(LLAMA_DIR / "llama-server.exe")
PROXY_PORT     = 8082
API_KEY        = "hermes-llamacpp"  # Hermes 连接时用的 API Key
LLAMA_PORT     = 8083               # 内部 llama-server 实际运行的端口
LLAMA_HOST     = "127.0.0.1"

# ============================================================
#  模型定义（从你的启动器参数精确提取）
# ============================================================
MODELS = {
    "hermes-35b-mtp": {
        "display": "Qwen3.6-35B-MTP",
        "file": "Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf",
        "category": "chat",
        "description": "快速聊天 / 日常任务",
        "args": [
            "-ngl", "99",
            "--cache-type-k", "q8_0",
            "--cache-type-v", "q8_0",
            "-c", "81920",
            "-b", "2048",
            "-t", "6",
            "--parallel", "1",
            "--flash-attn", "enabled",
            "--temp", "0.7",
            "--top-p", "0.9",
            "--top-k", "20",
            "--min-p", "0.0",
            "--repeat-penalty", "1.05",
            "--reasoning", "on",
            "--reasoning-budget", "2048",
            "--spec-type", "draft-mtp",
            "--spec-draft-n-max", "3",
            "--spec-draft-n-min", "1",
            "--jinja",
            "--alias", "Qwen3.6-35B-MTP",
        ],
    },
    "hermes-8b-vl": {
        "display": "Qwen3VL-8B",
        "file": "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
        "mmproj": "mmproj-Qwen3VL-8B-Instruct-F16.gguf",
        "category": "vision",
        "description": "视觉理解 / 图文对话",
        "args": [
            "-ngl", "99",
            "--cache-type-k", "f16",
            "--cache-type-v", "f16",
            "-c", "98304",
            "-b", "2048",
            "--ubatch-size", "2048",
            "-t", "6",
            "--parallel", "1",
            "--flash-attn", "enabled",
            "--temp", "0.7",
            "--top-p", "0.9",
            "--top-k", "20",
            "--min-p", "0.0",
            "--repeat-penalty", "1.05",
            "--jinja",
            "--alias", "Qwen3VL-8B",
        ],
    },
    "hermes-27b-mtp": {
        "display": "Qwen3.6-27B-MTP-IQ4",
        "file": "Qwen3.6-27B-MTP-IQ4_XS-Q8nextn.gguf",
        "category": "thinking",
        "description": "深度思考 / 复杂推理顾问",
        "args": [
            "-ngl", "99",
            "--cache-type-k", "q8_0",
            "--cache-type-v", "q8_0",
            "-c", "98304",
            "-b", "2048",
            "-t", "6",
            "--parallel", "1",
            "--flash-attn", "enabled",
            "--reasoning", "on",
            "--reasoning-budget", "2048",
            "--spec-type", "draft-mtp",
            "--spec-draft-n-max", "4",
            "--spec-draft-n-min", "1",
            "--temp", "0.3",
            "--top-p", "0.9",
            "--top-k", "20",
            "--min-p", "0.0",
            "--repeat-penalty", "1.05",
            "--jinja",
            "--alias", "Qwen3.6-27B-MTP-IQ4",
        ],
    },
}

# ============================================================
#  全局状态
# ============================================================
current_model_id = None  # 当前加载的模型 ID
current_process  = None  # llama-server 子进程
process_lock     = threading.Lock()
current_model_ready = threading.Event()  # 当前模型是否就绪
current_model_lock = threading.RLock()
model_loading = False

# ============================================================
#  日志
# ============================================================
def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)

# ============================================================
#  llama-server 进程管理
# ============================================================
def kill_current_server():
    """杀掉当前 llama-server 进程"""
    global current_process, current_model_id
    with process_lock:
        if current_process and current_process.poll() is None:
            pid = current_process.pid
            log(f"正在停止当前模型 (PID {pid})...", "SWITCH")
            # Windows 下用 taskkill 杀进程树
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10
            )
            try:
                current_process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            current_process = None
            current_model_id = None
            current_model_ready.clear()
            log("模型已停止", "SWITCH")

def start_server(model_id):
    """启动指定模型的 llama-server"""
    global current_process, current_model_id, model_loading

    model_cfg = MODELS[model_id]
    model_path = str(MODELS_DIR / model_cfg["file"])

    # 检查模型文件是否存在
    if not os.path.exists(model_path):
        log(f"模型文件不存在: {model_path}", "ERROR")
        return False

    # 构建参数
    srv_args = [
        LLAMA_SERVER,
        "-m", model_path,
    ] + model_cfg["args"] + [
        "--port", str(LLAMA_PORT),
        "--host", LLAMA_HOST,
    ]

    # 视觉模型需要 mmproj
    if "mmproj" in model_cfg:
        mmproj_path = str(MODELS_DIR / model_cfg["mmproj"])
        if os.path.exists(mmproj_path):
            srv_args += ["--mmproj", mmproj_path]
        else:
            log(f"mmproj 文件不存在: {mmproj_path}", "WARN")

    log(f"启动模型: {model_cfg['display']}", "SWITCH")
    log(f"命令: {' '.join(srv_args[:6])} ...", "SWITCH")

    try:
        p = subprocess.Popen(
            srv_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        current_process = p
        current_model_id = model_id
        current_model_ready.clear()
        return True
    except Exception as e:
        log(f"启动失败: {e}", "ERROR")
        return False

def wait_for_server_ready(timeout=60):
    """等待 llama-server 就绪（轮询 /v1/models）"""
    url = f"http://{LLAMA_HOST}:{LLAMA_PORT}/v1/models"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = _http_request("GET", url, timeout=3)
            if resp and resp.status == 200:
                current_model_ready.set()
                return True
        except:
            pass
        time.sleep(1)
    log(f"服务就绪超时 ({timeout}s)", "ERROR")
    return False

def switch_model(model_id):
    """切换到指定模型，等就绪"""
    global model_loading

    with current_model_lock:
        if model_loading:
            log("正在加载中，等待...", "SWITCH")
            current_model_ready.wait(timeout=120)
            if current_model_id == model_id:
                return True

        if current_model_id == model_id and current_model_ready.is_set():
            # 已经是这个模型且就绪
            return True

        model_loading = True

    try:
        with current_model_lock:
            kill_current_server()
            if not start_server(model_id):
                return False
            model_loading = True

        ready = wait_for_server_ready()
        with current_model_lock:
            if ready:
                log(f"✅ 模型切换完成: {MODELS[model_id]['display']}", "SWITCH")
            else:
                log(f"❌ 模型切换失败: {model_id}", "SWITCH")
            model_loading = False
            return ready
    except Exception as e:
        with current_model_lock:
            model_loading = False
        log(f"切换异常: {e}", "ERROR")
        return False

# ============================================================
#  简单的 HTTP 请求工具
# ============================================================
def _http_request(method, url, headers=None, body=None, timeout=30, stream=False):
    """用 urllib 做 HTTP 请求（免 requests 依赖）"""
    import urllib.request
    req = urllib.request.Request(url, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if body is not None:
        req.data = body.encode("utf-8") if isinstance(body, str) else body
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp
    except urllib.error.HTTPError as e:
        return e
    except Exception as e:
        raise e

# ============================================================
#  HTTP 服务端
# ============================================================
class ProxyHandler(BaseHTTPRequestHandler):
    """为 Hermes 提供 OpenAI 兼容的 API 代理"""

    def log_message(self, format, *args):
        # 静默，不用 stderr 刷屏
        pass

    def _check_auth(self):
        """验证 API Key"""
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {API_KEY}"
        if auth == expected:
            return True
        # 也允许无认证（本地调试）
        return True

    def _send_json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            raw = self.rfile.read(length)
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("utf-8", errors="replace")
        return ""

    def _resolve_model_id(self, requested_model):
        """将请求中的模型名映射到内部 ID"""
        # 直接匹配
        if requested_model in MODELS:
            return requested_model
        # 匹配 display 名
        for mid, cfg in MODELS.items():
            if cfg["display"] == requested_model:
                return mid
            if requested_model.startswith(cfg["display"]):
                return mid
        # 匹配文件名关键词
        for mid, cfg in MODELS.items():
            base = os.path.splitext(cfg["file"])[0]
            if base in requested_model or requested_model in base:
                return mid
        # 没匹配到
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if not self._check_auth():
            self._send_json(401, {"error": "unauthorized"})
            return

        # /v1/models - 列出所有可用模型
        if path == "/v1/models":
            models_list = []
            for mid, cfg in MODELS.items():
                models_list.append({
                    "id": mid,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "hermes-llama-proxy",
                    "permission": [],
                    "root": mid,
                    "description": cfg["description"],
                    "category": cfg["category"],
                })
            self._send_json(200, {
                "object": "list",
                "data": models_list,
            })
            return

        # /health 或 / - 健康检查
        if path in ("", "/health"):
            status = "running"
            current = current_model_id
            if current:
                status = f"loaded: {MODELS[current]['display']}"
            self._send_json(200, {
                "status": "ok",
                "current_model": current,
                "current_model_display": MODELS[current]["display"] if current else None,
                "models_available": list(MODELS.keys()),
            })
            return

        # /v1/props - 透传当前模型的 props
        if path == "/v1/props":
            if not current_model_ready.is_set():
                self._send_json(503, {"error": "model not ready"})
                return
            try:
                resp = _http_request("GET", f"http://{LLAMA_HOST}:{LLAMA_PORT}/props", timeout=5)
                data = resp.read().decode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data.encode("utf-8"))
            except Exception as e:
                self._send_json(502, {"error": str(e)})
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if not self._check_auth():
            self._send_json(401, {"error": "unauthorized"})
            return

        # 只处理聊天补全
        if path not in ("/v1/chat/completions", "/v1/completions"):
            self._send_json(404, {"error": "not found"})
            return

        body = self._read_body()
        try:
            req_data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid json"})
            return

        # 解析请求的模型
        requested_model = req_data.get("model", "")
        model_id = self._resolve_model_id(requested_model)

        if not model_id:
            self._send_json(400, {
                "error": f"unknown model: {requested_model}",
                "available": list(MODELS.keys()),
            })
            return

        # 切换模型（如果需要）
        if not switch_model(model_id):
            self._send_json(503, {"error": f"failed to load model: {model_id}"})
            return

        # 转发请求到 llama-server
        is_stream = req_data.get("stream", False)
        target_url = f"http://{LLAMA_HOST}:{LLAMA_PORT}{path}"

        # 重写 model 为 llama-server 能识别的别名
        req_data["model"] = MODELS[model_id]["display"]
        # 强制启用 KV cache 复用，否则每轮对话都要重新处理整个历史
        req_data["cache_prompt"] = True
        # 清洗 tools schema 中的 GBNF 爆炸约束 (maxLength 等)
        req_data, _ = sanitize_payload(req_data)

        # 转发请求头
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if is_stream else "application/json",
        }
        # 如果 Hermes 带了 API Key，也透传（llama-server 可能没设验证）
        auth = self.headers.get("Authorization", "")
        if auth:
            headers["Authorization"] = auth

        new_body = json.dumps(req_data, ensure_ascii=False)

        try:
            if is_stream:
                self._proxy_stream(target_url, headers, new_body)
            else:
                self._proxy_sync(target_url, headers, new_body)
        except BrokenPipeError:
            pass
        except Exception as e:
            log(f"转发请求失败: {e}", "ERROR")
            try:
                self._send_json(502, {"error": str(e)})
            except:
                pass

    def _proxy_stream(self, url, headers, body):
        """流式 SSE 转发"""
        import urllib.request
        req = urllib.request.Request(url, method="POST", data=body.encode("utf-8"))
        for k, v in headers.items():
            req.add_header(k, v)

        resp = urllib.request.urlopen(req, timeout=300)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            resp.close()

    def _proxy_sync(self, url, headers, body):
        """非流式转发"""
        resp = _http_request("POST", url, headers=headers, body=body, timeout=300)
        data = resp.read()

        self.send_response(resp.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        resp_headers = dict(resp.headers)
        for h in ("Content-Type",):
            if h in resp_headers and h != "Transfer-Encoding":
                pass
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        """CORS 预检"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()


# ============================================================
#  启动
# ============================================================
def cleanup():
    """退出时清理"""
    log("正在清理...", "EXIT")
    kill_current_server()

atexit.register(cleanup)

def main():
    banner = f"""
╔══════════════════════════════════════════════════════════╗
║          Hermes llama.cpp 智能模型切换代理 v1.0           ║
╠══════════════════════════════════════════════════════════╣
║  代理端口: {PROXY_PORT} (Hermes 连这个)                         ║
║  内部端口: {LLAMA_PORT} (llama-server)                         ║
║  API Key : {API_KEY}                                     ║
╠══════════════════════════════════════════════════════════╣
║  可用模型:                                                ║
║    hermes-35b-mtp  → 快速聊天 / 日常任务                    ║
║    hermes-8b-vl    → 视觉理解 / 图文对话                    ║
║    hermes-27b-mtp  → 深度思考 / 复杂推理顾问                 ║
╠══════════════════════════════════════════════════════════╣
║  启动后 Hermes 会自动发现模型并切换，无需人工干预              ║
║  默认加载第一个模型 (35B-MTP)                               ║
╚══════════════════════════════════════════════════════════╝
"""
    print(banner, flush=True)

    # 启动默认模型
    default_model = "hermes-35b-mtp"
    log(f"默认加载模型: {MODELS[default_model]['display']}", "START")
    if not start_server(default_model):
        log("启动默认模型失败，请检查路径和模型文件", "ERROR")
        sys.exit(1)
    if not wait_for_server_ready():
        log("服务就绪超时", "ERROR")
        sys.exit(1)
    log("默认模型已就绪 ✅", "START")

    # 启动 HTTP 代理
    server = HTTPServer(("0.0.0.0", PROXY_PORT), ProxyHandler)
    log(f"Hermes 代理启动在 http://localhost:{PROXY_PORT}", "START")
    log("等待 Hermes 连接...", "START")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("收到退出信号", "EXIT")
        server.shutdown()

if __name__ == "__main__":
    main()