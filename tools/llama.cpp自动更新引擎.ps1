# ============================================================
#  llama.cpp 自动更新脚本  v2.0
#  自动检测 GitHub 最新 Release，对比本地版本，
#  下载 + SHA256 校验 + 备份 + 覆盖更新
#  每步带中文说明 + Write-Progress 进度条
# ============================================================
# 用法：
#   powershell -ExecutionPolicy Bypass -File "update-llamacpp.ps1"
#   powershell -ExecutionPolicy Bypass -File "update-llamacpp.ps1" -Force
# ============================================================

param(
    [switch]$Force      # 跳过 "是否下载" 确认
)

$ErrorActionPreference = "Stop"
$LLAMA_DIR = $PSScriptRoot

# ---------- 颜色 ----------
$C_RESET  = [Console]::ForegroundColor
$C_TITLE  = "Cyan"
$C_OK     = "DarkGreen"
$C_WARN   = "Magenta"
$C_ERROR  = "Red"
$C_TAG    = "Cyan"
$C_HINT   = "DarkGray"
$C_MODEL  = "Yellow"

function Write-Color($text, $color) {
    $prev = [Console]::ForegroundColor
    [Console]::ForegroundColor = $color
    Write-Host $text -NoNewline
    [Console]::ForegroundColor = $prev
}

function Write-Line($text, $color) { Write-Color $text $color; Write-Host "" }

function Write-Separator {
    $w = $Host.UI.RawUI.WindowSize.Width
    if ($w -lt 40) { $w = 80 }
    Write-Line ([string]::new('─', $w)) $C_HINT
}

# ============================================================
#  进度条辅助
# ============================================================

$script:overallStep = 0
$script:totalSteps = 8

function Set-OverallProgress {
    param([int]$Step, [string]$Text)
    $script:overallStep = $Step
    $pct = [math]::Round(($Step - 1) / $script:totalSteps * 100, 0)
    Write-Progress -Activity "llama.cpp 更新进度" -Status "步骤 $Step/$($script:totalSteps): $Text" -PercentComplete $pct
}

function Complete-OverallProgress {
    Write-Progress -Activity "llama.cpp 更新进度" -Completed
}

# ============================================================
#  1. 获取本地版本
# ============================================================

function Get-LocalVersion {
    $serverExe = Join-Path $LLAMA_DIR "llama-server.exe"
    if (-not (Test-Path $serverExe)) {
        Write-Line "  ❌ 未找到 llama-server.exe，请确认脚本在 llama.cpp 目录下运行" $C_ERROR
        return $null, $null
    }
    $ver = $null
    $commit = $null
    try {
        $output = & $serverExe --version 2>&1
        if ($output -match 'version:\s*(\d+)') { $ver = $Matches[1] }
        if ($output -match '\(([a-f0-9]+)\)') { $commit = $Matches[1] }
    } catch {
        $msg = $_.Exception.Message
        if ($msg -match 'version:\s*(\d+)') { $ver = $Matches[1] }
        if ($msg -match '\(([a-f0-9]+)\)') { $commit = $Matches[1] }
    }
    if (-not $ver) {
        try {
            $tf = [System.IO.Path]::GetTempFileName()
            cmd /c "`"$serverExe`" --version > `"$tf`" 2>&1" | Out-Null
            $c = [System.IO.File]::ReadAllText($tf)
            Remove-Item $tf -ErrorAction SilentlyContinue
            if ($c -match 'version:\s*(\d+)') { $ver = $Matches[1] }
            if ($c -match '\(([a-f0-9]+)\)') { $commit = $Matches[1] }
        } catch { }
    }
    return $ver, $commit
}

# ============================================================
#  2. 获取 GitHub 最新 Release
# ============================================================

function Get-GitHubLatest {
    $apiUrl = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
    try {
        $response = Invoke-RestMethod -Uri $apiUrl -UserAgent "llama-updater" -TimeoutSec 15
        $tag = $response.tag_name
        $version = $tag -replace '^b', ''
        $htmlUrl = $response.html_url

        $asset = $null
        foreach ($a in $response.assets) {
            if ($a.name -match 'cudart.*cuda-12\.4.*x64.*\.zip') {
                $asset = $a
                break
            }
        }
        if (-not $asset) { return $null }

        $sha256 = ""
        if ($asset.digest -match 'sha256:([a-f0-9]{64})') {
            $sha256 = $Matches[1]
        }

        return @{
            Tag       = $tag
            Version   = $version
            Name      = $asset.name
            Size      = $asset.size
            DownloadUrl = $asset.browser_download_url
            Sha256    = $sha256
            HtmlUrl   = $htmlUrl
        }
    } catch { return $null }
}

