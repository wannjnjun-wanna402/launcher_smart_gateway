# ====================================================================================
#  🤖 AI 智能任务自适应网关启动器 v5.0 (Unified 27B Flagship Smart Gateway)
#  专为 Tesla V100 32GB 打造：纯 Qwen3.8-27B 旗舰统一矩阵 · 4.5秒无感热切换 · 0秒动态思考调控
#  Date: 2026-09-02
# ====================================================================================

# ====================================================================================
#  ⚠️ 核心调优、生产实测经验与底层物理规律总结 —— 维护与调整必读
# ====================================================================================
#
# 【一、纯 Qwen3.8-27B-Abliterated 旗舰统一矩阵核心哲学（2026-09-02 架构终极进化）】
#  1. 智力绝对信任：全线采用 52 分开源榜首、100/100 工具遵循满分的 Qwen3.8-27B-Abliterated-Q6_K。
#  2. 3大场景专属形态 4.5 秒无感热切换：
#     - 形态 1【双槽MTP 常驻极速态】：日常默认常驻，36.7 tok/s 原生投机喷涌，极低延迟；
#     - 形态 2【4并发流水线态】：多 Agent/批量并发时 4.5 秒切入，4 槽零排队交替吞吐 (45+ tok/s)；
#     - 形态 3【原生多模态视觉态】：发图时 4.5 秒挂载 mmproj-27B-F16，原生看图+原生顶尖代码一步到位！
#  3. 0秒动态思考等级调控 (0-second Dynamic Reasoning Level Modulation)：
#     - 简单任务（问答/翻译/搜索/正则）➔ 0秒注入 low/none 思考（预算 512），MTP 瞬间秒出；
#     - 默认中等（日常写代码/Bug排查/SQL）➔ 0秒注入 medium 思考（预算 2048）；
#     - 困难任务（Minecraft/完整系统/大型重构）➔ 0秒注入 xhigh 深度思考（预算 8192），榨干 27B 智力！
#
# 【二、模板与工具调用生死线（2026-08-31 深度专项长测铁律）】
#  - 生产部署必须死锁 chat_template_qwen_fixed.jinja (froggeric v22.4.0 旗舰版)；
#  - 0 次死循环、0 次 XML 标签泄漏，工具通过率 100%。
#
# 【三、DRY 采样器与防复读参数黄金基线】
#  - DRY 采样器定死关闭（--dry-multiplier 0.0），避免 Windows 路径 2-gram 变异；
#  - repeat-penalty 1.05 轻量乘性兜底。
#
# 【四、统一上下文池与显存安全红线】
#  - 27B 主模型 (20.89G) + mmproj 视觉头 (0.91G) = 21.80 GB，V100 32GB 剩余 10.2 GB 纯净显存；
#  - 双槽/4槽分配 144K (147,456) / 多模态分配 128K (131,072)，留有 5.4GB+ 安全裕量，切换 0% OOM 风险。
#
# 【五、服务日志命名与单日累加标准（严格遵守 AGENTS.md）】
#  - 命名规范：[端口号]_[功能名]_[YYYYMMDD].log，单日单文件追加模式，90天自动循环归档。
# ====================================================================================

[CmdletBinding()]
param(
    [string]$DailyLogFile = "",
    [string]$AutoStartMode = ""
)

# 强制控制台编码为 UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$RootDir = "E:\llama-win-cuda-12.4-x64"
$ModelsDir = "E:\models"
$TemplateFile = Join-Path $RootDir "chat_template_qwen_fixed.jinja"
$ServerExe = Join-Path $RootDir "llama-server.exe"

$today = (Get-Date).ToString("yyyyMMdd")
$LogDir = Join-Path $RootDir "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
if (-not $DailyLogFile) { $DailyLogFile = Join-Path $LogDir "8083_llama_$today.log" }
$ProxyLogFile = Join-Path $LogDir "8081_proxy_$today.log"

function Write-C([string]$text, [string]$fg = "White", [bool]$nl = $true) {
    if ($nl) {
        Write-Host $text -ForegroundColor $fg
    } else {
        Write-Host $text -ForegroundColor $fg -NoNewline
    }
}

