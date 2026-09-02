# 推理吞吐基准报告 — Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf

> 范围说明：本轮为 **推理解码吞吐（tok/s）基准扫描**，非统计模型正确性审计（速度扫描不涉及校准/SHAP/PSI）。
> 以下「严重度」针对 **部署配置风险与提速空间**，所有结论均来自 bench_v2 实测数据（16/17 配置有效，1 个启动失败）。
> 硬件：Tesla V100-PCIE-32GB（驱动模式 bench 记录为 `unknown`，按工作记忆为 **WDDM**；下称 WDDM）。

## 执行概况
- 模型：35B MoE（激活约 3B），IQ4 紧凑量化，GGUF 内置 MTP 草稿头
- 配置网格：思考(on/off) × MTP n-max(None/2/3/4/5/6/8) + 3 个杠杆变体 = 17 套
- 每配置：杀残留进程 → 启动 → /health 探活 → 5 类任务(coherent/code/factual/json/reasoning) 各生成 1024 token（首任务 256 预热不计）→ 杀进程 → 下一套
- 测量量：`/completion` 返回的 `timings.predicted_per_second`（解码 tok/s）
- 数据：results_v2.csv（80 行明细）、summary_v2.txt、runs_v2/*.log

## 核心结论速览
| 指标 | 数值 | 对应配置 |
|---|---|---|
| 全局单任务峰值 | **120.91 tok/s** | ron_mtpn5 / json |
| 最佳配置（平均） | **104.60 tok/s**（峰值 118.53） | ron_mtpn3 |
| 无 MTP 裸速（基线） | 80.69 tok/s | ron_mtpoff |
| MTP 最大增益 | +29%（80.7 → 104.6） | n-max=3 |
| 最差可用配置 | 76.50 tok/s（事实类暴跌至 43） | ron_mtpn8 |

## 发现明细（证据 / 影响 / 建议）

### 发现 1 — 思考开/关对解码速度无实质影响  [严重度: Info / 澄清旧误判]
- **证据**：同 n-max 下 on vs off 平均差最大仅 **0.75 tok/s**（mtpn3: 104.60 vs 103.85），其余均 <0.5。
- **影响**：直接证伪「关思考掉速」的早期猜测。之前观察到的 60 vs 80 差异，根因是 **MTP 未生效**（裸速本就 ~80），与思考开关无关。
- **建议**：按使用习惯开/关思考即可，不必为速度取舍。

### 发现 2 — MTP n-max=3 是稳健甜点，n-max≥5 平均回落，n-max=8 崩塌  [严重度: Medium（调优机会）]
- **证据**：
  - 平均 tok/s 随 n-max：None 80.7 → 2 103.6 → **3 104.6** → 4 103.2 → 5 98.2 → 6 95.0 → 8 76.5。
  - 但**单任务**仍在高位：json 在 n-max=4 达 119.6、n-max=5 达 120.9。
  - 平均回落由**事实类(factual)任务单调崩塌**驱动：factual 随 n-max 2→8 = 88→80→72→62→57→**43**。
- **影响**：这是 MTP「过度草稿」惩罚——事实回忆类 token 草稿接受率低，n-max 越大白算越多。n-max=8 时 factual 直接掉到 43，把平均分拉穿。
- **建议**：日常稳健选 **n-max=3**（各任务均衡，平均最高）；若只追单任务峰值且能接受事实类变慢，可用 n-max=4~5（json 冲 120）。

### 发现 3 — 关闭 flash-attn 后 MTP 模型无法启动  [严重度: High（可用性）]
- **证据**：`ron_mtp3_flashoff` 启动后 /health 超时（即用户所说的「最后一轮超时」），runs_v2/ron_mtp3_flashoff.log 为启动失败。
- **影响**：该配置下服务起不来，整轮只有这 1 套无效；脚本已优雅跳过并继续，未中断。
- **建议**：**必须保持 `--flash-attn enabled`**。不要为「省一点」关掉它。

### 发现 4 — KV 上下文长度与 batch 均非瓶颈  [严重度: Info]
- **证据**：ctx 32768 vs 81920（ron_mtp4_c32768 103.61 vs ron_mtp4 103.18）差 <0.5；batch 4096 vs 2048（ron_mtp4_b4096 102.13 vs 103.18）差 <1.1。
- **影响**：纯解码带宽受限，调 KV/批处理无收益。
- **建议**：上下文按实际需求设（81920 足够），无需为提速缩 KV。

### 发现 5 — 「网页 AI 称理论 130–150」在 WDDM 下不可达  [严重度: Medium（预期管理）]
- **证据**：本机实测峰值 **120.9 tok/s**、最佳配置均速 **104.6 tok/s**。前期 Roofline 反算：V100-PCIe HBM2≈900GB/s，35B-MoE 激活 3B 解码上限 WDDM 约 110、TCC 乐观约 130。
- **影响**：130 是 **TCC 模式下的乐观上界**，不是 WDDM 日常值。当前已摸到的 104~121 已贴近 WDDM 天花板。
- **建议**：要真冲 130，唯一正路是切 **TCC 驱动模式**（`nvidia-smi -dm 1` + 重启），再重跑本基准，预期 +10~25% → 均速 120~130、峰值 130~145。

## 推荐部署配置（日常）
```
模型: Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf
--reasoning on  --reasoning-budget 2048
--spec-type draft-mtp  --spec-draft-n-max 3  --spec-draft-n-min 1
--flash-attn enabled  -c 81920  -b 2048  -ngl 99  -t 6
预期: ~104~105 tok/s 均速, 峰值 ~118 (json 类可达 120)
```

## 下一步
1. 若接受切 TCC：改驱动模式后重跑 `bench_v2.bat`，验证能否破 130。
2. 27B 密集模型本轮已单独建制测试（见 `bench_27b.py` / `bench_27b.bat`）——其目标前向更贵、MTP 草稿相对更便宜，甜点 n-max 预计高于本 MoE 的 3，待实测确认。

---
**QA 分析**：ModelQualityAssuranceExpert（推理吞吐基准专项）
**数据日期**：2026-07-20  | **来源**：E:\llama-win-cuda-12.4-x64\speed_bench\results_v2.csv