# ============================================================
#  主流程
# ============================================================

Clear-Host
Write-Separator
Write-Line "    llama.cpp  一键更新工具" $C_TITLE
Write-Line "    每步带进度条，请耐心等待" $C_TITLE
Write-Separator

# ============================================================
#  步骤 1/8 : 检测本地版本
# ============================================================
Set-OverallProgress -Step 1 -Text "检测本地版本"
Write-Color "  [1/$($script:totalSteps)] 检测本地版本 ... " $C_HINT

$localVer, $localCommit = Get-LocalVersion
if (-not $localVer) {
    Write-Line "❌" $C_ERROR
    Write-Line "  未找到 llama-server.exe，请确认脚本在 llama.cpp 目录下运行" $C_ERROR
    Read-Host "  按回车退出"
    Complete-OverallProgress
    exit 1
}

Write-Line "✓" $C_OK
Write-Color "  当前本地版本: " $C_HINT
Write-Color "b$localVer" $C_MODEL
if ($localCommit) { Write-Color "  ($($localCommit.Substring(0,7)))" $C_HINT }
Write-Host ""

# ============================================================
#  步骤 2/8 : 查询 GitHub 最新版本
# ============================================================
Set-OverallProgress -Step 2 -Text "查询 GitHub 最新版本"
Write-Color "  [2/$($script:totalSteps)] 查询 GitHub 最新 Release ... " $C_HINT

$remote = Get-GitHubLatest
if (-not $remote) {
    Write-Line "⚠ 使用备用版本" $C_WARN
    $remote = @{
        Tag = "b9993"
        Version = "9993"
        Name = "cudart-llama-bin-win-cuda-12.4-x64.zip"
        Size = 391443627
        DownloadUrl = "https://github.com/ggml-org/llama.cpp/releases/download/b9993/cudart-llama-bin-win-cuda-12.4-x64.zip"
        Sha256 = ""
        HtmlUrl = "https://github.com/ggml-org/llama.cpp/releases/tag/b9993"
    }
}
Write-Line "✓" $C_OK

Write-Color "  最新 Release: " $C_HINT
Write-Color $remote.Tag $C_TAG
Write-Color "  ($([math]::Round($remote.Size/1MB,0)) MB)" $C_HINT
Write-Host ""

# ============================================================
#  步骤 3/8 : 版本对比
# ============================================================
Set-OverallProgress -Step 3 -Text "版本对比"
Write-Color "  [3/$($script:totalSteps)] 版本对比 ... " $C_HINT

$localNum = [int]$localVer
$remoteNum = [int]$remote.Version
$needUpdate = $false

if ($remoteNum -gt $localNum) {
    $needUpdate = $true
    Write-Line "发现新版本!" $C_OK
    Write-Color "  " $C_HINT
    Write-Color "b$localVer" $C_MODEL
    Write-Color " → " $C_HINT
    Write-Line "b$($remote.Version)" $C_OK
} elseif ($remoteNum -eq $localNum) {
    Write-Line "已是最新版" $C_OK
    Write-Color "  " $C_HINT
    Write-Line "b$localVer  (如需强制重装，请加 -Force 参数)" $C_TAG
    if (-not $Force) {
        Write-Host ""
        Read-Host "  按回车退出"
        Complete-OverallProgress
        exit 0
    }
    $needUpdate = $true
} else {
    Write-Line "本地版本更高" $C_WARN
    Write-Color "  本地 b$localVer > GitHub b$($remote.Version)，无需更新" $C_WARN
    Write-Host ""
    Read-Host "  按回车退出"
    Complete-OverallProgress
    exit 0
}

if (-not $needUpdate) {
    Read-Host "  按回车退出"
    Complete-OverallProgress
    exit 0
}

Write-Host ""
if (-not $Force) {
    Write-Color "  是否下载并更新? [y/N]: " $C_HINT
    $confirm = Read-Host
    if ($confirm -ne "y" -and $confirm -ne "Y") {
        Write-Line "  已取消。" $C_WARN
        Complete-OverallProgress
        exit 0
    }
}

