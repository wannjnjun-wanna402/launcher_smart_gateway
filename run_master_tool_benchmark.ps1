<#
=============================================================================
Qwen3.8 4大核心模型 × 2大模板 工具调用大满贯全自动长测编排器
(UTF-8 with BOM for Windows PowerShell 5.1 & PowerShell 7)
=============================================================================
评测目标：
  [1] 模型 [6]  : Qwen3.8-27B-NVFP4-MTP-MID-HIGH (官方基准)
  [2] 模型 [8]  : Qwen3.8-27B-Abliterated-Q6_K    (无审核 A-Q6_K)
  [3] 模型 [9]  : Qwen3.8-27B-UD-Q5_K_XL          (UD 深度微调 UD-Q5KXL)
  [4] 模型 [10] : Qwen3.8-27B-Uncensored-Q6_K     (无审查 U-Q6_K)

对比模板 (均中级思维 medium)：
  [A] Fixed-Medium : Qwen-Fixed-Chat-Templates (froggeric 修复模板)
  [B] Sharp-Medium : Qwen-Sharp-Chat-Templates (Sharp 精简模板)

总轮次：4 模型 × 2 模板 = 8 轮连续全自动测试 (每轮仅需 ~15 秒，全测约 2.5 分钟)
完成后自动生成聚合大屏 HTML 诊断报告，一目了然找出最优解！
=============================================================================
#>

# 强制 UTF-8 编码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ROOT_DIR = $PSScriptRoot
$MODELS_DIR = "E:\models"
$LLAMA_SERVER = Join-Path $ROOT_DIR "llama-server.exe"
$PYTHON_EXE = "C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe"
if (-not (Test-Path $PYTHON_EXE)) { $PYTHON_EXE = "python.exe" }
$API_KEY = "llamacpp"
$CTX_SIZE = 65536   # 64K 轻量上下文，极速完成测试

# 目标 4 个模型定义
$TARGET_MODELS = @(
    @{
        Key  = "NVFP4-MID-HIGH"
        Name = "Qwen3.8-27B-MID-HIGH (NVFP4官方基准)"
        File = Join-Path $MODELS_DIR "Qwen3.8-27B-NVFP4-MTP-MID-HIGH.gguf"
    },
    @{
        Key  = "A-Q6_K"
        Name = "Qwen3.8-27B-A-Q6_K (无审核Abliterated)"
        File = Join-Path $MODELS_DIR "Qwen3.8-27B-Abliterated-Q6_K.gguf"
    },
    @{
        Key  = "UD-Q5KXL"
        Name = "Qwen3.8-27B-UD-Q5KXL (UD深度微调)"
        File = Join-Path $MODELS_DIR "Qwen3.8-27B-UD-Q5_K_XL.gguf"
    },
    @{
        Key  = "U-Q6_K"
        Name = "Qwen3.8-27B-U-Q6_K (无审查Uncensored)"
        File = Join-Path $MODELS_DIR "Qwen3.8-27B-Uncensored-Q6_K.gguf"
    }
)

# 目标 2 个模板定义
$TEMPLATES = @(
    @{
        Key  = "Fixed-Medium"
        Name = "Fixed-Medium (froggeric)"
        File = Join-Path $ROOT_DIR "chat_template_qwen_fixed.jinja"
    },
    @{
        Key  = "Sharp-Medium"
        Name = "Sharp-Medium (Qwen-Sharp)"
        File = Join-Path $ROOT_DIR "tools\sharp_repo\chat_template.jinja"
    }
)

