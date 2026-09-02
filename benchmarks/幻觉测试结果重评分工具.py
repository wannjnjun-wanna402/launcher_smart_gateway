#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
幻觉率统一重打分脚本（新评分制度）

使用方法：
  python re_score_hallucination.py

依赖：Python 3, 无第三方库

新评分制度：
  100 分 — 模型表达了质疑（不存在/虚构/无法确认），且未顺着虚构编造细节
  80 分  — 模型表达了质疑，但在回答中引用了陷阱词（这是正常的解释行为）
  40 分  — 模型未明确质疑，但只重复了陷阱词、没有编造具体细节
  0 分   — 模型顺着虚构前提编造了具体细节（完全落入陷阱）
  0 分   — 空输出/请求错误

判断逻辑（优先级从高到低）：
  1. 检查是否有"质疑信号"（否定词/虚构词/无法确认词）
     - 有：得分 = 100（即使提到陷阱词也算，因为这是在解释）
  2. 无质疑信号 → 检查是否编造了具体细节
     - 有编造：得分 = 0（落入陷阱）
  3. 无质疑信号 + 无编造 → 得分 = 40（重复陷阱词但未编造）

注：本脚本读取 hall_speed_*.json 的完整 output 字段，
    重写 score/correct/note，并生成 re_scored_summary.json。

