# 对比基准计划：Qwen3.8-27B-NVFP4-MTP vs Qwen3.6-27B-Fable-711-MTP

> 生成时间：2026-08-21 ｜ 机器：Windows 11 24H2 + Tesla V100-PCIE-32GB ｜ 运行时：llama.cpp b10545（CUDA 12.4，目录 `E:\llama-win-cuda-12.4-x64`）
> 本文档自包含，可交给其他本地模型/执行者照做。

## 1. 背景与需求

- 用户已确认：Qwen3.8-27B-NVFP4（无 Blackwell 加速的 V100 上靠反量化跑）实测综合得分**略高于** Q6_K，且文件小 4GB，Q6_K 已删除。
- 本次需求：在相同条件下对比 **NVFP4** 与 **Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP（Q5_K_M）**，得出"日常主力用哪个"的结论。
- ASR/视频转写已明确不做，不在本计划范围内。

## 2. 任务目标

1. 速度：两模型在相同参数下的推理速度（tok/s），含 MTP 投机解码生效情况。
2. 质量：现有评测体系下的 accuracy / hallucination / toolScore / ragScore（与 models_db.json 同口径）。
3. 显存：两模型在目标上下文下的实际显存占用峰值。
4. 产出结论：给出"主力模型 + 备选模型"建议，并回填 models_db.json。

## 3. 最终交付物清单（验收标准）

| # | 交付物 | 格式/位置 | 完成判据 |
|---|--------|-----------|----------|
| 1 | 速度基准原始结果 | `bench_results/NVFP4_vs_Fable_YYYYMMDD_HHMMSS_speed.txt` | 两模型都有完整输出 |
| 2 | 质量评测结果 | `eval_results/..._NVFP4_*.json`、`..._Fable_*.json`（带时间戳，禁止同名覆盖） | 各含 accuracy/halluc/toolScore/ragScore 四项数值 |
| 3 | 显存记录 | 写入对比表（两模型加载后 `nvidia-smi` 读数） | 各一条 |
| 4 | 对比总表 | 本文件末尾第 8 节表格填完 | 所有格子有值或标注 N/A+原因 |
| 5 | models_db.json 回填 | NVFP4 条目的 speed/halluc/accuracy 更新为本次实测值，note 去掉"待填入"字样 | JSON 合法（UTF-8 无 BOM） |
| 6 | 结论一段话 | 本文件第 9 节 | 明确推荐哪个做主力及理由 |

## 4. ⚠️ 阻塞项（执行前必须先解决）

**`E:\models` 目录下没有 Fable 模型文件。** 已确认该目录现有 gguf：
Gemma-4-E4B、Ornith-1.0-35B(±mmproj)、PaddleOCR-VL-1.6(±mmproj)、Qwen3-0.6B、Qwen3.5-0.8B/4B、Qwen3VL-8B、**Qwen3.8-27B-NVFP4-MTP-MEDIUM**、Qwen3.8-27B-Uncensored-Q5_K_P、Qwen3.8-27B-FastMTP-32K。

执行者需先向用户确认其一：
- [ ] Fable 文件实际路径（可能在其他盘/目录）；或
- [ ] 需要重新下载（models_db.json 中记录名：`Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q5_K_M`，19.7GB）；或
- [ ] Fable 已被删除 → 本对比取消，告知用户。

## 5. 环境前置检查（必须全部通过才能开跑）

```powershell
# ① 杀掉所有 llama 进程（当前 PID 3064 的 llama-server 正占用 8081 端口和 ~27GB 显存，必须先停）
Get-Process -Name llama* | Stop-Process -Force

# ② 循环确认显存干净（<500MB 才算通过，最多等 60 秒）
nvidia-smi --query-gpu=memory.used --format=csv,noheader

# ③ 确认端口释放
netstat -ano | findstr :8081   # 应无 LISTENING
```

注意（AGENTS.md 事故复盘）：llama-server 先加载模型再监听端口，**只杀端口无效**，必须杀进程 + 循环查显存。

## 6. 模型与启动参数

### 6.1 NVFP4（基准组，参数照抄当前生产配置）

