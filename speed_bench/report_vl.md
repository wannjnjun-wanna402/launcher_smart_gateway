# Model QA Report - Qwen3VL-8B 输出速度基准

## Executive Summary
**Model**: Qwen3VL-8B-Instruct-Q4_K_M.gguf (8B 密集, 32层, 无MTP / 无 reasoning)
**类型**: 视觉-语言模型 (VLM) decode 吞吐基准
**硬件**: Tesla V100-PCIE-32GB (WDDM), i5-8500, 32GB RAM
**QA 类型**: 触发式（修复超时后重测）
**Overall Opinion**: Sound（无致命问题；1 个配置性调优点可榨极限）

## 一、测速方法论（可复现）
- 脚本: `bench_vl.py`（已修复），输出 `results_vl.csv` / `summary_vl.txt` / `runs_vl/`
- 测量: `POST /completion` 的 `timings.predicted_per_second`（纯解码 tok/s）
- 任务: code / factual / json 三类，各 `n_predict=1024`（首任务热身不计）
- 配置: 7 套，**全部 flash-attn=enabled**（修复后删光 flash-off 致命配置）
- 每配置前 kill 残留 llama-server + `/health` 探活 + 启动期致命签名快速失败

## 二、实测结果（7 套全 flash-on）

| 排名 | 配置 | 均值 | 峰值 | vs 启动器默认 |
|------|------|------|------|---------------|
| 1 | flash_f16_b2048_c32768 | 92.39 | 92.84 | +8.38 |
| 2 | flash_f16_b2048 | 92.28 | 92.58 | +8.27 |
| 3 | flash_f16_b4096 | 92.26 | 92.73 | +8.25 |
| 4 | flash_q8_b1024 | 84.18 | 84.82 | +0.17 |
| 5 | **def_flash_q8_b2048**（启动器默认） | 84.01 | 84.43 | — (基准) |
| 6 | flash_q8_b4096 | 83.91 | 84.41 | -0.10 |
| 7 | flash_q8_b2048_c32768 | 83.85 | 84.40 | -0.16 |

（def_=启动器当前默认：flash=on + KV=q8_0 + b=2048 + c=98304）

### 关键证据（逐因子）
1. **flash-attn 必须开** — 修复前日志已证伪：关 flash + q8_0 KV 直接崩溃
   (`V cache quantization requires flash_attn` → `failed to create llama_context`)；关 flash + f16 KV 能起但暴跌至 17 tok/s（慢 5 倍）。**结论强约束，非调优项。**
2. **KV 类型：f16 > q8_0，稳定 +8.3 tok/s（+8.3%）**
   - f16 三套均值 92.26~92.39；q8_0 三套均值 83.85~84.18
   - 这是本模型唯一有显著速度的杠杆（batch/ctx 无差）
3. **batch 1024 / 2048 / 4096 无差**（同 KV 类型下差 <0.3 tok/s）
4. **ctx 98304 / 32768 无差**（KV 容量非瓶颈）

## 三、推荐配置（按证据）
**极限最优**（8B 显存宽松，f16 KV 可装）：
```
-ngl 99 --flash-attn enabled --cache-type-k f16 --cache-type-v f16
-c 98304 -b 2048 --ubatch-size 2048 -t 6 --parallel 1
--mmproj E:\models\mmproj-Qwen3VL-8B-Instruct-F16.gguf
```
预期：~92 tok/s（峰值 92.8），较当前默认 84 提 +8.3。

**安全默认**（维持现状，显存占用最低）：flash=on + q8_0 + b2048 = 84 tok/s。

### 显存测算（为什么 8B 敢用 f16）
- 权重 4.7GB + f16 KV(98304 ctx) ≈ 12GB + CUDA ≈ 17GB < 32GB ✅
- q8_0 KV 仅 ~6GB，整机 ~11GB
- ⚠️ 风险边界：若 ctx 拉到训练上限 256K，f16 KV ≈ 31GB → 会爆。当前启动器 ctx=98304，安全。**固化 f16 时需锁定 ctx≤98304**（或加注释警告）。

## 四、理论 vs 实测（回答"应能更快"质疑）
- Roofline 裸解码上限 = 900GB/s ÷ 4.7GB ≈ 191 tok/s
- 实测 92 tok/s（f16），效率 ~48%
- 落差根因：8B 小模型每 token 的 **kernel 调度/launch 固定开销占比高**（非带宽受限），与 MTP 模型不同；此开销不随 KV 类型/batch/ctx 改变，故这些杠杆只有 KV 类型有效（免反量化省 1 次 GPU 读）。非配置缺陷，属架构级天花板。

## 五、Finding 汇总
| # | Finding | 严重度 | 域 | 建议 |
|---|---------|--------|----|----|
| 1 | 启动器 qwen3vl 分支 KV=q8_0，非极限最优；f16 可 +8.3 tok/s 且 8B 显存安全 | Low | 性能 | 可选固化 f16（锁 ctx≤98304） |
| 2 | 原测速脚本含 flash-off 致命配置导致超时卡死 | Medium(已修) | 测试工程 | 已删 flash-off + 加快速失败；脚本 v2 已部署 |
| 3 | flash-attn 关 + q8_0 KV 必崩（llama.cpp 硬约束） | Info | 约束 | 任何 q8_0 KV 场景必须 flash=on |

## 六、下一步
- **若固化 f16**：改 `launcher_main.ps1` qwen3vl 分支 `cache-type-k/v q8_0 → f16` + GUI `qwen3vl` profile 同步 + 重建 exe。
- 是否固化见对话确认。
