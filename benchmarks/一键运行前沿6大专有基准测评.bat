@echo off
chcp 65001 >nul
title 前沿 6 大专有基准大模型专业评测 (Frontier SOTA)

echo ===============================================================================
echo   🏆 前沿 6 大专有基准大模型专业评测 (Frontier SOTA 6-Benchmark Suite)
echo   涵盖: Terminal-Bench 2.1, SWE-bench Pro, DeepSWE, NL2Repo, GPQA Diamond, HLE
echo ===============================================================================
echo.

set PYTHON_EXE=C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe

echo 请选择评测模式:
echo   [1] 旗舰巅峰对决 (Qwen3.8-27B VS Ornith-1.5-35B) - 默认
echo   [2] 快速测试 Qwen3.8-27B 单模型
echo   [3] 快速测试 Ornith-1.5-35B 单模型
echo   [4] 全量评测所有模型 (Qwen 4B, 8B, 27B, 35B)
echo.

set /p choice="请输入选项 [1-4] (直接回车默认 1): "

if "%choice%"=="2" (
    "%PYTHON_EXE%" "%~dp0前沿6大专有基准大模型测评.py" --models q5kp
) else if "%choice%"=="3" (
    "%PYTHON_EXE%" "%~dp0前沿6大专有基准大模型测评.py" --models ornith
) else if "%choice%"=="4" (
    "%PYTHON_EXE%" "%~dp0前沿6大专有基准大模型测评.py" --models qwen35_4b,qwen3vl,q5kp,ornith
) else (
    "%PYTHON_EXE%" "%~dp0前沿6大专有基准大模型测评.py" --models q5kp,ornith
)

echo.
echo ===============================================================================
echo   评测已完成！详细 Markdown 对决战报已保存在 bench_results/ 目录下。
echo ===============================================================================
pause
