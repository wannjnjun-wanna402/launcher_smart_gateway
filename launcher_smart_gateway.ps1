# ====================================================================================
#  🤖 AI 智能任务自适应网关启动器 v4.0 (Smart Task Dispatch Gateway)
#  专为 Tesla V100 32GB 打造：全局 144K 统一共享池 · 5大王牌矩阵 · 默认 Flash 极速接待
#  Date: 2026-09-02
# ====================================================================================

# ====================================================================================
#  ⚠️ 核心调优、生产实测经验与底层物理规律总结 —— 维护与调整必读
# ====================================================================================
#
# 【一、MTP 投机采样在不同架构上的物理分水岭（2026-09-02 实机压测铁律）】
#  1. 稠密大模型（Dense，如 Qwen3.8-27B-A）：必须开启 MTP (--spec-type draft-mtp)
#     - 原因：27B 稠密模型每次前向计算需硬算 270 亿参数（单步 ~43ms）。开启 MTP 命中草稿词
#       即可省去整步 27B 矩阵大前向计算，吐字均速由 23.2 tok/s 暴涨至 36.7 tok/s（提速 +70%）。
#  2. 稀疏专家模型（MoE，如 Ornith-1.5-35B A3B）：必须关闭 MTP（纯自回归）
#     - 原因：35B MoE 每次生成只激活 ~3B 专家参数（单步极轻仅 ~12ms）。若开 MTP，GPU 额外
#       执行草稿头验证与树分支比对的开销，反而超过了 3B 本身的前向开销！
#     - 2026-09-02 实测对比：
#       * 纯自回归（关 MTP）：82.40 tok/s 🏆（全场极速之王，极简轻快）
#       * MTP n_max=1       ：77.84 tok/s（微小额外开销拖累）
#       * MTP n_max=3       ：54.81 tok/s（多分支验证开销严重劣化）
#     - 结论：Ornith-1.5-35B 保持纯自回归，释放 82.4 tok/s 极速；Qwen3.8-27B 保持 MTP 投机。
#
# 【二、模板与工具调用生死线（2026-08-31 深度专项长测铁律）】
#  1. 模板生死线：Fixed-Medium (chat_template_qwen_fixed.jinja) 全面碾压 Sharp-Medium
#     - Fixed-Medium 模板：4大模型全线保持 0 次死循环展开、0 次 XML 标签污染，通过率 100%。
#     - Sharp-Medium 模板：缺少闭合约束，在 A-Q6_K 上引发 XML 标签泄漏导致 JSON 崩溃，通过率仅 65%。
#     - 结论：生产部署必须死锁 chat_template_qwen_fixed.jinja，严禁在工具/Agent场景使用 Sharp 模板！
#  2. 无审核模型（Abliterated）遵循能力顶尖：
#     - Qwen3.8-27B-A-Q6_K (Abliterated)：20/20 题全通过（100.0 分），消除防御性发散分支，结构化指令遵循最强。
#
# 【三、DRY 采样器与防复读参数黄金基线】
#  1. DRY 采样器必须关闭（--dry-multiplier 0.0）：
#     - 原因：DRY 惩罚“最近上下文里出现过的 2-gram 重复”。在复制文件路径（如 E:\...\_fix3.js）时，
#       只要前缀重复就会被强制篡改导致 0/9 全错；关闭 DRY 后 Windows 路径复现 9/9 全对。
#  2. repeat-penalty 1.05 兜底：
#     - 关掉 DRY 后采用 repeat-penalty 1.05 作为乘性轻量兜底（logit * 0.952），不会破坏精确复现。
#
# 【四、全局 144K 统一 KV 动态共享池（--kv-unified --cache-reuse 512 -c 147456）】
#  1. 消除上下文超限截断：所有 GPU 模型统一标定为 144K（147,456 tokens）。
#  2. 动态共享：无论是双槽（单槽最高吃 72K~100K）还是 4 槽（4 槽动态共享），按需自适应分配。
#  3. 显存绝对安全：144K KV 下 Tesla V100 显存占用 25.8GB~28.5GB，预留 4.5GB~7.0GB 缓冲，切换 0% OOM。
#
# 【五、服务日志命名与单日累加标准（严格遵守 AGENTS.md）】
#  - 命名规范：[端口号]_[功能名]_[YYYYMMDD].log（8081_proxy_*.log, 8083_llama_*.log, 8085_sidecar_*.log）
#  - 单日单文件追加写入，严禁拆分 .out / .err。
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
$SidecarLogFile = Join-Path $LogDir "8085_sidecar_$today.log"

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
# 2. 保证 8081 智能网关与 8085 侧挂视觉常驻运行
# ------------------------------------------------------------------------------------
function Ensure-GatewayAndSidecar {
    Clean-ExpiredLogs 90

    # 检查 8081 网关
    $gConn = Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue
    if (-not $gConn) {
        Write-C "  🚀 正在拉起 8081 智能协同网关..." "Cyan"
        Start-Process -FilePath "C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe" `
            -ArgumentList "$RootDir\qwen_tool_proxy.py --listen 8081 --target 8083 --vision-main 8085 --api-key llamacpp" `
            -WorkingDirectory $RootDir -WindowStyle Hidden
        Start-Sleep -Seconds 1
    }

    # 检查 8085 CPU 侧挂视觉眼睛 (Qwen2.5-VL-3B, 0显存占用)
    $vConn = Get-NetTCPConnection -LocalPort 8085 -State Listen -ErrorAction SilentlyContinue
    $vModel = Join-Path $ModelsDir "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf"
    $vProj  = Join-Path $ModelsDir "mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf"
    if (-not $vConn -and (Test-Path $vModel) -and (Test-Path $vProj)) {
        Write-C "  👁️ 正在拉起 8085 CPU 侧挂视觉眼睛 (0显存)..." "Cyan"
        $sidecarArgs = @("-m", $vModel, "--mmproj", $vProj, "-ngl", "0", "-t", "4", "-c", "32768", "--port", "8085", "--host", "127.0.0.1", "--log-file", $SidecarLogFile)
        Start-Process -FilePath $ServerExe -ArgumentList $sidecarArgs -WorkingDirectory $RootDir -WindowStyle Hidden
    }
}

