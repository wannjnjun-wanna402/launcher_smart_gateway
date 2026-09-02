#!/usr/bin/env python
# -*- coding: utf-8 -*-
import subprocess, json, time, sys, os, re, tempfile
from pathlib import Path

LLAMA_DIR = Path(r"E:\llama-win-cuda-12.4-x64")
LLAMA_CLI = LLAMA_DIR / "llama-cli.exe"
MODELS_DIR = Path(r"E:\models")
TIMEOUT_SEC = 300
N_PREDICT = 512
PROMPT = "Write a detailed technical paragraph about the future of artificial intelligence."

MODELS = [
    {"name": "Qwen3VL-8B-Instruct-Q4_K_M", "file": "Qwen3VL-8B-Instruct-Q4_K_M.gguf", "bestfor": "视觉理解 图文对话",
     "args": ["-ngl", "99", "--cache-type-k", "f16", "--cache-type-v", "f16", "-c", "98304", "-b", "2048", "-t", "6",
              "--flash-attn", "enabled", "--temp", "0", "--top-k", "1",
              "--mmproj", str(MODELS_DIR / "mmproj-Qwen3VL-8B-Instruct-F16.gguf")]},
    {"name": "Qwen3.6-27B-MTP-IQ4_XS-Q8nextn", "file": "Qwen3.6-27B-MTP-IQ4_XS-Q8nextn.gguf", "bestfor": "越狱文本 轻量投机",
     "args": ["-ngl", "99", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "-c", "98304", "-b", "2048", "-t", "6",
              "--parallel", "1", "--flash-attn", "enabled", "--reasoning", "on", "--reasoning-budget", "2048",
              "--spec-type", "draft-mtp", "--spec-draft-n-max", "4", "--spec-draft-n-min", "1",
              "--temp", "0", "--top-k", "1", "--top-p", "0.9", "--min-p", "0.0", "--repeat-penalty", "1.05"]},
    {"name": "Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P", "file": "Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf", "bestfor": "深度自由 长文创作",
     "args": ["-ngl", "99", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "-c", "98304", "-b", "2048", "-t", "6",
              "--parallel", "1", "--flash-attn", "enabled", "--reasoning", "on", "--reasoning-budget", "2048",
              "--temp", "0", "--top-k", "1", "--top-p", "0.9", "--min-p", "0.0", "--repeat-penalty", "1.05"]},
    {"name": "Qwen3.6-35B-A3B-APEX-MTP-I-Compact", "file": "Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf", "bestfor": "极速思考 投机加速",
     "args": ["-ngl", "99", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "-c", "81920", "-b", "2048", "-t", "6",
              "--parallel", "1", "--flash-attn", "enabled", "--reasoning", "on", "--reasoning-budget", "2048",
              "--spec-type", "draft-mtp", "--spec-draft-n-max", "3", "--spec-draft-n-min", "1",
              "--temp", "0", "--top-k", "1", "--top-p", "0.9", "--min-p", "0.0", "--repeat-penalty", "1.05"]},
    {"name": "Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M", "file": "Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf", "bestfor": "极速自由 图文畅聊",
     "args": ["-ngl", "99", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "-c", "81920", "-b", "2048", "-t", "6",
              "--parallel", "1", "--flash-attn", "enabled", "--reasoning", "on", "--reasoning-budget", "2048",
              "--temp", "0", "--top-k", "1", "--top-p", "0.9", "--min-p", "0.0", "--repeat-penalty", "1.05",
              "--mmproj", str(MODELS_DIR / "mmproj-Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-f16.gguf")]},
    {"name": "Ornith-1.0-35B-UD-Q4_K_XL", "file": "Ornith-1.0-35B-UD-Q4_K_XL.gguf", "bestfor": "深度对话 精准回答",
     "args": ["-ngl", "99", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "-c", "81920", "-b", "2048", "-t", "6",
              "--parallel", "1", "--flash-attn", "enabled",
              "--temp", "0", "--top-k", "1", "--top-p", "0.9", "--min-p", "0.0", "--repeat-penalty", "1.05",
              "--mmproj", str(MODELS_DIR / "Ornith-1.0-35B-mmproj-BF16.gguf")]},
]

results = []

def banner(text):
    print("\n" + "=" * 70 + "\n  " + text + "\n" + "=" * 70)
    sys.stdout.flush()

