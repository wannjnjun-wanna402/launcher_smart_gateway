# -*- coding: utf-8 -*-
"""
bench_27b_mtp_v2.py - Qwen3.8 27B 梯队 (UD-Q6_K_M / HIGHEST / MID-HIGH) MTP 投机参数系统化实测

支持模型：
  1. Qwen3.8-27B-UD-Q6_K_M (E:/models/Qwen3.8-27B-UD-Q6_K_M.gguf ~21.5GB)
  2. Qwen3.8-27B-NVFP4-MTP-HIGHEST (E:/models/Qwen3.8-27B-NVFP4-MTP-HIGHEST.gguf ~21.6GB)
  3. Qwen3.8-27B-NVFP4-MTP-MID-HIGH (E:/models/Qwen3.8-27B-NVFP4-MTP-MID-HIGH.gguf ~15.8GB)

测试目标：
  1. 测出各模型在 96K 上下文 (98304) 下的最佳 MTP 甜点值（n-max 1..8）
  2. 验证 p-min 截断阈值 (0.0 vs 0.75 vs 0.85) 对实际生成速度与接受率的影响
  3. 对比 思考模式开启(Reasoning On) 与 思考模式关闭(Reasoning Off) 下 MTP 速度差异
  4. 严格遵守 AGENTS.md 规范：多模型切换清理、GPU 显存循环检查 (<500MB)、结果带时间戳防覆盖
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

# Windows 控制台编码配置
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

EXE = "E:/llama-win-cuda-12.4-x64/llama-server.exe"
HOST = "127.0.0.1"
PORT = 8081
NGL = 99
CTX_DEFAULT = 98304          # 96K 上下文 (96 * 1024)
THREADS = 6
CACHE_K = "q8_0"
CACHE_V = "q8_0"
CREATE_NO_WINDOW = 0x00000008

MMPROJ_FILE = "E:/models/mmproj-Qwen3.8-27B-F16.gguf"
TEMPLATE_FILE = "E:/llama-win-cuda-12.4-x64/chat_template_qwen_fixed.jinja"

MODELS_REGISTRY = {
    "ud": {
        "name": "Qwen3.8-27B-UD-Q6_K_M",
        "alias": "Qwen3.8-27B-UD-Q6",
        "path": "E:/models/Qwen3.8-27B-UD-Q6_K_M.gguf",
        "desc": "Qwen3.8-27B UD-Q6_K_M (全精度高保真)"
    },
    "highest": {
        "name": "Qwen3.8-27B-NVFP4-MTP-HIGHEST",
        "alias": "Qwen3.8-27B-HIGHEST",
        "path": "E:/models/Qwen3.8-27B-NVFP4-MTP-HIGHEST.gguf",
        "desc": "Qwen3.8-27B NVFP4-HIGHEST (最高精NVFP4+BF16 MTP)"
    },
    "mid-high": {
        "name": "Qwen3.8-27B-NVFP4-MTP-MID-HIGH",
        "alias": "Qwen3.8-27B-MID-HIGH",
        "path": "E:/models/Qwen3.8-27B-NVFP4-MTP-MID-HIGH.gguf",
        "desc": "Qwen3.8-27B NVFP4-MID-HIGH (轻量均衡)"
    }
}

# 5 类典型任务，覆盖不同 token 可预测性分布
TASKS = [
    {"name": "coherent_long", "prompt": "请写一篇关于“深海热泉生态系统”的科普长文，不少于800字，详细描述化能合成生物多样性、能量流动机制与深海地质特征。"},
    {"name": "code",          "prompt": "用Python实现一个高性能线程安全的LRU缓存类，支持最大容量、TTL过期时间淘汰、get/set/pop/clear方法，并编写完整单元测试。"},
    {"name": "factual",       "prompt": "用精确简洁的三段话分别解释：什么是量子纠缠？量子隐形传态是否超光速？量子纠缠在量子保密通信(QKD)中的实际作用是什么？"},
    {"name": "json",          "prompt": "输出一个标准的JSON格式数据，描述一个企业级AI研发团队架构，包含team_name, department, members(数组，至少5人，含name/role/level/skills/experience_years/current_project)及milestones。"},
    {"name": "reasoning",     "prompt": "甲乙两辆火车分别从相距600公里的A、B两地同时相对开出。甲车时速80公里，乙车时速120公里。一只信鸽以时速150公里在两车之间往返飞行（从甲车飞向乙车，遇到乙车立即返回飞向甲车，如此往返直到两车相遇）。请问：信鸽总共飞了多少公里？信鸽在相遇前一瞬间朝哪个方向飞？请给出严密完整的数学推导过程。"},
]

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(HERE, "runs_27b_mtp")
os.makedirs(RUNS_DIR, exist_ok=True)


def log(msg, master=None):
    """同时打印到控制台与主日志文件。"""
    timestamp = datetime.datetime.now().strftime("[%H:%M:%S] ")
    line = timestamp + msg
    print(line, flush=True)
    if master is not None:
        try:
            master.write(line + "\n")
            master.flush()
        except Exception:
            pass


def get_gpu_memory():
    """获取当前 GPU 显存占用 (MiB)。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if out:
            return int(out.splitlines()[0].strip())
    except Exception:
        pass
    return -1


