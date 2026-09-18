# llama.cpp 启动器项目上下文文档

> **本文档供其他 AI 或开发者快速接手项目使用。请完整阅读后再动手修改。**
> **最后更新：2026-07-15**

---

## ⚠️ 最重要：文件编码（AI 最常犯的错误）

### 启动器脚本的编码格式

| 属性 | 值 | 说明 |
|---|---|---|
| **字符编码** | **GBK** | 不是 UTF-8！不是 UTF-8-BOM！不是 GB2312！是 **GBK** |
| **换行符** | **CRLF (\r\n)** | Windows 格式 |
| **BOM** | **无** | 不带 BOM 头 |

### 验证方法

```bash
# 验证编码
python -c "
raw = open(r'H:\llama-bin-win-cuda-13.3-x64\qidongqi\AI大模型启动中心端口8081-v23.ps1','rb').read()
print('GBK:', '大模型'.encode('gbk') in raw)      # 应为 True
print('UTF8:', '大模型'.encode('utf-8') in raw)    # 应为 False
"
```

### 修改脚本时的强制规则

1. **绝对不能**用 Edit/Write 工具直接编辑——这些工具默认 UTF-8，会破坏 GBK 编码导致中文乱码
2. **必须**用 Python 脚本以 `encoding='gbk'` 读写：
   ```python
   with open(filepath, 'r', encoding='gbk') as f:
       content = f.read()
   # ... 修改 content ...
   with open(filepath, 'w', encoding='gbk') as f:
       f.write(content)
   ```
3. 写入的中文内容不能包含 GBK 字符集以外的字符（如 ✔、→、✅、⚠️ 等 emoji/special unicode），否则写入时报 `UnicodeEncodeError`
4. 修改后必须用 PowerShell 验证语法：
   ```bash
   powershell.exe -NoProfile -Command "[System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw '脚本路径'), [ref]$null) | Out-Null; Write-Host 'Syntax OK'"
   ```

### 日志文件编码

| 文件 | 编码 |
|---|---|
| `logs/run-*.log` | **UTF-8**（PowerShell `Add-Content -Encoding UTF8` 写入） |
| 启动器 `.ps1` | **GBK** |

---

## 一、项目概述

### 文件结构

```
H:\llama-bin-win-cuda-13.3-x64\
├── qidongqi\
│   ├── AI大模型启动中心端口8081-v88.ps1   ← 当前主启动器（UTF-8 BOM；版本化保留 v71–v88，旧版已清）
│   ├── api-proxy.js / mcp-server.js / start-mcp.bat ← MCP 子系统（前端托管用，留原位）
│   ├── anytxt_mcp_bridge.py / mcp-servers.json / mcp-proxy-config.json ← MCP 配置
│   ├── qwen_*_chat_template.jinja / qwen_fixed_readme.md ← 聊天模板
│   ├── 本机MCP配置指南.md / PROJECT_CONTEXT.md
│   └── scripts\                           ← 独立脚本归档（2026-08-26 整理）
│       ├── llama_bench-v6.ps1 / restart-all.ps1 / inspect_gguf.py
│       ├── test_*_benchmark.ps1 / mtp_repomethod_*.csv|.html
│       └── _dev\                          ← 版本生成/验证开发脚本存档
├── logs\
│   └── run-YYYY-MM-DD.log                 ← 运行日志（每日一个，UTF-8）
├── llama-server.exe                        ← llama.cpp b9986 服务端
├── launcher.ps1                            ← 入口，调用 qidongqi/v23.ps1
└── AI大模型启动中心端口8081.bat             ← 批处理入口
```

### 硬件环境

| 组件 | 规格 |
|---|---|
| GPU | **RTX 5080** 16GB VRAM (Blackwell SM12.0) |
| CUDA | **13.3** (驱动 610.62) |
| CPU | **i5-12490F** 6核12线程 |
| 内存 | **64GB** DDR4 |
| 可用 VRAM | ~14.5GB（系统占用约1.5GB） |

### 软件版本

| 组件 | 版本 |
|---|---|
| llama.cpp | **b9986** (commit 91c631b21) |
| 构建编译器 | Clang 20.1.8 |
| CUDA ARCHS | 750,800,860,890,900,1200,1210 |
| 特性 | USE_GRAPHS=1, BLACKWELL_NATIVE_FP4=1 |

---

## 二、启动器功能模块

### 2.1 启动流程

