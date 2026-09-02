@echo off
chcp 65001 >nul
title AI Model Launcher v3 · Artificial Analysis

if not exist "%~dp0logs" mkdir "%~dp0logs"

:: CUDA 运行时优化（2GB JIT 缓存 + 专用硬件调度流）
set "CUDA_CACHE_MAXSIZE=2147483648"
set "CUDA_DEVICE_MAX_CONNECTIONS=1"

for /f %%I in ('powershell -NoProfile -Command "(Get-Date).ToString('yyyyMMdd')"') do set "dt=%%I"
if "%dt%"=="" set "dt=%DATE:~-4%%DATE:~3,2%%DATE:~0,2%"
set "DAILY_LOG=%~dp0logs\8083_llama_%dt%.log"

echo ====================================================================================
echo   AI 大模型启动器 v3  ·  智能协同网关与服务加载中...
echo ====================================================================================
echo   [网关服务] 8081 智能协同网关启动中 (http://127.0.0.1:8081/v1)
echo   [算力大屏] http://127.0.0.1:8081/dashboard (实时用量监控 · 虚拟计费 · CC Switch)
echo   [评测基准] 全面接入 Artificial Analysis (AA) 权威人工智能分析指数 v4.1.1
echo   [日志路径] %DAILY_LOG%
echo ====================================================================================
echo.

:: 检查并立即拉起 8081 智能协同网关后台常驻服务
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8081" ^| findstr "LISTENING"') do set GATEWAY_PID=%%a
if "%GATEWAY_PID%"=="" (
    start "" /B "C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0qwen_tool_proxy.py" --listen 8081 --target 8083 --vision-main 8085 --api-key llamacpp >> "%~dp0logs\8081_proxy_%dt%.log" 2>&1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher_main.ps1" -DailyLogFile "%DAILY_LOG%"
echo.
echo 服务已退出。按任意键关闭窗口...
pause >nul
