@echo off
chcp 65001 >nul
echo [AnyTXT] 正在停止 AnyTXT 进程并释放系统资源...
taskkill /IM ATGUI.exe /F >nul 2>&1
taskkill /IM ATService.exe /F >nul 2>&1
echo [AnyTXT] 相关进程已退出，CPU 与磁盘 I/O 资源已完全释放！
ping 127.0.0.1 -n 2 >nul
