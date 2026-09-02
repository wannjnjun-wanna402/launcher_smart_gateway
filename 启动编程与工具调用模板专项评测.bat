@echo off
chcp 65001 >nul
title Qwen3.8 编程能力与工具调用模板专项评测

echo ========================================================================
echo   正在启动 Qwen3.8 编程能力与工具调用专项评测系统...
echo ========================================================================
echo.

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_tool_code_bench.ps1"

pause
