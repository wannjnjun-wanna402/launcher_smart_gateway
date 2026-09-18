# -*- coding: utf-8 -*-
"""
AI 智能网关 (Port 8081 -> Backend 8083 主模型 / Sidecar 8085 视觉眼睛)
功能：
 1. 端口隔离与反向代理：对外统一暴露 8081 端口，底层主模型运行于 8083，侧挂视觉运行于 8085。
 2. 模型名动态映射：将客户端传入的各种名称（如 gpt-4o, claude, qwen, default）透明重写为后端活跃模型。
 3. 智能视觉路由与 8085 CPU 侧挂眼睛：
    - 若主模型具备原生识图能力：直接转发 8083 主模型并注入 "speculative.n_max": 0 旁路 MTP。
    - 若主模型无识图能力：自动将图片路由至 8085 (Qwen3VL-4B CPU 侧挂)，解析视觉细节/OCR后转译注入上下文交主模型推理。
 4. 思考等级智能协调 (Reasoning Effort)：根据任务难度与关键词自动在 low / medium / high 三档间协调。
 5. 防爆上下文总百分比动态剪枝 (Context Guardrail)：
    - 基于总上下文动态百分比（85% Prompt 预算 / 15% 生成预留），拒绝固定长度硬编码。
    - 三层信息保真保护：系统提示词 100% 绝对保护、最近多轮对话 100% 完整保留、中间历史思维链折叠与百分比平滑修剪。
 6. 零延迟流式转发 (SSE Streaming)：完美支持打字机效果。
"""

import http.server
import json
import gzip
import logging
import re
import socket
import socketserver
import os
import sys
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Optional, Dict, Any, List, Callable, Tuple

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 配置常量
DEFAULT_GATEWAY_HOST = "0.0.0.0"
DEFAULT_GATEWAY_PORT = 8081
DEFAULT_BACKEND_PORT = 8083
DEFAULT_SIDECAR_VISION_PORT = 8085

# DeepSeek 官方识图模型闲时定价标准 (https://api-docs.deepseek.com/zh-cn/quick_start/pricing)
DEEPSEEK_PRICING = {
    "source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
    "model_standard": "deepseek-v4-flash-vision-exp / deepseek-chat (闲时)",
    "currency": "CNY",
    "prompt_cache_miss_cny_per_m": 1.50,  # 1.50 元 / 百万 tokens (0.0000015 元/token)
    "prompt_cache_hit_cny_per_m": 0.05,   # 0.05 元 / 百万 tokens (0.00000005 元/token)
    "completion_cny_per_m": 4.50          # 4.50 元 / 百万 tokens (0.0000045 元/token)
}

billing_lock = threading.Lock()
billing_ledger = {
    "global": {
        "text_requests": 0,
        "vision_requests": 0,
        "text_prompt_tokens": 0,
        "text_completion_tokens": 0,
        "text_hit_tokens": 0,
        "text_cost": 0.0,
        "vision_prompt_tokens": 0,
        "vision_completion_tokens": 0,
        "vision_hit_tokens": 0,
        "vision_cost": 0.0,
        "total_tokens": 0,
        "total_cost": 0.0
    },
    "keys": {
        "llamacpp": {
            "key": "llamacpp",
            "display_key": "llamacpp (默认通用Key)",
            "total_requests": 0,
            "text_requests": 0,
            "vision_requests": 0,
            "text_prompt_tokens": 0,
            "text_completion_tokens": 0,
            "text_cost": 0.0,
            "vision_prompt_tokens": 0,
            "vision_completion_tokens": 0,
            "vision_cost": 0.0,
            "total_tokens": 0,
            "total_cost": 0.0,
            "first_seen": "就绪待命",
            "last_active": "就绪待命"
        }
    }
}

DEFAULT_API_KEY = "llamacpp"

# 客户端常用模型别名列表 (无论主模型是谁，均支持通过这些别名透明调用)
COMMON_MODEL_ALIASES = [
    "Qwen", "qwen", "qwen3", "qwen2.5", "qwen-max", "qwen-plus", "qwen-turbo",
    "default",
    "gpt-4o", "gpt-4o-mini", "gpt-4", "gpt-3.5-turbo", "o1", "o3-mini",
    "claude-3-5-sonnet", "claude-3-7-sonnet", "claude-3-opus", "claude-sonnet", "claude",
    "deepseek-chat", "deepseek-reasoner", "deepseek-v3", "deepseek-r1",
    "llama-3.3", "llama-3.1", "llama3", "llama"
]

def extract_api_key(headers, path: str = "") -> str:
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        k = auth[7:].strip()
        if k and k.lower() not in ("none", "null", "undefined"):
            return k
    x_key = headers.get("x-api-key", "").strip()
    if x_key and x_key.lower() not in ("none", "null", "undefined"):
        return x_key
    if "?" in path and "key=" in path:
        m = re.search(r"[?&]key=([^&]+)", path)
        if m and m.group(1).strip() and m.group(1).strip().lower() not in ("none", "null", "undefined"):
            return m.group(1).strip()
    return DEFAULT_API_KEY

def mask_key(k: str) -> str:
    if len(k) <= 10:
        return k
    return f"{k[:4]}***{k[-4:]}"

def record_billing(api_key: str, is_vision: bool, prompt_tokens: int, completion_tokens: int, hit_tokens: int = 0):
    miss_tokens = max(0, prompt_tokens - hit_tokens)
    p_cost = (miss_tokens * 1.50 + hit_tokens * 0.05) / 1_000_000.0
    c_cost = (completion_tokens * 4.50) / 1_000_000.0
    req_cost = p_cost + c_cost
    total_tokens = prompt_tokens + completion_tokens
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")

    with billing_lock:
        g = billing_ledger["global"]
        if is_vision:
            g["vision_requests"] += 1
            g["vision_prompt_tokens"] += prompt_tokens
            g["vision_completion_tokens"] += completion_tokens
            g["vision_hit_tokens"] += hit_tokens
            g["vision_cost"] += req_cost
        else:
            g["text_requests"] += 1
            g["text_prompt_tokens"] += prompt_tokens
            g["text_completion_tokens"] += completion_tokens
            g["text_hit_tokens"] += hit_tokens
            g["text_cost"] += req_cost

        g["total_tokens"] += total_tokens
        g["total_cost"] += req_cost

        if api_key not in billing_ledger["keys"]:
            billing_ledger["keys"][api_key] = {
                "key": api_key,
                "display_key": mask_key(api_key),
                "total_requests": 0,
                "text_requests": 0,
                "vision_requests": 0,
                "text_prompt_tokens": 0,
                "text_completion_tokens": 0,
                "text_cost": 0.0,
                "vision_prompt_tokens": 0,
                "vision_completion_tokens": 0,
                "vision_cost": 0.0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "first_seen": now_str,
                "last_active": now_str
            }

        k_entry = billing_ledger["keys"][api_key]
        k_entry["total_requests"] += 1
        k_entry["last_active"] = now_str
        if is_vision:
            k_entry["vision_requests"] += 1
            k_entry["vision_prompt_tokens"] += prompt_tokens
            k_entry["vision_completion_tokens"] += completion_tokens
            k_entry["vision_cost"] += req_cost
        else:
            k_entry["text_requests"] += 1
            k_entry["text_prompt_tokens"] += prompt_tokens
            k_entry["text_completion_tokens"] += completion_tokens
            k_entry["text_cost"] += req_cost

        k_entry["total_tokens"] += total_tokens
        k_entry["total_cost"] += req_cost

# 全局共享状态
gateway_state = {
    "active_model_alias": "Qwen3.8-27B",
    "backend_port": DEFAULT_BACKEND_PORT,
    "sidecar_vision_port": DEFAULT_SIDECAR_VISION_PORT,
    "main_has_vision": True,
    "max_context": 98304,
    "current_preset": "default",
    "model_quant": "",
    "total_requests": 0,
    "multimodal_requests": 0,
    "sidecar_routed_requests": 0,
    "guarded_overflows": 0,
}

HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"
}

def log_gateway(msg: str):
    now = time.strftime("%H:%M:%S")
    try:
        print(f"\033[90m[{now}]\033[0m \033[36m[8081网关]\033[0m {msg}", flush=True)
    except Exception:
        try:
            # 去除可能引发编码异常的 emoji 并输出
            safe_msg = msg.encode("ascii", "ignore").decode("ascii")
            print(f"[{now}] [8081网关] {safe_msg}", flush=True)
        except Exception:
            pass