def get_driver_mode():
    """获取 NVIDIA 驱动模式 (WDDM / TCC)。"""
    try:
        out = subprocess.run(["nvidia-smi", "-q"],
                             capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if "Driver Model" in line:
                return line.split(":", 1)[1].strip()
        # 兜底查询
        smi = subprocess.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if smi:
            return f"Driver {smi}"
    except Exception:
        pass
    return "N/A"


def kill_all_llama(master=None):
    """彻底杀死所有 llama 相关进程、代理网关及端口占用，并循环确认显存 < 500MB (AGENTS.md 规范)。"""
    # 1. 杀 llama 进程
    try:
        subprocess.run(["taskkill", "/IM", "llama-server.exe", "/F"],
                       capture_output=True, timeout=10)
    except Exception:
        pass

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Process -Name *llama* -ErrorAction SilentlyContinue | Stop-Process -Force"],
            capture_output=True, timeout=10
        )
    except Exception:
        pass

    # 2. 清理占用 8081..8084 端口的残留进程（如后台残存的 proxy 网关）
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

    # 3. 循环检查显存释放情况（最多等 60 秒）
    t0 = time.time()
    while time.time() - t0 < 60:
        mem = get_gpu_memory()
        if mem >= 0 and mem < 500:
            break
        time.sleep(1)

    mem_final = get_gpu_memory()
    if mem_final > 500:
        log(f"⚠️ 警告: 显存清理后仍占用 {mem_final} MiB (未完全释放到 <500MB)", master)


def wait_port_free(host=HOST, port=PORT, timeout=20):
    """等待端口彻底释放。"""
    url = f"http://{host}:{port}/health"
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            time.sleep(0.5)
        except Exception:
            return True
    return False


