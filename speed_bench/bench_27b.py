# -*- coding: utf-8 -*-
"""
bench_27b.py - Qwen3.6-27B-MTP-IQ4_XS-Q8nextn 输出速度系统化测速

模型特性（已用 inspect_gguf 校验）：
  - 密集混合模型（非 MoE）：65 层，embedding 5120，含 SSM(Mamba) 层，
    每 4 层一次全注意力。训练上下文 262144(256K)，无专家路由。
  - 权重 15.4GB(IQ4_XS) + 内置 Q8 nextn 草稿头(MTP)。
  - 关键差异 vs 35B(MoE, 激活仅 3B)：27B 每 token 跑完整 27B 前向，
    目标前向昂贵 → MTP 草稿开销相对便宜 → 比 MoE 更耐受高 n-max。
    所以本脚本把 n-max 扫描到 8，并额外探 10，找密集模型的真实甜点。

与 bench_v2.py 同结构：5 类任务 × 长生成(n_predict=1024) × 宽配置网格，
进度条 + 每配置前杀残留 llama-server + 自动探驱动模式(WDDM/TCC)。
输出：results_27b.csv / summary_27b.txt / runs_27b/<label>.log / bench_27b_run.log
"""
import os
import sys
import json
import time
import csv
import argparse
import subprocess
import urllib.request

EXE = "E:/llama-win-cuda-12.4-x64/llama-server.exe"
MODEL = "E:/models/Qwen3.6-27B-MTP-IQ4_XS-Q8nextn.gguf"
HOST = "127.0.0.1"
PORT = 8081
NGL = 99
CTX_DEFAULT = 81920          # 一致基准；27B 256K 训练上下文，98304 也轻松装下
THREADS = 6
CACHE_K = "q8_0"
CACHE_V = "q8_0"
CREATE_NO_WINDOW = 0x00000008

# 5 类任务，覆盖不同生成分布（MTP 接受率不同，速度会有差异）
TASKS = [
    {"name": "coherent_long", "prompt": "请写一篇关于“深海生态系统”的科普长文，不少于800字，描述生物多样性与能量流动。"},
    {"name": "code",          "prompt": "用Python实现一个带LRU缓存的线程安全字典类，包含get/set/pop方法，并写使用示例。"},
    {"name": "factual",       "prompt": "用三句话解释量子纠缠是什么，以及它和通信的关系。"},
    {"name": "json",          "prompt": "输出一个JSON，描述一支虚构的技术团队，包含name、members(数组，每人有name/role/skills)、project字段。"},
    {"name": "reasoning",     "prompt": "一个水池，甲管单独注满需6小时，乙管单独注满需4小时，两管齐开同时注水，多久注满？请给出推理过程。"},
]

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(HERE, "runs_27b")
RESULTS = os.path.join(HERE, "results_27b.csv")
SUMMARY = os.path.join(HERE, "summary_27b.txt")
MASTER_LOG = os.path.join(HERE, "bench_27b_run.log")


def log(msg, master=None):
    line = msg
    print(line, flush=True)
    if master is not None:
        try:
            master.write(line + "\n")
            master.flush()
        except Exception:
            pass


def build_configs():
    cfgs = []
    def add(label, reasoning, nmax, batch=2048, flash="enabled", ctx=CTX_DEFAULT):
        cfgs.append(dict(label=label, reasoning=reasoning, nmax=nmax,
                         batch=batch, flash=flash, ctx=ctx))
    # 主网格：reasoning x MTP n-max 全扫描（密集模型耐受高 n-max，扫到 8）
    for r in ("on", "off"):
        for n in (None, 2, 3, 4, 5, 6, 8):
            nm = "off" if n is None else f"n{n}"
            add(f"r{r}_mtp{nm}", r, n)
    # 密集模型专属探针 / 杠杆变体
    add("ron_mtp10", "on", 10)                 # 探更高 n-max（MoE 会崩，密集或许可忍）
    add("ron_mtp4_c98304", "on", 4, ctx=98304) # 启动器原生 27B 上下文
    add("ron_mtp4_c32768", "on", 4, ctx=32768) # 更小 KV，释放显存压力
    add("rnative", "off", 2)                   # 完全对齐启动器 MTP-XS 默认：思考关 n-max=2
    return cfgs


