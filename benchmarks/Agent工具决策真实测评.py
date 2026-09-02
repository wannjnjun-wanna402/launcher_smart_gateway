#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
真实RAG能力评估框架 v2
======================
核心改进：真实搜索闭环 + 答案验证 + 难度分层

评估维度（总分 75）：
  1. 决策正确性 (20分) - 该调工具时调，不该调时直接答
  2. Query质量 (25分) - 生成的query质量 + 真实搜索效果验证
  3. 答案质量 (30分) - 答案准确性、时效性、无事实矛盾

关键特性：
  - 使用 DuckDuckGo 免费搜索（无需 API key）
  - 真实执行搜索并通过 tool role 回传（真正的 RAG 闭环）
  - 答案与搜索结果交叉验证 + 事实矛盾检测
  - 难度分层题目（简单/中等/困难）
"""

import argparse
import json
import sys
import time
import re
import subprocess
import urllib.parse
import urllib.request
import traceback
from datetime import datetime
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    import requests
except ImportError:
    print("ERROR: pip install requests")
    sys.exit(1)

DEFAULT_SERVER = "http://127.0.0.1:8081"
DEFAULT_API_KEY = "llamacpp"
DEFAULT_TIMEOUT = 180
RESULTS_DIR = "eval_results"

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search",
        "description": "搜索互联网获取最新信息",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"]
        }
    }
}


# ============================================================
#  真实搜索引擎 (DuckDuckGo API + Sogou API + Bing 兜底)
# ============================================================
class RealSearchEngine:
    """真实执行搜索 - 使用 API 而非 HTML 爬取"""
    
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    def __init__(self):
        self._ddg_available = None  # 缓存 DuckDuckGo 可用性
        self._sogou_available = None
    
    def search(self, query, max_results=5, max_retries=2):
        """执行真实搜索 - 多级降级策略"""
        results = {
            "query": query,
            "results": [],
            "count": 0,
            "latency_ms": 0,
            "source": "",
            "error": None,
            "retries": 0
        }
        
        t_total = time.time()
        
        # 主引擎：DuckDuckGo API (无需代理，全球可用，结构化数据)
        if not results["count"]:
            ok = self._search_duckduckgo(query, results, max_results)
            if ok:
                self._validate_results(results)
                if results["count"] > 0:
                    results["latency_ms"] = (time.time() - t_total) * 1000
                    return results
        
        # 备用引擎：Sogou 搜索 API (国内中文搜索最佳)
        if not results["count"]:
            ok = self._search_sogou(query, results, max_results)
            if ok:
                self._validate_results(results)
                if results["count"] > 0:
                    results["latency_ms"] = (time.time() - t_total) * 1000
                    return results
        
        # 兜底方案：Bing 搜索 (增加反爬防护)
        if not results["count"]:
            ok = self._search_bing_fallback(query, results, max_results)
            if ok:
                self._validate_results(results)
                if results["count"] > 0:
                    results["latency_ms"] = (time.time() - t_total) * 1000
                    return results
        
        results["latency_ms"] = (time.time() - t_total) * 1000
        return results
    
    def _validate_results(self, results):
        """验证搜索结果质量 - 过滤字典释义、无效内容"""
        valid_results = []
        for r in results.get("results", []):
            title = r.get("title", "")
            body = r.get("body", "")
            # 过滤条件：标题过短（可能是单字释义）或内容无意义
            if len(title) < 3:
                continue
            # 过滤纯字典/词典内容的特征
            if re.match(r'^[a-zA-Z\u4e00-\u9fff]$', title) and len(body) < 50:
                continue
            # 过滤 body 为 null/None 的无效结果
            if body is None or body.lower() == 'null':
                continue
            valid_results.append(r)
        
        results["results"] = valid_results
        results["count"] = len(valid_results)
    
    def _search_duckduckgo(self, query, results, max_results):
        """DuckDuckGo Instant Answer API - 结构化数据"""
        try:
            t0 = time.time()
            url = "https://api.duckduckgo.com/"
            params = {
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
                "no_redirect": "1"
            }
            headers = {
                "User-Agent": self.USER_AGENT,
                "Accept": "application/json"
            }
            
            resp = requests.get(url, params=params, headers=headers, timeout=8)
            if resp.status_code != 200:
                results["error"] = f"DDG HTTP {resp.status_code}"
                return False
            
            data = resp.json()
            
            # 从多个字段提取结果
            extracted = []
            
            # 1. Abstract (摘要)
            if data.get("AbstractText"):
                extracted.append({
                    "title": data.get("Heading", query),
                    "body": data["AbstractText"],
                    "url": data.get("AbstractURL", "")
                })
            
            # 2. RelatedTopics (相关主题) - 最重要的结果来源
            for topic in data.get("RelatedTopics", []):
                if isinstance(topic, dict) and topic.get("Text"):
                    extracted.append({
                        "title": topic.get("Text", "")[:80],
                        "body": topic.get("Text", ""),
                        "url": topic.get("FirstURL", "")
                    })
            
            # 3. Answer (直接回答)
            if data.get("Answer"):
                extracted.insert(0, {
                    "title": query,
                    "body": data["Answer"],
                    "url": ""
                })
            
            # 4. Definition (定义)
            if data.get("Definition"):
                extracted.append({
                    "title": data.get("Heading", query),
                    "body": data["Definition"],
                    "url": data.get("DefinitionURL", "")
                })
            
            if extracted:
                for item in extracted[:max_results]:
                    results["results"].append(item)
                results["count"] = len(results["results"])
                results["source"] = "DuckDuckGo"
                results["latency_ms"] = (time.time() - t0) * 1000
                return results["count"] > 0
            
        except requests.exceptions.Timeout:
            results["error"] = "DDG Timeout"
        except requests.exceptions.ConnectionError:
            results["error"] = "DDG ConnectionError"
            self._ddg_available = False
        except Exception as e:
            results["error"] = f"DDG: {str(e)[:80]}"
        results["latency_ms"] = (time.time() - t0) * 1000
        return False
    
    def _search_sogou(self, query, results, max_results):
        """Sogou 搜索 - 国内中文搜索"""
        try:
            t0 = time.time()
            url = "https://www.sogou.com/web"
            params = {"query": query}
            headers = {
                "User-Agent": self.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Cookie": "ABTEST=0|1700000000|v17; SNUID=1A2B3C4D5E6F7890"
            }
            
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code != 200:
                results["error"] = f"Sogou HTTP {resp.status_code}"
                return False
            
            html = resp.text
            
            # Sogou 搜索结果解析
            blocks = re.findall(
                r'<div class="vrwrap.*?<h3 class="vr-title.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<p[^>]*class="space-txt[^"]*"[^>]*>(.*?)</p>',
                html, re.S
            )
            
            if not blocks:
                # 备用正则
                blocks = re.findall(
                    r'<h3[^>]*class="vr-title[^"]*"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?</h3>.*?<p[^>]*>(.*?)</p>',
                    html, re.S
                )
            
            if blocks:
                for b_url, title, body in blocks[:max_results]:
                    title = re.sub(r'<[^>]+>', '', title).strip()
                    body = re.sub(r'<[^>]+>', '', body).strip()
                    if title and len(title) >= 3:
                        results["results"].append({
                            "title": title,
                            "body": body or title,
                            "url": b_url
                        })
                
                results["count"] = len(results["results"])
                results["source"] = "Sogou"
                results["latency_ms"] = (time.time() - t0) * 1000
                return results["count"] > 0
                
        except requests.exceptions.Timeout:
            results["error"] = "Sogou Timeout"
        except Exception as e:
            results["error"] = f"Sogou: {str(e)[:80]}"
        results["latency_ms"] = (time.time() - t0) * 1000
        return False
    
    def _search_bing_fallback(self, query, results, max_results):
        """Bing 搜索兜底 - 增加反爬防护"""
        try:
            t0 = time.time()
            url = "https://www.bing.com/search"
            params = {
                "q": query,
                "setlang": "zh-CN",
                "cc": "CN",
                "form": "QBRE",
                "first": "1"
            }
            headers = {
                "User-Agent": self.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1"
            }
            
            resp = requests.get(url, params=params, headers=headers, timeout=12)
            if resp.status_code != 200:
                results["error"] = f"Bing HTTP {resp.status_code}"
                return False
            
            html = resp.text
            
            # Bing 搜索结果解析
            blocks = re.findall(
                r'<li class="b_algo".*?<h2.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<p[^>]*>(.*?)</p>',
                html, re.S
            )
            
            if blocks:
                for b_url, title, body in blocks[:max_results]:
                    title = re.sub(r'<[^>]+>', '', title).strip()
                    body = re.sub(r'<[^>]+>', '', body).strip()
                    if title and len(title) >= 3:
                        results["results"].append({
                            "title": title,
                            "body": body or title,
                            "url": b_url
                        })
                
                results["count"] = len(results["results"])
                results["source"] = "Bing"
                results["latency_ms"] = (time.time() - t0) * 1000
                return results["count"] > 0
                
        except requests.exceptions.Timeout:
            results["error"] = "Bing Timeout"
        except Exception as e:
            results["error"] = f"Bing: {str(e)[:80]}"
        results["latency_ms"] = (time.time() - t0) * 1000
        return False


# ============================================================
#  模型管理
# ============================================================
class ModelManager:
    def __init__(self, server, api_key, launcher_path):
        self.server = server
        self.api_key = api_key
        self.launcher_path = launcher_path
        self.process = None
        
    def get_model_list(self):
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
            print(f"ERROR: {e}")
        return []
    
    def kill_port(self):
        """彻底清理所有 llama-server 进程和端口占用"""
        print("  🔧 清理残留进程...")
        
        # 第1步: 杀掉所有 llama-server 进程
        try:
            subprocess.run([
                "powershell", "-NoProfile", "-Command",
                "Get-Process | Where-Object { $_.ProcessName -match 'llama-server|llama-cli' } | "
                "ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue; "
                "Write-Host ('  已杀进程 PID: ' + $_.Id) }"
            ], capture_output=True, timeout=15)
        except Exception:
            pass
        
        # 第2步: 杀掉占用端口8081的进程
        try:
            result = subprocess.run([
                "powershell", "-NoProfile", "-Command",
                "$conn = Get-NetTCPConnection -LocalPort 8081 -ErrorAction SilentlyContinue; "
                "if ($conn) { $pid = $conn[0].OwningProcess; "
                "Write-Host ('  杀端口8081占用 PID: ' + $pid); "
                "Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue }"
            ], capture_output=True, timeout=10, text=True)
            if result.stdout.strip():
                print(f"    {result.stdout.strip()}")
        except Exception:
            pass
        
        # 第3步: 等待 GPU 显存释放（最多等 60 秒）
        for i in range(30):
            time.sleep(2)
            try:
                r = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5
                )
                mem_used = int(r.stdout.strip())
                print(f"    GPU显存: {mem_used}MB")
                if mem_used < 500:  # 显存低于 500MB 说明模型已完全卸载
                    print("  ✅ GPU 已清理干净")
                    time.sleep(1)  # 再等 1 秒确保稳定
                    return True
            except Exception:
                pass
            if i % 5 == 0 and i > 0:
                print(f"    等待显存释放... ({i*2}s)")
        
        print("  ⚠️ 显存未完全释放，继续尝试...")
        return False
    
    def start(self, index):
        # 先彻底清理，确保 GPU 干净
        self.kill_port()
        
        # 额外检查 GPU 显存（如果还很高，说明清理失败）
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            mem_used = int(r.stdout.strip())
            if mem_used > 500:
                print(f"  ⚠️ 警告: GPU 显存仍有 {mem_used}MB，可能有残留模型")
                for _ in range(15):
                    time.sleep(2)
                    r2 = subprocess.run(
                        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=5
                    )
                    if int(r2.stdout.strip()) < 500:
                        break
        except Exception:
            pass
        
        log_dir = Path(RESULTS_DIR)
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"launch_{index}.log"
        
        with open(log_file, "w", encoding="utf-8") as lf:
            self.process = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", self.launcher_path, "-ModelIndex", str(index), "-NoInteractive"],
                stdout=lf, stderr=lf
            )
        
        return self._wait_ready(300)
    
    def _wait_ready(self, timeout):
        start = time.time()
        while time.time() - start < timeout:
            try:
                r = requests.get(
                    f"{self.server}/v1/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=5
                )
                if r.status_code == 200:
                    data = r.json()
                    return data.get("data", [{}])[0].get("id", "unknown")
            except Exception:
                pass
            time.sleep(3)
        return None
    
    def stop(self):
        """停止当前模型并彻底清理"""
        # 第1步: 终止主进程
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
                print("  已终止 PowerShell 进程")
            except Exception:
                self.process.kill()
                print("  已强制终止 PowerShell 进程")
        
        # 第2步: 彻底杀所有 llama 相关进程
        try:
            result = subprocess.run([
                "powershell", "-NoProfile", "-Command",
                "Get-Process | Where-Object { $_.ProcessName -match 'llama' } | "
                "ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue; "
                "Write-Host ('  杀进程: ' + $_.ProcessName + ' PID:' + $_.Id) }"
            ], capture_output=True, timeout=15, text=True)
            if result.stdout.strip():
                print(result.stdout.strip())
        except Exception:
            pass
        
        # 第3步: 等待 GPU 显存释放（最多 60 秒）
        print("  等待 GPU 显存释放...")
        for i in range(30):
            time.sleep(2)
            try:
                r = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5
                )
                mem_used = int(r.stdout.strip())
                if mem_used < 500:
                    print(f"  ✅ GPU 显存已释放 ({mem_used}MB)")
                    time.sleep(1)
                    return True
            except Exception:
                pass
            if i == 4:
                print(f"  仍在释放中... ({mem_used}MB)")
        
        print("  ⚠️ 显存释放超时")
        return False


# ============================================================
#  模型 API
# ============================================================
class ModelAPI:
    def __init__(self, server, api_key):
        self.server = server
        self.api_key = api_key
    
    def call(self, model, messages, tools=None, timeout=180):
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
        tc_idx = {}
        
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
                    
                    rc = delta.get("reasoning_content")
                    if rc:
                        result["reasoning"] += rc
                    c = delta.get("content")
                    if c:
                        result["content"] += c
                    
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        fn = tc.get("function") or {}
                        if idx not in tc_idx:
                            tc_idx[idx] = {"name": "", "arguments": ""}
                        if fn.get("name"):
                            tc_idx[idx]["name"] += fn["name"]
                        if fn.get("arguments"):
                            tc_idx[idx]["arguments"] += fn["arguments"]
                    
                    finish = ch.get("finish_reason")
                    if finish and finish in ("stop", "tool_calls", "length", "content_filter"):
                        break
            
            result["tool_calls"] = [tc_idx[k] for k in sorted(tc_idx)]
            result["latency_ms"] = (time.time() - t0) * 1000
            
        except requests.exceptions.Timeout:
            result["error"] = f"TIMEOUT({timeout}s)"
            result["latency_ms"] = (time.time() - t0) * 1000
        except requests.exceptions.ConnectionError as e:
            result["error"] = f"CONN_ERR: {str(e)[:80]}"
            result["latency_ms"] = (time.time() - t0) * 1000
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {str(e)[:80]}"
            result["latency_ms"] = (time.time() - t0) * 1000
        
        return result


# ============================================================
#  评分系统
# ============================================================
class RealScorer:
    """真实能力评分 - 基于搜索结果交叉验证"""
    
    @staticmethod
    def score_decision(question, response):
        """决策正确性 (20分)"""
        should_call = question.get("should_call_tool", False)
        tool_calls = response.get("tool_calls", [])
        called = len(tool_calls) > 0
        
        if should_call and called:
            return 20, "✓ 正确调用工具"
        elif not should_call and not called:
            return 20, "✓ 正确抑制工具调用"
        elif should_call and not called:
            return 0, "❌ 应该调用工具却没调用"
        else:
            return 0, "❌ 不该调用却调用了"
    
    @staticmethod
    def score_query(question, response, search_result):
        """Query质量 (25分) - 基于真实搜索结果"""
        tool_calls = response.get("tool_calls", [])
        should_call = question.get("should_call_tool", False)
        
        if not tool_calls:
            # 模型没有调用工具
            if not should_call:
                # 不该调 → 正确抑制，Query分满分
                return 25, "正确抑制工具调用，Query分满分"
            else:
                # 该调但没调 → 0分
                return 0, "应该调用工具却没调用"
        
        # 模型调用了工具，评估 query 质量
        tc = tool_calls[0]
        args = tc.get("arguments", "")
        try:
            arg_obj = json.loads(args)
            query = arg_obj.get("query", "")
        except Exception:
            return 0, "query 格式错误"
        
        if not query:
            return 0, "query 为空"
        
        # 评分维度：长度(4) + 实体(6) + 时效(3) + 搜索效果(12) = 25
        score = 0
        details = []
        
        # 1. 查询长度 (4分)
        if len(query) < 5:
            score += 1
            details.append("query过短")
        elif len(query) > 100:
            score += 2
            details.append("query过长")
        else:
            score += 4
            details.append(f"query长度适中({len(query)}字)")
        
        # 2. 是否包含关键实体 (6分)
        entities = question.get("key_entities", [])
        if entities:
            hits = sum(1 for e in entities if e.lower() in query.lower())
            if hits == len(entities):
                score += 6
                details.append("包含所有关键实体")
            elif hits > 0:
                score += 3
                details.append(f"包含{hits}/{len(entities)}个实体")
            else:
                details.append("缺少关键实体")
        else:
            score += 3
            details.append("无预设实体，基础分")
        
        # 3. 是否包含时间限定 (3分)
        has_time = any(w in query.lower() for w in [
            "今天", "最新", "2026", "2025", "2024", "今年", "近日", "recent", "latest", "current"
        ])
        if has_time:
            score += 3
            details.append("包含时间限定")
        
        # 4. 基于搜索结果质量调整 (12分)
        search_count = search_result.get("count", 0)
        if search_count >= 3:
            score += 12
            details.append(f"搜索命中{search_count}条结果")
        elif search_count >= 1:
            score += 6
            details.append(f"搜索仅{search_count}条结果")
        else:
            details.append("搜索无结果")
        
        return max(0, min(25, score)), "; ".join(details)
    
    @staticmethod
    def score_answer(question, response, search_result):
        """答案质量 (30分) - 与搜索结果交叉验证"""
        content = response.get("content", "")
        reasoning = response.get("reasoning", "")
        full_text = reasoning + " " + content
        
        if not content.strip():
            return 0, "无回答内容"
        
        # 分数分配：长度(4) + 时效(4) + 一致性(10) + 无矛盾(6) + 完整性(6) = 30
        score = 0
        details = []
        search_results = search_result.get("results", [])
        
        all_search_text = " ".join(
            r.get("title", "") + " " + r.get("body", "")
            for r in search_results
        ).lower() if search_results else ""
        
        # 1. 回答长度 (4分)
        answer_len = len(content)
        if answer_len >= 500:
            score += 4
            details.append("回答充实")
        elif answer_len >= 200:
            score += 3
            details.append("回答适中")
        elif answer_len >= 50:
            score += 1
            details.append("回答较短")
        
        # 2. 时效性 (4分)
        time_indicators = ["截至", "最新", "2026", "2025", "2024", "今年", "本月", "recent", "as of"]
        has_time_info = any(t in full_text.lower() for t in time_indicators)
        if has_time_info:
            score += 4
            details.append("包含时效信息")
        
        # 3. 与搜索结果一致性 (10分) - 检查关键实体是否被引用
        if search_results and all_search_text:
            entities = question.get("key_entities", [])
            if entities:
                consistent_count = sum(
                    1 for e in entities
                    if e.lower() in full_text.lower() and e.lower() in all_search_text
                )
                if consistent_count >= len(entities) * 0.7:
                    score += 10
                    details.append("答案与搜索结果高度一致")
                elif consistent_count > 0:
                    score += 5
                    details.append(f"答案部分与搜索结果一致({consistent_count}/{len(entities)})")
                else:
                    details.append("答案与搜索结果不匹配")
            else:
                score += 5
                details.append("无预设实体，跳过一致性检查")
            
            # 4. 事实矛盾检测 (6分) - 检查答案是否包含与搜索结果矛盾的信息
            # 矛盾检测：提取答案中的数字/年份，检查是否与搜索结果中的数字冲突
            contradictions = []
            
            # 提取答案中的数字（年份、百分比、金额等）
            answer_numbers = set(re.findall(r'\b(19|20)\d{2}\b', content))  # 年份
            search_numbers = set(re.findall(r'\b(19|20)\d{2}\b', all_search_text))
            
            # 检查答案中的年份是否与搜索结果矛盾
            # 如果答案提到一个年份，但搜索结果中完全没有相关年份，可能是编造
            if answer_numbers and search_numbers:
                # 如果答案年份与搜索年份完全无关（差距超过5年），标记为可疑
                for year_str in answer_numbers:
                    year = int(year_str)
                    closest = min(search_numbers, key=lambda y: abs(int(y) - year))
                    if abs(year - int(closest)) > 5:
                        contradictions.append(f"年份{year_str}与搜索结果不符")
            
            # 检查答案中的关键数字（如市值、人口）是否与搜索结果一致
            answer_big_numbers = set(re.findall(r'\b\d+(?:\.\d+)?(?:亿|万亿|billion|trillion)\b', content))
            search_big_numbers = set(re.findall(r'\b\d+(?:\.\d+)?(?:亿|万亿|billion|trillion)\b', all_search_text))
            
            if answer_big_numbers and search_big_numbers:
                # 如果答案有数字但搜索没有对应数字，标记为可疑
                unmatched = answer_big_numbers - search_big_numbers
                if len(unmatched) > len(answer_big_numbers) * 0.5:
                    contradictions.append(f"数字信息未在搜索结果中验证")
            
            if not contradictions:
                score += 6
                details.append("无明显事实矛盾")
            elif len(contradictions) <= 1:
                score += 3
                details.append(f"轻微矛盾: {'; '.join(contradictions)}")
            else:
                details.append(f"事实矛盾: {'; '.join(contradictions[:3])}")
        else:
            # 没有搜索结果时，给基础分
            score += 5
            details.append("无搜索结果，跳过一致性/矛盾检测")
        
        # 5. 回答完整性 (6分)
        sub_questions = question.get("sub_questions", [])
        if sub_questions:
            covered = sum(
                1 for sq in sub_questions
                if any(kw in content for kw in sq.get("keywords", []))
            )
            if covered >= len(sub_questions) * 0.8:
                score += 6
                details.append("回答完整覆盖所有问题点")
            elif covered >= len(sub_questions) * 0.5:
                score += 3
                details.append(f"回答部分覆盖({covered}/{len(sub_questions)}个问题点)")
        else:
            # 没有子问题时，给基础完整性分
            score += 3
            details.append("无子问题，基础完整性分")
        
        return min(30, score), "; ".join(details)


# ============================================================
#  主测试流程
# ============================================================
class RealEvaluator:
    def __init__(self, args):
        self.args = args
        self.api = ModelAPI(args.server, args.apikey)
        self.manager = ModelManager(args.server, args.apikey, args.launcher)
        self.search_engine = RealSearchEngine()
        self.scorer = RealScorer()
        self.questions = []
        self.results = {}
    
    def load_questions(self):
        qpath = Path(self.args.questions_file)
        if not qpath.exists():
            qpath = Path(__file__).parent / self.args.questions_file
        
        with open(qpath, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.questions = data.get("questions", [])
        
        print(f"  ✅ 加载题目: {len(self.questions)} 道")
        print(f"     类型分布:")
        tool = sum(1 for q in self.questions if q.get("should_call_tool"))
        no_tool = len(self.questions) - tool
        print(f"       - 应该使用工具: {tool} 道")
        print(f"       - 应该直接回答: {no_tool} 道")
        return True
    
    def test_model(self, model_idx, model_name):
        print(f"\n{'='*60}")
        print(f"测试: {model_name}")
        print(f"{'='*60}")
        
        all_results = []
        
        for q_idx, q in enumerate(self.questions, 1):
            qid = q.get("id", q_idx)
            qtext = q.get("question", "")
            difficulty = q.get("difficulty", "中等")
            
            print(f"\n  [{q_idx}/{len(self.questions)}] {qtext[:50]}...")
            print(f"    难度: {difficulty} | 类型: {'需工具' if q.get('should_call_tool') else '直接答'}")
            
            # Stage 1: 模型生成响应（所有题目都给工具，让模型自己决定）
            messages = [{"role": "user", "content": qtext}]
            response = self.api.call(
                model_name, messages,
                tools=[SEARCH_TOOL],  # 始终提供工具
                timeout=self.args.timeout
            )
            
            if response["error"]:
                print(f"    ❌ 错误: {response['error']}")
                all_results.append({
                    "question_id": qid,
                    "error": response["error"],
                    "total_score": 0
                })
                continue
            
            # Stage 2: 真实搜索（如果模型调用了工具）
            tool_calls = response.get("tool_calls", [])
            search_result = {"query": "", "results": [], "count": 0, "latency_ms": 0}
            final_response = response
            should_call = q.get("should_call_tool", False)
            
            if tool_calls:
                tc = tool_calls[0]
                args = tc.get("arguments", "")
                try:
                    arg_obj = json.loads(args)
                    query = arg_obj.get("query", "")
                    if query:
                        print(f"    🔍 模型生成 query: '{query}'")
                        
                        # 关键检查：不该调工具却调了 → 不执行搜索，不注入 RAG
                        if not should_call:
                            print(f"    ⚠️ 该题不应调用工具，跳过搜索和 RAG")
                            # 记录 search_result 但不执行搜索（模型决策错误，不应受益于搜索）
                            search_result = {"query": query, "results": [], "count": 0, "latency_ms": 0}
                        else:
                            search_result = self.search_engine.search(query)
                            print(f"    📊 搜索结果: {search_result['count']} 条, "
                                  f"延迟 {search_result['latency_ms']:.0f}ms")
                            
                            # Stage 3: 真正的 RAG - 把搜索结果注入 user 消息（兼容 llama.cpp）
                            if search_result["count"] > 0:
                                # 提取搜索结果文本
                                search_text = ""
                                for i, r in enumerate(search_result["results"][:5], 1):
                                    title = r.get("title", "")
                                    body = r.get("body", "")
                                    search_text += f"\n[{i}] {title}\n{body}\n"
                                
                                # 构建 RAG prompt
                                rag_user_msg = f"""用户问题：{qtext}