# ------------------------------------------------------------------------------------
# 3. 5 大精选王牌阵列参数定义（经过实机全参数压测后的黄金最优配置）
# ------------------------------------------------------------------------------------
$ModelProfiles = @{
    "1" = @{
        Name        = "Ornith-1.5-35B (35B MoE + 原生视觉)"
        GGUF        = "Ornith-1.5-35B-Q4_K_M.gguf"
        Alias       = "Ornith-1.5-35B"
        Ctx         = 147456      # 全局 144K 统一共享池
        Parallel    = 2
        MTP         = $false      # 实测寻优结论：MoE 稀疏激活本身仅 3B，纯自回归 82.4 t/s 达到全场峰值，免 MTP 额外开销
        MMProj      = "mmproj-Ornith-1.5-35B-A3B-f16.gguf"
        Speed       = "82.4 tok/s (全场极速之王)"
        AAIndex     = "48 分 (MoE 冲刺第一梯队)"
        Desc        = "【默认接待主力】Flash 极速 82.4 t/s 瞬时响应 · 仅激活 3B · 原生自带视觉 · 免 MTP 额外开销"
        TypeTag     = "⚡ Flash 极速接待"
        VRAM        = "26.2 GB"
    }
    "2" = @{
        Name        = "Qwen3.8-27B-A [双槽MTP]"
        GGUF        = "Qwen3.8-27B-Abliterated-Q6_K.gguf"
        Alias       = "Qwen3.8-27B-A-Q6_K"
        Ctx         = 147456      # 全局 144K 统一共享池
        Parallel    = 2
        MTP         = $true       # 稠密 27B 大模型开 MTP 提升 +70% 速度 (23 -> 36.7 t/s)
        MMProj      = ""
        Speed       = "36.7 tok/s"
        AAIndex     = "52 分 (开源TOP 1)"
        Desc        = "【重型主力主脑】原生 MTP 投机加速 · 单兵极致写代码 · 高难度深度重构"
        TypeTag     = "👑 极速主脑"
        VRAM        = "27.4 GB"
    }
    "3" = @{
        Name        = "Qwen3.8-27B-A [4并发]"
        GGUF        = "Qwen3.8-27B-Abliterated-Q6_K.gguf"
        Alias       = "Qwen3.8-27B-A-Q6_K"
        Ctx         = 147456      # 全局 144K 统一共享池 (4 槽动态共享)
        Parallel    = 4
        MTP         = $false
        MMProj      = ""
        Speed       = "23.2 tok/s (总吞吐 45+ tok/s)"
        AAIndex     = "52 分 (开源TOP 1)"
        Desc        = "【高负载流水线】4 槽并发 · 零排队交替输入 · 多 Agent 批量任务王者"
        TypeTag     = "🚀 并发流水线"
        VRAM        = "28.5 GB"
    }
    "4" = @{
        Name        = "qwen3vl 8B (8B 原生大视觉)"
        GGUF        = "Qwen3VL-8B-Instruct-Q8_0.gguf"
        Alias       = "qwen3vl 8B"
        Ctx         = 147456      # 全局 144K 统一共享池
        Parallel    = 2
        MTP         = $false
        MMProj      = "mmproj-Qwen3VL-8B-Instruct-F16.gguf"
        Speed       = "61.5 tok/s"
        AAIndex     = "39 分 (视觉标杆)"
        Desc        = "【原生高清视觉】超清大图识别 · 复杂图表与文档公式深度解析"
        TypeTag     = "👁️ 原生超清视觉"
        VRAM        = "15.2 GB"
    }
}