```
E:\models\Qwen3.8-27B-NVFP4-MTP-MEDIUM.gguf   (15.3GB)

llama-server.exe -m E:\models\Qwen3.8-27B-NVFP4-MTP-MEDIUM.gguf ^
  --mmproj E:\models\mmproj-Qwen3.8-27B-F16.gguf --no-mmproj-offload ^
  -ngl 99 --cache-type-k q8_0 --cache-type-v q8_0 -c 262144 ^
  -b 2048 --ubatch-size 512 -t 6 --parallel 1 --flash-attn enabled ^
  --ctx-checkpoints 4 --spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0 ^
  --reasoning auto --reasoning-budget 2048 --reasoning-effort high ^
  --reasoning-format deepseek --no-reasoning-preserve --no-warmup ^
  --temp 0.7 --top-p 0.9 --top-k 20 --min-p 0.0 --repeat-penalty 1.0 --presence-penalty 1.5 ^
  --jinja --chat-template-file E:\llama-win-cuda-12.4-x64\chat_template_qwen_fixed.jinja ^
  --alias Qwen3.8-27B-NVFP4 --port 8081 --host 127.0.0.1 --tools all ^
  --mcp-servers-config E:\llama-win-cuda-12.4-x64\mcp_servers.json
```

### 6.2 Fable（对比组，参数镜像 NVFP4，仅换模型/别名）

```
E:\models\Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q5_K_M.gguf   (19.7GB)

与 6.1 完全相同的参数，仅替换：
  -m E:\models\Qwen3.6-27B-Fable-...-Q5_K_M.gguf
  --alias Qwen3.6-27B-Fable
（文件名以第 4 节确认的实际路径为准；该模型名含 NEO-MTP，保留 --spec-type draft-mtp）
```

### 6.3 显存风险与回退方案

- NVFP4：权重 ~15.3GB + 262K ctx Q8_0 KV ≈ 当前生产占用 27.2/32GB，**可跑**。
- Fable：权重 19.7GB（+4.4GB）→ 同参数预计 **~31.6GB，有 OOM 风险**。
- 回退方案（两模型必须用同一档，否则不可比）：若 Fable 在 `-c 262144` OOM，则**两个模型都改用 `-c 131072`** 重跑速度+质量评测，并在第 8 节表格标注实际使用的 ctx。
- 公平性原则：除 `-m/--alias`（及必要时共同的 `-c`）外，两模型参数必须逐字一致。

## 7. 执行流程（严格顺序，禁止并行两个 server）

```
[0] 前置检查（第5节）→ Fable 文件确认（第4节）
[1] 启动 NVFP4（6.1）→ 等 /health ok → nvidia-smi 记录显存 → 跑评测 A
[2] 按 AGENTS.md 清理（杀进程 + 显存<500MB）
[3] 启动 Fable（6.2）→ 等 /health ok → nvidia-smi 记录显存 → 跑评测 A
[4] 再清理一次，恢复用户原生产配置（6.1 参数重启 NVFP4）
```

评测 A（两模型各跑一遍，复用目录内现有脚本，不新造轮子）：
- **速度**：现有 `bench_speed.py` / `bench_spec_speed_*.py`（以脚本实际用法为准，先读脚本头部注释；MTP 生效与否看输出中的 draft accept 信息）。
- **幻觉**：现有 `re_score_hallucination.py` / `启动幻觉+速度测试.bat`。
- **质量/RAG/工具**：现有 `agent_real_eval.py`（题目用 `questions.json`/`questions_v2.json`，输出 JSON 带时间戳）。
- 执行者不确定某脚本用法时：先读该脚本源码和同目录历史结果文件（`bench_results\`、`eval_results\`）推断调用方式，**不要凭空猜参数**。

## 8. 对比总表（执行后填写）

| 维度 | NVFP4-MTP (15.3GB) | Fable-711-MTP (19.7GB) |
|------|--------------------|-----------------------|
| 实际显存峰值 @ 实测ctx | ? | ? |
| 速度 tok/s（prompt/生成） | ? | ? |
| MTP 接受率/是否生效 | ? | ? |
| accuracy | ? | ? |
| hallucination | ? | ? |
| toolScore / ragScore | ? / ? | ? / ? |
| 中文口吻/主观体验（可选） | ? | ? |

## 9. 结论（执行后填写）

> （待填：推荐主力 + 理由；若 Fable 文件缺失导致无法对比，在此注明并说明已告知用户。）

## 10. 风险与备注

1. **NVFP4 在 V100 上无 FP4 原生加速**（运行时反量化），速度优势来自 MTP 投机而非量化本身；对比时重点看 MTP accept 率是否两模型一致。
2. models_db.json 中 NVFP4 的 accuracy=62.5 / halluc=75.0 是旧评分，用户 2026-08-21 复测综合得分已高于 Q6_K(100)，本次评测后统一回填。
3. 结果文件一律带时间戳命名（AGENTS.md 规则），禁止覆盖历史数据。
4. 全程单实例：任何时候 `Get-Process -Name llama*` 只能有一个 server 在跑（2026-08-03 双 server 占 GPU 事故教训）。
5. 编码规范（AGENTS.md）：新增 .md/.json 用 UTF-8 无 BOM；.bat 中文需 `chcp 65001`。
