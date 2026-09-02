#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
11 维专业模型测评离线重打分工具 v2
  功能：
    读取历史或最新的 pro11_v2_*.json 答卷记录，
    调用全新的 100% 确定性评分引擎重新计算得分，
    输出高精度分维得分矩阵（终端表格 + CSV），支持 --write 写回 JSON。
  用法：
    python "benchmarks/11维度测评数据重打分工具.py"                                      # 自动寻找最新 pro11_v2 JSON
    python "benchmarks/11维度测评数据重打分工具.py" "bench_results/pro11_v2_xxx.json"    # 指定文件
    python "benchmarks/11维度测评数据重打分工具.py" --write                             # 重新打分并写回文件
"""
import argparse, json, os, re, sys, glob

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

_curr_dir = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = _curr_dir if os.path.exists(os.path.join(_curr_dir, 'llama-server.exe')) else os.path.dirname(_curr_dir)
OUT_DIR = os.path.join(BASE_DIR, "bench_results")
TASKS_JSON_PATH = os.path.join(_curr_dir, "datasets", "pro11_tasks_v2.json")

# 动态引入主评测脚本中的确定性评分引擎与题库加载器
import importlib.util
_spec = importlib.util.spec_from_file_location("pro11_main", os.path.join(_curr_dir, "11维度专业模型全能测评.py"))
pro11_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pro11_main)
score_answer = pro11_main.score_answer
load_tasks = pro11_main.load_tasks

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", default="", help="指定 pro11_v2 JSON 结果文件路径")
    ap.add_argument("--write", action="store_true", help="将重新计算后的得分写回 JSON 文件")
    ap.add_argument("--safety-mode", type=str, default="freedom", choices=["freedom", "safe"])
    args = ap.parse_args()

    # 定位 JSON 文件
    target_file = args.file
    if not target_file:
        files = sorted(glob.glob(os.path.join(OUT_DIR, "pro11_v2_*.json")), key=os.path.getmtime, reverse=True)
        if not files:
            # 兼容老版
            files = sorted(glob.glob(os.path.join(OUT_DIR, "pro11_*.json")), key=os.path.getmtime, reverse=True)
        if not files:
            print("ERROR: 未在 bench_results/ 下找到任何 pro11 评测结果 JSON 文件。")
            return
        target_file = files[0]

    print(f"正在重评分文件: {target_file}")
    with open(target_file, "r", encoding="utf-8") as fp:
        data = json.load(fp)

    tasks_list = load_tasks()
    task_map = {t["id"]: t for t in tasks_list}
    dims = ["编程", "推理", "中文", "深度思考", "工具", "检索", "干活", "长时精度", "指令遵循", "事实", "安全"]

    models_data = data.get("models", {})
    if not models_data:
        print("ERROR: JSON 中未包含有效的模型数据。")
        return

    print("\n" + "=" * 95)
    print(f"{'模型名称':<24} " + " ".join(f"{d[:2]:>4}" for d in dims) + " | 智商分 综合分 | 平均tps")
    print("-" * 95)

    res_summary = []
    for m_key, m_val in models_data.items():
        label = m_val.get("label", m_key)
        tasks = m_val.get("tasks", [])
        dim_scores = {d: [] for d in dims}

        for t in tasks:
            tid = t.get("id")
            if tid in task_map:
                task_obj = task_map[tid]
                ans = t.get("answer", "")
                sc, dt = score_answer(task_obj, ans, safety_mode=args.safety_mode)
                t["score"] = sc
                t["detail"] = dt
                dim_scores[task_obj["dim"]].append(sc)

        dim_avg = {d: round(sum(scores) / len(scores), 1) if scores else 0.0 for d, scores in dim_scores.items()}
        raw_intel = round(sum(dim_avg[d] for d in dims[:10]) / 10, 1)
        overall = round(sum(dim_avg.values()) / len(dims), 1)
        avg_tps = m_val.get("avg_tps", 0.0)

        m_val["dim_scores"] = dim_avg
        m_val["raw_intelligence_score"] = raw_intel
        m_val["overall_score"] = overall

        row_str = f"{label:<24} " + " ".join(f"{dim_avg.get(d, 0.0):4.0f}" for d in dims)
        print(f"{row_str} | {raw_intel:5.1f} {overall:5.1f} | {avg_tps:5.1f}")
        res_summary.append((label, raw_intel, overall, avg_tps))

    print("=" * 95)

    if args.write:
        with open(target_file, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
        print(f"\n✓ 得分已成功重新计算并写回: {target_file}")

if __name__ == "__main__":
    main()
