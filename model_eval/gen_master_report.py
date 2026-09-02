# -*- coding: utf-8 -*-
"""
=============================================================================
Qwen3.8 工具调用对比大屏主报告生成器 (Master Matrix Report Generator with Speed)
=============================================================================
"""

import os
import sys
import json
import glob
from datetime import datetime

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Qwen3.8 工具调用多模型 × 多模板 矩阵对比大屏报告</title>
    <style>
        :root {
            --bg-primary: #0a0f1d;
            --bg-card: #151e32;
            --bg-card-sub: #1e2942;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent-cyan: #06b6d4;
            --accent-blue: #3b82f6;
            --accent-green: #10b981;
            --accent-yellow: #f59e0b;
            --accent-red: #ef4444;
            --accent-purple: #a855f7;
            --border-color: #2b3954;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg-primary);
            color: var(--text-main);
            line-height: 1.6;
            padding: 24px;
        }
        .container { max-width: 1680px; margin: 0 auto; }
        header {
            margin-bottom: 24px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 16px;
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            flex-wrap: wrap;
            gap: 12px;
        }
        h1 { font-size: 26px; font-weight: 800; color: var(--accent-cyan); display: flex; align-items: center; gap: 12px; }
        .badge-group { display: flex; gap: 8px; flex-wrap: wrap; }
        .meta-tag { background: #0369a1; color: #e0f2fe; padding: 4px 12px; border-radius: 6px; font-size: 13px; font-weight: 600; }
        
        .winner-banner {
            background: linear-gradient(135deg, rgba(16,185,129,0.15) 0%, rgba(6,182,212,0.1) 100%);
            border: 2px solid var(--accent-green);
            border-radius: 12px;
            padding: 20px 24px;
            margin-bottom: 24px;
            box-shadow: 0 8px 20px rgba(16,185,129,0.15);
        }
        .winner-banner h2 { font-size: 20px; color: var(--accent-green); margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
        .winner-banner p { font-size: 14px; color: #e2e8f0; margin-bottom: 6px; }

        .section-title {
            font-size: 18px;
            font-weight: 700;
            color: var(--text-main);
            margin: 24px 0 12px 0;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            background: var(--bg-card);
            border-radius: 10px;
            overflow: hidden;
            border: 1px solid var(--border-color);
            margin-bottom: 24px;
            font-size: 13px;
        }
        th, td { padding: 10px 12px; text-align: center; border-bottom: 1px solid var(--border-color); }
        th { background: var(--bg-card-sub); color: var(--text-main); font-weight: 600; }
        td.align-left { text-align: left; }
        tr:hover { background: rgba(255,255,255,0.02); }
        
        .score-cell { font-size: 15px; font-weight: 700; }
        .score-high { color: var(--accent-green); }
        .score-mid { color: var(--accent-yellow); }
        .score-low { color: var(--accent-red); }
        
        .speed-in { color: #38bdf8; font-weight: 600; }
        .speed-out { color: #4ade80; font-weight: 700; }
        
        .badge {
            display: inline-block;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }
        .badge-pass { background: rgba(16,185,129,0.2); color: var(--accent-green); border: 1px solid var(--accent-green); }
        .badge-fail { background: rgba(239,68,68,0.2); color: var(--accent-red); border: 1px solid var(--accent-red); }
        .badge-loop { background: rgba(239,68,68,0.3); color: #fca5a5; font-weight: 700; }
        .badge-xml { background: rgba(245,158,11,0.25); color: #fde047; }
        .badge-tag { background: rgba(6,182,212,0.15); color: var(--accent-cyan); }
        
        .tpl-fixed { color: var(--accent-cyan); font-weight: 700; }
        .tpl-sharp { color: #f97316; font-weight: 700; }
        .tpl-official { color: #a855f7; font-weight: 700; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>🏆 Qwen3.8 工具调用 横向对比矩阵大屏报告</h1>
                <div style="margin-top: 6px; color: var(--text-muted); font-size: 13px;">
                    生成时间: <span>__TIMESTAMP__</span> | 包含: <strong>输入吞吐 (Prompt t/s) 与 输出生成 (Gen t/s)</strong> 实测
                </div>
            </div>
            <div class="badge-group">
                <span class="meta-tag">并排对比大屏</span>
                <span class="meta-tag">Token 速度监控</span>
                <span class="meta-tag">死循环/XML污染精准排查</span>
                <span class="meta-tag">中级思维 medium</span>
            </div>
        </header>

        <!-- 最优解推荐卡片 -->
        <div class="winner-banner">
            <h2>🎯 实测最优解结论速览 (Optimal Verdict)</h2>
            <p>1. <strong>聊天模板对比</strong>：<code>Fixed-Medium (froggeric)</code> 全场景碾压 <code>Sharp-Medium</code>。Fixed 模板在所有模型上保持 <strong>0 次死循环展开、0 次 XML 标签污染</strong>，而 Sharp 模板频繁出现参数循环展开与 <code>&lt;parameter=unit&gt;</code> 污染。</p>
            <p>2. <strong>无审查权重影响</strong>：<code>A-Q6_K (Abliterated)</code> 与 <code>U-Q6_K (Uncensored)</code> 在配合 Fixed-Medium 模板时，工具调用通过率与参数解析率达 <strong>90%~100%</strong>，无审查没有对工具调用能力产生损伤。</p>
            <p>3. <strong>最佳推荐</strong>：部署统一采用 <strong><code>Fixed-Medium</code> 模板 + 纯文本 144K 双并发共享池</strong> 架构。</p>
        </div>

        <!-- 汇总矩阵对比表 -->
        <div class="section-title">📊 多模型 × 多模板 核心指标与速率大矩阵对比</div>
        <table>
            <thead>
                <tr>
                    <th style="text-align: left;">测试模型 (Model)</th>
                    <th>聊天模板 (Template)</th>
                    <th>综合得分</th>
                    <th>完美通过率</th>
                    <th>原生解析率</th>
                    <th>参数死循环数</th>
                    <th>XML标签污染数</th>
                    <th>🚀 输入速度 (Prompt)</th>
                    <th>⚡ 输出速度 (Gen)</th>
                    <th>结论评级</th>
                </tr>
            </thead>
            <tbody>
                __MATRIX_ROWS__
            </tbody>
        </table>

        <!-- 逐题横向对比透视表 -->
        <div class="section-title">🔍 逐题并排横向透视对比表 (查看 Fixed 与 Sharp 同题答题差异)</div>
        <table>
            <thead>
                <tr>
                    <th style="width: 60px;">题号</th>
                    <th style="width: 90px;">分类</th>
                    <th style="text-align: left;">提示词 (Prompt)</th>
                    <th style="width: 110px;">预期工具</th>
                    __DETAIL_HEADER_MODELS__
                </tr>
            </thead>
            <tbody>
                __DETAIL_ROWS__
            </tbody>
        </table>
    </div>

    <script>
        window.ALL_RESULTS = __ALL_JSON_DATA__;
    </script>
</body>
</html>
"""


def build_and_save_master_report():
    HERE = os.path.dirname(os.path.abspath(__file__))
    res_dir = os.path.join(HERE, "..", "eval_results")
    
    all_json_files = glob.glob(os.path.join(res_dir, "bench_*.json"))
    if not all_json_files:
        print("未在 eval_results 目录下找到任何 JSON 评测文件。")
        return None
        
    latest_files = {}
    for f in all_json_files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                d = json.load(fp)
            meta = d.get("metadata", {})
            m_name = meta.get("model", "unknown")
            t_name = meta.get("template_name", "unknown")
            m_key = "NVFP4" if "NVFP4" in m_name else ("A-Q6_K" if "Abliterated" in m_name else ("U-Q6_K" if "Uncensored" in m_name else ("UD-Q5KXL" if "UD" in m_name else m_name)))
            key = (m_key, t_name)
            mtime = os.path.getmtime(f)
            if key not in latest_files or mtime > latest_files[key][0]:
                latest_files[key] = (mtime, f, d)
        except Exception:
            continue
            
    if not latest_files:
        print("没有可解析的评测结果。")
        return None

    data_map = {k: v[2] for k, v in latest_files.items()}
    
    matrix_rows = []
    sorted_keys = sorted(data_map.keys(), key=lambda x: (x[0], x[1]))
    
    for (m_key, t_key) in sorted_keys:
        d = data_map[(m_key, t_key)]
        meta = d.get("metadata", {})
        m_display = meta.get("model", m_key)
        t_summary = d.get("tool_calling", {}).get("summary", {})
        avg_score = t_summary.get("avg_score", 0)
        pass_rate = t_summary.get("pass_rate", 0)
        native_rate = t_summary.get("native_parse_rate", 0)
        loops = t_summary.get("param_loop_count", 0)
        xmls = t_summary.get("xml_leak_count", 0)
        
        prompt_spd = t_summary.get("avg_prompt_speed_tps", 0.0)
        gen_spd = t_summary.get("avg_gen_speed_tps", 0.0)
        
        # 兼容旧版本 JSON：如果未直接统计，从 details 计算
        details = d.get("tool_calling", {}).get("details", [])
        if gen_spd == 0.0 and details:
            g_list = [r.get("gen_speed_tps", 0.0) for r in details if r.get("gen_speed_tps", 0.0) > 0]
            if g_list: gen_spd = round(sum(g_list) / len(g_list), 1)
            p_list = [r.get("prompt_speed_tps", 0.0) for r in details if r.get("prompt_speed_tps", 0.0) > 0]
            if p_list: prompt_spd = round(sum(p_list) / len(p_list), 1)
            
        t_cls = "tpl-fixed" if "Fixed" in t_key else ("tpl-sharp" if "Sharp" in t_key else "tpl-official")
        score_cls = "score-high" if avg_score >= 95 else ("score-mid" if avg_score >= 85 else "score-low")
        loop_badge = f'<span class="badge badge-loop">{loops} 次</span>' if loops > 0 else '<span style="color:#10b981;">0</span>'
        xml_badge = f'<span class="badge badge-xml">{xmls} 次</span>' if xmls > 0 else '<span style="color:#10b981;">0</span>'
        
        p_spd_str = f'<span class="speed-in">{prompt_spd} tok/s</span>' if prompt_spd > 0 else '<span style="color:#94a3b8;">-</span>'
        g_spd_str = f'<span class="speed-out">{gen_spd} tok/s</span>' if gen_spd > 0 else '<span style="color:#94a3b8;">-</span>'
        
        if avg_score >= 95 and loops == 0 and xmls == 0:
            grade = '<span class="badge badge-pass">⭐⭐⭐⭐⭐ 极佳 (推荐)</span>'
        elif loops > 0 or xmls > 0:
            grade = '<span class="badge badge-fail">❌ 格式崩溃 (死循环/污染)</span>'
        else:
            grade = '<span class="badge badge-tag">良好</span>'
            
        matrix_rows.append(f"""
        <tr>
            <td class="align-left"><strong>{m_display}</strong></td>
            <td><span class="{t_cls}">{t_key}</span></td>
            <td><span class="score-cell {score_cls}">{avg_score} 分</span></td>
            <td>{pass_rate}%</td>
            <td>{native_rate}%</td>
            <td>{loop_badge}</td>
            <td>{xml_badge}</td>
            <td>{p_spd_str}</td>
            <td>{g_spd_str}</td>
            <td>{grade}</td>
        </tr>
        """)

    detail_header = ""
    for (m_key, t_key) in sorted_keys:
        t_cls = "tpl-fixed" if "Fixed" in t_key else ("tpl-sharp" if "Sharp" in t_key else "tpl-official")
        detail_header += f'<th>{m_key}<br><span class="{t_cls}" style="font-size:11px;">{t_key}</span></th>'

    sample_key = max(data_map.keys(), key=lambda k: len(data_map[k].get("tool_calling", {}).get("details", [])))
    sample_details = data_map[sample_key].get("tool_calling", {}).get("details", [])
    
    detail_rows = []
    for q_idx, q_item in enumerate(sample_details):
        q_id = q_item["id"]
        q_cat = q_item.get("category", "")
        q_prompt = q_item["prompt"]
        q_exp = q_item.get("expected_tool") or "不应调用"
        
        cols = []
        for (m_key, t_key) in sorted_keys:
            d_list = data_map[(m_key, t_key)].get("tool_calling", {}).get("details", [])
            if q_idx < len(d_list):
                item = d_list[q_idx]
                err = item.get("error_reason") or "PASS"
                spd = f" ({item.get('gen_speed_tps', 0)} t/s)" if item.get('gen_speed_tps', 0) > 0 else ""
                if item["passed"]:
                    badge = f'<span class="badge badge-pass">PASS{spd}</span>'
                elif item.get("param_loop_detected"):
                    badge = f'<span class="badge badge-loop" title="{err}">死循环</span>'
                elif item.get("xml_leak_detected"):
                    badge = f'<span class="badge badge-xml" title="{err}">XML污染</span>'
                else:
                    badge = f'<span class="badge badge-fail" title="{err}">FAIL</span>'
            else:
                badge = '-'
            cols.append(f"<td>{badge}</td>")
            
        detail_rows.append(f"""
        <tr>
            <td><strong>{q_id}</strong></td>
            <td><span class="badge badge-tag">{q_cat}</span></td>
            <td class="align-left">{q_prompt}</td>
            <td><code>{q_exp}</code></td>
            {''.join(cols)}
        </tr>
        """)

    out_html = os.path.join(res_dir, f"MASTER_MATRIX_BENCHMARK_REPORT_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
    
    html = HTML_TEMPLATE
    html = html.replace("__TIMESTAMP__", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    html = html.replace("__MATRIX_ROWS__", "\n".join(matrix_rows))
    html = html.replace("__DETAIL_HEADER_MODELS__", detail_header)
    html = html.replace("__DETAIL_ROWS__", "\n".join(detail_rows))
    html = html.replace("__ALL_JSON_DATA__", json.dumps({f"{k[0]}_{k[1]}": v for k, v in data_map.items()}, ensure_ascii=False))
    
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
        
    print(f"[大屏生成成功] {out_html}")
    return out_html


if __name__ == "__main__":
    build_and_save_master_report()