# ------------------------------------------------------------------------------------
# 0. 历史日志生命周期自动管理（自动清理超过 90 天的历史日志，循环保持磁盘干爽）
# ------------------------------------------------------------------------------------
function Clean-ExpiredLogs([int]$days = 90) {
    if (Test-Path $LogDir) {
        $threshold = (Get-Date).AddDays(-$days)
        $oldLogs = Get-ChildItem -Path $LogDir -Filter "*.log" -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $threshold }
        $cleaned = 0
        foreach ($f in $oldLogs) {
            try {
                Remove-Item -Path $f.FullName -Force -ErrorAction SilentlyContinue
                $cleaned += 1
            } catch {}
        }
        if ($cleaned -gt 0) {
            Write-C "  🧹 自动归档清理 $cleaned 个超过 90 天的历史旧日志" "DarkGray"
        }
    }
}

# ------------------------------------------------------------------------------------
# 1. 显存与进程绝对安全清理（严格遵守 AGENTS.md 标准）
# ------------------------------------------------------------------------------------
function Stop-LlamaProcesses {
    Write-C "  🧹 正在执行显存与旧进程安全回收..." "DarkYellow"
    Get-Process | Where-Object { $_.ProcessName -match 'llama' } | Stop-Process -Force -ErrorAction SilentlyContinue
    
    # 释放 8083 端口占用
    try {
        $conns = Get-NetTCPConnection -LocalPort 8083 -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    } catch {}

    # 循环检查 GPU 显存，低于 600MB 才算彻底干净
    $waited = 0
    while ($waited -lt 15) {
        try {
            $smi = & nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null
            if ($smi -and [int]$smi[0] -lt 600) {
                Write-C "  ✅ GPU 显存已完全释放 (当前占用: $($smi[0]) MB)" "Green"
                break
            }
        } catch {}
        Start-Sleep -Milliseconds 800
        $waited += 1
    }
}

# ------------------------------------------------------------------------------------
# 2. 保证 8081 智能自适应网关常驻运行
# ------------------------------------------------------------------------------------
function Ensure-GatewayAndSidecar {
    Clean-ExpiredLogs 90

    # 检查 8081 网关
    $gConn = Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue
    if (-not $gConn) {
        Write-C "  🚀 正在拉起 8081 智能自适应调度网关..." "Cyan"
        Start-Process -FilePath "C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" `
            -ArgumentList "$RootDir\qwen_tool_proxy.py --listen 8081 --target 8083 --api-key llamacpp" `
            -WorkingDirectory $RootDir -WindowStyle Hidden
        Start-Sleep -Seconds 1
    }
}

# ------------------------------------------------------------------------------------
# 3. 纯 Qwen3.8-27B 旗舰 3 大场景形态配置定义
# ------------------------------------------------------------------------------------
$ModelProfiles = @{
    "1" = @{
        Name        = "Qwen3.8-27B-A [双槽MTP]"
        GGUF        = "Qwen3.8-27B-Abliterated-Q6_K.gguf"
        Alias       = "Qwen3.8-27B-A-Q6_K"
        Ctx         = 147456      # 144K 统一动态共享池
        Parallel    = 2
        MTP         = $true       # 原生 MTP 双草稿投机加速
        MMProj      = ""
        Speed       = "36.7 tok/s (投机加速)"
        AAIndex     = "52 分 (开源TOP 1)"
        Desc        = "【默认基准常驻态】单兵极速 · 原生 MTP 加速 · 动态注入 low/medium/xhigh 思考"
        TypeTag     = "👑 极速基准态"
        VRAM        = "27.4 GB"
    }
    "2" = @{
        Name        = "Qwen3.8-27B-A [4并发流水线]"
        GGUF        = "Qwen3.8-27B-Abliterated-Q6_K.gguf"
        Alias       = "Qwen3.8-27B-A-Q6_K"
        Ctx         = 147456      # 144K 统一动态共享池
        Parallel    = 4
        MTP         = $false
        MMProj      = ""
        Speed       = "23.2 tok/s (总吞吐 45+ tok/s)"
        AAIndex     = "52 分 (开源TOP 1)"
        Desc        = "【高负载流水线态】4 槽并发 · 零排队交替输入 · 多 Agent 批量协作王者"
        TypeTag     = "🚀 并发流水线态"
        VRAM        = "28.5 GB"
    }
    "3" = @{
        Name        = "Qwen3.8-27B-A [原生多模态视觉]"
        GGUF        = "Qwen3.8-27B-Abliterated-Q6_K.gguf"
        Alias       = "Qwen3.8-27B-A-Q6_K"
        Ctx         = 131072      # 128K 黄金多模态池
        Parallel    = 2
        MTP         = $false
        MMProj      = "mmproj-Qwen3.8-27B-F16.gguf"
        Speed       = "31.5 tok/s"
        AAIndex     = "52 分 (全模态旗舰)"
        Desc        = "【原生多模态视觉态】挂载 mmproj-27B · 27B 原生看图 + 27B 顶尖写代码"
        TypeTag     = "👁️ 原生视觉态"
        VRAM        = "27.4 GB"
    }
}

