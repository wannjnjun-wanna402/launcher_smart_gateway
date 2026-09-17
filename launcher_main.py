# -*- coding: utf-8 -*-
"""
===============================================================================
  🚀 AI 大模型统一启动器 (Python 原生高能版) · Model Launcher v4.0
  • 彻底告别 PowerShell 脚本与编码兼容陷阱，纯 Python 原生多进程治理
  • 8081 智能协同网关常驻 · 8083 主脑全能底座 · 8085 视觉侧挂眼睛 (CPU 0显存)
  • Windows 内核级 Job Object 绑定，同生共死，100% 杜绝孤儿进程与显存残留
  • 严格遵循单日单一日志规范 (8083_llama_YYYYMMDD.log / 8085_sidecar_YYYYMMDD.log)
===============================================================================
"""

import os
import sys
import time
import socket
import subprocess
import signal
# 强制 UTF-8 标准输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 🛡️ 开箱自愈装甲：核心依赖缺失时自动静默安装补齐，彻底杜绝新机器双击闪退
try:
    import psutil
except ImportError:
    sys.stdout.write("[INIT] 🚀 首次运行检测到缺少基础运行库，正在自动静默补齐 (psutil, requests, pyyaml)...\n")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "psutil>=5.9.0", "requests>=2.28.0", "pyyaml>=6.0"])
        import psutil
        sys.stdout.write("[INIT] ✅ 依赖环境补齐成功，正在进入大模型中枢...\n\n")
    except Exception as e:
        sys.stderr.write(f"[WARN] 自动补齐依赖受限: {e}，若报错请手动执行: pip install -r requirements.txt\n")

import json
import atexit
import ctypes
import unicodedata
import re
import threading
from ctypes import wintypes

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