def detect_multimodal(messages: List[Dict[str, Any]]) -> bool:
    """检测请求是否含有多模态图片内容"""
    if not isinstance(messages, list):
        return False
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") in ("image_url", "image") or "image_url" in part:
                        return True
        elif isinstance(content, dict):
            if content.get("type") in ("image_url", "image") or "image_url" in content:
                return True
    return False

# ==========================================================
#  智能调度与任务遥测统计 (融合 OmniRoute / Portkey / LiteLLM 架构)
# ==========================================================
scheduler_lock = threading.Lock()
scheduler_stats = {
    "code_dry0_protected": 0,    # 代码开发任务 (锁定 dry=0, temp=0.1)
    "extraction_tasks": 0,       # 全文提取与背诵 (锁定 effort=none, dry=0, temp=0.0)
    "reasoning_tasks": 0,        # 复杂数理逻辑 (锁定 dry=0, temp=0.2, effort=high)
    "creative_tasks": 0,         # 创意小说文案 (自适应 temp=0.85, dry=0.8)
    "chat_tasks": 0,             # 日常轻量速答 (自适应 effort=low, dry=0.8, temp=0.7)
    "general_tasks": 0,          # 通用问答基线 (自适应 effort=medium, dry=0.4, temp=0.6)
    "retries_count": 0,          # 自动弹性重试成功次数
    "total": 0
}

# 任务难度统计 (简单 / 中等 / 困难) 严格遵循 Qwen-Fixed-Chat-Templates v22.5 官方规范
difficulty_lock = threading.Lock()
difficulty_stats = {
    "low": 0,       # 简单模式 (秒级速答，精炼思考 / 直出)
    "medium": 0,    # 中等模式 (通用基准，零系统词注入，100% KV Cache 命中)
    "high": 0,      # 困难模式 (代码/推导/数学，高阶深度多假设检验)
    "total": 0
}

class TaskProfile:
    def __init__(self, task_type: str, difficulty: str, effort: str, label: str,
                 dry_zero: bool, reason: str,
                 temperature: float = 0.6, top_p: float = 0.95, min_p: float = 0.05,
                 presence_penalty: float = 0.0, frequency_penalty: float = 0.0,
                 repeat_penalty: float = 1.0,
                 dry_multiplier: float = 0.4, dry_base: float = 1.75, dry_allowed_length: int = 2,
                 max_tokens: int = 8192, reasoning_budget: int = 2048):
        self.task_type = task_type
        self.difficulty = difficulty
        self.effort = effort
        self.label = label
        self.dry_zero = dry_zero
        self.reason = reason
        self.temperature = temperature
        self.top_p = top_p
        self.min_p = min_p
        self.presence_penalty = presence_penalty
        self.frequency_penalty = frequency_penalty
        self.repeat_penalty = repeat_penalty
        self.dry_multiplier = dry_multiplier
        self.dry_base = dry_base
        self.dry_allowed_length = dry_allowed_length
        self.max_tokens = max_tokens
        self.reasoning_budget = reasoning_budget

