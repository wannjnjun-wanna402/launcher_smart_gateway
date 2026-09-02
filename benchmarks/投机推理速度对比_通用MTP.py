#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Qwen3.8-27B MTP / 自投机 速度对比测试 v1
  目的：找出"纯解码 vs 自投机(draft-mtp)"的最佳 spec-draft-n-max，测真实 tok/s。
  流程：每轮配置(纯解码 / n-max=1,2,3,4,5,6,8) → 起 llama-server → 等就绪 →
        发固定 prompt 测 tok/s → 停服务 → 下一轮 → 进度条 + 对比表。
  基础参数与启动器一致：Q5_K_M + KV q8 + 256K + 无 mmproj + reasoning auto。
  Usage:
    python bench_spec_speed.py              # 全量对比（8 轮，约 10-20 分钟）
    python bench_spec_speed.py --rounds 3    # 只测前 3 轮（快速验证）
  Date: 2026-08-17
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
# 真 MTP 头 Q6_K 版本（与启动器 MTP-Q6 分支一致：KV q8 / 192K）
MODEL = r"E:\models\Qwen3.8-27B-MTP-Q6_K.gguf"
SERVER = "http://127.0.0.1:8081"
API_KEY = "llamacpp"
PORT = 8081

# 基础参数（与启动器 Qwen3.8-27B 分支一致，除 spec 参数动态加）
BASE_ARGS = [
    "-m", MODEL,
    "-ngl", "99",
    "--cache-type-k", "q8_0",
    "--cache-type-v", "q4_0",
    "-c", "196608",
    "-b", "2048",
    "-t", "6",
    "--parallel", "1",
    "--flash-attn", "enabled",
    "--reasoning", "auto",
    "--reasoning-budget", "2048",
    "--temp", "0.7",
    "--top-p", "0.9",
    "--top-k", "20",
    "--min-p", "0.0",
    "--repeat-penalty", "1.0",
    "--presence-penalty", "1.5",
    "--jinja",
    "--alias", "Qwen3.8-27B-MTP-Q6",
    "--port", str(PORT),
    "--host", "127.0.0.1",
]

# 测试配置列表：(标签, spec 参数列表或 None)
SPEC_CONFIGS = [
    ("纯解码 (无投机)", None),
    ("自投机 n-max=1", ["--spec-type", "draft-mtp", "--spec-draft-n-max", "1", "--spec-draft-n-min", "1"]),
    ("自投机 n-max=2", ["--spec-type", "draft-mtp", "--spec-draft-n-max", "2", "--spec-draft-n-min", "1"]),
    ("自投机 n-max=3", ["--spec-type", "draft-mtp", "--spec-draft-n-max", "3", "--spec-draft-n-min", "1"]),
    ("自投机 n-max=4", ["--spec-type", "draft-mtp", "--spec-draft-n-max", "4", "--spec-draft-n-min", "1"]),
    ("自投机 n-max=5", ["--spec-type", "draft-mtp", "--spec-draft-n-max", "5", "--spec-draft-n-min", "1"]),
    ("自投机 n-max=6", ["--spec-type", "draft-mtp", "--spec-draft-n-max", "6", "--spec-draft-n-min", "1"]),
    ("自投机 n-max=8", ["--spec-type", "draft-mtp", "--spec-draft-n-max", "8", "--spec-draft-n-min", "1"]),
]

