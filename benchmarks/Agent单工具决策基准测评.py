#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工具调用能力对比测试框架
==========================

四阶段测试协议:
  Stage 1 - 工具调用: 验证模型输出结构化格式
  Stage 2 - 联网搜索: 执行真实搜索，评估结果相关性
  Stage 3 - RAG: 注入搜索结果，评估时效性
  Stage 4 - 智能体搜索: 多轮迭代，检查搜索规划

动态评分:
  行为分 (30%) + 动态分 (30%) + 答案分 (40%)

Usage:
  python agent_tool_eval.py --models 1,2,3
  python agent_tool_eval.py --all
  python agent_tool_eval.py --models 1 --timeout 180
"""

import argparse
import json
import os
import sys
import time
import re
import subprocess
import traceback
from datetime import datetime
from pathlib import Path

# Windows 编码处理
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    import requests
except ImportError:
    print("ERROR: 需要 requests 库。pip install requests")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    from rich.table import Table
    from rich.panel import Panel
    from rich import print as rprint
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("建议安装 rich 库获得更好的显示效果: pip install rich")


# ============================================================
#  配置与常量
# ============================================================
DEFAULT_SERVER = "http://127.0.0.1:8081"
DEFAULT_API_KEY = "llamacpp"
DEFAULT_TIMEOUT = 180
DEFAULT_QUESTIONS_FILE = "questions.json"
LAUNCHER_SCRIPT = "launcher_main.ps1"
RESULTS_DIR = "eval_results"

# Tavily API (可选，用于真实搜索)
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_SEARCH_URL = "https://api.tavily.com/search"

# 工具定义
SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search",
        "description": "搜索互联网获取最新信息、新闻、数据",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，应覆盖核心实体与需求"},
                "max_results": {"type": "integer", "description": "返回结果数量，默认5"}
            },
            "required": ["query"]
        }
    }
}

# 阶段名称
STAGE_NAMES = {
    1: "工具调用",
    2: "联网搜索",
    3: "RAG",
    4: "智能体搜索"
}

console = Console() if RICH_AVAILABLE else None


# ============================================================
#  进度显示
# ============================================================
class ProgressDisplay:
    def __init__(self, total, model_name):
        self.total = total
        self.current = 0
        self.model_name = model_name
        self.start_time = time.time()
        
    def update(self, question_id, stage, status="running"):
        self.current += 1
        elapsed = time.time() - self.start_time
        if RICH_AVAILABLE and console:
            pct = (self.current / self.total) * 100
            bar_len = 30
            filled = int(bar_len * self.current / self.total)
            bar = "█" * filled + "░" * (bar_len - filled)
            status_icon = {"running": "⏳", "success": "✅", "error": "❌", "timeout": "⏰"}.get(status, "⏳")
            console.print(
                f"  [{bar}] {pct:.0f}% | "
                f"Q{question_id}/S{stage} {status_icon} | "
                f"elapsed: {elapsed:.1f}s"
            )
        else:
            pct = (self.current / self.total) * 100
            bar_len = 20
            filled = int(bar_len * self.current / self.total)
            bar = "#" * filled + "-" * (bar_len - filled)
            print(f"  [{bar}] {pct:.0f}% Q{question_id}/S{stage} elapsed:{elapsed:.1f}s", end="\r")
    
    def complete(self):
        total_time = time.time() - self.start_time
        if RICH_AVAILABLE and console:
            console.print(f"\n  ✅ 测试完成！总耗时: {total_time:.1f}s")
        else:
            print(f"\n  测试完成！总耗时: {total_time:.1f}s")


# ============================================================
#  模型启动与管理
# ============================================================
class ModelManager:
    def __init__(self, server, api_key, launcher_path):
        self.server = server
        self.api_key = api_key
        self.launcher_path = launcher_path
        self.process = None
        self.current_model_info = None
        
    def get_model_list(self):
        """获取模型列表"""
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", self.launcher_path, "-ListModels"],
                capture_output=True, timeout=30
            )
            out = r.stdout.decode("utf-8", errors="replace").strip()
            out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out)
            m = re.search(r"\[.*\]", out, re.S)
            if m:
                return json.loads(m.group(0))
        except Exception as e:
            print(f"ERROR: 获取模型列表失败: {e}")
        return []
    
    def kill_existing_server(self):
        """先杀掉可能占用端口的旧服务"""
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-NetTCPConnection -LocalPort 8081 -ErrorAction SilentlyContinue | "
                 "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"],
                capture_output=True, timeout=10
            )
            time.sleep(2)
        except Exception:
            pass
    
    def start_model(self, index):
        """启动指定模型（通过启动器，使用预设参数）"""
        print(f"\n  🚀 启动模型 #{index} (通过 AI启动器端口8081网页服务.bat)")
        print(f"     使用启动器预设参数 (CustomArgs)")
        
        # 先杀掉旧服务
        self.kill_existing_server()
        
        # 启动日志文件
        log_dir = Path("eval_results")
        log_dir.mkdir(exist_ok=True)
        launch_log = log_dir / f"launch_model_{index}.log"
        
        # 记录启动命令
        print(f"     启动器: {self.launcher_path}")
        print(f"     日志:   {launch_log}")
        
        # 启动进程，输出写入日志文件
        log_fh = open(launch_log, "w", encoding="utf-8")
        self.process = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", self.launcher_path, "-ModelIndex", str(index), "-NoInteractive"],
            stdout=log_fh, stderr=log_fh
        )
        
        # 等待模型就绪（大模型可能需要 3-5 分钟）
        model_name = self.wait_ready(timeout=300)
        
        if model_name:
            self.current_model_info = model_name
            print(f"  ✅ 模型已就绪: {model_name}")
            
            # 显示启动参数
            try:
                with open(launch_log, "r", encoding="utf-8", errors="replace") as f:
                    log_content = f.read()
                # 提取自定义启动参数
                param_match = re.search(r'自定义启动参数:\s*(.*)', log_content)
                if param_match:
                    print(f"  ⚙️  预设参数: {param_match.group(1).strip()}")
            except Exception:
                pass
        else:
            print(f"  ❌ 模型 #{index} 启动超时")
            # 显示日志末尾
            try:
                with open(launch_log, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                if lines:
                    print(f"     启动日志最后几行:")
                    for line in lines[-5:]:
                        print(f"       {line.rstrip()}")
            except Exception:
                pass
        
        return model_name
    
    def wait_ready(self, timeout=300):
        """等待模型就绪（超时5分钟，适配大模型）"""
        print(f"     等待模型加载 (超时 {timeout}s)...")
        start = time.time()
        last_status = ""
        
        while time.time() - start < timeout:
            try:
                r = requests.get(
                    f"{self.server}/v1/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=5
                )
                if r.status_code == 200:
                    data = r.json()
                    model_name = data.get("data", [{}])[0].get("id", "unknown")
                    elapsed = time.time() - start
                    print(f"     ✅ API 就绪 ({elapsed:.1f}s)")
                    return model_name
            except requests.exceptions.ConnectionError:
                # 服务还没启动，继续等待
                elapsed = time.time() - start
                if elapsed - int(elapsed) < 0.5:  # 每 ~10s 打印一次
                    status = f"     ⏳ 等待中... ({elapsed:.0f}s)"
                    if status != last_status:
                        print(status)
                        last_status = status
            except Exception as e:
                pass
            time.sleep(3)
        
        return None
    
    def stop_model(self):
        """停止模型，清理进程"""
        print(f"  🔄 停止模型...")
        
        # 先尝试优雅终止
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
                try:
                    self.process.wait(timeout=3)
                except Exception:
                    pass
        
        # 强制清理 llama 相关进程
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process | Where-Object { $_.ProcessName -match 'llama' } | "
                 "ForEach-Object { Write-Host \"Killing: $($_.ProcessName) PID:$($_.Id)\"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }"],
                capture_output=True, timeout=15,
                text=True
            )
            if result.stdout.strip():
                print(f"     {result.stdout.strip()}")
        except Exception:
            pass
        
        # 等待GPU显存释放
        print(f"     等待 GPU 释放...", end="")
        for i in range(15):
            time.sleep(2)
            try:
                r = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5
                )
                mem_used = int(r.stdout.strip())
                if mem_used < 500:
                    print(f" ✅ (显存已释放: {mem_used}MB)")
                    break
            except Exception:
                break
            if i == 14:
                print(f" ⚠️ (超时，可能仍有显存占用)")


# ============================================================
#  模型API调用
# ============================================================
class ModelAPI:
    def __init__(self, server, api_key):
        self.server = server
        self.api_key = api_key
    
    def chat_stream(self, model, messages, tools=None, timeout=180):
        """流式调用模型"""
        url = f"{self.server}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 8192,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        
        headers = {"Authorization": f"Bearer {self.api_key}"}
        result = {
            "tool_calls": [],
            "reasoning": "",
            "content": "",
            "error": None,
            "latency_ms": 0
        }
        
        t0 = time.time()
        tc_index = {}
        
        try:
            with requests.post(url, json=payload, headers=headers, 
                             timeout=timeout, stream=True) as r:
                r.raise_for_status()
                
                for raw in r.iter_lines():
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    
                    try:
                        chunk = json.loads(data_str)
                    except Exception:
                        continue
                    
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    
                    ch = choices[0]
                    delta = ch.get("delta") or {}
                    
                    # 思考内容
                    rc = delta.get("reasoning_content")
                    if rc:
                        result["reasoning"] += rc
                    
                    # 正文内容
                    c = delta.get("content")
                    if c:
                        result["content"] += c
                    
                    # 工具调用
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        fn = tc.get("function") or {}
                        if idx not in tc_index:
                            tc_index[idx] = {"name": "", "arguments": ""}
                        if fn.get("name"):
                            tc_index[idx]["name"] += fn["name"]
                        if fn.get("arguments"):
                            tc_index[idx]["arguments"] += fn["arguments"]
                    
                    # 结束条件
                    finish = ch.get("finish_reason")
                    if finish and finish in ("stop", "tool_calls", "length", "content_filter"):
                        break
            
            result["tool_calls"] = [tc_index[k] for k in sorted(tc_index)]
            result["latency_ms"] = (time.time() - t0) * 1000
            
        except requests.exceptions.Timeout:
            result["error"] = f"TIMEOUT: 请求超时 ({timeout}s)"
            result["latency_ms"] = (time.time() - t0) * 1000
        except requests.exceptions.ConnectionError as e:
            result["error"] = f"CONNECTION_ERROR: {str(e)[:100]}"
            result["latency_ms"] = (time.time() - t0) * 1000
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {str(e)[:100]}"
            result["latency_ms"] = (time.time() - t0) * 1000
        
        return result
    
    def single_turn(self, model, prompt, timeout=60):
        """单轮对话（不带工具）"""
        return self.chat_stream(
            model,
            [{"role": "user", "content": prompt}],
            tools=None,
            timeout=timeout
        )


# ============================================================
#  四阶段测试协议
# ============================================================
class StageTester:
    def __init__(self, api, model_name, timeout=180):
        self.api = api
        self.model_name = model_name
        self.timeout = timeout
        self.results = {}
        
    def stage1_tool_call(self, question):
        """Stage 1: 工具调用测试"""
        result = {
            "stage": 1,
            "name": "工具调用",
            "score": 0,
            "max_score": 10,
            "details": [],
            "error": None
        }
        
        try:
            q = question["question"]
            messages = [{"role": "user", "content": q}]
            
            response = self.api.chat_stream(
                self.model_name, messages,
                tools=[SEARCH_TOOL],
                timeout=self.timeout
            )
            
            if response["error"]:
                result["error"] = response["error"]
                result["score"] = 0
                result["details"].append(f"❌ 错误: {response['error']}")
                return result
            
            tool_calls = response["tool_calls"]
            
            # 评分：工具调用格式
            if not tool_calls:
                result["score"] = 0
                result["details"].append("❌ 未调用任何工具")
            else:
                tc = tool_calls[0]
                name = tc.get("name", "")
                args = tc.get("arguments", "")
                
                # 检查格式
                if name == "search":
                    result["score"] = 5
                    result["details"].append("✓ 工具名称正确 (search)")
                    
                    # 检查参数格式
                    try:
                        arg_obj = json.loads(args)
                        query = arg_obj.get("query", "")
                        if query and len(query) > 3:
                            result["score"] = 8
                            result["details"].append(f"✓ query 格式正确: '{query[:50]}'")
                            
                            # 检查关键词覆盖
                            keywords = question.get("keywords", [])
                            q_lower = query.lower()
                            hits = sum(1 for kw in keywords if kw.lower() in q_lower)
                            if hits >= len(keywords) * 0.5:
                                result["score"] = 10
                                result["details"].append(f"✓ 关键词覆盖率高 ({hits}/{len(keywords)})")
                            else:
                                result["details"].append(f"⚠ 关键词覆盖不足 ({hits}/{len(keywords)})")
                        else:
                            result["details"].append("⚠ query 为空或过短")
                    except Exception:
                        result["details"].append("⚠ arguments 不是合法 JSON")
                else:
                    result["details"].append(f"⚠ 工具名称不是 search，而是 '{name}'")
            
            result["details"].append(f"延迟: {response['latency_ms']:.0f}ms")
            
        except Exception as e:
            result["error"] = str(e)
            result["details"].append(f"❌ 异常: {str(e)[:100]}")
        
        return result
    
    def stage2_search(self, question, tool_call_result):
        """Stage 2: 联网搜索测试"""
        result = {
            "stage": 2,
            "name": "联网搜索",
            "score": 0,
            "max_score": 10,
            "details": [],
            "error": None
        }
        
        try:
            # 如果 Stage 1 没有有效工具调用，跳过
            if tool_call_result["score"] < 5:
                result["score"] = 5
                result["details"].append("⚠ 无有效工具调用，跳过搜索评估")
                return result
            
            # 获取 query
            q = question["question"]
            messages = [{"role": "user", "content": q}]
            
            response = self.api.chat_stream(
                self.model_name, messages,
                tools=[SEARCH_TOOL],
                timeout=self.timeout
            )
            
            if response["error"]:
                result["error"] = response["error"]
                result["details"].append(f"⚠ Stage 2 错误: {response['error']}")
                result["score"] = 3
                return result
            
            tool_calls = response["tool_calls"]
            if not tool_calls:
                result["score"] = 5
                result["details"].append("⚠ Stage 2 未调用工具")
                return result
            
            tc = tool_calls[0]
            args = tc.get("arguments", "")
            
            # 尝试执行真实搜索（如果配置了 Tavily）
            if TAVILY_API_KEY:
                try:
                    arg_obj = json.loads(args)
                    query = arg_obj.get("query", "")
                    
                    tavily_result = requests.post(
                        TAVILY_SEARCH_URL,
                        headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                        json={
                            "query": query,
                            "max_results": 5,
                            "search_depth": "basic"
                        },
                        timeout=15
                    )
                    
                    if tavily_result.status_code == 200:
                        search_data = tavily_result.json()
                        results_count = len(search_data.get("results", []))
                        
                        if results_count >= 3:
                            result["score"] = 10
                            result["details"].append(f"✓ 搜索返回 {results_count} 条结果")
                        elif results_count >= 1:
                            result["score"] = 7
                            result["details"].append(f"⚠ 搜索仅返回 {results_count} 条结果")
                        else:
                            result["score"] = 5
                            result["details"].append("⚠ 搜索无结果")
                    else:
                        result["score"] = 5
                        result["details"].append(f"⚠ Tavily API 返回 {tavily_result.status_code}")
                        
                except Exception as e:
                    result["score"] = 5
                    result["details"].append(f"⚠ 搜索执行失败: {str(e)[:80]}")
            else:
                # 无 Tavily，基于 query 质量评分
                try:
                    arg_obj = json.loads(args)
                    query = arg_obj.get("query", "")
                    expected_kws = question.get("keywords", [])
                    
                    q_lower = query.lower()
                    hits = sum(1 for kw in expected_kws if kw.lower() in q_lower)
                    coverage = hits / max(len(expected_kws), 1)
                    
                    result["score"] = 5 + coverage * 5
                    result["details"].append(f"ℹ Tavily 未配置，基于 query 质量评分")
                    result["details"].append(f"  query: '{query[:80]}'")
                    result["details"].append(f"  关键词覆盖: {hits}/{len(expected_kws)} ({coverage*100:.0f}%)")
                    
                except Exception:
                    result["score"] = 5
                    result["details"].append("⚠ 无法解析 arguments")
            
            result["details"].append(f"延迟: {response['latency_ms']:.0f}ms")
            
        except Exception as e:
            result["error"] = str(e)
            result["score"] = 3
            result["details"].append(f"❌ 异常: {str(e)[:100]}")
        
        return result
    
    def stage3_rag(self, question, search_context):
        """Stage 3: RAG 测试 - 注入搜索结果"""
        result = {
            "stage": 3,
            "name": "RAG",
            "score": 0,
            "max_score": 10,
            "details": [],
            "error": None
        }
        
        try:
            q = question["question"]
            
            # 构建带上下文的 prompt
            context_prompt = f"""基于以下搜索结果，回答用户的问题。

