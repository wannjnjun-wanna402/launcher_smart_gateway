# 三模型部署参数核对报告（已固化于 `launcher_main.ps1`）

> QA 类型：部署参数核对（非统计模型审计）｜核对日期：2026-07-20
> 数据来源：直接读取 `launcher_main.ps1` 三个分支确凿值（行号已标注），非凭记忆
> 配套基准报告：report_35b.md / report_27b.md / report_vl.md

## 一、概览

| 模型 | 类型 | 实测峰值 | 固化分支（行号） | alias |
|------|------|----------|------------------|-------|
| Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf | MoE + MTP | 120.9 t/s | `qwen3\.6-35b.*mtp` (764-792) | Qwen3.6-35B-MTP |
| Qwen3.6-27B-MTP-IQ4_XS-Q8nextn.gguf | 密集 + MTP | 69.3 t/s | `qwen3\.6-27b.*iq4_xs` (794-822) | Qwen3.6-27B-MTP-IQ4 |
| Qwen3VL-8B-Instruct-Q4_K_M.gguf | 密集·视觉 | 92.4 t/s | `qwen3vl` (711-736) | Qwen3VL-8B |

> 注：所有模型启动器自动追加 `--port 8081 --host 127.0.0.1`（见 1496/1623 行）。下文给出模型内 `customArgs` 原文 + 需手动确认项。

---

## 二、各模型完整参数

### 1. 35B-MTP（MoE + MTP 投机）— 峰值 120.9 t/s
**分支**：`qwen3\.6-35b.*mtp`（764-792 行）
```
-ngl 99 --cache-type-k q8_0 --cache-type-v q8_0 -c 81920 -b 2048 -t 6
--parallel 1 --flash-attn enabled
--temp 0.7 --top-p 0.9 --top-k 20 --min-p 0.0 --repeat-penalty 1.05
--reasoning on --reasoning-budget 2048
--spec-type draft-mtp --spec-draft-n-max 3 --spec-draft-n-min 1
--alias Qwen3.6-35B-MTP
```
- 纯文本模型，**无 mmproj**（正确）。
- reasoning 开 + n-max=3 为 bench_v2 实测最佳（均速 ~104 / 峰值 ~118）。
- `--reasoning-budget 2048` 经 A/B 实测确认：与不设 budget 速度无差（-0.1%），**保留无害**。

### 2. 27B-MTP-IQ4（密集 + MTP 投机）— 峰值 69.3 t/s
**分支**：`qwen3\.6-27b.*iq4_xs`（794-822 行，你的 IQ4_XS 模型走此）
```
-ngl 99 --cache-type-k q8_0 --cache-type-v q8_0 -c 98304 -b 2048 -t 6
--parallel 1 --flash-attn enabled
--reasoning on --reasoning-budget 2048
--spec-type draft-mtp --spec-draft-n-max 4 --spec-draft-n-min 1
--temp 0.3 --top-p 0.9 --top-k 20 --min-p 0.0 --repeat-penalty 1.05
--alias Qwen3.6-27B-MTP-IQ4
```
- 纯文本模型，无 mmproj（正确）。
- n-max=4（非 35B 的 3）+ reasoning on 为 bench_27b 实测最佳（均值 ~60.6 / 峰值 69.3）。
- 同源还有 `qwen3\.6-27b.*mtp` 分支（823-850 行），参数完全一致仅 alias 为 `Qwen3.6-27B-MTP`。

### 3. 8B-VL（密集·视觉）— 峰值 92.4 t/s
**分支**：`qwen3vl`（711-736 行）
```
-ngl 99 --cache-type-k f16 --cache-type-v f16 -c 98304 -b 2048 --ubatch-size 2048 -t 6
--parallel 1 --flash-attn enabled
--temp 0.7 --top-p 0.9 --top-k 20 --min-p 0.0 --repeat-penalty 1.05
--alias Qwen3VL-8B
```
- **⚠️ mmproj 缺口（见 Finding F1）**：上线命令**必须**补 `--mmproj E:\models\mmproj-Qwen3VL-8B-Instruct-F16.gguf`，但启动器当前分支不会自动挂。
- f16 KV 为 bench_vl 实测最佳（+8.3 t/s vs q8_0）。
- flash-attn 必须开（关+ q8_0 必崩、关+ f16 暴跌至 17）。

---

## 三、Finding

### F1 — 8B-VL 启动器分支漏挂 mmproj（Medium）
- **观察**：`qwen3vl` 分支（711-736 行）的 `customArgs` 未含 `--mmproj`；且启动器 mmproj 自动挂载逻辑（663-665 行）仅匹配 `qwen3\.6-(35b|27b).*(uncensored|aggressive)`，`qwen3vl` 不匹配 → 该模型 `$mmproj = $null`。
- **证据**：第1497 / 1624 行 `if ($Model.MMProj) { $srvArgs += "--mmproj", $Model.MMProj }` 因 `$Model.MMProj` 为空**不触发** → 用启动器选 8B 启动时无 mmproj。
- **影响**：模型能跑（纯文本 decode 正常，92 t/s 基准有效），但**视觉能力完全缺失**。若向该端点发送图片，会返回 `image input is not supported`（同之前 35B 日志现象）。bench_vl.py 手工测速时显式挂了 mmproj，故速度数据无误；但启动器一键部署的 8B 端点不支持看图。
- **修正前误述**：之前 21:42 报告称"启动器现在自动生成的就是带 mmproj 的这版命令" —— **该陈述错误**，特在此更正。
- **推荐修复**（待你确认后实施，不动原文件、按铁律备份）：
  - 方案 A（最小改）：在 `qwen3vl` 分支内直接 `$mmproj = Join-Path $MODELS_DIR "mmproj-Qwen3VL-8B-Instruct-F16.gguf"`（文件已确认存在）。
  - 方案 B（通用化）：把 663-665 行的视觉识别正则扩展为也匹配 `qwen3vl`，使其进入 668-696 的 mmproj 前缀匹配（兜底逻辑 681-692 已能按 `Qwen3VL-8B` 前缀找到对应 mmproj）。
- **严重度**：Medium（视觉功能缺失，非崩溃、非速度退化；但用户若以为启动器支持视觉会踩坑）。

---

## 四、接入信息（三个模型一致）
- Base URL：`http://127.0.0.1:8081`（前端接时不带 `/v1`，前端自动补）
- API Key：`llamacpp`
- 模型名查询：`http://127.0.0.1:8081/v1/models`

---
**QA 核对**：Model QA Specialist｜**日期**：2026-07-20｜**结论**：35B/27B 参数完整且正确；8B-VL 速度参数正确但**缺 mmproj 挂载（Finding F1，待修复）**。
