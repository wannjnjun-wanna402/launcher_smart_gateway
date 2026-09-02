# 本机已设置 MCP 服务器说明手册

> 更新日期：2026-08-26
> 本机已配置 **6 个本地 stdio MCP server + 1 个远程 HTTP MCP server（Tavily）**。本地 stdio 服务**不是常驻后台进程**，由 AI 客户端（如 Qwen Code、Claude Code、Harness 编排程序等）在启动时作为子进程按需拉起；Tavily 为远程托管 HTTP 端点（`https://mcp.tavily.com/mcp/`），无需本地进程；llama.cpp 作为纯推理 API（`http://127.0.0.1:8081/v1`），不挂载任何 MCP。

---

## 一、服务清单总览

| 服务标识 (ID) | 启动命令 / 脚本路径 | 核心功能 | 前置依赖 / 环境 |
|:---|:---|:---|:---|
| **`anytxt-mcp`** | `C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe E:\llama-win-cuda-12.4-x64\anytxt_mcp.py` | 本地文件**全文内容**秒级检索 + 离线 OCR 识别 | **ATGUI.exe 必须运行**（本机常驻）；新目录需加入监控或调用 `anytxt_sync_index` |
| **`bing-cn-mcp-server`** | `cmd /c npx -y bing-cn-mcp` | 必应中文全网搜索 + 网页正文抓取（自动过滤低质站） | Node.js / npx 环境已装；首次调用会自动下载包（建议超时 ≥ 30s） |
| **`excel-mcp`** | `uvx excel-mcp-server stdio` | Excel 表格自动读写、公式计算、图表生成 | uvx 环境已装（`C:\Users\wanna402\AppData\Local\hermes\bin\uvx.exe`） |
| **`bilibili-mcp`** | `C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe E:\llama-win-cuda-12.4-x64\bilibili_mcp\mcp_server.py` | **B站长视频字幕提取速读**、全站/分区热榜、评论弹幕分析 | Python 3.13 + 本机 `bilibili_mcp` 目录；无需 API Key，扫码可选登录 |
| **`hotnews-mcp`** | `cmd /c npx -y @wopal/mcp-server-hotnews` | **全网实时热榜聚合**（知乎、微博、B站、36氪、IT之家、抖音等） | Node.js / npx；直连各大平台公开接口，**零 API Key** |
| **`cn-funds-mcp`** | `cmd /c npx -y cn-funds-mcp` | **中国基金与金融数据**（天天基金/东方财富源：支付宝基金估值、持仓重仓股、大盘走势） | Node.js / npx；直连东方财富/天天基金公开接口，**零 API Key** |
| **`tavily`** | 远程 HTTP：`https://mcp.tavily.com/mcp/?tavilyApiKey=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 | **AI 优化的网络搜索 + 网页正文提取**（结果按相关性排序、去噪） | 远程托管端点，无需本地进程；API Key 内嵌于 URL，**需有效 Tavily Key**（本机已配） |

---

## 二、详细工具列表与使用场景

### 1. 📺 B站万能助手 (`bilibili-mcp`)
* **核心工具**：
  * `bili_subtitle` — 获取视频 AI 字幕（语音转文字，**长视频 10 秒总结核心**）
  * `bili_hot_videos` / `bili_weekly_hot` / `bili_rank` — 获取热门视频、每周必看、全站/各分区排行榜
  * `bili_search` — 搜索视频（支持按播放量、最新、弹幕排序）
  * `bili_comments` / `bili_danmaku` — 获取视频热门评论与弹幕列表
  * `bili_user_videos` / `bili_user_info` — UP主投稿列表与数据分析
* **日常提问范例**：
  > “帮我总结这个 B 站数码评测视频的核心观点和优缺点：BV1xx411c7mD”  
  > “看看今天科技区和单机游戏区排名前 5 的视频是什么”  
  > “搜一下最近关于《黑神话：悟空》播放量最高的攻略视频”

---

### 2. 🔥 全网聚合热点 (`hotnews-mcp`)
* **核心工具**：
  * `get_hot_news` — 按平台获取实时热搜列表（支持知乎、微博、B站、36氪、IT之家、百度、抖音、虎扑、豆瓣等）
* **日常提问范例**：
  > “汇总今天知乎、微博、36氪和 IT 之家的热搜前 5 名，告诉我今天科技圈有什么大事”  
  > “今天虎扑和抖音大家在热议什么话题？”  
  > “生成一份今天全网吃瓜热点早报”

---

### 3. 📈 基金与金融市场助手 (`cn-funds-mcp`)
* **核心工具**：
  * `search_fund` — 按名称、代码或拼音搜索公募基金（如“易方达蓝筹”或“005827”）
  * `get_fund_estimate` — 获取盘中实时估值与涨跌幅度估算
  * `get_fund_detail` / `get_fund_history_net` — 查询基金基本信息、历史净值走势与累计收益率
  * `get_fund_position` — **查询基金持仓明细（前十大重仓股与行业分布）**
  * `get_fund_manager` — 查询基金经理从业年限、历史管理业绩
  * `get_stock_quote` / `get_market_overview` — A 股大盘指数行情与板块主力资金流向
* **日常提问范例**：
  > “帮我查一下 012414（招商中证白酒）今天的实时估值是多少，前十大重仓股有哪些？”  
  > “我买了易方达蓝筹精选和中欧医疗健康，帮我对比一下它们最近半年的走势和重仓股票。”  
  > “今天 A 股大盘指数怎么样？主力资金流入最多的板块是哪个？”

---

### 4. 🔍 本地全文搜索与 OCR (`anytxt-mcp`)
* **核心工具**：
  * `anytxt_search` — 统计命中文件数（支持引号短语、`!排除` 等高级语法）
  * `anytxt_get_result` — 列出命中文件（路径、大小、修改时间、文件 ID）
  * `anytxt_get_fragment` / `anytxt_get_fragment_all` — 按文件 ID 读取关键词上下文片段
  * `anytxt_get_raw_text` — 按文件 ID 读取全文内容（建议先 fragment 确认）
  * `anytxt_ocr` — 离线 OCR 识别本地图片中的文字
  * `anytxt_sync_index` — 将指定目录立即同步进索引
* **日常提问范例**：
  > “帮我搜一下我电脑文档里有没有提到‘租赁合同违约金’的相关文件”  
  > “识别一下 `D:/Screenshots/1.png` 截图里的文字内容”

---

### 5. 🌐 必应中文搜索与网页提取 (`bing-cn-mcp-server`)
* **核心工具**：
  * `bing_search` — 必应中文搜索（query / count / offset）
  * `crawl_webpage` — 批量抓取网页正文（自动剔除知乎/小红书等反爬/广告干扰）
* **日常提问范例**：
  > “搜索一下 2026 年有什么最新单机游戏大作发售”  
  > “抓取并总结这个网页的正文内容：https://...”

---

### 6. 📊 Excel 表格操作 (`excel-mcp`)
* **核心工具**：
  * `create_workbook` / `read_data_from_excel` / `write_data_to_excel` — 工作簿读写
  * `apply_formula` / `validate_formula_syntax` — 公式与计算
  * `create_table` / `create_chart` — 数据表与统计图表生成
* **日常提问范例**：
  > "帮我把这段基金收益数据整理成一个 Excel 表格，并计算平均收益率"

---

### 7. 🌍 Tavily 网络搜索 (`tavily`)
* **接入方式**：远程 HTTP MCP（`https://mcp.tavily.com/mcp/`），非本地 stdio，无需 npx / Python 进程。
* **核心能力**：AI 优化的网络搜索（结果按相关性排序并去噪）、指定 URL 网页正文提取。（具体工具名以实际加载为准）
* **前置依赖**：有效 Tavily API Key，内嵌于连接 URL；本机 Key 存于 `~/.qwen/settings.json`（真实 Key 已直接写入本文档，无需另行查找）。
* **日常提问范例**：
  > "用 Tavily 搜一下 2026 年最新发布的 AI 大模型有哪些，给我带来源链接的摘要"