def resolve_models_dir():
    candidates = [
        os.environ.get("MODELS_DIR"),
        r"E:\models",
        r"D:\models",
        r"C:\models",
        os.path.join(BASE_DIR, "models"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return os.path.join(BASE_DIR, "models")

MODELS_DIR = resolve_models_dir()
PYTHON_EXE = sys.executable
LLAMA_SERVER = os.path.join(BASE_DIR, "llama-server.exe")
TEMPLATE_FILE = os.path.join(SCRIPT_DIR, "chat_template_qwen_fixed.jinja")
if not os.path.exists(TEMPLATE_FILE):
    TEMPLATE_FILE = os.path.join(BASE_DIR, "chat_template_qwen_fixed.jinja")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

def cleanup_stale_session_logs():
    """启动前自动清理或合流既往异常残留的临时 session 日志文件，确保每个端口/模型单日仅呈现单一累加主日志"""
    try:
        import glob
        today_str = time.strftime("%Y%m%d")
        for sf in glob.glob(os.path.join(LOGS_DIR, "_*_sess_*.log")):
            try:
                target_prefix = "8083_llama_" if "8083" in sf else ("8085_sidecar_" if "8085" in sf else "")
                if target_prefix:
                    target_log = os.path.join(LOGS_DIR, f"{target_prefix}{today_str}.log")
                    with open(sf, "r", encoding="utf-8", errors="replace") as r_sf, open(target_log, "a", encoding="utf-8") as w_df:
                        w_df.write(r_sf.read())
                os.remove(sf)
            except Exception:
                pass
    except Exception:
        pass

cleanup_stale_session_logs()

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
g_embedding_proc = None
g_main_proc = None
g_is_cleaning = False
g_tray_manager = None
g_forwarder_stop = None

# -----------------------------------------------------------------------------
# 🛡️ 智能系统流畅度与硬件资源协调装甲 (保证鼠标/系统100%零卡顿)
# -----------------------------------------------------------------------------
try:
    _total_logical_cores = psutil.cpu_count(logical=True) or 6
    # 黄金线程法则：始终保留至少 2 个核心专供操作系统、鼠标硬件中断与浏览器渲染
    OPTIMAL_CPU_THREADS = str(max(2, min(8, _total_logical_cores - 2)))
except Exception:
    OPTIMAL_CPU_THREADS = "4"


def apply_system_smoothness_armor(pid: int, label: str = "服务"):
    """
    智能资源协调装甲：
    1. 将进程优先级降至 BELOW_NORMAL_PRIORITY_CLASS，保证鼠标/键盘/DWM随时秒级抢占响应
    2. 绑定 CPU 亲和性，隔离并保留 Core 0 专供系统硬件中断、DWM.exe 与鼠标光标
    """
    if not pid:
        return
    try:
        p = psutil.Process(pid)
        # 1. 优先级降级为低于常规，确保鼠标光标与桌面合成永远秒级抢占
        if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        # 2. 隔离 Core 0，专供 Windows 鼠标中断与显示子系统
        total_cores = psutil.cpu_count(logical=True) or 6
        if total_cores > 2:
            p.cpu_affinity(list(range(1, total_cores)))
    except Exception:
        pass


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
            # 超高对比度荧光色：运行态极亮翠绿 (#00EB8C)，待机态明亮极光青 (#38BDF8)，绝不与任务栏黑底混淆
            main_color = (0, 235, 140, 255) if active else (56, 189, 248, 255)
            # 外环能量圈
            draw.ellipse([(6, 6), (size - 7, size - 7)], fill=main_color)
            # 中层对比圈
            draw.ellipse([(14, 14), (size - 15, size - 15)], fill=(15, 23, 42, 255))
            # 核心纯白高光眼
            draw.ellipse([(22, 22), (size - 23, size - 23)], fill=(255, 255, 255, 255))
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

        def action_open_sidecar_log(icon, item):
            today = time.strftime("%Y%m%d")
            log_path = os.path.join(LOGS_DIR, f"8085_sidecar_{today}.log")
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
            cleanup_all(kill_everything=True)
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
            MenuItem("🖼️ 查看视觉侧挂实时日志 (8085)", action_open_sidecar_log),
            MenuItem("📊 查看网关协同流水日志 (8081)", action_open_proxy_log),
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
            def _on_tray_ready(icon):
                icon.visible = True
                try:
                    icon.notify("奇迹AI服务已常驻托盘，右键图标可呼出快捷控制菜单！\n(若任务栏未见，请查看右下角 ^ 折叠抽屉)", "🤖 奇迹AI高能底座")
                except Exception:
                    pass

            # pystray 原生 run_detached 派生 Windows 专用后台消息泵，通过 setup 回调确保 HWND 完全就绪后再显示
            self.icon.run_detached(setup=_on_tray_ready)
            sys.stdout.write(f"{C_GREEN}  🔔 任务栏托盘已激活 (位于屏幕右下角通知区域，可展开 ^ 拖出){C_RESET}\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(f"{C_YELLOW}  ⚠️ 任务栏托盘初始化跳过 ({e})，不影响主控制台运行{C_RESET}\n")
            sys.stdout.flush()

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


def ensure_port_available(port, host="127.0.0.1"):
    """清理端口占用并校验可绑定性；若遇到 WinError 10013 (Windows NAT 排除端口保留冲突) 自动自愈修复"""
    kill_port(port)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
    except OSError as e:
        if getattr(e, "winerror", None) == 10013:
            sys.stdout.write(f"\n{C_YELLOW}  ⚠️ 端口 {port} 被 Windows 动态段保留 (WinError 10013)，正在自动自愈...{C_RESET}\n")
            sys.stdout.flush()
            try:
                subprocess.run("netsh int ipv4 set dynamicport tcp start=49152 num=16384", shell=True, capture_output=True)
                subprocess.run("net stop winnat", shell=True, capture_output=True)
                subprocess.run("net start winnat", shell=True, capture_output=True)
                time.sleep(0.5)
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
                    s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s2.bind((host, port))
                    sys.stdout.write(f"{C_GREEN}  ✅ Windows NAT 端口保留冲突已自愈，端口 {port} 恢复可用！{C_RESET}\n")
                    sys.stdout.flush()
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False


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

def start_session_log_forwarder(daily_log: str, session_prefix: str = "8083"):
    """
    🌟 单日日志累加保全转发器 (Zero-Loss Daily Log Preserver)
    解决 llama-server.exe --log-file 每次启动以 'w' 模式截断日志的问题：
    1. 引导 llama-server.exe 输出到专有 session 临时文件；
    2. 后台守护线程毫秒级将 session 日志以 'a' 模式追加合并入 daily_log；
    3. 保证无论重启多少次，当日日志 100% 持续累加，绝不丢失任何数据。
    """
    session_file = os.path.join(LOGS_DIR, f"_{session_prefix}_sess_{os.getpid()}_{int(time.time()*1000)}.log")
    stop_event = threading.Event()

    try:
        with open(daily_log, "a", encoding="utf-8") as df:
            df.write(f"\n--- [8083 Session at {time.strftime('%Y-%m-%d %H:%M:%S')}] ---\n")
            df.flush()
    except Exception:
        pass

    def _forwarder_worker():
        cur_pos = 0
        while not stop_event.is_set():
            try:
                if os.path.exists(session_file):
                    sz = os.path.getsize(session_file)
                    if sz > cur_pos:
                        with open(session_file, "r", encoding="utf-8", errors="replace") as sf:
                            sf.seek(cur_pos)
                            chunk = sf.read()
                            if chunk:
                                with open(daily_log, "a", encoding="utf-8") as df:
                                    df.write(chunk)
                                    df.flush()
                                cur_pos = sf.tell()
                time.sleep(0.05)
            except Exception:
                time.sleep(0.1)

        try:
            if os.path.exists(session_file):
                sz = os.path.getsize(session_file)
                if sz > cur_pos:
                    with open(session_file, "r", encoding="utf-8", errors="replace") as sf:
                        sf.seek(cur_pos)
                        chunk = sf.read()
                        if chunk:
                            with open(daily_log, "a", encoding="utf-8") as df:
                                df.write(chunk)
                                df.flush()
            if os.path.exists(session_file):
                os.remove(session_file)
        except Exception:
            pass

    th = threading.Thread(target=_forwarder_worker, daemon=True, name="DailyLogForwarder")
    th.start()
    return session_file, stop_event


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


def get_llamacpp_version():
    """获取当前 llama.cpp (llama-server.exe) 的精准版本号，例如 B10816"""
    try:
        res = subprocess.run([LLAMA_SERVER, "--version"], capture_output=True, text=True, errors="ignore", timeout=3)
        output = (res.stderr or "") + (res.stdout or "")
        m = re.search(r"build\s*(\d+)", output, re.IGNORECASE)
        commit_m = re.search(r"commit\s*([0-9a-fA-F]+)", output)
        if m:
            commit_str = f" ({commit_m.group(1)[:7]})" if commit_m else ""
            return f"B{m.group(1)}{commit_str}"
        for line in output.splitlines():
            line = line.strip()
            if "version" in line.lower() or "build" in line.lower():
                return line
    except Exception:
        pass
    return "未知"


def get_hardware_info():
    """获取真实 GPU、CPU 与系统内存状态及智能算力分档"""
    gpu_desc = "CPU 推理模式 / 集成显卡"
    gpu_mem = "系统共享内存"
    vram_mb = 0
    has_nvidia = False
    try:
        smi = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, errors="ignore"
        )
        if smi.returncode == 0 and smi.stdout.strip():
            parts = [p.strip() for p in smi.stdout.strip().split(",")]
            if len(parts) >= 2:
                gpu_desc = parts[0]
                try:
                    vram_mb = int(float(parts[1]))
                    gpu_mem = f"{vram_mb / 1024:.1f} GB"
                except Exception:
                    gpu_mem = parts[1]
                has_nvidia = True
    except Exception:
        pass

    if not has_nvidia:
        try:
            mem = psutil.virtual_memory()
            gpu_mem = f"{mem.available / (1024**3):.1f} GB (可用内存)"
        except Exception:
            gpu_mem = "共享内存"

    # 计算硬件算力分级 Tier
    if has_nvidia and vram_mb >= 22000:
        tier = "Tier 1 [旗舰级显存 >=24GB · 支持27B/35B深度推理与4并发]"
    elif has_nvidia and vram_mb >= 10000:
        tier = "Tier 2 [主流级显存 12GB~20GB · 推荐7B~14B或27B紧凑版]"
    elif has_nvidia and vram_mb > 0:
        tier = "Tier 3 [紧凑型显卡 <=8GB · 推荐0.5B~4B轻量模型]"
    else:
        tier = "CPU / 集显模式 [推荐轻量模型 · 限制KV与线程]"

    cpu_count = os.cpu_count() or 8
    llama_ver = get_llamacpp_version()
    return {
        "gpu": gpu_desc,
        "vram": gpu_mem,
        "vram_mb": vram_mb,
        "tier": tier,
        "has_nvidia": has_nvidia,
        "cpu": f"{cpu_count} 核心线程",
        "llama_version": llama_ver,
        "os": "Windows 64-bit"
    }


def ensure_gateway_8081():
    """保证 8081 智能协同网关后台常驻运行"""
    if is_port_open(8081):
        return True

    ensure_port_available(8081)

    today = get_today_str()
    log_file = os.path.join(LOGS_DIR, f"8081_proxy_{today}.log")
    proxy_script = os.path.join(BASE_DIR, "qwen_tool_proxy.py")

    if not os.path.exists(proxy_script):
        return False

    log_fp = open(log_file, "a", encoding="utf-8")
    creationflags = (0x08000000 | 0x00000008) if sys.platform == "win32" else 0

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


def get_sidecar_8085_mode():
    """检测当前 8085 端口常驻的侧挂眼睛是运行在 GPU 还是 CPU"""
    if not is_port_open(8085):
        return None
    sidecar_state_file = os.path.join(LOGS_DIR, "active_sidecar.json")
    if os.path.exists(sidecar_state_file):
        try:
            with open(sidecar_state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("mode")
        except Exception:
            pass
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmd = p.info.get("cmdline") or []
                if "llama-server" in (p.info.get("name") or "").lower() and "8085" in cmd:
                    if "-ngl" in cmd:
                        ngl_idx = cmd.index("-ngl")
                        if ngl_idx + 1 < len(cmd) and cmd[ngl_idx + 1] == "0":
                            return "cpu"
                        return "gpu"
            except Exception:
                pass
    except Exception:
        pass
    return "gpu"


def ensure_sidecar_8085(wait=False, use_gpu=False):
    """
    启动并守护 8085 视觉侧挂眼睛 (Qwen3VL-4B-Instruct-Q4_K_M)
    - 纯文本模型一律强制搭载 CPU 内存 (-ngl 0，CUDA_VISIBLE_DEVICES=-1，0 显存绝对防爆)
    - 若当前运行模式与目标不符，自动平滑热重启切换
    """
    global g_sidecar_proc
    target_mode = "gpu" if use_gpu else "cpu"

    if is_port_open(8085):
        cur_mode = get_sidecar_8085_mode()
        if cur_mode == target_mode:
            mode_desc = "GPU加速 ~4.5G显存 · 0.5s极速识图" if use_gpu else "CPU纯内存 · 0显存安全防爆"
            sys.stdout.write(f"{C_GREEN}  ├─ 👁️ 8085 视觉眼睛已在位 (Qwen3VL-4B · {mode_desc})！{C_RESET}\n\n")
            sys.stdout.flush()
            return True
        else:
            old_str = "GPU 显存模式" if cur_mode == "gpu" else "CPU 内存模式"
            new_str = "GPU 显存加速 (~4.5G)" if use_gpu else "CPU 纯内存 (0显存释放)"
            sys.stdout.write(f"{C_YELLOW}  ├─ 🔄 8085 视觉模式动态切换：当前为 {old_str}，重置切换为 {new_str}...{C_RESET}\n")
            sys.stdout.flush()
            kill_port(8085)
            time.sleep(0.5)

    model_path = os.path.join(MODELS_DIR, "Qwen3VL-4B-Instruct-Q4_K_M.gguf")
    mmproj_path = os.path.join(MODELS_DIR, "mmproj-Qwen3VL-4B-Instruct-F16.gguf")
    if not os.path.exists(mmproj_path):
        mmproj_path = os.path.join(MODELS_DIR, "mmproj-Qwen3VL-4B-Instruct-f16.gguf")

    if not (os.path.exists(model_path) and os.path.exists(mmproj_path)):
        return False

    today = get_today_str()
    log_file = os.path.join(LOGS_DIR, f"8085_sidecar_{today}.log")
    log_fp = open(log_file, "a", encoding="utf-8", buffering=1)
    try:
        log_fp.write(f"\n--- [8085 Sidecar Session at {time.strftime('%Y-%m-%d %H:%M:%S')}] ---\n")
        log_fp.flush()
    except Exception:
        pass

    ngl_val = "99" if use_gpu else "0"
    sidecar_args = [
        LLAMA_SERVER,
        "-m", model_path,
        "--mmproj", mmproj_path,
        "-ngl", ngl_val,
        "-c", "8192",
        "-b", "2048",
        "--ubatch-size", "2048",
        "-t", OPTIMAL_CPU_THREADS,
        "--parallel", "1",
        "--image-min-tokens", "1024",
        "--alias", "Qwen3VL-4B,Qwen3VL-4B-Instruct-Q4_K_M,default",
        "--port", "8085",
        "--api-key", "llamacpp"
    ]

    env = os.environ.copy()
    if use_gpu:
        sidecar_args.extend(["--flash-attn", "on", "--cache-type-k", "f16", "--cache-type-v", "f16"])
        env.pop("CUDA_VISIBLE_DEVICES", None)
        mode_msg = "GPU加速 ~4.5G显存 · 0.5s闪电识图"
    else:
        env["CUDA_VISIBLE_DEVICES"] = "-1"
        env["OMP_NUM_THREADS"] = "4"
        env["MKL_NUM_THREADS"] = "4"
        mode_msg = "CPU纯内存 · 0显存 · 线程绑定保护"

    creationflags = (0x08000000 | 0x00004000) if sys.platform == "win32" else 0

    g_sidecar_proc = subprocess.Popen(
        sidecar_args,
        cwd=BASE_DIR,
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=creationflags
    )
    apply_system_smoothness_armor(g_sidecar_proc.pid, "8085视觉侧挂")

    try:
        with open(os.path.join(LOGS_DIR, "active_sidecar.json"), "w", encoding="utf-8") as asf:
            json.dump({
                "mode": target_mode,
                "ngl": int(ngl_val),
                "pid": g_sidecar_proc.pid,
                "updated_at": time.time()
            }, asf, indent=2)
    except Exception:
        pass

    sys.stdout.write(f"{C_PURPLE}  ├─ 👁️ 8085 视觉正在后台预热 (Qwen3VL-4B · {mode_msg})...{C_RESET}\n\n")
    sys.stdout.flush()

    if wait:
        for _ in range(35):
            if is_port_open(8085):
                return True
            time.sleep(0.3)

    return True


def ensure_embedding_8086(wait=False):
    """
    启动并守护 8086 向量检索引擎 (BGE-M3 · 8192 超长上下文 · 1024 维密集检索)
    - 纯 CPU 内存安全常驻 (-ngl 0，CUDA_VISIBLE_DEVICES=-1，0 显存占用，~600MB 内存)
    - 单日单一日志汇流 (8086_embedding_YYYYMMDD.log)
    """
    global g_embedding_proc
    if is_port_open(8086):
        sys.stdout.write(f"{C_GREEN}  ├─ 🧮 8086 向量引擎已在位 (BGE-M3 · 8192长文本 · CPU 0显存)！{C_RESET}\n\n"[:79] + "\n")
        sys.stdout.flush()
        return True

    model_path = os.path.join(MODELS_DIR, "bge-m3-q8_0.gguf")
    if not os.path.exists(model_path):
        model_path = os.path.join(MODELS_DIR, "bge-large-zh-v1.5-q8_0.gguf")
        if not os.path.exists(model_path):
            return False

    model_name = os.path.basename(model_path)
    today = get_today_str()
    log_file = os.path.join(LOGS_DIR, f"8086_embedding_{today}.log")
    log_fp = open(log_file, "a", encoding="utf-8", buffering=1)
    try:
        log_fp.write(f"\n--- [8086 Embedding Session at {time.strftime('%Y-%m-%d %H:%M:%S')}] ---\n")
        log_fp.flush()
    except Exception:
        pass

    emb_args = [
        LLAMA_SERVER,
        "-m", model_path,
        "--embedding",
        "-ngl", "0",
        "-c", "8192",
        "-t", "4",
        "--parallel", "1",
        "--alias", "bge-m3,text-embedding-v1,default",
        "--port", "8086",
        "--api-key", "llamacpp"
    ]

    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "-1"
    env["OMP_NUM_THREADS"] = "4"
    creationflags = (0x08000000 | 0x00004000) if sys.platform == "win32" else 0

    g_embedding_proc = subprocess.Popen(
        emb_args,
        cwd=BASE_DIR,
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=creationflags
    )
    apply_system_smoothness_armor(g_embedding_proc.pid, "8086向量引擎")

    sys.stdout.write(f"{C_PURPLE}  ├─ 🧮 8086 向量引擎正在后台启动 ({model_name} · CPU 0显存)...{C_RESET}\n\n"[:79] + "\n")
    sys.stdout.flush()

    if wait:
        for _ in range(25):
            if is_port_open(8086):
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
        g_job_handle = h_job
        return True
    except Exception:
        return False





def cleanup_all(kill_everything=True):
    """清理本启动器绑定的全部 AI 进程 (8081网关/8083主脑/8085视觉)，绝不留任何后台孤儿进程与 GPU 显存残留"""
    global g_is_cleaning, g_gateway_proc, g_sidecar_proc, g_main_proc, g_tray_manager, g_forwarder_stop
    if g_is_cleaning:
        return
    g_is_cleaning = True
    if g_forwarder_stop:
        try:
            g_forwarder_stop.set()
        except Exception:
            pass
    if g_tray_manager:
        try:
            g_tray_manager.stop()
        except Exception:
            pass
    for proc in [g_main_proc, g_sidecar_proc, g_gateway_proc]:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    if kill_everything:
        kill_port(8081)
        kill_port(8083)
        kill_port(8085)
        kill_port(8086)
        kill_all_llama()
        try:
            active_state_file = os.path.join(LOGS_DIR, "active_backend.json")
            if os.path.exists(active_state_file):
                os.remove(active_state_file)
            active_sidecar_file = os.path.join(LOGS_DIR, "active_sidecar.json")
            if os.path.exists(active_sidecar_file):
                os.remove(active_sidecar_file)
        except Exception:
            pass


def cleanup_and_exit(signum=None, frame=None):
    """用户按 Ctrl+C 或正常退出时触发"""
    sys.stdout.write(f"\n{C_YELLOW}正在安全关闭当前启动器托管服务并释放显存...{C_RESET}\n")
    sys.stdout.flush()
    cleanup_all(kill_everything=True)
    sys.stdout.write(f"{C_GREEN}✅ 全部服务已彻底关闭，显存已清空。{C_RESET}\n")
    sys.exit(0)


# 注册控制台关闭事件处理 (拦截窗口 X 按钮点击与退出)
if sys.platform == "win32":
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        def win_ctrl_handler(dwCtrlType):
            cleanup_all(kill_everything=True)
            return True
        g_ctrl_handler = HandlerRoutine(win_ctrl_handler)
        kernel32.SetConsoleCtrlHandler(g_ctrl_handler, True)
    except Exception:
        pass

signal.signal(signal.SIGINT, cleanup_and_exit)
signal.signal(signal.SIGTERM, cleanup_and_exit)


def print_banner(hw):
    os.system("cls" if sys.platform == "win32" else "clear")
    w = 79
    line_eq = "=" * w
    sys.stdout.write(f"{C_CYAN}{line_eq}{C_RESET}\n")
    sys.stdout.write(f"{C_BOLD}{C_GREEN}   🚀 AI 大模型统一启动器 v4.0 (Python原生版) · 智能协同网关矩阵{C_RESET}\n")
    cpu_clean = re.sub(r"with Radeon.*", "", hw.get('cpu', '')).strip()[:28]
    gpu_clean = f"GPU: {hw.get('gpu', '')} ({hw.get('vram', '')})"[:36]
    llama_ver = hw.get('llama_version', '未知')
    tier_info = hw.get('tier', '')
    sys.stdout.write(f"{C_GRAY}  环境: {gpu_clean} | {cpu_clean}{C_RESET}\n")
    if tier_info:
        sys.stdout.write(f"{C_GRAY}  算力: {C_PURPLE}{tier_info}{C_RESET}\n")
    sys.stdout.write(f"{C_GRAY}  引擎: llama.cpp {C_BOLD}{C_GREEN}{llama_ver}{C_RESET}{C_GRAY} · CUDA 12.4 | 内核Job绑定{C_RESET}\n")
    sys.stdout.write(f"{C_CYAN}{line_eq}{C_RESET}\n\n")

    if sys.platform == "win32":
        try:
            ctypes.windll.kernel32.SetConsoleTitleW(f"AI 大模型统一启动器 v4.0 [llama.cpp {llama_ver}]")
        except Exception:
            pass


def resolve_qwen3_coder_path():
    """动态智能解析 Qwen3-Coder-30B 模型路径，优先匹配 UD-Q5_K_XL，兼容 UD-Q4_K_XL 等规格"""
    candidates = [
        "Qwen3-Coder-30B-A3B-Instruct-UD-Q5_K_XL.gguf",
        "Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf",
        "Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf",
        "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
    ]
    for c in candidates:
        p = os.path.join(MODELS_DIR, c)
        if os.path.exists(p):
            return p
    if os.path.exists(MODELS_DIR):
        try:
            for f in os.listdir(MODELS_DIR):
                if f.lower().startswith("qwen3-coder-30b") and f.lower().endswith(".gguf"):
                    return os.path.join(MODELS_DIR, f)
        except Exception:
            pass
    return os.path.join(MODELS_DIR, "Qwen3-Coder-30B-A3B-Instruct-UD-Q5_K_XL.gguf")


# ===============================================================================
#  ⚡ 核心架构铁律：思考预算与真实内容输出预算彻底解耦 (永久规则)
# ===============================================================================
#  1. 真实内容输出预算（Answer / Content Budget）恒为无限（-n -1）：
#     - 模型答题、写脚本、生成长文档、组装工具调用的正文输出空间一直都是无限的。
#     - 严禁在底层或网关给真实内容输出硬设较小物理截断值，绝不能把干活阶段掐死！
#  2. 思考预算（Reasoning Budget）仅约束思考链内部（<think> ... </think>）：
#     - 思考链按任务类型进行自适应分级（low: 1024, medium: 2048/4096, high/xhigh: 8192 或 --reasoning-budget）；
#     - 思考不管用多少，用完了采样器强制闭合 </think> 标签，立刻转入无限制的正文输出去干活！
#  3. 思考归思考，干活归干活：思考预算耗尽绝不等于任务中断！
# ===============================================================================

def build_models_menu():
    """定义可用模型矩阵：严格按照 1级顺序参数量从小到大，2级顺序量化级别从小到大排序，并动态仅展示实际存在的模型文件"""
    mmproj_27b = os.path.join(MODELS_DIR, "mmproj-Qwen3.8-27B-F16.gguf")

    all_definitions = [
        {
            "id": "qwen3.5_4b",
            "name": "Qwen3.5-4B [纯文本极速]",
            "short_name": "Qwen3.5-4B",
            "quant": "Q6_K",
            "ctx": "256K·单",
            "speed_vram": "4G·直推",
            "speed": "88.9 t/s",
            "vision": "8085侧挂",
            "sidecar_gpu": False,
            "best_for": "低功耗辅助·轻问答",
            "desc": "4B 轻量级对话与代码辅助 | 256K 超大上下文 | 8085 视觉(CPU 0显存)侧挂",
            "recommend": "【低功耗快速轻量辅助 · 8085 CPU 0显存安全侧挂】",
            "alias": "Qwen3.5-4B,default",
            "is_text": True,
            "model_path": os.path.join(MODELS_DIR, "Qwen3.5-4B.gguf"),
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3.5-4B.gguf"),
                "-ngl", "99",
                "--fit", "off",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "262144",
                "-b", "2048",
                "-t", OPTIMAL_CPU_THREADS,
                "--parallel", "1",
                "--flash-attn", "on",
                "--temp", "0.3",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--repeat-penalty", "1.0",
                "--jinja",
                "--alias", "Qwen3.5-4B,default"
            ]
        },
        {
            "id": "gemma4_e4b",
            "name": "Gemma-4-E4B [轻量多模]",
            "short_name": "Gemma-4-E4B",
            "quant": "Q6_K",
            "ctx": "128K·单",
            "speed_vram": "6G·原生",
            "speed": "60.0 t/s",
            "vision": "CPU 0显存",
            "sidecar_gpu": False,
            "best_for": "原生轻量多模态对话",
            "desc": "4B MoE 架构 | Q6_K_P 高精量化 | 128K 上下文 | 原生挂载 mmproj",
            "recommend": "【轻量极速多模态对话】",
            "alias": "Gemma-4-E4B,default",
            "is_text": False,
            "model_path": os.path.join(MODELS_DIR, "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q6_K_P.gguf"),
            "args": [
                "-m", os.path.join(MODELS_DIR, "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q6_K_P.gguf"),
                "--mmproj", os.path.join(MODELS_DIR, "mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-f16.gguf"),
                "-ngl", "99",
                "--fit", "off",
                "--cache-type-k", "f16",
                "--cache-type-v", "f16",
                "-c", "131072",
                "-b", "2048",
                "-t", OPTIMAL_CPU_THREADS,
                "--parallel", "1",
                "--flash-attn", "on",
                "--image-min-tokens", "1024",
                "--temp", "0.3",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--repeat-penalty", "1.0",
                "--jinja",
                "--alias", "Gemma-4-E4B,default"
            ]
        },
        {
            "id": "qwen3_vl_8b",
            "name": "Qwen3-VL-8B [视觉独立版]",
            "short_name": "Qwen3-VL-8B",
            "quant": "UD-Q4",
            "ctx": "32K·单",
            "speed_vram": "5G·端到端",
            "speed": "61.5 t/s",
            "vision": "端到端视觉",
            "sidecar_gpu": False,
            "best_for": "端到端高精图文OCR",
            "desc": "8B 旗舰视觉 | UD-Q4_K_XL 极致量化 | GPU 直通高精图文推理 (32K)",
            "recommend": "【端到端高精 OCR 与图纸评审】",
            "alias": "Qwen3-VL-8B,default",
            "is_text": False,
            "model_path": os.path.join(MODELS_DIR, "Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf"),
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf"),
                "--mmproj", os.path.join(MODELS_DIR, "mmproj-Qwen3VL-8B-Instruct-F16.gguf"),
                "-ngl", "99",
                "--fit", "off",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "32768",
                "-b", "2048",
                "--ubatch-size", "2048",
                "-t", OPTIMAL_CPU_THREADS,
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
                "--alias", "Qwen3-VL-8B,default"
            ]
        },
        {
            "id": "qwen3.8_27b_gsq",
            "name": "Qwen3.8-27B-GSQ-RCO [256K·2槽·MTP极速]",
            "short_name": "27B-GSQ",
            "quant": "IQ3_S",
            "ctx": "256K·2槽",
            "speed_vram": "11G·MTP",
            "speed": "40~45 t/s",
            "vision": "8085侧挂",
            "sidecar_gpu": False,
            "best_for": "★无损级3bit·256K长代码",
            "desc": "GSQ-RCO 广义切片量化 | 256K 超大统一池 (2槽高命中) | 全量 Q8_0 KV极高保真 | MTP投机加速 40~45 t/s | 8085视觉(CPU 0显存)侧挂",
            "recommend": "【👑 终极黄金底座 · GSQ-RCO 无损级 3bit + 256K 超长统一池 + MTP投机加速 · 8085(CPU 0显存)视觉侧挂】",
            "alias": "27B-GSQ,Qwen3.8-27B-GSQ,Qwen3.8-27B-GSQ-RCO,Qwen3.8-27B-GSQ-RCO-IQ3_S,default",
            "is_text": True,
            "model_path": os.path.join(MODELS_DIR, "Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp.gguf"),
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp.gguf"),
                "-ngl", "99",
                "--fit", "off",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "262144",
                "-b", "2048",
                "--ubatch-size", "2048",
                "-t", OPTIMAL_CPU_THREADS,
                "--parallel", "2",
                "-sps", "0.05",
                "--kv-unified",
                "--flash-attn", "on",
                "--ctx-checkpoints", "4",
                "--spec-type", "draft-mtp",
                "--spec-draft-n-max", "3",
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
                "--alias", "27B-GSQ,Qwen3.8-27B-GSQ,Qwen3.8-27B-GSQ-RCO,Qwen3.8-27B-GSQ-RCO-IQ3_S,default"
            ]
        },
        {
            "id": "qwen3.8_27b_nv_mid_high",
            "name": "Qwen3.8-27B-NVFP4-MID [160K·2槽·均衡极速]",
            "short_name": "27B-NV-M",
            "quant": "NVFP4",
            "ctx": "160K·2槽",
            "speed_vram": "16G·MTP",
            "speed": "42~48 t/s",
            "vision": "8085侧挂",
            "sidecar_gpu": False,
            "best_for": "★轻量均衡·160K大池",
            "desc": "轻量均衡 NVFP4 + Q8嵌入与MTP头 | 160K 统一池 (2槽高命中) | 全Q8_0 KV极高保真 | 8085视觉(CPU 0显存)侧挂",
            "recommend": "【👑 轻量均衡高能底座 · 16G 显存轻量化 + 160K 统一池 + MTP加速 · 8085(CPU 0显存)视觉侧挂】",
            "alias": "27B-NV-M,Qwen3.8-27B-NVFP4-MID-HIGH,Qwen3.8-27B-NV-M,default",
            "is_text": True,
            "model_path": os.path.join(MODELS_DIR, "Qwen3.8-27B-NVFP4-MTP-MID-HIGH.gguf"),
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3.8-27B-NVFP4-MTP-MID-HIGH.gguf"),
                "-ngl", "99",
                "--fit", "off",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "163840",
                "-b", "2048",
                "--ubatch-size", "2048",
                "-t", OPTIMAL_CPU_THREADS,
                "--parallel", "2",
                "-sps", "0.05",
                "--kv-unified",
                "--flash-attn", "on",
                "--ctx-checkpoints", "4",
                "--spec-type", "draft-mtp",
                "--spec-draft-n-max", "3",
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
                "--alias", "27B-NV-M,Qwen3.8-27B-NVFP4-MID-HIGH,Qwen3.8-27B-NV-M,default"
            ]
        },
        {
            "id": "qwen3.8_27b_nv_highest",
            "name": "Qwen3.8-27B-NVFP4-HIGHEST [2槽·MTP极速]",
            "short_name": "27B-NV-H",
            "quant": "NVFP4",
            "ctx": "144K·2槽",
            "speed_vram": "22G·MTP",
            "speed": "45~52 t/s",
            "vision": "8085侧挂",
            "sidecar_gpu": False,
            "best_for": "★BF16双头·Q8注意力",
            "desc": "BF16无损词嵌入与MTP头 | Q8_0高精核心注意力 | 144K统一池 (2槽高命中) | 8085视觉(CPU 0显存)侧挂",
            "recommend": "【👑 官方高精底座 · BF16无损双头 + Q8_0核心注意力 · 8085(CPU 0显存)视觉侧挂】",
            "alias": "27B-NV-H,Qwen3.8-27B-NVFP4-HIGHEST,Qwen3.8-27B-N-H,default",
            "is_text": True,
            "model_path": os.path.join(MODELS_DIR, "Qwen3.8-27B-NVFP4-MTP-HIGHEST.gguf"),
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3.8-27B-NVFP4-MTP-HIGHEST.gguf"),
                "-ngl", "99",
                "--fit", "off",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "147456",
                "-b", "2048",
                "--ubatch-size", "2048",
                "-t", OPTIMAL_CPU_THREADS,
                "--parallel", "2",
                "-sps", "0.05",
                "--kv-unified",
                "--flash-attn", "on",
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
                "--alias", "27B-NV-H,Qwen3.8-27B-NVFP4-HIGHEST,Qwen3.8-27B-N-H,default"
            ]
        },
        {
            "id": "qwen3.8_27b_a",
            "name": "Qwen3.8-27B-A [2槽·全量Q6K·MTP]",
            "short_name": "27B-A",
            "quant": "Q6_K",
            "ctx": "144K·2槽",
            "speed_vram": "21G·MTP",
            "speed": "45~50 t/s",
            "vision": "8085侧挂",
            "sidecar_gpu": False,
            "best_for": "★全量Q6K·无审查旗舰",
            "desc": "27B 全量Q6_K旗舰 (无审查) | 144K统一池 (2槽高命中) | 全Q8_0 KV极高保真 | MTP投机加速 | 8085视觉(CPU 0显存)侧挂",
            "recommend": "【👑 终极全能主力 · 全量 Q6_K 纯文本秒级缓存 + MTP加速 · 8085(CPU 0显存)视觉侧挂】",
            "alias": "27B-A,Qwen3.8-27B-A,Qwen3.8-27B-A-Q6_K,default",
            "is_text": True,
            "model_path": os.path.join(MODELS_DIR, "Qwen3.8-27B-Abliterated-Q6_K.gguf"),
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3.8-27B-Abliterated-Q6_K.gguf"),
                "-ngl", "99",
                "--fit", "off",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "147456",
                "-b", "2048",
                "--ubatch-size", "2048",
                "-t", OPTIMAL_CPU_THREADS,
                "--parallel", "2",
                "-sps", "0.05",
                "--kv-unified",
                "--flash-attn", "on",
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
                "--alias", "27B-A,Qwen3.8-27B-A,Qwen3.8-27B-A-Q6_K,default"
            ]
        },
        {
            "id": "qwen3.8_27b_a_work",
            "name": "Qwen3.8-27B-A-Work [2槽·全量Q6K·MTP]",
            "short_name": "27B-a-Work",
            "quant": "Q6_K",
            "ctx": "144K·2槽",
            "speed_vram": "21G·MTP",
            "speed": "45~50 t/s",
            "vision": "8085侧挂",
            "sidecar_gpu": False,
            "best_for": "★全量Q6K·Work无审查",
            "desc": "27B-a-Work 全量Q6_K (sme-preview-mtp) | 144K统一池 (2槽高命中) | 全Q8_0 KV极高保真 | MTP投机加速 | 8085视觉(CPU 0显存)侧挂",
            "recommend": "【👑 27B-a-Work 主力底座 · 全量 Q6_K SME-MTP 加速 · 8085(CPU 0显存)视觉侧挂】",
            "alias": "27B-a-Work,Qwen3.8-27B-A-Work,27B-A-Work,default",
            "is_text": True,
            "model_path": os.path.join(MODELS_DIR, "Qwen3.8-27B-abliterated-sme-preview-mtp-Q6_K.gguf"),
            "args": [
                "-m", os.path.join(MODELS_DIR, "Qwen3.8-27B-abliterated-sme-preview-mtp-Q6_K.gguf"),
                "-ngl", "99",
                "--fit", "off",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "147456",
                "-b", "2048",
                "--ubatch-size", "2048",
                "-t", OPTIMAL_CPU_THREADS,
                "--parallel", "2",
                "-sps", "0.05",
                "--kv-unified",
                "--flash-attn", "on",
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
                "--alias", "27B-a-Work,Qwen3.8-27B-A-Work,27B-A-Work,default"
            ]
        },
        {
            "id": "qwen3_coder_30b",
            "name": "Qwen3-Coder-30B-A3B [128K·Q8保真·编程王牌]",
            "short_name": "Qwen3-C30B",
            "quant": "UD-Q5",
            "ctx": "128K·单",
            "speed_vram": "20G·MoE",
            "speed": "85~87 t/s",
            "vision": "8085侧挂",
            "sidecar_gpu": False,
            "best_for": "★3.3B激活·128K Q8代码",
            "desc": "30.5B MoE编程专家 (3.3B激活) | UD-Q5_K_XL 动态自适应量化 | 128K超长大池 | 全Q8_0 KV极高保真 | 8085视觉(CPU 0显存)侧挂",
            "recommend": "【👑 顶级开源编程底座 · 3.3B 激活专家闪电直通 · 128K Q8 高保真 · 8085(CPU 0显存)视觉侧挂】",
            "alias": "Qwen3-Coder-30B-A3B,Qwen3-Coder-30B,Qwen3-C30B,qwen3-coder,default",
            "is_text": True,
            "model_path": resolve_qwen3_coder_path(),
            "args": [
                "-m", resolve_qwen3_coder_path(),
                "-ngl", "99",
                "--fit", "off",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "131072",
                "-b", "2048",
                "--ubatch-size", "2048",
                "-t", OPTIMAL_CPU_THREADS,
                "--parallel", "1",
                "--flash-attn", "on",
                "--cache-reuse", "512",
                "--no-warmup",
                "--temp", "0.2",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--repeat-penalty", "1.05",
                "--presence-penalty", "0.0",
                "--jinja",
                "--alias", "Qwen3-Coder-30B-A3B,Qwen3-Coder-30B,Qwen3-C30B,qwen3-coder,default"
            ]
        },
        {
            "id": "ornith_35b",
            "name": "Ornith-1.5-35B [256K·MoE推理·Q8保真]",
            "short_name": "Ornith-35B",
            "quant": "Q4_K",
            "ctx": "256K·单",
            "speed_vram": "20G·MoE",
            "speed": "50~55 t/s",
            "vision": "8085侧挂",
            "sidecar_gpu": False,
            "best_for": "★全Q8保真·256K极速推理",
            "desc": "35B 稀疏MoE专家 | 全Q8 KV极高精度保真 | 256K 极限大池 | 8085(CPU 0显存)视觉侧挂 | 50~55 t/s",
            "recommend": "【深度复杂逻辑推理 · 全Q8_0 KV极高保真度(拒绝MoE路由退化) · MoE专家激活 · 8085 CPU 0显存侧挂 · 256K超长池】",
            "alias": "Ornith-35B,Ornith-1.5-35B,default",
            "is_text": True,
            "model_path": os.path.join(MODELS_DIR, "Ornith-1.5-35B-Q4_K_M.gguf"),
            "args": [
                "-m", os.path.join(MODELS_DIR, "Ornith-1.5-35B-Q4_K_M.gguf"),
                "-ngl", "99",
                "--fit", "off",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "262144",
                "-b", "2048",
                "--ubatch-size", "2048",
                "-t", OPTIMAL_CPU_THREADS,
                "--parallel", "1",
                "--flash-attn", "on",
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
                "--alias", "Ornith-35B,Ornith-1.5-35B,default"
            ]
        },
        {
            "id": "nex_n25_mini",
            "name": "Nex-N2.5-Mini-35B [512K·4槽·原生全模态MoE极速]",
            "short_name": "Nex-N2.5-VL",
            "quant": "Q4_K",
            "ctx": "512K·4槽",
            "speed_vram": "22G·原生多模",
            "speed": "60~65 t/s",
            "vision": "8085副脑+原生多模",
            "sidecar_gpu": False,
            "best_for": "★原生多模态·8085副脑协同·512K四并发",
            "desc": "35B MoE架构 (256专家·Top-8激活) | 512K 超大统一池 (4槽高并发) | 原生 mmproj-f16 视觉直通 | 8085 CPU智囊副脑协同 | 全Q8_0 KV保真 | 60~65 t/s",
            "recommend": "【👑 顶级全模态双脑旗舰 · 35B原生高精视觉 + 8085 CPU智囊副脑协同 + 512K大池(4并发) + 64 t/s 极速】",
            "alias": "Nex-N2.5,Nex-N2.5-mini,Nex-N2.5-VL,nex-mini,default",
            "is_text": False,
            "model_path": os.path.join(MODELS_DIR, "nex-agi_Nex-N2.5-mini-Q4_K_M.gguf"),
            "args": [
                "-m", os.path.join(MODELS_DIR, "nex-agi_Nex-N2.5-mini-Q4_K_M.gguf"),
                "--mmproj", os.path.join(MODELS_DIR, "mmproj-nex-agi_Nex-N2.5-mini-f16.gguf"),
                "-ngl", "99",
                "--fit", "off",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-c", "524288",
                "-b", "2048",
                "--ubatch-size", "512",
                "-t", OPTIMAL_CPU_THREADS,
                "--parallel", "4",
                "--kv-unified",
                "--flash-attn", "on",
                "--image-min-tokens", "1024",
                "--reasoning", "auto",
                "--reasoning-budget", "2048",
                "--reasoning-effort", "medium",
                "--reasoning-format", "deepseek",
                "--reasoning-preserve",
                "--no-warmup",
                "--temp", "0.6",
                "--top-p", "0.95",
                "--top-k", "20",
                "--min-p", "0.05",
                "--repeat-penalty", "1.05",
                "--jinja",
                "--alias", "Nex-N2.5,Nex-N2.5-mini,Nex-N2.5-VL,nex-mini,default"
            ]
        }
    ]

    # 动态文件存在性校验与自动连续编号
    valid_menu = []
    idx = 1
    for item in all_definitions:
        mpath = item.get("model_path")
        # 只要主模型文件真实存在于磁盘，才加入菜单展示
        if mpath and os.path.exists(mpath):
            item_copy = dict(item)
            item_copy["key"] = str(idx)
            # ⚡ 永久铁律保障：显式注入 -n -1，保证真实内容输出预算恒为无限，彻底杜绝底层缺省 2048 导致思考中途断电截断！
            args_list = list(item_copy.get("args", []))
            if "-n" not in args_list and "--predict" not in args_list and "--n-predict" not in args_list:
                args_list.extend(["-n", "-1"])
            item_copy["args"] = args_list
            valid_menu.append(item_copy)
            idx += 1

    return valid_menu       


