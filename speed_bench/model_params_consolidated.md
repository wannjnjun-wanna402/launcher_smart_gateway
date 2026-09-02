# 三模型部署参数合并报告（V100-PCIe-32GB / WDDM）

> 数据来源：直接从 `launcher_main.ps1` 源码核对（带行号），非凭记忆。
> 所有参数均已实测固化（bench_v2 / bench_27b / bench_vl 测速结论）。
> 生成时间：2026-07-20 22:14
>
> ## ⚠️ 关键更新：--jinja 工具调用修复
> **问题**：启动器 `.ps1` 所有标「工具调用」的自定义分支原本缺 `--jinja` → llama-server 忽略 `tools` 参数，模型输出原始 JSON 字符串而非执行工具。
> **修复**：给 6 个分支（35B-MTP、35B uncensored/aggressive、27B-MTP-IQ4、27B-MTP、27B 无后缀、8B-VL）的 `customArgs` 各加 `"--jinja"`（源码 732/762/793/823/853/881 行）。GUI exe 本来就有 `--jinja`（默认勾选），无需重建。
> **原文件备份**：`launcher_main.ps1.bak20260720d`。

| 模型 | 类型 | 最佳配置 | 实测峰值 tok/s |
|------|------|----------|---------------|
| Qwen3.6-35B-MTP | MoE + MTP投机 | reasoning on + n-max=3 | 120.9 |
| Qwen3.6-27B-MTP-IQ4 | 密集 + MTP投机 | reasoning on + n-max=4 | 69.3 |
| Qwen3VL-8B-Instruct | 密集·视觉 | flash + f16 KV | 92.4 |

---

## 1. Qwen3.6-35B-MTP（MoE + MTP投机）

**匹配分支**：`qwen3\.6-35b.*mtp`（launcher 766-792 行）
**模型文件**：`E:\models\Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf`
**峰值**：120.9 tok/s（WDDM）；切 TCC 乐观 130+

```
-ngl 99
--cache-type-k q8_0 --cache-type-v q8_0
-c 81920 -b 2048 -t 6
--parallel 1 --flash-attn enabled
--temp 0.7 --top-p 0.9 --top-k 20 --min-p 0.0 --repeat-penalty 1.05
--reasoning on --reasoning-budget 2048
--spec-type draft-mtp --spec-draft-n-max 3 --spec-draft-n-min 1
--alias Qwen3.6-35B-MTP
```

**完整一键命令**：
```
E:\llama-win-cuda-12.4-x64\llama-server.exe -m E:\models\Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf -ngl 99 --cache-type-k q8_0 --cache-type-v q8_0 -c 81920 -b 2048 -t 6 --parallel 1 --flash-attn enabled --jinja --temp 0.7 --top-p 0.9 --top-k 20 --min-p 0.0 --repeat-penalty 1.05 --reasoning on --reasoning-budget 2048 --spec-type draft-mtp --spec-draft-n-max 3 --spec-draft-n-min 1
```

**注**：纯文本模型，无 mmproj。n-max=3 为 MTP 甜点（≥5 接受率暴跌）；reasoning 开/关速度无差；`--reasoning-budget` 是否保留已 A/B 实测确认（B 不更快，维持）。

---

## 2. Qwen3.6-27B-MTP-IQ4（密集 + MTP投机）

**匹配分支**：`qwen3\.6-27b.*iq4_xs`（launcher 796-822 行）
**模型文件**：`E:\models\Qwen3.6-27B-MTP-IQ4_XS-Q8nextn.gguf`
**峰值**：69.3 tok/s（WDDM）；切 TCC 乐观 ~80+
**注**：同源 `qwen3\.6-27b.*mtp` 分支参数一致，仅 alias 为 `Qwen3.6-27B-MTP`

```
-ngl 99
--cache-type-k q8_0 --cache-type-v q8_0
-c 98304 -b 2048 -t 6
--parallel 1 --flash-attn enabled
--reasoning on --reasoning-budget 2048
--spec-type draft-mtp --spec-draft-n-max 4 --spec-draft-n-min 1
--temp 0.3 --top-p 0.9 --top-k 20 --min-p 0.0 --repeat-penalty 1.05
--alias Qwen3.6-27B-MTP-IQ4
```

