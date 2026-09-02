#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基准测评可视化 HTML 报告生成器 (Visual HTML Report Generator)
  支持：
    - 前沿 6 大专有基准 (frontier6_*.json)
    - 11 维全能评测 (pro11_v2_*.json)
  特性：
    - 现代化玻璃拟物暗色 UI (Dark Mode + Glassmorphism)
    - 响应式交互柱状图 (Animated Bar Charts) + 多维雷达图 (SVG Radar Chart)
    - 官方 Head-to-Head 巅峰对决表
    - 交互式答卷排查与判分诊断折叠卡片
"""
import os, sys, json, glob

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

_curr_dir = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = _curr_dir if os.path.exists(os.path.join(_curr_dir, 'llama-server.exe')) else os.path.dirname(_curr_dir)
OUT_DIR = os.path.join(BASE_DIR, "bench_results")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ - 大模型基准测评可视化报告</title>
<style>
:root {
  --bg: #0b0f19;
  --card-bg: rgba(18, 26, 43, 0.75);
  --card-border: rgba(255, 255, 255, 0.08);
  --text: #f3f4f6;
  --text-muted: #9ca3af;
  --accent: #00f2fe;
  --accent2: #4facfe;
  --purple: #7928ca;
  --green: #10b981;
  --yellow: #f59e0b;
  --red: #ef4444;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
  background: var(--bg);
  background-image: radial-gradient(at 0% 0%, rgba(79, 172, 254, 0.12) 0px, transparent 50%),
                    radial-gradient(at 100% 100%, rgba(121, 40, 202, 0.12) 0px, transparent 50%);
  color: var(--text);
  line-height: 1.6;
  padding: 30px 20px;
  min-height: 100vh;
}
.container { max-width: 1200px; margin: 0 auto; }
.header {
  text-align: center;
  margin-bottom: 35px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--card-border);
}
.header h1 {
  font-size: 2.2rem;
  background: linear-gradient(135deg, #00f2fe 0%, #4facfe 50%, #7928ca 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  margin-bottom: 10px;
}
.header-meta {
  color: var(--text-muted);
  font-size: 0.95rem;
}
.badge {
  display: inline-block;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 0.85rem;
  font-weight: 600;
  margin: 0 4px;
  background: rgba(0, 242, 254, 0.15);
  color: var(--accent);
  border: 1px solid rgba(0, 242, 254, 0.3);
}

/* KPI Cards */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 20px;
  margin-bottom: 35px;
}
.kpi-card {
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  backdrop-filter: blur(12px);
  border-radius: 16px;
  padding: 22px;
  text-align: center;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}
.kpi-title { font-size: 0.9rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px; }
.kpi-val { font-size: 2.5rem; font-weight: 800; margin: 8px 0; color: #fff; }
.kpi-val.highlight {
  background: linear-gradient(135deg, #00f2fe, #4facfe);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.kpi-sub { font-size: 0.85rem; color: var(--text-muted); }

/* Charts Section */
.charts-grid {
  display: grid;
  grid-template-columns: 1.2fr 0.8fr;
  gap: 25px;
  margin-bottom: 35px;
}
@media (max-width: 900px) { .charts-grid { grid-template-columns: 1fr; } }
.chart-box {
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  backdrop-filter: blur(12px);
  border-radius: 16px;
  padding: 25px;
}
.chart-box h2 {
  font-size: 1.2rem;
  margin-bottom: 20px;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Bar Chart */
.bar-group { margin-bottom: 18px; }
.bar-header { display: flex; justify-content: space-between; margin-bottom: 6px; font-size: 0.95rem; }
.bar-track {
  height: 14px;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 7px;
  overflow: hidden;
  position: relative;
}
.bar-fill {
  height: 100%;
  border-radius: 7px;
  background: linear-gradient(90deg, #4facfe, #00f2fe);
  transition: width 1s cubic-bezier(0.4, 0, 0.2, 1);
}
.bar-fill.purple { background: linear-gradient(90deg, #7928ca, #ff0080); }
.bar-fill.green { background: linear-gradient(90deg, #10b981, #059669); }
.bar-fill.yellow { background: linear-gradient(90deg, #f59e0b, #d97706); }

/* Table */
.table-box {
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  backdrop-filter: blur(12px);
  border-radius: 16px;
  padding: 25px;
  margin-bottom: 35px;
  overflow-x: auto;
}
.table-box h2 { font-size: 1.2rem; margin-bottom: 15px; }
table { width: 100%; border-collapse: collapse; text-align: left; }
th, td { padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,0.06); }
th { font-size: 0.85rem; text-transform: uppercase; color: var(--text-muted); background: rgba(255,255,255,0.02); }
tr:hover { background: rgba(255,255,255,0.03); }
.score-badge {
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(0, 242, 254, 0.15);
  color: var(--accent);
}
.winner-tag {
  font-weight: 700;
  color: #10b981;
}

/* Question List Accordion */
.task-item {
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--card-border);
  border-radius: 10px;
  margin-bottom: 12px;
  overflow: hidden;
}
.task-head {
  padding: 14px 18px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  background: rgba(255,255,255,0.03);
}
.task-head:hover { background: rgba(255,255,255,0.05); }
.task-body {
  padding: 18px;
  border-top: 1px solid var(--card-border);
  display: none;
  font-size: 0.9rem;
}
.task-body.show { display: block; }
.code-block {
  background: #050811;
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 8px;
  padding: 12px;
  margin-top: 8px;
  font-family: Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-all;
  color: #e5e7eb;
  max-height: 260px;
  overflow-y: auto;
}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>__TITLE__</h1>
    <div class="header-meta">
      测评时间: <code>__STAMP__</code> | 
      参评模型: __MODEL_BADGES__
    </div>
  </div>

  <div class="kpi-grid">
    __KPI_CARDS__
  </div>

  <div class="charts-grid">
    <!-- Bar Chart -->
    <div class="chart-box">
      <h2>📊 各项专有基准得分柱状图</h2>
      <div id="bar-chart-container">
        __BAR_CHARTS__
      </div>
    </div>

    <!-- Radar Chart / Speed Chart -->
    <div class="chart-box">
      <h2>🎯 综合能力雷达多边形</h2>
      <div style="text-align: center; padding: 10px 0;">
        __RADAR_SVG__
      </div>
    </div>
  </div>

  <!-- Scorecard Table -->
  <div class="table-box">
    <h2>🏆 权威专有基准成绩矩阵</h2>
    __SCORE_TABLE__
  </div>

  <!-- Task Details -->
  <div class="table-box">
    <h2>📋 逐题答卷与确定性判分诊断明细</h2>
    <div id="task-list">
      __TASK_DETAILS__
    </div>
  </div>
</div>

<script>
function toggleTask(id) {
  var el = document.getElementById('body-' + id);
  if (el) el.classList.toggle('show');
}
</script>
</body>
</html>
"""