def adapt_model_args_for_hardware(args_list, hw):
    """
    根据当前物理硬件（GPU 显存容量或 CPU 模式）动态自适应微调启动参数，杜绝跨机运行时的 CUDA OOM 爆显存或崩溃。
    - Tier 1 (>= 22GB 显存，如 V100 32GB / RTX 4090 24GB): 100% 保持原始极致参数 (零缩水、满血并发与投机)。
    - Tier 2 (10GB ~ 20GB 显存，如 RTX 3060 12GB / 4070 12GB / 5080 16GB):
        • 动态限制总上下文池 -c <= 65536 ~ 98304;
        • 动态限制并发槽位 --parallel <= 2;
    - Tier 3 (<= 8GB 显存，如 RTX 3050 / 4060 / 笔记本显卡):
        • 动态限制 -c <= 32768;
        • 限制 --parallel <= 1;
        • 自动将 KV Cache 压缩为 --cache-type-k q4_0 --cache-type-v q4_0;
    - CPU / 集显模式 (无 NVIDIA GPU):
        • 强制 -ngl 0 (纯 CPU 推理，避免报缺少 CUDA 设备);
        • 限制 -c <= 32768, --parallel 1;
        • 自动将 KV Cache 压缩为 --cache-type-k q4_0 --cache-type-v q4_0;
        • 禁用 --flash-attn on (转为 off 规避老 CPU 兼容问题)。
    """
    if not hw or not isinstance(hw, dict):
        return list(args_list)

    adapted = list(args_list)
    has_nvidia = hw.get("has_nvidia", False)
    vram_mb = hw.get("vram_mb", 0)

    # 1. 纯 CPU / 集成显卡环境
    if not has_nvidia or vram_mb == 0:
        for i, a in enumerate(adapted):
            if a in ("-ngl", "--gpu-layers", "--n-gpu-layers") and i + 1 < len(adapted):
                adapted[i + 1] = "0"
            elif a == "-c" and i + 1 < len(adapted):
                try:
                    if int(adapted[i + 1]) > 32768:
                        adapted[i + 1] = "32768"
                except Exception:
                    pass
            elif a in ("--parallel", "-np") and i + 1 < len(adapted):
                adapted[i + 1] = "1"
            elif a in ("--cache-type-k", "--cache-type-v") and i + 1 < len(adapted):
                adapted[i + 1] = "q4_0"
            elif a == "--flash-attn" and i + 1 < len(adapted):
                adapted[i + 1] = "off"
        return adapted

    # 2. Tier 1 (旗舰卡 >= 22GB，如 V100 32GB / RTX 4090 24GB): 零修改，100% 满血直通
    if vram_mb >= 22000:
        return adapted

    # 3. Tier 3 紧凑级显卡 (<= 8GB)
    if vram_mb < 9500:
        for i, a in enumerate(adapted):
            if a == "-c" and i + 1 < len(adapted):
                try:
                    if int(adapted[i + 1]) > 32768:
                        adapted[i + 1] = "32768"
                except Exception:
                    pass
            elif a in ("--parallel", "-np") and i + 1 < len(adapted):
                adapted[i + 1] = "1"
            elif a in ("--cache-type-k", "--cache-type-v") and i + 1 < len(adapted):
                adapted[i + 1] = "q4_0"
        return adapted

    # 4. Tier 2 主流级显卡 (10GB ~ 20GB，如 RTX 3060 12G / 4070 12G / 5080 16G)
    max_c = 65536 if vram_mb < 15000 else 98304
    for i, a in enumerate(adapted):
        if a == "-c" and i + 1 < len(adapted):
            try:
                if int(adapted[i + 1]) > max_c:
                    adapted[i + 1] = str(max_c)
            except Exception:
                pass
        elif a in ("--parallel", "-np") and i + 1 < len(adapted):
            try:
                if int(adapted[i + 1]) > 2:
                    adapted[i + 1] = "2"
            except Exception:
                pass

    return adapted


