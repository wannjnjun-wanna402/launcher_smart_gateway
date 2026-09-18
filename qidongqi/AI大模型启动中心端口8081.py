#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 大模型启动中心 端口8081 (Python 移植增强版)
H:\\models 自动扫描 | llama-server 网页服务 | 性能监控 | 启动自检

基于 AI大模型启动中心端口8081-v94.ps1 完全重构移植
- 100% 保持 v94 启动预设、DRY 采样禁用铁律、qwen_fixed 模板配置与自检体系
- 原生 Python 毫秒级极速响应，无需加载 PowerShell 庞大运行时
- 精准 CJK 全角/半角排版与 ANSI 真彩色高亮
"""

import os
import sys
import re
import time
import socket
import ctypes
import datetime
import threading
import subprocess
import webbrowser
import urllib.request
import json
import winreg

# ==========================================================
#  控制台环境初始化 (UTF-8 与 ANSI 虚拟终端序列)
# ==========================================================
kernel32 = ctypes.windll.kernel32

# 设置控制台代码页为 65001 (UTF-8)
kernel32.SetConsoleOutputCP(65001)
kernel32.SetConsoleCP(65001)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 启用 Windows 控制台 VT100 / ANSI 转义序列支持
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
hStdOut = kernel32.GetStdHandle(-11)
mode = ctypes.c_ulong()
if kernel32.GetConsoleMode(hStdOut, ctypes.byref(mode)):
    kernel32.SetConsoleMode(hStdOut, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)

kernel32.SetConsoleTitleW("AI 大模型启动中心 端口8081")

# ==========================================================
#  常量与配置
# ==========================================================
DOMAIN     = "http://wannjnjun.eicp.net"
EXT_PORT   = "50080"
OLLAMA_KEY = "llamacpp"
LLAMA_KEY  = "llamacpp"
MCP_KEY    = "llamacpp"
LLAMA_DIR  = r"H:\llama-bin-win-cuda-13.3-x64"
MODELS_DIR = r"H:\models"
LLAMA_SERVER = os.path.join(LLAMA_DIR, "llama-server.exe")
LLAMA_PORT   = 8083   # llama-server 主模型底层引擎端口
SIDECAR_PORT = 8085   # llama-server 侧挂视觉眼睛 (Qwen3VL-4B CPU)
GATEWAY_PORT = 8081   # 外部统一智能网关端口 (模型映射/防爆总百分比动态剪枝/双模视觉路由/思考等级)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_gateway
import ai_tray
import ctypes
import atexit

_active_proc = None
_sidecar_proc = None

def start_vision_sidecar():
    """启动 8085 CPU 侧挂视觉模型 (Qwen3VL-4B-Instruct-Q4_K_M)"""
    global _sidecar_proc
    # 先检查 8085 是否已在运行且健康
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{SIDECAR_PORT}/health")
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            if resp.status == 200:
                write_c(f"  [8085 视觉侧挂] 眼睛模型已就绪 (Qwen3VL-4B on CPU)", "OK")
                return True
    except Exception:
        pass

    sidecar_model = os.path.join(MODELS_DIR, "Qwen3VL-4B-Instruct-Q4_K_M.gguf")
    sidecar_mmproj = os.path.join(MODELS_DIR, "mmproj-Qwen3VL-4B-Instruct-F16.gguf")
    if not os.path.isfile(sidecar_model) or not os.path.isfile(sidecar_mmproj):
        write_c("  [提示] 未找到 8085 侧挂视觉模型或 mmproj，跳过侧挂启动", "Warn")
        return False

    sidecar_args = [
        LLAMA_SERVER,
        "-m", sidecar_model,
        "--mmproj", sidecar_mmproj,
        "--alias", "Qwen3VL-4B-VisionEye",
        "-ngl", "0",
        "-t", "6",
        "-tb", "6",
        "-c", "8192",
        "-b", "1024",
        "-ub", "256",
        "--flash-attn", "auto",
        "--host", "127.0.0.1",
        "--port", str(SIDECAR_PORT),
        "--metrics"
    ]
    sidecar_log_out = os.path.join(LOG_DIR, "sidecar-8085.log")
    sidecar_log_err = os.path.join(LOG_DIR, "sidecar-8085.err")
    try:
        f_out = open(sidecar_log_out, "wb")
        f_err = open(sidecar_log_err, "wb")
        _sidecar_proc = subprocess.Popen(sidecar_args, stdout=f_out, stderr=f_err, creationflags=0)
        write_c(f"  [8085 视觉侧挂] 已拉起 CPU 眼睛模型进程 (Qwen3VL-4B-Instruct-Q4_K_M)...", "GPU")
        return True
    except Exception as e:
        write_c(f"  [8085 视觉侧挂] 启动失败: {e}", "Error")
        return False

def stop_vision_sidecar():
    """关闭 8085 CPU 侧挂视觉模型"""
    global _sidecar_proc
    if _sidecar_proc is not None and _sidecar_proc.poll() is None:
        try:
            _sidecar_proc.terminate()
            _sidecar_proc.wait(timeout=1.5)
        except Exception:
            try:
                _sidecar_proc.kill()
            except Exception:
                pass
        _sidecar_proc = None

def cleanup_everything():
    """无论以何种方式退出，彻底强杀主模型引擎、侧挂视觉引擎、关闭网关与托盘图标"""
    global _active_proc, _sidecar_proc
    try:
        ai_tray.stop_tray()
    except Exception:
        pass
    try:
        ai_gateway.stop_gateway()
    except Exception:
        pass
    if _active_proc is not None and _active_proc.poll() is None:
        try:
            _active_proc.terminate()
            _active_proc.wait(timeout=1.5)
        except Exception:
            try:
                _active_proc.kill()
            except Exception:
                pass
    if _sidecar_proc is not None and _sidecar_proc.poll() is None:
        try:
            _sidecar_proc.terminate()
            _sidecar_proc.wait(timeout=1.5)
        except Exception:
            try:
                _sidecar_proc.kill()
            except Exception:
                pass
    try:
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)
    except Exception:
        pass

def cleanup_everything_and_exit():
    cleanup_everything()
    os._exit(0)

# 注册 Windows 控制台关闭事件 (点击控制台右上角 X、注销、关机等)
PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
def console_ctrl_handler(ctrl_type):
    cleanup_everything()
    return False

_handler_ref = PHANDLER_ROUTINE(console_ctrl_handler)
try:
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, True)
except Exception:
    pass
atexit.register(cleanup_everything)
ai_gateway.set_shutdown_callback(cleanup_everything_and_exit)

LOG_DIR       = os.path.join(LLAMA_DIR, "logs")
LOG_SAMPLE_S  = 5
LOG_TIMEOUT   = 180
LOG_KEEP_DAYS = 30

os.makedirs(LOG_DIR, exist_ok=True)

# 自动清理 30 天前日志
try:
    now_ts = time.time()
    cutoff_ts = now_ts - (LOG_KEEP_DAYS * 86400)
    for fname in os.listdir(LOG_DIR):
        if fname.startswith("run-") and fname.endswith(".log"):
            fpath = os.path.join(LOG_DIR, fname)
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff_ts:
                try:
                    os.remove(fpath)
                except Exception:
                    pass
except Exception:
    pass

# ==========================================================
#  调色板与 ANSI 代码
# ==========================================================
ANSI = {
    "Black": "\033[30m",
    "Red": "\033[91m",
    "DarkRed": "\033[31m",
    "Green": "\033[92m",
    "DarkGreen": "\033[32m",
    "Yellow": "\033[93m",
    "DarkYellow": "\033[33m",
    "Blue": "\033[94m",
    "DarkBlue": "\033[34m",
    "Magenta": "\033[95m",
    "DarkMagenta": "\033[35m",
    "Cyan": "\033[96m",
    "DarkCyan": "\033[36m",
    "White": "\033[97m",
    "Gray": "\033[37m",
    "DarkGray": "\033[90m",
    "Reset": "\033[0m"
}

COLOR_MAP = {
    "Title":   "Cyan",
    "Model":   "Yellow",
    "Tag":     "Green",
    "Size":    "DarkCyan",
    "Desc":    "White",
    "Rank":    "DarkYellow",
    "Header":  "DarkGray",
    "Label":   "Gray",
    "Warn":    "Magenta",
    "Error":   "Red",
    "OK":      "DarkGreen",
    "Line":    "DarkGray",
    "GPU":     "DarkCyan",
    "CPU":     "DarkYellow",
    "RAM":     "Green",
    "Disk":    "DarkMagenta",
    "Foot":    "DarkGray",
    "Input":   "White",
    "Domain":  "DarkYellow",
    "Key":     "DarkRed"
}

DESC_COLOR_MAP = {
    "图": "Magenta",
    "狱": "Red",
    "思": "DarkGreen",
    "理": "Green",
    "算": "Cyan",
    "码": "DarkCyan",
    "文": "Yellow",
    "语": "DarkYellow",
    "速": "White",
    "长": "Blue",
    "轻": "Gray",
    "工": "DarkMagenta",
    "智": "DarkRed",
    "官": "Green"
}

def write_c(text="", color="White", end="\n"):
    target_color = COLOR_MAP.get(color, color)
    code = ANSI.get(target_color, ANSI["White"])
    sys.stdout.write(f"{code}{text}{ANSI['Reset']}{end}")
    sys.stdout.flush()

def write_l(text="", color="White"):
    write_c(text, color, end="\n")

def clear_host():
    os.system("cls" if os.name == "nt" else "clear")

# ==========================================================
#  排版与字符宽度计算 (精准适配 CJK 全角与符号)
# ==========================================================
def get_char_width(c: str) -> int:
    code = ord(c)
    if (0x4E00 <= code <= 0x9FFF or
        0x3400 <= code <= 0x4DBF or
        0xF900 <= code <= 0xFAFF or
        0x3000 <= code <= 0x303F or
        0xFF00 <= code <= 0xFF60 or
        0xFFE0 <= code <= 0xFFEF or
        0x20000 <= code <= 0x2FA1F):
        return 2
    # 特殊图形符号 (◎, ◇, ※, ★, ─)
    if c in ("◎", "◇", "※", "★", "─"):
        return 2
    return 1

def get_str_width(text: str) -> int:
    return sum(get_char_width(c) for c in text)

def pad_w(text: str, target: int) -> str:
    w = get_str_width(text)
    pad = target - w
    if pad <= 0:
        return text
    return text + (" " * pad)

# ==========================================================
#  单字纹章渲染 (图◎ / 思◇ / 工※ / 官 等)
# ==========================================================
def write_sigil_char(char: str, static: bool = False):
    color = DESC_COLOR_MAP.get(char, "White")
    sym = ""
    if char == "图":
        sym = "◎"
    elif char == "思":
        sym = "◇"
    elif char == "工":
        sym = "※"

    full_char = f"{char}{sym}"
    if static:
        write_c(full_char, color, end="")
        return

    # 动画模式（原位微闪烁特效）
    alt_colors = [color]
    alt_times = [0]
    if char == "图":
        alt_colors = ["Magenta", "DarkMagenta"]
        alt_times = [0.06, 0.06]
        color = "Magenta"
    elif char == "思":
        alt_colors = ["DarkGreen", "Green"]
        alt_times = [0.07, 0.07]
        color = "Green"
    elif char == "工":
        alt_colors = ["DarkMagenta", "DarkRed"]
        alt_times = [0.04, 0.04]
        color = "DarkMagenta"

    w = get_str_width(full_char)
    for i, ac in enumerate(alt_colors):
        write_c(full_char, ac, end="")
        if alt_times[i] > 0:
            time.sleep(alt_times[i])
        if i < len(alt_colors) - 1:
            sys.stdout.write("\b" * w)
            sys.stdout.flush()

def write_desc_c(desc: str, static: bool = False) -> int:
    order = ["图", "狱", "思", "理", "算", "码", "文", "语", "速", "长", "轻", "工", "智", "官"]
    chars = [o for o in order if o in desc]
    for c in desc:
        if c != " " and c not in chars:
            chars.append(c)

    total_w = 0
    for i, c in enumerate(chars):
        write_sigil_char(c, static=static)
        w = get_char_width(c)
        if c in ("图", "思", "工"):
            w += 2  # 特殊符号占 2 宽
        total_w += w
        if i < len(chars) - 1:
            sys.stdout.write(" ")
            sys.stdout.flush()
            total_w += 1
    return total_w

# ==========================================================
#  硬件检测
# ==========================================================
class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]

def get_hardware_info():
    info = {
        "GPU": "未检测到",
        "VRAM": "N/A",
        "GPUCLK": "",
        "CUDA": "",
        "CPU": "检测失败",
        "CPUCLK": "",
        "RAM": "N/A",
        "RAMCLK": "",
        "MB": "",
        "OS": "Windows",
        "LANIP": "192.168.11.131"
    }

    # ── 1. GPU (nvidia-smi) ──
    try:
        cmd = ["nvidia-smi", "--query-gpu=name,memory.total,clocks.gr,driver_version", "--format=csv,noheader,nounits"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            first_line = res.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            gpu_raw = parts[0]
            gpu_name = re.sub(r"^NVIDIA\s+(GeForce\s+)?", "", gpu_raw).strip()
            total_vram_mb = int(parts[1])
            info["GPU"] = gpu_name
            info["VRAM"] = f"{round(total_vram_mb / 1024.0, 1)}GB"
            if len(parts) > 2 and parts[2]:
                info["GPUCLK"] = f"{parts[2]}MHz"
            if len(parts) > 3 and parts[3]:
                info["CUDA"] = parts[3]
    except Exception:
        pass

    # ── 2. CPU (Windows Registry & os) ──
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        cpu_name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        mhz, _ = winreg.QueryValueEx(key, "~MHz")
        cpu_clean = cpu_name.replace("(R)", "").replace("(TM)", "").replace("Core", "").strip()
        cpu_clean = " ".join(cpu_clean.split())
        threads = os.cpu_count() or 12
        # 获取核心数（通过 wmic 或经验值近似）
        cores = threads // 2 if threads > 4 else threads
        info["CPU"] = f"{cpu_clean} {cores}C/{threads}T"
        info["CPUCLK"] = f"{mhz}MHz"
    except Exception:
        pass

    # ── 3. RAM (GlobalMemoryStatusEx) ──
    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        total_gb = round(stat.ullTotalPhys / (1024**3), 1)
        info["RAM"] = f"{total_gb}GB"
        # 尝试通过 wmic 读取内存频率
        try:
            m_res = subprocess.run(["wmic", "memorychip", "get", "ConfiguredClockSpeed,Speed"], capture_output=True, text=True, timeout=2)
            if m_res.returncode == 0:
                nums = re.findall(r"\b\d{3,5}\b", m_res.stdout)
                if nums:
                    info["RAMCLK"] = f"{nums[0]}MHz"
        except Exception:
            pass
    except Exception:
        pass

    # ── 4. 主板 (Registry BIOS) ──
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS")
        vendor, _ = winreg.QueryValueEx(key, "BaseBoardManufacturer")
        prod, _ = winreg.QueryValueEx(key, "BaseBoardProduct")
        vendor = vendor.strip()
        prod = prod.strip()
        if prod and prod != "To be filled by O.E.M.":
            info["MB"] = f"{vendor} {prod}" if vendor not in prod else prod
        elif vendor:
            info["MB"] = vendor
    except Exception:
        pass

    # ── 5. 系统 ──
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        prod_name, _ = winreg.QueryValueEx(key, "ProductName")
        try:
            b_str, _ = winreg.QueryValueEx(key, "CurrentBuildNumber")
            b_num = int(b_str)
        except Exception:
            b_num = 0
        try:
            d_ver, _ = winreg.QueryValueEx(key, "DisplayVersion")
        except Exception:
            d_ver = ""

        os_str = prod_name.replace("Microsoft ", "").strip()
        # 微软为兼容性在注册表中保留了 Windows 10 字样，Build >= 22000 即为 Windows 11
        if b_num >= 22000 and "Windows 10" in os_str:
            os_str = os_str.replace("Windows 10", "Windows 11")
        if d_ver:
            os_str = f"{os_str} {d_ver}"
        info["OS"] = os_str
    except Exception:
        info["OS"] = "Windows 11"

    # ── 6. LAN IP ──
    try:
        hostname = socket.gethostname()
        ips = socket.gethostbyname_ex(hostname)[2]
        valid_ips = [ip for ip in ips if not ip.startswith(("127.", "169.254."))]
        # 优先选取 192.168.*
        for ip in valid_ips:
            if ip.startswith("192.168."):
                info["LANIP"] = ip
                break
        else:
            if valid_ips:
                info["LANIP"] = valid_ips[-1]
    except Exception:
        info["LANIP"] = "192.168.11.131"

    return info

# ==========================================================
#  llama.cpp 版本自检
# ==========================================================
def get_llama_version():
    ver_info = {
        "Version": "",
        "BuildNum": "",
        "Commit": "",
        "Compiler": "",
        "Build": "",
        "OK": False
    }
    if not os.path.isfile(LLAMA_SERVER):
        return ver_info

    try:
        res = subprocess.run([LLAMA_SERVER, "--version"], capture_output=True, text=True, timeout=5)
        combined_output = f"{res.stdout}\n{res.stderr}"
        for line in combined_output.splitlines():
            s = line.strip()
            # 格式: version: 0.3.0-dev (build 10786, commit de8656bd9)
            m_ver = re.search(r"version:\s*(\S+)", s)
            if m_ver:
                ver_info["Version"] = m_ver.group(1)

            m_build_num = re.search(r"build\s+(\d+)", s, re.I)
            if m_build_num:
                ver_info["BuildNum"] = f"b{m_build_num.group(1)}"

            m_commit = re.search(r"commit\s+([0-9a-fA-F]+)", s, re.I)
            if m_commit:
                ver_info["Commit"] = m_commit.group(1)
            elif re.search(r"\(([0-9a-fA-F]{7,})\)", s):
                ver_info["Commit"] = re.search(r"\(([0-9a-fA-F]{7,})\)", s).group(1)

            m_build = re.search(r"built with\s+(.+?)\s+for\s+(.+)$", s)
            if m_build:
                ver_info["Compiler"] = m_build.group(1)
                ver_info["Build"] = m_build.group(2)

        ver_info["OK"] = bool(ver_info["Version"] or ver_info["BuildNum"])
    except Exception:
        pass

    return ver_info

# ==========================================================
#  模型发现与智能分类 (保持 v94 的三级排序和预设映射)
# ==========================================================
BPW_MAP = {
    "Q2_g64": 2.2, "IQ3_XXS": 3.06, "IQ3_S": 3.44, "IQ3_M": 3.90,
    "3.7bpw": 3.70, "Q4_0": 4.55, "NVFP4": 4.25, "Q4_K_M": 4.85, "Q8_0": 8.50, "f16": 16.0
}

def get_models():
    if not os.path.isdir(MODELS_DIR):
        return []

    # 排除辅助文件与草稿 sidecar
    files = [
        f for f in os.listdir(MODELS_DIR)
        if f.endswith(".gguf") and not re.search(r"mmproj|mm-project|DFlash|FastMTP", f, re.I)
    ]
    files.sort()

    models = []
    for f in files:
        full_path = os.path.join(MODELS_DIR, f)
        base_name = os.path.splitext(f)[0]
        size_bytes = os.path.getsize(full_path)
        size_gb = round(size_bytes / (1024**3), 1)
        size_str = f"{size_gb}GB" if size_gb >= 1 else f"{round(size_bytes / (1024**2))}MB"

        # 特征分类匹配 (与 v94 规则逐字对齐)
        if re.search(r"Qwen3VL-4B", base_name, re.I):
            display = "qwen3vl:4b-q4km"; ctx = "64K"; tag = "视觉/侧挂"; desc = "图 官 速 文"; spd = "140tok"; rank = "★★★★"; score = 78.5
        elif re.search(r"Qwen3VL.*unc", base_name, re.I):
            display = "qwen3vl:8b-hauhau-q8"; ctx = "96K"; tag = "视觉"; desc = "图 狱 文"; spd = "49tok"; rank = "★★★★"; score = 79.7
        elif re.search(r"Qwen3\.5-4B", base_name, re.I):
            display = "qwen3.5:4b-q8"; ctx = "256K"; tag = "官方"; desc = "官 速 文 长"; spd = "126tok"; rank = "★★★"; score = 77.2
        elif re.search(r"Qwen3\.6-35B.*iq3", base_name, re.I):
            display = "qwen3.6:35b-a3b"; ctx = "16K"; tag = "越狱"; desc = "狱 智 算 码"; spd = "107tok"; rank = "★★★"; score = 75.9
        elif re.search(r"Qwen3\.5-9B.*unc", base_name, re.I):
            display = "qwen3.5:9b-hauhau-q8"; ctx = "64K"; tag = "越狱"; desc = "狱 文 算"; spd = "80tok"; rank = "★★"; score = 69.6
        elif re.search(r"Ornith-1\.5.*Q8_0", base_name, re.I):
            display = "Ornith-1.5-9B"; ctx = "256K"; tag = "官方"; desc = "官 图 思 码 工 理"; spd = "85tok"; rank = "★★★"; score = 73.5
        elif re.search(r"Ternary-Bonsai.*Q2_g64", base_name, re.I):
            display = "bonsai-27b:q2g64"; ctx = "96K"; tag = "三进制"; desc = "图 思 理 算 长 轻"; spd = "73tok/s"; rank = "★★★★"; score = 80.5
        elif re.search(r"Qwen3\.8-27B-Ridge", base_name, re.I):
            display = "Qwen3.8-27B-Ridge"; ctx = "96K"; tag = "Ridge"; desc = "图 思 理 算 码"; spd = "84tok"; rank = "★★★★"; score = 80.0
        elif re.search(r"Qwen3\.8-27B-Heretic.*iq4.*mtp", base_name, re.I):
            display = "Qwen3.8-27B-H-A-M"; ctx = "48K"; tag = "IQ4-MTP"; desc = "图 思 理 算 码 智"; spd = "43tok"; rank = "★★★★★"; score = 84.0
        elif re.search(r"Qwen3\.8-27B-NVFP4.*VERY.LOW", base_name, re.I):
            display = "Qwen3.8-27B-NVFP4-M"; ctx = "16K"; tag = "NVFP4-MTP"; desc = "图 思 理 算 码 极速"; spd = "52tok"; rank = "★★★★★"; score = 82.0
        elif re.search(r"Qwen3\.8-27B-NVFP4-STARVED", base_name, re.I):
            display = "Qwen3.8-27B-NVFP4-STARVED"; ctx = "32K"; tag = "STARVED"; desc = "图 思 理 算 码 快"; spd = "55tok"; rank = "★★★★"; score = 76.0
        elif re.search(r"Qwen3\.8-27B-NVFP4", base_name, re.I):
            display = "Qwen3.8-27B-NVFP4"; ctx = "16K"; tag = "NVFP4"; desc = "图 思 理 算 码 快"; spd = "55tok"; rank = "★★★★"; score = 78.0
        # ========== Qwen3.8-27B-GSQ 系列 (区分 MTP 单并发 与 非MTP 4并发共享池) ==========
        elif re.search(r"Qwen3\.8-27B-GSQ.*mtp", base_name, re.I):
            display = "Qwen3.8-27B-GSQ-MTP"; ctx = "64K"; tag = "MTP"; desc = "图 思 理 算 码 极速"; spd = "65tok"; rank = "★★★★★"; score = 83.0
        elif re.search(r"Qwen3\.8-27B-GSQ", base_name, re.I):
            display = "Qwen3.8-27B-GSQ"; ctx = "64K"; tag = "4并发"; desc = "图 思 码 4并 智"; spd = "45tok"; rank = "★★★★"; score = 79.5
        elif re.search(r"Qwen3\.8-27B-UD.*IQ3_S", base_name, re.I):
            display = "Qwen3.8-27B-UD-IQ3_S"; ctx = "96K"; tag = "视觉"; desc = "图 思 理 算 码"; spd = "45tok"; rank = "★★★★"; score = 79.0
        elif re.search(r"Qwen3\.8-27B", base_name, re.I):
            display = "Qwen3.8-27B-UD-IQ3"; ctx = "96K"; tag = "视觉"; desc = "图 思 理 算 码"; spd = "45tok"; rank = "★★★★"; score = 79.0
        else:
            display = base_name; ctx = "自动"; tag = "其他"; desc = "未评测"; spd = "-"; rank = "★★"; score = 0.0

        # 量化级别
        q_matches = re.findall(r"(IQ[0-9]+_[A-Za-z0-9_]+|Q[0-9]+_[A-Za-z0-9_]+)", base_name)
        quant = q_matches[-1] if q_matches else ""
        if re.search(r"Qwen3\.8-27B-Ridge", base_name, re.I):
            quant = "3.7bpw"
        if "NVFP4" in base_name:
            quant = "NVFP4"

        # 参数规模 (4B / 9B / 27B / 35B)
        p_match = re.search(r"-(\d+(?:\.\d+)?)[Bb]", base_name)
        param_num = float(p_match.group(1)) if p_match else 0.0

        # 代数次序 (3.5 < 3.6 < 3.8 < ornith)
        if re.search(r"Qwen3\.5", base_name, re.I):
            iter_order = 1
        elif re.search(r"Qwen3VL|Vision", base_name, re.I):
            iter_order = 1
        elif re.search(r"Qwen3\.6|35B.*A3B|Ternary-Bonsai", base_name, re.I):
            iter_order = 2
        elif re.search(r"Qwen3\.8", base_name, re.I):
            iter_order = 3
        elif re.search(r"ornith", base_name, re.I):
            iter_order = 4
        else:
            iter_order = 9

        bpw = BPW_MAP.get(quant, 9.0)

        models.append({
            "name": base_name,
            "display": display,
            "size_str": size_str,
            "size_gb": size_gb,
            "quant": quant,
            "param_num": param_num,
            "iter_order": iter_order,
            "bpw": bpw,
            "ctx_str": ctx,
            "spd_str": spd,
            "tag": tag,
            "desc": desc,
            "rank": rank,
            "score": score,
            "path": full_path
        })

    # 三级排序：参数量升序 -> 迭代代数升序 -> 量化精度(BPW)升序 -> 名称
    models.sort(key=lambda m: (m["param_num"], m["iter_order"], m["bpw"], m["name"]))
    for i, m in enumerate(models, 1):
        m["index"] = i

    return models

# ==========================================================
#  短别名与视觉塔匹配
# ==========================================================
def get_short_alias(file_name: str) -> str:
    base = re.sub(r"\.gguf$", "", file_name, flags=re.I)
    base = re.sub(r"-[IQ][A-Z]\d+[_-][A-Z_]+$", "", base)
    base = re.sub(r"-[IQ][A-Z]\d+$", "", base)
    base = re.sub(r"-Q\d+_[A-Z_0-9]+$", "", base)
    base = re.sub(r"-Q\d+$", "", base)
    base = re.sub(r"-HauhauCS.*$", "", base)
    base = re.sub(r"-Aggressive.*$", "", base)
    base = re.sub(r"-Uncensored.*$", "", base)
    base = re.sub(r"-Instruct.*$", "", base)
    base = re.sub(r"-[bf]16$", "", base)
    base = re.sub(r"-BF16$", "", base)
    return base[:30]

def find_matching_mmproj(model_name: str) -> str:
    if re.search(r"Qwen3VL-4B", model_name, re.I):
        for f in os.listdir(MODELS_DIR):
            if "mmproj-Qwen3VL-4B" in f and f.endswith(".gguf"):
                return os.path.join(MODELS_DIR, f)
        return ""

    if re.search(r"Qwen3VL-8B", model_name, re.I):
        for f in os.listdir(MODELS_DIR):
            if "Qwen3VL-8B" in f and "mmproj" in f and f.endswith(".gguf"):
                return os.path.join(MODELS_DIR, f)
        return ""

    if re.search(r"Qwen3\.8-27B", model_name, re.I):
        for f in os.listdir(MODELS_DIR):
            if "Qwen3.8-27B" in f and "mmproj" in f and f.endswith(".gguf"):
                return os.path.join(MODELS_DIR, f)
        return ""

    if re.search(r"Ornith-1\.5", model_name, re.I):
        for f in os.listdir(MODELS_DIR):
            if "Ornith-1.5-9B-mmproj" in f and f.endswith(".gguf"):
                return os.path.join(MODELS_DIR, f)
        return ""

    if "IQ4_XS" not in model_name:
        return ""

    m_series = re.search(r"^([A-Za-z0-9._-]+?\d+B)", model_name)
    series_key = m_series.group(1) if m_series else ""
    if not series_key:
        return ""

    mm_files = [f for f in os.listdir(MODELS_DIR) if "mmproj" in f and f.endswith(".gguf")]
    for f in mm_files:
        if series_key in f:
            return os.path.join(MODELS_DIR, f)

    short_prefix = re.sub(r"-?\d+B.*$", "", series_key)
    if len(short_prefix) >= 5:
        for f in mm_files:
            if short_prefix in f:
                return os.path.join(MODELS_DIR, f)

    return ""

# ==========================================================
#  菜单显示
# ==========================================================
def show_menu(models, hw, llama_ver):
    clear_host()

    # 标题
    write_c("  AI 大模型启动中心  端口 8081", "Title")
    print()

    # 硬件信息
    write_c("  GPU: ", "Label", end="")
    write_c(hw["GPU"], "GPU", end="")
    write_c(f" {hw['VRAM']}", "GPU", end="")
    if hw["GPUCLK"]:
        write_c(f"  {hw['GPUCLK']}", "GPU", end="")
    write_c("  |  CPU: ", "Label", end="")
    write_c(hw["CPU"], "CPU", end="")
    if hw["CPUCLK"]:
        write_c(f"  {hw['CPUCLK']}", "CPU", end="")
    write_c("  |  RAM: ", "Label", end="")
    write_c(hw["RAM"], "RAM", end="")
    if hw["RAMCLK"]:
        write_c(f"  {hw['RAMCLK']}", "RAM", end="")
    print()

    write_c("  主板: ", "Label", end="")
    write_c(hw["MB"] if hw["MB"] else "N/A", "CPU" if hw["MB"] else "Foot", end="")
    write_c("  |  CUDA: ", "Label", end="")
    write_c(hw["CUDA"] if hw["CUDA"] else "N/A", "GPU" if hw["CUDA"] else "Foot", end="")
    write_c("  |  OS: ", "Label", end="")
    write_c(hw["OS"], "CPU")

    # llama.cpp 版本
    if llama_ver and llama_ver["OK"]:
        write_c("  llama.cpp: ", "Label", end="")
        disp_ver = llama_ver.get("BuildNum") or (f"v{llama_ver['Version']}" if llama_ver.get("Version") else "就绪")
        write_c(disp_ver, "GPU", end="")
        if llama_ver.get("Commit"):
            write_c(f" ({llama_ver['Commit']})", "GPU", end="")
        if llama_ver.get("Compiler"):
            write_c(f"  |  {llama_ver['Compiler']}", "Foot", end="")
        write_c("  |  状态: ", "Label", end="")
        write_c("就绪", "OK")
    else:
        write_c("  llama.cpp: ", "Label", end="")
        write_c("未检测到 (llama-server.exe 缺失或无法运行)", "Error")

    print()

    # 模型列表表头
    write_c("  模型列表:", "Title")
    write_c("  ", "Label", end="")
    write_c(pad_w("序号", 4), "Label", end=" ")
    write_c(pad_w("模型名", 32), "Label", end=" ")
    write_c(pad_w("大小", 8), "Label", end=" ")
    write_c(pad_w("功能说明", 24), "Label", end=" ")
    write_c(pad_w("速度", 9), "Label", end=" ")
    write_c(pad_w("上下文", 7), "Label", end=" ")
    write_c(pad_w("量化", 9), "Label", end=" ")
    write_c("  得分", "Label")
    write_c("  " + ("─" * 115), "Line")

    # 模型列表行
    for m in models:
        sc = m["score"]
        if sc >= 85:
            sc_color = "Green"
        elif sc >= 75:
            sc_color = "Cyan"
        elif sc >= 65:
            sc_color = "Yellow"
        else:
            sc_color = "DarkGray"
        score_str = f"{sc:5.1f}" if sc > 0 else "  N/A"

        write_c("  ", "Input", end="")
        write_c(pad_w(f"[{m['index']}]", 4), "Input", end=" ")
        write_c(pad_w(m["display"], 32), "Model", end=" ")

        size_w = get_str_width(m["size_str"])
        if size_w < 8:
            sys.stdout.write(" " * (8 - size_w))
        write_c(m["size_str"], "Size", end=" ")

        sigil_w = write_desc_c(m["desc"], static=True)
        if sigil_w < 24:
            sys.stdout.write(" " * (24 - sigil_w))
        sys.stdout.write(" ")

        write_c(pad_w(m["spd_str"], 9), "Size", end=" ")
        write_c(pad_w(m["ctx_str"], 7), "GPU", end=" ")
        write_c(pad_w(m["quant"], 9), "Warn", end=" ")
        write_c(score_str, sc_color)

    print()

    # MCP 配置说明
    write_c("  MCP 配置:", "Title")
    write_c("  ", "Label", end="")
    write_c(pad_w("服务名", 25), "Label", end="")
    write_c("说明", "Label")
    write_c("  " + ("─" * 92), "Line")
    write_c("  ", "Input", end="")
    write_c(pad_w("ddg-search", 25), "Warn", end="")
    write_c("DuckDuckGo 联网搜索（前端托管，无需 Key）", "Desc")
    write_c("  ", "Input", end="")
    write_c(pad_w("anytxt", 25), "Warn", end="")
    write_c("AnyTxt 本地文档检索（桥接 127.0.0.1:9920，前端托管）", "Desc")
    print()

    write_c("  注: ", "Label", end="")
    write_c("MCP 已不再由 llama.cpp 后端挂载（已去 --tools/--webui-mcp-proxy/--mcp-servers-config）。", "Foot")
    write_c("       改用前端 harness（Qwen Code Desktop / Claude Desktop 等）读取 ", "Foot", end="")
    write_c("本机MCP配置指南.md", "Warn", end="")
    write_c(" 自行托管，减轻后端负担。", "Foot")
    print()

    # 单字图例
    write_c("  单字图例: ", "Title", end="")
    row1 = [("官", "官方"), ("图", "多模态"), ("狱", "越狱"), ("思", "思考"), ("理", "推理"), ("算", "数学"), ("码", "编码"), ("文", "中文")]
    for char, name in row1:
        write_sigil_char(char, static=True)
        write_c(f"={name}  ", "Label", end="")
    print()
    write_c("            ", "Label", end="")
    row2 = [("语", "多语"), ("速", "极速"), ("长", "长文"), ("轻", "轻量"), ("工", "工具"), ("智", "旗舰")]
    for char, name in row2:
        write_sigil_char(char, static=True)
        write_c(f"={name}  ", "Label", end="")
    print("\n")

    # 连接信息
    write_c("  连接信息:", "Title")
    write_c("  外网: ", "Label", end="")
    write_c(f"{DOMAIN}:{EXT_PORT}/llama/v1", "Domain", end="")
    write_c("  Key: ", "Label", end="")
    write_c(LLAMA_KEY, "Key")

    write_c("  💬 对话 WebUI: ", "Label", end="")
    write_c(f"http://127.0.0.1:{GATEWAY_PORT}/", "OK", end="")
    write_c("  (专注大模型 Web 聊天对话，支持图文多模态)", "Desc")

    write_c("  🌐 监控看板  : ", "Label", end="")
    write_c(f"http://127.0.0.1:{GATEWAY_PORT}/dashboard", "GPU", end="")
    write_c("  (专注网关监控，硬件显存/请求吞吐/虚拟账单)", "Desc")

    write_c("  接口: ", "Label", end="")
    write_c(f"http://127.0.0.1:{GATEWAY_PORT}/v1", "GPU", end="")
    write_c("  (Chatbox / Qwen Code / 任意 OpenAI 兼容客户端，免 Key)", "OK")

    write_c("  Claude: ", "Label", end="")
    write_c(f"http://127.0.0.1:{GATEWAY_PORT}/v1/messages", "GPU", end="")
    write_c("  (Claude Desktop 客户端)", "OK")

    write_c("  主模型: ", "Label", end="")
    write_c(f"http://127.0.0.1:{LLAMA_PORT}", "Foot", end="")
    write_c("  (8083 主推理引擎)", "Foot")

    write_c("  视觉眼睛: ", "Label", end="")
    write_c(f"http://127.0.0.1:{SIDECAR_PORT}", "Foot", end="")
    write_c("  (8085 CPU 侧挂视觉眼睛 Qwen3VL-4B)", "Foot")

    write_c("  账单自检: ", "Label", end="")
    write_c(f"http://127.0.0.1:{GATEWAY_PORT}/api/gateway/billing", "OK", end="")
    write_c("  (DeepSeek 闲时标准虚拟价格与 Key 用量自检表)", "Foot")

    write_c("  Ctrl+C ", "Error", end="")
    write_c("停止服务  |  ", "Foot", end="")
    write_c("M ", "Warn", end="")
    write_c("查看 MCP 配置  |  ", "Foot", end="")
    write_c("K ", "Title", end="")
    write_c("查看 Key 虚拟账单自检表", "Foot")
    print()

def show_billing_guide():
    clear_host()
    write_c("  ═══════════════════════════════════════════════════════════════════════════", "Title")
    write_c("     API Key 虚拟计费与用量自检报表 (完全依照 DeepSeek 识图模型闲时标准)      ", "Title")
    write_c("  ═══════════════════════════════════════════════════════════════════════════", "Title")
    print()
    write_c("  计费费率对照 (DeepSeek 官方闲时定价):", "OK")
    write_c("    • 缓存未命中输入 : ￥ 1.50 / 百万 Tokens (0.0000015 元/Token)", "Label")
    write_c("    • 缓存命中输入   : ￥ 0.05 / 百万 Tokens (0.00000005 元/Token)", "Label")
    write_c("    • 输出生成 Token : ￥ 4.50 / 百万 Tokens (0.0000045 元/Token)", "Label")
    write_c("    • 官方定价参考   : https://api-docs.deepseek.com/zh-cn/quick_start/pricing", "Foot")
    print()

    # 尝试从网关拉取实时自检数据
    data = None
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{GATEWAY_PORT}/api/gateway/billing")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        write_c(f"  [提示] 网关服务尚未启动或无法连接 (http://127.0.0.1:{GATEWAY_PORT}/api/gateway/billing): {e}", "Warn")

    if data and "summary" in data:
        s = data["summary"]
        write_c("  全局用量与费用统计:", "Title")
        write_c(f"    • 文本累计费用 : ￥ {s.get('text_cost_cny', 0):.4f}  (Tokens: {s.get('text_tokens', 0):,}, 调用: {s.get('text_requests', 0)} 次)", "GPU")
        write_c(f"    • 识图累计费用 : ￥ {s.get('vision_cost_cny', 0):.4f}  (Tokens: {s.get('vision_tokens', 0):,}, 识图: {s.get('vision_requests', 0)} 次)", "Size")
        write_c(f"    • 总虚拟价值   : ￥ {s.get('total_cost_cny', 0):.4f}  (总 Tokens: {s.get('total_tokens', 0):,})", "OK")
        print()

        write_c("  API Key 用量自检明细表:", "Title")
        write_c("  " + "-" * 85, "Line")
        header = f"  {'API Key':<24} {'请求数':<8} {'文本Token':<12} {'文本金额':<12} {'识图Token':<12} {'识图金额':<12} {'总金额(CNY)':<12}"
        write_c(header, "Label")
        write_c("  " + "-" * 85, "Line")

        keys = data.get("keys", [])
        if not keys:
            write_c("  (当前暂无 API Key 调用记录)", "Foot")
        else:
            for k in keys:
                line_str = f"  {k.get('display_key', k.get('key','')):<24} {k.get('total_requests',0):<8} {k.get('text_tokens',0):<12} ￥{k.get('text_cost_cny',0):<10.4f} {k.get('vision_tokens',0):<12} ￥{k.get('vision_cost_cny',0):<10.4f} ￥{k.get('total_cost_cny',0):<10.4f}"
                write_c(line_str, "Desc")
        write_c("  " + "-" * 85, "Line")
        print()
    else:
        write_c("  启动任一模型后，智能网关将在后台自动计量每个 Key 的文本与识图消费。", "Foot")
        print()

    try:
        input("  按 Enter 返回主菜单...")
    except (EOFError, KeyboardInterrupt):
        pass

def show_mcp_guide(hw):
    clear_host()
    write_c("  MCP / 第三方客户端 接入配置指南", "Title")
    print()
    lan_ip = hw.get("LANIP", "192.168.11.131")
    write_c("  连接方式:", "Title")
    write_c(f"  1. 外网: {DOMAIN}:{EXT_PORT}/llama/v1  Key: {LLAMA_KEY}", "GPU")
    print()
    write_c("  外网 MCP: 本机后端已移除 MCP/SSE 挂载，改由前端 harness 托管。", "Warn")
    write_c("    详见 qidongqi/本机MCP配置指南.md（含 Claude Desktop / Qwen Code 配置示例）。", "Key")
    print()

    write_c("  Claude Desktop:", "GPU")
    write_c(r"    edit %APPDATA%\Claude\claude_desktop_config.json", "Label")
    claude_cfg = f'{{"mcpServers":{{"llama-lan":{{"command":"npx","args":["-y","@anthropic/ollama-mcp","--host","{lan_ip}","--port","8081"]}}}}}}'
    write_c(f"    {claude_cfg}", "Desc")
    print()

    write_c("  Cursor / Cline / Open WebUI:", "GPU")
    write_c(f"    Base URL: http://{lan_ip}:8081/v1 (无需 API Key)", "Desc")
    print()

    try:
        input("  按 Enter 返回菜单...")
    except (EOFError, KeyboardInterrupt):
        pass

# ==========================================================
#  辅助监控与性能采集
# ==========================================================
class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint), ("dwHighDateTime", ctypes.c_uint)]

_last_idle = 0
_last_kernel = 0
_last_user = 0

def get_cpu_and_ram():
    global _last_idle, _last_kernel, _last_user
    cs = "N/A"
    try:
        idle = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        if kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            i = (idle.dwHighDateTime << 32) | idle.dwLowDateTime
            k = (kernel.dwHighDateTime << 32) | kernel.dwLowDateTime
            u = (user.dwHighDateTime << 32) | user.dwLowDateTime
            if _last_kernel > 0:
                idle_diff = i - _last_idle
                total_diff = (k - _last_kernel) + (u - _last_user)
                if total_diff > 0:
                    pct = round((1.0 - idle_diff / total_diff) * 100, 1)
                    cs = f"{max(0.0, min(100.0, pct))}%"
            _last_idle, _last_kernel, _last_user = i, k, u
    except Exception:
        pass

    ms = "N/A"
    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        total_gb = round(stat.ullTotalPhys / (1024**3), 2)
        free_gb = round(stat.ullAvailPhys / (1024**3), 2)
        used_gb = round(total_gb - free_gb, 2)
        pct = stat.dwMemoryLoad
        ms = f"{used_gb}GB/{total_gb}GB ({pct}%)"
    except Exception:
        pass

    return cs, ms

def get_gpu_sample():
    try:
        cmd = ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,clocks.sm", "--format=csv,noheader,nounits"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            p = [x.strip() for x in res.stdout.strip().split(",")]
            return {
                "GU": f"{p[0]}%",
                "MU": f"{p[1]}%",
                "MVU": f"{round(int(p[2])/1024, 2)}GB",
                "MVT": f"{round(int(p[3])/1024, 2)}GB",
                "T": f"{p[4]}C",
                "P": f"{p[5]}W",
                "C": f"{p[6]}MHz",
                "GU_num": int(p[0]),
                "P_num": float(p[5]),
                "MVU_num": round(int(p[2])/1024, 2)
            }
    except Exception:
        pass
    return None

def get_server_metrics(port: int) -> str:
    try:
        url = f"http://127.0.0.1:{port}/metrics"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2) as resp:
            content = resp.read().decode("utf-8", errors="ignore")

        gen_tps = 0.0
        prompt_tps = 0.0
        gen_tokens = 0
        prompt_tokens = 0
        active_reqs = 0

        for line in content.splitlines():
            line = line.strip()
            m = re.match(r"^llamacpp:predicted_tokens_seconds\s+([0-9.e+-]+)", line)
            if m:
                gen_tps = float(m.group(1))
            m = re.match(r"^llamacpp:prompt_tokens_seconds\s+([0-9.e+-]+)", line)
            if m:
                prompt_tps = float(m.group(1))
            m = re.match(r"^llamacpp:tokens_predicted_total\s+([0-9.e+-]+)", line)
            if m:
                gen_tokens = int(float(m.group(1)))
            m = re.match(r"^llamacpp:prompt_tokens_total\s+([0-9.e+-]+)", line)
            if m:
                prompt_tokens = int(float(m.group(1)))
            m = re.match(r"^llamacpp:requests_processing\s+([0-9.e+-]+)", line)
            if m:
                active_reqs = int(float(m.group(1)))

        parts = []
        if gen_tokens > 0 and gen_tps > 0:
            parts.append(f"gen:{gen_tps:.1f}t/s")
        if prompt_tokens > 0 and prompt_tps > 0:
            parts.append(f"prompt:{prompt_tps:.1f}t/s")
        if gen_tokens > 0:
            parts.append(f"gen_tok:{gen_tokens}")
        if prompt_tokens > 0:
            parts.append(f"prompt_tok:{prompt_tokens}")
        if active_reqs > 0:
            parts.append(f"reqs:{active_reqs}")

        return " ".join(parts)
    except Exception:
        return ""

def get_model_props(port: int) -> str:
    parts = []
    try:
        url = f"http://127.0.0.1:{port}/props"
        with urllib.request.urlopen(url, timeout=3) as resp:
            j = json.loads(resp.read().decode("utf-8", errors="ignore"))
            if "model_ftype" in j:
                parts.append(f"ftype={j['model_ftype']}")
            if "total_slots" in j:
                parts.append(f"slots={j['total_slots']}")
            if "modalities" in j:
                vis = "vision" if j["modalities"].get("vision") else "text"
                parts.append(f"modality={vis}")
            if "build_info" in j:
                parts.append(f"build={j['build_info']}")
    except Exception:
        pass

    try:
        url = f"http://127.0.0.1:{port}/slots"
        with urllib.request.urlopen(url, timeout=2) as resp:
            slots = json.loads(resp.read().decode("utf-8", errors="ignore"))
            if slots and isinstance(slots, list):
                s0 = slots[0]
                if "n_ctx" in s0:
                    parts.append(f"slot_ctx={s0['n_ctx']}")
                if "speculative" in s0 and s0["speculative"] is not None:
                    parts.append(f"spec={s0['speculative']}")
    except Exception:
        pass

    return " ".join(parts)

def safe_read_file(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

# ==========================================================
#  模型服务启动自检与写入
# ==========================================================
def write_self_check(log_file: str, port: int, server_out: str, server_err: str, intent_str: str):
    sep = "-" * 70
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    sc_lines = [
        "",
        sep,
        f"[{now_str}] === 模型服务启动自检 ===",
        "  [意图参数] (launcher 请求)",
        f"    {intent_str if intent_str else '(无)'}",
        "  [实际加载] (llama.cpp 启动日志解析)"
    ]

    raw = safe_read_file(server_out) + "\n" + safe_read_file(server_err)
    kv = {}

    m = re.search(r"(?m)^build:\s*(\S+)", raw)
    if m:
        kv["build"] = m.group(1)

    m = re.search(r"n_gpu_layers already set by user to (\d+)", raw)
    if m:
        kv["n_gpu_layers(offload)"] = f"{m.group(1)} (用户指定)"
    else:
        m2 = re.search(r"(?m)n_gpu_layers\s*[:=]\s*(\d+)", raw)
        if m2:
            kv["n_gpu_layers(offload)"] = m2.group(1)

    m = re.search(r"n_ctx_slot\s*=\s*(\d+)", raw)
    if m:
        kv["n_ctx(实际)"] = f"{m.group(1)} (slot)"
    else:
        m2 = re.search(r"(?m)n_ctx\s*=\s*(\d+)", raw)
        if m2:
            kv["n_ctx(实际)"] = m2.group(1)

    m = re.search(r"(?i)KV cache size\s*=?\s*([\d.]+)\s*MiB", raw)
    if m:
        kv["KV cache"] = f"{m.group(1)} MiB"
    else:
        m2 = re.search(r"(?i)kv_unified\s*=\s*['\"]?(\w+)", raw)
        if m2:
            kv["kv_unified"] = m2.group(1)

    if re.search(r"(?i)flash", raw):
        kv["flash_attn"] = "detected (见意图参数 / 日志)"

    if re.search(r"(?i)KV cache shifting is not supported|disabling KV cache shifting", raw):
        kv["context-shift"] = "[警告] 请求但被禁用! (this context 不支持 KV cache shifting)"
    elif re.search(r"(?i)context shift enabled|KV cache shifting enabled", raw):
        kv["context-shift"] = "enabled"

    if re.search(r"(?i)cache_reuse is not supported|cache-reuse.*disabled|will be disabled", raw):
        kv["cache-reuse"] = "[警告] 被禁用 (multimodal 不支持 cache_reuse)"

    m = re.search(r"(?i)total parameters\s*=\s*([\d.]+)", raw)
    if m:
        kv["total_params"] = m.group(1)

    if kv:
        for k, v in kv.items():
            sc_lines.append(f"    {k}: {v}")
    else:
        sc_lines.append("    (server 日志未捕获关键行，见下方原始文件)")

    # /props 字段
    sc_lines.append("  [实际 /props]")
    props_ok = False
    for _ in range(3):
        try:
            url = f"http://127.0.0.1:{port}/props"
            with urllib.request.urlopen(url, timeout=3) as resp:
                j = json.loads(resp.read().decode("utf-8", errors="ignore"))
            nctx = j.get("n_ctx") or (j.get("default_generation_settings") or {}).get("n_ctx")
            if nctx:
                dgs = j.get("default_generation_settings") or {}
                params = dgs.get("params") or {}
                rf = j.get("reasoning_format") or dgs.get("reasoning_format") or params.get("reasoning_format") or "?"
                mod = "vision" if (j.get("modalities") or {}).get("vision") else "text"
                binfo = j.get("build_info", "")
                p_line = f"    n_ctx={nctx} ftype={j.get('model_ftype', '')} modality={mod} build={binfo} reasoning_format={rf}"
                if dgs.get("reasoning_effort"):
                    p_line += f" effort={dgs['reasoning_effort']}"
                sc_lines.append(p_line)

                ct = j.get("chat_template", "")
                m_tpl = re.search(r'template_version\s*=\s*"([^"]+)"', ct)
                if m_tpl:
                    sc_lines.append(f"    chat_template: {m_tpl.group(1)}")
                props_ok = True
                break
        except Exception:
            pass
        time.sleep(1)

    if not props_ok:
        sc_lines.append("    (props 获取失败或 n_ctx 为空)")

    # 关键原文摘录
    sc_lines.append("  [权威启动日志原文摘录]")
    if raw:
        match_keywords = re.compile(r"(?i)(build|context|kv cache|kv_size|kv_unified|n_gpu_layers|flash|slot|load:|warn|error|ggml|llama_context|llama_kv|device|offload|total parameters|reasoning|template|cache_reuse|shifting)")
        matched_lines = [l.strip() for l in raw.splitlines() if match_keywords.search(l)]
        keep = matched_lines[-40:]
        if keep:
            for l in keep:
                sc_lines.append(f"    {l}")
        else:
            sc_lines.append("    (无匹配行)")
    else:
        sc_lines.append("    (无)")

    # 原始文件
    sc_lines.append("  [原始启动日志文件]")
    if server_err and os.path.isfile(server_err):
        sc_lines.append(f"    stderr(完整原始日志): {server_err}")
    if server_out and os.path.isfile(server_out):
        sc_lines.append(f"    stdout : {server_out}")
    sc_lines.append(sep)

    with open(log_file, "a", encoding="utf-8") as f:
        f.write("\n".join(sc_lines) + "\n")

# ==========================================================
#  后台监控线程 (加载采样 + 运行监控 + 峰值统计)
# ==========================================================
def run_logger_thread(stop_event, log_file, port, sample_sec, timeout_sec, server_out, server_err, intent_str):
    loaded = False
    elapsed = 0
    snaps = []

    # Phase 1: 等待加载
    while not stop_event.is_set() and not loaded and elapsed < timeout_sec:
        time.sleep(sample_sec)
        elapsed += sample_sec

        try:
            url = f"http://127.0.0.1:{port}/health"
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    loaded = True
        except Exception:
            pass

        cpu_str, ram_str = get_cpu_and_ram()
        g = get_gpu_sample()
        sm = get_server_metrics(port)

        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        s = f"[{now_str}] loading +{elapsed}s | CPU:{cpu_str} RAM:{ram_str}"
        if g:
            s += f" | GPU:{g['GU']} VRAM:{g['MVU']}/{g['MVT']} T:{g['T']} P:{g['P']}"
        if sm:
            s += f" | {sm}"
        snaps.append(s)

    if stop_event.is_set() and not loaded:
        with open(log_file, "a", encoding="utf-8") as f:
            for s in snaps:
                f.write(s + "\n")
            f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] !! STOPPED during load ({elapsed}s)\n")
        return

    if not loaded:
        with open(log_file, "a", encoding="utf-8") as f:
            for s in snaps:
                f.write(s + "\n")
            f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] !! TIMEOUT ({timeout_sec}s)\n")
        return

    # 写入 loading 采样与 LOADED
    with open(log_file, "a", encoding="utf-8") as f:
        for s in snaps:
            f.write(s + "\n")
        f.write(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] === LOADED in {elapsed}s ===\n")
        props_str = get_model_props(port)
        if props_str:
            f.write(f"  model_info : {props_str}\n")

    # 执行启动自检
    try:
        write_self_check(log_file, port, server_out, server_err, intent_str)
    except Exception:
        pass

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] === MONITORING ===\n")

    # Phase 2: 持续性能采样与峰值追踪
    n = 0
    peak_eval_tps = 0.0
    peak_prompt_tps = 0.0
    peak_gpu_util = 0
    peak_power = 0.0
    peak_vram = 0.0

    while not stop_event.is_set():
        time.sleep(sample_sec)
        n += 1

        cpu_str, ram_str = get_cpu_and_ram()
        g = get_gpu_sample()
        sm = get_server_metrics(port)

        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        ln = f"[{now_str}] #{n:03d} | CPU:{cpu_str} RAM:{ram_str}"
        if g:
            ln += f" | GPU:{g['GU']} VRAM:{g['MVU']}/{g['MVT']} T:{g['T']} P:{g['P']} SM:{g['C']}"
            if g["GU_num"] > peak_gpu_util:
                peak_gpu_util = g["GU_num"]
            if g["P_num"] > peak_power:
                peak_power = g["P_num"]
            if g["MVU_num"] > peak_vram:
                peak_vram = g["MVU_num"]

        if sm:
            ln += f" | {sm}"
            m_gen = re.search(r"gen:([0-9.]+)t/s", sm)
            if m_gen:
                val = float(m_gen.group(1))
                if val > peak_eval_tps:
                    peak_eval_tps = val
            m_pr = re.search(r"prompt:([0-9.]+)t/s", sm)
            if m_pr:
                val = float(m_pr.group(1))
                if val > peak_prompt_tps:
                    peak_prompt_tps = val

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(ln + "\n")

    # 退出写入
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n[{now_str}] === STOPPED (samples:{n} peak_eval:{peak_eval_tps:.1f}t/s peak_prompt:{peak_prompt_tps:.1f}t/s peak_gpu:{peak_gpu_util}% peak_power:{peak_power:.0f}W peak_vram:{peak_vram:.1f}GB) ===\n")

# ==========================================================
#  杀死现有 llama-server 进程
# ==========================================================
def kill_old_llama_server():
    try:
        ai_gateway.stop_gateway()
        res = subprocess.run(["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True)
        if "llama-server.exe" in res.stdout:
            write_c("  发现已有 llama-server 进程，正在关闭...", "Warn")
            subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)
            time.sleep(2)
            # 等待核心端口 8083 释放
            for _ in range(10):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                res = s.connect_ex(("127.0.0.1", LLAMA_PORT))
                s.close()
                if res != 0:
                    write_c(f"  核心端口 {LLAMA_PORT} 已释放", "OK")
                    break
                time.sleep(1)
            else:
                write_c(f"  核心端口 {LLAMA_PORT} 仍未释放，建议稍后重试", "Error")
            print()
    except Exception:
        pass

# ==========================================================
#  模型启动主逻辑 (Invoke-Launch)
# ==========================================================
def invoke_launch(model: dict):
    print()
    write_c(f"  ── 启动模型：{model['name']} ──", "Title")

    if not os.path.isfile(model["path"]):
        write_c("  [错误] 模型文件不存在！", "Error")
        try:
            input("  按 Enter 返回...")
        except Exception:
            pass
        return

    # 1. 自动 kill 已有的 llama-server 进程
    kill_old_llama_server()

    # 2. 查询可用显存
    free_vram_mb = 0
    total_vram_mb = 0
    try:
        smi = subprocess.run(["nvidia-smi", "--query-gpu=memory.free,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True)
        if smi.returncode == 0 and smi.stdout.strip():
            parts = smi.stdout.strip().splitlines()[0].split(",")
            free_vram_mb = int(parts[0].strip())
            total_vram_mb = int(parts[1].strip())
    except Exception:
        pass

    # 3. 参数匹配 (严格对齐 v94 规则)
    ngl = 999
    ctx = 65536
    batch = 2048
    ubatch = 512
    threads = -1
    threads_b = -1
    flash_attn = "auto"
    ctk = "f16"
    ctv = "f16"
    spec_type = ""
    extra_server = ""
    preset_name = "default"
    m_name = model["name"]

    # 聊天模板：完全使用 Qwen-Fixed-Chat-Templates (v22.5 官方规范模板)
    # dry-multiplier 策略：由 8081 智能网关按任务自适应调度，仅代码任务动态开启 dry=0 保护路径与反斜杠，日常闲聊开启 DRY 采样防死循环
    fixed_jinja_path = os.path.join(LLAMA_DIR, r"qidongqi\qwen_fixed_chat_template.jinja")

    # ========== Ornith-1.5-9B (Q8_0 9.07GB) 官方模型 256K上下文 ==========
    if re.search(r"Ornith-1\.5.*Q8_0", m_name, re.I):
        flash_attn = "on"; ctk = "q8_0"; ctv = "q8_0"
        ctx = 262144; batch = 2048; ubatch = 512
        extra_server = f"--reasoning-effort medium --reasoning-format deepseek --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0 --presence-penalty 1.5 --repeat-penalty 1.0 --image-min-tokens 1024 --no-mmproj-offload --jinja --chat-template-file {fixed_jinja_path}"
        preset_name = "ornith-1.5-9b-q8-256k-vision"

    # ========== Ternary-Bonsai-27B (Q2_g64 7.1GB) 多模态 96K上下文 ==========
    elif re.search(r"Ternary-Bonsai.*Q2_g64", m_name, re.I):
        flash_attn = "on"; ctk = "q4_0"; ctv = "q4_0"
        ctx = 98304; batch = 2048; ubatch = 512
        extra_server = f"--reasoning-effort medium --repeat-penalty 1.2 --image-min-tokens 1024 --jinja --chat-template-file {fixed_jinja_path} --reasoning-format deepseek"
        preset_name = "bonsai-27b-vision"

    # ========== Qwen3.6-35B-A3B (IQ3_M 15GB) 16K上下文 ==========
    elif re.search(r"35B.*A3B", m_name, re.I):
        ctk = "q4_0"; ctv = "q4_0"; flash_attn = "on"
        ctx = 16384
        extra_server = f"--min-p 0 --presence-penalty 1.5 --jinja --chat-template-file {fixed_jinja_path} --reasoning-format deepseek"
        preset_name = "35b-a3b-16k"

    # ========== Qwen3VL-8B (8.2GB) 视觉 96K上下文 ==========
    elif re.search(r"Qwen3VL|Vision|vision", m_name, re.I):
        flash_attn = "on"; ctk = "q8_0"; ctv = "q8_0"
        ctx = 98304
        extra_server = "--min-p 0 --presence-penalty 1.5"
        preset_name = "vision"

    # ========== Qwen3.5-4B (Q8_0 4.2GB) 默认最大256K上下文 ==========
    elif re.search(r"Qwen3\.5-4B", m_name, re.I):
        flash_attn = "on"; ctk = "q4_0"; ctv = "q4_0"
        ctx = 262144
        extra_server = f"--min-p 0 --presence-penalty 1.5 --jinja --chat-template-file {fixed_jinja_path} --reasoning-format deepseek"
        preset_name = "4b-max-256k"

    # ========== Qwen3.8-27B-GSQ-MTP (IQ3_S 11.3GB, 单并发, MTP=2, 64K, 视觉图元约束, 输出不设限) ==========
    elif re.search(r"Qwen3\.8-27B-GSQ.*mtp", m_name, re.I):
        flash_attn = "on"; ctk = "q4_0"; ctv = "q4_0"
        ctx = 65536; batch = 2048; ubatch = 512
        spec_type = "draft-mtp"
        extra_server = f"--parallel 1 --n-predict -1 -ctxcp 4 --spec-draft-n-max 2 --reasoning-budget 2048 --reasoning-effort medium --repeat-penalty 1.0 --image-min-tokens 1024 --image-max-tokens 4096 --no-mmproj-offload --reasoning-preserve --jinja --chat-template-file {fixed_jinja_path} --reasoning-format deepseek"
        preset_name = "qwen3.8-27b-gsq-mtp2-64k"

    # ========== Qwen3.8-27B-GSQ (IQ3_S 11.0GB, 4并发, 共享KV池 64K, 视觉图元约束, 输出不设限) ==========
    # ⚠️【避坑警示】：切勿外挂 Qwen3.8-27B-DFlash2 草稿模型！DFlash2 算子在 Windows 不支持 GPU 导致强制回退 CPU，
    # 每步产生 300ms 延迟，实测速度由 53.8 tok/s 暴跌至 2.76 tok/s。若要投机，请选用自带原生 MTP 头的 Qwen3.8-27B-GSQ-MTP！
    elif re.search(r"Qwen3\.8-27B-GSQ", m_name, re.I):
        flash_attn = "on"; ctk = "q4_0"; ctv = "q4_0"
        ctx = 65536; batch = 2048; ubatch = 512
        spec_type = "ngram-mod"
        extra_server = f"--parallel 4 --n-predict -1 --kv-unified --cache-ram 16384 --reasoning-budget 2048 --reasoning-effort medium --repeat-penalty 1.0 --image-min-tokens 1024 --image-max-tokens 4096 --no-mmproj-offload --reasoning-preserve --jinja --chat-template-file {fixed_jinja_path} --reasoning-format deepseek"
        preset_name = "qwen3.8-27b-gsq-4slots-kvu-64k"

    # ========== Qwen3.8-27B-UD-IQ3_S (96K 上下文) ==========
    elif re.search(r"Qwen3\.8-27B-UD.*IQ3_S", m_name, re.I):
        flash_attn = "on"; ctk = "q4_0"; ctv = "q4_0"
        ctx = 98304; batch = 2048; ubatch = 512
        extra_server = f"--reasoning-budget 2048 --reasoning-effort medium --repeat-penalty 1.0 --image-min-tokens 1024 --no-mmproj-offload --jinja --chat-template-file {fixed_jinja_path} --reasoning-format deepseek"
        preset_name = "qwen3.8-27b-vision-96k"

    # ========== Qwen3.8-27B-NVFP4-STARVED (32K 上下文 无MTP) ==========
    elif re.search(r"NVFP4-STARVED", m_name, re.I):
        flash_attn = "on"; ctk = "q4_0"; ctv = "q4_0"
        ctx = 32768; batch = 1024; ubatch = 128
        spec_type = "none"
        extra_server = f"--parallel 1 -ctxcp 4 --reasoning-budget 2048 --reasoning-effort medium --repeat-penalty 1.0 --image-min-tokens 1024 --no-mmproj-offload --jinja --chat-template-file {fixed_jinja_path} --reasoning-format deepseek"
        preset_name = "qwen3.8-27b-nvfp4-starved-32k"

    # ========== Qwen3.8-27B-NVFP4-MTP-VERY-LOW (16K 上下文) ==========
    elif re.search(r"NVFP4", m_name, re.I):
        flash_attn = "on"; ctk = "q4_0"; ctv = "q4_0"
        ctx = 16384; batch = 1024; ubatch = 128
        spec_type = "none"
        extra_server = f"--parallel 1 -ctxcp 4 --reasoning-budget 2048 --reasoning-effort medium --repeat-penalty 1.0 --image-min-tokens 1024 --no-mmproj-offload --jinja --chat-template-file {fixed_jinja_path} --reasoning-format deepseek"
        preset_name = "qwen3.8-27b-nvfp4-16k"

    # ========== Qwen3.8-27B-Ridge-3.7bpw (80K q4 KV MTP3, 思考预算2048, 输出不设限) ==========
    elif re.search(r"Qwen3\.8-27B-Ridge", m_name, re.I):
        flash_attn = "on"; ctk = "q4_0"; ctv = "q4_0"
        ctx = 81920; batch = 2048; ubatch = 256
        extra_server = f"--parallel 1 --n-predict -1 -ctxcp 4 --reasoning-budget 2048 --reasoning-effort medium --repeat-penalty 1.0 --spec-draft-n-max 3 --image-min-tokens 1024 --no-mmproj-offload --jinja --chat-template-file {fixed_jinja_path} --reasoning-format deepseek"
        preset_name = "qwen3.8-27b-ridge-96k"

    # ========== Qwen3.5-9B (64K 上下文) ==========
    elif re.search(r"Qwen3\.5-9B", m_name, re.I):
        flash_attn = "on"; ctk = "q4_0"; ctv = "q4_0"
        ctx = 65536
        extra_server = f"--min-p 0 --presence-penalty 1.5 --jinja --chat-template-file {fixed_jinja_path} --reasoning-format deepseek"
        preset_name = "qwen3.5-9b-64k"

    # ========== 通用兜底 ==========
    elif model["size_gb"] >= 8:
        flash_attn = "on"; ctk = "q4_0"; ctv = "q4_0"
        preset_name = "8b-plus"
    elif model["size_gb"] >= 4:
        flash_attn = "on"; ctk = "q4_0"; ctv = "q4_0"
        preset_name = "4b-8b"
    else:
        preset_name = "small"

    # 投机解码策略
    if not spec_type:
        if not re.search(r"q4km|iq4xs", preset_name):
            spec_type = "ngram-mod"

    # 查找匹配的 mmproj
    mmproj_path = find_matching_mmproj(model["name"])

    # VRAM 预估与警告
    if total_vram_mb > 0:
        est_vram = model["size_gb"] * 1024 + (ctx / 1024) * model["size_gb"] * 0.4
        if free_vram_mb < est_vram * 0.8:
            write_c("  ┌─────────────────────────────────────────────┐", "Error")
            write_c("  │  VRAM 警告                                  │", "Error")
            write_c(f"  │  GPU: {total_vram_mb}MB / {free_vram_mb}MB free                │", "Error")
            write_c(f"  │  模型: {model['size_gb']}GB | ctx: {ctx} | preset: {preset_name}    │", "Error")
            write_c(f"  │  预计 ~{round(est_vram/1024, 1)}GB VRAM                │", "Error")
            write_c("  └─────────────────────────────────────────────┘", "Error")
            print()
            write_c("  按 Enter 继续，输入 q 取消...", "Warn")
            try:
                ans = input().strip()
                if ans.lower() == "q":
                    return
            except Exception:
                return

    # 生成短别名
    short_alias = get_short_alias(os.path.basename(model["path"]))
    if re.search(r"Qwen3\.8-27B-GSQ.*mtp", model["name"], re.I):
        short_alias = "Qwen3.8-27B-GSQ-MTP"
    elif re.search(r"Qwen3\.8-27B-GSQ", model["name"], re.I):
        short_alias = "Qwen3.8-27B-GSQ"
    elif re.search(r"Qwen3\.8-27B-UD", model["name"], re.I):
        short_alias = "Qwen3.8-27B"

    # 构建完整参数列表
    cmd_args = [
        LLAMA_SERVER,
        "-m", model["path"],
        "--alias", short_alias,
        "-ngl", str(ngl),
        "-c", str(ctx),
        "-b", str(batch),
        "-ub", str(ubatch),
        "-t", str(threads),
        "-tb", str(threads_b),
        "--flash-attn", flash_attn,
        "-ctk", ctk,
        "-ctv", ctv,
        "--host", "0.0.0.0",
        "--port", str(LLAMA_PORT),
        "--metrics",
        "--context-shift",
        "--keep", "1024",
        "--cache-reuse", "256"
    ]
    if mmproj_path:
        cmd_args.extend(["--mmproj", mmproj_path])
    if spec_type:
        cmd_args.extend(["--spec-type", spec_type])
    if extra_server:
        cmd_args.extend(extra_server.split())

    # 启动参数总览
    print()
    write_c("  ┌────────────────────────────────────────────────────────────┐", "Title")
    write_c(f"  │           启动参数总览   preset: {preset_name}", "Title")
    write_c("  └────────────────────────────────────────────────────────────┘", "Title")
    write_c("  模型路径    : ", "Label", end=""); write_c(model["path"], "Model")
    write_c("  别名        : ", "Label", end=""); write_c(short_alias, "Model")
    param_scale = f"{model['param_num']:g}B" if model["param_num"] > 0 else "?"
    write_c("  参数规模    : ", "Label", end=""); write_c(param_scale, "Size", end=""); write_c(f"   (磁盘 {model['size_str']})", "Size")
    write_c("  上下文      : ", "Label", end=""); write_c(f"{ctx} tokens", "Size")
    write_c("  显存offload : ", "Label", end=""); write_c(f"ngl={ngl} (全层 GPU)", "GPU")
    write_c("  batch/ubatch: ", "Label", end=""); write_c(f"{batch} / {ubatch}", "Size")
    write_c("  threads     : ", "Label", end=""); write_c(f"{threads} (auto)", "Size")
    write_c("  flash-attn  : ", "Label", end=""); write_c(flash_attn, "GPU")
    write_c("  KV cache    : ", "Label", end=""); write_c(f"{ctk} / {ctv}", "Size")
    if mmproj_path:
        write_c("  mmproj      : ", "Label", end=""); write_c(mmproj_path, "Warn")
    if spec_type:
        write_c("  spec-type   : ", "Label", end=""); write_c(spec_type, "GPU")
    write_c("  服务地址    : ", "Label", end=""); write_c(f"http://127.0.0.1:{LLAMA_PORT}", "OK")
    write_c("  metrics     : ", "Label", end=""); write_c(f"启用 (http://127.0.0.1:{LLAMA_PORT}/metrics)", "OK")
    write_c("  tools/MCP   : ", "Label", end=""); write_c("后端不挂载（前端 harness 托管，见 本机MCP配置指南.md）", "Warn")
    write_c("  滑动/保留   : ", "Label", end=""); write_c("context-shift on / keep 1024 / cache-reuse 256", "Size")
    print()
    write_c("  推理参数 extraServer:", "Title")
    write_c(f"    {extra_server}", "Desc")
    print()

    # 日志文件初始化
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    today_log_file = os.path.join(LOG_DIR, f"run-{today_str}.log")
    run_ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    server_out_path = os.path.join(LOG_DIR, f"server-{run_ts}.txt")
    server_err_path = os.path.join(LOG_DIR, f"server-{run_ts}.err")

    param_str = f"ngl={ngl} ctx={ctx} batch={batch} ubatch={ubatch} flash={flash_attn} kv={ctk}/{ctv} threads={threads} spec={spec_type} preset={preset_name}"
    if mmproj_path:
        param_str += " mmproj=yes"

    intent_str = f"model={short_alias}; path={model['path']}; ctx={ctx}; ngl={ngl}; batch={batch}; ubatch={ubatch}; flash={flash_attn}; kv={ctk}/{ctv}; spec={spec_type if spec_type else 'none'}; mmproj={'yes' if mmproj_path else 'no'}; context-shift=on; keep=1024; cache-reuse=256; reasoning=medium/deepseek; template=qwen_fixed_chat_template.jinja"

    # 写入 run-*.log 头部
    sep70 = "=" * 70
    with open(today_log_file, "a", encoding="utf-8") as f:
        f.write(f"\n{sep70}\n")
        f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MODEL STARTUP\n")
        f.write(f"  model   : {short_alias} ({model['size_str']})\n")
        f.write(f"  path    : {model['path']}\n")
        f.write(f"  port    : {LLAMA_PORT}\n")
        f.write(f"  params  : {param_str}\n")
        f.write(f"{sep70}\n")

    write_c("  日志: ", "Label", end="")
    write_c(today_log_file, "OK", end="")
    write_c("  (后台采样中)  启动日志: ", "Foot", end="")
    write_c(server_err_path, "Foot")

    print()
    write_c("  提示: ", "Warn", end="")
    write_c("如需停止服务，请在此窗口按 ", "Desc", end="")
    write_c("Ctrl+C", "Error", end="")
    write_c(" 确认退出", "Desc")
    print()

    # 判断主模型是否具备原生视觉能力
    has_native_vision = bool(mmproj_path) or bool(re.search(r"Qwen3VL|Vision|vision|Ornith|Bonsai", model["name"], re.I))

    # 启动 8085 CPU 侧挂视觉眼睛 (如果主模型没有视觉能力，自动拉起 8085 提供图文解析支持)
    if not has_native_vision:
        write_c("  [识图路由] 主模型为纯文本模型 -> 正在启动 8085 CPU 视觉眼睛辅助引擎...", "Warn")
        start_vision_sidecar()
    else:
        write_c("  [识图路由] 主模型自带原生视觉能力 -> 优先使用主模型原生识图", "OK")

    # 启动 llama-server 进程 (监听 8083 后端核心端口)
    global _active_proc
    f_out = open(server_out_path, "wb")
    f_err = open(server_err_path, "wb")
    proc = subprocess.Popen(cmd_args, stdout=f_out, stderr=f_err, creationflags=0)
    _active_proc = proc

    # 启动 8081 智能反向代理网关 (对外统一接口，具备总百分比防爆剪枝、双模视觉路由、多模态旁路MTP与模型映射)
    ai_gateway.start_gateway(
        host="0.0.0.0",
        port=GATEWAY_PORT,
        backend_port=LLAMA_PORT,
        sidecar_port=SIDECAR_PORT,
        main_has_vision=has_native_vision,
        model_alias=short_alias,
        max_context=ctx,
        preset=preset_name,
        quant=model.get("quant", "")
    )

    # 注册/更新右下角任务栏状态托盘图标
    ai_tray.start_tray(model_name=f"{short_alias} (运行中)", on_exit=cleanup_everything_and_exit)
    ai_tray.update_tray_status(f"{short_alias} (运行中)")

    # 启动后台性能监控线程
    stop_logger = threading.Event()
    t_logger = threading.Thread(
        target=run_logger_thread,
        args=(stop_logger, today_log_file, LLAMA_PORT, LOG_SAMPLE_S, LOG_TIMEOUT, server_out_path, server_err_path, intent_str),
        daemon=True
    )
    t_logger.start()

    # 实时 tail 回显启动日志并检查健康状态
    out_pos = 0
    err_pos = 0
    browser_opened = False
    backend_health_url = f"http://127.0.0.1:{LLAMA_PORT}"
    gateway_ui_url = f"http://127.0.0.1:{GATEWAY_PORT}"

    def tail_file(path, pos):
        if not os.path.isfile(path):
            return pos
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(pos)
                chunk = f.read()
                if chunk:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    return f.tell()
        except Exception:
            pass
        return pos

    try:
        while proc.poll() is None:
            time.sleep(1)
            f_out.flush()
            f_err.flush()
            out_pos = tail_file(server_out_path, out_pos)
            err_pos = tail_file(server_err_path, err_pos)

            if not browser_opened:
                try:
                    req = urllib.request.Request(f"{backend_health_url}/health")
                    with urllib.request.urlopen(req, timeout=2) as resp:
                        if resp.status == 200:
                            browser_opened = True
                            write_c("  [OK] 模型加载完成！服务已正式就绪：", "OK")
                            write_c(f"    💬 原生对话 WebUI : http://127.0.0.1:{GATEWAY_PORT}/", "OK")
                            write_c(f"    🌐 智能监控看板   : http://127.0.0.1:{GATEWAY_PORT}/dashboard", "GPU")
                            print()
                except Exception:
                    pass

    except KeyboardInterrupt:
        print()
        write_c("  正在停止服务并关闭 llama-server...", "Warn")
        cleanup_everything()
        write_c("  llama-server 与智能网关已终止", "OK")

    finally:
        f_out.flush()
        f_err.flush()
        out_pos = tail_file(server_out_path, out_pos)
        err_pos = tail_file(server_err_path, err_pos)
        f_out.close()
        f_err.close()

        stop_logger.set()
        t_logger.join(timeout=3)
        cleanup_everything()
        try:
            ai_tray.start_tray(model_name="待机中 (请选择模型)", on_exit=cleanup_everything_and_exit)
        except Exception:
            pass

    print()
    try:
        input("  按 Enter 返回菜单...")
    except Exception:
        pass

# ==========================================================
#  主循环入口
# ==========================================================
def main():
    hw = get_hardware_info()
    llama_ver = get_llama_version()
    models = get_models()

    if not models:
        write_c(r"  错误: 在 H:\models 中未找到 GGUF 模型文件。", "Error")
        try:
            input()
        except Exception:
            pass
        return

    # 打开启动器即在任务栏右下角常驻状态托盘 (待机模式)
    try:
        ai_tray.start_tray(model_name="待机中 (请选择模型)", on_exit=cleanup_everything_and_exit)
    except Exception:
        pass

    while True:
        try:
            show_menu(models, hw, llama_ver)
        except KeyboardInterrupt:
            print()
            try:
                ans = input("  确认退出？(Y/N): ").strip().upper()
                if ans == "Y":
                    break
            except Exception:
                break
            continue

        try:
            write_c(f"  请选择 [1-{len(models)} / M / 0]: ", "Input", end="")
            choice = input().strip()
        except (KeyboardInterrupt, EOFError):
            print()
            try:
                ans = input("  确认退出？(Y/N): ").strip().upper()
                if ans == "Y":
                    break
            except Exception:
                break
            continue

        if choice == "0":
            break
        if choice.upper() == "M":
            show_mcp_guide(hw)
            continue
        if choice.upper() == "K":
            show_billing_guide()
            continue

        if choice.isdigit():
            num = int(choice)
            if 1 <= num <= len(models):
                invoke_launch(models[num - 1])
                continue

        write_c("  输入错误，请重新输入！", "Error")
        time.sleep(1.5)

    cleanup_everything()

if __name__ == "__main__":
    main()
