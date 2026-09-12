#!/usr/bin/env python3
"""
奇迹 API 网关 — 把你的 llama.cpp 变成商业级 API 服务
===========================================================
功能：
  - 多模型自动切换（35B-MTP / 8B-VL / 27B-MTP）
  - 多 API Key 管理（可给朋友/同事发独立 Key）
  - 每 Key 用量追踪（token 数、请求数、最后使用时间）
  - 按 Key 限流（可配置每分钟请求上限）
  - 实时用量统计 HTTP API
  - 完整的 OpenAI 兼容接口
  - 管理员 CLI 管理 Key

用法：
  python miracle_api.py                    # 启动服务
  python miracle_api.py --add-key mykey    # 添加 API Key
  python miracle_api.py --list-keys        # 列出所有 Key
  python miracle_api.py --remove-key mykey # 删除 Key
  python miracle_api.py --stats            # 查看用量统计

Hermes 配置：
  model:
    provider: custom
    base_url: http://localhost:8082
    api_key: <你的 Key>
    default: hermes-35b-mtp
"""

import subprocess
import time
import json
import os
import sys
import threading
import atexit
import hashlib
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from collections import defaultdict
from qwen_tool_proxy import sanitize_payload

# ============================================================
#  配置
# ============================================================
LLAMA_DIR      = Path(r"E:\llama-win-cuda-12.4-x64")
MODELS_DIR     = Path(r"E:\models")
LLAMA_SERVER   = str(LLAMA_DIR / "llama-server.exe")
PROXY_PORT     = 51108
LLAMA_PORT     = 8083
LLAMA_HOST     = "127.0.0.1"
KEYS_FILE      = str(LLAMA_DIR / "miracle_api_keys.json")
STATS_FILE     = str(LLAMA_DIR / "miracle_api_stats.json")
RATE_LIMIT_RPM = 60  # 每 Key 每分钟最大请求数

# ============================================================
#  模型定义（从你的启动器参数精确提取）
# ============================================================
MODELS = {
    "hermes-35b-mtp": {
        "display": "Qwen3.6-35B-MTP",
        "file": "Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf",
        "category": "chat",
        "description": "快速聊天 / 日常任务",
        "context_length": 32768,
        "pricing": {"prompt": 0.15, "completion": 0.60},
        "args": [
            "-ngl", "99",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "-c", "32768", "-b", "2048", "-t", "6",
            "--parallel", "1", "--flash-attn", "enabled",
            "--temp", "0.7", "--top-p", "0.9", "--top-k", "20",
            "--min-p", "0.0", "--repeat-penalty", "1.05",
            "--spec-type", "draft-mtp",
            "--spec-draft-n-max", "3", "--spec-draft-n-min", "1",
            "--no-mmap", "--mlock", "--cont-batching",
            "--jinja", "--alias", "Qwen3.6-35B-MTP",
        ],
    },
    "hermes-8b-vl": {
        "display": "Qwen3VL-8B",
        "file": "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
        "mmproj": "mmproj-Qwen3VL-8B-Instruct-F16.gguf",
        "category": "vision",
        "description": "视觉理解 / 图文对话",
        "context_length": 32768,
        "pricing": {"prompt": 0.05, "completion": 0.20},
        "args": [
            "-ngl", "99",
            "--cache-type-k", "f16", "--cache-type-v", "f16",
            "-c", "32768", "-b", "1024", "--ubatch-size", "512",
            "-t", "6", "--parallel", "1", "--flash-attn", "enabled",
            "--temp", "0.7", "--top-p", "0.9", "--top-k", "20",
            "--min-p", "0.0", "--repeat-penalty", "1.05",
            "--no-mmap", "--cont-batching",
            "--jinja", "--alias", "Qwen3VL-8B",
        ],
    },
    "hermes-27b-mtp": {
        "display": "Qwen3.6-27B-MTP-IQ4",
        "file": "Qwen3.6-27B-MTP-IQ4_XS-Q8nextn.gguf",
        "category": "thinking",
        "description": "深度思考 / 复杂推理顾问",
        "context_length": 65536,
        "pricing": {"prompt": 0.10, "completion": 0.40},
        "args": [
            "-ngl", "99",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "-c", "65536", "-b", "2048", "-t", "6",
            "--parallel", "1", "--flash-attn", "enabled",
            "--reasoning", "on", "--reasoning-budget", "4096",
            "--spec-type", "draft-mtp",
            "--spec-draft-n-max", "4", "--spec-draft-n-min", "1",
            "--temp", "0.3", "--top-p", "0.9", "--top-k", "20",
            "--min-p", "0.0", "--repeat-penalty", "1.05",
            "--no-mmap", "--mlock", "--cont-batching",
            "--jinja", "--alias", "Qwen3.6-27B-MTP-IQ4",
        ],
    },
}

