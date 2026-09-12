@echo off
chcp 65001 >nul
echo [AnyTXT] 正在启动 AnyTXT Searcher (随用随开)...
start "" "E:\AnyTXT Searcher\ATGUI.exe"
echo [AnyTXT] 服务已拉起，搜索完成后可随时双击【关闭AnyTXT.bat】释放系统资源。
ping 127.0.0.1 -n 3 >nul
