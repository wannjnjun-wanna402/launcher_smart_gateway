# -*- coding: utf-8 -*-
import os
import sys
import subprocess

def create_shortcuts():
    desktop = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\wanna402"), "Desktop")
    workspace = r"E:\llama-win-cuda-12.4-x64"
    py_exe = r"C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe"
    script_path = os.path.join(workspace, "launcher_smart_gateway.py")

    ps_script = f"""
$wsh = New-Object -ComObject WScript.Shell

# 1. 工作区快捷方式
$wsLnk = $wsh.CreateShortcut('{os.path.join(workspace, "启动AI智能任务自适应网关.lnk")}')
$wsLnk.TargetPath = '{py_exe}'
$wsLnk.Arguments = '"{script_path}"'
$wsLnk.WorkingDirectory = '{workspace}'
$wsLnk.Description = 'AI 智能任务自适应网关 (纯 Python 原生驱动引擎 · 27B 旗舰统一矩阵)'
$wsLnk.Save()

# 2. 桌面快捷方式
$dtLnk = $wsh.CreateShortcut('{os.path.join(desktop, "启动AI智能任务自适应网关.lnk")}')
$dtLnk.TargetPath = '{py_exe}'
$dtLnk.Arguments = '"{script_path}"'
$dtLnk.WorkingDirectory = '{workspace}'
$dtLnk.Description = 'AI 智能任务自适应网关 (纯 Python 原生驱动引擎 · 27B 旗舰统一矩阵)'
$dtLnk.Save()

# 3. 桌面旧快捷方式同步更新
$oldBatLnk = '{os.path.join(desktop, "启动AI智能任务自适应网关.bat - 快捷方式.lnk")}'
if (Test-Path $oldBatLnk) {{
    $old = $wsh.CreateShortcut($oldBatLnk)
    $old.TargetPath = '{py_exe}'
    $old.Arguments = '"{script_path}"'
    $old.WorkingDirectory = '{workspace}'
    $old.Description = 'AI 智能任务自适应网关 (纯 Python 原生驱动引擎 · 27B 旗舰统一矩阵)'
    $old.Save()
}}
Write-Host "SUCCESS"
"""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    print("Output:", res.stdout.strip())
    if res.stderr:
        print("Error:", res.stderr.strip())

if __name__ == "__main__":
    create_shortcuts()
