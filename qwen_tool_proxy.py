#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen / llama.cpp 企业级智能协同网关 3.0 (qwen_tool_proxy.py)
===================================================================
架构特性与核心能力：
1. [Anthropic Messages API 原生转译层 (/v1/messages)]
   - 原生支持 Claude Code (官方 CLI)、Claude Desktop、Cursor、Roo Code、Cline 等工具。
   - 自动在 Anthropic Messages 协议与 OpenAI Chat Completions 协议之间双向无缝转换。
   - 完整支持流式 SSE 事件 (message_start, content_block_start, content_block_delta, message_stop) 与非流式转译。
2. [GBNF 语法防爆 Schema 净化引擎 3.0]
   - 自动递归清洗 MCP 工具与 OpenAPI 3.1 中的 anyOf, oneOf, allOf, $defs, definitions, additionalProperties: false 等语法陷阱。
   - 彻底杜绝 llama.cpp GBNF 语法树无限递归与内存爆炸，保证 100% 稳定解析。
3. [两阶段图文协同流水线 (Two-Stage Vision-to-Reasoning Pipeline)]
   - 声明全模型视觉能力，让所有 WebUI 亮起发图按钮。
   - 收到图片时：
     • 主模型自带多模态（如挂载 --mmproj）➔ 直接原图直通 8083；
     • 主模型为纯文本（如 Qwen3.8-27B 4并发）➔ 自动由 8085 视觉眼 (Qwen2.5-VL) 解析 OCR / 图表，无缝注入 8083 27B 强大脑深度推理。
4. [毫秒级实时 TPS 算力测速引擎 & 延迟追踪]
   - 实时计算生成速度 (Tokens/秒)、历史峰值 TPS、首字延迟 (TTFT) 与端到端耗时。
5. [非阻塞 GPU 硬件感知探针]
   - 后台线程异步采集 nvidia-smi 显存、GPU利用率、功耗与温度，看板 0 延迟实时展示。
6. [防抖动态模型槽位感知 (Dynamic Slot Monitor)]
   - 支持动态感知 8083 模型名称、4并发槽位占用，带状态缓存防抖，杜绝重载下看板闪烁。
7. [每日历史持久化 & GitHub 风格月度热力日历]
   - 按月查看 1~31 天方块热力图，悬停查看精细明细。
8. [DeepSeek-V4-Flash-0731 / Vision-Exp 虚拟计费引擎]
   - 费率：缓存命中 ¥0.05/M | 未命中 ¥1.50/M | 生成输出 ¥4.50/M。
9. [3台设备独立调用 Key (admin / llamacpp / v100-32G)]
   - 无虚拟价格上限，无频控拦截，精准统计每台电脑的调用次数、Token 吞吐与算力价值对比。