# ============================================================
#  API Key 管理
# ============================================================
def load_keys():
    """加载 API Key 数据库"""
    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # 默认创建管理员 Key
    return {
        "admin": {
            "name": "管理员",
            "created": time.time(),
            "enabled": True,
            "note": "管理员 Key，不限流",
            "rate_limit_unlimited": True,
        }
    }

def save_keys(keys):
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(keys, f, ensure_ascii=False, indent=2)

def load_stats():
    """加载用量统计"""
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

def add_key(key_id, name="", note=""):
    keys = load_keys()
    if key_id in keys:
        print(f"❌ Key '{key_id}' 已存在")
        return False
    keys[key_id] = {
        "name": name or key_id,
        "created": time.time(),
        "enabled": True,
        "note": note,
        "rate_limit_unlimited": False,
    }
    save_keys(keys)
    print(f"✅ 已添加 Key: {key_id}")
    print(f"   名称: {name or key_id}")
    print(f"   备注: {note}")
    return True

def remove_key(key_id):
    keys = load_keys()
    if key_id not in keys:
        print(f"❌ Key '{key_id}' 不存在")
        return False
    if key_id == "admin":
        print("❌ 不能删除管理员 Key")
        return False
    info = keys.pop(key_id)
    save_keys(keys)
    print(f"✅ 已删除 Key: {key_id} ({info['name']})")
    return True

def list_keys():
    keys = load_keys()
    stats = load_stats()
    print(f"\n{'='*70}")
    print(f"  API Key 列表")
    print(f"{'='*70}")
    print(f"  {'Key':<20} {'名称':<16} {'状态':<8} {'请求数':<10} {'总Token':<12} {'最后使用'}")
    print(f"  {'-'*70}")
    for kid, info in keys.items():
        status = "✅" if info.get("enabled", True) else "❌"
        s = stats.get(kid, {})
        reqs = s.get("total_requests", 0)
        toks = s.get("total_tokens", 0)
        last = s.get("last_used", "")
        note = info.get("note", "")
        ul = " ∞" if info.get("rate_limit_unlimited") else ""
        print(f"  {kid:<20} {info['name']:<16} {status}{ul:<7} {reqs:<10} {toks:<12} {last}")
        if note:
            print(f"  {'':20} 📝 {note}")
    print(f"{'='*70}\n")

def show_stats():
    stats = load_stats()
    keys = load_keys()
    if not stats:
        print("暂无用量数据")
        return

    print(f"\n{'='*70}")
    print(f"  用量统计（全部 Key）")
    print(f"{'='*70}")
    print(f"  {'Key':<20} {'名称':<16} {'请求数':<10} {'输入Token':<12} {'输出Token':<12} {'总Token':<12} {'花费(分)':<10}")
    print(f"  {'-'*70}")

    grand_total_req = 0
    grand_total_tok = 0
    grand_total_cost = 0.0

    for kid, s in sorted(stats.items()):
        name = keys.get(kid, {}).get("name", kid)
        reqs = s.get("total_requests", 0)
        ptoks = s.get("total_prompt_tokens", 0)
        ctoks = s.get("total_completion_tokens", 0)
        toks = ptoks + ctoks
        cost = s.get("total_cost_fen", 0.0)
        grand_total_req += reqs
        grand_total_tok += toks
        grand_total_cost += cost
        print(f"  {kid:<20} {name:<16} {reqs:<10} {ptoks:<12} {ctoks:<12} {toks:<12} ¥{cost/100:.4f}")
    
    print(f"  {'-'*70}")
    print(f"  {'合计':<38} {grand_total_req:<10} {'':<12} {'':<12} {grand_total_tok:<12} ¥{grand_total_cost/100:.4f}")

    # 按模型统计
    model_stats = defaultdict(lambda: {"requests": 0, "tokens": 0, "cost": 0.0})
    for kid, s in stats.items():
        for mid, ms in s.get("per_model", {}).items():
            model_stats[mid]["requests"] += ms.get("requests", 0)
            model_stats[mid]["tokens"] += ms.get("tokens", 0)
            model_stats[mid]["cost"] += ms.get("cost", 0.0)

    if model_stats:
        print(f"\n  {'='*70}")
        print(f"  按模型统计")
        print(f"{'='*70}")
        print(f"  {'模型':<24} {'请求数':<10} {'Token':<12} {'花费(分)':<10}")
        print(f"  {'-'*56}")
        for mid, ms in sorted(model_stats.items()):
            dn = MODELS.get(mid, {}).get("display", mid)
            print(f"  {dn:<24} {ms['requests']:<10} {ms['tokens']:<12} ¥{ms['cost']/100:.4f}")

    print(f"{'='*70}\n")

