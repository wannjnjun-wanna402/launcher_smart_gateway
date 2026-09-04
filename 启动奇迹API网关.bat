@echo off
chcp 65001 >nul
title 奇迹API网关 · 监控中心

if not exist "%~dp0logs" mkdir "%~dp0logs"

set "CUDA_CACHE_MAXSIZE=2147483648"
set "CUDA_DEVICE_MAX_CONNECTIONS=1"

for /f %%I in ('"C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" -c "import time; print(time.strftime('%%%%Y%%%%m%%%%d'))"') do set "dt=%%I"
if "%dt%"=="" set "dt=%DATE:~-4%%DATE:~3,2%%DATE:~0,2%"

echo ====================================================================================
echo   🚀 奇迹智能协同网关 (端口: 8081 -^> 8083)
echo ====================================================================================
echo   [统一接口] http://127.0.0.1:8081/v1 (Claude Code / OpenAI 双兼容)
echo   [监控大屏] http://127.0.0.1:8081/dashboard (硬件遥测 · 槽位在位 · 计费流水)
echo ====================================================================================
echo.

:: 启动 8081 网关
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8081" ^| findstr "LISTENING"') do set GATEWAY_PID=%%a
if "%GATEWAY_PID%"=="" (
    start "" /B "C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0qwen_tool_proxy.py" --listen 8081 --target 8083 --api-key llamacpp >> "%~dp0logs\8081_proxy_%dt%.log" 2>&1
)

start "" "http://127.0.0.1:8081/dashboard"
echo 网关已在后台常驻运行，浏览器已为您打开监控大屏。
echo 如需启动推理模型，请双击【启动AI大模型.bat】选择模型启动。
echo.
pause
