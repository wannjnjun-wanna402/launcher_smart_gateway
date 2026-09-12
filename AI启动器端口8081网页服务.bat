@echo off
chcp 65001 >nul
title AI Model Launcher · Unified 27B Flagship
cd /d "%~dp0"
python "%~dp0launcher_main.py" %*
pause

