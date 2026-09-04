@echo off
chcp 65001 >nul
title AI 大模型启动器 v3.5 · Python 原生版

if not exist "%~dp0logs" mkdir "%~dp0logs"

:: CUDA 运行时优化：2GB JIT 编译缓存 + 专用硬件调度流
set "CUDA_CACHE_MAXSIZE=2147483648"
set "CUDA_DEVICE_MAX_CONNECTIONS=1"

:: 直接调用 Python 原生启动器 (告别 PowerShell 编码与缓冲延迟)
"C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0launcher_main.py"

echo.
echo 服务已安全退出，按任意键关闭窗口...
pause >nul