# 彻底清理后台进程并冷却显存
function Cleanup-LlamaEnvironment {
    Write-Host "  [清理] 正在安全终止 llama 服务进程..." -ForegroundColor Cyan
    Get-Process | Where-Object { $_.ProcessName -match "llama" } | Stop-Process -Force -ErrorAction SilentlyContinue
    
    try {
        $conns = Get-NetTCPConnection -LocalPort 8081 -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    } catch { }
    
    $maxWait = 45
    $waited = 0
    while ($waited -lt $maxWait) {
        try {
            $smiOut = & nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null
            if ($smiOut) {
                $usedMB = [int]($smiOut.Trim())
                if ($usedMB -lt 500) {
                    Write-Host "  [就绪] GPU 显存已释放 (当前占用: ${usedMB} MB)" -ForegroundColor Green
                    break
                } else {
                    Write-Host "  [冷却] 显存占用: ${usedMB} MB，等待释放中 (${waited}s)..." -ForegroundColor Yellow
                }
            }
        } catch { break }
        Start-Sleep -Seconds 2
        $waited += 2
    }
}

# 启动单次 llama-server
function Start-LlamaServerInstance {
    param(
        [string]$ModelPath,
        [string]$TemplateFile,
        [string]$TemplateName
    )
    
    $argsList = @(
        "-m", $ModelPath,
        "-ngl", "99",
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        "-c", $CTX_SIZE.ToString(),
        "-b", "2048",
        "--ubatch-size", "2048",
        "-t", "6",
        "--parallel", "1",
        "--flash-attn", "on",
        "--ctx-checkpoints", "4",
        "--spec-type", "draft-mtp",
        "--spec-draft-n-max", "2",
        "--spec-draft-n-min", "1",
        "--reasoning", "auto",
        "--reasoning-budget", "2048",
        "--reasoning-effort", "medium",
        "--reasoning-format", "deepseek",
        "--reasoning-preserve",
        "--no-warmup",
        "--temp", "0.2",
        "--top-p", "0.95",
        "--top-k", "20",
        "--min-p", "0.05",
        "--repeat-penalty", "1.05",
        "--port", "8081",
        "--host", "127.0.0.1",
        "--api-key", $API_KEY
    )
    
    if ($TemplateFile -and (Test-Path $TemplateFile)) {
        $argsList += @("--jinja", "--chat-template-file", $TemplateFile)
    } else {
        $argsList += @("--jinja")
    }
    
    $logDir = Join-Path $ROOT_DIR "eval_results\logs"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    $timeTag = Get-Date -Format "yyyyMMdd_HHmmss"
    $outFile = Join-Path $logDir "server_out_${timeTag}.log"
    $errFile = Join-Path $logDir "server_err_${timeTag}.log"
    
    $proc = Start-Process -FilePath $LLAMA_SERVER -ArgumentList $argsList -PassThru -WindowStyle Hidden -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    
    $deadline = (Get-Date).AddSeconds(180)
    $ready = $false
    $headers = @{ "Authorization" = "Bearer $API_KEY" }
    
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) {
            Write-Host " ❌ 服务意外退出！" -ForegroundColor Red
            if (Test-Path $errFile) { Get-Content $errFile -Tail 15 | Write-Host -ForegroundColor DarkRed }
            break
        }
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8081/v1/models" -Headers $headers -Method Get -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($resp -and $resp.data) {
                $ready = $true
                break
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    
    if (-not $ready) {
        if (-not $proc.HasExited) { $proc.Kill() }
        return $null
    }
    return $proc
}

# ===========================================================================
# 自动化执行流程
# ===========================================================================

Clear-Host
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host " 🚀 Qwen3.8 工具调用 4模型 × 2模板 大满贯全自动长测 (8 轮连续执行)   " -ForegroundColor Green
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host " 测试模型: NVFP4-MID-HIGH / A-Q6_K / UD-Q5KXL / U-Q6_K" -ForegroundColor Yellow
Write-Host " 测试模板: Fixed-Medium (froggeric) vs Sharp-Medium (Qwen-Sharp)" -ForegroundColor Yellow
Write-Host " 题库场景: 20 题深度专项 (死循环展开 / XML标签污染 / 多轮代码诊断 / 负样本)" -ForegroundColor Yellow
Write-Host " 运行方式: 全自动无须人工干预，测试完毕自动生成交互式矩阵主报告" -ForegroundColor White
Write-Host "========================================================================`n" -ForegroundColor Cyan