---

## 三、各 AI 客户端完整注册配置

可以直接复制以下 JSON 块，放入对应客户端的配置文件中：

### 1. Qwen Code Desktop（`~/.qwen/settings.json`）
```jsonc
{
  "mcpServers": {
    "anytxt-mcp": {
      "command": "C:\\Users\\wanna402\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
      "args": ["E:\\llama-win-cuda-12.4-x64\\anytxt_mcp.py"]
    },
    "bing-cn-mcp-server": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "bing-cn-mcp"],
      "timeout": 30000
    },
    "excel-mcp": {
      "command": "uvx",
      "args": ["excel-mcp-server", "stdio"]
    },
    "bilibili-mcp": {
      "command": "C:\\Users\\wanna402\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
      "args": ["E:\\llama-win-cuda-12.4-x64\\bilibili_mcp\\mcp_server.py"]
    },
    "hotnews-mcp": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "@wopal/mcp-server-hotnews"],
      "timeout": 30000
    },
    "cn-funds-mcp": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "cn-funds-mcp"],
      "timeout": 30000
    },
    "tavily": {
      "httpUrl": "https://mcp.tavily.com/mcp/?tavilyApiKey=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",
      "timeout": 30000
    }
  }
}
```

### 2. Claude Code（`~/.claude/settings.json` 或项目 `.mcp.json`）
```jsonc
{
  "mcpServers": {
    "anytxt-mcp": {
      "command": "C:\\Users\\wanna402\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
      "args": ["E:\\llama-win-cuda-12.4-x64\\anytxt_mcp.py"]
    },
    "bing-cn-mcp-server": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "bing-cn-mcp"]
    },
    "excel-mcp": {
      "command": "uvx",
      "args": ["excel-mcp-server", "stdio"]
    },
    "bilibili-mcp": {
      "command": "C:\\Users\\wanna402\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
      "args": ["E:\\llama-win-cuda-12.4-x64\\bilibili_mcp\\mcp_server.py"]
    },
    "hotnews-mcp": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "@wopal/mcp-server-hotnews"]
    },
    "cn-funds-mcp": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "cn-funds-mcp"]
    },
    "tavily": {
      "httpUrl": "https://mcp.tavily.com/mcp/?tavilyApiKey=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",
      "timeout": 30000
    }
  }
}
```