def server_args(cfg):
    a = [EXE, "-ngl", str(NGL), "-c", str(cfg["ctx"]),
         "-b", str(cfg["batch"]), "--ubatch-size", str(cfg["batch"]),
         "--flash-attn", cfg["flash"],
         "--cache-type-k", CACHE_K, "--cache-type-v", CACHE_V,
         "-t", str(THREADS), "--api-key", "llamacpp",
         "--host", HOST, "--port", str(PORT), "-m", MODEL]
    if cfg["reasoning"] == "on":
        a += ["--reasoning", "on", "--reasoning-budget", "2048"]
    else:
        a += ["--reasoning", "off"]
    if cfg["nmax"] is not None:
        a += ["--spec-type", "draft-mtp",
              "--spec-draft-n-max", str(cfg["nmax"]),
              "--spec-draft-n-min", "1"]
    return a


def kill_server():
    try:
        subprocess.run(["taskkill", "/IM", "llama-server.exe", "/F"],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def wait_port_free(timeout=20):
    url = f"http://{HOST}:{PORT}/health"
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            time.sleep(0.5)
        except Exception:
            return True
    return False


def wait_health(timeout=180):
    url = f"http://{HOST}:{PORT}/health"
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def complete(prompt, n_predict):
    url = f"http://{HOST}:{PORT}/completion"
    data = json.dumps({
        "prompt": prompt,
        "n_predict": n_predict,
        "temperature": 0,
        "top_p": 1,
        "top_k": 1,
        "stream": False,
        "cache_prompt": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer llamacpp"},
        method="POST")
    last_err = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise last_err


def get_driver_mode():
    try:
        out = subprocess.run(["nvidia-smi", "-q"],
                             capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if "Driver Model" in line:
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "unknown"


def progress_bar(done, total, width=28):
    frac = done / total if total else 0
    filled = int(width * frac)
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {done}/{total} ({frac*100:.0f}%)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个配置（冒烟测试用）")
    ap.add_argument("--skip", type=int, default=0, help="跳过前 N 个配置")
    ap.add_argument("--predict", type=int, default=1024, help="每个任务的生成 token 数")
    args = ap.parse_args()

    n_predict = args.predict
    cfgs = build_configs()
    if args.skip:
        cfgs = cfgs[args.skip:]
    if args.limit:
        cfgs = cfgs[:args.limit]

    os.makedirs(RUNS_DIR, exist_ok=True)
    master = open(MASTER_LOG, "w", encoding="utf-8")
    rows = []
    per_config = {}

    dm = get_driver_mode()
    log(f"Driver model: {dm}", master)
    log(f"Configs: {len(cfgs)} | n_predict={n_predict} | tasks={len(TASKS)}", master)
    log("=" * 64, master)

    total = len(cfgs)
    for ci, cfg in enumerate(cfgs):
        label = cfg["label"]
        log(progress_bar(ci, total) + f"  launching {label} "
            f"(reasoning={cfg['reasoning']} nmax={cfg['nmax']} "
            f"batch={cfg['batch']} flash={cfg['flash']} ctx={cfg['ctx']})", master)

        log_path = os.path.join(RUNS_DIR, f"{label}.log")
        kill_server()
        wait_port_free()

        with open(log_path, "w", encoding="utf-8") as logf:
            proc = subprocess.Popen(
                server_args(cfg), stdout=logf, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW)
            try:
                ok = wait_health()
                if not ok:
                    kill_server()
                    wait_port_free()
                    proc = subprocess.Popen(
                        server_args(cfg), stdout=logf, stderr=subprocess.STDOUT,
                        creationflags=CREATE_NO_WINDOW)
                    ok = wait_health()
                if not ok:
                    log(f"  !! {label}: 启动失败（health 超时），见 {log_path}", master)
                    rows.append(dict(config=label, task="(failed)", tok_s=None,
                                     reasoning=cfg["reasoning"], nmax=cfg["nmax"],
                                     batch=cfg["batch"], flash=cfg["flash"], ctx=cfg["ctx"]))
                    continue
                time.sleep(2)
                per_config[label] = []
                nt = len(TASKS)
                for ti, task in enumerate(TASKS):
                    is_warmup = (ti == 0)
                    np_ = 256 if is_warmup else n_predict
                    try:
                        resp = complete(task["prompt"], np_)
                    except Exception as e:
                        log(f"  [{ti+1}/{nt}] {task['name']}: ERROR {e}", master)
                        continue
                    spd = None
                    if resp and "timings" in resp and "predicted_per_second" in resp["timings"]:
                        spd = resp["timings"]["predicted_per_second"]
                    if is_warmup:
                        log(f"  [warmup] {task['name']}: {spd}", master)
                        continue
                    log(progress_bar(ci + 1, total) +
                        f"  {label} | task {ti+1}/{nt} {task['name']}: {spd} tok/s", master)
                    rows.append(dict(config=label, task=task["name"], tok_s=spd,
                                     reasoning=cfg["reasoning"], nmax=cfg["nmax"],
                                     batch=cfg["batch"], flash=cfg["flash"], ctx=cfg["ctx"]))
                    if spd is not None:
                        per_config[label].append(spd)
            finally:
                try:
                    proc.terminate()
                except Exception:
                    pass
                kill_server()
                wait_port_free()

    with open(RESULTS, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["config", "task", "tok_s",
                                          "reasoning", "nmax", "batch", "flash", "ctx"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    summary = []
    for label, speeds in per_config.items():
        if speeds:
            summary.append((label, sum(speeds)/len(speeds), max(speeds), min(speeds)))
    summary.sort(key=lambda x: x[1], reverse=True)

    with open(SUMMARY, "w", encoding="utf-8") as f:
        f.write("llama.cpp 输出速度测速汇总 (27B)\n")
        f.write("模型: Qwen3.6-27B-MTP-IQ4_XS-Q8nextn.gguf (密集混合, 65层, 内置Q8 nextn MTP)\n")
        f.write(f"GPU: Tesla V100-PCIE-32GB | 驱动模式: {dm}\n")
        f.write(f"生成长度: n_predict={n_predict} | 任务数: {len(TASKS)} | 配置数: {total}\n")
        f.write("测量: /completion timings.predicted_per_second (解码 tok/s)\n")
        f.write("=" * 64 + "\n\n")
        f.write(f"{'排名':<4}{'配置':<18}{'平均':>10}{'最高':>10}{'最低':>10}\n")
        f.write("-" * 52 + "\n")
        for i, (label, avg, mx, mn) in enumerate(summary, 1):
            f.write(f"{i:<4}{label:<18}{avg:>9.2f}{mx:>10.2f}{mn:>10.2f}\n")
        f.write("\n（ron_=思考开 roff_=思考关 | mtpN=MTP n-max=N | mtpoff=关MTP | "
                "cN=ctx | rnative=启动器默认 思考关 n-max=2）\n")
        if not summary:
            f.write("（无有效结果）\n")

    log("\n" + "=" * 64, master)
    log(f"完成。驱动模式={dm} | 结果: {RESULTS} | 汇总: {SUMMARY}", master)
    if summary:
        top = summary[0]
        log(f"本轮峰值: {top[0]} = 平均 {top[1]:.2f} tok/s (单任务最高 {top[2]:.2f})", master)
    master.close()


if __name__ == "__main__":
    main()
