# -*- coding: utf-8 -*-
"""
========================================================================================
  🚀 AI 大模型统一启动器 (Python 原生高能版) · Model Launcher v4.0
  • 彻底告别 PowerShell 脚本与编码兼容陷阱，纯 Python 原生多进程治理
  • 8081 智能协同网关常驻 · 8083 主脑全能底座 · 8085 视觉侧挂眼睛 (CPU 0显存)
  • Windows 内核级 Job Object 绑定，同生共死，100% 杜绝孤儿进程与显存残留
  • 严格遵循单日单一日志规范 (8083_llama_YYYYMMDD.log / 8085_sidecar_YYYYMMDD.log)
========================================================================================
"""

import os
import sys
import time
import socket
import subprocess
import signal
import json
import psutil
import atexit
import ctypes
import unicodedata
import re
from ctypes import wintypes

# 强制 UTF-8 标准输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 智能解析 llama.cpp 核心运行与模型目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def resolve_llama_server_dir():
    if os.path.exists(os.path.join(SCRIPT_DIR, "llama-server.exe")):
        return SCRIPT_DIR
    env_dir = os.environ.get("LLAMA_SERVER_DIR")
    if env_dir and os.path.exists(os.path.join(env_dir, "llama-server.exe")):
        return env_dir
    default_dir = r"E:\llama-win-cuda-12.4-x64"
    if os.path.exists(os.path.join(default_dir, "llama-server.exe")):
        return default_dir
    return SCRIPT_DIR

