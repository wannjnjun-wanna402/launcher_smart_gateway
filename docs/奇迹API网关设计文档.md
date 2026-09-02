# Miracle API Gateway — Windows EXE 架构设计文档

> **版本**: v2.0 (EXE 重构版)
> **目标平台**: Windows 10/11 x64
> **硬件基准**: Tesla V100-PCIE-32GB / 6核 CPU / CUDA 12.4
> **底层引擎**: llama.cpp (b9982+)
> **设计日期**: 2026-07-21

---

## 一、系统概览

### 1.1 设计目标

将现有 `miracle_api.py` (Python CLI 脚本) 重构为 **原生 Windows EXE 桌面应用**，提供：

- 图形化一键启动/停止服务
- 模型参数可视化配置与自动优化
- 实时 GPU/速度/延迟监控面板
- API 密钥与网络配置管理
- 结构化日志与错误处理

### 1.2 技术选型

| 层级 | 选型 | 理由 |
|------|------|------|
| **UI 框架** | PySide6 (Qt6) | 原生 Windows 渲染、组件丰富、与 Python 后端无缝衔接 |
| **打包工具** | PyInstaller / Nuitka | 一键打包为单 EXE，附带 DLL 资源 |
| **后端引擎** | llama-server.exe (子进程) | 直接复用现有二进制，零迁移成本 |
| **HTTP 代理** | Python http.server → 嵌入 Qt 线程 | 保持 OpenAI 兼容 API 转发逻辑 |
| **GPU 监控** | nvidia-smi CLI 子进程 | 无需额外 SDK 依赖，跨驱动版本兼容 |
| **配置存储** | JSON (miracle_config.json) | 轻量、人可读、版本可控 |
| **日志** | Python logging + RotatingFileHandler | 自动轮转，不无限增长 |

### 1.3 与现有系统的关系

```
现有架构:
  miracle_api.py (CLI) ──→ llama-server.exe (子进程) ──→ GPU
         ↑
  Hermes / 外部客户端 (HTTP :51108)

EXE 架构:
  MiracleGateway.exe
    ├─ Qt UI 主窗口
    ├─ ServiceController (管理 llama-server 子进程)
    ├─ ParamOptimizer (参数优化引擎)
    ├─ MonitorPanel (实时监控)
    ├─ HttpProxy (OpenAI 兼容 API 转发, 嵌入 Qt 线程)
    └─ ConfigManager (JSON 配置持久化)
         │
         └──→ llama-server.exe (子进程) ──→ GPU
                  ↑
         Hermes / 外部客户端 (HTTP :51108)
```

---

## 二、模块架构

### 2.1 模块关系图

```
┌─────────────────────────────────────────────────────────────────────┐
│                     MiracleGateway.exe                              │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Qt UI 主窗口 (MainWindow)                 │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │   │
│  │  │ 服务控制  │  │ 模型配置  │  │ 实时监控  │  │ 系统配置  │   │   │
│  │  │   Tab    │  │   Tab    │  │   Tab    │  │   Tab    │   │   │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │   │
│  └───────┼─────────────┼────────────┼─────────────┼──────────┘   │
│          │             │             │             │               │
│  ┌───────▼─────────────▼─────────────▼─────────────▼──────────┐   │
│  │                      核心服务层                              │   │
│  │                                                             │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │   │
│  │  │  Service     │  │    Param     │  │   Config     │     │   │
│  │  │  Controller  │  │   Optimizer  │  │   Manager    │     │   │
│  │  │              │  │              │  │              │     │   │
│  │  │ - 启停服务    │  │ - 自动参数    │  │ - JSON 读写   │     │   │
│  │  │ - 进程管理    │  │ - 手动覆盖    │  │ - 密钥管理    │     │   │
│  │  │ - 健康检查    │  │ - 模型探测    │  │ - 网络配置    │     │   │
│  │  └──────┬───────┘  └──────────────┘  └──────────────┘     │   │
│  │         │                                                   │   │
│  │  ┌──────▼──────────────────────────────────────────────┐   │   │
│  │  │              Monitor Engine                          │   │   │
│  │  │  ┌─────────┐  ┌──────────┐  ┌─────────────────┐    │   │   │
│  │  │  │ GPU     │  │ Speed    │  │ Log             │    │   │   │
│  │  │  │ Probe   │  │ Tracker  │  │ Recorder        │    │   │   │
│  │  │  │         │  │          │  │                 │    │   │   │
│  │  │  │nvidia-smi│ │SSE解析   │  │RotatingFile     │    │   │   │
│  │  │  │10s 周期  │  │实时估算  │  │10MB×5 备份      │    │   │   │
│  │  │  └─────────┘  └──────────┘  └─────────────────┘    │   │   │
│  │  └─────────────────────────────────────────────────────┘   │   │
│  │                                                             │   │
│  │  ┌─────────────────────────────────────────────────────┐   │   │
│  │  │              HTTP Proxy (嵌入 QThread)               │   │   │
│  │  │  /v1/chat/completions  → 转发到 llama-server :8083   │   │   │
│  │  │  /v1/models            → 模型列表                    │   │   │
│  │  │  /v1/dashboard         → 用量统计                    │   │   │
│  │  │  /health               → 健康检查                    │   │   │
│  │  └─────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
└──────────────────────────────┼──────────────────────────────────────┘
                               │ subprocess.Popen
                               ▼
                    ┌─────────────────────┐
                    │  llama-server.exe   │
                    │  (独立子进程)        │
                    │  --port 8083        │
                    │  -m <model.gguf>    │
                    │  -ngl 99 ...        │
                    └──────────┬──────────┘
                               │ CUDA 12.4
                               ▼
                    ┌─────────────────────┐
                    │  GPU (V100 32GB)    │
                    └─────────────────────┘
```

