# -*- coding: utf-8 -*-
"""
bench_vl.py - Qwen3VL-8B-Instruct-Q4_K_M 输出速度系统化测速

模型特性（已用 inspect_gguf 校验）：
  - qwen3vl 架构：8B 密集模型（非 MoE），32 层，embedding 4096，
    head_count=32 / kv=8 / key_length=128，训练上下文 262144(256K)。
  - 权重 4.7GB(Q4_K_M) + 视觉投影 mmproj-Qwen3VL-8B-Instruct-F16(1.16GB)。
  - 无 MTP、无 reasoning（VL 不支持思考）→ 速度杠杆只剩：
      flash-attn 开关 / KV 缓存类型(q8_0 vs f16) / batch 大小 / ctx 长度。
  - 8B 很小 → 理论上限（900GB/s ÷ 4.7GB ≈ 190 tok/s）远高于 35B/27B，
    真正的瓶颈是 kernel 开销/batch/attention 实现，故重点扫这几个杠杆。

⚠️ 关键修正（v2）：Qwen3VL-8B 上 flash-attn 必须开。
   证据见 runs_vl/：开 flash → 84~92 tok/s；关 flash 时
     - q8_0 KV 直接崩溃起不来 ("V cache quantization requires flash_attn")
     - f16 KV 能起但暴跌到 17 tok/s (5x 慢)
   故 flash-off 配置全部剔除；并加启动期 fatal 签名快速失败，
   避免无效配置干等 health 超时（之前"超时"根因）。

输出：results_vl.csv / summary_vl.txt / runs_vl/<label>.log / bench_vl_run.log
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
MODEL = "E:/models/Qwen3VL-8B-Instruct-Q4_K_M.gguf"
MMPROJ = "E:/models/mmproj-Qwen3VL-8B-Instruct-F16.gguf"
HOST = "127.0.0.1"
PORT = 8081
NGL = 99
CTX_DEFAULT = 98304          # 启动器默认 96K；训练上下文 256K，32768 也轻松
THREADS = 6
CREATE_NO_WINDOW = 0x00000008

# 4 类纯文本任务（测解码 tok/s；VL 的图文路径不影响纯文本 decode 速度）
TASKS = [
    {"name": "coherent_long", "prompt": "请写一篇关于“深海生态系统”的科普长文，不少于800字，描述生物多样性与能量流动。"},
    {"name": "code",          "prompt": "用Python实现一个带LRU缓存的线程安全字典类，包含get/set/pop方法，并写使用示例。"},
    {"name": "factual",       "prompt": "用三句话解释量子纠缠是什么，以及它和通信的关系。"},
    {"name": "json",          "prompt": "输出一个JSON，描述一支虚构的技术团队，包含name、members(数组，每人有name/role/skills)、project字段。"},
]

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(HERE, "runs_vl")
RESULTS = os.path.join(HERE, "results_vl.csv")
SUMMARY = os.path.join(HERE, "summary_vl.txt")
MASTER_LOG = os.path.join(HERE, "bench_vl_run.log")


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
    """VL 无 MTP/无 reasoning：仅扫 flash=on 下的 KV类型 x batch x ctx。

    ⚠️ flash-attn 必须开（关 flash 时 q8_0 KV 崩溃、f16 KV 暴跌 5x），
    故 flash-off 配置全部剔除。证据见 runs_vl/ 历史日志。
    """
    cfgs = []
    def add(label, flash, kv, batch=2048, ctx=CTX_DEFAULT):
        cfgs.append(dict(label=label, flash=flash, kv=kv,
                         batch=batch, ctx=ctx))
    # baseline = 启动器当前默认（flash=on + q8_0 + b2048）
    add("def_flash_q8_b2048",    "enabled", "q8_0", 2048)
    add("flash_f16_b2048",       "enabled", "f16", 2048)
    add("flash_q8_b4096",        "enabled", "q8_0", 4096)
    add("flash_f16_b4096",       "enabled", "f16", 4096)
    add("flash_q8_b1024",        "enabled", "q8_0", 1024)          # 小 batch 探针
    add("flash_q8_b2048_c32768", "enabled", "q8_0", 2048, 32768)    # 小 ctx 探针
    add("flash_f16_b2048_c32768","enabled", "f16", 2048, 32768)
    return cfgs


# 服务启动期 fatal 签名：出现即代表该配置无法启动，立即跳过（不再干等 health 超时）
FATAL_SIGNATURES = [
    "V cache quantization requires flash_attn",
    "failed to create llama_context",
    "failed to create context",
    "common_init_: failed",
]


def _log_has_fatal(log_path):
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8000)
        for sig in FATAL_SIGNATURES:
            if sig in head:
                return True
    except Exception:
        pass
    return False


def server_args(cfg):
    a = [EXE, "-ngl", str(NGL), "-c", str(cfg["ctx"]),
         "-b", str(cfg["batch"]), "--ubatch-size", str(cfg["batch"]),
         "--flash-attn", cfg["flash"],
         "--cache-type-k", cfg["kv"], "--cache-type-v", cfg["kv"],
         "-t", str(THREADS), "--api-key", "llamacpp",
         "--host", HOST, "--port", str(PORT), "-m", MODEL,
         "--mmproj", MMPROJ]
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


def wait_health(timeout=180, log_path=None):
    url = f"http://{HOST}:{PORT}/health"
    t0 = time.time()
    while time.time() - t0 < timeout:
        if log_path and _log_has_fatal(log_path):
            return False
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
            f"(flash={cfg['flash']} kv={cfg['kv']} "
            f"batch={cfg['batch']} ctx={cfg['ctx']})", master)

        log_path = os.path.join(RUNS_DIR, f"{label}.log")
        kill_server()
        wait_port_free()

        with open(log_path, "w", encoding="utf-8") as logf:
            proc = subprocess.Popen(
                server_args(cfg), stdout=logf, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW)
            try:
                ok = wait_health(log_path=log_path)
                if not ok:
                    # 快速失败：若日志已出现 fatal 签名，无需重试
                    if _log_has_fatal(log_path):
                        log(f"  !! {label}: 启动失败（fatal 签名），见 {log_path}", master)
                    else:
                        kill_server()
                        wait_port_free()
                        proc = subprocess.Popen(
                            server_args(cfg), stdout=logf, stderr=subprocess.STDOUT,
                            creationflags=CREATE_NO_WINDOW)
                        ok = wait_health(log_path=log_path)
                    if not ok:
                        log(f"  !! {label}: 启动失败（health 超时），见 {log_path}", master)
                        rows.append(dict(config=label, task="(failed)", tok_s=None,
                                         flash=cfg["flash"], kv=cfg["kv"],
                                         batch=cfg["batch"], ctx=cfg["ctx"]))
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
                                     flash=cfg["flash"], kv=cfg["kv"],
                                     batch=cfg["batch"], ctx=cfg["ctx"]))
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
                                          "flash", "kv", "batch", "ctx"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    summary = []
    for label, speeds in per_config.items():
        if speeds:
            summary.append((label, sum(speeds)/len(speeds), max(speeds), min(speeds)))
    summary.sort(key=lambda x: x[1], reverse=True)

    with open(SUMMARY, "w", encoding="utf-8") as f:
        f.write("llama.cpp 输出速度测速汇总 (Qwen3VL-8B)\n")
        f.write("模型: Qwen3VL-8B-Instruct-Q4_K_M.gguf (8B 密集, 32层, 无MTP/无reasoning)\n")
        f.write("视觉投影: mmproj-Qwen3VL-8B-Instruct-F16.gguf\n")
        f.write(f"GPU: Tesla V100-PCIE-32GB | 驱动模式: {dm}\n")
        f.write(f"生成长度: n_predict={n_predict} | 任务数: {len(TASKS)} | 配置数: {total}\n")
        f.write("测量: /completion timings.predicted_per_second (解码 tok/s)\n")
        f.write("=" * 64 + "\n\n")
        f.write(f"{'排名':<4}{'配置':<24}{'平均':>10}{'最高':>10}{'最低':>10}\n")
        f.write("-" * 60 + "\n")
        for i, (label, avg, mx, mn) in enumerate(summary, 1):
            f.write(f"{i:<4}{label:<24}{avg:>9.2f}{mx:>10.2f}{mn:>10.2f}\n")
        f.write("\n（def_=启动器默认 flash=on kv=q8_0 b=2048 | _f16=KV用f16 | _bN= batch=N | _c32768= ctx=32768）\n")
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