$zipPath = Join-Path $LLAMA_DIR $remote.Name
$backupDir = Join-Path $LLAMA_DIR "backup_b$localVer"

# ============================================================
#  步骤 4/8 : 下载更新包
# ============================================================
Set-OverallProgress -Step 4 -Text "下载更新包"
Write-Color "  [4/$($script:totalSteps)] 下载更新包 ... " $C_HINT

try {
    $webClient = New-Object System.Net.WebClient
    $webClient.Headers.Add("User-Agent", "llama-updater")
    Register-ObjectEvent -InputObject $webClient -EventName DownloadProgressChanged -Action {
        $pct = $eventArgs.ProgressPercentage
        $received = $eventArgs.BytesReceived / 1MB
        $total = $eventArgs.TotalBytesToReceive / 1MB
        $global:downloadPct = $pct
        Write-Progress -Activity "下载 cudart-llama-bin-win-cuda-12.4-x64.zip" `
            -Status "已下载 $([math]::Round($received,1)) MB / $([math]::Round($total,1)) MB ($pct%)" `
            -PercentComplete $pct
    } | Out-Null
    $webClient.DownloadFile($remote.DownloadUrl, $zipPath)
    Write-Progress -Activity "下载" -Completed
    Write-Line "✓" $C_OK
} catch {
    Write-Line "❌" $C_ERROR
    Write-Color "  下载失败: " $C_HINT; Write-Line $_ $C_ERROR
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force -ErrorAction SilentlyContinue }
    Complete-OverallProgress
    Read-Host "  按回车退出"
    exit 1
}

# ============================================================
#  步骤 5/8 : SHA256 校验
# ============================================================
Set-OverallProgress -Step 5 -Text "SHA256 校验"
Write-Color "  [5/$($script:totalSteps)] SHA256 校验 ... " $C_HINT

if ($remote.Sha256) {
    try {
        Write-Progress -Activity "SHA256 校验" -Status "计算文件哈希 ..." -PercentComplete 50
        $actualHash = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.ToLower()
        Write-Progress -Activity "SHA256 校验" -Status "对比哈希值 ..." -PercentComplete 90
        if ($actualHash -eq $remote.Sha256.ToLower()) {
            Write-Progress -Activity "SHA256 校验" -Completed
            Write-Line "✓ 匹配" $C_OK
        } else {
            Write-Progress -Activity "SHA256 校验" -Completed
            Write-Line "❌ 不匹配" $C_ERROR
            Write-Color "  期望: " $C_HINT; Write-Line $remote.Sha256 $C_ERROR
            Write-Color "  实际: " $C_HINT; Write-Line $actualHash $C_ERROR
            Write-Line "  文件可能已损坏或被篡改，请重试。" $C_ERROR
            Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
            Complete-OverallProgress
            Read-Host "  按回车退出"
            exit 1
        }
    } catch {
        Write-Progress -Activity "SHA256 校验" -Completed
        Write-Line "⚠ 校验失败: $_" $C_WARN
    }
} else {
    Write-Progress -Activity "SHA256 校验" -Completed
    Write-Line "GitHub 未提供，跳过校验" $C_HINT
}

# ============================================================
#  步骤 6/8 : 备份旧文件
# ============================================================
Set-OverallProgress -Step 6 -Text "备份旧文件"
Write-Color "  [6/$($script:totalSteps)] 备份旧文件 → $backupDir ... " $C_HINT

try {
    Write-Progress -Activity "备份旧文件" -Status "创建备份目录 ..." -PercentComplete 10
    if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory -Path $backupDir -Force | Out-Null }

    $backupPatterns = @("*.exe", "*.dll")
    $backupExclude  = @($zipPath)
    $backupCount = 0
    $totalFiles = 0

    # 先统计总数
    foreach ($pattern in $backupPatterns) {
        $totalFiles += (Get-ChildItem -Path $LLAMA_DIR -File -Filter $pattern).Count
    }
    if ($totalFiles -eq 0) { $totalFiles = 1 }

    $copiedSoFar = 0
    foreach ($pattern in $backupPatterns) {
        Get-ChildItem -Path $LLAMA_DIR -File -Filter $pattern | ForEach-Object {
            $inBackup = $_.FullName -replace [regex]::Escape($LLAMA_DIR), [regex]::Escape($backupDir)
            $targetDir = Split-Path $inBackup -Parent
            if (-not (Test-Path $targetDir)) { New-Item -ItemType Directory -Path $targetDir -Force | Out-Null }
            Copy-Item -Path $_.FullName -Destination $inBackup -Force
            $backupCount++
            $copiedSoFar++
            $bpct = [math]::Round($copiedSoFar / $totalFiles * 100, 0)
            Write-Progress -Activity "备份旧文件" -Status "已备份 $backupCount 个文件" -PercentComplete $bpct
        }
    }
    Write-Progress -Activity "备份旧文件" -Completed
    Write-Line "✓ 已备份 $backupCount 个文件" $C_OK
} catch {
    Write-Progress -Activity "备份旧文件" -Completed
    Write-Line "⚠ 备份失败: $_" $C_WARN
}