### 2.2 核心模块职责

| 模块 | 类名 | 职责 | 信号/事件 |
|------|------|------|----------|
| **服务控制器** | `ServiceController` | 管理 llama-server 子进程生命周期 | `service_started`, `service_stopped`, `service_error` |
| **参数优化引擎** | `ParamOptimizer` | 根据 GPU 显存 + 模型元数据自动计算最优参数 | `params_optimized`, `optimization_failed` |
| **监控面板** | `MonitorEngine` | 10s 周期采集 GPU 功率/显存 + 实时速度追踪 | `gpu_updated`, `speed_updated`, `log_appended` |
| **HTTP 代理** | `HttpProxyThread` | OpenAI 兼容 API 转发，嵌入 QThread | `request_completed`, `usage_recorded` |
| **配置管理器** | `ConfigManager` | JSON 配置文件读写、API Key 管理 | `config_changed` |

---

## 三、参数优化引擎

### 3.1 设计原理

参数优化的核心目标：**在有限 GPU 显存下，平衡推理质量与速度**。

关键约束：
- GPU 显存 = 模型权重 + KV Cache + 计算缓冲区
- `batch_size` 越大 → prompt 处理越快 → 但显存占用增加
- `n_threads` 越多 → CPU 计算越快 → 但超过物理核心数反而降速
- `context_length` 越大 → 可处理更长对话 → 但 KV Cache 线性增长
- KV Cache 量化 (q8_0/f16) → 质量与显存的权衡

### 3.2 显存估算模型

```python
def estimate_vram(model_params_billion, quant_bits, context_length, 
                  batch_size, kv_quant_bytes_per_token):
    """
    估算总 GPU 显存占用 (MB)
    
    参数:
        model_params_billion: 模型参数量 (十亿), 如 35.0
        quant_bits: 量化位数, 如 4.0 (Q4), 2.0 (IQ2)
        context_length: 上下文长度, 如 81920
        batch_size: 批大小, 如 2048
        kv_quant_bytes_per_token: KV cache 每层每 token 字节数
    
    返回:
        total_vram_mb: 预估总显存 (MB)
    """
    # 1. 模型权重显存
    #    权重(MB) = 参数量(B) × 量化位数 / 8 / 1024
    model_weight_mb = model_params_billion * quant_bits / 8 * 1024
    
    # 2. KV Cache 显存
    #    受 context_length 和量化类型影响
    #    公式: kv_cache = 2 × n_layers × hidden_dim × context × bytes_per_element
    #    简化估算 (经验值):
    #      q8_0: ~1.0 bytes/element → 每千 token 约 0.5-2 MB/层
    #      f16:  ~2.0 bytes/element → 翻倍
    #    经验公式: kv_cache_mb ≈ context_length × kv_factor
    kv_factor = {
        'q4_0': 0.008, 'q5_0': 0.010, 'q8_0': 0.016, 'f16': 0.032
    }.get(kv_quant, 0.016)
    kv_cache_mb = context_length * kv_factor * model_params_billion / 7  # 按层归一化
    
    # 3. 计算缓冲区 (batch 相关)
    #    经验值: batch_size × 0.5 MB
    compute_buffer_mb = batch_size * 0.5
    
    # 4. 框架开销 (约 500MB 固定)
    overhead_mb = 500
    
    total_vram_mb = model_weight_mb + kv_cache_mb + compute_buffer_mb + overhead_mb
    return total_vram_mb
```

### 3.3 自动参数优化算法 (伪代码)

