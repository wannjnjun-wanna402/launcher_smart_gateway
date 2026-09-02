# ============================================================
#  AI大模型启动器  v2
#  自动识别 E:\models 目录下所有 GGUF 模型，分类并启动
#  显示模型、显存、资源占用概览
#  Date: 2026-06-29
# ============================================================

# ============================================================
#  ⚠️ 核心调优与生产实测经验总结 —— 接手者必读
# ============================================================
# 【工具调用 4模型 × 2模板 深度专项长测（2026-08-31 实测铁律）】
#  2026-08-31 对 4 大主流 27B 权重与 2 大 Jinja 模板进行了 20 题深度专项长测：
#  1. 模板生死线：Fixed-Medium (froggeric) 全面碾压 Sharp-Medium
#     - Fixed-Medium 模板：4大模型全线保持 0 次死循环展开、0 次 XML 标签污染，
#       通过率 85%~100%，参数解析率 87.5%~100%。
#     - Sharp-Medium 模板：严重缺少参数闭合约束，在 A-Q6_K 与 U-Q6_K 上引发多达
#       6 次 XML 标签泄漏（如输出 <parameter=unit> 破坏 JSON 语法导致 Unterminated string 崩溃），
#       通过率暴跌至 65%，且 Token 生成量翻倍（4.3K 暴增至 8.6K），造成严重显存与算力浪费。
#     - 结论：Agent / 工具调用生产部署必须死锁 chat_template_qwen_fixed.jinja，严禁在工具场景使用 Sharp 模板！
#
#  2. 无审核/去对齐模型（Abliterated / Uncensored）工具调用遵循能力最强：
#     - Qwen3.8-27B-A-Q6_K (Abliterated)：斩获 100.0 分满分（20/20 全通过），原生解析率 100%，
#       生成 36.7 tok/s，Prompt 吞吐 111.2 tok/s；在 Q18 逆向指令/恶意诱导陷阱中也是唯一正确识破并果断调工具的模型！
#     - Qwen3.8-27B-U-Q6_K (Uncensored)：获得 95.0 分（19/20），生成 36.8 tok/s，Prompt 吞吐 115.0 tok/s。
#     - 证明：去除对齐不仅不破坏工具 Schema 格式，反而消除了防御性发散分支，结构化指令遵循精度达到顶峰。
#
#  3. 2026-08-31 工具长测速度与评分同步（Fixed-Medium 模板）：
#     - [1] Qwen3.8-27B-A-Q6_K         : 100.0 分 | 通过 100% | 生成 36.7 tok/s | Prompt 111.2 tok/s (王者力荐)
#     - [2] Qwen3.8-27B-U-Q6_K         :  95.0 分 | 通过  95% | 生成 36.8 tok/s | Prompt 115.0 tok/s (极佳)
#     - [3] Qwen3.8-27B-NVFP4-MID-HIGH :  90.0 分 | 通过  90% | 生成 38.7 tok/s | Prompt 106.3 tok/s (生成最快)
#     - [4] Qwen3.8-27B-UD-Q5KXL       :  89.2 分 | 通过  85% | 生成 37.8 tok/s | Prompt  89.0 tok/s (良好)
#
# 【DRY 采样器：为什么 Qwen3.8 分支必须关闭（--dry-multiplier 0.0）】
#  2026-08-29 用 Qwen3.8-27B 实测"原样复现路径 E:\ai123\aigame\_fix3.js 三行"：
#    A. dry 0.8 + allowed-length 2（旧默认）→ 0/9 全错（缺反斜杠/目录重复/丢字母）
#    B. dry 0.8 + allowed-length 4          → 0/7 全错（连 .js 都变异成 .cs）
#    C. dry 0.4 + allowed-length 2          → 0/9 全错（数字被惩罚：fix3→fix2→fix1）
#    D. dry 0.0（关闭）                     → 9/9 全对 ✅
#  原因：DRY 惩罚"最近上下文里出现过的 2-gram 以上重复"（指数级 multiplier*base^len），
#  路径/文件名这类"必须逐字符精确复现"的字符串，只要前缀重复就会被强制"岔开"→ 每次
#  变体都是新错误。DRY 只适合防散文复读，不适合 agentic 工具调用场景。
#  → Qwen3.8 各分支 + qwen3vl + Ornith 一律 --dry-multiplier 0.0（本文件已改）。
#  → Qwen3.5-4B / Qwen3.5-0.8B / Gemma 保留 0.8：纯对话/视觉无工具调用，DRY 防复读有正面价值。
#  → 注意：这是采样层问题，模板（提示层）管不到，别指望换模板能修。
#
# 【repeat-penalty 1.0 → 1.05 的原因】
#  关掉 DRY 后需要一个轻量防复读兜底。repeat-penalty 是 token 级乘性惩罚
#  （重复 token 的 logit 乘以 1/1.05≈0.952），不像 DRY 那样指数级打整条序列，
#  不会破坏精确复现（D 组实测是 repeat 1.0 下 9/9；1.05 是保守兜底值，机制上安全，
#  建议重启后可用同样方法复核一次）。若遇到模型复读（"好的好的好的…"）可调 1.1~1.2；
#  精确复现任务建议 ≤1.05。
#
# 【A-Q6_K 压力测试验证（2026-08-29，dry 0.0 + repeat 1.05）】
#  9 类场景 × 3 次 = 27 请求 / 57 行，56/57 行正确（98.2%），工具调用场景全部稳住：
#    ✅ 反斜杠 5 类全对：基础路径×3行、深嵌套5层目录、UNC 双前导反斜杠、
#       JSON 双反斜杠转义、中文目录+副本名（对比旧配置 0/9 → 现 21/21）
#    ✅ 复读 2 类全对：同一路径连写5行、数字变体 _fix1~_fix5
#    ✅ 混合：JSON 重复键
#    ⚠️ 唯一失分点："好的"×20 自然语言复读 1/3 概率少输出一个（repeat-penalty 1.05
#       对 2-token 重复的轻微抑制，属预期代价；agentic 场景不会要求复读这种文本）
#  → 结论：dry 0.0 + repeat 1.05 是 agentic 精确复现的最优平衡点，无需再调。
#
# 【模板 chat_template_qwen_fixed.jinja 的作用边界】
#  模板修的是"提示层"：XML 工具格式（防 Windows 路径反斜杠被 JSON 转义吃坏）、
#  thinking 只进 reasoning_content（防思考标签污染工具解析）、思考跨轮保留
#  （防失忆）、terseness 精简、reasoning_effort 档位（none/low/medium/xhigh）。
#  模板修不了"采样层"（token 级字符串拼错），采样层只能靠采样参数解决。
#
# 【参数分级：哪些可以调 / 哪些定死】
#  可调（只影响输出风格，不会崩）：
#    --temp 0.3 / --top-p 0.95 / --top-k 20 / --min-p 0.05（采样随机度）
#    --repeat-penalty / --presence-penalty（防复读，轻微）
#  定死（动了会破坏已验证链路，改前必须重新实测）：
#    --dry-multiplier 0.0（必须关，见上）
#    --reasoning / --reasoning-budget 2048 / --reasoning-effort / --reasoning-format
#      / --reasoning-preserve（思考链路，2026-08-25 起逐步实测锁定）
#    --chat-template-file $QWEN38_TEMPLATE_FILE（模板路径，勿换回旧模板）
#    --spec-type draft-mtp / --spec-draft-n-max 2（投机解码，实测均速 40+ tok/s 的关键）
#    -ngl 99 / -c / -b / --ubatch-size / -t 6 / --parallel / --flash-attn on
#      / --cache-type-k/v（显存与并发布局，按显卡算好）
# ============================================================

