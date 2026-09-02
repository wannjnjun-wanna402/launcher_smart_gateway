# QA 修正报告 — reasoning-budget 对 V100 吞吐的真实影响

**模型**: Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf (MoE + MTP 投机, n-max=3)
**硬件**: Tesla V100-PCIe-32GB, WDDM, i5-8500
**测试**: bench_budget_ab (A=预算2048 / B=不设预算), 各 4 任务 × 512 tokens + 1 预热
**日期**: 2026-07-20
**关联**: 修正 `qa_live_35b_mtp.md` 中 "🔴 速度漏点" 那条 finding

---

## 实测数据

| 配置 | code | factual | json | reasoning | **均值** |
|------|------|---------|------|-----------|----------|
| A (budget=2048, backend OFF) | 87.23 | 97.43 | 113.55 | 99.91 | **99.53** |
| B (budget 不设, backend ON)  | 104.57 | 87.20 | 109.49 | 96.51 | **99.44** |
| Δ (B−A) | **+17.34** | **−10.23** | **−4.06** | **−3.40** | **−0.09 (−0.1%)** |

## 结论

**B 不比 A 快，均值差 −0.09 tok/s (−0.1%)，落在测量噪声内。保留 budget=2048 现状，不改动启动器 / exe 默认。**

各任务波动 ±10~17 tok/s，远大于两配置间的 −0.1% 均值差。说明**任务间 MTP 接受率差异 >> backend sampling 开关带来的任何开销**。

## 修正之前的判断（自我纠错）

我在 `qa_live_35b_mtp.md` 把 `backend sampling is not compatible with reasoning budget, disabling` 定性为 **🔴 真实速度漏点**，推断"去掉 budget 能救回 backend sampling、把吞吐再抬一截"。

**实测推翻了该推断**：
- 上游 b8786 发布说明的 ~30% 回退是 **Vulkan 后端**报告的，不能直接套到 **CUDA/V100**。
- 本机 A/B 实测：backend 关 vs 开，吞吐无显著差异。
- 该 warning 在本场景应降级为 **Info（无害观察）**——它真实存在、但不构成速度漏点。

**QA 教训**：跨后端（Vulkan vs CUDA）、跨硬件（消费卡 vs 数据中心卡）的性能推断，必须用本机实测验证后才能定性为 finding。本次先发了推断、后被数据打脸，是流程上的失误，已在此修正。

## 维持不变的部署参数 (launcher_main.ps1 35B-MTP 分支 + exe 35b_mtp 默认)

```
-ngl 99 --cache-type-k q8_0 --cache-type-v q8_0 -c 81920 -b 2048 -t 6
--parallel 1 --flash-attn enabled
--reasoning on --reasoning-budget 2048
--spec-type draft-mtp --spec-draft-n-max 3 --spec-draft-n-min 1
--alias Qwen3.6-35B-MTP
```

## 仍有效的观察 (Info 级)

- `--reasoning-preserve` 可用：多轮对话想保留思考内容可加，可选增强。
- 图像 500：纯文本模型无 mmproj，前端发图会 500，与速度无关，别给此端点发图。
- 想破 130：唯一正路仍是切 TCC (`nvidia-smi -dm 1` + 重启) 后重跑。

---
**QA Analyst**: Model QA Specialist
**QA Date**: 2026-07-20
**Next Review**: 切 TCC 重测时 / 27B 测速完成时