```
ALGORITHM: AutoOptimizeParams(model_file, gpu_vram_mb, cpu_cores, 
                               user_priority, manual_overrides)

INPUT:
    model_file       — GGUF 模型文件路径
    gpu_vram_mb      — GPU 总显存 (MB), 如 32768
    cpu_cores        — CPU 物理核心数, 如 6
    user_priority    — "speed" | "balanced" | "quality"
    manual_overrides — 用户手动指定的参数覆盖, 如 {"batch_size": 2048}

OUTPUT:
    optimized_params — 完整参数字典

1.  PARSE model_file → extract metadata:
      model_params_billion  (参数量, 十亿)
      quant_bits            (量化位数)
      n_layers              (层数)
      hidden_dim            (隐藏维度)
      has_vision            (是否视觉模型)
      architecture          (架构类型: MoE / Dense)

2.  SAFETY_MARGIN ← 0.85   // 保留 15% 安全余量
    USABLE_VRAM ← gpu_vram_mb × SAFETY_MARGIN

3.  // --- Step 1: 确定 n_threads ---
    IF manual_overrides has "n_threads":
        n_threads ← manual_overrides["n_threads"]
    ELSE:
        n_threads ← MIN(cpu_cores, 8)   // 超过 8 线程收益递减
        IF architecture == "MoE":
            n_threads ← cpu_cores       // MoE 的 CPU 专家需要更多线程

4.  // --- Step 2: 确定 KV Cache 量化 ---
    IF user_priority == "speed":
        kv_type_k ← "q4_0"
        kv_type_v ← "q4_0"
    ELIF user_priority == "balanced":
        kv_type_k ← "q8_0"
        kv_type_v ← "q8_0"
    ELSE:  // "quality"
        kv_type_k ← "f16"
        kv_type_v ← "f16"
    
    IF has_vision:
        kv_type_k ← "f16"   // 视觉模型强制 f16 保证质量
        kv_type_v ← "f16"

5.  // --- Step 3: 确定上下文长度 ---
    IF manual_overrides has "context_length":
        context_length ← manual_overrides["context_length"]
    ELSE:
        // 从最大开始，逐步降低直到显存够用
        context_length ← model_native_context   // 如 32768
        WHILE context_length >= 4096:
            estimated ← EstimateVRAM(model_params_billion, quant_bits,
                                      context_length, batch_size,
                                      kv_quant_bytes)
            IF estimated <= USABLE_VRAM:
                BREAK
            context_length ← context_length × 0.5  // 减半重试

6.  // --- Step 4: 确定 batch_size ---
    IF manual_overrides has "batch_size":
        batch_size ← manual_overrides["batch_size"]
    ELSE:
        // 初始 batch_size 估算
        remaining_vram ← USABLE_VRAM - EstimateVRAM(... without batch ...)
        batch_size ← MIN(2048, MAX(512, INT(remaining_vram / 0.5)))
        
        IF user_priority == "speed":
            batch_size ← MIN(4096, batch_size × 2)
        IF user_priority == "quality":
            batch_size ← MAX(512, batch_size × 0.5)

7.  // --- Step 5: 确定投机解码 ---
    IF architecture == "MoE" AND model_params_billion > 20:
        spec_type ← "draft-mtp"     // 大 MoE 用 MTP
        spec_n_max ← 3
    ELIF NOT has_vision:
        spec_type ← "ngram-mod"     // 普通模型用 ngram
        spec_n_max ← 0
    ELSE:
        spec_type ← None            // 视觉模型不投机

8.  // --- Step 6: 确定推理参数 ---
    IF user_priority == "speed":
        temp ← 0.7, top_p ← 0.9, top_k ← 20
    ELIF user_priority == "quality":
        temp ← 0.3, top_p ← 0.9, top_k ← 20
    ELSE:
        temp ← 0.7, top_p ← 0.9, top_k ← 20

9.  // --- Step 7: 固定参数 ---
    ngl ← 99                        // 全层上 GPU
    parallel ← 1                    // 单用户无需并行
    flash_attn ← "enabled"          // V100 支持

10. RETURN {
        ngl, context_length, batch_size, n_threads,
        parallel, flash_attn, kv_type_k, kv_type_v,
        spec_type, spec_n_max,
        temp, top_p, top_k, min_p, repeat_penalty
    }
```

### 3.4 参数优化实测基准 (V100 32GB)

| 模型 | 量化 | 参数量 | 上下文 | batch | KV量化 | 预估显存 | 实测显存 | 速度 |
|------|------|--------|--------|-------|--------|---------|---------|---------|
| Qwen3.6-35B-MTP | IQ-Compact | 35B | 81920 | 2048 | q8_0/q8_0 | ~28.5 GB | 18.5 GB | ~104 tok/s |
| Qwen3.6-27B-MTP | IQ4_XS | 27B | 98304 | 2048 | q8_0/q8_0 | ~24.2 GB | 18.5 GB | ~60 tok/s |
| Qwen3VL-8B | Q4_K_M | 8B | 98304 | 2048 | f16/f16 | ~12.8 GB | 18.5 GB | ~80 tok/s |

> 注：预估偏保守，实际 MoE 架构激活参数远小于总参数，显存占用低于估算。

### 3.5 手动调整滑块设计

```
┌─ 手动参数覆盖 ──────────────────────────────────────┐
│                                                     │
│  Batch Size      [████████████████░░░░] 2048       │
│  范围: 1 - 512     说明: prompt 处理批量             │
│                                                     │
│  n_Threads       [██████░░░░░░░░░░░░░░] 6           │
│  范围: 1 - 16      说明: CPU 计算线程数              │
│                                                     │
│  Context Length  [████████████████████] 81920       │
│  范围: 2048 - 131072  说明: 最大上下文窗口           │
│                                                     │
│  ⚠ 手动参数将覆盖自动优化结果                        │
│  [恢复自动优化]  [应用手动参数]                      │
│                                                     │
│  预估显存: 28.5 GB / 32 GB  ████████████░░ 87%     │
└─────────────────────────────────────────────────────┘
```

---

## 四、UI 布局设计 (PySide6 / Qt6)

### 4.1 主窗口布局示意图

