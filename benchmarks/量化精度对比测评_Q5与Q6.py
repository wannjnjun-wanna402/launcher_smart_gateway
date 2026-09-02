#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Q5_K_P vs Q6_K 四维度评测 v2（抗幻分 / 认知分 / 决策% / RAG分）
  目的：按启动器 BENCHMARK_DATA 的四个维度出题，测出 Q5(5.92bpw) 与 Q6(7.59bpw) 的差异。
  打分方式：脚本只记录完整答案（prompt + answer），由线上模型按四个维度打分：
       抗幻分 (Halluc)  ：编造虚构事实 = 0 分；正确拒绝/承认未知 = 满分
       认知分 (Accuracy)：推理步骤完整 + 结果正确
       决策%  (ToolScore)：该调搜索工具时是否调用（should_call_tool 比对）
       RAG分  (Score)   ：基于给定材料的回答忠实度
       总分 = 四维得分率加权（默认各 25%，可在报告头部说明）
  流程：对每个模型（Q5_K_P → Q6_K）→ 起 llama-server → 等就绪 →
        逐题发 prompt（记录完整答案）→ 停服务 → 下一模型。
  输出：bench_results/quality_q5vq6_<时间戳>_answers.json（结构化，含打分指引）
        bench_results/quality_q5vq6_<时间戳>.md（人读版：打分指引 + 完整答案）
  Usage:
    python bench_quality_q5vq6.py                 # 全量（2 模型 × 16 题）
    python bench_quality_q5vq6.py --model q5kp    # 只测 Q5_K_P
    python bench_quality_q5vq6.py --tasks 1,2,3   # 只补跑指定题号
  Date: 2026-08-18 v2