```
用户双击 .bat → launcher.ps1 → v23.ps1
  ↓
Get-HardwareInfo()  ← 检测GPU/CPU/RAM
Get-LlamaVersion()  ← 检测llama.cpp版本
Get-Models()        ← 扫描 H:\models\*.gguf
  ↓
Show-Menu()         ← 显示模型列表菜单
  ↓
用户选择模型编号
  ↓
Invoke-Launch()     ← 核心启动逻辑
  ├── 根据模型匹配预设参数
  ├── 计算VRAM预算，决定ngl/ctx
  ├── 查找mmproj（仅IQ4_XS视觉模型）
  ├── Start-RunLogger()  ← 启动后台日志采样Job
  ├── Start-Process llama-server.exe  ← 启动服务（不阻塞）
  ├── while循环轮询 /health  ← 等模型加载完成
  ├── 加载完成后自动打开浏览器 → http://127.0.0.1:8081
  └── llama-server退出后清理日志Job
```

### 2.2 模型预设策略

启动器根据模型文件名自动匹配预设，决定最优参数：

#### 27B 三变体独立策略

| 模型 | 大小 | 预设名 | ngl | ctx | KV类型 | mmproj | spec | 说明 |
|---|---|---|---|---|---|---|---|---|
| **Q4_K_M** | 16.8GB | `27b-q4km-quality` | ~52 | 8192 | q8_0 | ❌不加载 | ❌关闭 | 质量>速度，部分层走CPU |
| **IQ4_XS** | 15.1GB | `27b-iq4xs-vision` | ~54 | 4096 | q8_0 | ✅加载 | ❌关闭 | 视觉极限优化 |
| **IQ2_M** | 10.0GB | `27b-iq2-fast` | 999 | 65536 | f16 | ❌不加载 | ngram-mod | 全GPU加速 |

#### 其他模型

| 模型类型 | 预设名 | ngl | ctx | KV类型 | spec |
|---|---|---|---|---|---|
| 35B-A3B / MoE | `large-moe` | 999→--fit | 8192 | q8_0 | ngram-mod |
| Qwen3VL-8B | `vision` | 999 | 65536 | f16 | ngram-mod |
| 8B+ 纯文本 | `8b-plus` | 999 | 65536 | q8_0 | ngram-mod |
| 4-8B 中型 | `4b-8b` | 999 | 65536 | f16 | ngram-mod |
| 4B 以下 | `small` | 999 | 65536 | f16 | ngram-mod |

### 2.3 mmproj 守卫规则

```powershell
# Find-MatchingMmproj 函数开头
if ($ModelName -notmatch 'IQ4_XS') { return "" }
```

**只有文件名包含 `IQ4_XS` 的模型才加载 mmproj**。其他所有模型（Q4_K_M、IQ2_M、纯文本模型）一律不加载。

原因：Qwen3.5-4B 纯文本模型曾被错误匹配到 Qwen3.5-9B 的 mmproj，导致 n_embd 维度不匹配崩溃。

### 2.4 VRAM 预算公式

```
vramForKv = freeVRAM - 模型权重(MB) - 计算缓冲(500-1024MB) - 安全余量(1024MB)
ngl = floor(vramForKv / 每层大小MB), 限制在 [40, 65] 范围
```

27B 模型精确参数（来自 GGUF 元数据）：
- 架构: qwen35
- 层数: 65（64 block + 1 output）
- n_kv_heads: 4（GQA）
- head_dim: 256
- 每层大小: Q4_K_M=247MB, IQ4_XS=232MB, IQ2_M=154MB
- 每 token KV(f16): 260KB
- 每 token KV(q8_0): 130KB

### 2.5 命令行参数构建

```powershell
$cmdArgs = @(
    "-m", $Model.Path,
    "--alias", $shortAlias,
    "-ngl", $ngl,
    "-c", $ctx,
    "-b", $batch,        # 逻辑批处理
    "-ub", $ubatch,      # 物理批处理
    "-t", $threads,      # CPU线程（重型模型=6，其他=-1自动）
    "-tb", $threadsB,
    "--flash-attn", $flashAttn,
    "-ctk", $ctk,        # KV cache K 类型
    "-ctv", $ctv,        # KV cache V 类型
    "--host", "0.0.0.0",
    "--port", $LLAMA_PORT,
    "--metrics"          # 启用 /metrics 端点（日志采集必需）
)
if ($mmprojPath) { $cmdArgs += @("--mmproj", $mmprojPath) }
if ($specType -ne "") { $cmdArgs += @("--spec-type", $specType) }
```

**重要**：`--metrics` 参数必须存在，否则日志系统无法采集 tok/s 数据。

---

## 三、日志系统

### 3.1 日志文件

| 属性 | 值 |
|---|---|
| 路径 | `H:\llama-bin-win-cuda-13.3-x64\logs\run-YYYY-MM-DD.log` |
| 命名 | 每日一个文件 |
| 写入方式 | 追加（同一天多次运行都写入同一文件） |
| 编码 | UTF-8 |
| 自动清理 | 30天前的日志自动删除 |

### 3.2 日志格式

