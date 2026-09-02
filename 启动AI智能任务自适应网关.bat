@echo off
chcp 65001 >nul
title AI 智能任务自适应网关 · Unified 27B Flagship Gateway v5.0

if not exist "%~dp0logs" mkdir "%~dp0logs"

:: CUDA 运行时性能优化 (2GB JIT 编译缓存 + 硬件调度流锁)
set "CUDA_CACHE_MAXSIZE=2147483648"
set "CUDA_DEVICE_MAX_CONNECTIONS=1"

for /f %%I in ('powershell -NoProfile -Command "(Get-Date).ToString('yyyyMMdd')"') do set "dt=%%I"
if "%dt%"=="" set "dt=%DATE:~-4%%DATE:~3,2%%DATE:~0,2%"
set "DAILY_LOG=%~dp0logs\8083_llama_%dt%.log"

echo ====================================================================================
echo   🤖 AI 智能任务自适应网关 · Unified 27B Flagship Gateway v5.0
echo ====================================================================================
echo   [网关统一入口] http://127.0.0.1:8081/v1 (全应用统一接入点)
echo   [实时算力大屏] http://127.0.0.1:8081/dashboard (动态槽位 · 瞬时吞吐 · 虚拟账本)
echo   [核心架构规范] 纯 27B 旗舰统一矩阵 · 4.5秒无感热切换 · 0秒动态思考等级调控
echo   [今日主脑日志] %DAILY_LOG%
echo ====================================================================================
echo.

:: 检查并拉起 8081 智能自适应调度网关服务
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8081" ^| findstr "LISTENING"') do set GATEWAY_PID=%%a
if "%GATEWAY_PID%"=="" (
    start "" /B "C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0qwen_tool_proxy.py" --listen 8081 --target 8083 --api-key llamacpp >> "%~dp0logs\8081_proxy_%dt%.log" 2>&1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher_smart_gateway.ps1" -DailyLogFile "%DAILY_LOG%"
echo.
echo 服务已退出。按任意键关闭窗口...
pause >nul