以下是搜索到的相关信息：
{search_text}

请基于以上搜索结果，回答用户的问题。要求：
1. 直接回答问题，不要说"根据搜索结果"之类的话
2. 如果搜索结果中有具体数据（如日期、金额），请准确引用
3. 如果搜索结果不足以回答问题，说明原因"""
                                
                                print(f"    📚 注入搜索结果 ({len(search_text)}字)，请求回答...")
                                
                                # 用新的 user 消息追加到对话中（简化版 RAG）
                                messages_for_rag = messages + [{"role": "user", "content": rag_user_msg}]
                                
                                final_response = self.api.call(
                                    model_name, messages_for_rag,
                                    tools=None,  # ⚠️ 不提供工具，强制生成回答
                                    timeout=self.args.timeout
                                )
                                
                                # 调试输出
                                print(f"    🔍 RAG 响应: error={final_response.get('error')}, "
                                      f"content_len={len(final_response.get('content', ''))}, "
                                      f"tool_calls={len(final_response.get('tool_calls', []))}")
                                
                                # 检查 RAG 结果
                                if not final_response["error"] and final_response.get("content"):
                                    print(f"    ✅ RAG回答生成 ({len(final_response['content'])}字)")
                                elif final_response.get("tool_calls"):
                                    print(f"    ⚠️ 模型在 RAG 阶段又调用了工具")
                                    tc_args = final_response["tool_calls"][0].get("arguments", "")
                                    if tc_args:
                                        final_response["content"] = f"[RAG阶段又调工具] {tc_args[:200]}"
                                    else:
                                        final_response["content"] = "[模型在 RAG 阶段再次调用工具]"
                                else:
                                    print(f"    ⚠️ RAG回答失败，使用原始响应 (error={final_response.get('error')})")
                                    final_response = response
                except Exception as e:
                    print(f"    ⚠ 解析 query 失败: {e}")
            
            # Stage 4: 评分
            dec_score, dec_detail = self.scorer.score_decision(q, response)
            qry_score, qry_detail = self.scorer.score_query(q, response, search_result)
            ans_score, ans_detail = self.scorer.score_answer(q, final_response, search_result)
            
            total = dec_score + qry_score + ans_score
            
            # 打印结果
            status = "✅" if total >= 60 else "⚠️" if total >= 40 else "❌"
            print(f"    {status} 得分: {total}/75 "
                  f"(决策:{dec_score}/20 Query:{qry_score}/25 答案:{ans_score}/30)")
            
            all_results.append({
                "question_id": qid,
                "question": qtext,
                "difficulty": difficulty,
                "should_call_tool": q.get("should_call_tool"),
                "response": {
                    "tool_calls": response["tool_calls"],
                    "reasoning": response["reasoning"][:200],
                    "content": final_response["content"][:500],
                    "latency_ms": response["latency_ms"]
                },
                "search": {
                    "query": search_result.get("query", ""),
                    "count": search_result.get("count", 0),
                    "latency_ms": search_result.get("latency_ms", 0)
                },
                "scores": {
                    "decision": dec_score,
                    "query": qry_score,
                    "answer": ans_score,
                    "total": total,
                    "details": {
                        "decision": dec_detail,
                        "query": qry_detail,
                        "answer": ans_detail
                    }
                }
            })
        
        # 汇总
        valid = [r for r in all_results if "error" not in r]
        if valid:
            avg_total = sum(r["scores"]["total"] for r in valid) / len(valid)
            avg_dec = sum(r["scores"]["decision"] for r in valid) / len(valid)
            avg_qry = sum(r["scores"]["query"] for r in valid) / len(valid)
            avg_ans = sum(r["scores"]["answer"] for r in valid) / len(valid)
            
            # 按难度分组
            difficulty_scores = {}
            for r in valid:
                d = r.get("difficulty", "未知")
                if d not in difficulty_scores:
                    difficulty_scores[d] = []
                difficulty_scores[d].append(r["scores"]["total"])
            
            print(f"\n  📊 {model_name} 汇总:")
            print(f"     决策: {avg_dec:.1f}/20")
            print(f"     Query: {avg_qry:.1f}/25")
            print(f"     答案: {avg_ans:.1f}/30")
            print(f"     总分: {avg_total:.1f}/75")
            print(f"     按难度: ", end="")
            for d, scores in sorted(difficulty_scores.items()):
                avg = sum(scores) / len(scores)
                print(f"{d}:{avg:.0f}  ", end="")
            print()
        
        return all_results
    
    def run(self):
        print("=" * 60)
        print("真实RAG能力评估框架 v2")
        print("=" * 60)
        print(f"服务器: {self.args.server}")
        print(f"超时: {self.args.timeout}s")
        print(f"搜索引擎: Bing (国内可用) + DuckDuckGo (备用)")
        
        if not self.load_questions():
            return
        
        models = self.manager.get_model_list()
        if not models:
            print("❌ 无法获取模型列表")
            return
        
        if self.args.models:
            indices = [int(x.strip()) for x in self.args.models.split(",")]
        else:
            indices = [m["index"] for m in models]
        
        test_models = [m for m in models if m["index"] in indices]
        print(f"\n将测试 {len(test_models)} 个模型")
        
        for m in test_models:
            idx = m["index"]
            name = m["name"]
            
            print(f"\n{'#'*60}")
            print(f"# [{idx}/{len(test_models)}] {name}")
            print(f"{'#'*60}")
            
            print(f"  🚀 启动模型 #{idx}...")
            model_name = self.manager.start(idx)
            
            if not model_name:
                print(f"  ❌ 启动失败")
                continue
            
            print(f"  ✅ 模型就绪: {model_name}")
            
            try:
                results = self.test_model(idx, model_name)
                
                # 保存结果
                model_result = {
                    "model_index": idx,
                    "model_name": model_name,
                    "timestamp": datetime.now().isoformat(),
                    "results": results
                }
                
                # 汇总
                valid = [r for r in results if "error" not in r]
                if valid:
                    model_result["average_scores"] = {
                        "decision": round(sum(r["scores"]["decision"] for r in valid) / len(valid), 1),
                        "query": round(sum(r["scores"]["query"] for r in valid) / len(valid), 1),
                        "answer": round(sum(r["scores"]["answer"] for r in valid) / len(valid), 1),
                        "total": round(sum(r["scores"]["total"] for r in valid) / len(valid), 1)
                    }
                
                result_file = Path(RESULTS_DIR) / f"{model_name}_real_eval.json"
                with open(result_file, "w", encoding="utf-8") as f:
                    json.dump(model_result, f, ensure_ascii=False, indent=2)
                
                self.results[model_name] = model_result
                
            except Exception as e:
                print(f"  ❌ 测试异常: {e}")
                traceback.print_exc()
            finally:
                print(f"\n  🔄 停止模型 {name}...")
                self.manager.stop()
                time.sleep(2)  # 额外等待显存稳定
        
        self._generate_report()
    
    def _generate_report(self):
        if not self.results:
            return
        
        print(f"\n{'='*60}")
        print("📊 真实RAG能力对比报告")
        print(f"{'='*60}")
        
        sorted_models = sorted(
            self.results.items(),
            key=lambda x: x[1].get("average_scores", {}).get("total", 0),
            reverse=True
        )
        
        print(f"\n{'#':<3} {'模型':<45} {'决策':<10} {'Query':<10} {'答案':<10} {'总分':<10}")
        print("-" * 85)
        
        for rank, (name, data) in enumerate(sorted_models, 1):
            scores = data.get("average_scores", {})
            print(f"{rank:<3} {name[:43]:<45} "
                  f"{scores.get('decision', 0):5.1f}/20   "
                  f"{scores.get('query', 0):5.1f}/25   "
                  f"{scores.get('answer', 0):5.1f}/30   "
                  f"{scores.get('total', 0):5.1f}")
        
        # 保存汇总
        summary = {
            "timestamp": datetime.now().isoformat(),
            "models": {
                name: data.get("average_scores", {})
                for name, data in self.results.items()
            }
        }
        
        summary_file = Path(RESULTS_DIR) / "real_eval_summary.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        
        print(f"\n📁 结果: {RESULTS_DIR}/")


def main():
    parser = argparse.ArgumentParser(description="真实RAG能力评估 v2")
    parser.add_argument("--models", type=str, default="", help="模型索引，如 '1,2,3'")
    parser.add_argument("--all", action="store_true", help="测试所有模型")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--apikey", default=DEFAULT_API_KEY)
    parser.add_argument("--launcher", default=None)
    parser.add_argument("--questions-file", default="questions_v2.json")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    
    args = parser.parse_args()
    
    if args.launcher is None:
        args.launcher = str(Path(__file__).parent / "launcher_main.ps1")
    
    # 如果指定了 --all，清空 models 参数（让 run() 方法使用所有模型）
    if args.all:
        args.models = ""
    
    Path(RESULTS_DIR).mkdir(exist_ok=True)
    
    evaluator = RealEvaluator(args)
    evaluator.run()


if __name__ == "__main__":
    main()