```
┌──────────────────────────────────────────────────────────────────────┐
│  ⚡ Miracle API Gateway                              ─  □  ×          │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────┬─────────┬─────────┬─────────┐  ┌──────────────────┐  │
│  │ 服务控制 │ 模型配置 │ 实时监控 │ 系统配置 │  │ ● 服务运行中      │  │
│  └─────────┴─────────┴─────────┴─────────┘  │ 当前: 35B-MTP     │  │
│  ════════════════════════════════════════  │ :51108            │  │
│                                              └──────────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                      (Tab 内容区)                             │   │
│  │                                                              │   │
│  │                                                              │   │
│  │                                                              │   │
│  │                                                              │   │
│  │                                                              │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ 日志面板                                          [清空] [▼] │   │
│  │ [12:30:00] [INFO] 服务已启动, 端口 51108                    │   │
│  │ [12:30:10] [MON] 35B-MTP | 0.0 tok/s | 25W | 18.5GB/32GB  │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.2 Tab 1: 服务控制

```
┌──────────────────────────────────────────────────────────────────────┐
│  服务控制                                                            │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│         ┌──────────────────────────────────┐                        │
│         │                                  │                        │
│         │      🟢 服务运行中               │                        │
│         │      端口: 51108                 │                        │
│         │      运行时间: 00:15:32          │                        │
│         │                                  │                        │
│         │   [  ⏹ 停止服务  ]               │                        │
│         └──────────────────────────────────┘                        │
│                                                                      │
│  ── 或 ──                                                            │
│                                                                      │
│         ┌──────────────────────────────────┐                        │
│         │                                  │                        │
│         │      🔴 服务已停止               │                        │
│         │                                  │                        │
│         │   [  ▶ 启动服务  ]               │                        │
│         └──────────────────────────────────┘                        │
│                                                                      │
│  ── 当前加载模型 ──                                                   │
│                                                                      │
│  模型:  Qwen3.6-35B-MTP                                              │
│  文件:  Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf                     │
│  路径:  E:\models\                                                   │
│  显存:  18,547 MB / 32,768 MB (56.6%)                               │
│  ████████████████████░░░░░░░░░░░░░░░░░░  56.6%                      │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.3 Tab 2: 模型配置

```
┌──────────────────────────────────────────────────────────────────────┐
│  模型配置                                                            │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ── 模型选择 ──                                                      │
│                                                                      │
│  预置模型:  [ Qwen3.6-35B-MTP          ▼ ]   类别: 快速聊天         │
│             ├ Qwen3.6-35B-MTP (35B, 快速聊天)                       │
│             ├ Qwen3VL-8B      (8B, 视觉理解)                        │
│             └ Qwen3.6-27B-MTP (27B, 深度思考)                       │
│                                                                      │
│  ┌─ 添加自定义模型 ────────────────────────────────────────────┐    │
│  │  GGUF 文件: [E:\models\new-model.gguf          ] [浏览...]  │    │
│  │  显示名称:  [My Custom Model                     ]           │    │
│  │  mmproj:    [                              ] [浏览...] (可选)│    │
│  │                                                             │    │
│  │  优化策略:  ◉ 速度优先  ○ 均衡  ○ 质量优先                  │    │
│  │                                                             │    │
│  │  ┌─ 自动计算结果 ───────────────────────────────────────┐  │    │
│  │  │  模型参数量:    35.0 B  (自动探测)                   │  │    │
│  │  │  量化位数:      4.0 bit (自动探测)                   │  │    │
│  │  │  预估显存:      28.5 GB / 32 GB  (87%)              │  │    │
│  │  │  ████████████████████████░░░░░  87%                 │  │    │
│  │  │  推荐参数:                                            │  │    │
│  │  │    -ngl 99  -c 81920  -b 2048  -t 6                 │  │    │
│  │  │    --cache-type-k q8_0  --cache-type-v q8_0         │  │    │
│  │  │    --flash-attn enabled  --parallel 1               │  │    │
│  │  │    --spec-type draft-mtp  --spec-draft-n-max 3      │  │    │
│  │  └──────────────────────────────────────────────────────┘  │    │
│  │                                                             │    │
│  │  ┌─ 手动覆盖 (可选) ─────────────────────────────────────┐  │    │
│  │  │  Batch Size    [████████████████░░] 2048   (1-512)   │  │    │
│  │  │  n_Threads     [██████░░░░░░░░░░░░] 6       (1-16)   │  │    │
│  │  │  Context Len   [████████████████░░] 81920  (2K-128K) │  │    │
│  │  │  KV Cache K    [q8_0 ▼]                              │  │    │
│  │  │  KV Cache V    [q8_0 ▼]                              │  │    │
│  │  │  温度           [████████░░░░░░░░░] 0.7   (0-2)      │  │    │
│  │  │  ☑ 启用投机解码  类型: [draft-mtp ▼]                 │  │    │
│  │  │  ☑ 启用推理链    budget: [2048]                       │  │    │
│  │  │  [恢复自动优化]                    [应用并加载]        │  │    │
│  │  └──────────────────────────────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.4 Tab 3: 实时监控

```
┌──────────────────────────────────────────────────────────────────────┐
│  实时监控                                          刷新: 每 10 秒    │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │  GPU 利用率  │  │  GPU 功率   │  │  输出速度   │  │  延迟(ms)  │ │
│  │             │  │             │  │             │  │            │ │
│  │   56.6%    │  │   125W    │  │  104.2    │  │   45ms    │ │
│  │  显存使用    │  │  /250W    │  │  tok/s    │  │  首 token  │ │
│  │  18.5/32GB  │  │             │  │             │  │            │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
│                                                                      │
│  当前模型:  Qwen3.6-35B-MTP                                          │
│  运行状态:  ● 在线                                                    │
│                                                                      │
│  ── 显存占用趋势 (最近 5 分钟) ──                                     │
│  32GB ┤                                                              │
│       │                                                              │
│  24GB ┤          ╱╲                                                  │
│       │         ╱  ╲     ╱╲                                          │
│  18GB ┤    ╱╲  ╱    ╲___╱  ╲___                                      │
│       │   ╱  ╲╱                                                        │
│  12GB ┤                                                              │
│       ├────┬────┬────┬────┬────┬────                                 │
│       0    1m   2m   3m   4m   5m                                    │
│                                                                      │
│  ── 速度趋势 (最近 5 分钟) ──                                         │
│  120  ┤         ╱╲                                                   │
│       │        ╱  ╲      ╱╲                                          │
│   80  ┤   ╱╲  ╱    ╲___╱  ╲___                                       │
│       │  ╱  ╲╱                                                         │
│   40  ┤                                                              │
│       │                                                              │
│    0  └────┬────┬────┬────┬────┬────                                 │
│         0    1m   2m   3m   4m   5m                                  │
│                                                                      │
│  ── GPU 功率趋势 (最近 5 分钟) ──                                     │
│  250W ┤                                                              │
│       │     ╱╲     ╱╲                                                │
│  150W ┤    ╱  ╲___╱  ╲___                                            │
│       │   ╱                                                            │
│   50W ┤___                                                            │
│       └────┬────┬────┬────┬────┬────                                 │
│         0    1m   2m   3m   4m   5m                                  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.5 Tab 4: 系统配置