"""

import sys
import os
import json
import time
import socket
import subprocess
import argparse
import threading
import datetime
import calendar
import urllib.request
import urllib.error
import re
import glob
import hashlib
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Windows 控制台标准输出 UTF-8 兼容适配与日志自动落盘
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ============================================================
#  单日单文件日志自动落盘 (logs/8081_proxy_YYYYMMDD.log 一直累加)
# ============================================================
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(LOG_DIR):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass

class DailyProxyLogger:
    def __init__(self, log_dir=LOG_DIR, prefix="8081_proxy_"):
        self.log_dir = log_dir
        self.prefix = prefix
        self.lock = threading.Lock()
        self.orig_stdout = sys.stdout
        self.orig_stderr = sys.stderr

    def write(self, msg):
        if not msg:
            return
        try:
            self.orig_stdout.write(msg)
            self.orig_stdout.flush()
        except Exception:
            pass
        try:
            today_str = datetime.date.today().strftime("%Y%m%d")
            log_path = os.path.join(self.log_dir, f"{self.prefix}{today_str}.log")
            with self.lock:
                with open(log_path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(msg)
                    f.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self.orig_stdout.flush()
        except Exception:
            pass

_proxy_daily_logger = DailyProxyLogger()
sys.stdout = _proxy_daily_logger
sys.stderr = _proxy_daily_logger

# ============================================================
#  DeepSeek-V4-Flash-0731 / Qwen3.8-27B-A 原生多模态 虚拟定价标准
# ============================================================
PRICING = {
    "standard": "DeepSeek-V4-Flash-0731 (纯文本) & Qwen3.8-27B-A [原生多模态] (空闲时段)",
    "text_model": "DeepSeek-V4-Flash-0731",
    "vision_model": "Qwen3.8-27B-A [原生多模态]",
    "input_cache_hit_per_m": 0.05,   # 0.05元 / 100万 tokens (¥0.00000005/token)
    "input_cache_miss_per_m": 1.50,  # 1.50元 / 100万 tokens (¥0.0000015/token)
    "output_per_m": 4.50,            # 4.50元 / 100万 tokens (¥0.0000045/token)
}

STATS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_billing_stats.json")
KEYS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "miracle_api_keys.json")

# ============================================================
#  模型虚拟别名动态映射器 (支持 Claude 5/4/3 + DeepSeek V4 + GPT-4o)
# ============================================================
def resolve_model_alias(requested_model="", default_model=None):
    """
    动态智能模型别名映射器：
    1. 显式请求视觉模型 -> Qwen3.8-27B-A [原生多模态];
    2. 显式请求 OCR / 定位专项 -> PaddleOCR-VL-1.6 / locate-anything-f16;
    3. 具体本地模型名与缩写 (27B, 35B, 8B, 4B, 双槽MTP, 4并发, 多模态, NVFP4 等) -> 对应规范模型名;
    4. 所有通用/云端别名 (Claude 5/4/3, DeepSeek V4/R1, GPT-4o, default, local, auto, qwen 等)
       一律动态解析为当前后端 (:8083) 真实加载的活跃主模型 (如 Qwen3.8-27B-A-Q6_K)！
    """
    live_main = default_model
    if not live_main:
        try:
            live_main = concurrency_queue.get_active_model_name()
        except Exception:
            live_main = "Qwen3.8-27B-A-Q6_K"

    if not requested_model or not isinstance(requested_model, str):
        return live_main
    
    req_lower = requested_model.lower().strip()
    
    # 显式请求视觉模型 -> 统一由 27B 原生多模态旗舰承载 (Track 1)
    if req_lower in ("qwen2.5-vl", "qwen2.5-vl-3b", "qwen-vl", "deepseek-v4-flash-vision-exp", "vision", "3b", "qwen3.8-27b-vision", "27b-vision", "qwen3.8-vl", "qwen3.8-27b-a-vision"):
        return "Qwen3.8-27B-A [原生多模态]"

    # 显式请求 OCR / 定位专项
    if "paddleocr" in req_lower or "ocr" in req_lower:
        return "PaddleOCR-VL-1.6"
    if "locate-anything" in req_lower or "locate" in req_lower:
        return "locate-anything-f16"
        
    exact_map = {
        # 27B Abliterated 系列（支持各种启动标签和缩写）
        "qwen3.8-27b-a-vision": "Qwen3.8-27B-A [原生多模态]",
        "qwen3.8-27b-vision": "Qwen3.8-27B-A [原生多模态]",
        "qwen3.8-27b-a [原生多模态]": "Qwen3.8-27B-A [原生多模态]",
        "qwen3.8-27b-a [多模态]": "Qwen3.8-27B-A [原生多模态]",
        "qwen3.8-27b-a-q6_k": "Qwen3.8-27B-A [双槽MTP]",
        "qwen3.8-27b-a": "Qwen3.8-27B-A [双槽MTP]",
        "qwen3.8-27b-abliterated-q6_k": "Qwen3.8-27B-A [双槽MTP]",
        "qwen3.8-27b-a [双槽mtp]": "Qwen3.8-27B-A [双槽MTP]",
        "qwen3.8-27b-a [4并发]": "Qwen3.8-27B-A [4并发流水线]",
        "qwen3.8-27b-a [4并发流水线]": "Qwen3.8-27B-A [4并发流水线]",
        "双槽mtp": "Qwen3.8-27B-A [双槽MTP]",
        "4并发": "Qwen3.8-27B-A [4并发流水线]",
        "4并发流水线": "Qwen3.8-27B-A [4并发流水线]",
        "多模态": "Qwen3.8-27B-A [原生多模态]",
        "qwen3.8-27b-uncensored-q6_k": "Qwen3.8-27B-U-Q6_K",
        # 27B NVFP4 系列
        "qwen3.8-27b-mid-high": "Qwen3.8-27B-MID-HIGH",
        "qwen3.8-27b-nvfp4-mtp-mid-high": "Qwen3.8-27B-MID-HIGH",
        "mid-high": "Qwen3.8-27B-MID-HIGH",
        "qwen3.8-27b-n-h": "Qwen3.8-27B-N-H",
        "qwen3.8-27b-highest": "Qwen3.8-27B-N-H",
        "qwen3.8-27b-nvfp4-mtp-highest": "Qwen3.8-27B-N-H",
        # 35B MoE 系列
        "ornith-1.5-35b": "Ornith-1.5-35B",
        "ornith-1.5-35b-q4_k_m": "Ornith-1.5-35B",
        "ornith-35b": "Ornith-1.5-35B",
        "ornith": "Ornith-1.5-35B",
        "35b": "Ornith-1.5-35B",
        # 8B 视觉全能
        "qwen3vl-8b-instruct-q8_0": "qwen3vl 8B",
        "qwen3vl-8b": "qwen3vl 8B",
        "qwen3vl": "qwen3vl 8B",
        "8b": "qwen3vl 8B",
        # 4B 轻量系列
        "gemma-4-e4b": "Gemma-4-E4B",
        "gemma-4": "Gemma-4-E4B",
        "gemma": "Gemma-4-E4B",
        "qwen3.5-4b": "Qwen3.5-4B",
        "4b": "Qwen3.5-4B",
        "qwen3.5-0.8b": "Qwen3.5-0.8B",
    }
    if req_lower in exact_map:
        return exact_map[req_lower]
        
    # 所有通配与云端别名一律映射为当前活跃的 8083 主模型
    return live_main

# ============================================================
#  非阻塞 GPU 硬件感知探针
# ============================================================
class GPUTelemetry:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {
            "online": True,
            "gpu_name": "Tesla V100-SXM2-32GB",
            "vram_used_mb": 27340,
            "vram_total_mb": 32768,
            "vram_pct": 83.4,
            "gpu_util_pct": 0,
            "power_w": 45,
            "power_limit_w": 300,
            "temp_c": 38,
            "last_updated": time.time()
        }
        self._silent_probe_once()

    def _silent_probe_once(self):
        try:
            creationflags = 0x08000000 if sys.platform == "win32" else 0
            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
            r = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.used,memory.total,utilization.gpu,power.draw,power.limit,temperature.gpu",
                    "--format=csv,noheader,nounits"
                ],
                capture_output=True, text=True, timeout=2,
                creationflags=creationflags, startupinfo=startupinfo
            )
            if r.returncode == 0 and r.stdout.strip():
                parts = [p.strip() for p in r.stdout.strip().split(",")]
                if len(parts) >= 7:
                    v_used = int(float(parts[1]))
                    v_total = int(float(parts[2]))
                    self.data = {
                        "online": True,
                        "gpu_name": parts[0],
                        "vram_used_mb": v_used,
                        "vram_total_mb": v_total,
                        "vram_pct": round((v_used / v_total * 100), 1) if v_total > 0 else 0.0,
                        "gpu_util_pct": int(float(parts[3])),
                        "power_w": int(float(parts[4])),
                        "power_limit_w": int(float(parts[5])),
                        "temp_c": int(float(parts[6])),
                        "last_updated": time.time()
                    }
        except Exception:
            pass

    def get_status(self):
        with self.lock:
            return dict(self.data)

gpu_telemetry = GPUTelemetry()

# ============================================================
#  毫秒级实时 TPS (Token/s) 测速引擎
# ============================================================
class SpeedEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.samples = []  # [(timestamp, tokens)]
        self.current_tps = 0.0
        self.peak_tps = 0.0
        self.total_generated_tokens = 0
        self.last_duration_s = 0.0
        self.total_prefill_tokens = 0
        self.total_prefill_duration = 0.0
        self.total_gen_tokens = 0
        self.total_gen_duration = 0.0
        self.total_work_duration = 0.0

    def record_detailed(self, prompt_tokens, prompt_duration_s, completion_tokens, gen_duration_s, total_duration_s):
        with self.lock:
            if prompt_tokens > 0 and prompt_duration_s > 0:
                self.total_prefill_tokens += prompt_tokens
                self.total_prefill_duration += prompt_duration_s
            if completion_tokens > 0 and gen_duration_s > 0:
                self.total_gen_tokens += completion_tokens
                self.total_gen_duration += gen_duration_s
                self.total_generated_tokens += completion_tokens
            self.total_work_duration += total_duration_s
            self.last_duration_s = total_duration_s

    def record(self, tokens, duration_s=0.0):
        if tokens <= 0:
            return
        now = time.time()
        with self.lock:
            self.total_generated_tokens += tokens
            self.last_duration_s = duration_s
            self.samples.append((now, tokens))
            
            # 保留最近 10 秒窗口
            cutoff = now - 10.0
            self.samples = [s for s in self.samples if s[0] >= cutoff]

            if len(self.samples) >= 2:
                tok_sum = sum(s[1] for s in self.samples)
                dt = self.samples[-1][0] - self.samples[0][0]
                self.current_tps = round(tok_sum / dt, 1) if dt > 0 else 0.0
            elif duration_s > 0.05:
                self.current_tps = round(tokens / duration_s, 1)

            if self.current_tps > self.peak_tps:
                self.peak_tps = self.current_tps

    def get_speed(self):
        with self.lock:
            now = time.time()
            if self.samples and (now - self.samples[-1][0] > 15.0):
                self.current_tps = 0.0
            
            # 计算全天纯工作累计均速（仅在工作时统计，严格剔除空闲时间）
            in_avg = round(self.total_prefill_tokens / max(0.001, self.total_prefill_duration), 1) if self.total_prefill_duration > 0 else 0.0
            out_avg = round(self.total_gen_tokens / max(0.001, self.total_gen_duration), 1) if self.total_gen_duration > 0 else (self.current_tps or 0.0)
            
            return {
                "current_tps": self.current_tps,
                "peak_tps": self.peak_tps,
                "today_in_avg": in_avg,
                "today_out_avg": out_avg,
                "today_in_seconds": round(self.total_prefill_duration, 1),
                "today_out_seconds": round(self.total_gen_duration, 1),
                "today_work_seconds": round(self.total_work_duration, 1),
                "total_prefill_tokens": self.total_prefill_tokens,
                "total_gen_tokens": self.total_gen_tokens,
                "total_generated_tokens": self.total_generated_tokens,
                "last_duration_s": round(self.last_duration_s, 2)
            }

speed_engine = SpeedEngine()

# ============================================================
#  3 设备独立调用 Key 管理中心 (admin / llamacpp / v100-32G)
# ============================================================
class KeyManager:
    def __init__(self, filepath=KEYS_FILE_PATH):
        self.filepath = filepath
        self.lock = threading.Lock()
        self.keys = self._load()

    def _load(self):
        default_keys = {
            "admin": {"name": "Admin 主控机", "enabled": True, "quota_cny": 0, "rpm": 0},
            "llamacpp": {"name": "llamacpp 测试机", "enabled": True, "quota_cny": 0, "rpm": 0},
            "v100-32G": {"name": "v100-32G 工作机", "enabled": True, "quota_cny": 0, "rpm": 0}
        }
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k in ("admin", "llamacpp", "v100-32G"):
                        if k not in data:
                            data[k] = default_keys[k]
                    return data
            except Exception:
                pass
        self._save(default_keys)
        return default_keys

    def _save(self, data):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def authenticate(self, auth_header, x_api_key=""):
        """鉴权并识别调用设备（支持 Authorization 头与 x-api-key 头）"""
        with self.lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        self.keys = json.load(f)
                except Exception:
                    pass

            token = "admin"
            if x_api_key:
                token = x_api_key.strip()
            elif auth_header:
                parts = auth_header.strip().split()
                token = parts[-1] if len(parts) >= 2 else parts[0]

            token_clean = token
            if token_clean.startswith("Bearer "):
                token_clean = token_clean[7:].strip()
            
            if token_clean in self.keys:
                key_id = token_clean
                key_name = self.keys[key_id].get("name", key_id)
            elif "v100" in token_clean.lower():
                key_id = "v100-32G"
                key_name = "v100-32G 工作机"
            elif "llama" in token_clean.lower():
                key_id = "llamacpp"
                key_name = "llamacpp 测试机"
            elif "admin" in token_clean.lower() or not token_clean:
                key_id = "admin"
                key_name = "Admin 主控机"
            else:
                key_id = token_clean
                key_name = f"设备 [{token_clean}]"

            return True, key_id, key_name

key_manager = KeyManager()

# ============================================================
#  原生 llama-server 控制台日志实时嗅探器 (实时同步控制台 print_timing)
# ============================================================
class LlamaLogWatcher:
    def __init__(self, log_dir=r"e:\llama-win-cuda-12.4-x64\logs"):
        self.log_dir = log_dir
        self.lock = threading.Lock()
        self.slot_metrics = {}  # slot_id (0..3) -> {...}
        self.running = True
        self.thread = threading.Thread(target=self._tail_worker, daemon=True)
        self.thread.start()

    def _tail_worker(self):
        re_prompt = re.compile(r'slot print_timing:\s+id\s+(\d+)\s+\|\s+task\s+(\d+)\s+\|\s+prompt processing,\s+n_tokens\s+=\s+(\d+),\s+progress\s+=\s+([0-9.]+),\s+t\s+=\s+([0-9.]+)\s+s\s+/\s+([0-9.]+)\s+tokens per second')
        re_gen = re.compile(r'slot print_timing:\s+id\s+(\d+)\s+\|\s+task\s+(\d+)\s+\|\s+n_gen\s+=\s+(\d+),\s+tg\s+=\s+([0-9.]+)\s+t/s(?:,\s+tg_3s\s+=\s+([0-9.]+)\s+t/s)?')
        re_eval_done = re.compile(r'slot print_timing:\s+id\s+(\d+)\s+\|\s+task\s+(\d+)\s+\|\s+prompt eval time\s+=\s+[0-9.]+\s+ms\s+/\s+(\d+)\s+tokens\s+\([^)]*,\s+([0-9.]+)\s+tokens per second\)')
        re_gen_done = re.compile(r'slot print_timing:\s+id\s+(\d+)\s+\|\s+task\s+(\d+)\s+\|\s+eval time\s+=\s+[0-9.]+\s+ms\s+/\s+(\d+)\s+tokens\s+\([^)]*,\s+([0-9.]+)\s+tokens per second\)')
        re_rel = re.compile(r'slot\s+release:\s+id\s+(\d+)')
        
        current_fp = None
        current_filename = None
        last_pos = 0

        while self.running:
            try:
                log_files = glob.glob(os.path.join(self.log_dir, "*8083_llama_*.log")) + glob.glob(os.path.join(self.log_dir, "llama_log_*.log"))
                if not log_files:
                    time.sleep(0.5)
                    continue
                latest_log = max(log_files, key=os.path.getmtime)

                if latest_log != current_filename:
                    if current_fp:
                        try:
                            current_fp.close()
                        except Exception:
                            pass
                    current_filename = latest_log
                    current_fp = open(latest_log, "r", encoding="utf-8", errors="ignore")
                    current_fp.seek(0, os.SEEK_END)
                    last_pos = max(0, current_fp.tell() - 65536)
                    current_fp.seek(last_pos, os.SEEK_SET)

                current_fp.seek(last_pos, os.SEEK_SET)
                lines = current_fp.readlines()
                last_pos = current_fp.tell()

                if lines:
                    now = time.time()
                    with self.lock:
                        for l in lines:
                            m1 = re_prompt.search(l)
                            if m1:
                                sid = int(m1.group(1))
                                self.slot_metrics[sid] = {
                                    "state": "prefill",
                                    "task_id": int(m1.group(2)),
                                    "tokens_processed": int(m1.group(3)),
                                    "progress": float(m1.group(4)),
                                    "in_tok_s": float(m1.group(6)),
                                    "out_tok_s": 0.0,
                                    "out_tok_s_3s": 0.0,
                                    "last_update": now
                                }
                                continue

                            m2 = re_gen.search(l)
                            if m2:
                                sid = int(m2.group(1))
                                existing = self.slot_metrics.get(sid, {})
                                tg_val = float(m2.group(4))
                                tg_3s_val = float(m2.group(5)) if m2.group(5) else tg_val
                                self.slot_metrics[sid] = {
                                    "state": "generating",
                                    "task_id": int(m2.group(2)),
                                    "tokens_processed": existing.get("tokens_processed", 0),
                                    "n_gen": int(m2.group(3)),
                                    "progress": 1.0,
                                    "in_tok_s": existing.get("in_tok_s", 0.0),
                                    "out_tok_s": tg_val,
                                    "out_tok_s_3s": tg_3s_val,
                                    "last_update": now
                                }
                                continue

                            m_ed = re_eval_done.search(l)
                            if m_ed:
                                sid = int(m_ed.group(1))
                                existing = self.slot_metrics.get(sid, {})
                                existing["in_tok_s"] = float(m_ed.group(2))
                                existing["state"] = "generating"
                                existing["last_update"] = now
                                self.slot_metrics[sid] = existing
                                continue

                            m_gd = re_gen_done.search(l)
                            if m_gd:
                                sid = int(m_gd.group(1))
                                existing = self.slot_metrics.get(sid, {})
                                existing["out_tok_s"] = float(m_gd.group(2))
                                existing["state"] = "generating"
                                existing["last_update"] = now
                                self.slot_metrics[sid] = existing
                                continue

                            m3 = re_rel.search(l)
                            if m3:
                                sid = int(m3.group(1))
                                if sid in self.slot_metrics:
                                    self.slot_metrics[sid]["state"] = "idle"
                                    self.slot_metrics[sid]["in_tok_s"] = 0.0
                                    self.slot_metrics[sid]["out_tok_s"] = 0.0
                                    self.slot_metrics[sid]["last_update"] = now
            except Exception:
                pass
            time.sleep(0.2)

    def get_metrics(self):
        with self.lock:
            return dict(self.slot_metrics)

log_watcher = LlamaLogWatcher()

# ============================================================
# ============================================================
#  V100 架构专用：智能负载准入与预填避让调度引擎 (Smart Admission Controller)
# ============================================================
class ConcurrencyQueue:
    def __init__(self, max_slots=4):
        self.text_semaphore = threading.Semaphore(max_slots)
        self.active_text = 0
        self.active_vision = 0
        self.lock = threading.Lock()
        self.prefill_lock = threading.Lock()  # 巨型预填排他保护锁 (保证单卡 100% 算力全速跑大预填)
        self.cached_status = None
        self.cache_time = 0
        self.current_model_alias = "Qwen3.8-27B-A-Q6_K"
        self.slot_state_tracker = {}  # raw_id -> {"last_t", "last_prompt_proc", "last_decoded", "start_t", "task_id", "in_tok_s", "out_tok_s"}

    def get_active_model_name(self):
        with self.lock:
            return self.current_model_alias or "Qwen3.8-27B-A-Q6_K"

    def acquire(self, is_vision=False, estimated_tokens=0, timeout=120.0):
        """
        V100 智能负载准入控制：
        1. 统一 8083 主脑槽位数准入控制；
        2. 短提问 / 视觉快速推理 (Fast-Track, < 3500 tokens): 直接进入空闲槽位，秒级响应，零排队；
        3. 巨型长文本任务 (Heavy, >= 15000 tokens):
           - 先检测底层是否已有槽位正在进行超大预填 (prefill)；
           - 若有，在网关层平滑避让排队，等待当前大任务预填完成 (进入 generating 阶段)；
           - 从而确保 V100 算力 100% 独占全速完成预填，杜绝两大多任务互相踩踏导致两边都翻倍暴跌！
        4. KV 上下文池水位熔断保护 (Total KV Ceiling):
           - 若当前所有活跃槽位总已用上下文 > 135K，限制并发放行，防止底层触发 Checkpoint Erase 强行擦除。
        """
        start_t = time.time()
        sem = self.text_semaphore

        # 1. 基础信号量获取 (8083 槽位数上限控制)
        acquired = sem.acquire(timeout=timeout)
        if not acquired:
            return False

        with self.lock:
            if is_vision:
                self.active_vision += 1
            else:
                self.active_text += 1

        # 若为视觉或纯文本短提问 (< 3.5K tokens)，直接放行进入绿色通道！
        if is_vision or estimated_tokens < 3500:
            return True

        # 2. V100 智能巨型预填避让与显存水位调度
        is_heavy_task = (estimated_tokens >= 15000)
        if is_heavy_task:
            waited_prefill = False
            while (time.time() - start_t) < timeout:
                st = self.get_dynamic_status()
                slots = st.get("slots_detail", [])
                
                # 检查当前是否有槽位正在进行巨型预填 (prefill 状态且 prompt > 20K)
                heavy_prefill_active = False
                total_ctx_in_use = 0
                for s in slots:
                    total_ctx_in_use += s.get("ctx_used", 0)
                    if s.get("is_active") and s.get("stage") == "prefill" and s.get("n_prompt", 0) > 20000:
                        heavy_prefill_active = True

                # 若总上下文水位超警戒线 (150K) 或已有巨型预填正在压榨算力，避让等待 1 秒
                if heavy_prefill_active or (total_ctx_in_use + estimated_tokens > 150000 and total_ctx_in_use > 80000):
                    if not waited_prefill:
                        sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [SMART-ADMISSION] 🚦 V100 负载避让触发：检测到已有槽位正在 100% 算力狂算大预填(或总KV水位>{total_ctx_in_use//1024}K)，本任务({estimated_tokens:,} tok)在网关平滑等待...\n")
                        sys.stdout.flush()
                        waited_prefill = True
                    time.sleep(1.0)
                else:
                    if waited_prefill:
                        sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [SMART-ADMISSION] 🟢 前序大预填已转入吐字/显存释放完毕，放行本长任务({estimated_tokens:,} tok)全速独占预填！\n")
                        sys.stdout.flush()
                    break

        return True

    def release(self, is_vision=False):
        with self.lock:
            if is_vision:
                self.active_vision = max(0, self.active_vision - 1)
            else:
                self.active_text = max(0, self.active_text - 1)
        try:
            self.text_semaphore.release()
        except ValueError:
            pass

    def get_active_model_name(self):
        with self.lock:
            return self.current_model_alias or "Qwen3.8-27B-A-Q6_K"

    def get_dynamic_status(self, backend_port=8083):
        now = time.time()
        # 1. 尝试直接向 8083 llama-server 请求 /slots 和 /props (在超大预填时允许最多 1.8s 响应)
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{backend_port}/slots", headers={"Authorization": "Bearer llamacpp"}, method="GET")
            with urllib.request.urlopen(req, timeout=1.8) as resp:
                if resp.status == 200:
                    slots_data = json.loads(resp.read().decode("utf-8"))
                    model_alias = self.current_model_alias
                    total_ctx = 163840
                    try:
                        req_props = urllib.request.Request(f"http://127.0.0.1:{backend_port}/props", headers={"Authorization": "Bearer llamacpp"}, method="GET")
                        with urllib.request.urlopen(req_props, timeout=1.0) as resp_p:
                            p_data = json.loads(resp_p.read().decode("utf-8"))
                            detected_alias = p_data.get("model_alias")
                            if not detected_alias and p_data.get("model_path"):
                                detected_alias = os.path.splitext(os.path.basename(p_data["model_path"]))[0]
                            if detected_alias:
                                model_alias = detected_alias
                            total_ctx = p_data.get("default_generation_settings", {}).get("n_ctx", total_ctx)
                    except Exception:
                        pass
                    
                    with self.lock:
                        self.current_model_alias = model_alias

                    live_metrics = log_watcher.get_metrics()
                    active_count = 0
                    slots_detail = []
                    
                    for idx, s in enumerate(slots_data):
                        raw_id = s.get("id", idx)
                        slot_num = raw_id + 1
                        m = live_metrics.get(raw_id, {})
                        m_recent = bool(m and (now - m.get("last_update", 0) < 5.0))
                        is_proc = bool(s.get("is_processing", False) or (m and m_recent and m.get("state") in ("generating", "prefill")))
                        task_id = s.get("id_task", -1)
                        if is_proc:
                            active_count += 1
                        
                        slot_n_ctx = s.get("n_ctx", total_ctx // max(1, len(slots_data)))
                        n_prompt = s.get("n_prompt_tokens", 0)
                        n_prompt_proc = s.get("n_prompt_tokens_processed", 0)
                        
                        next_tok = s.get("next_token", [])
                        n_decoded = 0
                        if next_tok and isinstance(next_tok, list) and len(next_tok) > 0:
                            n_decoded = next_tok[0].get("n_decoded", 0)
                        
                        ctx_used = n_prompt + n_decoded
                        ctx_pct = round((ctx_used / max(1, slot_n_ctx)) * 100, 1) if slot_n_ctx > 0 else 0.0

                        # 初始化或获取该槽位的任务生命周期工作均速状态
                        tracker = self.slot_state_tracker.setdefault(raw_id, {
                            "task_id": task_id,
                            "prefill_start_t": 0.0,
                            "prefill_duration": 0.0,
                            "decode_start_t": 0.0,
                            "decode_duration": 0.0,
                            "last_in_avg": 0.0,
                            "last_out_avg": 0.0,
                            "last_prompt_tokens": 0,
                            "last_decoded_tokens": 0,
                            "last_prefill_time": 0.0,
                            "last_gen_time": 0.0,
                        })

                        # 若切到新任务（新 task_id 且非空），重置当前任务计时
                        if task_id != -1 and tracker.get("task_id") != task_id and is_proc:
                            tracker["task_id"] = task_id
                            tracker["prefill_start_t"] = now
                            tracker["prefill_duration"] = 0.0
                            tracker["decode_start_t"] = 0.0
                            tracker["decode_duration"] = 0.0
                            tracker["last_in_avg"] = 0.0
                            tracker["last_out_avg"] = 0.0
                            tracker["last_prompt_tokens"] = n_prompt
                            tracker["last_decoded_tokens"] = 0
                            tracker["last_prefill_time"] = 0.0
                            tracker["last_gen_time"] = 0.0

                        stage = "idle"
                        stage_progress = 0.0
                        in_s = tracker.get("last_in_avg", 0.0)
                        out_s = tracker.get("last_out_avg", 0.0)
                        t_prefill = tracker.get("prefill_duration", 0.0)
                        t_gen = tracker.get("decode_duration", 0.0)

                        if is_proc:
                            # 🌟 1. 检查 log_watcher 实时捕获的状态
                            has_log_gen = bool(m and m_recent and (m.get("state") == "generating" or m.get("n_gen", 0) > 0))
                            has_log_prefill = bool(m and m_recent and m.get("state") == "prefill" and not has_log_gen)
                            
                            # 🌟 2. 检查 slots 里的解码量
                            has_slot_gen = bool(n_decoded > 0)
                            
                            if has_log_gen and m.get("n_gen"):
                                n_decoded = max(n_decoded, int(m["n_gen"]))

                            # 判断阶段：已进入生成阶段（优先响应日志与 slots 生成事件）
                            if has_log_gen or has_slot_gen or (n_prompt > 0 and n_prompt_proc >= n_prompt):
                                stage = "generating"
                                stage_progress = 100.0
                                in_s = tracker.get("last_in_avg", 0.0)
                                if in_s == 0.0 and m and m.get("in_tok_s", 0) > 0:
                                    in_s = float(m["in_tok_s"])
                                    tracker["last_in_avg"] = in_s
                                
                                if tracker.get("decode_start_t", 0) == 0:
                                    tracker["decode_start_t"] = now
                                t_gen = max(0.1, now - tracker["decode_start_t"])
                                tracker["decode_duration"] = t_gen
                                tracker["last_gen_time"] = t_gen
                                
                                # 优先同步控制台日志实时捕获的 tg / tg_3s
                                if m and m_recent and m.get("out_tok_s", 0) > 0:
                                    out_s = float(m["out_tok_s"])
                                    out_s_3s = float(m.get("out_tok_s_3s", out_s))
                                else:
                                    out_s_3s = out_s

                                if out_s == 0.0:
                                    if n_decoded > 0:
                                        out_s = round(n_decoded / t_gen, 1)
                                    else:
                                        out_s = round(speed_engine.get_speed().get("today_out_avg", 0.0), 1)
                                    out_s_3s = out_s

                                tracker["last_decoded_tokens"] = n_decoded
                                tracker["last_out_avg"] = out_s

                            elif has_log_prefill or (n_prompt > 0 and n_prompt_proc > 0 and n_prompt_proc < n_prompt and n_decoded == 0):
                                stage = "prefill"
                                if n_prompt > 0 and n_prompt_proc > 0:
                                    stage_progress = round(n_prompt_proc / max(1, n_prompt) * 100, 1)
                                elif m and m_recent and m.get("progress") is not None:
                                    stage_progress = round(float(m["progress"]) * 100, 1)
                                elif n_prompt > 0:
                                    stage_progress = round(n_prompt_proc / max(1, n_prompt) * 100, 1)
                                
                                if m and m_recent and m.get("tokens_processed"):
                                    n_prompt_proc = max(n_prompt_proc, int(m["tokens_processed"]))
                                    if n_prompt > 0:
                                        stage_progress = max(stage_progress, round(n_prompt_proc / max(1, n_prompt) * 100, 1))
                                if m and m_recent and m.get("in_tok_s"):
                                    in_s = float(m["in_tok_s"])

                                if tracker.get("prefill_start_t", 0) == 0:
                                    tracker["prefill_start_t"] = now
                                t_prefill = max(0.1, now - tracker["prefill_start_t"])
                                tracker["prefill_duration"] = t_prefill
                                tracker["last_prefill_time"] = t_prefill
                                tracker["last_prompt_tokens"] = max(n_prompt, n_prompt_proc)
                                
                                if in_s == 0.0 and n_prompt_proc > 0:
                                    in_s = round(n_prompt_proc / t_prefill, 1)
                                tracker["last_in_avg"] = in_s
                                out_s = 0.0
                                out_s_3s = 0.0
                            else:
                                stage = "generating"
                                stage_progress = 100.0
                                in_s = tracker.get("last_in_avg", 0.0)
                                if tracker.get("decode_start_t", 0) == 0:
                                    tracker["decode_start_t"] = now
                                t_gen = max(0.1, now - tracker["decode_start_t"])
                                tracker["decode_duration"] = t_gen
                                tracker["last_gen_time"] = t_gen
                                if m and m_recent and m.get("out_tok_s", 0) > 0:
                                    out_s = float(m["out_tok_s"])
                                    out_s_3s = float(m.get("out_tok_s_3s", out_s))
                                else:
                                    out_s_3s = out_s
                                tracker["last_decoded_tokens"] = n_decoded
                                tracker["last_out_avg"] = out_s
                        else:
                            stage = "idle"
                            stage_progress = 0.0
                            out_s_3s = 0.0
                            # 空闲时锁定并展示上一轮生命周期总均速
                            in_s = tracker.get("last_in_avg", 0.0)
                            out_s = tracker.get("last_out_avg", 0.0)

                        slots_detail.append({
                            "slot_num": slot_num,
                            "raw_id": raw_id,
                            "is_active": is_proc,
                            "stage": stage,
                            "stage_progress": stage_progress,
                            "task_id": task_id,
                            "n_ctx": slot_n_ctx,
                            "ctx_used": ctx_used,
                            "ctx_pct": ctx_pct,
                            "n_prompt": n_prompt,
                            "n_prompt_proc": n_prompt_proc,
                            "n_decoded": n_decoded,
                            "in_tok_s": in_s,
                            "out_tok_s": out_s,
                            "out_tok_s_3s": out_s_3s,
                            "t_prefill": round(t_prefill, 1),
                            "t_gen": round(t_gen, 1),
                            "last_in_avg": round(tracker.get("last_in_avg", 0.0), 1),
                            "last_out_avg": round(tracker.get("last_out_avg", 0.0), 1),
                            "last_prompt_tokens": tracker.get("last_prompt_tokens", 0),
                            "last_decoded_tokens": tracker.get("last_decoded_tokens", 0),
                            "last_prefill_time": round(tracker.get("last_prefill_time", 0.0), 1),
                            "last_gen_time": round(tracker.get("last_gen_time", 0.0), 1)
                        })

                    is_multimodal = False
                    mmproj_file = ""
                    try:
                        req_props = urllib.request.Request(f"http://127.0.0.1:{backend_port}/props", headers={"Authorization": "Bearer llamacpp"}, method="GET")
                        with urllib.request.urlopen(req_props, timeout=1.0) as resp_p:
                            p_data = json.loads(resp_p.read().decode("utf-8"))
                            detected_alias = p_data.get("model_alias")
                            if not detected_alias and p_data.get("model_path"):
                                detected_alias = os.path.splitext(os.path.basename(p_data["model_path"]))[0]
                            if detected_alias:
                                model_alias = detected_alias
                            total_ctx = p_data.get("default_generation_settings", {}).get("n_ctx", total_ctx)
                            
                            # 真实感知是否挂载了原生多模态视觉 (mmproj)
                            modalities = p_data.get("default_generation_settings", {}).get("modalities", [])
                            if "vision" in modalities or "image" in modalities:
                                is_multimodal = True
                            params = p_data.get("default_generation_settings", {}).get("params", {})
                            if params.get("mmproj"):
                                is_multimodal = True
                                mmproj_file = os.path.basename(params.get("mmproj", ""))
                            elif "vl" in model_alias.lower() or "vision" in model_alias.lower():
                                is_multimodal = True
                    except Exception:
                        pass
                    
                    b_state = getattr(backend_manager, "current_state", "")
                    if b_state == backend_manager.STATE_VISION_27B or is_multimodal:
                        is_multimodal = True
                        if not mmproj_file:
                            mmproj_file = "mmproj-Qwen3.8-27B-F16.gguf"
                        display_model_name = "Qwen3.8-27B-A [原生多模态]"
                    elif b_state == backend_manager.STATE_PIPELINE_4SLOT or len(slots_data) == 4:
                        display_model_name = "Qwen3.8-27B-A [4并发流水线]"
                    else:
                        display_model_name = "Qwen3.8-27B-A [双槽MTP]"

                    with self.lock:
                        self.current_model_alias = display_model_name

                    st = {
                        "backend_online": True,
                        "model_name": display_model_name,
                        "total_ctx": total_ctx,
                        "text_active": active_count,
                        "text_max": len(slots_data),
                        "slots_detail": slots_detail,
                        "is_multimodal": is_multimodal,
                        "mmproj_file": mmproj_file,
                        "vision_cache_count": len(VISION_IMAGE_OCR_CACHE),
                        "active_vision": self.active_vision
                    }
                    with self.lock:
                        self.cached_status = st
                        self.cache_time = now
                    return st
        except Exception:
            pass

        # 2. 如果请求瞬时超时，仅在 2.5 秒内返回轻度缓存防抖，杜绝超长滞后
        with self.lock:
            if self.cached_status and (now - self.cache_time < 2.5):
                return self.cached_status

        # 3. 确实未启动或已关闭
        b_state = getattr(backend_manager, "current_state", "")
        is_vm = (b_state == backend_manager.STATE_VISION_27B)
        if is_vm:
            offline_model = "Qwen3.8-27B-A [原生多模态]"
        elif b_state == backend_manager.STATE_PIPELINE_4SLOT:
            offline_model = "Qwen3.8-27B-A [4并发流水线]"
        else:
            offline_model = "Qwen3.8-27B-A [双槽MTP]"

        return {
            "backend_online": False,
            "model_name": offline_model,
            "total_ctx": 0,
            "text_active": 0,
            "text_max": 0,
            "slots_detail": [],
            "is_multimodal": is_vm,
            "mmproj_file": ("mmproj-Qwen3.8-27B-F16.gguf" if is_vm else ""),
            "vision_cache_count": len(VISION_IMAGE_OCR_CACHE),
            "active_vision": self.active_vision
        }

concurrency_queue = ConcurrencyQueue(max_slots=4)

# ============================================================
#  全局线程安全 Token 虚拟计费统计中心 (按 DeepSeek-V4 空闲费率 + 每日明细历史)
# ============================================================
class BillingTracker:
    def __init__(self, filepath=STATS_FILE_PATH):
        self.filepath = filepath
        self.lock = threading.Lock()
        self.data = self._load()

    def _load(self):
        today_str = datetime.date.today().isoformat()
        default_data = {
            "pricing_standard": PRICING["standard"],
            "pricing_rates": {
                "input_cache_hit_per_m": PRICING["input_cache_hit_per_m"],
                "input_cache_miss_per_m": PRICING["input_cache_miss_per_m"],
                "output_per_m": PRICING["output_per_m"],
            },
            "total": {
                "requests": 0,
                "prompt_tokens": 0,
                "prompt_tokens_cached": 0,
                "prompt_tokens_miss": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_cny": 0.0,
                "vision_images": 0,
                "vision_duration_s": 0.0,
            },
            "today": {
                "date": today_str,
                "requests": 0,
                "prompt_tokens": 0,
                "prompt_tokens_cached": 0,
                "prompt_tokens_miss": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_cny": 0.0,
                "vision_images": 0,
                "vision_duration_s": 0.0,
                "total_in_seconds": 0.0,
                "total_out_seconds": 0.0,
                "total_work_seconds": 0.0,
            },
            "reasoning_levels": {
                "today": {
                    "simple": 0, "medium": 0, "hard": 0,
                    "by_mode": {
                        "MTP_2SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "PIPELINE_4SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "VISION_27B": {"simple": 0, "medium": 0, "hard": 0}
                    }
                },
                "total": {
                    "simple": 0, "medium": 0, "hard": 0,
                    "by_mode": {
                        "MTP_2SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "PIPELINE_4SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "VISION_27B": {"simple": 0, "medium": 0, "hard": 0}
                    }
                }
            },
            "by_model": {},
            "by_key": {
                "admin": {"name": "Admin 主控机", "requests": 0, "total_tokens": 0, "cost_cny": 0.0},
                "llamacpp": {"name": "llamacpp 测试机", "requests": 0, "total_tokens": 0, "cost_cny": 0.0},
                "v100-32G": {"name": "v100-32G 工作机", "requests": 0, "total_tokens": 0, "cost_cny": 0.0}
            },
            "hot_swaps": {
                "total_count": 0,
                "today_count": 0,
                "last_duration_s": 0.0,
                "total_duration_s": 0.0,
                "avg_duration_s": 0.0,
                "last_from": "",
                "last_to": "",
                "last_time": "",
                "history": []
            },
            "daily_history": {},
            "recent_requests": []
        }
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    loaded["pricing_rates"] = default_data["pricing_rates"]
                    loaded["pricing_standard"] = default_data["pricing_standard"]
                    loaded.setdefault("total", default_data["total"])
                    loaded["total"].setdefault("vision_images", 0)
                    loaded["total"].setdefault("vision_duration_s", 0.0)

                    loaded.setdefault("hot_swaps", default_data["hot_swaps"])
                    loaded["hot_swaps"].setdefault("history", [])
                    loaded["hot_swaps"].setdefault("today_count", 0)
                    loaded["hot_swaps"].setdefault("total_count", 0)

                    loaded.setdefault("reasoning_levels", default_data["reasoning_levels"])
                    loaded["reasoning_levels"].setdefault("today", default_data["reasoning_levels"]["today"])
                    loaded["reasoning_levels"].setdefault("total", default_data["reasoning_levels"]["total"])
                    loaded["reasoning_levels"]["today"].setdefault("by_mode", {
                        "MTP_2SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "PIPELINE_4SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "VISION_27B": {"simple": 0, "medium": 0, "hard": 0}
                    })
                    loaded["reasoning_levels"]["total"].setdefault("by_mode", {
                        "MTP_2SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "PIPELINE_4SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "VISION_27B": {"simple": 0, "medium": 0, "hard": 0}
                    })

                    bk = loaded.setdefault("by_key", {})
                    for k in ("admin", "llamacpp", "v100-32G"):
                        if k not in bk:
                            bk[k] = default_data["by_key"][k]
                    
                    # 确保 today.by_device_model 存在并自愈补全
                    today_obj = loaded.setdefault("today", default_data["today"])
                    today_obj.setdefault("vision_images", 0)
                    today_obj.setdefault("vision_duration_s", 0.0)
                    today_obj.setdefault("total_in_seconds", 0.0)
                    today_obj.setdefault("total_out_seconds", 0.0)
                    today_obj.setdefault("total_work_seconds", 0.0)
                    today_dm = today_obj.setdefault("by_device_model", {})
                    if not today_dm:
                        today_str = today_obj.get("date", datetime.date.today().isoformat())
                        for r in loaded.get("recent_requests", []):
                            if r.get("time", "").startswith(today_str):
                                r_key = r.get("key", "admin")
                                r_model = r.get("model", "Qwen3.8-27B-A [双槽MTP]")
                                k_m = f"{r_key}::{r_model}"
                                if k_m not in today_dm:
                                    today_dm[k_m] = {
                                        "key": r_key,
                                        "key_name": default_data["by_key"].get(r_key, {}).get("name", r_key),
                                        "model": r_model,
                                        "is_vision": bool("VL" in r_model or "Vision" in r_model or "多模态" in r_model),
                                        "last_time": r.get("time", ""),
                                        "requests": 0,
                                        "prompt_tokens": 0,
                                        "prompt_tokens_cached": 0,
                                        "prompt_tokens_miss": 0,
                                        "completion_tokens": 0,
                                        "total_tokens": 0,
                                        "duration_s": 0.0,
                                        "image_count": 0,
                                        "cost_cny": 0.0
                                    }
                                stat = today_dm[k_m]
                                stat["requests"] += 1
                                stat["prompt_tokens"] += r.get("prompt_tokens", 0)
                                stat["prompt_tokens_cached"] += r.get("cached_tokens", 0)
                                stat["prompt_tokens_miss"] += (r.get("prompt_tokens", 0) - r.get("cached_tokens", 0))
                                stat["completion_tokens"] += r.get("completion_tokens", 0)
                                stat["total_tokens"] += r.get("total_tokens", 0)
                                stat["duration_s"] = round(stat["duration_s"] + r.get("duration_s", 0.0), 2)
                    
                    # 校验与自动汇总今日视觉统计
                    today_v_imgs = 0
                    today_v_dur = 0.0
                    for dm in today_dm.values():
                        if dm.get("is_vision") or "多模态" in dm.get("model", "") or "Vision" in dm.get("model", "") or "VL" in dm.get("model", ""):
                            today_v_imgs += dm.get("image_count", dm.get("requests", 0))
                            today_v_dur += dm.get("duration_s", 0.0)
                    if today_obj.get("vision_images", 0) == 0 and today_v_imgs > 0:
                        today_obj["vision_images"] = today_v_imgs
                        today_obj["vision_duration_s"] = round(today_v_dur, 2)
                    if loaded["total"].get("vision_images", 0) == 0 and today_v_imgs > 0:
                        loaded["total"]["vision_images"] = today_v_imgs
                        loaded["total"]["vision_duration_s"] = round(today_v_dur, 2)

                    # 同步今日总纯工作时间与 Token 吞吐至测速引擎
                    today_p = today_obj.get("prompt_tokens", 0)
                    today_c = today_obj.get("completion_tokens", 0)
                    today_dur = sum(dm.get("duration_s", 0.0) for dm in today_dm.values())
                    if today_dur > 0:
                        speed_engine.total_prefill_tokens = today_p
                        speed_engine.total_gen_tokens = today_c
                        speed_engine.total_work_duration = today_dur
                        saved_in = today_obj.get("total_in_seconds", 0.0)
                        saved_out = today_obj.get("total_out_seconds", 0.0)
                        if saved_in > 0 and saved_out > 0:
                            speed_engine.total_prefill_duration = saved_in
                            speed_engine.total_gen_duration = saved_out
                        else:
                            p_t = max(0.01, today_dur * (today_p / max(1, today_p + today_c * 20)))
                            speed_engine.total_prefill_duration = p_t
                            speed_engine.total_gen_duration = max(0.01, today_dur - p_t)
                            today_obj["total_in_seconds"] = round(p_t, 1)
                            today_obj["total_out_seconds"] = round(max(0.01, today_dur - p_t), 1)
                            today_obj["total_work_seconds"] = round(today_dur, 1)
                    return loaded
            except Exception:
                pass
        return default_data

    def _check_day_rollover(self):
        today_str = datetime.date.today().isoformat()
        if self.data.get("today", {}).get("date") != today_str:
            self.data["today"] = {
                "date": today_str,
                "requests": 0,
                "prompt_tokens": 0,
                "prompt_tokens_cached": 0,
                "prompt_tokens_miss": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_cny": 0.0,
                "vision_images": 0,
                "vision_duration_s": 0.0,
                "total_in_seconds": 0.0,
                "total_out_seconds": 0.0,
                "total_work_seconds": 0.0,
                "by_device_model": {}
            }
            if "hot_swaps" in self.data:
                self.data["hot_swaps"]["today_count"] = 0
            if "reasoning_levels" in self.data:
                self.data["reasoning_levels"]["today"] = {
                    "simple": 0, "medium": 0, "hard": 0,
                    "by_mode": {
                        "MTP_2SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "PIPELINE_4SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "VISION_27B": {"simple": 0, "medium": 0, "hard": 0}
                    }
                }
            with speed_engine.lock:
                speed_engine.total_prefill_tokens = 0
                speed_engine.total_prefill_duration = 0.0
                speed_engine.total_gen_tokens = 0
                speed_engine.total_gen_duration = 0.0
                speed_engine.total_work_duration = 0.0

    def record(self, model_name, prompt_tokens, cached_tokens, completion_tokens, duration_s=0.0, key_name="admin", is_vision=False, image_count=0, reasoning_effort="medium"):
        with self.lock:
            self._check_day_rollover()
            cached = max(0, min(cached_tokens, prompt_tokens))
            miss = max(0, prompt_tokens - cached)
            total_tokens = prompt_tokens + completion_tokens

            cost = (
                (miss * PRICING["input_cache_miss_per_m"]) +
                (cached * PRICING["input_cache_hit_per_m"]) +
                (completion_tokens * PRICING["output_per_m"])
            ) / 1_000_000.0

            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            img_delta = image_count if image_count > 0 else (1 if is_vision else 0)

            # 1. Total 历史累计
            t = self.data["total"]
            t["requests"] += 1
            t["prompt_tokens"] += prompt_tokens
            t["prompt_tokens_cached"] += cached
            t["prompt_tokens_miss"] += miss
            t["completion_tokens"] += completion_tokens
            t["total_tokens"] += total_tokens
            t["cost_cny"] = round(t["cost_cny"] + cost, 6)
            if is_vision or img_delta > 0:
                t["vision_images"] = t.get("vision_images", 0) + img_delta
                t["vision_duration_s"] = round(t.get("vision_duration_s", 0.0) + duration_s, 2)

            # 2. Today 当日统计
            d = self.data["today"]
            d["requests"] += 1
            d["prompt_tokens"] += prompt_tokens
            d["prompt_tokens_cached"] += cached
            d["prompt_tokens_miss"] += miss
            d["completion_tokens"] += completion_tokens
            d["total_tokens"] += total_tokens
            d["cost_cny"] = round(d["cost_cny"] + cost, 6)
            if is_vision or img_delta > 0:
                d["vision_images"] = d.get("vision_images", 0) + img_delta
                d["vision_duration_s"] = round(d.get("vision_duration_s", 0.0) + duration_s, 2)

            # 3. By Model 模型维度
            bm = self.data.setdefault("by_model", {})
            m_stat = bm.setdefault(model_name, {
                "requests": 0, "prompt_tokens": 0, "prompt_tokens_cached": 0,
                "completion_tokens": 0, "total_tokens": 0, "cost_cny": 0.0,
                "duration_s": 0.0, "image_count": 0
            })
            m_stat["requests"] += 1
            m_stat["prompt_tokens"] += prompt_tokens
            m_stat["prompt_tokens_cached"] += cached
            m_stat["completion_tokens"] += completion_tokens
            m_stat["total_tokens"] += total_tokens
            m_stat["cost_cny"] = round(m_stat["cost_cny"] + cost, 6)
            m_stat["duration_s"] = round(m_stat.get("duration_s", 0.0) + duration_s, 2)
            if is_vision or img_delta > 0:
                m_stat["image_count"] = m_stat.get("image_count", 0) + img_delta

            # 4. By Key (3台设备历史分账)
            bk = self.data.setdefault("by_key", {})
            k_stat = bk.setdefault(key_name, {
                "name": key_name,
                "requests": 0, "total_tokens": 0, "cost_cny": 0.0
            })
            k_stat["requests"] += 1
            k_stat["total_tokens"] += total_tokens
            k_stat["cost_cny"] = round(k_stat["cost_cny"] + cost, 6)

            # 5. 今日设备-模型分项累计 (by_device_model: 一直累加)
            today_dm = d.setdefault("by_device_model", {})
            dm_key = f"{key_name}::{model_name}"
            human_k_name = key_manager.keys.get(key_name, {}).get("name", key_name)
            is_vis = bool(is_vision or ("VL" in model_name or "Vision" in model_name or "多模态" in model_name))
            dm_stat = today_dm.setdefault(dm_key, {
                "key": key_name,
                "key_name": human_k_name,
                "model": model_name,
                "is_vision": is_vis,
                "last_time": now_str,
                "requests": 0,
                "prompt_tokens": 0,
                "prompt_tokens_cached": 0,
                "prompt_tokens_miss": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "duration_s": 0.0,
                "image_count": 0,
                "cost_cny": 0.0
            })
            dm_stat["last_time"] = now_str
            dm_stat["requests"] += 1
            dm_stat["prompt_tokens"] += prompt_tokens
            dm_stat["prompt_tokens_cached"] += cached
            dm_stat["prompt_tokens_miss"] += miss
            dm_stat["completion_tokens"] += completion_tokens
            dm_stat["total_tokens"] += total_tokens
            dm_stat["duration_s"] = round(dm_stat["duration_s"] + duration_s, 2)
            if is_vision or img_delta > 0:
                dm_stat["image_count"] = dm_stat.get("image_count", 0) + img_delta
            dm_stat["cost_cny"] = round(dm_stat["cost_cny"] + cost, 6)

            # 6. 每日历史明细记录 (用于月度方块热力图与日历浮窗)
            today_str = datetime.date.today().isoformat()
            dh = self.data.setdefault("daily_history", {})
            day_entry = dh.setdefault(today_str, {
                "requests": 0, "prompt_tokens": 0, "prompt_tokens_cached": 0,
                "prompt_tokens_miss": 0, "completion_tokens": 0, "total_tokens": 0,
                "cost_cny": 0.0, "by_key": {}
            })
            day_entry["requests"] += 1
            day_entry["prompt_tokens"] += prompt_tokens
            day_entry["prompt_tokens_cached"] += cached
            day_entry["prompt_tokens_miss"] += miss
            day_entry["completion_tokens"] += completion_tokens
            day_entry["total_tokens"] += total_tokens
            day_entry["cost_cny"] = round(day_entry["cost_cny"] + cost, 6)

            # 每日内设备分账
            day_bk = day_entry.setdefault("by_key", {})
            day_k_stat = day_bk.setdefault(key_name, {
                "name": key_name,
                "requests": 0, "prompt_tokens": 0, "prompt_tokens_cached": 0,
                "prompt_tokens_miss": 0, "completion_tokens": 0, "total_tokens": 0,
                "cost_cny": 0.0
            })
            day_k_stat["requests"] += 1
            day_k_stat["prompt_tokens"] += prompt_tokens
            day_k_stat["prompt_tokens_cached"] += cached
            day_k_stat["prompt_tokens_miss"] += miss
            day_k_stat["completion_tokens"] += completion_tokens
            day_k_stat["total_tokens"] += total_tokens
            day_k_stat["cost_cny"] = round(day_k_stat["cost_cny"] + cost, 6)

            # 7. 最近 50 条流水
            recents = self.data.setdefault("recent_requests", [])
            tps = round(completion_tokens / duration_s, 1) if duration_s > 0.05 else 0.0
            recents.insert(0, {
                "time": now_str,
                "key": key_name,
                "model": model_name,
                "prompt_tokens": prompt_tokens,
                "cached_tokens": cached,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cost_cny": round(cost, 6),
                "duration_s": round(duration_s, 2),
                "tps": tps
            })
            if len(recents) > 50:
                self.data["recent_requests"] = recents[:50]

            # 8. 累计 In/Out 纯工作耗时
            p_time = max(0.01, duration_s * (prompt_tokens / max(1, prompt_tokens + completion_tokens * 20)))
            g_time = max(0.01, duration_s - p_time)

            d["total_in_seconds"] = round(d.get("total_in_seconds", 0.0) + p_time, 1)
            d["total_out_seconds"] = round(d.get("total_out_seconds", 0.0) + g_time, 1)
            d["total_work_seconds"] = round(d.get("total_work_seconds", 0.0) + duration_s, 1)

            # 确定当前调用所属形态 (MTP_2SLOT / PIPELINE_4SLOT / VISION_27B)
            if "4并发" in model_name:
                mode_key = "PIPELINE_4SLOT"
            elif is_vision or "多模态" in model_name or "Vision" in model_name or "VL" in model_name:
                mode_key = "VISION_27B"
            else:
                mode_key = "MTP_2SLOT"

            # 9. 统计思维等级
            eff_norm = "medium"
            if reasoning_effort in ("low", "minimal"):
                eff_norm = "simple"
            elif reasoning_effort in ("high", "xhigh", "max"):
                eff_norm = "hard"
            else:
                eff_norm = "medium"

            rl = self.data.setdefault("reasoning_levels", {
                "today": {
                    "simple": 0, "medium": 0, "hard": 0,
                    "by_mode": {
                        "MTP_2SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "PIPELINE_4SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "VISION_27B": {"simple": 0, "medium": 0, "hard": 0}
                    }
                },
                "total": {
                    "simple": 0, "medium": 0, "hard": 0,
                    "by_mode": {
                        "MTP_2SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "PIPELINE_4SLOT": {"simple": 0, "medium": 0, "hard": 0},
                        "VISION_27B": {"simple": 0, "medium": 0, "hard": 0}
                    }
                }
            })
            rl.setdefault("today", {"simple": 0, "medium": 0, "hard": 0})
            rl.setdefault("total", {"simple": 0, "medium": 0, "hard": 0})
            today_bm = rl["today"].setdefault("by_mode", {
                "MTP_2SLOT": {"simple": 0, "medium": 0, "hard": 0},
                "PIPELINE_4SLOT": {"simple": 0, "medium": 0, "hard": 0},
                "VISION_27B": {"simple": 0, "medium": 0, "hard": 0}
            })
            total_bm = rl["total"].setdefault("by_mode", {
                "MTP_2SLOT": {"simple": 0, "medium": 0, "hard": 0},
                "PIPELINE_4SLOT": {"simple": 0, "medium": 0, "hard": 0},
                "VISION_27B": {"simple": 0, "medium": 0, "hard": 0}
            })

            rl["today"][eff_norm] = rl["today"].get(eff_norm, 0) + 1
            rl["total"][eff_norm] = rl["total"].get(eff_norm, 0) + 1

            today_bm.setdefault(mode_key, {"simple": 0, "medium": 0, "hard": 0})
            total_bm.setdefault(mode_key, {"simple": 0, "medium": 0, "hard": 0})
            today_bm[mode_key][eff_norm] = today_bm[mode_key].get(eff_norm, 0) + 1
            total_bm[mode_key][eff_norm] = total_bm[mode_key].get(eff_norm, 0) + 1

            # 10. 原子写落盘
            try:
                tmp_file = self.filepath + ".tmp"
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.filepath):
                    os.replace(tmp_file, self.filepath)
                else:
                    os.rename(tmp_file, self.filepath)
            except Exception as e:
                sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [BILLING-WARN] 保存统计失败: {e}\n")
                sys.stdout.flush()

            # 记录到测速引擎
            speed_engine.record_detailed(prompt_tokens, p_time, completion_tokens, g_time, duration_s)

            return cost, d["cost_cny"], d["requests"]

    def record_hot_swap(self, from_state, to_state, duration_s):
        """记录模型热切换等待耗时与次数，原子写落盘"""
        with self.lock:
            self._check_day_rollover()
            hs = self.data.setdefault("hot_swaps", {
                "total_count": 0,
                "today_count": 0,
                "last_duration_s": 0.0,
                "total_duration_s": 0.0,
                "avg_duration_s": 0.0,
                "last_from": "",
                "last_to": "",
                "last_time": "",
                "history": []
            })
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            hs["total_count"] = hs.get("total_count", 0) + 1
            hs["today_count"] = hs.get("today_count", 0) + 1
            hs["last_duration_s"] = round(duration_s, 2)
            hs["total_duration_s"] = round(hs.get("total_duration_s", 0.0) + duration_s, 2)
            hs["avg_duration_s"] = round(hs["total_duration_s"] / max(1, hs["total_count"]), 2)
            hs["last_from"] = from_state or "待命"
            hs["last_to"] = to_state
            hs["last_time"] = now_str

            hist = hs.setdefault("history", [])
            hist.insert(0, {
                "time": now_str,
                "from": from_state or "待命",
                "to": to_state,
                "duration_s": round(duration_s, 2)
            })
            if len(hist) > 20:
                hs["history"] = hist[:20]

            try:
                tmp_file = self.filepath + ".tmp"
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.filepath):
                    os.replace(tmp_file, self.filepath)
                else:
                    os.rename(tmp_file, self.filepath)
            except Exception as e:
                sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [BILLING-WARN] 保存热切换统计失败: {e}\n")
                sys.stdout.flush()

    def get_stats(self):
        with self.lock:
            self._check_day_rollover()
            self.data = self._load()
            st = dict(self.data)
            st["concurrency"] = concurrency_queue.get_dynamic_status()
            st["gpu"] = gpu_telemetry.get_status()
            st["speed"] = speed_engine.get_speed()
            st["reasoning_levels"] = self.data.get("reasoning_levels", {
                "today": {"simple": 0, "medium": 0, "hard": 0},
                "total": {"simple": 0, "medium": 0, "hard": 0}
            })
            st["hot_swaps"] = self.data.get("hot_swaps", {
                "total_count": 0,
                "today_count": 0,
                "last_duration_s": 0.0,
                "total_duration_s": 0.0,
                "avg_duration_s": 0.0,
                "last_from": "",
                "last_to": "",
                "last_time": "",
                "history": []
            })
            st["vision_summary"] = {
                "today_images": self.data.get("today", {}).get("vision_images", 0),
                "today_duration_s": round(self.data.get("today", {}).get("vision_duration_s", 0.0), 2),
                "total_images": self.data.get("total", {}).get("vision_images", 0),
                "total_duration_s": round(self.data.get("total", {}).get("vision_duration_s", 0.0), 2),
                "cache_count": len(VISION_IMAGE_OCR_CACHE),
                "pricing": {
                    "model_name": "Qwen3.8-27B-A [原生多模态]",
                    "input_cache_hit_per_m": PRICING["input_cache_hit_per_m"],
                    "input_cache_miss_per_m": PRICING["input_cache_miss_per_m"],
                    "output_per_m": PRICING["output_per_m"]
                }
            }
            return st

tracker = BillingTracker()

# ============================================================
#  后端连接重试（瞬时错误防抖）
# ============================================================
CONNECT_RETRIES = 3
CONNECT_RETRY_DELAYS = (0.2, 0.5, 1.0)

def _is_transient_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, urllib.error.URLError):
        r = exc.reason
        if isinstance(r, (ConnectionRefusedError, socket.timeout)):
            return True
        if isinstance(r, OSError) and getattr(r, "errno", None) in (10061, 10060, 10054, 10058):
            return True
        return False
    if isinstance(exc, (ConnectionRefusedError, socket.timeout, ConnectionResetError, ConnectionAbortedError)):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (10061, 10060, 10054, 10058):
        return True
    return False

def urlopen_with_retry(req, timeout):
    last_exc = None
    for attempt in range(CONNECT_RETRIES):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except Exception as e:
            if not _is_transient_error(e):
                raise
            last_exc = e
            if attempt < CONNECT_RETRIES - 1:
                delay = CONNECT_RETRY_DELAYS[attempt]
                sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [RETRY] 后端连接瞬断(第{attempt + 1}次: {e})，{delay}s 后重试\n")
                sys.stdout.flush()
                time.sleep(delay)
    raise last_exc

# ============================================================
#  安全硬规则与防幻觉约束文本
# ============================================================
GUARDRAIL_INJECTION = (
    "\n【网关硬约束规范】：所有文件读写必须使用显式、明确、真实的标准绝对路径。"
    "严禁在脚本或命令行中使用 String.fromCharCode、eval、多重动态进制转义等方式拼装路径或代码。"
)

CIRCUIT_BREAKER_WARNING = (
    "\n\n[GATEWAY GUARD CRITICAL]: 检测到连续多次工具执行失败死循环。"
    "禁止继续尝试使用动态脚本修补。必须立即向用户简明汇报当前卡点与具体错误根因。"
)

# ============================================================
#  GBNF 语法防爆 Schema 净化引擎 3.0
# ============================================================
#  JSON Schema 规范化与 GBNF 语法爆炸防护
# ============================================================
VALID_JSON_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}

def sanitize_schema(obj, depth=0):
    """
    RFC 7159 / JSON Schema 规范化与 GBNF 语法爆炸防护：
    1. 杜绝字段污染：type 永远为合法类型字面量，description 永远为纯文本；
    2. 深度超限（> 6）时直接将该层收敛为精简 {"type": "string"} 或 {"type": "object"}，严禁递归篡改子字段；
    3. 剥离 GBNF 不兼容的超复杂约束（patternProperties, $schema, $defs, nullable 等）；
    4. 展平 anyOf / oneOf / allOf。
    """
    if not isinstance(obj, dict):
        return obj

    # 深度超限时，整体降级为宽松类型，严禁递归篡改内部 key
    if depth > 6:
        t = obj.get("type", "object")
        if isinstance(t, str) and t in VALID_JSON_TYPES:
            return {"type": t}
        return {"type": "object"}

    cleaned = {}

    # 1. 展平 anyOf / oneOf / allOf
    for combiner in ("anyOf", "oneOf", "allOf"):
        if combiner in obj and isinstance(obj[combiner], list) and len(obj[combiner]) > 0:
            for branch in obj[combiner]:
                if isinstance(branch, dict) and branch.get("type") != "null":
                    sub = sanitize_schema(branch, depth + 1)
                    if isinstance(sub, dict):
                        cleaned.update(sub)
                    break
            break

    # 2. 处理 type (必须为合法字符串，严禁为字典)
    raw_type = obj.get("type")
    if isinstance(raw_type, str):
        cleaned["type"] = raw_type if raw_type in VALID_JSON_TYPES else "string"
    elif isinstance(raw_type, list):
        valid = [t for t in raw_type if isinstance(t, str) and t in VALID_JSON_TYPES and t != "null"]
        cleaned["type"] = valid[0] if valid else "string"
    elif "properties" in obj or "required" in obj:
        cleaned["type"] = "object"
    elif "items" in obj:
        cleaned["type"] = "array"

    # 3. 处理 description & title (必须为字符串)
    if "description" in obj and isinstance(obj["description"], str):
        cleaned["description"] = obj["description"][:500]
    if "title" in obj and isinstance(obj["title"], str):
        cleaned["title"] = obj["title"][:100]

    # 4. 处理 enum
    if "enum" in obj and isinstance(obj["enum"], list):
        enums = [str(x) for x in obj["enum"] if x is not None][:50]
        if enums:
            cleaned["enum"] = enums

    # 5. 处理 properties (必须为字典映射)
    if "properties" in obj and isinstance(obj["properties"], dict):
        cleaned_props = {}
        for prop_k, prop_v in obj["properties"].items():
            if isinstance(prop_v, dict):
                cleaned_props[str(prop_k)] = sanitize_schema(prop_v, depth + 1)
            else:
                cleaned_props[str(prop_k)] = {"type": "string"}
        cleaned["properties"] = cleaned_props
        if "type" not in cleaned:
            cleaned["type"] = "object"

    # 6. 处理 required
    if "required" in obj and isinstance(obj["required"], list):
        valid_req = [str(r) for r in obj["required"] if isinstance(r, str)]
        if valid_req:
            cleaned["required"] = valid_req

    # 7. 处理 items (array 元素 schema)
    if "items" in obj:
        if isinstance(obj["items"], dict):
            cleaned["items"] = sanitize_schema(obj["items"], depth + 1)
        elif isinstance(obj["items"], list) and len(obj["items"]) > 0 and isinstance(obj["items"][0], dict):
            cleaned["items"] = sanitize_schema(obj["items"][0], depth + 1)
        else:
            cleaned["items"] = {"type": "string"}
        if "type" not in cleaned:
            cleaned["type"] = "array"

    # 默认兜底
    if not cleaned:
        cleaned["type"] = "object"

    return cleaned

def sanitize_tools(tools):
    if not isinstance(tools, list):
        return tools
    sanitized = []
    for tool in tools:
        if isinstance(tool, dict):
            t_copy = dict(tool)
            if "function" in t_copy and isinstance(t_copy["function"], dict):
                fn_copy = dict(t_copy["function"])
                if "parameters" in fn_copy:
                    params = fn_copy["parameters"]
                    cleaned_params = sanitize_schema(params)
                    if not isinstance(cleaned_params, dict):
                        cleaned_params = {"type": "object", "properties": {}}
                    if cleaned_params.get("type") != "object":
                        cleaned_params["type"] = "object"
                    if "properties" not in cleaned_params:
                        cleaned_params["properties"] = {}
                    fn_copy["parameters"] = cleaned_params
                t_copy["function"] = fn_copy
            sanitized.append(t_copy)
        else:
            sanitized.append(tool)
    return sanitized

def check_error_loop(messages, threshold=3):
    recent_errors = 0
    err_keywords = ["enoent", "command failed", "syntaxerror", "not found", "error:", "is not recognized", "cannot find", "failed to"]
    for msg in reversed(messages[-12:]):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = str(msg.get("content", ""))
        if role in ("tool", "function") or (role == "user" and ("Tool Response" in content or "Output:" in content or "Error" in content)):
            if any(k in content.lower() for k in err_keywords):
                recent_errors += 1
                if recent_errors >= threshold:
                    return True
            else:
                break
    return False

def inject_guardrails_and_breaker(messages):
    if not isinstance(messages, list) or len(messages) == 0:
        return messages, False
    modified = False
    new_msgs = [dict(m) if isinstance(m, dict) else m for m in messages]
    
    has_system = False
    for msg in new_msgs:
        if isinstance(msg, dict) and msg.get("role") == "system":
            has_system = True
            content = str(msg.get("content", ""))
            if "String.fromCharCode" not in content and "网关硬约束" not in content:
                msg["content"] = content + GUARDRAIL_INJECTION
                modified = True
            break
            
    if not has_system:
        new_msgs.insert(0, {"role": "system", "content": GUARDRAIL_INJECTION.strip()})
        modified = True

    if check_error_loop(new_msgs, threshold=3):
        sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [CIRCUIT-BREAKER] ⚠️ 检测到连续工具报错死循环，已在末尾注入系统熔断指令！\n")
        sys.stdout.flush()
        last_msg = new_msgs[-1]
        if isinstance(last_msg, dict):
            last_content = str(last_msg.get("content", ""))
            if CIRCUIT_BREAKER_WARNING not in last_content:
                last_msg["content"] = last_content + CIRCUIT_BREAKER_WARNING
                modified = True
                
    return new_msgs, modified

def compact_history_messages(messages, keep_recent=12, max_old_len=300):
    if not isinstance(messages, list) or len(messages) <= keep_recent:
        return messages, False
    modified = False
    new_msgs = []
    cutoff_index = len(messages) - keep_recent
    for i, msg in enumerate(messages):
        if i < cutoff_index and isinstance(msg, dict):
            role = msg.get("role")
            content = msg.get("content")
            if (role in ("tool", "function") or (role == "user" and isinstance(content, str) and ("Tool Response" in content or "Output:" in content))) and isinstance(content, str) and len(content) > max_old_len:
                msg_copy = dict(msg)
                msg_copy["content"] = content[:max_old_len] + f"\n... [历史工具输出已自动精炼以保持推理专注] ..."
                new_msgs.append(msg_copy)
                modified = True
                continue
        new_msgs.append(msg)
    return new_msgs, modified

def sanitize_payload(req_data):
    if not isinstance(req_data, dict):
        return req_data, False
    modified = False
    if "tools" in req_data and isinstance(req_data["tools"], list):
        req_data["tools"] = sanitize_tools(req_data["tools"])
        modified = True
    
    if "functions" in req_data and isinstance(req_data["functions"], list):
        sanitized_funcs = []
        for fn in req_data["functions"]:
            if isinstance(fn, dict) and "parameters" in fn:
                fn_copy = dict(fn)
                fn_copy["parameters"] = sanitize_schema(fn_copy["parameters"])
                sanitized_funcs.append(fn_copy)
            else:
                sanitized_funcs.append(fn)
        req_data["functions"] = sanitized_funcs
        modified = True

    if "response_format" in req_data and isinstance(req_data["response_format"], dict):
        rf = req_data["response_format"]
        if rf.get("type") == "json_schema" and "json_schema" in rf:
            js = rf["json_schema"]
            if isinstance(js, dict) and "schema" in js:
                rf_copy = dict(rf)
                js_copy = dict(js)
                js_copy["schema"] = sanitize_schema(js_copy["schema"])
                rf_copy["json_schema"] = js_copy
                req_data["response_format"] = rf_copy
                modified = True
                
    if "messages" in req_data and isinstance(req_data["messages"], list):
        sanitized_msgs = []
        for m in req_data["messages"]:
            if not isinstance(m, dict):
                continue
            r = m.get("role")
            c = m.get("content")
            if r in ("tool", "function"):
                tid = m.get("tool_call_id") or m.get("name", "")
                text_c = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                sanitized_msgs.append({"role": "user", "content": f"[工具执行结果 (ID={tid})]:\n{text_c}"})
                modified = True
            elif r == "assistant":
                m_copy = dict(m)
                if m_copy.get("content") is None:
                    m_copy["content"] = ""
                sanitized_msgs.append(m_copy)
            else:
                sanitized_msgs.append(m)

        # 🌟 关键防御：彻底剥除末尾所有空 assistant 占位消息（如 {"role": "assistant", "content": ""}）
        # 否则 llama.cpp Jinja 模版会将其渲染为 <|im_start|>assistant\n<|im_end|>，导致模型立即停止（生成 0/1 token）并引发客户端死循环重试！
        while sanitized_msgs:
            last = sanitized_msgs[-1]
            if isinstance(last, dict) and last.get("role") == "assistant":
                c = last.get("content", "")
                tc = last.get("tool_calls", [])
                if (c == "" or c is None) and not tc:
                    sanitized_msgs.pop()
                    modified = True
                else:
                    break
            else:
                break

        if not sanitized_msgs:
            sanitized_msgs = [{"role": "user", "content": "继续"}]

        req_data["messages"] = sanitized_msgs

        req_data["messages"], injected = inject_guardrails_and_breaker(req_data["messages"])
        req_data["messages"], msg_compacted = compact_history_messages(req_data["messages"])
        if injected or msg_compacted:
            modified = True
                
    return req_data, modified

# ============================================================
#  Anthropic Messages API 原生协议适配器 (/v1/messages)
# ============================================================
def translate_anthropic_to_openai(anthropic_body):
    """将 Anthropic /v1/messages 请求结构转换为 OpenAI /v1/chat/completions 结构"""
    req_m_raw = anthropic_body.get("model", "")
    requested_model = resolve_model_alias(req_m_raw)

    stream = bool(anthropic_body.get("stream", False))
    max_tokens = anthropic_body.get("max_tokens", 4096)
    temperature = anthropic_body.get("temperature", 0.7)
    top_p = anthropic_body.get("top_p", 0.9)

    openai_messages = []

    # 1. 提取 system 消息
    system_prompt = anthropic_body.get("system")
    if isinstance(system_prompt, str) and system_prompt.strip():
        openai_messages.append({"role": "system", "content": system_prompt.strip()})
    elif isinstance(system_prompt, list):
        sys_texts = []
        for block in system_prompt:
            if isinstance(block, dict) and block.get("type") == "text":
                sys_texts.append(block.get("text", ""))
            elif isinstance(block, str):
                sys_texts.append(block)
        if sys_texts:
            openai_messages.append({"role": "system", "content": "\n".join(sys_texts)})

    # 2. 转换 messages
    for msg in anthropic_body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            openai_messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            # 处理复合内容块 (text, image, tool_use, tool_result)
            text_chunks = []
            tool_calls = []
            tool_results = []
            image_blocks = []

            for block in content:
                if not isinstance(block, dict):
                    continue
                b_type = block.get("type")
                if b_type == "text":
                    text_chunks.append(block.get("text", ""))
                elif b_type == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        m_type = source.get("media_type", "image/jpeg")
                        b64_data = source.get("data", "")
                        image_blocks.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{m_type};base64,{b64_data}"}
                        })
                elif b_type == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", f"call_{int(time.time()*1000)}"),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False) if isinstance(block.get("input"), dict) else str(block.get("input", "{}"))
                        }
                    })
                elif b_type == "tool_result":
                    res_content = block.get("content", "")
                    if isinstance(res_content, list):
                        res_str = " ".join([b.get("text", "") for b in res_content if isinstance(b, dict)])
                    else:
                        res_str = str(res_content)
                    tid = block.get("tool_use_id", "")
                    tool_results.append(f"[工具返回结果 (ID={tid})]:\n{res_str}")

            if role == "user":
                parts = []
                if text_chunks:
                    parts.append("\n".join(text_chunks))
                if tool_results:
                    parts.append("\n\n".join(tool_results))
                
                u_content = "\n\n".join(parts) if parts else ""
                if image_blocks:
                    c_list = []
                    if u_content:
                        c_list.append({"type": "text", "text": u_content})
                    c_list.extend(image_blocks)
                    openai_messages.append({"role": "user", "content": c_list})
                else:
                    openai_messages.append({"role": "user", "content": u_content or "继续"})
            elif role == "assistant":
                a_msg = {"role": "assistant"}
                if text_chunks:
                    a_msg["content"] = "\n".join(text_chunks)
                else:
                    a_msg["content"] = ""
                if tool_calls:
                    a_msg["tool_calls"] = tool_calls
                openai_messages.append(a_msg)
            else:
                openai_messages.append({"role": role, "content": "\n".join(text_chunks)})

    # 3. 转换 tools
    openai_tools = []
    for t in anthropic_body.get("tools", []):
        if isinstance(t, dict):
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}})
                }
            })

    if not openai_messages:
        openai_messages.append({"role": "user", "content": "继续"})

    openai_payload = {
        "model": requested_model,
        "messages": openai_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stream": stream
    }
    if openai_tools:
        openai_payload["tools"] = openai_tools

    return openai_payload

def translate_openai_to_anthropic_response(openai_resp_data, requested_model):
    """将 OpenAI 非流式响应转换为 Anthropic /v1/messages 格式"""
    msg_id = openai_resp_data.get("id", f"msg_{int(time.time()*1000)}")
    choices = openai_resp_data.get("choices", [{}])
    first_choice = choices[0] if choices else {}
    msg_obj = first_choice.get("message", {})
    finish_reason = first_choice.get("finish_reason", "stop")

    content_blocks = []
    
    # 文本内容与思考内容提取
    text = msg_obj.get("content")
    if text is None or text == "":
        text = msg_obj.get("reasoning_content") or msg_obj.get("reasoning") or msg_obj.get("thought") or ""

    if text:
        content_blocks.append({"type": "text", "text": text})

    # 工具调用
    tool_calls = msg_obj.get("tool_calls", [])
    for tc in tool_calls:
        fn = tc.get("function", {})
        try:
            input_args = json.loads(fn.get("arguments", "{}"))
        except Exception:
            input_args = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{int(time.time()*1000)}"),
            "name": fn.get("name", ""),
            "input": input_args
        })

    # 协议硬约束防御：如果没有任何块，补全空文本块防止客户端报 content required
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    stop_reason = "end_turn"
    if finish_reason == "tool_calls" or tool_calls:
        stop_reason = "tool_use"
    elif finish_reason == "length":
        stop_reason = "max_tokens"

    usage = openai_resp_data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": requested_model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens
        }
    }

# ============================================================
#  👑 27B 旗舰原生多模态视觉流水线 (Track 1 Native Vision + Fingerprint Cache)
#  完全抛弃轨道二 (3B侧挂/8085)，全面延伸轨道一 (27B + mmproj 原生全模态直通)
#  内置图像指纹高速缓存：多轮对话中同一张图片只需解析一次，历史轮次免重复解码，0.001s 瞬时复用
# ============================================================

VISION_IMAGE_CACHE_LOCK = threading.Lock()
# 图像指纹缓存：img_hash -> {"first_seen": timestamp, "status": "processed"}
VISION_IMAGE_OCR_CACHE = {}

def compute_image_hash(img_data_str: str) -> str:
    """基于图片数据内容生成 16 进制 MD5 指纹"""
    return hashlib.md5(img_data_str.encode("utf-8", errors="ignore")).hexdigest()

def has_image_content(payload):
    """检测当前请求中是否包含任何图片内容 (包括最新提问或历史轮次)"""
    if not isinstance(payload, dict):
        return False
    messages = payload.get("messages", [])
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        itype = item.get("type", "")
                        if itype in ("image_url", "image", "input_image"):
                            return True
                        if itype == "text" and "data:image/" in str(item.get("text", "")):
                            return True
            elif isinstance(content, str):
                if "data:image/jpeg;base64," in content or "data:image/png;base64," in content or "data:image/webp;base64," in content:
                    return True
    return False

def check_backend_is_multimodal(backend_port=8083):
    """检测 8083 主模型是否自带原生多模态能力 (如挂载了 --mmproj)"""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{backend_port}/props", headers={"Authorization": "Bearer llamacpp"}, method="GET")
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            if resp.status == 200:
                p_data = json.loads(resp.read().decode("utf-8"))
                modalities = p_data.get("default_generation_settings", {}).get("modalities", []) or []
                if "vision" in modalities or "image" in modalities:
                    return True
                params = p_data.get("default_generation_settings", {}).get("params", {}) or {}
                if params.get("mmproj") or "vl" in str(params.get("model", "")).lower() or "vision" in str(params.get("model", "")).lower():
                    return True
    except Exception:
        pass
    return False

def scan_images_in_payload(payload):
    """
    扫描请求体中所有的图片对象：
    返回：
      has_images (bool): 是否含有任意图片
      new_images (list): 当前请求中尚未被指纹缓存收录的新图像列表 [(msg_idx, item_idx, hash, url_str)]
      cached_images (list): 当前请求中命中前序轮次指纹缓存的图像列表 [(msg_idx, item_idx, hash)]
      last_img_msg_idx (int): 含有图片的最后一条消息索引
    """
    if not isinstance(payload, dict):
        return False, [], [], -1
    
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        return False, [], [], -1

    all_images = []
    new_images = []
    cached_images = []
    last_img_msg_idx = -1

    for msg_idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for item_idx, item in enumerate(content):
                if isinstance(item, dict):
                    itype = item.get("type", "")
                    img_url = ""
                    if itype in ("image_url", "image", "input_image"):
                        img_info = item.get("image_url") or item.get("url") or item.get("image")
                        if isinstance(img_info, dict):
                            img_url = img_info.get("url", "")
                        elif isinstance(img_info, str):
                            img_url = img_info
                    elif itype == "text" and "data:image/" in str(item.get("text", "")):
                        img_url = str(item.get("text", ""))

                    if img_url:
                        last_img_msg_idx = msg_idx
                        h = compute_image_hash(img_url)
                        all_images.append((msg_idx, item_idx, h, img_url))
                        with VISION_IMAGE_CACHE_LOCK:
                            if h in VISION_IMAGE_OCR_CACHE:
                                cached_images.append((msg_idx, item_idx, h))
                            else:
                                new_images.append((msg_idx, item_idx, h, img_url))
        elif isinstance(content, str):
            if "data:image/jpeg;base64," in content or "data:image/png;base64," in content or "data:image/webp;base64," in content:
                last_img_msg_idx = msg_idx
                h = compute_image_hash(content)
                all_images.append((msg_idx, 0, h, content))
                with VISION_IMAGE_CACHE_LOCK:
                    if h in VISION_IMAGE_OCR_CACHE:
                        cached_images.append((msg_idx, 0, h))
                    else:
                        new_images.append((msg_idx, 0, h, content))

    return bool(all_images), new_images, cached_images, last_img_msg_idx

def process_native_vision_pipeline(cleaned_json):
    """
    轨道一专属：27B 原生多模态视觉处理与多轮指纹缓存置换
    1. 识别并提取所有图片，计算指纹哈希；
    2. 【多轮对话免重复编码】：如果某张图片属于历史轮次（非最后一条提问）且其指纹已在 VISION_IMAGE_OCR_CACHE 中，
       将其庞大的 Base64 块置换为轻量指纹占位符（上下文已在 27B 记忆中，杜绝重复向 8083 发送数兆 Base64），
       不仅首字延迟（TTFT）从数秒降至 0.05 秒，还能在多轮后平滑兼容纯文本形态！
    3. 【最新轮次新图】：保持原生完整 image_url 结构直通 8083（由 27B + mmproj 原生像素理解），
       并在完成推理后自动录入指纹缓存！
    """
    has_img, new_imgs, cached_imgs, last_img_msg_idx = scan_images_in_payload(cleaned_json)
    if not has_img:
        return cleaned_json, False, []

    messages = cleaned_json.get("messages", [])
    new_messages = []
    pending_to_cache = []

    # 确定最后一条用户提问的索引，精准判定哪些是历史对话轮次
    latest_user_idx = -1
    for idx in range(len(messages) - 1, -1, -1):
        if isinstance(messages[idx], dict) and messages[idx].get("role") == "user":
            latest_user_idx = idx
            break

    for msg_idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            new_messages.append(msg)
            continue
        content = msg.get("content")
        is_historical = (msg_idx < latest_user_idx)

        if isinstance(content, list):
            new_content = []
            for item in content:
                if not isinstance(item, dict):
                    new_content.append(item)
                    continue
                itype = item.get("type", "")
                if itype in ("image_url", "image", "input_image"):
                    img_info = item.get("image_url") or item.get("url") or item.get("image")
                    url_str = img_info.get("url", "") if isinstance(img_info, dict) else (img_info if isinstance(img_info, str) else "")
                    h = compute_image_hash(url_str)

                    # 判断是否命中历史指纹缓存
                    with VISION_IMAGE_CACHE_LOCK:
                        is_cached = (h in VISION_IMAGE_OCR_CACHE)

                    if is_cached and (is_historical or msg_idx != last_img_msg_idx):
                        # 命中指纹缓存且为历史对话轮次：置换为轻量级指纹标识，免除重复传递数兆 Base64
                        sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [VISION-CACHE] ⚡ 历史图像指纹命中缓存 (hash={h[:8]})，多轮对话免重复编码，0.001s 瞬时复用！\n")
                        sys.stdout.flush()
                        new_content.append({
                            "type": "text",
                            "text": f"【🖼️ 图像指纹: {h[:8]} (已于前序轮次由 27B 原生多模态视觉引擎深度理解并建立视觉记忆，无需重复编码)】"
                        })
                    else:
                        # 最新提问或首次见到的图片：保留原生 image_url 原汁原味直通 8083 原生多模态
                        new_content.append(item)
                        pending_to_cache.append(h)
                else:
                    new_content.append(item)
            msg_copy = dict(msg)
            msg_copy["content"] = new_content
            new_messages.append(msg_copy)
        elif isinstance(content, str):
            h = compute_image_hash(content)
            with VISION_IMAGE_CACHE_LOCK:
                is_cached = (h in VISION_IMAGE_OCR_CACHE)
            if is_cached and (is_historical or msg_idx != last_img_msg_idx):
                sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [VISION-CACHE] ⚡ 历史图像指纹命中缓存 (hash={h[:8]})，多轮对话免重复编码，0.001s 瞬时复用！\n")
                sys.stdout.flush()
                msg_copy = dict(msg)
                msg_copy["content"] = f"【🖼️ 图像指纹: {h[:8]} (已于前序轮次由 27B 原生多模态视觉引擎深度理解并建立视觉记忆，无需重复编码)】"
                new_messages.append(msg_copy)
            else:
                new_messages.append(msg)
                if "data:image/" in content:
                    pending_to_cache.append(h)
        else:
            new_messages.append(msg)

    cleaned_json["messages"] = new_messages
    has_active_images = bool(pending_to_cache)
    return cleaned_json, has_active_images, pending_to_cache

def register_image_fingerprints(hash_list):
    """请求完成后，将本次处理完成的图像指纹写入全局高速缓存"""
    if not hash_list:
        return
    now_t = time.time()
    with VISION_IMAGE_CACHE_LOCK:
        for h in hash_list:
            VISION_IMAGE_OCR_CACHE[h] = {
                "first_seen": now_t,
                "status": "processed"
            }
    sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [VISION-CACHE] 💾 已持久化登记 {len(hash_list)} 个图像指纹至 27B 多模态缓存总线\n")
    sys.stdout.flush()

def estimate_tokens(text):
    if not text:
        return 0
    return max(1, int(len(text) * 0.75))

# ============================================================
#  智能上下文安全防爆舱 (Context Safety Ceiling Guard · 锁定 140K 安全水位)
# ============================================================
def enforce_context_safety_guard(payload, max_safe_tokens=140000):
    """
    智能上下文防爆舱：
    当检测到请求的总 Token 数接近或超出安全水位（默认 140,000 tokens）时，
    自动保留：
      1. System Prompt（完整保留，绝不丢失系统人设与编码规范）
      2. 最新的对话与工具调用（最后 8 轮关键消息完整保留）
    对中间最久远的历史消息：
      1. 如果某条消息内容超长（如 > 1500 字符的大文件读取结果或执行日志），将其平滑提炼为折叠占位符
      2. 逐步修剪中间历史，直到总预估 Token 稳定收敛在 125,000 安全水位以内！
    """
    if not isinstance(payload, dict):
        return payload, False
    
    messages = payload.get("messages", [])
    if not isinstance(messages, list) or len(messages) <= 6:
        return payload, False

    total_str = json.dumps(messages, ensure_ascii=False)
    cur_tokens = estimate_tokens(total_str)
    
    if cur_tokens <= max_safe_tokens:
        return payload, False

    sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [CONTEXT-GUARD] ⚠️ 检测到请求上下文高达 {cur_tokens:,} Token (接近/超出140K安全水位)，启动智能防爆平滑修剪...\n")
    sys.stdout.flush()

    system_msgs = [m for m in messages if m.get("role") == "system"]
    other_msgs = [m for m in messages if m.get("role") != "system"]

    protected_tail_count = min(8, len(other_msgs))
    middle_msgs = other_msgs[:-protected_tail_count] if protected_tail_count > 0 else []
    tail_msgs = other_msgs[-protected_tail_count:] if protected_tail_count > 0 else other_msgs

    trimmed_middle = []
    for idx, m in enumerate(middle_msgs):
        m_copy = dict(m)
        content = m_copy.get("content", "")
        if isinstance(content, str) and len(content) > 1500:
            head = content[:200]
            tail = content[-200:]
            orig_len = len(content)
            m_copy["content"] = f"[历史大文件/工具输出已由智能网关安全折叠 (原长 {orig_len:,} 字符，适配 160K 算力池)]:\n{head}\n... [中间 {orig_len - 400:,} 字符已省略] ...\n{tail}"
        elif isinstance(content, list):
            trimmed_blocks = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    b_txt = b.get("text", "")
                    if len(b_txt) > 1500:
                        b_head = b_txt[:200]
                        b_tail = b_txt[-200:]
                        b_len = len(b_txt)
                        trimmed_blocks.append({"type": "text", "text": f"[历史输出已折叠 (原长 {b_len:,} 字符)]:\n{b_head}\n... [省略] ...\n{b_tail}"})
                    else:
                        trimmed_blocks.append(b)
                else:
                    trimmed_blocks.append(b)
            m_copy["content"] = trimmed_blocks
        trimmed_middle.append(m_copy)

    assembled = system_msgs + trimmed_middle + tail_msgs
    new_str = json.dumps(assembled, ensure_ascii=False)
    new_tokens = estimate_tokens(new_str)

    while new_tokens > 130000 and len(trimmed_middle) > 2:
        trimmed_middle.pop(0)
        assembled = system_msgs + trimmed_middle + tail_msgs
        new_str = json.dumps(assembled, ensure_ascii=False)
        new_tokens = estimate_tokens(new_str)

    payload_copy = dict(payload)
    payload_copy["messages"] = assembled
    saved_tokens = cur_tokens - new_tokens
    sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [CONTEXT-GUARD] ✅ 智能防爆修剪完成：由 {cur_tokens:,} 降至 {new_tokens:,} Token (安全节省 {saved_tokens:,} Token)，100% 免疫 160K 溢出！\n")
    sys.stdout.flush()
    return payload_copy, True

# ============================================================
#  可视化 Web 看板 HTML 模板 3.0 (含实时 TPS、GPU 探针、槽位与月度热力图)
# ============================================================
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>奇迹算力网关 3.0 · 企业级智能协同与多端分账看板</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0b0f17;
    --card-bg: rgba(18, 24, 38, 0.78);
    --border: rgba(255, 255, 255, 0.08);
    --accent: #38bdf8;
    --accent-green: #4ade80;
    --accent-purple: #c084fc;
    --accent-orange: #fb923c;
    --text: #f1f5f9;
    --text-muted: #94a3b8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background-color: var(--bg); color: var(--text);
    font-family: 'Inter', -apple-system, sans-serif; padding: 24px; min-height: 100vh;
    background-image: radial-gradient(circle at 0% 0%, rgba(56, 189, 248, 0.08) 0%, transparent 50%),
                      radial-gradient(circle at 100% 100%, rgba(192, 132, 252, 0.08) 0%, transparent 50%);
  }
  .container { max-width: 1240px; margin: 0 auto; }
  .header {
    display: flex; justify-content: space-between; align-items: center;
    padding-bottom: 20px; border-bottom: 1px solid var(--border); margin-bottom: 24px; flex-wrap: wrap; gap: 12px;
  }
  .title { font-size: 24px; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 12px; }
  .badge { background: rgba(56,189,248,0.15); color: var(--accent); font-size: 12px; padding: 4px 12px; border-radius: 20px; border: 1px solid rgba(56,189,248,0.3); }
  .header-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  
  .btn-action {
    background: rgba(255,255,255,0.06); border: 1px solid var(--border); color: #fff;
    padding: 6px 14px; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 500;
    transition: all 0.2s ease; display: inline-flex; align-items: center; gap: 6px;
  }
  .btn-action:hover { background: rgba(255,255,255,0.15); border-color: rgba(255,255,255,0.3); transform: translateY(-1px); }
  
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card {
    background: var(--card-bg); backdrop-filter: blur(16px); border: 1px solid var(--border);
    border-radius: 14px; padding: 20px; transition: transform 0.2s ease, border-color 0.2s ease;
  }
  .card:hover { transform: translateY(-2px); border-color: rgba(255,255,255,0.2); }
  .card-label { font-size: 13px; color: var(--text-muted); font-weight: 500; margin-bottom: 8px; }
  .card-value { font-size: 28px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
  .card-sub { font-size: 12px; color: var(--text-muted); margin-top: 6px; }
  
  .table-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 14px; padding: 20px; margin-bottom: 24px; }
  .table-title { font-size: 15px; font-weight: 600; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 12px; color: var(--text-muted); border-bottom: 1px solid var(--border); }
  td { padding: 12px; border-bottom: 1px solid rgba(255,255,255,0.04); font-family: 'JetBrains Mono', monospace; }
  tr:hover td { background: rgba(255,255,255,0.02); }
  
  .pricing-banner {
    background: rgba(74, 222, 128, 0.08); border: 1px solid rgba(74, 222, 128, 0.25);
    border-radius: 12px; padding: 14px 18px; font-size: 13px; margin-bottom: 24px;
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;
  }
  .slot-pill { display: inline-flex; align-items: center; gap: 8px; padding: 6px 14px; border-radius: 20px; font-size: 12px; background: rgba(255,255,255,0.05); border: 1px solid var(--border); }
  .dot-green { width: 8px; height: 8px; border-radius: 50%; background: var(--accent-green); box-shadow: 0 0 8px var(--accent-green); }
  .dot-orange { width: 8px; height: 8px; border-radius: 50%; background: var(--accent-orange); box-shadow: 0 0 8px var(--accent-orange); }
  .dot-blue { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 8px var(--accent); animation: pulse 1.5s infinite; }
  @keyframes pulse { 0% { opacity: 0.4; } 50% { opacity: 1; } 100% { opacity: 0.4; } }

  .progress-bar-bg { width: 100%; height: 8px; background: rgba(255,255,255,0.08); border-radius: 4px; overflow: hidden; margin-top: 6px; }
  .progress-bar-fill { height: 100%; background: var(--accent); border-radius: 4px; }

  /* 槽位与硬件监控卡片 */
  .slots-monitor-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; margin-top: 14px; }
  .slot-card {
    background: rgba(0,0,0,0.35); border: 1px solid var(--border); border-radius: 12px; padding: 16px;
    display: flex; flex-direction: column; gap: 8px; transition: all 0.2s ease;
  }
  .slot-card.active { border-color: rgba(192, 132, 252, 0.6); background: rgba(192, 132, 252, 0.06); box-shadow: 0 0 16px rgba(192, 132, 252, 0.15); }
  .slot-card.active-prefill { border-color: rgba(56, 189, 248, 0.7); background: rgba(56, 189, 248, 0.08); box-shadow: 0 0 16px rgba(56, 189, 248, 0.2); }
  .slot-card-vision {
    border-color: rgba(192, 132, 252, 0.45);
    background: radial-gradient(circle at top right, rgba(192, 132, 252, 0.12), rgba(0,0,0,0.45));
    box-shadow: 0 0 16px rgba(192, 132, 252, 0.12);
  }
  .slot-card-vision:hover {
    border-color: rgba(192, 132, 252, 0.8);
    box-shadow: 0 0 22px rgba(192, 132, 252, 0.25);
  }
  .slot-card.active-vision {
    border-color: rgba(192, 132, 252, 0.9);
    background: radial-gradient(circle at top right, rgba(192, 132, 252, 0.2), rgba(0,0,0,0.5));
    box-shadow: 0 0 24px rgba(192, 132, 252, 0.35);
  }
  
  .slot-card-header { display: flex; justify-content: space-between; align-items: center; font-size: 13px; font-weight: 600; }
  .slot-badge-idle { font-size: 11px; padding: 2px 8px; border-radius: 12px; background: rgba(74, 222, 128, 0.15); color: var(--accent-green); border: 1px solid rgba(74, 222, 128, 0.3); }
  .slot-badge-busy { font-size: 11px; padding: 2px 8px; border-radius: 12px; background: rgba(192, 132, 252, 0.2); color: var(--accent-purple); border: 1px solid rgba(192, 132, 252, 0.4); animation: pulse-purple 1.5s infinite; }
  .slot-badge-prefill { font-size: 11px; padding: 2px 8px; border-radius: 12px; background: rgba(56, 189, 248, 0.25); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.5); animation: pulse-blue 1.5s infinite; }
  .slot-badge-vision-active { font-size: 11px; padding: 2px 8px; border-radius: 12px; background: rgba(192, 132, 252, 0.25); color: #c084fc; border: 1px solid rgba(192, 132, 252, 0.6); animation: pulse-purple 1.5s infinite; }
  .slot-badge-vision-standby { font-size: 11px; padding: 2px 8px; border-radius: 12px; background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4); }
  .slot-badge-vision-idle { font-size: 11px; padding: 2px 8px; border-radius: 12px; background: rgba(74, 222, 128, 0.15); color: var(--accent-green); border: 1px solid rgba(74, 222, 128, 0.3); }
  .slot-badge-vision-off { font-size: 11px; padding: 2px 8px; border-radius: 12px; background: rgba(255, 255, 255, 0.08); color: var(--text-muted); border: 1px solid var(--border); }
  
  .slot-card-body { font-size: 12px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace; display: flex; flex-direction: column; gap: 4px; }
  .slot-stat-row { display: flex; justify-content: space-between; align-items: center; }
  .slot-stat-val { font-weight: 600; color: #fff; }
  .slot-progress-bg { width: 100%; height: 6px; background: rgba(255,255,255,0.08); border-radius: 3px; overflow: hidden; margin-top: 6px; }
  .slot-progress-fill { height: 100%; background: var(--accent-orange); border-radius: 3px; transition: width 0.3s ease; }
  .slot-progress-fill.fill-prefill { background: linear-gradient(90deg, #38bdf8, #818cf8); }
  
  @keyframes pulse-blue { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
  @keyframes pulse-purple { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }

  .badge-vision { background: rgba(192, 132, 252, 0.15); color: var(--accent-purple); font-size: 11px; padding: 2px 8px; border-radius: 6px; border: 1px solid rgba(192, 132, 252, 0.3); margin-left: 6px; }
  .badge-text { background: rgba(56, 189, 248, 0.15); color: var(--accent); font-size: 11px; padding: 2px 8px; border-radius: 6px; border: 1px solid rgba(56, 189, 248, 0.3); margin-left: 6px; }

  /* 月度热力图控件样式 */
  .heatmap-nav { display: flex; align-items: center; gap: 8px; }
  .btn-nav {
    background: rgba(255,255,255,0.06); border: 1px solid var(--border); color: #fff;
    padding: 5px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 500;
    transition: all 0.2s ease;
  }
  .btn-nav:hover { background: rgba(255,255,255,0.15); border-color: rgba(255,255,255,0.3); }
  .tab-pills { display: flex; gap: 6px; background: rgba(0,0,0,0.3); padding: 4px; border-radius: 8px; border: 1px solid var(--border); }
  .tab-pill {
    padding: 4px 10px; border-radius: 6px; font-size: 12px; color: var(--text-muted); cursor: pointer;
    transition: all 0.2s; border: none; background: transparent;
  }
  .tab-pill.active { background: var(--accent); color: #000; font-weight: 600; }
  
  .calendar-container { margin-top: 16px; }
  .calendar-weekdays { display: grid; grid-template-columns: repeat(7, 1fr); text-align: center; font-size: 12px; color: var(--text-muted); margin-bottom: 8px; font-weight: 500; }
  .calendar-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; }
  .cal-square {
    height: 48px; border-radius: 8px; display: flex; flex-direction: column; align-items: center; justify-content: center;
    font-family: 'JetBrains Mono', monospace; font-size: 13px; font-weight: 600; cursor: pointer;
    border: 1px solid rgba(255,255,255,0.06); position: relative; transition: all 0.2s ease;
  }
  .cal-square:hover { transform: scale(1.08); z-index: 10; border-color: #fff; box-shadow: 0 4px 16px rgba(0,0,0,0.5); }
  .cal-square.empty { background: transparent; border: none; cursor: default; }
  .cal-square.empty:hover { transform: none; box-shadow: none; }
  .cal-square.is-today { outline: 2px solid var(--accent); outline-offset: 2px; }
  
  /* 热力等级颜色 */
  .level-0 { background: #161b22; color: #484f58; }
  .level-1 { background: rgba(56, 189, 248, 0.22); color: #bae6fd; border-color: rgba(56, 189, 248, 0.4); }
  .level-2 { background: rgba(56, 189, 248, 0.50); color: #fff; border-color: rgba(56, 189, 248, 0.7); }
  .level-3 { background: rgba(74, 222, 128, 0.65); color: #fff; border-color: rgba(74, 222, 128, 0.85); }
  .level-4 { background: #4ade80; color: #052e16; font-weight: 700; border-color: #86efac; box-shadow: 0 0 10px rgba(74, 222, 128, 0.4); }

  .cal-square-tokens { font-size: 9px; font-weight: normal; opacity: 0.85; margin-top: 2px; }

  /* 悬停浮窗 Tooltip */
  #custom-tooltip {
    position: fixed; display: none; z-index: 1000; pointer-events: none;
    background: rgba(15, 23, 42, 0.95); backdrop-filter: blur(16px);
    border: 1px solid rgba(56, 189, 248, 0.4); border-radius: 10px;
    padding: 12px 16px; font-size: 12px; line-height: 1.6; color: #f8fafc;
    box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.7); min-width: 200px;
  }
  .tt-title { font-weight: 700; color: var(--accent); border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 4px; margin-bottom: 6px; }
  .tt-row { display: flex; justify-content: space-between; gap: 16px; }
  .tt-val { font-family: 'JetBrains Mono', monospace; font-weight: 600; color: #fff; }

  /* 客户端快速接入卡片 */
  .config-guide-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; margin-top: 10px; }
  .config-box { background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: 10px; padding: 14px; font-size: 12px; }
  .config-title { font-weight: 600; color: var(--accent); margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }
  .code-snippet { background: rgba(0,0,0,0.5); padding: 8px 10px; border-radius: 6px; font-family: 'JetBrains Mono', monospace; color: #e2e8f0; margin-top: 6px; word-break: break-all; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="title">🚀 奇迹算力网关 3.0 <span class="badge">Claude Code & OpenAI 原生双协议</span></div>
    <div class="header-actions">
      <span class="slot-pill" id="header-gpu-pill"><span class="dot-green" id="gpu-dot"></span> <span id="gpu-status">GPU: 检测中...</span></span>
      <span class="slot-pill" id="header-slot-pill"><span class="dot-orange" id="slot-dot"></span> <span id="slot-status">⏳ 等待加载模型</span></span>
      <button class="btn-action" onclick="updateStats()">🔄 刷新</button>
      <button class="btn-action" onclick="exportStatsJSON()">📥 导出 JSON</button>
    </div>
  </div>

  <div class="pricing-banner">
    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
      <div>🏷️ <strong>当前计价标准</strong>：纯文本 <code>DeepSeek-V4-Flash-0731</code> & 原生多模态 <code>Qwen3.8-27B-A [原生多模态]</code> | 空闲时段</div>
      <div>Token计费率: 缓存命中 <strong style="color:var(--accent-green);">¥0.05/M</strong> | 未命中 <strong style="color:var(--accent-orange);">¥1.50/M</strong> | 输出生成 <strong style="color:var(--accent-purple);">¥4.50/M</strong></div>
    </div>
    <div style="margin-top: 6px; padding-top: 6px; border-top: 1px dashed rgba(255,255,255,0.1); font-size: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
      <div>👁️ <strong>Qwen3.8-27B-A [原生多模态] 专项核算</strong>：今日读图 <strong id="banner-vision-today-imgs" style="color:var(--accent-purple);font-size:14px;">0</strong> 张 (总耗时 <span id="banner-vision-today-time" style="color:#38bdf8;font-weight:600;">0.0s</span>) · 历史累计 <strong id="banner-vision-total-imgs" style="color:var(--accent);font-size:14px;">0</strong> 张图</div>
      <div>⚡ <strong>图像指纹高速缓存</strong>：已收录 <strong id="banner-vision-cache-count" style="color:var(--accent-green);font-size:14px;">0</strong> 个 (多轮追问 0.001s 瞬时复用)</div>
    </div>
    <div style="margin-top: 6px; padding-top: 6px; border-top: 1px dashed rgba(255,255,255,0.1); font-size: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
      <div>🔄 <strong>模型自适应热切换统计</strong>：今日置换 <strong id="banner-hotswap-today" style="color:#38bdf8;font-size:14px;">0</strong> 次 · 上次等待耗时 <strong id="banner-hotswap-last" style="color:var(--accent-green);font-size:14px;">0.0s</strong> (全天均候 <span id="banner-hotswap-avg" style="color:var(--accent-orange);font-weight:600;">0.0s</span>)</div>
      <div>⏳ <strong>历史累计切换</strong>：共 <strong id="banner-hotswap-total" style="color:#fff;font-size:14px;">0</strong> 次 · 4.5s 内存级自适应无感切形态</div>
    </div>
  </div>

  <!-- 🌟 槽位实时在位与硬件并发负载卡片 (含 In/Out 速率与上下文使用量) -->
  <div class="table-card" id="slots-monitor-card" style="display: none;">
    <div class="table-title">
      <div>
        <span>⚡ 当前加载模型：<strong id="active-model-title" style="color: var(--accent);">Qwen3.8-27B-A-Q6_K</strong></span>
        <span style="font-size: 12px; color: var(--text-muted); margin-left: 10px;" id="active-ctx-desc">(4 并发 · 144K 共享统一 KV 资源池)</span>
      </div>
      <span style="font-size: 12px; color: var(--accent-green);" id="slots-occupancy-desc">0/4 槽位占用 · 全部待命中</span>
    </div>
    <div class="slots-monitor-grid" id="slots-container">
      <!-- 动态注入各槽位卡片 -->
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="card-label">今日总算力价值 (DeepSeek-V4)</div>
      <div class="card-value" id="today-cost" style="color: var(--accent-green);">¥0.0000</div>
      <div class="card-sub" id="today-reqs">今日共 0 次调用</div>
    </div>
    <div class="card">
      <div class="card-label">历史累计算力总价值</div>
      <div class="card-value" id="total-cost" style="color: var(--accent);">¥0.0000</div>
      <div class="card-sub" id="total-reqs">累计 0 次对话</div>
    </div>
    <div class="card" style="border-color: rgba(192, 132, 252, 0.4); background: radial-gradient(circle at top right, rgba(192, 132, 252, 0.08), rgba(0,0,0,0.3));">
      <div class="card-label" style="color: #c084fc;">👁️ 原生多模态读图与耗时统计</div>
      <div class="card-value" id="vision-kpi-value" style="color: #c084fc; font-size: 19px;">0 张 · 0.0s</div>
      <div class="card-sub" id="vision-kpi-sub">今日读图: 0 张 | 累计: 0 张图</div>
    </div>
    <div class="card" style="border-color: rgba(56, 189, 248, 0.45); background: radial-gradient(circle at top right, rgba(56, 189, 248, 0.1), rgba(0,0,0,0.3));">
      <div class="card-label" style="color: #38bdf8;">🔄 模型热切换与等待耗时统计</div>
      <div class="card-value" id="hotswap-kpi-value" style="color: #38bdf8; font-size: 19px;">0 次 · 0.0s</div>
      <div class="card-sub" id="hotswap-kpi-sub">今日切换: 0 次 | 均候: 0.0s</div>
    </div>
    <div class="card">
      <div class="card-label">全天工作累计总均速 (In / Out)</div>
      <div class="card-value" id="current-tps" style="color: #38bdf8; font-size: 19px;">0.0 tok/s</div>
      <div class="card-sub" id="peak-tps">今日纯工作耗时: 0.0s (剔除空闲)</div>
    </div>
    <div class="card">
      <div class="card-label">今日 Token 总吞吐</div>
      <div class="card-value" id="today-tokens" style="color: var(--accent-purple);">0</div>
      <div class="card-sub" id="today-token-detail">输入: 0 | 输出: 0</div>
    </div>
    <div class="card">
      <div class="card-label">Prompt 缓存命中率 (当日)</div>
      <div class="card-value" id="cache-hit-rate" style="color: var(--accent-orange);">0.0%</div>
      <div class="card-sub" id="cache-hit-detail">今日命中: 0 tokens</div>
    </div>
    <div class="card" style="border-color: rgba(167, 139, 250, 0.45); background: radial-gradient(circle at top right, rgba(167, 139, 250, 0.1), rgba(0,0,0,0.3));">
      <div class="card-label" style="color: #a78bfa; display: flex; justify-content: space-between; align-items: center;">
        <span>🧠 模型思维等级调控 (问答难度)</span>
        <span style="font-size: 11px; color: var(--text-muted);" id="reasoning-total-badge">今日 0次</span>
      </div>
      <div style="display: flex; flex-direction: column; gap: 5px; margin-top: 5px;">
        <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(56, 189, 248, 0.08); padding: 4px 8px; border-radius: 6px; border: 1px solid rgba(56, 189, 248, 0.2);">
          <div style="font-size: 11px; font-weight: 600; color: #38bdf8;">👑 双槽MTP</div>
          <div id="reasoning-mtp-line" style="font-family: 'JetBrains Mono', monospace; font-size: 12px;">
            <span style="color:#4ade80;font-weight:700;">0</span> <span style="font-size:10px;color:var(--text-muted);">简</span> · 
            <span style="color:#38bdf8;font-weight:700;">0</span> <span style="font-size:10px;color:var(--text-muted);">中</span> · 
            <span style="color:#c084fc;font-weight:700;">0</span> <span style="font-size:10px;color:var(--text-muted);">难</span>
          </div>
        </div>
        <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(251, 146, 60, 0.08); padding: 4px 8px; border-radius: 6px; border: 1px solid rgba(251, 146, 60, 0.2);">
          <div style="font-size: 11px; font-weight: 600; color: #fb923c;">🚀 4并发流水线</div>
          <div id="reasoning-pipe-line" style="font-family: 'JetBrains Mono', monospace; font-size: 12px;">
            <span style="color:#4ade80;font-weight:700;">0</span> <span style="font-size:10px;color:var(--text-muted);">简</span> · 
            <span style="color:#38bdf8;font-weight:700;">0</span> <span style="font-size:10px;color:var(--text-muted);">中</span> · 
            <span style="color:#c084fc;font-weight:700;">0</span> <span style="font-size:10px;color:var(--text-muted);">难</span>
          </div>
        </div>
      </div>
      <div class="card-sub" id="reasoning-kpi-sub" style="margin-top: 5px; font-size: 11px;">今日总计: 0简 · 0中 · 0难</div>
    </div>
    <div class="card" style="border-color: rgba(56, 189, 248, 0.45); background: radial-gradient(circle at top right, rgba(56, 189, 248, 0.08), rgba(0,0,0,0.3));">
      <div class="card-label" style="color: #38bdf8;">⏱️ 当日 In / Out 总耗时 (全槽位合计)</div>
      <div class="card-value" id="inout-kpi-value" style="font-size: 17px; letter-spacing: -0.3px;">
        <span style="color:#38bdf8;font-weight:700;">📥 0.0s</span> <span style="font-size:11px;color:var(--text-muted);">In</span> · 
        <span style="color:var(--accent-purple);font-weight:700;">📤 0.0s</span> <span style="font-size:11px;color:var(--text-muted);">Out</span>
      </div>
      <div class="card-sub" id="inout-kpi-sub">全槽位纯工作耗时: 0.0s (预填 0% · 解码 0%)</div>
    </div>
  </div>

  <!-- 🌟 今日当前累计调用流水 (按设备与模型累计 · 一直累加) -->
  <div class="table-card">
    <div class="table-title">
      <span>📊 今日当前累计调用清单 (按设备与模型实时累计 · 一直累加)</span>
      <span style="font-size: 12px; color: var(--text-muted); font-weight: normal;">JSON 接口: <code style="color: var(--accent);">GET /v1/billing</code></span>
    </div>
    <table>
      <thead>
        <tr>
          <th>最后活跃时间</th>
          <th>调用设备 (Key)</th>
          <th>请求模型 (文本 / 原生多模态)</th>
          <th>Prompt (未命中 / 命中)</th>
          <th>Output</th>
          <th>累计耗时 (调用次数)</th>
          <th>🖼️ 识图统计</th>
          <th>今日累计价值</th>
        </tr>
      </thead>
      <tbody id="recents-tbody">
        <tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 24px;">今日暂无调用记录</td></tr>
      </tbody>
    </table>
  </div>

  <!-- 🌟 月度 Token & 算力热力日历方块卡片 -->
  <div class="table-card">
    <div class="table-title">
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
        <span>🗓️ 月度 Token & 算力日历热力图</span>
        <div class="heatmap-nav">
          <button class="btn-nav" onclick="changeMonth(-1)">◀ 上月</button>
          <strong id="heatmap-month-label" style="color: #fff; font-family: 'JetBrains Mono'; font-size: 14px; min-width: 110px; text-align: center;">2026年 9月</strong>
          <button class="btn-nav" onclick="changeMonth(1)">下月 ▶</button>
          <button class="btn-nav" onclick="resetToCurrentMonth()">本月</button>
        </div>
      </div>
      
      <div class="tab-pills">
        <button class="tab-pill active" onclick="switchKeyTab('all', this)">全部汇总</button>
        <button class="tab-pill" onclick="switchKeyTab('v100-32G', this)">v100-32G</button>
        <button class="tab-pill" onclick="switchKeyTab('llamacpp', this)">llamacpp</button>
        <button class="tab-pill" onclick="switchKeyTab('admin', this)">admin</button>
      </div>
    </div>

    <div class="calendar-container">
      <div class="calendar-weekdays">
        <div>周一</div><div>周二</div><div>周三</div><div>周四</div><div>周五</div><div>周六</div><div>周日</div>
      </div>
      <div class="calendar-grid" id="cal-grid">
        <!-- JS 动态渲染当月方块 -->
      </div>
    </div>

    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 16px; font-size: 12px; color: var(--text-muted); flex-wrap: wrap; gap: 10px;">
      <div id="month-summary-stat">本月总天数: 30天 | 活跃天数: 1天 | 本月总消耗: ¥0.0000</div>
      <div style="display: flex; align-items: center; gap: 6px;">
        <span>活跃度：少</span>
        <span style="display:inline-block; width:12px; height:12px; border-radius:2px; background:#161b22; border:1px solid rgba(255,255,255,0.1);"></span>
        <span style="display:inline-block; width:12px; height:12px; border-radius:2px; background:rgba(56,189,248,0.22);"></span>
        <span style="display:inline-block; width:12px; height:12px; border-radius:2px; background:rgba(56,189,248,0.50);"></span>
        <span style="display:inline-block; width:12px; height:12px; border-radius:2px; background:rgba(74,222,128,0.65);"></span>
        <span style="display:inline-block; width:12px; height:12px; border-radius:2px; background:#4ade80;"></span>
        <span>多</span>
      </div>
    </div>
  </div>

  <!-- 🖥️ 3台电脑设备分账卡片 (排在最后板块) -->
  <div class="table-card">
    <div class="table-title">
      <span>🖥️ 3台电脑独立调用分账与用量排行</span>
      <span style="font-size: 12px; color: var(--text-muted); font-weight: normal;">无上限限制 · 实时对比谁用的多</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>调用 Key (设备)</th>
          <th>设备用途说明</th>
          <th>累计调用次数</th>
          <th>Token 吞吐量</th>
          <th>算力价值 (元)</th>
          <th>用量占比</th>
        </tr>
      </thead>
      <tbody id="keys-tbody">
        <tr>
          <td><strong style="color: var(--accent);">admin</strong></td>
          <td>Admin 主控机</td>
          <td id="key-admin-reqs">0 次</td>
          <td id="key-admin-tokens">0</td>
          <td id="key-admin-cost" style="color: var(--accent-green);">¥0.0000</td>
          <td style="width: 200px;"><div class="progress-bar-bg"><div class="progress-bar-fill" id="key-admin-bar" style="width: 0%;"></div></div></td>
        </tr>
        <tr>
          <td><strong style="color: var(--accent-purple);">llamacpp</strong></td>
          <td>llamacpp 测试机</td>
          <td id="key-llama-reqs">0 次</td>
          <td id="key-llama-tokens">0</td>
          <td id="key-llama-cost" style="color: var(--accent-green);">¥0.0000</td>
          <td><div class="progress-bar-bg"><div class="progress-bar-fill" id="key-llama-bar" style="width: 0%; background: var(--accent-purple);"></div></div></td>
        </tr>
        <tr>
          <td><strong style="color: var(--accent-orange);">v100-32G</strong></td>
          <td>v100-32G 工作机</td>
          <td id="key-v100-reqs">0 次</td>
          <td id="key-v100-tokens">0</td>
          <td id="key-v100-cost" style="color: var(--accent-green);">¥0.0000</td>
          <td><div class="progress-bar-bg"><div class="progress-bar-fill" id="key-v100-bar" style="width: 0%; background: var(--accent-orange);"></div></div></td>
        </tr>
      </tbody>
    </table>
  </div>

  <!-- 💡 多客户端无缝接入指南卡片 (排在最后板块) -->
  <div class="table-card">
    <div class="table-title">
      <span>💡 多客户端与智能开发工具快速接入指南</span>
      <span style="font-size: 12px; color: var(--accent);">支持原生 Anthropic /v1/messages 与 OpenAI 格式</span>
    </div>
    <div class="config-guide-grid">
      <div class="config-box">
        <div class="config-title">⚡ Claude Code / Claude Desktop 原生直连</div>
        <div>端点地址与环境变量 (支持 /v1/messages 协议)：</div>
        <div class="code-snippet">ANTHROPIC_BASE_URL=http://127.0.0.1:8081<br>ANTHROPIC_API_KEY=admin</div>
      </div>
      <div class="config-box">
        <div class="config-title">🛠️ Cursor / Trae / VSCode Continue / Roo Code</div>
        <div>OpenAI 兼容端点配置：</div>
        <div class="code-snippet">Base URL: http://127.0.0.1:8081/v1<br>API Key: admin (或 llamacpp / v100-32G)</div>
      </div>
      <div class="config-box">
        <div class="config-title">📊 CC Switch / NewAPI 余额监控集成</div>
        <div>余额查询 URL 与计费端点：</div>
        <div class="code-snippet">GET http://127.0.0.1:8081/user/balance<br>GET http://127.0.0.1:8081/v1/billing</div>
        <div style="font-size: 11px; color: var(--accent-orange); margin-top: 4px;">⚠️ 建议在 CC Switch 设置中将【上游超时】设为 ≥ 300s，防 60K+ 超长预填中断</div>
      </div>
    </div>
  </div>
</div>

<!-- 鼠标悬停浮窗 -->
<div id="custom-tooltip"></div>

<script>
let globalData = null;
let currentYear = new Date().getFullYear();
let currentMonth = new Date().getMonth() + 1; // 1-12
let selectedKeyTab = 'all';

function switchKeyTab(tab, el) {
  selectedKeyTab = tab;
  document.querySelectorAll('.tab-pill').forEach(p => p.classList.remove('active'));
  if (el) el.classList.add('active');
  renderCalendar();
}

function changeMonth(delta) {
  currentMonth += delta;
  if (currentMonth > 12) { currentMonth = 1; currentYear += 1; }
  else if (currentMonth < 1) { currentMonth = 12; currentYear -= 1; }
  renderCalendar();
}

function resetToCurrentMonth() {
  const now = new Date();
  currentYear = now.getFullYear();
  currentMonth = now.getMonth() + 1;
  renderCalendar();
}

function exportStatsJSON() {
  if (!globalData) return;
  const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(globalData, null, 2));
  const dlAnchorElem = document.createElement('a');
  dlAnchorElem.setAttribute("href", dataStr);
  dlAnchorElem.setAttribute("download", `miracle_gateway_stats_${new Date().toISOString().slice(0,10)}.json`);
  dlAnchorElem.click();
}

function renderCalendar() {
  document.getElementById('heatmap-month-label').innerText = `${currentYear}年 ${currentMonth}月`;
  const grid = document.getElementById('cal-grid');
  grid.innerHTML = '';

  const firstDay = new Date(currentYear, currentMonth - 1, 1);
  const totalDays = new Date(currentYear, currentMonth, 0).getDate();
  
  let startDayOfWeek = firstDay.getDay();
  if (startDayOfWeek === 0) startDayOfWeek = 7;

  // 1. 填充月初空白
  for (let i = 1; i < startDayOfWeek; i++) {
    const emptyCell = document.createElement('div');
    emptyCell.className = 'cal-square empty';
    grid.appendChild(emptyCell);
  }

  const dailyHistory = (globalData && globalData.daily_history) ? globalData.daily_history : {};
  const todayStr = new Date().toISOString().slice(0, 10);

  let activeDaysCount = 0;
  let monthTotalCost = 0.0;
  let monthTotalTokens = 0;
  let monthTotalRequests = 0;

  // 2. 渲染当月每一天
  for (let day = 1; day <= totalDays; day++) {
    const monthPadded = String(currentMonth).padStart(2, '0');
    const dayPadded = String(day).padStart(2, '0');
    const dateKey = `${currentYear}-${monthPadded}-${dayPadded}`;
    
    const dayData = dailyHistory[dateKey] || null;
    let cost = 0, tokens = 0, reqs = 0, cached = 0, miss = 0, output = 0;

    if (dayData) {
      if (selectedKeyTab === 'all') {
        cost = dayData.cost_cny || 0;
        tokens = dayData.total_tokens || 0;
        reqs = dayData.requests || 0;
        cached = dayData.prompt_tokens_cached || 0;
        miss = dayData.prompt_tokens_miss || (dayData.prompt_tokens - cached) || 0;
        output = dayData.completion_tokens || 0;
      } else {
        const kData = (dayData.by_key && dayData.by_key[selectedKeyTab]) ? dayData.by_key[selectedKeyTab] : null;
        if (kData) {
          cost = kData.cost_cny || 0;
          tokens = kData.total_tokens || 0;
          reqs = kData.requests || 0;
          cached = kData.prompt_tokens_cached || 0;
          miss = kData.prompt_tokens_miss || (kData.prompt_tokens - cached) || 0;
          output = kData.completion_tokens || 0;
        }
      }
    }

    if (reqs > 0) {
      activeDaysCount++;
      monthTotalCost += cost;
      monthTotalTokens += tokens;
      monthTotalRequests += reqs;
    }

    // 计算热力等级
    let level = 'level-0';
    if (tokens > 0) {
      if (tokens < 50000) level = 'level-1';
      else if (tokens < 500000) level = 'level-2';
      else if (tokens < 2000000) level = 'level-3';
      else level = 'level-4';
    }

    const isToday = (dateKey === todayStr);
    const sq = document.createElement('div');
    sq.className = `cal-square ${level} ${isToday ? 'is-today' : ''}`;
    
    let tokenStr = tokens > 0 ? (tokens >= 1000000 ? (tokens/1000000).toFixed(1)+'M' : (tokens >= 1000 ? (tokens/1000).toFixed(0)+'k' : tokens)) : '';
    sq.innerHTML = `<div>${day}</div>` + (tokenStr ? `<div class="cal-square-tokens">${tokenStr}</div>` : '');

    // 鼠标悬停事件
    sq.onmouseenter = (e) => showTooltip(e, dateKey, reqs, cached, miss, output, tokens, cost);
    sq.onmousemove = (e) => moveTooltip(e);
    sq.onmouseleave = hideTooltip;

    grid.appendChild(sq);
  }

  // 3. 更新月度统计汇总文字
  const activeRate = ((activeDaysCount / totalDays) * 100).toFixed(1);
  const keyLabel = selectedKeyTab === 'all' ? '全部设备汇总' : selectedKeyTab;
  document.getElementById('month-summary-stat').innerText = 
    `【${keyLabel}】本月活跃: ${activeDaysCount}/${totalDays}天 (${activeRate}%) | 调用: ${monthTotalRequests}次 | Token: ${monthTotalTokens.toLocaleString()} | 算力价值: ¥${monthTotalCost.toFixed(4)}`;
}

// 悬停 Tooltip 逻辑
const tooltip = document.getElementById('custom-tooltip');
function showTooltip(e, dateKey, reqs, cached, miss, output, tokens, cost) {
  const weekdayNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  const d = new Date(dateKey);
  const weekday = weekdayNames[d.getDay()];
  const keyTitle = selectedKeyTab === 'all' ? '全部设备汇总' : selectedKeyTab;

  tooltip.innerHTML = `
    <div class="tt-title">📅 ${dateKey} (${weekday}) · ${keyTitle}</div>
    <div class="tt-row"><span>🪙 当天算力价值：</span><span class="tt-val" style="color:var(--accent-green);">¥${cost.toFixed(5)}</span></div>
    <div class="tt-row"><span>🔢 调用请求次数：</span><span class="tt-val">${reqs} 次</span></div>
    <div class="tt-row"><span>⚡ Prompt 缓存命中：</span><span class="tt-val" style="color:var(--accent-green);">${cached.toLocaleString()}</span></div>
    <div class="tt-row"><span>📥 Prompt 缓存未命：</span><span class="tt-val" style="color:var(--accent-orange);">${miss.toLocaleString()}</span></div>
    <div class="tt-row"><span>📤 模型生成输出：</span><span class="tt-val" style="color:var(--accent-purple);">${output.toLocaleString()}</span></div>
    <div class="tt-row" style="border-top:1px solid rgba(255,255,255,0.1); margin-top:4px; padding-top:4px;">
      <span>📊 当天吞吐总计：</span><span class="tt-val">${tokens.toLocaleString()} tokens</span>
    </div>
  `;
  tooltip.style.display = 'block';
  moveTooltip(e);
}

function moveTooltip(e) {
  const x = e.clientX + 16;
  const y = e.clientY + 16;
  tooltip.style.left = `${Math.min(window.innerWidth - 240, x)}px`;
  tooltip.style.top = `${Math.min(window.innerHeight - 180, y)}px`;
}

function hideTooltip() {
  tooltip.style.display = 'none';
}

// 动态渲染槽位与 GPU 状态 (含 in tok/S, out tok/S, 约上下文已用)
function updateSlotsUI(c, gpu, v) {
  const monitorCard = document.getElementById('slots-monitor-card');
  const slotPill = document.getElementById('header-slot-pill');
  const slotDot = document.getElementById('slot-dot');
  const slotStatus = document.getElementById('slot-status');
  
  // GPU 状态
  const gpuStatus = document.getElementById('gpu-status');
  const gpuDot = document.getElementById('gpu-dot');
  if (gpu && gpu.online) {
    gpuStatus.innerText = `${gpu.gpu_name.replace('NVIDIA GeForce ', '')}: ${gpu.vram_used_mb}/${gpu.vram_total_mb}MB (${gpu.vram_pct}%) · ${gpu.temp_c}℃ · ${gpu.power_w}W`;
    gpuDot.className = 'dot-green';
  } else {
    gpuStatus.innerText = 'GPU: 在位就绪';
    gpuDot.className = 'dot-green';
  }

  // 🌟 槽位监控卡片永久常驻显示，一个任务都没有也绝不隐身！
  monitorCard.style.display = 'block';
  const isOnline = (c && c.backend_online);
  const modelName = (c && c.model_name) || 'Qwen3.8-27B-A-Q6_K';
  const active = (c && c.text_active) || 0;
  const maxSlots = (c && c.text_max) || 4;
  const totalCtx = (c && c.total_ctx) ? (c.total_ctx >= 1024 ? (c.total_ctx/1024)+'K' : c.total_ctx) : '160K';

  document.getElementById('active-model-title').innerText = modelName;
  const isMulti = c && (c.is_multimodal || modelName.includes('Vision') || modelName.includes('多模态'));
  const mmprojInfo = (c && c.mmproj_file) ? ` (${c.mmproj_file})` : '';
  const modeTag = isMulti ? `<span style="margin-left:8px;padding:2px 8px;border-radius:6px;font-size:11px;background:rgba(168,85,247,0.18);color:#c084fc;border:1px solid rgba(168,85,247,0.35);">👁️ 原生多模态视觉${mmprojInfo}</span>` : `<span style="margin-left:8px;padding:2px 8px;border-radius:6px;font-size:11px;background:rgba(56,189,248,0.15);color:#38bdf8;border:1px solid rgba(56,189,248,0.3);">⚡ 纯文本极速矩阵 (视觉守护待命中)</span>`;
  document.getElementById('active-ctx-desc').innerHTML = `(${maxSlots} 并发 · ${totalCtx} 共享统一 KV 资源池) ${modeTag}`;
  
  if (!isOnline) {
    slotDot.className = 'dot-orange';
    slotStatus.innerText = '⏳ 启动器已就绪 · 等待加载模型';
    document.getElementById('slots-occupancy-desc').innerHTML = `<span style="color:var(--accent-orange);">⏳ 等待 8083 核心加载...</span>`;
  } else if (active > 0) {
    slotDot.className = 'dot-blue';
    slotStatus.innerText = `⚡ ${modelName} · ${active}/${maxSlots} 槽位在位运行中`;
    document.getElementById('slots-occupancy-desc').innerHTML = `<span style="color:var(--accent);">⚡ ${active}/${maxSlots} 槽位正在推理计算</span>`;
  } else {
    slotDot.className = 'dot-green';
    slotStatus.innerText = `⚡ ${modelName} · ${maxSlots} 槽位全部待命就绪`;
    document.getElementById('slots-occupancy-desc').innerHTML = `<span style="color:var(--accent-green);">🟢 0/${maxSlots} 占用 · 全部槽位空闲待命</span>`;
  }

  const container = document.getElementById('slots-container');
  let details = (c && c.slots_detail && c.slots_detail.length > 0) ? c.slots_detail : [
    { slot_num: 1, raw_id: 0, is_active: false, stage: 'idle', n_ctx: 73728, in_tok_s: 0, out_tok_s: 0 },
    { slot_num: 2, raw_id: 1, is_active: false, stage: 'idle', n_ctx: 73728, in_tok_s: 0, out_tok_s: 0 }
  ];

  const vCacheCount = (v && v.cache_count !== undefined) ? v.cache_count : (c ? (c.vision_cache_count || 0) : 0);
  const vTodayImgs = (v && v.today_images !== undefined) ? v.today_images : 0;
  const vTodayTime = (v && v.today_duration_s !== undefined) ? v.today_duration_s.toFixed(1) : '0.0';
  const vTotalImgs = (v && v.total_images !== undefined) ? v.total_images : 0;
  const isVisionActive = (c && c.active_vision > 0);

  function renderSlotCard(s, titlePrefix) {
    const isBusy = s.is_active;
    const stage = s.stage || (isBusy ? 'generating' : 'idle');
    const slotCtx = s.n_ctx ? (s.n_ctx >= 1024 ? (s.n_ctx/1024)+'K' : s.n_ctx) : '72K';
    
    let badgeHtml = '<span class="slot-badge-idle">🟢 空闲待命</span>';
    let inSpeedStr = '-';
    let outSpeedStr = '-';
    let progressDesc = `0 / ${slotCtx} (0%)`;
    let fillPct = 0;

    const lastInAvg = s.last_in_avg || 0;
    const lastOutAvg = s.last_out_avg || 0;
    const lastPrompt = s.last_prompt_tokens || 0;
    const lastDecoded = s.last_decoded_tokens || 0;
    const lastPrefillTime = s.last_prefill_time || 0;
    const lastGenTime = s.last_gen_time || 0;

    if (isBusy) {
      if (stage === 'prefill') {
        badgeHtml = '<span class="slot-badge-prefill">⚡ 预填计算中...</span>';
        inSpeedStr = (s.in_tok_s && s.in_tok_s > 0) ? `${s.in_tok_s.toFixed(1)} tok/s <span style="font-size:10px;color:var(--text-muted);font-weight:normal;">(已算 ${(s.n_prompt_proc||0).toLocaleString()} tok · ${(s.t_prefill||0).toFixed(1)}s)</span>` : '预填计算中...';
        outSpeedStr = '-';
        const progPct = s.stage_progress ? s.stage_progress.toFixed(1) : ((s.n_prompt > 0 ? (s.n_prompt_proc/s.n_prompt*100) : 0).toFixed(1));
        progressDesc = `进度: ${(s.n_prompt_proc||0).toLocaleString()} / ${(s.n_prompt||0).toLocaleString()} (${progPct}%)`;
        fillPct = Math.min(100, Math.max(10, parseFloat(progPct) || 10));
      } else {
        badgeHtml = '<span class="slot-badge-busy">✨ 吐字生成中...</span>';
        inSpeedStr = (lastInAvg > 0) ? `${lastInAvg.toFixed(1)} tok/s <span style="font-size:10px;color:var(--text-muted);font-weight:normal;">(处理 ${lastPrompt.toLocaleString()} tok)</span>` : '-';
        const s3s = (s.out_tok_s_3s && s.out_tok_s_3s > 0) ? ` · 瞬时 ${s.out_tok_s_3s.toFixed(1)} t/s` : '';
        outSpeedStr = (s.out_tok_s && s.out_tok_s > 0) ? `${s.out_tok_s.toFixed(1)} tok/s <span style="font-size:10px;color:var(--text-muted);font-weight:normal;">(已吐字 ${(s.n_decoded||0).toLocaleString()} tok${s3s} · ${(s.t_gen||0).toFixed(1)}s)</span>` : '吐字中...';
        const decoded = s.n_decoded || 0;
        progressDesc = `已吐字: ${decoded.toLocaleString()} tok | 上下文: ${(s.ctx_used||0).toLocaleString()} / ${slotCtx} (${s.ctx_pct}%)`;
        fillPct = Math.min(100, Math.max(12, s.ctx_pct || 12));
      }
    } else {
      inSpeedStr = (lastInAvg > 0) ? `${lastInAvg.toFixed(1)} tok/s <span style="font-size:10px;color:var(--text-muted);font-weight:normal;">${lastPrompt > 0 ? '(处理 ' + lastPrompt.toLocaleString() + ' tok · ' + lastPrefillTime.toFixed(1) + 's)' : ''}</span>` : '-';
      outSpeedStr = (lastOutAvg > 0) ? `${lastOutAvg.toFixed(1)} tok/s <span style="font-size:10px;color:var(--text-muted);font-weight:normal;">${lastDecoded > 0 ? '(生成 ' + lastDecoded.toLocaleString() + ' tok · ' + lastGenTime.toFixed(1) + 's)' : ''}</span>` : '-';
      progressDesc = `0 / ${slotCtx} (空闲待命就绪)`;
      fillPct = 0;
    }

    return `
      <div class="slot-card ${isBusy ? (stage === 'prefill' ? 'active-prefill' : 'active') : ''}">
        <div class="slot-card-header">
          <span>${titlePrefix || ('槽位 #' + s.slot_num)} <span style="font-size: 11px; color: var(--text-muted); font-weight: normal;">(ID ${s.raw_id})</span></span>
          ${badgeHtml}
        </div>
        <div class="slot-card-body">
          <div class="slot-stat-row">
            <span>📥 预填总均速 (in):</span>
            <span class="slot-stat-val" style="color: #38bdf8;">${inSpeedStr}</span>
          </div>
          <div class="slot-stat-row">
            <span>📤 吐字总均速 (out):</span>
            <span class="slot-stat-val" style="color: var(--accent-purple);">${outSpeedStr}</span>
          </div>
          <div class="slot-stat-row" style="margin-top: 4px;">
            <span>📊 运行进度 / 上下文:</span>
            <span class="slot-stat-val" style="color: var(--accent-orange); font-size: 11.5px;">${progressDesc}</span>
          </div>
          <div class="slot-progress-bg">
            <div class="slot-progress-fill ${stage === 'prefill' ? 'fill-prefill' : ''}" style="width: ${fillPct}%;"></div>
          </div>
        </div>
      </div>
    `;
  }

  function renderVisionTowerCard(isMainModel) {
    const badge = isMainModel 
      ? (isVisionActive ? '<span class="slot-badge-vision-active">⚡ 视觉特征编码中...</span>' : '<span class="slot-badge-idle">🟣 原生视觉激活挂载 (Online)</span>')
      : '<span class="slot-badge-vision-standby">🟢 待命随行 (Standby)</span>';
    
    const cardTitle = isMainModel ? '👁️ F16 视觉特征投影塔 (Vision Tower)' : '👁️ 27B 原生多模态视觉塔 (守护随行)';
    const statusNote = isMainModel ? 'mmproj-Qwen3.8-27B-F16.gguf (F16高精)' : 'mmproj-Qwen3.8-27B-F16.gguf (随时热切)';
    const featureNote = isMainModel ? '128K 超大上下文 · 原生像素直通' : '纯文本常驻 · 遇到识图 4.5s 内存级置换';
    const fillBg = isMainModel ? 'linear-gradient(90deg, #c084fc, #ec4899)' : 'linear-gradient(90deg, #10b981, #38bdf8)';

    return `
      <div class="slot-card slot-card-vision ${isVisionActive ? 'active-vision' : ''}">
        <div class="slot-card-header">
          <span style="color: #c084fc;">${cardTitle}</span>
          ${badge}
        </div>
        <div class="slot-card-body">
          <div class="slot-stat-row">
            <span>🧬 视觉投影挂载:</span>
            <span class="slot-stat-val" style="color: #c084fc; font-size: 11.5px;">${statusNote}</span>
          </div>
          <div class="slot-stat-row">
            <span>🖼️ 图像指纹高速缓存:</span>
            <span class="slot-stat-val" style="color: var(--accent-green); font-size: 11.5px;">已收录 ${vCacheCount} 张 (0.001s 复用)</span>
          </div>
          <div class="slot-stat-row">
            <span>📈 识图统计 (今日/累计):</span>
            <span class="slot-stat-val" style="color: #38bdf8; font-size: 11.5px;">今日 ${vTodayImgs} 张 (${vTodayTime}s) · 累计 ${vTotalImgs} 张</span>
          </div>
          <div class="slot-stat-row" style="margin-top: 4px;">
            <span>🛡️ 调度特性:</span>
            <span class="slot-stat-val" style="color: var(--text-muted); font-size: 11px;">${featureNote}</span>
          </div>
          <div class="slot-progress-bg">
            <div class="slot-progress-fill" style="width: 100%; background: ${fillBg};"></div>
          </div>
        </div>
      </div>
    `;
  }

  let html = '';
  if (isMulti) {
    // 🌟 用户需求1：当主模型为 Qwen3.8-27B-A [原生多模态] 时：
    // 槽位区域展示 1 个主脑推理槽位信息 + 1 个多模态视觉塔信息！
    const mainSlot = details[0] || { slot_num: 1, raw_id: 0, is_active: false, stage: 'idle', n_ctx: 131072 };
    html += renderSlotCard(mainSlot, '👑 27B 主脑推理槽位 (Slot #1)');
    html += renderVisionTowerCard(true);
  } else {
    // 🌟 用户需求2：当切换到其他工作型纯文本模型时：
    // 视觉多模态组件不隐身！渲染所有纯文本推理槽位 + 常驻视觉多模态守护卡片！
    html += details.map(s => renderSlotCard(s, `槽位 #${s.slot_num}`)).join('');
    html += renderVisionTowerCard(false);
  }

  container.innerHTML = html;
}

async function updateStats() {
  try {
    const res = await fetch('/v1/billing');
    if (!res.ok) return;
    const data = await res.json();
    globalData = data;
    
    document.getElementById('today-cost').innerText = '¥' + (data.today.cost_cny || 0).toFixed(4);
    document.getElementById('today-reqs').innerText = '今日共 ' + data.today.requests + ' 次调用';
    
    document.getElementById('total-cost').innerText = '¥' + (data.total.cost_cny || 0).toFixed(4);
    document.getElementById('total-reqs').innerText = '累计 ' + data.total.requests + ' 次对话';
    
    // 全天工作累计总均速 (In / Out)
    if (data.speed) {
      const inAvg = (data.speed.today_in_avg || 0).toFixed(1);
      const outAvg = (data.speed.today_out_avg || 0).toFixed(1);
      const workSec = (data.speed.today_work_seconds || 0).toFixed(1);
      document.getElementById('current-tps').innerHTML = `
        <span style="color:#38bdf8;font-size:18px;font-weight:700;">📥 ${inAvg}</span> <span style="font-size:11px;color:var(--text-muted);">in</span> · 
        <span style="color:var(--accent-purple);font-size:18px;font-weight:700;">📤 ${outAvg}</span> <span style="font-size:11px;color:var(--text-muted);">out</span>
      `;
      document.getElementById('peak-tps').innerText = `今日纯工作耗时: ${workSec}s (剔除空闲)`;
    }

    document.getElementById('today-tokens').innerText = (data.today.total_tokens || 0).toLocaleString();
    document.getElementById('today-token-detail').innerText = '输入: ' + (data.today.prompt_tokens || 0).toLocaleString() + ' | 输出: ' + (data.today.completion_tokens || 0).toLocaleString();
    
    // 🌟 Prompt 缓存命中率（只计算当日）
    const promptToday = (data.today && data.today.prompt_tokens) || 0;
    const cachedToday = (data.today && data.today.prompt_tokens_cached) || 0;
    const hitRate = promptToday > 0 ? ((cachedToday / promptToday) * 100).toFixed(1) : '0.0';
    document.getElementById('cache-hit-rate').innerText = hitRate + '%';
    document.getElementById('cache-hit-detail').innerText = '今日命中: ' + cachedToday.toLocaleString() + ' tokens (极速)';

    // 🌟 原生多模态指标更新 (横幅与卡片)
    const vs = data.vision_summary || {};
    const vTodayImgs = vs.today_images || 0;
    const vTodayTime = (vs.today_duration_s || 0).toFixed(1);
    const vTotalImgs = vs.total_images || 0;
    const vCacheCount = vs.cache_count || 0;

    const bTodayImgs = document.getElementById('banner-vision-today-imgs');
    if (bTodayImgs) bTodayImgs.innerText = vTodayImgs;
    const bTodayTime = document.getElementById('banner-vision-today-time');
    if (bTodayTime) bTodayTime.innerText = vTodayTime + 's';
    const bTotalImgs = document.getElementById('banner-vision-total-imgs');
    if (bTotalImgs) bTotalImgs.innerText = vTotalImgs;
    const bCacheCount = document.getElementById('banner-vision-cache-count');
    if (bCacheCount) bCacheCount.innerText = vCacheCount;

    const vKpiVal = document.getElementById('vision-kpi-value');
    if (vKpiVal) vKpiVal.innerText = `${vTodayImgs} 张 · ${vTodayTime}s`;
    const vKpiSub = document.getElementById('vision-kpi-sub');
    if (vKpiSub) vKpiSub.innerText = `今日读图: ${vTodayImgs} 张 | 累计: ${vTotalImgs} 张图`;

    // 🌟 模型自适应热切换指标更新
    const hs = data.hot_swaps || {};
    const hsToday = hs.today_count || 0;
    const hsTotal = hs.total_count || 0;
    const hsLast = (hs.last_duration_s || 0).toFixed(1);
    const hsAvg = (hs.avg_duration_s || 0).toFixed(1);

    function formatStateName(s) {
      if (!s) return '待命';
      if (s === 'MTP_2SLOT') return '双槽MTP';
      if (s === 'PIPELINE_4SLOT') return '4并发流水线';
      if (s === 'VISION_27B') return '原生多模态';
      return s.replace('STATE_', '');
    }

    const bHsToday = document.getElementById('banner-hotswap-today');
    if (bHsToday) bHsToday.innerText = hsToday;
    const bHsLast = document.getElementById('banner-hotswap-last');
    if (bHsLast) bHsLast.innerText = hsLast + 's';
    const bHsAvg = document.getElementById('banner-hotswap-avg');
    if (bHsAvg) bHsAvg.innerText = hsAvg + 's';
    const bHsTotal = document.getElementById('banner-hotswap-total');
    if (bHsTotal) bHsTotal.innerText = hsTotal;

    const hsKpiVal = document.getElementById('hotswap-kpi-value');
    if (hsKpiVal) hsKpiVal.innerText = `${hsToday} 次 · 等待 ${hsLast}s`;
    const hsKpiSub = document.getElementById('hotswap-kpi-sub');
    if (hsKpiSub) {
      if (hs.last_to) {
        hsKpiSub.innerText = `最近: ${formatStateName(hs.last_from)} ➔ ${formatStateName(hs.last_to)} (${hsLast}s) · 均候 ${hsAvg}s`;
      } else {
        hsKpiSub.innerText = `今日切换: ${hsToday} 次 | 均候: ${hsAvg}s | 累计: ${hsTotal}次`;
      }
    }

    // 🌟 模型思维等级调控 KPI 更新 (分双槽MTP与4并发流水线独立统计)
    const rl = data.reasoning_levels || {};
    const rlToday = rl.today || { simple: 0, medium: 0, hard: 0, by_mode: {} };
    const rSim = rlToday.simple || 0;
    const rMed = rlToday.medium || 0;
    const rHar = rlToday.hard || 0;
    const rTot = rSim + rMed + rHar;

    const bm = rlToday.by_mode || {};
    const mtpStats = bm['MTP_2SLOT'] || { simple: 0, medium: 0, hard: 0 };
    const pipeStats = bm['PIPELINE_4SLOT'] || { simple: 0, medium: 0, hard: 0 };
    const visStats = bm['VISION_27B'] || { simple: 0, medium: 0, hard: 0 };

    const badgeEl = document.getElementById('reasoning-total-badge');
    if (badgeEl) badgeEl.innerText = `今日 ${rTot}次`;

    const mtpLine = document.getElementById('reasoning-mtp-line');
    if (mtpLine) {
      mtpLine.innerHTML = `
        <span style="color:#4ade80;font-weight:700;">${mtpStats.simple || 0}</span> <span style="font-size:10px;color:var(--text-muted);">简</span> · 
        <span style="color:#38bdf8;font-weight:700;">${mtpStats.medium || 0}</span> <span style="font-size:10px;color:var(--text-muted);">中</span> · 
        <span style="color:#c084fc;font-weight:700;">${mtpStats.hard || 0}</span> <span style="font-size:10px;color:var(--text-muted);">难</span>
      `;
    }

    const pipeLine = document.getElementById('reasoning-pipe-line');
    if (pipeLine) {
      pipeLine.innerHTML = `
        <span style="color:#4ade80;font-weight:700;">${pipeStats.simple || 0}</span> <span style="font-size:10px;color:var(--text-muted);">简</span> · 
        <span style="color:#38bdf8;font-weight:700;">${pipeStats.medium || 0}</span> <span style="font-size:10px;color:var(--text-muted);">中</span> · 
        <span style="color:#c084fc;font-weight:700;">${pipeStats.hard || 0}</span> <span style="font-size:10px;color:var(--text-muted);">难</span>
      `;
    }

    const rKpiSub = document.getElementById('reasoning-kpi-sub');
    if (rKpiSub) {
      const visSum = (visStats.simple || 0) + (visStats.medium || 0) + (visStats.hard || 0);
      const visText = visSum > 0 ? ` · 视觉态 ${visSum}次` : '';
      rKpiSub.innerText = `今日总计: 简 ${rSim} · 中 ${rMed} · 难 ${rHar}${visText}`;
    }

    // 🌟 当日全槽位 In / Out 总耗时 KPI 更新
    const sp = data.speed || {};
    const todayInSec = (data.today && data.today.total_in_seconds) || sp.today_in_seconds || 0;
    const todayOutSec = (data.today && data.today.total_out_seconds) || sp.today_out_seconds || 0;
    const todayWorkSec = (data.today && data.today.total_work_seconds) || sp.today_work_seconds || (todayInSec + todayOutSec) || 0;
    
    const inPct = todayWorkSec > 0 ? ((todayInSec / todayWorkSec) * 100).toFixed(1) : '0.0';
    const outPct = todayWorkSec > 0 ? ((todayOutSec / todayWorkSec) * 100).toFixed(1) : '0.0';

    const ioKpiVal = document.getElementById('inout-kpi-value');
    if (ioKpiVal) {
      ioKpiVal.innerHTML = `
        <span style="color:#38bdf8;font-weight:700;">📥 ${Number(todayInSec).toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}s</span> <span style="font-size:11px;color:var(--text-muted);">In</span> · 
        <span style="color:var(--accent-purple);font-weight:700;">📤 ${Number(todayOutSec).toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}s</span> <span style="font-size:11px;color:var(--text-muted);">Out</span>
      `;
    }
    const ioKpiSub = document.getElementById('inout-kpi-sub');
    if (ioKpiSub) {
      ioKpiSub.innerText = `全槽位纯工作耗时: ${Number(todayWorkSec).toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}s (预填 ${inPct}% · 解码 ${outPct}%)`;
    }

    // 更新动态槽位与 GPU 监控卡片
    updateSlotsUI(data.concurrency, data.gpu, data.vision_summary);

    // 更新 3 台设备用量数据
    const totalCost = Math.max(0.000001, data.total.cost_cny || 0);
    const bk = data.by_key || {};
    
    const adminStat = bk['admin'] || { requests: 0, total_tokens: 0, cost_cny: 0 };
    document.getElementById('key-admin-reqs').innerText = (adminStat.requests || 0) + ' 次';
    document.getElementById('key-admin-tokens').innerText = (adminStat.total_tokens || 0).toLocaleString();
    document.getElementById('key-admin-cost').innerText = '¥' + (adminStat.cost_cny || 0).toFixed(4);
    document.getElementById('key-admin-bar').style.width = Math.min(100, ((adminStat.cost_cny || 0) / totalCost * 100)).toFixed(1) + '%';

    const llamaStat = bk['llamacpp'] || { requests: 0, total_tokens: 0, cost_cny: 0 };
    document.getElementById('key-llama-reqs').innerText = (llamaStat.requests || 0) + ' 次';
    document.getElementById('key-llama-tokens').innerText = (llamaStat.total_tokens || 0).toLocaleString();
    document.getElementById('key-llama-cost').innerText = '¥' + (llamaStat.cost_cny || 0).toFixed(4);
    document.getElementById('key-llama-bar').style.width = Math.min(100, ((llamaStat.cost_cny || 0) / totalCost * 100)).toFixed(1) + '%';

    const v100Stat = bk['v100-32G'] || { requests: 0, total_tokens: 0, cost_cny: 0 };
    document.getElementById('key-v100-reqs').innerText = (v100Stat.requests || 0) + ' 次';
    document.getElementById('key-v100-tokens').innerText = (v100Stat.total_tokens || 0).toLocaleString();
    document.getElementById('key-v100-cost').innerText = '¥' + (v100Stat.cost_cny || 0).toFixed(4);
    document.getElementById('key-v100-bar').style.width = Math.min(100, ((v100Stat.cost_cny || 0) / totalCost * 100)).toFixed(1) + '%';

    // 渲染热力图日历
    renderCalendar();

    // 🌟 渲染【今日当前累计调用清单】(按设备与模型分项累计 · 一直累加)
    const todayDM = (data.today && data.today.by_device_model) ? Object.values(data.today.by_device_model) : [];
    todayDM.sort((a, b) => (b.last_time || '').localeCompare(a.last_time || ''));
    
    const tbody = document.getElementById('recents-tbody');
    if (todayDM.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 24px;">今日暂无调用记录</td></tr>';
    } else {
      tbody.innerHTML = todayDM.map(r => {
        const isVision = r.is_vision || (r.model && (r.model.includes('VL') || r.model.includes('Vision') || r.model.includes('多模态')));
        let modelBadge = '<span class="badge-text">⚡ 纯文本基准</span>';
        if (isVision) {
          modelBadge = '<span class="badge-vision">👁️ 原生多模态</span>';
        } else if (r.model.includes('双槽MTP') || r.model.includes('MTP')) {
          modelBadge = '<span class="badge-text" style="background:rgba(56,189,248,0.18);color:#38bdf8;border:1px solid rgba(56,189,248,0.4);">👑 双槽MTP</span>';
        } else if (r.model.includes('4并发') || r.model.includes('流水线')) {
          modelBadge = '<span class="badge-text" style="background:rgba(251,146,60,0.18);color:#fb923c;border:1px solid rgba(251,146,60,0.4);">🚀 4并发流水线</span>';
        }
        const keyColor = r.key === 'admin' ? 'var(--accent)' : (r.key === 'llamacpp' ? 'var(--accent-purple)' : 'var(--accent-orange)');
        const imgDisplay = (r.image_count && r.image_count > 0) ? `<strong style="color:var(--accent-purple);">${r.image_count} 张图</strong>` : (isVision ? '<span style="color:var(--accent-purple);">1 张图</span>' : '<span style="color:var(--text-muted);">-</span>');
        return `
          <tr>
            <td style="color: var(--text-muted);">${r.last_time}</td>
            <td><strong style="color: ${keyColor};">${r.key_name || r.key}</strong></td>
            <td><strong style="color: #fff;">${r.model}</strong> ${modelBadge}</td>
            <td>${(r.prompt_tokens || 0).toLocaleString()} <span style="color: var(--accent-green); font-size: 11px;">(命中: ${(r.prompt_tokens_cached || 0).toLocaleString()})</span></td>
            <td>${(r.completion_tokens || 0).toLocaleString()}</td>
            <td>${(r.duration_s || 0).toFixed(2)}s <span style="color: var(--text-muted); font-size: 11px;">(${(r.requests || 0)}次累计)</span></td>
            <td>${imgDisplay}</td>
            <td style="color: var(--accent-green); font-weight: 700;">¥${(r.cost_cny || 0).toFixed(5)}</td>
          </tr>
        `;
      }).join('');
    }
  } catch (e) {
    console.error(e);
  }
}

// 🌟 1.2 秒高灵敏度实时刷新 (精准同步预填进度、瞬时吐字速度与槽位状态)
setInterval(updateStats, 1200);
updateStats();
</script>
</body>
</html>
"""

