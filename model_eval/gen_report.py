# -*- coding: utf-8 -*-
"""
H5 评测报告生成器
=================
读取 run_eval.py 产出的 results.json，生成纯离线、手机友好的 H5 报告：
  - 内联 SVG 雷达图（无 CDN 依赖，断网可用）
  - 维度得分条、关键指标卡
  - 每维度可展开的原始题目 / 模型输出 / 对错标注 / 响应时间
  - 自动判分 vs 需人工复核 的透明标注

用法：
  python gen_report.py                      # 读取 results.json，输出 results_report.html
  python gen_report.py --in my_result.json --out report.html
"""
import argparse
import html
import json
import math
import os

DIMS = [
    "幻觉率", "正确率", "工具调用", "响应速度", "数学能力",
    "思考能力", "编码能力", "专业度", "能力边界", "非安全对齐",
]

# 哪些维度为“启发式自动判分，需人工复核”
HEURISTIC_DIMS = {"幻觉率", "思考能力", "编码能力", "专业度", "能力边界", "非安全对齐"}

CSS = """
* { margin:0; padding:0; box-sizing:border-box;
    font-family: system-ui, -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; }
body { background: linear-gradient(145deg,#f6f9fe 0%,#eef2f7 100%); padding:16px 12px 48px;
    min-height:100vh; display:flex; flex-direction:column; align-items:center; color:#13233b; }
.wrap { max-width:760px; width:100%; background:rgba(255,255,255,.72); backdrop-filter:blur(4px);
    border-radius:28px; box-shadow:0 14px 34px rgba(10,30,60,.12); padding:20px 16px 30px; }
h1 { font-size:22px; font-weight:700; color:#0a1a2f; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.sub { font-size:13px; color:#5c6f8c; margin:8px 0 4px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin:16px 0; }
.card { background:#fff; border-radius:18px; padding:12px 10px; text-align:center;
    box-shadow:0 4px 12px rgba(0,0,0,.04); border:1px solid rgba(255,255,255,.6); }
.card .l { font-size:12px; color:#3f506e; }
.card .v { font-size:24px; font-weight:700; color:#0c2644; margin-top:4px; }
.card .s { font-size:12px; color:#5c6f8c; margin-top:2px; }
.sec { margin:22px 0 8px; font-size:16px; font-weight:600; color:#152b44;
    border-left:4px solid #2b5d9e; padding-left:10px; }
.radar { display:flex; justify-content:center; margin:10px 0; }
.radar svg { width:100%; max-width:360px; height:auto; }
.bars { display:flex; flex-direction:column; gap:8px; margin:8px 0; }
.bar { display:flex; align-items:center; gap:8px; font-size:13px; }
.bar .name { width:78px; flex:none; color:#1f314b; text-align:right; }
.bar .track { flex:1; background:#e3eaf3; border-radius:8px; height:16px; overflow:hidden; }
.bar .fill { height:100%; border-radius:8px; background:linear-gradient(90deg,#3d7eb3,#2b5d9e); }
.bar .pct { width:42px; flex:none; text-align:left; color:#0c2644; font-weight:600; }
table { width:100%; border-collapse:collapse; font-size:13px; margin:6px 0; }
th,td { padding:9px 8px; border-bottom:1px solid rgba(0,0,0,.05); text-align:left; vertical-align:top; }
th { background:#e3eaf3; color:#0b1f36; font-weight:600; }
.ok { color:#1f6c3b; font-weight:700; }
.bad { color:#b13a3a; font-weight:700; }
.na { color:#9b6f1c; }
details { background:#fff; border-radius:16px; margin:8px 0; padding:6px 10px;
    box-shadow:0 3px 10px rgba(0,0,0,.03); border:1px solid rgba(255,255,255,.7); }
summary { cursor:pointer; font-weight:600; color:#122b44; font-size:14px; padding:6px 2px; }
.q { font-size:12px; color:#33455f; margin:4px 0; }
pre { white-space:pre-wrap; word-break:break-word; background:#f4f7fb; border-radius:10px;
    padding:8px; font-size:12px; max-height:180px; overflow:auto; color:#1a2c44; }
.tag { display:inline-block; font-size:11px; padding:1px 7px; border-radius:10px; margin-left:6px; }
.tag.auto { background:#e3f0e8; color:#1f6c3b; }
.tag.manual { background:#fdeccf; color:#9b6f1c; }
.note { font-size:12px; color:#5c6f8c; margin:4px 0; }
.foot { margin-top:24px; font-size:12px; color:#425875; border-top:1px solid #d7e0ed; padding-top:14px; line-height:1.6; }
"""


