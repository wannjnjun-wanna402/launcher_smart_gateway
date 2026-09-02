#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
针对性重测 / 断点续测（retest）
============================================================================
只重跑上一轮“未通过 / 空输出 / 异常”的题目，正常题直接沿用旧记录 —— 不重复
测已经 OK 的组，省时间。重测完自动合并、重新聚合、覆写该模型的报告与 JSON。

【判定“需重测”的条件（任一即重测）】
  1. output 为空（模型没产出正式答案，多为思考型模型思维链吃满预算所致）
  2. error 字段非空（接口/超时错误）
  3. score.empty == True（被空输出守卫判为无效）
  4. tok_s 异常（> 10000，历史 1024000 爆炸值 = 空输出兜底 bug 的残留）

【复用】
  - run_test_suite.py：题库(build_questions，seed 固定可复现) / query_model / 评分 / 报告
  - run_eval_all.py：start_server / wait_ready / kill_server / gen_combined

【用法】
  python retest_model.py --model 35B            # 关键词匹配 results_*.json（文件名或model_name）
  python retest_model.py --index 8              # 直接用启动器菜单序号起服务
  python retest_model.py --model 35B --dims 幻觉率,数学能力   # 只重测指定维度的失败题
  python retest_model.py --all                  # 对所有已有 results_*.json 重测其失败题
  python retest_model.py --model 35B --rebuild-all           # 重测后重新生成 report_all.html
