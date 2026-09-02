# Live Run QA — Qwen3.6-35B-MTP (推荐配置实测)

**分析对象**: 用户贴出的 `launcher_main.ps1` 35B-MTP 推荐分支 + GUI exe 默认 `35b_mtp` 配置的一次真实运行日志
**模型**: `Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf` (MoE + MTP 投机, V100-PCIe-32GB, WDDM)
**配置**: `-ngl 99 --cache-type-k/v q8_0 -c 81920 -b 2048 -t 6 --parallel 1 --flash-attn enabled --reasoning on --reasoning-budget 2048 --spec-type draft-mtp --spec-draft-n-max 3 --spec-draft-n-min 1 --temp 0.7 --top-p 0.9 --top-k 20 --min-p 0.0 --repeat-penalty 1.05`
**QA 类型**: 触发式 (上线后首次真实运行复核)
**总体结论**: 配置功能正确、MTP 生效；发现 1 个真实速度漏点（显式 reasoning-budget 关闭 backend sampling），2 个非致命异常。

## Findings Summary
| #   | Finding                                                  | Severity        | Domain         | Remediation                                                       |
| --- | -------------------------------------------------------- | --------------- | -------------- | ----------------------------------------------------------------- |
| 1   | 显式 `--reasoning-budget 2048` 关闭 backend sampling → 每 token GPU→CPU logits 传输开销 | Medium (速度)   | Performance    | A/B 测速：reasoning-on 预算不限制 vs 2048；若不限更快则去掉 budget |
| 2   | 图像请求 → HTTP 500（纯文本模型，无 mmproj）             | Low (可用性)    | Integration    | 不要向此端点发图；视觉需求走 qwen3vl 模型                          |
| 3   | 支持 `--reasoning-preserve`（多轮保留思考）              | Info            | 可选增强       | 如需多轮连贯可加 `--reasoning-preserve`                            |
| 4   | MTP 生效、配置稳健（验证通过）                           | Info / Pass     | Performance    | 无需改动                                                          |
| 5   | 用户采样参数(--temp/top-p/...)仍生效                     | Info / 澄清     | 质量           | 无需改动                                                          |

## 详细分析

### 1. 速度漏点（Finding 1）—— 最关键
**观测**: 日志中每个 reasoning 任务起始都出现
`W common_sampler_init: backend sampling is not compatible with reasoning budget, disabling`
（tasks 682 / 1419 / 1651 均出现）。

**证据链**: 上游 llama.cpp b8786 发布说明明确指出——reasoning-budget sampler 一旦存在（即显式设了 budget，含 0/128/1024/2048 等），便会 **禁用 backend sampling**；backend sampling 让 GPU 直接选择 token、避免每 token 的 GPU→CPU logits 全量传输。Vulkan 后端因此报告过 ~30% 速度回退；CUDA 上惩罚较小但真实存在。

**实测吞吐（本日志，budget=2048 即 backend 关闭状态下）**:
| task | tok/s | draft 接受率 | mean draft len |
| ---- | ----- | ------------ | -------------- |
| 0    | 69.35 | 0.4436       | 2.33           |
| 682  | 60.99 | 0.3502       | 2.05           |
| 1419 | 92.57 | 0.8209       | 3.46           |
| 1651 | 81.64 | 0.6255       | 2.88           |

**影响**: 这解释了为何 bench_v2 峰值 120.9 卡在 130 理论天花板之下——整轮测速 backend sampling 始终关闭。去掉显式 budget 后，推理仍在（`--reasoning on` 默认 budget=-1 即不限制），backend sampling 恢复，V100 上有望回补一部分差距。

**建议**: 先跑 `bench_budget_ab` 定量（V100 上的真实 delta），再决定是否改启动器/exe 默认。

### 2. 图像 500（Finding 2）
`3.36 / 3.42` 两次 `got exception: image input is not supported - hint: if this is unexpected, you may need to provide the mmproj`。
本模型为纯文本 MTP 模型，无 mmproj。前端向此端点发了图片 → 500。不影响服务存活，但污染日志、浪费请求。**客户端侧问题，非服务端 bug**。视觉需求应路由到 qwen3vl 分支的视觉模型。

### 3. reasoning-preserve（Finding 3）
加载期提示 `chat template supports preserving reasoning, consider enabling it via --reasoning-preserve`。可选：多轮对话保留思考内容以提升连贯性。

### 4. MTP 验证（Finding 4）
draft 接受率 0.35–0.82，mean draft len 2.05–3.46，真实聊天吞吐 60–93 tok/s，与 bench_v2（最佳 104 均 / 118 峰 / 120.9 单峰）一致。**速度波动来自接受率（内容相关），非 bug**。注意：reasoning 开启在结构化内容上反而拉高接受率（task 1419 接受率 0.82 → 92.6 tok/s），印证"推荐 reasoning 开 + n-max=3"正确。

### 5. 采样参数澄清（Finding 5）
`backend sampling` 指的是 llama.cpp 内部 GPU 直选 token 的优化，**与用户的 `--temp/top-p/top-k/min-p/repeat-penalty` 无关**。这些参数走 sampler chain 仍生效。警告不会导致你的多样性/质量设置失效。

## 推荐动作
1. 跑 `bench_budget_ab.py/.bat`：reasoning-on + n-max=3，比 budget=2048 vs 不限制两档，量化 V100 上的速度差。
2. 若不限制更快（预期），将 `launcher_main.ps1` 35B-MTP 分支与 GUI exe 默认 `35b_mtp` 的 `--reasoning-budget 2048` 去掉（保留 `--reasoning on`）。接受副作用：难 prompt 思考可能更长（无硬上限），或仅在需要时设较大 budget。
3. 视觉请求走 qwen3vl；勿向本端点发图。

## 验证结论
配置可正确加载并提供服务，MTP 已交付预期加速。显式 reasoning-budget 是唯一可能解锁更多速度的项，待 A/B 实测确认后再改启动器。

---
**QA Analyst**: ModelQualityAssuranceExpert
**QA Date**: 2026-07-20
**Next Scheduled Review**: A/B 实测后
