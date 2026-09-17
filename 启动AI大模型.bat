@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo [错误] 未检测到 Python，请先安装 Python 3.10+ 并勾选 Add to PATH & pause & exit /b 1)
python "%~dp0launcher_main.py" %*
pause
