@echo off
chcp 65001
set PYTHONUTF8=1
echo ============================================
echo  A/B speed test: reasoning-budget effect on V100 throughput
echo  A = budget 2048  (backend sampling OFF)
echo  B = budget off   (backend sampling ON)
echo  4 tasks x 512 tokens + 1 warmup per config
echo  Make sure no other llama-server is running
echo ============================================
echo.
pause
"%~dp0bench_budget_ab.py"
echo.
echo Done. Results: results_budget_ab.csv / summary_budget_ab.txt
pause