def generate_radar_svg(categories, values, max_val=100, size=280):
    import math
    cx, cy, r = size / 2, size / 2, size * 0.38
    n = len(categories)
    if n < 3:
        return "<p style='color:var(--text-muted);'>维度少于3项，不渲染雷达图</p>"

    # Grid rings
    grid_svg = ""
    for level in [0.25, 0.5, 0.75, 1.0]:
        pts = []
        for i in range(n):
            angle = (i * 2 * math.pi / n) - (math.pi / 2)
            x = cx + r * level * math.cos(angle)
            y = cy + r * level * math.sin(angle)
            pts.append(f"{x:.1f},{y:.1f}")
        grid_svg += f'<polygon points="{" ".join(pts)}" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>'

    # Web axis lines & labels
    axis_svg = ""
    for i in range(n):
        angle = (i * 2 * math.pi / n) - (math.pi / 2)
        x2 = cx + r * math.cos(angle)
        y2 = cy + r * math.sin(angle)
        axis_svg += f'<line x1="{cx}" y1="{cy}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="rgba(255,255,255,0.1)"/>'
        
        # Label pos
        lx = cx + (r + 20) * math.cos(angle)
        ly = cy + (r + 15) * math.sin(angle)
        anchor = "middle"
        if math.cos(angle) > 0.3: anchor = "start"
        elif math.cos(angle) < -0.3: anchor = "end"
        label_text = categories[i][:6]
        axis_svg += f'<text x="{lx:.1f}" y="{ly:.1f}" fill="#9ca3af" font-size="10" text-anchor="{anchor}" dominant-baseline="middle">{label_text}</text>'

    # Data polygon
    data_pts = []
    for i, v in enumerate(values):
        val_ratio = min(max(v, 0), max_val) / max_val
        angle = (i * 2 * math.pi / n) - (math.pi / 2)
        x = cx + r * val_ratio * math.cos(angle)
        y = cy + r * val_ratio * math.sin(angle)
        data_pts.append(f"{x:.1f},{y:.1f}")

    poly_str = " ".join(data_pts)
    data_svg = f'''
    <polygon points="{poly_str}" fill="rgba(0, 242, 254, 0.25)" stroke="#00f2fe" stroke-width="2.5"/>
    '''
    for pt in data_pts:
        px, py = pt.split(",")
        data_svg += f'<circle cx="{px}" cy="{py}" r="3.5" fill="#4facfe"/>'

    return f'''
    <svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">
      {grid_svg}
      {axis_svg}
      {data_svg}
    </svg>
    '''