搜索结果:
{json.dumps(search_context, ensure_ascii=False, indent=2) if search_context else '暂无搜索结果'}

用户问题: {q}

要求:
1. 引用搜索结果中的具体信息
2. 如果搜索结果不足以回答，请说明
3. 标注信息的时效性"""
            
            messages = [{"role": "user", "content": context_prompt}]
            
            response = self.api.single_turn(
                self.model_name,
                context_prompt,
                timeout=self.timeout
            )
            
            if response["error"]:
                result["error"] = response["error"]
                result["score"] = 0
                result["details"].append(f"❌ 错误: {response['error']}")
                return result
            
            content = response["content"]
            reasoning = response["reasoning"]
            full_text = reasoning + content
            
            if not full_text.strip():
                result["score"] = 0
                result["details"].append("❌ 无回答内容")
                return result
            
            # 评分：答案质量
            score = 5  # 基础分
            
            # 检查是否引用了搜索结果
            cite_markers = ["根据", "据", "参考", "研究显示", "数据表明"]
            if any(m in full_text for m in cite_markers):
                score += 2
                result["details"].append("✓ 回答包含引用标记")
            
            # 检查时效性
            time_markers = ["截至", "最新", "2026", "2025", "今年", "本月"]
            if any(m in full_text for m in time_markers):
                score += 2
                result["details"].append("✓ 回答包含时效信息")
            
            # 检查长度
            if len(content) > 200:
                score += 1
                result["details"].append("✓ 回答内容充实")
            elif len(content) > 50:
                result["details"].append("⚠ 回答内容较短")
            
            result["score"] = min(score, 10)
            result["details"].append(f"回答长度: {len(full_text)} 字符")
            result["details"].append(f"延迟: {response['latency_ms']:.0f}ms")
            
        except Exception as e:
            result["error"] = str(e)
            result["score"] = 0
            result["details"].append(f"❌ 异常: {str(e)[:100]}")
        
        return result
    
    def stage4_agent_search(self, question):
        """Stage 4: 智能体搜索 - 多轮迭代"""
        result = {
            "stage": 4,
            "name": "智能体搜索",
            "score": 0,
            "max_score": 10,
            "details": [],
            "error": None,
            "iterations": []
        }
        
        try:
            q = question["question"]
            max_iterations = 3
            
            # 第一轮：生成搜索计划
            plan_prompt = f"""你是一个搜索专家。对于这个问题："{q}"