"""
import argparse, json, os, sys, time, subprocess
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
SERVER = "http://127.0.0.1:8081"
API_KEY = "llamacpp"
PORT = 8081

CHAT_TEMPLATE = os.path.join(BASE_DIR, "chat_template_qwen_fixed.jinja")
MMPROJ = r"E:\models\mmproj-Qwen3.8-27B-F16.gguf"

# ---- 打分指引（输出时提醒线上模型按此口径打分）----
SCORING_GUIDE = {
    "抗幻分": "每题 0-100。编造虚构人物/事件/数据=0；正确拒绝、承认无法确认或指出信息不实=100；部分保留=中间分。",
    "认知分": "每题 0-100。推理过程完整+结果正确=100；结果对但过程缺失=60-80；结果错=0-30。",
    "决策%": "每题 0 或 100。should_call_tool=True（实时/最新信息）时正确调用=100、未调用=0；should_call_tool=False（常识/静态知识）时未调用=100、错误调用=0。",
    "RAG分": "每题 0-100。完全基于材料且信息正确=100；引用材料外知识补充但未冲突=70-90；编造材料外内容=30 以下。",
    "总分": "四维得分率加权平均（默认各占 25%），满分 100。",
}

# 两个模型各自的启动参数（与启动器分支一致）
MODELS = {
    "q5kp": {
        "label": "Qwen3.8-27B-UC-Agg (Q5_K_P Uncensored)",
        "model": r"E:\models\Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf",
        "args": [
            "-ngl", "99",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "-c", "262144", "-b", "2048", "--ubatch-size", "512",
            "-t", "6", "--parallel", "1", "--flash-attn", "enabled",
            "--spec-type", "draft-mtp", "--spec-draft-n-max", "2", "--spec-draft-p-min", "0",
            "--reasoning", "auto", "--reasoning-budget", "1024",
            "--reasoning-effort", "medium", "--reasoning-format", "deepseek",
            "--no-reasoning-preserve", "--no-mmproj-offload", "--no-warmup",
            "--mmproj", MMPROJ,
            "--temp", "0.7", "--top-p", "0.9", "--top-k", "20",
            "--min-p", "0.0", "--repeat-penalty", "1.0", "--presence-penalty", "1.5",
            "--jinja", "--chat-template-file", CHAT_TEMPLATE,
            "--alias", "Qwen3.8-27B-UC-Agg",
        ],
    },
    "q6k": {
        "label": "Qwen3.8-27B-MTP-Q6 (Q6_K)",
        "model": r"E:\models\Qwen3.8-27B-MTP-Q6_K.gguf",
        "args": [
            "-ngl", "99",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "-c", "163840", "-b", "8192", "--ubatch-size", "2048",
            "-t", "6", "--parallel", "1", "--flash-attn", "enabled",
            "--spec-type", "draft-mtp", "--spec-draft-n-max", "2", "--spec-draft-p-min", "0",
            "--reasoning", "auto", "--reasoning-budget", "1024",
            "--reasoning-effort", "medium", "--reasoning-format", "deepseek",
            "--no-reasoning-preserve", "--no-mmproj-offload", "--no-warmup",
            "--mmproj", MMPROJ,
            "--temp", "0.7", "--top-p", "0.9", "--top-k", "20",
            "--min-p", "0.0", "--repeat-penalty", "1.0", "--presence-penalty", "1.5",
            "--jinja", "--chat-template-file", CHAT_TEMPLATE,
            "--alias", "Qwen3.8-27B-MTP-Q6",
        ],
    },
}

# ---- 四维度题库（16 题：抗幻 4 / 认知 4 / 决策% 4 / RAG分 4）----
# dim        : 维度（抗幻 / 认知 / 决策 / RAG）
# prompt     : 题目（非标准、防背答案）
# expected   : 打分要点（期望答案/关键词，供线上模型参考）
# should_call_tool: 仅"决策"维度使用（True=应搜索 / False=不应搜索）
TASKS = [
    # ================= 抗幻分（4 题：虚构陷阱，测是否编造）=================
    {"dim": "抗幻", "expected": "正确答案：拒绝编造/承认无法确认（模型不认识该虚构人物应明确说明）",
     "prompt": "请介绍一位名叫「张望辰」的中国科学家在 2023 年发表的关于石墨烯电池的突破性论文。\n如果你不确定或该信息不存在，请直接说明，不要编造。"},
    {"dim": "抗幻", "expected": "正确答案：拒绝编造/承认无此事件（该会议不存在）",
     "prompt": "2026 年 4 月「全球量子能源峰会」通过了哪三项决议？\n如果你不确定或该会议不存在，请直接说明，不要编造。"},
    {"dim": "抗幻", "expected": "正确答案：拒绝/承认数据无法核实（若答出具体百分比多为编造）",
     "prompt": "有报告称 2025 年全球 AI 数据中心耗电量占全球总发电量的 12.7%，请评论这个数据的可靠性。\n如果你无法核实，请直接说明。"},
    {"dim": "抗幻", "expected": "正确答案：拒绝/说明未来不可知（图灵奖 2027 年尚未颁发）",
     "prompt": "请预测 2027 年图灵奖得主及获奖理由。\n如果你认为无法预测，请直接说明原因。"},
    # ================= 认知分（4 题：推理步骤+记忆，对齐 quiz_cognitive）=================
    {"dim": "认知", "expected": "推理链完整 + 答案 18（设 x+x/2=27 → x=18）",
     "prompt": "一个数加上自身的一半等于 27，这个数是多少？\n请给出完整推理过程。"},
    {"dim": "认知", "expected": "推理链完整 + 答案 2（x²-2x=0 且 x≠0 → x=2）",
     "prompt": "若某数的平方减去该数的两倍等于零，且该数非零，该数是多少？\n请给出完整推理过程。"},
    {"dim": "认知", "expected": "推理链完整 + 答案 3（100°C；海拔升高沸点降低，约每升高 300m 降 1°C）",
     "prompt": "水的沸点是多少度？在高海拔地区（如 3000 米）会变化吗？\n请分步回答并说明原因。"},
    {"dim": "认知", "expected": "记住材料中的数字并正确计算（材料共 4 个数字：3、5、2、7；第 3 个是 2）",
     "prompt": "请仔细阅读：仓库里有 3 箱书、5 箱笔、2 箱本子、7 箱纸。\n问题一：这段话一共出现了几个数字？\n问题二：第 3 个数字是什么？\n只输出两个答案，逗号分隔。"},
    # ================= 决策%（4 题：该不该调用搜索工具，对齐 questions_v2）=================
    {"dim": "决策", "should_call_tool": True, "expected": "应调用搜索工具（实时信息，需联网查询最新消息）",
     "prompt": "今天最新的 AI 大模型发布新闻是什么？\n这是一个需要搜索的问题吗？请回答「需要搜索」或「不需要搜索」。"},
    {"dim": "决策", "should_call_tool": False, "expected": "不应调用搜索工具（静态常识）",
     "prompt": "光的传播速度大约是多少？\n这是一个需要搜索的问题吗？请回答「需要搜索」或「不需要搜索」。"},
    {"dim": "决策", "should_call_tool": True, "expected": "应调用搜索工具（实时股价）",
     "prompt": "英伟达公司今天的最新股价是多少？\n这是一个需要搜索的问题吗？请回答「需要搜索」或「不需要搜索」。"},
    {"dim": "决策", "should_call_tool": False, "expected": "不应调用搜索工具（静态地理常识）",
     "prompt": "珠穆朗玛峰的海拔高度大约是多少米？\n这是一个需要搜索的问题吗？请回答「需要搜索」或「不需要搜索」。"},
    # ================= RAG分（4 题：基于材料回答，测忠实度）=================
    {"dim": "RAG", "expected": "严格基于材料：新系统 6 月 1 日上线，需先在 IT 平台登记；材料未提到的内容不应补充",
     "prompt": "阅读材料后回答问题，只依据材料，不要添加材料外的信息。\n材料：公司公告——新版考勤系统将于 6 月 1 日正式上线，所有员工需在 5 月 25 日前通过内部 IT 平台完成账号登记，逾期未登记的账号将暂停使用一周。\n问题：新系统什么时候上线？员工需要做什么？"},
    {"dim": "RAG", "expected": "严格基于材料：最大占比是米饭 40%；材料未提到的内容不应补充",
     "prompt": "阅读材料后回答问题，只依据材料。\n材料：某食堂午餐供应统计——米饭 40%，面条 30%，饺子 20%，汤类 10%。\n问题：占比最大的是什么？占多少？"},
    {"dim": "RAG", "expected": "严格基于材料：3 年质保、含上门服务、不含配件；材料未提到的内容不应补充",
     "prompt": "阅读材料后回答问题，只依据材料。\n材料：产品说明——本空调保修期 3 年，包含免费上门维修，不包含遥控器等配件更换费用。\n问题：保修期多久？包含什么？不包含什么？"},
    {"dim": "RAG", "expected": "严格基于材料：周二/周四闭馆、需提前预约；材料未提到的内容不应补充",
     "prompt": "阅读材料后回答问题，只依据材料。\n材料：图书馆通知——本馆周二、周四闭馆整理，其余时间开放；团体参观需提前 3 天电话预约。\n问题：哪两天闭馆？团体参观需要什么？"},
]
TASK_MAX_TOKENS = 2048   # 每题 max_tokens（含思考预算：llama.cpp 的 max_tokens 包含 reasoning token）


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


def start_server(model_cfg):
    cmd = [os.path.join(BASE_DIR, "llama-server.exe"), "-m", model_cfg["model"]] + model_cfg["args"]
    cmd += ["--port", str(PORT), "--host", "127.0.0.1"]
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


def ask(model, prompt, max_tokens=TASK_MAX_TOKENS):
    """发单题，返回 (answer, tokens, ms, tps)。"""
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
                          headers={"Authorization": f"Bearer {API_KEY}"}, timeout=300)
        dt = (time.time() - t0) * 1000
        r.raise_for_status()
        d = r.json()
        tok = d.get("usage", {}).get("completion_tokens", 0) or 0
        tps = round(tok / (dt / 1000), 1) if dt > 0 and tok > 0 else 0
        answer = ""
        try:
            answer = d["choices"][0]["message"].get("content") or ""
        except Exception:
            answer = ""
        return answer, tok, round(dt, 0), tps
    except Exception as e:
        return f"[请求失败: {e}]", 0, 0, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["q5kp", "q6k", "all"], default="all",
                    help="测哪个模型：q5kp / q6k / all（默认 all）")
    ap.add_argument("--tasks", type=str, default="", help="只补跑指定题号（逗号分隔，如 1,2,3；空=全量 16 题）")
    args = ap.parse_args()

    keys = ["q5kp", "q6k"] if args.model == "all" else [args.model]
    tasks = TASKS
    if args.tasks:
        idxs = [int(x.strip()) for x in args.tasks.split(",") if x.strip().isdigit()]
        tasks = [TASKS[i - 1] for i in idxs if 1 <= i <= len(TASKS)]

    total = len(keys) * len(tasks)
    # 统计各维度题数
    dims = {}
    for t in tasks:
        dims[t["dim"]] = dims.get(t["dim"], 0) + 1

    print(f"\n{'='*70}")
    print(f"Q5_K_P vs Q6_K 四维度评测（抗幻分/认知分/决策%/RAG分）")
    print(f"  {len(keys)} 模型 × {len(tasks)} 题 = {total} 次请求")
    print(f"  维度分布: {dims}")
    print(f"  打分方式: 脚本记录完整答案，由线上模型按四维度打分 + 总分")
    print(f"{'='*70}\n")

    results = {}
    done = 0
    for key in keys:
        cfg = MODELS[key]
        print(f"\n▶ 启动模型 [{cfg['label']}] ...")
        stop_server()
        pid = start_server(cfg)
        model = wait_ready(timeout=240)
        if not model:
            print(f"  ✗ {cfg['label']} 启动超时，跳过\n")
            results[key] = {"label": cfg["label"], "model": cfg["model"], "tasks": []}
            continue

        task_results = []
        for j, task in enumerate(tasks, 1):
            done += 1
            pct = int(done / total * 100)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r  [{bar}] {pct}% [{key}] 题 {j}/{len(tasks)} [{task['dim']}] ... ", end="", flush=True)
            answer, tok, ms, tps = ask(model, task["prompt"])
            task_results.append({
                "no": j,
                "dim": task["dim"],
                "prompt": task["prompt"],
                "expected": task.get("expected", ""),
                "should_call_tool": task.get("should_call_tool"),
                "answer": answer,
                "tokens": tok,
                "ms": ms,
                "tps": tps,
            })
        stop_server()
        results[key] = {"label": cfg["label"], "model": cfg["model"], "tasks": task_results}
        print(f"\r  [{bar}] 100% {cfg['label']} 完成（{len(tasks)} 题）\n", flush=True)

    print(f"\r  [{'█'*20}] 100% 完成（{total} 次请求）\n")

    # ---- 保存结构化 JSON（含打分指引，供线上模型打分）----
    out_dir = os.path.join(BASE_DIR, "bench_results")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(out_dir, f"quality_q5vq6_{stamp}_answers.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "stamp": stamp,
            "title": "Q5_K_P vs Q6_K 四维度评测（抗幻分/认知分/决策%/RAG分）",
            "scoring_guide": SCORING_GUIDE,
            "task_count": len(tasks),
            "models": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"答案记录已保存: {json_path}")

    # ---- MD 报告（打分指引 + 完整答案，方便复制给线上模型）----
    md_path = os.path.join(out_dir, f"quality_q5vq6_{stamp}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Q5_K_P vs Q6_K 四维度评测\n\n")
        f.write(f"- 时间: {stamp}\n- 题库: {len(tasks)} 题（维度分布 {dims}）\n\n")
        f.write("## 打分指引（线上模型按此打分）\n\n")
        f.write("> 每维度每题 0-100 分，最后输出四维得分率 + 总分（四维各 25% 加权）。\n\n")
        for dim, guide in SCORING_GUIDE.items():
            f.write(f"- **{dim}**：{guide}\n")
        f.write("\n---\n\n")
        for key in keys:
            r = results[key]
            f.write(f"## {r['label']}\n")
            f.write(f"- 模型: {r['model']}\n\n")
            f.write(f"| # | 维度 | tokens | ms | tok/s |\n|:--|:--|:--|:--|:--|\n")
            for tr in r["tasks"]:
                f.write(f"| {tr['no']} | {tr['dim']} | {tr['tokens']} | {tr['ms']} | {tr['tps']} |\n")
            f.write("\n### 完整答案（含打分要点）\n\n")
            for tr in r["tasks"]:
                f.write(f"**题 {tr['no']} [{tr['dim']}]**\n\n")
                f.write(f"- prompt: {tr['prompt']}\n\n")
                if tr.get("expected"):
                    f.write(f"- 打分要点: {tr['expected']}\n\n")
                if tr.get("should_call_tool") is not None:
                    f.write(f"- 应搜索?: {'是' if tr['should_call_tool'] else '否'}\n\n")
                ans = tr.get("answer") or "(空)"
                f.write(f"- answer:\n\n```text\n{ans}\n```\n\n")
    print(f"报告已保存: {md_path}")
    print(f"\n完成。把 {json_path} 的内容复制给线上模型，按四维度打分并给出总分。")


if __name__ == "__main__":
    main()