def analyze_and_adapt_task(messages: List[Dict[str, Any]], requested_effort: Optional[str] = None) -> TaskProfile:
    """
    智能任务感知与全维度自适应参数调度引擎 (Task-Adaptive Multi-Parameter Matrix):
    - 深度扫描输入语义、代码语法、文件路径、数学公式与创作特征
    - 全维度自适应调节: 3层难度(Low/Medium/High) / reasoning_budget / reasoning_effort / temperature / dry_multiplier / max_tokens
    """
    all_text = ""
    for msg in messages:
        c = msg.get("content")
        if isinstance(c, str):
            all_text += c + " "
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text":
                    all_text += p.get("text", "") + " "

    # 1. 检查内联控制标签 (最高优先级)
    if "<|think_off|>" in all_text:
        return TaskProfile(
            task_type="EXTRACTION", difficulty="low", effort="none",
            label="非思考原生直出 (Low - 内联标签)", dry_zero=True,
            reason="用户内联指定 <|think_off|>，锁定 effort=none, think_budget=0, temp=0.0, dry=0.0 极速直出",
            temperature=0.0, top_p=1.0, min_p=0.0, presence_penalty=0.0, frequency_penalty=0.0,
            dry_multiplier=0.0, dry_base=1.75, dry_allowed_length=2, max_tokens=8192, reasoning_budget=0
        )
    if "<|think_ultracode|>" in all_text:
        return TaskProfile(
            task_type="CODE", difficulty="high", effort="high",
            label="代码极客模式 (High - 内联标签)", dry_zero=True,
            reason="用户内联指定 <|think_ultracode|>，锁定 high 深度思考、think_budget=3072, temp=0.1 与 dry=0.0 路径保护",
            temperature=0.1, top_p=0.95, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0,
            dry_multiplier=0.0, dry_base=1.75, dry_allowed_length=2, max_tokens=16384, reasoning_budget=3072
        )
    if "<|think_xhigh|>" in all_text or "<|think_high|>" in all_text or "<|think_max|>" in all_text or "<|think_extreme|>" in all_text:
        return TaskProfile(
            task_type="REASONING", difficulty="high", effort="high",
            label="深度推理任务 (High - 内联标签)", dry_zero=True,
            reason="用户内联指定 <|think_xhigh|>，匹配 high 深度思考、think_budget=3072, temp=0.2 与 dry=0 保护公式推导",
            temperature=0.2, top_p=0.90, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0,
            dry_multiplier=0.0, dry_base=1.75, dry_allowed_length=2, max_tokens=16384, reasoning_budget=3072
        )
    if "<|think_low|>" in all_text or "<|think_minimal|>" in all_text:
        return TaskProfile(
            task_type="CHAT", difficulty="low", effort="low",
            label="日常轻量交互 (Low - 内联标签)", dry_zero=False,
            reason="用户内联指定 <|think_low|>，匹配 low 精炼思考、think_budget=512, temp=0.7 与 DRY 防死循环",
            temperature=0.7, top_p=0.90, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0,
            dry_multiplier=0.8, dry_base=1.75, dry_allowed_length=2, max_tokens=4096, reasoning_budget=512
        )
    if "<|think_medium|>" in all_text:
        return TaskProfile(
            task_type="GENERAL", difficulty="medium", effort="medium",
            label="通用知识问答 (Medium - 内联标签)", dry_zero=False,
            reason="用户内联指定 <|think_medium|>，匹配 medium 通用基准、think_budget=2048 与适度防循环",
            temperature=0.6, top_p=0.95, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0,
            dry_multiplier=0.4, dry_base=1.75, dry_allowed_length=2, max_tokens=8192, reasoning_budget=2048
        )

    # 2. 客户端显式传参
    if requested_effort:
        req = requested_effort.lower().strip()
        if req in ("none", "off"):
            return TaskProfile(
                task_type="EXTRACTION", difficulty="low", effort="none",
                label="非思考原生直出 (Low - 客户端指定)", dry_zero=True,
                reason="客户端传参 off，锁定 effort=none, think_budget=0, temp=0.0, dry=0.0 极速直出",
                temperature=0.0, top_p=1.0, min_p=0.0, presence_penalty=0.0, frequency_penalty=0.0,
                dry_multiplier=0.0, dry_base=1.75, dry_allowed_length=2, max_tokens=8192, reasoning_budget=0
            )
        elif req == "ultracode":
            return TaskProfile(
                task_type="CODE", difficulty="high", effort="high",
                label="代码极客任务 (High - 客户端指定)", dry_zero=True,
                reason="客户端传参 ultracode，匹配 high 思考、think_budget=3072, temp=0.1 与 dry=0 保护路径",
                temperature=0.1, top_p=0.95, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0,
                dry_multiplier=0.0, dry_base=1.75, dry_allowed_length=2, max_tokens=16384, reasoning_budget=3072
            )
        elif req in ("high", "xhigh", "max", "extreme"):
            return TaskProfile(
                task_type="REASONING", difficulty="high", effort="high",
                label="深度推理任务 (High - 客户端指定)", dry_zero=True,
                reason="客户端传参 high，匹配 high 深度思考、think_budget=3072, temp=0.2 与 dry=0 保护公式",
                temperature=0.2, top_p=0.90, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0,
                dry_multiplier=0.0, dry_base=1.75, dry_allowed_length=2, max_tokens=16384, reasoning_budget=3072
            )
        elif req in ("low", "minimal"):
            return TaskProfile(
                task_type="CHAT", difficulty="low", effort="low",
                label="日常轻量交互 (Low - 客户端指定)", dry_zero=False,
                reason="客户端传参 low，匹配 low 精炼思考、think_budget=512, temp=0.7 与 DRY 防死循环",
                temperature=0.7, top_p=0.90, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0,
                dry_multiplier=0.8, dry_base=1.75, dry_allowed_length=2, max_tokens=4096, reasoning_budget=512
            )
        elif req in ("medium", "default"):
            return TaskProfile(
                task_type="GENERAL", difficulty="medium", effort="medium",
                label="通用知识问答 (Medium - 客户端指定)", dry_zero=False,
                reason="客户端传参 medium，匹配 medium 基准、think_budget=2048 与适度防循环",
                temperature=0.6, top_p=0.95, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0,
                dry_multiplier=0.4, dry_base=1.75, dry_allowed_length=2, max_tokens=8192, reasoning_budget=2048
            )

    # 3. 语义特征嗅探
    user_text = ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            c = msg.get("content")
            if isinstance(c, str):
                user_text = c
            elif isinstance(c, list):
                for p in c:
                    if isinstance(p, dict) and p.get("type") == "text":
                        user_text += p.get("text", "")
            break

    if not user_text:
        return TaskProfile(
            task_type="GENERAL", difficulty="medium", effort="medium",
            label="通用基准 (Medium - 默认)", dry_zero=False, reason="空用户文本",
            temperature=0.6, top_p=0.95, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0,
            dry_multiplier=0.4, dry_base=1.75, dry_allowed_length=2, max_tokens=8192, reasoning_budget=2048
        )

    text_lower = user_text.lower().strip()

    # === 难度 1 (Low): 纯文本提取 / 原文搬运 / 背诵全文 (EXTRACTION -> 强制关闭思考 none, think_budget=0, temp=0.0, dry=0.0) ===
    extraction_keywords = [
        "一字不差", "原样输出", "不要做任何总结", "耗费验证思考", "完整正文", "全文输出",
        "输出全文", "提取正文", "提取全文", "背诵全文", "背诵整篇", "背诵文章", "朗诵全文",
        "原文搬运", "原样复制", "照抄", "直接从第一段输出到最后一段", "不要思考", "无需思考",
        "无须思考", "显示全文", "给出全文", "原样打印", "原样贴出"
    ]
    if any(k in text_lower for k in extraction_keywords) or ("背诵" in text_lower and any(k in text_lower for k in ["全文", "文章", "篇", "序", "词", "诗"])):
        return TaskProfile(
            task_type="EXTRACTION", difficulty="low", effort="none",
            label="全文提取搬运 (Low - 纯文本直出)", dry_zero=True,
            reason="检测到原文提取/背诵任务，自适应锁定 effort=none, think_budget=0, temp=0.0, dry=0.0 绝对保真，杜绝思考截断",
            temperature=0.0, top_p=1.0, min_p=0.0, presence_penalty=0.0, frequency_penalty=0.0,
            dry_multiplier=0.0, dry_base=1.75, dry_allowed_length=2, max_tokens=8192, reasoning_budget=0
        )

    # === 难度 3 (High): 代码开发与文件路径任务 (CODE -> 强制 dry=0, repeat_penalty=1.0, temp=0.1, high思考, think_budget=3072, max_tokens=16384) ===
    has_file_path = bool(re.search(r"[a-zA-Z]:\\[^ \n\r\t]+", user_text) or re.search(r"(?:/|\\)[\w\.\-]+(?:\.py|\.js|\.ts|\.cpp|\.c|\.h|\.go|\.rs|\.java|\.sh|\.bat|\.ps1|\.sql|\.json|\.toml|\.yaml|\.gguf|\.bin|\.svg|\.html|\.css|\.xml)", user_text, re.I))
    has_code_syntax = ("```" in user_text) or ("<svg" in text_lower) or ("<html" in text_lower) or ("<script" in text_lower) or ("<div" in text_lower) or ("<animate" in text_lower) or ("def " in user_text and ":" in user_text) or ("function" in text_lower and "{" in user_text) or ("import " in user_text) or ("#include" in user_text)
    code_keywords = [
        "写代码", "写一个", "实现一个", "做个", "做一个", "写段代码", "编写程序", "编写代码", "写个脚本", "写函数", "类实现",
        "debug", "报错", "修bug", "修复", "bug原因", "异常处理", "traceback", "syntaxerror", "exception",
        "算法", "leetcode", "时间复杂度", "空间复杂度", "递归", "动态规划", "二分查找", "排序算法",
        "svg", "smil", "html", "css", "xml", "canvas", "webgl", "glsl", "shader", "vue", "react",
        "动画", "组件", "页面", "自包含", "前端", "后端", "微服务", "重构", "接口",
        "python", "javascript", "typescript", "golang", "rust", "c++", "c语言", "java", "sql", "cuda", "regex", "正则表达式",
        "git", "pip", "npm", "docker", "cmake", "bash", "powershell", "api接口", "爬虫", "json", "yaml", "toml"
    ]
    is_code_task = has_file_path or has_code_syntax or any(k in text_lower for k in code_keywords)
    if is_code_task:
        return TaskProfile(
            task_type="CODE", difficulty="high", effort="high",
            label="代码开发任务 (High - dry=0与3K思考预算)", dry_zero=True,
            reason="检测到编程/调试/路径/前端语法特征，自动分配 High 难度: dry=0 铁律、repeat_penalty=1.0 释放代码语法、think_budget=3072、max_tokens=16384",
            temperature=0.1, top_p=0.95, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0, repeat_penalty=1.0,
            dry_multiplier=0.0, dry_base=1.75, dry_allowed_length=2, max_tokens=16384, reasoning_budget=3072
        )

    # === 难度 3 (High): 高阶数学公式与复杂逻辑推导 (REASONING -> 深度思考 high, dry=0, temp=0.2, think_budget=3072, max_tokens=16384) ===
    math_keywords = [
        "证明", "推导", "数学", "公式", "求积分", "求导", "极限", "微分方程", "概率论", "线性代数",
        "深度分析", "详细推导", "为什么", "仔细思考", "深度思考", "架构设计", "方案对比", "原理分析"
    ]
    if any(k in text_lower for k in math_keywords) or (len(user_text) > 200 and ("1." in user_text or "步骤" in user_text)):
        return TaskProfile(
            task_type="REASONING", difficulty="high", effort="high",
            label="深度推理任务 (High - 逻辑演绎与3K思考预算)", dry_zero=True,
            reason="检测到数学/推导/长篇复合问题，自适应匹配 High 难度: think_budget=3072、max_tokens=16384、dry=0 保护公式推演",
            temperature=0.2, top_p=0.90, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0,
            dry_multiplier=0.0, dry_base=1.75, dry_allowed_length=2, max_tokens=16384, reasoning_budget=3072
        )

    # === 难度 2 (Medium): 创意写作与小说文案 (CREATIVE -> medium思考, think_budget=2048, temp=0.85, dry=0.8, max_tokens=8192) ===
    creative_keywords = [
        "写小说", "写故事", "创作故事", "写散文", "编一个故事", "角色扮演", "扮演一个", "拟人",
        "剧本", "同人", "文案", "文案策划", "广告词", "小红书文案", "朋友圈文案", "润色文笔",
        "写首诗", "作诗", "现代诗", "赋诗", "写一首", "编剧", "短篇小说", "武侠", "科幻小说"
    ]
    if any(k in text_lower for k in creative_keywords):
        return TaskProfile(
            task_type="CREATIVE", difficulty="medium", effort="medium",
            label="创意写作任务 (Medium - 文思泉涌与2K思考预算)", dry_zero=False,
            reason="检测到文学故事/创意写作，自适应匹配 Medium 难度: think_budget=2048、temp=0.85、DRY 杜绝车轱辘话",
            temperature=0.85, top_p=0.95, min_p=0.05, presence_penalty=0.15, frequency_penalty=0.15,
            dry_multiplier=0.8, dry_base=1.75, dry_allowed_length=3, max_tokens=8192, reasoning_budget=2048
        )

    # === 难度 1 (Low): 日常极简速答 (CHAT -> low 秒级直出, think_budget=512, temp=0.7, dry=0.8, max_tokens=4096) ===
    if len(user_text) < 45:
        chat_keywords = [
            "你好", "您好", "在吗", "hi", "hello", "早", "早上好", "晚上好",
            "谢谢", "多谢", "再见", "拜拜", "你是谁", "自我介绍", "介绍一下自己",
            "翻译成", "英译中", "中译英", "翻译一下", "润色一下", "概括", "总结一下",
            "几点了", "今天星期几", "讲个笑话", "聊天", "在干嘛", "测试"
        ]
        if any(k in text_lower for k in chat_keywords) or len(user_text) < 12:
            return TaskProfile(
                task_type="CHAT", difficulty="low", effort="low",
                label="日常轻量交互 (Low - 512词精炼速答)", dry_zero=False,
                reason="日常寒暄与超短问答，自适应匹配 Low 难度: low 精炼思考、think_budget=512，秒级直出防死循环",
                temperature=0.7, top_p=0.90, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0,
                dry_multiplier=0.8, dry_base=1.75, dry_allowed_length=2, max_tokens=4096, reasoning_budget=512
            )

    # === 难度 2 (Medium): 通用基准问答 (GENERAL -> medium基线, think_budget=2048, temp=0.6, dry=0.4, max_tokens=8192) ===
    return TaskProfile(
        task_type="GENERAL", difficulty="medium", effort="medium",
        label="通用知识问答 (Medium - 2K思考预算与基线缓存)", dry_zero=False,
        reason="标准知识问答，自适应匹配 Medium 难度: medium 基线、think_budget=2048、max_tokens=8192 保障正文完整输出",
        temperature=0.6, top_p=0.95, min_p=0.05, presence_penalty=0.0, frequency_penalty=0.0,
        dry_multiplier=0.4, dry_base=1.75, dry_allowed_length=2, max_tokens=8192, reasoning_budget=2048
    )

