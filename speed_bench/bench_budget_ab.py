#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bench_budget_ab.py -- A/B 测速：量化 --reasoning-budget 对 V100 吞吐的影响
聚焦验证 Findings #1: 显式 reasoning-budget 会关闭 backend sampling(每token GPU->CPU logits 传输开销)

仅两档配置(reasoning 均开, n-max 固定 3):
  A: --reasoning on --reasoning-budget 2048   (当前推荐, backend sampling 关闭)
  B: --reasoning on (无 budget, 默认 -1 不限制) (backend sampling 开启, 预期更快)

每档跑 4 类任务(code/factual/json/reasoning)各 n_predict=512 + 1 次 256 预热(不计),
每档开始前杀残留 llama-server, 带进度条。输出 results_budget_ab.csv / summary_budget_ab.txt / runs_budget_ab/.
"""
import os, sys, time, subprocess, json, signal, shutil
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
from pathlib import Path

LLAMA_SERVER = r"E:\llama-win-cuda-12.4-x64\llama-server.exe"
MODEL       = r"E:\models\Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf"
PORT        = 8081
BASE_URL    = f"http://127.0.0.1:{PORT}"
N_PREDICT   = 512
WARMUP      = 256
HERE        = Path(__file__).resolve().parent
RUNS_DIR    = HERE / "runs_budget_ab"
RESULT_CSV  = HERE / "results_budget_ab.csv"
SUMMARY_TXT = HERE / "summary_budget_ab.txt"

COMMON = [
    "-m", MODEL,
    "-ngl", "99",
    "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
    "-c", "81920", "-b", "2048", "-t", "6",
    "--parallel", "1", "--flash-attn", "enabled",
    "--port", str(PORT),
    "--temp", "0.7", "--top-p", "0.9", "--top-k", "20",
    "--min-p", "0.0", "--repeat-penalty", "1.05",
]

# A: 当前推荐(带 budget); B: 不限制 budget
CONFIGS = [
    {"label": "A_budget2048", "extra": ["--reasoning", "on", "--reasoning-budget", "2048",
                                        "--spec-type", "draft-mtp", "--spec-draft-n-max", "3", "--spec-draft-n-min", "1"]},
    {"label": "B_budgetUnbounded", "extra": ["--reasoning", "on",
                                             "--spec-type", "draft-mtp", "--spec-draft-n-max", "3", "--spec-draft-n-min", "1"]},
]

TASKS = [
    ("warmup",   "请用一段连贯、详实的中文，介绍深圳的城市发展与科技创新产业，循序渐进展开。", WARMUP),
    ("code",     "用 Python 写一个快速排序，并附中文注释说明时间复杂度。", N_PREDICT),
    ("factual",  "请列举中国主要的河流及其流经省份，尽量准确完整。", N_PREDICT),
    ("json",     '请输出一个 JSON，包含 5 个虚构用户的 name/age/city 字段，不要多余解释。', N_PREDICT),
    ("reasoning", "一个水池有进水管和出水管，单开进水管 6 小时满，单开出水管 9 小时空，同时开多久满？请给出推理过程。", N_PREDICT),
]

def kill_server():
    try:
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except Exception:
        pass
    time.sleep(2)

def wait_port_free(timeout=20):
    import socket
    for _ in range(timeout):
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=1):
                time.sleep(1); continue
        except Exception:
            return True
    return False

def launch(cfg):
    args = [LLAMA_SERVER] + COMMON + cfg["extra"]
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=0x00000008)
    return p

def health_ok(retry=30):
    import urllib.request
    for _ in range(retry):
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False

def complete(prompt, n_predict):
    import urllib.request
    payload = {
        "prompt": prompt, "n_predict": n_predict,
        "temperature": 0.7, "top_p": 0.9, "top_k": 20,
        "min_p": 0.0, "repeat_penalty": 1.05,
        "cache_prompt": True, "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/completion", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))

def progress(done, total, label, sub=""):
    w = 20
    filled = int(w * done / total)
    bar = "#" * filled + "-" * (w - filled)
    line = f"[{bar}] {done}/{total} {label} {sub}"
    print("\r" + line.lstrip(), end="", flush=True)
    if done == total and not sub:
        print()

def main():
    RUNS_DIR.mkdir(exist_ok=True)
    rows = []
    total_cfgs = len(CONFIGS)
    for ci, cfg in enumerate(CONFIGS, 1):
        print(f"\n=== Config {ci}/{total_cfgs}: {cfg['label']} ===")
        kill_server()
        if not wait_port_free():
            print("WARN: port still busy, forcing"); kill_server(); time.sleep(3)
        p = launch(cfg)
        logp = RUNS_DIR / f"{cfg['label']}.log"
        if not health_ok():
            print(f"  LAUNCH FAILED for {cfg['label']} (health timeout)")
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass
            rows.append({"config": cfg["label"], "task": "LAUNCH_FAIL", "tok_s": float("nan"), "accept": float("nan")})
            continue
        tcount = len(TASKS)
        for ti, (tname, prompt, npred) in enumerate(TASKS, 1):
            try:
                res = complete(prompt, npred)
                t = res.get("timings", {})
                tps = t.get("predicted_per_second", float("nan"))
                acc = res.get("draft", {}).get("acceptance_rate") if "draft" in res else None
            except Exception as e:
                tps, acc = float("nan"), float("nan")
                print(f"  task {tname} ERROR: {e}")
            if tname != "warmup":
                rows.append({"config": cfg["label"], "task": tname, "tok_s": round(tps, 2),
                             "accept": (round(acc, 4) if isinstance(acc, (int, float)) else acc)})
            tag = f"task {ti}/{tcount} {tname}: {tps:.1f} tok/s"
            progress(ti, tcount, cfg["label"], tag)
        try:
            if p.poll() is None:
                p.terminate(); p.wait(timeout=10)
        except Exception:
            try: p.kill()
            except Exception: pass
        kill_server()

    # write csv
    with open(RESULT_CSV, "w", encoding="utf-8") as f:
        f.write("config,task,tok_s,accept\n")
        for r in rows:
            f.write(f"{r['config']},{r['task']},{r['tok_s']},{r['accept']}\n")

    # summary
    import statistics
    def avg(label):
        vals = [r["tok_s"] for r in rows if r["config"] == label and isinstance(r["tok_s"], (int, float)) and not str(r['tok_s']).lower().startswith('nan')]
        return round(statistics.mean(vals), 2) if vals else float("nan")
    a, b = avg("A_budget2048"), avg("B_budgetUnbounded")
    delta = (round(b - a, 2) if isinstance(a,(int,float)) and isinstance(b,(int,float)) else float("nan"))
    pct = (round((b - a) / a * 100, 1) if isinstance(a,(int,float)) and a else float("nan"))
    with open(SUMMARY_TXT, "w", encoding="utf-8") as f:
        f.write("=== bench_budget_ab summary ===\n")
        f.write(f"A (budget=2048, backend OFF):  mean {a} tok/s\n")
        f.write(f"B (budget unbounded, backend ON): mean {b} tok/s\n")
        f.write(f"Delta B-A: {delta} tok/s  ({pct}%)\n")
        if isinstance(pct,(int,float)) and pct > 0:
            f.write("=> 去掉显式 reasoning-budget 更快，建议改启动器默认\n")
        elif isinstance(pct,(int,float)) and pct < 0:
            f.write("=> 保留 budget=2048 更快，维持现状\n")
        else:
            f.write("=> 两档持平，差异不显著\n")
    print("\n\n=== SUMMARY ===")
    print(f"A budget=2048 mean : {a} tok/s")
    print(f"B unbounded   mean : {b} tok/s")
    print(f"Delta B-A          : {delta} tok/s ({pct}%)")
    print(f"csv : {RESULT_CSV}")
    print(f"txt : {SUMMARY_TXT}")

if __name__ == "__main__":
    main()