# 多任务题库：6 类 × 2 题 = 12 题（temperature=0 确定性，token 可预测性差异覆盖 MTP 投机收益差异）
# 每类 2 题取平均，最终按"总 tokens / 总耗时"算平均 tok/s（更真实反映混合任务）
TASKS = [
    # 短问答（低 token 量）
    {"cat": "短问答", "prompt": "中国的首都是哪个城市？简要回答。"},
    {"cat": "短问答", "prompt": "水的沸点是多少摄氏度？简要回答。"},
    # 长文生成（高 token 量，中文）
    {"cat": "长文生成", "prompt": "请写一篇约 300 字的散文，主题：秋天的雨。"},
    {"cat": "长文生成", "prompt": "请写一篇约 300 字的说明文，主题：如何养成早睡习惯。"},
    # 代码生成（token 高度可预测 → MTP 投机收益大）
    {"cat": "代码生成", "prompt": "用 Python 写一个快速排序函数，包含注释。"},
    {"cat": "代码生成", "prompt": "用 Python 写一个计算斐波那契数列前 30 项的程序。"},
    # 数学推理（中等可预测）
    {"cat": "数学推理", "prompt": "计算 23 × 47，并说明计算过程。"},
    {"cat": "数学推理", "prompt": "若 x² - 5x + 6 = 0，求 x 的所有解，并写出过程。"},
    # 翻译（token 可预测性较高 → MTP 收益中等）
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
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"]
    cmd = [os.path.join(BASE_DIR, "llama-server.exe")] + BASE_ARGS
    if spec_args:
        cmd += spec_args
    args += ["-Command", "& '" + "' '".join(cmd).replace("'", "''", 1) + "'"]
    # 用 Start-Process 脱离句柄，避免 bash 等待
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
        # 提取答案正文（reasoning-format deepseek 时思考在 reasoning_content，正文在 content）
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
    print(f"Qwen3.8-27B MTP/自投机速度对比 · 共 {total} 轮")
    print(f"模型: {MODEL}")
    print(f"基础: KV q8 / 192K / 无 mmproj / reasoning auto")
    print(f"{'='*64}\n")

    results = []
    for i, (label, spec_args) in enumerate(configs, 1):
        stop_server()          # 清残留
        pid = start_server(spec_args)
        model = wait_ready(timeout=240)
        if not model:
            print(f"\r  [{bar}] {pct}% {label} ✗ 启动超时，跳过\n")
            results.append({"label": label, "avg_tps": 0, "tokens": 0, "ms": 0,
                            "per_cat": {}, "per_task": []})
            continue

        # 跑全部 12 题：记录每题，按"总 tokens/总耗时"算平均 tok/s
        task_results = []
        sum_tok = 0
        sum_ms = 0.0
        for j, task in enumerate(TASKS, 1):
            # 进度条：配置进度 + 题进度
            pct = int(((i - 1) * len(TASKS) + (j - 1)) / (total * len(TASKS)) * 100)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r  [{bar}] {pct}% [{i}/{total}] {label} · 题 {j}/{len(TASKS)} [{task['cat']}] ... ",
                  end="", flush=True)
            tps, tok, ms, answer = measure_speed(model, task["prompt"])
            task_results.append({"cat": task["cat"], "tps": tps, "tokens": tok, "ms": ms,
                                 "prompt": task["prompt"], "answer": answer})
            sum_tok += tok
            sum_ms += ms

        stop_server()
        # 平均 tok/s = 总 tokens / 总耗时（比单纯平均 tps 更真实）
        avg_tps = round(sum_tok / (sum_ms / 1000), 1) if sum_ms > 0 and sum_tok > 0 else 0
        # 分任务类均值（每类 2 题的 tps 平均）
        per_cat = {}
        for tr in task_results:
            per_cat.setdefault(tr["cat"], []).append(tr["tps"])
        per_cat = {k: round(sum(v) / len(v), 1) for k, v in per_cat.items()}

        results.append({"label": label, "avg_tps": avg_tps, "tokens": sum_tok,
                        "ms": round(sum_ms, 0), "per_cat": per_cat, "per_task": task_results})
        mark = "✓" if avg_tps > 0 else "✗"
        print(f"\r  [{bar}] 100% {label} {mark} 平均 {avg_tps} t/s ({sum_tok} tokens / {sum_ms:.0f}ms)  "
              f"[{i}/{total}]\n", flush=True)

    # 完成进度条
    print(f"\r  [{'█'*20}] 100% 完成（{total} 轮 × {len(TASKS)} 题）\n")

    # 对比表（平均 + 分任务矩阵）
    print(f"\n{'='*64}")
    print(f"对比结果（平均 tok/s 越高越好）")
    print(f"{'='*64}")
    cats = ["短问答", "长文生成", "代码生成", "数学推理", "翻译", "总结摘要"]
    print(f"  {'配置':<18} {'平均tok/s':>8}  " + "  ".join(f"{c[:2]:>4}" for c in cats))
    print(f"  {'-'*18} {'-'*8}  " + "  ".join(["----"] * len(cats)))
    best = None
    for r in results:
        row = [f"  {r['label']:<18} {r['avg_tps']:>8}"]
        for c in cats:
            v = r["per_cat"].get(c, 0)
            row.append(f"{v:>6}")
        print("  ".join(row))
        if r["avg_tps"] > 0 and (best is None or r["avg_tps"] > best["avg_tps"]):
            best = r
    if best:
        print(f"\n  🏆 最佳: {best['label']} = {best['avg_tps']} t/s（平均）")
    print(f"{'='*64}")

    # 保存报告（含分任务矩阵）
    out_dir = os.path.join(BASE_DIR, "bench_results")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = os.path.join(out_dir, f"spec_speed_{stamp}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Qwen3.8-27B MTP/自投机速度对比（多任务平均）\n\n")
        f.write(f"- 模型: {MODEL}\n- 时间: {stamp}\n- 基础: KV q8 / 192K / 无 mmproj / reasoning auto\n")
        f.write(f"- 题库: 6 类 × 2 题 = 12 题，平均 tok/s = 总 tokens / 总耗时\n\n")
        f.write("| 配置 | 平均tok/s | 总tokens | 总耗时ms |")
        for c in cats:
            f.write(f" {c} |")
        f.write("\n|:-----|:--------|:-------|:-------|")
        for c in cats:
            f.write(":---|")
        f.write("\n")
        for r in results:
            f.write(f"| {r['label']} | {r['avg_tps']} | {r['tokens']} | {r['ms']} |")
            for c in cats:
                f.write(f" {r['per_cat'].get(c, 0)} |")
            f.write("\n")
        if best:
            f.write(f"\n**最佳: {best['label']} = {best['avg_tps']} t/s（平均）**\n")

        # ---- 完整答案记录（每题 prompt + answer，供事后分析输出分布/质量） ----
        f.write("\n---\n\n## 完整答案记录\n\n")
        for r in results:
            f.write(f"### {r['label']}\n\n")
            for k, tr in enumerate(r.get("per_task", []), 1):
                f.write(f"**题 {k} [{tr['cat']}]**（{tr['tokens']} tokens / {tr['ms']:.0f}ms）\n\n")
                f.write(f"- prompt: {tr.get('prompt','')}\n\n")
                ans = tr.get("answer") or "(无输出/失败)"
                f.write(f"- answer:\n\n```text\n{ans}\n```\n\n")
    print(f"\n报告已保存: {md_path}")

    # ---- 结构化答案 JSON（方便程序化分析：按类别/配置统计分布） ----
    json_path = os.path.join(out_dir, f"spec_speed_{stamp}_answers.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "model": MODEL,
            "stamp": stamp,
            "base": "KV q8 / 192K / 无 mmproj / reasoning auto",
            "configs": [
                {
                    "label": r["label"],
                    "avg_tps": r["avg_tps"],
                    "tasks": r.get("per_task", []),
                }
                for r in results
            ],
        }, f, ensure_ascii=False, indent=2)
    print(f"答案记录已保存: {json_path}")


if __name__ == "__main__":
    main()