请制定一个搜索计划，包括：
1. 需要搜索的关键词
2. 每个关键词的搜索目的
3. 预期能获取的信息

请用JSON格式输出，格式如下：
{{"plan": [{{"keyword": "...", "purpose": "...", "expected_info": "..."}}]}}"""
            
            plan_response = self.api.single_turn(
                self.model_name, plan_prompt, timeout=60
            )
            
            if plan_response["error"]:
                result["error"] = plan_response["error"]
                result["score"] = 3
                result["details"].append(f"⚠ 规划阶段错误: {plan_response['error']}")
                return result
            
            # 检查规划质量
            plan_text = plan_response["content"] + plan_response["reasoning"]
            try:
                # 尝试提取 JSON 规划
                json_match = re.search(r'\{[^{}]*\}', plan_text, re.S)
                if json_match:
                    plan_data = json.loads(json_match.group())
                    plan_items = plan_data.get("plan", [])
                    
                    if len(plan_items) >= 2:
                        result["score"] += 3
                        result["details"].append(f"✓ 规划包含 {len(plan_items)} 个搜索步骤")
                    elif len(plan_items) >= 1:
                        result["score"] += 2
                        result["details"].append(f"⚠ 规划仅 {len(plan_items)} 个步骤")
            except Exception:
                result["details"].append("⚠ 规划格式非标准 JSON")
            
            # 第二轮：执行搜索（模拟）
            search_prompt = f"""基于搜索计划，执行搜索并整合结果。