def run(model):
    path = MODELS_DIR / model["file"]
    if not path.exists():
        print("  [跳过] 文件不存在:", path)
        return None

    print("  清理残留进程...", end=" ")
    sys.stdout.flush()
    try:
        subprocess.run(["taskkill", "/F", "/IM", "llama-cli.exe"], capture_output=True, timeout=5)
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True, timeout=5)
        print("done")
    except:
        print("跳过")
    time.sleep(1)

    cmd = [str(LLAMA_CLI), "-m", str(path), "-n", str(N_PREDICT), "--prompt", PROMPT, "--single-turn"] + model["args"]
    print("  命令: llama-cli.exe -m", model["file"], "-n", N_PREDICT, "...")
    sys.stdout.flush()

    # 写临时文件避免管道缓冲问题
    tf = tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".txt", encoding="utf-8")
    tmp = tf.name
    tf.close()

    t0 = time.time()
    cs = ttft = ps = gs = None
    first_ok = False

    try:
        with open(tmp, "w", encoding="utf-8") as out:
            proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, text=True)
            while True:
                r = proc.poll()
                if r is not None:
                    break
                t = time.time() - t0
                if t > TIMEOUT_SEC:
                    print("\n  [超时]", int(t), "s")
                    proc.kill()
                    break
                if int(t) % 10 == 0 and t > 5:
                    print("  运行中...", int(t), "s")
                    sys.stdout.flush()
                time.sleep(1)
            proc.wait(timeout=5)

        with open(tmp, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # Skip patterns that are NOT model output (status/progress/info lines)
        skip_patterns = ["llama:", "loading", "model", "warn", "error", "info",
                        "▄", "█", "▀", "░", "╂", "╊",
                        "/exit", "/regen", "/clear", "/read", "/glob", "/image", "/video",
                        "[Start thinking]", "[End thinking]",
                        "System Info", "llama.cpp", "build", "CPU", "GPU",
                        "loaded in", "prompt eval", "eval time",
                        "Generation:", "Prompt:", "total time",
                        ">", "available commands"]

        print("  输出", len(lines), "行")
        for line in lines:
            line = line.rstrip()

            # Detect cold start & TTFT:
            # In single-turn mode, there's no "> " prompt marker.
            # The first non-skip line with actual content = first token.
            if not first_ok and line.strip():
                is_skip = any(p.lower() in line.lower() for p in skip_patterns)
                if not is_skip and len(line.strip()) > 2:
                    if not cs:
                        cs = time.time() - t0
                        print("  [模型就绪] 冷启动 %.1fs" % cs)
                    ttft = time.time() - t0 - cs
                    first_ok = True
                    print("  [首token] TTFT %.2fs" % ttft)

            m = re.search(r'Generation:\s*([\d.]+)\s*t/s', line)
            if m:
                gs = float(m.group(1))
            m2 = re.search(r'Prompt:\s*([\d.]+)\s*t/s', line)
            if m2:
                ps = float(m2.group(1))

        print("  最后输出:")
        for line in lines[-15:]:
            print("   ", line.rstrip()[:140])
        sys.stdout.flush()

        return {"cs": round(cs, 1) if cs else None, "ttft": round(ttft, 2) if ttft else None,
                "ps": round(ps, 1) if ps else None, "gs": round(gs, 1) if gs else None}

    except Exception as e:
        print("  [异常]", e)
        return None
    finally:
        t = time.time() - t0
        if gs:
            cs_str = "%.1fs" % cs if cs else "-"
            ttft_str = "%.2fs" % ttft if ttft else "-"
            print("  >> TPS: %.1f  冷启动: %s  TTFT: %s" % (gs, cs_str, ttft_str))
        else:
            print("  >> 失败 (%ds)" % t)
        sys.stdout.flush()
        try:
            os.unlink(tmp)
        except:
            pass


def main():
    banner("AI 模型速度基准测试 (%d个模型)" % len(MODELS))

    for i, m in enumerate(MODELS, 1):
        banner("[%d/%d] %s" % (i, len(MODELS), m["name"]))
        sys.stdout.flush()

        d = run(m)
        results.append({"name": m["name"], "file": m["file"],
                        "cold_start_s": d["cs"] if d else None, "ttft_s": d["ttft"] if d else None,
                        "prompt_tps": d["ps"] if d else None, "gen_tps": d["gs"] if d else None,
                        "bestfor": m["bestfor"], "status": "ok" if d and d["gs"] else "fail"})

        with open(LLAMA_DIR / "bench_results.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        if i < len(MODELS):
            print("  等待 3 秒...")
            time.sleep(3)

    banner("测试结果")
    print("%-3s %-38s %-9s %-7s %-9s %s" % ("#", "模型", "冷启动", "TTFT", "Prompt", "TPS"))
    print("-" * 80)
    for i, r in enumerate(results, 1):
        cs = ("%ss" % r["cold_start_s"]) if r["cold_start_s"] else "-"
        tt = ("%ss" % r["ttft_s"]) if r["ttft_s"] else "-"
        ps = ("%s t/s" % r["prompt_tps"]) if r["prompt_tps"] else "-"
        gs = ("%s t/s" % r["gen_tps"]) if r["gen_tps"] else "FAIL"
        print("%-3s %-38s %-9s %-7s %-9s %s" % (i, r["name"], cs, tt, ps, gs))

    banner("BENCHMARK_DATA 格式")
    for r in results:
        if r["gen_tps"]:
            print('    "%s" = [PSCustomObject]@{' % r["name"])
            print("        Speed     = %s" % r["gen_tps"])
            print("        Accuracy  = $null")
            print("        Halluc    = $null")
            print("        Score     = $null")
            print("        Rank      = $null")
            print('        BestFor   = "%s"' % r["bestfor"])
            print("    }")

if __name__ == "__main__":
    main()