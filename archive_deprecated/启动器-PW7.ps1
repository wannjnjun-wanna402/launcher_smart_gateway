# ====================================================================================
#  🤖 AI 通用多模型矩阵启动器 · PowerShell 7 原生终端引擎 (自由选定/切换全模型)
#  专为 E:\models 全量 GGUF 库设计：自由挑选 35B/27B/4B/Ornith/Gemma/PaddleOCR 等任意模型
#  自动挂载 8081 智能协同网关 (OpenAI + Anthropic 双协议/CC Switch/Token计费/实时算力大屏)
#  遵循 AGENTS.md 规范：UTF-8 with BOM 编码 · 单日单文件累加日志 · 严格显存与进程安全生命周期
#  Date: 2026-09-02
# ====================================================================================

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $scriptDir "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

# --- Step 0: 启动前环境与 GPU 显存彻底安全清理 (严格遵守 AGENTS.md 铁律) ---
try {
    Get-Process | Where-Object { $_.ProcessName -match 'llama' } | Stop-Process -Force -ErrorAction SilentlyContinue
    Get-NetTCPConnection -LocalPort 8083 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
} catch { }

# 循环检查 GPU 显存直至安全释放 (< 600MB)
for ($waitIdx = 0; $waitIdx -lt 10; $waitIdx++) {
    try {
        $usedStr = (nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null).Trim()
        if ($usedStr -and [int]$usedStr -lt 600) { break }
    } catch { }
    Start-Sleep -Milliseconds 300
}

# --- Step 1: 优雅退出与 Ctrl+C 信号管理 ---
try {
    [Console]::TreatControlCAsInput = $false
    [Console]::CancelKeyPress += [ConsoleCancelEventHandler]{
        param($sender, $e)
        Write-Host ""
        Write-Host "  ⚠️ 接收到退出信号，正在安全关闭 AI 进程并回收显存..." -ForegroundColor Yellow
        try {
            Get-Process | Where-Object { $_.ProcessName -match 'llama' } | Stop-Process -Force -ErrorAction SilentlyContinue
            Get-NetTCPConnection -LocalPort 8083 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
        } catch { }
    }
} catch { }

# --- Step 2: 绑定每日主脑日志文件 ---
$dt = Get-Date -Format "yyyyMMdd"
$dailyLog = Join-Path $logDir ("8083_llama_{0}.log" -f $dt)

Write-Host "====================================================================================" -ForegroundColor Cyan
Write-Host "   🤖 AI 通用多模型矩阵启动器 · PowerShell 7 (全模型自由挑选 · 8081智能协同网关)" -ForegroundColor Green
Write-Host "====================================================================================" -ForegroundColor Cyan
Write-Host "   [网关统一入口] http://127.0.0.1:8081/v1 (全应用统一接入点 · 支持 OpenAI / Anthropic)" -ForegroundColor White
Write-Host "   [实时监控大屏] http://127.0.0.1:8081/dashboard (动态槽位 / 瞬时吞吐 / Token账本)" -ForegroundColor White
Write-Host "   [运行环境架构] PS $($PSVersionTable.PSVersion.ToString()) · 单日累加日志: 8083_llama_$dt.log" -ForegroundColor DarkGray
Write-Host "====================================================================================" -ForegroundColor Cyan
Write-Host ""

$exitCode = 0
try {
    & (Join-Path $scriptDir "launcher_main.ps1") -DailyLogFile $dailyLog @args
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
} catch {
    Write-Host ""
    Write-Host "  ❌ 启动器运行异常: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  StackTrace: $($_.ScriptStackTrace)" -ForegroundColor DarkGray
    $exitCode = 1
} finally {
    # 退出时彻底清场，杜绝 GPU 显存泄漏
    try {
        Get-Process | Where-Object { $_.ProcessName -match 'llama' } | Stop-Process -Force -ErrorAction SilentlyContinue
        Get-NetTCPConnection -LocalPort 8083 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    } catch { }
}

Write-Host ""
Write-Host "  ✅ 服务已完全安全退出，GPU 显存已释放。" -ForegroundColor Green
Start-Sleep -Seconds 2
exit $exitCode
