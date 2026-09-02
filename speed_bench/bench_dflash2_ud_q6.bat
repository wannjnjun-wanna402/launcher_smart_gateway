@echo off
chcp 65001 >nul
title Qwen3.8-27B-UD-Q6_K_M 纯 DFlash2 精准测速套件
echo ======================================================================
echo  Qwen3.8-27B-UD-Q6_K_M 纯 DFlash2 扩散投机测速
echo  主模型: E:\models\Qwen3.8-27B-UD-Q6_K_M.gguf (~21.5GB)
echo  草稿模型: E:\models\Qwen3.8-27B-DFlash2-Q4_K_M.gguf (~1.14GB)
echo  已知基准参考: 裸速 = 22.30 tok/s | MTP n=2 纪录 = 37.13 tok/s
echo  硬件环境: Tesla V100 32GB (无需重复测裸速与MTP，直击 DFlash2 核心甜点)
echo ======================================================================
echo.
echo 请选择 DFlash2 评测方案:
echo   [1] 核心步长阶梯扫描 (Core: n=3, 5, 7, 9 共4组, 约 6-8 分钟) [推荐]
echo   [2] 步长 + 置信度过滤全测 (Full: 包含 p-min=0.75 质量过滤, 共6组, 约 10-12 分钟)
echo   [3] 快速冒烟测试 (Smoke: 仅测 n=3 与 n=7, 共2组, 约 2-3 分钟)
echo.
set /p CHOICE="请输入选项 (1/2/3，默认 1): "

set PY_EXE=C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe
if not exist "%PY_EXE%" set PY_EXE=python.exe

if "%CHOICE%"=="2" (
    echo.
    echo [执行] 步长 + 置信度过滤全测 (Full: 6组)
    "%PY_EXE%" "%~dp0bench_dflash2_ud_q6.py" --mode full --ctx 32768
) else if "%CHOICE%"=="3" (
    echo.
    echo [执行] 快速冒烟测试 (Smoke: 2组)
    "%PY_EXE%" "%~dp0bench_dflash2_ud_q6.py" --mode smoke --ctx 32768
) else (
    echo.
    echo [执行] 核心步长阶梯扫描 (Core: 4组)
    "%PY_EXE%" "%~dp0bench_dflash2_ud_q6.py" --mode core --ctx 32768
)

echo.
echo ============================ 测试完成 ============================
echo  结果文件请查看 speed_bench\ 目录下的 CSV、TXT 与 Markdown 报告
echo ==================================================================
pause