# ============================================================
#  llama-server 进程管理
# ============================================================
current_model_id = None
current_process  = None
process_lock     = threading.Lock()
current_model_ready = threading.Event()
current_model_lock = threading.RLock()
model_loading = False

# ============================================================
#  输出速度追踪
# ============================================================
speed_history = []  # [(timestamp, tokens), ...]
speed_current = 0.0  # tokens/sec 实时值
speed_peak = 0.0     # 历史最高
speed_lock = threading.Lock()
monitor_running = threading.Event()

def record_speed(tokens_generated, duration_ms=0):
    """记录一次完成的 token 数用于速度计算"""
    global speed_current, speed_peak
    now = time.time()
    with speed_lock:
        speed_history.append((now, tokens_generated))
        # 只保留最近 10 秒的数据
        cutoff = now - 10
        while speed_history and speed_history[0][0] < cutoff:
            speed_history.pop(0)
        # 计算最近 X 秒的平均速度
        if len(speed_history) >= 2:
            total_tok = sum(t[1] for t in speed_history)
            total_t = speed_history[-1][0] - speed_history[0][0]
            speed_current = total_tok / total_t if total_t > 0 else 0.0
        elif duration_ms > 0 and tokens_generated > 0:
            # 只有一次请求，用本次的 duration 估算
            speed_current = tokens_generated / (duration_ms / 1000.0)
        if speed_current > speed_peak:
            speed_peak = speed_current

def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)

def kill_current_server():
    global current_process, current_model_id
    with process_lock:
        if current_process and current_process.poll() is None:
            pid = current_process.pid
            log(f"停止当前模型 (PID {pid})...", "SWITCH")
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

