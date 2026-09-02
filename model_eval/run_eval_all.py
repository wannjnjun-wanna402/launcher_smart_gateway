#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键评测 orchestrator（逐模型自动测 + 自动记录 + 进度）

真正的一键：本脚本自己接管“关旧服务 → 起下一个模型 → 跑题库(默认 50 题) → 记录 → 下一个”的循环。
复用 run_test_suite.py 的题库 / 评分 / 报告逻辑（import，不重造轮子）。

工作流程：
  1. 调用启动器的 -ListModels 拿到模型清单（菜单序号 -> 模型名/类别）
  2. 按清单顺序，对每个模型：
     - 确保 8081 空闲（杀掉残留服务）
     - 用启动器 -ServerMode -ModelIndex N 在隐藏窗口拉起 llama-server（前台阻塞于子进程）
     - 轮询 /v1/models 等到就绪，记下真实 model id
     - 跑 build_questions() 全部题目（单一 tqdm 进度条贯穿所有模型所有题）
     - aggregate 聚合并生成该模型的 report_<name>.html + results_<name>.json（自动记录）
     - 按端口杀掉本模型服务，进入下一个
  3. 全部完成后生成合并对比报告 report_all.html（雷达叠加 + 对比表 + window.RESULTS）

依赖：requests（必选）、rich/tqdm（可选，进度美化）
用法：
  python run_eval_all.py                 # 按清单顺序测全部模型
  python run_eval_all.py --models 1,2,3  # 只测指定序号（菜单序号，1-based）
  python run_eval_all.py --limit 20      # 每模型只跑前 20 题（快速冒烟）
