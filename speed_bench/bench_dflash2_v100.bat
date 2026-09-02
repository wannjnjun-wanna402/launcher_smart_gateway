@chcp 65001 >nul
@echo off
title Qwen3.8-27B DFlash2 vs MTP vs Baseline Benchmark (V100)

echo ==============================================================================
echo        Qwen3.8-27B DFlash 2 vs MTP vs Baseline Benchmark Suite
echo ==============================================================================
echo.
echo Select Benchmark Mode:
echo   [1] Quick Mode (3 core configs + 3 tasks, ~3-4 mins)
echo   [2] Full Mode  (7 parameter grids + 6 real-world tasks, full report)
echo.
set /p MODE_CHOICE="Enter option (1 or 2, default 1): "

if "%MODE_CHOICE%"=="2" goto run_full
goto run_quick

:run_full
echo.
echo [INFO] Starting Full Benchmark Mode...
"C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0bench_dflash2_v100.py"
goto end

:run_quick
echo.
echo [INFO] Starting Quick Benchmark Mode...
"C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0bench_dflash2_v100.py" --quick
goto end

:end
echo.
echo ==============================================================================
echo Benchmark finished. Press any key to exit...
pause >nul
