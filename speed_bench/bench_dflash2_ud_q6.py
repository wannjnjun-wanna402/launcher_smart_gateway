# -*- coding: utf-8 -*-
"""
bench_dflash2_ud_q6.py - Qwen3.8-27B-UD-Q6_K_M 纯 DFlash2 扩散草稿头专用精简测速脚本
=================================================================================================
说明：
  1. 彻底移除基准(0值/无投机)与 MTP 重复测试（已确定基准裸速=22.30 tok/s，MTP n=2=37.13 tok/s）；
  2. 针对 Tesla V100 32GB 显卡与 27B-UD 模型特点，直击 DFlash2 核心甜点参数（n-max: 3, 5, 7, 9 及 p-min 过滤）；
  3. 挂载 Qwen3.8-27B-DFlash2-Q4_K_M.gguf (~1.14GB) 全量 GPU 卸载加速；
  4. 严格遵守 AGENTS.md 规范：每次切换彻底杀死残留进程、显存释放检查 (<500MB)、结果时间戳防覆盖。
"""

import os
import sys
import json
import time
import csv
import argparse
import datetime
import subprocess
import urllib.request
import urllib.error

# 确保 Windows 终端 UTF-8 中文正常输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ============================================================
#  基础路径与全局常量配置
# ============================================================
HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(HERE)
RUNS_DIR = os.path.join(HERE, "runs_dflash2_ud_q6")
os.makedirs(RUNS_DIR, exist_ok=True)

LLAMA_SERVER = os.path.join(WORKSPACE_ROOT, "llama-server.exe").replace("\\", "/")
TARGET_MODEL = "E:/models/Qwen3.8-27B-UD-Q6_K_M.gguf"
DFLASH_MODEL = "E:/models/Qwen3.8-27B-DFlash2-Q4_K_M.gguf"
MMPROJ_MODEL = "E:/models/mmproj-Qwen3.8-27B-F16.gguf"
CHAT_TEMPLATE = os.path.join(WORKSPACE_ROOT, "chat_template_qwen_fixed.jinja").replace("\\", "/")

HOST = "127.0.0.1"
PORT = 8081
CREATE_NO_WINDOW = 0x00000008

# 已测得的基准参考常量（无需重复测）
KNOWN_BASELINE_SPEED = 22.30   # 裸速基准 (tok/s)
KNOWN_MTP_BEST_SPEED = 37.13   # MTP n=2 冠军纪录 (tok/s)

# ============================================================
#  6 大典型测试用例
# ============================================================
BENCH_TASKS = [
    {
        "id": "gsm8k_math",
        "category": "数学推理 (GSM8K/Chain-of-Thought)",
        "prompt": "甲乙两个水管同时向一个水池注水。单独开甲管，6小时注满；单独开乙管，8小时注满。如果先开甲管2小时，然后两管同时开，还需要多少小时注满？请给出详细分步计算过程与最终答案。",
        "max_tokens": 512,
        "temperature": 0.3
    },
    {
        "id": "python_code",
        "category": "代码编写 (High Regularity)",
        "prompt": "请用 Python 编写一个高性能、线程安全的并发连接池管理器（ConnectionPool），支持最大连接数、最小空闲连接数、超时回收与健康检查，并附带使用示例与异常处理。",
        "max_tokens": 512,
        "temperature": 0.3
    },
    {
        "id": "json_structured",
        "category": "结构化数据 (JSON Schema)",
        "prompt": "请输出一个符合标准 RFC8259 规范的 JSON 数据，描述一个云原生微服务集群的架构拓扑，包含 cluster_id, region, nodes (不少于4个节点，含ip, cpu_cores, ram_gb, status, running_pods数组) 以及 service_mesh 配置，不要输出额外解释文字。",
        "max_tokens": 512,
        "temperature": 0.3
    },
    {
        "id": "translation_en2zh",
        "category": "文献翻译 (Linguistic Conversion)",
        "prompt": "Translate the following technical paragraph into natural and professional Chinese:\n'Speculative decoding is an algorithmic acceleration paradigm that leverages a lightweight draft model to predict multiple candidate tokens in parallel, followed by a single batched verification pass by the target large language model. This decouples the memory bandwidth bottleneck from autoregressive generation, yielding significant speedups without altering the output probability distribution.'",
        "max_tokens": 384,
        "temperature": 0.3
    },
    {
        "id": "creative_long",
        "category": "开放长文 (Low Predictability)",
        "prompt": "请写一篇关于“深海热泉生态系统与生命起源假说”的深度科普短文，不少于600字，包含化能合成作用原理、极端嗜热微生物特性及其对地外生命探索的启示，结构清晰明了。",
        "max_tokens": 512,
        "temperature": 0.3
    },
    {
        "id": "template_repetition",
        "category": "模板规律扩展 (Peak Speed Ceiling)",
        "prompt": "请按照以下格式连续列出10个标准技术规范条目：[规范编号] - [技术名称] - [适用场景] - [核心参数] - [合规等级]，从 SPEC-001 列到 SPEC-010。",
        "max_tokens": 512,
        "temperature": 0.3
    }
]