#### 启动头（含完整参数）
```
======================================================================
[2026-07-15 09:24:16] MODEL STARTUP
  model   : Qwen3.6-27B (15.7GB)
  path    : H:\models\Qwen3.6-27B-Q4_K_M.gguf
  port    : 8081
  params  : ngl=52 ctx=8192 batch=2048 ubatch=512 flash=on kv=q8_0/q8_0 threads=6 spec= preset=27b-q4km-quality
======================================================================
```

#### 加载阶段
```
[09:24:35] === LOADED in 15s ===
  model_info : ftype=Q4_K_M slots=4 modality=text build=b9986-91c631b21
[09:24:23] loading +5s | CPU:7.5% RAM:20.5GB/63.82GB (32.1%) | GPU:0% VRAM:0.94GB/15.92GB T:37C P:46.40W
```

#### 运行监控（每5秒）
```
[09:24:42] #001 | CPU:98.2% RAM:27.95GB/63.82GB (43.8%) | GPU:0% VRAM:13.28GB/15.92GB T:37C P:46.79W SM:2670MHz | gen:7.1t/s prompt:12.1t/s gen_tok:9 prompt_tok:13 reqs:1
```

字段说明：
- `gen:X.Xt/s` — 生成速度（tokens/second）
- `prompt:X.Xt/s` — 预填充速度
- `gen_tok:N` — 累计生成 token 数
- `prompt_tok:N` — 累计 prompt token 数
- `reqs:N` — 当前活跃请求数

#### 退出统计（含峰值）
```
[09:30:00] === STOPPED (samples:26 peak_eval:7.1t/s peak_prompt:87.6t/s peak_gpu:18% peak_power:83W peak_vram:13.3GB) ===
```

### 3.3 数据采集源

| 数据 | 采集方法 | 接口/命令 |
|---|---|---|
| GPU利用率/温度/功耗/频率/显存 | `nvidia-smi --query-gpu=...` | CSV格式解析 |
| CPU利用率 | `Get-Counter '\Processor(_Total)\% Processor Time'` | PowerShell性能计数器 |
| 内存使用 | `Get-CimInstance Win32_OperatingSystem` | WMI |
| tok/s | `GET http://127.0.0.1:PORT/metrics` | Prometheus格式 |
| 模型信息 | `GET http://127.0.0.1:PORT/props` + `/slots` | JSON |

### 3.4 /metrics 端点格式（b9986）

**这是 AI 最容易搞错的地方。** llama.cpp b9986 的 /metrics 用 `llamacpp:` 前缀，不是 `llama_context_`：

```
llamacpp:predicted_tokens_seconds  7.1     ← 生成速度 tok/s
llamacpp:prompt_tokens_seconds     87.6    ← 预填充速度 tok/s
llamacpp:tokens_predicted_total    9       ← 累计生成token数
llamacpp:prompt_tokens_total       475     ← 累计prompt token数
llamacpp:requests_processing       1       ← 活跃请求数
```

### 3.5 /props 端点可用字段（b9986）

**不是所有字段都有！** 实际可用：
- `model_ftype` — 量化类型（如 "Q4_K_M", "IQ4_XS - 4.25 bpw"）
- `total_slots` — 并行槽位数
- `modalities.vision` — 是否视觉模型
- `build_info` — 构建版本

**不存在的字段**（不要尝试读取）：`model_arch`, `model_n_layer`, `model_n_vocab`, `model_n_embd`, `model_n_head`, `n_gpu_layers`, `kv_cache_type_k`, `flash_attn`

---

## 四、性能实测数据（2026-07-15）

### 4.1 三个 27B 变体实测

| 模型 | ngl | gen t/s | prompt t/s | GPU% | CPU% | 功耗 | VRAM | 结论 |
|---|---|---|---|---|---|---|---|---|
| **IQ2_M** (10GB) | 999 | **61.5** | 899.1 | 94-95% | 5-42% | 348W | 14.9GB | ✅ 全GPU，性能最佳 |
| **Q4_K_M** (16.8GB) | 48 | **7.1** | 87.6 | 0-18% | 65-98% | 80W | 13.3GB | 🔴 CPU严重瓶颈 |
| **IQ4_XS** (15.1GB) | 56 | 未测到 | 176.8 | 19-56% | 60-82% | 103W | 14.9GB | 🟡 部分CPU |

### 4.2 Q4_K_M 速度瓶颈数学模型

从实测数据反推：
- GPU 每层计算时间：**0.25ms**（RTX 5080 极快）
- CPU 每层计算时间：**7.6ms**（i5-12490F，比GPU慢30倍）
- 瓶颈：17层在CPU → 每token CPU耗时 128.8ms → 仅 7.1 t/s

### 4.3 Q4_K_M 不同配置预测

