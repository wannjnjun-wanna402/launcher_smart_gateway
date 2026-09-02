@echo off
chcp 65001 >nul
echo ======================================================================
echo  llama.cpp 投机解码 MTP 测速 - Qwen3.8-27B-NVFP4-MTP-MID-HIGH
echo  模型: E:\models\Qwen3.8-27B-NVFP4-MTP-MID-HIGH.gguf
echo  上下文: 160K (163840)
echo  网格: n-max (1..8) + p-min (0.75, 0.85) + 思考开/关对照 + 5大典型任务
echo  清理: 每次切换自动杀死残留进程并循环确认显存释放
echo ======================================================================
echo.
echo 请选择测试模式:
echo   [1] 完整全网格测试 (Full Grid: 17 套配置，深度寻找最佳甜点，约 20-25 分钟)
echo   [2] 核心 n-max 扫描 (Core Grid: 8 套配置，测 n-max 1..8，约 10 分钟)
echo   [3] p-min 截断扫描 (p-min Grid: 8 套配置，测概率阈值影响，约 10 分钟)
echo   [4] 快速冒烟测试 (Smoke Test: 3 套配置，验证环境与流程，约 2 分钟)
echo.
set /p MODE_CHOICE="请输入选项 (1/2/3/4，默认 1): "

if "%MODE_CHOICE%"=="2" (
    set BENCH_MODE=core
) else if "%MODE_CHOICE%"=="3" (
    set BENCH_MODE=pmin
) else if "%MODE_CHOICE%"=="4" (
    set BENCH_MODE=smoke
) else (
    set BENCH_MODE=full
)

echo.
echo 已选择模式: %BENCH_MODE%
echo 按任意键开始测试...
pause >nul

"C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0bench_nvfp4_27b_mtp.py" --mode %BENCH_MODE% --ctx 163840

echo.
echo ============================ 测试完成 ============================
echo  结果文件请查看 speed_bench\ 目录下的 CSV、TXT 与 Markdown 报告
echo ==================================================================
pause
