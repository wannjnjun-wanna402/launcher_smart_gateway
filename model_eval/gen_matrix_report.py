# -*- coding: utf-8 -*-
"""
=============================================================================
Qwen3.8 工具调用深度专项报告生成器 (HTML Report Generator)
=============================================================================
将工具调用专项评测 JSON 生成现代化的 HTML5 诊断报告：
- Sharp-Medium vs Fixed-Medium 模板表现
- 参数死循环展开 / XML 污染 / 意图识别准确率
- 无审查与官方基准模型横向对比
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
    <title>Qwen3.8 工具调用深度专项评测报告</title>
    <style>
        :root {
            --bg-primary: #0f172a;
            --bg-card: #1e293b;
            --bg-card-sub: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent-cyan: #06b6d4;
            --accent-blue: #3b82f6;
            --accent-green: #10b981;
            --accent-yellow: #f59e0b;
            --accent-red: #ef4444;
            --border-color: #475569;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg-primary);
            color: var(--text-main);
            line-height: 1.6;
            padding: 24px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
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
        h1 { font-size: 26px; font-weight: 700; color: var(--accent-cyan); display: flex; align-items: center; gap: 10px; }
        .meta-tag { background: #0369a1; color: #e0f2fe; padding: 4px 10px; border-radius: 6px; font-size: 13px; }
        
        .grid-summary {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3);
        }
        .card h3 { font-size: 14px; color: var(--text-muted); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
        .card .big-num { font-size: 32px; font-weight: 800; }
        .card .sub-text { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
        
        .c-green { color: var(--accent-green); }
        .c-yellow { color: var(--accent-yellow); }
        .c-red { color: var(--accent-red); }
        .c-cyan { color: var(--accent-cyan); }
        
        .diagnosis-box {
            background: linear-gradient(135deg, rgba(6,182,212,0.1) 0%, rgba(59,130,246,0.05) 100%);
            border: 1px solid #0891b2;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }
        .diagnosis-box h2 { font-size: 18px; color: var(--accent-cyan); margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
        .diagnosis-box ul { padding-left: 20px; }
        .diagnosis-box li { margin-bottom: 6px; font-size: 14px; }
        
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 12px;
            background: var(--bg-card);
            border-radius: 8px;
            overflow: hidden;
            font-size: 14px;
        }
        th, td { padding: 12px 14px; text-align: left; border-bottom: 1px solid var(--border-color); }
        th { background: var(--bg-card-sub); color: var(--text-main); font-weight: 600; }
        tr:hover { background: rgba(255,255,255,0.03); }
        
        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }
        .badge-pass { background: rgba(16,185,129,0.2); color: var(--accent-green); border: 1px solid var(--accent-green); }
        .badge-fail { background: rgba(239,68,68,0.2); color: var(--accent-red); border: 1px solid var(--accent-red); }
        .badge-tag { background: rgba(6,182,212,0.15); color: var(--accent-cyan); }
        
        pre {
            background: #090d16;
            padding: 8px 12px;
            border-radius: 6px;
            font-family: Consolas, Monaco, "Courier New", monospace;
            font-size: 12px;
            color: #cbd5e1;
            overflow-x: auto;
            max-height: 180px;
            margin-top: 6px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>🛠️ Qwen3.8 工具调用专项诊断报告</h1>
                <div style="margin-top: 6px; color: var(--text-muted); font-size: 13px;">
                    生成时间: <span>__TIMESTAMP__</span> | 评测目标: 聊天模板对比 (Sharp vs Fixed) × 权重无审查深度检验
                </div>
            </div>
            <div>
                <span class="meta-tag">中级思维 (Medium Effort)</span>
                <span class="meta-tag">工具调用专项 (20 题)</span>
            </div>
        </header>

        <!-- 核心指标摘要卡片 -->
        <div class="grid-summary">
            <div class="card">
                <h3>工具调用总评分</h3>
                <div class="big-num c-cyan">__TOOL_SCORE__ 分</div>
                <div class="sub-text">完美通过率: <span class="c-green">__PASS_RATE__%</span></div>
            </div>
            <div class="card">
                <h3>原生结构化解析率</h3>
                <div class="big-num c-green">__NATIVE_PARSE__%</div>
                <div class="sub-text">OpenAI tool_calls 结构化返回率</div>
            </div>
            <div class="card">
                <h3>参数死循环展开数</h3>
                <div class="big-num c-red">__PARAM_LOOPS__</div>
                <div class="sub-text">字段重复写几十遍 / 递归展开异常</div>
            </div>
            <div class="card">
                <h3>XML 标签污染数</h3>
                <div class="big-num c-yellow">__XML_LEAKS__</div>
                <div class="sub-text">&lt;parameter&gt; 等非标准标签渗漏次数</div>
            </div>
        </div>

        <!-- 诊断结论框 -->
        <div class="diagnosis-box">
            <h2>🔍 工具调用专项根因诊断与建议 (Expert Diagnostics)</h2>
            <ul>
                __DIAGNOSTICS_HTML__
            </ul>
        </div>

        <!-- 工具调用明细表格 -->
        <div class="card" style="padding: 0; overflow: hidden;">
            <table>
                <thead>
                    <tr>
                        <th style="width: 80px;">题号</th>
                        <th style="width: 140px;">分类</th>
                        <th>提示词 (Prompt)</th>
                        <th style="width: 140px;">预期工具</th>
                        <th style="width: 90px;">状态</th>
                        <th style="width: 70px;">耗时</th>
                        <th>执行诊断 / 参数反馈</th>
                    </tr>
                </thead>
                <tbody>
                    __TOOL_ROWS__
                </tbody>
            </table>
        </div>
    </div>

    <script>
        window.RESULTS = __RAW_JSON_OBJECT__;
    </script>
</body>
</html>
"""