def start_server(model_id):
    global current_process, current_model_id
    model_cfg = MODELS[model_id]
    model_path = str(MODELS_DIR / model_cfg["file"])
    if not os.path.exists(model_path):
        log(f"模型文件不存在: {model_path}", "ERROR")
        return False

    srv_args = [LLAMA_SERVER, "-m", model_path] + model_cfg["args"] + [
        "--port", str(LLAMA_PORT), "--host", LLAMA_HOST,
    ]
    if "mmproj" in model_cfg:
        mmproj_path = str(MODELS_DIR / model_cfg["mmproj"])
        if os.path.exists(mmproj_path):
            srv_args += ["--mmproj", mmproj_path]

    log(f"启动模型: {model_cfg['display']}", "SWITCH")
    try:
        p = subprocess.Popen(
                    srv_args,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        current_process = p
        current_model_id = model_id
        current_model_ready.clear()
        return True
    except Exception as e:
        log(f"启动失败: {e}", "ERROR")
        return False

def wait_for_server_ready(timeout=90):
    import urllib.request
    url = f"http://{LLAMA_HOST}:{LLAMA_PORT}/v1/models"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = urllib.request.urlopen(url, timeout=3)
            if resp.status == 200:
                current_model_ready.set()
                return True
        except:
            pass
        time.sleep(1)
    return False

def switch_model(model_id):
    global model_loading
    with current_model_lock:
        if model_loading:
            current_model_ready.wait(timeout=120)
            if current_model_id == model_id:
                return True
        if current_model_id == model_id and current_model_ready.is_set():
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
                log(f"✅ 已切换: {MODELS[model_id]['display']}", "SWITCH")
            else:
                log(f"❌ 切换失败: {model_id}", "SWITCH")
            model_loading = False
            return ready
    except Exception as e:
        with current_model_lock:
            model_loading = False
        log(f"切换异常: {e}", "ERROR")
        return False

# ============================================================
#  用量追踪
# ============================================================
def record_usage(api_key, model_id, prompt_tokens, completion_tokens, duration_ms):
    """记录一次 API 调用的用量"""
    # 记录输出速度
    record_speed(completion_tokens, duration_ms)
    stats = load_stats()
    if api_key not in stats:
        stats[api_key] = {
            "total_requests": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "total_cost_fen": 0.0,
            "total_duration_ms": 0,
            "per_model": {},
            "first_used": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_used": "",
        }
    
    s = stats[api_key]
    s["total_requests"] += 1
    s["total_prompt_tokens"] += prompt_tokens
    s["total_completion_tokens"] += completion_tokens
    s["total_tokens"] += prompt_tokens + completion_tokens
    s["total_duration_ms"] += duration_ms
    s["last_used"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # 计算费用（每百万 token 的价格）
    pricing = MODELS.get(model_id, {}).get("pricing", {"prompt": 0, "completion": 0})
    cost = (prompt_tokens * pricing["prompt"] + completion_tokens * pricing["completion"]) / 1_000_000
    s["total_cost_fen"] += cost

    # 按模型统计
    if model_id not in s["per_model"]:
        s["per_model"][model_id] = {"requests": 0, "tokens": 0, "cost": 0.0}
    s["per_model"][model_id]["requests"] += 1
    s["per_model"][model_id]["tokens"] += prompt_tokens + completion_tokens
    s["per_model"][model_id]["cost"] += cost

    save_stats(stats)

# ============================================================
#  限流器
# ============================================================
rate_limit_buckets = {}  # key -> list of timestamps
rate_limit_lock = threading.Lock()

def check_rate_limit(api_key):
    """检查是否超过限流，返回 (allowed, remaining, reset_seconds)"""
    keys_db = load_keys()
    key_info = keys_db.get(api_key, {})
    if key_info.get("rate_limit_unlimited", False) or key_info.get("rpm", 0) == 0:
        return True, -1, 0

    now = time.time()
    window = 60.0  # 1 分钟窗口
    
    with rate_limit_lock:
        if api_key not in rate_limit_buckets:
            rate_limit_buckets[api_key] = []
        
        # 清理过期时间戳
        bucket = rate_limit_buckets[api_key]
        rate_limit_buckets[api_key] = [t for t in bucket if now - t < window]
        bucket = rate_limit_buckets[api_key]
        
        if len(bucket) >= RATE_LIMIT_RPM:
            oldest = bucket[0]
            reset_in = int(window - (now - oldest))
            return False, 0, reset_in
        
        # 还没到达阈值，临时记录（等请求成功后正式记录）
        return True, RATE_LIMIT_RPM - len(bucket), 0

def record_rate_limit(api_key):
    """记录一次通过的请求（限流用）"""
    now = time.time()
    with rate_limit_lock:
        if api_key not in rate_limit_buckets:
            rate_limit_buckets[api_key] = []
        rate_limit_buckets[api_key].append(now)

# ============================================================
#  HTTP 服务端
# ============================================================
def _http_request(method, url, headers=None, body=None, timeout=30):
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if body is not None:
        req.data = body.encode("utf-8") if isinstance(body, str) else body
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp, None
    except urllib.error.HTTPError as e:
        return e, None
    except Exception as e:
        return None, str(e)

class MiracleHandler(BaseHTTPRequestHandler):
    """奇迹 API 网关处理器"""

    def log_message(self, format, *args):
        pass

    def _get_api_key(self):
        """从请求头提取 API Key"""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        # 也支持 x-api-key 头
        return self.headers.get("X-API-Key", "")

    def _authenticate(self):
        """验证 API Key，返回 (key_id, error_response)"""
        api_key = self._get_api_key()
        keys_db = load_keys()
        
        if not api_key:
            return None, self._send_json(401, {
                "error": {"message": "缺少 API Key。请在 Authorization 头中传入 Bearer <key>", "type": "auth_error"},
            })
        
        if api_key not in keys_db:
            return None, self._send_json(401, {
                "error": {"message": "API Key 无效", "type": "auth_error"},
            })
        
        key_info = keys_db[api_key]
        if not key_info.get("enabled", True):
            return None, self._send_json(403, {
                "error": {"message": "API Key 已被禁用", "type": "auth_error"},
            })
        
        # 限流检查
        allowed, remaining, reset_in = check_rate_limit(api_key)
        if not allowed:
            return None, self._send_json(429, {
                "error": {
                    "message": f"请求过频，请 {reset_in} 秒后重试",
                    "type": "rate_limit_error",
                    "reset_seconds": reset_in,
                }
            })
        
        return api_key, None

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-API-Gateway", "Miracle-API/v1")
        self.send_header("X-API-Version", "1.0.0")
        self.end_headers()
        self.wfile.write(body)
        return None

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
        if requested_model in MODELS:
            return requested_model
        for mid, cfg in MODELS.items():
            if cfg["display"] == requested_model:
                return mid
            if requested_model.startswith(cfg["display"]):
                return mid
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        # /v1/models - 列出模型
        if path == "/v1/models":
            api_key, err = self._authenticate()
            if err:
                return
            models_list = []
            for mid, cfg in MODELS.items():
                models_list.append({
                    "id": mid,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "miracle-api",
                    "description": cfg["description"],
                    "category": cfg["category"],
                    "context_length": cfg.get("context_length", 0),
                })
            self._send_json(200, {
                "object": "list",
                "data": models_list,
            })
            return

        # /v1/dashboard - 用量仪表盘（仅管理员）
        if path == "/v1/dashboard":
            api_key, err = self._authenticate()
            if err or api_key != "admin":
                self._send_json(403, {"error": {"message": "仅管理员可查看", "type": "auth_error"}})
                return
            self._serve_dashboard()
            return

        # /health
        if path in ("", "/health"):
            self._send_json(200, {
                "status": "ok",
                "service": "Miracle API Gateway",
                "version": "1.0.0",
                "current_model": current_model_id,
                "models_available": list(MODELS.keys()),
                "uptime_seconds": int(time.time() - start_time),
            })
            return

        # /dashboard - HTML 仪表盘
        if path == "/dashboard":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))
            return

        self._send_json(404, {"error": {"message": "Not Found", "type": "not_found"}})

    def _serve_dashboard(self):
        """返回用量统计页面（JSON）"""
        stats = load_stats()
        keys = load_keys()
        
        # 汇总
        summary = {
            "total_keys": len(keys),
            "total_requests": sum(s.get("total_requests", 0) for s in stats.values()),
            "total_tokens": sum(s.get("total_tokens", 0) for s in stats.values()),
            "total_cost_fen": sum(s.get("total_cost_fen", 0.0) for s in stats.values()),
            "active_keys": len([k for k, v in keys.items() if v.get("enabled", True)]),
        }
        
        # 按 Key 详情
        key_details = {}
        for kid, info in keys.items():
            s = stats.get(kid, {})
            key_details[kid] = {
                "name": info["name"],
                "enabled": info.get("enabled", True),
                "unlimited": info.get("rate_limit_unlimited", False),
                "note": info.get("note", ""),
                "created": info.get("created", 0),
                "stats": {
                    "requests": s.get("total_requests", 0),
                    "tokens": s.get("total_tokens", 0),
                    "cost_fen": round(s.get("total_cost_fen", 0.0), 4),
                    "last_used": s.get("last_used", "从未使用"),
                }
            }
        
        self._send_json(200, {
            "summary": summary,
            "keys": key_details,
        })

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # 认证
        api_key, err = self._authenticate()
        if err:
            return

        # CORS 预检
        if path == "/v1/chat/completions" or path == "/v1/completions":
            pass
        else:
            self._send_json(404, {"error": {"message": "Not Found", "type": "not_found"}})
            return

        body = self._read_body()
        try:
            req_data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": {"message": "无效的 JSON", "type": "invalid_request"}})
            return

        # 解析模型
        requested_model = req_data.get("model", "")
        model_id = self._resolve_model_id(requested_model)
        if not model_id:
            self._send_json(400, {
                "error": {
                    "message": f"未知模型: {requested_model}，可用: {', '.join(MODELS.keys())}",
                    "type": "invalid_model",
                }
            })
            return

        # 切换模型
        t_start = time.time()
        if not switch_model(model_id):
            self._send_json(503, {
                "error": {"message": f"模型加载失败: {model_id}", "type": "server_error"}
            })
            return

        # 转发请求到 llama-server
        is_stream = req_data.get("stream", False)
        target_url = f"http://{LLAMA_HOST}:{LLAMA_PORT}{path}"
        req_data["model"] = MODELS[model_id]["display"]
        # 强制启用 KV cache 复用，否则每轮对话都要重新处理整个历史
        req_data["cache_prompt"] = True
        # 清洗 tools schema 中的 GBNF 爆炸约束 (maxLength 等)
        req_data, _ = sanitize_payload(req_data)

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if is_stream else "application/json",
        }
        auth = self.headers.get("Authorization", "")
        if auth:
            headers["Authorization"] = auth

        new_body = json.dumps(req_data, ensure_ascii=False)

        try:
            if is_stream:
                self._proxy_stream(target_url, headers, new_body, api_key, model_id, t_start)
            else:
                self._proxy_sync(target_url, headers, new_body, api_key, model_id, t_start)
        except BrokenPipeError:
            pass
        except Exception as e:
            log(f"转发失败: {e}", "ERROR")
            try:
                self._send_json(502, {"error": {"message": str(e), "type": "proxy_error"}})
            except:
                pass

    def _get_usage_from_response(self, resp_data, model_id):
        """从响应中提取 token 用量"""
        usage = resp_data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or usage.get("generated_tokens", 0)
        return prompt_tokens, completion_tokens

    def _record_and_respond(self, api_key, model_id, t_start, prompt_tokens, completion_tokens):
        """记录用量并返回响应头"""
        duration = int((time.time() - t_start) * 1000)
        record_usage(api_key, model_id, prompt_tokens, completion_tokens, duration)
        record_rate_limit(api_key)

    def _proxy_stream(self, url, headers, body, api_key, model_id, t_start):
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
        self.send_header("X-API-Gateway", "Miracle-API/v1")
        self.end_headers()

        prompt_tokens = 0
        completion_tokens = 0
        last_speed_time = time.time()
        speed_acc_tokens = 0  # 本次记录周期内累计的 token 估算值

        try:
            buffer = ""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                buffer += text
                self.wfile.write(chunk)
                self.wfile.flush()
                
                # 逐行解析 SSE，从 delta.content 中估算 token 实时记录速度
                now = time.time()
                while "\n" in buffer:
                    idx = buffer.index("\n")
                    line = buffer[:idx + 1]
                    buffer = buffer[idx + 1:]
                    if line.startswith("data: ") and line != "data: [DONE]\n":
                        data_str = line[6:]
                        try:
                            data = json.loads(data_str)
                            # 提取 delta 内容估算 token
                            choices = data.get("choices", [])
                            for ch in choices:
                                delta = ch.get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    # 估算 token 数：中英文混合约 1 token ≈ 2 字符
                                    est_tokens = max(1, len(content) // 2)
                                    speed_acc_tokens += est_tokens
                                    completion_tokens += est_tokens  # 近似
                            # 提取 usage（最后一条数据才有）
                            usage = data.get("usage", {})
                            if usage:
                                new_ptok = usage.get("prompt_tokens") or usage.get("input_tokens", 0)
                                new_ctok = usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("generated_tokens", 0)
                                if new_ctok > 0:
                                    completion_tokens = new_ctok
                                if new_ptok > 0:
                                    prompt_tokens = new_ptok
                        except:
                            pass
                
                # 大约每秒记录一次速度（用累计的估算 token）
                if now - last_speed_time >= 1.0 and speed_acc_tokens > 0:
                    record_speed(speed_acc_tokens)
                    speed_acc_tokens = 0
                    last_speed_time = now
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            resp.close()
            # 记录剩余的 token（流结束但还没记录）
            if speed_acc_tokens > 0:
                record_speed(speed_acc_tokens)
            # 记录用量
            self._record_and_respond(api_key, model_id, t_start, prompt_tokens, completion_tokens)

    def _proxy_sync(self, url, headers, body, api_key, model_id, t_start):
        """非流式转发"""
        resp, err = _http_request("POST", url, headers=headers, body=body, timeout=300)
        if err:
            self._send_json(502, {"error": {"message": err, "type": "proxy_error"}})
            return

        data = resp.read()
        
        # 解析用量
        prompt_tokens = 0
        completion_tokens = 0
        try:
            resp_data = json.loads(data.decode("utf-8"))
            prompt_tokens, completion_tokens = self._get_usage_from_response(resp_data, model_id)
        except:
            pass

        # 记录用量
        self._record_and_respond(api_key, model_id, t_start, prompt_tokens, completion_tokens)

        # 响应
        self.send_response(resp.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-API-Gateway", "Miracle-API/v1")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

# ============================================================
#  CLI 管理
# ============================================================
def cli_manage():
    if len(sys.argv) < 2:
        return False
    
    cmd = sys.argv[1]
    
    if cmd == "--add-key":
        if len(sys.argv) < 3:
            print("用法: python miracle_api.py --add-key <key_id> [名称] [备注]")
            return True
        key_id = sys.argv[2]
        name = sys.argv[3] if len(sys.argv) > 3 else ""
        note = sys.argv[4] if len(sys.argv) > 4 else ""
        add_key(key_id, name, note)
        return True
    
    if cmd == "--remove-key":
        if len(sys.argv) < 3:
            print("用法: python miracle_api.py --remove-key <key_id>")
            return True
        remove_key(sys.argv[2])
        return True
    
    if cmd == "--list-keys":
        list_keys()
        return True
    
    if cmd == "--stats":
        show_stats()
        return True
    
    return False

# ============================================================
#  HTML 仪表盘
# ============================================================
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>奇迹 API 网关 — 仪表盘</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', sans-serif; background: #0f0f1a; color: #e0e0e0; padding: 20px; }
h1 { font-size: 1.5em; margin-bottom: 20px; color: #a78bfa; }
.card { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
.card h2 { font-size: 1em; color: #9ca3af; margin-bottom: 8px; }
.card .big { font-size: 2em; font-weight: 700; color: #a78bfa; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }
table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
th { text-align: left; padding: 8px 12px; border-bottom: 1px solid #2a2a4a; color: #9ca3af; font-weight: 500; }
td { padding: 8px 12px; border-bottom: 1px solid #1f1f35; }
.key-id { font-family: 'Consolas', monospace; color: #fbbf24; }
.status-on { color: #34d399; }
.status-off { color: #ef4444; }
.note { color: #6b7280; font-size: 0.85em; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; }
.tag-admin { background: #7c3aed20; color: #a78bfa; border: 1px solid #7c3aed40; }
.tag-unlimited { background: #f59e0b20; color: #fbbf24; border: 1px solid #f59e0b40; }
footer { margin-top: 30px; text-align: center; color: #4b5563; font-size: 0.8em; }
</style>
</head>
<body>
<div id="app">
<h1>⚡ 奇迹 API 网关</h1>
<div class="grid">
  <div class="card"><h2>API Key</h2><div class="big" id="totalKeys">-</div></div>
  <div class="card"><h2>总请求数</h2><div class="big" id="totalReqs">-</div></div>
  <div class="card"><h2>总 Token</h2><div class="big" id="totalTokens">-</div></div>
  <div class="card"><h2>总花费</h2><div class="big" id="totalCost">-</div></div>
</div>
<div class="card">
  <h2>🔑 API Key 管理</h2>
  <table><thead><tr><th>Key</th><th>名称</th><th>状态</th><th>请求数</th><th>Token</th><th>花费</th><th>最后使用</th></tr></thead>
  <tbody id="keyTable"></tbody></table>
</div>
<footer>Miracle API Gateway v1.0 — 你的私人 LLM API 服务</footer>
</div>
<script>
async function load() {
  try {
    const r = await fetch('/v1/dashboard', { headers: { 'Authorization': 'Bearer admin' } });
    const d = await r.json();
    document.getElementById('totalKeys').textContent = d.summary.total_keys;
    document.getElementById('totalReqs').textContent = d.summary.total_requests.toLocaleString();
    document.getElementById('totalTokens').textContent = d.summary.total_tokens.toLocaleString();
    document.getElementById('totalCost').textContent = '¥' + (d.summary.total_cost_fen / 100).toFixed(4);
    const tbody = document.getElementById('keyTable');
    tbody.innerHTML = '';
    for (const [kid, info] of Object.entries(d.keys)) {
      const tr = document.createElement('tr');
      const status = info.enabled ? '<span class="status-on">● 启用</span>' : '<span class="status-off">● 禁用</span>';
      const tags = kid === 'admin' ? ' <span class="tag tag-admin">管理员</span>' : '';
      if (info.unlimited) tags += ' <span class="tag tag-unlimited">∞</span>';
      tr.innerHTML = `<td><span class="key-id">${kid}</span>${tags}</td>
        <td>${info.name}</td>
        <td>${status}</td>
        <td>${info.stats.requests.toLocaleString()}</td>
        <td>${info.stats.tokens.toLocaleString()}</td>
        <td>¥${info.stats.cost_fen.toFixed(4)}</td>
        <td class="note">${info.stats.last_used}</td>`;
      tbody.appendChild(tr);
    }
  } catch(e) { document.getElementById('app').innerHTML = '<p>无法加载仪表盘（需要管理员 Key）</p>'; }
}
load();
</script>
</body>
</html>"""

# ============================================================
#  启动
# ============================================================
start_time = time.time()

def cleanup():
    monitor_running.clear()
    log("正在清理...", "EXIT")
    kill_current_server()

atexit.register(cleanup)

def check_gpu_and_cleanup():
    """检查 GPU 占用，清理残留的 llama-server 进程（不碰其他程序）"""
    log("检查 GPU 状态...", "START")
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return
        parts = result.stdout.strip().split(", ")
        if len(parts) != 2:
            return
        used = int(parts[0])
        total = int(parts[1])
        pct = round(used / total * 100, 1) if total > 0 else 0
        log(f"GPU 显存: {used}/{total} MB ({pct}%)", "START")

        # 清理残留的 llama-server 进程（只杀父进程不是我们的）
        orphan_found = False
        for attempt in range(3):
            ps_result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV"],
                capture_output=True, encoding="gbk", timeout=10
            )
            if "llama-server.exe" not in ps_result.stdout:
                break

            # 用 wmic 获取所有 llama-server PID 和父进程 PID
            orphan_pids = []
            try:
                wmic = subprocess.run(
                    ["wmic", "process", "where", "name='llama-server.exe'", "get", "processid,parentprocessid"],
                    capture_output=True, encoding="gbk", timeout=10
                )
                my_pid = str(os.getpid())
                wmic_out = wmic.stdout or ""
                NL = chr(10)
                for line in wmic_out.strip().split(NL):
                    line = line.strip()
                    if not line or "ParentProcessId" in line:
                        continue
                    parts2 = line.split()
                    if len(parts2) >= 2:
                        pid = parts2[-1].strip()
                        ppid = parts2[0].strip()
                        if pid and ppid and ppid != my_pid:
                            orphan_pids.append(pid)
            except Exception:
                orphan_pids = ["*"]

            if orphan_pids:
                orphan_found = True
                if orphan_pids == ["*"]:
                    log("发现残留 llama-server 进程！正在清理...", "WARN")
                    subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"],
                                 capture_output=True, timeout=10)
                else:
                    for pid in orphan_pids:
                        log(f"发现残留 llama-server (PID {pid})，正在清理...", "WARN")
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                     capture_output=True, timeout=10)
                time.sleep(2)
            else:
                break

        if orphan_found:
            log("等待显存释放...", "START")
            for i in range(20):
                time.sleep(2)
                try:
                    r2 = subprocess.run(
                        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=5
                    )
                    if r2.returncode == 0:
                        now_used = int(r2.stdout.strip())
                        log(f"  显存: {now_used}/{total} MB", "START")
                        if now_used < used * 0.5:
                            log("显存已释放", "START")
                            break
                except:
                    pass
        elif pct > 80:
            log(f"显存被其他程序占用 ({pct}%)，跳过清理", "START")
        else:
            log("GPU 状态正常", "START")
    except FileNotFoundError:
        log("nvidia-smi 不可用，跳过 GPU 检查", "START")
    except Exception as e:
        log(f"GPU 检查异常: {e}", "WARN")


# ============================================================
#  实时监控线程
# ============================================================
def monitor_loop():
    """每 10 秒刷新一次监控日志"""
    global current_model_id
    BOLD = "\033[1m"
    RESET = "\033[0m"
    gpu_info = "GPU: N/A"
    while monitor_running.is_set():
        with speed_lock:
            spd = speed_current
        with current_model_lock:
            mid = current_model_id
            ready = current_model_ready.is_set()
        model_name = MODELS.get(mid, {}).get("display", mid) if mid else "N/A"
        speed_str = f"{BOLD}{spd:.1f}{RESET} tokens/秒"
        ts = time.strftime("%H:%M:%S")

        # GPU 每 10 秒刷新
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=power.draw,power.limit,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                parts = r.stdout.strip().split(", ")
                if len(parts) == 4:
                    pw = float(parts[0])
                    pw_max = float(parts[1])
                    mem_u = int(float(parts[2]))
                    mem_t = int(float(parts[3]))
                    mem_pct = mem_u / mem_t * 100 if mem_t > 0 else 0
                    gpu_info = f"{BOLD}{pw:.0f}W{BOLD}/{pw_max:.0f}W  显存: {mem_u}/{mem_t} MB ({mem_pct:.1f}%)"
        except:
            gpu_info = "GPU: N/A"

        line = f"[{ts}] | {model_name} | {speed_str} | {gpu_info}{RESET}"
        print(f"{line}", flush=True)

        time.sleep(10)

def main():
    # 检查是否 CLI 管理模式
    if cli_manage():
        return

    # 初始化 Key 文件
    keys = load_keys()
    if "admin" not in keys:
        keys["admin"] = {
            "name": "管理员", "created": time.time(),
            "enabled": True, "note": "管理员 Key，不限流",
            "rate_limit_unlimited": True,
        }
        save_keys(keys)

    BOLD = "\033[1m"
    RESET = "\033[0m"
    print(f"\n{'='*60}", flush=True)
    print(f"  ⚡ 奇迹 API 网关 — AI 系统监控器", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"  模型规格:", flush=True)
    for mid, cfg in MODELS.items():
        ctx = f"{cfg['context_length']:,}"
        print(f"    {BOLD}{mid}{RESET} | 上下文长度: {BOLD}{ctx}{RESET} tokens | 历史最高输出速度: {BOLD}N/A{RESET} tokens/秒", flush=True)
    print(f"  ---", flush=True)
    print(f"  📍 本机:  http://localhost:{PROXY_PORT}/v1", flush=True)
    print(f"  🌐 DDNS:  http://wannjnjun.eicp.net:{PROXY_PORT}/v1", flush=True)
    print(f"  🔑 Key:   admin", flush=True)
    print(f"  📊 面板:  http://localhost:{PROXY_PORT}/dashboard", flush=True)
    print(f"{'='*60}\n", flush=True)

    # GPU 检查：清理残留 llama-server，不碰其他程序
    check_gpu_and_cleanup()
    # 启动默认模型
    default = "hermes-35b-mtp"
    log(f"加载默认模型: {MODELS[default]['display']}", "START")
    if not start_server(default):
        log("默认模型启动失败，检查模型文件", "ERROR")
        sys.exit(1)
    if not wait_for_server_ready():
        log("服务就绪超时", "ERROR")
        sys.exit(1)
    log("默认模型已就绪 ✅", "START")

    # 启动 HTTP 服务
    server = HTTPServer(("0.0.0.0", PROXY_PORT), MiracleHandler)
    log(f"奇迹 API 网关启动在 http://localhost:{PROXY_PORT}", "START")

    # 启动实时监控线程
    monitor_running.set()
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    time.sleep(0.1)  # 等主线程打完 log 再让监控线程开始刷新
    log("监控日志已启动，每 10 秒刷新", "START")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("收到退出信号", "EXIT")
        server.shutdown()

if __name__ == "__main__":
    main()

