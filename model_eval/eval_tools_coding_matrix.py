# -*- coding: utf-8 -*-
"""
=============================================================================
Qwen3.8 工具调用深度专项评测引擎 (带输入/输出 Token 速度记录)
=============================================================================
专注于评估：
- 聊天模板差异 (Qwen-Sharp vs Qwen-Fixed)
- 参数死循环重复展开 / XML标签泄露 / 递归调用失控
- 输入速度 (Prompt t/s) 与 输出速度 (Gen t/s) 实测记录
- 无审查模型 (Abliterated / Uncensored) vs 官方对齐模型 (NVFP4) 在工具调用上的能力
=============================================================================
"""

import os
import sys
import json
import time
import re
import argparse
import urllib.request
import urllib.error
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    from tool_code_test_cases import TOOLS_SCHEMA, TOOL_QUESTIONS
except ImportError:
    import tool_code_test_cases as tc
    TOOLS_SCHEMA = tc.TOOLS_SCHEMA
    TOOL_QUESTIONS = tc.TOOL_QUESTIONS


def get_loaded_models(api_base, api_key="llamacpp", timeout=5):
    url = api_base.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, method="GET")
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
            return [m.get("id") for m in data.get("data", [])]
    except Exception:
        return []


def post_chat_completion(api_base, payload, api_key="llamacpp", timeout=60):
    url = api_base.rstrip("/") + "/chat/completions"
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_text = resp.read().decode("utf-8", "ignore")
            elapsed = time.time() - t0
            res_json = json.loads(raw_text)
            return {"status": "ok", "data": res_json, "elapsed": elapsed, "error": None}
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        err_body = e.read().decode("utf-8", "ignore") if e.fp else ""
        return {"status": "http_error", "code": e.code, "error": f"HTTP {e.code}: {err_body}", "elapsed": elapsed}
    except Exception as e:
        elapsed = time.time() - t0
        return {"status": "error", "error": f"{type(e).__name__}: {str(e)}", "elapsed": elapsed}


def detect_parameter_loop(raw_arguments):
    if not raw_arguments or not isinstance(raw_arguments, str):
        return False, None
    keys_found = re.findall(r'"([a-zA-Z0-9_\-]+)"\s*:', raw_arguments)
    from collections import Counter
    counts = Counter(keys_found)
    repeated = [k for k, count in counts.items() if count > 1]
    if repeated:
        return True, f"参数中存在键名重复死循环展开: {repeated} (最高重复 {max(counts.values())} 次)"
    return False, None


