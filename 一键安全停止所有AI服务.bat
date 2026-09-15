@echo off
chcp 65001 >nul
cd /d "%~dp0"
python "%~dp0stop_ai_services.py" %*
pause
