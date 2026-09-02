import subprocess
import json
import time

def get_cpu_processes():
    # 1. Get instantaneous CPU percentage per process over 1 second
    ps_script = """
    $p1 = Get-Process | Select-Object Id, ProcessName, CPU, WorkingSet64, Path
    Start-Sleep -Seconds 1
    $p2 = Get-Process | Select-Object Id, ProcessName, CPU, WorkingSet64, Path

    $table = foreach ($proc in $p2) {
        $prev = $p1 | Where-Object { $_.Id -eq $proc.Id }
        if ($prev -and $proc.CPU -and $prev.CPU) {
            $cpuPct = [math]::Round(($proc.CPU - $prev.CPU) / [Environment]::ProcessorCount * 100, 1)
            [PSCustomObject]@{
                Id = $proc.Id
                Name = $proc.ProcessName
                CpuPct = $cpuPct
                CpuTotal = [math]::Round($proc.CPU, 1)
                RamMB = [math]::Round($proc.WorkingSet64 / 1MB, 1)
                Path = $proc.Path
            }
        }
    }
    $table | Sort-Object CpuPct -Descending | Select-Object -First 20 | ConvertTo-Json
    """
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True, encoding="utf-8")
    try:
        data = json.loads(res.stdout)
        if isinstance(data, dict):
            data = [data]
        return data
    except Exception as e:
        print("Error parsing json:", e, res.stdout, res.stderr)
        return []

if __name__ == "__main__":
    print("=" * 80)
    print(f"{'PID':<8} {'进程名':<25} {'当前瞬时CPU%':<14} {'累计CPU秒':<12} {'内存(MB)':<10} {'路径'}")
    print("=" * 80)
    procs = get_cpu_processes()
    for p in procs:
        pid = p.get('Id', 0)
        name = p.get('Name', '')
        cpu_pct = p.get('CpuPct', 0)
        cpu_tot = p.get('CpuTotal', 0)
        ram = p.get('RamMB', 0)
        path = p.get('Path') or '系统服务 / 无权限'
        if len(path) > 45:
            path = "..." + path[-42:]
        print(f"{pid:<8} {name:<25} {cpu_pct:<14} {cpu_tot:<12} {ram:<10} {path}")
    print("=" * 80)