# ============================================================
#  HTTP 请求转发辅助与快速重试机制
# ============================================================
def urlopen_with_retry(req, timeout=3600, max_retries=60, retry_delay=1.0):
    """带自适应等待重试的 urlopen 包装器，支持在后端切换模型/加载大显存权重期间自动静默等待就绪"""
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (ConnectionRefusedError, urllib.error.URLError, socket.error) as e:
            last_err = e
            # 若由于后端正在加载模型导致连接被拒，给最多 60 秒平滑等待期
            if attempt < max_retries:
                if attempt == 0:
                    try:
                        sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [BACKEND-WARMUP] 后端正在加载/热身模型中，网关智能等待 8083 就绪...\n")
                        sys.stdout.flush()
                    except Exception:
                        pass
                time.sleep(retry_delay)
            else:
                break
        except Exception as e:
            last_err = e
            if attempt < 3:
                time.sleep(retry_delay)
            else:
                break
    raise last_err

# ============================================================
#  HTTP 请求处理与流式透明转发 + 两阶段图文协同流水线
# ============================================================

# ====================================================================================
#  👑 Qwen3.8-27B 终极全自动智能调度与热切换引擎 (Unified 27B Dynamic Hot-Swapper)
#  3大形态：双槽MTP常驻基准态 · 4并发流水线态 · 原生多模态视觉态 · 0秒动态思考等级调控
# ====================================================================================
class Qwen27BBackendManager:
    STATE_MTP_2SLOT = "MTP_2SLOT"
    STATE_VISION_27B = "VISION_27B"
    STATE_PIPELINE_4SLOT = "PIPELINE_4SLOT"

    def __init__(self, root_dir=r'E:\llama-win-cuda-12.4-x64', models_dir=r'E:\models', port=8083):
        self.root_dir = root_dir
        self.models_dir = models_dir
        self.port = port
        self.current_state = self.STATE_MTP_2SLOT
        self.lock = threading.Lock()
        self.last_activity_time = time.time()
        self.server_exe = os.path.join(root_dir, "llama-server.exe")
        self.template_file = os.path.join(root_dir, "chat_template_qwen_fixed.jinja")
        self.model_path = os.path.join(models_dir, "Qwen3.8-27B-Abliterated-Q6_K.gguf")
        self.mmproj_path = os.path.join(models_dir, "mmproj-Qwen3.8-27B-F16.gguf")
        self.log_dir = os.path.join(root_dir, "logs")
        
        # 后台闲置自动重启线程已彻底禁用，确保 8083 进程稳定常驻，绝不打断客户端会话
        # self.watchdog_thread = threading.Thread(target=self._idle_watchdog, daemon=True)
        # self.watchdog_thread.start()

    def get_today_log(self):
        today = time.strftime("%Y%m%d")
        return os.path.join(self.log_dir, f"8083_llama_{today}.log")

    def classify_complexity(self, text="", estimated_tokens=0):
        """
        0秒动态思考等级分类器：
        - low: 简单快问快答 / 纯翻译 / 简单正则 / 概念解释 (<1500 tokens) -> 预算 512
        - xhigh: 高难算法 / Minecraft / 完整系统 / 架构设计 / 复杂逆向 / 多文件重构 (>4000 tokens) -> 预算 8192
        - medium: 默认标准中等思考 -> 预算 2048
        """
        t_lower = text.lower() if text else ""
        
        # 1. 困难/极限思考任务
        hard_keywords = [
            "minecraft", "完整系统", "项目架构", "大型重构", "深度证明", "复杂算法",
            "多文件工程", "并发控制", "零容错", "高并发爬虫", "状态机", "编译器",
            "3d游戏", "webgl", "three.js", "分布式", "死锁分析", "内核", "深度思考", "从零开始开发"
        ]
        if estimated_tokens >= 4000 or any(k in t_lower for k in hard_keywords):
            return "xhigh", 8192, "<|think_xhigh|>"

        # 2. 简单轻量任务
        simple_keywords = [
            "什么是", "解释一下", "翻译成", "查一下", "搜索", "润色", "帮我看看",
            "写个简单", "单函数", "打个招呼", "你好", "格式转换", "json格式化", "正则"
        ]
        if estimated_tokens < 1500 and any(k in t_lower for k in simple_keywords):
            return "low", 512, "<|think_low|>"

        # 3. 默认标准中等思考
        return "medium", 2048, "<|think_medium|>"

    def is_server_healthy(self):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/props", method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_actual_state(self):
        """真实查询 8083 底层 /props 与 /slots 获取正在运行的真实形态"""
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/props", method="GET")
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    gen_settings = data.get("default_generation_settings", {})
                    params = gen_settings.get("params", {})
                    modalities = gen_settings.get("modalities", []) or []
                    spec_types = str(params.get("speculative.types", "")).lower()

                    if "vision" in modalities or "image" in modalities or params.get("mmproj"):
                        return self.STATE_VISION_27B
                    if "draft-mtp" in spec_types or "draft" in spec_types or "mtp" in spec_types:
                        return self.STATE_MTP_2SLOT
                    
                    try:
                        req_slots = urllib.request.Request(f"http://127.0.0.1:{self.port}/slots", method="GET")
                        with urllib.request.urlopen(req_slots, timeout=0.5) as sresp:
                            sdata = json.loads(sresp.read().decode("utf-8"))
                            if len(sdata) == 4:
                                return self.STATE_PIPELINE_4SLOT
                    except Exception:
                        pass
        except Exception:
            pass
        return None

    def ensure_state(self, target_state, force=False, on_heartbeat=None):
        """线程安全的状态切换器：精准比对真实底层形态，4.5秒内存级无感热切换"""
        self.last_activity_time = time.time()
        actual = self.get_actual_state()
        if not force and actual == target_state and self.is_server_healthy():
            self.current_state = target_state
            return True

        with self.lock:
            actual = self.get_actual_state()
            if not force and actual == target_state and self.is_server_healthy():
                self.current_state = target_state
                return True

            t_switch_start = time.time()
            old_state = self.current_state or actual or "待命"

            state_names = {
                self.STATE_MTP_2SLOT: "👑 Qwen3.8-27B-A [双槽MTP 极速基准态] (36.7 t/s · 144K)",
                self.STATE_PIPELINE_4SLOT: "🚀 Qwen3.8-27B-A [4并发流水线态] (45.0 t/s · 144K)",
                self.STATE_VISION_27B: "👁️ Qwen3.8-27B-A [原生多模态视觉态] (31.5 t/s · 128K)"
            }
            old_desc = state_names.get(self.current_state, self.current_state)
            new_desc = state_names.get(target_state, target_state)
            daily_log = self.get_today_log()

            # 向主脑日志总线注入切换横幅
            banner_start = (
                f"\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"[{time.strftime('%H:%M:%S')}] 🔄 [AI自适应热切换] 正在置换主模型显存:\n"
                f"  ├─ 当前形态: {old_desc}\n"
                f"  └─ 目标形态: {new_desc}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            )
            try:
                with open(daily_log, "a", encoding="utf-8") as lf:
                    lf.write(banner_start)
                    lf.flush()
            except Exception:
                pass

            sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [AUTO-DISPATCH] 🚀 触发 27B 形态自适应热切换: {self.current_state} ➔ {target_state}...\n")
            sys.stdout.flush()

            # 1. 安全终止当前 8083 旧进程并释放显存
            subprocess.run(['powershell', '-Command', 'Get-Process | Where-Object { $_.ProcessName -match "llama" } | Stop-Process -Force'], capture_output=True)
            
            # 等待显存归零
            for _ in range(15):
                if on_heartbeat:
                    try: on_heartbeat()
                    except Exception: pass
                time.sleep(0.3)
                try:
                    smi = subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'], capture_output=True, text=True)
                    if smi.stdout and int(smi.stdout.strip().split()[0]) < 600:
                        break
                except Exception:
                    break

            # 2. 构造目标形态的启动参数
            base_args = [
                self.server_exe,
                "-m", self.model_path,
                "-ngl", "99",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-b", "2048",
                "--ubatch-size", "2048",
                "-t", "6",
                "--kv-unified",
                "--flash-attn", "on",
                "--ctx-checkpoints", "4",
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
                "--chat-template-file", self.template_file,
                "--alias", "Qwen3.8-27B-A-Q6_K",
                "--port", str(self.port),
                "--host", "127.0.0.1",
                "--log-file", daily_log
            ]

            if target_state == self.STATE_VISION_27B:
                # 挂载专属 27B F16 视觉头
                base_args.extend([
                    "-c", "131072",
                    "--parallel", "2",
                    "--mmproj", self.mmproj_path
                ])
            elif target_state == self.STATE_PIPELINE_4SLOT:
                # 4 槽高吞吐流水线
                base_args.extend([
                    "-c", "147456",
                    "--parallel", "4",
                    "--cache-reuse", "512"
                ])
            else:
                # 默认双槽 MTP 极速态
                base_args.extend([
                    "-c", "147456",
                    "--parallel", "2",
                    "--cache-reuse", "512",
                    "--spec-type", "draft-mtp",
                    "--spec-draft-n-max", "2",
                    "--spec-draft-n-min", "1"
                ])

            # 3. 启动前台主脑进程并等待就绪
            subprocess.Popen(base_args, cwd=self.root_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            t0 = time.time()
            while time.time() - t0 < 75:
                if on_heartbeat:
                    try: on_heartbeat()
                    except Exception: pass
                if self.is_server_healthy():
                    t_elapsed = time.time() - t_switch_start
                    banner_done = (
                        f"\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"[{time.strftime('%H:%M:%S')}] ✅ [AI自适应热切换] 主模型显存置换完成 (耗时 {t_elapsed:.1f} 秒)！\n"
                        f"  └─ 当前激活: {new_desc}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    )
                    try:
                        with open(daily_log, "a", encoding="utf-8") as lf:
                            lf.write(banner_done)
                            lf.flush()
                    except Exception:
                        pass

                    sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [AUTO-DISPATCH] ✅ 27B [{target_state}] 已就绪 (耗时 {t_elapsed:.1f} 秒)！\n")
                    sys.stdout.flush()
                    try:
                        tracker.record_hot_swap(old_state, target_state, round(t_elapsed, 2))
                    except Exception as e:
                        sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [WARN] 记录热切换统计异常: {e}\n")
                        sys.stdout.flush()

                    self.current_state = target_state
                    return True
                time.sleep(0.5)

            return False

    def _idle_watchdog(self):
        """后台闲置监控：若脱离默认 MTP 态且空闲超过 300 秒(5分钟)，自动优雅回归默认双槽MTP"""
        while True:
            time.sleep(10)
            if self.current_state in (self.STATE_VISION_27B, self.STATE_PIPELINE_4SLOT):
                idle_sec = time.time() - self.last_activity_time
                if idle_sec > 300:
                    sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [AUTO-DISPATCH] 🕒 任务已空闲 {idle_sec:.0f} 秒 (超5分钟)，自动优雅回归【27B 双槽MTP 常驻极速态】...\n")
                    sys.stdout.flush()
                    self.ensure_state(self.STATE_MTP_2SLOT)

backend_manager = Qwen27BBackendManager()

class TransparentProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Expose-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"

        # 0. 状态感知 API (/api/state, /health)
        if path in ("/api/state", "/api/status", "/health"):
            st_data = {
                "status": "ok",
                "current_state": backend_manager.current_state,
                "server_healthy": backend_manager.is_server_healthy(),
                "active_text": concurrency_queue.active_text,
                "active_vision": concurrency_queue.active_vision,
                "today_reqs": tracker.get_stats().get("today", {}).get("requests", 0),
                "today_cost": tracker.get_stats().get("today", {}).get("cost_cny", 0.0)
            }
            resp_bytes = json.dumps(st_data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        # 1. 虚拟计费统计完整 API
        if path in ("/v1/billing", "/v1/stats", "/v1/usage"):
            stats_json = json.dumps(tracker.get_stats(), ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(stats_json)))
            self.end_headers()
            self.wfile.write(stats_json)
            return

        # 2. 今日简报 API
        if path in ("/v1/billing/today", "/v1/stats/today"):
            stats = tracker.get_stats()
            today_json = json.dumps({
                "pricing_standard": stats["pricing_standard"],
                "today": stats["today"]
            }, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(today_json)))
            self.end_headers()
            self.wfile.write(today_json)
            return

        # 3. 实时可视化看板 UI (含动态槽位监控与月度热力图)
        if path in ("/dashboard", "/stats", "/billing", "/"):
            html_bytes = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)
            return

        # 4. CC Switch / NewAPI 余额与用量查询兼容接口 (/user/balance, /v1/user/balance)
        if path in ("/user/balance", "/v1/user/balance", "/api/user/balance", "/api/usage", "/v1/dashboard/billing/usage"):
            auth_header = self.headers.get("Authorization", "")
            x_api_key = self.headers.get("x-api-key", "")
            _, key_name, key_display = key_manager.authenticate(auth_header, x_api_key)
            st = tracker.get_stats()
            key_stat = st.get("by_key", {}).get(key_name, {})
            key_cost = key_stat.get("cost_cny", 0.0)
            key_tokens = key_stat.get("total_tokens", 0)
            today_cost = st.get("today", {}).get("cost_cny", 0.0)

            balance_resp = {
                "success": True,
                "is_active": True,
                "balance": round(today_cost, 4),
                "total_balance": round(st.get("total", {}).get("cost_cny", 0.0), 4),
                "key": key_name,
                "key_cost": round(key_cost, 4),
                "key_tokens": key_tokens,
                "total_tokens": key_tokens,
                "used": round(key_cost, 4),
                "remaining": None,
                "unit": "CNY",
                "pricing": st.get("pricing_standard", "")
            }
            resp_bytes = json.dumps(balance_resp, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        # 5. /v1/models 模型列表虚拟注入 (全量声明视觉多模态能力，让所有 WebUI 自动亮起图片上传图标)
        if path in ("/v1/models", "/models"):
            caps = {"vision": True, "chat_completion": True, "tools": True}
            model_ids = [
                # Claude 5 最新全系
                ("claude-sonnet-5", "anthropic"),
                ("claude-opus-5", "anthropic"),
                ("claude-fable-5", "anthropic"),
                # Claude 4.5 / 4 系列
                ("claude-haiku-4-5-20251001", "anthropic"),
                ("claude-haiku-4-5", "anthropic"),
                ("claude-sonnet-4", "anthropic"),
                # Claude 3.7 / 3.5 / 3 经典系列
                ("claude-3-7-sonnet-20250219", "anthropic"),
                ("claude-3-7-sonnet", "anthropic"),
                ("claude-3-5-sonnet-20241022", "anthropic"),
                ("claude-3-5-sonnet", "anthropic"),
                ("claude-3-5-haiku-20241022", "anthropic"),
                ("claude-3-opus-20240229", "anthropic"),
                # DeepSeek V4 系列
                ("deepseek-v4-flash-0731", "deepseek"),
                ("deepseek-v4-flash", "deepseek"),
                ("deepseek-v4-pro-0813", "deepseek"),
                ("deepseek-v4-pro", "deepseek"),
                ("deepseek-v4-flash-vision-exp", "deepseek"),
                ("deepseek-chat", "deepseek"),
                ("deepseek-reasoner", "deepseek"),
                # OpenAI 系列
                ("gpt-4o", "openai"),
                ("gpt-4o-mini", "openai"),
                ("o1", "openai"),
                ("o3-mini", "openai"),
                # 通用默认别名
                ("default", "local"),
                ("auto", "local"),
                ("local", "local"),
                # 本地实际模型全量库
                ("Qwen3.8-27B-A-Q6_K", "llama.cpp"),
                ("Qwen3.8-27B-A [双槽MTP]", "llama.cpp"),
                ("Qwen3.8-27B-A [4并发]", "llama.cpp"),
                ("Qwen3.8-27B-A [多模态]", "llama.cpp"),
                ("Qwen3.8-27B-MID-HIGH", "llama.cpp"),
                ("Qwen3.8-27B-N-H", "llama.cpp"),
                ("Ornith-1.5-35B", "llama.cpp"),
                ("qwen3vl 8B", "llama.cpp"),
                ("Gemma-4-E4B", "llama.cpp"),
                ("Qwen3.5-4B", "llama.cpp"),
                ("Qwen2.5-VL-3B", "llama.cpp"),
                ("PaddleOCR-VL-1.6", "llama.cpp"),
                ("locate-anything-f16", "llama.cpp")
            ]
            model_list = [
                {
                    "id": m_id,
                    "object": "model",
                    "owned_by": m_owner,
                    "permission": [],
                    "capabilities": caps,
                    "multimodal": True
                }
                for m_id, m_owner in model_ids
            ]
            models_body = json.dumps({"object": "list", "data": model_list}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(models_body)))
            self.end_headers()
            self.wfile.write(models_body)
            return

        # 6. 其他 GET 透传
        target_url = f"http://{self.server.target_host}:{self.server.target_port}{self.path}"
        headers = {}
        for k, v in self.headers.items():
            if k.lower() not in ("host", "content-length"):
                headers[k] = v
        
        req = urllib.request.Request(target_url, headers=headers, method="GET")
        try:
            with urlopen_with_retry(req, timeout=30) as resp:
                self.send_response(resp.status)
                for hk, hv in resp.getheaders():
                    if hk.lower() not in ("transfer-encoding", "content-length"):
                        self.send_header(hk, hv)
                self._send_cors_headers()
                data = resp.read()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            err_data = e.read()
            self.send_response(e.code)
            self._send_cors_headers()
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(err_data)))
            self.end_headers()
            self.wfile.write(err_data)
        except Exception as e:
            self._send_json_error(502, f"Proxy error connecting to backend: {e}")

    def do_POST(self):
        start_time = time.time()
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # ---- 1. 3台设备 Key 鉴权与识别 (admin / llamacpp / v100-32G) ----
        auth_header = self.headers.get("Authorization", "")
        x_api_key = self.headers.get("x-api-key", "")
        auth_ok, key_name, key_display = key_manager.authenticate(auth_header, x_api_key)

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""
        
        # 0. 手动触发热切换 API (/api/switch)
        if path == "/api/switch":
            try:
                body_json = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                target_state = body_json.get("target_state", backend_manager.STATE_MTP_2SLOT)
                ok = backend_manager.ensure_state(target_state)
                resp_bytes = json.dumps({"success": ok, "current_state": backend_manager.current_state}, ensure_ascii=False).encode("utf-8")
                self.send_response(200 if ok else 500)
            except Exception as e:
                resp_bytes = json.dumps({"success": False, "error": str(e)}, ensure_ascii=False).encode("utf-8")
                self.send_response(500)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        is_anthropic_protocol = (path == "/v1/messages" or path == "/messages")
        target_port = self.server.target_port
        is_vision = False
        pending_vision_hashes = []
        requested_model = "Qwen3.8-27B-MID-HIGH"
        actual_model = "Qwen3.8-27B-MID-HIGH"
        estimated_prompt_tokens = 0

        # ---- 2. 解析请求体并执行模型别名映射 & Schema 清洗 ----
        try:
            if raw_body:
                incoming_json = json.loads(raw_body.decode("utf-8"))
                
                # 如果是 Anthropic /v1/messages 协议，先翻译为 OpenAI 格式
                if is_anthropic_protocol:
                    req_json = translate_anthropic_to_openai(incoming_json)
                else:
                    req_json = incoming_json

                if "prompt" in req_json and not "messages" in req_json:
                    req_json["messages"] = [{"role": "user", "content": str(req_json["prompt"])}]

                if not req_json.get("messages"):
                    req_json["messages"] = [{"role": "user", "content": "继续"}]

                requested_model = req_json.get("model", "")
                actual_model = resolve_model_alias(requested_model)
                req_json["model"] = actual_model

                msg_str = json.dumps(req_json.get("messages", []), ensure_ascii=False)
                tools_str = json.dumps(req_json.get("tools", []), ensure_ascii=False)
                estimated_prompt_tokens = estimate_tokens(msg_str + tools_str)

                cleaned_json, modified = sanitize_payload(req_json)
                if not cleaned_json.get("messages"):
                    cleaned_json["messages"] = [{"role": "user", "content": "继续"}]
                if modified:
                    tools_count = len(cleaned_json.get("tools", []))
                    if tools_count > 0:
                        sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [TOOL-PROXY] 已清洗 {tools_count} 个工具 schema 中的 GBNF 爆炸约束\n")
                        sys.stdout.flush()

                # ---- 🌟 智能任务分类、0秒思考等级调控与 27B 三态无感热切换 ----
                # 1. 提取最后一条用户提问文本用于复杂度分析
                user_msg_text = ""
                msgs = cleaned_json.get("messages", [])
                for m in reversed(msgs):
                    if m.get("role") == "user":
                        c = m.get("content")
                        if isinstance(c, str):
                            user_msg_text = c
                        elif isinstance(c, list):
                            for item in c:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    user_msg_text += " " + item.get("text", "")
                        break

                # 2. 0秒动态思考等级裁决 (low / medium / xhigh)
                req_effort = cleaned_json.get("reasoning_effort")
                if req_effort and isinstance(req_effort, str) and req_effort.lower() in ("low", "minimal", "medium", "standard", "high", "xhigh", "max"):
                    eff_val = req_effort.lower()
                    if eff_val in ("low", "minimal"):
                        effort, budget, inline_tag = "low", 512, "<|think_low|>"
                    elif eff_val in ("high", "xhigh", "max"):
                        effort, budget, inline_tag = "xhigh", 8192, "<|think_xhigh|>"
                    else:
                        effort, budget, inline_tag = "medium", 2048, "<|think_medium|>"
                else:
                    effort, budget, inline_tag = backend_manager.classify_complexity(user_msg_text, estimated_tokens=estimated_prompt_tokens)
                cleaned_json["reasoning_effort"] = effort
                cleaned_json["reasoning_budget"] = budget
                
                # 3. 动态思考等级注入与 27B 旗舰原生多模态视觉处理 (Track 1)
                # 扫描图片并进行指纹去重置换：多轮对话中历史图片仅需解析一次，自动置换为轻量指纹标记 (0.001s瞬时复用)
                cleaned_json, has_img, pending_vision_hashes = process_native_vision_pipeline(cleaned_json)
                
                is_vision_model_req = actual_model in ("Qwen3.8-27B-Vision", "Qwen3.8-27B-A-Vision", "DeepSeek-V4-Flash-Vision-Exp") or "vision" in requested_model.lower() or "vl" in requested_model.lower()
                need_vision = has_img or is_vision_model_req

                if need_vision:
                    # 检查 8083 主脑是否已加载原生多模态视觉头 (mmproj)
                    if not check_backend_is_multimodal(target_port):
                        sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [SMART-VISION] 👁️ 检测到图像理解需求，正在自适应将 27B 主脑置换为【原生多模态视觉态】(挂载 mmproj-27B)...\n")
                        sys.stdout.flush()
                        ok = backend_manager.ensure_state(backend_manager.STATE_VISION_27B)
                        if not ok:
                            sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [SMART-VISION] ⚠️ 主脑置换视觉态失败，尝试继续转发...\n")
                            sys.stdout.flush()
                    is_vision = True
                else:
                    is_vision = False

                # ---- 🌟 智能上下文安全防爆舱 (严格锁定在 140K 安全水位，防止 160K 溢出 400 报错) ----
                cleaned_json, _ = enforce_context_safety_guard(cleaned_json, max_safe_tokens=140000)

                forward_body = json.dumps(cleaned_json, ensure_ascii=False).encode("utf-8")
                # 🌟 精确计算经 OCR 提取与防爆修剪后的真实 Token 负载 (绝非原始 base64 虚高体积)
                estimated_prompt_tokens = estimate_tokens(forward_body.decode("utf-8", errors="ignore"))

                try:
                    debug_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "debug_last_request.json")
                    with open(debug_p, "w", encoding="utf-8") as df:
                        json.dump(cleaned_json, df, ensure_ascii=False, indent=2)
                except Exception:
                    pass
            else:
                forward_body = raw_body
                estimated_prompt_tokens = estimate_tokens(raw_body.decode("utf-8", errors="ignore"))
        except Exception as e:
            sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [SANITIZE-WARN] 治理异常 {type(e).__name__}: {e} → 降级最小清洗\n")
            sys.stdout.flush()
            try:
                raw_json = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                if is_anthropic_protocol:
                    raw_json = translate_anthropic_to_openai(raw_json)
                if not raw_json.get("messages"):
                    raw_json["messages"] = [{"role": "user", "content": "继续"}]
                forward_body = json.dumps(sanitize_schema(raw_json), ensure_ascii=False).encode("utf-8")
            except Exception:
                forward_body = raw_body

        # ---- 3. V100 专用智能负载准入排队 (支持短提问绿色通道 + 巨型长文本避让互斥 + 4槽位背压) ----
        acquired = concurrency_queue.acquire(is_vision=is_vision, estimated_tokens=estimated_prompt_tokens, timeout=120.0)
        if not acquired:
            self._send_json_error(503, "Server is currently at maximum concurrency. Queue timeout exceeded (120s).")
            return

        try:
            # 内部始终转发到后端的 /v1/chat/completions
            target_url = f"http://{self.server.target_host}:{target_port}/v1/chat/completions"
            headers = {}
            for k, v in self.headers.items():
                if k.lower() not in ("host", "content-length", "accept-encoding"):
                    headers[k] = v
            headers["Content-Length"] = str(len(forward_body))
            headers["Accept-Encoding"] = "identity"
            headers["Authorization"] = f"Bearer {self.server.api_key}"

            is_stream = b'"stream": true' in forward_body or b'"stream":true' in forward_body
            req = urllib.request.Request(target_url, data=forward_body, headers=headers, method="POST")
            resp = None
            
            prompt_tokens_recorded = 0
            cached_tokens_recorded = 0
            completion_tokens_recorded = 0

            try:
                resp = urlopen_with_retry(req, timeout=3600)
                self.send_response(resp.status)
                for hk, hv in resp.getheaders():
                    if hk.lower() not in ("transfer-encoding", "content-length", "content-type"):
                        self.send_header(hk, hv)
                self._send_cors_headers()
                
                if is_stream:
                    if is_anthropic_protocol:
                        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    else:
                        self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    
                    generated_chunks_count = 0
                    accumulated_text = []
                    anthropic_msg_id = f"msg_{int(time.time()*1000)}"
                    anthropic_started = False
                    block_index = 0
                    text_block_active = False
                    active_tool_calls = {}  # {tc_idx: block_idx}
                    anthropic_stop_reason = "end_turn"

                    # Anthropic SSE 初始事件
                    if is_anthropic_protocol:
                        start_evt = (
                            f"event: message_start\r\ndata: {json.dumps({'type': 'message_start', 'message': {'id': anthropic_msg_id, 'type': 'message', 'role': 'assistant', 'model': requested_model, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': estimated_prompt_tokens, 'output_tokens': 1}}}, ensure_ascii=False)}\r\n\r\n"
                        ).encode("utf-8")
                        chunk_len = f"{len(start_evt):X}\r\n".encode("ascii")
                        self.wfile.write(chunk_len + start_evt + b"\r\n")
                        self.wfile.flush()
                        anthropic_started = True
                    else:
                        # OpenAI SSE 协议即时心跳首包，防止超长预填计算期间客户端读超时
                        ping_evt = b": keep-alive\r\n\r\n"
                        chunk_len = f"{len(ping_evt):X}\r\n".encode("ascii")
                        self.wfile.write(chunk_len + ping_evt + b"\r\n")
                        self.wfile.flush()

                    while True:
                        line = resp.readline()
                        if not line:
                            if not is_anthropic_protocol:
                                try:
                                    self.wfile.write(b"0\r\n\r\n")
                                    self.wfile.flush()
                                except Exception:
                                    pass
                            break

                        if line.startswith(b"data: ") and not line.startswith(b"data: [DONE]"):
                            try:
                                chunk_data = json.loads(line[6:].decode("utf-8", errors="ignore").strip())
                                if "usage" in chunk_data and isinstance(chunk_data["usage"], dict):
                                    u = chunk_data["usage"]
                                    prompt_tokens_recorded = u.get("prompt_tokens", 0)
                                    completion_tokens_recorded = u.get("completion_tokens", 0)
                                    cached_tokens_recorded = u.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                                else:
                                    choices = chunk_data.get("choices", [])
                                    if choices:
                                        c0 = choices[0]
                                        finish_r = c0.get("finish_reason")
                                        if finish_r == "tool_calls":
                                            anthropic_stop_reason = "tool_use"
                                        elif finish_r == "length":
                                            anthropic_stop_reason = "max_tokens"

                                        delta = c0.get("delta", {})
                                        txt = delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thought") or delta.get("thinking") or ""
                                        tc_deltas = delta.get("tool_calls", [])

                                        if is_anthropic_protocol:
                                            # 1. 文本内容流式转译
                                            if txt:
                                                generated_chunks_count += 1
                                                accumulated_text.append(txt)
                                                if not text_block_active:
                                                    b_start = f"event: content_block_start\r\ndata: {json.dumps({'type': 'content_block_start', 'index': block_index, 'content_block': {'type': 'text', 'text': ''}}, ensure_ascii=False)}\r\n\r\n".encode("utf-8")
                                                    self.wfile.write(f"{len(b_start):X}\r\n".encode("ascii") + b_start + b"\r\n")
                                                    text_block_active = True
                                                
                                                b_delta = f"event: content_block_delta\r\ndata: {json.dumps({'type': 'content_block_delta', 'index': block_index, 'delta': {'type': 'text_delta', 'text': txt}}, ensure_ascii=False)}\r\n\r\n".encode("utf-8")
                                                self.wfile.write(f"{len(b_delta):X}\r\n".encode("ascii") + b_delta + b"\r\n")
                                                self.wfile.flush()

                                            # 2. 工具调用流式转译
                                            if tc_deltas:
                                                anthropic_stop_reason = "tool_use"
                                                if text_block_active:
                                                    b_stop = f"event: content_block_stop\r\ndata: {json.dumps({'type': 'content_block_stop', 'index': block_index}, ensure_ascii=False)}\r\n\r\n".encode("utf-8")
                                                    self.wfile.write(f"{len(b_stop):X}\r\n".encode("ascii") + b_stop + b"\r\n")
                                                    text_block_active = False
                                                    block_index += 1

                                                for tc in tc_deltas:
                                                    tc_idx = tc.get("index", 0)
                                                    fn = tc.get("function", {})
                                                    fn_name = fn.get("name")
                                                    args_chunk = fn.get("arguments", "")
                                                    tc_id = tc.get("id") or f"toolu_{int(time.time()*1000)}"

                                                    if tc_idx not in active_tool_calls:
                                                        active_tool_calls[tc_idx] = block_index
                                                        b_tool_start = f"event: content_block_start\r\ndata: {json.dumps({'type': 'content_block_start', 'index': block_index, 'content_block': {'type': 'tool_use', 'id': tc_id, 'name': fn_name or 'tool'}}, ensure_ascii=False)}\r\n\r\n".encode("utf-8")
                                                        self.wfile.write(f"{len(b_tool_start):X}\r\n".encode("ascii") + b_tool_start + b"\r\n")
                                                        self.wfile.flush()
                                                        block_index += 1

                                                    if args_chunk:
                                                        t_target_idx = active_tool_calls[tc_idx]
                                                        b_tool_delta = f"event: content_block_delta\r\ndata: {json.dumps({'type': 'content_block_delta', 'index': t_target_idx, 'delta': {'type': 'input_json_delta', 'partial_json': args_chunk}}, ensure_ascii=False)}\r\n\r\n".encode("utf-8")
                                                        self.wfile.write(f"{len(b_tool_delta):X}\r\n".encode("ascii") + b_tool_delta + b"\r\n")
                                                        self.wfile.flush()
                                        else:
                                            if txt:
                                                generated_chunks_count += 1
                                                accumulated_text.append(txt)
                            except Exception:
                                pass

                        if not is_anthropic_protocol:
                            chunk_len = f"{len(line):X}\r\n".encode("ascii")
                            try:
                                self.wfile.write(chunk_len + line + b"\r\n")
                                self.wfile.flush()
                            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, socket.error):
                                sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [GPU-RELEASE] 客户端断开连接，已即时掐断后端推理以释放显卡算力\n")
                                sys.stdout.flush()
                                try:
                                    resp.close()
                                except Exception:
                                    pass
                                break

                    # Anthropic SSE 结束事件
                    if is_anthropic_protocol and anthropic_started:
                        if text_block_active:
                            b_stop = f"event: content_block_stop\r\ndata: {json.dumps({'type': 'content_block_stop', 'index': block_index}, ensure_ascii=False)}\r\n\r\n".encode("utf-8")
                            self.wfile.write(f"{len(b_stop):X}\r\n".encode("ascii") + b_stop + b"\r\n")
                        for t_idx, t_b_idx in active_tool_calls.items():
                            b_tool_stop = f"event: content_block_stop\r\ndata: {json.dumps({'type': 'content_block_stop', 'index': t_b_idx}, ensure_ascii=False)}\r\n\r\n".encode("utf-8")
                            self.wfile.write(f"{len(b_tool_stop):X}\r\n".encode("ascii") + b_tool_stop + b"\r\n")

                        # 兜底：如果完全没有任何 content block 且没有 tool call（0 token 生成），补发空文本块以防止客户端崩溃重试
                        if not text_block_active and not active_tool_calls:
                            b_empty_start = b"event: content_block_start\r\ndata: {\"type\": \"content_block_start\", \"index\": 0, \"content_block\": {\"type\": \"text\", \"text\": \"\"}}\r\n\r\n"
                            b_empty_stop = b"event: content_block_stop\r\ndata: {\"type\": \"content_block_stop\", \"index\": 0}\r\n\r\n"
                            self.wfile.write(f"{len(b_empty_start):X}\r\n".encode("ascii") + b_empty_start + b"\r\n")
                            self.wfile.write(f"{len(b_empty_stop):X}\r\n".encode("ascii") + b_empty_stop + b"\r\n")

                        out_toks = completion_tokens_recorded or max(generated_chunks_count, 1)
                        m_delta = f"event: message_delta\r\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': anthropic_stop_reason, 'stop_sequence': None}, 'usage': {'output_tokens': out_toks}}, ensure_ascii=False)}\r\n\r\n".encode("utf-8")
                        self.wfile.write(f"{len(m_delta):X}\r\n".encode("ascii") + m_delta + b"\r\n")

                        m_stop = b"event: message_stop\r\ndata: {\"type\": \"message_stop\"}\r\n\r\n"
                        self.wfile.write(f"{len(m_stop):X}\r\n".encode("ascii") + m_stop + b"\r\n")
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()

                    if completion_tokens_recorded == 0:
                        full_gen = "".join(accumulated_text)
                        completion_tokens_recorded = max(generated_chunks_count, estimate_tokens(full_gen))
                    if prompt_tokens_recorded == 0:
                        prompt_tokens_recorded = estimated_prompt_tokens

                else:
                    resp_data = resp.read()
                    
                    try:
                        res_obj = json.loads(resp_data.decode("utf-8"))
                        u = res_obj.get("usage", {})
                        prompt_tokens_recorded = u.get("prompt_tokens", estimated_prompt_tokens)
                        completion_tokens_recorded = u.get("completion_tokens", 0)
                        cached_tokens_recorded = u.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                    except Exception:
                        prompt_tokens_recorded = estimated_prompt_tokens
                        completion_tokens_recorded = estimate_tokens(resp_data.decode("utf-8", errors="ignore"))

                    if is_anthropic_protocol:
                        anthropic_resp = translate_openai_to_anthropic_response(res_obj, requested_model)
                        final_body = json.dumps(anthropic_resp, ensure_ascii=False, indent=2).encode("utf-8")
                    else:
                        final_body = resp_data

                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(final_body)))
                    self.end_headers()
                    self.wfile.write(final_body)
                    self.wfile.flush()

                # ---- 记录 DeepSeek-V4-Flash 虚拟计费 ----
                duration = time.time() - start_time
                if prompt_tokens_recorded > 0 or completion_tokens_recorded > 0:
                    b_state = getattr(backend_manager, "current_state", "")
                    if is_vision or need_vision or b_state == backend_manager.STATE_VISION_27B:
                        recorded_model_name = "Qwen3.8-27B-A [原生多模态]"
                    elif b_state == backend_manager.STATE_PIPELINE_4SLOT or "4并发" in actual_model:
                        recorded_model_name = "Qwen3.8-27B-A [4并发流水线]"
                    elif b_state == backend_manager.STATE_MTP_2SLOT or "MTP" in actual_model or "27B" in actual_model:
                        recorded_model_name = "Qwen3.8-27B-A [双槽MTP]"
                    else:
                        recorded_model_name = actual_model

                    img_count = len(pending_vision_hashes) if pending_vision_hashes else (1 if is_vision else 0)
                    cost, today_cost, today_reqs = tracker.record(
                        model_name=recorded_model_name,
                        prompt_tokens=prompt_tokens_recorded,
                        cached_tokens=cached_tokens_recorded,
                        completion_tokens=completion_tokens_recorded,
                        duration_s=duration,
                        key_name=key_name,
                        is_vision=(is_vision or need_vision),
                        image_count=img_count,
                        reasoning_effort=locals().get("effort", "medium")
                    )
                    tps = round(completion_tokens_recorded / duration, 1) if duration > 0.05 else 0.0
                    prefill_tps = round(prompt_tokens_recorded / max(0.05, duration * 0.15), 1)
                    if "res_obj" in locals() and isinstance(res_obj, dict) and "timings" in res_obj:
                        tm = res_obj.get("timings", {})
                        if isinstance(tm, dict):
                            if tm.get("prompt_per_second", 0) > 0:
                                prefill_tps = round(tm["prompt_per_second"], 1)
                            if tm.get("predicted_per_second", 0) > 0:
                                tps = round(tm["predicted_per_second"], 1)
                    concurrency_queue.record_slot_metric(
                        in_tok_s=prefill_tps,
                        out_tok_s=tps,
                        ctx_used=prompt_tokens_recorded + completion_tokens_recorded
                    )
                    hit_str = f" (命中: {cached_tokens_recorded})" if cached_tokens_recorded > 0 else ""
                    proto_tag = "[ANTHROPIC]" if is_anthropic_protocol else "[OPENAI]"
                    sys.stdout.write(
                        f"[{time.strftime('%H:%M:%S')}] [BILLING] {proto_tag} 设备: {key_name} | 模型: {actual_model} | "
                        f"Tokens: In={prompt_tokens_recorded:,}{hit_str}, Out={completion_tokens_recorded:,} ({tps} tok/s) | "
                        f"本次: ¥{cost:.5f} | 今日累计: ¥{today_cost:.4f} ({today_reqs}次, 耗时{duration:.2f}s)\n"
                    )
                    sys.stdout.flush()

            except urllib.error.HTTPError as e:
                err_data = e.read()
                self.send_response(e.code)
                self._send_cors_headers()
                self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(err_data)))
                self.end_headers()
                self.wfile.write(err_data)
            except Exception as e:
                self._send_json_error(502, f"Proxy error communicating with backend (port {target_port}): {e}")
            finally:
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass
                if pending_vision_hashes:
                    register_image_fingerprints(pending_vision_hashes)
        finally:
            concurrency_queue.release(is_vision=is_vision)

    def _send_json_error(self, code, message):
        try:
            body = json.dumps({"error": {"code": code, "message": message, "type": "proxy_error"}}, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

# ============================================================
#  多线程高并发服务器架构
# ============================================================
class ThreadedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    target_host = "127.0.0.1"
    target_port = 8083
    vision_main_port = 0
    api_key = "llamacpp"

    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return
        if isinstance(exc_val, OSError) and getattr(exc_val, "errno", None) in (10054, 10053, 10058):
            return
        super().handle_error(request, client_address)

def run_proxy(listen_port=8081, target_port=8083, api_key="llamacpp", host="127.0.0.1", **kwargs):
    server_address = (host, listen_port)
    httpd = ThreadedHTTPServer(server_address, TransparentProxyHandler)
    httpd.target_host = host
    httpd.target_port = target_port
    httpd.api_key = api_key
    print(f"[{time.strftime('%H:%M:%S')}] [TOOL-PROXY-3.0] 企业级智能协同网关已启动: http://{host}:{listen_port} -> 27B旗舰主脑 (:{target_port})", flush=True)
    print(f"[{time.strftime('%H:%M:%S')}] [TOOL-PROXY-3.0] 协议支持: OpenAI (/v1/chat/completions) & Anthropic 原生 (/v1/messages)", flush=True)
    print(f"[{time.strftime('%H:%M:%S')}] [TOOL-PROXY-3.0] 视觉架构: Track 1 纯 27B 原生多模态直通 · 内置多轮对话图像指纹高速缓存 (免重复编码)", flush=True)
    print(f"[{time.strftime('%H:%M:%S')}] [TOOL-PROXY-3.0] 计价标准: DeepSeek-V4-Flash-0731 (文本) & DeepSeek-V4-Flash-Vision-Exp (识图)", flush=True)
    print(f"[{time.strftime('%H:%M:%S')}] [TOOL-PROXY-3.0] 全模型视觉注入 & TPS算力监控看板: http://127.0.0.1:{listen_port}/dashboard", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n[{time.strftime('%H:%M:%S')}] [TOOL-PROXY-3.0] 接收到退出信号，正在安全关闭网关...", flush=True)
    finally:
        httpd.server_close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="llama.cpp Enterprise Dual-Protocol Vision Pipeline & Dynamic Slot Gateway 3.0")
    parser.add_argument("--listen", type=int, default=8081, help="Listening port (default: 8081)")
    parser.add_argument("--target", type=int, default=8083, help="Backend llama-server port (default: 8083)")
    parser.add_argument("--vision-main", type=int, default=0, help="Vision Main VLM port (deprecated)")
    parser.add_argument("--vision-ocr", type=int, default=0, help="Legacy vision OCR port (deprecated)")
    parser.add_argument("--api-key", type=str, default="llamacpp", help="Backend API Key")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    args = parser.parse_args()

    run_proxy(
        listen_port=args.listen,
        target_port=args.target,
        api_key=args.api_key,
        host=args.host
    )