问题: "{q}"
已知信息: {plan_text[:200]}

请直接回答问题，无需调用工具。"""
            
            search_response = self.api.single_turn(
                self.model_name, search_prompt, timeout=self.timeout
            )
            
            if not search_response["error"] and search_response["content"]:
                result["score"] += 3
                result["details"].append("✓ 成功生成回答")
                
                # 检查回答质量
                answer = search_response["content"]
                if len(answer) > 300:
                    result["score"] += 2
                    result["details"].append("✓ 回答内容充实")
                elif len(answer) > 100:
                    result["score"] += 1
                    result["details"].append("⚠ 回答内容一般")
            else:
                result["details"].append("❌ 无法生成有效回答")
            
            # 第三轮：自我验证
            verify_prompt = f"""请检查你的回答是否完整、准确。

原问题: "{q}"
你的回答: {search_response.get('content', '')[:300]}

如果发现问题，请补充或修正。"""
            
            verify_response = self.api.single_turn(
                self.model_name, verify_prompt, timeout=60
            )
            
            if not verify_response["error"] and verify_response["content"]:
                result["score"] += 2
                result["details"].append("✓ 完成自我验证")
            
            result["score"] = min(result["score"], 10)
            result["details"].append(f"总迭代: {max_iterations} 轮")
            
        except Exception as e:
            result["error"] = str(e)
            result["score"] = 3
            result["details"].append(f"❌ 异常: {str(e)[:100]}")
        
        return result


# ============================================================
#  动态评分系统
# ============================================================
class ScoreCalculator:
    @staticmethod
    def calculate_behavior_score(stage1_result):
        """行为分 (30%)：工具调用准确率"""
        raw = stage1_result.get("score", 0)
        max_score = stage1_result.get("max_score", 10)
        # 转换为 0-30 分
        return round((raw / max_score) * 30, 1) if max_score > 0 else 0
    
    @staticmethod
    def calculate_dynamic_score(stage2_result, stage4_result):
        """动态分 (30%)：搜索过程合理性"""
        s2 = stage2_result.get("score", 0)
        s4 = stage4_result.get("score", 0)
        # 平均两个阶段，转换为 0-30
        return round(((s2 + s4) / 20) * 30, 1)
    
    @staticmethod
    def calculate_answer_score(stage3_result):
        """答案分 (40%)：最终回答质量"""
        raw = stage3_result.get("score", 0)
        max_score = stage3_result.get("max_score", 10)
        # 转换为 0-40 分
        return round((raw / max_score) * 40, 1) if max_score > 0 else 0
    
    @staticmethod
    def calculate_total(behavior, dynamic, answer):
        """计算总分"""
        return round(behavior + dynamic + answer, 1)


# ============================================================
#  日志记录
# ============================================================
class Logger:
    def __init__(self, model_name, output_dir):
        self.model_name = model_name
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.output_dir / f"{model_name}_eval.log"
        self.results_file = self.output_dir / f"{model_name}_results.json"
        self.log_data = []
        
    def log(self, message, level="INFO"):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message
        }
        self.log_data.append(entry)
        return entry
    
    def log_stage(self, question_id, stage_name, result):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "question_id": question_id,
            "stage": stage_name,
            "score": result.get("score", 0),
            "max_score": result.get("max_score", 10),
            "details": result.get("details", []),
            "error": result.get("error")
        }
        self.log_data.append(entry)
        return entry
    
    def save(self):
        with open(self.log_file, "w", encoding="utf-8") as f:
            json.dump(self.log_data, f, ensure_ascii=False, indent=2)
    
    def save_results(self, results):
        with open(self.results_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)


# ============================================================
#  主测试流程
# ============================================================
class AgentToolEvaluator:
    def __init__(self, args):
        self.args = args
        self.api = ModelAPI(args.server, args.apikey)
        self.manager = ModelManager(args.server, args.apikey, args.launcher)
        self.questions = []
        self.model_results = {}
        
    def load_questions(self):
        """加载题目库"""
        questions_path = Path(self.args.questions_file)
        if not questions_path.exists():
            questions_path = Path(__file__).parent / self.args.questions_file
        
        try:
            with open(questions_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.questions = data.get("questions", [])
            print(f"  ✅ 加载题目库: {len(self.questions)} 道题")
            return True
        except Exception as e:
            print(f"  ❌ 加载题目库失败: {e}")
            return False
    
    def run_model_test(self, model_index, model_name):
        """运行单个模型测试"""
        print(f"\n{'='*60}")
        print(f"测试模型: {model_name}")
        print(f"{'='*60}")
        
        tester = StageTester(self.api, model_name, self.args.timeout)
        logger = Logger(model_name, RESULTS_DIR)
        progress = ProgressDisplay(len(self.questions) * 4, model_name)
        
        all_stage_results = []
        
        for q_idx, question in enumerate(self.questions, 1):
            q_id = question.get("id", q_idx)
            q_text = question.get("question", "")
            should_use_tool = question.get("should_use_tool", True)
            
            if RICH_AVAILABLE and console:
                console.print(f"\n  📝 Q{q_id}: {q_text[:60]}...")
            
            # Stage 1: 工具调用
            stage1 = tester.stage1_tool_call(question)
            logger.log_stage(q_id, "工具调用", stage1)
            progress.update(q_id, 1, "success" if stage1["score"] >= 5 else "error")
            
            # Stage 2: 联网搜索
            stage2 = tester.stage2_search(question, stage1)
            logger.log_stage(q_id, "联网搜索", stage2)
            progress.update(q_id, 2, "success" if stage2["score"] >= 5 else "error")
            
            # Stage 3: RAG
            search_context = {"query": stage1.get("details", [""])[0] if stage1["details"] else "",
                             "results": []}
            stage3 = tester.stage3_rag(question, search_context)
            logger.log_stage(q_id, "RAG", stage3)
            progress.update(q_id, 3, "success" if stage3["score"] >= 5 else "error")
            
            # Stage 4: 智能体搜索
            stage4 = tester.stage4_agent_search(question)
            logger.log_stage(q_id, "智能体搜索", stage4)
            progress.update(q_id, 4, "success" if stage4["score"] >= 5 else "error")
            
            # 计算本题各维度得分
            behavior = ScoreCalculator.calculate_behavior_score(stage1)
            dynamic = ScoreCalculator.calculate_dynamic_score(stage2, stage4)
            answer = ScoreCalculator.calculate_answer_score(stage3)
            total = ScoreCalculator.calculate_total(behavior, dynamic, answer)
            
            all_stage_results.append({
                "question_id": q_id,
                "question": q_text,
                "should_use_tool": should_use_tool,
                "stages": {
                    "stage1_tool_call": stage1,
                    "stage2_search": stage2,
                    "stage3_rag": stage3,
                    "stage4_agent_search": stage4
                },
                "scores": {
                    "behavior": behavior,
                    "dynamic": dynamic,
                    "answer": answer,
                    "total": total
                }
            })
            
            if RICH_AVAILABLE and console:
                color = "green" if total >= 20 else ("yellow" if total >= 15 else "red")
                console.print(f"  ✅ Q{q_id} 得分: [bold {color}]{total:.1f}/100[/] "
                            f"(行为:{behavior:.0f}/30 动态:{dynamic:.0f}/30 答案:{answer:.0f}/40)")
        
        progress.complete()
        
        # 汇总统计
        if all_stage_results:
            avg_behavior = sum(r["scores"]["behavior"] for r in all_stage_results) / len(all_stage_results)
            avg_dynamic = sum(r["scores"]["dynamic"] for r in all_stage_results) / len(all_stage_results)
            avg_answer = sum(r["scores"]["answer"] for r in all_stage_results) / len(all_stage_results)
            avg_total = sum(r["scores"]["total"] for r in all_stage_results) / len(all_stage_results)
        else:
            avg_behavior = avg_dynamic = avg_answer = avg_total = 0
        
        model_result = {
            "model_index": model_index,
            "model_name": model_name,
            "timestamp": datetime.now().isoformat(),
            "questions_count": len(self.questions),
            "average_scores": {
                "behavior": round(avg_behavior, 1),
                "dynamic": round(avg_dynamic, 1),
                "answer": round(avg_answer, 1),
                "total": round(avg_total, 1)
            },
            "stage_results": all_stage_results
        }
        
        # 保存结果
        logger.save()
        logger.save_results(model_result)
        
        self.model_results[model_name] = model_result
        
        # 打印汇总
        print(f"\n  📊 {model_name} 测评汇总:")
        print(f"     行为分: {avg_behavior:.1f}/30")
        print(f"     动态分: {avg_dynamic:.1f}/30")
        print(f"     答案分: {avg_answer:.1f}/40")
        print(f"     总分:   {avg_total:.1f}/100")
        
        return model_result
    
    def run(self):
        """运行测试"""
        print("=" * 60)
        print("AI 工具调用能力对比测试框架 v1.0")
        print("=" * 60)
        print(f"服务器: {self.args.server}")
        print(f"题目库: {self.args.questions_file}")
        print(f"超时:   {self.args.timeout}s")
        print()
        
        # 加载题目
        if not self.load_questions():
            return
        
        # 获取模型列表
        models = self.manager.get_model_list()
        if not models:
            print("❌ 无法获取模型列表")
            return
        
        # 选择测试的模型
        if self.args.models:
            test_indices = [int(x.strip()) for x in self.args.models.split(",")]
        else:
            test_indices = [m["index"] for m in models]
        
        test_models = [m for m in models if m["index"] in test_indices]
        print(f"\n将测试 {len(test_models)} 个模型:")
        for m in test_models:
            print(f"  #{m['index']} {m['name']}")
        
        # 逐个模型测试
        for model_info in test_models:
            idx = model_info["index"]
            name = model_info["name"]
            
            print(f"\n{'#'*60}")
            print(f"# [{idx}/{len(models)}] {name}")
            print(f"{'#'*60}")
            
            # 启动模型
            model_name = self.manager.start_model(idx)
            if not model_name:
                print(f"❌ 模型 #{idx} 启动失败")
                continue
            
            try:
                self.run_model_test(idx, model_name)
            except Exception as e:
                print(f"❌ 测试异常: {e}")
                traceback.print_exc()
            finally:
                # 停止模型
                self.manager.stop_model()
                time.sleep(3)  # 冷却
        
        # 生成对比报告
        self.generate_comparison_report()
    
    def generate_comparison_report(self):
        """生成对比报告"""
        if not self.model_results:
            print("\n⚠ 无测试结果")
            return
        
        print(f"\n{'='*60}")
        print("📊 全模型对比报告")
        print(f"{'='*60}")
        
        # 排序
        sorted_results = sorted(
            self.model_results.items(),
            key=lambda x: x[1]["average_scores"]["total"],
            reverse=True
        )
        
        if RICH_AVAILABLE and console:
            # 使用 Rich 表格
            table = Table(title="工具调用能力对比报告")
            table.add_column("排名", justify="center")
            table.add_column("模型", style="cyan", no_wrap=True)
            table.add_column("行为分", justify="center")
            table.add_column("动态分", justify="center")
            table.add_column("答案分", justify="center")
            table.add_column("总分", justify="center", style="bold")
            
            for rank, (name, result) in enumerate(sorted_results, 1):
                scores = result["average_scores"]
                table.add_row(
                    str(rank),
                    name[:40],
                    f"{scores['behavior']:.1f}/30",
                    f"{scores['dynamic']:.1f}/30",
                    f"{scores['answer']:.1f}/40",
                    f"[bold]{scores['total']:.1f}[/]"
                )
            
            console.print(table)
        else:
            # 纯文本表格
            print(f"\n{'#':<3} {'模型':<40} {'行为':<8} {'动态':<8} {'答案':<8} {'总分':<8}")
            print("-" * 75)
            for rank, (name, result) in enumerate(sorted_results, 1):
                scores = result["average_scores"]
                print(f"{rank:<3} {name[:38]:<40} {scores['behavior']:5.1f}/30  {scores['dynamic']:5.1f}/30  {scores['answer']:5.1f}/40  {scores['total']:5.1f}")
        
        # 保存汇总结果
        summary = {
            "timestamp": datetime.now().isoformat(),
            "models_count": len(self.model_results),
            "results": {}
        }
        for name, result in self.model_results.items():
            summary["results"][name] = result["average_scores"]
        
        summary_file = Path(RESULTS_DIR) / "comparison_summary.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        
        print(f"\n📁 结果已保存至: {RESULTS_DIR}/")
        print(f"   - 各模型详细结果: {model_name}_eval.log / {model_name}_results.json")
        print(f"   - 汇总报告: comparison_summary.json")


# ============================================================
#  命令行入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="AI 工具调用能力对比测试框架 v1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python agent_tool_eval.py --models 1,2,3        # 测试指定模型
  python agent_tool_eval.py --all                   # 测试所有模型
  python agent_tool_eval.py --models 1 --timeout 180
        """
    )
    parser.add_argument(
        "--models", type=str, default="",
        help="要测试的模型索引，用逗号分隔，如 '1,2,3'。不指定时用 --all 或默认全部"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="测试所有模型"
    )
    parser.add_argument(
        "--server", default=DEFAULT_SERVER,
        help=f"API 服务器地址 (默认: {DEFAULT_SERVER})"
    )
    parser.add_argument(
        "--apikey", default=DEFAULT_API_KEY,
        help=f"API Key (默认: {DEFAULT_API_KEY})"
    )
    parser.add_argument(
        "--launcher", default=None,
        help="启动器脚本路径 (默认: 同目录下的 launcher_main.ps1)"
    )
    parser.add_argument(
        "--questions-file", default=DEFAULT_QUESTIONS_FILE,
        help=f"题目库 JSON 文件路径 (默认: {DEFAULT_QUESTIONS_FILE})"
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help=f"每轮超时时间（秒）(默认: {DEFAULT_TIMEOUT})"
    )
    
    args = parser.parse_args()
    
    # 设置 launcher 路径
    if args.launcher is None:
        script_dir = Path(__file__).parent
        args.launcher = str(script_dir / LAUNCHER_SCRIPT)
    
    # 处理模型选择
    if not args.models and not args.all:
        print("ℹ 未指定 --models 或 --all，默认测试所有模型")
        args.all = True
    
    # 创建结果目录
    Path(RESULTS_DIR).mkdir(exist_ok=True)
    
    # 运行测试
    evaluator = AgentToolEvaluator(args)
    evaluator.run()


if __name__ == "__main__":
    main()