# ============================================================
#  步骤 7/8 : 解压并覆盖
# ============================================================
Set-OverallProgress -Step 7 -Text "解压并覆盖文件"
Write-Color "  [7/$($script:totalSteps)] 解压并覆盖文件 ... " $C_HINT

try {
    Write-Progress -Activity "解压覆盖" -Status "清理旧文件 ..." -PercentComplete 20
    $oldFilesToRemove = @()
    Get-ChildItem -Path $LLAMA_DIR -File -Filter "*.exe" | ForEach-Object { $oldFilesToRemove += $_.FullName }
    Get-ChildItem -Path $LLAMA_DIR -File -Filter "*.dll" | ForEach-Object { $oldFilesToRemove += $_.FullName }
    $removeCount = 0
    foreach ($f in $oldFilesToRemove) {
        if (Test-Path $f) {
            try { Remove-Item $f -Force -ErrorAction SilentlyContinue; $removeCount++ } catch { }
        }
    }

    Write-Progress -Activity "解压覆盖" -Status "已清理 $removeCount 个旧文件，正在解压 ..." -PercentComplete 50
    Expand-Archive -Path $zipPath -DestinationPath $LLAMA_DIR -Force

    Write-Progress -Activity "解压覆盖" -Completed
    Write-Line "✓" $C_OK
} catch {
    Write-Progress -Activity "解压覆盖" -Completed
    Write-Line "❌" $C_ERROR
    Write-Color "  解压/覆盖失败: " $C_HINT; Write-Line $_ $C_ERROR
    Write-Line "  重要：旧文件已备份到 $backupDir，可手动恢复" $C_WARN
    Complete-OverallProgress
    Read-Host "  按回车退出"
    exit 1
}

# 清理 zip
Remove-Item $zipPath -Force -ErrorAction SilentlyContinue

# ============================================================
#  步骤 8/8 : 验证新版本
# ============================================================
Set-OverallProgress -Step 8 -Text "验证新版本"
Write-Color "  [8/$($script:totalSteps)] 验证新版本 ... " $C_HINT

Write-Progress -Activity "验证新版本" -Status "正在读取版本号 ..." -PercentComplete 50
Start-Sleep -Milliseconds 500
$newVer, $newCommit = Get-LocalVersion

if ($newVer) {
    Write-Progress -Activity "验证新版本" -Completed
    Write-Line "✓" $C_OK
    Write-Host ""
    Write-Separator
    Write-Line "  ✅ 更新完成！" $C_OK
    Write-Color "  旧版本: " $C_HINT; Write-Line "b${localVer} ($($localCommit.Substring(0,7)))" $C_MODEL
    Write-Color "  新版本: " $C_HINT; Write-Line "b${newVer} ($($newCommit.Substring(0,7)))" $C_OK
    Write-Color "  备份目录: " $C_HINT; Write-Line $backupDir $C_TAG
    Write-Separator
    Write-Host ""
    Write-Color "  如需回滚，请运行: " $C_HINT
    Write-Line "" $C_RESET
    Write-Color "    Copy-Item -Path ""$backupDir\*"" -Destination ""$LLAMA_DIR\"" -Force" $C_TAG
    Write-Host ""
    Write-Color "  或手动从备份目录复制文件" $C_HINT
    Write-Host ""
} else {
    Write-Progress -Activity "验证新版本" -Completed
    Write-Line "⚠ 无法读取新版本号，请手动验证" $C_WARN
    Write-Color "  备份位于: " $C_HINT; Write-Line $backupDir $C_TAG
}

Complete-OverallProgress
Read-Host "  按回车退出"