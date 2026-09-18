# -*- coding: utf-8 -*-
"""
AI 启动中心 - 系统托盘状态窗口 (System Tray)
功能：
 1. 在 Windows 任务栏右下角常驻高辨识度 AI 图标与气泡提示。
 2. 状态提示：鼠标悬停显示当前活跃模型、运行状态与端口。
 3. 右键菜单：
    - 🌐 打开网关网页看板 (http://127.0.0.1:8081)
    - 💬 打开原生聊天页面 (http://127.0.0.1:8083)
    - 📋 复制 API 地址 (http://127.0.0.1:8081/v1)
    - 🛑 结束一切并退出（联动强杀 llama-server、关闭网关、退出启动器）
"""

import threading
import webbrowser
import os
import subprocess
from typing import Optional, Callable

try:
    import pystray
    from PIL import Image, ImageDraw
    HAVE_TRAY = True
except ImportError:
    HAVE_TRAY = False

_tray_icon: Optional[pystray.Icon] = None
_on_exit_callback: Optional[Callable] = None
_current_model_name: str = "未加载"

def _create_tray_image(width=64, height=64, active=True):
    """绘制高质感圆形发光 AI 托盘图标"""
    img = Image.new("RGBA", (width, height), color=(0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 外圈柔光底色
    outer_color = (0, 240, 255, 230) if active else (100, 116, 139, 200)
    inner_color = (11, 15, 25, 245)
    text_color = (0, 240, 255) if active else (148, 163, 184)

    # 绘制外发光圆环
    d.ellipse((4, 4, width - 5, height - 5), fill=outer_color)
    d.ellipse((8, 8, width - 9, height - 9), fill=inner_color)
    # 中心点缀小芯片核心
    d.rounded_rectangle((19, 19, width - 20, height - 20), radius=4, fill=(0, 40, 60))
    # 绘制 'AI' 字母
    d.text((21, 17), "AI", fill=text_color)
    return img

def _open_dashboard(icon, item):
    webbrowser.open("http://127.0.0.1:8081/dashboard")

def _open_chat(icon, item):
    webbrowser.open("http://127.0.0.1:8081/")

def _open_billing(icon, item):
    webbrowser.open("http://127.0.0.1:8081/api/gateway/billing?key=llamacpp")

def _copy_key(icon, item):
    try:
        cmd = 'Set-Clipboard -Value "llamacpp"'
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True)
    except Exception:
        pass

def _copy_api(icon, item):
    api_url = "http://127.0.0.1:8081/v1"
    try:
        cmd = f'Set-Clipboard -Value "{api_url}"'
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True)
    except Exception:
        pass

def _exit_everything(icon, item):
    """右键点击快速结束一切"""
    global _tray_icon, _on_exit_callback
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception:
            pass
        _tray_icon = None

    if _on_exit_callback:
        # 执行外部退出强杀回调
        try:
            _on_exit_callback()
        except Exception:
            pass

    # 强行自杀并清理所有相关进程
    try:
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)
    except Exception:
        pass
    os._exit(0)

def start_tray(model_name: str = "就绪待命", on_exit: Optional[Callable] = None):
    """启动托盘图标线程"""
    global _tray_icon, _on_exit_callback, _current_model_name
    if not HAVE_TRAY:
        return None

    _current_model_name = model_name
    _on_exit_callback = on_exit

    if _tray_icon is not None:
        try:
            _tray_icon.title = f"AI 大模型中心 | {model_name} (8081网关)"
        except Exception:
            pass
        return _tray_icon

    menu = pystray.Menu(
        pystray.MenuItem("💬 打开原生对话 WebUI (8081)", _open_chat, default=True),
        pystray.MenuItem("🌐 打开智能网关监控看板 (8081/dashboard)", _open_dashboard),
        pystray.MenuItem("📊 查看 DeepSeek 闲时计费自检", _open_billing),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🔑 复制默认 API Key (llamacpp)", _copy_key),
        pystray.MenuItem("📋 复制 OpenAI API 接口地址 (8081/v1)", _copy_api),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda text: f"🟢 当前模型: {_current_model_name}", lambda i, item: None, enabled=False),
        pystray.MenuItem("🔑 统一 Key: llamacpp (免配置)", lambda i, item: None, enabled=False),
        pystray.MenuItem("🛡️ 防爆剪枝: 85%动态 / 视觉: 8085", lambda i, item: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🛑 快速结束一切并退出", _exit_everything)
    )

    image = _create_tray_image(active=True)
    _tray_icon = pystray.Icon(
        "ai_launcher_tray",
        image,
        f"AI 大模型中心 | {model_name} (8081网关)",
        menu
    )

    # 在守护线程中异步运行托盘事件循环
    _tray_icon.run_detached()
    return _tray_icon

def update_tray_status(model_name: str):
    """更新托盘提示文案"""
    global _tray_icon, _current_model_name
    _current_model_name = model_name
    if _tray_icon:
        try:
            _tray_icon.title = f"AI 大模型中心 | {model_name} (8081网关)"
        except Exception:
            pass

def stop_tray():
    """停止并移除托盘图标"""
    global _tray_icon
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception:
            pass
        _tray_icon = None
