<#
=============================================================================
Qwen3.8 工具调用深度专项评测自动化编排器 (Tool Calling Benchmark)
(UTF-8 with BOM for PowerShell 5.1 & PowerShell 7)
=============================================================================
遵循 AGENTS.md 规范：
1. 切换模型/模板时，先强制杀 llama 进程与 8081 端口
2. 循环监测 GPU 显存，低于 500MB 才启动下一个任务 (防双进程挤占显存)
3. 专注于 Sharp-Medium vs Fixed-Medium 模板差异与无审查权重工具调用能力测试
4. 64K 轻量上下文，纯工具调用测试极速完成 (单轮仅需 ~15 秒)
=============================================================================
#>

param(
    [string]$ModelChoice = "",
    [string]$TemplateChoice = "",
    [int]$Parallel = 1,
    [int]$CtxSize = 65536,
    [switch]$AutoRun
)

# 确保控制台中文输出正常
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ROOT_DIR = $PSScriptRoot
$MODELS_DIR = "E:\models"
$LLAMA_SERVER = Join-Path $ROOT_DIR "llama-server.exe"
$PYTHON_EXE = "C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe"
if (-not (Test-Path $PYTHON_EXE)) {
    $PYTHON_EXE = "python.exe"
}

$API_KEY = "llamacpp"

# 专注测试两大主流模板 (均锁定中级思维 medium)
$TEMPLATES = @{
    "Fixed-Medium" = @{
        Name = "Fixed-Medium"
        Desc = "Qwen-Fixed-Chat-Templates (froggeric) + 中级思维 [推荐基准]"
        File = Join-Path $ROOT_DIR "chat_template_qwen_fixed.jinja"
    }
    "Sharp-Medium" = @{
        Name = "Sharp-Medium"
        Desc = "Qwen-Sharp-Chat-Templates + 中级思维 [待排查模板]"
        File = Join-Path $ROOT_DIR "tools\sharp_repo\chat_template.jinja"
    }
}

# 候选模型扫描
function Get-TestModels {
    $allGguf = Get-ChildItem -Path $MODELS_DIR -Filter "*.gguf" -File | Where-Object { $_.Name -notmatch "mmproj|draft|dflash" }
    $models = @()
    
    $targetKeywords = @("Abliterated", "Uncensored-Q6_K", "NVFP4-MTP-MID-HIGH", "Unsloth", "Qwen3.8")
    
    foreach ($kw in $targetKeywords) {
        $matched = $allGguf | Where-Object { $_.Name -match $kw }
        foreach ($m in $matched) {
            if ($models.FullName -notcontains $m.FullName) {
                $models += $m
            }
        }
    }
    
    foreach ($m in $allGguf) {
        if ($m.Name -match "Qwen" -and ($models.FullName -notcontains $m.FullName)) {
            $models += $m
        }
    }
    return $models
}

# 彻底清理后台 llama 进程并释放 GPU 显存
function Cleanup-LlamaEnvironment {
    Write-Host "`n[1/3] 正在安全终止所有 llama 服务进程..." -ForegroundColor Cyan
    Get-Process | Where-Object { $_.ProcessName -match "llama" } | Stop-Process -Force -ErrorAction SilentlyContinue
    
    try {
        $conns = Get-NetTCPConnection -LocalPort 8081 -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    } catch { }
    
    Write-Host "[2/3] 正在等待 GPU 显存释放 (<500MB)..." -ForegroundColor Cyan
    $maxWait = 45
    $waited = 0
    while ($waited -lt $maxWait) {
        try {
            $smiOut = & nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null
            if ($smiOut) {
                $usedMB = [int]($smiOut.Trim())
                if ($usedMB -lt 500) {
                    Write-Host "  ✅ GPU 显存已完全释放 (当前占用: ${usedMB} MB)" -ForegroundColor Green
                    break
                } else {
                    Write-Host "  ⏳ 当前 GPU 显存占用: ${usedMB} MB，等待释放中 (${waited}s)..." -ForegroundColor Yellow
                }
            }
        } catch {
            break
        }
        Start-Sleep -Seconds 2
        $waited += 2
    }
    Write-Host "[3/3] 环境清理完毕，端口与显存已就绪！`n" -ForegroundColor Green
}

