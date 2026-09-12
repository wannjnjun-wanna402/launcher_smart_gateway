@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo   🚀 AI 大模型极限速度与 MTP/上下文参数评测工具
echo ========================================================
echo.
python "%~dp0bench_speed_matrix.py" --model all --suite matrix --predict 512
pause