def radar_svg(values, labels, size=360):
    c = size / 2
    R = size * 0.38
    n = len(values)
    rings = [20, 40, 60, 80, 100]
    parts = [f'<svg viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg">']

    # 网格环
    for lvl in rings:
        pts = []
        for i in range(n):
            ang = -math.pi / 2 + i * 2 * math.pi / n
            x = c + R * lvl / 100 * math.cos(ang)
            y = c + R * lvl / 100 * math.sin(ang)
            pts.append(f"{x:.1f},{y:.1f}")
        parts.append(f'<polygon points="{" ".join(pts)}" fill="none" stroke="#c2cfe0" stroke-width="1"/>')

    # 轴线 + 标签
    for i in range(n):
        ang = -math.pi / 2 + i * 2 * math.pi / n
        x = c + R * math.cos(ang)
        y = c + R * math.sin(ang)
        parts.append(f'<line x1="{c:.1f}" y1="{c:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="#c2cfe0" stroke-width="1"/>')
        lx = c + (R + 16) * math.cos(ang)
        ly = c + (R + 16) * math.sin(ang)
        anchor = "middle"
        if math.cos(ang) > 0.3:
            anchor = "start"
        elif math.cos(ang) < -0.3:
            anchor = "end"
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="10" fill="#183658" '
                     f'text-anchor="{anchor}" dominant-baseline="middle">{html.escape(labels[i])}</text>')

    # 数据多边形
    dpts = []
    dxy = []
    for i, v in enumerate(values):
        ang = -math.pi / 2 + i * 2 * math.pi / n
        x = c + R * max(0, min(100, v)) / 100 * math.cos(ang)
        y = c + R * max(0, min(100, v)) / 100 * math.sin(ang)
        dpts.append(f"{x:.1f},{y:.1f}")
        dxy.append((x, y))
    parts.append(f'<polygon points="{" ".join(dpts)}" fill="rgba(42,98,175,.28)" '
                 f'stroke="#2b5d9e" stroke-width="2"/>')
    for x, y in dxy:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="#1c4979"/>')

    parts.append("</svg>")
    return "".join(parts)


def dim_score(payload, dim):
    if dim == "响应速度":
        return payload.get("speed", {}).get("speed_score") or 0.0
    ds = payload.get("dims_summary", {}).get(dim, {})
    return ds.get("auto_rate") if ds.get("auto_rate") is not None else 0.0


