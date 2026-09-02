@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PYEXE=C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe
if not exist "%PYEXE%" (
    echo [ERROR] Python not found: %PYEXE%
    pause
    exit /b 1
)

echo ============================================
echo   Miracle API Gateway v1.0
echo   llama.cpp backend service
echo ============================================
echo.
echo Starting service...
echo.

"%PYEXE%" "%~dp0miracle_api.py"
echo.
echo Service exited.
pause