param(
    [int]$ModelIndex = 0,   # 非交互：直接启动第 N 个模型（跳过菜单）。0=不启用
    [switch]$ListModels,    # 非交互：仅输出模型清单（ASCII JSON），供评测编排脚本解析
    [switch]$NoInteractive, # 非交互：跳过所有 Safe-ReadHost/TTY 渲染，供后台子进程免崩（评测脚本用）
    [string]$DailyLogFile = "",  # 由.bat传入的每日日志文件路径
    [string]$CudaVersion = "未检测",    # CUDA Toolkit 版本
    [string]$CublasVersion = "未检测",  # cuBLAS DLL 文件名/版本
    [string]$CudartVersion = "未检测",  # cudart DLL 文件名/版本
    [string]$NvccVersion = ""           # nvcc 编译版本
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::Default
$Host.UI.RawUI.WindowTitle = "AI大模型启动器"

# CUDA 运行时优化（2GB JIT 编译缓存 + 专用硬件调度流）
$env:CUDA_CACHE_MAXSIZE = "2147483648"
$env:CUDA_DEVICE_MAX_CONNECTIONS = "1"

# 全程禁用 Ctrl+C 中断：防止复制窗口内容时误触 Ctrl+C 关闭脚本/服务。
# 原理：Ctrl+C 变为普通输入字符（不产生中断信号），脚本不读取该字符，无副作用。
# pwsh 7 补充：脚本运行中 Ctrl+C 是 SIGINT（PSReadLine 绑定只对交互提示符生效），
# 必须用 [Console]::CancelKeyPress 拦截（e.Cancel=$true）才不杀进程。
# 关闭窗口请用右上角 ×；菜单选择用数字键 + 回车。
# 注：非交互模式（-ListModels / 后台子进程）无控制台句柄，设置会抛"句柄无效"，须 try/catch 跳过。
try {
    [Console]::TreatControlCAsInput = $true
    [Console]::CancelKeyPress += [ConsoleCancelEventHandler]{
        param($sender, $e)
        $e.Cancel = $true
        Write-Host "" -ForegroundColor DarkGray
        Write-Host "  [提示] Ctrl+C 已拦截（防误触），关闭请用右上角 x" -ForegroundColor Yellow
    }
    Write-Host "  [提示] 本窗口已禁用 Ctrl+C 中断（防复制误触），关闭请用右上角 x"
} catch { }

# 非交互模式辅助：NoInteractive 时跳过所有 Read-Host（后台子进程无 TTY，Read-Host 会崩）
function Safe-ReadHost($prompt) {
    if ($NoInteractive) { return "" }
    # PS 5.1 里 Read-Host $null 报"name 不能是空值"——prompt 为空时走无参 Read-Host
    if ($null -eq $prompt -or $prompt -eq "") { return Read-Host }
    return Read-Host $prompt
}

# 前端 / agent 接入配置（单一来源：菜单与启动输出共用）
$ApiKey = "llamacpp"

# 打印前端 / agent 接入信息（OpenAI 兼容与协议说明）
function Show-FrontendConnect($Port) {
    Write-Host ""
    Write-Color "  ▸ 接入地址: " $C_SECSUB
    Write-Color "http://127.0.0.1:$Port/v1" $C_TAG
    Write-Color "  密钥: " $C_HINT
    Write-Color "admin / llamacpp / v100-32G" $C_WARN
    Write-Line " (3台电脑独立分账)" $C_HINT
    Write-Color "  ▸ 协议支持: " $C_SECSUB
    Write-Color "llama.cpp 原生仅支持 OpenAI 格式" $C_TAG
    Write-Line " | 开启 CC Switch 后双向通吃 Anthropic + OpenAI" $C_OK
    Write-Color "  ▸ 模型拉取: " $C_HINT
    Write-Color "http://127.0.0.1:$Port/v1/models" $C_TAG
    Write-Line " (支持 Claude 5/4/3、DeepSeek-V4、GPT-4o 别名自动路由)" $C_HINT
    Write-Color "  ▸ 算力大屏: " $C_HINT
    Write-Color "http://127.0.0.1:$Port/dashboard" $C_TAG
    Write-Line " (DeepSeek-V4 虚拟计费 · 3台电脑分账排行)" $C_HINT
    Write-Color "  ▸ 评测基准: " $C_SECSUB
    Write-Color "Artificial Analysis (AA)" "Magenta"
    Write-Line " 权威人工智能分析指数 v4.1.1" $C_TAG
    Write-Color "  ▸ 视觉协同: " $C_SECSUB
    Write-Color "👁️ 8085 视觉眼睛 (Qwen2.5-VL-3B · CPU纯内存/0显存)" $C_OK
    Write-Line " | 纯文本主模型自动获 OCR 识图能力，支持直接发图" $C_TAG
    Write-Host ""
}

# 确保 8081 智能协同网关常驻运行（双击启动器即刻拉起，无需等待选模型）
function Ensure-GatewayRunning {
    param([int]$VisionPort = 8085)
    $listenPort = 8081
    $backendPort = 8083
    $pyExe = "C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe"
    if (-not (Test-Path $pyExe)) { $pyExe = "python.exe" }
    
    $baseDir = if ($PSScriptRoot) { $PSScriptRoot } elseif ($LLAMA_DIR) { $LLAMA_DIR } else { "E:\llama-win-cuda-12.4-x64" }
    $proxyScript = Join-Path $baseDir "qwen_tool_proxy.py"
    
    $conns8081 = Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue
    if ($conns8081) {
        return
    }

    if (Test-Path $proxyScript) {
        try {
            $logDir = Join-Path $baseDir "logs"
            if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
            $proxyLogFile = Join-Path $logDir ("8081_proxy_" + (Get-Date -Format "yyyyMMdd") + ".log")

            $proxyArgs = @($proxyScript, "--listen", "$listenPort", "--target", "$backendPort", "--vision-main", "$VisionPort", "--api-key", "$ApiKey")
            $script:g_ProxyProcess = Start-Process -FilePath $pyExe -ArgumentList $proxyArgs -PassThru -WindowStyle Hidden
            
            # 轮询 3 秒确认 8081 端口成功监听
            for ($k = 0; $k -lt 6; $k++) {
                Start-Sleep -Milliseconds 500
                $chk = Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue
                if ($chk) { break }
            }
            Write-DailyLog "GATEWAY_INIT: Started 8081 Proxy Gateway PID=$($script:g_ProxyProcess.Id) on launcher startup | log=$proxyLogFile"
        } catch {
            Write-DailyLog "GATEWAY_INIT_WARN: Failed to start proxy on launcher startup: $_"
        }
    }
}

# ============================================================
#  统一日志系统 — 单日单文件追加，ms 级时间戳
# ============================================================
$g_DailyLogFile = if ($DailyLogFile) { $DailyLogFile } else { $null }
# 日志滚动策略：每天最多 2 个文件（主日志 + 1 个溢出文件）。
# 主日志超过阈值后自动滚动到溢出文件（不再无限制增长）；显式 -DailyLogFile 时由用户自管、不滚动。
$script:g_LogMain          = $null
$script:g_LogOverflow      = $null
$script:g_LogThreshold     = 50MB   # 主日志超过此大小（50MB）则滚动到溢出文件
$script:g_LogOverflowMax   = 2GB    # 溢出文件(.2.log)上限，超过则停写+告警，防撑爆磁盘
$script:g_LogRolled        = $false
$script:g_LogOverflowCapped= $false
$script:g_LogStamp         = $null  # 当前日志文件对应的自然日戳(yyyyMMdd)，用于跨午夜切分
# 会话级计数器（用于 SESSION END 汇总）
$script:g_SessionErrors    = 0
$script:g_PeakGpuMem       = 0

function Write-DailyLog {
    param([string]$Message, [switch]$NoNewLine)
    if (-not $g_DailyLogFile) { return }
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss.fff")
    $line = "[$ts] $Message"
    try {
        # ---- #1 跨午夜按自然日切分：若当前日期与文件日期戳不符，切换到新日期文件 ----
        if ($script:g_LogMain -and $script:g_LogStamp) {
            $today = (Get-Date).ToString("yyyyMMdd")
            if ($today -ne $script:g_LogStamp) {
                $logDir = Split-Path $script:g_LogMain -Parent
                $script:g_LogStamp        = $today
                $script:g_LogMain         = Join-Path $logDir ("8083_llama_" + $today + ".log")
                $script:g_LogOverflow     = Join-Path $logDir ("8083_llama_" + $today + ".2.log")
                $g_DailyLogFile           = $script:g_LogMain
                $script:g_LogRolled       = $false
                $script:g_LogOverflowCapped = $false
                [System.IO.File]::AppendAllText(
                    $script:g_LogMain,
                    "[$ts] [DAY_ROLLOVER] 跨午夜，日志切换到新日期文件：$($script:g_LogMain)`n",
                    [System.Text.UTF8Encoding]::new($false))
            }
        }

        # ---- 动态选择写入目标：主文件超阈值 → 溢出文件（每天最多 2 个）----
        $target = $g_DailyLogFile
        if ($script:g_LogMain -and $script:g_LogOverflow -and $g_DailyLogFile -eq $script:g_LogMain) {
            if ((Test-Path $script:g_LogMain) -and ((Get-Item $script:g_LogMain).Length -gt $script:g_LogThreshold)) {
                $g_DailyLogFile = $script:g_LogOverflow
                if (-not $script:g_LogRolled) {
                    [System.IO.File]::AppendAllText(
                        $script:g_LogOverflow,
                        "[$ts] [LOG_ROLLOVER] 主日志超过 $([math]::Round($script:g_LogThreshold/1MB))MB，后续写入溢出文件：$($script:g_LogOverflow)`n",
                        [System.Text.UTF8Encoding]::new($false))
                    $script:g_LogRolled = $true
                }
                $target = $script:g_LogOverflow
            }
        }
        elseif ($script:g_LogOverflow -and $g_DailyLogFile -eq $script:g_LogOverflow) {
            # ---- #2 溢出文件(.2.log)兜底：超过上限则停写+告警，避免撑爆磁盘 ----
            if ((Test-Path $script:g_LogOverflow) -and ((Get-Item $script:g_LogOverflow).Length -gt $script:g_LogOverflowMax)) {
                if (-not $script:g_LogOverflowCapped) {
                    [System.IO.File]::AppendAllText(
                        $script:g_LogOverflow,
                        "[$ts] [LOG_OVERFLOW_CAPPED] 溢出文件超过 $([math]::Round($script:g_LogOverflowMax/1GB))GB，停止写入以防撑满磁盘（请归档旧日志后清理）`n",
                        [System.Text.UTF8Encoding]::new($false))
                    $script:g_LogOverflowCapped = $true
                }
                return
            }
        }
        Add-Content -Path $target -Value $line -Encoding UTF8
    } catch { }
}

function Write-DailyLogHeader {
    Write-DailyLog "========================================================================"
    Write-DailyLog "AI 大模型启动器 v3 — 日志系统初始化"
    Write-DailyLog "========================================================================"
    Write-DailyLog ""

    # ---- 系统信息 ----
    try {
        $os = (Get-CimInstance Win32_OperatingSystem)
        $ramTotal = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
        $ramFree  = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
        $osName  = $os.Caption -replace "Microsoft ", ""
        $osBuild = $os.BuildNumber
        Write-DailyLog "SYSTEM: OS=${osName} (Build ${osBuild}) | RAM=${ramTotal}GB total, ${ramFree}GB free"
    } catch { Write-DailyLog "SYSTEM: (无法获取系统信息)" }

    # ---- 磁盘 ----
    try {
        $drv = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='E:'"
        if ($drv) {
            $totalGB = [math]::Round($drv.Size / 1GB, 1)
            $freeGB  = [math]::Round($drv.FreeSpace / 1GB, 1)
            $pctFree = [math]::Round(($drv.FreeSpace / $drv.Size) * 100.0, 1)
            Write-DailyLog "DISK: E: ${totalGB}GB total, ${freeGB}GB free (${pctFree}% available)"
        }
    } catch { }

    # ---- GPU + CUDA ----
    try {
        $gpu = Get-GPUInfo
        if ($gpu) {
            $vramGB = [math]::Round($gpu.VRAM / 1024.0, 1)
            $cudaDrv = try { (nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>$null).Trim() } catch { "N/A" }
            Write-DailyLog "GPU: $($gpu.Name) ${vramGB}GB VRAM | CUDA Driver ${cudaDrv}"
        }
    } catch { }

    # ---- 依赖库版本（从 .bat 传入或自动检测） ----
    if ($CudaVersion -and $CudaVersion -ne "未检测") {
        Write-DailyLog "DEP: CUDA Toolkit ${CudaVersion}"
    } else {
        # 兜底检测
        try {
            $cudaVer = (nvidia-smi 2>$null | Select-String "CUDA Version" | ForEach-Object { $_.ToString() -replace '.*CUDA Version:\s*', '' }).Trim()
            if ($cudaVer) { Write-DailyLog "DEP: CUDA Toolkit ${cudaVer}" }
        } catch { }
    }
    if ($CudartVersion -and $CudartVersion -ne "未检测") {
        Write-DailyLog "DEP: cudart ${CudartVersion}"
    }
    if ($CublasVersion -and $CublasVersion -ne "未检测") {
        Write-DailyLog "DEP: cuBLAS ${CublasVersion}"
    }
    if ($NvccVersion) {
        Write-DailyLog "DEP: nvcc release ${NvccVersion}"
    }
    # cuDNN 检测（从 cudnn*.dll 或 cudart 关联推断）
    try {
        $cudnnFiles = Get-ChildItem (Join-Path $LLAMA_DIR "cudnn*") -ErrorAction SilentlyContinue
        if ($cudnnFiles) {
            foreach ($cf in $cudnnFiles) {
                Write-DailyLog "DEP: cuDNN file present = $($cf.Name)"
            }
        } else {
            # 尝试从 PATH 或 CUDA_PATH 找 cudnn64*.dll
            $cudnnPath = try { (Get-Command "cudnn64_*.dll" -ErrorAction SilentlyContinue).Source } catch { $null }
            if (-not $cudnnPath) { $cudnnPath = try { (Get-ChildItem "${env:CUDA_PATH}\bin\cudnn64*.dll" -ErrorAction SilentlyContinue | Select-Object -First 1).FullName } catch { $null } }
            if ($cudnnPath) {
                $cudnnVer = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($cudnnPath)
                Write-DailyLog "DEP: cuDNN $($cudnnVer.FileVersion) ($cudnnPath)"
            }
        }
    } catch { }

    # ---- llama.cpp 版本 + 编译器 ----
    try {
        $verOut = cmd /c "`"$LLAMA_SERVER`" --version 2>&1" | Out-String
        $verOut -match 'version:\s*(\d+)' | Out-Null
        $bVer = $Matches[1]
        $verOut -match 'built with (.+?)$' | Out-Null
        $compiler = $Matches[1]
        Write-DailyLog "LLAMA.CPP: b${bVer} | ${compiler}"
    } catch { Write-DailyLog "LLAMA.CPP: (版本检测失败)" }

    # ---- CPU 信息 ----
    try {
        $cpuName = ((Get-CimInstance Win32_Processor).Name.Trim() -replace '\s+', ' ')
        $cores = (Get-CimInstance Win32_Processor).NumberOfCores
        $logical = [Environment]::ProcessorCount
        Write-DailyLog "CPU: ${cpuName} | ${cores}C/${logical}T"
    } catch { }

    Write-DailyLog ""
}

# ---------- 路径配置 ----------
# 用脚本自身所在目录自定位，文件夹改名（去掉版本号等）后无需改动。
$LLAMA_DIR   = $PSScriptRoot
$MODELS_DIR  = "E:\models"
$LLAMA_SERVER = Join-Path $LLAMA_DIR "llama-server.exe"

# ---------- Qwen3.8 思考模式控制（froggeric 修复模板） ----------
# 接入 chat_template_qwen_fixed.jinja（官方 Qwen3.8 模板有思考过长/空think污染/token浪费等问题）。
#   $QWEN38_REASONING_EFFORT : "xhigh"/"high"/"medium"/"low" —— 默认 "medium"=中度思考（中性基线，不注入额外指令）
#   $QWEN38_ENABLE_THINKING  : $true=保留思考(简短) / $false=完全关闭思考(最快,放弃推理)
#   $QWEN38_REASONING_FORMAT : "deepseek"=仅独立字段（think 只进 reasoning_content，官方推荐，agent 友好） / "deepseek-legacy"=正文带<think>思考过程+兼容独立字段（旧）
# 会话中也可在提问里加 <|think_off|> 临时关思考、<|think_low|> 临时降档（模板内置支持）。
$QWEN38_REASONING_EFFORT = "medium"
$QWEN38_ENABLE_THINKING  = $true
$QWEN38_REASONING_FORMAT = "deepseek"
# llama-server 原生参数控制思考（PS5.1 传 JSON 会吞引号导致解析失败，故不用 --chat-template-kwargs）：
#   --reasoning auto/off   ↔ 模板 enable_thinking（off=完全关闭思考）
#   --reasoning-effort     ↔ 模板 reasoning_effort（low=简短思考）
#   --reasoning-format     ↔ deepseek（think 仅注入 reasoning_content 字段，正文不残留 <think>，官方推荐）
$QWEN38_REASONING_MODE   = if ($QWEN38_ENABLE_THINKING) { "auto" } else { "off" }
$QWEN38_TEMPLATE_FILE    = Join-Path $PSScriptRoot "chat_template_qwen_fixed.jinja"

# ---------- llama.cpp 版本自动检测 ----------
# 目录名可能落后于实际二进制（用户常只换 exe 不改名），
# 因此直接运行 llama-server --version 解析真实构建号，而非读文件夹名。
function Get-LlamaVersion {
    # llama-server --version 把版本号写到 stderr。本脚本全局 $ErrorActionPreference="Stop"，
    # 会把原生命令的 stderr 包装成 NativeCommandError，其 Exception.Message 即含 "version: 9957 ..."。
    # 主方案：直接执行并 catch 该错误，从错误消息解析构建号（已验证在 Stop 下稳定返回 b9957）。
    # 此前用 2> 文件重定向的方案在 Stop 下两个重定向文件均为空（stderr 被当作终止错误、未落盘），故一直返回"未知"。
    # 兜底方案：用 cmd /c 合并 stdout+stderr 到临时文件再读取（覆盖将来以非 Stop 偏好运行脚本的场景）。
    $ver = $null
    try {
        & $LLAMA_SERVER --version 2>$null | Out-Null
    } catch {
        $msg = $_.Exception.Message
        # 新版输出 "version: 0.1.0-dev (build 10435, commit ...)"：优先匹配 build N（旧正则 version:\s*(\d+) 会误捕 0.1.0 的 0 → B0）
        if ($msg -match 'build\s*(\d+)') { $ver = $Matches[1] }
        elseif ($msg -match 'version:\s*(\d+)') { $ver = $Matches[1] }
        elseif ($msg -match '\b(\d{4,})\b') { $ver = $Matches[1] }
    }
    if (-not $ver) {
        try {
            $tf = [System.IO.Path]::GetTempFileName()
            cmd /c "`"$LLAMA_SERVER`" --version > `"$tf`" 2>&1" | Out-Null
            $c = [System.IO.File]::ReadAllText($tf)
            Remove-Item $tf -ErrorAction SilentlyContinue
            if ($c -match 'build\s*(\d+)') { $ver = $Matches[1] }
            elseif ($c -match 'version:\s*(\d+)') { $ver = $Matches[1] }
            elseif ($c -match '\b(\d{4,})\b') { $ver = $Matches[1] }
        } catch { }
    }
    if (-not $ver) { $ver = "未知" }
    return $ver
}
$LLAMA_VERSION = Get-LlamaVersion

# ---------- 颜色 ----------
$C_RESET  = [Console]::ForegroundColor
$C_TITLE  = "Cyan"        # 主标题
$C_SECTION = "Yellow"     # 选择模型 (9 个)
$C_SECSUB = "Green"     # ▸ 操作选项 / ▸ [...]
$C_MODEL  = "Yellow"
$C_TAG    = "Cyan"
$C_WARN   = "Magenta"
$C_HINT   = "DarkGray"
$C_INPUT  = "White"
$C_ERROR  = "Red"
$C_OK     = "DarkGreen"
$C_VRAM   = "DarkCyan"  # 显存信息专用色
$C_CTX    = "DarkYellow"  # 上下文专用色（琥珀色，与显存/其他数据列区分）

# ============================================================
#  能力字总库（每个字独立代表一项能力，多能力模型并列单字组合）
#    图 = 多模态视觉      狱 = 越狱/越狱释放      调 = 工具调用
#    思 = 思考/推理模式    码 = 编码/代码          译 = 翻译/多语言
#    通 = 通用能力（推理/数学/对话/创作/极速/稳定 合并）
#    双 = 双并发/双槽并行（--parallel ≥ 2）
# ============================================================

# 能力字配色（按单字映射，多字组合取首字配色）
$ABILITY_COLORS = @{
    "图" = "Red"          # 多模态 → 大红色
    "狱" = "Magenta"      # 越狱 → 品红
    "调" = "Yellow"       # 工具调用 → 黄
    "思" = "Cyan"         # 思考 → 青
    "码" = "Blue"         # 编码 → 蓝
    "译" = "DarkMagenta"  # 翻译 → 深品红
    "通" = "DarkYellow"   # 通用能力 → 琥珀
    "双" = "Green"        # 双并发 → 绿
    "四" = "DarkGreen"    # 4并发 → 深绿
}

