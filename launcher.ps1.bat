@echo off
chcp 65001 >nul
set "CUDA_CACHE_MAXSIZE=2147483648"
set "CUDA_DEVICE_MAX_CONNECTIONS=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher_main.ps1" %*
