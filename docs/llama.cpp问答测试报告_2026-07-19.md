# llama.cpp 运行日志审计报告 — 2026-07-19

**模型质量保障专家（QA）视角** ｜ 数据源：`E:\llama-win-cuda-12.4-x64\logs\llama_log_20260719.log` + 7 个 `server_raw_*` 原始日志
**主机**：V100-PCIE-32GB / i5-8500 6C6T / 32GB RAM / Win11 ｜ **llama.cpp**：b10058 → b10068（用户在 19:18→21:34 之间换了二进制）
**整体结论**：**Sound with Findings（已修复 3/5）** —— 启动器核心功能正常。审计发现的 5 个缺陷中，**启动器侧 3 项已修复**（#1 `-ubatch-size` 单横杠、#4 RemoteException 误报 ERROR、#5 Qwen3VL caps"思考模式"）；**客户端侧 2 项（#2 工具调用 500、#3 80K 溢出）须调用方修正**，启动器内无对应代码，修复指引见第五节。

---

## 一、运行日志总览（昨天跑过什么）

| 时间 | 模型 | llama.cpp | 结果 | 关键指标 |
|---|---|---|---|---|
| 08:26 | 27B-MTP-IQ4_XS | — | （早段，本日志仅尾部） | — |
| 17:04–18:xx | **35B-A3B** | b10058 | 启动成功，长会话 | prompt_eval≈470–500 t/s；tg≈55 t/s（68K 长提示） |
| 19:18 | 35B-A3B | b10058 | 重启成功 | 同上 |
| 21:23 | **Qwen3VL-8B** | — | ❌ **启动失败** | `error: invalid argument: -ubatch-size` |
| 21:34 | Qwen3VL-8B | — | ❌ **启动失败** | 同上（第二次） |
| 21:38 | 27B-MTP-IQ4_XS | b10068 | ✅ 成功 | 启动 27.3s，监听 8081 |
| 21:40 | **Qwen3VL-8B** | b10068 | ✅ **修复后成功** | tg≈77 t/s，latency≈13ms |
| 21:54 | Qwen3VL-8B | b10068 | ✅ 成功 | tg≈75 t/s（二次验证） |

> 日志共 **251 条 ERROR**，其中 **121 条**为 `tool_choice` 工具调用解析错误（集中在 17:04 的 35B 会话）。

---

## 二、BUG 报告（按严重度排序）

### 🔴 BUG #1 — Qwen3VL 因 `-ubatch-size` 单横杠非法参数启动失败 【严重·功能性】
- **你已知的那个，几乎可以肯定就是它。** 日志铁证：
  - `21:23:18.063 ERROR ... Qwen3VL ... | error: invalid argument: -ubatch-size`（会话 0.1min 即结束）
  - `21:34:30.107 ERROR ... Qwen3VL ... | error: invalid argument: -ubatch-size`（会话 0min 即结束）
- **根因**：TTFT 优化那步给 Qwen3VL 分支写成了单横杠 `-ubatch-size`，但 llama.cpp 只认 `-ub` / `--ubatch-size`；单横杠被拆成无效短选项 → 直接启动失败。其余模型没这个参数所以不崩，唯独 8B 起不来。
- **影响**：Qwen3VL 完全无法启动，连 8081 都起不了服务。
- **修复状态：✅ 已修复并验证**。21:40 / 21:54 同一模型改用 `--ubatch-size` 后正常启动，tg≈75–77 t/s。当前 `launcher_main.ps1` L680 已确认为 `"--ubatch-size", "2048"`（已 grep 复核）。
- **教训（已记入记忆）**：给启动器加参数，凡是不确定短写的，一律用 `--` 长格式；单横杠只用于确认存在的短选项（`-ngl/-c/-b/-t/-p`）。

### 🟠 BUG #2 — 35B 工具调用（function calling）完全失效：客户端/服务端 JSON 合同不匹配 【中等·功能性】
- **证据**：35B 会话中爆发 **121 条**同类错误，典型两条配对出现：
  ```
  Wrong type supplied for parameter 'tool_choice'. Expected 'string', using default value:
  [json.exception.type_error.302] type must be string, but is object
  srv operator(): got exception: {"error":{"code":500,"message":"Failed to parse tools:
  [json.exception.type_error.302] type must be string, but is null; tools = [
    {"function": {"description": null, "name": "web_search", ...}, "type": "function"}]"}}
  ```