# ---------- 模型分类关键词 ----------
$VISION_KEYWORDS = @("vision", "vl")
$ASR_KEYWORDS = @("asr", "whisper", "nemotron-3.5-asr")

# ============================================================
#  辅助函数
# ============================================================

function Write-Color($text, $color) {
    $prev = [Console]::ForegroundColor
    [Console]::ForegroundColor = $color
    Write-Host $text -NoNewline
    [Console]::ForegroundColor = $prev
}

function Write-Line($text, $color) {
    Write-Color $text $color
    Write-Host ""
}

# 计算终端显示宽度（中文字符占2列，ASCII占1列）
function Get-DisplayWidth($text) {
    $w = 0
    foreach ($c in $text.ToCharArray()) {
        if ([int]$c -gt 127) { $w += 2 } else { $w += 1 }
    }
    return $w
}

# 按显示宽度补齐空格（对齐中文字符串）
function Pad-Display($text, $targetWidth) {
    $dw = Get-DisplayWidth $text
    $pad = $targetWidth - $dw
    if ($pad -le 0) { return $text }
    return $text + (" " * $pad)
}

# ============================================================
#  Artificial Analysis (AA) 权威人工智能分析指数 v4.1.1
#  覆盖: GDPval-AA, τ³-银行, 终端测试, SciCode, 人类最后考试, GPQA钻石, CritPt, AA全知, AA-LCR
# ============================================================
$BENCHMARK_DATA = @{
    "Qwen3.8-27B-Abliterated-Q6_K" = [PSCustomObject]@{
        AAIndex = 52.0
        Speed   = 36.7
        BestFor = "双槽极速"
    }
    "Qwen3.8-27B-NVFP4-MTP-MID-HIGH" = [PSCustomObject]@{
        AAIndex = 52.0
        Speed   = 38.7
        BestFor = "超长共享"
    }
    "Qwen3.8-27B-NVFP4-MTP-HIGHEST" = [PSCustomObject]@{
        AAIndex = 52.0
        Speed   = 37.9
        BestFor = "官方高精"
    }
    "Ornith-1.5-35B-Q4_K_M" = [PSCustomObject]@{
        AAIndex = 48.0
        Speed   = 72.1
        BestFor = "深度推理"
    }
    "Qwen3VL-8B-Instruct-Q8_0" = [PSCustomObject]@{
        AAIndex = 39.0
        Speed   = 61.5
        BestFor = "视觉标杆"
    }
    "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q6_K_P" = [PSCustomObject]@{
        AAIndex = 36.0
        Speed   = 60.0
        BestFor = "轻量多模"
    }
    "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M" = [PSCustomObject]@{
        AAIndex = 32.0
        Speed   = 88.9
        BestFor = "极速越狱"
    }
    "Qwen2.5-VL-3B-Instruct-Q4_K_M" = [PSCustomObject]@{
        AAIndex = 28.0
        Speed   = 25.0
        BestFor = "视觉眼睛"
    }
}

# 按参数量级别从小到大排序模型（4B < 8B < 27B < 35B，同级别排一起；ASR放末尾）
# 同级别内按推荐优先级与名称排序
function Sort-ModelsByRank($models) {
    $asr = @($models | Where-Object { $_.Category -eq "asr" })
    $rest = @($models | Where-Object { $_.Category -ne "asr" })
    if ($rest.Count -gt 0) {
        $rest = @($rest | ForEach-Object {
            $m = $_
            $pm = [regex]::Matches($m.Name, "(\d+(?:\.\d+)?)B")
            $rank = 0.0
            foreach ($match in $pm) {
                $v = [double]$match.Groups[1].Value
                if ($v -gt $rank) { $rank = $v }
            }
            if ($rank -eq 0 -and $m.SizeGB) {
                $rank = [math]::Round([double]$m.SizeGB, 1)
            }
            
            # 同参数量级别下的推荐优先级（如 27B 内部：双槽MTP -> 4并发 -> 多模态 -> NVFP4）
            $tier = 99
            if ($m.DisplayName -match "双槽MTP")      { $tier = 1 }
            elseif ($m.DisplayName -match "4并发")    { $tier = 2 }
            elseif ($m.DisplayName -match "多模态")   { $tier = 3 }
            elseif ($m.DisplayName -match "MID-HIGH") { $tier = 4 }
            elseif ($m.DisplayName -match "N-H")      { $tier = 5 }

            $m | Add-Member -NotePropertyName "_Rank" -NotePropertyValue $rank -Force
            $m | Add-Member -NotePropertyName "_TierSort" -NotePropertyValue $tier -Force
            $m
        } | Sort-Object _Rank, _TierSort, DisplayName)
    }
    return @($rest) + @($asr)
}

function Show-Banner {
    Clear-Host
    Write-Host ""
    # ── 超级大标题（粗线色块风格：3 行，高对比度，自适应宽度居中）──
    # figlet ASCII art 在 Windows 控制台下渲染差（# 散乱/不可辨认），改用粗线+颜色对比方案
    $w = $Host.UI.RawUI.WindowSize.Width
    if ($w -lt 40) { $w = 80 }
    $sep = [string]::new('=', $w)

    Write-Color $sep $C_TITLE
    $line = (" AI  大模型启动器  ·  Model Launcher ").PadLeft(([math]::Max(0, ($w - [System.Text.Encoding]::Default.GetByteCount(" AI  大模型启动器  ·  Model Launcher ")) / 2)))
    # 简化居中：用空格填充两侧（GBK 下 CJK=2显示宽）
    $text = "  AI   大模型启动器  ·  AI Model Launcher  "
    $padTotal = $w - [System.Text.Encoding]::Default.GetByteCount($text)
    if ($padTotal -gt 0) {
        $leftPad = [math]::Floor($padTotal / 2)
        $rightPad = $padTotal - $leftPad
        $text = (" " * $leftPad) + $text + (" " * $rightPad)
    }
    Write-Color $text $C_MODEL
    Write-Color $sep $C_TITLE
    Write-Host ""

    # ---- 硬件信息（两行排列） ----
    # 第1行: GPU+CUDA | CPU
    # 第2行: RAM+Build | OS

    $gpu = Get-GPUInfo
    if ($gpu) {
        $vramGB = [math]::Round($gpu.VRAM / 1024.0, 1)
        try { $cudaDrv = (nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>$null).Trim() } catch { $cudaDrv = "" }
        if (!$cudaDrv) { $cudaDrv = "N/A" }

        $cores = Get-PhysicalCores
        $threads = [Environment]::ProcessorCount
        try {
            $cpuName = ((Get-CimInstance Win32_Processor).Name.Trim() -replace '\s+', ' ')
        } catch { $cpuName = "Unknown" }
        try {
            $ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
        } catch { $ramGB = "?" }

        try {
            $os = (Get-CimInstance Win32_OperatingSystem).Caption -replace "Microsoft ", ""
        } catch { $os = "Windows" }

        # ┌─ 第1行（算力硬件：GPU + CPU） ────────────────────┐
        Write-Color "   " $C_HINT
        Write-Color "GPU:" $C_HINT
        Write-Color " $($gpu.Name) ${vramGB}G VRAM  " $C_TAG
        Write-Color "|" $C_HINT
        Write-Color "  CPU:" $C_HINT
        Write-Color "${cpuName} ${cores}C/${threads}T" $C_TAG
        Write-Host ""

        # └─ 第2行（其余信息：RAM + CUDA + Toolkit + 构建 + OS） ┘
        Write-Color "   " $C_HINT
        Write-Color "RAM:" $C_HINT
        Write-Color "${ramGB}G  " $C_TAG
        Write-Color "|" $C_HINT
        Write-Color "  CUDA:" $C_HINT
        Write-Color "$cudaDrv  " $C_TAG
        Write-Color "|" $C_HINT
        Write-Color "  Toolkit:" $C_HINT
        Write-Color "12.4  " $C_TAG
        Write-Color ("llama.cpp b" + $LLAMA_VERSION + "  ") $C_TAG
        Write-Color "|" $C_HINT
        Write-Color "  OS:" $C_HINT
        Write-Line $os $C_TAG
    } else {
        Write-Color "     GPU:  " $C_HINT
        Write-Color "CPU only" $C_WARN
        Write-Host ""
        try {
            $cores = Get-PhysicalCores; $threads = [Environment]::ProcessorCount
            $cpuName = ((Get-CimInstance Win32_Processor).Name.Trim() -replace '\s+', ' ')
        } catch { $cpuName = "?"; $cores="?"; $threads="?" }
        try { $ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1) } catch { $ramGB="?" }
        Write-Color "     CPU:  " $C_HINT
        Write-Color "${cpuName}  ${cores}C/${threads}T  " $C_TAG
        Write-Color "|  RAM:  " $C_HINT
        Write-Color "${ramGB} GB" $C_TAG
        Write-Host ""
    }

    Write-Host ""

    Show-FrontendConnect 8081
    }

# 获取 GPU 信息（仅 NVIDIA，用 nvidia-smi 校准显存）
function Get-GPUInfo {
    try {
        $exclude = @("Microsoft", "Parsec", "Oray", "GameViewer", "Virtual", "IddDriver")
        $allGPUs = Get-CimInstance Win32_VideoController
        $gpu = $null
        $maxVRAM = 0
        foreach ($g in $allGPUs) {
            $skip = $false
            foreach ($kw in $exclude) {
                if ($g.Name -match $kw) { $skip = $true; break }
            }
            if ($skip) { continue }
            $vram = [math]::Round($g.AdapterRAM / 1MB)
            if ($g.Name -match "NVIDIA") {
                try {
                    $smi = & nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
                    if ($smi) { $vram = [int]($smi.Trim()) }
                } catch { }
            }
            if ($vram -gt $maxVRAM) {
                $maxVRAM = $vram
                $gpu = @{ Name = $g.Name; VRAM = $vram }
            }
        }
        if ($gpu) { return $gpu }
    } catch { }
    return $null
}

# 获取 CPU 物理核心数（排除超线程）
function Get-PhysicalCores {
    try {
        $cores = (Get-CimInstance Win32_Processor).NumberOfCores
        if ($cores -gt 0) { return $cores }
    } catch { }
    return [Environment]::ProcessorCount
}

# 根据硬件 + 模型自动计算最优参数
function Get-AutoParams {
    param($Model, $GPU)

    $totalVRAM = if ($GPU) { $GPU.VRAM } else { 0 }

    # ---- ngl: V100 32GB 可完整加载绝大多数模型 ----
    $sizeGB = $Model.SizeGB
    $ngl = 0

    if ($totalVRAM -gt 0) {
        # V100 32GB TCC 档位
        if ($Model.Category -eq "vision") {
            if ($sizeGB -le 6.0)        { $ngl = 99 }
            elseif ($sizeGB -le 14.0)   { $ngl = 80 }
            else                        { $ngl = 50 }
        } else {
            if ($sizeGB -le 28.0)       { $ngl = 99 }
            else                        { $ngl = 60 }
        }
    }

    # ---- 上下文长度 (V100 32GB 可开大上下文) ----
    $ctx = 8192
    if ($sizeGB -le 1.0)        { $ctx = 32768 }
    elseif ($sizeGB -le 4.0)    { $ctx = 32768 }
    elseif ($sizeGB -le 8.0)    { $ctx = 16384 }
    elseif ($sizeGB -le 15.0)   { $ctx = 16384 }
    else                        { $ctx = 8192 }

    # ---- 温度 ----
    $temp = 0.7
    if ($Model.DisplayName -match "Coder|代码")     { $temp = 0.1 }
    elseif ($Model.DisplayName -match "DeepSeek|R1") { $temp = 0.6 }
    elseif ($Model.Category -eq "vision")            { $temp = 0.7 }

    # ---- 线程数：物理核心 ----
    $threads = Get-PhysicalCores
    if ($threads -le 0) { $threads = 4 }

    # ---- 批处理 / FlashAttn / mmap ----
    # V100 大算力, batch 可加大
    $batch = if ($totalVRAM -gt 16000) { 2048 } else { 1024 }
    $flashAttn = ($totalVRAM -gt 0)
    $noMmap = $false

    return @{
        NGL       = $ngl
        CtxSize   = $ctx
        Temp      = $temp
        Threads   = $threads
        BatchSize = $batch
        FlashAttn = $flashAttn
        NoMmap    = $noMmap
    }
}

# ============================================================
#  模型发现与分类
# ============================================================

