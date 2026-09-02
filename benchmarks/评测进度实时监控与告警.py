#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后台监控脚本 - 实时监控 RAG 测评进度
每 30 秒检查一次状态，异常时自动报告
"""
import json
import os
import sys
import time
import glob
from datetime import datetime
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

RESULTS_DIR = Path("e:/llama-win-cuda-12.4-x64/eval_results")
LOG_DIR = Path("e:/llama-win-cuda-12.4-x64/eval_results")

def get_completed_models():
    """获取已完成的模型列表"""
    results = []
    for f in glob.glob(str(RESULTS_DIR / "*_real_eval.json")):
        if "summary" in f.lower():
            continue
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            model_name = data.get("model_name", "Unknown")
            avg = data.get("average_scores", {})
            results.append({
                "model": model_name,
                "total": avg.get("total", 0),
                "decision": avg.get("decision", 0),
                "query": avg.get("query", 0),
                "answer": avg.get("answer", 0),
                "file": os.path.basename(f),
                "timestamp": data.get("timestamp", ""),
                "valid_count": len([r for r in data.get("results", []) if "error" not in r]),
                "total_count": len(data.get("results", [])),
            })
        except Exception:
            pass
    return sorted(results, key=lambda x: x["total"], reverse=True)

def get_current_launch_logs():
    """获取当前正在运行的模型日志"""
    logs = []
    for f in sorted(glob.glob(str(LOG_DIR / "launch_*.log")), key=os.path.getmtime, reverse=True):
        try:
            mtime = os.path.getmtime(f)
            age = time.time() - mtime
            if age < 600:  # 10分钟内的日志
                with open(f, "r", encoding="utf-8", errors="replace") as fp:
                    content = fp.read()[-2000:]  # 最后2000字符
                logs.append({
                    "file": os.path.basename(f),
                    "age_seconds": int(age),
                    "content": content
                })
        except Exception:
            pass
    return logs

def check_search_quality():
    """检查已完成模型的搜索结果质量"""
    issues = []
    for f in glob.glob(str(RESULTS_DIR / "*_real_eval.json")):
        if "summary" in f.lower():
            continue
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            
            model_name = data.get("model_name", "Unknown")
            for r in data.get("results", []):
                if "error" in r:
                    continue
                search = r.get("search", {})
                count = search.get("count", 0)
                query = search.get("query", "")
                
                # 检查搜索结果质量
                if count > 0:
                    # 检查返回结果是否包含字典释义
                    search_text = json.dumps(search, ensure_ascii=False).lower()
                    if len(query) <= 2:
                        issues.append(f"⚠️ {model_name} Q{r.get('question_id')}: 搜索query过短 '{query}'")
        except Exception as e:
            pass
    return issues

def generate_status_report():
    """生成状态报告"""
    completed = get_completed_models()
    launch_logs = get_current_launch_logs()
    search_issues = check_search_quality()
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    report = []
    report.append(f"\n{'='*60}")
    report.append(f"📊 RAG 测评监控报告 - {now}")
    report.append(f"{'='*60}")
    
    if completed:
        report.append(f"\n✅ 已完成 {len(completed)} 个模型：")
        report.append("-" * 60)
        for m in completed:
            status = "⚠️" if m["valid_count"] < m["total_count"] else "✅"
            report.append(f"  {status} {m['model']:<30} 总分:{m['total']:5.1f}/75 "
                         f"(决策:{m['decision']:.0f} Query:{m['query']:.0f} 答案:{m['answer']:.0f}) "
                         f"[{m['valid_count']}/{m['total_count']}题]")
    else:
        report.append("\n⏳ 暂无模型完成，等待中...")
    
    if launch_logs:
        report.append(f"\n🔄 当前运行中模型日志（最近{len(launch_logs)}个）：")
        for log in launch_logs[:2]:  # 只显示最近2个
            report.append(f"  - {log['file']} ({log['age_seconds']}秒前)")
            # 提取关键信息
            last_lines = log['content'].strip().split('\n')[-5:]
            for line in last_lines:
                line = line.strip()
                if line and len(line) < 100:
                    report.append(f"    | {line}")
    
    if search_issues:
        report.append(f"\n⚠️ 搜索质量警告 ({len(search_issues)}个)：")
        for issue in search_issues[:5]:
            report.append(f"  {issue}")
        if len(search_issues) > 5:
            report.append(f"  ... 还有 {len(search_issues) - 5} 个警告")
    
    report.append(f"\n{'='*60}")
    return "\n".join(report)

def monitor_loop():
    """监控循环"""
    print("🔍 启动 RAG 测评后台监控...")
    print("   监控间隔: 30秒")
    print("   日志目录: eval_results/")
    print()
    
    last_report_time = 0
    consecutive_errors = 0
    
    try:
        while True:
            now = time.time()
            
            # 每30秒生成一次报告
            if now - last_report_time >= 30:
                report = generate_status_report()
                print(report)
                last_report_time = now
                
                # 检查是否所有模型都完成
                completed = get_completed_models()
                if len(completed) >= 7:  # 假设有7个模型
                    print("\n🎉 所有模型测评完成！")
                    break
            
            # 检查脚本是否仍在运行
            eval_process = os.popen('tasklist /FI "IMAGENAME eq python.exe" /FO CSV').read()
            if "python" not in eval_process.lower():
                # 检查是否还有 launch 日志在更新
                launch_logs = get_current_launch_logs()
                if not launch_logs and len(get_completed_models()) > 0:
                    print("\n✅ 测评脚本已完成，生成最终报告...")
                    break
            
            time.sleep(10)  # 10秒检查一次
    
    except KeyboardInterrupt:
        print("\n📊 监控已停止")
    except Exception as e:
        print(f"\n❌ 监控异常: {e}")

if __name__ == "__main__":
    if "--once" in sys.argv:
        # 单次报告模式
        print(generate_status_report())
    else:
        # 持续监控模式
        monitor_loop()