```
┌──────────────────────────────────────────────────────────────────────┐
│  系统配置                                                            │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ── 网络配置 ──                                                      │
│                                                                      │
│  本机 IP 地址:  [ 192.168.1.100           ]   (自动检测)            │
│  监听端口:     [ 51108                    ]   (默认: 8000)           │
│  DDNS 地址:    [ wannjnjun.eicp.net:51108 ]   (可选)                │
│                                                                      │
│  ── API 密钥管理 ──                                                  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Key ID     │ 名称        │ 状态  │ 请求数 │ Token  │ 最后使用│   │
│  │─────────────┼────────────┼──────┼────────┼────────┼─────────│   │
│  │ admin       │ 管理员      │ ✅ ∞ │    142 │  1.2M  │ 刚刚    │   │
│  │ friend01    │ 小王        │ ✅   │     23 │  45K   │ 2小时前 │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  [ 生成新密钥 ]  [刷新]  [导出统计]                                  │
│                                                                      │
│  ── 新密钥生成 ──                                                    │
│                                                                      │
│  自动生成:  a7f3b9c2e1d4f6a8b0c3e5d7f9a1b3c2                        │
│  (32 位十六进制, 随机生成)                                           │
│  [复制]  [保存并启用]                                                │
│                                                                      │
│  ── 日志配置 ──                                                      │
│                                                                      │
│  日志文件:   E:\llama-win-cuda-12.4-x64\gateway.log                 │
│  最大大小:   [ 10 ] MB × 5 个备份                                    │
│  日志级别:   [ INFO ▼ ]  (DEBUG / INFO / WARN / ERROR)               │
│  ☑ 记录请求用量   ☑ 记录 GPU 状态   ☐ 记录完整请求体                │
│                                                                      │
│  ── 启动选项 ──                                                      │
│                                                                      │
│  ☑ 开机自启动 (注册表)                                               │
│  ☑ 启动时自动加载上次使用的模型                                      │
│  ☑ 启动时自动清理残留 llama-server 进程                              │
│  ☐ 启动时最小化到系统托盘                                            │
│                                                                      │
│  ── 路径配置 ──                                                      │
│                                                                      │
│  llama-server:  [ E:\llama-win-cuda-12.4-x64\llama-server.exe ] [..]│
│  模型目录:      [ E:\models                                    ] [..]│
│  配置文件:      E:\llama-win-cuda-12.4-x64\miracle_config.json      │
│                                                                      │
│  [ 保存配置 ]  [ 恢复默认 ]                                          │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 五、错误处理机制

### 5.1 错误分类与处理策略

| 错误类型 | 触发条件 | 处理方式 | UI 反馈 |
|---------|---------|---------|---------|
| **GPU 检测失败** | nvidia-smi 不可用 / 驱动异常 | 跳过 GPU 监控，降级为 CPU 模式 | 弹窗: "⚠ GPU 检测失败，已降级为 CPU 模式。请检查驱动。" |
| **显存不足** | 模型加载后 OOM | 自动减小 context_length 重试 | 弹窗: "⚠ 显存不足，已自动调整上下文长度为 X" |
| **模型文件缺失** | GGUF 文件路径无效 | 阻止启动，提示选择文件 | 弹窗: "❌ 模型文件不存在: E:\models\xxx.gguf" |
| **端口被占用** | 51108 已被使用 | 自动尝试 +1 重试 (最多 5 次) | 状态栏: "端口 51108 被占用，已切换至 51109" |
| **llama-server 崩溃** | 子进程意外退出 | 自动重启 (最多 3 次) | 弹窗: "⚠ llama-server 崩溃，正在自动重启 (2/3)" |
| **模型切换超时** | wait_for_server_ready 超时 90s | 返回错误，保留旧模型 | 弹窗: "❌ 模型切换超时，已回滚至上一个模型" |
| **配置文件损坏** | JSON 解析失败 | 备份损坏文件，使用默认配置 | 弹窗: "⚠ 配置文件损坏，已备份并使用默认配置" |
| **网络绑定失败** | 0.0.0.0 绑定权限不足 | 降级为 127.0.0.1 | 状态栏: "仅本机访问模式 (127.0.0.1)" |

### 5.2 错误处理流程图

```
                    ┌──────────────┐
                    │  操作触发     │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  执行操作     │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  成功?       │
                    └──┬───────┬───┘
                  Yes   │       │  No
                        │       │
                ┌───────▼┐  ┌───▼────────────┐
                │ 正常流程 │  │ 错误分类        │
                └────────┘  └───┬────────────┘
                                │
                 ┌──────────────┼──────────────┐
                 │              │              │
          ┌──────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
          │ 可自动恢复   │ │ 需用户介入│ │ 严重错误    │
          │             │ │          │ │             │
          │ - 端口重试   │ │ - 文件缺失│ │ - 驱动损坏  │
          │ - 显存调整   │ │ - 权限不足│ │ - 硬件故障  │
          │ - 进程重启   │ │          │ │             │
          └──────┬──────┘ └────┬─────┘ └──────┬──────┘
                 │             │              │
          ┌──────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
          │ 自动修复     │ │ 弹窗提示  │ │ 弹窗 + 日志 │
          │ + 日志记录   │ │ + 阻止操作│ │ + 停止服务  │
          └─────────────┘ └──────────┘ └─────────────┘