def classify_task_difficulty(messages: List[Dict[str, Any]], requested_effort: Optional[str] = None) -> Tuple[str, str]:
    """向后兼容原有接口"""
    p = analyze_and_adapt_task(messages, requested_effort)
    return p.effort, p.label

def estimate_tokens_smart(msgs: List[Dict[str, Any]]) -> int:
    """
    精确估算消息列表的 Token 数量：
    - CJK 汉字/标点：~0.85 Token / 字符 (1.2 chars/token)
    - 英文单词/代码：~0.35 Token / 字符 (2.85 chars/token)
    - 图像占位：~1280 Tokens / 图
    """
    total_tokens = 0
    for m in msgs:
        total_tokens += 4  # 每条消息的基础元数据开销 (role, formatting tokens)
        c = m.get("content", "")
        if isinstance(c, str):
            cjk_count = len(re.findall(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", c))
            other_count = len(c) - cjk_count
            total_tokens += int(cjk_count * 0.85 + other_count * 0.35)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict):
                    p_type = p.get("type", "")
                    if p_type == "text":
                        txt = p.get("text", "")
                        cjk_count = len(re.findall(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", txt))
                        other_count = len(txt) - cjk_count
                        total_tokens += int(cjk_count * 0.85 + other_count * 0.35)
                    elif p_type in ("image_url", "image") or "image_url" in p:
                        total_tokens += 1280

        # 精确计入可能包含数千 Tokens 的思考链 (reasoning_content)
        rc = m.get("reasoning_content")
        if isinstance(rc, str) and rc:
            cjk_rc = len(re.findall(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", rc))
            other_rc = len(rc) - cjk_rc
            total_tokens += int(cjk_rc * 0.85 + other_rc * 0.35) + 6
    return max(1, total_tokens)

def collapse_thinking_traces(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """
    折叠早期历史消息中的 <think>...</think> 与 reasoning_content 思考链，保留最终答案，
    以最小的语义损失释放巨量 Token 空间，彻底根治多轮对话导致的上下文爆满死循环
    """
    optimized = []
    saved_tokens = 0
    total_len = len(messages)
    for idx, m in enumerate(messages):
        # 最近一条 Assistant 消息不折叠思考链，保留当前连贯性
        if idx >= total_len - 2 or m.get("role") != "assistant":
            optimized.append(m)
            continue

        m_copy = dict(m)
        has_changed = False

        # 1. 折叠早期历史中的 reasoning_content (DeepSeek / Qwen 思考字段)
        if "reasoning_content" in m_copy and m_copy["reasoning_content"]:
            rc_str = str(m_copy["reasoning_content"])
            if len(rc_str) > 30 and rc_str != "[已折叠早期思考过程]":
                saved_tokens += max(1, len(rc_str) // 2)
                m_copy["reasoning_content"] = "[已折叠早期思考过程]"
                has_changed = True

        # 2. 折叠 content 中内嵌的 <think>...</think>
        c = m_copy.get("content")
        if isinstance(c, str) and "<think>" in c and "</think>" in c:
            new_c = re.sub(r"<think>[\s\S]*?</think>", "[已折叠早期思考链过程]", c).strip()
            saved_tokens += max(1, (len(c) - len(new_c)) // 2)
            m_copy["content"] = new_c
            has_changed = True

        optimized.append(m_copy if has_changed else m)
    return optimized, saved_tokens

def guard_context_overflow(messages: List[Dict[str, Any]], max_allowed_tokens: int) -> List[Dict[str, Any]]:
    """
    防爆上下文保护（总百分比动态预算与最小信息丢失剪枝）：
     1. 动态预算划分：
        - 预留生成空间：P_gen = max(1024, min(4096, int(max_allowed_tokens * 0.15))) (15% 动态占比)
        - Prompt 安全上限：B_prompt = max_allowed_tokens - P_gen (85% 动态占比)
     2. 三层保护策略：
        - 第一层：System/Developer 提示词 100% 绝对保护（永不修剪）
        - 第二层：最近 2~4 条对话 100% 完整保留（确保当前指令和上下文直接衔接）
        - 第三层：中间历史消息自适应折叠思考链与按超限比例平滑滑动
    """
    if not isinstance(messages, list) or len(messages) <= 2:
        return messages

    # 1. 计算总百分比动态预算 (85% Prompt 预算，15% 生成预留)
    safe_prompt_budget = max(1024, int(max_allowed_tokens * 0.85))

    initial_tokens = estimate_tokens_smart(messages)
    if initial_tokens <= safe_prompt_budget:
        return messages

    # 2. 第一阶段信息无损压缩：折叠早期中间轮次的 <think> 思考链
    msgs_optimized, saved = collapse_thinking_traces(messages)
    cur_tokens = estimate_tokens_smart(msgs_optimized)
    if cur_tokens <= safe_prompt_budget:
        gateway_state["guarded_overflows"] += 1
        log_gateway(f"\033[33m[防爆保护触发]\033[0m 上下文估算 {initial_tokens} tokens 接近 85% 预算 ({safe_prompt_budget})，\033[32m已通过折叠历史思考链无损压缩至 {cur_tokens} tokens\033[0m")
        return msgs_optimized

    # 3. 第二阶段多层保护滑动修剪 (保 System、保最近交互、剪中间历史)
    has_system = msgs_optimized[0].get("role") in ("system", "developer")
    system_msgs = [msgs_optimized[0]] if has_system else []
    history_turns = msgs_optimized[1:] if has_system else msgs_optimized[:]

    # 保护最近 2 轮对话 (通常为 2~4 条消息)
    protected_tail_count = min(len(history_turns), 3)
    tail_msgs = history_turns[-protected_tail_count:]
    middle_msgs = history_turns[:-protected_tail_count]

    # 从头部按对修剪中间消息
    pruned_count = 0
    while middle_msgs and estimate_tokens_smart(system_msgs + middle_msgs + tail_msgs) > safe_prompt_budget:
        middle_msgs.pop(0)
        pruned_count += 1
        if middle_msgs and middle_msgs[0].get("role") == "assistant":
            middle_msgs.pop(0)
            pruned_count += 1

    final_msgs = system_msgs + middle_msgs + tail_msgs
    final_tokens = estimate_tokens_smart(final_msgs)

    gateway_state["guarded_overflows"] += 1
    excess_pct = round(((initial_tokens - safe_prompt_budget) / safe_prompt_budget) * 100, 1)
    log_gateway(f"\033[33m[防爆保护触发]\033[0m 总上限 {max_allowed_tokens} tokens (Prompt 85% 动态预算: {safe_prompt_budget}) | 原始估算 {initial_tokens} tokens (超限 +{excess_pct}%) | \033[32m已完成系统词保真+思考链压缩+百分比平滑修剪 -> 最终 {final_tokens} tokens\033[0m")
    return final_msgs

def call_vision_sidecar(messages: List[Dict[str, Any]], sidecar_port: int = DEFAULT_SIDECAR_VISION_PORT) -> Optional[str]:
    """
    调用 8085 CPU 侧挂视觉模型 (Qwen3VL-4B-Instruct) 进行图像解析与 OCR 提取：
    将多模态请求提炼为高质量结构化图文感知报告，供 8083 纯文本主模型使用
    """
    try:
        url = f"http://127.0.0.1:{sidecar_port}/v1/chat/completions"
        # 提取包含图像的最后一条用户消息
        vision_msgs = []
        for m in messages:
            if m.get("role") in ("system", "developer"):
                vision_msgs.append(m)
            elif detect_multimodal([m]):
                vision_msgs.append(m)

        if not vision_msgs:
            vision_msgs = messages

        payload = {
            "model": "Qwen3VL-4B",
            "messages": vision_msgs,
            "temperature": 0.2,
            "max_tokens": 1536,
            "stream": False
        }
        data_b = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data_b, headers={"Content-Type": "application/json"}, method="POST")

        with urllib.request.urlopen(req, timeout=45) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            choices = res_json.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        log_gateway(f"\033[31m[视觉侧挂眼睛调用异常]\033[0m {e}")
    return None

def transform_multimodal_to_text_with_sidecar(messages: List[Dict[str, Any]], sidecar_port: int) -> List[Dict[str, Any]]:
    """
    将消息列表中的图像内容通过 8085 侧挂模型转译为结构化自然语言上下文，
    替换掉原生图片二进制/URL，从而无缝赋能 8083 纯文本主模型
    """
    vision_description = call_vision_sidecar(messages, sidecar_port)
    if not vision_description:
        vision_description = "(视觉侧挂模型未能成功返回图像描述，请主模型根据现有文字上下文处理)"

    transformed = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(p, dict) and (p.get("type") in ("image_url", "image") or "image_url" in p) for p in c):
            # 提取原有的文本问题
            user_texts = [p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text"]
            combined_user_text = "\n".join(filter(None, user_texts))

            injected_content = f"【图像视觉感知与OCR解析报告（来自 8085 侧挂视觉眼睛）】:\n{vision_description}\n\n【用户关于图像的问题/指令】:\n{combined_user_text if combined_user_text else '请结合上述图像内容进行详细分析与解答。'}"
            m_copy = dict(m)
            m_copy["content"] = injected_content
            transformed.append(m_copy)
        else:
            transformed.append(m)
    return transformed

DASHBOARD_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gateway_dashboard.html")
_shutdown_callback: Optional[Callable] = None

def set_shutdown_callback(cb: Callable):
    global _shutdown_callback
    _shutdown_callback = cb

def get_live_hardware() -> Dict[str, Any]:
    hw = {
        "vram_used_mb": 0, "vram_total_mb": 16384,
        "gpu_util": 0, "power_w": 0, "temp_c": 0,
        "ram_used_gb": 0, "ram_total_gb": 63.8
    }
    try:
        res = subprocess.run([
            "nvidia-smi",
            "--query-gpu=memory.used,memory.total,utilization.gpu,power.draw,temperature.gpu",
            "--format=csv,noheader,nounits"
        ], capture_output=True, text=True, timeout=1)
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.strip().splitlines()[0].split(",")]
            if len(parts) >= 5:
                hw["vram_used_mb"] = int(float(parts[0]))
                hw["vram_total_mb"] = int(float(parts[1]))
                hw["gpu_util"] = int(float(parts[2]))
                hw["power_w"] = int(float(parts[3]))
                hw["temp_c"] = int(float(parts[4]))
    except Exception:
        pass

    try:
        import ctypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total_b = stat.ullTotalPhys
            avail_b = stat.ullAvailPhys
            used_b = total_b - avail_b
            hw["ram_used_gb"] = round(used_b / (1024**3), 1)
            hw["ram_total_gb"] = round(total_b / (1024**3), 1)
    except Exception:
        pass

    return hw

class AIGatewayHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, format, *args):
        # 屏蔽原生输出，使用统一 gateway log
        pass

    def do_OPTIONS(self):
        # 支持 CORS 跨域请求
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):
        backend_url = f"http://127.0.0.1:{gateway_state['backend_port']}{self.path}"

        # 1. 独立智能网关看板 (/dashboard)
        if self.path in ("/dashboard", "/dashboard/", "/dashboard.html") or self.path.startswith("/dashboard?"):
            if os.path.isfile(DASHBOARD_HTML_PATH):
                try:
                    with open(DASHBOARD_HTML_PATH, "r", encoding="utf-8") as f:
                        html_content = f.read()
                    html_bytes = html_content.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html_bytes)))
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(html_bytes)
                    return
                except Exception:
                    pass

        # 2. 虚拟价格与账单自检 API (DeepSeek 闲时标准)
        if self.path.startswith("/api/gateway/billing"):
            with billing_lock:
                g = billing_ledger["global"]
                keys_list = []
                for k, v in billing_ledger["keys"].items():
                    item = dict(v)
                    item["text_cost_cny"] = round(item["text_cost"], 6)
                    item["vision_cost_cny"] = round(item["vision_cost"], 6)
                    item["total_cost_cny"] = round(item["total_cost"], 6)
                    keys_list.append(item)
                keys_list.sort(key=lambda x: x["total_cost"], reverse=True)

                target_key = "llamacpp"
                if "?" in self.path and "key=" in self.path:
                    m = re.search(r"[?&]key=([^&]+)", self.path)
                    if m and m.group(1).strip():
                        target_key = m.group(1).strip()

                target_key_usage = billing_ledger["keys"].get(target_key)
                if not target_key_usage and target_key == "llamacpp":
                    target_key_usage = {
                        "key": "llamacpp",
                        "display_key": "llamacpp (默认通用Key)",
                        "total_requests": 0, "text_cost_cny": 0.0, "vision_cost_cny": 0.0, "total_cost_cny": 0.0,
                        "text_tokens": 0, "vision_tokens": 0, "total_tokens": 0, "last_active": "就绪待命"
                    }

                res = {
                    "status": "ok",
                    "default_key": DEFAULT_API_KEY,
                    "target_key": target_key,
                    "target_key_usage": target_key_usage,
                    "pricing_standard": DEEPSEEK_PRICING,
                    "summary": {
                        "total_cost_cny": round(g["total_cost"], 6),
                        "text_cost_cny": round(g["text_cost"], 6),
                        "vision_cost_cny": round(g["vision_cost"], 6),
                        "total_tokens": g["total_tokens"],
                        "text_tokens": g["text_prompt_tokens"] + g["text_completion_tokens"],
                        "text_prompt_tokens": g["text_prompt_tokens"],
                        "text_completion_tokens": g["text_completion_tokens"],
                        "vision_tokens": g["vision_prompt_tokens"] + g["vision_completion_tokens"],
                        "vision_prompt_tokens": g["vision_prompt_tokens"],
                        "vision_completion_tokens": g["vision_completion_tokens"],
                        "total_requests": g["text_requests"] + g["vision_requests"],
                        "text_requests": g["text_requests"],
                        "vision_requests": g["vision_requests"]
                    },
                    "keys": keys_list
                }
            res_b = json.dumps(res, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(res_b)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(res_b)
            return

        # 3. 看板实时状态 API
        if self.path.startswith("/api/gateway/status"):
            st = dict(gateway_state)
            st["hardware"] = get_live_hardware()
            # 尝试拉取后端的 metrics 吞吐数据与 props 元数据
            try:
                m_req = urllib.request.Request(f"http://127.0.0.1:{gateway_state['backend_port']}/metrics", method="GET")
                with urllib.request.urlopen(m_req, timeout=0.6) as m_resp:
                    m_text = m_resp.read().decode("utf-8", errors="ignore")
                    m_eval = re.search(r"llamacpp:tokens_predicted_per_second\s+([0-9.]+)", m_text)
                    m_prompt = re.search(r"llamacpp:prompt_tokens_per_second\s+([0-9.]+)", m_text)
                    st["metrics"] = {
                        "eval_tps": float(m_eval.group(1)) if m_eval else 0.0,
                        "prompt_tps": float(m_prompt.group(1)) if m_prompt else 0.0
                    }
            except Exception:
                st["metrics"] = {"eval_tps": 0.0, "prompt_tps": 0.0}

            try:
                p_req = urllib.request.Request(f"http://127.0.0.1:{gateway_state['backend_port']}/props", method="GET")
                with urllib.request.urlopen(p_req, timeout=0.6) as p_resp:
                    p_data = json.loads(p_resp.read().decode("utf-8", errors="ignore"))
                    st["model_ftype"] = p_data.get("model_ftype", "")
                    st["model_path"] = p_data.get("model_path", "")
            except Exception:
                pass

            if not st.get("model_quant"):
                st["model_quant"] = gateway_state.get("model_quant") or st.get("model_ftype") or ""

            with billing_lock:
                g = billing_ledger["global"]
                st["billing"] = {
                    "total_cost_cny": round(g["total_cost"], 6),
                    "text_cost_cny": round(g["text_cost"], 6),
                    "vision_cost_cny": round(g["vision_cost"], 6),
                    "total_tokens": g["total_tokens"],
                    "text_tokens": g["text_prompt_tokens"] + g["text_completion_tokens"],
                    "vision_tokens": g["vision_prompt_tokens"] + g["vision_completion_tokens"],
                    "text_requests": g["text_requests"],
                    "vision_requests": g["vision_requests"],
                    "key_count": len(billing_ledger["keys"])
                }

            with difficulty_lock:
                tot = max(1, difficulty_stats["total"])
                st["difficulty_stats"] = {
                    "low": difficulty_stats["low"],
                    "medium": difficulty_stats["medium"],
                    "high": difficulty_stats["high"],
                    "total": difficulty_stats["total"],
                    "template_version": "qwen3.8-froggeric-v22.5",
                    "low_pct": round((difficulty_stats["low"] / tot) * 100, 1) if difficulty_stats["total"] > 0 else 0.0,
                    "medium_pct": round((difficulty_stats["medium"] / tot) * 100, 1) if difficulty_stats["total"] > 0 else 0.0,
                    "high_pct": round((difficulty_stats["high"] / tot) * 100, 1) if difficulty_stats["total"] > 0 else 0.0,
                }

            with scheduler_lock:
                s_tot = max(1, scheduler_stats["total"])
                st["scheduler_stats"] = {
                    "code_dry0_protected": scheduler_stats["code_dry0_protected"],
                    "extraction_tasks": scheduler_stats.get("extraction_tasks", 0),
                    "reasoning_tasks": scheduler_stats["reasoning_tasks"],
                    "creative_tasks": scheduler_stats.get("creative_tasks", 0),
                    "chat_tasks": scheduler_stats["chat_tasks"],
                    "general_tasks": scheduler_stats["general_tasks"],
                    "retries_count": scheduler_stats["retries_count"],
                    "total": scheduler_stats["total"],
                    "code_pct": round((scheduler_stats["code_dry0_protected"] / s_tot) * 100, 1) if scheduler_stats["total"] > 0 else 0.0,
                }
            st_bytes = json.dumps(st, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(st_bytes)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(st_bytes)
            return

        # 3. 拦截 /v1/models 提供丰富的客户端友好模型别名
        if self.path in ("/v1/models", "/models"):
            data = {"object": "list", "data": []}
            try:
                req = urllib.request.Request(backend_url, method="GET")
                with urllib.request.urlopen(req, timeout=0.5) as resp:
                    raw_data = resp.read()
                    data = json.loads(raw_data.decode("utf-8"))
            except Exception:
                pass

            # 注入当前活跃主模型与丰富的常用模型别名 (如 Qwen, qwen, gpt-4o, claude, deepseek 等全兼容)
            active_name = gateway_state["active_model_alias"]
            model_ids = {m.get("id") for m in data.get("data", [])}
            now_ts = int(time.time())
            aliases = [active_name] + [a for a in COMMON_MODEL_ALIASES if a != active_name]
            for alias in aliases:
                if alias not in model_ids:
                    data.setdefault("data", []).append({
                        "id": alias,
                        "object": "model",
                        "created": now_ts,
                        "owned_by": "local-gateway",
                        "root": active_name
                    })
                    model_ids.add(alias)

            res_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(res_bytes)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(res_bytes)
            return

        # 4. 其他 GET 请求（如 /health、/props、/metrics 或原生 UI 静态资源）透明反代
        self.forward_raw_request("GET", backend_url)

    def do_POST(self):
        path = self.path
        backend_url = f"http://127.0.0.1:{gateway_state['backend_port']}{path}"

        # 远程/看板终止请求
        if path == "/api/gateway/shutdown":
            res_b = b'{"status":"ok","message":"shutting down"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(res_b)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(res_b)
            if _shutdown_callback:
                threading.Thread(target=_shutdown_callback, daemon=True).start()
            return

        if path in ("/v1/chat/completions", "/chat/completions", "/v1/completions", "/completions"):
            self.handle_chat_completions(backend_url)
        else:
            self.forward_raw_request("POST", backend_url)

    def handle_chat_completions(self, backend_url: str):
        content_len = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_len) if content_len > 0 else b"{}"

        try:
            body = json.loads(post_data.decode("utf-8"))
        except Exception:
            # 无法解析则原样转发
            self.forward_bytes("POST", backend_url, post_data)
            return

        gateway_state["total_requests"] += 1
        active_model = gateway_state["active_model_alias"]

        # 1. 模型名映射
        orig_model = body.get("model", "")
        body["model"] = active_model

        # 2. 多模态嗅探与双模智能视觉路由
        messages = body.get("messages", [])
        is_multimodal = detect_multimodal(messages)
        main_has_vision = gateway_state.get("main_has_vision", True)
        sidecar_port = gateway_state.get("sidecar_vision_port", DEFAULT_SIDECAR_VISION_PORT)

        if is_multimodal:
            gateway_state["multimodal_requests"] += 1
            if main_has_vision:
                # ── 分支 A: 主模型具备原生视觉能力 ──
                # 在 API 请求中传入 "speculative.n_max": 0 临时关闭 MTP，直接交由 8083 原生处理
                body["speculative.n_max"] = 0
                if "extra_body" not in body or not isinstance(body.get("extra_body"), dict):
                    body["extra_body"] = {}
                body["extra_body"]["speculative.n_max"] = 0
                log_gateway(f"🖼️ 多模态识图请求 [主模型自带识图: {active_model}] -> \033[32m优先原生识图 (安全旁路MTP)\033[0m")
            else:
                # ── 分支 B: 主模型为纯文本模型 -> 启用 8085 CPU 侧挂眼睛 ──
                gateway_state["sidecar_routed_requests"] += 1
                log_gateway(f"👁️ 多模态识图请求 [主模型为纯文本: {active_model}] -> \033[36m调用 8085 CPU 侧挂视觉眼睛预解析图文\033[0m")
                messages = transform_multimodal_to_text_with_sidecar(messages, sidecar_port)
                body["messages"] = messages
                log_gateway(f"✨ 8085 视觉解析完成并注入语义上下文 -> 递交 8083 主模型进行强大文本推理")

        # 3. 智能任务感知与自适应参数调度 (融合 LiteLLM / OmniRoute / Portkey 架构)
        current_effort = body.get("reasoning_effort")
        task_prof = analyze_and_adapt_task(messages, current_effort)
        effort = task_prof.effort
        diff_label = task_prof.label
        body["reasoning_effort"] = effort

        # 记录任务难度频次统计 (简单 / 中等 / 困难)
        stat_key = "medium"
        if effort in ("low", "minimal", "none", "off"):
            stat_key = "low"
        elif effort in ("high", "xhigh", "max", "ultracode", "extreme"):
            stat_key = "high"
        elif effort == "medium":
            stat_key = "medium"

        with difficulty_lock:
            if stat_key in difficulty_stats:
                difficulty_stats[stat_key] += 1
                difficulty_stats["total"] += 1
            curr_low = difficulty_stats["low"]
            curr_med = difficulty_stats["medium"]
            curr_high = difficulty_stats["high"]

        # 记录智能调度感知遥测统计 (代码 / 提取 / 推理 / 创作 / 闲聊 / 通用)
        with scheduler_lock:
            scheduler_stats["total"] += 1
            if task_prof.task_type == "CODE":
                scheduler_stats["code_dry0_protected"] += 1
            elif task_prof.task_type == "EXTRACTION":
                scheduler_stats["extraction_tasks"] += 1
            elif task_prof.task_type == "REASONING":
                scheduler_stats["reasoning_tasks"] += 1
            elif task_prof.task_type == "CREATIVE":
                scheduler_stats["creative_tasks"] += 1
            elif task_prof.task_type == "CHAT":
                scheduler_stats["chat_tasks"] += 1
            else:
                scheduler_stats["general_tasks"] += 1
            curr_code_dry0 = scheduler_stats["code_dry0_protected"]

        log_gateway(f"[智能调度] 任务感知: 【{diff_label}】 (effort={effort}) | 累计: 代码({scheduler_stats['code_dry0_protected']}) 提取({scheduler_stats['extraction_tasks']}) 推理({scheduler_stats['reasoning_tasks']}) 创作({scheduler_stats['creative_tasks']}) 闲聊({scheduler_stats['chat_tasks']}) 通用({scheduler_stats['general_tasks']})")
        if task_prof.reason:
            log_gateway(f"          ↳ 调度决策理由: {task_prof.reason}")

        # 4. 防爆上下文保护 (总百分比动态预算与最小信息丢失剪枝)
        max_ctx = gateway_state.get("max_context", 98304)
        if messages:
            body["messages"] = guard_context_overflow(messages, max_ctx)

        # 5. 任务自适应全维度参数矩阵动态注入 (Task-Adaptive Sampling Matrix)
        # 根据任务类型精细化调节: reasoning_effort / temperature / dry_multiplier / top_p / min_p / penalties / max_tokens
        body["reasoning_effort"] = task_prof.effort
        body["dry_multiplier"] = task_prof.dry_multiplier
        body["dry_base"] = task_prof.dry_base
        body["dry_allowed_length"] = task_prof.dry_allowed_length
        body["temperature"] = task_prof.temperature
        body["top_p"] = task_prof.top_p
        body["min_p"] = task_prof.min_p
        body["presence_penalty"] = task_prof.presence_penalty
        body["frequency_penalty"] = task_prof.frequency_penalty
        body["repeat_penalty"] = task_prof.repeat_penalty

        # 思考长度根据任务难度智能设置上限 (reasoning_budget 与 thinking_budget_tokens 双轨对齐)
        if "reasoning_budget" not in body or body.get("reasoning_budget") is None or body.get("reasoning_budget") < 0:
            body["reasoning_budget"] = task_prof.reasoning_budget
        else:
            body["reasoning_budget"] = min(int(body["reasoning_budget"]), 8192)

        body["thinking_budget_tokens"] = body["reasoning_budget"]

        # 清洗并对齐 chat_template_kwargs，杜绝 WebUI 前端误传 enable_thinking: false 掐断思考
        if "chat_template_kwargs" not in body or not isinstance(body["chat_template_kwargs"], dict):
            body["chat_template_kwargs"] = {}
        if task_prof.reasoning_budget > 0 and task_prof.effort != "none":
            body["chat_template_kwargs"]["enable_thinking"] = True
        elif task_prof.effort == "none":
            body["chat_template_kwargs"]["enable_thinking"] = False

        # 输出长度不设置上限：锁定 max_tokens = -1，彻底解放任务正文生成空间，直到自然输出结束符为止
        body["max_tokens"] = -1
        if "max_completion_tokens" in body:
            body["max_completion_tokens"] = -1

        # 任务专属参数注入遥测日志
        log_gateway(f"⚙️ [自适应调度] 【{task_prof.label}】 难度={task_prof.difficulty} | effort={task_prof.effort} | 思考上限={body['reasoning_budget']} tokens | 输出长度=不设限(-1) | temp={task_prof.temperature} | repeat_penalty={task_prof.repeat_penalty} | dry_mult={task_prof.dry_multiplier}")

        # 注入标准停止词，确保模型在遇到结束符号时立即停止生成
        existing_stop = body.get("stop", [])
        if isinstance(existing_stop, str):
            existing_stop = [existing_stop]
        elif not isinstance(existing_stop, list):
            existing_stop = []
        for stop_w in ("<|im_end|>", "<|endoftext|>", "<|im_start|>"):
            if stop_w not in existing_stop:
                existing_stop.append(stop_w)
        body["stop"] = existing_stop

        # 重新序列化
        forward_data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        is_streaming = bool(body.get("stream", False))

        # 6. 执行代理转发与虚拟计费
        api_key = extract_api_key(self.headers, self.path)
        prompt_tokens_est = estimate_tokens_smart(body.get("messages", []))
        self.forward_transformed_request(backend_url, forward_data, is_streaming, api_key, is_multimodal, prompt_tokens_est)

    def forward_transformed_request(self, backend_url: str, data: bytes, is_streaming: bool, api_key: str = "local-default", is_vision: bool = False, prompt_tokens_est: int = 100):
        headers = {}
        for k, v in self.headers.items():
            if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() != "content-length":
                headers[k] = v
        headers["Content-Length"] = str(len(data))
        headers["Host"] = f"127.0.0.1:{gateway_state['backend_port']}"

        # OmniRoute 风格弹性退避重试 (最多 2 次重试，应对瞬态高并发抖动与显存加载)
        max_retries = 2
        retry_delays = [0.3, 0.8]
        resp = None
        last_error = None

        for attempt in range(max_retries + 1):
            req = urllib.request.Request(backend_url, data=data, headers=headers, method="POST")
            try:
                resp = urllib.request.urlopen(req, timeout=120)
                if attempt > 0:
                    with scheduler_lock:
                        scheduler_stats["retries_count"] += 1
                    log_gateway(f"🔄 [弹性容错引擎] 第 {attempt} 次重试成功连通后端")
                break
            except urllib.error.HTTPError as e:
                # 针对 502/503/429 等可恢复状态码进行指数退避重试
                if e.code in (502, 503, 429) and attempt < max_retries:
                    time.sleep(retry_delays[attempt])
                    continue
                err_body = e.read()
                self.send_response(e.code)
                for k, v in e.headers.items():
                    if k.lower() not in HOP_BY_HOP_HEADERS:
                        self.send_header(k, v)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(err_body)
                return
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(retry_delays[attempt])
                    continue

        if resp is None:
            # 重试耗尽，返回 Portkey / OmniRoute 风格友好结构化错误
            err_dict = {
                "error": {
                    "message": f"AI 模型推理引擎 (端口 {gateway_state['backend_port']}) 暂不可达或未启动: {str(last_error)}",
                    "type": "gateway_backend_unavailable",
                    "code": 503,
                    "tip": "请确认模型已在启动中心中启动，或访问 http://127.0.0.1:8081/dashboard 查看网关监控看板"
                }
            }
            err_msg = json.dumps(err_dict, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(err_msg)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(err_msg)
            return

        self.send_response(resp.status)
        for k, v in resp.headers.items():
            if k.lower() not in HOP_BY_HOP_HEADERS:
                self.send_header(k, v)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # 流式传输或完整传输
        try:
            if is_streaming:
                stream_tokens = 0
                stream_prompt_tokens = prompt_tokens_est
                stream_hit_tokens = 0
                while True:
                    chunk = resp.read(512)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    try:
                        chunk_text = chunk.decode("utf-8", errors="ignore")
                        for line in chunk_text.splitlines():
                            line_s = line.strip()
                            if line_s.startswith("data: ") and line_s != "data: [DONE]":
                                j = json.loads(line_s[6:])
                                if "usage" in j and j["usage"]:
                                    u = j["usage"]
                                    stream_prompt_tokens = u.get("prompt_tokens", stream_prompt_tokens)
                                    stream_tokens = u.get("completion_tokens", stream_tokens)
                                    stream_hit_tokens = u.get("prompt_tokens_details", {}).get("cached_tokens", stream_hit_tokens)
                                elif "choices" in j and j["choices"]:
                                    delta = j["choices"][0].get("delta", {})
                                    txt = delta.get("content") or delta.get("reasoning_content") or ""
                                    if txt:
                                        stream_tokens += max(1, len(txt) // 3)
                    except Exception:
                        pass
                record_billing(api_key, is_vision, stream_prompt_tokens, stream_tokens, stream_hit_tokens)
            else:
                body = resp.read()
                self.wfile.write(body)
                try:
                    resp_json = json.loads(body.decode("utf-8", errors="ignore"))
                    u = resp_json.get("usage", {})
                    p_tok = u.get("prompt_tokens", prompt_tokens_est)
                    c_tok = u.get("completion_tokens", 0)
                    h_tok = u.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                    record_billing(api_key, is_vision, p_tok, c_tok, h_tok)
                except Exception:
                    record_billing(api_key, is_vision, prompt_tokens_est, 50, 0)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            resp.close()

    def forward_raw_request(self, method: str, backend_url: str):
        content_len = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(content_len) if content_len > 0 else None
        self.forward_bytes(method, backend_url, data)

    def forward_bytes(self, method: str, backend_url: str, data: Optional[bytes]):
        headers = {}
        for k, v in self.headers.items():
            if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() != "content-length":
                headers[k] = v
        if data is not None:
            headers["Content-Length"] = str(len(data))
        client_accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower()
        headers["Accept-Encoding"] = "gzip, deflate"
        headers["Host"] = f"127.0.0.1:{gateway_state['backend_port']}"

        req = urllib.request.Request(backend_url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_body = resp.read()
                is_gzipped = resp.headers.get("Content-Encoding", "").lower() == "gzip"
                if is_gzipped and not client_accepts_gzip:
                    try:
                        resp_body = gzip.decompress(resp_body)
                        is_gzipped = False
                    except Exception:
                        pass

                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() not in ("content-length", "content-encoding", "cache-control", "pragma", "expires"):
                        self.send_header(k, v)
                if is_gzipped:
                    self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(resp_body)))
                self.send_header("Access-Control-Allow-Origin", "*")

                # 针对原生 WebUI 根路径 (/) 与 /index.html 强制注入防缓存头，彻底杜绝浏览器历史磁盘强缓存
                if self.path in ("/", "", "/index.html"):
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                else:
                    cc = resp.headers.get("Cache-Control")
                    if cc:
                        self.send_header("Cache-Control", cc)

                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for k, v in e.headers.items():
                if k.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(k, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            # 当访问根路径 / (原生对话 WebUI) 且后端 8083 尚未就绪时，返回友好的自动刷新等待引导页 (严格定位为 WebUI 载入态)
            if method == "GET" and self.path in ("/", ""):
                waiting_html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="2">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate, max-age=0">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <title>AI 原生对话 WebUI - 载入显存中...</title>
  <style>
    body {
      margin: 0; background: #070a12; color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", sans-serif;
      display: flex; align-items: center; justify-content: center; height: 100vh;
    }
    .box {
      background: #0f172a; border: 1px solid rgba(0, 240, 255, 0.25); border-radius: 16px;
      padding: 36px 44px; text-align: center; max-width: 520px; box-shadow: 0 12px 40px rgba(0,0,0,0.6);
    }
    .spinner {
      width: 44px; height: 44px; border: 3px solid #1e293b; border-top: 3px solid #00f0ff;
      border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 20px;
    }
    @keyframes spin { 100% { transform: rotate(360deg); } }
    h2 { margin: 0 0 10px; color: #00f0ff; font-size: 20px; }
    p { margin: 8px 0; color: #94a3b8; font-size: 14px; line-height: 1.6; }
    .badge {
      display: inline-block; padding: 4px 12px; background: rgba(0, 240, 255, 0.1);
      color: #00f0ff; border-radius: 6px; font-size: 12px; margin: 10px 0;
    }
    .actions { margin-top: 22px; display: flex; gap: 12px; justify-content: center; }
    a {
      display: inline-block; padding: 9px 16px; border-radius: 8px; font-size: 13px; text-decoration: none;
      background: #1e293b; color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); font-weight: 500;
    }
    a:hover { background: #38bdf8; color: #070a12; }
  </style>
</head>
<body>
  <div class="box">
    <div class="spinner"></div>
    <h2>💬 AI 原生对话 WebUI 准备中</h2>
    <div class="badge">统一入口: http://127.0.0.1:8081/ · 目标服务: 原生对话</div>
    <p>底层推理引擎 (端口 8083) 正在将大模型载入 GPU 显存中...</p>
    <p><b>本页面每隔 2 秒自动重试</b>，模型载入就绪后将立即自动进入原生对话 WebUI，无需手动刷新！</p>
    <div class="actions">
      <a href="/dashboard" target="_blank">🌐 前往独立监控看板 (/dashboard)</a>
      <a href="http://127.0.0.1:8083" target="_blank">⚡ 直连底层端口 (:8083)</a>
    </div>
  </div>
</body>
</html>"""
                waiting_b = waiting_html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(waiting_b)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(waiting_b)
                return

            err = json.dumps({"error": str(e)}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(err)

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

_server_instance: Optional[ThreadedHTTPServer] = None
_server_thread: Optional[threading.Thread] = None

def start_gateway(host: str = DEFAULT_GATEWAY_HOST,
                  port: int = DEFAULT_GATEWAY_PORT,
                  backend_port: int = DEFAULT_BACKEND_PORT,
                  sidecar_port: int = DEFAULT_SIDECAR_VISION_PORT,
                  main_has_vision: bool = True,
                  model_alias: str = "Qwen3.8-27B",
                  max_context: int = 98304,
                  preset: str = "default",
                  quant: str = "") -> bool:
    """启动 8081 智能网关守护线程"""
    global _server_instance, _server_thread

    gateway_state["backend_port"] = backend_port
    gateway_state["sidecar_vision_port"] = sidecar_port
    gateway_state["main_has_vision"] = main_has_vision
    gateway_state["active_model_alias"] = model_alias
    gateway_state["max_context"] = max_context
    gateway_state["current_preset"] = preset
    if quant:
        gateway_state["model_quant"] = quant

    # 检查 8081 是否已被当前实例占用
    if _server_instance is not None:
        log_gateway(f"网关已在运行，更新活跃模型 -> {model_alias} (视觉能力: {'自带' if main_has_vision else '8085侧挂'}, 上下文上限: {max_context})")
        return True

    try:
        _server_instance = ThreadedHTTPServer((host, port), AIGatewayHandler)
    except OSError as e:
        log_gateway(f"\033[31m[错误] 端口 {port} 绑定失败，可能被占用: {e}\033[0m")
        return False

    _server_thread = threading.Thread(target=_server_instance.serve_forever, daemon=True)
    _server_thread.start()
    vis_status = "原生优先" if main_has_vision else "8085 CPU 侧挂辅助眼睛"
    log_gateway(f"\033[32m[启动成功] 智能反向代理网关监听于 http://{host}:{port} -> 主模型 :http://127.0.0.1:{backend_port}\033[0m")
    log_gateway(f"          [特性] 模型别名透明映射 | 识图路由({vis_status}) | 思考等级自适应 | 防爆总百分比动态剪枝")
    return True

def stop_gateway():
    """停止 8081 智能网关"""
    global _server_instance, _server_thread
    if _server_instance:
        try:
            _server_instance.shutdown()
            _server_instance.server_close()
        except Exception:
            pass
        _server_instance = None
        _server_thread = None
        log_gateway("智能网关已关闭并释放端口")

if __name__ == "__main__":
    start_gateway(
        host="0.0.0.0",
        port=8081,
        backend_port=8083,
        sidecar_port=8085,
        main_has_vision=True,
        model_alias="Qwen3.8-27B-GSQ-MTP",
        max_context=98304,
        preset="qwen3.8-27b-gsq-mtp2-96k"
    )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_gateway()
