# -*- coding: utf-8 -*-
"""
========================================================================================
  🚀 深度速度评测与 MTP/上下文极限参数调优流水线 (Speed Benchmark Matrix v2.0)
  • 专用于 Qwen3-Coder-30B-A3B 与 Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp 的极限性能实测
  • 30B-A3B (原生 MoE 架构，无 MTP 头): 深度测试全上下文梯度 (8K..192K) 与 KV Cache 精度
  • 27B-GSQ (密集架构，内置 MTP 头): 深度扫描 MTP 阶数 (off, n=1..4)、p-min 阈值、槽位与上下文
  • 严格遵循 AGENTS.md 规范：纯 Python 原生多进程治理、GPU 显存清理监测、时间戳防覆盖
========================================================================================
"""

import os
import sys
import json
import time
import csv
import re
import argparse
import datetime
import subprocess
import urllib.request
import urllib.error

# 强制 UTF-8 标准输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 基础目录与执行文件定位
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
EXE = os.path.join(BASE_DIR, "llama-server.exe")
MODELS_DIR = r"E:\models" if os.path.exists(r"E:\models") else os.path.join(BASE_DIR, "models")
TEMPLATE_FILE = os.path.join(BASE_DIR, "chat_template_qwen_fixed.jinja")

HOST = "127.0.0.1"
PORT = 8081

# 模型定义注册表
MODELS_REGISTRY = {
    "30b": {
        "name": "Qwen3-Coder-30B-A3B-Instruct-UD-Q5_K_XL",
        "alias": "Qwen3-Coder-30B-A3B",
        "path": os.path.join(MODELS_DIR, "Qwen3-Coder-30B-A3B-Instruct-UD-Q5_K_XL.gguf"),
        "desc": "30.5B MoE 编程专家 (3.3B 激活, UD-Q5_K_XL 高精动态量化, 原生无 MTP 头)",
        "default_ctx": 196608,
        "has_mtp": False,
        "is_moe": True,
    },
    "27b": {
        "name": "Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp",
        "alias": "Qwen3.8-27B-GSQ-IQ3_S",
        "path": os.path.join(MODELS_DIR, "Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp.gguf"),
        "desc": "27B 广义切片量化 (无损级 3-bit, 密集模型, 内置 MTP 头)",
        "default_ctx": 262144,
        "has_mtp": True,
        "is_moe": False,
    },
}

# 评测标准 Prompt 集（包含代码长文生成、深度逻辑推理与系统架构）
TASKS = [
    {
        "name": "code_lru",
        "prompt": (
            "请用 Python 3.12 实现一个生产级高性能、线程安全的 LRU 缓存类（支持泛型类型提示、"
            "TTL 绝对过期与滑动过期双模式、淘汰事件回调钩子、最大容量限制），"
            "并编写包含并发读写、高压淘汰测试的完整 unittest 测试套件与详细架构设计说明。"
        ),
    },
    {
        "name": "reasoning_train",
        "prompt": (
            "甲乙两列高速列车分别从相距 1200 公里的 A、B 两城同时相对开出。甲车以时速 240 公里匀速行驶，"
            "乙车以时速 160 公里匀速行驶。一架高速无人机以时速 400 公里在两车之间不间断往返巡航"
            "（从甲车车头飞向乙车车头，一接触即瞬间掉头飞向甲车，如此往复直到两车相遇）。"
            "请问：1. 无人机在两车相遇前总共飞行了多少公里？ 2. 无人机在相遇前最后一秒内的朝向是什么？"
            "请给出极其严密的微积分或代数证明与物理轨迹极限分析。"
        ),
    },
    {
        "name": "tech_arch",
        "prompt": (
            "请撰写一份针对万亿级参数大模型分布式混合并行训练（结合 3D 并行 Tensor/Pipeline/Data、"
            "ZeRO-3 显存优化、FlashAttention-3 算子融合与 InfiniBand 网络拥塞控制）的万字系统架构设计文档核心摘要，"
            "列出 5 个关键瓶颈及其数学建模与工程解决方案。"
        ),
    },
]

RUNS_DIR = os.path.join(SCRIPT_DIR, "runs_speed_matrix")
os.makedirs(RUNS_DIR, exist_ok=True)