| 配置 | ctx | KV | ngl | CPU层 | 预测 gen t/s |
|---|---|---|---|---|---|
| 当前(旧) | 8192 | f16 | 48 | 17 | 7.1 |
| 已改 | 8192 | q8_0 | 52 | 13 | ~9 |
| 速度优先 | 4096 | q8_0 | 54 | 11 | ~10 |
| 极限 | 2048 | q8_0 | 55 | 10 | ~11 |

**结论：Q4_K_M 在 16GB 显卡上速度上限约 10-11 t/s，物理瓶颈无法突破。**

### 4.4 35B-A3B MoE 速度暴跌事件

| 时间 | 现象 | 根因 |
|---|---|---|
| 最初 | 96 t/s | 旧参数正常运行 |
| 改造后 | 10 t/s | 新启动器给了 ctx=65536，但14.4GB模型+大ctx超出16GB VRAM |
| 修复 | ~? | 改为 ctx=8192 + `--fit on --fit-ctx 4096` 让框架自动调整 |

### 4.5 已知性能数据（启动器菜单显示的评测分数）

| 模型 | 准确率 | 幻觉率 | 速度 | 综合得分 |
|---|---|---|---|---|
| qwen3vl:8b-hauhau-q8 | 100% | 7% | 90tok/s | 79.7 |
| qwen3.6:27b-hauhau-iq2m | 100% | 13% | 59tok/s | 77.3 |
| qwen3.5:4b-q4km | 100% | 20% | 159tok/s | 77.2 |
| qwen3.6:35b-a3b-hauhau-iq3m | 100% | 20% | 96tok/s | 75.9 |
| qwopus-glm-18b:q4km | 100% | 20% | 67tok/s | 75.3 |
| qwen2.5:14b-instruct-q4km | 87% | 13% | 80tok/s | 71.2 |
| qwen3.5:9b-hauhau-q8 | 100% | 40% | 79tok/s | 69.6 |

---

## 五、常见问题与已踩的坑

### 5.1 编码问题（最高频）
- **症状**：修改脚本后中文变乱码
- **根因**：用了 UTF-8 编码写入 GBK 文件
- **解决**：所有修改必须用 Python `encoding='gbk'`

### 5.2 tok/s 采不到
- **症状**：日志里 tok/s 为空
- **根因1**：指标名用错了（`llama_context_*` → 应为 `llamacpp:*`）
- **根因2**：没加 `--metrics` 启动参数
- **解决**：cmdArgs 必须包含 `--metrics`；正则匹配 `llamacpp:predicted_tokens_seconds`

### 5.3 mmproj 维度不匹配崩溃
- **症状**：`mismatch between text model (n_embd=2560) and mmproj (n_embd=4096)`
- **根因**：纯文本模型被错误匹配到其他模型的 mmproj
- **解决**：Find-MatchingMmproj 开头加守卫 `if ($ModelName -notmatch 'IQ4_XS') { return "" }`

### 5.4 大模型 ctx 过大导致速度暴跌
- **症状**：35B 模型从 96 t/s 暴跌到 10 t/s
- **根因**：ctx=65536 的 KV cache 超出 VRAM，被迫 swap 到系统内存
- **解决**：VRAM 预算公式精确计算最大 ctx；MoE 模型用 `--fit on`

### 5.5 PowerShell 数组操作符优先级
- **症状**：`$header = @("=", "=" * 60, ...) -join "\`r\`n"` 报 InvalidArgument
- **根因**：`@()` 内的 `*` 和 `-f` 操作符优先级冲突
- **解决**：改用字符串拼接 `$header += $sep + "\`r\`n"`

### 5.6 GPU SM 频率 vs CUDA 版本混淆
- **易错**：`nvidia-smi` 返回的 `compute_cap=12.0` 是 SM 计算能力，不是 CUDA 版本
- **实际**：RTX 5080 = SM 12.0 (Blackwell)，CUDA 版本看 `nvidia-smi` 表头 = **13.3**

### 5.7 -t 线程数设置
- **易错**：`-t 12`（用逻辑线程数）
- **正确**：`-t 6`（用物理核数）。i5-12490F 是 6核12线程，推理是计算密集型，超线程兄弟核共享 FPU 会争抢

---

## 六、连接信息

| 用途 | 地址 |
|---|---|
| 本机网页聊天 | http://127.0.0.1:8081 |
| 局域网访问 | http://192.168.x.x:8081 |
| OpenAI API | http://127.0.0.1:8081/v1/chat/completions |

---

## 七、待办 / 优化方向

- [ ] Q4_K_M 速度优先预设（ctx=4096 KV=q8_0 ngl=54）待用户确认后实施
- [ ] IQ4_XS 需要完整生成测试（上次只测了 prompt，没测到 gen t/s）
- [ ] Power Limit 从 360W 拉到 380W 的效果对比测试
- [ ] 35B-A3B MoE 修复后的速度验证（预期恢复到 80-96 t/s）