---

## 四、日常使用与维护注意事项

1. **按需启用与上下文成本**：
   - 当前配置的 6 个服务全部为轻量级 stdio 进程，工具 Schema 总开销适中。
   - 如果某个时期完全用不到 Excel 或 B 站，可在客户端配置中临时注释或禁用，以节省微量 Prompt 上下文。
2. **ATGUI 后台常驻**：
   - `anytxt-mcp` 依赖本地 `ATGUI.exe` 运行。开机后确保 AnyTXT 在任务栏托盘运行即可正常使用全文搜和 OCR。
3. **Node.js / npx 首次启动缓存**：
   - `hotnews-mcp`、`cn-funds-mcp`、`bing-cn-mcp-server` 采用 `npx -y` 运行，首次调用会自动下载并缓存，后续调用均为本地秒级启动。
4. **基金与金融数据实时性**：
   - `cn-funds-mcp` 盘中实时估值为交易时间内模拟估值，每日实际官方净值通常在交易日晚上 20:00 ~ 22:00 由基金公司清算后更新。
5. **Tavily API Key（远程）**：
   - `tavily` 为远程托管 HTTP MCP，无需本地进程或 npx，直连 `https://mcp.tavily.com/mcp/`。
   - API Key 内嵌于连接 URL；本机 Key 存于 `~/.qwen/settings.json`（真实 Key 亦已直接写入本文档的总览表与配置块；注意：本文件含明文密钥，请勿对外同步/分享）。Key 失效或有额度限制时需到 [Tavily 控制台](https://tavily.com) 更新。
