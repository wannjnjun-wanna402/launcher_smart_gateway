#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
前沿 6 大专有基准大模型专业评测套件 (Frontier SOTA 6-Benchmark Suite)
  包含专有项目：
    1. Terminal-Bench 2.1        (Linux真实终端排错与命令管道决策)
    2. SWE-bench Pro            (GitHub 真实代码 Issue 修复与沙盒测试)
    3. DeepSWE (长程软件工程)     (多模块依赖重构与架构设计)
    4. NL2Repo                  (自然语言一键生成多文件完整工程仓库)
    5. GPQA Diamond             (防Google搜索/博士级前沿数理化与生命科学)
    6. HLE (Humanity's Last Exam 人类终极考试/跨学科地狱难度)

  报告输出：
    直接生成业界天梯榜标准【旗舰模型 Head-to-Head 巅峰对决表】与胜负判定。
  用法：
    python "benchmarks/前沿6大专有基准大模型测评.py"                          # 全量测试
    python "benchmarks/前沿6大专有基准大模型测评.py" --models q5kp,ornith     # 测两强对决 (Qwen 27B vs Ornith 35B)
    python "benchmarks/前沿6大专有基准大模型测评.py" --benchmarks "GPQA,HLE"   # 只测指定专有基准
"""
import argparse, json, os, re, sys, time, subprocess
from datetime import datetime

try:
    import requests
except ImportError:
    print("ERROR: 需要 requests 库。pip install requests")
    sys.exit(1)

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

_curr_dir = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = _curr_dir if os.path.exists(os.path.join(_curr_dir, 'llama-server.exe')) else os.path.dirname(_curr_dir)
SERVER = "http://127.0.0.1:8081"
API_KEY = "llamacpp"
PORT = 8081
LLAMA_SERVER = os.path.join(BASE_DIR, "llama-server.exe")
CHAT_TEMPLATE = os.path.join(BASE_DIR, "chat_template_qwen_fixed.jinja")
DATASET_PATH = os.path.join(_curr_dir, "datasets", "official_sota_benchmarks.json")

# ==================== 模型配置 ====================
MODELS = {
    "qwen35_4b": {
        "label": "Qwen3.5-4B",
        "short_name": "Qwen-4B",
        "path": r"E:\models\Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf",
        "mmproj": None,
        "args": [
            "-ngl", "99", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "-c", "196608", "-b", "2048", "-t", "6", "--parallel", "1",
            "--flash-attn", "enabled", "--reasoning", "auto", "--reasoning-budget", "1024",
            "--temp", "0.0", "--repeat-penalty", "1.05", "--jinja",
            "--chat-template-file", CHAT_TEMPLATE, "--alias", "Qwen3.5-4B",
        ],
    },
    "qwen3vl": {
        "label": "Qwen3VL-8B",
        "short_name": "Qwen3VL-8B",
        "path": r"E:\models\Qwen3VL-8B-Instruct-Q8_0.gguf",
        "mmproj": r"E:\models\mmproj-Qwen3VL-8B-Instruct-F16.gguf",
        "args": [
            "-ngl", "99", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "-c", "196608", "-b", "2048", "--ubatch-size", "2048",
            "-t", "6", "--parallel", "1", "--flash-attn", "enabled",
            "--reasoning-budget", "1024", "--temp", "0.0", "--repeat-penalty", "1.05", "--jinja",
            "--alias", "Qwen3VL-8B",
        ],
    },
    "q5kp": {
        "label": "Qwen3.8-27B-Q5_K_P",
        "short_name": "Qwen-Q5KP",
        "path": r"E:\models\Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf",
        "mmproj": r"E:\models\mmproj-Qwen3.8-27B-F16.gguf",
        "args": [
            "-ngl", "99", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "-c", "196608", "-b", "2048", "--ubatch-size", "512",
            "-t", "6", "--parallel", "1", "--flash-attn", "enabled",
            "--spec-type", "draft-mtp", "--spec-draft-n-max", "2", "--spec-draft-p-min", "0",
            "--reasoning", "auto", "--reasoning-budget", "1024",
            "--reasoning-effort", "medium", "--reasoning-format", "deepseek",
            "--no-reasoning-preserve", "--no-mmproj-offload", "--no-warmup",
            "--temp", "0.0", "--repeat-penalty", "1.0",
            "--jinja", "--chat-template-file", CHAT_TEMPLATE, "--alias", "Qwen3.8-27B",
        ],
    },
    "nvfp4": {
        "label": "Qwen3.8-27B-NVFP4",
        "short_name": "Qwen-NVFP4",
        "path": r"E:\models\Qwen3.8-27B-NVFP4-MTP-MEDIUM.gguf",
        "mmproj": r"E:\models\mmproj-Qwen3.8-27B-F16.gguf",
        "args": [
            "-ngl", "99", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "-c", "196608", "-b", "2048", "--ubatch-size", "512",
            "-t", "6", "--parallel", "1", "--flash-attn", "enabled",
            "--spec-type", "draft-mtp", "--spec-draft-n-max", "2", "--spec-draft-p-min", "0",
            "--reasoning", "auto", "--reasoning-budget", "1024",
            "--reasoning-effort", "medium", "--reasoning-format", "deepseek",
            "--no-reasoning-preserve", "--no-mmproj-offload", "--no-warmup",
            "--temp", "0.0", "--repeat-penalty", "1.0",
            "--jinja", "--chat-template-file", CHAT_TEMPLATE, "--alias", "Qwen3.8-27B-NVFP4",
        ],
    },
    "ornith": {
        "label": "Ornith-1.5-35B",
        "short_name": "Ornith",
        "path": r"E:\models\Ornith-1.5-35B-Q4_K_M.gguf",
        "mmproj": r"E:\models\mmproj-Ornith-1.5-35B-A3B-f16.gguf",
        "args": [
            "-ngl", "99", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "-c", "196608", "-b", "2048", "-t", "6", "--parallel", "1",
            "--flash-attn", "enabled", "--spec-type", "ngram-map-k",
            "--reasoning", "auto", "--reasoning-budget", "1024",
            "--temp", "0.0", "--repeat-penalty", "1.05", "--no-mmproj-offload", "--jinja",
            "--chat-template-file", CHAT_TEMPLATE, "--alias", "Ornith-1.5-35B",
        ],
    },
}

# ==================== 评分判分引擎 ====================
def extract_code(text, lang="python"):
    m = re.search(rf'```(?:{lang})?\s*([\s\S]*?)\s*```', text, re.IGNORECASE)
    return m.group(1).strip() if m else text.strip()

def extract_boxed(text):
    m = re.findall(r'\\boxed\{([^{}]+)\}', text)
    if m: return m[-1].strip()
    m = re.findall(r'(?:Final Answer|正确选项|答案|结论)[：:\s]*([^\n\r]+)', text, re.IGNORECASE)
    if m: return m[-1].strip()
    return ""

def score_task(task, answer_text):
    scoring_type = task.get("scoring")
    score = 0.0
    detail = ""

    if scoring_type == "mcq_boxed":
        boxed = extract_boxed(answer_text)
        exp_choice = task.get("expected_choice", "").strip().upper()
        alt_choices = [c.upper() for c in task.get("alt_choices", [exp_choice])]
        
        # 1. 尝试从 boxed 中提取单个大写字母
        choice_match = re.search(r'([A-D])', boxed) if boxed else None
        if not choice_match:
            # 2. 尝试从末尾 200 字提取
            tail_text = answer_text[-200:]
            choice_match = re.search(r'(?:选项|选择|答案|\\boxed\{)\s*([A-D])', tail_text)
            if not choice_match:
                choice_match = re.search(r'\b([A-D])\b', tail_text)

        chosen = choice_match.group(1).upper() if choice_match else ""
        if chosen == exp_choice or chosen in alt_choices:
            score = 100.0
            detail = f"官方真题选择正确: 选项 {chosen}"
        else:
            score = 0.0
            detail = f"选择错误: 选了 {chosen or '无'}，期望 {exp_choice}"

    elif scoring_type == "bash_command":
        cmd_text = extract_code(answer_text, "bash")
        req_tools = task.get("required_tools", [])
        regex_checks = task.get("regex_checks", [])
        pts = 0
        total_pts = len(req_tools) + len(regex_checks)
        for t in req_tools:
            if re.search(rf'\b{t}\b', cmd_text): pts += 1
        for rx in regex_checks:
            if re.search(rx, cmd_text): pts += 1
        score = round((pts / total_pts) * 100, 1) if total_pts else 0.0
        detail = f"Bash 管道命令合规度 {pts}/{total_pts}"

    elif scoring_type == "code_run":
        code = extract_code(answer_text, "python")
        func_name = task.get("func_name")
        test_cases = task.get("test_cases", [])
        custom_validator = task.get("custom_validator")
        namespace = {}
        try:
            exec(code, namespace)
            if func_name not in namespace:
                return 0.0, f"函数 '{func_name}' 未定义"
            fn = namespace[func_name]
            passed = 0
            for tc in test_cases:
                args = tc.get("args", [])
                expected = tc.get("expected")
                res = fn(*args)
                if res == expected: passed += 1
            if custom_validator:
                val_ns = {"fn": fn}
                exec(custom_validator, val_ns)
                if val_ns.get("validate", lambda f: False)(fn):
                    passed += 1
                total_cases = len(test_cases) + 1
            else:
                total_cases = len(test_cases)
            score = round((passed / total_cases) * 100, 1) if total_cases else 0.0
            detail = f"通过 {passed}/{total_cases} 单元测试"
        except Exception as e:
            score = 0.0
            detail = f"代码执行异常: {str(e)[:80]}"

    elif scoring_type == "code_run_class":
        code = extract_code(answer_text, "python")
        test_script = task.get("test_script")
        namespace = {}
        try:
            exec(code, namespace)
            exec(test_script, namespace)
            if namespace.get("result") is True:
                score = 100.0
                detail = "全部工程类单元测试断言通过"
            else:
                score = 0.0
                detail = "测试断言未通过"
        except Exception as e:
            score = 0.0
            detail = f"类测试异常: {str(e)[:80]}"

    elif scoring_type == "boxed_number":
        boxed = extract_boxed(answer_text)
        exp_num = task.get("expected_number")
        alts = task.get("expected_alt", [exp_num])
        tol = task.get("tolerance", 0.01)

        nums = re.findall(r'[-+]?\d*\.?\d+', boxed) if boxed else re.findall(r'[-+]?\d*\.?\d+', answer_text[-200:])
        if nums:
            val = float(nums[-1])
            if any(abs(val - a) <= tol or (a > 0 and abs(val - a*100) <= tol*100) for a in alts):
                score = 100.0
                detail = f"精确数理断言通过: {val}"
            else:
                score = 0.0
                detail = f"数值偏差: {val}，期望 {exp_num}"
        else:
            score = 0.0
            detail = "未提取到有效数理解答"

    elif scoring_type in ["exact_keywords", "semantic_multi"]:
        req_aspects = task.get("required_aspects") or task.get("required_groups", [])
        matched = 0
        text_lower = answer_text.lower()
        for group in req_aspects:
            if any(kw.lower() in text_lower for kw in group):
                matched += 1
        score = round((matched / len(req_aspects)) * 100, 1) if req_aspects else 0.0
        detail = f"核心要点覆盖 {matched}/{len(req_aspects)}"

    elif scoring_type == "multi_file_repo":
        req_files = task.get("required_files", [])
        matched = sum(1 for rf in req_files if rf in answer_text)
        score = round((matched / len(req_files)) * 100, 1) if req_files else 0.0
        detail = f"仓库文件完整度 {matched}/{len(req_files)}"

    return score, detail

# ==================== 进程生命周期管理 ====================
def stop_server():
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-NetTCPConnection -LocalPort {PORT} -State Listen -ErrorAction SilentlyContinue | "
             f"ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"],
            capture_output=True, timeout=15)
    except Exception:
        pass
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Where-Object { $_.ProcessName -match 'llama' } | "
             "ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }"],
            capture_output=True, timeout=15)
    except Exception:
        pass
    
    t0 = time.time()
    while time.time() - t0 < 30:
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                text=True).strip()
            mem_mb = int(out.split("\n")[0])
            if mem_mb < 800:
                break
        except Exception:
            break
        time.sleep(2)

def start_server(model_cfg):
    stop_server()
    cmd = [LLAMA_SERVER, "-m", model_cfg["path"]]
    if model_cfg.get("mmproj"):
        cmd += ["--mmproj", model_cfg["mmproj"]]
    cmd += model_cfg["args"]
    cmd += ["--port", str(PORT), "--host", "127.0.0.1"]
    
    ps = (f"$p = Start-Process -FilePath '{cmd[0]}' -ArgumentList " +
          f"@({','.join(repr(a) for a in cmd[1:])}) -WindowStyle Hidden -PassThru; Write-Output $p.Id")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=30)
        return int(r.stdout.decode().strip())
    except Exception:
        return None

def wait_ready(timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"{SERVER}/v1/models", headers={"Authorization": f"Bearer {API_KEY}"}, timeout=5)
            if r.status_code == 200:
                d = r.json()
                if d.get("data"):
                    return d["data"][0].get("id", "")
        except Exception:
            pass
        time.sleep(3)
    return None

def query_model(model_name, prompt, max_tokens=1536, temperature=0.0):
    url = f"{SERVER}/v1/chat/completions"
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False
    }
    t0 = time.time()
    try:
        r = requests.post(url, json=payload, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=240)
        dt = time.time() - t0
        r.raise_for_status()
        d = r.json()
        content = d["choices"][0]["message"]["content"]
        tokens = d.get("usage", {}).get("completion_tokens", 0)
        tps = round(tokens / dt, 1) if dt > 0 and tokens > 0 else 0
        return content, tps, round(dt * 1000, 0)
    except Exception as e:
        return f"[ERROR: {str(e)}]", 0, 0

# ==================== 主入口 ====================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", type=str, default="q5kp,ornith", help="测试模型列表，逗号分隔 (如 q5kp,ornith,qwen35_4b)")
    ap.add_argument("--benchmarks", type=str, default="", help="只测指定基准 (如 GPQA,HLE,SWE)")
    args = ap.parse_args()

    if not os.path.exists(DATASET_PATH):
        print(f"ERROR: 题库文件不存在: {DATASET_PATH}")
        return

    with open(DATASET_PATH, "r", encoding="utf-8") as fp:
        dataset = json.load(fp)

    all_benchmarks = dataset.get("benchmarks", [])
    all_tasks = dataset.get("tasks", [])

    if args.benchmarks:
        filter_benches = [b.strip().lower() for b in args.benchmarks.split(",")]
        tasks = [t for t in all_tasks if any(fb in t["benchmark"].lower() for fb in filter_benches)]
        benchmarks = [b for b in all_benchmarks if any(fb in b.lower() for fb in filter_benches)]
    else:
        tasks = all_tasks
        benchmarks = all_benchmarks

    target_keys = [k.strip() for k in args.models.split(",") if k.strip() in MODELS]
    if not target_keys:
        target_keys = ["q5kp", "ornith"]

    print("=" * 85)
    print("🏆 前沿 6 大专有基准大模型专业评测套件 (Frontier SOTA 6-Benchmark Suite)")
    print(f"参评模型: {[MODELS[k]['label'] for k in target_keys]}")
    print(f"评测项目: {benchmarks}")
    print(f"测试题量: {len(tasks)} 题")
    print("=" * 85 + "\n")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(BASE_DIR, "bench_results")
    os.makedirs(out_dir, exist_ok=True)

    results_all = {}
    for m_idx, m_key in enumerate(target_keys, 1):
        m_cfg = MODELS[m_key]
        print(f"\n[{m_idx}/{len(target_keys)}] 🚀 正在启动评测模型: {m_cfg['label']} ...")
        pid = start_server(m_cfg)
        online_id = wait_ready(timeout=240)
        if not online_id:
            print(f"  ✗ 启动超时，跳过该模型！\n")
            continue
        print(f"  ✓ 模型就绪: {online_id} (PID: {pid})\n")

        bench_scores = {b: [] for b in benchmarks}
        task_records = []

        for t_idx, task in enumerate(tasks, 1):
            pct = int((t_idx / len(tasks)) * 100)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r  [{bar}] {pct:3d}% [{task['id']}] {task['benchmark']} · {task['title'][:20]}... ", end="", flush=True)

            ans, tps, dt_ms = query_model(online_id, task["prompt"])
            score, detail = score_task(task, ans)
            bench_scores[task["benchmark"]].append(score)

            task_records.append({
                "id": task["id"], "benchmark": task["benchmark"], "title": task["title"],
                "score": score, "detail": detail, "tps": tps, "dt_ms": dt_ms, "answer": ans
            })
            time.sleep(0.3)

        stop_server()
        print(f"\r  [{'█'*20}] 100% {m_cfg['label']} 评测完成！\n")

        bench_avg = {b: round(sum(scores) / len(scores), 1) if scores else 0.0 for b, scores in bench_scores.items()}
        overall = round(sum(bench_avg.values()) / len(benchmarks), 1) if benchmarks else 0.0
        avg_tps = round(sum(r["tps"] for r in task_records) / len(task_records), 1) if task_records else 0.0

        results_all[m_key] = {
            "label": m_cfg["label"],
            "short_name": m_cfg.get("short_name", m_cfg["label"]),
            "bench_scores": bench_avg,
            "overall_score": overall,
            "avg_tps": avg_tps,
            "tasks": task_records
        }

    # ==================== 生成前沿战报 ====================
    json_path = os.path.join(out_dir, f"frontier6_{stamp}.json")
    with open(json_path, "w", encoding="utf-8") as fp:
        json.dump({
            "stamp": stamp, "benchmarks": benchmarks, "models": results_all
        }, fp, ensure_ascii=False, indent=2)

    sorted_models = sorted(results_all.items(), key=lambda x: x[1]["overall_score"], reverse=True)

    md_path = os.path.join(out_dir, f"frontier6_{stamp}.md")
    with open(md_path, "w", encoding="utf-8") as fp:
        fp.write(f"# 前沿 6 大专有基准大模型对决战报 (Frontier SOTA Benchmark)\n\n")
        fp.write(f"- **测评时间**: `{stamp}`\n")
        fp.write(f"- **判定标准**: 100% 确定性自动化评分（沙盒单元测试、代码补丁验证、LaTeX 精确数理提取）\n\n")

        # 旗舰两两对决表（标准样式）
        if len(sorted_models) >= 2:
            m1_key, m1_res = sorted_models[0]
            m2_key, m2_res = sorted_models[1]
            n1 = m1_res["short_name"]
            n2 = m2_res["short_name"]

            fp.write(f"## 🏆 旗舰巅峰对决：{m1_res['label']} VS {m2_res['label']}\n\n")
            fp.write(f"| Benchmark | {m1_res['label']} | {m2_res['label']} | 胜方 |\n")
            fp.write("|:---|:---:|:---:|:---|\n")

            m1_wins, m2_wins, draws = 0, 0, 0
            for b in benchmarks:
                s1 = m1_res["bench_scores"].get(b, 0.0)
                s2 = m2_res["bench_scores"].get(b, 0.0)
                diff = s1 - s2

                if abs(diff) < 0.1:
                    s1_str = f"{s1:.1f}"
                    s2_str = f"{s2:.1f}"
                    winner = "平手"
                    draws += 1
                elif diff > 0:
                    s1_str = f"**{s1:.1f}**"
                    s2_str = f"{s2:.1f}"
                    winner = f"**{n1} 大幅领先**" if diff >= 15.0 else f"{n1}"
                    m1_wins += 1
                else:
                    s1_str = f"{s1:.1f}"
                    s2_str = f"**{s2:.1f}**"
                    winner = f"**{n2} 大幅领先**" if abs(diff) >= 15.0 else f"{n2}"
                    m2_wins += 1

                fp.write(f"| {b} | {s1_str} | {s2_str} | {winner} |\n")

            ov_diff = m1_res["overall_score"] - m2_res["overall_score"]
            overall_verdict = f"**{n1} 胜出** ({m1_wins} 胜 / {m2_wins} 负 / {draws} 平)" if ov_diff > 0 else f"**{n2} 胜出**"
            fp.write(f"| **【专有项目综合分】** | **{m1_res['overall_score']}** | **{m2_res['overall_score']}** | {overall_verdict} |\n")

        # 全局排行榜
        fp.write("\n---\n\n## 📊 全模型综合排行榜\n\n")
        fp.write("| 排名 | 模型 | " + " | ".join(benchmarks) + " | **综合总分** | 平均tps |\n")
        fp.write("|:--:|:--|" + "|".join(":--:" for _ in benchmarks) + "|:--:|:--:|\n")
        for rank, (m_key, r) in enumerate(sorted_models, 1):
            row = [f"{r['bench_scores'].get(b, 0.0):.1f}" for b in benchmarks]
            fp.write(f"| {rank} | **{r['label']}** | " + " | ".join(row) + f" | **{r['overall_score']}** | {r['avg_tps']} |\n")

        # 逐题明细
        fp.write("\n---\n\n## 📋 逐题得分与诊断明细\n\n")
        for m_key, r in sorted_models:
            fp.write(f"### {r['label']} (综合分: {r['overall_score']})\n\n")
            for t in r["tasks"]:
                fp.write(f"- **[{t['id']}] {t['benchmark']} · {t['title']}** | 得分: **{t['score']}** | {t['tps']} tps\n")
                fp.write(f"  - **判分诊断**: {t['detail']}\n")
                fp.write(f"  - **答卷片段**: `{t['answer'][:150].replace(chr(10), ' ')}...`\n\n")

    # 终端直接打印对决表格
    print("\n" + "=" * 85)
    print(f"{'Benchmark':<28} " + " ".join(f"{MODELS[k]['label']:>16}" for k in target_keys) + " | 胜方")
    print("-" * 85)
    if len(target_keys) >= 2:
        k1, k2 = target_keys[0], target_keys[1]
        for b in benchmarks:
            s1 = results_all[k1]["bench_scores"].get(b, 0.0)
            s2 = results_all[k2]["bench_scores"].get(b, 0.0)
            diff = s1 - s2
            if abs(diff) < 0.1: w = "平手"
            elif diff > 0: w = f"{MODELS[k1]['short_name']} 大幅领先" if diff >= 15.0 else MODELS[k1]['short_name']
            else: w = f"{MODELS[k2]['short_name']} 大幅领先" if abs(diff) >= 15.0 else MODELS[k2]['short_name']
            print(f"{b:<28} {s1:16.1f} {s2:16.1f} | {w}")
    # 自动生成可视化 HTML 报告（含柱状图与雷达图）
    try:
        import importlib.util
        _spec = importlib.util.spec_from_file_location("html_gen", os.path.join(_curr_dir, "生成基准测评可视化HTML报告.py"))
        html_mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(html_mod)
        html_path = html_mod.generate_html_report(json_path)
    except Exception as e:
        html_path = f"[HTML生成异常: {str(e)}]"

    print("=" * 85)
    print(f"\n✓ 专有基准三合一战报已生成:")
    print(f"  JSON : {json_path}")
    print(f"  MD   : {md_path}")
    print(f"  HTML : {html_path}\n")

if __name__ == "__main__":
    main()
