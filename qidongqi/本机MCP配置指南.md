# 本机 MCP 配置指南（前端托管版）

> **背景**：本机 llama.cpp（端口 8081）自 **v88** 起**不再在后端挂载任何 MCP**。
> 已移除启动参数里的 `--tools all` / `--webui-mcp-proxy` / `--mcp-servers-config`，
> 并删除了启动阶段额外拉起的 `mcp-proxy` SSE 进程（原 127.0.0.1:8001）。
>
> **目的**：把工具 / MCP 的 schema 与编排从 llama.cpp 的上下文窗口里挪走，减轻后端负担；
> 改由你用的**前端 harness**（Qwen Code Desktop / Claude Desktop / Cursor / Cline 等）
> 在本机作为 MCP 宿主，自行拉起并管理工具。llama.cpp 只负责纯推理。

---

## 1. 配置文件位置

- **主配置**：`H:\llama-bin-win-cuda-13.3-x64\qidongqi\mcp-servers.json`
- **格式**：Claude Desktop 标准 `mcpServers`（Qwen Code / Cursor / Cline 等通用，直接可用）
- **内容**（两个 server）：

```json
{
  "mcpServers": {
    "ddg-search": {
      "command": "uvx",
      "args": ["--with", "duckduckgo-mcp-server[browser]", "duckduckgo-mcp-server"],
      "env": {
        "DDG_SAFE_SEARCH": "MODERATE",
        "DDG_REGION": "cn-zh"
      }
    },
    "anytxt": {
      "command": "C:\\Users\\Administrator\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe",
      "args": ["H:\\llama-bin-win-cuda-13.3-x64\\qidongqi\\anytxt_mcp_bridge.py"]
    }
  }
}
```

---

## 2. 两个 MCP 服务说明

| 服务 | 作用 | 启动方式 | 前置依赖 |
|---|---|---|---|
| **ddg-search** | DuckDuckGo 联网搜索（无需 Key） | `uvx` 拉起 `duckduckgo-mcp-server`（stdio） | 已装 `uv` / `uvx` |
| **anytxt** | 本地文档全文检索（桥接 AnyTxt HTTP @ 127.0.0.1:9920） | 隔离 venv 的 python 跑 `anytxt_mcp_bridge.py`（stdio，7 个工具） | ① AnyTxt 软件已运行；② venv python 已装 `mcp==1.9.4` |

> **anytxt 7 个工具**：`anytxt_search` / `anytxt_get_context` / `anytxt_get_fragment` /
> `anytxt_get_fragment_all` / `anytxt_read_file` / `anytxt_sync_index` / `anytxt_ocr`

---

## 3. 各前端如何挂载（把上面 `mcpServers` 块加进对应配置文件）

### A. Qwen Code Desktop（本机已在用，连本地 8081）
配置文件：`C:\Users\Administrator\.qwen\settings.json`（User 作用域）

- **方式一（直接编辑）**：在 JSON 顶层加一个 `mcpServers` 键（与已有的 `modelProviders` 并列），
  把第 1 节的 `mcpServers` 块整体复制进去。
- **方式二（命令）**：
  ```
  qwen mcp add ddg-search --transport stdio uvx --with duckduckgo-mcp-server[browser] duckduckgo-mcp-server
  qwen mcp add anytxt --transport stdio "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" "H:\llama-bin-win-cuda-13.3-x64\qidongqi\anytxt_mcp_bridge.py"
  ```
- **验证**：在 Qwen Code 里输入 `/mcp`，看 ddg-search / anytxt 是否 ready；模型即可调用搜索 / 文档检索。
- **说明**：Qwen Code 的 `mcpServers` 语法与 `mcp-servers.json` **完全一致**，可直接整体复制，无需改字段名。

### B. Claude Desktop
配置文件：`%APPDATA%\Claude\claude_desktop_config.json`
- 把第 1 节的 `mcpServers` 块并入该 JSON 的 `mcpServers` 字段，重启 Claude Desktop 即生效。

### C. Cursor / Cline / 其他兼容 harness
- 这些工具大多直接吃 Claude Desktop 格式的 `mcpServers`，按各自设置入口粘贴即可。

### D. 通用 OpenAI 兼容前端（仅工具调用，无需 MCP 服务）
- llama.cpp 仍接受请求体里的 `tools` 字段并产出 `tool_calls`；只要前端自己定义工具 schema
  并在收到 `tool_calls` 后执行，就能用，**不依赖任何 MCP 服务**（适合自写 agent / 轻量前端）。

---

## 4. 验证

1. 用启动器 v88 启本机模型，确认启动日志里**没有** `--tools` / `--webui-mcp-proxy` / `--mcp-servers-config` 字样。
2. 前端 `/mcp`（Qwen Code）或对应 MCP 面板看到 ddg-search / anytxt 为 ready。
3. 让模型「联网搜一下 XXX」或「在本地文档里找 YYY」验证工具实际可用。

---

## 5. 可选：恢复对外 SSE 端点（仅远程用）

v88 移除了本机 `mcp-proxy`（原 127.0.0.1:8001）。如需外网经 DDNS 用 MCP，可单独跑：

```
uvx mcp-proxy --named-server-config H:\llama-bin-win-cuda-13.3-x64\qidongqi\mcp-proxy-config.json --allow-origin * --port 8001 --stateless
```

再由 nginx / auth-server 转发 `/sse` → 127.0.0.1:8001。默认**不启用**（按需手动启动）。

---

## 6. 回滚

- 启动器是版本化文件：`launcher.ps1` 现指向 `...-v88.ps1`；要恢复后端挂载，把 `launcher.ps1`
  改回指向 `...-v87.ps1`（旧文件原样保留，未改动）。
- 前端 MCP 不想用时，删掉对应配置文件里的 `mcpServers` 块即可，不影响 llama.cpp。
