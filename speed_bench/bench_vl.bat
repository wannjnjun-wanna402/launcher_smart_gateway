@echo off
chcp 65001
set PYTHONUTF8=1
echo ============================================
echo  Qwen3VL-8B speed test: flash-attn / KV / batch / ctx
echo  12 configs x 4 tasks x 1024 tokens
echo  Make sure no other llama-server is running
echo ============================================
echo.
pause
"%~dp0bench_vl.py"
echo.
echo Done. Results: results_vl.csv / summary_vl.txt
pause