def wait_server_ready(proc, host=HOST, port=PORT, timeout=240, log_path=None, master=None):
    """等待 llama-server 启动就绪。"""
    url = f"http://{host}:{port}/health"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer llamacpp"})
    t0 = time.time()
    while time.time() - t0 < timeout:
        # 进程已提前退出
        if proc and proc.poll() is not None:
            log(f"  ❌ llama-server 进程已异常退出 (exit code={proc.returncode})", master)
            return False

        if log_path and os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    if "CUDA out of memory" in content or "out of memory" in content.lower():
                        log("  ❌ 致命错误: CUDA OOM 显存不足！", master)
                        return False
                    if "failed to create llama_context" in content or "failed to create context" in content:
                        log("  ❌ 致命错误: 创建 context 失败！", master)
                        return False
                    if "error loading model" in content.lower():
                        log("  ❌ 致命错误: 模型加载失败！", master)
                        return False
            except Exception:
                pass

        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def build_configs(mode="core", ctx=CTX_DEFAULT):
    """
    构建测试配置网格：
      - core: 核心 n-max 扫描 (off, n=1..6) 开思考，共 7 组（耗时适中，迅速锁定甜点）
      - full: 全网格 (off, n=1..8 + p-min 截断 + 思考开/关对比)，共 17 组
      - pmin: p-min 阈值测试 (0.0 vs 0.75 vs 0.85)，共 8 组
      - smoke: 快速冒烟 (off vs n=2 vs n=3)，共 3 组
    """
    cfgs = []

    def add(label, reasoning, nmax, pmin=0.0, batch=2048, ctx_val=ctx, desc=""):
        cfgs.append({
            "label": label,
            "reasoning": reasoning,
            "nmax": nmax,
            "pmin": pmin,
            "batch": batch,
            "ctx": ctx_val,
            "desc": desc
        })

    if mode == "smoke":
        add("smoke_mtp_off", "on", None, desc="冒烟-基准裸速 (MTP关, 思考开)")
        add("smoke_mtp_n2",  "on", 2,    desc="冒烟-MTP n-max=2 (思考开)")
        add("smoke_mtp_n3",  "on", 3,    desc="冒烟-MTP n-max=3 (思考开)")
        return cfgs

    if mode in ("full", "core"):
        add("r_on_mtp_off", "on", None, desc="基准裸速 (MTP关, 思考开)")
        add("r_on_mtp_n1",  "on", 1,    desc="MTP n-max=1 (思考开)")
        add("r_on_mtp_n2",  "on", 2,    desc="MTP n-max=2 (思考开, 现役默认)")
        add("r_on_mtp_n3",  "on", 3,    desc="MTP n-max=3 (思考开)")
        add("r_on_mtp_n4",  "on", 4,    desc="MTP n-max=4 (思考开)")
        add("r_on_mtp_n5",  "on", 5,    desc="MTP n-max=5 (思考开)")
        add("r_on_mtp_n6",  "on", 6,    desc="MTP n-max=6 (思考开)")
        if mode == "full":
            add("r_on_mtp_n8",  "on", 8,    desc="MTP n-max=8 (思考开)")

    if mode in ("full", "pmin"):
        add("r_on_mtp_n2_p0.75", "on", 2, pmin=0.75, desc="MTP n-max=2, p-min=0.75")
        add("r_on_mtp_n3_p0.75", "on", 3, pmin=0.75, desc="MTP n-max=3, p-min=0.75")
        add("r_on_mtp_n4_p0.75", "on", 4, pmin=0.75, desc="MTP n-max=4, p-min=0.75")
        add("r_on_mtp_n5_p0.75", "on", 5, pmin=0.75, desc="MTP n-max=5, p-min=0.75")
        add("r_on_mtp_n2_p0.85", "on", 2, pmin=0.85, desc="MTP n-max=2, p-min=0.85 (高置信)")
        add("r_on_mtp_n3_p0.85", "on", 3, pmin=0.85, desc="MTP n-max=3, p-min=0.85 (高置信)")
        add("r_on_mtp_n4_p0.85", "on", 4, pmin=0.85, desc="MTP n-max=4, p-min=0.85 (高置信)")

    if mode == "full":
        add("r_off_mtp_off", "off", None, desc="基准裸速 (MTP关, 思考关)")
        add("r_off_mtp_n2",  "off", 2,    desc="MTP n-max=2 (思考关)")
        add("r_off_mtp_n3",  "off", 3,    desc="MTP n-max=3 (思考关)")
        add("r_off_mtp_n4",  "off", 4,    desc="MTP n-max=4 (思考关)")
        add("r_off_mtp_n5",  "off", 5,    desc="MTP n-max=5 (思考关)")

    return cfgs


