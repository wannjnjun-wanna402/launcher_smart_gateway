@chcp 65001 >nul
@echo off
title 一键安全停止所有 AI 进程与释放显存

echo ==============================================================================
echo              一键安全停止所有 AI 服务与后台孤儿进程 (关机/重启前专用)
echo ==============================================================================
echo.
echo [1/3] 正在安全终止 llama-server、代理网关及推理进程...
taskkill /F /IM llama-server.exe 2>nul
taskkill /F /IM llama-cli.exe 2>nul
taskkill /F /FI "WINDOWTITLE eq Qwen / llama.cpp*" 2>nul

echo [2/3] 正在清理后台孤儿搜索与可能卡死的脚本进程 (find.exe, rogue node)...
taskkill /F /IM find.exe 2>nul
taskkill /F /IM grep.exe 2>nul

echo [3/3] 正在重置 8081、8083 与 8085 端口...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8081 :8083 :8085"') do (
    taskkill /F /PID %%a 2>nul
)

echo.
echo ==============================================================================
echo [SUCCESS] 所有 AI 进程与显卡显存已彻底清空，系统已处于最干净状态！
echo           现在你可以安全关机、重启电脑，或者重新启动 AI 服务。
echo ==============================================================================
echo.
pause