# ------------------------------------------------------------------------------------
# 4. 启动指定形态引擎
# ------------------------------------------------------------------------------------
function Start-SelectedProfile([string]$key) {
    $p = $ModelProfiles[$key]
    if (-not $p) { return }

    Stop-LlamaProcesses
    Ensure-GatewayAndSidecar

    $modelPath = Join-Path $ModelsDir $p.GGUF
    if (-not (Test-Path $modelPath)) {
        Write-C "  ❌ 错误: 未找到模型文件 $modelPath" "Red"
        return
    }

    Write-C ""
    Write-C "====================================================================================" "Cyan"
    Write-C "  🚀 正在加载 27B 形态: $($p.Name)  [$($p.TypeTag)]" "Green"
    Write-C "====================================================================================" "Cyan"
    Write-C "  ├─ 🎯 智能指数 : $($p.AAIndex)" "White"
    Write-C "  ├─ ⚡ 运行速度 : $($p.Speed)" "Yellow"
    Write-C "  ├─ 📚 上下文池 : $($p.Ctx / 1024)K (统一 KV 动态共享池)" "White"
    Write-C "  ├─ 🚦 槽位并发 : $($p.Parallel) 并发槽位 $(if ($p.MTP) { '(🔥 挂载原生 MTP 双草稿投机)' } else { '(无 MTP)' })" "White"
    Write-C "  ├─ 💾 显存预算 : $($p.VRAM) (预留 5.4GB+ 安全裕量)" "White"
    Write-C "  ├─ 🌐 统一接口 : http://127.0.0.1:8081/v1 (已接管 8083 主脑)" "Cyan"
    Write-C "  └─ 📊 算力大屏 : http://127.0.0.1:8081/dashboard" "Cyan"
    Write-C "====================================================================================" "Cyan"
    Write-C "  ⏳ 正在向 Tesla V100 注入显存，预计耗时约 4~5 秒..." "DarkGray"
    Write-C ""

    $argList = @(
        "-m", $modelPath,
        "-ngl", "99",
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        "-c", "$($p.Ctx)",
        "-b", "2048",
        "--ubatch-size", "2048",
        "-t", "6",
        "--parallel", "$($p.Parallel)",
        "--kv-unified",
        "--flash-attn", "on",
        "--ctx-checkpoints", "4",
        "--reasoning", "auto",
        "--reasoning-budget", "2048",
        "--reasoning-effort", "medium",
        "--reasoning-format", "deepseek",
        "--reasoning-preserve",
        "--no-warmup",
        "--temp", "0.3",
        "--top-p", "0.95",
        "--top-k", "20",
        "--min-p", "0.05",
        "--dry-multiplier", "0.0",
        "--repeat-penalty", "1.05",
        "--presence-penalty", "0.0",
        "--jinja",
        "--chat-template-file", $TemplateFile,
        "--alias", "$($p.Alias)",
        "--port", "8083",
        "--host", "127.0.0.1",
        "--log-file", $DailyLogFile
    )

    if ($p.MTP) {
        $argList += @("--spec-type", "draft-mtp", "--spec-draft-n-max", "2", "--spec-draft-n-min", "1", "--cache-reuse", "512")
    }

    if ($p.MMProj) {
        $projPath = Join-Path $ModelsDir $p.MMProj
        if (Test-Path $projPath) {
            $argList += @("--mmproj", $projPath)
        }
    }

    $finalArgs = $argList -join " "
    
    $startLog = "`n[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff'))] --- SESSION START [$($p.Name)] ---`n" +
                "MODEL: $($p.Name)`n" +
                "ARGS: $finalArgs`n"
    [System.IO.File]::AppendAllText($DailyLogFile, $startLog, [System.Text.Encoding]::UTF8)

    & $ServerExe @argList
}