function Get-Models {
    $models = @()
    $ggufFiles = Get-ChildItem -Path $MODELS_DIR -File | Where-Object { $_.Name -match '\.gguf' } | Sort-Object Name
    $mmprojFiles = @{}
    foreach ($f in $ggufFiles) {
        if ($f.Name -match "mmproj|mm-project|vision-proj") {
            $mmprojFiles[$f.FullName] = $f
        }
    }

    foreach ($f in $ggufFiles) {
        if ($f.Name -match "mmproj|mm-project|fastmtp|dflash") { continue }   # 投机草稿/多模态 sidecar（非独立模型），跳过

        $name = $f.BaseName

        # 跳过专用外挂/侧挂与草稿模型（PaddleOCR 和 Qwen2.5-VL 专职作为 27B 的 CPU 视觉外挂，不占用主菜单项）
        if ($name -match "PaddleOCR-VL|Qwen2\.5-VL-3B|Qwen3-0\.6B") { continue }
        $sizeGB = [math]::Round($f.Length / 1GB, 1)
        $sizeMB = [math]::Round($f.Length / 1MB, 0)
        $sizeStr = if ($sizeGB -ge 1) { "${sizeGB} GB" } else { "${sizeMB} MB" }
        $path = $f.FullName

        $category = "text"
        $mmproj = $null
        $tag = ""
        $customArgs = $null
        $useCustom  = $false
        $ability    = ""   # 特长能力解说（2-4字：图文/思考/工具调用/编码/推理/投机等），显示在"特长"列

        foreach ($kw in $VISION_KEYWORDS) {
            if ($name -match [regex]::Escape($kw)) {
                $category = "vision"
                break
            }
        }
        foreach ($kw in $ASR_KEYWORDS) {
            if ($name -match [regex]::Escape($kw)) {
                $category = "asr"
                break
            }
        }

        


        if ($category -eq "vision") {
            $base = $f.BaseName
            $candidates = @()
            foreach ($mk in $mmprojFiles.Keys) {
                $mb = [System.IO.Path]::GetFileNameWithoutExtension($mk)
                if ($base -match "qwen3\.6-35b" -and $mb -match "qwen3\.6-35b") {
                    $candidates += $mk
                }
                elseif ($base -match "qwen3\.6-27b" -and $mb -match "qwen3\.6-27b") {
                    $candidates += $mk
                }
            }
            # 兜底：如果精确匹配不到，按模型名前缀查找通用 mmproj
            if ($candidates.Count -eq 0) {
                $basePrefix = $base -replace '-\d+B.*', '' -replace '-Q\d+.*', '' -replace '\.gguf', ''
                $escapedPrefix = [regex]::Escape($basePrefix)
                foreach ($mk in $mmprojFiles.Keys) {
                    $mb = [System.IO.Path]::GetFileNameWithoutExtension($mk)
                    $mmpBase = $mb -replace '-mmproj.*', ''
                    $escapedMmp = [regex]::Escape($mmpBase)
                    if ($mb -match "mmproj" -and ($mb -match $escapedPrefix -or $basePrefix -match $escapedMmp)) {
                        $candidates += $mk
                        break
                    }
                }
            }
            if ($candidates.Count -gt 0) {
                $mmproj = $candidates[0]
            }

        }

        # 模型描述 + 标签 + 说明 + 场景推荐
        switch -Regex ($name) {
            "qwen3vl"                   {
                $displayName = "qwen3vl 8B"
                $tag  = "[8B][多模态][V100优化]"
                $ability = "图调速通"
                $customArgs = @(
                    "-ngl", "99",
                    "--cache-type-k", "q8_0",
                    "--cache-type-v", "q8_0",
                    "-c", "196608",
                    "-b", "2048",
                    "--ubatch-size", "2048",
                    "-t", "6",
                    "--parallel", "1",
                    "--flash-attn", "on",
                    "--image-min-tokens", "1024",
                    "--temp", "0.3",
                    "--top-p", "0.95",
                    "--top-k", "20",
                    "--min-p", "0.05",
                    "--dry-multiplier", "0.0",
                    "--dry-base", "1.75",
                    "--dry-allowed-length", "2",
                    "--dry-penalty-last-n", "256",
                    "--repeat-penalty", "1.05",
                    "--presence-penalty", "0.0",
                    "--jinja",
                    "--alias", "Qwen3VL-8B"
                )
                # 视觉模型：显式挂载 mmproj（qwen3vl 不匹配 vision 正则，自动挂载逻辑不触发）
                $mmproj = Join-Path $MODELS_DIR "mmproj-Qwen3VL-8B-Instruct-F16.gguf"
                $useCustom = $true
                break
            }
            # PaddleOCR-VL-1.6：0.9B 文档解析专用（OCR/表格/公式/图表/印章），官方 GGUF
            "paddleocr-vl"              {
                $displayName = "PaddleOCR-VL-1.6"
                $tag  = "[0.9B][文档解析][OCR][V100轻载]"
                $ability = "图通"
                $customArgs = @(
                    "-ngl", "99",
                    "--cache-type-k", "f16",
                    "--cache-type-v", "f16",
                    "-c", "32768",
                    "-b", "2048",
                    "--ubatch-size", "2048",
                    "-t", "6",
                    "--parallel", "1",
                    "--flash-attn", "on",
                    "--temp", "0",
                    "--jinja",
                    "--alias", "PaddleOCR-VL-1.6"
                )
                # 视觉模型：显式挂载 mmproj（官方 GGUF 的 mmproj 文件名含 -mmproj）
                $mmproj = Join-Path $MODELS_DIR "PaddleOCR-VL-1.6-GGUF-mmproj.gguf"
                $useCustom = $true
                break
            }
            # Qwen2.5-VL-3B-Instruct：3B 全能视觉小钢炮（端侧极速视觉/UI/图表/复杂场景）
            "qwen2\.5-vl-3b"              {
                $displayName = "Qwen2.5-VL-3B"
                $tag  = "[3B][Qwen2.5-VL][全能视觉眼睛][V100/CPU通用]"
                $ability = "图思话"
                $category = "vision"
                $customArgs = @(
                    "-ngl", "99",
                    "--cache-type-k", "q8_0",
                    "--cache-type-v", "q8_0",
                    "-c", "32768",
                    "-b", "2048",
                    "--ubatch-size", "2048",
                    "-t", "6",
                    "--parallel", "1",
                    "--flash-attn", "on",
                    "--temp", "0.3",
                    "--jinja",
                    "--alias", "Qwen2.5-VL-3B"
                )
                $mmproj = Join-Path $MODELS_DIR "mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf"
                $useCustom = $true
                break
            }
            # Qwen3.8-27B Uncensored HauhauCS Aggressive Q5_K_P（KV q8 + 256K 上下文）
            # 2026-08-18：当前用【嵌入式 MTP】（--spec-type draft-mtp，模型自带 MTP 头，b10435 原生支持，无需补丁）。
            #   实测（2026-08-18 spec_speed_q5kp，12 题多任务平均）：n-max=2 最优 = 38.7 t/s（无投机 23.2 → 提速 67%）
            #   FastMTP 完整版（3.02x，需补丁版 llama-server）备用参数——拿到补丁 exe 后启用下面两行并把 n-max 改 3：
            #     "--spec-draft-model", (Join-Path $MODELS_DIR "Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-FastMTP-32K.gguf"),
            #     "--spec-draft-ngl", "99",
            #   未打补丁 build 加载 sidecar 会报 "expected 5120, 248320, got 5120, 32768"。
            # Qwen3.8-27B Uncensored (Q6_K / Q5_K_P) + 144K 纯文本共享双并发 + Fixed 中级模板 + MTP
            "qwen3\.8-27b.*uncensored" {
                $isQ6 = ($name -match "q6_k")
                $displayName = if ($isQ6) { "Qwen3.8-27B-U-Q6_K" } else { "Qwen3.8-27B-UC-Agg" }
                $tag  = if ($isQ6) { "[27B][U-Q6_K][纯文本][MTP][144K双槽]" } else { "[27B][Uncensored][Q5_K_P][纯文本][MTP][96K]" }
                $ctxLen = if ($isQ6) { "147456" } else { "98304" }
                $parallel = if ($isQ6) { "2" } else { "1" }
                $ability = "狱思调双"
                $category = "text"
                $mmproj = $null
                $customArgs = @(
                    "-ngl", "99",
                    "--cache-type-k", "q8_0",
                    "--cache-type-v", "q8_0",
                    "-c", $ctxLen,
                    "-b", "2048",
                    "--ubatch-size", "2048",
                    "-t", "6",
                    "--parallel", $parallel,
                    "--kv-unified",
                    "--cache-reuse", "512",
                    "--flash-attn", "on",
                    "--ctx-checkpoints", "4",
                    "--spec-type", "draft-mtp",
                    "--spec-draft-n-max", "2",
                    "--spec-draft-n-min", "1",
                    "--temp", "0.3",
                    "--top-p", "0.95",
                    "--top-k", "20",
                    "--min-p", "0.05",
                    "--dry-multiplier", "0.0",
                    "--dry-base", "1.75",
                    "--dry-allowed-length", "2",
                    "--dry-penalty-last-n", "256",
                    "--repeat-penalty", "1.05",
                    "--presence-penalty", "0.0",
                    "--jinja",
                    "--chat-template-file", $QWEN38_TEMPLATE_FILE,
                    "--reasoning", $QWEN38_REASONING_MODE,
                    "--reasoning-effort", $QWEN38_REASONING_EFFORT,
                    "--reasoning-format", $QWEN38_REASONING_FORMAT,
                    "--reasoning-preserve",
                    "--no-warmup",
                    "--alias", $displayName
                )
                $useCustom = $true
                break
            }
            # Qwen3.8-27B NVFP4-MID-HIGH（Blackwell NVFP4 量化 + 4并发 256K 共享统一KV池 + 关MTP省显存）
            "qwen3\.8-27b.*nvfp4.*mtp.*(mid-high|medium)|qwen3\.8-27b.*mid-high" {
                $isMidHigh = ($name -match "mid-high")
                $displayName = "Qwen3.8-27B-MID-HIGH"
                $tag  = "[27B][MID-HIGH][纯文本][4并发][256K共享]"
                $alias = "Qwen3.8-27B-MID-HIGH"
                $ability = "思调"
                $category = "text"
                $mmproj = $null
                $customArgs = @(
                    "-ngl", "99",
                    "--cache-type-k", "q8_0",
                    "--cache-type-v", "q8_0",
                    "-c", "262144",
                    "-b", "2048",
                    "--ubatch-size", "512",
                    "-t", "6",
                    "--parallel", "4",
                    "--kv-unified",
                    "--cache-reuse", "512",
                    "--flash-attn", "on",
                    "--ctx-checkpoints", "2",
                    "--reasoning", $QWEN38_REASONING_MODE,
                    "--reasoning-budget", "2048",
                    "--reasoning-effort", $QWEN38_REASONING_EFFORT,
                    "--reasoning-format", $QWEN38_REASONING_FORMAT,
                    "--reasoning-preserve",
                    "--no-warmup",
                    "--temp", "0.3",
                    "--top-p", "0.95",
                    "--top-k", "20",
                    "--min-p", "0.05",
                    "--dry-multiplier", "0.0",
                    "--dry-base", "1.75",
                    "--dry-allowed-length", "2",
                    "--dry-penalty-last-n", "256",
                    "--repeat-penalty", "1.05",
                    "--presence-penalty", "0.0",
                    "--jinja",
                    "--chat-template-file", $QWEN38_TEMPLATE_FILE,
                    "--alias", $alias
                )
                $useCustom = $true
                break
            }
            # Qwen3.8-27B UD (Q5_K_XL / Q6_K_M) + 144K 纯文本共享双并发 + Fixed 中级模板 + MTP
            "qwen3\.8-27b.*ud" {
                $isQ5XL = ($name -match "q5_k_xl")
                $displayName = if ($isQ5XL) { "Qwen3.8-27B-UD-Q5KXL" } else { "Qwen3.8-27B-UD-Q6" }
                $tag  = if ($isQ5XL) { "[27B][UD-Q5KXL][纯文本][MTP][144K双槽]" } else { "[27B][UD-Q6][多模态][MTP][180K]" }
                $ctxLen = if ($isQ5XL) { "147456" } else { "184320" }
                $ability = "思调双"
                $category = "text"
                $mmproj = $null
                $customArgs = @(
                    "-ngl", "99",
                    "--cache-type-k", "q8_0",
                    "--cache-type-v", "q8_0",
                    "-c", $ctxLen,
                    "-b", "2048",
                    "--ubatch-size", "2048",
                    "-t", "6",
                    "--parallel", "2",
                    "--kv-unified",
                    "--cache-reuse", "512",
                    "--flash-attn", "on",
                    "--ctx-checkpoints", "4",
                    "--spec-type", "draft-mtp",
                    "--spec-draft-n-max", "2",
                    "--spec-draft-n-min", "1",
                    "--reasoning", $QWEN38_REASONING_MODE,
                    "--reasoning-budget", "2048",
                    "--reasoning-effort", $QWEN38_REASONING_EFFORT,
                    "--reasoning-format", $QWEN38_REASONING_FORMAT,
                    "--reasoning-preserve",
                    "--no-warmup",
                    "--temp", "0.3",
                    "--top-p", "0.95",
                    "--top-k", "20",
                    "--min-p", "0.05",
                    "--dry-multiplier", "0.0",
                    "--dry-base", "1.75",
                    "--dry-allowed-length", "2",
                    "--dry-penalty-last-n", "256",
                    "--repeat-penalty", "1.05",
                    "--presence-penalty", "0.0",
                    "--jinja",
                    "--chat-template-file", $QWEN38_TEMPLATE_FILE,
                    "--alias", $displayName
                )
                $useCustom = $true
                break
            }
            # Qwen3.8-27B Abliterated Q6_K 原生多模态单并发（mmproj 挂载；多模态下 cache-reuse 被源码强制禁用，故不设）
            "qwen3\.8-27b.*abliterated.*q6_k|qwen3\.8-27b.*abliterated" {
                $displayName = "Qwen3.8-27B-A [多模态]"
                $tag  = "[27B][A-Q6_K][原生多模态视觉][MTP][144K单槽]"
                $ability = "图思调狱"
                $category = "vision"
                $mmproj = Join-Path $MODELS_DIR "mmproj-Qwen3.8-27B-F16.gguf"
                $customArgs = @(
                    "-ngl", "99",
                    "--cache-type-k", "q8_0",
                    "--cache-type-v", "q8_0",
                    "-c", "147456",
                    "-b", "2048",
                    "--ubatch-size", "2048",
                    "-t", "6",
                    "--parallel", "1",   # 多模态单并发（mmproj 挂载 → cache-reuse 禁用，单槽最稳）
                    "--flash-attn", "on",
                    "--image-min-tokens", "1024",
                    "--no-mmproj-offload",   # mmproj 挂 CPU（不占显存）
                    "--ctx-checkpoints", "4",
                    "--spec-type", "draft-mtp",
                    "--spec-draft-n-max", "2",
                    "--spec-draft-n-min", "1",
                    "--reasoning", $QWEN38_REASONING_MODE,
                    "--reasoning-budget", "2048",
                    "--reasoning-effort", $QWEN38_REASONING_EFFORT,
                    "--reasoning-format", $QWEN38_REASONING_FORMAT,
                    "--reasoning-preserve",
                    "--no-warmup",
                    "--temp", "0.3",
                    "--top-p", "0.95",
                    "--top-k", "20",
                    "--min-p", "0.05",
                    "--dry-multiplier", "0.0",
                    "--dry-base", "1.75",
                    "--dry-allowed-length", "2",
                    "--dry-penalty-last-n", "256",
                    "--repeat-penalty", "1.05",
                    "--presence-penalty", "0.0",
                    "--jinja",
                    "--chat-template-file", $QWEN38_TEMPLATE_FILE,
                    "--alias", "Qwen3.8-27B-A-Q6_K"
                )
                $useCustom = $true
                break
            }
            # Qwen3.8-27B NVFP4-MTP-HIGHEST（纯文本 4并发 160K 共享池 + 关 MTP + Fixed 模板）
            "qwen3\.8-27b.*nvfp4.*mtp.*highest|qwen3\.8-27b.*highest" {
                $displayName = "Qwen3.8-27B-N-H"
                $tag  = "[27B][NVFP4-H][纯文本][4并发][160K共享]"
                $ability = "通思调"
                $category = "text"
                $mmproj = $null
                $customArgs = @(
                    "-ngl", "99",
                    "--cache-type-k", "q8_0",
                    "--cache-type-v", "q8_0",
                    "-c", "163840",   # kv-unified 共享池 160K（4槽位共享统一池；显存稳定在 28G 黄金区间）
                    "-b", "2048",
                    "--ubatch-size", "512",
                    "-t", "6",
                    "--parallel", "4",   # 纯文本 4 槽并发
                    "--kv-unified",      # 统一 KV 缓冲区（多槽共享，启用 cache-reuse）
                    "--cache-reuse", "512",   # 前缀/KV 缓存复用
                    "--flash-attn", "on",
                    "--ctx-checkpoints", "2",
                    "--reasoning", $QWEN38_REASONING_MODE,
                    "--reasoning-budget", "2048",
                    "--reasoning-effort", $QWEN38_REASONING_EFFORT,
                    "--reasoning-format", $QWEN38_REASONING_FORMAT,
                    "--reasoning-preserve",
                    "--no-warmup",
                    "--temp", "0.3",
                    "--top-p", "0.95",
                    "--top-k", "20",
                    "--min-p", "0.05",
                    "--dry-multiplier", "0.0",
                    "--dry-base", "1.75",
                    "--dry-allowed-length", "2",
                    "--dry-penalty-last-n", "256",
                    "--repeat-penalty", "1.05",
                    "--presence-penalty", "0.0",
                    "--jinja",
                    "--chat-template-file", $QWEN38_TEMPLATE_FILE,
                    "--alias", "Qwen3.8-27B-N-H"
                )
                $useCustom = $true
                break
            }
            "qwen3\.5-4b"            {
                $displayName = "Qwen3.5-4B"
                $tag  = "[4B][文本][Uncensored][Q4_K_M]"
                $ability = "狱思通"
                $customArgs = @(
                    "-ngl", "99",
                    "--cache-type-k", "q8_0",
                    "--cache-type-v", "q8_0",
                    "-c", "262144",
                    "-b", "2048",
                    "-t", "6",
                    "--parallel", "1",
                    "--flash-attn", "on",
                    "--reasoning", $QWEN38_REASONING_MODE,
                    "--reasoning-effort", $QWEN38_REASONING_EFFORT,
                    "--reasoning-format", $QWEN38_REASONING_FORMAT,
                    "--reasoning-preserve",
                    "--temp", "0.3",
                    "--top-p", "0.95",
                    "--top-k", "20",
                    "--min-p", "0.05",
                    "--dry-multiplier", "0.8",
                    "--dry-base", "1.75",
                    "--dry-allowed-length", "2",
                    "--dry-penalty-last-n", "256",
                    "--repeat-penalty", "1.0",
                    "--presence-penalty", "0.0",
                    "--jinja",
                    "--chat-template-file", $QWEN38_TEMPLATE_FILE,
                    "--alias", "Qwen3.5-4B"
                )
                $useCustom = $true
                break
            }

            # Qwen3.5-0.8B（532MB 极轻量，参数全拉满：全量 GPU + KV f16 + 256K 原生上下文）
            # 显存核算：权重 0.5 GiB + KV f16@256K（2KV头×24层×256hd）≈ 12 GiB → 总 ~13 GiB，余 ~19 GiB
            "qwen3\.5-0\.8b"         {
                $displayName = "Qwen3.5-0.8B"
                $tag  = "[0.8B][文本][Q4_K_M][256K][全拉满]"
                $ability = "通"
                $customArgs = @(
                    "-ngl", "99",
                    "--cache-type-k", "f16",
                    "--cache-type-v", "f16",
                    "-c", "262144",
                    "-b", "2048",
                    "-t", "6",
                    "--parallel", "1",
                    "--flash-attn", "on",
                    "--reasoning", $QWEN38_REASONING_MODE,
                    "--reasoning-effort", $QWEN38_REASONING_EFFORT,
                    "--reasoning-format", $QWEN38_REASONING_FORMAT,
                    "--reasoning-preserve",
                    "--temp", "0.3",
                    "--top-p", "0.95",
                    "--top-k", "20",
                    "--min-p", "0.05",
                    "--dry-multiplier", "0.8",
                    "--dry-base", "1.75",
                    "--dry-allowed-length", "2",
                    "--dry-penalty-last-n", "256",
                    "--repeat-penalty", "1.0",
                    "--presence-penalty", "0.0",
                    "--jinja",
                    "--chat-template-file", $QWEN38_TEMPLATE_FILE,
                    "--alias", "Qwen3.5-0.8B"
                )
                $useCustom = $true
                break
            }

            "gemma.*4.*e4b"          {
                $displayName = "Gemma-4-E4B"
                $tag  = "[4B][MoE][多模态][Q6_K_P]"
                $ability = "狱图通"
                $category = "vision"
                $mmproj = Join-Path $MODELS_DIR "mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-f16.gguf"
                $customArgs = @(
                    "-ngl", "99",
                    "--cache-type-k", "f16",
                    "--cache-type-v", "f16",
                    "-c", "131072",
                    "-b", "2048",
                    "-t", "6",
                    "--parallel", "1",
                    "--flash-attn", "on",
                    "--image-min-tokens", "1024",
                    "--temp", "0.3",
                    "--top-p", "0.95",
                    "--top-k", "20",
                    "--min-p", "0.05",
                    "--dry-multiplier", "0.8",
                    "--dry-base", "1.75",
                    "--dry-allowed-length", "2",
                    "--dry-penalty-last-n", "256",
                    "--repeat-penalty", "1.0",
                    "--presence-penalty", "0.0",
                    "--jinja",
                    "--alias", "Gemma-4-E4B"
                )
                $useCustom = $true
                break
            }

            "ornith-1\.[05]"     {
                $displayName = if ($name -match "1\.5") { "Ornith-1.5-35B" } else { "Ornith-1.0-35B" }
                $tag  = "[35B][Q4_K_M][多模态][128K]"
                $ability = "狱图通思"
                $category = "vision"
                $mmproj = Join-Path $MODELS_DIR "mmproj-Ornith-1.5-35B-A3B-f16.gguf"
                $customArgs = @(
                    "-ngl", "99",
                    "--cache-type-k", "q8_0",
                    "--cache-type-v", "q8_0",
                    "-c", "131072",
                    "-b", "4096",
                    "--ubatch-size", "4096",
                    "-t", "6",
                    "--parallel", "1",
                    "--flash-attn", "on",
                    "--image-min-tokens", "1024",
                    "--ctx-checkpoints", "4",
                    "--reasoning", $QWEN38_REASONING_MODE,
                    "--reasoning-effort", $QWEN38_REASONING_EFFORT,
                    "--reasoning-format", $QWEN38_REASONING_FORMAT,
                    "--reasoning-preserve",
                    "--no-mmproj-offload",
                    "--temp", "0.3",
                    "--top-p", "0.95",
                    "--top-k", "20",
                    "--min-p", "0.05",
                    "--dry-multiplier", "0.0",
                    "--dry-base", "1.75",
                    "--dry-allowed-length", "2",
                    "--dry-penalty-last-n", "256",
                    "--repeat-penalty", "1.05",
                    "--presence-penalty", "0.0",
                    "--jinja",
                    "--chat-template-file", $QWEN38_TEMPLATE_FILE,
                    "--alias", $displayName
                )
                $useCustom = $true
                break
            }

            default          {
                $displayName = $name
                $tag  = "[未知]"
                # 未知模型按名字推断能力字组合：狱=越狱 / 码=编码 / 理=推理 / 图=多模态 / 调=工具
                $ability = ""
                if ($name -match "uncensored|aggressive|jailbreak|越狱") { $ability += "狱" }
                if ($name -match "coder|code|代码")                       { $ability += "码" }
                if ($name -match "deepseek|r1|推理|reasoning|math|数学|算|multilingual|翻译|trans|对话|创作|speed|fast") { $ability += "通" }
                if ($mmproj -or $category -eq "vision")                   { $ability += "图" }
                if ($name -match "qwen3|tool|工具")                       { $ability += "调" }
                if ($name -match "reason|think|思考")                     { $ability += "思" }
                if (-not $ability)                                         { $ability = "通" }   # 兜底：通用能力
            }
        }

        # 特长能力统一规则（2026-08-18）：
        # 1) 每个模型特长最多 4 个字（超长裁剪，如 Ornith 的 狱图通思 → 保留前 4）
        if ($ability.Length -gt 4)                       { $ability = $ability.Substring(0, 4) }
        # 3) 并发标记：--parallel ≥ 4 标记"四"，--parallel ≥ 2 标记"双"
        if ($customArgs) {
            for ($i = 0; $i -lt $customArgs.Count - 1; $i++) {
                if ($customArgs[$i] -eq "--parallel") {
                    $pCount = [int]$customArgs[$i + 1]
                    if ($pCount -ge 4) {
                        if (-not $ability.Contains("四")) { $ability += "四" }
                    } elseif ($pCount -ge 2) {
                        if (-not $ability.Contains("双")) { $ability += "双" }
                    }
                    break
                }
            }
        }

        # 上下文长度（总览列显示用）：自定义参数模型从 -c 提取；自动参数模型按服务模式规则（≤4GB→32K，其余 16K）
        $ctxVal = 16384
        if ($customArgs) {
            for ($i = 0; $i -lt $customArgs.Count - 1; $i++) {
                if ($customArgs[$i] -eq "-c") { $ctxVal = [int]$customArgs[$i + 1]; break }
            }
        } elseif ($sizeGB -le 4.0) {
            $ctxVal = 32768
        }
        $ctxStr = "$([math]::Round($ctxVal / 1024))K"

        # 量化级别（从文件名提取，统一为大写显示）：NVFP4 / Q8_0 / Q6_K / Q5_K_M / Q4_K_M / IQ4_XS / F16 等
        # 正则顺序：NVFP4 放最前（避免 Q 误捕）；IQ\d+ 次之（避免 IQ4_XS 被 Q4_XS 抢匹配）；Q8nextn 单列
        $quant = "-"
        if ($name -match "(NVFP4|IQ\d+_[A-Z0-9]+|Q\d+_[A-Z0-9]+(?:_[A-Z0-9]+)?|Q8nextn|F16|BF16|FP16)") {
            $quant = $Matches[1].ToUpper()
        }

        # KV 缓存量化（总览列显示用）：自定义参数模型从 --cache-type-k/v 提取（如 q8_0→Q8）；
        # 未显式设置（自动参数/未知模型）按 llama-server 默认 f16 显示 F16
        $kvK = "f16"; $kvV = "f16"
        if ($customArgs) {
            for ($i = 0; $i -lt $customArgs.Count - 1; $i++) {
                if ($customArgs[$i] -eq "--cache-type-k") { $kvK = $customArgs[$i + 1] }
                elseif ($customArgs[$i] -eq "--cache-type-v") { $kvV = $customArgs[$i + 1] }
            }
        }
        $kvCache = (Get-KvDisplay $kvK) + "/" + (Get-KvDisplay $kvV)

        $models += [PSCustomObject]@{
            Name         = $name
            DisplayName  = $displayName
            Tag          = $tag
            Path         = $path
            Category     = $category
            SizeStr      = $sizeStr
            SizeMB       = $sizeMB
            SizeGB       = $sizeGB
            MMProj       = $mmproj
            CustomArgs   = $customArgs
            UseCustom    = $useCustom
            CtxStr       = $ctxStr
            Ability      = $ability
            Quant        = $quant
            KvCache      = $kvCache
        }

        # ---- Abliterated 纯文本 双并发+MTP 变体（不挂 mmproj → 统一池双槽 144K + 嵌入式 MTP）----
        if ($name -match "abliterated") {
            $models += [PSCustomObject]@{
                Name         = $name
                DisplayName  = "Qwen3.8-27B-A [双槽MTP]"
                Tag          = "[27B][A-Q6_K][纯文本][双槽MTP加速][144K]"
                Path         = $path
                Category     = "text"
                SizeStr      = $sizeStr
                SizeMB       = $sizeMB
                SizeGB       = $sizeGB
                MMProj       = $null
                CustomArgs   = @(
                    "-ngl", "99",
                    "--cache-type-k", "q8_0",
                    "--cache-type-v", "q8_0",
                    "-c", "147456",   # kv-unified 共享池 144K（双槽各 72K；显存稳定在 29.8G 黄金区间）
                    "-b", "2048",
                    "--ubatch-size", "2048",
                    "-t", "6",
                    "--parallel", "2",   # 纯文本 2 槽双并发
                    "--kv-unified",      # 统一 KV 缓冲区（启用 cache-reuse）
                    "--cache-reuse", "512",   # 前缀/KV 缓存复用
                    "--flash-attn", "on",
                    "--ctx-checkpoints", "4",
                    "--spec-type", "draft-mtp",
                    "--spec-draft-n-max", "2",
                    "--spec-draft-n-min", "1",
                    "--reasoning", $QWEN38_REASONING_MODE,
                    "--reasoning-budget", "2048",
                    "--reasoning-effort", $QWEN38_REASONING_EFFORT,
                    "--reasoning-format", $QWEN38_REASONING_FORMAT,
                    "--reasoning-preserve",
                    "--no-warmup",
                    "--temp", "0.3",
                    "--top-p", "0.95",
                    "--top-k", "20",
                    "--min-p", "0.05",
                    "--dry-multiplier", "0.0",
                    "--dry-base", "1.75",
                    "--dry-allowed-length", "2",
                    "--dry-penalty-last-n", "256",
                    "--repeat-penalty", "1.05",
                    "--presence-penalty", "0.0",
                    "--jinja",
                    "--chat-template-file", $QWEN38_TEMPLATE_FILE,
                    "--alias", "Qwen3.8-27B-A-Q6_K"
                )
                UseCustom    = $true
                CtxStr       = "144K"
                Ability      = "狱思调双"
                Quant        = $quant
                KvCache      = $kvCache
            }

            # ---- Abliterated 纯文本 4 并发变体（不挂 mmproj → cache-reuse 生效，需 kv-unified；关 MTP 高吞吐）----
            $models += [PSCustomObject]@{
                Name         = $name
                DisplayName  = "Qwen3.8-27B-A [4并发]"
                Tag          = "[27B][A-Q6_K][纯文本][4槽高吞吐][160K共享]"
                Path         = $path
                Category     = "text"
                SizeStr      = $sizeStr
                SizeMB       = $sizeMB
                SizeGB       = $sizeGB
                MMProj       = $null
                CustomArgs   = @(
                    "-ngl", "99",
                    "--cache-type-k", "q8_0",
                    "--cache-type-v", "q8_0",
                    "-c", "163840",   # kv-unified 共享池 160K（4槽位共享统一池；显存稳定在 27G~30.5G 黄金区间）
                    "-b", "2048",
                    "--ubatch-size", "512",
                    "-t", "6",
                    "--parallel", "4",   # 纯文本 4 槽并发
                    "--kv-unified",      # 统一 KV 缓冲区（否则 kv_unified=false → 无法平移 → cache-reuse 被静默禁用）
                    "--cache-reuse", "512",   # 前缀/KV 缓存复用（纯文本模型才生效；多模态下被源码禁用）
                    "--flash-attn", "on",
                    "--ctx-checkpoints", "2",
                    "--reasoning", $QWEN38_REASONING_MODE,
                    "--reasoning-budget", "2048",
                    "--reasoning-effort", $QWEN38_REASONING_EFFORT,
                    "--reasoning-format", $QWEN38_REASONING_FORMAT,
                    "--reasoning-preserve",
                    "--no-warmup",
                    "--temp", "0.3",
                    "--top-p", "0.95",
                    "--top-k", "20",
                    "--min-p", "0.05",
                    "--dry-multiplier", "0.0",
                    "--dry-base", "1.75",
                    "--dry-allowed-length", "2",
                    "--dry-penalty-last-n", "256",
                    "--repeat-penalty", "1.05",
                    "--presence-penalty", "0.0",
                    "--jinja",
                    "--chat-template-file", $QWEN38_TEMPLATE_FILE,
                    "--alias", "Qwen3.8-27B-A-Q6_K"
                )
                UseCustom    = $true
                CtxStr       = "160K"
                Ability      = "狱思调四"
                Quant        = $quant
                KvCache      = $kvCache
            }
        }
    }

    # 排序统一由 Sort-ModelsByRank 处理（按参数量级别排，菜单与 -ListModels 共用）

    return $models
}

