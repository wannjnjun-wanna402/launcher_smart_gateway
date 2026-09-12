@echo off
chcp 65001 >nul
title AI 智能任务自适应网关 · Unified 27B Flagship
cd /d "%~dp0"
python "%~dp0launcher_smart_gateway.py" %*
pause

