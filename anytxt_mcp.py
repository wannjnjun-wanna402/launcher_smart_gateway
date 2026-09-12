#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
anytxt_mcp.py (根目录重定向引导器)
已迁移至统一目录: E:\llama-win-cuda-12.4-x64\mcp\anytxt_mcp.py
"""
import os
import sys

target = os.path.join(os.path.dirname(__file__), "mcp", "anytxt_mcp.py")
if __name__ == "__main__":
    os.execv(sys.executable, [sys.executable, target] + sys.argv[1:])