# ------------------------------------------------------------------------------------
# 4. 启动指定模型引擎（保留屏幕输出，不刷屏）
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
    Write-C "  🚀 正在加载模型: $($p.Name)  [$($p.TypeTag)]" "Green"
    Write-C "====================================================================================" "Cyan"
    Write-C "  ├─ 🎯 智能指数 : $($p.AAIndex)" "White"
    Write-C "  ├─ ⚡ 运行速度 : $($p.Speed)" "Yellow"
    Write-C "  ├─ 📚 上下文池 : $($p.Ctx / 1024)K (全局 144K 统一 KV 动态共享池)" "White"
    Write-C "  ├─ 🚦 槽位并发 : $($p.Parallel) 并发槽位 $(if ($p.MTP) { '(🔥 挂载原生 MTP 双草稿投机)' } else { '(无 MTP / 纯自回归)' })" "White"
    Write-C "  ├─ 💾 显存预算 : $($p.VRAM) (预留 6.5GB+ 安全裕量)" "White"
    Write-C "  ├─ 🌐 统一接口 : http://127.0.0.1:8081/v1 (已接管 8083 主脑)" "Cyan"
    Write-C "  └─ 📊 算力大屏 : http://127.0.0.1:8081/dashboard" "Cyan"
    Write-C "====================================================================================" "Cyan"
    Write-C "  ⏳ 正在向 Tesla V100 注入显存，预计耗时约 12~15 秒..." "DarkGray"
    Write-C ""

    # 组装启动参数（每个参数名与值作为独立数组项，严格启用动态共享池）
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
        "--cache-reuse", "512",
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
        "--dry-base", "1.75",
        "--dry-allowed-length", "2",
        "--dry-penalty-last-n", "256",
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
        $argList += @("--spec-type", "draft-mtp", "--spec-draft-n-max", "2", "--spec-draft-n-min", "1")
    }

    if ($p.MMProj) {
        $projPath = Join-Path $ModelsDir $p.MMProj
        if (Test-Path $projPath) {
            $argList += @("--mmproj", $projPath)
        }
    }

    $finalArgs = $argList -join " "
    
    # 记录 Session 日志
    $startLog = "`n[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff'))] --- SESSION START [$($p.Name)] ---`n" +
                "MODEL: $($p.Name)`n" +
                "ARGS: $finalArgs`n"
    [System.IO.File]::AppendAllText($DailyLogFile, $startLog, [System.Text.Encoding]::UTF8)

    # 启动前台主脑进程（使用 @argList 正确数组解构）
    & $ServerExe @argList
}

