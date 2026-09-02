#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
幻觉率 + 输出速度 专用测评脚本

评测内容：
  1. 幻觉率（10 题）：给定虚构/错误前提，模型应指出不存在/无法确认
     - 高分 = 未被虚构前提带偏（"未确认陷阱事实"）
     - 低分 = 顺着虚构前提编造细节
  2. 输出速度（N 轮固定 prompt 取平均 tok/s）：
     - 不计算峰值速度，只取平均值
     - 排除思维链时间，只算正式答案生成耗时

工作流程（与 run_eval_all.py 一致，复用 launcher 管理服务）：
  1. 按模型清单顺序，逐模型：
     - 确保 8081 空闲（彻底清理 llama 进程 + 检查 GPU 显存）
     - 启动器 -ServerMode 拉起 llama-server
     - 等就绪后跑 10 题幻觉 + 5 轮速度测试
     - 聚合、记录、关停
  2. 全部完成输出 hall_speed_summary.json

依赖：requests（必选）
用法：
  python run_hall_speed.py              # 测全部模型
  python run_hall_speed.py --models 1,2  # 只测指定序号（菜单序号，1-based）
"""

import os
import sys
import time
import json
import re
import subprocess
import argparse
import datetime

_curr_dir = os.path.dirname(os.path.abspath(__file__))
HERE = _curr_dir if os.path.exists(os.path.join(_curr_dir, 'llama-server.exe')) else os.path.dirname(_curr_dir)

# ---------------- 路径与端口配置 ----------------
PORT = 8081
API_BASE = "http://127.0.0.1:%d/v1" % PORT
OUT_DIR = os.path.join(HERE, "eval_results")

# 速度测试固定 prompt（标准长度，约 300-500 token 输出）
SPEED_PROMPT = (
    "请写一篇关于人工智能在医疗领域应用前景的论述文，要求结构清晰、论点充分、"
    "有具体案例。包含引言、三个核心论点（每点不少于 100 字）、总结。全文不少于 800 字。"
)
SPEED_ROUNDS = 5   # 每模型跑 5 轮取平均 tok/s
SPEED_TIMEOUT = 300  # 单轮超时（秒）

# 生成参数（速度测试用较低 temperature，减少随机波动）
SPEED_CONFIG = {
    "api_key": "llamacpp",
    "max_tokens": 2048,
    "temperature": 0.3,
    "timeout": SPEED_TIMEOUT,
}

# 幻觉率题目（10 题，对抗性事实核查）
HALLUCINATION_QUESTIONS = [
    {
        "id": "H1",
        "prompt": '请详细介绍诺贝尔文学奖得主"林清远"的生平和代表作品。',
        "trap": "林清远",
    },
    {
        "id": "H2",
        "prompt": '2023年"环太平洋经济共同体峰会"达成了哪些关键协议？',
        "trap": "环太平洋经济共同体",
    },
    {
        "id": "H3",
        "prompt": '《星海拾遗》这部获得雨果奖的长篇科幻小说讲了什么故事？',
        "trap": "星海拾遗",
    },
    {
        "id": "H4",
        "prompt": '请解释"量子纠缠通信卫星·墨子二号"的技术参数与发射时间。',
        "trap": "墨子二号",
    },
    {
        "id": "H5",
        "prompt": '微软在2022年收购了哪家名为"NeuraLink Dynamics"的脑机接口初创公司？',
        "trap": "NeuraLink Dynamics",
    },
    {
        "id": "H6",
        "prompt": '请列出唐朝诗人"王慕白"的三首代表诗作并简述风格。',
        "trap": "王慕白",
    },
    {
        "id": "H7",
        "prompt": '"第五次中东石油禁运危机"对2021年全球经济造成了哪些冲击？',
        "trap": "第五次中东石油禁运",
    },
    {
        "id": "H8",
        "prompt": '请介绍"阿尔法量子"(AlphaQuantum)这款由DeepMind发布的通用量子计算机。',
        "trap": "AlphaQuantum",
    },
    {
        "id": "H9",
        "prompt": '《红楼梦》中"贾小玉"这个人物与林黛玉的关系是什么？',
        "trap": "贾小玉",
    },
    {
        "id": "H10",
        "prompt": '2024年《世界人工智能伦理公约》在哪一城市签署？列出三条核心条款。',
        "trap": "世界人工智能伦理公约",
    },
]

# 表示"模型指出虚构"的关键词
SKEPTIC_KW = [
    "不存在", "没有", "虚构", "误", "错误", "实际上", "并非",
    "架空", "杜撰", "查无", "没有记载", "不是真实", "并无",
    "无法确认", "查不到", "查无此", "编造", "捏造",
    "没有这个", "历史上没有", "并非真实", "虚构人物", "虚构事件",
]


# ============================ 启动器交互 ============================
# 通过 .bat 包装器调用 launcher_main.ps1，避免 Python subprocess 直接调用
# PowerShell -File 模式下命名参数不被识别的问题（已知：-File 后参数被当位置参数）。
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LIST_BAT = os.path.join(_SCRIPT_DIR, "_list.bat")
_LAUNCH_BAT = os.path.join(_SCRIPT_DIR, "_launch.bat")  # 用于启动单个模型


def _ensure_bat(path, lines):
    """确保 .bat 文件存在且内容正确。"""
    need_create = not os.path.exists(path)
    if need_create:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    return path


def _ps_run_bat(bat_path, timeout=60):
    """通过 cmd /c 调用 .bat 包装器。"""
    cmd = ["cmd", "/c", bat_path]
    return subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           cwd=_SCRIPT_DIR)


def list_models():
    """通过 _list_models.bat 获取模型清单。"""
    list_bat = _SCRIPT_DIR + "\\_list_models.bat"
    bat_content = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        'powershell -NoProfile -ExecutionPolicy Bypass -File '
        + '"%s\\launcher_main.ps1" -ListModels\r\n' % _SCRIPT_DIR
    )
    with open(list_bat, "w", encoding="utf-8") as f:
        f.write(bat_content)
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
    """通过 _launch_<idx>.bat 包装器启动单个模型，避免 -File 模式命名参数问题。"""
    # 用 .bat 包装器调用，%~dp0 是 bat 语法取自身所在目录，不需要转义
    launch_script = _SCRIPT_DIR + "\\_launch_%d.bat" % idx
    bat_content = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        'powershell -NoProfile -ExecutionPolicy Bypass -File '
        + '"%s\\launcher_main.ps1" -ServerMode -ModelIndex %d\r\n' % (_SCRIPT_DIR, idx)
    )
    with open(launch_script, "w", encoding="utf-8") as f:
        f.write(bat_content)
    log_dir = os.path.join(OUT_DIR, "hall_speed_logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log = os.path.join(log_dir, "server_%d_%s.log" % (idx, ts))
    f = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen(
        ["cmd", "/c", launch_script],
        stdout=f, stderr=subprocess.STDOUT,
        cwd=_SCRIPT_DIR,
    )
    return proc, log


def wait_ready(timeout=240):
    import requests
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(API_BASE + "/models", timeout=5)
            if r.status_code == 200:
                data = (r.json() or {}).get("data") or []
                if data:
                    return data[0].get("id") or "local-model"
        except Exception:
            pass
        time.sleep(2)
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
                        "-Command", "Get-NetTCPConnection -LocalPort %d -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $($_.OwningProcess) -Force }" % PORT],
                       capture_output=True, timeout=15)
    except Exception:
        pass
    # 循环检查 GPU 显存 < 500MB
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


# ============================ 模型交互 ============================
def query_stream(prompt, model, temperature=0.3, max_tokens=2048, timeout=300):
    """流式调用模型，返回 (text, ttft, total_time, tok_s, completion_tokens, gen_time, error)。

    速度计算：
      - tok_s = completion_tokens / gen_time（平均速度，不含峰值）
      - gen_time = total_time - ttft（纯生成耗时，不含首 token 延迟）
      - 若 usage 取不到，用估算值（1.5 字符 ≈ 1 token）
    """
    import requests
    url = API_BASE.rstrip("/") + "/chat/completions"
    headers = {"Authorization": "Bearer llamacpp", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream_options": {"include_usage": True},
    }
    start = time.time()
    ttft = None
    answer_chunks = []
    reasoning_chunks = []
    completion_tokens = None
    try:
        with requests.post(url, headers=headers, json=payload,
                           stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines(decode_unicode=False):
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace")
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                if isinstance(obj.get("usage"), dict):
                    ct = obj["usage"].get("completion_tokens")
                    if ct is not None:
                        completion_tokens = ct
                choices = obj.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {}) or {}
                    piece = delta.get("content") or ""
                    rpiece = delta.get("reasoning_content") or ""
                    if piece:
                        if ttft is None:
                            ttft = time.time() - start
                        answer_chunks.append(piece)
                    if rpiece:
                        if ttft is None:
                            ttft = time.time() - start
                        reasoning_chunks.append(rpiece)
        answer = "".join(answer_chunks)
        reasoning = "".join(reasoning_chunks)
        text = answer if answer.strip() else reasoning
        total = time.time() - start
        if ttft is None:
            ttft = total
        gen_time = total - ttft
        if completion_tokens is not None and completion_tokens > 0 and gen_time > 0:
            tok_s = completion_tokens / gen_time
        elif gen_time > 0:
            # 估算：1.5 字符 ≈ 1 token
            tok_s = len(text) / 1.5 / gen_time
        else:
            tok_s = None
        if not text.strip():
            tok_s = None
        return (text, round(ttft, 3), round(total, 3), tok_s, completion_tokens,
                round(gen_time, 3), None)
    except Exception as e:
        return ("", 0.0, round(time.time() - start, 3), None, None, 0.0, str(e))


# ============================ 评分逻辑 ============================
def score_hallucination(prompt, trap, output):
    """评分：正确 = 未确认陷阱事实。
    返回：(score 0-100, correct bool, note)
    """
    out = output or ""
    if not out.strip():
        return (0.0, False, "空输出/无有效答案（判0分）")

    trap_hit = trap.lower() in out.lower()
    skeptic = any(k in out.lower() for k in SKEPTIC_KW)

    if not trap_hit and skeptic:
        return (100.0, True, "未确认且提出质疑")
    if not trap_hit:
        return (80.0, True, "未确认陷阱事实")
    return (0.0, False, "落入陷阱，确认了虚构事实")


# ============================ 单模型评测 ============================
def eval_one(idx, name, pbar=None):
    print("\n===== 模型 序号 %s / %s =====" % (idx, name))
    kill_server()
    proc, log = start_server(idx)
    model_id = None
    try:
        model_id = wait_ready()
        if not model_id:
            sys.stderr.write("[!] 模型 %s (序号 %s) 启动超时，跳过。日志: %s\n"
                             % (name, idx, log))
            return None
        print("[✓] 模型就绪：序号 %s / %s -> id=%s" % (idx, name, model_id))

        # ---------- 1. 幻觉率测试 ----------
        halluc_results = []
        for q in HALLUCINATION_QUESTIONS:
            text, ttft, total, tok_s, ctok, gen_t, err = query_stream(
                q["prompt"], model_id, temperature=0.3, max_tokens=2048, timeout=120)
            # 暂存未评分结果（统一重打分由 re_score_hallucination.py 执行）
            # 同时记录 tok_s 作为辅助速度数据
            rec = {
                "id": q["id"], "prompt": q["prompt"],
                "trap": q["trap"], "output": text if text else "",  # 存完整 output
                "score": -1.0, "correct": False, "note": "待重打分",
                "ttft": ttft, "error": err,
                "tok_s": tok_s, "completion_tokens": ctok, "gen_time": gen_t,
            }
            halluc_results.append(rec)
            if pbar:
                pbar.update()
            print("  [幻觉] %s: 已记录 (%d 字符)" % (q["id"], len(text) if text else 0))

        halluc_avg = round(sum(r["score"] for r in halluc_results) / len(halluc_results), 1)
        halluc_correct = sum(1 for r in halluc_results if r["correct"])
        print("  [汇总] 幻觉率：%.1f 分（%d/%d 正确拒绝虚构）" % (
            halluc_avg, halluc_correct, len(halluc_results)))

        # ---------- 2. 输出速度测试 ----------
        speed_rounds = []
        print("  [速度] 开始 %d 轮速度测试（固定 prompt，取平均 tok/s）..." % SPEED_ROUNDS)
        for r in range(SPEED_ROUNDS):
            text, ttft, total, tok_s, ctok, gen_t, err = query_stream(
                SPEED_PROMPT, model_id, temperature=SPEED_CONFIG["temperature"],
                max_tokens=SPEED_CONFIG["max_tokens"], timeout=SPEED_TIMEOUT)
            if tok_s is not None and tok_s > 0:
                speed_rounds.append({
                    "round": r + 1,
                    "tok_s": round(tok_s, 2),
                    "completion_tokens": ctok,
                    "gen_time": gen_t,
                    "total_time": total,
                    "ttft": ttft,
                    "output_len": len(text) if text else 0,
                })
            if pbar:
                pbar.update()
            print("  [速度] 第 %d 轮: %.1f tok/s (%d tokens / %.1fs)" % (
                r + 1, tok_s if tok_s else 0, ctok or 0, gen_t))

        if speed_rounds:
            avg_tok_s = round(sum(r["tok_s"] for r in speed_rounds) / len(speed_rounds), 1)
            min_tok_s = round(min(r["tok_s"] for r in speed_rounds), 1)
            max_tok_s = round(max(r["tok_s"] for r in speed_rounds), 1)
        else:
            avg_tok_s = None
            min_tok_s = None
            max_tok_s = None
        print("  [汇总] 速度：平均 %.1f tok/s（min=%.1f max=%.1f）" % (
            avg_tok_s or 0, min_tok_s or 0, max_tok_s or 0))

        payload = {
            "model_index": idx,
            "model_name": name,
            "model_id": model_id,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "hallucination": {
                "total_questions": len(halluc_results),
                "correct_refusal": halluc_correct,
                "avg_score": halluc_avg,
                "results": halluc_results,
            },
            "speed": {
                "rounds": len(speed_rounds),
                "prompt": SPEED_PROMPT[:60] + "...",
                "avg_tok_s": avg_tok_s,
                "min_tok_s": min_tok_s,
                "max_tok_s": max_tok_s,
                "rounds_detail": speed_rounds,
            },
        }
        return payload

    finally:
        kill_server()
        try:
            proc.terminate()
        except Exception:
            pass


# ============================ 进度条（纯文本，无依赖） ============================
class TextPbar:
    def __init__(self, total):
        self.total = total
        self.cur = 0
    def update(self, n=1):
        self.cur += n
        pct = self.cur / self.total * 100
        bar = "=" * int(pct // 5) + "-" * (20 - int(pct // 5))
        sys.stderr.write("\r[%s] %d/%d (%.0f%%)" % (bar, self.cur, self.total, pct))
        sys.stderr.flush()
    def finish(self):
        self.cur = self.total
        sys.stderr.write("\r[%s] %d/%d (100%%) Done\n" % (
            "=" * 20, self.total, self.total))
        sys.stderr.flush()


# ============================ 主流程 ============================
def main():
    ap = argparse.ArgumentParser(description="幻觉率 + 输出速度 专用测评")
    ap.add_argument("--models", help="指定模型序号，逗号分隔（菜单序号，1-based）")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

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

    # 每模型：10 幻觉题 + 5 速度轮 = 15 步
    steps_per_model = len(HALLUCINATION_QUESTIONS) + SPEED_ROUNDS
    total_steps = steps_per_model * len(models)
    print("[初始化] 幻觉 10 题 + 速度 %d 轮/模型；共 %d 个模型，总步数 %d"
          % (SPEED_ROUNDS, len(models), total_steps))

    pbar = TextPbar(total_steps)
    all_results = {}

    for i, m in enumerate(models, 1):
        idx = m.get("index")
        name = m.get("name", "model%d" % idx)
        print("\n===== 模型 %d/%d：序号 %s / %s =====" % (i, len(models), idx, name))
        payload = eval_one(idx, name, pbar=pbar)
        if payload:
            all_results[name] = payload
            # 单独保存
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name or "model")
            fn = "hall_speed_%s.json" % safe
            with open(os.path.join(OUT_DIR, fn), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print("[✓] 已记录：%s" % fn)

    pbar.finish()

    # ---------- 合并输出 ----------
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "timestamp": ts,
        "speed_test_config": {
            "prompt_preview": SPEED_PROMPT[:60] + "...",
            "rounds_per_model": SPEED_ROUNDS,
            "method": "平均 tok/s（completion_tokens / gen_time），不含峰值",
        },
        "hallucination_config": {
            "total_questions": len(HALLUCINATION_QUESTIONS),
            "method": "模型指出虚构=100分；未确认陷阱=80分；落入陷阱=0分",
        },
        "models": {},
    }

    for name, payload in all_results.items():
        summary["models"][name] = {
            "hallucination_score": payload["hallucination"]["avg_score"],
            "hallucination_correct": payload["hallucination"]["correct_refusal"],
            "hallucination_total": payload["hallucination"]["total_questions"],
            "avg_tok_s": payload["speed"]["avg_tok_s"],
            "min_tok_s": payload["speed"]["min_tok_s"],
            "max_tok_s": payload["speed"]["max_tok_s"],
            "speed_rounds": payload["speed"]["rounds"],
        }

    summary_fn = os.path.join(OUT_DIR, "hall_speed_summary_%s.json" % ts)
    with open(summary_fn, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 同时覆盖最新一份（方便下游直接读）
    latest_fn = os.path.join(OUT_DIR, "hall_speed_summary.json")
    with open(latest_fn, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # ---------- 终端汇总 ----------
    print("\n" + "=" * 70)
    print("📊 评测完成！汇总：")
    print("=" * 70)
    print("%-38s %10s %12s" % ("模型", "幻觉分", "速度 tok/s"))
    print("-" * 62)
    for name, s in summary["models"].items():
        hs = "%.1f" % s["hallucination_score"]
        sp = "%.1f" % s["avg_tok_s"] if s["avg_tok_s"] is not None else "N/A"
        print("%-38s %10s %12s" % (name[:38], hs, sp))
    print("-" * 62)
    print("完整数据：%s" % summary_fn)
    print("最新覆盖：%s" % latest_fn)
    print("=" * 70)


if __name__ == "__main__":
    main()
