@echo off
chcp 65001 >nul
set "CUDA_CACHE_MAXSIZE=2147483648"
set "CUDA_DEVICE_MAX_CONNECTIONS=1"
"C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0launcher_main.py" %*
