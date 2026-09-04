# 项目编码规则与开发准则

## ⚡ 核心准则：全面废弃 PowerShell，Python (.py) 绝对第一优先
- ❌ **完全抛弃 PowerShell (.ps1)**：严禁编写新 `.ps1` 脚本，历史 `.ps1` 全部废弃，彻底杜绝 PowerShell 编码（BOM/GBK）、语法截断与进程树残留等深坑。
- ✅ **全面采用 Python (.py) 脚本**：本项目以 Python 为绝对第一公民，所有核心启动器、服务网关、调度监控、测评流水线一律使用纯 Python 编写；只有 Python 完全搞不定的极端底层场景才考虑其他技术。
- ✅ **.bat 仅作极简双击引导**：`.bat` 脚本只保留 2~5 行，仅负责设置 `chcp 65001` 与直接调用 `python.exe xxx.py`，严禁在 batch 中编写复杂的流程控制。

---

## 中文编码格式标准

本项目所有含中文的文本文件统一遵守以下编码标准：

| 文件类型 | 编码格式 | 说明 |
|:---------|:---------|:------|
| `.py` | **UTF-8 without BOM** | Python 标准，PEP 8 规范，本项目核心格式 |
| `.bat` / `.cmd` | **UTF-8 without BOM** + `chcp 65001` | cmd.exe 用 65001 代码页正确显示中文 |
| `.md` / `.json` / `.yaml` / `.toml` | **UTF-8 without BOM** | 通用标准 |
| `.ps1` | *已废弃* | 不再维护使用 |

### 实际文件对照

| 文件 | 当前编码 | 状态 | 说明 |
|:-----|:---------|:-----|:-----|
| `启动AI大模型.bat` | UTF-8 + chcp 65001 | ✅ | 桌面双击引导器（直接调 launcher_main.py） |
| `launcher_main.py` | UTF-8 | ✅ | **核心模型启动器 (Python 原生高能版)** |
| `qwen_tool_proxy.py` | UTF-8 | ✅ | **核心智能协同网关** |
| `启动奇迹API网关.bat` | UTF-8 + chcp 65001 | ✅ | 网关桌面引导器 |
| `launcher_main.ps1` | - | 🚫 | **已废弃，由 launcher_main.py 取代** |

### 注意事项

- 创建或修改含中文的文件时，写入前显式指定编码为 UTF-8
- 禁止使用 GBK/GB2312/CP936 编码写入新文件
- 遇到 GBK 旧文件时转换为 UTF-8（.bat 转 UTF-8 + chcp 65001）

---

## 🔴 多模型顺序测评 — 不可犯的错误

> 2026-08-03 真实事故复盘：旧脚本因清理不彻底导致两个 llama-server 同时占用 GPU，GPU 占用 31.6%，测试数据无效。

### 1. 多模型切换时，必须彻底清理

- ❌ 错误做法：只杀端口 8081（`Get-NetTCPConnection -LocalPort 8081`）
  - 原因：llama-server 先加载模型到显存，再监听端口。加载期间端口未开，杀端口无效。
- ✅ 正确做法：
  1. `Get-Process | Where-Object { $_.ProcessName -match 'llama' }` 杀所有 llama 进程
  2. 再杀端口 8081 占用
  3. **循环检查 GPU 显存**（`nvidia-smi --query-gpu=memory.used`），低于 500MB 才算干净
  4. 最多等 60 秒，超时打印警告但不阻塞

### 2. 结果文件禁止同名覆盖

- ❌ 错误做法：`Qwen3.5-4B_real_eval.json`（每次覆盖）
- ✅ 正确做法：`Qwen3.5-4B_real_eval_20260803_103910.json`（加时间戳）

### 3. RAG 回传搜索结果 — 不用 tool role

- ❌ 错误做法：用 `role: "tool"` 回传，llama.cpp 不识别，返回空响应
- ✅ 正确做法：把搜索结果作为新的 `user` 消息注入
- ✅ 同时：RAG 阶段**不提供 tools**（`tools=None`），强制模型生成文本

### 4. 搜索引擎 — 国内可用才用

| 搜索引擎 | 国内可用 | 中文质量 | 用法 |
|:---------|:--------:|:--------:|:-----|
| 搜狗 | ✅ | ✅ | 首选 |
| 360 | ✅ | ✅ | 备用 |
| Bing HTML | ✅ | ⚠️ 差 | 兜底（全球内容） |
| DuckDuckGo | ❌ 被墙 | — | 不要用 |
| 百度 | ✅ | ✅ | 反爬严重，纯爬取解析不到 |

### 5. 评分系统 — 不该调工具的题也要给 tools

- ❌ 错误：`should_call_tool=False` 的题不给 `tools` → 模型当然不调，但这测不出决策能力
- ✅ 正确：所有题目都给 `tools=[SEARCH_TOOL]`，让模型自己决定调不调

### 6. Windows 特定陷阱与最佳实践

| 陷阱 / 规范 | 正确做法 |
|:------------|:---------|
| PowerShell 脚本与编码 | ❌ 彻底废除 `.ps1`，一律使用 `.py` 编写脚本 |
| 直接用 `python` 命令 | 用完整路径 `C:\...\Python313\python.exe` 或当前虚拟环境 python |
| BAT 脚本中文 | UTF-8 + `chcp 65001`，仅用于拉起 Python 脚本 |
| 启动器调用 | 直接运行 `python launcher_main.py` 或双击 `启动AI大模型.bat` |

### 7. 全模型测评前必做检查 (纯 Python 原生检查)

```bash
# 确认无残留 llama 进程与显存
python -c "import psutil, subprocess; [p.kill() for p in psutil.process_iter() if 'llama' in p.name().lower()]; subprocess.run(['nvidia-smi'])"
```

---

## 📋 服务日志命名与单日累加标准（严格遵守）

未来新增任何后端端口、网关、侧挂引擎或额外功能，其产生的日志必须**严格遵守以下规范**：

### 1. 命名格式规范
- **标准格式**：`[端口号]_[功能名]_[YYYYMMDD].log`（端口号在最前，功能中缀在中间，年月日后缀在最后）。
- **标准对照示例**：
  - `8081_proxy_20260902.log`：8081 智能协同网关日志（计费/协议转译/流水）
  - `8083_llama_20260902.log`：8083 主脑推理引擎日志
  - `8085_sidecar_20260902.log`：8085 视觉侧挂眼睛日志
  - `8087_locate_20260902.log`：（如未来新增 8087 定位专项引擎）

### 2. 单日单一文件持续累加规则
- ❌ **严禁拆分**：禁止按时间点分割多个文件，禁止拆分为 `.out.log` 与 `.err.log`。
- ✅ **唯一累加**：同类服务在当天只允许产生**唯一一个** `.log` 文件，每次重启或产生新日志均以 `append`（追加）模式写入。
- ✅ **自动落盘**：Python 服务内置 `DailyProxyLogger` 双写控制台与当日日志；`llama-server` 统一挂载 `--log-file` 参数。