# 启动单次 llama-server (采用 Start-Process 日志重定向，防管道死锁)
function Start-LlamaServerInstance {
    param(
        [string]$ModelPath,
        [string]$TemplateKey,
        [int]$ParallelSlots = 1,
        [int]$ContextLimit = 65536
    )
    
    $tpl = $TEMPLATES[$TemplateKey]
    $modelFile = Split-Path $ModelPath -Leaf
    $ctxK = [math]::Round($ContextLimit / 1024)
    
    Write-Host "------------------------------------------------------------" -ForegroundColor DarkCyan
    Write-Host "  正在拉起 llama-server 服务..." -ForegroundColor Cyan
    Write-Host "  模型文件 : $modelFile" -ForegroundColor Yellow
    Write-Host "  聊天模板 : $($tpl.Name) ($($tpl.Desc))" -ForegroundColor Yellow
    Write-Host "  上下文长 : ${ctxK}K ($ContextLimit tokens, 快速评测)" -ForegroundColor Yellow
    Write-Host "  并发槽数 : $ParallelSlots 槽" -ForegroundColor Yellow
    Write-Host "------------------------------------------------------------" -ForegroundColor DarkCyan
    
    $argsList = @(
        "-m", $ModelPath,
        "-ngl", "99",
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        "-c", $ContextLimit.ToString(),
        "-b", "2048",
        "--ubatch-size", "2048",
        "-t", "6",
        "--parallel", $ParallelSlots.ToString(),
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
    
    if ($ParallelSlots -ge 2) {
        $argsList += @("--kv-unified", "--cache-reuse", "512")
    }
    
    if ($tpl.File -and (Test-Path $tpl.File)) {
        $argsList += @("--jinja", "--chat-template-file", $tpl.File)
    } else {
        $argsList += @("--jinja")
    }
    
    $logDir = Join-Path $ROOT_DIR "eval_results\logs"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    $timeTag = Get-Date -Format "yyyyMMdd_HHmmss"
    $outFile = Join-Path $logDir "server_out_${timeTag}.log"
    $errFile = Join-Path $logDir "server_err_${timeTag}.log"
    
    $proc = Start-Process -FilePath $LLAMA_SERVER -ArgumentList $argsList -PassThru -WindowStyle Hidden -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    
    Write-Host "正在等待服务加载并就绪 (最多等待 180 秒)..." -NoNewline
    $deadline = (Get-Date).AddSeconds(180)
    $ready = $false
    $headers = @{ "Authorization" = "Bearer $API_KEY" }
    
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) {
            Write-Host " ❌ 服务进程意外退出！" -ForegroundColor Red
            if (Test-Path $errFile) {
                Write-Host "--- 错误日志片段 ---" -ForegroundColor Yellow
                Get-Content $errFile -Tail 15 | Write-Host -ForegroundColor DarkRed
            }
            break
        }
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8081/v1/models" -Headers $headers -Method Get -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($resp -and $resp.data) {
                $ready = $true
                Write-Host " ✅ 服务已就绪！" -ForegroundColor Green
                break
            }
        } catch { }
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 2
    }
    
    if (-not $ready) {
        Write-Host "`n[错误] 服务启动超时，终止当前任务。" -ForegroundColor Red
        if (Test-Path $errFile) {
            Write-Host "--- 错误日志最后20行 ---" -ForegroundColor Yellow
            Get-Content $errFile -Tail 20 | Write-Host -ForegroundColor DarkRed
        }
        if (-not $proc.HasExited) { $proc.Kill() }
        return $null
    }
    
    return $proc
}