def log(msg, master=None):
    """同时输出到控制台并追加写入日志文件"""
    ts = datetime.datetime.now().strftime("[%H:%M:%S] ")
    line = ts + msg
    print(line, flush=True)
    if master:
        try:
            master.write(line + "\n")
            master.flush()
        except Exception:
            pass


def get_gpu_memory():
    """获取当前 GPU 显存占用 (MiB)"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        if out:
            return int(out.splitlines()[0].strip())
    except Exception:
        pass
    return -1


def kill_all_llama(master=None):
    """严格遵循 AGENTS.md 规范：彻底终结 llama-server 及残留进程，循环检查显存 < 500MB"""
    # 1. 杀死 llama 进程
    try:
        subprocess.run(["taskkill", "/IM", "llama-server.exe", "/F"], capture_output=True, timeout=5)
    except Exception:
        pass

    # 2. 释放端口
    for p in (8081, 8082, 8083, 8084, 8085):
        try:
            res = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
            for line in res.stdout.splitlines():
                if f":{p} " in line and "LISTENING" in line:
                    parts = line.strip().split()
                    pid = parts[-1]
                    if pid.isdigit() and int(pid) > 0 and int(pid) != os.getpid():
                        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=5)
        except Exception:
            pass

    # 3. 循环等待显存释放 (< 500MB)
    t0 = time.time()
    while time.time() - t0 < 30:
        mem = get_gpu_memory()
        if mem >= 0 and mem < 500:
            break
        time.sleep(0.5)

    mem_final = get_gpu_memory()
    if mem_final > 500:
        log(f"⚠️ 提示: 当前 GPU 显存占用 {mem_final} MiB (未低于 500MB)", master)


def wait_server_ready(proc, host=HOST, port=PORT, timeout=120, log_path=None, master=None):
    """等待 llama-server 端口与 health 检查就绪"""
    url = f"http://{host}:{port}/health"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer llamacpp"})
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc and proc.poll() is not None:
            log(f"  ❌ llama-server 进程异常退出 (exit code={proc.returncode})", master)
            return False

        if log_path and os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    txt = f.read()
                    if "CUDA out of memory" in txt or "out of memory" in txt.lower():
                        log("  ❌ 显存不足 (CUDA OOM)！", master)
                        return False
                    if "failed to create llama_context" in txt or "failed to create context" in txt:
                        log("  ❌ 上下文创建失败 (Context allocation failed)！", master)
                        return False
                    if "error loading model" in txt.lower():
                        log("  ❌ 模型加载失败！", master)
                        return False
            except Exception:
                pass

        try:
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def build_configs_for_model(model_key, suite="matrix"):
    """
    根据模型架构特征与评测套件，生成定制测试矩阵：
    """
    cfgs = []

    def add(label, nmax, ctx, ctk="q8_0", ctv="q8_0", pmin=0.0, parallel=1, desc=""):
        cfgs.append({
            "label": label,
            "nmax": nmax,
            "ctx": ctx,
            "ctk": ctk,
            "ctv": ctv,
            "pmin": pmin,
            "parallel": parallel,
            "desc": desc,
        })

    # =========================================================================
    # 1. Qwen3-Coder-30B-A3B (MoE 稀疏激活, 无 MTP 头)
    # =========================================================================
    if model_key == "30b":
        if suite == "smoke":
            add("smoke_ctx32k", None, 32768, desc="冒烟-标准 32K 上下文 (MTP关)")
            return cfgs

        # A. 上下文长度阶梯扫描 (8K -> 192K，探究上下文对单 token 速度与显存的影响)
        add("ctx_008k_q8", None, 8192,   ctk="q8_0", ctv="q8_0", desc="极限理论裸速 (ctx=8K, 全显存极速)")
        add("ctx_016k_q8", None, 16384,  ctk="q8_0", ctv="q8_0", desc="高频对话裸速 (ctx=16K)")
        add("ctx_032k_q8", None, 32768,  ctk="q8_0", ctv="q8_0", desc="标准编程基准 (ctx=32K)")
        add("ctx_064k_q8", None, 65536,  ctk="q8_0", ctv="q8_0", desc="中长代码基准 (ctx=64K)")
        add("ctx_096k_q8", None, 98304,  ctk="q8_0", ctv="q8_0", desc="大长文本基准 (ctx=96K)")
        add("ctx_128k_q8", None, 131072, ctk="q8_0", ctv="q8_0", desc="超长上下文 (ctx=128K)")
        add("ctx_192k_q8", None, 196608, ctk="q8_0", ctv="q8_0", desc="生产默认最大池 (ctx=192K, Q8_0 KV)")

        # B. KV Cache 精度对比 (在 64K 上下文下对比性能与显存)
        add("ctx_064k_f16", None, 65536, ctk="f16",  ctv="f16",  desc="全精度 KV Cache (ctx=64K, F16 KV)")
        add("ctx_064k_q4",  None, 65536, ctk="q4_0", ctv="q4_0", desc="省显存 KV Cache (ctx=64K, Q4_0 KV)")
        return cfgs

    # =========================================================================
    # 2. Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp (密集模型, 3-bit 量化, 内置 MTP 头)
    # =========================================================================
    if model_key == "27b":
        if suite == "smoke":
            add("smoke_mtp_off", None, 32768, desc="冒烟-基准裸速 (MTP关, ctx=32K)")
            add("smoke_mtp_n2",  2,    32768, desc="冒烟-MTP n=2 (ctx=32K)")
            return cfgs

        # A. 基准裸速 (不同上下文)
        add("baseline_ctx32k_mtp_off", None, 32768,  desc="基准裸速 (ctx=32K, MTP关)")
        add("baseline_ctx96k_mtp_off", None, 98304,  desc="中长裸速 (ctx=96K, MTP关)")
        add("baseline_ctx256k_mtp_off", None, 262144, desc="极限裸速 (ctx=256K, MTP关)")

        # B. MTP 阶数扫描 (在 ctx=32K 下锁定最佳甜点)
        add("mtp_n1_ctx32k", 1, 32768, desc="MTP n-max=1 (投机1步)")
        add("mtp_n2_ctx32k", 2, 32768, desc="MTP n-max=2 (投机2步)")
        add("mtp_n3_ctx32k", 3, 32768, desc="MTP n-max=3 (投机3步)")
        add("mtp_n4_ctx32k", 4, 32768, desc="MTP n-max=4 (投机4步)")

        # C. 置信度截断 (p-min 阈值)
        add("mtp_n2_p0.75_ctx32k", 2, 32768, pmin=0.75, desc="MTP n=2, p-min=0.75 (过滤低置信)")
        add("mtp_n3_p0.75_ctx32k", 3, 32768, pmin=0.75, desc="MTP n=3, p-min=0.75")
        add("mtp_n3_p0.85_ctx32k", 3, 32768, pmin=0.85, desc="MTP n=3, p-min=0.85 (高置信)")

        # D. 生产环境大上下文下的 MTP 实测 (ctx=96K 与 ctx=256K)
        add("prod_ctx96k_mtp_n2", 2, 98304, desc="生产推荐环境 (ctx=96K, MTP n=2)")
        add("prod_ctx256k_mtp_n2", 2, 262144, desc="生产极限环境 (ctx=256K, MTP n=2)")
        add("prod_ctx256k_mtp_n3_p0.75", 3, 262144, pmin=0.75, desc="生产极限高阶 (ctx=256K, MTP n=3, p0.75)")
        return cfgs

    return cfgs


def server_args(model_info, cfg):
    """根据模型与测试配置构建 llama-server 启动参数"""
    a = [
        EXE,
        "-m", model_info["path"],
        "-ngl", "99",
        "-c", str(cfg["ctx"]),
        "-b", "2048",
        "--ubatch-size", "2048",
        "--flash-attn", "on",
        "--cache-type-k", cfg.get("ctk", "q8_0"),
        "--cache-type-v", cfg.get("ctv", "q8_0"),
        "-t", "6",
        "--parallel", str(cfg.get("parallel", 1)),
        "--api-key", "llamacpp",
        "--host", HOST,
        "--port", str(PORT),
        "--alias", model_info["alias"],
        "--temp", "0.2",
        "--top-p", "0.95",
        "--top-k", "20",
        "--min-p", "0.05",
        "--repeat-penalty", "1.05",
        "--no-warmup",
    ]

    if os.path.exists(TEMPLATE_FILE):
        a += ["--jinja", "--chat-template-file", TEMPLATE_FILE]

    # MTP 投机参数 (仅在模型本身支持且配置开启时挂载)
    if cfg.get("nmax") is not None and cfg["nmax"] > 0:
        a += [
            "--spec-type", "draft-mtp",
            "--spec-draft-n-max", str(cfg["nmax"]),
            "--spec-draft-n-min", "1",
        ]
        if cfg.get("pmin", 0.0) > 0:
            a += ["--spec-draft-p-min", str(cfg["pmin"])]

    return a


def send_completion_benchmark(prompt, n_predict=512, timeout=240):
    """向 llama-server 发送评测请求，获取端到端耗时、Prompt 吞吐、生成吞吐及 token 计数"""
    url = f"http://{HOST}:{PORT}/completion"
    data = json.dumps({
        "prompt": prompt,
        "n_predict": n_predict,
        "temperature": 0.2,
        "top_p": 0.95,
        "stream": False,
        "cache_prompt": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer llamacpp"},
        method="POST",
    )

    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        t_wall = time.time() - t0
        res = json.loads(resp.read().decode("utf-8"))

    # 从 llama.cpp 返回中解析统计指标
    timings = res.get("timings", {})
    prompt_n = timings.get("prompt_n", 0)
    prompt_ms = timings.get("prompt_ms", 0.0)
    prompt_per_second = timings.get("prompt_per_second", 0.0)
    if prompt_per_second <= 0 and prompt_ms > 0:
        prompt_per_second = (prompt_n / prompt_ms) * 1000.0

    predicted_n = timings.get("predicted_n", 0)
    predicted_ms = timings.get("predicted_ms", 0.0)
    predicted_per_second = timings.get("predicted_per_second", 0.0)
    if predicted_per_second <= 0 and predicted_ms > 0:
        predicted_per_second = (predicted_n / predicted_ms) * 1000.0

    # 若无 timings 则根据总耗时折算
    if predicted_per_second <= 0 and t_wall > 0:
        predicted_per_second = predicted_n / t_wall

    return {
        "content_len": len(res.get("content", "")),
        "prompt_n": prompt_n,
        "prompt_tps": round(prompt_per_second, 2),
        "predicted_n": predicted_n,
        "predicted_tps": round(predicted_per_second, 2),
        "wall_time_s": round(t_wall, 2),
    }


def run_benchmark(model_key, suite="matrix", n_predict=512, dry_run=False):
    """执行完整的测速矩阵任务"""
    if model_key not in MODELS_REGISTRY:
        print(f"❌ 未知模型键名: {model_key}，可用值: {list(MODELS_REGISTRY.keys())}")
        return

    model_info = MODELS_REGISTRY[model_key]
    model_name = model_info["name"]
    model_path = model_info["path"]

    if not os.path.exists(model_path):
        print(f"❌ 模型文件不存在: {model_path}")
        return

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = model_info["alias"].replace(".", "_")

    log_path = os.path.join(SCRIPT_DIR, f"bench_speed_{safe_name}_{ts}.log")
    csv_path = os.path.join(SCRIPT_DIR, f"results_speed_{safe_name}_{ts}.csv")
    txt_path = os.path.join(SCRIPT_DIR, f"summary_speed_{safe_name}_{ts}.txt")
    report_path = os.path.join(SCRIPT_DIR, f"report_speed_{safe_name}_{ts}.md")

    master_fp = open(log_path, "w", encoding="utf-8")

    log("=" * 80, master_fp)
    log(f"🚀 AI 模型极限速度与 MTP/上下文基准评测流水线", master_fp)
    log(f"• 模型: {model_name}", master_fp)
    log(f"• 架构: {'MoE 稀疏激活 (~3.3B Active)' if model_info['is_moe'] else 'Dense 密集计算 (27B 全参)'}", master_fp)
    log(f"• 路径: {model_path} ({os.path.getsize(model_path) / (1024**3):.2f} GB)", master_fp)
    log(f"• 模式: {suite} | 单任务预测 token 数: {n_predict}", master_fp)
    log("=" * 80, master_fp)

    cfgs = build_configs_for_model(model_key, suite=suite)
    log(f"📋 共规划 {len(cfgs)} 组测试配置，正在启动逐项实测...\n", master_fp)

    if dry_run:
        log("=== DRY RUN 预览参数 ===", master_fp)
        for i, c in enumerate(cfgs, 1):
            args = server_args(model_info, c)
            log(f"[{i}/{len(cfgs)}] {c['label']}: {' '.join(args)}", master_fp)
        master_fp.close()
        return

    csv_rows = []
    summary_results = []

    for idx, cfg in enumerate(cfgs, 1):
        label = cfg["label"]
        desc = cfg["desc"]
        log(f"\n────────────────────────────────────────────────────────────────────────", master_fp)
        log(f"▶ [{idx}/{len(cfgs)}] 正在测试配置: {label} ({desc})", master_fp)

        # 1. 彻底清理
        kill_all_llama(master_fp)
        time.sleep(1.5)

        # 2. 构造启动参数
        args = server_args(model_info, cfg)
        srv_log_file = os.path.join(RUNS_DIR, f"srv_{safe_name}_{label}_{ts}.log")
        srv_fp = open(srv_log_file, "w", encoding="utf-8")

        creationflags = 0x08000000 if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            args,
            cwd=BASE_DIR,
            stdout=srv_fp,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

        # 3. 等待就绪
        ready = wait_server_ready(proc, host=HOST, port=PORT, timeout=120, log_path=srv_log_file, master=master_fp)
        if not ready:
            log(f"  ❌ 配置 {label} 启动失败！(详见日志: {srv_log_file})", master_fp)
            try:
                proc.kill()
            except Exception:
                pass
            srv_fp.close()
            continue

        vram_used = get_gpu_memory()
        log(f"  ✅ 服务已成功就绪 | GPU 显存占用: {vram_used} MiB", master_fp)

        # 4. 执行多任务基准测速
        task_speeds = []
        task_prompt_speeds = []

        for tidx, task in enumerate(TASKS, 1):
            tname = task["name"]
            try:
                m_res = send_completion_benchmark(task["prompt"], n_predict=n_predict, timeout=180)
                gen_tps = m_res["predicted_tps"]
                pp_tps = m_res["prompt_tps"]
                task_speeds.append(gen_tps)
                task_prompt_speeds.append(pp_tps)

                log(
                    f"    • 任务 {tidx} [{tname}]: 生成速度 {gen_tps:6.2f} tok/s | 提示词处理 {pp_tps:7.2f} tok/s | 生成 {m_res['predicted_n']} tok (耗时 {m_res['wall_time_s']}s)",
                    master_fp,
                )
                csv_rows.append({
                    "model": model_info["alias"],
                    "config": label,
                    "desc": desc,
                    "ctx": cfg["ctx"],
                    "nmax": cfg.get("nmax", "off"),
                    "pmin": cfg.get("pmin", 0.0),
                    "vram_mib": vram_used,
                    "task": tname,
                    "gen_tok_per_sec": gen_tps,
                    "prompt_tok_per_sec": pp_tps,
                    "generated_tokens": m_res["predicted_n"],
                    "wall_time_sec": m_res["wall_time_s"],
                })
            except Exception as e:
                log(f"    ❌ 任务 {tidx} [{tname}] 异常: {e}", master_fp)

        # 5. 计算当前配置汇总平均
        if task_speeds:
            avg_speed = sum(task_speeds) / len(task_speeds)
            max_speed = max(task_speeds)
            min_speed = min(task_speeds)
            avg_pp = sum(task_prompt_speeds) / len(task_prompt_speeds)
            log(f"  🏁 【{label} 测速汇总】: 平均生成速度: {avg_speed:6.2f} tok/s (最高: {max_speed:.2f}, 最低: {min_speed:.2f}) | 平均 PP: {avg_pp:.2f} tok/s", master_fp)
            summary_results.append({
                "label": label,
                "desc": desc,
                "avg_tps": avg_speed,
                "max_tps": max_speed,
                "min_tps": min_speed,
                "avg_pp": avg_pp,
                "vram": vram_used,
                "ctx": cfg["ctx"],
                "nmax": cfg.get("nmax", "off"),
                "pmin": cfg.get("pmin", 0.0),
            })

        # 6. 关闭服务
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        srv_fp.close()

    # 全部测试完成后彻底清理
    kill_all_llama(master_fp)

    # 排序总结
    summary_results.sort(key=lambda x: x["avg_tps"], reverse=True)

    # 写入 CSV
    if csv_rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)

    # 写入 TXT
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(f"模型: {model_name} 速度与 MTP/上下文参数优化测速汇总\n")
        f.write(f"测试时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"{'排名':<4} {'配置标识':<28} {'平均生成(t/s)':<14} {'最高(t/s)':<12} {'最低(t/s)':<12} {'提示词PP(t/s)':<14} {'显存(MB)':<10}\n")
        f.write("-" * 96 + "\n")
        for i, r in enumerate(summary_results, 1):
            f.write(f"{i:<4} {r['label']:<28} {r['avg_tps']:<14.2f} {r['max_tps']:<12.2f} {r['min_tps']:<12.2f} {r['avg_pp']:<14.2f} {r['vram']:<10}\n")
        f.write("-" * 96 + "\n\n")
        f.write("【配置详细说明与甜点解析】:\n")
        for i, r in enumerate(summary_results, 1):
            f.write(f"  #{i} {r['label']} (ctx={r['ctx']//1024}K, MTP={r['nmax']}, pmin={r['pmin']}) -> 平均 {r['avg_tps']:.2f} tok/s | {r['desc']}\n")

    # 写入 Markdown 报告
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# {model_name} 极限速度评测与 MTP/上下文参数优化报告\n\n")
        f.write(f"- **测试日期**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- **模型规格**: `{model_name}` ({os.path.getsize(model_path)/(1024**3):.2f} GB)\n")
        f.write(f"- **硬件平台**: Tesla V100 32GB (CUDA 12.4/13.0)\n\n")
        f.write("## 1. 测试结果排行榜\n\n")
        f.write("| 排名 | 配置名称 | 平均生成速度 (tok/s) | 最高速度 (tok/s) | 提示词处理 PP (tok/s) | 上下文大小 | MTP 阶数 | 显存占用 (MB) | 配置描述 |\n")
        f.write("|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---|\n")
        for i, r in enumerate(summary_results, 1):
            f.write(f"| #{i} | `{r['label']}` | **{r['avg_tps']:.2f}** | {r['max_tps']:.2f} | {r['avg_pp']:.2f} | {r['ctx']//1024}K | `{r['nmax']}` | {r['vram']} | {r['desc']} |\n")
        f.write("\n## 2. 结论与最佳推荐配置\n\n")
        if summary_results:
            top = summary_results[0]
            f.write(f"👑 **理论最大速度配置**: `{top['label']}`（**{top['avg_tps']:.2f} tok/s**，峰值 **{top['max_tps']:.2f} tok/s**）\n\n")

    log("\n" + "=" * 80, master_fp)
    log(f"🎉 评测全部完成！", master_fp)
    log(f"• 汇总简报: {txt_path}", master_fp)
    log(f"• Markdown 报告: {report_path}", master_fp)
    log(f"• 详细 CSV: {csv_path}", master_fp)
    log("=" * 80, master_fp)
    master_fp.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI 大模型极限测速与参数优化流水线")
    parser.add_argument("--model", type=str, choices=["30b", "27b", "all"], default="30b", help="评测目标模型: 30b 或 27b 或 all")
    parser.add_argument("--suite", type=str, choices=["smoke", "mtp_sweep", "ctx_sweep", "kv_sweep", "matrix"], default="matrix", help="测试套件类型")
    parser.add_argument("--predict", type=int, default=512, help="每次测试生成的最大 token 数")
    parser.add_argument("--dry-run", action="store_true", help="仅打印配置命令，不实际启动推理")

    args = parser.parse_args()

    targets = ["30b", "27b"] if args.model == "all" else [args.model]
    for target in targets:
        run_benchmark(target, suite=args.suite, n_predict=args.predict, dry_run=args.dry_run)