BASE_DIR = resolve_llama_server_dir()
MODELS_DIR = r"E:\models" if os.path.exists(r"E:\models") else os.path.join(BASE_DIR, "models")
PYTHON_EXE = sys.executable
LLAMA_SERVER = os.path.join(BASE_DIR, "llama-server.exe")
TEMPLATE_FILE = os.path.join(SCRIPT_DIR, "chat_template_qwen_fixed.jinja")
if not os.path.exists(TEMPLATE_FILE):
    TEMPLATE_FILE = os.path.join(BASE_DIR, "chat_template_qwen_fixed.jinja")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# ANSI 终端色彩
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_CYAN = "\033[96m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_PURPLE = "\033[95m"
C_BLUE = "\033[94m"
C_RED = "\033[91m"
C_GRAY = "\033[90m"
C_WHITE = "\033[97m"

# 全局进程句柄与生命周期互斥锁
g_gateway_proc = None
g_sidecar_proc = None
g_main_proc = None
g_is_cleaning = False
g_tray_manager = None


class MiracleTrayManager:
    """Windows 任务栏右下角通知区域状态托盘与右键快捷控制中心"""
    def __init__(self, on_exit_callback=None):
        self.on_exit_callback = on_exit_callback
        self.model_name = "待命选择中"
        self.status_text = "等待选择模型"
        self.is_running = False
        self.icon = None
        self.thread = None
        self.console_visible = True
        self.active_log_file = None

    def _create_icon_image(self, active=False):
        try:
            from PIL import Image, ImageDraw
            size = 64
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            # 背景色：深色科技底座，运行态为科技黑绿，待机态为沉稳黑灰
            bg = (15, 23, 42, 245) if active else (30, 41, 59, 230)
            border = (0, 229, 153, 255) if active else (148, 163, 184, 255)
            draw.rounded_rectangle([(4, 4), (size - 5, size - 5)], radius=16, fill=bg, outline=border, width=3)
            cx, cy = size // 2, size // 2
            if active:
                # 绿色高亮能量核心 + 科技菱形
                draw.ellipse([(cx - 7, cy - 7), (cx + 7, cy + 7)], fill=(0, 229, 153, 255))
                draw.polygon([(cx, cy - 18), (cx + 14, cy), (cx, cy + 18), (cx - 14, cy)], outline=(56, 189, 248, 255), width=2)
            else:
                # 待机银灰菱形
                draw.polygon([(cx, cy - 14), (cx + 12, cy), (cx, cy + 14), (cx - 12, cy)], fill=(148, 163, 184, 255))
            return img
        except Exception:
            return None

    def _build_menu(self):
        import pystray
        from pystray import MenuItem, Menu
        import webbrowser

        def action_open_dashboard(icon, item):
            webbrowser.open("http://127.0.0.1:8081/dashboard")

        def action_open_web(icon, item):
            webbrowser.open("http://127.0.0.1:8081")

        def action_open_main_log(icon, item):
            today = time.strftime("%Y%m%d")
            log_path = self.active_log_file or os.path.join(LOGS_DIR, f"8083_llama_{today}.log")
            if os.path.exists(log_path):
                os.startfile(log_path)

        def action_open_proxy_log(icon, item):
            today = time.strftime("%Y%m%d")
            log_path = os.path.join(LOGS_DIR, f"8081_proxy_{today}.log")
            if os.path.exists(log_path):
                os.startfile(log_path)

        def action_open_models_dir(icon, item):
            if os.path.exists(MODELS_DIR):
                os.startfile(MODELS_DIR)

        def action_toggle_console(icon, item):
            hwnd = ctypes.windll.kernel32.GetConsoleWindow() if hasattr(ctypes.windll, "kernel32") else 0
            if hwnd:
                try:
                    import win32gui, win32con
                    if win32gui.IsWindowVisible(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                        self.console_visible = False
                    else:
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                        win32gui.SetForegroundWindow(hwnd)
                        self.console_visible = True
                    self.update_menu()
                except Exception:
                    pass

        def action_exit(icon, item):
            cleanup_all()
            self.stop()
            os._exit(0)

        status_display = f"🤖 状态: {self.status_text}"
        model_display = f"📌 模型: {self.model_name}"
        toggle_text = "🪟 隐藏控制台窗口 (静默后台)" if self.console_visible else "🪟 显示控制台窗口 (呼出黑框)"

        return Menu(
            MenuItem(status_display, None, enabled=False),
            MenuItem(model_display, None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("🌐 打开智能网关控制台 (8081)", action_open_dashboard, default=True),
            MenuItem("💬 打开 Web 对话体验界面", action_open_web),
            Menu.SEPARATOR,
            MenuItem("📜 查看主脑模型实时日志 (8083)", action_open_main_log),
            MenuItem("👁️ 查看网关协同流水日志 (8081)", action_open_proxy_log),
            MenuItem("📁 打开模型权重存放目录", action_open_models_dir),
            MenuItem(toggle_text, action_toggle_console),
            Menu.SEPARATOR,
            MenuItem("⏹️ 完全安全退出 (终结服务释放资源)", action_exit)
        )

    def start(self):
        try:
            import pystray
            img = self._create_icon_image(self.is_running)
            if not img:
                return
            self.icon = pystray.Icon(
                "MiracleAILauncher",
                img,
                f"奇迹AI启动器: {self.model_name}",
                menu=self._build_menu()
            )
            self.thread = threading.Thread(target=self.icon.run, daemon=True)
            self.thread.start()
        except Exception:
            pass

    def update_status(self, model_name, status_text="运行中", is_running=True, log_file=None):
        self.model_name = model_name
        self.status_text = status_text
        self.is_running = is_running
        if log_file:
            self.active_log_file = log_file
        if self.icon:
            try:
                new_img = self._create_icon_image(is_running)
                if new_img:
                    self.icon.icon = new_img
                self.icon.title = f"奇迹AI: {model_name} ({status_text})"
                self.icon.menu = self._build_menu()
                self.icon.update_menu()
            except Exception:
                pass

    def update_menu(self):
        if self.icon:
            try:
                self.icon.menu = self._build_menu()
                self.icon.update_menu()
            except Exception:
                pass

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None


def init_system_tray():
    """初始化 Windows 任务栏状态托盘图标"""
    global g_tray_manager
    if g_tray_manager is None:
        g_tray_manager = MiracleTrayManager(on_exit_callback=cleanup_and_exit)
        g_tray_manager.start()


def update_system_tray(model_name, status_text="运行中", is_running=True, log_file=None):
    """更新任务栏托盘图标状态与提示"""
    global g_tray_manager
    if g_tray_manager:
        g_tray_manager.update_status(model_name, status_text, is_running, log_file)



def get_today_str():
    return time.strftime("%Y%m%d")


def is_port_open(port, host="127.0.0.1"):
    """检测指定 TCP 端口是否处于监听状态"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def kill_port(port):
    """清理占用指定端口的进程 (纯 Python psutil 毫秒级极速关闭)"""
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.pid:
                try:
                    p = psutil.Process(conn.pid)
                    p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
    except Exception:
        pass


def kill_all_llama():
    """安全清空所有残留 llama 进程 (纯 Python 毫秒级)"""
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                name = proc.info["name"] or ""
                if "llama" in name.lower():
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass


def check_gpu_memory():
    """等待 GPU 显存完全释放至安全水位 (< 600MB)"""
    for _ in range(12):
        try:
            smi = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, errors="ignore"
            )
            if smi.returncode == 0:
                used = int(smi.stdout.strip() or "0")
                if used < 600:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return True


def get_hardware_info():
    """获取 GPU、CPU 与系统内存状态"""
    gpu_desc = "NVIDIA Tesla V100 32GB"
    gpu_mem = "32.0 GB"
    try:
        smi = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, errors="ignore"
        )
        if smi.returncode == 0 and smi.stdout.strip():
            parts = [p.strip() for p in smi.stdout.strip().split(",")]
            if len(parts) >= 2:
                gpu_desc = parts[0]
                gpu_mem = parts[1]
    except Exception:
        pass

    cpu_count = os.cpu_count() or 8
    return {
        "gpu": gpu_desc,
        "vram": gpu_mem,
        "cpu": f"{cpu_count} 核心线程",
        "os": "Windows 64-bit"
    }


def ensure_gateway_8081():
    """保证 8081 智能协同网关后台常驻运行"""
    if is_port_open(8081):
        return True

    today = get_today_str()
    log_file = os.path.join(LOGS_DIR, f"8081_proxy_{today}.log")
    proxy_script = os.path.join(BASE_DIR, "qwen_tool_proxy.py")

    if not os.path.exists(proxy_script):
        return False

    log_fp = open(log_file, "a", encoding="utf-8")
    creationflags = 0x08000000 if sys.platform == "win32" else 0

    global g_gateway_proc
    p = subprocess.Popen(
        [PYTHON_EXE, proxy_script, "--listen", "8081", "--target", "8083", "--vision-main", "8085", "--api-key", "llamacpp"],
        cwd=BASE_DIR,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=creationflags
    )
    g_gateway_proc = p

    for _ in range(25):
        if is_port_open(8081):
            return True
        time.sleep(0.2)
    return False


def ensure_sidecar_8085(wait=False):
    """
    启动并守护 8085 视觉侧挂眼睛 (Qwen3-VL-8B)
    纯 CPU / 系统内存常驻运行，强制隔离 CUDA，绝对 0 显存占用！
    支持异步后台预热，主脑直接并行加载，绝不阻塞用户等待！
    """
    global g_sidecar_proc
    if is_port_open(8085):
        sys.stdout.write(f"{C_GREEN}  ├─ 👁️ 8085 视觉眼睛已常驻在位 (Qwen3-VL-8B · 0显存 · 瞬时复用)！{C_RESET}\n\n")
        sys.stdout.flush()
        return True

    model_path = os.path.join(MODELS_DIR, "Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf")
    mmproj_path = os.path.join(MODELS_DIR, "mmproj-Qwen3VL-8B-Instruct-F16.gguf")

    if not (os.path.exists(model_path) and os.path.exists(mmproj_path)):
        return False

    today = get_today_str()
    log_file = os.path.join(LOGS_DIR, f"8085_sidecar_{today}.log")
    log_fp = open(log_file, "a", encoding="utf-8")

    sidecar_args = [
        LLAMA_SERVER,
        "-m", model_path,
        "--mmproj", mmproj_path,
        "-ngl", "0",
        "-c", "8192",
        "-b", "1024",
        "--ubatch-size", "1024",
        "-t", "6",
        "--parallel", "1",
        "--image-min-tokens", "1024",
        "--alias", "Qwen3-VL-8B",
        "--port", "8085",
        "--api-key", "llamacpp",
        "--log-file", log_file
    ]

    # 关键：隔离 CUDA 环境变量，使 8085 纯 CPU 运行，绝不触碰 V100 显存
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "-1"
    creationflags = 0x08000000 if sys.platform == "win32" else 0

    g_sidecar_proc = subprocess.Popen(
        sidecar_args,
        cwd=BASE_DIR,
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=creationflags
    )

    sys.stdout.write(f"{C_PURPLE}  ├─ 👁️ 8085 视觉眼睛正在后台并行预热 (Qwen3-VL-8B · CPU纯内存 · 0显存)...{C_RESET}\n\n")
    sys.stdout.flush()

    if wait:
        for _ in range(35):
            if is_port_open(8085):
                return True
            time.sleep(0.3)

    return True


def enable_kill_child_processes_on_exit():
    """
    通过 Windows 内核 Job Object 机制，将启动器进程及所有派生的子进程绑定为不可分割的作业。
    一旦控制台窗口被关闭（无论是点右上角红叉 X、任务管理器结束、还是退出），
    Windows 内核级别保证 100% 强制同步终结全部子进程 (8081网关/8083主脑/8085视觉)，绝无任何独活！
    """
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        CreateJobObjectW = kernel32.CreateJobObjectW
        CreateJobObjectW.restype = wintypes.HANDLE
        CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]

        SetInformationJobObject = kernel32.SetInformationJobObject
        SetInformationJobObject.restype = wintypes.BOOL
        SetInformationJobObject.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]

        AssignProcessToJobObject = kernel32.AssignProcessToJobObject
        AssignProcessToJobObject.restype = wintypes.BOOL
        AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        GetCurrentProcess = kernel32.GetCurrentProcess
        GetCurrentProcess.restype = wintypes.HANDLE

        h_job = CreateJobObjectW(None, None)
        if not h_job:
            return False

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_void_p),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryLimit", ctypes.c_size_t),
                ("PeakJobMemoryLimit", ctypes.c_size_t),
            ]

        JobObjectExtendedLimitInformation = 9
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        SetInformationJobObject(
            h_job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info)
        )
        AssignProcessToJobObject(h_job, GetCurrentProcess())
        return True
    except Exception:
        return False


def cleanup_all():
    """清理所有绑定的网关与大模型进程，确保无任何孤儿进程独活"""
    global g_is_cleaning, g_gateway_proc, g_sidecar_proc, g_main_proc, g_tray_manager
    if g_is_cleaning:
        return
    g_is_cleaning = True
    if g_tray_manager:
        try:
            g_tray_manager.stop()
        except Exception:
            pass
    for proc in [g_main_proc, g_sidecar_proc, g_gateway_proc]:
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
    kill_port(8081)
    kill_port(8083)
    kill_port(8085)
    kill_all_llama()


def cleanup_and_exit(signum=None, frame=None):
    """用户按 Ctrl+C 或正常退出时触发"""
    sys.stdout.write(f"\n{C_YELLOW}正在安全终结全部 AI 进程 (8081/8083/8085)...{C_RESET}\n")
    sys.stdout.flush()
    cleanup_all()
    sys.stdout.write(f"{C_GREEN}✅ 全部服务已彻底关闭，无任何后台进程独活。{C_RESET}\n")
    sys.exit(0)


# 注册控制台关闭事件处理 (拦截窗口 X 按钮点击)
if sys.platform == "win32":
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        def win_ctrl_handler(dwCtrlType):
            cleanup_all()
            return True
        g_ctrl_handler = HandlerRoutine(win_ctrl_handler)
        kernel32.SetConsoleCtrlHandler(g_ctrl_handler, True)
    except Exception:
        pass

atexit.register(cleanup_all)
signal.signal(signal.SIGINT, cleanup_and_exit)
signal.signal(signal.SIGTERM, cleanup_and_exit)


def print_banner(hw):
    os.system("cls" if sys.platform == "win32" else "clear")
    w = 88
    line_eq = "=" * w
    sys.stdout.write(f"{C_CYAN}{line_eq}{C_RESET}\n")
    sys.stdout.write(f"{C_BOLD}{C_GREEN}      🚀 AI 大模型统一启动器 v4.0 (Python 原生高能版) · 智能协同网关矩阵{C_RESET}\n")
    sys.stdout.write(f"{C_CYAN}{line_eq}{C_RESET}\n")
    sys.stdout.write(f"{C_GRAY}  硬件环境：{hw['gpu']} (显存: {hw['vram']}) | {hw['cpu']} | {hw['os']}{C_RESET}\n")
    sys.stdout.write(f"{C_GRAY}  核心准则：纯 Python 原生驱动 · Windows 内核 Job 绑定 · 网关毫秒级三态裁决{C_RESET}\n")
    sys.stdout.write(f"{C_CYAN}{line_eq}{C_RESET}\n\n")


def build_models_menu():
    """定义可用模型矩阵 (全部 27B 统一搭载 mmproj + --no-mmproj-offload + draft-mtp + -kvu + 4并发)"""
    mmproj_27b = os.path.join(MODELS_DIR, "mmproj-Qwen3.8-27B-F16.gguf")

    return [
        {
            "key": "1",
            "name": "Qwen3.8-27B-A [全能底座]",
            "quant": "27B·Q6_K",
            "ctx": "160K (4槽)",
            "speed_vram": "21G·MTP投机",
            "vision": "CPU 0显存",
            "best_for": "★ 终极主力(三态自适应)",
            "desc": "27B 旗舰 | 160K 统一池 | 原生 MTP 投机加速 | 0显存 CPU 视觉 | 4槽高吞吐流水线",
            "recommend": "【👑 终极全能主力 · 网关自适应三态裁决】",
            "alias": "Qwen3.8-27B-A-Q6_K",
            "is_text": False,
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3.8-27B-Abliterated-Q6_K.gguf"),
                "--mmproj", mmproj_27b,
                "-ngl", "99",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "163840",
                "-b", "2048",
                "--ubatch-size", "2048",
                "-t", "6",
                "--parallel", "4",
                "--kv-unified",
                "--cache-reuse", "512",
                "--flash-attn", "on",
                "--image-min-tokens", "1024",
                "--no-mmproj-offload",
                "--ctx-checkpoints", "4",
                "--spec-type", "draft-mtp",
                "--spec-draft-n-max", "2",
                "--spec-draft-n-min", "1",
                "--reasoning", "auto",
                "--reasoning-budget", "2048",
                "--reasoning-effort", "medium",
                "--reasoning-format", "deepseek",
                "--reasoning-preserve",
                "--no-warmup",
                "--temp", "0.3",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--dry-multiplier", "0.0",
                "--dry-base", "1.75",
                "--dry-allowed-length", "2",
                "--dry-penalty-last-n", "256",
                "--repeat-penalty", "1.05",
                "--presence-penalty", "0.0",
                "--jinja",
                "--chat-template-file", TEMPLATE_FILE,
                "--alias", "Qwen3.8-27B-A-Q6_K"
            ]
        },
        {
            "key": "2",
            "name": "Qwen3.8-27B-A [双槽MTP]",
            "quant": "27B·Q6_K",
            "ctx": "144K (2槽)",
            "speed_vram": "21G·MTP极速",
            "vision": "GPU 直通",
            "best_for": "日常深度编程·极速单发",
            "desc": "27B 旗舰 | 144K 统一池 (单槽72K) | 原生 MTP 极速推导 (45+ tok/s) | 视觉直通",
            "recommend": "【日常深度编程 · 极速单任务】",
            "alias": "Qwen3.8-27B-A-Q6_K",
            "is_text": False,
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3.8-27B-Abliterated-Q6_K.gguf"),
                "--mmproj", mmproj_27b,
                "-ngl", "99",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "147456",
                "-b", "2048",
                "--ubatch-size", "2048",
                "-t", "6",
                "--parallel", "2",
                "--kv-unified",
                "--cache-reuse", "512",
                "--flash-attn", "on",
                "--image-min-tokens", "1024",
                "--no-mmproj-offload",
                "--ctx-checkpoints", "4",
                "--spec-type", "draft-mtp",
                "--spec-draft-n-max", "2",
                "--spec-draft-n-min", "1",
                "--reasoning", "auto",
                "--reasoning-budget", "2048",
                "--reasoning-effort", "medium",
                "--reasoning-format", "deepseek",
                "--reasoning-preserve",
                "--no-warmup",
                "--temp", "0.3",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--dry-multiplier", "0.0",
                "--dry-base", "1.75",
                "--dry-allowed-length", "2",
                "--dry-penalty-last-n", "256",
                "--repeat-penalty", "1.05",
                "--presence-penalty", "0.0",
                "--jinja",
                "--chat-template-file", TEMPLATE_FILE,
                "--alias", "Qwen3.8-27B-A-Q6_K"
            ]
        },
        {
            "key": "3",
            "name": "Qwen3.8-27B-A [4并发]",
            "quant": "27B·Q6_K",
            "ctx": "160K (4槽)",
            "speed_vram": "21G·4槽并行",
            "vision": "GPU 直通",
            "best_for": "多Agent高并发高吞吐",
            "desc": "27B 旗舰 | 160K 统一池 | 4 槽并行高并发高吞吐 | 视觉直通",
            "recommend": "【多 Agent 高并发竞争】",
            "alias": "Qwen3.8-27B-A-Q6_K",
            "is_text": False,
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3.8-27B-Abliterated-Q6_K.gguf"),
                "--mmproj", mmproj_27b,
                "-ngl", "99",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "163840",
                "-b", "2048",
                "--ubatch-size", "512",
                "-t", "6",
                "--parallel", "4",
                "--kv-unified",
                "--cache-reuse", "512",
                "--flash-attn", "on",
                "--image-min-tokens", "1024",
                "--no-mmproj-offload",
                "--ctx-checkpoints", "2",
                "--spec-type", "draft-mtp",
                "--spec-draft-n-max", "2",
                "--spec-draft-n-min", "1",
                "--reasoning", "auto",
                "--reasoning-budget", "2048",
                "--reasoning-effort", "medium",
                "--reasoning-format", "deepseek",
                "--reasoning-preserve",
                "--no-warmup",
                "--temp", "0.3",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--repeat-penalty", "1.05",
                "--presence-penalty", "0.0",
                "--jinja",
                "--chat-template-file", TEMPLATE_FILE,
                "--alias", "Qwen3.8-27B-A-Q6_K"
            ]
        },
        {
            "key": "4",
            "name": "Qwen3.8-27B [NVFP4极致]",
            "quant": "27B·NVFP4",
            "ctx": "160K (2槽)",
            "speed_vram": "16G·MTP极速",
            "vision": "GPU 直通",
            "best_for": "官方高精·极限推导探索",
            "desc": "27B NVFP4 极致量化 | 160K 统一池 | MTP 极速推导 (生成峰值突破 50+ tok/s) | 视觉直通",
            "recommend": "【官方高精 · 极限速度探索】",
            "alias": "Qwen3.8-27B-N-H",
            "is_text": False,
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3.8-27B-NVFP4-MTP-HIGHEST.gguf"),
                "--mmproj", mmproj_27b,
                "-ngl", "99",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "163840",
                "-b", "2048",
                "--ubatch-size", "512",
                "-t", "6",
                "--parallel", "4",
                "--kv-unified",
                "--cache-reuse", "512",
                "--flash-attn", "on",
                "--image-min-tokens", "1024",
                "--no-mmproj-offload",
                "--ctx-checkpoints", "2",
                "--spec-type", "draft-mtp",
                "--spec-draft-n-max", "2",
                "--spec-draft-n-min", "1",
                "--reasoning", "auto",
                "--reasoning-budget", "2048",
                "--reasoning-effort", "medium",
                "--reasoning-format", "deepseek",
                "--reasoning-preserve",
                "--no-warmup",
                "--temp", "0.3",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--repeat-penalty", "1.05",
                "--jinja",
                "--chat-template-file", TEMPLATE_FILE,
                "--alias", "Qwen3.8-27B-N-H"
            ]
        },
        {
            "key": "5",
            "name": "Qwen3.8-27B [NVFP4超长]",
            "quant": "27B·NVFP4",
            "ctx": "256K (2槽)",
            "speed_vram": "16G·MTP加速",
            "vision": "GPU 直通",
            "best_for": "超长上下文·大代码推演",
            "desc": "27B NVFP4 极致量化 | 256K 超大统一KV池 | MTP加速 | 视觉直通",
            "recommend": "【超长上下文 · 巨型代码库推演】",
            "alias": "Qwen3.8-27B-MID-HIGH",
            "is_text": False,
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3.8-27B-NVFP4-MTP-MID-HIGH.gguf"),
                "--mmproj", mmproj_27b,
                "-ngl", "99",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "262144",
                "-b", "2048",
                "--ubatch-size", "512",
                "-t", "6",
                "--parallel", "4",
                "--kv-unified",
                "--cache-reuse", "512",
                "--flash-attn", "on",
                "--image-min-tokens", "1024",
                "--no-mmproj-offload",
                "--ctx-checkpoints", "2",
                "--spec-type", "draft-mtp",
                "--spec-draft-n-max", "2",
                "--spec-draft-n-min", "1",
                "--reasoning", "auto",
                "--reasoning-budget", "2048",
                "--reasoning-effort", "medium",
                "--reasoning-format", "deepseek",
                "--reasoning-preserve",
                "--no-warmup",
                "--temp", "0.3",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--repeat-penalty", "1.05",
                "--jinja",
                "--chat-template-file", TEMPLATE_FILE,
                "--alias", "Qwen3.8-27B-MID-HIGH"
            ]
        },
        {
            "key": "6",
            "name": "Ornith-1.5-35B [MoE大脑]",
            "quant": "35B·Q4_K",
            "ctx": "128K (单槽)",
            "speed_vram": "23G·MoE并行",
            "vision": "CPU 0显存",
            "best_for": "深度复杂逻辑·数理证明",
            "desc": "35B 稀疏混合专家 | 128K 超长上下文 | 原生挂载 mmproj-35B (CPU 0显存)",
            "recommend": "【深度复杂逻辑与数理推理】",
            "alias": "Ornith-1.5-35B",
            "is_text": False,
            "args": [
                "-m", os.path.join(MODELS_DIR, "Ornith-1.5-35B-Q4_K_M.gguf"),
                "--mmproj", os.path.join(MODELS_DIR, "mmproj-Ornith-1.5-35B-A3B-f16.gguf"),
                "-ngl", "99",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "131072",
                "-b", "4096",
                "--ubatch-size", "4096",
                "-t", "6",
                "--parallel", "1",
                "--flash-attn", "on",
                "--image-min-tokens", "1024",
                "--no-mmproj-offload",
                "--ctx-checkpoints", "4",
                "--reasoning", "auto",
                "--reasoning-budget", "2048",
                "--reasoning-effort", "medium",
                "--reasoning-format", "deepseek",
                "--reasoning-preserve",
                "--temp", "0.3",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--repeat-penalty", "1.05",
                "--jinja",
                "--chat-template-file", TEMPLATE_FILE,
                "--alias", "Ornith-1.5-35B"
            ]
        },
        {
            "key": "7",
            "name": "Qwen3-VL-8B [视觉独立版]",
            "quant": "8B·UD-Q4",
            "ctx": "32K  (单槽)",
            "speed_vram": "8G ·GPU直通",
            "vision": "端到端视觉",
            "best_for": "高精图文OCR·图纸评审",
            "desc": "8B 旗舰视觉 | UD-Q4_K_XL 极致量化 | GPU 直通高精图文推理 (32K)",
            "recommend": "【端到端高精 OCR 与图纸评审】",
            "alias": "Qwen3-VL-8B",
            "is_text": False,
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf"),
                "--mmproj", os.path.join(MODELS_DIR, "mmproj-Qwen3VL-8B-Instruct-F16.gguf"),
                "-ngl", "99",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "32768",
                "-b", "2048",
                "--ubatch-size", "2048",
                "-t", "6",
                "--parallel", "1",
                "--flash-attn", "on",
                "--image-min-tokens", "1024",
                "--no-warmup",
                "--temp", "0.3",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--repeat-penalty", "1.05",
                "--jinja",
                "--alias", "Qwen3-VL-8B"
            ]
        },
        {
            "key": "8",
            "name": "Gemma-4-E4B [轻量多模]",
            "quant": "4B·Q6_K",
            "ctx": "128K (单槽)",
            "speed_vram": "5G ·原生轻量",
            "vision": "CPU 0显存",
            "best_for": "轻量极速多模态对话",
            "desc": "4B MoE 架构 | Q6_K_P 高精量化 | 128K 上下文 | 原生挂载 mmproj",
            "recommend": "【轻量极速多模态对话】",
            "alias": "Gemma-4-E4B",
            "is_text": False,
            "args": [
                "-m", os.path.join(MODELS_DIR, "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q6_K_P.gguf"),
                "--mmproj", os.path.join(MODELS_DIR, "mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-f16.gguf"),
                "-ngl", "99",
                "--cache-type-k", "f16",
                "--cache-type-v", "f16",
                "-c", "131072",
                "-b", "2048",
                "-t", "6",
                "--parallel", "1",
                "--flash-attn", "on",
                "--image-min-tokens", "1024",
                "--temp", "0.3",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--repeat-penalty", "1.0",
                "--jinja",
                "--alias", "Gemma-4-E4B"
            ]
        },
        {
            "key": "9",
            "name": "Qwen3.5-4B [纯文本极速]",
            "quant": "4B·Q6_K",
            "ctx": "256K (单槽)",
            "speed_vram": "4G ·低功耗",
            "vision": "8085侧挂",
            "best_for": "低功耗代码辅助·轻量问答",
            "desc": "4B 轻量级对话与代码辅助 | 256K 超大上下文 | 8085 视觉眼睛侧挂",
            "recommend": "【低功耗快速轻量辅助】",
            "alias": "Qwen3.5-4B",
            "is_text": True,
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3.5-4B.gguf") if os.path.exists(os.path.join(MODELS_DIR, "Qwen3.5-4B.gguf")) else os.path.join(MODELS_DIR, "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf"),
                "-ngl", "99",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "262144",
                "-b", "2048",
                "-t", "6",
                "--parallel", "1",
                "--flash-attn", "on",
                "--temp", "0.3",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--repeat-penalty", "1.0",
                "--jinja",
                "--alias", "Qwen3.5-4B"
            ]
        }
    ]


def str_display_width(s):
    """精确计算包含 ANSI 颜色代码与中日韩 CJK 全角字符的终端可见宽度"""
    clean = re.sub(r"\033\[[0-9;]*m", "", s)
    return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in clean)


def pad_display(s, target_width, align="left"):
    """按字符终端可见显示宽度进行精准中英文空格填充对齐"""
    cur = str_display_width(s)
    pad = max(0, target_width - cur)
    if align == "right":
        return " " * pad + s
    elif align == "center":
        left = pad // 2
        right = pad - left
        return " " * left + s + " " * right
    return s + " " * pad


def render_models_grid(menu):
    """一横排整齐渲染模型列表，上下严格对齐，清晰展示功能参数与推荐场景"""
    headers = ["序号", "启动形态与模型名称", "规格量化", "上下文/槽位", "显存/推导加速", "视觉方案", "核心定位与推荐场景"]
    widths  = [5,    26,                  11,       12,          15,            10,       28]

    header_line = " ".join(pad_display(f"{C_BOLD}{C_CYAN}{h}{C_RESET}", w) for h, w in zip(headers, widths))
    sep_line = f"{C_GRAY}" + " ".join("─" * w for w in widths) + f"{C_RESET}"

    sys.stdout.write(f"\n{header_line}\n")
    sys.stdout.write(f"{sep_line}\n")

    for item in menu:
        col_key = pad_display(f"{C_BOLD}{C_CYAN}[{item['key']}]{C_RESET}", widths[0])
        col_name = pad_display(f"{C_GREEN}{item['name']}{C_RESET}", widths[1])
        col_quant = pad_display(f"{C_YELLOW}{item.get('quant', '-')}{C_RESET}", widths[2])
        col_ctx = pad_display(f"{C_CYAN}{item.get('ctx', '-')}{C_RESET}", widths[3])
        col_speed = pad_display(f"{C_PURPLE}{item.get('speed_vram', '-')}{C_RESET}", widths[4])
        col_vision = pad_display(f"{C_BLUE}{item.get('vision', '-')}{C_RESET}", widths[5])
        col_best = pad_display(f"{C_WHITE}{item.get('best_for', '-')}{C_RESET}", widths[6])

        sys.stdout.write(f"{col_key} {col_name} {col_quant} {col_ctx} {col_speed} {col_vision} {col_best}\n")

    sys.stdout.write(f"{sep_line}\n")
    exit_key = pad_display(f"{C_BOLD}{C_RED}[0]{C_RESET}", widths[0])
    sys.stdout.write(f"{exit_key} {C_GRAY}退出启动器 (安全关闭并清理全部后台服务与显存){C_RESET}\n\n")

def main():
    global g_main_proc

    # 处理 CLI 选项 (例如 --list / --list-models)
    if any(arg.lower() in ("-listmodels", "--list-models", "list", "--list", "-l") for arg in sys.argv[1:]):
        menu = build_models_menu()
        out = [{"index": idx + 1, "name": m["alias"], "tag": m["desc"], "category": "vision" if not m["is_text"] else "text"} for idx, m in enumerate(menu)]
        print(json.dumps(out, ensure_ascii=False))
        return

    # 启用 Windows 内核级进程同生共死 Job 绑定 (控制台红叉一关，内核强制一并终结全部子进程)
    enable_kill_child_processes_on_exit()

    hw = get_hardware_info()
    print_banner(hw)

    # 启动 Windows 任务栏通知区域状态托盘 (右键随时快捷操作与退出)
    init_system_tray()

    # 1. 基础组件初始化：拉起 8081 智能协同网关
    sys.stdout.write(f"{C_BOLD}正在联动拉起 8081 智能协同网关...{C_RESET}\n")
    if ensure_gateway_8081():
        sys.stdout.write(f"{C_GREEN}  ├─ ✅ 8081 智能协同网关已就绪 (http://127.0.0.1:8081/dashboard){C_RESET}\n\n")

    # 2. 呈现模型菜单 (整齐排列一横排网格 UI)
    menu = build_models_menu()
    sys.stdout.write(f"{C_BOLD}{C_CYAN}请选择要固定启动的主模型：{C_RESET}")
    render_models_grid(menu)

    if len(sys.argv) > 1:
        arg = sys.argv[1].strip()
        if arg in ("--help", "-h"):
            sys.stdout.write("用法: python launcher_main.py [模型编号: 1-9 | 0(退出)]\n")
            return
        choice = arg
    else:
        try:
            choice = input(f"{C_BOLD}请输入选项编号 [默认 1]: {C_RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n已取消输入，安全退出。\n")
            choice = "0"

    if not choice:
        choice = "1"
    if choice == "0":
        cleanup_and_exit()

    selected = None
    for item in menu:
        if item["key"] == choice:
            selected = item
            break
    if not selected:
        selected = menu[0]

    sys.stdout.write(f"\n{C_CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}\n")
    sys.stdout.write(f"  🚀 正在启动: {C_BOLD}{selected['name']}{C_RESET}\n")
    sys.stdout.write(f"{C_CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}\n\n")

    # 3. 视觉与多模态组件适配 (仅纯文本侧挂模型按需拉起 8085，原生多模态直接释放 8085)
    if selected.get("is_text") and selected.get("vision") == "8085侧挂":
        if is_port_open(8085):
            sys.stdout.write(f"{C_GREEN}  ├─ 👁️ 8085 视觉眼睛已在位 (Qwen3-VL-8B · 0显存)！{C_RESET}\n\n")
        else:
            sys.stdout.write(f"{C_PURPLE}  ├─ 👁️ 检测到当前模型为纯文本，正在按需启动 8085 视觉侧挂眼睛...{C_RESET}\n\n")
            ensure_sidecar_8085(wait=False)
    else:
        if is_port_open(8085):
            sys.stdout.write(f"{C_YELLOW}  ├─ 🧹 当前模型自带原生多模态，正在关闭 8085 侧挂以释放 CPU 与内存...{C_RESET}\n")
            kill_port(8085)
        sys.stdout.write(f"{C_GREEN}  ├─ 🖼️ 原生多模态全模态底座：GPU/CPU 视觉直通，网关自适应调度 (无需 8085 侧挂，0 内存浪费)！{C_RESET}\n\n")

    # 4. 清理 8083 旧进程并校验显存安全
    kill_port(8083)
    check_gpu_memory()

    # 5. 启动 8083 主脑引擎
    today = get_today_str()
    main_log_file = os.path.join(LOGS_DIR, f"8083_llama_{today}.log")
    
    server_cmd = [LLAMA_SERVER] + selected["args"] + [
        "--port", "8083",
        "--api-key", "llamacpp",
        "--log-file", main_log_file
    ]

    sys.stdout.write(f"{C_GREEN}  🔥 正在极速加载主脑至 V100 32GB 显存 (日志落盘: {os.path.basename(main_log_file)})...{C_RESET}\n")
    update_system_tray(model_name=selected["name"], status_text="模型加载中 (V100 32GB)...", is_running=False, log_file=main_log_file)
    sys.stdout.flush()

    main_env = os.environ.copy()
    main_env["CUDA_CACHE_MAXSIZE"] = "2147483648"
    main_env["CUDA_DEVICE_MAX_CONNECTIONS"] = "1"
    creationflags = 0x08000000 if sys.platform == "win32" else 0

    # 以子进程前台常驻运行，注入专用 CUDA JIT 2GB 编译流
    g_main_proc = subprocess.Popen(
        server_cmd,
        cwd=BASE_DIR,
        env=main_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags
    )

    # 毫秒级极速高频轮询检测端口
    ready = False
    for i in range(160):
        if is_port_open(8083):
            ready = True
            break
        time.sleep(0.25)
        if i % 4 == 0:
            sys.stdout.write(".")
            sys.stdout.flush()

    if ready:
        update_system_tray(model_name=selected["name"], status_text="运行中 (8081网关/8083主脑)", is_running=True, log_file=main_log_file)
        sys.stdout.write(f"\n\n{C_BOLD}{C_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}\n")
        sys.stdout.write(f"  🎉 主脑引擎已成功常驻！端口: http://127.0.0.1:8083/v1\n")
        sys.stdout.write(f"  📡 网关双通接口: http://127.0.0.1:8081/v1 (Claude Code / ccswitch)\n")
        sys.stdout.write(f"  📊 算力监控大屏: http://127.0.0.1:8081/dashboard\n")
        sys.stdout.write(f"  🔔 任务栏托盘状态已激活：右下角图标可双击打开大屏，右键随时完全退出/隐藏黑框\n")
        sys.stdout.write(f"{C_BOLD}{C_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}\n\n")
        sys.stdout.write(f"{C_GRAY}系统处于锁定常驻托管状态，按 Ctrl+C 安全停止...{C_RESET}\n\n")
    else:
        sys.stdout.write(f"\n{C_RED}⚠️ 8083 端口未能在 45 秒内就绪，请检查 {main_log_file}{C_RESET}\n")

    try:
        while True:
            time.sleep(1)
            if g_main_proc.poll() is not None:
                sys.stdout.write(f"\n{C_YELLOW}主脑进程已退出 (code={g_main_proc.returncode})。{C_RESET}\n")
                break
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_all()


if __name__ == "__main__":
    main()