def log(msg, master_fp=None):
    ts = datetime.datetime.now().strftime("[%H:%M:%S] ")
    line = ts + msg
    print(line, flush=True)
    if master_fp is not None:
        try:
            master_fp.write(line + "\n")
            master_fp.flush()
        except Exception:
            pass


def get_gpu_info():
    """获取当前 GPU 型号与显存占用 (MiB)"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if out:
            parts = [p.strip() for p in out.splitlines()[0].split(",")]
            name = parts[0]
            used = int(parts[1].replace("MiB", "").strip())
            total = int(parts[2].replace("MiB", "").strip())
            util = parts[3]
            return {"name": name, "used": used, "total": total, "util": util}
    except Exception:
        pass
    return {"name": "Tesla V100", "used": -1, "total": 32768, "util": "N/A"}


def kill_existing_llama(master_fp=None):
    """彻底终止所有残留的 llama 进程、代理网关并释放端口与显存"""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Process *llama* -ErrorAction SilentlyContinue | Stop-Process -Force"],
                capture_output=True, timeout=10
            )
        except Exception:
            pass
        try:
            subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"],
                           capture_output=True, timeout=5)
            subprocess.run(["taskkill", "/F", "/IM", "llama-cli.exe"],
                           capture_output=True, timeout=5)
        except Exception:
            pass

    # 清理占用 8081..8084 端口的残留进程
    for p in (8081, 8082, 8083, 8084):
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

    # 循环等待显存降到安全水位 (< 500MB)
    max_wait = 60
    start_t = time.time()
    while time.time() - start_t < max_wait:
        info = get_gpu_info()
        if info["used"] < 500 or info["used"] == -1:
            return True
        time.sleep(1)

    info = get_gpu_info()
    log(f"⚠️ 显存清理等待超时，当前显存占用: {info['used']} MiB，继续尝试...", master_fp)
    return False


def wait_for_server_ready(proc, port=PORT, timeout=240, master_fp=None):
    """等待 llama-server HTTP /health 接口就绪"""
    url = f"http://{HOST}:{port}/health"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer llamacpp"}, method="GET")
    start_time = time.time()
    while time.time() - start_time < timeout:
        if proc and proc.poll() is not None:
            log(f"  ❌ llama-server 进程异常退出 (exit code={proc.returncode})", master_fp)
            return False
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def send_chat_completion(prompt, max_tokens=512, temperature=0.3, port=PORT):
    """发送 OpenAI 兼容聊天补全请求并提取详细 timings"""
    url = f"http://{HOST}:{port}/v1/chat/completions"
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.05,
        "stream": False
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer llamacpp"},
        method="POST"
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            elapsed_wall = time.time() - t0
            raw = resp.read()
            data = json.loads(raw.decode("utf-8"))

            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)

            timings = data.get("timings", {})
            prompt_speed = timings.get("prompt_per_second", None)
            gen_speed = timings.get("predicted_per_second", None)
            draft_n = timings.get("draft_n", None)
            draft_accepted = timings.get("draft_accepted_n", None)
            draft_acc_rate = timings.get("draft_accept_rate", None)

            if gen_speed is None and completion_tokens > 0 and elapsed_wall > 0:
                gen_speed = completion_tokens / elapsed_wall
            if prompt_speed is None:
                prompt_speed = 0.0

            return {
                "success": True,
                "content": content,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "prompt_speed": prompt_speed if prompt_speed is not None else 0.0,
                "gen_speed": gen_speed if gen_speed is not None else 0.0,
                "elapsed_wall": elapsed_wall,
                "draft_n": draft_n,
                "draft_accepted": draft_accepted,
                "draft_acc_rate": draft_acc_rate,
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "elapsed_wall": time.time() - t0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "prompt_speed": 0.0,
            "gen_speed": 0.0
        }


# ============================================================
#  直击 DFlash2 核心候选配置矩阵
# ============================================================
def build_config_suite(mode="core"):
    """
    针对 V100 32GB 机器直击 DFlash2 可能的最佳参数：
      - core (推荐, 4组, 约6-8分钟):
          1. DFlash2_n3 (小步长快速验证)
          2. DFlash2_n5 (中等步长平衡点)
          3. DFlash2_n7 (官方推荐步长)
          4. DFlash2_n9 (长步长冲刺)
      - full (6组, 约10-12分钟):
          包含 n=3, 5, 7, 9 以及 p-min=0.75 质量过滤对照
      - smoke (2组, 约2-3分钟):
          快速测试 n=3 与 n=7
    """
    configs = []

    if mode == "smoke":
        configs.append({
            "label": "DFlash2_n3",
            "desc": "DFlash2 紧凑轻量 (n-max=3, 降低单次扩散开销)",
            "n_max": 3,
            "p_min": 0.0,
            "extra_args": []
        })
        configs.append({
            "label": "DFlash2_n7",
            "desc": "DFlash2 官方推荐 (n-max=7, 默认贪婪)",
            "n_max": 7,
            "p_min": 0.0,
            "extra_args": []
        })
        return configs

    # 1. 核心步长阶梯扫描 (n-max: 3, 4, 5, 7, 9)
    configs.append({
        "label": "DFlash2_n3",
        "desc": "DFlash2 紧凑轻量 (n-max=3, 扩散计算耗时最短)",
        "n_max": 3,
        "p_min": 0.0,
        "extra_args": []
    })
    configs.append({
        "label": "DFlash2_n4",
        "desc": "DFlash2 阶梯测试 (n-max=4)",
        "n_max": 4,
        "p_min": 0.0,
        "extra_args": []
    })
    configs.append({
        "label": "DFlash2_n5",
        "desc": "DFlash2 适中步长 (n-max=5, 步长与延迟均衡点)",
        "n_max": 5,
        "p_min": 0.0,
        "extra_args": []
    })
    configs.append({
        "label": "DFlash2_n7",
        "desc": "DFlash2 官方标准 (n-max=7, 官方推荐基准值)",
        "n_max": 7,
        "p_min": 0.0,
        "extra_args": []
    })
    configs.append({
        "label": "DFlash2_n9",
        "desc": "DFlash2 极限长步 (n-max=9, 探索最大单次命中收益)",
        "n_max": 9,
        "p_min": 0.0,
        "extra_args": []
    })

    if mode == "full":
        # 2. 置信度过滤优化项 (探究过滤低概率 token 是否能减少回滚惩罚)
        configs.append({
            "label": "DFlash2_n4_p0.75",
            "desc": "DFlash2 置信度过滤 (n-max=4, p-min=0.75)",
            "n_max": 4,
            "p_min": 0.75,
            "extra_args": []
        })
        configs.append({
            "label": "DFlash2_n5_p0.75",
            "desc": "DFlash2 置信度过滤 (n-max=5, p-min=0.75 质量截断)",
            "n_max": 5,
            "p_min": 0.75,
            "extra_args": []
        })
        configs.append({
            "label": "DFlash2_n7_p0.75",
            "desc": "DFlash2 置信度过滤 (n-max=7, p-min=0.75 官方+质量截断)",
            "n_max": 7,
            "p_min": 0.75,
            "extra_args": []
        })

    return configs


def main():
    parser = argparse.ArgumentParser(description="Qwen3.8-27B-UD-Q6_K_M 纯 DFlash2 投机解码精准测速")
    parser.add_argument("--mode", choices=["core", "full", "smoke"], default="core",
                        help="评测模式: core (5组核心步长 n=3/4/5/7/9, 推荐), full (8组含置信度), smoke (2组冒烟)")
    parser.add_argument("--nmax", type=int, default=None,
                        help="指定单个 n-max 值进行单项精准测速 (例如 --nmax 4)")
    parser.add_argument("--ctx", type=int, default=32768,
                        help="评测上下文大小 (默认: 32768 = 32K，轻量快速)")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅打印配置，不实际执行")
    args = parser.parse_args()

    # 检查必要文件
    for fpath, desc in [
        (LLAMA_SERVER, "llama-server 可执行文件"),
        (TARGET_MODEL, "主模型 Qwen3.8-27B-UD-Q6_K_M.gguf"),
        (DFLASH_MODEL, "草稿模型 Qwen3.8-27B-DFlash2-Q4_K_M.gguf"),
        (CHAT_TEMPLATE, "修复版 Jinja 对话模板"),
    ]:
        if not os.path.exists(fpath):
            print(f"❌ 错误: 未找到 {desc}: {fpath}")
            sys.exit(1)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    master_log_path = os.path.join(HERE, f"bench_dflash2_ud_q6_{ts}.log")
    csv_report_path = os.path.join(HERE, f"results_dflash2_ud_q6_{ts}.csv")
    txt_summary_path = os.path.join(HERE, f"summary_dflash2_ud_q6_{ts}.txt")
    md_report_path = os.path.join(HERE, f"report_dflash2_ud_q6_{ts}.md")

    if args.nmax is not None:
        configs = [{
            "label": f"DFlash2_n{args.nmax}",
            "desc": f"DFlash2 单项定制测试 (n-max={args.nmax})",
            "n_max": args.nmax,
            "p_min": 0.0,
            "extra_args": []
        }]
    else:
        configs = build_config_suite(mode=args.mode)

    if args.dry_run:
        print("=== DRY RUN: DFLASH2 PURE BENCHMARK CONFIGURATIONS ===")
        for i, cfg in enumerate(configs, 1):
            print(f"[{i}/{len(configs)}] {cfg['label']}: {cfg['desc']} (n_max={cfg['n_max']}, p_min={cfg['p_min']})")
        return

    master_fp = open(master_log_path, "w", encoding="utf-8")
    gpu_info = get_gpu_info()

    log("=" * 80, master_fp)
    log("🚀 Qwen3.8-27B-UD-Q6_K_M 纯 DFlash2 精准测速套件启动", master_fp)
    log(f"📅 测试启动时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", master_fp)
    log(f"🎯 主模型: {TARGET_MODEL} (~21.5 GB)", master_fp)
    log(f"⚡ 草稿模型: {DFLASH_MODEL} (~1.14 GB, 全量 GPU 卸载)", master_fp)
    log(f"🎮 硬件平台: {gpu_info['name']} (总显存: {gpu_info['total']} MiB)", master_fp)
    log(f"📏 上下文大小: {args.ctx} ({args.ctx // 1024}K) | 模式: {args.mode} ({len(configs)} 组 DFlash2 配置)", master_fp)
    log(f"📌 已知对照参考: 基准裸速 = {KNOWN_BASELINE_SPEED} tok/s | MTP n=2 纪录 = {KNOWN_MTP_BEST_SPEED} tok/s", master_fp)
    log("=" * 80, master_fp)

    all_results = []
    config_summaries = []

    csv_fields = [
        "Timestamp", "Config_Label", "Config_Desc", "Task_ID", "Task_Category",
        "Prompt_Tokens", "Completion_Tokens", "Prompt_Speed_TokS", "Gen_Speed_TokS",
        "Elapsed_Sec", "Draft_N", "Draft_Accepted", "Draft_Accept_Rate", "Success"
    ]

    for cfg_idx, cfg in enumerate(configs, 1):
        log("\n" + "#" * 80, master_fp)
        log(f"[{cfg_idx}/{len(configs)}] 正在测试 DFlash2 配置: {cfg['label']} ({cfg['desc']})", master_fp)
        log(f"     参数设置: --spec-type draft-dflash --spec-draft-n-max {cfg['n_max']} --spec-draft-p-min {cfg['p_min']}", master_fp)
        log("#" * 80, master_fp)

        # 1. 彻底杀死旧进程
        kill_existing_llama(master_fp)
        time.sleep(2)

        # 2. 构建 llama-server 启动命令
        cmd = [
            LLAMA_SERVER,
            "-m", TARGET_MODEL,
            "-ngl", "99",
            "-md", DFLASH_MODEL,
            "-ngld", "99",
            "--spec-type", "draft-dflash",
            "--spec-draft-n-max", str(cfg["n_max"]),
            "-c", str(args.ctx),
            "-b", "2048",
            "--ubatch-size", "2048",
            "-t", "6",
            "--flash-attn", "on",
            "--cache-type-k", "q8_0",
            "--cache-type-v", "q8_0",
            "--jinja",
            "--chat-template-file", CHAT_TEMPLATE,
            "--reasoning", "auto",
            "--reasoning-budget", "2048",
            "--reasoning-effort", "medium",
            "--reasoning-format", "deepseek",
            "--no-reasoning-preserve",
            "--temp", "0.3",
            "--top-p", "0.95",
            "--top-k", "20",
            "--min-p", "0.05",
            "--dry-multiplier", "0.8",
            "--dry-base", "1.75",
            "--dry-allowed-length", "2",
            "--dry-penalty-last-n", "256",
            "--repeat-penalty", "1.0",
            "--presence-penalty", "0.0",
            "--alias", "Qwen3.8-27B-UD-Q6-DFlash2",
            "--port", str(PORT),
            "--host", HOST,
            "--api-key", "llamacpp",
            "--no-warmup"
        ]

        if cfg["p_min"] > 0:
            cmd.extend(["--spec-draft-p-min", str(cfg["p_min"])])

        if os.path.exists(MMPROJ_MODEL):
            cmd.extend(["--mmproj", MMPROJ_MODEL, "--no-mmproj-offload"])

        if cfg["extra_args"]:
            cmd.extend(cfg["extra_args"])

        run_log_file = os.path.join(RUNS_DIR, f"{cfg['label']}_{ts}.log")
        run_fp = open(run_log_file, "w", encoding="utf-8")

        log(f"  启动命令: {' '.join(cmd[:10])} ... --spec-draft-n-max {cfg['n_max']}", master_fp)

        # 3. 启动后台服务器
        proc = subprocess.Popen(
            cmd,
            stdout=run_fp,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )

        log("  等待 llama-server 初始化并加载主模型与 DFlash2 草稿模型 (约35-40秒)...", master_fp)
        if not wait_for_server_ready(proc, port=PORT, timeout=240, master_fp=master_fp):
            log(f"  ❌ 启动失败，请检查日志: {run_log_file}", master_fp)
            try:
                proc.terminate()
            except Exception:
                pass
            run_fp.close()
            continue

        gpu_loaded = get_gpu_info()
        log(f"  ✅ 服务就绪！当前显存占用: {gpu_loaded['used']} MiB", master_fp)
        time.sleep(2)

        # 4. 执行预热任务 (Warmup)
        log("  ♨️ 正在执行系统预热 (Warmup)...", master_fp)
        warmup_res = send_chat_completion("请写出数字 1 到 20 并用逗号分隔。", max_tokens=64, temperature=0.0, port=PORT)
        if warmup_res["success"]:
            log(f"     预热完成: 生成速度 = {warmup_res['gen_speed']:.2f} tok/s", master_fp)

        # 5. 执行正式任务集评测
        cfg_speeds = []
        cfg_prefill_speeds = []

        for task_idx, task in enumerate(BENCH_TASKS, 1):
            log(f"  -> [{task_idx}/{len(BENCH_TASKS)}] 执行任务: {task['id']} - {task['category']}", master_fp)

            res = send_chat_completion(
                prompt=task["prompt"],
                max_tokens=task["max_tokens"],
                temperature=task["temperature"],
                port=PORT
            )

            if res["success"]:
                log(f"     📊 结果: 生成 = {res['gen_speed']:.2f} tok/s | Prefill = {res['prompt_speed']:.1f} tok/s | 生成 {res['completion_tokens']} toks ({res['elapsed_wall']:.2f}s)", master_fp)
                if res["draft_n"] is not None and res["draft_accepted"] is not None:
                    log(f"        投机数据: 提出 {res['draft_n']} tokens, 接受 {res['draft_accepted']} tokens (接受率: {res['draft_acc_rate']:.1%})", master_fp)
                cfg_speeds.append(res["gen_speed"])
                cfg_prefill_speeds.append(res["prompt_speed"])
            else:
                log(f"     ❌ 任务失败: {res.get('error')}", master_fp)

            all_results.append({
                "Timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Config_Label": cfg["label"],
                "Config_Desc": cfg["desc"],
                "Task_ID": task["id"],
                "Task_Category": task["category"],
                "Prompt_Tokens": res["prompt_tokens"],
                "Completion_Tokens": res["completion_tokens"],
                "Prompt_Speed_TokS": round(res["prompt_speed"], 2),
                "Gen_Speed_TokS": round(res["gen_speed"], 2),
                "Elapsed_Sec": round(res["elapsed_wall"], 2),
                "Draft_N": res["draft_n"],
                "Draft_Accepted": res["draft_accepted"],
                "Draft_Accept_Rate": f"{res['draft_acc_rate']:.1%}" if res["draft_acc_rate"] is not None else "N/A",
                "Success": res["success"]
            })
            time.sleep(1)

        # 6. 计算本配置的平均性能
        if cfg_speeds:
            avg_gen_speed = sum(cfg_speeds) / len(cfg_speeds)
            avg_prefill_speed = sum(cfg_prefill_speeds) / len(cfg_prefill_speeds) if cfg_prefill_speeds else 0.0
            max_speed = max(cfg_speeds)
            min_speed = min(cfg_speeds)
            log(f"  ⭐ 【{cfg['label']} 汇总】: 平均速度 = {avg_gen_speed:.2f} tok/s (峰值: {max_speed:.2f}, 最低: {min_speed:.2f})", master_fp)
            config_summaries.append({
                "label": cfg["label"],
                "desc": cfg["desc"],
                "n_max": cfg["n_max"],
                "p_min": cfg["p_min"],
                "avg_speed": avg_gen_speed,
                "max_speed": max_speed,
                "min_speed": min_speed,
                "avg_prefill": avg_prefill_speed,
                "vram_mb": gpu_loaded["used"]
            })

        # 7. 关闭本轮进程
        try:
            proc.terminate()
        except Exception:
            pass
        run_fp.close()
        kill_existing_llama(master_fp)
        time.sleep(3)

    # 写入 CSV 数据
    with open(csv_report_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        for row in all_results:
            writer.writerow(row)

    # 排序汇总列表
    config_summaries.sort(key=lambda x: x["avg_speed"], reverse=True)

    # 写入 TXT 汇总
    with open(txt_summary_path, "w", encoding="utf-8") as f:
        f.write("=" * 88 + "\n")
        f.write("Qwen3.8-27B-UD-Q6_K_M 纯 DFlash2 扩散投机测速排名\n")
        f.write(f"测试时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"主模型: {TARGET_MODEL}\n")
        f.write(f"草稿模型: {DFLASH_MODEL}\n")
        f.write(f"硬件环境: {gpu_info['name']} | 上下文: {args.ctx} ({args.ctx // 1024}K)\n")
        f.write(f"基准参考: 裸速 = {KNOWN_BASELINE_SPEED:.2f} tok/s | MTP n=2 纪录 = {KNOWN_MTP_BEST_SPEED:.2f} tok/s\n")
        f.write("=" * 88 + "\n\n")
        f.write(f"{'排名':<4}{'DFlash2 配置':<26}{'平均(tok/s)':>14}{'峰值(tok/s)':>14}{'对比裸速':>12}{'对比MTP(n2)':>14}{'显存(MB)':>10}\n")
        f.write("-" * 88 + "\n")
        for i, cs in enumerate(config_summaries, 1):
            vs_base = ((cs["avg_speed"] - KNOWN_BASELINE_SPEED) / KNOWN_BASELINE_SPEED) * 100.0
            vs_mtp = ((cs["avg_speed"] - KNOWN_MTP_BEST_SPEED) / KNOWN_MTP_BEST_SPEED) * 100.0
            f.write(f"{i:<4}{cs['label']:<26}{cs['avg_speed']:>14.2f}{cs['max_speed']:>14.2f}{vs_base:>+11.1f}%{vs_mtp:>+13.1f}%{cs['vram_mb']:>10}\n")
        f.write("-" * 88 + "\n\n")
        f.write("【各配置详细说明】:\n")
        for i, cs in enumerate(config_summaries, 1):
            f.write(f"  #{i} {cs['label']}: {cs['desc']} -> 平均 {cs['avg_speed']:.2f} tok/s\n")

    # 写入 Markdown 详细对比分析报告
    with open(md_report_path, "w", encoding="utf-8") as f:
        f.write("# Qwen3.8-27B-UD-Q6_K_M 纯 DFlash2 扩散投机测速报告\n\n")
        f.write(f"- **测试时间**: `{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n")
        f.write(f"- **目标主模型**: `Qwen3.8-27B-UD-Q6_K_M.gguf` (~21.5 GB)\n")
        f.write(f"- **草稿模型**: `Qwen3.8-27B-DFlash2-Q4_K_M.gguf` (~1.14 GB, 全量 GPU 卸载)\n")
        f.write(f"- **硬件平台**: `{gpu_info['name']}` ({gpu_info['total']} MiB VRAM)\n")
        f.write(f"- **测试上下文**: `{args.ctx}` ({args.ctx // 1024}K)\n\n")

        f.write("## 1. DFlash2 实测排名总表\n\n")
        f.write("| 排名 | DFlash2 配置 | 参数说明 | 平均速度 (tok/s) | 峰值速度 (tok/s) | 相对基准裸速 (22.3) | 相对MTP纪录 (37.1) | 显存 (MiB) |\n")
        f.write("|:----:|:-------------|:---------|:----------------:|:----------------:|:-------------------:|:------------------:|:----------:|\n")
        for i, cs in enumerate(config_summaries, 1):
            vs_base = ((cs["avg_speed"] - KNOWN_BASELINE_SPEED) / KNOWN_BASELINE_SPEED) * 100.0
            vs_mtp = ((cs["avg_speed"] - KNOWN_MTP_BEST_SPEED) / KNOWN_MTP_BEST_SPEED) * 100.0
            f.write(f"| {i} | `{cs['label']}` | {cs['desc']} | **{cs['avg_speed']:.2f}** | {cs['max_speed']:.2f} | **{vs_base:+.1f}%** | **{vs_mtp:+.1f}%** | {cs['vram_mb']} |\n")

        if config_summaries:
            top = config_summaries[0]
            vs_mtp_top = ((top["avg_speed"] - KNOWN_MTP_BEST_SPEED) / KNOWN_MTP_BEST_SPEED) * 100.0
            f.write(f"\n## 2. 核心结论\n\n")
            f.write(f"- 🏆 **DFlash2 最优配置**: `{top['label']}` ({top['desc']})\n")
            f.write(f"- ⚡ **DFlash2 最高速度**: 平均 **{top['avg_speed']:.2f} tok/s** (单项最高 {top['max_speed']:.2f} tok/s)\n")
            if top["avg_speed"] > KNOWN_MTP_BEST_SPEED:
                f.write(f"- 🚀 **超越 MTP**: 相比原生 MTP n=2 (37.13 tok/s) **提速 {vs_mtp_top:+.1f}%**，建议切换至 DFlash2！\n")
            else:
                f.write(f"- ℹ️ **对比 MTP**: 原生 MTP n=2 (37.13 tok/s) 仍快于 DFlash2 ({top['avg_speed']:.2f} tok/s, 差 {vs_mtp_top:.1f}%)，建议保持原生 MTP 投机！\n")

    log("\n" + "=" * 80, master_fp)
    log("🎉 全部 DFlash2 测速完成！", master_fp)
    log(f"📊 CSV 结果表: {csv_report_path}", master_fp)
    log(f"📝 汇总文本: {txt_summary_path}", master_fp)
    log(f"📑 Markdown 报告: {md_report_path}", master_fp)
    if config_summaries:
        top = config_summaries[0]
        log(f"🏆 DFlash2 冠军配置: {top['label']} -> 平均 {top['avg_speed']:.2f} tok/s", master_fp)
    log("=" * 80, master_fp)
    master_fp.close()


if __name__ == "__main__":
    main()
