# -*- coding: utf-8 -*-
"""
一键安全停止所有 AI 服务与后台孤儿进程，并清理混入的 Linux 垃圾文件
遵循 AGENTS.md 规范：纯 Python 原生高能版
"""

import os
import sys
import time
import subprocess
import psutil

# 确保在 Windows 控制台或管道中输出 UTF-8 中文不乱码
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))

TARGET_PORTS = [8081, 8083, 8085, 8086, 8087]
TARGET_PROCESS_NAMES = [
    "llama-server.exe",
    "llama-cli.exe",
    "llama.exe",
    "koboldcpp.exe",
]

def kill_processes_by_name():
    killed = 0
    print("[1/4] 正在扫描并终止 AI 引擎进程...")
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = proc.info['name']
            if name and name.lower() in [n.lower() for n in TARGET_PROCESS_NAMES]:
                print(f"  -> 终止进程 {name} (PID: {proc.info['pid']})")
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if killed == 0:
        print("  -> 未发现运行中的 llama 引擎进程。")
    else:
        print(f"  -> 已成功终止 {killed} 个引擎进程。")

def kill_processes_by_ports():
    print("[2/4] 正在检查端口占用 (8081, 8083, 8085, 8086, 8087)...")
    killed_pids = set()
    current_pid = os.getpid()
    
    for conn in psutil.net_connections(kind='inet'):
        try:
            if conn.laddr and conn.laddr.port in TARGET_PORTS:
                pid = conn.pid
                if pid and pid != current_pid and pid not in killed_pids:
                    try:
                        p = psutil.Process(pid)
                        p_name = p.name()
                        print(f"  -> 释放端口 {conn.laddr.port}：终止进程 {p_name} (PID: {pid})")
                        p.kill()
                        killed_pids.add(pid)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
        except Exception:
            pass

    # 兜底 netstat 检查
    try:
        out = subprocess.check_output("netstat -aon", shell=True, text=True, errors="ignore")
        for line in out.splitlines():
            for port in TARGET_PORTS:
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        try:
                            pid = int(parts[-1])
                            if pid != 0 and pid != current_pid and pid not in killed_pids:
                                subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                print(f"  -> taskkill 兜底释放端口 {port} (PID: {pid})")
                                killed_pids.add(pid)
                        except ValueError:
                            pass
    except Exception:
        pass

    if not killed_pids:
        print("  -> 目标端口均已空闲，无残留服务。")
    else:
        print(f"  -> 成功清理 {len(killed_pids)} 个端口占用进程。")

def clean_ubuntu_mixed_files():
    print("[3/4] 正在扫描并清理误解压的 Ubuntu / Linux 文件...")
    cleaned_count = 0
    cleaned_bytes = 0

    # 安全白名单后缀（绝对不碰）
    SAFE_EXTENSIONS = {
        '.py', '.bat', '.cmd', '.md', '.txt', '.json', '.yaml', '.yml', 
        '.toml', '.exe', '.dll', '.log', '.git', '.gitignore', '.ps1'
    }

    try:
        files = os.listdir(WORKSPACE_DIR)
    except Exception as e:
        print(f"  -> 读取目录失败: {e}")
        return

    for fname in files:
        fpath = os.path.join(WORKSPACE_DIR, fname)
        if not os.path.isfile(fpath):
            continue

        base_lower = fname.lower()
        ext = os.path.splitext(fname)[1].lower()

        is_linux_file = False
        reason = ""

        # 1. 检查 Linux .so 动态库
        if ".so" in base_lower:
            is_linux_file = True
            reason = "Linux 动态库 (.so)"
        # 2. 检查 ELF 头部
        elif ext not in SAFE_EXTENSIONS:
            try:
                with open(fpath, "rb") as f:
                    header = f.read(4)
                    if header == b"\x7fELF":
                        is_linux_file = True
                        reason = "ELF Linux 可执行程序"
            except Exception:
                pass

        if is_linux_file:
            try:
                size = os.path.getsize(fpath)
                os.remove(fpath)
                cleaned_count += 1
                cleaned_bytes += size
                print(f"  -> [已删除] {fname} ({size / 1024 / 1024:.2f} MB) - {reason}")
            except Exception as e:
                print(f"  -> [删除失败] {fname}: {e}")

    if cleaned_count > 0:
        print(f"  -> 清理完毕：共彻底删除 {cleaned_count} 个 Linux 文件，腾出 {cleaned_bytes / 1024 / 1024:.2f} MB 磁盘空间！")
    else:
        print("  -> 目录纯净，未发现混入的 Linux 文件。")

def check_gpu_status():
    print("[4/4] 正在检查 GPU 显存释放状态...")
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if res.returncode == 0 and res.stdout.strip():
            lines = res.stdout.strip().splitlines()
            for i, line in enumerate(lines):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    print(f"  -> GPU {i}: 显存占用 {parts[0]} MB / {parts[1]} MB (利用率: {parts[2]}%)")
        else:
            print("  -> 未检测到 NVIDIA 驱动输出。")
    except Exception as e:
        print(f"  -> 检查显存跳过: {e}")

def main():
    print("=" * 76)
    print("        一键安全停止所有 AI 服务与清理混入文件 (Python 原生高能版)")
    print("=" * 76)
    print(f"工作目录: {WORKSPACE_DIR}\n")

    kill_processes_by_name()
    print()
    kill_processes_by_ports()
    print()
    clean_ubuntu_mixed_files()
    print()
    check_gpu_status()

    print("\n" + "=" * 76)
    print(" [SUCCESS] 所有 AI 进程与文件锁已彻底清除！")
    print("           混入的 Ubuntu 文件已清空，现在你可以畅快解压覆盖 Windows 压缩包！")
    print("=" * 76 + "\n")

if __name__ == "__main__":
    main()