============================================================================
"""

import os
import sys
import json
import glob
import argparse
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import run_test_suite as suite   # 题库 / query_model / 评分 / 报告
import run_eval_all as orch      # 服务起停 / 合并报告

OUT_DIR = orch.OUT_DIR
_RESULT_KEYS = ("id", "dim", "prompt", "output", "ttft", "total_time",
                "error", "tok_s", "completion_tokens", "gen_time", "score")


# ============================ 判定与聚合 ============================
def is_bad(r):
    """判断某题记录是否需要重测。"""
    out = (r.get("output") or "").strip()
    if not out:
        return True
    if r.get("error"):
        return True
    sc = r.get("score") or {}
    if sc.get("empty"):
        return True
    toks = r.get("tok_s")
    if isinstance(toks, (int, float)) and toks and toks > 10000:
        return True
    return False


def recompute_tok_s(results):
    """离线重算 tok/s，修正思维链污染。
    判定：completion_tokens 远超纯输出文本估算 token（>3倍）即视为 reasoning 混入。
    改用 输出文本长度/1.4 ÷ gen_time 算纯内容生成速度。返回修正条数。
    """
    changed = 0
    for r in results:
        out = (r.get("output") or "").strip()
        gt = r.get("gen_time")
        ct = r.get("completion_tokens")
        if not out or not isinstance(gt, (int, float)) or gt <= 0:
            continue
        est = max(1, round(len(out) / 1.4))
        if isinstance(ct, (int, float)) and ct > 0 and ct > est * 3:
            new_tok = round(est / gt, 2)
            old = r.get("tok_s")
            if not isinstance(old, (int, float)) or abs(new_tok - old) > 0.01:
                r["tok_s"] = new_tok
                changed += 1
    return changed


def rebuild_payload(results, meta, model_id):
    """用（新+旧）合并后的 results 重新聚合，生成完整 payload。"""
    dims_summary, speed = suite.aggregate(results)
    score_vals = [dims_summary[d]["avg_score"] for d in suite.DIMENSIONS
                  if dims_summary[d]["avg_score"] is not None]
    overall = round(sum(score_vals) / len(score_vals), 1) if score_vals else 0.0
    auto_total = sum(1 for r in results if r["score"].get("auto")
                     and r["score"].get("correctness") is not None)
    auto_ok = sum(1 for r in results if r["score"].get("auto")
                  and r["score"].get("correctness") is True)
    auto_rate = round(100.0 * auto_ok / auto_total, 1) if auto_total else None

    meta2 = dict(meta)
    meta2["model"] = model_id or meta.get("model")
    meta2["timestamp"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta2["total_questions"] = len(results)
    meta2["retested"] = True
    return {
        "meta": meta2,
        "overall": overall,
        "auto_rate": auto_rate,
        "speed": speed,
        "dim_order": suite.DIMENSIONS,
        "dims": dims_summary,
        "results": [{k: r.get(k) for k in _RESULT_KEYS} for r in results],
    }


# ============================ 单文件重测 ============================
def retest_file(json_path, only_dims=None, index_override=None, do_recompute=False):
    """对单个 results_<name>.json 执行针对性重测。返回新 payload 或 None。"""
    payload = json.load(open(json_path, encoding="utf-8"))
    meta = payload.get("meta", {})
    results = payload.get("results", [])
    name = meta.get("model_name") or os.path.basename(json_path)[8:-5]
    idx = index_override if index_override is not None else meta.get("model_index")

    if idx is None:
        sys.stderr.write("[!] %s 缺少 model_index，请用 --index 指定启动器菜单序号。\n" % name)
        return None

    if do_recompute:
        n_fixed = recompute_tok_s(results)
        if n_fixed:
            print("  [recompute] 离线修正 %d 条 tok/s（思维链污染）" % n_fixed)

    need = [r for r in results if is_bad(r) and (not only_dims or r.get("dim") in only_dims)]
    print("\n===== 重测目标：%s（启动器序号 %s）=====" % (name, idx))
    print("  上一轮共 %d 题；需重测 %d 题（空输出/错误/异常）。" % (len(results), len(need)))
    if not need:
        print("  ✓ 该模型无需重测，全部题目上一轮均正常。")
        if do_recompute:
            safe = orch._safe(name)
            payload2 = rebuild_payload(results, meta, None)
            suite.generate_report(payload2, os.path.join(OUT_DIR, "report_%s.html" % safe))
            with open(os.path.join(OUT_DIR, "results_%s.json" % safe), "w", encoding="utf-8") as fh:
                json.dump(payload2, fh, ensure_ascii=False, indent=2)
            print("  [recompute] 已落盘修正后的 tok/s。")
            return payload2
        return payload
    # 按维度列出待重测题
    from collections import Counter
    dc = Counter(r.get("dim") for r in need)
    print("  分布：" + " ".join("%s×%d" % (d, c) for d, c in dc.items()))

    qmap = {q["id"]: q for q in suite.build_questions()}
    by_id = {r["id"]: r for r in results}

    orch.kill_server()
    proc, log = orch.start_server(idx)
    rows = []
    try:
        model_id = orch.wait_ready()
        if not model_id:
            sys.stderr.write("[!] 模型 %s 启动超时，跳过。日志：%s\n" % (name, log))
            return None
        print("  [✓] 模型就绪 id=%s，开始重测 %d 题……" % (model_id, len(need)))

        cfg = dict(suite.CONFIG)
        cfg["api_base"] = orch.API_BASE
        cfg["model"] = model_id
        cfg["output_dir"] = OUT_DIR

        ui = suite.ProgressUI(len(need))
        for r in need:
            q = qmap.get(r["id"])
            if not q:
                ui.update(desc="跳过 %s（题库无此ID）" % r["id"])
                continue
            text, ttft, total, tok_s, ctok, gen_time, err = suite.query_model(q["prompt"], cfg)
            sc = suite.score_question(q, text)
            by_id[q["id"]] = {
                "id": q["id"], "dim": q["dim"], "prompt": q["prompt"],
                "output": text, "ttft": ttft, "total_time": total,
                "error": err, "tok_s": tok_s, "completion_tokens": ctok,
                "gen_time": gen_time, "scorer": q["scorer"], "score": sc,
            }
            rows.append({
                "id": q["id"], "dim": q["dim"], "ttft": ttft, "total": total,
                "tok_s": tok_s, "qscore": sc.get("qscore"),
                "ok": bool(text.strip()), "note": sc.get("note", ""),
            })
            ui.update(desc="重测 %s %s" % (q["id"], q["dim"]))
        ui.finish()
    finally:
        orch.kill_server()
        try:
            proc.terminate()
        except Exception:
            pass

    # 保序合并（正常题沿用旧记录，失败题替换为新记录）
    new_results = [by_id[r["id"]] for r in results]
    payload2 = rebuild_payload(new_results, meta, model_id)

    safe = orch._safe(name)
    suite.generate_report(payload2, os.path.join(OUT_DIR, "report_%s.html" % safe))
    with open(os.path.join(OUT_DIR, "results_%s.json" % safe), "w", encoding="utf-8") as f:
        json.dump(payload2, f, ensure_ascii=False, indent=2)

    # 打印每组重测结果（响应时间 / 准确性 / tok_s）
    print("\n  ---- 本轮重测逐题结果 ----")
    print("  %-5s %-8s %8s %8s %9s  %s" % ("ID", "维度", "TTFT(s)", "tok/s", "得分", "判定"))
    still_empty = 0
    for x in rows:
        if not x["ok"]:
            still_empty += 1
        print("  %-5s %-8s %8s %8s %9s  %s" % (
            x["id"], (x["dim"] or "")[:6],
            "%.2f" % x["ttft"] if x["ttft"] is not None else "—",
            ("%.1f" % x["tok_s"]) if x["tok_s"] is not None else "空",
            ("%.1f" % x["qscore"]) if x["qscore"] is not None else "—",
            ("✓有输出" if x["ok"] else "✗仍空") + ("｜" + x["note"] if x["note"] else ""),
        ))
    good = sum(1 for x in rows if x["ok"])
    valid_toks = [x["tok_s"] for x in rows if x["tok_s"] is not None]
    avg_toks = round(sum(valid_toks) / len(valid_toks), 2) if valid_toks else None
    print("\n  ---- 重测小结 ----")
    print("  重测 %d 题 → 有效输出 %d 题 / 仍空 %d 题" % (len(rows), good, still_empty))
    print("  本轮重测平均 tokens/s：%s" % (avg_toks if avg_toks is not None else "—（无有效样本）"))
    print("  新综合分：%s（自动正确率 %s）" % (
        payload2["overall"],
        ("%.1f%%" % payload2["auto_rate"]) if payload2["auto_rate"] is not None else "—"))
    print("  已覆写：report_%s.html / results_%s.json" % (safe, safe))
    return payload2


# ============================ 定位待测文件 ============================
def find_result_files(keyword=None):
    files = [f for f in glob.glob(os.path.join(OUT_DIR, "results_*.json"))
             if os.path.basename(f) != "results_all.json"]
    if keyword:
        kw = keyword.lower()
        files = [f for f in files if kw in os.path.basename(f).lower()
                 or kw in (json.load(open(f, encoding="utf-8")).get("meta", {})
                           .get("model_name", "").lower())]
    return sorted(files)


def rebuild_report_all():
    """读回所有 results_*.json，重新生成 report_all.html。"""
    files = find_result_files()
    payloads = []
    for f in files:
        try:
            payloads.append(json.load(open(f, encoding="utf-8")))
        except Exception:
            pass
    if payloads:
        out = orch.gen_combined(payloads)
        print("\n[✓] 已重建合并对比报告：%s" % out)


def main():
    ap = argparse.ArgumentParser(description="针对性重测：只重跑上一轮失败/空输出/异常的题")
    ap.add_argument("--model", help="关键词匹配 results_*.json（文件名或 model_name），如 35B")
    ap.add_argument("--index", type=int, help="直接指定启动器菜单序号（覆盖 json 里的 model_index）")
    ap.add_argument("--all", action="store_true", help="对所有已有 results_*.json 重测其失败题")
    ap.add_argument("--dims", help="只重测指定维度（逗号分隔），如 幻觉率,数学能力")
    ap.add_argument("--rebuild-all", action="store_true", help="重测后重新生成 report_all.html")
    ap.add_argument("--recompute", action="store_true", help="离线重算 tok/s（修正思维链污染），不重跑模型")
    args = ap.parse_args()

    only_dims = set(x.strip() for x in args.dims.split(",")) if args.dims else None

    if args.all:
        files = find_result_files()
    elif args.model:
        # 支持逗号分隔多模型：--model Qwen3.5-4B,Qwen3-0.6B
        keywords = [k.strip() for k in args.model.split(",") if k.strip()]
        if len(keywords) > 1:
            seen = set()
            files = []
            for kw in keywords:
                for f in find_result_files(kw):
                    if f not in seen:
                        seen.add(f)
                        files.append(f)
        else:
            files = find_result_files(args.model)
    else:
        sys.stderr.write("请用 --model <关键词> 或 --index <序号> 或 --all 指定重测目标。\n")
        sys.exit(1)

    if not files:
        sys.stderr.write("[!] 未找到匹配的 results_*.json（目录：%s）。\n" % OUT_DIR)
        sys.exit(1)

    print("[初始化] 将重测以下结果文件：")
    for f in files:
        print("   - %s" % os.path.basename(f))

    for f in files:
        idx_override = args.index if (args.model or args.index) and len(files) == 1 else None
        retest_file(f, only_dims=only_dims, index_override=idx_override, do_recompute=args.recompute)

    if args.rebuild_all:
        rebuild_report_all()

    print("\n[完成] 针对性重测结束。")


if __name__ == "__main__":
    main()