# 执行单轮评测与报告生成
function Run-SingleBenchTask {
    param(
        [string]$ModelPath,
        [string]$TemplateKey,
        [int]$ParallelSlots = 1,
        [int]$ContextLimit = 65536
    )
    
    Cleanup-LlamaEnvironment
    
    $proc = Start-LlamaServerInstance -ModelPath $ModelPath -TemplateKey $TemplateKey -ParallelSlots $ParallelSlots -ContextLimit $ContextLimit
    if (-not $proc) { return $false }
    
    $modelBaseName = [System.IO.Path]::GetFileNameWithoutExtension($ModelPath)
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outDir = Join-Path $ROOT_DIR "eval_results"
    if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
    
    $jsonOut = Join-Path $outDir "bench_${modelBaseName}_${TemplateKey}_p${ParallelSlots}_${stamp}.json"
    $htmlOut = Join-Path $outDir "bench_${modelBaseName}_${TemplateKey}_p${ParallelSlots}_${stamp}.html"
    $evalPy = Join-Path $ROOT_DIR "model_eval\eval_tools_coding_matrix.py"
    $reportPy = Join-Path $ROOT_DIR "model_eval\gen_matrix_report.py"
    
    Write-Host "`n🚀 开始执行工具调用专项评测套件 (20 题)..." -ForegroundColor Cyan
    & $PYTHON_EXE $evalPy --api-base "http://127.0.0.1:8081/v1" --api-key $API_KEY --template-name $TemplateKey --concurrency-mode "parallel-$ParallelSlots" --out $jsonOut
    
    if (Test-Path $jsonOut) {
        Write-Host "📊 正在渲染 HTML5 交互式诊断报告..." -ForegroundColor Cyan
        & $PYTHON_EXE $reportPy $jsonOut
        Write-Host "✅ 报告生成完毕: $htmlOut" -ForegroundColor Green
    }
    
    try {
        if (-not $proc.HasExited) { $proc.Kill() }
    } catch { }
    Cleanup-LlamaEnvironment
    return $true
}

# ===========================================================================
# 交互式主菜单
# ===========================================================================

Clear-Host
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host "       🛠️ Qwen3.8 工具调用深度专项评测系统 (PowerShell 版)             " -ForegroundColor Green
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host " 聚焦排查：Sharp vs Fixed 模板死循环展开 / 无审查微调对工具调用的影响" -ForegroundColor DarkGray
Write-Host " 运行模式：纯工具调用 20 题专项评测 (单轮仅需 ~15 秒，免去代码评测耗时)" -ForegroundColor DarkCyan
Write-Host ""

$scannedModels = Get-TestModels
if ($scannedModels.Count -eq 0) {
    Write-Host "[错误] 未在 $MODELS_DIR 找到任何 .gguf 模型文件！" -ForegroundColor Red
    exit 1
}

Write-Host "【已检测到的可用 Qwen3.8 测试模型】:" -ForegroundColor Yellow
for ($i = 0; $i -lt $scannedModels.Count; $i++) {
    $m = $scannedModels[$i]
    $sizeGB = [math]::Round($m.Length / 1GB, 1)
    Write-Host "  [$($i+1)] $($m.Name) ($sizeGB GB)" -ForegroundColor White
}

Write-Host "`n【对比聊天模板】(统一中级思维 medium):" -ForegroundColor Yellow
Write-Host "  [1] Fixed-Medium : Qwen-Fixed-Chat-Templates (froggeric) [推荐基准]" -ForegroundColor White
Write-Host "  [2] Sharp-Medium : Qwen-Sharp-Chat-Templates [待排查模板]" -ForegroundColor White
Write-Host ""

Write-Host "【请选择评测模式】:" -ForegroundColor Cyan
Write-Host "  [1] ⭐ 当前模型 (Abliterated-Q6_K) 模板对比: Fixed vs Sharp (仅需 2 轮测试)" -ForegroundColor Green
Write-Host "  [2] 🔍 无审查 vs 官方基准模型 横向对比 (A-Q6_K vs Uncensored vs NVFP4 在 Fixed 模板下)" -ForegroundColor Yellow
Write-Host "  [3] 🏆 3 模型 × 2 模板 全矩阵大满贯测试 (共 6 轮)" -ForegroundColor White
Write-Host "  [4] 🔧 自选单模型单模板测试" -ForegroundColor Magenta
Write-Host "  [Q] 退出" -ForegroundColor DarkGray
Write-Host ""