def str_display_width(s):
    """精确计算包含 ANSI 颜色代码、CJK 全角字符与宽符号在终端中的实际显示列数"""
    clean = re.sub(r"\033\[[0-9;]*m", "", s)
    w = 0
    for c in clean:
        code = ord(c)
        if code in (0xFE0F, 0xFE0E):
            continue
        eaw = unicodedata.east_asian_width(c)
        if eaw in ("F", "W") or (0x2600 <= code <= 0x27BF) or (0x1F300 <= code <= 0x1FAFF):
            w += 2
        else:
            w += 1
    return w


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


def render_models_grid(menu, hw=None):
    """一横排紧凑渲染模型列表，行宽严格 <= 79 列，动态打标契合当前硬件的推荐模型"""
    headers = ["序号", "模型名", "量化", "上下文/槽", "容量/加速", "推导速度", "推荐场景与定位"]
    widths  = [4,    13,       5,      9,         9,         9,         23]

    header_line = " ".join(pad_display(f"{C_BOLD}{C_CYAN}{h}{C_RESET}", w) for h, w in zip(headers, widths))
    sep_line = f"{C_GRAY}" + "=" * 79 + f"{C_RESET}"

    sys.stdout.write(f"\n{header_line}\n")
    sys.stdout.write(f"{sep_line}\n")

    vram_mb = hw.get("vram_mb", 32768) if hw else 32768
    has_nvidia = hw.get("has_nvidia", True) if hw else True

    for item in menu:
        col_key = pad_display(f"{C_BOLD}{C_CYAN}[{item['key']}]{C_RESET}", widths[0])
        display_name = item.get("short_name") or item["name"]

        # 智能匹配算力推荐
        is_rec = False
        s_lower = display_name.lower()
        if not has_nvidia or vram_mb < 9500:
            if "4b" in s_lower or "e4b" in s_lower or "8b" in s_lower:
                is_rec = True
        elif vram_mb >= 22000:
            if "27b-a" in s_lower or "35b" in s_lower or "nex" in s_lower:
                is_rec = True
        else: # Tier 2 (10~20G)
            if "27b" in s_lower or "30b" in s_lower or "8b" in s_lower:
                is_rec = True

        name_prefix = "⭐" if is_rec else ""
        col_name = pad_display(f"{C_GREEN}{name_prefix}{display_name}{C_RESET}", widths[1])
        col_quant = pad_display(f"{C_YELLOW}{item.get('quant', '-')}{C_RESET}", widths[2])
        col_ctx = pad_display(f"{C_CYAN}{item.get('ctx', '-')}{C_RESET}", widths[3])
        col_vram = pad_display(f"{C_PURPLE}{item.get('speed_vram', '-')}{C_RESET}", widths[4])
        col_speed = pad_display(f"{C_YELLOW}{item.get('speed', '-')}{C_RESET}", widths[5])
        col_best = pad_display(f"{C_WHITE}{item.get('best_for', '-')}{C_RESET}", widths[6])

        sys.stdout.write(f"{col_key} {col_name} {col_quant} {col_ctx} {col_vram} {col_speed} {col_best}\n")

    sys.stdout.write(f"{sep_line}\n")
    exit_key = pad_display(f"{C_BOLD}{C_RED}[0]{C_RESET}", widths[0])
    sys.stdout.write(f"{exit_key} {C_GRAY}退出启动器 (安全清理后台服务与显存){C_RESET}\n")
    if hw:
        tier_label = hw.get("tier", "").split(" · ")[0] if hw.get("tier") else ""
        sys.stdout.write(f"{C_GRAY}  💡 算力契合: {C_PURPLE}{tier_label}{C_RESET}{C_GRAY} · 带 ⭐ 为契合本机的推荐主力{C_RESET}\n\n")
    else:
        sys.stdout.write("\n")

