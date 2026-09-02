# -*- coding: utf-8 -*-
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

files = {
    'Official-Medium': 'eval_results/bench_Qwen3.8-27B-Abliterated-Q6_K_Official-Medium_p1_20260831_102107.json',
    'Sharp-Medium': 'eval_results/bench_Qwen3.8-27B-Abliterated-Q6_K_Sharp-Medium_p1_20260831_102903.json',
    'Fixed-Medium': 'eval_results/bench_Qwen3.8-27B-Abliterated-Q6_K_Fixed-Medium_p1_20260831_103738.json'
}

data = {k: json.load(open(f, 'r', encoding='utf-8')) for k, f in files.items()}

print("=== 失败题目深度诊断 ===")
for k in ['Official-Medium', 'Sharp-Medium', 'Fixed-Medium']:
    print(f"\n>>> 模板: {k} <<<")
    t_fails = [r for r in data[k]['tool_calling']['details'] if not r['passed']]
    c_fails = [r for r in data[k]['coding']['details'] if not r['passed']]
    print(f"工具调用未通过数: {len(t_fails)}")
    for f in t_fails:
        print(f"  - [{f['id']}] {f['category']} | Score: {f['score']} | 原因: {f['error_reason']}")
        print(f"    Raw Tool Args: {f.get('raw_arguments')}")
        print(f"    Parsed Args  : {f.get('parsed_args')}")
    print(f"编程能力未通过数: {len(c_fails)}")
    for f in c_fails:
        print(f"  - [{f['id']}] {f['title']} | Type: {f['error_type']} | Err: {f['error_msg']}")