```

### 5.3 GPU 检测失败弹窗示意

```
┌──────────────────────────────────────────────┐
│  ⚠ GPU 检测失败                         ×  │
├──────────────────────────────────────────────┤
│                                              │
│  无法检测到 GPU 或 NVIDIA 驱动异常。         │
│                                              │
│  可能原因:                                   │
│  • NVIDIA 驱动未安装或版本过低               │
│  • nvidia-smi 不在系统 PATH 中               │
│  • GPU 被其他进程独占                        │
│                                              │
│  当前状态: 已降级为 CPU 模式 (速度将大幅降低)│
│                                              │
│  建议:                                       │
│  1. 检查驱动版本 (需 581.00+)                │
│  2. 确认 nvidia-smi 可执行                   │
│  3. 重启后再试                               │
│                                              │
│  [查看日志]  [重试检测]  [继续 CPU 模式]    │
│                                              │
└──────────────────────────────────────────────┘
```

---

## 六、程序结构

### 6.1 文件组织

```
MiracleGateway/
├── main.py                     # 入口: 创建 QApplication + MainWindow
├── miracle_gateway.spec        # PyInstaller 打包配置
├── requirements.txt            # Python 依赖
│
├── core/                       # 核心服务层 (无 UI 依赖)
│   ├── __init__.py
│   ├── service_controller.py   # ServiceController: 子进程管理
│   ├── param_optimizer.py      # ParamOptimizer: 参数优化引擎
│   ├── monitor_engine.py       # MonitorEngine: GPU/速度监控
│   ├── http_proxy.py           # HttpProxyThread: API 转发
│   ├── config_manager.py       # ConfigManager: JSON 配置
│   ├── gguf_parser.py          # GGUF 元数据解析器
│   └── logger.py               # 日志配置 (RotatingFileHandler)
│
├── ui/                         # Qt UI 层
│   ├── __init__.py
│   ├── main_window.py          # MainWindow: 主窗口 + Tab 切换
│   ├── widgets/
│   │   ├── service_tab.py      # 服务控制 Tab
│   │   ├── model_tab.py        # 模型配置 Tab
│   │   ├── monitor_tab.py      # 实时监控 Tab
│   │   ├── config_tab.py       # 系统配置 Tab
│   │   ├── log_panel.py        # 底部日志面板
│   │   ├── chart_widget.py     # 趋势图 (matplotlib QtAgg)
│   │   └── status_indicator.py # 状态指示灯组件
│   ├── dialogs/
│   │   ├── error_dialog.py     # 错误弹窗
│   │   ├── add_model_dialog.py # 添加模型对话框
│   │   └── key_gen_dialog.py   # 密钥生成对话框
│   └── resources/
│       ├── icons/              # SVG 图标
│       └── style.qss           # Qt 样式表 (暗色主题)
│
├── config/
│   └── miracle_config.json     # 默认配置模板
│
└── tests/                      # 单元测试
    ├── test_param_optimizer.py
    ├── test_gguf_parser.py
    └── test_service_controller.py
```

### 6.2 核心类设计

```python
# core/service_controller.py
from PySide6.QtCore import QObject, Signal
import subprocess, time, threading

