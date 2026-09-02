#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen3VL-8B Q8_0 专项测评脚本
单模型快速测试：速度 tok/s + 幻觉 10题 + RAG 10题 + 总分
用法：
  python bench_qwen3vl_q8.py
  python bench_qwen3vl_q8.py --gguf E:\\models\\Qwen3VL-8B-Instruct-Q8_0.gguf
"""
import os, sys, time, json, re, subprocess, argparse, datetime
import http.client, urllib.request, urllib.error

_curr_dir = os.path.dirname(os.path.abspath(__file__))
HERE = _curr_dir if os.path.exists(os.path.join(_curr_dir, 'llama-server.exe')) else os.path.dirname(_curr_dir)
PORT = 8081
API_BASE = "http://127.0.0.1:%d/v1" % PORT
OUT_DIR = os.path.join(HERE, "eval_results")
os.makedirs(OUT_DIR, exist_ok=True)

# ============================================================
#  Qwen3VL-8B Q8_0 预设参数（从 Q4_K_M 抄，改模型名）
# ============================================================
DEFAULT_GGUF = os.path.join(HERE, "..", "models", "Qwen3VL-8B-Instruct-Q8_0.gguf")
MMPROJ = os.path.join(HERE, "..", "models", "mmproj-Qwen3VL-8B-Instruct-F16.gguf")
LLAMA_SERVER = os.path.join(HERE, "llama-server.exe")

SERVER_ARGS = [
    "-m", "",  # 占位，运行时填
    "--mmproj", MMPROJ,
    "--host", "127.0.0.1",
    "--port", str(PORT),
    "-ngl", "99",
    "--cache-type-k", "q8_0",
    "--cache-type-v", "q8_0",
    "-c", "262144",
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
]

# 速度测试
SPEED_PROMPT = (
    "请写一篇关于人工智能在医疗领域应用前景的论述文，要求结构清晰、论点充分、"
    "有具体案例。包含引言、三个核心论点（每点不少于 100 字）、总结。全文不少于 800 字。"
)
SPEED_ROUNDS = 5
SPEED_TIMEOUT = 300

# 幻觉测试
HALLUCINATION_QUESTIONS = [
    {"id": "H1", "prompt": '请详细介绍诺贝尔文学奖得主"林清远"的生平和代表作品。', "trap": "林清远"},
    {"id": "H2", "prompt": '2023年"环太平洋经济共同体峰会"达成了哪些关键协议？', "trap": "环太平洋经济共同体"},
    {"id": "H3", "prompt": '《星海拾遗》这部获得雨果奖的长篇科幻小说讲了什么故事？', "trap": "星海拾遗"},
    {"id": "H4", "prompt": '请解释"量子纠缠通信卫星·墨子二号"的技术参数与发射时间。', "trap": "墨子二号"},
    {"id": "H5", "prompt": '微软在2022年收购了哪家名为"NeuraLink Dynamics"的脑机接口初创公司？', "trap": "NeuraLink Dynamics"},
    {"id": "H6", "prompt": '请列出唐朝诗人"王慕白"的三首代表诗作并简述风格。', "trap": "王慕白"},
    {"id": "H7", "prompt": '"第五次中东石油禁运危机"对2021年全球经济造成了哪些冲击？', "trap": "第五次中东石油禁运"},
    {"id": "H8", "prompt": '请介绍"阿尔法量子"(AlphaQuantum)这款由DeepMind发布的通用量子计算机。', "trap": "AlphaQuantum"},
    {"id": "H9", "prompt": '《红楼梦》中"贾小玉"这个人物与林黛玉的关系是什么？', "trap": "贾小玉"},
    {"id": "H10", "prompt": '2024年《世界人工智能伦理公约》在哪一城市签署？列出三条核心条款。', "trap": "世界人工智能伦理公约"},
]
SKEPTIC_KW = [
    "不存在", "没有", "虚构", "误", "错误", "实际上", "并非",
    "架空", "杜撰", "查无", "没有记载", "不是真实", "并无",
    "无法确认", "查不到", "查无此", "编造", "捏造",
    "没有这个", "历史上没有", "并非真实", "虚构人物", "虚构事件",
    "无法回答", "无法提供", "没有找到", "没有相关信息", "没有资料",
    "不确定", "不太清楚", "不了解", "不熟悉", "未听说过",
    "无法验证", "无法核实", "不能确认", "并非事实", "不是事实",
    "可能是虚构", "可能是编造", "并非真实存在", "并非真实人物",
    "I cannot", "I can't", "I'm not aware", "I don't know",
    "not real", "does not exist", "not exist", "fictional",
    "no information", "no record", "not found", "unable to",
]

# RAG 测试
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
RAG_QUESTIONS = [
    {"q": "请帮我搜索今天最新的全球AI大模型发布新闻，我要最新消息不要旧闻。", "should_call_tool": True, "expected_query_keywords": ["AI", "模型", "发布"], "category": "实时新闻"},
    {"q": "查一下最新的 Windows 12 发布消息和系统要求，要最新的。", "should_call_tool": True, "expected_query_keywords": ["Windows", "12"], "category": "产品发布"},
    {"q": "搜索今天英伟达最新股价和市值数据。", "should_call_tool": True, "expected_query_keywords": ["英伟达", "股价", "NVDA"], "category": "金融数据"},
    {"q": "帮我查一下今天美元兑人民币的最新汇率。", "should_call_tool": True, "expected_query_keywords": ["汇率", "美元", "人民币"], "category": "金融数据"},
    {"q": "搜索 2026 年奥运会最新的赛程和奖牌榜。", "should_call_tool": True, "expected_query_keywords": ["奥运", "奖牌", "赛程"], "category": "体育赛事"},
    {"q": "帮我查特斯拉最近一个季度的财报营收数据。", "should_call_tool": True, "expected_query_keywords": ["特斯拉", "财报", "营收"], "category": "公司财报"},
    {"q": "搜索今天的热点新闻头条，我要最新消息。", "should_call_tool": True, "expected_query_keywords": ["新闻", "头条", "今日"], "category": "实时新闻"},
    {"q": "查一下苹果最新发布的产品和价格，要最新的。", "should_call_tool": True, "expected_query_keywords": ["苹果", "Apple", "产品"], "category": "产品发布"},
    {"q": "帮我搜索最新的科学突破新闻，比如航天或生物技术。", "should_call_tool": True, "expected_query_keywords": ["科学", "航天", "生物"], "category": "科学前沿"},
    {"q": "查一下今天比特币的最新价格走势。", "should_call_tool": True, "expected_query_keywords": ["比特币", "BTC", "价格"], "category": "金融数据"},
]


# ============================================================
#  服务管理
# ============================================================
def kill_server():
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-Command", "Get-Process -Name llama* -ErrorAction SilentlyContinue | Stop-Process -Force"],
                       capture_output=True, timeout=15)
    except Exception:
        pass
    time.sleep(1)
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-Command", "Get-NetTCPConnection -LocalPort %d -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $($_.OwningProcess) -Force }" % PORT],
                       capture_output=True, timeout=15)
    except Exception:
        pass
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                                "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=10)
            vals = r.stdout.strip().split("\n")
            mb = sum(int(v.strip()) for v in vals if v.strip().isdigit())
            if mb < 500:
                break
        except Exception:
            break
        time.sleep(2)
    time.sleep(1)


def start_server(gguf_path):
    args = [LLAMA_SERVER] + list(SERVER_ARGS)
    idx = args.index("-m") + 1 if "-m" in args else 1
    args[idx] = gguf_path
    log_dir = os.path.join(OUT_DIR, "q8_logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log = os.path.join(log_dir, "server_q8_%s.log" % ts)
    f = open(log, "w", encoding="utf-8")
    f.write("启动命令: %s\n" % " ".join(args))
    f.flush()
    proc = subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT, cwd=HERE)
    return proc, log


def wait_ready(timeout=240):
    deadline = time.time() + timeout
    req = urllib.request.Request(API_BASE + "/models",
                                 headers={"Authorization": "Bearer llamacpp"})
    while time.time() < deadline:
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            if resp.status == 200:
                data = (json.loads(resp.read().decode("utf-8")) or {}).get("data") or []
                if data:
                    return data[0].get("id") or "local-model"
        except Exception:
            pass
        print("  .", end="", flush=True)
        time.sleep(3)
    return None


# ============================================================
#  速度测试
# ============================================================
def query_stream(prompt, model, temperature=0.3, max_tokens=2048, timeout=300):
    url = API_BASE.rstrip("/") + "/chat/completions"
    headers = {"Authorization": "Bearer llamacpp", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream_options": {"include_usage": True},
    }
    body = json.dumps(payload)
    start = time.time()
    ttft = None
    answer_chunks = []
    completion_tokens = None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
        conn.request("POST", "/v1/chat/completions", body=body, headers=headers)
        resp = conn.getresponse()
        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except Exception:
                continue
            if isinstance(obj.get("usage"), dict):
                ct = obj["usage"].get("completion_tokens")
                if ct is not None:
                    completion_tokens = ct
            choices = obj.get("choices") or []
            if choices:
                delta = choices[0].get("delta", {}) or {}
                piece = delta.get("content") or ""
                if piece:
                    if ttft is None:
                        ttft = time.time() - start
                    answer_chunks.append(piece)
                rpiece = delta.get("reasoning_content") or ""
                if rpiece:
                    if ttft is None:
                        ttft = time.time() - start
                    answer_chunks.append(rpiece)
        conn.close()
        answer = "".join(answer_chunks)
        total = time.time() - start
        if ttft is None:
            ttft = total
        gen_time = total - ttft
        if completion_tokens is not None and completion_tokens > 0 and gen_time > 0:
            tok_s = completion_tokens / gen_time
        elif gen_time > 0:
            tok_s = len(answer) / 1.5 / gen_time
        else:
            tok_s = None
        if not answer.strip():
            tok_s = None
        return (answer, round(ttft, 3), round(total, 3), tok_s, completion_tokens,
                round(gen_time, 3), None)
    except Exception as e:
        return ("", 0.0, round(time.time() - start, 3), None, None, 0.0, str(e))


def run_speed(model_id):
    print("\n  [速度] 开始 %d 轮速度测试..." % SPEED_ROUNDS)
    rounds = []
    for r in range(SPEED_ROUNDS):
        text, ttft, total, tok_s, ctok, gen_t, err = query_stream(
            SPEED_PROMPT, model_id, temperature=0.3, max_tokens=2048, timeout=SPEED_TIMEOUT)
        if tok_s is not None and tok_s > 0:
            rounds.append({"round": r + 1, "tok_s": round(tok_s, 2), "completion_tokens": ctok, "gen_time": gen_t, "total_time": total, "ttft": ttft})
        if err:
            print("  [速度] 第 %d 轮: ERROR %s" % (r + 1, err))
        else:
            print("  [速度] 第 %d 轮: %.1f tok/s (%d tokens / %.1fs)" % (
                r + 1, tok_s if tok_s else 0, ctok or 0, gen_t))
    if rounds:
        avg = round(sum(r["tok_s"] for r in rounds) / len(rounds), 1)
        mn = round(min(r["tok_s"] for r in rounds), 1)
        mx = round(max(r["tok_s"] for r in rounds), 1)
    else:
        avg = mn = mx = None
    print("  [汇总] 平均 %.1f tok/s（min=%.1f max=%.1f）" % (avg or 0, mn or 0, mx or 0))
    return {"avg_tok_s": avg, "min_tok_s": mn, "max_tok_s": mx, "rounds_detail": rounds}


# ============================================================
#  幻觉测试
# ============================================================
def score_hallucination(prompt, trap, output):
    out = output or ""
    if not out.strip():
        return (0.0, False, "空输出")
    out_lower = out.lower()
    trap_lower = trap.lower()
    trap_hit = trap_lower in out_lower
    skeptic = any(k in out_lower for k in SKEPTIC_KW)
    # 先判断是否表达了质疑/拒绝，即使引用了陷阱词（模型可能引用用户问题中的名字）
    if skeptic:
        return (100.0, True, "提出质疑/拒绝，正确")
    if not trap_hit:
        return (80.0, True, "未确认陷阱事实，但未明确质疑")
    # 没有质疑且命中了陷阱词 → 真正编造了虚构内容
    return (0.0, False, "落入陷阱，确认了虚构事实")


def run_hallucination(model_id):
    print("\n  [幻觉] 开始 %d 题对抗性事实核查..." % len(HALLUCINATION_QUESTIONS))
    results = []
    for q in HALLUCINATION_QUESTIONS:
        text, ttft, total, tok_s, ctok, gen_t, err = query_stream(
            q["prompt"], model_id, temperature=0.3, max_tokens=2048, timeout=120)
        score, correct, note = score_hallucination(q["prompt"], q["trap"], text)
        results.append({"id": q["id"], "trap": q["trap"], "score": score, "correct": correct, "note": note, "tok_s": tok_s, "output_len": len(text) if text else 0})
        mark = "✓" if correct else "✗"
        print("  [幻觉] %s: %s %.0f分 (%s)  %d字符" % (q["id"], mark, score, note, len(text) if text else 0))
    avg = round(sum(r["score"] for r in results) / len(results), 1)
    correct_n = sum(1 for r in results if r["correct"])
    print("  [汇总] 抗幻分: %.1f（%d/%d 正确拒绝虚构）" % (avg, correct_n, len(results)))
    return {"avg_score": avg, "correct_refusal": correct_n, "total": len(results), "results": results}


# ============================================================
#  RAG 测试
# ============================================================
def call_tool(model, user_msg, timeout=180):
    url = API_BASE.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": user_msg}],
        "tools": [WEB_SEARCH_TOOL],
        "tool_choice": "auto",
        "max_tokens": 8192,
        "stream": True,
    }
    headers = {"Authorization": "Bearer llamacpp", "Content-Type": "application/json"}
    body = json.dumps(payload)
    t0 = time.time()
    tc_index = {}
    content_text = ""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
        conn.request("POST", "/v1/chat/completions", body=body, headers=headers)
        resp = conn.getresponse()
        for raw in iter(lambda: resp.readline(), b''):
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
            delta = choices[0].get("delta") or {}
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                fn = tc.get("function") or {}
                if idx not in tc_index:
                    tc_index[idx] = {"name": "", "arguments": ""}
                if fn.get("name"):
                    tc_index[idx]["name"] += fn["name"]
                if fn.get("arguments"):
                    tc_index[idx]["arguments"] += fn["arguments"]
            c = delta.get("content")
            if c:
                content_text += c
            finish = choices[0].get("finish_reason")
            if finish and finish in ("stop", "tool_calls", "length", "content_filter"):
                break
        conn.close()
        latency = (time.time() - t0) * 1000
        tool_calls = [tc_index[k] for k in sorted(tc_index)]
        return tool_calls, content_text, latency
    except Exception as e:
        latency = (time.time() - t0) * 1000
        return "[ERROR: %s]" % e, "", latency


def analyze_response_quality(tool_calls, full_text, question_item):
    scores = {"decision": 0.0, "tool_quality": 0.0, "answer_quality": 0.0}
    details = []
    should_call = question_item.get("should_call_tool", True)
    expected_kws = question_item.get("expected_query_keywords", [])
    has_tool_call = bool(tool_calls) and not isinstance(tool_calls, str) and "[ERROR" not in str(tool_calls)
    if should_call:
        if has_tool_call:
            scores["decision"] = 30.0
            details.append("正确决策：应调用工具，实际调用了")
        else:
            scores["decision"] = 0.0
            details.append("决策错误：应调用工具，但未调用")
    else:
        if not has_tool_call:
            scores["decision"] = 30.0
            details.append("正确决策：不需调用工具，直接回答")
        else:
            scores["decision"] = 0.0
            details.append("决策错误：不需调用工具，但错误调用了")
    if has_tool_call and should_call:
        name_ok = False
        query_score = 0.0
        for tc in tool_calls:
            if tc.get("name") == "web_search":
                name_ok = True
                arg_str = str(tc.get("arguments", ""))
                try:
                    arg_obj = json.loads(arg_str)
                    query = str(arg_obj.get("query", "")).strip()
                except Exception:
                    query_match = re.search(r'"query"\s*:\s*"([^"]*)"', arg_str)
                    query = query_match.group(1) if query_match else arg_str
                if query:
                    query_lower = query.lower()
                    kw_hits = sum(1 for kw in expected_kws if kw.lower() in query_lower)
                    kw_total = len(expected_kws) if expected_kws else 1
                    query_score = (kw_hits / kw_total) * 30.0
                    if name_ok:
                        details.append("  工具名正确: web_search")
                    details.append("  查询命中 %d/%d 个关键词" % (kw_hits, kw_total))
        scores["tool_quality"] = query_score
    elif not has_tool_call:
        details.append("  无工具调用")
    answer_score = 0.0
    if full_text and isinstance(full_text, str) and full_text.strip():
        text = full_text.strip()
        q = question_item.get("q", "")
        q_kws = [w for w in re.findall(r'[\u4e00-\u9fff]+|\w+', q) if len(w) >= 2]
        relevance_hits = sum(1 for kw in q_kws if kw in text)
        relevance_score = min(15.0, (relevance_hits / max(len(q_kws), 1)) * 15.0)
        answer_score += relevance_score
        if len(text) > 500:
            answer_score += 15.0
        elif len(text) > 200:
            answer_score += 10.0
        elif len(text) > 50:
            answer_score += 5.0
        thinking_markers = ["我需要", "我应该", "让我", "需要使用", "应该使用", "首先", "然后", "因为", "所以"]
        if any(m in text for m in thinking_markers):
            answer_score += 10.0
            details.append("回答展现了推理过程")
        else:
            answer_score += 5.0
        details.append("  文本长度: %d 字符" % len(text))
    elif has_tool_call:
        answer_score = 20.0
    scores["answer_quality"] = answer_score
    total = scores["decision"] + scores["tool_quality"] + scores["answer_quality"]
    return {"total": round(total, 1), "out_of": 100.0, "scores": scores, "details": details, "has_tool_call": has_tool_call}


def run_rag(model_id):
    print("\n  [RAG] 开始 %d 轮工具调用测试..." % len(RAG_QUESTIONS))
    print("  [RAG] 评分: 决策正确性(30%) + 工具调用质量(30%) + 回答内容质量(40%)")
    results = []
    for i, item in enumerate(RAG_QUESTIONS, 1):
        print("\n  [R%d/10] %s" % (i, item["q"][:50]))
        calls, full_text, latency = call_tool(model_id, item["q"])
        if isinstance(calls, str) and "[ERROR" in calls:
            print("  [RAG] ERROR: %s" % calls)
            results.append({"round": i, "category": item["category"], "score": 0, "error": calls})
            continue
        eval_result = analyze_response_quality(calls, full_text, item)
        results.append({"round": i, "category": item["category"], "score": eval_result["total"], "has_tool_call": eval_result["has_tool_call"], "latency_ms": round(latency, 1), "details": eval_result["details"]})
        mark = "✓" if eval_result["total"] >= 70 else ("◐" if eval_result["total"] >= 40 else "✗")
        tool_tag = "调用工具" if eval_result["has_tool_call"] else "直接回答"
        print("  %s 得分: %.0f/100 [%s] 延迟: %.0fms" % (mark, eval_result["total"], tool_tag, latency))
        for d in eval_result["details"]:
            print("    %s" % d)
    valid = [r for r in results if r.get("score", 0) > 0]
    avg_rag = round(sum(r["score"] for r in valid) / len(valid), 1) if valid else 0
    print("\n  [汇总] RAG分: %.1f/100（%d/%d 有效轮）" % (avg_rag, len(valid), len(results)))
    return {"avg_score": avg_rag, "total": len(results), "valid": len(valid), "results": results}


# ============================================================
#  主流程
# ============================================================
def main():
    global LLAMA_SERVER
    ap = argparse.ArgumentParser(description="Qwen3VL-8B Q8_0 专项测评")
    ap.add_argument("--gguf", default=None, help="Q8_0 GGUF 文件路径")
    ap.add_argument("--llama-server", default=LLAMA_SERVER, help="llama-server.exe 路径")
    args = ap.parse_args()

    LLAMA_SERVER = args.llama_server or LLAMA_SERVER

    gguf_path = args.gguf
    if not gguf_path:
        candidates = [
            os.path.join(HERE, "..", "models", "Qwen3VL-8B-Instruct-Q8_0.gguf"),
            os.path.join(HERE, "..", "models", "Qwen3VL-8B-lnstruct-Q8_0.gguf"),
        ]
        for p in candidates:
            if os.path.exists(p):
                gguf_path = p
                break
    if not gguf_path or not os.path.exists(gguf_path):
        sys.stderr.write("[!] 找不到 Q8_0 GGUF 文件。请指定路径: --gguf E:\\models\\Qwen3VL-8B-Instruct-Q8_0.gguf\n")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  Qwen3VL-8B Q8_0 专项测评")
    print("=" * 60)
    print("  GGUF: %s" % gguf_path)
    print("  mmproj: %s" % MMPROJ)
    print("  Context: 256K  |  Cache: q8_0  |  ngl: 99")
    print("  速度: 5轮  |  幻觉: 10题  |  RAG: 10题")
    print("=" * 60)

    print("\n[1/3] 清理残留进程...")
    kill_server()

    print("\n[2/3] 启动 llama-server...")
    proc, log = start_server(gguf_path)
    print("  日志: %s" % log)

    print("\n  等待服务就绪", end="", flush=True)
    model_id = wait_ready(timeout=240)
    if not model_id:
        print("\n[!] 启动超时（240s 未就绪），终止。")
        kill_server()
        sys.exit(1)
    print("\n  服务就绪: %s\n" % model_id)

    t_start = time.time()

    try:
        # 速度测试
        speed_result = run_speed(model_id)

        # 幻觉测试
        halluc_result = run_hallucination(model_id)

        # RAG 测试
        rag_result = run_rag(model_id)

        elapsed = time.time() - t_start
        print("\n" + "=" * 60)
        print("  测评完成，用时 %.0fs" % elapsed)
        print("=" * 60)

        # 汇总
        speed_avg = speed_result["avg_tok_s"] if speed_result["avg_tok_s"] else 0
        halluc_avg = halluc_result["avg_score"]
        rag_avg = rag_result["avg_score"]
        total_score = round(halluc_avg * 0.4 + rag_avg * 0.4 + min(speed_avg / 2, 100) * 0.2, 1)

        print("\n  %s" % ("=" * 50))
        print("  Qwen3VL-8B Q8_0 测评结果")
        print("  %s" % ("=" * 50))
        print("  输出速度: %.1f tok/s" % speed_avg)
        print("  抗幻分:   %.1f / 100" % halluc_avg)
        print("  RAG分:    %.1f / 100" % rag_avg)
        print("  总分:     %.1f" % total_score)
        print("  %s" % ("=" * 50))

        # 保存结果
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        payload = {
            "model": "Qwen3VL-8B-Q8_0",
            "gguf": gguf_path,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_sec": round(elapsed, 1),
            "config": {"context": 262144, "cache": "q8_0", "ngl": 99},
            "speed": speed_result,
            "hallucination": halluc_result,
            "rag": rag_result,
            "summary": {
                "speed_tok_s": speed_avg,
                "halluc_score": halluc_avg,
                "rag_score": rag_avg,
                "total_score": total_score,
            },
        }
        out_path = os.path.join(OUT_DIR, "qwen3vl_q8_bench_%s.json" % stamp)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("\n  结果已保存: %s" % out_path)

    finally:
        kill_server()
        try:
            proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()