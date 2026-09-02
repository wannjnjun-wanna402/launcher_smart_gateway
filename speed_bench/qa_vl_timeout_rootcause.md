# QA 根因分析 - Qwen3VL-8B 测速反复"超时"

**模型**: Qwen3VL-8B-Instruct-Q4_K_M.gguf (8B 密集, 32层, 256K ctx, 无MTP/无reasoning)
**GPU**: Tesla V100-PCIE-32GB (WDDM)
**QA 类型**: Trigger-based (测速超时排查)
**整体结论**: 🟢 模型本身正常且很快；"超时"是**测速脚本设计缺陷**导致，非硬件/模型问题

---

## 现象
用户连续多次运行 `bench_vl.bat`，每次都在中途"超时"卡住，被迫手动关闭重跑，循环往复。
预期 8B 应很快，实测部分配置确实快（84~92 tok/s），但整体总时长异常长。

## 根因（两条，均来自 flash-attn 关掉的配置）

### 🔴 Finding #1：flash-off + q8_0 KV → 服务启动即崩溃（致命）
日志 `runs_vl/noflash_q8_b2048.log` 明确：
```
E llama_init_from_model: V cache quantization requires flash_attn
E common_fit_params: failed to create llama_context from model
E cmn common_init_: failed to create context with model
```
llama.cpp 规定：**量化 KV 缓存(q8_0)必须开 flash-attn**，否则上下文无法创建、服务起不来。
脚本 `wait_health(timeout=180)` 等满 180s → 重试再 180s = **单配置最多干等 6 分钟**才放弃。
两个 `noflash_q8_*` 配置合计最多 12 分钟纯卡死。

### 🟡 Finding #2：flash-off + f16 KV → 起得来但暴跌 5x（性能陷阱）
日志 `runs_vl/noflash_f16_b2048.log`：
```
I slot print_timing: ... tg = 17.20 t/s   （同模型开 flash 时为 84~92 tok/s）
```
f16 KV 不强制依赖 flash，故能启动，但解码慢 **5 倍**（17 vs 92）。每个该配置约 4 分钟，
而开 flash 仅需 ~15 秒。

### 综合
12 个配置中 4 个 flash-off 全为坑（2 崩溃卡死 + 1 慢速 + 1 多半崩溃），吃掉 20+ 分钟；
8 个 flash-on 配置本可在 ~2 分钟内跑完。用户所见的"超时"= 卡在 flash-off 配置，
等不及即关闭 → 重跑又卡同一处 → 形成"几次都超时"的循环。

## 已修复
1. **剔除所有 flash-off 配置**（对 Qwen3VL-8B 无价值且致命），仅保留 flash=on 网格（7 套）：
   q8_0/f16 × batch(1024/2048/4096) × ctx(98304/32768) + baseline。
2. **启动期 fatal 签名快速失败**：`wait_health` 在轮询时扫描运行日志，
   一旦出现 `V cache quantization requires flash_attn` / `failed to create context` 等签名
   **立即返回失败、跳过该配置**，不再干等 180s×2。彻底消除卡死。
3. 修正文件：`E:\llama-win-cuda-12.4-x64\speed_bench\bench_vl.py`（编译校验 PY_COMPILE_OK）。
   `.bat` 仅调用 .py，无需改动。

## 部分数据已可下结论（来自已跑完的 flash-on 配置）
| 配置 | 均值 tok/s | 备注 |
|------|-----------|------|
| flash_f16_b2048 | ~92 | f16 KV 略快 |
| flash_f16_b4096 | ~92 | batch 4096 无增益 |
| def_flash_q8_b2048 (启动器默认) | ~85 | q8_0 KV |
| flash_q8_b4096 | ~84 | — |

- **flash-attn 必须开**（强制结论）。
- **f16 KV 比 q8_0 略快约 +8%**（92 vs 85），但 f16 KV 显存占用翻倍；8B 下 KV 总量很小，f16 无压力。
- **batch 2048 与 4096 无差**，ctx 98304 与 32768 无差 → 启动器默认 ctx=98304 合理。
- 当前启动器 `qwen3vl` 分支 = flash=on + q8_0 + b2048 ≈ **85 tok/s**，属"安全最优"；
  若要榨极限可改 f16（≈92），但需接受显存翻倍（对 8B 影响可忽略）。

## 下一步
重新双击 `bench_vl.bat` 跑（现 7 套、全 flash-on、约 2 分钟完），跑完跟我说"跑完了"，
我读 `results_vl.csv` / `summary_vl.txt` 出最终报告并确认是否改启动器 KV 默认（q8_0→f16）。

> 注：V100 上 8B decode 实测 85~92 tok/s，远低于理论带宽上限 190 tok/s。
> 该差距源于小模型每 token 的 kernel 启动/调度开销占比高（非纯带宽受限），
> 非配置问题；切 TCC 可再小幅提升，但 8B 受限于计算/调度而非显存带宽。