def server_args(model_info, cfg):
    """根据模型信息与配置字典生成 llama-server 启动参数。"""
    a = [
        EXE,
        "-m", model_info["path"],
        "-ngl", str(NGL),
        "-c", str(cfg["ctx"]),
        "-b", str(cfg["batch"]),
        "--ubatch-size", str(cfg["batch"]),
        "--flash-attn", "on",
        "--cache-type-k", CACHE_K,
        "--cache-type-v", CACHE_V,
        "-t", str(THREADS),
        "--parallel", "1",
        "--api-key", "llamacpp",
        "--host", HOST,
        "--port", str(PORT),
        "--alias", model_info["alias"],
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
        "--no-warmup",
    ]

    if os.path.exists(TEMPLATE_FILE):
        a += ["--jinja", "--chat-template-file", TEMPLATE_FILE]

    if os.path.exists(MMPROJ_FILE):
        a += ["--mmproj", MMPROJ_FILE, "--no-mmproj-offload"]

    if cfg["reasoning"] == "on":
        a += [
            "--reasoning", "auto",
            "--reasoning-budget", "2048",
            "--reasoning-effort", "medium",
            "--reasoning-format", "deepseek",
            "--no-reasoning-preserve"
        ]
    else:
        a += ["--reasoning", "off"]

    if cfg["nmax"] is not None and cfg["nmax"] > 0:
        a += [
            "--spec-type", "draft-mtp",
            "--spec-draft-n-max", str(cfg["nmax"]),
            "--spec-draft-n-min", "1"
        ]
        if cfg.get("pmin", 0.0) > 0:
            a += ["--spec-draft-p-min", str(cfg["pmin"])]

    return a