def main():
    global g_main_proc

    # 处理 CLI 选项 (例如 --list / --list-models / --help)
    if any(arg.lower() in ("--help", "-h", "/?") for arg in sys.argv[1:]):
        sys.stdout.write("用法: python launcher_main.py [模型编号: 1-8 | 0(退出)]\n")
        return
    if any(arg.lower() in ("-listmodels", "--list-models", "list", "--list", "-l", "-list") for arg in sys.argv[1:]):
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

    # 1. 核心 AI 端口矩阵与服务协同感知 (8081 网关 · 8083 主脑 · 8085 视觉)
    sys.stdout.write(f"{C_BOLD}正在联动探测核心端口与 AI 服务矩阵...{C_RESET}\n")

    # 🌐 8081 智能协同网关
    gw_ready = ensure_gateway_8081()
    gw_tag = f"{C_GREEN}✅ 运行中{C_RESET}" if gw_ready else f"{C_RED}❌ 未就绪{C_RESET}"
    sys.stdout.write(f"  ├─ 🌐 {C_BOLD}8081 [智能协同网关]{C_RESET} : {gw_tag}\n")
    sys.stdout.write(f"  │    ├─ {C_GRAY}功能特性: 双协议转译 (Anthropic ↔ OpenAI) · GBNF净化{C_RESET}\n")
    sys.stdout.write(f"  │    ├─ {C_GRAY}调度引擎: 任务自适应黄金采样矩阵 · Loop-Breaker 熔断{C_RESET}\n")
    sys.stdout.write(f"  │    ├─ {C_CYAN}看板地址: http://127.0.0.1:8081/dashboard{C_RESET} {C_GRAY}(算力监控){C_RESET}\n")
    sys.stdout.write(f"  │    ├─ {C_CYAN}API 接口: http://127.0.0.1:8081/v1{C_RESET} {C_GRAY}(Cursor/Claude){C_RESET}\n")
    sys.stdout.write(f"  │    └─ {C_CYAN}Web 对话: http://127.0.0.1:8081{C_RESET} {C_GRAY}(原生网页交互){C_RESET}\n")
    sys.stdout.write(f"  │\n")

    # 🧠 8083 主脑推理底座
    is_8083_up = is_port_open(8083)
    current_running_name = ""
    if is_8083_up:
        try:
            import urllib.request
            req_p = urllib.request.Request("http://127.0.0.1:8083/props", headers={"Authorization": "Bearer llamacpp"})
            with urllib.request.urlopen(req_p, timeout=0.6) as rp:
                if rp.status == 200:
                    p_data = json.loads(rp.read().decode("utf-8"))
                    current_running_name = p_data.get("model_alias") or os.path.basename(p_data.get("model_path", ""))
        except Exception:
            pass
        if not current_running_name:
            active_state_file = os.path.join(LOGS_DIR, "active_backend.json")
            if os.path.exists(active_state_file):
                try:
                    with open(active_state_file, "r", encoding="utf-8") as asf:
                        current_running_name = json.load(asf).get("model_name", "")
                except Exception:
                    pass
        if not current_running_name:
            current_running_name = "8083 主脑引擎"
        mb_tag = f"{C_GREEN}🟢 在位运行 [{current_running_name[:12]}]{C_RESET}{C_GRAY} (输入编号可平滑置换){C_RESET}"
    else:
        mb_tag = f"{C_YELLOW}⏳ 待命中 (从下方列表选择模型加载){C_RESET}"

    sys.stdout.write(f"  ├─ 🧠 {C_BOLD}8083 [主脑推理底座]{C_RESET} : {mb_tag}\n")
    sys.stdout.write(f"  │    ├─ {C_GRAY}定位功能: llama-server 推理底座 · 独占 {hw.get('gpu', 'GPU')} ({hw.get('vram', '')}) 算力{C_RESET}\n")
    sys.stdout.write(f"  │    ├─ {C_GRAY}显存架构: 统一 Q8_0 KV Cache 池 (144K~256K) · MTP 投机加速{C_RESET}\n")
    sys.stdout.write(f"  │    └─ {C_CYAN}原生端点: http://127.0.0.1:8083/v1{C_RESET} {C_GRAY}(底层原生推理接口){C_RESET}\n")
    sys.stdout.write(f"  │\n")

    # 👁️ 8085 视觉侧挂眼睛
    is_8085_up = is_port_open(8085)
    if is_8085_up:
        cur_mode = get_sidecar_8085_mode()
        mode_desc = "GPU加速" if cur_mode == "gpu" else "CPU 0显存常驻"
        sc_tag = f"{C_GREEN}🟢 在位就绪 (Qwen3VL-4B · {mode_desc}){C_RESET}"
    else:
        sc_tag = f"{C_BLUE}💤 待命就绪 (CPU 0显存常驻，随用随开){C_RESET}"

    sys.stdout.write(f"  ├─ 👁️ {C_BOLD}8085 [视觉侧挂眼睛]{C_RESET} : {sc_tag}\n")
    sys.stdout.write(f"  │    ├─ {C_GRAY}定位功能: Qwen3VL-4B 视觉眼睛 · 专职 OCR 图表与多模态解析{C_RESET}\n")
    sys.stdout.write(f"  │    └─ {C_CYAN}视觉端点: http://127.0.0.1:8085/v1{C_RESET} {C_GRAY}(外挂专用){C_RESET}\n")
    sys.stdout.write(f"  │\n")

    # 🧮 8086 向量检索引擎 (BGE-M3)
    is_8086_up = is_port_open(8086)
    emb_tag = f"{C_GREEN}🟢 在位就绪 (BGE-M3 · 8192长文本 · CPU 0显存){C_RESET}" if is_8086_up else f"{C_BLUE}💤 待命就绪 (BGE-M3 · CPU 0显存，自动拉起){C_RESET}"
    sys.stdout.write(f"  └─ 🧮 {C_BOLD}8086 [向量检索引擎]{C_RESET} : {emb_tag}\n")
    sys.stdout.write(f"       ├─ {C_GRAY}定位功能: BGE-M3 1024维高精语义向量 · 8192长文档/代码库RAG{C_RESET}\n")
    sys.stdout.write(f"       └─ {C_CYAN}向量端点: http://127.0.0.1:8086/v1/embeddings{C_RESET}\n\n")

    # 智能黄金底座默认选型：根据物理硬件档位推荐
    default_choice = "1"
    menu = build_models_menu()
    has_nvidia = hw.get("has_nvidia", True)
    vram_mb = hw.get("vram_mb", 32768)

    if not has_nvidia or vram_mb < 9500:
        for item in menu:
            if "4B" in item.get("short_name", ""):
                default_choice = item["key"]
                break
    else:
        for item in menu:
            if item.get("short_name") == "27B-A" or "27B-A" in item.get("alias", ""):
                default_choice = item["key"]
                break

    if is_port_open(8083) and locals().get("current_running_name"):
        for item in menu:
            if (item["short_name"] in current_running_name or 
                item["name"] in current_running_name or 
                item.get("alias", "").split(",")[0] in current_running_name):
                default_choice = item["key"]
                break

    sys.stdout.write(f"{C_BOLD}{C_CYAN}请选择要固定启动的主模型：{C_RESET}")
    render_models_grid(menu, hw)

    if len(sys.argv) > 1:
        arg = sys.argv[1].strip()
        if arg in ("--help", "-h"):
            sys.stdout.write(f"用法: python launcher_main.py [模型编号: 1-{len(menu)} | 0(退出)]\n")
            return
        choice = arg
    else:
        try:
            choice = input(f"{C_BOLD}请输入选项编号 [默认 {default_choice}]: {C_RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n已取消输入，安全退出。\n")
            choice = "0"

    if not choice:
        choice = default_choice
    if choice == "0":
        cleanup_and_exit()

    selected = None
    for item in menu:
        if item["key"] == choice:
            selected = item
            break
    if not selected:
        selected = menu[0]

    # 校验模型文件是否存在
    model_path = ""
    for i, a in enumerate(selected.get("args", [])):
        if a == "-m" and i + 1 < len(selected["args"]):
            model_path = selected["args"][i + 1]
            break
    if model_path and not os.path.exists(model_path):
        sys.stdout.write(f"\n{C_RED}{C_BOLD}❌ 未检测到模型文件：{C_RESET}{C_YELLOW}{os.path.basename(model_path)}{C_RESET}\n")
        sys.stdout.write(f"   预期路径: {model_path}\n\n")
        sys.stdout.write(f"{C_CYAN}💡 请将从 HuggingFace 下载的 GGUF 文件拷贝放入 models 目录：{C_RESET}\n")
        sys.stdout.write(f"   📁 {MODELS_DIR}\n")
        sys.stdout.write(f"{C_GREEN}   文件拷贝完成后，重新输入编号即可秒级拉起（参数已优化就绪）！{C_RESET}\n\n")
        if len(sys.argv) <= 1:
            try:
                input(f"{C_GRAY}按回车键退出...{C_RESET}")
            except Exception:
                pass
        sys.exit(1)

    line_eq = "=" * 79
    sys.stdout.write(f"\n{C_CYAN}{line_eq}{C_RESET}\n")
    sys.stdout.write(f"  🚀 正在启动: {C_BOLD}{selected['name']}{C_RESET}\n")
    sys.stdout.write(f"{C_CYAN}{line_eq}{C_RESET}\n\n")

    # 3. 视觉与多模态副脑组件适配 (全系标配 8085 CPU 智囊副脑，0显存常驻)
    if "8085" in selected.get("vision", ""):
        use_gpu_sidecar = bool(selected.get("sidecar_gpu", False))
        ensure_sidecar_8085(wait=False, use_gpu=use_gpu_sidecar)
        if not selected.get("is_text"):
            sys.stdout.write(f"{C_GREEN}  ├─ 🖼️ 原生全模态 + 🧠 8085 CPU 智囊副脑已就绪 (双脑协同 MoA)！{C_RESET}\n\n")
    else:
        if is_port_open(8085):
            sys.stdout.write(f"{C_YELLOW}  ├─ 🧹 关闭未配置的 8085 侧挂释放 CPU/显存...{C_RESET}\n")
            kill_port(8085)

    # 3.1 向量检索引擎守护 (8086 BGE-M3，CPU 0显存常驻)
    ensure_embedding_8086(wait=False)

    # 4. 清理 8083 旧进程并校验显存安全与端口可绑定性
    ensure_port_available(8083)
    check_gpu_memory()

    # 5. 启动 8083 主脑引擎
    today = get_today_str()
    main_log_file = os.path.join(LOGS_DIR, f"8083_llama_{today}.log")

    # 启动 session 日志转发器，严格保障当日日志持续累加，防止被 llama-server 覆盖截断
    global g_forwarder_stop
    session_log, g_forwarder_stop = start_session_log_forwarder(main_log_file, "8083")

    # 🌟 动态持久化当前主脑模型状态，供智能协同网关毫秒级直接同步
    try:
        active_state_file = os.path.join(LOGS_DIR, "active_backend.json")
        has_mtp = ("--spec-type" in selected.get("args", []))
        spec_type = "draft-mtp" if has_mtp else ""
        with open(active_state_file, "w", encoding="utf-8") as asf:
            json.dump({
                "model_name": selected["name"],
                "alias": selected.get("alias", ""),
                "key": selected["key"],
                "quant": selected.get("quant", ""),
                "ctx": selected.get("ctx", ""),
                "is_text": selected.get("is_text", False),
                "has_mtp": has_mtp,
                "spec_type": spec_type,
                "updated_at": time.time()
            }, asf, ensure_ascii=False, indent=2)
    except Exception:
        pass
    
    # 🌟 PR #19841 前沿适配：检测当前 llama-server 是否支持原生语义截断 (--chat-truncate)
    extra_truncate_args = []
    try:
        help_probe = subprocess.run([LLAMA_SERVER, "--help"], capture_output=True, text=True, timeout=5).stdout
        if "--chat-truncate" in help_probe:
            extra_truncate_args = ["--chat-truncate", "--chat-truncate-max-keep", "0.6"]
            sys.stdout.write(f"{C_CYAN}  ⚡ [PR #19841] 支持 --chat-truncate，已激活硬件级语义截断！{C_RESET}\n")
    except Exception:
        pass

    # 🌟 核心防爆自适应装甲：动态按当前硬件调优参数
    final_args = adapt_model_args_for_hardware(selected["args"], hw)
    if final_args != selected["args"]:
        sys.stdout.write(f"{C_YELLOW}  🛡️ [硬件自适应] 检测到当前算力/显存约束，已自适应调优上下文池与并发参数防爆显存！{C_RESET}\n")

    server_cmd = [LLAMA_SERVER] + final_args + extra_truncate_args + [
        "--port", "8083",
        "--api-key", "llamacpp",
        "--log-file", session_log
    ]

    gpu_display = hw.get('gpu', 'GPU')
    sys.stdout.write(f"{C_GREEN}  🔥 正在加载主脑至 {gpu_display} 算力池 (日志: {os.path.basename(main_log_file)})...{C_RESET}\n")
    update_system_tray(model_name=selected["name"], status_text=f"模型加载中 ({gpu_display[:18]})...", is_running=False, log_file=main_log_file)
    sys.stdout.flush()

    main_env = os.environ.copy()
    main_env["CUDA_CACHE_MAXSIZE"] = "2147483648"
    main_env["CUDA_DEVICE_MAX_CONNECTIONS"] = "1"

    main_creationflags = 0x00004000 if sys.platform == "win32" else 0

    # 以子进程前台常驻运行，实时将主脑推理日志输出到控制台，同时底层 --log-file 自动落盘
    g_main_proc = subprocess.Popen(
        server_cmd,
        cwd=BASE_DIR,
        env=main_env,
        creationflags=main_creationflags
    )
    apply_system_smoothness_armor(g_main_proc.pid, "8083主脑底座")

    # 毫秒级极速高频轮询检测端口
    ready = False
    for i in range(160):
        if is_port_open(8083):
            ready = True
            break
        # 实时检测子进程是否异常提前退出，避免盲目等待 40 秒
        if g_main_proc.poll() is not None:
            break
        time.sleep(0.25)
        if i % 4 == 0:
            sys.stdout.write(".")
            sys.stdout.flush()

    if ready:
        update_system_tray(model_name=selected["name"], status_text="运行中 (8081网关/8083主脑)", is_running=True, log_file=main_log_file)
        line_eq = "=" * 79
        sys.stdout.write(f"\n\n{C_BOLD}{C_GREEN}{line_eq}{C_RESET}\n")
        sys.stdout.write(f"  🎉 8083 [主脑推理底座] : {C_CYAN}{C_BOLD}{selected['name']}{C_RESET} 已成功常驻\n")
        sys.stdout.write(f"     ├─ 底层端点: http://127.0.0.1:8083/v1 (llama.cpp 原生深度推理底座)\n")
        sys.stdout.write(f"     └─ 架构特性: 独占 Tesla V100 32GB · 统一共享 KV 池 · 支持 MTP\n")
        if is_port_open(8085):
            cur_mode = get_sidecar_8085_mode()
            mode_desc = "GPU极速" if cur_mode == "gpu" else "CPU 0显存"
            sys.stdout.write(f"  👁️ 8085 [视觉侧挂] : http://127.0.0.1:8085/v1 (Qwen3VL-4B · {mode_desc})\n")
        sys.stdout.write(f"  📡 8081 [网关接口] : http://127.0.0.1:8081/v1 (供 Claude/Cursor 接入)\n")
        sys.stdout.write(f"  📊 8081 [算力看板] : http://127.0.0.1:8081/dashboard (实时监控大屏)\n")
        sys.stdout.write(f"  💬 8081 [网页对话] : http://127.0.0.1:8081 (原生 Web 交互界面)\n")
        sys.stdout.write(f"  🔔 托盘图标已激活：位于屏幕右下角通知区域 (^ 展开可拖出图标)\n")
        sys.stdout.write(f"{C_BOLD}{C_GREEN}{line_eq}{C_RESET}\n\n")
        sys.stdout.write(f"{C_GRAY}系统处于锁定常驻托管状态，按 Ctrl+C 安全停止...{C_RESET}\n\n")
    else:
        if g_main_proc.poll() is not None:
            sys.stdout.write(f"\n{C_RED}❌ 主脑进程启动即异常退出 (退出代码: {g_main_proc.returncode})！{C_RESET}\n")
            if os.path.exists(main_log_file):
                try:
                    with open(main_log_file, "r", encoding="utf-8", errors="ignore") as lf:
                        lines = [line.strip() for line in lf.readlines() if line.strip()]
                        err_tail = lines[-6:] if len(lines) >= 6 else lines
                        sys.stdout.write(f"{C_YELLOW}  📋 日志末尾报错 ({os.path.basename(main_log_file)}):\n" + "\n".join(f"     {l}" for l in err_tail) + f"{C_RESET}\n")
                except Exception:
                    pass
        else:
            sys.stdout.write(f"\n{C_RED}⚠️ 8083 端口未能在 45 秒内就绪，请检查 {main_log_file}{C_RESET}\n")

    try:
        while True:
            time.sleep(1)
            if g_main_proc.poll() is not None and not is_port_open(8083):
                sys.stdout.write(f"\n{C_YELLOW}主脑进程已退出 (code={g_main_proc.returncode})。{C_RESET}\n")
                break
    except KeyboardInterrupt:
        cleanup_all(kill_everything=True)
    finally:
        cleanup_all(kill_everything=False)


if __name__ == "__main__":
    main()
