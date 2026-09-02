@echo off
chcp 65001 >nul
echo ============================================
echo  专项认知能力测评 - 全模型自动串行
echo ============================================
echo.
echo 题库: quiz_cognitive_v1.json (62题)
echo   - 推理类 40 题 / 记忆类 12 题 / 工具类 10 题
echo.
echo 将依次测试全部 9 个模型，每个模型 62 题
echo 预计总耗时较长，请耐心等待
echo.
echo 开始时间: %DATE% %TIME%
echo ============================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "python \"%~dp0全模型认知能力自动测评.py\" 2>&1"
echo.
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] 脚本异常退出，错误码 %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)
echo ============================================
echo 测评完成！
echo 结束时间: %DATE% %TIME%
echo 结果目录: eval_results\bench_cognitive\
echo ============================================
pause