def generate_single_report(json_path, output_html_path=None):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    meta = data.get("metadata", {})
    t_summary = data.get("tool_calling", {}).get("summary", {})
    t_details = data.get("tool_calling", {}).get("details", [])
    
    diag_lines = []
    
    # 模板与死循环诊断
    param_loops = t_summary.get("param_loop_count", 0)
    xml_leaks = t_summary.get("xml_leak_count", 0)
    native_rate = t_summary.get("native_parse_rate", 0)
    pass_rate = t_summary.get("pass_rate", 0)
    
    if param_loops > 0:
        diag_lines.append(f"<li style='color:#ef4444;'><strong>【参数重复死循环严重警报】</strong> 本次评测捕获到 {param_loops} 次键名重复循环展开（如 path/offset 重复写几十遍导致体积膨胀）。这是 <code>Qwen-Sharp</code> 等篡改模板生成边界诱发的典型死循环，请立即停止在 Agent 中使用该模板！</li>")
    else:
        diag_lines.append("<li><strong>【参数生成干净】</strong> 0 次参数重复展开，键值对结构严谨规整。</li>")
        
    if xml_leaks > 0:
        diag_lines.append(f"<li style='color:#f59e0b;'><strong>【XML 标签污染】</strong> 捕获到 {xml_leaks} 次参数包含 <code>&lt;parameter=...&gt;</code> 标签，证明模板强加的 XML 规则污染了 JSON 参数。</li>")
        
    if native_rate >= 95.0:
        diag_lines.append(f"<li><strong>【原生提取率优秀】</strong> 原生 tool_calls 解析率达到 {native_rate}%，与 llama-server 抽取器高度协同。</li>")
    else:
        diag_lines.append(f"<li><strong>【格式失配】</strong> 原生解析率仅 {native_rate}%，部分工具调用被降级为正文字符串。</li>")
        
    diag_lines.append(f"<li><strong>【模型与模板结论】</strong> 当前模型: <code>{meta.get('model')}</code> | 模板: <code>{meta.get('template_name')}</code> | 完美通过率: <strong>{pass_rate}%</strong>。推荐使用 <code>Fixed-Medium (froggeric)</code> 作为全场景唯一标准模板。</li>")
    
    tool_rows = []
    for row in t_details:
        badge = '<span class="badge badge-pass">PASS</span>' if row["passed"] else '<span class="badge badge-fail">FAIL</span>'
        loop_badge = '<span class="badge badge-fail" style="margin-left:4px;">死循环</span>' if row.get("param_loop_detected") else ''
        xml_badge = '<span class="badge badge-fail" style="margin-left:4px;">XML污染</span>' if row.get("xml_leak_detected") else ''
        
        diag = row.get("error_reason") or "完美调用并正确解析参数"
        raw_args_html = f"<pre><code>{row.get('raw_arguments', '')[:250]}</code></pre>" if not row["passed"] and row.get('raw_arguments') else ""
        
        tool_rows.append(f"""
        <tr>
            <td><strong>{row['id']}</strong></td>
            <td><span class="badge badge-tag">{row['category']}</span></td>
            <td>{row['prompt']}</td>
            <td><code>{row.get('expected_tool') or '不应调用'}</code></td>
            <td>{badge}{loop_badge}{xml_badge}</td>
            <td>{row.get('elapsed_seconds', 0)}s</td>
            <td>
                <div style="font-size:13px; color:{'#10b981' if row['passed'] else '#f87171'}">{diag}</div>
                {raw_args_html}
            </td>
        </tr>
        """)
        
    html = HTML_TEMPLATE
    html = html.replace("__TIMESTAMP__", meta.get("timestamp", str(datetime.now())))
    html = html.replace("__TOOL_SCORE__", str(t_summary.get("avg_score", 0)))
    html = html.replace("__PASS_RATE__", str(t_summary.get("pass_rate", 0)))
    html = html.replace("__NATIVE_PARSE__", str(t_summary.get("native_parse_rate", 0)))
    html = html.replace("__PARAM_LOOPS__", str(param_loops))
    html = html.replace("__XML_LEAKS__", str(xml_leaks))
    html = html.replace("__DIAGNOSTICS_HTML__", "\n".join(diag_lines))
    html = html.replace("__TOOL_ROWS__", "\n".join(tool_rows))
    html = html.replace("__RAW_JSON_OBJECT__", json.dumps(data, ensure_ascii=False))
    
    if not output_html_path:
        output_html_path = json_path.replace(".json", ".html")
        
    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write(html)
        
    print(f"[报告生成成功] 可视化报告已保存至: {output_html_path}")
    return output_html_path


if __name__ == "__main__":
    if len(sys.argv) > 1:
        generate_single_report(sys.argv[1])
    else:
        files = glob.glob(os.path.join(HERE, "..", "eval_results", "bench_*.json"))
        if files:
            latest = max(files, key=os.path.getmtime)
            generate_single_report(latest)