- **根因**（客户端侧，非启动器崩）：调用方（Codex / 某 OpenAI SDK 客户端）发出的请求体有两处不被 llama.cpp b10058 接受：
  1. `tool_choice` 发成了**对象** `{"type":"function","function":{...}}`，而服务端期望**字符串** `"auto"/"none"/"required"`；
  2. `tools[].function.description` 发成了 **`null`**，而服务端 nlohmann-json 解析要求其为字符串（或省略）。
- **影响**：**每一次带工具的请求都返回 HTTP 500，35B 的「工具调用」能力实测不可用**（启动器 caps 里却写着"工具调用"，属宣称与实际不符）。纯聊天不受影响。
- **修复方向（客户端，不在启动器）**：
  - 让客户端发 `tool_choice: "auto"`（字符串），且 `tools[].function.description` 给字符串或省略，不要发 `null`；
  - 或把 llama.cpp 升到更新版本（你 21:34 后已换 b10068，可能对 `null`/`object` 容忍度更好 —— **但尚未在 b10068 上实测工具调用**，需你发一条带工具请求验证）。
- **建议**：在连 127.0.0.1:8081 的客户端里关掉/修正 tool_choice 写法，先验证工具调用是否恢复。

### 🟠 BUG #3 — 35B 上下文溢出：请求超过 80K 上限被拒 【中等·配置限制】
- **证据**：
  ```
  17:56:56 request (81920 tokens) exceeds the available context size (81920 tokens)
  17:59:03 request (82173 tokens) exceeds the available context size (81920 tokens)
  18:16:34 request (82205 tokens) exceeds the available context size (81920 tokens)
  ```
- **根因**：35B 当前 `-c 81920`（80K）。长会话 + 生成逼近/超过 80K 时服务端直接 500。硬件约束：35B@80K 已占 ~28.5G（余 3.5G 最紧），上 96K 会 OOM，所以 80K 是 V100 32G 下的硬上限。
- **影响**：超长上下文请求失败。
- **修复方向（客户端）**：在连本地 8081 的客户端启用 auto-compact（你 Codex config 里的 `model_auto_compact_token_limit=48000` 是给**远程模型**用的，对本地 8081 不生效，本地需客户端自己配）。

### 🟡 BUG #4 — PowerShell `RemoteException` 被误记为 ERROR（良性 stderr 噪音） 【低·日志质量】
- **证据**：35B（17:05:33、19:18:34）与 Qwen3VL（21:41:16、21:54:22）加载时各出现一条 `ERROR [POWERSHELL] ... System.Management.Automation.RemoteException`，但**紧随其后模型都 `model_loaded` + `server_listening` 成功**。对应的 `server_raw` 原始日志干净无错（仅 Qwen-VL image-tokens 提示类 warning）。
- **根因**：llama-server 加载期向 stderr 打了一行信息/警告，被 PowerShell `2>&1` 管道包成 `RemoteException` 错误记录，但服务本身正常。
- **影响**：无功能影响，但抬高 ERROR 计数、容易吓到人、淹没真实错误。
- **修复方向**：在日志包装层把这类良性 stderr 降级为 INFO/WARN（需定位那一行具体 stderr 文本再精准过滤）。

### 🟡 BUG #5 — Qwen3VL caps 标注"思考模式"但 reasoning 未注入 【低·一致性】
- **证据**：`launcher_main.ps1` L673 Qwen3VL 的 `caps` 含 `"思考模式"`，但其 `customArgs`（L674–690）**没有** `--reasoning on`（按 7/19 决议，VL 延迟敏感不注入 reasoning）。
- **影响**：启动器菜单向用户宣称支持思考模式，实际未开启 → 宣称与配置不符。
- **修复方向**：二选一 —— ① 从 caps 移除"思考模式"；② 给 Qwen3VL 也加 `--reasoning on --reasoning-budget 2048`（会增加首字延迟）。建议选 ①。

### ⚪ 已修复/信息项
- **`-t 8`→`-t 6`**：17:04、19:18 的 35B 仍带 `-t 8`（超 i5-8500 的 6 线程），当日已落盘改为 `-t 6`，21:38 后所有会话均为 `-t 6`。
- **`--no-mmap` 硬编码**：21:23 的 Qwen3VL 还带 `--no-mmap`，21:34 起已移除（与全模型 mmap 一致）。
- **`QWEN_VL_KEYWORDS=@("qwen2.5-vl")`**（L221）：当前无模型匹配，属遗留死配置，靠 `qwen3vl` 分支兜底，无功能影响，建议清理或补充注释。

