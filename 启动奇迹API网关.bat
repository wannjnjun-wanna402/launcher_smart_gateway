@echo off
chcp 65001 >nul
title 奇迹API网关 · 监控中心
cd /d "%~dp0"
where python >nul 2>nul || (echo [错误] 未检测到 Python，请先安装 Python 3.10+ 并勾选 Add to PATH & pause & exit /b 1)

if not exist "%~dp0logs" mkdir "%~dp0logs"

echo ====================================================================================
echo   🚀 奇迹智能协同网关 (端口: 8081 -^> 8083)
echo ====================================================================================
echo   [统一接口] http://127.0.0.1:8081/v1 (Claude Code / OpenAI 双兼容)
echo   [监控大屏] http://127.0.0.1:8081/dashboard (硬件遥测 · 槽位在位 · 计费流水)
echo ====================================================================================
echo.

:: 检查 8081 是否已在运行
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8081" ^| findstr "LISTENING"') do set GATEWAY_PID=%%a
if "%GATEWAY_PID%"=="" (
    start "" /B python "%~dp0qwen_tool_proxy.py" --listen 8081 --target 8083 --api-key llamacpp
)

start "" "http://127.0.0.1:8081/dashboard"
echo 网关已在后台常驻运行，浏览器已为您打开监控大屏。
echo 如需启动推理模型，请双击【启动AI大模型.bat】选择模型启动。
echo.
pause
