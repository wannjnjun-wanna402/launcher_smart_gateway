#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 大模型启动中心 端口8081 (根入口脚本)
直接转发执行 qidongqi/AI大模型启动中心端口8081.py
"""
import os
import sys
import importlib.util

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT_DIR)

if __name__ == "__main__":
    target_script = os.path.join(ROOT_DIR, "qidongqi", "AI大模型启动中心端口8081.py")
    if os.path.isfile(target_script):
        spec = importlib.util.spec_from_file_location("__main__", target_script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        print(f"错误: 未找到启动器核心脚本: {target_script}")
        try:
            input("按 Enter 退出...")
        except Exception:
            pass