def generate_html_report(json_path):
    if not os.path.exists(json_path):
        print(f"ERROR: 文件不存在 {json_path}")
        return None

    with open(json_path, "r", encoding="utf-8") as fp:
        data = json.load(fp)

    is_frontier6 = "benchmarks" in data
    stamp = data.get("stamp", "Unknown")
    models = data.get("models", {})
    if not models:
        print("ERROR: 数据中没有模型记录")
        return None

    title = "前沿 6 大专有基准对决战报" if is_frontier6 else "11 维专业模型全能测评报告"
    categories = data.get("benchmarks", []) if is_frontier6 else data.get("dims", [])

    model_badges = "".join([f'<span class="badge">{m_data.get("label", m_k)}</span>' for m_k, m_data in models.items()])

    # 1. KPI Cards
    kpi_cards = ""
    for m_k, m_data in models.items():
        score = m_data.get("overall_score", 0.0)
        tps = m_data.get("avg_tps", 0.0)
        label = m_data.get("label", m_k)
        kpi_cards += f'''
        <div class="kpi-card">
          <div class="kpi-title">{label} 综合得分</div>
          <div class="kpi-val highlight">{score:.1f}</div>
          <div class="kpi-sub">推理生成速度: <b>{tps}</b> tok/s</div>
        </div>
        '''

    # 2. Bar Charts
    bar_charts = ""
    # Use primary model or compare
    primary_model_key = list(models.keys())[0]
    primary_model = models[primary_model_key]
    scores_dict = primary_model.get("bench_scores", {}) if is_frontier6 else primary_model.get("dim_scores", {})
    
    for cat in categories:
        s = scores_dict.get(cat, 0.0)
        color_cls = ""
        if s >= 80: color_cls = ""
        elif s >= 60: color_cls = "green"
        elif s >= 40: color_cls = "yellow"
        else: color_cls = "purple"

        bar_charts += f'''
        <div class="bar-group">
          <div class="bar-header">
            <span><b>{cat}</b></span>
            <span class="score-badge">{s:.1f} 分</span>
          </div>
          <div class="bar-track">
            <div class="bar-fill {color_cls}" style="width: {s}%"></div>
          </div>
        </div>
        '''

    # 3. Radar Chart
    radar_values = [scores_dict.get(cat, 0.0) for cat in categories]
    radar_svg = generate_radar_svg(categories, radar_values, max_val=100)

    # 4. Score Table
    table_html = "<table><thead><tr><th>Benchmark 评测项目</th>"
    for m_k, m_data in models.items():
        table_html += f"<th>{m_data.get('label', m_k)}</th>"
    if len(models) >= 2:
        table_html += "<th>胜方</th>"
    table_html += "</tr></thead><tbody>"

    for cat in categories:
        table_html += f"<tr><td><b>{cat}</b></td>"
        row_scores = []
        for m_k, m_data in models.items():
            sc = (m_data.get("bench_scores", {}) if is_frontier6 else m_data.get("dim_scores", {})).get(cat, 0.0)
            row_scores.append(sc)
            table_html += f"<td><span class='score-badge'>{sc:.1f}</span></td>"
        if len(models) >= 2:
            diff = row_scores[0] - row_scores[1]
            if abs(diff) < 0.1: w_text = "平手"
            elif diff > 0: w_text = f"<b>{list(models.values())[0].get('short_name','Qwen')} 胜</b>"
            else: w_text = f"<b>{list(models.values())[1].get('short_name','Ornith')} 胜</b>"
            table_html += f"<td class='winner-tag'>{w_text}</td>"
        table_html += "</tr>"
    table_html += "</tbody></table>"

    # 5. Task Details
    task_details = ""
    for m_k, m_data in models.items():
        tasks = m_data.get("tasks", [])
        for idx, t in enumerate(tasks):
            t_id = t.get("id", f"T{idx}")
            t_title = t.get("title", "")
            t_bench = t.get("benchmark") or t.get("dim", "")
            t_score = t.get("score", 0.0)
            t_detail = t.get("detail", "")
            t_ans = t.get("answer", "").replace("<", "&lt;").replace(">", "&gt;")

            score_color = "var(--accent)" if t_score >= 80 else ("var(--yellow)" if t_score >= 50 else "var(--red)")

            task_details += f'''
            <div class="task-item">
              <div class="task-head" onclick="toggleTask('{t_id}')">
                <span><b>[{t_id}]</b> {t_bench} · {t_title}</span>
                <span><b style="color:{score_color}; font-size:1.05rem;">{t_score:.1f}分</b> <small style="color:var(--text-muted);">({t.get('tps',0)} tok/s)</small> ▾</span>
              </div>
              <div class="task-body" id="body-{t_id}">
                <p><b>判分诊断:</b> <code>{t_detail}</code></p>
                <div style="margin-top:8px; font-weight:600; color:var(--text-muted);">模型完整输出答卷:</div>
                <div class="code-block">{t_ans}</div>
              </div>
            </div>
            '''

    out_html = HTML_TEMPLATE
    out_html = out_html.replace("__TITLE__", title)
    out_html = out_html.replace("__STAMP__", stamp)
    out_html = out_html.replace("__MODEL_BADGES__", model_badges)
    out_html = out_html.replace("__KPI_CARDS__", kpi_cards)
    out_html = out_html.replace("__BAR_CHARTS__", bar_charts)
    out_html = out_html.replace("__RADAR_SVG__", radar_svg)
    out_html = out_html.replace("__SCORE_TABLE__", table_html)
    out_html = out_html.replace("__TASK_DETAILS__", task_details)

    html_path = os.path.splitext(json_path)[0] + ".html"
    with open(html_path, "w", encoding="utf-8") as fp:
        fp.write(out_html)

    print(f"✓ 可视化 HTML 报告生成成功: {html_path}")
    return html_path

def open_in_browser(html_path):
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    try:
        if os.path.exists(chrome_path):
            import subprocess
            subprocess.Popen([chrome_path, html_path])
        else:
            os.startfile(html_path)
    except Exception:
        pass

def main():
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    if not target:
        files = sorted(glob.glob(os.path.join(OUT_DIR, "frontier6_*.json")), key=os.path.getmtime, reverse=True)
        if not files:
            files = sorted(glob.glob(os.path.join(OUT_DIR, "pro11_v2_*.json")), key=os.path.getmtime, reverse=True)
        if files:
            target = files[0]
        else:
            print("未找到任何评测 JSON 文件")
            return
    hp = generate_html_report(target)
    if hp and os.path.exists(hp):
        open_in_browser(hp)

if __name__ == "__main__":
    main()
