# 27B 密集模型输出速度测速报告

**模型**: Qwen3.6-27B-MTP-IQ4_XS-Q8nextn.gguf
**类型**: 密集混合模型（非 MoE），65 层，含 SSM(Mamba) 递归层，训练上下文 256K，内置 Q8 nextn MTP 草稿头，权重 15.4GB
**硬件**: Tesla V100-PCIE-32GB, WDDM, i5-8500
**测试**: bench_27b (18 套配置), n_predict=1024, 5 类任务(code/factual/json/reasoning + 1 预热)
**日期**: 2026-07-20
**测量**: `/completion` 返回 `timings.predicted_per_second` (解码 tok/s)

---

## 一、核心结论

| 指标 | 值 | 说明 |
|------|-----|------|
| **裸速 (MTP off)** | **34.0 tok/s** | 密集模型每 token 跑完整 27B 前向的硬代价 |
| **MTP 增益** | **34 → 69（≈2.0×）** | 投机解码对密集模型增益远大于对 MoE(35B 仅 +30%) |
| **最佳 n-max** | **4** | 均值 60.6 / 峰值 69.29，甜点峰值 |
| **冲刺峰值** | **69.29 tok/s** | ron_mtp4_c32768（reasoning 任务，n-max=4） |
| **reasoning 开关** | **无速度影响** | ron vs roff 同 n-max 几乎并排（印证 35B 结论） |
| **ctx 影响** | **无** | n-max=4 在 81920/98304/32768 下均值 60.58/60.69/60.84，KV 非瓶颈 |

## 二、n-max 甜点曲线（均值 tok/s）

```
n-max   均值    峰值    评估
off     34.1    34.4   基准（裸速）
2       56.1    59.1   可用
3       57.6    63.9   不错
4       60.6    69.3   ★ 最佳（甜点）
5       58.9    66.6   略降
6       55.3    62.9   开始崩（factual 掉 34）
8       43.9    50.9   崩（factual 掉 25）
10      42.6    50.4   崩
```

**规律**：n-max 2→4 单调上升（56→60.6），4 达峰，5 起回落，≥6 显著崩。与 35B（甜点 3）相比，27B 密集模型因目标前向贵、草稿相对便宜，**甜点略高 1 档（4 vs 3）**，但高 n-max 同样不耐受。

## 三、证据表（节选，全部 18 套见 results_27b.csv）

| 配置 | code | factual | json | reasoning | 均值 |
|------|------|---------|------|-----------|------|
| ron_mtpn4 (★推荐) | 65.98 | 43.20 | 64.32 | 68.84 | **60.58** |
| ron_mtp4_c32768 | 66.24 | 43.23 | 64.60 | 69.29 | **60.84** |
| ron_mtpn5 | 66.51 | 39.79 | 63.88 | 65.71 | 58.97 |
| ron_mtpn3 | 63.24 | 44.29 | 60.77 | 62.22 | 57.63 |
| ron_mtpn2 (=启动器现默认) | 58.94 | 49.04 | 57.42 | 59.14 | 56.13 |
| ron_mtpoff (裸速) | 34.04 | 34.35 | 34.04 | 34.12 | 34.14 |

> ron_=思考开 / roff_=思考关。rnative = 启动器当前默认（思考关 + n-max=2）。

## 四、与 35B-MTP 对比

| 维度 | 35B (MoE) | 27B (密集) |
|------|-----------|-----------|
| 裸速 | ~80 tok/s | ~34 tok/s |
| MTP 增益 | +30% (80→104) | **+103% (34→69)** |
| 最佳 n-max | 3 | **4** |
| 冲刺峰值 | 120.9 tok/s | **69.29 tok/s** |
| 绝对速度更低原因 | 激活仅 3B | 每 token 跑完整 27B 前向（权重 15.4GB） |

**结论**：27B 绝对速度低于 35B（权重更大），但 MTP 相对增益翻倍——密集模型更依赖投机解码。两者甜点仅差 1 档（4 vs 3）。

## 五、启动器现状偏差（Finding: Medium）

`launcher_main.ps1` 当前 27B 两个 MTP 分支**均非最优**：

1. **`iq4_xs` 分支**（line 794，你的模型 `Qwen3.6-27B-MTP-IQ4_XS-Q8nextn.gguf` 走此分支）：`--spec-draft-n-max 2` + 无 reasoning → 实测 n-max=4 比它快 **~4.5 均值 / ~10 峰值 tok/s**（56→60.6 / 59→69），约 8–15% 可避免损失。
2. **`mtp` 分支**（line 821）：`--spec-draft-n-max 8` + n-min=4 → 实测 n-max=8 是**最差配置之一**（均值 43.8，factual 掉到 25），应降到 4。

**推荐修正**（待用户确认后写入）：
- `iq4_xs` 分支：`n-max 2 → 4`，`n-min 1 保持`，加 `--reasoning on --reasoning-budget 2048`（无速度代价，保留思考能力）
- `mtp` 分支：`n-max 8 → 4`，`n-min 4 → 1`

## 六、推荐部署命令（27B 最佳配置）

```
E:\llama-win-cuda-12.4-x64\llama-server.exe ^
  -m E:\models\Qwen3.6-27B-MTP-IQ4_XS-Q8nextn.gguf ^
  -ngl 99 --cache-type-k q8_0 --cache-type-v q8_0 ^
  -c 81920 -b 2048 -t 6 --parallel 1 --flash-attn enabled ^
  --reasoning on --reasoning-budget 2048 ^
  --spec-type draft-mtp --spec-draft-n-max 4 --spec-draft-n-min 1
```

**预期**：均速 ~60.6、峰值 ~69.3 tok/s（WDDM）。想再冲须切 TCC（`nvidia-smi -dm 1` + 重启）。

---
**QA Analyst**: Model QA Specialist
**QA Date**: 2026-07-20
**Next Review**: 切 TCC 重测 / 用户确认是否固化进启动器
