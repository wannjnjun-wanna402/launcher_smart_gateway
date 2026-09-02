# UTF-8 with BOM
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$wsh = New-Object -ComObject WScript.Shell
$desktop = "d:\Users\wanna402\Desktop"
$workspace = "E:\llama-win-cuda-12.4-x64"
$pyExe = "C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe"
$pyScript = "E:\llama-win-cuda-12.4-x64\launcher_smart_gateway.py"

# 1. 工作区快捷方式
$wsLnkPath = Join-Path $workspace "启动AI智能任务自适应网关.lnk"
$wsLnk = $wsh.CreateShortcut($wsLnkPath)
$wsLnk.TargetPath = $pyExe
$wsLnk.Arguments = "`"$pyScript`""
$wsLnk.WorkingDirectory = $workspace
$wsLnk.Description = "AI 智能任务自适应网关 (纯 Python 原生驱动引擎 · 27B 旗舰统一矩阵)"
$wsLnk.Save()
Write-Host "✅ 工作区快捷方式已建立: $wsLnkPath"

# 2. 桌面快捷方式
$dtLnkPath = Join-Path $desktop "启动AI智能任务自适应网关.lnk"
$dtLnk = $wsh.CreateShortcut($dtLnkPath)
$dtLnk.TargetPath = $pyExe
$dtLnk.Arguments = "`"$pyScript`""
$dtLnk.WorkingDirectory = $workspace
$dtLnk.Description = "AI 智能任务自适应网关 (纯 Python 原生驱动引擎 · 27B 旗舰统一矩阵)"
$dtLnk.Save()
Write-Host "✅ 桌面快捷方式已建立: $dtLnkPath"

# 3. 桌面旧 .bat 快捷方式同步更新
$oldLnkPath = Join-Path $desktop "启动AI智能任务自适应网关.bat - 快捷方式.lnk"
if (Test-Path $oldLnkPath) {
    $oldLnk = $wsh.CreateShortcut($oldLnkPath)
    $oldLnk.TargetPath = $pyExe
    $oldLnk.Arguments = "`"$pyScript`""
    $oldLnk.WorkingDirectory = $workspace
    $oldLnk.Description = "AI 智能任务自适应网关 (纯 Python 原生驱动引擎 · 27B 旗舰统一矩阵)"
    $oldLnk.Save()
    Write-Host "✅ 桌面旧 .bat 快捷方式已同步指向 Python 引擎: $oldLnkPath"
}