def evaluate_tool_call(q, response_data):
    choices = response_data.get("choices", [])
    if not choices:
        return {
            "score": 0.0,
            "passed": False,
            "native_tool_called": False,
            "tool_name": None,
            "raw_arguments": None,
            "parsed_args": None,
            "json_valid": False,
            "param_matched": False,
            "param_loop_detected": False,
            "xml_leak_detected": False,
            "thinking_leak": False,
            "error_reason": "API 响应中 choices 为空"
        }
    
    message = choices[0].get("message", {})
    content = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []
    thinking_leak = ("<think>" in content) or any("<think>" in str(tc) for tc in tool_calls)
    
    if not q["should_call"]:
        if not tool_calls:
            if "<tool_call>" in content or ('"name":' in content and "parameters" in content):
                return {
                    "score": 40.0, "passed": False, "native_tool_called": False,
                    "tool_name": "UnparsedTextTool", "raw_arguments": content[:300],
                    "parsed_args": None, "json_valid": False, "param_matched": False,
                    "param_loop_detected": False, "xml_leak_detected": False,
                    "thinking_leak": thinking_leak, "error_reason": "正文中输出了非结构化的伪工具调用文本 (文本误触发)"
                }
            return {
                "score": 100.0, "passed": True, "native_tool_called": False,
                "tool_name": None, "raw_arguments": None, "parsed_args": None,
                "json_valid": True, "param_matched": True, "param_loop_detected": False,
                "xml_leak_detected": False, "thinking_leak": thinking_leak, "error_reason": None
            }
        else:
            called_name = tool_calls[0].get("function", {}).get("name")
            return {
                "score": 0.0, "passed": False, "native_tool_called": True,
                "tool_name": called_name, "raw_arguments": str(tool_calls[0]),
                "parsed_args": None, "json_valid": False, "param_matched": False,
                "param_loop_detected": False, "xml_leak_detected": False,
                "thinking_leak": thinking_leak, "error_reason": f"误触发工具调用: 预期不调用，但实际调用了 {called_name}"
            }
    
    if not tool_calls:
        if "<tool_call>" in content or '"name":' in content:
            return {
                "score": 20.0, "passed": False, "native_tool_called": False,
                "tool_name": "UnparsedTextTool", "raw_arguments": content[:300],
                "parsed_args": None, "json_valid": False, "param_matched": False,
                "param_loop_detected": False, "xml_leak_detected": False,
                "thinking_leak": thinking_leak, "error_reason": "【模板格式不匹配】模型生成了工具调用文本，但 llama-server 无法解析为 OpenAI tool_calls 结构"
            }
        return {
            "score": 0.0, "passed": False, "native_tool_called": False,
            "tool_name": None, "raw_arguments": None, "parsed_args": None,
            "json_valid": False, "param_matched": False, "param_loop_detected": False,
            "xml_leak_detected": False, "thinking_leak": thinking_leak, "error_reason": "未能识别意图，未发起任何工具调用"
        }
    
    first_call = tool_calls[0]
    func_info = first_call.get("function", {})
    called_name = func_info.get("name")
    raw_args = func_info.get("arguments") or ""
    
    has_loop, loop_msg = detect_parameter_loop(raw_args if isinstance(raw_args, str) else json.dumps(raw_args))
    has_xml_leak = ("<parameter" in str(raw_args)) or ("</parameter>" in str(raw_args)) or ("</tool_call>" in str(raw_args))
    
    if called_name != q["expected_tool"]:
        return {
            "score": 30.0, "passed": False, "native_tool_called": True,
            "tool_name": called_name, "raw_arguments": raw_args, "parsed_args": None,
            "json_valid": False, "param_matched": False, "param_loop_detected": has_loop,
            "xml_leak_detected": has_xml_leak, "thinking_leak": thinking_leak,
            "error_reason": f"调用了错误的工具: 预期 {q['expected_tool']}, 实际 {called_name}"
        }
    
    try:
        if isinstance(raw_args, dict): parsed_args = raw_args
        else: parsed_args = json.loads(raw_args)
        json_valid = True
    except Exception as e:
        return {
            "score": 50.0, "passed": False, "native_tool_called": True,
            "tool_name": called_name, "raw_arguments": raw_args, "parsed_args": None,
            "json_valid": False, "param_matched": False, "param_loop_detected": has_loop,
            "xml_leak_detected": has_xml_leak, "thinking_leak": thinking_leak,
            "error_reason": f"工具参数 JSON 解析失败: {str(e)}"
        }
    
    if has_xml_leak:
        return {
            "score": 60.0, "passed": False, "native_tool_called": True,
            "tool_name": called_name, "raw_arguments": raw_args, "parsed_args": parsed_args,
            "json_valid": json_valid, "param_matched": False, "param_loop_detected": has_loop,
            "xml_leak_detected": True, "thinking_leak": thinking_leak,
            "error_reason": "【模板XML污染】参数中渗漏了 <parameter> 等非标准 XML 标签"
        }
        
    if has_loop:
        return {
            "score": 60.0, "passed": False, "native_tool_called": True,
            "tool_name": called_name, "raw_arguments": raw_args, "parsed_args": parsed_args,
            "json_valid": json_valid, "param_matched": False, "param_loop_detected": True,
            "xml_leak_detected": has_xml_leak, "thinking_leak": thinking_leak,
            "error_reason": f"【参数重复死循环】{loop_msg}"
        }

    param_checks = q.get("param_checks", {})
    param_matched = True
    missing_keys = []
    mismatched_vals = []
    
    for k, expected_vals in param_checks.items():
        if k not in parsed_args:
            param_matched = False
            missing_keys.append(k)
            continue
        
        actual_val = parsed_args[k]
        if isinstance(expected_vals, list):
            match_found = False
            for exp in expected_vals:
                if isinstance(exp, str) and isinstance(actual_val, str) and exp.lower() in actual_val.lower():
                    match_found = True
                    break
                elif exp == actual_val:
                    match_found = True
                    break
                elif isinstance(actual_val, list) and exp in actual_val:
                    match_found = True
                    break
            if not match_found:
                param_matched = False
                mismatched_vals.append(f"{k}={actual_val}")
    
    if not param_matched:
        reasons = []
        if missing_keys: reasons.append(f"缺失必要参数: {missing_keys}")
        if mismatched_vals: reasons.append(f"参数值不匹配: {mismatched_vals}")
        return {
            "score": 75.0, "passed": False, "native_tool_called": True,
            "tool_name": called_name, "raw_arguments": raw_args, "parsed_args": parsed_args,
            "json_valid": True, "param_matched": False, "param_loop_detected": False,
            "xml_leak_detected": False, "thinking_leak": thinking_leak,
            "error_reason": " | ".join(reasons)
        }
    
    return {
        "score": 100.0, "passed": True, "native_tool_called": True,
        "tool_name": called_name, "raw_arguments": raw_args, "parsed_args": parsed_args,
        "json_valid": True, "param_matched": True, "param_loop_detected": False,
        "xml_leak_detected": False, "thinking_leak": thinking_leak, "error_reason": None
    }


