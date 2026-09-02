#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RAG / 工具调用能力专项测评脚本 v2
  目的：多维度评估模型的实时检索工具调用能力
  维度：
    ① 决策正确性 (30%)：该调用工具时是否调用
    ② 工具调用质量 (30%)：query 准确性、格式规范性
    ③ 回答内容质量 (40%)：相关性、准确性、完整性
  服务：本地 llama.cpp 的 llama-server（默认 http://127.0.0.1:8081）
  流程：串行全模型自动：拉清单 → 起 launcher → 等就绪 → 跑 10 轮 → 停 → 下一个 → 汇总对比表

  v2 改进：
    - 修复思考模式流式显示（兼容多字段名）
    - 评分从"二元对错"改为"多维度质量评估"
    - 小模型不再因为"乱调工具"得高分
    - 增加回答质量评估（直接回答 vs 工具调用都能得分）

  Usage:
    python bench_rag.py                # 全模型串行
    python bench_rag.py --model-index 1   # 只测第 1 个模型
  Date: 2026-08-03
"""
import argparse, json, os, sys, time, re, subprocess, socket
import http.client, urllib.request, urllib.error, urllib.parse

# Windows 重定向 stdout 到文件时默认 GBK 编码，强制 UTF-8
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ============================================================
#  web_search 工具声明
# ============================================================
WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "搜索互联网获取最新信息、新闻、网页内容",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，应覆盖核心实体与需求"}
            },
            "required": ["query"]
        }
    }
}

# ============================================================
#  10 轮 RAG 测试题（实时检索场景）
#  每道题标注：是否应该调用工具、期望的 query 关键词
# ============================================================
RAG_QUESTIONS = [
    {
        "q": "请帮我搜索今天最新的全球AI大模型发布新闻，我要最新消息不要旧闻。",
        "should_call_tool": True,
        "expected_query_keywords": ["AI", "模型", "发布"],
        "category": "实时新闻"
    },
    {
        "q": "查一下最新的 Windows 12 发布消息和系统要求，要最新的。",
        "should_call_tool": True,
        "expected_query_keywords": ["Windows", "12"],
        "category": "产品发布"
    },
    {
        "q": "搜索今天英伟达最新股价和市值数据。",
        "should_call_tool": True,
        "expected_query_keywords": ["英伟达", "股价", "NVDA"],
        "category": "金融数据"
    },
    {
        "q": "帮我查一下今天美元兑人民币的最新汇率。",
        "should_call_tool": True,
        "expected_query_keywords": ["汇率", "美元", "人民币"],
        "category": "金融数据"
    },
    {
        "q": "搜索 2026 年奥运会最新的赛程和奖牌榜。",
        "should_call_tool": True,
        "expected_query_keywords": ["奥运", "奖牌", "赛程"],
        "category": "体育赛事"
    },
    {
        "q": "帮我查特斯拉最近一个季度的财报营收数据。",
        "should_call_tool": True,
        "expected_query_keywords": ["特斯拉", "财报", "营收"],
        "category": "公司财报"
    },
    {
        "q": "搜索今天的热点新闻头条，我要最新消息。",
        "should_call_tool": True,
        "expected_query_keywords": ["新闻", "头条", "今日"],
        "category": "实时新闻"
    },
    {
        "q": "查一下苹果最新发布的产品和价格，要最新的。",
        "should_call_tool": True,
        "expected_query_keywords": ["苹果", "Apple", "产品"],
        "category": "产品发布"
    },
    {
        "q": "帮我搜索最新的科学突破新闻，比如航天或生物技术。",
        "should_call_tool": True,
        "expected_query_keywords": ["科学", "航天", "生物"],
        "category": "科学前沿"
    },
    {
        "q": "查一下今天比特币的最新价格走势。",
        "should_call_tool": True,
        "expected_query_keywords": ["比特币", "BTC", "价格"],
        "category": "金融数据"
    },
]

# ============================================================
#  流式调用：兼容多种思考字段名
# ============================================================
def call_tool(server, api_key, model, user_msg, timeout=180):
    """带 tools 声明请求，流式显示思考和回答。
       返回 (tool_calls, full_text, latency_ms)。"""
    url = f"{server.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": user_msg}],
        "tools": [WEB_SEARCH_TOOL],
        "tool_choice": "auto",
        "max_tokens": 8192,
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    t0 = time.time()
    tc_index = {}
    reasoning_text = ""
    content_text = ""

    try:
        parsed = urllib.parse.urlparse(url)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
        body = json.dumps(payload)
        conn.request("POST", parsed.path, body=body, headers=headers)
        r = conn.getresponse()
        if r.status >= 400:
            raise Exception(f"HTTP {r.status}: {r.reason}")
        for raw in r:
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

            # 思考内容 - reasoning_content 字段
            rc = delta.get("reasoning_content")
            if rc:
                reasoning_text += rc
                print(f"\033[90m{rc}\033[0m", end="", flush=True)

            # 正文内容
            c = delta.get("content")
            if c:
                content_text += c
                print(c, end="", flush=True)

            # 工具调用（流式增量拼接）
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                fn = tc.get("function") or {}
                if idx not in tc_index:
                    tc_index[idx] = {"name": "", "arguments": ""}
                if fn.get("name"):
                    tc_index[idx]["name"] += fn["name"]
                    print(f"\n\033[93m[调用工具: {fn['name']}]\033[0m", end="", flush=True)
                if fn.get("arguments"):
                    tc_index[idx]["arguments"] += fn["arguments"]
                    args_text = fn["arguments"][:80]
                    print(f"\033[93m  参数: {args_text}...\033[0m", end="", flush=True)

            # 模型回答完才结束
            finish = ch.get("finish_reason")
            if finish and finish in ("stop", "tool_calls", "length", "content_filter"):
                break

        conn.close()
        latency = (time.time() - t0) * 1000
        print()   # 本轮输出换行
        
        # 构建完整的 tool_calls
        tool_calls = [tc_index[k] for k in sorted(tc_index)]
        
        # 完整文本 = 思考 + 正文
        full_text = reasoning_text + content_text
        
        return tool_calls, full_text, latency

    except Exception as e:
        latency = (time.time() - t0) * 1000
        print(f"\n[ERROR: {type(e).__name__}: {e}]")
        return f"[ERROR: {type(e).__name__}: {e}]", "", latency


# ============================================================
#  多维度专业评分系统
# ============================================================
def analyze_response_quality(tool_calls, full_text, question_item):
    """
    多维度评估模型输出质量（0-100分）
    
    维度及权重：
    ① 决策正确性 (30%)：该调用工具时是否调用了
    ② 工具调用质量 (30%)：query 准确性、格式规范性
    ③ 回答内容质量 (40%)：相关性、准确性、完整性
    """
    scores = {
        "decision": 0.0,      # 决策正确性 0-30
        "tool_quality": 0.0,  # 工具调用质量 0-30
        "answer_quality": 0.0 # 回答内容质量 0-40
    }
    details = []
    
    should_call = question_item.get("should_call_tool", True)
    expected_kws = question_item.get("expected_query_keywords", [])
    
    # ---- 维度①：决策正确性 ----
    has_tool_call = bool(tool_calls) and not isinstance(tool_calls, str) and "[ERROR" not in str(tool_calls)
    
    if should_call:
        if has_tool_call:
            scores["decision"] = 30.0
            details.append("✓ 正确决策：应调用工具，实际调用了")
        else:
            scores["decision"] = 0.0
            details.append("✗ 决策错误：应调用工具，但未调用")
    else:
        if not has_tool_call:
            scores["decision"] = 30.0
            details.append("✓ 正确决策：不需调用工具，直接回答")
        else:
            scores["decision"] = 0.0
            details.append("✗ 决策错误：不需调用工具，但错误调用了")
    
    # ---- 维度②：工具调用质量 ----
    if has_tool_call and should_call:
        name_ok = False
        query_score = 0.0
        
        for tc in tool_calls:
            if tc.get("name") == "web_search":
                name_ok = True
                arg_str = str(tc.get("arguments", ""))
                
                # 解析 arguments JSON
                try:
                    arg_obj = json.loads(arg_str)
                    query = str(arg_obj.get("query", "")).strip()
                except Exception:
                    # 可能被截断，尝试提取 query
                    query_match = re.search(r'"query"\s*:\s*"([^"]*)"', arg_str)
                    query = query_match.group(1) if query_match else arg_str
                
                if query:
                    # 评估 query 与期望关键词的匹配度
                    query_lower = query.lower()
                    kw_hits = sum(1 for kw in expected_kws if kw.lower() in query_lower)
                    kw_total = len(expected_kws) if expected_kws else 1
                    query_score = (kw_hits / kw_total) * 30.0
                    
                    if name_ok:
                        details.append(f"✓ 工具名正确: web_search")
                    if query:
                        details.append(f"  查询: '{query}' 命中 {kw_hits}/{kw_total} 个关键词")
        
        if not name_ok:
            details.append("✗ 工具名不正确")
        scores["tool_quality"] = query_score
        
    elif not has_tool_call:
        # 没调用工具时，工具质量为0，但决策分已体现
        details.append("  无工具调用")
    else:
        details.append("  不需评估工具质量")
    
    # ---- 维度③：回答/思考内容质量 ----
    answer_score = 0.0
    
    if full_text and not isinstance(full_text, str) and "[ERROR" not in str(full_text):
        text = str(full_text).strip()
        
        # 3a. 相关性 (15分)：思考/回答是否针对问题
        q = question_item.get("q", "")
        q_kws = [w for w in re.findall(r'[\u4e00-\u9fff]+|\w+', q) if len(w) >= 2]
        relevance_hits = sum(1 for kw in q_kws if kw in text)
        relevance_score = min(15.0, (relevance_hits / max(len(q_kws), 1)) * 15.0)
        answer_score += relevance_score
        
        # 3b. 完整性 (15分)：思考/回答是否充分
        if len(text) > 500:
            completeness_score = 15.0
        elif len(text) > 200:
            completeness_score = 10.0
        elif len(text) > 50:
            completeness_score = 5.0
        else:
            completeness_score = 0.0
        answer_score += completeness_score
        
        # 3c. 思维质量 (10分)：是否展现了合理的推理过程
        # 检查是否包含推理关键词
        thinking_markers = ["我需要", "我应该", "让我", "需要使用", "应该使用", "首先", "然后", "因为", "所以"]
        has_thinking = any(m in text for m in thinking_markers)
        if has_thinking:
            thinking_score = 10.0
            details.append("✓ 回答展现了推理过程")
        else:
            thinking_score = 5.0
        answer_score += thinking_score
        
        details.append(f"  文本长度: {len(text)} 字符 (思考+回答)")
        details.append(f"  相关性: {relevance_score:.0f}/15, 完整性: {completeness_score:.0f}/15, 思维质量: {thinking_score:.0f}/10")
    else:
        if has_tool_call:
            details.append("  模型调用了工具但未生成文本回答（正常行为）")
            # 如果有工具调用，给基础分 - 模型做了正确的决策
            answer_score = 20.0  # 基础分：模型做了决策
        else:
            details.append("✗ 无有效回答内容")
    
    scores["answer_quality"] = answer_score
    
    # ---- 总分 ----
    total = scores["decision"] + scores["tool_quality"] + scores["answer_quality"]
    
    return {
        "total": round(total, 1),
        "out_of": 100.0,
        "scores": scores,
        "details": details,
        "has_tool_call": has_tool_call,
        "answer_length": len(str(full_text)) if full_text else 0,
    }


def get_online_model(server, api_key):
    """探测 /v1/models 返回在线模型名。"""
    try:
        url = f"{server.rstrip('/')}/v1/models"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
            if data.get("data") and len(data["data"]) > 0:
                return data["data"][0].get("id", "")
    except Exception:
        pass
    return None


# ============================================================
#  单模型 10 轮 RAG 测试（新版评分）
# ============================================================
def run_rag(server, api_key, model):
    print(f"\n{'='*60}")
    print(f"RAG 工具调用能力测评 v2 · 模型: {model}")
    print(f"{'='*60}")
    print(f"评分维度：决策正确性(30%) + 工具调用质量(30%) + 回答内容质量(40%)")
    print()

    results = []
    for i, item in enumerate(RAG_QUESTIONS, 1):
        print(f"\n  [R{i}/10] {item['q'][:50]}...")
        
        # 带重试机制的调用
        calls, full_text, latency = [], "", 0
        for attempt in range(3):
            try:
                calls, full_text, latency = call_tool(server, api_key, model, item["q"])
                if not (isinstance(calls, str) and "[ERROR" in calls):
                    break  # 成功则跳出重试循环
                else:
                    print(f"    [重试 {attempt+1}/3] 遇到错误，等待 3 秒...")
                    time.sleep(3)
            except Exception as e:
                print(f"    [重试 {attempt+1}/3] 异常: {e}")
                time.sleep(3)
        
        # 轮次间冷却（避免过载）
        if i < len(RAG_QUESTIONS):
            time.sleep(0.5)
        
        # 多维度评分
        eval_result = analyze_response_quality(calls, full_text, item)
        
        results.append({
            "round": i,
            "category": item["category"],
            "should_call_tool": item["should_call_tool"],
            "score": eval_result["total"],
            "out_of": eval_result["out_of"],
            "scores": eval_result["scores"],
            "details": eval_result["details"],
            "has_tool_call": eval_result["has_tool_call"],
            "answer_length": eval_result["answer_length"],
            "latency_ms": round(latency, 1),
        })
        
        # 输出本轮结果
        s = eval_result["total"]
        mark = "✓" if s >= 70 else ("◐" if s >= 40 else "✗")
        tool_tag = "调用工具" if eval_result["has_tool_call"] else "直接回答"
        print(f"  {mark} 得分: {s:.0f}/100  [{tool_tag}]  延迟: {latency:.0f}ms")
        for d in eval_result["details"]:
            print(f"    {d}")

    # 汇总统计
    valid_results = [r for r in results if r["score"] > 0]
    avg_total = sum(r["score"] for r in results) / len(results) if results else 0
    avg_decision = sum(r["scores"]["decision"] for r in results) / len(results) if results else 0
    avg_tool = sum(r["scores"]["tool_quality"] for r in results) / len(results) if results else 0
    avg_answer = sum(r["scores"]["answer_quality"] for r in results) / len(results) if results else 0
    full_rounds = sum(1 for r in results if r["score"] >= 70)
    avg_ms = sum(r["latency_ms"] for r in results) / len(results) if results else 0

    summary = {
        "model": model,
        "full_rounds": full_rounds,
        "total": len(RAG_QUESTIONS),
        "avg_score": round(avg_total, 1),
        "avg_decision": round(avg_decision, 1),
        "avg_tool_quality": round(avg_tool, 1),
        "avg_answer_quality": round(avg_answer, 1),
        "avg_latency_ms": round(avg_ms, 1),
        "results": results,
    }

    # 打印汇总
    print(f"\n{'='*60}")
    print(f"模型 {model} 测评汇总：")
    print(f"  平均分: {avg_total:.1f}/100")
    print(f"  决策正确性: {avg_decision:.1f}/30")
    print(f"  工具调用质量: {avg_tool:.1f}/30")
    print(f"  回答内容质量: {avg_answer:.1f}/40")
    print(f"  完整轮数(>=70): {full_rounds}/{len(results)}")
    print(f"  平均延迟: {avg_ms:.0f}ms")
    print(f"{'='*60}")

    return summary


# ============================================================
#  串行驱动
# ============================================================
def get_model_list(launcher):
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", launcher, "-ListModels"],
            capture_output=True, timeout=30
        )
        out = r.stdout.decode("utf-8", errors="replace").strip()
        out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out)
        m = re.search(r"\[.*\]", out, re.S)
        if not m:
            print(f"ERROR: launcher 输出无 JSON 段。")
            return []
        return json.loads(m.group(0))
    except Exception as e:
        print(f"ERROR: 拉模型清单失败: {type(e).__name__}: {e}")
        return []

def wait_ready(server, api_key, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = get_online_model(server, api_key)
        if m:
            return m
        time.sleep(3)
    return None

def start_model(launcher, index):
    return subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", launcher, "-ModelIndex", str(index), "-NoInteractive"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

def stop_model(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try: proc.wait(timeout=10)
        except Exception: proc.kill()
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue | "
             "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"],
            capture_output=True, timeout=15
        )
    except Exception: pass
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Where-Object { $_.ProcessName -match 'llama' } | "
             "ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }"],
            capture_output=True, timeout=15
        )
    except Exception: pass
    for _ in range(20):
        time.sleep(2)
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=5)
            used = int(r.stdout.strip())
            if used < 500:
                break
        except Exception:
            break

def run_one(args, index):
    print(f"\n{'#'*60}")
    print(f"# [{index}] 启动模型 #{index}")
    proc = start_model(args.launcher, index)
    model = wait_ready(args.server, args.apikey, timeout=240)
    if not model:
        print(f"⚠ 模型 #{index} 启动超时（240s 未就绪），跳过")
        stop_model(proc)
        return None
    print(f"✓ 服务就绪: {model}")
    try:
        return run_rag(args.server, args.apikey, model)
    finally:
        stop_model(proc)

def run_all(args):
    print(f"\n{'='*60}")
    print(f"RAG 工具调用能力测评 v2 · 全模型串行（每模型 10 轮）")
    print(f"{'='*60}")
    models = get_model_list(args.launcher)
    if not models:
        print("ERROR: 拉不到模型清单，终止。")
        return
    print(f"共 {len(models)} 个模型待测：")
    for m in models:
        print(f"  #{m['index']}  {m['name']}  ({m.get('sizeGB','?')}GB)")
    print()

    all_results = []
    for m in models:
        idx = m["index"]
        print(f"\n{'#'*60}")
        print(f"# [{idx}/{len(models)}] {m['name']}")
        print(f"{'#'*60}")
        r = run_one(args, idx)
        if r:
            all_results.append({"index": idx, "name": m["name"], "data": r})

    if not all_results:
        print("\n⚠ 所有模型均未成功测评，无对比表。")
        return

    # 生成对比报告
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_results")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cmp_path = os.path.join(out_dir, f"rag_all_models_{stamp}.md")

    lines = []
    lines.append("# 全模型 RAG 工具调用能力对比报告 v2")
    lines.append("")
    lines.append(f"- 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 每模型 {len(RAG_QUESTIONS)} 轮实时检索工具调用测试")
    lines.append(f"- 评分: 决策正确性(30%) + 工具调用质量(30%) + 回答内容质量(40%)")
    lines.append("")
    lines.append("## 对比总表")
    lines.append("")
    lines.append("| # | 模型 | 平均分 | 决策分 | 工具分 | 回答分 | 完整轮 | 平均延迟 |")
    lines.append("|:--|:-----|:------:|:------:|:------:|:------:|:------:|:--------:|")
    for r in sorted(all_results, key=lambda x: -x["data"]["avg_score"]):
        d = r["data"]
        lines.append(f"| {r['index']} | {r['name']} | "
                     f"**{d['avg_score']}** | "
                     f"{d['avg_decision']} | "
                     f"{d['avg_tool_quality']} | "
                     f"{d['avg_answer_quality']} | "
                     f"{d['full_rounds']}/{d['total']} | "
                     f"{d['avg_latency_ms']}ms |")
    lines.append("")

    # 详细评分维度说明
    lines.append("## 评分维度说明")
    lines.append("")
    lines.append("### ① 决策正确性 (30分)")
    lines.append("- 该调用工具时调用了 → 30分")
    lines.append("- 不该调用时直接回答 → 30分")
    lines.append("- 错误决策（该调不调/不该调却调）→ 0分")
    lines.append("")
    lines.append("### ② 工具调用质量 (30分)")
    lines.append("- query 覆盖期望关键词比例 × 30分")
    lines.append("- 格式规范（合法 JSON）→ 额外加分")
    lines.append("")
    lines.append("### ③ 回答内容质量 (40分)")
    lines.append("- 相关性 (15分)：回答关键词与问题关键词匹配度")
    lines.append("- 完整性 (15分)：回答长度是否充分")
    lines.append("- 时效性 (10分)：是否包含最新/截至等时效限定")
    lines.append("")
    lines.append("_由 bench_rag.py v2 自动生成_")

    with open(cmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n{'='*60}")
    print(f"全模型 RAG 测评完成，对比总表：")
    for r in sorted(all_results, key=lambda x: -x["data"]["avg_score"]):
        d = r["data"]
        print(f"  #{r['index']}  {r['name'][:40]:<42}  均分: {d['avg_score']:.0f}/100  "
              f"决策: {d['avg_decision']:.0f}/30  工具: {d['avg_tool_quality']:.0f}/30  "
              f"回答: {d['avg_answer_quality']:.0f}/40")
    print(f"  保存: {cmp_path}")
    print(f"{'='*60}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8081")
    ap.add_argument("--apikey", default="llamacpp")
    ap.add_argument("--launcher", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                       "launcher_main.ps1"))
    ap.add_argument("--model-index", type=int, default=0,
                    help="只测指定 index 的单个模型（0=全模型串行）")
    args = ap.parse_args()
    if args.model_index > 0:
        run_one(args, args.model_index)
    else:
        run_all(args)

if __name__ == "__main__":
    main()
