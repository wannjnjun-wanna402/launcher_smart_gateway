# -*- coding: utf-8 -*-
"""
生成全量问答实测内容与生成质量对照归档表
"""
import os, json, glob, re

BASE_DIR = r"E:\llama-win-cuda-12.4-x64\speed_bench"
RUNS_DIR = os.path.join(BASE_DIR, "runs_speed_matrix")

# 检查日志并汇总
print("QA answers archiving utility ready.")
