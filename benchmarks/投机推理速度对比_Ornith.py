#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Ornith-1.5-35B-Q4_K_M ngram 投机速度对比测试 v1
  目的：Ornith-1.5 不带 MTP 头（753 tensor 已确认无 spec/mtp），唯一可用的投机方案是
        llama.cpp 内置的 ngram 投机（--spec-type ngram-mod / ngram-map-k）。
        扫描 ngram 参数找最佳配置，测真实 tok/s（V100 32GB）。
  流程：每轮配置(纯解码 / ngram-mod n-max 16/32/48 / ngram-map-k) → 起 llama-server →
        等就绪 → 发固定 prompt 测 tok/s → 停服务 → 下一轮 → 进度条 + 对比表。
  基础参数与启动器 Ornith-1.5 分支一致：KV q8/q8 + 192K + mmproj内存 + froggeric v22.3 模板。
  Usage:
    python bench_spec_speed_ornith.py              # 全量对比（5 轮，约 10-15 分钟）
    python bench_spec_speed_ornith.py --rounds 2    # 只测前 2 轮（快速验证）
  Date: 2026-08-22
"""
import argparse, json, os, sys, time, re, subprocess, socket
from datetime import datetime

try:
    import requests
except ImportError:
    print("ERROR: 需要 requests 库。pip install requests")
    sys.exit(1)

# Windows 重定向 stdout 时强制 UTF-8（进度条/中文/✓ 不崩）
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

_curr_dir = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = _curr_dir if os.path.exists(os.path.join(_curr_dir, 'llama-server.exe')) else os.path.dirname(_curr_dir)
# Ornith-1.5-35B-Q4_K_M（与启动器 Ornith-1.5 分支一致：KV q8/q8 + 192K + mmproj 内存 + ctx-checkpoints 4）
MODEL = r"E:\models\Ornith-1.5-35B-Q4_K_M.gguf"
SERVER = "http://127.0.0.1:8081"
API_KEY = "llamacpp"
PORT = 8081

# 基础参数（与启动器 Ornith-1.5 分支一致，除投机参数动态加）
BASE_ARGS = [
    "-m", MODEL,
    "-ngl", "99",
    "--cache-type-k", "q8_0",
    "--cache-type-v", "q8_0",
    "-c", "196608",
    "-b", "2048",
    "-t", "6",
    "--parallel", "1",
    "--flash-attn", "enabled",
    "--ctx-checkpoints", "4",
    "--mmproj", os.path.join("E:\\models", "mmproj-Ornith-1.5-35B-A3B-f16.gguf"),
    "--no-mmproj-offload",
    "--no-warmup",
    "--temp", "0.7",
    "--top-p", "0.9",
    "--top-k", "20",
    "--min-p", "0.0",
    "--repeat-penalty", "1.05",
    "--jinja",
    "--chat-template-file", os.path.join(BASE_DIR, "chat_template_qwen_fixed.jinja"),
    "--alias", "Ornith-1.5-35B",
    "--port", str(PORT),
    "--host", "127.0.0.1",
]

# 测试配置列表：(标签, 投机参数列表或 None)
# 无 MTP 头 → 用 ngram 投机（llama.cpp PR #19493 官方方案，Qwen3.5-35B 实测 81 t/s）
# ngram-mod: 在历史中找匹配片段做 draft（重复性文本收益大，日常对话中等）
# ngram-map-k: map-k 变体，接受反馈动态缩短 draft
SPEC_CONFIGS = [
    ("纯解码 (无投机)", None),
    ("ngram-mod n-max=16", ["--spec-type", "ngram-mod", "--spec-ngram-mod-n-max", "16"]),
    ("ngram-mod n-max=32", ["--spec-type", "ngram-mod", "--spec-ngram-mod-n-max", "32"]),
    ("ngram-mod n-max=48", ["--spec-type", "ngram-mod", "--spec-ngram-mod-n-max", "48"]),
    ("ngram-map-k", ["--spec-type", "ngram-map-k"]),
]

# 多任务题库：6 类 × 2 题 = 12 题（temperature=0 确定性，token 可预测性差异覆盖投机收益差异）
# 每类 2 题取平均，最终按"总 tokens / 总耗时"算平均 tok/s（更真实反映混合任务）
TASKS = [
    # 短问答（低 token 量）
    {"cat": "短问答", "prompt": "中国的首都是哪个城市？简要回答。"},
    {"cat": "短问答", "prompt": "水的沸点是多少摄氏度？简要回答。"},
    # 长文生成（高 token 量，中文）
    {"cat": "长文生成", "prompt": "请写一篇约 300 字的散文，主题：秋天的雨。"},
    {"cat": "长文生成", "prompt": "请写一篇约 300 字的说明文，主题：如何养成早睡习惯。"},
    # 代码生成（token 高度可预测 → 投机收益大）
    {"cat": "代码生成", "prompt": "用 Python 写一个快速排序函数，包含注释。"},
    {"cat": "代码生成", "prompt": "用 Python 写一个计算斐波那契数列前 30 项的程序。"},
    # 数学推理（中等可预测）
    {"cat": "数学推理", "prompt": "计算 23 × 47，并说明计算过程。"},
    {"cat": "数学推理", "prompt": "若 x² - 5x + 6 = 0，求 x 的所有解，并写出过程。"},
    # 翻译（token 可预测性较高 → 投机收益中等）
    {"cat": "翻译", "prompt": "把下面翻译成英文：人工智能正在改变我们的生活方式。"},
    {"cat": "翻译", "prompt": "把下面翻译成中文：The quick brown fox jumps over the lazy dog."},
    # 总结摘要（中等）
    {"cat": "总结摘要", "prompt": "用 3 句话总结：可再生能源包括太阳能、风能和水能，它们对减少碳排放有重要作用，但也面临储存和成本等挑战。"},
    {"cat": "总结摘要", "prompt": "用 2 句话概括：机器学习是人工智能的核心，通过数据训练模型来做出预测，在图像识别和自然语言处理中广泛应用。"},
]
TASK_MAX_TOKENS = 400   # 每题 max_tokens（固定，保证可比）


def get_online_model():
    try:
        r = requests.get(f"{SERVER}/v1/models", headers={"Authorization": f"Bearer {API_KEY}"}, timeout=8)
        r.raise_for_status()
        d = r.json()
        if d.get("data"):
            return d["data"][0].get("id", "")
    except Exception:
        pass
    return None


def wait_ready(timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = get_online_model()
        if m:
            return m
        time.sleep(3)
    return None


def start_server(spec_args):
    cmd = [os.path.join(BASE_DIR, "llama-server.exe")] + BASE_ARGS
    if spec_args:
        cmd += spec_args
    ps = (f"$p = Start-Process -FilePath '{cmd[0]}' -ArgumentList " +
          f"@({','.join(repr(a) for a in cmd[1:])}) -WindowStyle Hidden -PassThru; Write-Output $p.Id")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=30)
        pid = int(r.stdout.decode().strip())
        return pid
    except Exception:
        return None


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
    time.sleep(3)


def measure_speed(model, prompt, max_tokens=TASK_MAX_TOKENS):
    """发单题 prompt，测 tok/s 并返回答案文本。返回 (tok/s, tokens, ms, answer)。"""
    url = f"{SERVER}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }
    t0 = time.time()
    try:
        r = requests.post(url, json=payload,
                          headers={"Authorization": f"Bearer {API_KEY}"}, timeout=240)
        dt = (time.time() - t0) * 1000
        r.raise_for_status()
        d = r.json()
        tok = 0
        if d.get("usage") and d["usage"].get("completion_tokens"):
            tok = d["usage"]["completion_tokens"]
        tps = round(tok / (dt / 1000), 1) if dt > 0 and tok > 0 else 0
        answer = ""
        try:
            answer = d["choices"][0]["message"].get("content") or ""
        except Exception:
            answer = ""
        return tps, tok, round(dt, 0), answer
    except Exception as e:
        return 0, 0, 0, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=0, help="只测前 N 轮（0=全量）")
    args = ap.parse_args()

    configs = SPEC_CONFIGS if args.rounds <= 0 else SPEC_CONFIGS[:args.rounds]
    total = len(configs)
    print(f"\n{'='*64}")
    print(f"Ornith-1.5-35B-Q4_K_M ngram 投机速度对比 · 共 {total} 轮")
    print(f"模型: {MODEL}")
    print(f"基础: KV q8/q8 / 192K / mmproj内存 / ctx-checkpoints 4 / froggeric v22.3")
    print(f"{'='*64}\n")

    results = []
    for i, (label, spec_args) in enumerate(configs, 1):
        stop_server()          # 清残留
        pid = start_server(spec_args)
        model = wait_ready(timeout=240)
        if not model:
            print(f"\r  {label} ✗ 启动超时，跳过（可能参数不被当前 build 支持）\n")
            results.append({"label": label, "avg_tps": 0, "tokens": 0, "ms": 0,
                            "per_cat": {}, "per_task": []})
            continue

        # 跑全部 12 题：记录每题，按"总 tokens/总耗时"算平均 tok/s
        task_results = []
        sum_tok = 0
        sum_ms = 0.0
        for j, task in enumerate(TASKS, 1):
            pct = int(((i - 1) * len(TASKS) + (j - 1)) / (total * len(TASKS)) * 100)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r  [{bar}] {pct}% [{i}/{total}] {label} - {task['cat']} ... ", end="", flush=True)
            try:
                tps, tok, ms, answer = measure_speed(model, task["prompt"])
            except Exception:
                tps, tok, ms, answer = 0, 0, 0, ""
            task_results.append({
                "cat": task["cat"], "prompt": task["prompt"],
                "tps": tps, "tokens": tok, "ms": ms, "answer": answer,
            })
            if tok > 0:
                sum_tok += tok
                sum_ms += ms
            time.sleep(0.5)
        stop_server()

        avg_tps = round(sum_tok / (sum_ms / 1000), 1) if sum_ms > 0 and sum_tok > 0 else 0
        # 按类别平均
        per_cat = {}
        for tr in task_results:
            per_cat.setdefault(tr["cat"], []).append(tr["tps"])
        per_cat_avg = {c: round(sum(v) / len(v), 1) for c, v in per_cat.items() if v}
        results.append({
            "label": label, "avg_tps": avg_tps, "tokens": sum_tok, "ms": sum_ms,
            "per_cat": per_cat_avg, "per_task": task_results,
        })
        print(f"\r  [{bar}] 100% [{i}/{total}] {label} 完成 · 平均 {avg_tps} tok/s\n", flush=True)

    print(f"\r  [{'█'*20}] 100% 完成\n")

    # ---- 保存 JSON ----
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(BASE_DIR, "bench_results")
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, f"spec_speed_ornith_{stamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "stamp": stamp, "model": MODEL, "base_args": BASE_ARGS,
            "configs": configs, "tasks": TASKS, "results": results,
        }, f, ensure_ascii=False, indent=2)

    # ---- MD 报告 ----
    md_path = os.path.join(out_dir, f"spec_speed_ornith_{stamp}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Ornith-1.5 ngram 投机速度对比\n\n- 时间: {stamp}\n- 模型: {MODEL}\n\n")
        f.write("## 总览（平均 tok/s = 总 tokens / 总耗时）\n\n")
        f.write("| 配置 | 平均 tok/s | 总 tokens | 总耗时(s) |\n")
        f.write("|:--|:--:|:--:|:--:|\n")
        for r in results:
            f.write(f"| {r['label']} | {r['avg_tps']} | {r['tokens']} | {r['ms']/1000:.1f} |\n")
        f.write("\n## 按类别 tok/s\n\n")
        f.write("| 配置 | " + " | ".join(TASKS[i]["cat"] for i in range(0, len(TASKS), 2)) + " |\n")
        f.write("|:--|" + "|".join(":--:" for _ in range(len(TASKS) // 2)) + "|\n")
        for r in results:
            cats = [str(r["per_cat"].get(c, "-")) for c in [TASKS[i]["cat"] for i in range(0, len(TASKS), 2)]]
            f.write(f"| {r['label']} | " + " | ".join(cats) + " |\n")
        f.write("\n## 每题明细\n\n")
        for r in results:
            f.write(f"\n### {r['label']}\n\n")
            for tr in r["per_task"]:
                f.write(f"- [{tr['cat']}] {tr['tps']} tok/s, {tr['tokens']} tok, {tr['ms']}ms\n")
                f.write(f"  ans: {tr['answer'][:80]}...\n")

    print(f"结果已保存:")
    print(f"  JSON: {json_path}")
    print(f"  MD  : {md_path}")


if __name__ == "__main__":
    main()
