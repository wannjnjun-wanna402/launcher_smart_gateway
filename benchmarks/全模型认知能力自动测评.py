##!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
专项认知能力测评脚本 v2
  题库：quiz_cognitive_v1.json（推理/记忆/工具）
  服务：本地 llama.cpp 的 llama-server（默认 http://127.0.0.1:8081）
  模式：运行时只采集行为数据（思考/正文/延迟/GPU），不打分。
       跑完输出 待AI评分 JSON → 统一交付费 AI 打分。
  参照：bench_rag.py 样式（http.client，无外部依赖）
  Usage:
    python bench_cognitive.py --quiz quiz_cognitive_v1.json       # 全模型串行
    python bench_cognitive.py --quiz quiz_cognitive_no_tool.json  # 去掉工具题
    python bench_cognitive.py --quiz ... --models 1               # 只测第 1 个
  Date: 2026-08-03
"""
import argparse, json, os, sys, time, re
import http.client
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime

# ============================================================
#  HTTP 调服务（参照 bench_rag.py 样式，http.client，无外部依赖）
# ============================================================
def _do_chat(server, api_key, model, user_msg, max_tokens=8192, stream=True, timeout=180,
             tools=None, tool_choice=None):
    """流式请求 /v1/chat/completions。
       返回 (reasoning_text, content_text, latency_ms, usage_dict)。
       usage_dict = {"prompt_tokens":..., "completion_tokens":..., "total_tokens":...} 或 None"""
    url = f"{server.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": user_msg}],
        "max_tokens": max_tokens,
        "stream": stream,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    t0 = time.time()
    body = json.dumps(payload)
    try:
        parsed = urllib.parse.urlparse(url)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
        conn.request("POST", parsed.path, body=body, headers=headers)
        r = conn.getresponse()
        if r.status >= 400:
            raise Exception(f"HTTP {r.status}: {r.reason}")
        if not stream:
            raw = r.read().decode("utf-8", errors="replace")
            obj = json.loads(raw)
            choices = obj.get("choices") or []
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            usage = obj.get("usage", None)
            conn.close()
            return "", content, (time.time() - t0) * 1000, usage

        reasoning_text = ""
        content_text = ""
        usage = None
        in_reasoning = True
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
            # 流式最后 chunk 带 usage（llama.cpp --stream 支持）
            u = chunk.get("usage")
            if isinstance(u, dict) and u.get("completion_tokens") is not None:
                usage = u
            choices = chunk.get("choices") or []
            if not choices:
                continue
            ch = choices[0]
            delta = ch.get("delta") or {}

            rc = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking")
            if rc:
                if not in_reasoning:
                    print("\n\033[90m[思考中]\033[0m", end="", flush=True)
                    in_reasoning = True
                reasoning_text += rc
                print(f"\033[90m{rc}\033[0m", end="", flush=True)

            c = delta.get("content")
            if c:
                if in_reasoning:
                    print("\n\033[0m[回答]\n", end="", flush=True)
                    in_reasoning = False
                content_text += c
                print(c, end="", flush=True)

            finish = ch.get("finish_reason")
            if finish and finish in ("stop", "tool_calls", "length", "content_filter"):
                break

        conn.close()
        print()
        return reasoning_text, content_text, (time.time() - t0) * 1000, usage
    except Exception as e:
        latency = (time.time() - t0) * 1000
        print(f"\n[ERROR: {type(e).__name__}: {e}]")
        return "", f"[ERROR: {type(e).__name__}: {e}]", latency, None


def get_online_model(server, api_key):
    """探测 /v1/models 返回的在线模型名。"""
    try:
        req = urllib.request.Request(f"{server.rstrip('/')}/v1/models",
                                     headers={"Authorization": f"Bearer {api_key}"})
        resp = urllib.request.urlopen(req, timeout=10)
        if resp.status == 200:
            data = json.loads(resp.read().decode("utf-8"))
            lst = data.get("data") or []
            if lst:
                return lst[0].get("id", "")
    except Exception:
        pass
    return None


# web_search 工具声明（OpenAI 兼容 tools 参数，测试模型是否发出格式正确的工具调用）
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

def call_tool(server, api_key, model, user_msg, timeout=180):
    """[保留兼容] 改用 _do_chat，不直接使用 requests"""
    return _do_chat(server, api_key, model, user_msg, max_tokens=8192,
                    tools=[WEB_SEARCH_TOOL], tool_choice="auto", timeout=timeout)

def score_tool_call(tool_calls, expected_tool, expected_args_keywords):
    """工具调用格式正确率 0-1：测"工具调用能力"而非"搜索词恰好是中文"。
       发出期望工具名（如 web_search）且 arguments 为合法 JSON 且 query 非空 = 1.0
       （模型用英文/自然语言搜索词同样算正确检索行为）；
       只发出工具名但参数异常 = 0.5；直接编造文本回答（无 tool_calls）= 0。
       expected_args_keywords 仅作参考（命中不加分，不命中不扣分）。"""
    if not tool_calls or isinstance(tool_calls, str) or "[ERROR" in str(tool_calls):
        return 0.0
    name_hit = False
    args_ok = False
    for tc in tool_calls:
        if tc.get("name") == expected_tool:
            name_hit = True
            arg_str = str(tc.get("arguments", ""))
            # 参数是合法 JSON 且 query 非空 = 工具调用格式完整
            try:
                arg_obj = json.loads(arg_str)
                if isinstance(arg_obj, dict) and str(arg_obj.get("query", "")).strip():
                    args_ok = True
            except Exception:
                # 参数不是 JSON（可能被截断），但至少包含 query 字样也算
                if "query" in arg_str and len(arg_str) > 10:
                    args_ok = True
    return round(1.0 if (name_hit and args_ok) else (0.5 if name_hit else 0.0), 4)

# ============================================================
#  三指标计算
# ============================================================
def normalize(text):
    """中文文本归一化：去空白/标点，便于宽松匹配。"""
    if not text:
        return ""
    # 去所有空白
    t = re.sub(r"\s+", "", str(text))
    # 去常见标点（中英）
    t = re.sub(r"[，。、；：？！,.;:?!（）()\[\]{}「」『』]", "", t)
    return t.lower()

def score_chain_completeness(response, expected_chain):
    """指标①：推理链完整性评分 0-1
       命中 expected_chain 中间结论的比例。归一化后宽松匹配。"""
    if not expected_chain:
        return 1.0 if response and "[ERROR" not in response else 0.0
    norm_resp = normalize(response)
    if not norm_resp:
        return 0.0
    hit = 0
    for step in expected_chain:
        norm_step = normalize(step)
        if norm_step and norm_step in norm_resp:
            hit += 1
    return round(hit / len(expected_chain), 4)

def score_originality(response, expected_keywords, anti_memo_check):
    """指标②：答案原创性比例 0-1
       原创性 = 含期望关键词 且 不命中 anti_memo_check 背诵特征词。
       含期望关键词=1，命中背诵特征=扣分。"""
    if not response or "[ERROR" in response:
        return 0.0
    norm_resp = normalize(response)
    has_kw = any(normalize(k) in norm_resp for k in expected_keywords)
    if not has_kw:
        return 0.0
    # 命中背诵特征词则扣分（每命中一个扣 0.5，不低于 0）
    penalty = 0
    for m in anti_memo_check:
        if m and normalize(m) in norm_resp:
            penalty += 0.5
    return round(max(0.0, 1.0 - penalty), 4)

def is_correct(response, expected_keywords):
    """答案正确性：含至少一个期望关键词。"""
    if not response or "[ERROR" in response:
        return False
    norm_resp = normalize(response)
    return any(normalize(k) in norm_resp for k in expected_keywords)

def memory_dependency_hit(response, anti_memo_check):
    """指标③的分子：是否命中记忆依赖特征词（背诵训练数据）。"""
    if not response or not anti_memo_check:
        return False
    norm_resp = normalize(response)
    return any(normalize(m) in norm_resp for m in anti_memo_check if m)

# ============================================================
#  进度条（stderr + 换行式，不被 call_model 流式 stdout 冲掉）
# ============================================================
class TextPbar:
    def __init__(self, total):
        self.total = total
        self.cur = 0
        self.last_pct = -1
    def update(self, n=1):
        self.cur += n
        pct = self.cur / self.total * 100
        bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
        # 只整10%才换行，避免刷屏
        cur_dec = int(pct)
        if cur_dec >= self.last_pct + 10:
            self.last_pct = cur_dec
            sys.stderr.write("  >> 总进度 [{:<20}] {} / {} ({:.0f}%)\n".format(
                bar, self.cur, self.total, pct))
            sys.stderr.flush()
    def finish(self):
        sys.stderr.write("  >> 总进度 [{:<20}] {}/{} (100%) Done\n\n".format(
            "█" * 20, self.total, self.total))
        sys.stderr.flush()

# ============================================================
#  GPU 利用率
# ============================================================
def _gpu_util():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        v = r.stdout.strip()
        if v.isdigit():
            return int(v)
    except Exception:
        pass
    return -1


def _gpu_info():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return "-"


# ============================================================
#  串行驱动：测全部模型（通过 .bat 包装器避 PowerShell -File 陷阱）
# ============================================================
import subprocess, socket

_curr_dir = os.path.dirname(os.path.abspath(__file__))
HERE = _curr_dir if os.path.exists(os.path.join(_curr_dir, 'llama-server.exe')) else os.path.dirname(_curr_dir)
OUT_DIR = os.path.join(HERE, "eval_results", "bench_cognitive")
os.makedirs(OUT_DIR, exist_ok=True)


def _ps_run_bat(bat_path, timeout=60):
    """通过 cmd /c 调用 .bat 包装器。"""
    cmd = ["cmd", "/c", bat_path]
    return subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           cwd=HERE)


def list_models():
    """通过 _list_models.bat 获取模型清单。"""
    list_bat = HERE + "\\_list_models.bat"
    content = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        'powershell -NoProfile -ExecutionPolicy Bypass -File '
        + '"%s\\launcher_main.ps1" -ListModels\r\n' % HERE
    )
    with open(list_bat, "w", encoding="utf-8") as f:
        f.write(content)
    res = _ps_run_bat(list_bat, timeout=120)
    text = res.stdout or ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            try:
                arr = json.loads(s)
                if isinstance(arr, list) and arr:
                    return arr
            except Exception:
                pass
    raise RuntimeError("无法解析模型清单。\n启动器输出：\n" + text[-800:])


def start_server(idx):
    """通过 _launch_<idx>.bat 包装器启动单个模型。"""
    launch_script = HERE + "\\_launch_%d.bat" % idx
    content = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        'powershell -NoProfile -ExecutionPolicy Bypass -File '
        + '"%s\\launcher_main.ps1" -ServerMode -ModelIndex %d\r\n' % (HERE, idx)
    )
    with open(launch_script, "w", encoding="utf-8") as f:
        f.write(content)
    log_dir = os.path.join(OUT_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = os.path.join(log_dir, "server_%d_%s.log" % (idx, ts))
    f = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen(
        ["cmd", "/c", launch_script],
        stdout=f, stderr=subprocess.STDOUT,
        cwd=HERE,
    )
    return proc, log


def wait_ready(server, api_key, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = get_online_model(server, api_key)
        if m:
            return m
        time.sleep(3)
    return None


def kill_server():
    """彻底清理：杀所有 llama 进程 + 端口 + 循环检查 GPU 显存。"""
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-Command", "Get-Process -Name llama* -ErrorAction SilentlyContinue | Stop-Process -Force"],
                       capture_output=True, timeout=15)
    except Exception:
        pass
    time.sleep(1)
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-Command", "Get-NetTCPConnection -LocalPort 8081 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $($_.OwningProcess) -Force }"],
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


def run_bench_with_pbar(server, api_key, quiz_path, max_tokens, timeout, pbar=None):
    """跑完只收集行为数据（思考/正文/延迟/GPU），不打分。输出 待AI评分 JSON。"""
    with open(quiz_path, "r", encoding="utf-8-sig") as f:
        quiz = json.load(f)
    meta = quiz.get("_meta", {})
    items = quiz.get("items", [])
    total = len(items)
    print(f"\n  {'='*50}")
    print(f"  题库: {quiz_path}  共 {total} 题")
    print(f"    推理类: {meta.get('reasoning_items', '?')}  "
          f"记忆类: {meta.get('memory_items', '?')}  "
          f"工具类: {meta.get('tool_items', '?')}")
    print(f"  {'='*50}\n")

    model = get_online_model(server, api_key)
    if not model:
        print(f"  ERROR: 探不到在线模型。")
        return None
    print(f"  在线模型: {model}\n")

    records = []
    t_start = time.time()

    for i, q in enumerate(items, 1):
        qtype = {"reasoning": "推理", "memory": "记忆", "tool": "工具"}.get(q["type"], q["type"])
        pct = int(i / total * 100)
        bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
        gpu_util = -1
        tok_s = None
        comp_tokens = None

        sys.stderr.write("\n[{} / {}] 进度 [{:<20}] {:>3}%  Q{} [{}]  提问: {}\n".format(
            i, total, bar, pct, q["id"], qtype, (q["question"] or "")[:60]))
        sys.stderr.flush()

        if q["type"] == "tool":
            reasoning_text, content_text, latency, usage = _do_chat(
                server, api_key, model, q["question"],
                max_tokens=max_tokens, timeout=timeout, tools=[WEB_SEARCH_TOOL],
                tool_choice="auto")
        else:
            reasoning_text, content_text, latency, usage = _do_chat(
                server, api_key, model, q["question"],
                max_tokens=max_tokens, timeout=timeout)

        # 计算输出速度 tok/s
        if usage and usage.get("completion_tokens", 0) > 0 and latency > 10:
            comp_tokens = usage["completion_tokens"]
            tok_s = round(comp_tokens / (latency / 1000), 1)
        else:
            comp_tokens = None
            tok_s = None

        gpu_util = _gpu_util()
        full_text = reasoning_text + content_text
        sys.stderr.write("  [完成] 延迟={:.0f}ms  字符数={:>5}  输出token={:>4}  速度={:>5.1f} tok/s  GPU={}%\n".format(
            latency, len(full_text), comp_tokens if comp_tokens else 0, tok_s if tok_s else 0, gpu_util))
        sys.stderr.flush()

        records.append({
            "id": q["id"],
            "domain": q.get("domain"),
            "category": q.get("category"),
            "type": q["type"],
            "reasoning_steps": q.get("reasoning_steps"),
            "memory_dependency": q.get("memory_dependency"),
            "question": q.get("question"),
            "expected_keywords": q.get("expected_keywords"),
            "expected_chain": q.get("expected_chain"),
            "anti_memo_check": q.get("anti_memo_check"),
            "reasoning_text": reasoning_text,
            "content_text": content_text,
            "full_text": full_text,
            "latency_ms": round(latency, 1),
            "completion_tokens": comp_tokens,
            "tok_s": tok_s,
            "gpu_util_pct": gpu_util,
            "output_len": len(full_text),
            "score": None,
            "scored_by": None,
        })
        if pbar:
            pbar.update()

    elapsed = time.time() - t_start

    # 计算平均输出速度
    valid_tps = [r["tok_s"] for r in records if r["tok_s"] is not None and r["tok_s"] > 0]
    avg_tok_s = round(sum(valid_tps) / len(valid_tps), 1) if valid_tps else None
    valid_n = len(valid_tps)

    print(f"\n  测评完成，用时 {elapsed:.0f}s，共 {total} 题\n")
    print(f"  输出速度：{avg_tok_s} tok/s（{valid_n}/{total} 题有 token 数据）")

    summary = {
        "model": model,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "quiz_path": quiz_path,
        "total": total,
        "elapsed_sec": round(elapsed, 1),
        "avg_tok_s": avg_tok_s,
        "avg_tok_s_valid_n": valid_n,
        "status": "待AI评分",
    }

    # 保存待评分 JSON
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^\w\-]", "_", model)[:40]
    json_path = os.path.join(OUT_DIR, f"cog_待评分_{safe}_{stamp}.json")
    full = {"summary": summary, "items": records}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=2)

    print(f"  {'='*50}")
    print(f"  已收集 {total} 题全部回答，等待 AI 评分")
    print(f"  平均输出速度: {avg_tok_s} tok/s（{valid_n}/{total} 题有效）")
    print(f"  待评分文件: {json_path}")
    print(f"  模型: {model}  |  用时: {elapsed:.0f}s")
    print(f"  {'='*50}")
    return {"summary": summary, "items": records, "json_path": json_path}


def run_one_model(server, api_key, quiz_path, max_tokens, timeout, idx, name, pbar=None):
    """起→等就绪→测→停 单个模型。"""
    print(f"\n{'='*60}")
    print(f"  启动模型 #{idx}  {name}")
    print(f"{'='*60}")
    kill_server()
    proc, log = start_server(idx)
    model_id = wait_ready(server, api_key, timeout=240)
    if not model_id:
        print(f"  ⚠ 模型 #{idx} 启动超时（240s 未就绪），跳过。日志: {log}")
        kill_server()
        try: proc.terminate()
        except Exception: pass
        return None
    print(f"  ✓ 服务就绪: {model_id}")
    try:
        result = run_bench_with_pbar(server, api_key, quiz_path, max_tokens, timeout, pbar=pbar)
    finally:
        kill_server()
        try: proc.terminate()
        except Exception: pass
    return result


def main():
    ap = argparse.ArgumentParser(description="专项认知能力测评（全模型自动串行）")
    ap.add_argument("--server", default="http://127.0.0.1:8081")
    ap.add_argument("--apikey", default="llamacpp")
    ap.add_argument("--quiz", default=os.path.join(HERE, os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasets", "quiz_cognitive_v1.json") if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasets", "quiz_cognitive_v1.json")) else "quiz_cognitive_v1.json"))
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--models", help="指定模型序号，逗号分隔（菜单序号，1-based）；不指定则测全部")
    args = ap.parse_args()

    # 获取模型清单
    try:
        models = list_models()
    except Exception as e:
        sys.stderr.write("[!] 获取模型清单失败：%s\n" % e)
        sys.exit(1)

    if args.models:
        want = set()
        for x in args.models.split(","):
            x = x.strip()
            if x.isdigit():
                want.add(int(x))
        models = [m for m in models if m.get("index") in want]
    if not models:
        sys.stderr.write("[!] 没有可测模型。\n")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"专项认知能力测评 · 全模型串行（全自动）")
    print(f"  题库: {args.quiz}")
    print(f"  共 {len(models)} 个模型待测：")
    for m in models:
        print(f"    #{m['index']}  {m['name']}  ({m.get('sizeGB','?')}GB)")
    print(f"{'='*60}\n")

    # 每模型 52 题（40推理 + 12记忆，无工具题）
    steps_per_model = 52
    total_steps = steps_per_model * len(models)
    pbar = TextPbar(total_steps)

    all_results = []
    all_json_paths = []
    for i, m in enumerate(models, 1):
        idx = m["index"]
        name = m.get("name", "model%d" % idx)
        print(f"\n{'#'*60}")
        print(f"# 模型 {i}/{len(models)}：#{idx}  {name}")
        print(f"{'#'*60}")
        result = run_one_model(args.server, args.apikey, args.quiz,
                               args.max_tokens, args.timeout, idx, name, pbar=pbar)
        if result:
            all_results.append({"index": idx, "name": name, "summary": result["summary"],
                                "json_path": result.get("json_path")})
            print(f"  [✓] 待评分文件: {result.get('json_path')}")

    pbar.finish()

    # 汇总：列出所有待评分文件 + 输出速度
    print(f"\n{'='*60}")
    print(f"全模型行为数据采集完成，等待付费 AI 统一评分")
    print(f"{'='*60}")
    print(f"{'#':<3}  {'模型':<45}  {'平均 tok/s':>10}  {'状态'}")
    print("-" * 95)
    speed_map = {}
    for r in all_results:
        status = r["summary"].get("status", "待AI评分")
        tps = r["summary"].get("avg_tok_s")
        tps_str = "%.1f" % tps if tps is not None else "-"
        print(f"{r['index']:<3}  {r['name']:<45}  {tps_str:>10}  {status}")
        if tps is not None:
            speed_map[r["name"]] = round(tps, 1)
    print("-" * 95)
    print(f"\n所有待评分 JSON 文件已保存在: {OUT_DIR}")
    print(f"文件名格式: cog_待评分_<模型名>_<时间戳>.json")
    print(f"{'='*60}")

    # 与启动器 BENCHMARK_DATA.Speed 对比，输出差异表
    launcher_path = os.path.join(HERE, "launcher_main.ps1")
    print(f"\n{'='*60}")
    print(f"输出速度与启动器 BENCHMARK_DATA 对比")
    print(f"{'='*60}")
    print(f"{'模型':<45}  {'启动器':>8}  {'本次实测':>8}  {'差异'}")
    print("-" * 85)
    for r in all_results:
        name = r["name"]
        tps = r["summary"].get("avg_tok_s")
        if tps is None:
            continue
        # 从 launcher_main.ps1 提取该模型的 Speed
        old_speed = _find_benchmark_speed(launcher_path, name)
        if old_speed is None:
            print(f"{name:<45}  {'未记录':>8}  {tps:>8.1f}  新增")
        else:
            diff = round(tps - old_speed, 1)
            mark = "" if abs(diff) < 2 else ("⬆" if diff > 0 else "⬇")
            print(f"{name:<45}  {old_speed:>8.1f}  {tps:>8.1f}  {diff:+.1f} {mark}")
    print("-" * 85)
    print(f"\n是否将本次实测速度覆盖到 launcher_main.ps1？(Y/N) ", end="", flush=True)
    reply = input().strip().upper()
    if reply == "Y":
        _update_benchmark_speed(launcher_path, speed_map)
        print(f"  已更新 launcher_main.ps1 中 {len(speed_map)} 个模型的 Speed")
    else:
        print(f"  跳过，未修改 launcher_main.ps1")
    print(f"{'='*60}")


def _find_benchmark_speed(launcher_path, model_name):
    """从 launcher_main.ps1 的 $BENCHMARK_DATA 中提取模型 Speed。"""
    try:
        with open(launcher_path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except Exception:
        return None
    lines = text.splitlines()
    in_map = False
    for i, line in enumerate(lines):
        if line.strip().startswith("$BENCHMARK_DATA"):
            in_map = True
            continue
        if not in_map:
            continue
        stripped = line.strip()
        if stripped.startswith('"') and stripped.endswith('"'):
            key = stripped.strip('" =')
            key = key.strip().rstrip('"')
            if key == model_name:
                # 找下面几行的 Speed = xxx
                for j in range(i + 1, min(i + 10, len(lines))):
                    ml = lines[j].strip()
                    if ml.startswith("Speed"):
                        try:
                            v = ml.split("=")[1].split()[0]
                            return float(v)
                        except Exception:
                            return None
                return None
        if stripped == "}":
            break
    return None


def _update_benchmark_speed(launcher_path, speed_map):
    """将 speed_map {model_name: speed} 更新到 launcher_main.ps1。"""
    with open(launcher_path, "r", encoding="utf-8-sig") as f:
        lines = f.readlines()
    in_map = False
    updated = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("$BENCHMARK_DATA"):
            in_map = True
            continue
        if not in_map:
            continue
        stripped = line.strip()
        if stripped.startswith('"') and stripped.endswith('"'):
            key = stripped.split('"')[1]
            if key in speed_map:
                # 找下面几行的 Speed = xxx 并替换
                for j in range(i + 1, min(i + 10, len(lines))):
                    if lines[j].strip().startswith("Speed"):
                        lines[j] = lines[j][:12] + " %s    # 实测覆盖 2026-08-03 bench_cognitive.py\n" % speed_map[key]
                        updated += 1
                        break
        if stripped == "}":
            break
    with open(launcher_path, "w", encoding="utf-8-sig") as f:
        f.writelines(lines)



if __name__ == "__main__":
    main()
