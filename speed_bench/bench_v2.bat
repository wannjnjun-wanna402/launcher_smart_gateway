@echo off
chcp 936 >nul
echo ============================================
echo  llama.cpp Speed Benchmark v2  (V100-PCIE 32GB)
echo  Model : Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf
echo  Grid  : reasoning x MTP n-max full scan + levers
echo  Flow  : kill old server -> launch -> 5 tasks -> kill -> next
echo  Output: results_v2.csv / summary_v2.txt / runs_v2/
echo  Note  : it will taskkill any running llama-server first
echo ============================================
echo.
echo  Make sure NO other llama-server is running, then press any key.
pause >nul
"C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0bench_v2.py"
echo.
echo ============ DONE ============
echo  Results: results_v2.csv
echo  Summary: summary_v2.txt
echo  Run log: bench_v2_run.log
echo ============ DONE ============
pause
