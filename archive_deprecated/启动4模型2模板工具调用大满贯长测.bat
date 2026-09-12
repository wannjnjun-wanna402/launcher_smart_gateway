@echo off
chcp 65001 >nul
title Qwen3.8 工具调用 4模型 x 2模板 大满贯全自动长测
cd /d "%~dp0"

echo =======================================================================
echo   正在启动 Qwen3.8 工具调用 4模型 x 2模板 大满贯全自动长测...
echo =======================================================================
echo.
echo 测试模型: [6] NVFP4-MID-HIGH / [8] A-Q6_K / [9] UD-Q5KXL / [10] U-Q6_K
echo 测试模板: Fixed-Medium (froggeric) vs Sharp-Medium (Qwen-Sharp)
echo 上下文长: 统一 64K (极速模式)
echo 题库场景: 20 题深度专项 (死循环/XML污染/多轮RAG闭环/负样本)
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_master_tool_benchmark.ps1"

echo.
echo =======================================================================
echo   评测已结束，按任意键关闭窗口...
echo =======================================================================
pause >nul
