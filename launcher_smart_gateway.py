#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================================
 🤖 AI 智能任务自适应网关启动器 v5.0 (方案B · 纯 Python 原生驱动引擎)
 专为 Tesla V100 32GB 打造：纯 Qwen3.8-27B 旗舰统一矩阵 · 4.5秒无感热切换 · 0秒动态思考调控
 彻底告别 Windows CMD 乱码、ANSI 颜色失效与热切换进程脱钩崩溃限制
 Date: 2026-09-02
====================================================================================
"""

import os
import sys
import time
import socket
import json
import urllib.request
import urllib.error
import subprocess
import signal

# ------------------------------------------------------------------------------------
# 基础路径与环境常量
# ------------------------------------------------------------------------------------
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = r"E:\models"
SERVER_EXE = os.path.join(ROOT_DIR, "llama-server.exe")
TEMPLATE_FILE = os.path.join(ROOT_DIR, "chat_template_qwen_fixed.jinja")
LOG_DIR = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ------------------------------------------------------------------------------------
# ANSI 控制台彩色输出与 Windows 终端配置
# ------------------------------------------------------------------------------------
ANSI_COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "cyan": "\033[96m",
    "white": "\033[97m",
    "gray": "\033[90m",
}

def init_terminal():
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # 启用控制台虚拟终端颜色支持 (ENABLE_VIRTUAL_TERMINAL_PROCESSING)
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            mode.value |= 0x0004
            kernel32.SetConsoleMode(handle, mode)
            # 设置控制台窗口标题
            kernel32.SetConsoleTitleW("AI 智能任务自适应网关 - Unified 27B Flagship Gateway v5.0")
        except Exception:
            pass
        os.system("")

def print_c(text: str, color: str = "white", end: str = "\n"):
    prefix = ANSI_COLORS.get(color.lower(), ANSI_COLORS["white"])
    reset = ANSI_COLORS["reset"]
    print(f"{prefix}{text}{reset}", end=end, flush=True)

# ------------------------------------------------------------------------------------
# 0. 历史日志生命周期自动管理（自动清理超过 90 天的历史日志）
# ------------------------------------------------------------------------------------
def clean_expired_logs(days: int = 90):
    if not os.path.exists(LOG_DIR):
        return
    now = time.time()
    threshold = now - (days * 86400)
    cleaned = 0
    try:
        for fname in os.listdir(LOG_DIR):
            if fname.endswith(".log"):
                fpath = os.path.join(LOG_DIR, fname)
                try:
                    if os.path.getmtime(fpath) < threshold:
                        os.remove(fpath)
                        cleaned += 1
                except Exception:
                    pass
    except Exception:
        pass
    if cleaned > 0:
        print_c(f"  🧹 自动归档清理 {cleaned} 个超过 {days} 天的历史旧日志", "gray")

# ------------------------------------------------------------------------------------
# 1. 端口检测与网关守护
# ------------------------------------------------------------------------------------
def is_port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0

def ensure_gateway():
    clean_expired_logs(90)
    if not is_port_listening(8081):
        print_c("  🚀 正在拉起 8081 智能自适应调度网关...", "cyan")
        python_exe = sys.executable
        proxy_py = os.path.join(ROOT_DIR, "qwen_tool_proxy.py")
        today = time.strftime("%Y%m%d")
        proxy_log = os.path.join(LOG_DIR, f"8081_proxy_{today}.log")
        
        try:
            log_f = open(proxy_log, "a", encoding="utf-8")
            creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
            subprocess.Popen(
                [python_exe, proxy_py, "--listen", "8081", "--target", "8083", "--api-key", "llamacpp"],
                cwd=ROOT_DIR,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=creationflags
            )
            # 等待网关就绪
            for _ in range(20):
                if is_port_listening(8081):
                    break
                time.sleep(0.2)
        except Exception as e:
            print_c(f"  ⚠️ 启动 8081 网关警告: {e}", "yellow")

# ------------------------------------------------------------------------------------
# 2. 显存与进程绝对安全清理（严格遵守 AGENTS.md 标准）
# ------------------------------------------------------------------------------------
def stop_all_ai_services():
    print_c("  🧹 正在安全停止所有 AI 进程与显存回收...", "yellow")
    # 终止所有 llama 相关进程
    try:
        subprocess.run(
            ['powershell', '-NoProfile', '-Command', 'Get-Process | Where-Object { $_.ProcessName -match "llama" } | Stop-Process -Force'],
            capture_output=True
        )
    except Exception:
        pass
    
    # 释放 8083 端口占用
    try:
        subprocess.run(
            ['powershell', '-NoProfile', '-Command', 'Get-NetTCPConnection -LocalPort 8083 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }'],
            capture_output=True
        )
    except Exception:
        pass

    # 循环检查 GPU 显存，低于 600MB 才算彻底干净
    for _ in range(10):
        try:
            res = subprocess.run(
                ['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'],
                capture_output=True, text=True
            )
            if res.returncode == 0 and res.stdout.strip():
                lines = [l.strip() for l in res.stdout.strip().splitlines() if l.strip()]
                if lines and int(lines[0]) < 600:
                    break
        except Exception:
            pass
        time.sleep(0.5)

# ------------------------------------------------------------------------------------
# 3. 纯 Qwen3.8-27B 旗舰 3 大场景形态配置定义
# ------------------------------------------------------------------------------------
STATE_MTP_2SLOT = "MTP_2SLOT"
STATE_PIPELINE_4SLOT = "PIPELINE_4SLOT"
STATE_VISION_27B = "VISION_27B"

MODEL_PROFILES = {
    "1": {
        "state_key": STATE_MTP_2SLOT,
        "name": "Qwen3.8-27B-A [双槽MTP]",
        "speed": "36.7 tok/s (投机加速)",
        "aa_index": "52 分 (开源TOP 1)",
        "desc": "【默认基准常驻态】单兵极速 · 原生 MTP 加速 · 动态注入 low/medium/xhigh 思考",
        "type_tag": "👑 极速基准态",
        "vram": "27.4 GB"
    },
    "2": {
        "state_key": STATE_PIPELINE_4SLOT,
        "name": "Qwen3.8-27B-A [4并发流水线]",
        "speed": "23.2 tok/s (总吞吐 45+ tok/s)",
        "aa_index": "52 分 (开源TOP 1)",
        "desc": "【高负载流水线态】4 槽并发 · 零排队交替输入 · 多 Agent 批量协作王者",
        "type_tag": "🚀 并发流水线态",
        "vram": "28.5 GB"
    },
    "3": {
        "state_key": STATE_VISION_27B,
        "name": "Qwen3.8-27B-A [原生多模态视觉]",
        "speed": "31.5 tok/s",
        "aa_index": "52 分 (全模态旗舰)",
        "desc": "【原生多模态视觉态】挂载 mmproj-27B · 27B 原生看图 + 27B 顶尖写代码",
        "type_tag": "👁️ 原生视觉态",
        "vram": "27.4 GB"
    }
}

STATE_DISPLAY_MAP = {
    STATE_MTP_2SLOT: "👑 Qwen3.8-27B-A [双槽MTP 极速基准态] (36.7 t/s · 144K)",
    STATE_PIPELINE_4SLOT: "🚀 Qwen3.8-27B-A [4并发流水线态] (45+ t/s · 144K)",
    STATE_VISION_27B: "👁️ Qwen3.8-27B-A [原生多模态视觉态] (31.5 t/s · 128K)"
}

# ------------------------------------------------------------------------------------
# 4. 触发网关热切换与实时状态守护循环 (Live State Guard)
# ------------------------------------------------------------------------------------
def switch_backend_state(target_state: str) -> bool:
    url = "http://127.0.0.1:8081/api/switch"
    payload = json.dumps({"target_state": target_state}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("success", False)
    except Exception:
        return False

def get_backend_state():
    url = "http://127.0.0.1:8081/api/state"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

def start_selected_profile(key: str):
    p = MODEL_PROFILES.get(key)
    target_state = p["state_key"] if p else STATE_MTP_2SLOT

    ensure_gateway()

    today = time.strftime("%Y%m%d")
    daily_log = os.path.join(LOG_DIR, f"8083_llama_{today}.log")

    print_c("", "white")
    print_c("====================================================================================", "cyan")
    print_c(f"  🚀 正在向 Tesla V100 注入显存，载入形态: {p['name'] if p else target_state}...", "green")
    print_c("====================================================================================", "cyan")
    print_c(f"  ├─ 🌐 统一接口 : http://127.0.0.1:8081/v1 (全应用统一接入点)", "cyan")
    print_c(f"  ├─ 📊 算力大屏 : http://127.0.0.1:8081/dashboard", "cyan")
    print_c(f"  ├─ ⚡ 运行基准 : 27B 双槽MTP / 4并发 / 原生视觉 4.5秒无感热切换矩阵", "white")
    print_c(f"  └─ 💾 今日日志 : {daily_log}", "white")
    print_c("====================================================================================", "cyan")
    print_c("  ⏳ 正在进行内存级初始化，预计耗时约 4~5 秒...", "gray")
    print_c("", "white")

    # 触发目标形态加载
    ok = switch_backend_state(target_state)
    if ok:
        print_c(f"  ✅ 目标形态 [{target_state}] 已成功就绪！", "green")
    else:
        print_c("  ⏳ 网关正在自适应调度中，进入实时守护监控...", "yellow")

    print_c("\n" + "=" * 84, "cyan")
    print_c("  🟢 AI 智能网关实时守护与算力中枢已进入常驻运行 (按 Ctrl+C 安全停止服务)", "green")
    print_c("=" * 84 + "\n", "cyan")

    # 进入常驻实时监控守护循环（完全免疫热切换导致的进程脱钩与退出）
    last_known_state = target_state
    tick = 0
    try:
        while True:
            time.sleep(1.5)
            tick += 1
            st = get_backend_state()
            if not st:
                # 若网关意外掉线，自动拉起
                ensure_gateway()
                continue

            current_st = st.get("current_state", "UNKNOWN")
            is_healthy = st.get("server_healthy", False)
            active_text = st.get("active_text", 0)
            active_vis = st.get("active_vision", 0)
            today_reqs = st.get("today_reqs", 0)
            today_cost = st.get("today_cost", 0.0)

            # 感知后台自适应热切换并打印通知
            if current_st != last_known_state:
                disp_name = STATE_DISPLAY_MAP.get(current_st, current_st)
                print_c(f"[{time.strftime('%H:%M:%S')}] 🔄 [动态热切换] 当前激活形态: {disp_name} (4.5s 内存级就绪)", "yellow")
                last_known_state = current_st

            # 每 10 次循环打印一次心跳状态行
            if tick % 10 == 0:
                disp_name = STATE_DISPLAY_MAP.get(current_st, current_st)
                status_icon = "🟢" if is_healthy else "⏳"
                print_c(f"[{time.strftime('%H:%M:%S')}] {status_icon} [状态巡检] 形态: {disp_name} | 活跃任务: {active_text} | 今日调用: {today_reqs} 次 (¥{today_cost:.2f})", "gray")

    except KeyboardInterrupt:
        print_c("\n\n  ⚠️ 接收到退出信号 (Ctrl+C)，正在安全关闭所有 AI 服务...", "yellow")
        stop_all_ai_services()
        print_c("  ✅ 服务已完全安全退出。", "green")
        sys.exit(0)

# ------------------------------------------------------------------------------------
# 5. 主菜单与自动热等待倒计时交互
# ------------------------------------------------------------------------------------
def main():
    init_terminal()
    ensure_gateway()

    print_c("====================================================================================", "cyan")
    print_c("   🤖 AI 智能任务自适应网关  ·  Unified 27B Flagship Gateway v5.0 (方案B · 纯Python引擎)", "green")
    print_c("====================================================================================", "cyan")
    print_c("   [网关统一入口] http://127.0.0.1:8081/v1 (全应用统一接入点)", "white")
    print_c("   [实时监控看板] http://127.0.0.1:8081/dashboard", "white")
    print_c("   [核心架构规范] 纯 27B 旗舰统一矩阵 · 4.5秒内存级热切 · 0秒动态思考等级调控", "gray")
    print_c("====================================================================================", "cyan")
    print_c("   请选择启动模式 (默认 5 秒后自动载入 【1】 Qwen3.8-27B-A [双槽MTP] 常驻基准态):", "yellow")
    print_c("", "white")
    print_c("   [1] 👑 Qwen3.8-27B-A [双槽MTP]     │ 36.7 t/s │ AA:52分 │ 日常单兵极速 / 默认常驻 (默认首选)", "green")
    print_c("   [2] 🚀 Qwen3.8-27B-A [4并发流水线] │ 45.0 t/s │ AA:52分 │ 4槽交替流水线 / 多Agent批量协同", "cyan")
    print_c("   [3] 👁️ Qwen3.8-27B-A [原生多模态]  │ 31.5 t/s │ AA:52分 │ 挂载 mmproj-27B / 原生视觉深度推理", "yellow")
    print_c("   [4] 🛠️ 纯后台网关守护模式 (仅常驻 8081 网关，全权自适应无感热调度)", "gray")
    print_c("   [Q] 退出启动器", "red")
    print_c("------------------------------------------------------------------------------------", "cyan")

    timeout_sec = 5
    selected = None

    if sys.platform == "win32":
        try:
            import msvcrt
            for remaining in range(timeout_sec, 0, -1):
                sys.stdout.write(f"\r   ⏳ 默认启动 [1] 27B 双槽MTP 常驻基准态 倒计时: {remaining} 秒 (按 1~4 键手动选定)... ")
                sys.stdout.flush()
                start_t = time.time()
                while time.time() - start_t < 1.0:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        try:
                            key_str = ch.decode('utf-8', errors='ignore')
                        except Exception:
                            key_str = ""
                        if key_str in ['1', '2', '3', '4', 'q', 'Q', '\r', '\n']:
                            selected = '1' if key_str in ['\r', '\n'] else key_str.upper()
                            break
                    time.sleep(0.05)
                if selected is not None:
                    break
            sys.stdout.write("\r" + " " * 85 + "\r")
            sys.stdout.flush()
        except Exception:
            selected = "1"
    else:
        selected = "1"

    if not selected:
        selected = "1"
        print_c("   ⏳ 倒计时结束，自动载入默认 [1] 27B 双槽MTP 常驻基准态...", "yellow")
    else:
        print_c(f"   👉 已选定模式: [{selected}]", "green")

    if selected == "Q":
        print_c("已退出。", "gray")
        sys.exit(0)
    elif selected in ["1", "2", "3"]:
        start_selected_profile(selected)
    elif selected == "4":
        print_c("🟢 纯后台网关守护已就绪 (8081)，全权按需 4.5 秒自适应热调度...", "green")
        start_selected_profile("1")
    else:
        print_c("无效选择，默认启动 [1] 27B 双槽MTP 常驻基准态...", "yellow")
        start_selected_profile("1")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print_c(f"\n❌ 启动器运行异常: {e}", "red")
        traceback.print_exc()
        try:
            input("\n按回车键退出...")
        except Exception:
            pass