# KV 缓存量化显示格式：q8_0→Q8 / f16→F16 / i8→I8 / bf16→BF16 / 其它原样大写
function Get-KvDisplay($t) {
    if ($t -match "^bf(\d+)") { return "BF" + $Matches[1] }
    if ($t -match "^[qfi](\d+)") { return $t.Substring(0, 1).ToUpper() + $Matches[1] }
    return $t.ToUpper()
}

# ============================================================
#  菜单显示
# ============================================================

function Show-ModelGrid {
    param([array]$Models, [string]$Title)

    # 读取 JSON 数据库（PS 5.1 兼容：显式 UTF-8 编码读取）
    $dbPath = Join-Path $PSScriptRoot "models_db.json"
    $db = @{}
    if (Test-Path $dbPath) {
        try {
            $dbObj = Get-Content $dbPath -Raw -Encoding UTF8 -ErrorAction SilentlyContinue | ConvertFrom-Json
            if ($dbObj) {
                foreach ($prop in $dbObj.PSObject.Properties) {
                    $db[$prop.Name] = $prop.Value
                }
            }
        } catch { $db = @{} }
    }

    # 构建数据表
    $tableData = @()
    $idx = 0
    foreach ($m in $Models) {
        $idx++
        $rec = if ($db.ContainsKey($m.DisplayName)) { $db[$m.DisplayName] } elseif ($db.ContainsKey($m.Name)) { $db[$m.Name] } else { $null }

        $speed   = if ($rec -and $null -ne $rec.speed)    { ([math]::Round($rec.speed, 1).ToString("0.0") + " t/s") } else { "-" }
        $aaStr   = if ($rec -and $null -ne $rec.aaIndex)  { [math]::Round($rec.aaIndex, 0).ToString() } elseif ($m.Name -match "paddleocr|locate-anything") { "专项" } else { "-" }
        $bestFor = if ($rec -and $null -ne $rec.bestFor)  { $rec.bestFor } else { "通用模型" }
        if ($bestFor.Length -gt 4) { $bestFor = $bestFor.Substring(0, 4) }

        $tableData += [PSCustomObject]@{
            "序号"   = "[$idx]"
            "模型名" = $m.DisplayName
            "tok/S"  = $speed
            "大小"   = $m.SizeStr
            "上下文" = $m.CtxStr
            "KV缓存" = $m.KvCache
            "量化"   = $m.Quant
            "AA指数" = $aaStr
            "特长"   = $m.Ability
            "场景定位" = $bestFor
        }
    }

    # 手动 Pad-Display 渲染 (AA指数移动到量化后面)
    $widths = @(5, 24, 10, 8, 7, 8, 9, 8, 8, 10)
    $headers = @('序号','模型名','tok/S','大小','上下文','KV缓存','量化','AA指数','特长','场景定位')

    # 根据单元格内容返回配色
    function Get-CellColor($col, $val) {
        $num = -1.0
        if ($val -match '^([\d.]+)') { $num = [double]$Matches[1] }
        switch ($col) {
            "序号"   { return $C_INPUT }
            "模型名" { return $C_MODEL }
            "tok/S"  {
                if ($num -lt 0)   { return $C_HINT }
                if ($num -ge 60)  { return $C_OK }
                if ($num -ge 35)  { return $C_TAG }
                return "Yellow"
            }
            "大小"   { return $C_VRAM }
            "上下文" { return $C_CTX }
            "KV缓存" {
                if ($val -match "F16")      { return "White" }
                if ($val -match "^Q8/Q8$")  { return "Cyan" }
                if ($val -match "Q\d+")     { return "Yellow" }
                return "DarkGray"
            }
            "量化" {
                if ($val -match "NVFP4")            { return "Magenta" }
                if ($val -match "F16|BF16|FP16")    { return "White" }
                if ($val -match "Q8")               { return "Green" }
                if ($val -match "Q6")               { return "Cyan" }
                if ($val -match "Q5")               { return "Yellow" }
                if ($val -match "Q4|IQ4")           { return "DarkYellow" }
                if ($val -match "Q3|Q2|Q1|IQ[23]")  { return "DarkRed" }
                return "DarkGray"
            }
            "AA指数" {
                if ($val -eq "专项") { return "DarkGray" }
                if ($num -ge 50)     { return "Cyan" }       # 顶尖开源 (Qwen3.8-27B 52分)
                if ($num -ge 40)     { return "Green" }      # 优秀 (Ornith 35B 48分)
                if ($num -ge 30)     { return "Yellow" }     # 良好 (Qwen3VL 39分, Gemma4 36分)
                if ($num -ge 20)     { return "DarkYellow" } # 端侧 (Qwen2.5-VL 28分)
                return "DarkGray"
            }
            "场景定位" { return $C_TAG }
            default { return $C_MODEL }
        }
    }

    # 表头
    for ($i = 0; $i -lt $headers.Count; $i++) {
        Write-Color (Pad-Display $headers[$i] $widths[$i]) $C_HINT
        if ($i -lt $headers.Count - 1) { Write-Color " " $C_HINT }
    }
    Write-Host ""

    # 数据行
    foreach ($row in $tableData) {
        $cols = @('序号','模型名','tok/S','大小','上下文','KV缓存','量化','AA指数','特长','场景定位')
        for ($i = 0; $i -lt $cols.Count; $i++) {
            $val = $row.($cols[$i])
            if ($cols[$i] -eq "特长") {
                # 特长列：能力字逐字着色（ABILITY_COLORS），宽度不足补空格
                $wUsed = 0
                foreach ($ch in $val.ToCharArray()) {
                    $cc = if ($ABILITY_COLORS.ContainsKey($ch.ToString())) { $ABILITY_COLORS[$ch.ToString()] } else { $C_HINT }
                    Write-Color $ch.ToString() $cc
                    $wUsed += 2
                }
                if ($wUsed -lt $widths[$i]) { Write-Color (" " * ($widths[$i] - $wUsed)) $C_HINT }
            } else {
                Write-Color (Pad-Display $val $widths[$i]) (Get-CellColor $cols[$i] $val)
            }
            if ($i -lt $cols.Count - 1) { Write-Color " " $C_HINT }
        }
        Write-Host ""
    }
}

