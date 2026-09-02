@echo off
cd /d "%~dp0"
echo ============================================================
echo  llama.cpp Speed Benchmark  (V100-PCIE 32GB)
echo  Configs: reasoning on/off  x  spec off / 2 / 3 / 4 / 6 / 8
echo  Each config auto-loads, generates, then is killed.
echo  Results -> results.csv  and  summary.txt
echo ============================================================
echo.
"C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" bench.py
echo.
echo Benchmark finished. Tell the AI "done" so it can read the results.
pause