class ServiceController(QObject):
    """管理 llama-server 子进程生命周期"""
    
    service_started = Signal(str)      # model_name
    service_stopped = Signal()
    service_error = Signal(str)        # error_msg
    model_switched = Signal(str, str)  # old_model, new_model
    
    def __init__(self, config: ConfigManager):
        super().__init__()
        self.config = config
        self.process = None
        self.current_model = None
        self._start_time = 0
        
    def start(self, model_id: str, params: dict) -> bool:
        """启动 llama-server 子进程"""
        # 构建命令行参数
        # subprocess.Popen (CREATE_NO_WINDOW)
        # 等待健康检查通过
        # 发射 service_started 信号
        
    def stop(self) -> bool:
        """停止服务"""
        # taskkill /F /T /PID
        # 等待进程退出
        # 发射 service_stopped 信号
        
    def switch_model(self, model_id: str, params: dict) -> bool:
        """切换模型: 停止旧 → 启动新"""
        # 记录耗时
        # 发射 model_switched 信号
        
    def is_running(self) -> bool:
        """检查服务是否运行中"""
        
    def uptime(self) -> int:
        """返回运行时间 (秒)"""
```

```python
# core/param_optimizer.py
class ParamOptimizer:
    """参数优化引擎: 根据硬件和模型自动计算最优参数"""
    
    def __init__(self, gpu_vram_mb: int, cpu_cores: int):
        self.gpu_vram = gpu_vram_mb
        self.cpu_cores = cpu_cores
        
    def optimize(self, model_meta: dict, priority: str = "balanced",
                 overrides: dict = None) -> dict:
        """
        自动优化参数
        
        Args:
            model_meta: GGUF 元数据 (参数量, 量化位数, 层数, 架构)
            priority: "speed" | "balanced" | "quality"
            overrides: 用户手动覆盖的参数
            
        Returns:
            完整参数字典, 可直接拼接为 llama-server 命令行
        """
        
    def estimate_vram(self, model_meta: dict, params: dict) -> float:
        """预估显存占用 (GB)"""
        
    def to_args(self, params: dict) -> list[str]:
        """参数字典 → llama-server 命令行参数列表"""
```

```python
# core/monitor_engine.py
from PySide6.QtCore import QObject, Signal, QTimer, QThread

class MonitorEngine(QObject):
    """实时监控引擎"""
    
    gpu_updated = Signal(float, float, int, int)  # power_w, power_limit, vram_used, vram_total
    speed_updated = Signal(float)                   # tokens/sec
    log_appended = Signal(str, str)                 # message, level
    
    def __init__(self):
        super().__init__()
        self._gpu_timer = QTimer()
        self._gpu_timer.timeout.connect(self._probe_gpu)
        self._gpu_timer.setInterval(10000)  # 10 秒
        
    def start(self):
        self._gpu_timer.start()
        
    def stop(self):
        self._gpu_timer.stop()
        
    def _probe_gpu(self):
        """调用 nvidia-smi 采集 GPU 状态"""
        # subprocess.run(["nvidia-smi", "--query-gpu=..."])
        # 发射 gpu_updated 信号
        
    def record_speed(self, tokens: int):
        """记录输出速度 (从 HTTP 代理调用)"""
        # 滑动窗口计算
        # 发射 speed_updated 信号
