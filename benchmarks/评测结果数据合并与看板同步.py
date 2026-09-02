#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从 bench_results/cog_*.json 汇总各模型测评指标，生成可回填 launcher_main.ps1
BENCHMARK_DATA 的 PowerShell 代码段（输出到 bench_results/BENCHMARK_DATA_new.ps1）。

字段映射（与启动器 BENCHMARK_DATA 一致）：
  Speed    : 保留启动器已有实测 tok/s（本脚本不覆盖速度）
  Accuracy : 推理类正确率%（reasoning_accuracy_pct，最能代表真实认知能力）
  Halluc   : 抗幻分 = 100 - 记忆依赖率%（memory_dependency_rate_pct 越低越抗幻）
  Score    : 综合质量分 = 推理正确率*0.5 + 记忆正确率*0.2 + 推理链完整性*15 + 原创性*15（满分100）
  Rank     : 按 Score 降序排名
  BestFor  : 保留启动器已有场景标签

用法：
  python merge_benchmark.py
  → 生成 bench_results/BENCHMARK_DATA_new.ps1（PS 代码段，粘贴替换 launcher 里的 $BENCHMARK_DATA 块）
"""
import json, glob, os, re

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_results", "BENCHMARK_DATA_new.ps1")

def load_launcher_bench():
    """从 launcher_main.ps1 提取现有 Speed / BestFor（键=模型文件名）。"""
    launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher_main.ps1")
    src = open(launcher, "r", encoding="utf-8-sig").read()
    # 抓 BENCHMARK_DATA = @{ ... } 块
    m = re.search(r"\$BENCHMARK_DATA\s*=\s*@\{(.*?)\n\}", src, re.S)
    old = {}
    if m:
        block = m.group(1)
        for em in re.finditer(r'"([^"]+)"\s*=\s*\[PSCustomObject\]@\{(.*?)\n\s*\}', block, re.S):
            key = em.group(1)
            body = em.group(2)
            sp = re.search(r"Speed\s*=\s*([\d.]+)", body)
            bf = re.search(r"BestFor\s*=\s*\"([^\"]*)\"", body)
            old[key] = {"Speed": float(sp.group(1)) if sp else None,
                        "BestFor": bf.group(1) if bf else ""}
    return old

def main():
    files = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "bench_results", "cog_*.json")))
    if not files:
        print("没有找到 cog_*.json 评测结果。先跑 bench_cognitive.py。")
        return
    old = load_launcher_bench()
    data = {}
    for f in files:
        d = json.load(open(f, "r", encoding="utf-8"))
        s = d["summary"]
        name = s.get("model", "")
        # 通过 cog JSON 的模型名（--alias）映射回文件名：用 items 里匹配不到，就靠 alias 前缀
        data[name] = {
            "reasoning_acc": s.get("reasoning_accuracy_pct", 0),
            "memory_acc": s.get("memory_accuracy_pct", 0),
            "tool_acc": s.get("tool_accuracy_pct", 0),
            "chain": s.get("chain_completeness_avg", 0),
            "originality": s.get("originality_avg", 0),
            "mem_dep": s.get("memory_dependency_rate_pct", 0),
            "json": os.path.basename(f),
        }

    # 计算综合分与排名
    rows = []
    for name, v in data.items():
        score = (v["reasoning_acc"] * 0.5 + v["memory_acc"] * 0.2
                 + v["chain"] * 15 + v["originality"] * 15)
        rows.append((name, v, round(score, 1)))
    rows.sort(key=lambda r: -r[2])
    for i, (name, v, score) in enumerate(rows, 1):
        v["score"] = score
        v["rank"] = i

    # 生成 PS 代码段
    lines = ["# ============================================================",
             "#  基准测试数据 —— 由 merge_benchmark.py 自动生成",
             "#  Speed   : 保留启动器原有实测 tok/s",
             "#  Accuracy: 推理类正确率%（专项认知测评）",
             "#  Halluc  : 抗幻分 = 100 - 记忆依赖率%",
             "#  ToolScore: 工具调用准确率%（发出格式正确 web_search 调用的比例）",
             "#  Score   : 综合质量分 = 推理正确率*0.5 + 记忆正确率*0.2 + 推理链完整性*15 + 原创性*15",
             "#  Rank    : 按 Score 降序",
             "#  BestFor : 保留启动器原有场景标签",
             "# ============================================================",
             "$BENCHMARK_DATA = @{"]
    for name, v, score in rows:
        oldv = old.get(name, {})
        speed = oldv.get("Speed")
        best = oldv.get("BestFor", "")
        lines.append(f"    \"{name}\" = [PSCustomObject]@{{")
        lines.append(f"        Speed     = {speed if speed is not None else '$null'}    # 保留实测")
        lines.append(f"        Accuracy  = {v['reasoning_acc']}")
        lines.append(f"        Halluc    = {round(100 - v['mem_dep'], 1)}")
        lines.append(f"        ToolScore = {v['tool_acc']}")
        lines.append(f"        Score     = {score}")
        lines.append(f"        Rank      = {v['rank']}")
        lines.append(f"        BestFor   = \"{best}\"")
        lines.append(f"    }}")
    lines.append("}")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"已生成: {OUT}")
    print(f"共 {len(rows)} 个模型有测评数据：")
    for name, v, score in rows:
        print(f"  Rank#{v['rank']}  {name[:48]:<50} 推理{v['reasoning_acc']}% 记忆{v['memory_acc']}% "
              f"工具{v['tool_acc']}% 链{v['chain']:.2f} 原创{v['originality']:.2f} 记忆依赖{v['mem_dep']}% → 得分{score}")
    print("\n下一步：把该文件内容替换进 launcher_main.ps1 的 $BENCHMARK_DATA 块（346~404 行附近）。")

if __name__ == "__main__":
    main()