# ------------------------------------------------------------------------------------
# 5. 主菜单与自动热等待倒计时
# ------------------------------------------------------------------------------------
Ensure-GatewayAndSidecar

Write-C "====================================================================================" "Cyan"
Write-C "   🤖 AI 智能任务自适应网关  ·  Unified 27B Flagship Gateway v5.0" "Green"
Write-C "====================================================================================" "Cyan"
Write-C "   [网关统一入口] http://127.0.0.1:8081/v1 (全应用统一接入点)" "White"
Write-C "   [实时监控看板] http://127.0.0.1:8081/dashboard" "White"
Write-C "   [核心架构规范] 纯 27B 旗舰统一矩阵 · 4.5秒内存级热切 · 0秒动态思考等级调控" "DarkGray"
Write-C "====================================================================================" "Cyan"
Write-C "   请选择启动模式 (默认 5 秒后自动载入 【1】 Qwen3.8-27B-A [双槽MTP] 常驻基准态):" "Yellow"
Write-C ""

Write-C "   [1] 👑 Qwen3.8-27B-A [双槽MTP]     │ 36.7 t/s │ AA:52分 │ 日常单兵极速 / 默认常驻 (默认首选)" "Green"
Write-C "   [2] 🚀 Qwen3.8-27B-A [4并发流水线] │ 45.0 t/s │ AA:52分 │ 4槽交替流水线 / 多Agent批量协同" "Cyan"
Write-C "   [3] 👁️ Qwen3.8-27B-A [原生多模态]  │ 31.5 t/s │ AA:52分 │ 挂载 mmproj-27B / 原生视觉深度推理" "Yellow"
Write-C "   [4] 🛠️ 纯后台网关守护模式 (仅常驻 8081 网关，全权自适应无感热调度)" "DarkGray"
Write-C "   [Q] 退出启动器" "Red"
Write-C "------------------------------------------------------------------------------------" "Cyan"

$choice = ""
$timeout = 5
$canRead = $false
try {
    $canRead = -not [Console]::IsInputRedirected
} catch {
    $canRead = $false
}

if ($canRead) {
    for ($i = $timeout; $i -ge 1; $i--) {
        Write-Host "`r   ⏳ 默认启动 [1] 27B 双槽MTP 常驻基准态 倒计时: $i 秒 (按 1~4 键手动选定)... " -ForegroundColor Yellow -NoNewline
        try {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                $choice = $key.KeyChar.ToString()
                break
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    Write-Host ""
} else {
    Write-Host "   ⏳ 自动载入默认 [1] 27B 双槽MTP 常驻基准态..." -ForegroundColor Yellow
}

if (-not $choice -or $choice -eq "`r" -or $choice -eq "`n") { $choice = "1" }
$choice = $choice.ToUpper()

if ($choice -eq "Q") {
    Write-C "退出。" "DarkGray"
    exit
} elseif ($choice -in @("1", "2", "3")) {
    Start-SelectedProfile $choice
} elseif ($choice -eq "4") {
    Write-C "🟢 纯后台网关守护已就绪 (8081)，全权按需 4.5 秒自适应热调度..." "Green"
    while ($true) { Start-Sleep -Seconds 3600 }
} else {
    Write-C "无效选择，默认启动 [1] 27B 双槽MTP 常驻基准态..." "Yellow"
    Start-SelectedProfile "1"
}