def run_tool_benchmark(args):
    print("=" * 76)
    print("       ⚡ Qwen3.8 工具调用深度专项评测 (带输入/输出 Token 测速)")
    print(f"  API Base      : {args.api_base}")
    print(f"  模型别名      : {args.model}")
    print(f"  聊天模板名称  : {args.template_name}")
    print(f"  测试题量      : 工具调用全场景 {len(TOOL_QUESTIONS)} 题 (64K 极速评测模式)")
    print("=" * 76)
    
    loaded = get_loaded_models(args.api_base, api_key=args.api_key)
    if not loaded:
        print(f"[错误] 无法连接到 {args.api_base}/models，请确认服务已就绪！")
        sys.exit(1)
    
    actual_model = loaded[0]
    print(f"[就绪] 目标模型: {actual_model}")
    
    results = {
        "metadata": {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model": actual_model,
            "template_name": args.template_name,
            "api_base": args.api_base,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "total_questions": len(TOOL_QUESTIONS)
        },
        "tool_calling": {
            "summary": {},
            "details": []
        },
        "overall_score": 0.0
    }
    
    tool_scores = []
    native_calls_count = 0
    json_valid_count = 0
    param_matched_count = 0
    param_loop_count = 0
    xml_leak_count = 0
    thinking_leaks_count = 0
    
    prompt_speeds = []
    gen_speeds = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    
    print("\n" + "-" * 50)
    print("  开始逐题评测工具调用能力与吞吐速率")
    print("-" * 50)
    
    for i, q in enumerate(TOOL_QUESTIONS, start=1):
        print(f"  [{i:02d}/{len(TOOL_QUESTIONS):02d}] {q['id']} ({q['category']}): {q['prompt'][:22]}...", end=" ", flush=True)
        
        payload = {
            "model": actual_model,
            "messages": [{"role": "user", "content": q["prompt"]}],
            "tools": TOOLS_SCHEMA,
            "tool_choice": "auto",
            "temperature": args.temperature,
            "max_tokens": 1024,
            "stream": False
        }
        
        res = post_chat_completion(args.api_base, payload, api_key=args.api_key, timeout=args.timeout)
        
        prompt_t = 0
        comp_t = 0
        p_speed = 0.0
        g_speed = 0.0
        
        if res["status"] != "ok":
            print(f"❌ [请求失败: {res.get('error')}]")
            eval_res = {
                "score": 0.0, "passed": False, "native_tool_called": False,
                "tool_name": None, "raw_arguments": None, "parsed_args": None,
                "json_valid": False, "param_matched": False, "param_loop_detected": False,
                "xml_leak_detected": False, "thinking_leak": False,
                "error_reason": f"API Request Failed: {res.get('error')}"
            }
            elapsed = res["elapsed"]
            raw_content = ""
            reasoning = ""
        else:
            elapsed = res["elapsed"]
            res_data = res["data"]
            choice = res_data.get("choices", [{}])[0]
            raw_content = choice.get("message", {}).get("content") or ""
            reasoning = choice.get("message", {}).get("reasoning_content") or ""
            eval_res = evaluate_tool_call(q, res_data)
            
            # 提取 Token 消耗与速度
            usage = res_data.get("usage", {})
            prompt_t = usage.get("prompt_tokens", 0)
            comp_t = usage.get("completion_tokens", 0)
            total_prompt_tokens += prompt_t
            total_completion_tokens += comp_t
            
            timings = res_data.get("timings", {})
            if timings.get("prompt_per_second"):
                p_speed = round(timings.get("prompt_per_second"), 1)
            elif timings.get("prompt_ms") and timings.get("prompt_ms") > 0:
                p_speed = round(prompt_t / (timings.get("prompt_ms") / 1000), 1)
                
            if timings.get("predicted_per_second"):
                g_speed = round(timings.get("predicted_per_second"), 1)
            elif timings.get("predicted_ms") and timings.get("predicted_ms") > 0:
                g_speed = round(comp_t / (timings.get("predicted_ms") / 1000), 1)
            elif comp_t > 0 and elapsed > 0:
                g_speed = round(comp_t / elapsed, 1)
                
            if p_speed > 0: prompt_speeds.append(p_speed)
            if g_speed > 0: gen_speeds.append(g_speed)
            
            # 多轮工具闭环支持
            if q.get("mock_tool_result") and eval_res["passed"]:
                tool_turn_msg = f"[工具 {q['expected_tool']} 返回结果]:\n{json.dumps(q['mock_tool_result'], ensure_ascii=False)}"
                follow_payload = {
                    "model": actual_model,
                    "messages": [
                        {"role": "user", "content": q["prompt"]},
                        {"role": "assistant", "content": raw_content or f"Calling tool {q['expected_tool']}"},
                        {"role": "user", "content": tool_turn_msg}
                    ],
                    "temperature": args.temperature,
                    "max_tokens": 1024
                }
                follow_res = post_chat_completion(args.api_base, follow_payload, api_key=args.api_key, timeout=args.timeout)
                if follow_res["status"] == "ok":
                    follow_content = follow_res["data"].get("choices", [{}])[0].get("message", {}).get("content") or ""
                    kws = q.get("follow_up_checks", [])
                    if not any(kw.lower() in follow_content.lower() for kw in kws):
                        eval_res["score"] = 85.0
                        eval_res["error_reason"] = f"多轮合成缺少关键信息 (预期含 {kws})"
            
            speed_str = f"In: {p_speed} t/s | Out: {g_speed} t/s" if g_speed > 0 else f"{elapsed:.2f}s"
            if eval_res["passed"]:
                print(f"✅ PASS ({speed_str})")
            else:
                print(f"❌ FAIL [{eval_res['error_reason'][:30]}] ({speed_str})")
        
        tool_scores.append(eval_res["score"])
        if eval_res.get("native_tool_called"): native_calls_count += 1
        if eval_res.get("json_valid"): json_valid_count += 1
        if eval_res.get("param_matched"): param_matched_count += 1
        if eval_res.get("param_loop_detected"): param_loop_count += 1
        if eval_res.get("xml_leak_detected"): xml_leak_count += 1
        if eval_res.get("thinking_leak"): thinking_leaks_count += 1
            
        results["tool_calling"]["details"].append({
            "id": q["id"],
            "category": q["category"],
            "prompt": q["prompt"],
            "should_call": q["should_call"],
            "expected_tool": q["expected_tool"],
            "score": eval_res["score"],
            "passed": eval_res["passed"],
            "native_tool_called": eval_res.get("native_tool_called"),
            "tool_name": eval_res.get("tool_name"),
            "raw_arguments": eval_res.get("raw_arguments"),
            "parsed_args": eval_res.get("parsed_args"),
            "param_loop_detected": eval_res.get("param_loop_detected"),
            "xml_leak_detected": eval_res.get("xml_leak_detected"),
            "thinking_leak": eval_res.get("thinking_leak"),
            "error_reason": eval_res.get("error_reason"),
            "elapsed_seconds": round(elapsed, 2),
            "prompt_tokens": prompt_t,
            "completion_tokens": comp_t,
            "prompt_speed_tps": p_speed,
            "gen_speed_tps": g_speed,
            "raw_content": raw_content[:400],
            "reasoning_content": reasoning[:200]
        })
    
    expected_calls = sum(1 for q in TOOL_QUESTIONS if q["should_call"])
    avg_prompt_speed = round(sum(prompt_speeds) / len(prompt_speeds), 1) if prompt_speeds else 0.0
    avg_gen_speed = round(sum(gen_speeds) / len(gen_speeds), 1) if gen_speeds else 0.0
    
    tool_summary = {
        "total_questions": len(TOOL_QUESTIONS),
        "avg_score": round(sum(tool_scores) / len(tool_scores), 1),
        "pass_rate": round(sum(1 for s in tool_scores if s == 100.0) / len(TOOL_QUESTIONS) * 100, 1),
        "native_parse_rate": round(native_calls_count / expected_calls * 100, 1) if expected_calls else 100.0,
        "json_valid_rate": round(json_valid_count / len(TOOL_QUESTIONS) * 100, 1),
        "param_match_rate": round(param_matched_count / len(TOOL_QUESTIONS) * 100, 1),
        "param_loop_count": param_loop_count,
        "xml_leak_count": xml_leak_count,
        "thinking_leak_count": thinking_leaks_count,
        "avg_prompt_speed_tps": avg_prompt_speed,
        "avg_gen_speed_tps": avg_gen_speed,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens
    }
    results["tool_calling"]["summary"] = tool_summary
    results["overall_score"] = tool_summary["avg_score"]
    
    print("\n" + "=" * 76)
    print(f"  评测完成！工具调用总得分: {results['overall_score']} 分")
    print(f"  完美通过率: {tool_summary['pass_rate']}% | 原生结构化解析率: {tool_summary['native_parse_rate']}%")
    print(f"  🚀 平均输入处理速度 (Prompt): {avg_prompt_speed} tok/s")
    print(f"  ⚡ 平均输出生成速度 (Gen)   : {avg_gen_speed} tok/s")
    print(f"  死循环数: {param_loop_count} 次 | XML污染数: {xml_leak_count} 次 | 思维泄露: {thinking_leaks_count} 次")
    print("=" * 76)
    
    out_file = args.out
    if not out_file:
        os.makedirs(os.path.join(HERE, "..", "eval_results"), exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sanitized_model = re.sub(r"[^\w\-.]", "_", actual_model)
        out_file = os.path.join(HERE, "..", "eval_results", f"bench_{sanitized_model}_{args.template_name}_{stamp}.json")
    
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"  结果已写入: {out_file}")
    return results, out_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Qwen3.8 Dedicated Tool Calling Benchmark Runner with Speed Tracking")
    parser.add_argument("--api-base", default="http://127.0.0.1:8081/v1", help="llama-server API 地址")
    parser.add_argument("--api-key", default="llamacpp", help="API Key (默认 llamacpp)")
    parser.add_argument("--model", default="local-model", help="模型别名")
    parser.add_argument("--template-name", default="Fixed-Medium", help="模板名称 (Sharp-Medium / Fixed-Medium)")
    parser.add_argument("--concurrency-mode", default="parallel-1", help="并发配置")
    parser.add_argument("--temperature", type=float, default=0.2, help="采样温度")
    parser.add_argument("--max-tokens", type=int, default=1024, help="最大生成 Token 数")
    parser.add_argument("--timeout", type=int, default=60, help="单题超时秒数")
    parser.add_argument("--out", default="", help="结果输出 JSON 文件路径")
    
    args = parser.parse_args()
    run_tool_benchmark(args)
