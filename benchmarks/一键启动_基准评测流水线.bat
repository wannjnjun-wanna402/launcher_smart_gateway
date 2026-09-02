@echo off
chcp 65001 >nul
title Model Speed Benchmark Test
cd /d "%~dp0"

set PYEXE=C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe
if not exist "%PYEXE%" (
    echo [ERROR] Python not found: %PYEXE%
    pause
    exit /b 1
)

echo ========================================
echo  Model Speed Benchmark Test
echo  Testing all models (cold start, TTFT, TPS)
echo  Results saved to bench_results.json
echo ========================================
echo.
echo Press any key to start...
pause >nul
"%PYEXE%" 基线命令行推理速度测试.py
echo.
echo Test completed. Press any key to exit...
pause >nul