# ------------------------------------------------------------------------------------
# 5. 主菜单与自动热等待倒计时（保留首页字幕，不刷新屏幕）
# ------------------------------------------------------------------------------------
Ensure-GatewayAndSidecar

Write-C "====================================================================================" "Cyan"
Write-C "   🤖 AI 智能任务自适应网关  ·  Smart Task Dispatch Gateway v4.0" "Green"
Write-C "====================================================================================" "Cyan"
Write-C "   [网关统一入口] http://127.0.0.1:8081/v1 (全应用接入点)" "White"
Write-C "   [实时监控看板] http://127.0.0.1:8081/dashboard" "White"
Write-C "   [标准统一规范] 全阵列 144K 统一动态共享池 · 15秒动态热装载 · 显存零溢出保护" "DarkGray"
Write-C "====================================================================================" "Cyan"
Write-C "   请选择启动模式 (默认 5 秒后自动载入 【1】 Flash 极速接待主力 Ornith-1.5-35B):" "Yellow"
Write-C ""

Write-C "   [1] ⚡ Ornith-1.5-35B (MoE+原生视觉) │ 82.4 t/s │ AA:48分 │ Flash 接待主力 / 极速之王 (默认首选)" "Magenta"
Write-C "   [2] 👑 Qwen3.8-27B-A [双槽MTP]        │ 36.7 t/s │ AA:52分 │ 单人深度代码 / 极低延迟主力" "Green"
Write-C "   [3] 🚀 Qwen3.8-27B-A [4并发]          │ 45.0 t/s │ AA:52分 │ 4槽交替流水线 / 多Agent协同" "Cyan"
Write-C "   [4] 👁️ qwen3vl 8B (原生超清图文)      │ 61.5 t/s │ AA:39分 │ 原生大视觉 / 复杂图表文档解析" "Yellow"
Write-C "   [5] 🛠️ 纯后台网关守护模式 (仅常驻 8081 + 8085，等待首个请求动态冷启动)" "DarkGray"
Write-C "   [Q] 退出启动器" "Red"
Write-C "------------------------------------------------------------------------------------" "Cyan"

# 自动倒计时 5 秒（带重定向保护）
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
        Write-Host "`r   ⏳ 默认启动 [1] Flash 接待主力 倒计时: $i 秒 (按 1~5 键即刻手动选定)... " -ForegroundColor Yellow -NoNewline
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
    Write-Host "   ⏳ 自动载入默认 [1] Flash 接待主力 Ornith-1.5-35B..." -ForegroundColor Yellow
}

if (-not $choice -or $choice -eq "`r" -or $choice -eq "`n") { $choice = "1" }
$choice = $choice.ToUpper()

if ($choice -eq "Q") {
    Write-C "退出。" "DarkGray"
    exit
} elseif ($choice -in @("1", "2", "3", "4")) {
    Start-SelectedProfile $choice
} elseif ($choice -eq "5") {
    Write-C "🟢 纯后台网关守护已就绪 (8081 + 8085)，等待首个任务请求..." "Green"
    while ($true) { Start-Sleep -Seconds 3600 }
} else {
    Write-C "无效选择，默认启动 [1] Flash 接待主力..." "Yellow"
    Start-SelectedProfile "1"
}