# 检查模型文件是否存在
foreach ($m in $TARGET_MODELS) {
    if (-not (Test-Path $m.File)) {
        Write-Host "[错误] 未找到模型文件: $($m.File)" -ForegroundColor Red
        exit 1
    }
}

$evalResultsDir = Join-Path $ROOT_DIR "eval_results"
if (-not (Test-Path $evalResultsDir)) { New-Item -ItemType Directory -Path $evalResultsDir -Force | Out-Null }

$evalPy = Join-Path $ROOT_DIR "model_eval\eval_tools_coding_matrix.py"
$reportPy = Join-Path $ROOT_DIR "model_eval\gen_matrix_report.py"
$masterPy = Join-Path $ROOT_DIR "model_eval\gen_master_report.py"

$totalTasks = $TARGET_MODELS.Count * $TEMPLATES.Count
$taskIndex = 1
$generatedJsons = @{}

$overallStart = Get-Date

foreach ($m in $TARGET_MODELS) {
    foreach ($t in $TEMPLATES) {
        Write-Host "------------------------------------------------------------------------" -ForegroundColor DarkCyan
        Write-Host "【轮次 $taskIndex / $totalTasks】模型: [$($m.Name)]  ×  模板: [$($t.Name)]" -ForegroundColor Green
        Write-Host "------------------------------------------------------------------------" -ForegroundColor DarkCyan
        
        Cleanup-LlamaEnvironment
        
        Write-Host "  正在启动 llama-server 服务..." -ForegroundColor Cyan
        $proc = Start-LlamaServerInstance -ModelPath $m.File -TemplateFile $t.File -TemplateName $t.Key
        
        if (-not $proc) {
            Write-Host "  ❌ 本轮服务启动失败，跳过。" -ForegroundColor Red
            $taskIndex++
            continue
        }
        Write-Host "  ✅ 服务就绪！开始执行 20 道工具调用专项测试..." -ForegroundColor Green
        
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $jsonOut = Join-Path $evalResultsDir "bench_$($m.Key)_$($t.Key)_p1_${stamp}.json"
        
        & $PYTHON_EXE $evalPy --api-base "http://127.0.0.1:8081/v1" --api-key $API_KEY --template-name $t.Key --out $jsonOut
        
        if (Test-Path $jsonOut) {
            $generatedJsons["$($m.Key)_$($t.Key)"] = $jsonOut
            # 渲染单份 HTML
            & $PYTHON_EXE $reportPy $jsonOut | Out-Null
        }
        
        try {
            if (-not $proc.HasExited) { $proc.Kill() }
        } catch { }
        
        $taskIndex++
        Write-Host ""
    }
}

Cleanup-LlamaEnvironment

$overallElapsed = [math]::Round(((Get-Date) - $overallStart).TotalMinutes, 1)

Write-Host "`n========================================================================" -ForegroundColor Green
Write-Host " 🎉 8 轮大满贯测试全部执行完毕！总耗时: $overallElapsed 分钟" -ForegroundColor Green
Write-Host " 正在汇总数据并生成交互式矩阵主大屏报告..." -ForegroundColor Cyan
Write-Host "========================================================================" -ForegroundColor Green

# 生成汇总大屏报告
& $PYTHON_EXE $masterPy

# 自动打开生成的最新主报告
$masterReports = Get-ChildItem -Path $evalResultsDir -Filter "MASTER_MATRIX_BENCHMARK_REPORT_*.html" | Sort-Object LastWriteTime -Descending
if ($masterReports.Count -gt 0) {
    $latestReport = $masterReports[0].FullName
    Write-Host "`n🌐 正在自动在浏览器中打开主报告: $latestReport" -ForegroundColor Yellow
    Start-Process $latestReport
}

Write-Host "`n长测任务圆满结束！请查看浏览器中的可视化图表分析。" -ForegroundColor Green
