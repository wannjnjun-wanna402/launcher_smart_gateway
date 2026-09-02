@echo off
chcp 65001 >nul
title Qwen3.8-27B 新模型 MTP 投机参数测速套件
echo ======================================================================
echo  llama.cpp 投机解码 MTP 测速 - Qwen3.8-27B 梯队 (96K 上下文)
echo  支持模型:
echo    [1] Qwen3.8-27B-UD-Q6_K_M (~21.5GB)
echo    [2] Qwen3.8-27B-NVFP4-MTP-HIGHEST (~21.6GB)
echo  默认上下文: 96K (98304)
echo  规范保障: 每次切换自动杀死残留进程并循环确认显存释放到 ^<500MB
echo ======================================================================
echo.
echo 请选择要执行的测速方案:
echo   [1] 两个新模型全部测试 (UD-Q6 + HIGHEST 核心扫描, 约 16-20 分钟) [推荐]
echo   [2] 仅测 Qwen3.8-27B-UD-Q6_K_M (核心扫描: n=1..6, 约 8-10 分钟)
echo   [3] 仅测 Qwen3.8-27B-HIGHEST (核心扫描: n=1..6, 约 8-10 分钟)
echo   [4] 两个新模型全网格深度测试 (Full Grid: 17套配置/模型, 约 40-50 分钟)
echo   [5] 快速冒烟测试 (Smoke Test: 验证流程环境, 约 2 分钟)
echo.
set /p CHOICE="请输入选项 (1/2/3/4/5，默认 1): "

set PY_EXE=C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe
if not exist "%PY_EXE%" set PY_EXE=python.exe

if "%CHOICE%"=="2" (
    echo.
    echo [执行] 仅测 Qwen3.8-27B-UD-Q6_K_M (核心扫描)
    "%PY_EXE%" "%~dp0bench_27b_mtp_v2.py" --model ud --mode core --ctx 98304
) else if "%CHOICE%"=="3" (
    echo.
    echo [执行] 仅测 Qwen3.8-27B-HIGHEST (核心扫描)
    "%PY_EXE%" "%~dp0bench_27b_mtp_v2.py" --model highest --mode core --ctx 98304
) else if "%CHOICE%"=="4" (
    echo.
    echo [执行] 两个新模型全网格测试 (Full Grid)
    "%PY_EXE%" "%~dp0bench_27b_mtp_v2.py" --model new --mode full --ctx 98304
) else if "%CHOICE%"=="5" (
    echo.
    echo [执行] 快速冒烟测试 (Smoke Test)
    "%PY_EXE%" "%~dp0bench_27b_mtp_v2.py" --model new --mode smoke --ctx 98304
) else (
    echo.
    echo [执行] 两个新模型核心扫描 (UD-Q6 + HIGHEST)
    "%PY_EXE%" "%~dp0bench_27b_mtp_v2.py" --model new --mode core --ctx 98304
)

echo.
echo ============================ 测试完成 ============================
echo  结果文件请查看 speed_bench\ 目录下的 CSV、TXT 与 Markdown 对比报告
echo ==================================================================
pause