# ============================================================
#  系统资源监控（内联版 — 在主线程 ForEach-Object 中每 10 秒采样）
#  Start-Job 在子进程中无法可靠调用 nvidia-smi 和 WMI，
#  改为直接用函数在主线程管道内定时采样。
# ============================================================
function Write-MonitorSample {
    param(
        [string]$LogFile,
        [string]$ModelName
    )

    if (-not $LogFile) { return }

    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss.fff")

    # ---- CPU ----
    $cpu = -1.0
    try {
        $cpuData = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq "_Total" }
        if ($cpuData) { $cpu = [math]::Round($cpuData.PercentProcessorTime, 1) }
    } catch { $cpu = -1.0 }

    # ---- RAM ----
    $ramUsed = -1.0; $ramTotal = -1.0; $ramPct = -1.0
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
        $ramTotal = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
        $ramFree  = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
        $ramUsed  = [math]::Round($ramTotal - $ramFree, 1)
        if ($ramTotal -gt 0) { $ramPct = [math]::Round(($ramUsed / $ramTotal) * 100.0, 1) }
    } catch { }

    # ---- GPU ----
    $gpuMemUsed = -1; $gpuMemTotal = -1; $gpuUtil = -1; $gpuTemp = -1
    try {
        $smi = & nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu `
            --format=csv,noheader,nounits 2>$null
        if ($smi) {
            $parts = $smi -split ','
            $gpuMemUsed  = [int]($parts[0].Trim())
            $gpuMemTotal = [int]($parts[1].Trim())
            $gpuUtil     = [int]($parts[2].Trim())
            $gpuTemp     = [int]($parts[3].Trim())
            if ($gpuMemUsed -gt $script:g_PeakGpuMem) { $script:g_PeakGpuMem = $gpuMemUsed }
        }
    } catch { }

    # ---- Disk ----
    $diskFreeGB = -1.0
    try {
        $drv = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='E:'" -ErrorAction SilentlyContinue
        if ($drv) { $diskFreeGB = [math]::Round($drv.FreeSpace / 1GB, 1) }
    } catch { }

    # ---- 告警检查 ----
    $alerts = @()
    $gpuPct = if ($gpuMemTotal -gt 0) { [math]::Round(($gpuMemUsed / $gpuMemTotal) * 100.0, 1) } else { -1 }
    if ($gpuPct -gt 90) { $alerts += "WARN GPU_VRAM ${gpuPct}% > 90%" }
    if ($gpuUtil -eq 0 -and $gpuPct -gt 70) { $alerts += "WARN GPU_STALLED VRAM=${gpuPct}% util=0%" }
    if ($cpu -gt 80) { $alerts += "WARN CPU_LOAD ${cpu}% > 80%" }
    if ($diskFreeGB -ge 0 -and $diskFreeGB -lt 5.0) { $alerts += "WARN DISK_LOW E: ${diskFreeGB}GB < 5GB" }

    $monLine = "MONITOR model=${ModelName} | GPU_MEM=${gpuMemUsed}/${gpuMemTotal}MB(${gpuPct}%) GPU_UTIL=${gpuUtil}% GPU_TEMP=${gpuTemp}C | CPU=${cpu}% | RAM=${ramUsed}/${ramTotal}GB(${ramPct}%) | DISK_FREE=${diskFreeGB}GB"

    try {
        [System.IO.File]::AppendAllText($LogFile, "[$ts] $monLine" + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
        foreach ($a in $alerts) {
            [System.IO.File]::AppendAllText($LogFile, "[$ts] $a" + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
        }
    } catch { }
}

function Start-LlamaServer {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$ServerArgs
    )

    # 确保没有残留的 CUDA_VISIBLE_DEVICES 环境变量导致主模型识别不到 GPU
    [Environment]::SetEnvironmentVariable('CUDA_VISIBLE_DEVICES', $null, 'Process')
    Remove-Item Env:\CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue

    # 从 ServerArgs 中提取模型路径和名称
    $modelPath = ""
    $modelName = ""
    for ($i = 0; $i -lt $ServerArgs.Count; $i++) {
        if ($ServerArgs[$i] -eq "-m" -and ($i + 1) -lt $ServerArgs.Count) {
            $modelPath = $ServerArgs[$i + 1]
            $modelName = [System.IO.Path]::GetFileNameWithoutExtension($modelPath)
            break
        }
    }

    $logDir = Join-Path $LLAMA_DIR "logs"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

    # ---- 每日日志（主日志）：每天 1 个文件，自动累加；超阈值滚动到 1 个溢出文件 ----
    if (-not $g_DailyLogFile) {
        $stamp = (Get-Date).ToString("yyyyMMdd")
        $script:g_LogStamp = $stamp
        $script:g_LogMain = Join-Path $logDir ("8083_llama_" + $stamp + ".log")
        $script:g_LogOverflow = Join-Path $logDir ("8083_llama_" + $stamp + ".2.log")
        $script:g_DailyLogFile = $script:g_LogMain
    }

    # 写入启动头
    Write-DailyLog ""
    Write-DailyLog "--- SESSION START [${modelName}] ---"
    Write-DailyLog "MODEL: ${modelName}"
    Write-DailyLog "ARGS: $($ServerArgs -join ' ')"

    # ---- 单日单文件：服务器原始输出直接并入每日日志，不再生成 per-run 原始日志 ----
    Write-DailyLog "LOG: 当前日志文件 = ${script:g_DailyLogFile}"

    # 提取并分离对外监听端口与 llama-server 内部后端端口
    $listenPort = 8081
    for ($i = 0; $i -lt $ServerArgs.Count - 1; $i++) {
        if ($ServerArgs[$i] -eq "--port") { $listenPort = [int]$ServerArgs[$i + 1]; break }
    }
    $backendPort = 8083
    if ($listenPort -eq $backendPort) { $backendPort = 8084 }

    # 更新 ServerArgs 中 llama-server 实际监听的端口为 backendPort
    $newArgs = @()
    for ($i = 0; $i -lt $ServerArgs.Count; $i++) {
        if ($ServerArgs[$i] -eq "--port" -and ($i + 1) -lt $ServerArgs.Count) {
            $newArgs += "--port", "$backendPort"
            $i++
        } else {
            $newArgs += $ServerArgs[$i]
        }
    }
    $ServerArgs = $newArgs

    # ---- 彻底清理残留的 llama 进程与 8083 端口（严格遵守 AGENTS.md 规范） ----
    try {
        Get-Process | Where-Object { $_.ProcessName -match "llama" } | Stop-Process -Force -ErrorAction SilentlyContinue
        Get-NetTCPConnection -LocalPort $backendPort -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    } catch { }
    
    # 循环检查 GPU 显存直至安全释放
    for ($waitIdx = 0; $waitIdx -lt 10; $waitIdx++) {
        try {
            $usedStr = (nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null).Trim()
            if ($usedStr -and [int]$usedStr -lt 600) { break }
        } catch { }
        Start-Sleep -Milliseconds 300
    }
    Ensure-GatewayRunning

    # ---- 仅限文本型 27B 模型：自动挂载 8085 (Qwen2.5-VL-3B) CPU 视觉侧挂 ----
    $script:g_Sidecar8085Process = $null
    $visionMainPort = 0

    $hasMmproj = $false
    for ($i = 0; $i -lt $ServerArgs.Count; $i++) {
        if ($ServerArgs[$i] -eq "--mmproj") { $hasMmproj = $true; break }
    }
    $isText27B = ($modelName -match "27b" -and -not $hasMmproj)
    if ($isText27B) {
        $qwenVlModel = Join-Path $MODELS_DIR "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf"
        $qwenVlMmproj = Join-Path $MODELS_DIR "mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf"

        # 启动 8085 Qwen2.5-VL-3B (主视觉推理大脑，纯 CPU 运行，强制 CUDA 隔离以保持 0 显存占用)
        if ((Test-Path $qwenVlModel) -and (Test-Path $qwenVlMmproj)) {
            try {
                $conns8085 = Get-NetTCPConnection -LocalPort 8085 -ErrorAction SilentlyContinue
                foreach ($c in $conns8085) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
                $v8085Log = Join-Path $logDir ("8085_sidecar_" + (Get-Date -Format "yyyyMMdd") + ".log")
                $args8085 = @(
                    "-m", $qwenVlModel,
                    "--mmproj", $qwenVlMmproj,
                    "-ngl", "0",
                    "-c", "32768",
                    "-b", "2048",
                    "-t", "6",
                    "--parallel", "1",
                    "--port", "8085",
                    "--api-key", $ApiKey,
                    "--log-file", $v8085Log
                )
                # 启动 8085 Qwen2.5-VL-3B (主视觉推理大脑，纯 CPU 运行，使用独立 ProcessStartInfo 隔离 CUDA，绝不污染主进程)
                $psi = New-Object System.Diagnostics.ProcessStartInfo
                $psi.FileName = $LLAMA_SERVER
                $psi.Arguments = ($args8085 -join " ")
                $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
                $psi.CreateNoWindow = $true
                $psi.UseShellExecute = $false
                $psi.EnvironmentVariables["CUDA_VISIBLE_DEVICES"] = "-1"
                $script:g_Sidecar8085Process = [System.Diagnostics.Process]::Start($psi)

                # 确保当前主会话的 CUDA 环境不受任何污染（保证主模型 100% 识别 GPU）
                [Environment]::SetEnvironmentVariable('CUDA_VISIBLE_DEVICES', $null, 'Process')
                Remove-Item Env:\CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue

                $visionMainPort = 8085
                Write-DailyLog "SIDECAR: Started 8085 Qwen2.5-VL-3B CPU Vision Engine PID=$($script:g_Sidecar8085Process.Id) | log=$v8085Log"
                Write-Host ""
                Write-Line "  ┌─ 👁️ 8085 视觉侧挂眼睛已自动激活 ──────────────────────────────────────────" $C_TAG
                Write-Line "  │  视觉模型: Qwen2.5-VL-3B (纯 CPU 内存运行 · 0 显存占用 · 32K 上下文)" $C_MODEL
                Write-Line "  │  协同模式: 两阶段级联图文协同 (发图给 8081 网关自动 OCR 注入 27B 逻辑大脑)" $C_OK
                Write-Line "  │  协同端口: http://127.0.0.1:8085/v1 (内部受控)" $C_HINT
                Write-Line "  └────────────────────────────────────────────────────────────────────────" $C_TAG
                Write-Host ""
            } catch {
                Write-DailyLog "SIDECAR_WARN: Failed to start 8085 sidecar: $_"
            }
        }
    } elseif ($hasMmproj) {
        Write-Host ""
        Write-Line "  ┌─ 🖼️ 原生多模态视觉大脑已就绪 ──────────────────────────────────────────" $C_TAG
        Write-Line "  │  视觉投影: mmproj-F16 原生挂载 (支持直接传入高分辨率图像与 MTP 投机加速)" $C_MODEL
        Write-Line "  └────────────────────────────────────────────────────────────────────────" $C_TAG
        Write-Host ""
    }

    # 确保 8081 智能协同网关常驻运行并提示看板地址
    Ensure-GatewayRunning -VisionPort $visionMainPort
    Write-Color "   [智能网关] " $C_SECSUB
    Write-Line "http://127.0.0.1:8081/dashboard (实时用量 · 虚拟计费 · 算力看板)" $C_TAG

    $enc = [System.Text.UTF8Encoding]::new($false)

    # 系统资源监控（内联，每10秒采样，用 Stopwatch 计时几乎无阻塞）
    Write-Host "   资源监控已启动 → 写入每日日志（每10秒采样）"
    $monitorStopwatch = [System.Diagnostics.Stopwatch]::StartNew()

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    # 注入原生日志落盘参数
    $ServerArgs = $ServerArgs + @("--api-key", $ApiKey)  # 共享：前端/agent 接入须带 key=$ApiKey；推理(reasoning)改为按模型按需注入(35B/27B 开，VL/MTP 关)。原始服务器输出经 2>&1 管道并入每日日志（不再单独 --log-file）

    # 性能基线（按槽位分开：多槽并发时各槽交错输出，混算会误报）
    $prevTg = @{}                    # 每槽上一次 tg
    $prevNDec = @{}                  # 每槽上一次 n_decoded
    $tgCount = 0
    $expectedTg = @{}                # 每槽首次记录作为基线
    $tgHistory = New-Object System.Collections.ArrayList   # 汇总滑动窗口（最近 10 个采样，用于 SUMMARY）
    $slotTgHistory = @{}             # 每槽短历史（最近 5 个，用于同槽 JITTER 检测）
    # $lastTgLogTime = [DateTime]::MinValue  # dead code
    $sessionStartTime = Get-Date
    $script:g_SessionErrors = 0
    $script:g_PeakGpuMem    = 0
    # 上下文利用率追踪：-c 为总预算，b10679 自动分给 nSlots 个槽，按"单槽容量"统计
    $ctxSize = 16384
    $nSlots = 1
    for ($i = 0; $i -lt $ServerArgs.Count - 1; $i++) {
        if ($ServerArgs[$i] -eq "-c") { $ctxSize = [int]$ServerArgs[$i + 1] }
        elseif ($ServerArgs[$i] -eq "--parallel") { $nSlots = [int]$ServerArgs[$i + 1] }
    }
    $slotCtxSize = if ($nSlots -gt 0) { [math]::Floor($ctxSize / $nSlots) } else { $ctxSize }
    if ($slotCtxSize -le 0) { $slotCtxSize = $ctxSize }
    # 上下文占用按"单槽单请求"统计：prompt eval 刷新该槽请求起点，tg 行取该槽 generation 解码数，
    # 峰值用于 SUMMARY，避免旧统计跨请求累计导致 ctx_usage 虚高（曾出现 887%）。
    $script:g_ReqPromptTokens = @{}  # 每槽当前请求的 prompt token 数（键=槽位）
    $script:g_PeakCtxUsed     = @{}  # 每槽单请求上下文占用峰值（prompt + 解码）

    try {
        # 2>&1 合并；逐行处理：控制台照显 + 解析推理事件 + 写入每日日志
        & $LLAMA_SERVER @ServerArgs 2>&1 | ForEach-Object {
            $raw = $_.ToString()
            $line = $raw.Replace("`r", "")
            $line  # 控制台输出
            # 单文件策略：每条服务器原始输出一并入每日日志（"SRV |" 前缀），保证 1 个文件含全部内容
            Write-DailyLog "SRV | ${line}"

            # ---- 系统资源监控（每10秒 Stopwatch 采样，<100ms 几乎无感）----
            if ($monitorStopwatch.Elapsed.TotalSeconds -ge 10) {
                Write-MonitorSample -LogFile $g_DailyLogFile -ModelName $modelName
                $monitorStopwatch.Restart()
            }

            # ---- 解析推理事件 ----
            # token 生成速度 (tg)
            if ($line -match 'tg\s*=\s*([\d.]+)\s*t/s') {
                $tg = [double]$Matches[1]
                if ($line -match 'n_decoded\s*=\s*(\d+)') { $nDec = [int]$Matches[1] } else { $nDec = 0 }
                # 槽位识别：多槽并发时各槽 tg 交错出现，检测必须按槽独立（避免混算误报）
                $slotKey = "0"
                if ($line -match '\bid\s+(\d+)') { $slotKey = $Matches[1] }

                # 每槽首次记录作为该槽基线
                if (-not $expectedTg.ContainsKey($slotKey)) { $expectedTg[$slotKey] = $tg }

                # 汇总滑动窗口（最近 10 个采样，用于 SUMMARY）
                [void]$tgHistory.Add($tg)
                if ($tgHistory.Count -gt 10) { $tgHistory.RemoveAt(0) }

                # 每槽短历史（最近 5 个，用于同槽 JITTER 检测）
                if (-not $slotTgHistory.ContainsKey($slotKey)) { $slotTgHistory[$slotKey] = New-Object System.Collections.ArrayList }
                $slotHist = $slotTgHistory[$slotKey]
                [void]$slotHist.Add($tg)
                if ($slotHist.Count -gt 5) { $slotHist.RemoveAt(0) }

                # ---- 多级瓶颈检测（同槽比较）----
                $bottleneck = ""

                # 1) 速度跌至该槽基线一半以下（持续性能退化）
                if ($expectedTg[$slotKey] -gt 0 -and $tg -lt ($expectedTg[$slotKey] * 0.5)) {
                    $bottleneck = " [BOTTLENECK] slot${slotKey} tg=${tg} t/s (<50% of baseline $($expectedTg[$slotKey]) t/s)"
                }
                # 2) 高延迟标记：tg < 5 t/s
                if ($tg -lt 5.0) {
                    $bottleneck = " [HIGH_LATENCY] slot${slotKey} generation ${tg} t/s < 5 t/s threshold"
                }
                # 3) 波动检测：与该槽自身短历史均值比较（双槽时整体窗口混算会误报）
                if ($slotHist.Count -ge 3) {
                    $slotAvg = ($slotHist | Measure-Object -Average).Average
                    if ($slotAvg -gt 0) {
                        $devPct = [math]::Abs(($tg - $slotAvg) / $slotAvg) * 100.0
                        if ($devPct -gt 30.0 -and -not $bottleneck) {
                            $bottleneck = " [JITTER] slot${slotKey} tg=${tg} t/s deviates ${devPct:F1}% from its recent avg ${slotAvg:F1} t/s"
                        }
                    }
                }
                # 4) 内存压力关联：该槽 n_decoded 突降（cache 驱逐）
                $currentNDec = $nDec
                if ($prevTg.ContainsKey($slotKey) -and $prevTg[$slotKey] -gt 0 -and $prevNDec.ContainsKey($slotKey) -and $currentNDec -lt ($prevNDec[$slotKey] * 0.3) -and $currentNDec -gt 0) {
                    $bottleneck = " [CACHE_EVICT] slot${slotKey} n_decoded=${currentNDec} dropped >70% from previous $($prevNDec[$slotKey]) — likely KV cache eviction"
                }
                $prevNDec[$slotKey] = $currentNDec

                Write-DailyLog "INFERENCE model=${modelName} | slot=${slotKey} | tg=${tg} t/s | latency=$([math]::Round(1000/$tg, 1))ms | n_decoded=${nDec}${bottleneck}"
                $prevTg[$slotKey] = $tg
                $tgCount++
                # 上下文占用：该槽请求 prompt + 已解码（n_decoded 为该槽 generation 累计值）
                if (-not $script:g_ReqPromptTokens.ContainsKey($slotKey)) { $script:g_ReqPromptTokens[$slotKey] = 0 }
                $ctxUsed = $script:g_ReqPromptTokens[$slotKey] + $nDec
                if (-not $script:g_PeakCtxUsed.ContainsKey($slotKey) -or $ctxUsed -gt $script:g_PeakCtxUsed[$slotKey]) { $script:g_PeakCtxUsed[$slotKey] = $ctxUsed }
            }

            # prompt 处理速度
            if ($line -match 'prompt eval time.*?([\d.]+)\s*tokens per second') {
                $pp = [double]$Matches[1]
                $bottleneck_pp = ""
                if ($pp -lt 20.0) { $bottleneck_pp = " [BOTTLENECK] slow prompt eval ${pp} t/s (< 20 t/s)" }
                Write-DailyLog "INFERENCE model=${modelName} | prompt_eval=${pp} t/s${bottleneck_pp}"
            }
            if ($line -match 'prompt processing.*?([\d.]+)\s*tokens per second') {
                $pp = [double]$Matches[1]
                Write-DailyLog "INFERENCE model=${modelName} | prompt_process=${pp} t/s"
            }

            # prompt eval time (ms) and tokens
            if ($line -match 'prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens') {
                $ppMs = [double]$Matches[1]
                $ppTokens = [int]$Matches[2]
                $slotKey = "0"
                if ($line -match '\bid\s+(\d+)') { $slotKey = $Matches[1] }
                $script:g_ReqPromptTokens[$slotKey] = $ppTokens   # 该槽新请求开始，刷新请求起点
                if (-not $script:g_PeakCtxUsed.ContainsKey($slotKey) -or $ppTokens -gt $script:g_PeakCtxUsed[$slotKey]) { $script:g_PeakCtxUsed[$slotKey] = $ppTokens }
                # 单槽容量口径：-c 为总预算，实际单槽 = ctxSize/nSlots，req_ctx 按单槽统计
                $ctxUtil = [math]::Round($ppTokens / $slotCtxSize * 100, 1)
                Write-DailyLog "INFERENCE model=${modelName} | slot=${slotKey} | prompt_eval_duration=${ppMs}ms | prompt_tokens=${ppTokens} | req_ctx=${ctxUtil}%($ppTokens/$slotCtxSize)"
            }

            # 模型加载完成
            if ($line -match 'model loaded') {
                Write-DailyLog "EVENT model=${modelName} | model_loaded"
            }
            # 模型加载耗时
            if ($line -match 'load time\s*=\s*([\d.]+)\s*ms') {
                $loadMs = [double]$Matches[1]
                Write-DailyLog "EVENT model=${modelName} | load_time=${loadMs}ms"
            }
            # 服务监听
            if ($line -match 'listening on') {
                $elapsed = (Get-Date) - $sessionStartTime
                Write-DailyLog "EVENT model=${modelName} | server_listening | startup_elapsed=$([math]::Round($elapsed.TotalSeconds, 1))s"
            }
            # 错误/异常分类
            if ($line -match '(error|exception|failed|assert|GGML_ASSERT)') {
                $errType = "UNKNOWN"
                if ($line -match 'failed to fit params') { $errType = "OOM" }
                elseif ($line -match 'CUDA|cuBLAS|cuda') { $errType = "CUDA" }
                elseif ($line -match 'GGML_ASSERT') { $errType = "ASSERT" }
                elseif ($line -match 'image input is not supported') { $errType = "MULTIMODAL" }
                elseif ($line -match 'got exception|parse_error|ill-formed UTF-8|json\.exception') { $errType = "CLIENT" }
                elseif ($line -match 'RemoteException|NativeCommandError') { $errType = "POWERSHELL" }

                # BUG#4 修复：PowerShell 把 llama-server 的良性 stderr 包成 RemoteException/NativeCommandError，
                # 但模型随后 model_loaded + server_listening 正常。此类属噪音，降级为 INFO 以免污染 ERROR 计数、
                # 误导判断。CLIENT（got exception / parse_error / ill-formed UTF-8）是请求方编码或格式问题
                # （如 GBK 中文、畸形 JSON），同样属于非服务端故障，不计入错误数。
                # 真实致命错误（含 "error:"/"failed" 等关键字的 stderr）以 WARN 记录在本每日日志中，
                # 完整原始输出见每条 "SRV |" 行。
                if ($errType -eq "POWERSHELL" -or $errType -eq "CLIENT") {
                    Write-DailyLog "INFO [${errType}] model=${modelName} | 非服务端故障（请求方问题/噪音），不计入错误数: ${line}"
                } else {
                    $script:g_SessionErrors++
                    Write-DailyLog "ERROR [${errType}] model=${modelName} | ${line}"
                }
            }
        }
    } finally {
        # 终止 8085 Qwen2.5-VL-3B 侧挂进程（释放 CPU 资源）
        if ($script:g_Sidecar8085Process -and -not $script:g_Sidecar8085Process.HasExited) {
            Stop-Process -Id $script:g_Sidecar8085Process.Id -Force -ErrorAction SilentlyContinue
        }
        # 安全清理所有 llama-server 进程以释放 GPU 显存
        try {
            Get-Process | Where-Object { $_.ProcessName -match "llama" } | Stop-Process -Force -ErrorAction SilentlyContinue
        } catch { }
        # 保持 8081 智能协同网关常驻运行，供随时访问 /dashboard 监控看板与虚拟计费
        # 结束前最后一次采样
        Write-MonitorSample -LogFile $g_DailyLogFile -ModelName $modelName
        # 会话结束汇总
        $sessionElapsed = (Get-Date) - $sessionStartTime
        Write-DailyLog "EVENT model=${modelName} | session_ended | elapsed=$([math]::Round($sessionElapsed.TotalMinutes, 1))min"
        # 生成汇总统计
        $sessionMinutes = [math]::Round($sessionElapsed.TotalMinutes, 1)
        $summaryBase = "SUMMARY model=${modelName} | duration=${sessionMinutes}min | errors=$($script:g_SessionErrors) | peak_gpu=$($script:g_PeakGpuMem)MB | inferences=$($tgCount)"
        if ($tgHistory.Count -gt 0) {
            $avgTgTotal = ($tgHistory | Measure-Object -Average).Average
            $minTg = ($tgHistory | Measure-Object -Minimum).Minimum
            $maxTg = ($tgHistory | Measure-Object -Maximum).Maximum
            $avgLatency = [math]::Round(1000 / $avgTgTotal, 1)
            # 各槽峰值取最大（单槽兼容原逻辑），分母用单槽容量
            $peakCtxUsed = 0
            foreach ($v in $script:g_PeakCtxUsed.Values) { if ($v -gt $peakCtxUsed) { $peakCtxUsed = $v } }
            $ctxUtilPct = if ($slotCtxSize -gt 0) { [math]::Round($peakCtxUsed / $slotCtxSize * 100, 1) } else { 0 }
            Write-DailyLog "${summaryBase} | win_avg_tg=$([math]::Round($avgTgTotal, 1)) t/s | avg_latency=${avgLatency}ms | min_tg=$([math]::Round($minTg, 1)) | max_tg=$([math]::Round($maxTg, 1)) | peak_ctx=${ctxUtilPct}%($peakCtxUsed/$slotCtxSize) | samples=$($tgHistory.Count)"
        } else {
            Write-DailyLog "${summaryBase} | (无推理采样)"
        }
        Write-DailyLog "--- SESSION END [${modelName}] ---"
        Write-DailyLog ""
        $ErrorActionPreference = $prevEAP
    }
}