"""

import os
import sys
import time
import json
import re
import subprocess
import argparse
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import run_test_suite as suite
except ImportError as e:
    sys.stderr.write("无法导入 run_test_suite.py（题库/评分所在）：%s\n" % e)
    sys.stderr.write("请确认 run_eval_all.py 与 run_test_suite.py 在同一目录。\n")
    sys.exit(2)

# ---------------- 路径与端口配置 ----------------
# 启动器脚本（与 llama-server.exe 同目录，自定位，改名不影响）
LAUNCHER = os.path.join(os.path.dirname(HERE), "launcher_main.ps1")
PORT = 8081
API_BASE = "http://127.0.0.1:%d/v1" % PORT
OUT_DIR = os.path.join(HERE, "eval_output")


# ============================ 启动器交互 ============================
def _ps_run(args, timeout=60):
    """以不显示窗口方式调用 powershell。args 为参数列表。"""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"] + args
    return subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)


def list_models():
    """调用启动器 -ListModels，解析 ASCII JSON 模型清单。"""
    res = _ps_run(["-File", LAUNCHER, "-ListModels"], timeout=120)
    text = (res.stdout or "")
    # 找到以 [ 开头的 JSON 行（避免其他噪音）
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            try:
                arr = json.loads(s)
                if isinstance(arr, list) and arr:
                    return arr
            except Exception:
                pass
    raise RuntimeError("无法解析模型清单。启动器输出：\n" + text[-800:])


def start_server(idx):
    """隐藏窗口拉起 llama-server（前台阻塞于子进程），日志写入文件。返回 (proc, log_path)。"""
    log_dir = os.path.join(OUT_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log = os.path.join(log_dir, "server_%d.log" % idx)
    f = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
         "-File", LAUNCHER, "-ServerMode", "-ModelIndex", str(idx)],
        stdout=f, stderr=subprocess.STDOUT,
    )
    return proc, log


def wait_ready(timeout=240):
    """轮询 /v1/models，返回真实 model id；超时返回 None。"""
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


def wait_stopped(timeout=60):
    """等到 8081 不再响应（服务已停）。"""
    import requests
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(API_BASE + "/models", timeout=3)
        except Exception:
            return True
        time.sleep(1)
    return False


def kill_server():
    """按端口 8081 找到监听进程并杀掉整棵进程树（taskkill /T /F）。"""
    pids = set()
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, timeout=20).stdout
        for line in out.splitlines():
            if (":%d " % PORT) in line and "LISTENING" in line:
                parts = line.split()
                if parts:
                    pids.add(parts[-1])
    except Exception:
        pass
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                           capture_output=True, timeout=20)
        except Exception:
            pass
    time.sleep(3)
    wait_stopped(30)


# ============================ 单模型评测 ============================
def eval_one(meta, pbar, limit=0):
    """对单个模型完成：启动→就绪→全题评测→聚合→记录→关停。返回该模型 payload 或 None。"""
    idx = meta.get("index")
    name = meta.get("name", "model%d" % idx)
    # 先确保端口空闲，避免测到上一个残留服务
    kill_server()

    proc, log = start_server(idx)
    try:
        model_id = wait_ready()
        if not model_id:
            sys.stderr.write("\n[!] 模型 %s (序号 %s) 启动超时，跳过。日志: %s\n" % (name, idx, log))
            return None
        print("\n[✓] 模型就绪：序号 %s / %s  ->  id=%s" % (idx, name, model_id))

        cfg = dict(suite.CONFIG)
        cfg["api_base"] = API_BASE
        cfg["model"] = model_id
        cfg["output_dir"] = OUT_DIR
        cfg["timeout"] = 300  # 35B 长推理放宽

        questions = suite.build_questions()
        if limit > 0:
            questions = questions[:limit]
        n = len(questions)

        results = []
        for qi, q in enumerate(questions, 1):
            text, ttft, total, tok_s, ctokens, gen_time, err = suite.query_model(q["prompt"], cfg)
            sc = suite.score_question(q, text)
            rec = {
                "id": q["id"], "dim": q["dim"], "prompt": q["prompt"],
                "output": text, "ttft": ttft, "total_time": total,
                "error": err, "tok_s": tok_s, "completion_tokens": ctokens,
                "gen_time": gen_time, "scorer": q["scorer"], "score": sc,
            }
            results.append(rec)
            pbar.update(desc="模型%s %s 题%d/%d" % (idx, name[:16], qi, n))

        # 聚合
        dims_summary, speed = suite.aggregate(results)
        score_vals = [dims_summary[d]["avg_score"] for d in suite.DIMENSIONS
                      if dims_summary[d]["avg_score"] is not None]
        overall = round(sum(score_vals) / len(score_vals), 1) if score_vals else 0.0
        auto_total = sum(1 for r in results if r["score"].get("auto")
                         and r["score"].get("correctness") is not None)
        auto_ok = sum(1 for r in results if r["score"].get("auto")
                      and r["score"].get("correctness") is True)
        auto_rate = round(100.0 * auto_ok / auto_total, 1) if auto_total else None

        meta_info = {
            "model": model_id, "model_index": idx, "model_name": name,
            "api_base": API_BASE,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_questions": len(results),
            "sizeGB": meta.get("sizeGB"), "category": meta.get("category"),
        }
        payload = {
            "meta": meta_info,
            "overall": overall,
            "auto_rate": auto_rate,
            "speed": speed,
            "dim_order": suite.DIMENSIONS,
            "dims": dims_summary,
            "results": [
                {k: r[k] for k in ("id", "dim", "prompt", "output",
                                   "ttft", "total_time", "error",
                                   "tok_s", "completion_tokens", "gen_time", "score")}
                for r in results
            ],
        }
        return payload
    finally:
        kill_server()
        try:
            proc.terminate()
        except Exception:
            pass


# ============================ 合并对比报告 ============================
def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name or "model")


def gen_combined(payloads):
    """生成 report_all.html：对比表 + 雷达叠加 + 综合条形图 + 逐模型明细 + window.RESULTS。"""
    dims = suite.DIMENSIONS
    models = [p["meta"]["model_name"] for p in payloads]
    overalls = [p["overall"] for p in payloads]
    best_i = overalls.index(max(overalls)) if overalls else 0

    # 对比表行
    rows = []
    for p in payloads:
        m = p["meta"]
        d = p["dims"]
        cells = []
        for dim in dims:
            v = d.get(dim, {}).get("avg_score")
            cells.append("—" if v is None else ("%.1f" % v))
        rows.append({
            "name": m["model_name"], "overall": p["overall"],
            "auto_rate": p["auto_rate"], "p50": d.get("响应速度", {}).get("p50_ttft"),
            "p95": d.get("响应速度", {}).get("p95_ttft"),
            "tok_s": p["speed"].get("avg_tok_s"), "cells": cells,
        })

    # 雷达 datasets
    radar_datasets = []
    palette = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed",
               "#0891b2", "#db2777", "#65a30d", "#ea580c", "#4f46e5"]
    for i, p in enumerate(payloads):
        data = []
        for dim in dims:
            v = p["dims"].get(dim, {}).get("avg_score")
            data.append(0 if v is None else v)
        radar_datasets.append({
            "label": p["meta"]["model_name"],
            "data": data,
            "borderColor": palette[i % len(palette)],
            "backgroundColor": palette[i % len(palette)] + "22",
            "borderWidth": 2,
        })

    combined = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "models": payloads,
    }

    # 逐模型明细（折叠）：维度表 + 逐题表
    detail_html = []
    for p in payloads:
        m = p["meta"]
        d = p["dims"]
        dim_tr = "".join(
            "<tr><td>%s</td><td>%.1f</td><td>%s</td><td>%s</td></tr>" % (
                dim,
                d.get(dim, {}).get("avg_score") or 0,
                d.get(dim, {}).get("auto_rate") if d.get(dim, {}).get("auto_rate") is not None else "—",
                d.get(dim, {}).get("n", 0),
            ) for dim in dims
        )
        speed = d.get("响应速度", {})
        qrows = []
        for r in p["results"]:
            sc = r.get("score", {})
            qrows.append(
                "<tr><td>%s</td><td>%s</td><td>%.1f</td><td>%s</td><td>%.1f</td><td>%s%s</td></tr>" % (
                    r.get("id"), r.get("dim"),
                    sc.get("qscore") if sc.get("qscore") is not None else 0,
                    r.get("ttft"),
                    r.get("tok_s") if r.get("tok_s") is not None else 0,
                    ("✓" if sc.get("correctness") is True else ("✗" if sc.get("correctness") is False else "—")),
                    (" · %s" % sc.get("note", "")) if sc.get("note") else "",
                )
            )
        qrows_html = "".join(qrows)
        detail_html.append("""
        <details>
          <summary>🧩 %s —— 综合 %.1f 分 · 自动正确率 %s · TTFT p50 %ss / p95 %ss</summary>
          <div class="panel" style="margin-top:8px">
            <h2>维度评分</h2>
            <table><thead><tr><th>维度</th><th>得分</th><th>自动正确率</th><th>题数</th></tr></thead>
            <tbody>%s</tbody></table>
            <p class="meta">响应速度：p50=%ss p95=%ss 速度分=%s 平均TTFT=%ss</p>
            <h2>逐题明细（%d 题）</h2>
            <div class="scroll">
              <table><thead><tr><th>ID</th><th>维度</th><th>分</th><th>TTFT</th><th>tokens/s</th><th>判定</th></tr></thead>
              <tbody>%s</tbody></table>
            </div>
          </div>
        </details>
        """ % (
            m["model_name"], p["overall"],
            ("%.1f%%" % p["auto_rate"] if p["auto_rate"] is not None else "—"),
            speed.get("p50_ttft", "—"), speed.get("p95_ttft", "—"),
            dim_tr,
            speed.get("p50_ttft", "—"), speed.get("p95_ttft", "—"),
            speed.get("speed_score", "—"), speed.get("avg_ttft", "—"),
            len(p["results"]), qrows_html,
        ))
    detail_html = "".join(detail_html)

    # 对比表 HTML
    header_cells = "".join("<th>%s</th>" % d for d in dims)
    table_rows = []
    for r in rows:
        tds = "".join("<td>%s</td>" % c for c in r["cells"])
        table_rows.append(
            "<tr><td><b>%s</b></td><td><b>%.1f</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>%s</tr>" % (
                r["name"], r["overall"],
                ("%.1f%%" % r["auto_rate"] if r["auto_rate"] is not None else "—"),
                r["p50"] if r["p50"] is not None else "—",
                r["p95"] if r["p95"] is not None else "—",
                ("%.1f" % r["tok_s"] if r["tok_s"] is not None else "—"),
                tds,
            )
        )
    table_rows_html = "".join(table_rows)

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>本地大模型综合能力·多模型对比报告</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;}
  body{background:linear-gradient(145deg,#eef2f7 0%%,#f6f9fe 100%%);padding:18px 14px 50px;color:#0c2644;max-width:1180px;margin:0 auto;}
  h1{font-size:24px;font-weight:700;margin-bottom:6px;}
  .meta{color:#5c6f8c;font-size:13px;margin:4px 0 18px;}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px;}
  .card{background:#fff;border-radius:18px;padding:14px 10px;text-align:center;box-shadow:0 6px 14px rgba(0,0,0,.05);}
  .card .v{font-size:22px;font-weight:700;color:#1b2f4f;}
  .card .l{font-size:12px;color:#3f506e;margin-top:4px;}
  .panel{background:#fff;border-radius:22px;padding:16px;margin-bottom:20px;box-shadow:0 6px 18px rgba(0,0,0,.05);}
  .panel h2{font-size:16px;margin:10px 0 8px;color:#152b44;}
  .charts{display:flex;flex-wrap:wrap;gap:16px;}
  .chart-box{flex:1 1 360px;}
  canvas{max-width:100%%;height:auto !important;}
  table{width:100%%;border-collapse:collapse;font-size:13px;margin-top:6px;}
  th,td{padding:8px 6px;border-bottom:1px solid #eef1f5;text-align:left;vertical-align:top;}
  th{background:#eef3f9;color:#0b1f36;position:sticky;top:0;}
  details{margin-bottom:8px;border:1px solid #eef1f5;border-radius:12px;padding:8px 12px;background:#fafcff;}
  summary{cursor:pointer;font-weight:600;color:#122b44;}
  .scroll{max-height:560px;overflow:auto;}
  @media(max-width:600px){h1{font-size:20px;}}
</style>
</head>
<body>
<div id="app">
  <h1>🧠 本地大模型综合能力 · 多模型对比报告</h1>
  <div class="meta">生成时间：%s ｜ 模型数：%d ｜ 每模型题量：%d ｜ 端口：%d</div>

  <div class="cards">
    <div class="card"><div class="v">%d</div><div class="l">参与评测模型数</div></div>
    <div class="card"><div class="v">%.1f</div><div class="l">最高综合分（%s）</div></div>
    <div class="card"><div class="v">%.1f</div><div class="l">最低综合分</div></div>
    <div class="card"><div class="v">%s</div><div class="l">最快 p50 TTFT（%s）</div></div>
  </div>

  <div class="panel">
    <h2>📊 维度对比表（10 维度 + 综合）</h2>
    <div class="scroll">
      <table>
        <thead><tr><th>模型</th><th>综合</th><th>自动正确率</th><th>p50 TTFT</th><th>p95 TTFT</th><th>平均tokens/s</th>%s</tr></thead>
        <tbody>%s</tbody>
      </table>
    </div>
  </div>

  <div class="panel">
    <h2>🕸️ 维度雷达（多模型叠加）</h2>
    <div class="charts">
      <div class="chart-box"><canvas id="radar"></canvas></div>
      <div class="chart-box"><canvas id="bar"></canvas></div>
    </div>
  </div>

  <div class="panel">
    <h2>🧩 逐模型明细</h2>
    %s
  </div>
</div>

<script>
// AI可读数据：多模型合并评测结果挂载在 window.RESULTS（结构：{generated, models:[逐模型payload]}）
window.RESULTS = __RESULTS_JSON__;
const RESULTS = window.RESULTS;
const DIMS = %s;
const radarData = {
  labels: DIMS,
  datasets: %s
};
new Chart(document.getElementById('radar'), {
  type: 'radar',
  data: radarData,
  options: { responsive:true, scales:{ r:{ suggestedMin:0, suggestedMax:100, ticks:{stepSize:20} } } }
});
new Chart(document.getElementById('bar'), {
  type: 'bar',
  data: { labels: %s, datasets:[{ label:'综合分', data:%s, backgroundColor:'#2563eb' }] },
  options: { responsive:true, scales:{ y:{ suggestedMin:0, suggestedMax:100 } } }
});
</script>
</body>
</html>
""" % (
        combined["generated"], len(payloads),
        (len(payloads[0]["results"]) if payloads else 0), PORT,
        len(payloads),
        max(overalls) if overalls else 0, payloads[best_i]["meta"]["model_name"] if payloads else "—",
        min(overalls) if overalls else 0,
        (rows[best_i]["p50"] if rows and rows[best_i]["p50"] is not None else "—"),
        (models[best_i] if models else "—"),
        header_cells, table_rows_html,
        detail_html,
        json.dumps(dims, ensure_ascii=False),
        json.dumps(radar_datasets, ensure_ascii=False),
        json.dumps(models, ensure_ascii=False),
        json.dumps(overalls, ensure_ascii=False),
    )

    out = os.path.join(OUT_DIR, "report_all.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html.replace("__RESULTS_JSON__", json.dumps(combined, ensure_ascii=False)))
    # 合并结构化数据
    with open(os.path.join(OUT_DIR, "results_all.json"), "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    return out


# ============================ 主流程 ============================
def main():
    ap = argparse.ArgumentParser(description="一键逐模型评测（自动起停服务 + 进度 + 记录）")
    ap.add_argument("--models", help="指定模型序号，逗号分隔，如 1,2,3（菜单序号，1-based）；默认全部")
    ap.add_argument("--limit", type=int, default=0, help="每模型只跑前 N 题（快速冒烟）")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    try:
        models = list_models()
    except Exception as e:
        sys.stderr.write("[!] 获取模型清单失败：%s\n" % e)
        sys.stderr.write("    请确认启动器路径正确、E:\\models 有 GGUF 文件。\n")
        sys.exit(1)

    if args.models:
        want = set()
        for x in args.models.split(","):
            x = x.strip()
            if x.isdigit():
                want.add(int(x))
        models = [m for m in models if m.get("index") in want]
    if not models:
        sys.stderr.write("[!] 没有可测模型（检查 --models 序号或 E:\\models）。\n")
        sys.exit(1)

    sample = suite.build_questions()
    q_total = len(sample)
    if args.limit > 0:
        q_total = min(q_total, args.limit)
    total = q_total * len(models)
    print("[初始化] 题库 %d 题；将依次测试 %d 个模型，每模型 %d 题，总题量 %d"
          % (len(sample), len(models), q_total, total))
    print("[提示] 35B 在 GTX 1060 上约 8 t/s，全流程可能耗时数十分钟~数小时，请保持窗口开启。")

    ui = suite.ProgressUI(total)
    all_payloads = []
    for i, m in enumerate(models, 1):
        print("\n===== 模型 %d/%d：序号 %s / %s ====="
              % (i, len(models), m.get("index"), m.get("name")))
        payload = eval_one(m, ui, limit=args.limit)
        if payload:
            all_payloads.append(payload)
            safe = _safe(m.get("name"))
            suite.generate_report(payload, os.path.join(OUT_DIR, "report_%s.html" % safe))
            with open(os.path.join(OUT_DIR, "results_%s.json" % safe), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print("[✓] 已记录：report_%s.html / results_%s.json" % (safe, safe))
    ui.finish()

    if all_payloads:
        out = gen_combined(all_payloads)
        print("\n[完成] 合并对比报告：%s" % out)
        print("        逐模型报告：%s/report_<模型名>.html" % OUT_DIR)
    else:
        print("\n[!] 没有模型成功完成评测，请检查启动器日志（%s/logs/）。" % OUT_DIR)


if __name__ == "__main__":
    main()
