# AI Model Launcher - PowerShell 7 entry (no cmd involved)
# Ctrl+C fix for pwsh 7: PSReadLine takes over console input, so
# [Console]::TreatControlCAsInput is unreliable. Instead we bind
# Ctrl+C to a no-op via Set-PSReadLineKeyHandler (works in 2.4.5).
# Double-click 启动器-PW7.lnk to start.

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path (Join-Path $scriptDir "logs"))) {
    New-Item -ItemType Directory -Path (Join-Path $scriptDir "logs") | Out-Null
}

# --- Step 1: neutralise Ctrl+C under pwsh 7 ---
# NOTE: Set-PSReadLineKeyHandler only affects interactive prompt editing.
# During script execution (Read-Host), Ctrl+C is a process-level SIGINT,
# so we must use [Console]::CancelKeyPress to cancel it (e.Cancel = $true).
try {
    [Console]::CancelKeyPress += [ConsoleCancelEventHandler]{
        param($sender, $e)
        $e.Cancel = $true
        Write-Host "" -ForegroundColor DarkGray
        Write-Host "[PW7] Ctrl+C intercepted (no-op). Close with the X button." -ForegroundColor Yellow
    }
    Write-Host "[PW7] Ctrl+C intercepted via CancelKeyPress"
} catch {
    # Non-interactive host (e.g. -ListModels) has no console: ignore.
    Write-Host "[PW7] Ctrl+C handler skipped (non-interactive)"
}

# --- Step 2: run the launcher, never let the window die on error ---
$dt = Get-Date -Format "yyyyMMdd"
$dailyLog = Join-Path $scriptDir ("logs\8083_llama_{0}.log" -f $dt)

Write-Host "[PW7] Host: PowerShell $($PSVersionTable.PSVersion.ToString())"
Write-Host "[PW7] Log:  $dailyLog"
Write-Host ""

try {
    & (Join-Path $scriptDir "launcher_main.ps1") -DailyLogFile $dailyLog @args
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
} catch {
    Write-Host ""
    Write-Host "[PW7] ERROR: launcher crashed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "[PW7] Stack:  $($_.ScriptStackTrace)" -ForegroundColor DarkGray
    $exitCode = 1
}

Write-Host ""
Write-Host "[PW7] Launcher exited (code=$exitCode). Closing in 5 seconds..."
Start-Sleep -Seconds 5
exit $exitCode
