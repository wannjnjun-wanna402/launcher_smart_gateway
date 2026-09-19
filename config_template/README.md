# 通用网关架构 - 跨机器配置指南

## 核心原则
- **零硬编码**：所有机器差异通过 `*.local.json` 配置，核心代码不含任何绝对路径
- **约定优于配置**：遵循目录约定即可零配置运行，个性化仅需覆盖 `*.local.json`
- **单机一份配置**：每台机器复制 `*.example.json` 为 `*.local.json` 并修改即可

## 目录结构约定
```
<项目根目录>/
├── llama-server.exe          # llama.cpp 服务端（必需）
├── models/                   # 模型目录（必需）
│   ├── *.gguf               # 主模型文件
│   └── mmproj-*.gguf        # 多模态投影文件
├── config/                   # 配置目录（自动创建）
│   ├── hardware.local.json      # 机器硬件环境（自动生成/手动修正）
│   ├── models.local.json        # 模型参数配置（必需）
│   ├── mcp_servers.local.json   # MCP 服务配置（可选）
│   ├── skills.local.json        # 本地技能配置（可选）
│   └── gateway.local.json       # 网关参数配置（可选）
└── logs/                     # 日志目录（自动创建）
```

## 快速开始（新机器）
1. 克隆项目到任意目录
2. 将 `llama-server.exe` 放在项目根目录
3. 在 `models/` 目录放入模型文件
4. 运行 `python launcher_main.py --setup` 进入配置向导
5. 向导完成后直接运行 `python launcher_main.py`

## 配置文件详解

### 1. hardware.local.json - 硬件环境（自动生成/手动修正）
```json
{
  "llama_server_path": "",           // 留空自动查找，或指定绝对路径
  "models_dir": "",                  // 留空自动查找 models/ 目录
  "python_exe": "",                  // 留空使用当前 python
  "gpu_memory_mb": 0,                // 0=自动检测，或手动指定显存 MB
  "cpu_threads": 0,                  // 0=自动计算，或手动指定
  "cuda_version": "auto"             // "auto"/"12.4"/"13.0" 等
}
```

### 2. models.local.json - 模型参数（核心配置）
```json
{
  "version": "1.0",
  "comment": "本机模型启动配置，复制自 models.example.json 并修改路径/参数",
  "models": [
    {
      "id": "my_27b_q6k",                    // 唯一标识
      "name": "Qwen3.8-27B-Q6K [单槽·MTP·原生多模]",  // 显示名
      "model_filename": "Qwen3.8-27B-Abliterated-Q6_K.gguf",  // 模型文件名（在 models_dir 下）
      "mmproj_filename": "mmproj-Qwen3.8-27B-F16.gguf",       // 投影文件名（多模态必需）
      "quant": "Q6_K",
      "ctx": "144K·单槽",
      "vision": "原生多模+8085副脑",
      "is_text": false,
      "args": [                            // 启动参数（按需覆盖，空值使用默认）
        "-c", "147456",
        "--parallel", "1",
        "--spec-type", "draft-mtp",
        "--spec-draft-n-max", "2",
        "--spec-draft-n-min", "1",
        "-n", "-1",
        "--temp", "0.3",
        "--top-p", "0.95"
      ],
      "api_params": {                      // API 调用参数（供网关/前端使用）
        "max_tokens": -1,
        "temperature": 0.3,
        "top_p": 0.95,
        "reasoning_budget": 2048
      }
    }
  ]
}
```

### 3. mcp_servers.local.json - MCP 服务
```json
{
  "version": "1.0",
  "servers": [
    {
      "name": "anytxt",
      "enabled": true,
      "command": "${PYTHON}",
      "args": ["${BASE_DIR}/qidongqi/anytxt_mcp_bridge.py"],
      "env": {},
      "timeout": 30
    },
    {
      "name": "web-search",
      "enabled": false,
      "command": "uvx",
      "args": ["--with", "sogou-mcp-server", "sogou-mcp-server"],
      "env": {"REGION": "cn"}
    }
  ]
}
```

### 4. skills.local.json - 本地技能
```json
{
  "version": "1.0",
  "skills": [
    {"name": "memory-recall", "enabled": true, "category": "memory"},
    {"name": "billing-stats", "enabled": true, "category": "billing"},
    {"name": "clock", "enabled": true, "category": "time"},
    {"name": "workspace-read", "enabled": true, "category": "workspace"}
  ]
}
```

### 5. gateway.local.json - 网关参数
```json
{
  "version": "1.0",
  "listen_port": 8081,
  "target_port": 8083,
  "vision_port": 8085,
  "embedding_port": 8086,
  "mcp_port": 8087,
  "api_key": "llamacpp",
  "pricing": {
    "standard": {"input": 1.5, "output": 4.5, "cache_miss": 0.05, "image": 0.0015}
  }
}
```

## 环境变量覆盖（最高优先级）
| 变量名 | 作用 |
|--------|------|
| `LLAMA_SERVER_DIR` | 指定 llama-server.exe 所在目录 |
| `MODELS_DIR` | 指定模型目录 |
| `CONFIG_DIR` | 指定配置目录（默认项目根目录/config） |
| `PYTHON_EXE` | 指定 Python 解释器路径 |

## 常见问题

**Q: 模型加载慢/显存不够？**
A: 修改 `models.local.json` 中对应模型的 `-c` (上下文长度)、`--parallel` (并发槽位)、`--cache-type-k/v` (KV 量化)。

**Q: 多机器同步配置？**
A: 仅同步 `*.example.json` 到 Git，`*.local.json` 在 `.gitignore` 中，每台机器独立维护。

**Q: 如何切换 llama.cpp 版本？**
A: 替换根目录 `llama-server.exe` 及同目录下的 `*.dll`，或设置 `LLAMA_SERVER_DIR` 环境变量指向新版本目录。

---

*此文档随架构演进更新，版本同步见 Git 提交记录。*