# ============================================================
#  网页服务快捷模式
# ============================================================

function Main-ServerMode {
    $gpu = Get-GPUInfo
    $totalVRAM = 0
    $gpuLabel = "未检测到 NVIDIA GPU，默认 CPU 运行"
    if ($gpu) {
        $totalVRAM = $gpu.VRAM
        $vramGB = [math]::Round($gpu.VRAM / 1024.0, 1)
        $physicalCores = Get-PhysicalCores
        $gpuLabel = "$($gpu.Name) | ${vramGB} GB | CPU: ${physicalCores}核"
    }

    $models = Sort-ModelsByRank (Get-Models)

    # 启动时写入每日日志头（系统信息、依赖版本等）
    Write-DailyLogHeader

    # 🌟 启动器初始化：只要打开启动器，立刻自动在后台拉起 8081 智能网关
    Ensure-GatewayRunning

    if ($models.Count -eq 0) {
        Write-Line "  错误: 在 E:\models 中未找到任何 GGUF 模型文件。" $C_ERROR
        Safe-ReadHost
        return
    }

    Show-Banner
    # 统一渲染：网关大屏接入 + 标题 + 列标题 + 模型列表（启动器唯一 UI）
    Show-ModelGrid -Models $models -Title "网页服务模式"

    if ($ModelIndex -gt 0) {
        # 非交互：跳过菜单，直接选用指定序号模型
        if ($ModelIndex -lt 1 -or $ModelIndex -gt $models.Count) {
            Write-Line "  -ModelIndex 超出范围，有效范围 1-$($models.Count)" $C_ERROR
            return
        }
        $num = $ModelIndex
        Write-Line "  自动选择模型 [$num]: $($models[$num-1].DisplayName)" $C_TAG
    } else {
        Write-Host ""
        # 能力字图例（每个字用对应配色，让用户对照"特长"列识别模型能力）
        Write-Color "  能力字: " $C_HINT
        $legend = @(
            @("图", "多模态"), @("狱", "越狱"),   @("调", "工具"),  @("思", "思考"),
            @("码", "编码"),   @("译", "翻译"),   @("通", "通用能力"),
            @("双", "双并发"), @("四", "4并发")
        )
        for ($i = 0; $i -lt $legend.Count; $i++) {
            $ch   = $legend[$i][0]
            $mean = $legend[$i][1]
            $col  = if ($ABILITY_COLORS.ContainsKey($ch)) { $ABILITY_COLORS[$ch] } else { $C_HINT }
            Write-Color $ch $col
            Write-Color "=$mean" $C_HINT
            if ($i -lt $legend.Count - 1) { Write-Color "  " $C_HINT }
        }
        Write-Host ""
        Write-Host ""
        Write-Color "  请选择模型 [1-$($models.Count)]: " $C_INPUT
        $modelChoice = Safe-ReadHost
        $num = 0
        if (-not [int]::TryParse($modelChoice, [ref]$num) -or $num -lt 1 -or $num -gt $models.Count) {
            Write-Line "  无效选择，已退出。" $C_WARN
            Safe-ReadHost
            return
        }
    }

    $selectedModel = $models[$num - 1]

    # 视觉模型：llama-server 加 --mmproj 即可支持
    # ASR 模型：需命令行调用
    if ($selectedModel.Category -eq "asr") {
        Write-Host ""
        Write-Line "  ASR 模型暂不支持网页服务模式" $C_WARN
        Write-Color "  " $C_HINT
        Write-Color $selectedModel.DisplayName $C_MODEL
        Write-Line " 是语音模型，请使用命令行方式调用。" $C_WARN
        Safe-ReadHost "  按回车退出"
        return
    }

    # 自定义参数模型（如 35B MoE）：完全使用模型内固定参数，不经自动/手动调参
    if ($selectedModel.UseCustom) {
        $srvArgs = @("-m", $selectedModel.Path) + $selectedModel.CustomArgs + @("--port", 8081, "--host", "127.0.0.1")
        if ($selectedModel.MMProj) { $srvArgs += "--mmproj", $selectedModel.MMProj }

        Write-Host ""
        Write-Host ""
        Write-Color "  模型: " $C_HINT
        Write-Color $selectedModel.Tag $C_TAG
        Write-Color " " $C_HINT
        Write-Line $selectedModel.DisplayName $C_MODEL

        # 模式详细说明与场景指引（多处显著标注）
        if ($selectedModel.Tag -match "双槽MTP|双并发.*MTP|MTP.*144K") {
            Write-Color "  ▸ 模式特性: " $C_SECSUB
            Write-Line "【主力推荐】纯文本 2 槽双并发 + 原生 MTP 投机加速 (生成 36.7 tok/s) | 144K 统一池 (单槽72K) | 8085 CPU 视觉眼睛" $C_TAG
        } elseif ($selectedModel.Tag -match "4并发|4槽") {
            Write-Color "  ▸ 模式特性: " $C_SECSUB
            Write-Line "【高吞吐推荐】纯文本 4 槽高并发 | 160K 统一共享池 (关 MTP 保证 4 槽显存裕量) | 8085 CPU 视觉眼睛" $C_TAG
        } elseif ($selectedModel.Tag -match "多模态") {
            Write-Color "  ▸ 模式特性: " $C_SECSUB
            Write-Line "【原生多模态】直接挂载 mmproj-F16 视觉大脑 + 原生 MTP 投机加速 | 144K 单槽上下文 | 支持直接传入高清图像" $C_TAG
        }

        Write-Color "  自定义启动参数: " $C_HINT
        Write-Line ($selectedModel.CustomArgs -join " ") $C_TAG
        Show-FrontendConnect 8081
        Write-Color "  按 " $C_HINT
        Write-Color "Ctrl+C" $C_WARN
        Write-Line " 停止服务" $C_HINT
        Write-Host ""
        Write-Host ""

        # PaddleOCR-VL-1.6：打印官方指令提示表（用户记不住指令）
        if ($selectedModel.Name -match "paddleocr-vl") {
            Write-Line "  ┌─ PaddleOCR-VL-1.6 指令表（发图 + 指令文本）────────────────────" $C_TAG
            Write-Line "  │  识别文字    →  OCR:" $C_MODEL
            Write-Line "  │  提取表格    →  Table Recognition:" $C_MODEL
            Write-Line "  │  提取公式    →  Formula Recognition:" $C_MODEL
            Write-Line "  │  提取图表    →  Chart Recognition:" $C_MODEL
            Write-Line "  │  提取印章    →  Seal Recognition:" $C_MODEL
            Write-Line "  │  文字定位    →  Spotting:" $C_MODEL
            Write-Line "  └────────────────────────────────────────────────────────────" $C_TAG
            Write-Host ""
        }

        Start-LlamaServer @srvArgs
        return
    }

    $auto = Get-AutoParams -Model $selectedModel -GPU $gpu

    # 服务模式需要更大上下文（WebUI系统提示词就很长）
    # V100 32GB：4B 以下 32K，其余 16K
    if ($selectedModel.SizeGB -le 4.0) {
        $auto.CtxSize = 32768
    } else {
        $auto.CtxSize = 16384
    }

    # 直接显示自动参数并启动，不需要确认
    Write-Host ""
    Write-Host ""
    Write-Color "  模型: " $C_HINT
    Write-Color $selectedModel.Tag $C_TAG
    Write-Color " " $C_HINT
    Write-Line $selectedModel.DisplayName $C_MODEL
    Write-Color "  GPU 层: " $C_HINT
    Write-Color "$($auto.NGL)  " $C_MODEL
    Write-Color "上下文: " $C_HINT
    Write-Color "$($auto.CtxSize)  " $C_MODEL
    Write-Color "温度: " $C_HINT
        Write-Line "$($auto.Temp)" $C_MODEL
        Show-FrontendConnect 8081
        Write-Color "  按 " $C_HINT
    Write-Color "Ctrl+C" $C_WARN
    Write-Line " 停止服务" $C_HINT
    Write-Host ""
    Write-Host ""

    # 构建 server 参数直接启动
    $srvArgs = @(
        "-m", $selectedModel.Path,
        "-ngl", $auto.NGL,
        "-c", $auto.CtxSize,
        "-t", $auto.Threads,
        "-b", $auto.BatchSize,
        "--port", 8081,
        "--host", "127.0.0.1"
    )
    if ($auto.FlashAttn) { $srvArgs += "--flash-attn", "on" }
    if ($selectedModel.MMProj) { $srvArgs += "--mmproj", $selectedModel.MMProj }

    Start-LlamaServer @srvArgs
}

# ============================================================
# ============================================================
#  主循环
# ============================================================

# ---------- 入口 ----------
try {
    if ($ListModels) {
        try {
            # 非交互模式：输出模型清单（ASCII JSON 数组），供评测编排脚本解析后逐个启动
            $ml = Sort-ModelsByRank (Get-Models)
            $i = 0
            $arr = @()
            foreach ($m in $ml) {
                $i++
                $arr += [PSCustomObject]@{
                    index    = $i
                    name     = $m.Name
                    tag      = $m.Tag
                    sizeGB   = $m.SizeGB
                    category = $m.Category
                    useCustom = [bool]$m.UseCustom
                }
            }
            Write-Output (ConvertTo-Json $arr -Compress)
        } catch {
            Write-Output ("[LISTMODELS_ERROR] " + $_.Exception.Message + " @ " + $_.InvocationInfo.ScriptName + ":" + $_.InvocationInfo.ScriptLineNumber)
        }
        return
    }
    Main-ServerMode
} catch {
    Write-Host ""
    Write-Line "  发生错误: $_" $C_ERROR
    Write-Host $_.ScriptStackTrace
    Write-Host ""
    Safe-ReadHost "  按回车退出"
}
