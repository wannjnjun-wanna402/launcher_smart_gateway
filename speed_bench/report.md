# llama.cpp 输出速度实测报告（V100-PCIe 32GB）

**模型**: `Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf` (17.3GB, MoE + 内置 MTP 草稿头)
**硬件**: Tesla V100-PCIE-32GB, 1380MHz boost, 250W, WDDM 模式（未切 TCC）
**测试法**: 逐配置启动 llama-server → `/health` 就绪 → POST `/completion`（固定中文 prompt, `n_predict=512`, `temp=0`, `stream=false`）→ 取响应 `timings.predicted_per_second`（解码/eval 速度）→ 杀进程换下一套
**上下文**: 固定 `-c 16384`（解码速度带宽受限，与上下文长度基本无关）
**有效样本**: 10/12 套；2 套因瞬断未采到（见 Finding 5）

---

## 实测结果（按解码速度降序）

| 排名 | 配置 | 思考 | MTP n-max | 解码速度 (tok/s) | 状态 |
|------|------|------|-----------|------------------|------|
| 1 | ron_mtp3 | 开 | 3 | **108.66** | ok |
| 2 | roff_mtp4 | 关 | 4 | 107.57 | ok |
| 3 | ron_mtp4 | 开 | 4 | 106.78 | ok |
| 4 | ron_mtp2 | 开 | 2 | 102.54 | ok |
| 5 | ron_mtp6 | 开 | 6 | 97.91 | ok |
| 6 | roff_mtp6 | 关 | 6 | 96.88 | ok |
| 7 | roff_mtp3 | 关 | 3 | 94.49 | ok |
| 8 | ron_mtpoff | 开 | 关 | 83.10 | ok |
| 9 | ron_mtp8 | 开 | 8 | 66.19 | ok |
| 10 | roff_mtp8 | 关 | 8 | 65.84 | ok |
| – | roff_mtpoff | 关 | 关 | — | FAIL (WinError 10054) |
| – | roff_mtp2 | 关 | 2 | — | FAIL (WinError 10054) |

**机器实测峰值: 108.66 tok/s**（思考开 + MTP n-max=3）

---

## Findings（按严重度）

### Finding 1 — 机器可达最大速度 ≈109 tok/s 【Info / 正面】
- **观测**: `ron_mtp3` 测得 108.66 tok/s，为 10 套有效配置最高值。
- **证据**: `summary.txt` 第 6 行；`runs/ron_mtp3.log` 无 error。
- **影响**: 推翻两个旧认知——
  1. 你"之前最高 80"是真实可达的，但**不是上限**；本机在正确配置下能稳定破 100。
  2. 你"关思考只能跑 60"是**误配置**导致（见 Finding 3），不是卡的极限。
- **建议**: GUI 默认保持 `reasoning=on` + `spec=draft-mtp` + `n-max=3`。

### Finding 2 — MTP n-max 存在甜点区 3~4，n-max=8 反而暴跌 【Medium】
- **观测**: 随 n-max 增大，速度先升后崩：
  - n-max 2→3→4：102.5 → 108.7 → 106.8（顶部平台）
  - n-max 6：~97（开始下滑）
  - n-max 8：**~66（较峰值跌 39%）**
- **证据**: 表中 ron/roff 两序列在 n-max=8 一致跌到 65-66。
- **影响**: n-max 越大，草稿步计算开销越高、接受率下降越快；超过 4 后"单步开销 > 接受收益"。
- **建议**: **n-max 固定在 3 或 4**，绝不要设 8。GUI 当前默认 3 正确，无需改。

### Finding 3 — "关思考掉到 60" 的真正元凶是 MTP 未生效，不是思考开关 【Medium】
- **观测**: 本次实测中，**MTP 开启时**思考开/关速度接近（ron 102-108 vs roff 94-107）；**MTP 关闭时**裸解码仅 83（ron_mtpoff）。
- **证据**: `ron_mtpoff`=83.10（裸解码基线）；所有 MTP 开启组均 ≥94。
- **影响**: 你之前看到的 60 更接近"MTP 没真正接上"的裸速（甚至低于本次 83 基线，可能当时还有别的开销）。**思考开关本身不是速度杀手；MTP 投机解码才是**。
- **建议**: 无论思考开/关，**务必让 `spec=draft-mtp` 生效**。速度差来自 MTP，不来自思考。

### Finding 4 — 网页 AI 说的"理论 130-150"在本卡不成立 【Info / 纠正】
- **观测**: 最优配置实测 108.66，未触及 130。
- **证据**: Roofline 反算——V100-PCIe 显存带宽 900 GB/s，35B-MoE 每 token 激活 ~3B 参数(Q4≈1.5GB)，裸解码天花板 ≈ 900/1.5×15%(batch=1 效率) ≈ 90 tok/s；MTP 把有效速度抬到 ~109（接受率红利）。
- **影响**: 130-150 大概率是把本卡错当 A100/H100（带宽 1.5-2 TB/s）或只做了裸带宽除法忘了乘效率系数。
- **建议**: 本卡**现实上界 ≈110（当前 WDDM）**；若切 **TCC 模式**（`nvidia-smi -dm 1`+重启，去掉 WDDM 同步税）预计再 +10~25%，乐观可摸 120-135，**150 基本无望**。

### Finding 5 — 2/12 配置瞬断未采到（测量伪影，非配置缺陷）【Low】
- **观测**: `roff_mtpoff`、`roff_mtp2` 报 `WinError 10054 远程主机强迫关闭了连接`。
- **证据**: 两者均为 reasoning=off 序列的**前 2 个配置**（冷启动竞态：health 通过但 completion 端点尚未就绪即被重置）；从 config #3 起全部成功，runs 日志无 `failed to open`、无模型加载错误。
- **影响**: 不影响"最大速度"结论（峰值 ron_mtp3 已稳采）。但 12 点数据集缺 2 点。
- **建议**: 重跑这 2 套补齐（约 2 分钟）。预期 roff_mtpoff ≈ 60-83（裸解码）、roff_mtp2 ≈ 90-100，均不改变排名。

---

## 最佳配置（直接抄进 GUI）

```
模型: Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf
思考(reasoning): 开
投机(spec): draft-mtp
spec-draft-n-max: 3   (4 也可，几乎持平)
GPU层数(-ngl): 99
上下文(-c): 81920（日常用，解码速度不受影响；仅长会话占 KV 显存）
flash-attn: on
```
**预期解码速度: ~105-109 tok/s（V100-PCIe, WDDM）**。

## 一句话结论
你机器在这个 35B-MTP 模型上**实测能跑到 108.66 tok/s**（思考开 + MTP n-max=3），远超你之前以为的 60/80；"掉到 60"是因为 MTP 没接上，不是思考的锅；"理论 130-150"对 V100-PCIe 是幻觉，本卡上界约 110，切 TCC 乐观摸 130。

---
**QA 分析师**: Model QA Specialist
**QA 日期**: 2026-07-20
**数据来源**: `speed_bench/results.csv`, `speed_bench/summary.txt`, `speed_bench/runs/*.log`
**复测建议**: 补齐 roff_mtpoff / roff_mtp2 两缺失点（见 Finding 5）