def send_completion(prompt, n_predict, timeout=300):
    """向 llama-server 发送推理请求并解析结果。"""
    url = f"http://{HOST}:{PORT}/completion"
    data = json.dumps({
        "prompt": prompt,
        "n_predict": n_predict,
        "temperature": 0.3,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.05,
        "stream": False,
        "cache_prompt": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer llamacpp"},
        method="POST"
    )

    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise last_err


def progress_bar(done, total, width=28):
    frac = done / total if total else 0
    filled = int(width * frac)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {done}/{total} ({frac*100:.0f}%)"


def run_bench_for_model(model_info, mode, ctx, predict, limit, skip, dry_run):
    """针对单个模型执行完整的 MTP 测速流程。"""
    model_name = model_info["name"]
    model_path = model_info["path"]
    model_alias = model_info["alias"]

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = model_name.replace(".", "_")
    master_log_path = os.path.join(HERE, f"bench_mtp_{safe_name}_run_{ts}.log")
    results_csv_path = os.path.join(HERE, f"results_mtp_{safe_name}_{ts}.csv")
    summary_txt_path = os.path.join(HERE, f"summary_mtp_{safe_name}_{ts}.txt")
    report_md_path = os.path.join(HERE, f"report_mtp_{safe_name}_{ts}.md")

    cfgs = build_configs(mode=mode, ctx=ctx)
    if skip > 0:
        cfgs = cfgs[skip:]
    if limit > 0:
        cfgs = cfgs[:limit]

    total_cfgs = len(cfgs)
    n_predict = predict if mode != "smoke" else 256

    if dry_run:
        print(f"\n=== DRY RUN: {model_name} ===")
        for i, cfg in enumerate(cfgs, 1):
            cmd = " ".join(server_args(model_info, cfg))
            print(f"[{i}/{total_cfgs}] {cfg['label']}: {cfg['desc']}")
            print(f"  CMD: {cmd}\n")
        return None

    if not os.path.exists(model_path):
        print(f"❌ 错误: 模型文件不存在: {model_path}")
        return None
    if not os.path.exists(EXE):
        print(f"❌ 错误: llama-server 可执行文件不存在: {EXE}")
        return None

    master = open(master_log_path, "w", encoding="utf-8")
    dm = get_driver_mode()

    log("=" * 76, master)
    log(f"🚀 {model_name} MTP 投机解码参数系统化评测", master)
    log(f"📅 开始时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", master)
    log(f"📦 模型路径: {model_path}", master)
    log(f"🎮 GPU设备: Tesla V100-PCIE-32GB | 驱动模式: {dm}", master)
    log(f"📏 上下文设置: {ctx} ({ctx // 1024}K) | KV Cache: {CACHE_K}/{CACHE_V}", master)
    log(f"⚙️ 测试模式: {mode} | 配置数: {total_cfgs} | 任务数: {len(TASKS)} | 每任务生成: {n_predict} tokens", master)
    log(f"📄 输出文件: {results_csv_path}", master)
    log("=" * 76, master)

    rows = []
    per_config_stats = {}

    for ci, cfg in enumerate(cfgs, 1):
        label = cfg["label"]
        desc = cfg["desc"]
        log(f"\n{progress_bar(ci - 1, total_cfgs)} [配置 {ci}/{total_cfgs}] 正在测试: {label}", master)
        log(f"  说明: {desc}", master)
        log(f"  参数: n-max={cfg['nmax']}, p-min={cfg['pmin']}, reasoning={cfg['reasoning']}, ctx={cfg['ctx']}", master)

        log_path = os.path.join(RUNS_DIR, f"{safe_name}_{label}_{ts}.log")

        # 1. 彻底清理残留
        kill_all_llama(master)
        wait_port_free()
        time.sleep(2)

        # 2. 启动服务
        cmd = server_args(model_info, cfg)
        log(f"  启动命令: {' '.join(cmd[:6])} ... --spec-draft-n-max {cfg['nmax']}", master)

        with open(log_path, "w", encoding="utf-8") as logf:
            proc = subprocess.Popen(
                cmd, stdout=logf, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW
            )

            try:
                ok = wait_server_ready(proc, timeout=240, log_path=log_path, master=master)
                if not ok:
                    log(f"  ❌ {label} 启动失败 (超时或报错)，详情见: {log_path}", master)
                    rows.append({
                        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "model": model_name,
                        "config": label,
                        "desc": desc,
                        "task": "(failed_to_start)",
                        "decode_tok_s": None,
                        "prefill_tok_s": None,
                        "gen_tokens": None,
                        "elapsed_s": None,
                        "vram_loaded_mb": None,
                        "reasoning": cfg["reasoning"],
                        "nmax": cfg["nmax"] if cfg["nmax"] is not None else 0,
                        "pmin": cfg["pmin"],
                        "ctx": cfg["ctx"]
                    })
                    continue

                vram_loaded = get_gpu_memory()
                log(f"  ✅ 服务就绪！初始显存占用: {vram_loaded} MiB", master)
                time.sleep(2)

                per_config_stats[label] = {
                    "speeds": [],
                    "prefill_speeds": [],
                    "vram_loaded": vram_loaded,
                    "cfg": cfg,
                    "desc": desc,
                }

                num_tasks = len(TASKS)
                for ti, task in enumerate(TASKS):
                    is_warmup = (ti == 0)
                    task_n_predict = 256 if is_warmup else n_predict
                    task_name = f"[预热] {task['name']}" if is_warmup else task["name"]

                    log(f"    -> 正在执行 [{ti+1}/{num_tasks}] {task_name} (预计生成 {task_n_predict} tokens)...", master)
                    t_start = time.time()
                    try:
                        resp = send_completion(task["prompt"], task_n_predict)
                        t_elapsed = time.time() - t_start
                    except Exception as e:
                        log(f"    ❌ 任务失败: {e}", master)
                        continue

                    decode_spd = None
                    prefill_spd = None
                    gen_toks = None

                    if resp and "timings" in resp:
                        timings = resp["timings"]
                        decode_spd = timings.get("predicted_per_second")
                        prefill_spd = timings.get("prompt_per_second")
                        gen_toks = timings.get("predicted_n")

                    if is_warmup:
                        log(f"    ♨️ 预热完成: 解码速度 = {decode_spd:.2f} tok/s (耗时 {t_elapsed:.1f}s)", master)
                        continue

                    log(f"    📊 结果: 解码 = {decode_spd:.2f} tok/s | Prefill = {prefill_spd:.1f} tok/s | 生成 {gen_toks} tok ({t_elapsed:.1f}s)", master)

                    if decode_spd is not None:
                        per_config_stats[label]["speeds"].append(decode_spd)
                    if prefill_spd is not None:
                        per_config_stats[label]["prefill_speeds"].append(prefill_spd)

                    rows.append({
                        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "model": model_name,
                        "config": label,
                        "desc": desc,
                        "task": task["name"],
                        "decode_tok_s": round(decode_spd, 2) if decode_spd else None,
                        "prefill_tok_s": round(prefill_spd, 1) if prefill_spd else None,
                        "gen_tokens": gen_toks,
                        "elapsed_s": round(t_elapsed, 2),
                        "vram_loaded_mb": vram_loaded,
                        "reasoning": cfg["reasoning"],
                        "nmax": cfg["nmax"] if cfg["nmax"] is not None else 0,
                        "pmin": cfg["pmin"],
                        "ctx": cfg["ctx"]
                    })

                if per_config_stats[label]["speeds"]:
                    avg_s = sum(per_config_stats[label]["speeds"]) / len(per_config_stats[label]["speeds"])
                    max_s = max(per_config_stats[label]["speeds"])
                    min_s = min(per_config_stats[label]["speeds"])
                    log(f"  ⭐ {label} 汇总: 平均 = {avg_s:.2f} tok/s | 峰值 = {max_s:.2f} tok/s | 最低 = {min_s:.2f} tok/s", master)

            finally:
                try:
                    proc.terminate()
                except Exception:
                    pass
                kill_all_llama(master)
                wait_port_free()

    # 写入 CSV 详细结果
    with open(results_csv_path, "w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "timestamp", "model", "config", "desc", "task", "decode_tok_s",
            "prefill_tok_s", "gen_tokens", "elapsed_s", "vram_loaded_mb",
            "reasoning", "nmax", "pmin", "ctx"
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # 汇总排序
    summary_list = []
    for label, data in per_config_stats.items():
        speeds = data["speeds"]
        if speeds:
            avg_spd = sum(speeds) / len(speeds)
            max_spd = max(speeds)
            min_spd = min(speeds)
            summary_list.append({
                "label": label,
                "desc": data["desc"],
                "avg": avg_spd,
                "max": max_spd,
                "min": min_spd,
                "vram": data["vram_loaded"],
                "cfg": data["cfg"]
            })

    summary_list.sort(key=lambda x: x["avg"], reverse=True)

    # 写入 TXT 汇总
    with open(summary_txt_path, "w", encoding="utf-8") as f:
        f.write("=" * 76 + "\n")
        f.write(f"{model_name} MTP 投机解码速度测速汇总\n")
        f.write(f"测试时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"模型文件: {model_path}\n")
        f.write(f"GPU平台: Tesla V100-PCIE-32GB | 驱动: {dm}\n")
        f.write(f"上下文大小: {ctx} ({ctx // 1024}K) | 每任务生成 token: {n_predict}\n")
        f.write("=" * 76 + "\n\n")
        f.write(f"{'排名':<4}{'配置名称':<22}{'平均(tok/s)':>12}{'最高(tok/s)':>12}{'最低(tok/s)':>12}{'显存(MB)':>10}\n")
        f.write("-" * 76 + "\n")
        for i, s in enumerate(summary_list, 1):
            f.write(f"{i:<4}{s['label']:<22}{s['avg']:>12.2f}{s['max']:>12.2f}{s['min']:>12.2f}{s['vram']:>10}\n")
        f.write("-" * 76 + "\n\n")
        f.write("【配置说明与甜点分析】:\n")
        for i, s in enumerate(summary_list, 1):
            f.write(f"  #{i} {s['label']}: {s['desc']} -> 平均 {s['avg']:.2f} tok/s\n")

    # 写入 Markdown 报告
    with open(report_md_path, "w", encoding="utf-8") as f:
        f.write(f"# {model_name} MTP 投机解码速度评测报告\n\n")
        f.write(f"- **测试日期**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- **模型权重**: `{model_path}`\n")
        f.write(f"- **硬件平台**: Tesla V100-PCIE-32GB (Driver: {dm})\n")
        f.write(f"- **测试上下文**: `{ctx}` ({ctx // 1024}K 上下文)\n")
        f.write(f"- **任务配置**: 5 个标准多领域任务，长文本生成 (n_predict={n_predict})\n\n")
        f.write("## 1. 测速排名总表\n\n")
        f.write("| 排名 | 配置名称 | 描述 | 平均速度 (tok/s) | 最高速度 (tok/s) | 最低速度 (tok/s) | 显存 (MiB) |\n")
        f.write("|:----:|:---------|:-----|:----------------:|:----------------:|:----------------:|:----------:|\n")
        for i, s in enumerate(summary_list, 1):
            f.write(f"| {i} | `{s['label']}` | {s['desc']} | **{s['avg']:.2f}** | {s['max']:.2f} | {s['min']:.2f} | {s['vram']} |\n")

        if summary_list:
            top = summary_list[0]
            baseline = next((s for s in summary_list if "off" in s["label"] and "mtp" in s["label"]), None)
            base_spd = baseline["avg"] if baseline else 30.0
            boost_pct = ((top["avg"] - base_spd) / base_spd) * 100.0 if base_spd else 0

            f.write(f"\n## 2. 核心结论与参数推荐\n\n")
            f.write(f"- 🏆 **最佳配置**: `{top['label']}` ({top['desc']})\n")
            f.write(f"- ⚡ **最佳速度**: 平均 **{top['avg']:.2f} tok/s**，单任务峰值 **{top['max']:.2f} tok/s**\n")
            if baseline:
                f.write(f"- 📈 **相对基准(裸速)**: 从 {baseline['avg']:.2f} tok/s 提升到 {top['avg']:.2f} tok/s (**加速 +{boost_pct:.1f}%**)\n")
            f.write(f"\n### 推荐生产参数 (`launcher_main.ps1`):\n```text\n")
            f.write(f"--spec-type draft-mtp --spec-draft-n-max {top['cfg']['nmax']} --spec-draft-n-min 1")
            if top['cfg']['pmin'] > 0:
                f.write(f" --spec-draft-p-min {top['cfg']['pmin']}")
            f.write("\n```\n")

    log("\n" + "=" * 76, master)
    log(f"🎉 模型 {model_name} 测试完成！", master)
    log(f"📊 CSV 结果文件: {results_csv_path}", master)
    log(f"📝 汇总文本: {summary_txt_path}", master)
    log(f"📑 Markdown 报告: {report_md_path}", master)
    if summary_list:
        top = summary_list[0]
        log(f"🏆 冠军配置: {top['label']} ({top['desc']}) -> 平均 {top['avg']:.2f} tok/s", master)
    log("=" * 76, master)
    master.close()

    return {
        "model_name": model_name,
        "summary": summary_list,
        "report_path": report_md_path,
        "csv_path": results_csv_path,
    }


def main():
    parser = argparse.ArgumentParser(description="Qwen3.8-27B 新模型 MTP 速度系统化测试")
    parser.add_argument("--model", choices=["ud", "highest", "mid-high", "all", "new"], default="new",
                        help="选择测试模型: ud (UD-Q6_K_M), highest (HIGHEST), mid-high, new (两个新模型), all (三个模型)")
    parser.add_argument("--mode", choices=["core", "full", "pmin", "smoke"], default="core",
                        help="测试模式: core(推荐，n=1..6核心扫描，约8分钟/模型), full(全网格17套), pmin(截断测试), smoke(冒烟2分钟)")
    parser.add_argument("--ctx", type=int, default=CTX_DEFAULT,
                        help=f"上下文大小 (默认: {CTX_DEFAULT} = 96K)")
    parser.add_argument("--predict", type=int, default=1024,
                        help="每个任务生成 token 数 (默认: 1024)")
    parser.add_argument("--limit", type=int, default=0,
                        help="仅测试前 N 个配置")
    parser.add_argument("--skip", type=int, default=0,
                        help="跳过前 N 个配置")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅打印配置及启动命令，不实际运行")
    args = parser.parse_args()

    targets = []
    if args.model == "ud":
        targets = [MODELS_REGISTRY["ud"]]
    elif args.model == "highest":
        targets = [MODELS_REGISTRY["highest"]]
    elif args.model == "mid-high":
        targets = [MODELS_REGISTRY["mid-high"]]
    elif args.model == "new":
        targets = [MODELS_REGISTRY["ud"], MODELS_REGISTRY["highest"]]
    elif args.model == "all":
        targets = [MODELS_REGISTRY["ud"], MODELS_REGISTRY["highest"], MODELS_REGISTRY["mid-high"]]

    print("=" * 76)
    print("🚀 Qwen3.8-27B MTP 系统化测速套件启动")
    print(f"📦 计划测试模型数: {len(targets)}")
    for t in targets:
        print(f"   - {t['name']} ({t['desc']})")
    print(f"⚙️ 模式: {args.mode} | 上下文: {args.ctx} ({args.ctx // 1024}K)")
    print("=" * 76)

    all_results = []
    for idx, target in enumerate(targets, 1):
        print(f"\n\n==================== [模型 {idx}/{len(targets)}: {target['name']}] ====================")
        res = run_bench_for_model(
            model_info=target,
            mode=args.mode,
            ctx=args.ctx,
            predict=args.predict,
            limit=args.limit,
            skip=args.skip,
            dry_run=args.dry_run
        )
        if res:
            all_results.append(res)
        # 模型间彻底清理并冷却 5 秒
        kill_all_llama()
        time.sleep(5)

    if len(all_results) > 1 and not args.dry_run:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        comp_md = os.path.join(HERE, f"comparison_mtp_27b_models_{ts}.md")
        with open(comp_md, "w", encoding="utf-8") as f:
            f.write("# Qwen3.8-27B 新模型 MTP 性能综合横向对比\n\n")
            f.write(f"- **测试日期**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- **测试上下文**: `{args.ctx}` ({args.ctx // 1024}K)\n\n")
            f.write("## 各模型冠军配置对比\n\n")
            f.write("| 模型名称 | 最佳配置 | 最佳速度 (tok/s) | 基准裸速 (tok/s) | 加速比 | 初始显存 (MiB) |\n")
            f.write("|:---------|:---------|:----------------:|:----------------:|:------:|:--------------:|\n")
            for r in all_results:
                mname = r["model_name"]
                slist = r["summary"]
                if slist:
                    top = slist[0]
                    base = next((s for s in slist if "off" in s["label"] and "mtp" in s["label"]), None)
                    base_s = base["avg"] if base else 30.0
                    boost = ((top["avg"] - base_s) / base_s) * 100.0 if base_s else 0
                    f.write(f"| `{mname}` | `{top['label']}` | **{top['avg']:.2f}** | {base_s:.2f} | **+{boost:.1f}%** | {top['vram']} |\n")
        print("\n" + "=" * 76)
        print(f"📊 多模型横向对比报告已生成: {comp_md}")
        print("=" * 76)


if __name__ == "__main__":
    main()