def generate(payload, out_path):
    meta = payload.get("meta", {})
    speed = payload.get("speed", {})
    scores = [dim_score(payload, d) for d in DIMS]
    overall = round(sum(scores) / len(scores), 1)

    # 指标卡
    auto_total = meta.get("auto_scored_total", 0)
    auto_ok = meta.get("auto_correct_total", 0)
    auto_rate = round(100 * auto_ok / auto_total, 1) if auto_total else None
    hallu = payload["dims_summary"].get("幻觉率", {}).get("auto_rate")
    correct = payload["dims_summary"].get("正确率", {}).get("auto_rate")
    tool = payload["dims_summary"].get("工具调用", {}).get("auto_rate")
    safety = payload["dims_summary"].get("非安全对齐", {}).get("auto_rate")

    cards = [
        ("综合得分", f"{overall}", "10维均值"),
        ("自动判分率", f"{auto_rate}%", f"{auto_ok}/{auto_total}题"),
        ("正确率", f"{correct}%", "客观MCQ"),
        ("工具调用", f"{tool}%", "JSON参数"),
        ("平均TTFT", f"{speed.get('avg_ttft')}s", f"P95={speed.get('p95_ttft')}s"),
        ("安全拒绝率", f"{safety}%", "10题对抗"),
    ]
    cards_html = "".join(
        f'<div class="card"><div class="l">{html.escape(l)}</div>'
        f'<div class="v">{v}</div><div class="s">{html.escape(s)}</div></div>'
        for l, v, s in cards)

    # 维度得分条
    bars_html = "".join(
        f'<div class="bar"><div class="name">{html.escape(d)}</div>'
        f'<div class="track"><div class="fill" style="width:{max(2,scores[i]):.0f}%"></div></div>'
        f'<div class="pct">{scores[i]:.0f}</div></div>'
        for i, d in enumerate(DIMS))

    # 维度汇总表
    rows = []
    for d in DIMS:
        ds = payload["dims_summary"].get(d, {})
        if d == "响应速度":
            row = (f"{ds.get('n',0) if False else speed.get('n')}", "—",
                   f"avg {speed.get('avg_ttft')}s / P95 {speed.get('p95_ttft')}s",
                   "归一化")
        else:
            row = (str(ds.get("n", 0)), f"{ds.get('auto_rate')}%",
                   f"{ds.get('auto_correct')}/{ds.get('auto_scored')}",
                   "自动" if d not in HEURISTIC_DIMS else "启发式")
        tagcls = "auto" if d not in HEURISTIC_DIMS else "manual"
        rows.append(
            f"<tr><td>{html.escape(d)}</td><td>{row[0]}</td><td>{row[1]}</td>"
            f"<td>{html.escape(row[2])}</td>"
            f'<td><span class="tag {tagcls}">{row[3]}</span></td></tr>')
    table_html = ("<table><thead><tr><th>维度</th><th>题数</th><th>得分</th>"
                  "<th>明细</th><th>判分</th></tr></thead><tbody>"
                  + "".join(rows) + "</tbody></table>")

    # 详细展开
    detail_html = ""
    by_dim = {}
    for r in payload.get("results", []):
        by_dim.setdefault(r["dim"], []).append(r)
    for d in DIMS:
        recs = by_dim.get(d, [])
        if not recs:
            continue
        items = []
        for r in recs:
            if r["correct"] is True:
                mark = '<span class="ok">✓ 正确</span>'
            elif r["correct"] is False:
                mark = '<span class="bad">✗ 错误</span>'
            else:
                mark = '<span class="na">— 人工</span>'
            ttft = f"{r['ttft']:.2f}s" if r.get("ttft") is not None else "—"
            out = html.escape(r.get("output", "") or "(空)")
            prompt = html.escape(r.get("prompt", "")[:160])
            items.append(
                f'<div class="q"><b>{html.escape(r["id"])}</b> · {mark} · '
                f'TTFT {ttft} · 总 {r.get("total_time",0):.1f}s'
                f'<span class="tag {"auto" if d not in HEURISTIC_DIMS else "manual"}">'
                f'{"自动" if d not in HEURISTIC_DIMS else "启发式"}</span></div>'
                f'<div class="q">问：{prompt}{"…" if len(r.get("prompt",""))>160 else ""}</div>'
                f'<pre>答：{out}</pre>'
                f'<div class="note">判分备注：{html.escape(str(r.get("note","")))}</div>')
        tagcls = "auto" if d not in HEURISTIC_DIMS else "manual"
        detail_html += (f'<details><summary>{html.escape(d)} '
                        f'<span class="tag {tagcls}">'
                        f'{"自动判分" if d not in HEURISTIC_DIMS else "启发式·需复核"}'
                        f'</span> · {len(recs)}题</summary>'
                        + "".join(items) + "</details>")

    speed_note = ("响应速度得分按公式 speed_score = min(100, 100×0.8/平均TTFT) 归一化，"
                  "0.8s 计满分；TTFT 含 prefill 时间。")
    foot = (f"模型：<b>{html.escape(str(meta.get('model','')))}</b> ｜ 基础地址："
            f"{html.escape(str(meta.get('base_url','')))} ｜ 生成时间：{html.escape(str(meta.get('timestamp','')))}<br>"
            f"总题数 {meta.get('total_questions')}（响应速度维度跨全部题目测 TTFT，不单列题）。"
            f"本报告由真实模型输出生成（非模板占位）。<br>"
            f"<b>判分说明：</b>正确率/工具调用/数学为可自动精确判分；幻觉率/思考/编码/专业度/能力边界/安全为"
            f"启发式自动判分并<b>需人工复核</b>。{speed_note}")

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>模型能力评测报告 · H5</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>🧪 模型能力评测报告</h1>
<div class="sub">10 维度系统评测 · 共 {meta.get('total_questions')} 题 · 移动端适配</div>
<div class="cards">{cards_html}</div>

<div class="sec">综合能力雷达（10 维度，0-100）</div>
<div class="radar">{radar_svg(scores, DIMS)}</div>

<div class="sec">维度得分</div>
<div class="bars">{bars_html}</div>

<div class="sec">维度汇总</div>
{table_html}

<div class="sec">逐题明细（点击展开）</div>
{detail_html}

<div class="foot">{foot}</div>
</div></body></html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", default="results.json")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    inp = getattr(args, "in")
    out = args.out or (os.path.splitext(inp)[0] + "_report.html")
    with open(inp, encoding="utf-8") as f:
        payload = json.load(f)
    print("报告已生成:", generate(payload, out))
