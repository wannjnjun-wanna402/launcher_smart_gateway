# -*- coding: utf-8 -*-
"""
llama.cpp 输出速度自动测速 (V100-PCIe 32GB)
纯标准库实现：逐个配置启动 llama-server -> 等 /health -> 发固定 prompt 测解码速度 -> 杀进程 -> 下一套
结果写入 results.csv 与 summary.txt（按 eval tok/s 降序）
"""
import subprocess, urllib.request, urllib.error, json, time, os, re, sys

EXE   = r"E:\llama-win-cuda-12.4-x64\llama-server.exe"
MODEL = r"E:\models\Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf"
PORT  = "8081"
HOST  = "127.0.0.1"
CTX   = "16384"
NPREDICT = 512
PROMPT = ("请详细解释量子计算的基本原理，包括叠加态、量子纠缠和量子门，"
          "并举例说明其在密码学与优化问题上的潜在优势，以及当前工程实现面临的主要物理限制。")

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(HERE, "runs")
os.makedirs(RUNS_DIR, exist_ok=True)
RESULTS = os.path.join(HERE, "results.csv")
SUMMARY = os.path.join(HERE, "summary.txt")


def kill_existing():
    try:
        subprocess.run(["taskkill", "/IM", "llama-server.exe", "/F"],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def build_args(reasoning, nmax):
    args = [EXE, "-m", MODEL, "--host", HOST, "--port", PORT,
            "-ngl", "99", "-c", CTX, "-b", "2048", "--ubatch-size", "2048",
            "-t", "6", "--flash-attn", "enabled", "--api-key", "llamacpp",
            "--reasoning", "on" if reasoning else "off"]
    if nmax and nmax > 0:
        args += ["--spec-type", "draft-mtp",
                 "--spec-draft-n-max", str(nmax), "--spec-draft-n-min", "1"]
    return args


def wait_health(timeout=180):
    url = f"http://{HOST}:{PORT}/health"
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def send_completion():
    url = f"http://{HOST}:{PORT}/completion"
    data = json.dumps({
        "prompt": PROMPT,
        "n_predict": NPREDICT,
        "temperature": 0.0,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer llamacpp"},
        method="POST")
    with urllib.request.urlopen(req, timeout=240) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_log_speed(log_path):
    """备用：从服务器日志里抓 eval 速度 (行内 'tokens per second')"""
    try:
        txt = open(log_path, "r", encoding="utf-8", errors="ignore").read()
        m = re.findall(r"\(([\d.]+)\s*tokens per second\)", txt)
        if m:
            return float(m[-1])
    except Exception:
        pass
    return None


def measure(reasoning, nmax):
    name = f"r{'on' if reasoning else 'off'}_mtp{nmax if nmax else 'off'}"
    log = os.path.join(RUNS_DIR, name + ".log")
    args = build_args(reasoning, nmax)
    kill_existing()
    time.sleep(2)
    print(f"[*] launching {name} ...", flush=True)
    try:
        with open(log, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(args, stdout=lf, stderr=subprocess.STDOUT)
            if not wait_health():
                print(f"[!] {name}: health timeout (see {log})", flush=True)
                proc.terminate()
                return name, None, "health_timeout"
            try:
                res = send_completion()
            except Exception as e:
                print(f"[!] {name}: completion error {e}", flush=True)
                proc.terminate()
                return name, None, f"completion_err:{e}"
            proc.terminate()
        speed = None
        try:
            timings = res.get("timings", {})
            speed = (timings.get("predicted_per_second")
                     or timings.get("predicted_tokens_per_second"))
        except Exception:
            speed = None
        if speed is None:
            speed = parse_log_speed(log)
        status = "ok" if speed is not None else "no_speed"
        print(f"[+] {name}: {speed} tok/s", flush=True)
        return name, speed, status
    except Exception as e:
        print(f"[!] {name}: exception {e}", flush=True)
        return name, None, f"exception:{e}"


def main():
    configs = [(r, n) for r in (False, True)
               for n in (0, 2, 3, 4, 6, 8)]
    rows = []
    for reasoning, nmax in configs:
        name, speed, status = measure(reasoning, nmax)
        rows.append((name, reasoning, nmax, speed, status))
        time.sleep(1)

    # results.csv
    with open(RESULTS, "w", encoding="utf-8") as f:
        f.write("config,reasoning,spec_nmax,eval_tok_s,status\n")
        for name, reasoning, nmax, speed, status in rows:
            f.write(f"{name},{'on' if reasoning else 'off'},"
                    f"{nmax if nmax else 'off'},"
                    f"{speed if speed is not None else ''},{status}\n")

    # summary.txt (sorted)
    valid = [(r[0], r[3]) for r in rows if r[3] is not None]
    valid.sort(key=lambda x: -x[1])
    with open(SUMMARY, "w", encoding="utf-8") as f:
        f.write("=== llama.cpp Speed Benchmark (V100-PCIe 32GB) ===\n")
        f.write(f"model : {MODEL}\nctx   : {CTX}   n_predict: {NPREDICT}\n\n")
        f.write("Ranked by eval tok/s (highest first):\n")
        for i, (nm, sp) in enumerate(valid, 1):
            f.write(f"{i:2d}. {nm:14s} {sp:.2f} tok/s\n")
        f.write("\nAll configs:\n")
        for name, reasoning, nmax, speed, status in rows:
            f.write(f"  {name:14s} reasoning={'on' if reasoning else 'off'} "
                    f"mtp={nmax if nmax else 'off'} -> "
                    f"{speed if speed is not None else 'FAIL'} ({status})\n")
    print("\n=== DONE === results in results.csv / summary.txt", flush=True)


if __name__ == "__main__":
    main()