$choice = Read-Host "请输入选项序号 [默认 1]"
if (-not $choice) { $choice = "1" }

switch ($choice.ToUpper()) {
    "1" {
        # 对比 Fixed 与 Sharp
        $targetModel = $scannedModels | Where-Object { $_.Name -match "Abliterated" } | Select-Object -First 1
        if (-not $targetModel) { $targetModel = $scannedModels[0] }
        
        Write-Host "`n🎯 即将对模型 [$($targetModel.Name)] 连续评测 Fixed-Medium 与 Sharp-Medium..." -ForegroundColor Cyan
        Run-SingleBenchTask -ModelPath $targetModel.FullName -TemplateKey "Fixed-Medium" -ParallelSlots 1 -ContextLimit $CtxSize
        Run-SingleBenchTask -ModelPath $targetModel.FullName -TemplateKey "Sharp-Medium" -ParallelSlots 1 -ContextLimit $CtxSize
        Write-Host "`n🎉 模板对比评测完成！请查看 eval_results 目录下的 HTML 诊断报告。" -ForegroundColor Green
    }
    "2" {
        # 跨模型对比：无审查/微调 vs 官方对齐 (统一在 Fixed-Medium 下测试)
        $m1 = $scannedModels | Where-Object { $_.Name -match "Abliterated" } | Select-Object -First 1
        $m2 = $scannedModels | Where-Object { $_.Name -match "Uncensored.*Q6_K" } | Select-Object -First 1
        $m3 = $scannedModels | Where-Object { $_.Name -match "UD.*Q5_K_XL" } | Select-Object -First 1
        $m4 = $scannedModels | Where-Object { $_.Name -match "NVFP4.*MID-HIGH" } | Select-Object -First 1
        
        $testList = @($m1, $m2, $m3, $m4) | Where-Object { $_ -ne $null }
        Write-Host "`n🔍 开始跨模型横向对比无审查/UD微调与官方对齐在 Fixed-Medium 模板下的工具调用能力..." -ForegroundColor Cyan
        foreach ($m in $testList) {
            Write-Host "`n>>> 测试模型: [$($m.Name)] <<<" -ForegroundColor Yellow
            Run-SingleBenchTask -ModelPath $m.FullName -TemplateKey "Fixed-Medium" -ParallelSlots 1 -ContextLimit $CtxSize
        }
        Write-Host "`n🎉 跨模型对比评测完成！请查看 eval_results 报告对比分数。" -ForegroundColor Green
    }
    "3" {
        # 3 模型 × 2 模板 = 6 轮
        $selected = $scannedModels | Select-Object -First 3
        foreach ($m in $selected) {
            foreach ($t in @("Fixed-Medium", "Sharp-Medium")) {
                Write-Host "`n>>> 启动轮次: [$($m.Name)] x [$t] <<<" -ForegroundColor Cyan
                Run-SingleBenchTask -ModelPath $m.FullName -TemplateKey $t -ParallelSlots 1 -ContextLimit $CtxSize
            }
        }
        Write-Host "`n🎉 全矩阵评测完成！" -ForegroundColor Green
    }
    "4" {
        $mIdx = Read-Host "请输入要测试的模型编号 (1-$($scannedModels.Count))"
        $mObj = $scannedModels[[int]$mIdx - 1]
        
        Write-Host "请选择模板: [1] Fixed-Medium  [2] Sharp-Medium"
        $tIdx = Read-Host "输入模板序号 [1-2]"
        $tKey = switch ($tIdx) { "1" { "Fixed-Medium" } "2" { "Sharp-Medium" } default { "Fixed-Medium" } }
        
        Run-SingleBenchTask -ModelPath $mObj.FullName -TemplateKey $tKey -ParallelSlots 1 -ContextLimit $CtxSize
    }
    "Q" {
        Write-Host "已退出。"
        exit 0
    }
}
