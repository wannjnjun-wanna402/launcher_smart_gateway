@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo   🚀 Qwen3-Coder-30B 与 Qwen3.8-27B 理论极速与 MTP 深度测速
echo ========================================================
echo.
python "%~dp0speed_bench\bench_speed_matrix.py" --model all --suite matrix --predict 512
pause