**完整一键命令**：
```
E:\llama-win-cuda-12.4-x64\llama-server.exe -m E:\models\Qwen3.6-27B-MTP-IQ4_XS-Q8nextn.gguf -ngl 99 --cache-type-k q8_0 --cache-type-v q8_0 -c 98304 -b 2048 -t 6 --parallel 1 --flash-attn enabled --jinja --reasoning on --reasoning-budget 2048 --spec-type draft-mtp --spec-draft-n-max 4 --spec-draft-n-min 1 --temp 0.3 --top-p 0.9 --top-k 20 --min-p 0.0 --repeat-penalty 1.05
```

**注**：密集模型裸速仅 ~34 tok/s，靠 MTP 拉到 69.3（增益 +103%）；n-max=4 甜点（比 35B 高 1 档）；无 mmproj（纯文本）。

---

## 3. Qwen3VL-8B-Instruct（密集·视觉）

**匹配分支**：`qwen3vl`（launcher 711-737 行）
**模型文件**：`E:\models\Qwen3VL-8B-Instruct-Q4_K_M.gguf`
**mmproj**：`E:\models\mmproj-Qwen3VL-8B-Instruct-F16.gguf`（A 方案已显式挂载，735 行）
**峰值**：92.4 tok/s（WDDM）；小模型理论 190，受 kernel 开销限制
⚠️ **风险边界**：f16 KV 在 ctx=98304 占 ~12GB（整机 ~17GB，安全）；若手动把 ctx 拉到 256K，f16 KV≈31GB → 爆显存，需回退 q8_0。

```
-ngl 99
--cache-type-k f16 --cache-type-v f16
-c 98304 -b 2048 --ubatch-size 2048 -t 6
--parallel 1 --flash-attn enabled
--temp 0.7 --top-p 0.9 --top-k 20 --min-p 0.0 --repeat-penalty 1.05
--alias Qwen3VL-8B
（mmproj 由启动器自动追加：--mmproj E:\models\mmproj-Qwen3VL-8B-Instruct-F16.gguf）
```

**完整一键命令**：
```
E:\llama-win-cuda-12.4-x64\llama-server.exe -m E:\models\Qwen3VL-8B-Instruct-Q4_K_M.gguf -ngl 99 --cache-type-k f16 --cache-type-v f16 -c 98304 -b 2048 --ubatch-size 2048 -t 6 --parallel 1 --flash-attn enabled --jinja --temp 0.7 --top-p 0.9 --top-k 20 --min-p 0.0 --repeat-penalty 1.05 --mmproj E:\models\mmproj-Qwen3VL-8B-Instruct-F16.gguf
```

**注**：无 MTP、无 reasoning（VL 无思考）；f16 KV 比 q8_0 快 +8.3 tok/s；batch/ctx 无差；flash-attn 必须开（关+f16 暴跌到 17，关+q8_0 直接崩溃）。

---

## 4. 固化状态汇总

| 项 | 启动器 .ps1 | GUI exe | 状态 |
|----|------------|---------|------|
| 35B-MTP 分支 | ✅ n-max=3 + reasoning on | ✅ 35b_mtp 默认 | 已部署 |
| 27B-MTP-XS 分支 | ✅ n-max=4 + reasoning on | ✅ 27b_mtp_xs 默认 | 已部署 |
| 8B-VL 分支 KV | ✅ q8_0→f16 | ✅ cache_k/v=f16 | 已部署 |
| 8B-VL mmproj | ✅ A 方案(735行) | ✅ 原生处理 | 已修复 |
| --jinja 工具调用 | ✅ 6 分支已加(732/762/793/823/853/881) | ✅ 默认勾选 | 已修复 |

**备份文件**：`launcher_main.ps1.bak20260720a`（mmproj 修复前）；`launcher_main.ps1.bak20260720d`（jinja 修复前）；桌面 exe 备份 `.bak20260720` / `.bak20260720b` / `.bak20260720c`。

## 5. 统一动作建议
- 想破各自峰值 → 切 **TCC**（`nvidia-smi -dm 1` + 重启）后重跑 bench 脚本。
- 8B 视觉端点现在能正常收图（mmproj 已挂）。
- 35B / 27B 为纯文本，无需 mmproj。