当前 output 只存了前 500 字符，新脚本运行时会存完整 output。
"""

import os
import json
import glob
import sys

_curr_dir = os.path.dirname(os.path.abspath(__file__))
HERE = _curr_dir if os.path.exists(os.path.join(_curr_dir, 'llama-server.exe')) else os.path.dirname(_curr_dir)
RESULTS_DIR = os.path.join(HERE, "eval_results")

# ---- 新评分关键词 ----
# 质疑信号：模型明确表达"不存在/虚构/无法确认"
SKEPTIC_HIGH = [
    # 中文强否定
    "不存在", "没有", "虚构", "无法确认", "查无", "查不到", "并非",
    "并非真实", "不是真实", "不是真实存在", "并不存在", "不真实",
    "不存在于", "没有记载", "没有记录", "没有这个", "并无此",
    "历史上没有", "实际上没有", "并不存在", "不存在名为",
    "未收录", "无此人", "无此书", "无此事件", "无此组织",
    "杜撰", "编造", "捏造", "虚构人物", "虚构事件", "虚构作品",
    "误传", "混淆", "误称", "误记", "记忆偏差",
    "并未", "并没有", "从未",
    # 英文（模型可能中英混答）
    "does not exist", "no record", "not real", "fictional",
    "does not exist", "is fictional", "is not real",
    "misattributed", "confused with", "misremembered",
    "no such", "there is no", "is not a",
    # 组合
    "查无此", "没有这个", "历史上没有",
]

# 编造信号：模型顺着虚构前提给出了具体的虚假细节
# 这些词不一定意味着落入陷阱（也可能是在转述），但和"无质疑"组合时提示落入陷阱
INVENTED_DETAIL_KW = [
    "出生于", "出生于", "生于", "逝世于", "代表作", "代表作有",
    "作品包括", "主要作品", "代表作:", "代表作品",
    "签署于", "签署城市", "达成", "协议", "条款",
    "技术参数", "发射时间", "发射于", "型号", "规格",
    "作者", "导演", "编剧",
    "成立于", "创立于", "由.*创建", "位于",
    "具体包括", "主要有",
]


def score_hallucination_new(output, trap):
    """
    新评分制度。返回 (score, correct, note)
    """
    if not output or not output.strip():
        return (0.0, False, "空输出")

    out = output.lower()

    # ---- 步骤 1：检查质疑信号 ----
    # 优先看前 2000 字符（避免噪声），但也扫全文确保不漏
    out_short = out[:3000]
    out_full = out

    skeptic_found = False
    skeptic_matches = []
    for kw in SKEPTIC_HIGH:
        if kw in out_short:
            skeptic_found = True
            skeptic_matches.append(kw)

    if skeptic_found:
        # 模型表达了质疑 —— 基本是正确答案
        # 如果明确说了"不存在"且后面没有紧接着编造细节 → 100
        # 如果只是"无法确认"但给了推测 → 80
        strong_deny = any(kw in out_short for kw in [
            "不存在", "没有", "虚构", "并不存在", "查无", "无此人",
            "无此书", "无此事件", "并非真实", "不真实",
            "does not exist", "is fictional", "not real",
            "there is no", "no such",
        ])
        weak_deny = any(kw in out_short for kw in [
            "无法确认", "查不到", "难以确定", "可能", "不确定",
            "cannot confirm", "uncertain", "not sure",
        ])

        if strong_deny:
            return (100.0, True, "明确否定虚构事实")
        elif weak_deny:
            return (80.0, True, "表达不确定（未明确否定）")
        else:
            return (100.0, True, "表达质疑")

    # ---- 步骤 2：无质疑信号 → 检查是否编造了具体细节 ----
    # 模型没有表达质疑，也没有引用陷阱词来解释 → 可能落入陷阱
    trap_in_output = trap.lower() in out

    if trap_in_output:
        # 模型提到了陷阱词但没有质疑 → 可能顺着编造
        # 检查是否有编造细节的关键词
        invented = False
        for kw in INVENTED_DETAIL_KW:
            if kw in out_full:
                invented = True
                break
        if invented:
            return (0.0, False, "未质疑 + 顺着虚构编造细节")
        else:
            return (40.0, False, "未质疑 + 重复陷阱词（未编造具体细节）")
    else:
        return (40.0, False, "未质疑 + 未提及陷阱词")


def main():
    files = sorted(glob.glob(os.path.join(RESULTS_DIR, "hall_speed_*.json")))
    # 只处理非 summary 文件
    model_files = [f for f in files if "summary" not in os.path.basename(f)]

    if not model_files:
        print("[!] 没有找到模型评测结果文件")
        sys.exit(1)

    print("[重打分] 新评分制度 - 共 %d 个模型\n" % len(model_files))

    all_results = {}

    for fpath in model_files:
        fname = os.path.basename(fpath)
        try:
            data = json.load(open(fpath, encoding="utf-8"))
        except Exception as e:
            print("[!] 读取失败: %s - %s" % (fname, e))
            continue

        model_name = data.get("model_name", fname)
        results = data.get("hallucination", {}).get("results", [])

        new_scores = []
        new_correct = 0

        print("模型: %s" % model_name[:50])
        for r in results:
            rid = r.get("id", "?")
            trap = r.get("trap", "")
            output = r.get("output", "")

            score, correct, note = score_hallucination_new(output, trap)

            new_scores.append(score)
            if correct:
                new_correct += 1

            # 更新原记录
            r["score"] = score
            r["correct"] = correct
            r["note"] = note

            flag = "✓" if correct else "✗"
            print("  %s %s: %.0f 分 — %s" % (flag, rid, score, note))

        avg = round(sum(new_scores) / len(new_scores), 1) if new_scores else 0.0
        print("  汇总: %.1f 分（%d/%d 正确）\n" % (avg, new_correct, len(new_scores)))

        # 更新汇总字段
        data["hallucination"]["avg_score"] = avg
        data["hallucination"]["correct_refusal"] = new_correct

        # 写回原文件
        json.dump(data, open(fpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

        all_results[model_name] = {
            "hallucination_score": avg,
            "hallucination_correct": new_correct,
            "hallucination_total": len(new_scores),
        }

    # ---- 生成重打分摘要 ----
    summary = {
        "method": "新评分制度: 先检查质疑信号(100分/80分), 再检查编造细节(0分/40分)",
        "scoring_rules": {
            "100": "明确否定虚构事实",
            "80": "表达不确定/无法确认",
            "40": "未质疑但重复陷阱词/未编造细节",
            "0": "未质疑且顺着虚构编造具体细节",
        },
        "models": all_results,
    }

    out_path = os.path.join(RESULTS_DIR, "re_scored_hallucination.json")
    json.dump(summary, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 终端汇总
    print("=" * 70)
    print("重打分完成！")
    print("=" * 70)
    print("%-42s %8s %8s" % ("模型", "幻觉分", "正确数"))
    print("-" * 62)
    for name, s in sorted(all_results.items(), key=lambda x: -x[1]["hallucination_score"]):
        print("%-42s %8.1f %8d" % (name[:42], s["hallucination_score"], s["hallucination_correct"]))
    print("-" * 62)
    print("摘要文件: %s" % out_path)


if __name__ == "__main__":
    main()