---

## 三、修复状态汇总

| # | 缺陷 | 严重度 | 状态 | 责任方 |
|---|---|---|---|---|
| 1 | Qwen3VL `-ubatch-size` 单横杠 | 高(功能) | ✅ 已修+验证 | 启动器 |
| 2 | 35B 工具调用 500（tool_choice/tools） | 中(功能) | ⏳ 待客户端修（见下） | 客户端 |
| 3 | 35B 80K 上下文溢出 | 中(配置) | ⏳ 待客户端 auto-compact（见下） | 客户端 |
| 4 | RemoteException 误报 ERROR | 低(日志) | ✅ 已修（L1359-1371 降级 INFO） | 启动器 |
| 5 | Qwen3VL caps 标思考模式但未注入 | 低(一致) | ✅ 已修（L673 移除"思考模式"） | 启动器 |

---

## 四、给用户的验证/下一步建议

1. **BUG#1 你已知**：日志已实锤 21:23/21:34 失败、21:40 修复后 tg≈77/s，当前文件为 `--ubatch-size`，无需再动。
2. **BUG#2 你可能没意识到是 Bug**：35B 的工具调用（web_search 等）目前**实测全挂**，是客户端把 `tool_choice` 当对象、`description` 发 `null` 导致。先在客户端把 tool_choice 改成字符串、description 给字符串或省略；或发一条带工具请求到 b10068 验证是否已随版本缓解。
3. **BUG#3**：长文超 80K 会被拒，本地 8081 客户端需自己开 auto-compact（远程模型的 compact 配置不作用于本地）。
4. 要我直接改启动器侧的两项（BUG#4 日志降级、BUG#5 caps 去"思考模式"）的话说一声，我顺手改。

---
**QA 分析师**：ModelQualityAssuranceExpert ｜ **审计日期**：2026-07-20 ｜ **数据日期**：2026-07-19

---

## 五、修复执行记录（2026-07-20）

### 启动器侧（launcher_main.ps1，已落盘）
- **BUG#1** ✅ 前次已修并验证：Qwen3VL 分支 `-ubatch-size` 单横杠 → `--ubatch-size`（L680）。
- **BUG#4** ✅ 已修：日志包装层（L1359–1371）新增判定——凡 `RemoteException`/`NativeCommandError`（PowerShell 把 llama-server 良性 stderr 包成的噪音）一律降级为 `INFO [POWERSHELL]`，不再计入 ERROR；真实致命错误仍原样写入 `logs/server_raw_*.log`。**校验**：BOM 保留（ef bb bf）、`ParseFile` 语法 = PARSE_OK。
- **BUG#5** ✅ 已修：Qwen3VL 的 `caps` 移除"思考模式"（L673），与其"VL 不注入 reasoning"策略一致。35B(L699)/27B(L780) 的"思考模式"保留（二者确有 `--reasoning on`）。

### 客户端侧（启动器无对应代码，须用户在调用方处理）
- **BUG#2（工具调用 500）修复指引**：连 `127.0.0.1:8081` 的客户端（Codex / OpenAI SDK）发出的请求体须改两处——
  1. `tool_choice` 发**字符串** `"auto"` / `"none"` / `"required"`，**不要**发对象 `{"type":"function",...}`；
  2. `tools[].function.description` 给**字符串**或**省略**，不要发 `null`。
  验证：在 b10068 上发一条带 `web_search` 等工具的请求，若不再 500 即恢复。若仍 500，说明 b10068 未缓解，需客户端严格按上述格式发。
- **BUG#3（80K 溢出）修复指引**：本地 8081 客户端须自行开启 auto-compact（把长上下文压缩到 < 80K 再发）。注意：你 Codex config 里的 `model_auto_compact_token_limit=48000` 是给**远程模型**用的，对本地 8081 **不生效**，须在连本地的客户端/agent 配置里单独设。

### 复跑验证建议
1. 真机执行 `launcher_main.ps1 -ListModels`，确认无悬空 `elseif`/分支报错（ParseFile 抓不到此类运行期结构错误）。
2. 发一条带工具请求到 35B，确认 BUG#2 是否随 b10068 缓解。

---

**QA 分析师**：ModelQualityAssuranceExpert ｜ **审计日期**：2026-07-20 ｜ **数据日期**：2026-07-19 ｜ **修复日期**：2026-07-20
