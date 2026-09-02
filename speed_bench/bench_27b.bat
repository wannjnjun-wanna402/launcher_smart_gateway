@echo off
chcp 936 >nul
echo ============================================
echo  llama.cpp Speed Benchmark - 27B dense MTP
echo  Model : Qwen3.6-27B-MTP-IQ4_XS-Q8nextn.gguf
echo  Grid  : reasoning x MTP n-max (2..10) + levers
echo  Flow  : kill old server -> launch -> 5 tasks -> kill -> next
echo  Output: results_27b.csv / summary_27b.txt / runs_27b/
echo  Note  : it will taskkill any running llama-server first
echo ============================================
echo.
echo  Make sure NO other llama-server is running, then press any key.
pause >nul
"C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0bench_27b.py"
echo.
echo ============ DONE ============
echo  Results: results_27b.csv
echo  Summary: summary_27b.txt
echo  Run log: bench_27b_run.log
echo ============ DONE ============
pause