```

### 6.3 配置文件结构 (miracle_config.json)

```json
{
    "version": "2.0",
    "network": {
        "listen_host": "0.0.0.0",
        "listen_port": 51108,
        "ddns_address": "wannjnjun.eicp.net:51108",
        "local_ip": "192.168.1.100"
    },
    "llama": {
        "server_path": "E:\\llama-win-cuda-12.4-x64\\llama-server.exe",
        "models_dir": "E:\\models",
        "internal_port": 8083,
        "internal_host": "127.0.0.1"
    },
    "api_keys": {
        "admin": {
            "name": "管理员",
            "enabled": true,
            "rate_limit_unlimited": true,
            "created": 1737500000
        }
    },
    "models": {
        "hermes-35b-mtp": {
            "display": "Qwen3.6-35B-MTP",
            "file": "Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf",
            "category": "chat",
            "context_length": 81920,
            "priority": "speed",
            "auto_params": true,
            "manual_overrides": {},
            "params": { "...": "optimized params" }
        }
    },
    "default_model": "hermes-35b-mtp",
    "logging": {
        "file": "gateway.log",
        "max_size_mb": 10,
        "backup_count": 5,
        "level": "INFO"
    },
    "startup": {
        "auto_start": false,
        "auto_load_last_model": true,
        "auto_cleanup_orphan": true,
        "minimize_to_tray": false
    },
    "gpu": {
        "vram_total_mb": 32768,
        "power_limit_w": 250,
        "safety_margin": 0.85
    }
}
```

---

## 七、技术可行性分析

### 7.1 关键技术点

| 技术点 | 方案 | 风险 | 缓解 |
|--------|------|------|------|
| **GGUF 元数据解析** | 读取文件头 magic + metadata 键值对 | 格式版本变化 | 使用 llama.cpp 自带 `llama-gguf` 工具或逆向解析 |
| **PySide6 打包** | PyInstaller --onefile | EXE 体积大 (~80MB) | 可接受，用户单次下载 |
| **nvidia-smi 调用** | subprocess + CSV 输出 | 驱动版本差异 | 解析失败时降级，不影响核心功能 |
| **SSE 流式转发** | Python http.server + urllib | 单线程阻塞 | 使用 ThreadingHTTPServer |
| **Qt 信号槽跨线程** | QThread + Signal/Slot | 线程安全 | Qt 自动排队连接，无需手动锁 |
| **趋势图渲染** | matplotlib + QtAgg 后端 | 性能开销 | 限制数据点数 (300 点)，10s 刷新 |

### 7.2 性能预算

| 指标 | 目标 | 实现方式 |
|------|------|---------|
| UI 响应延迟 | < 100ms | Qt 原生渲染，重操作放 QThread |
| GPU 监控开销 | < 50ms / 10s | nvidia-smi 子进程，非阻塞 |
| HTTP 代理转发延迟 | < 5ms | 内存中直接 pipe，无磁盘 IO |
| 内存占用 (EXE 自身) | < 200MB | PySide6 ~80MB + Python ~30MB |
| 启动时间 | < 3s (UI) | 延迟加载非关键模块 |

### 7.3 打包方案

```python
# miracle_gateway.spec
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[
        # 不打包 llama-server.exe，运行时从配置路径加载
    ],
    datas=[
        ('ui/resources/style.qss', 'ui/resources'),
        ('ui/resources/icons', 'ui/resources/icons'),
        ('config/miracle_config.json', 'config'),
    ],
    hiddenimports=['PySide6.QtCharts'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name='MiracleGateway',
    console=False,          # 无控制台窗口
    icon='ui/resources/icons/miracle.ico',
    onefile=True,
    upx=True,              # 压缩
)
```

### 7.4 依赖清单

```
# requirements.txt
PySide6>=6.6.0          # Qt6 UI 框架
matplotlib>=3.8.0       # 趋势图渲染
psutil>=5.9.0           # 进程/系统信息
pyinstaller>=6.0.0      # 打包工具 (仅开发时)
```

---

## 八、从现有代码迁移路径

### 8.1 迁移对照表

| 现有 (miracle_api.py) | 迁移到 | 变更说明 |
|----------------------|--------|---------|
| `MODELS` 字典 (硬编码) | `config/miracle_config.json` → `models` 节 | 可编辑、持久化 |
| `start_server()` / `kill_current_server()` | `core/service_controller.py` | 加 Qt 信号、错误处理 |
| `switch_model()` | `ServiceController.switch_model()` | 加切换耗时日志 |
| `monitor_loop()` (threading) | `core/monitor_engine.py` (QTimer) | 10s 周期不变 |
| `record_speed()` | `MonitorEngine.record_speed()` | 保留滑动窗口逻辑 |
| `MiracleHandler` (HTTP) | `core/http_proxy.py` (QThread) | 改用 ThreadingHTTPServer |
| `check_gpu_and_cleanup()` | `ServiceController._cleanup_gpu()` | 保留 wmic 逻辑 |
| `load_keys()` / `save_keys()` | `ConfigManager` 统一管理 | 合并到单一 JSON |
| `DASHBOARD_HTML` | `ui/widgets/dashboard.py` (Qt 原生) | 不再用 HTML |
| `cli_manage()` | UI 操作替代 | --add-key 等改为 UI 按钮 |
| `print()` 日志 | `core/logger.py` (RotatingFileHandler) | 结构化日志 |

### 8.2 迁移阶段

```
Phase 1: 核心层迁移 (1-2 天)
  ├── 提取 ServiceController from miracle_api.py
  ├── 提取 ParamOptimizer (新写)
  ├── 提取 MonitorEngine from monitor_loop()
  ├── 提取 HttpProxyThread from MiracleHandler
  └── 提取 ConfigManager from load_keys/save_keys

Phase 2: UI 层搭建 (2-3 天)
  ├── MainWindow + 4 Tab 框架
  ├── 服务控制 Tab (启停按钮 + 状态灯)
  ├── 模型配置 Tab (下拉 + 滑块 + 参数预览)
  ├── 实时监控 Tab (数值卡片 + 趋势图)
  └── 系统配置 Tab (网络 + 密钥 + 日志)

Phase 3: 集成与测试 (1-2 天)
  ├── 信号槽连接 (UI ↔ Core)
  ├── 错误处理弹窗
  ├── 端到端测试 (启动→切换模型→发请求→监控)
  └── PyInstaller 打包 EXE

Phase 4: 优化 (持续)
  ├── GGUF 自动探测集成
  ├── 参数优化算法调优
  ├── 趋势图性能优化
  └── 开机自启动
```

---

## 九、总结

本设计基于用户现有 `miracle_api.py` 的完整功能集，将其重构为 PySide6 原生 Windows EXE 应用。核心价值：

1. **零迁移成本** — 直接复用 llama-server.exe 二进制和现有 GGUF 模型
2. **参数自动优化** — 基于显存估算模型，自动计算 batch_size / context_length / KV 量化
3. **实时可视化** — GPU 功率/显存/速度/延迟 10 秒刷新，趋势图 5 分钟滚动
4. **错误自愈** — 端口冲突自动重试、进程崩溃自动重启、显存不足自动降级
5. **单文件分发** — PyInstaller 打包为单个 EXE，无需安装 Python 环境

预计开发周期: **5-7 天** (已有核心逻辑，主要是 UI 层和参数优化引擎)。
