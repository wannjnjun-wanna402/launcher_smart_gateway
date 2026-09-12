# 本地 MCP 工具集目录 (MCP Hub)

本目录集中存放与管理本机所有 MCP (Model Context Protocol) 服务组件与配置文件。

## 📁 目录结构
```text
mcp/
├── mcp_servers.json       # 核心集中配置文件 (全套 7 大 MCP 服务配置)
├── mcp.json               # 精简配置 (供轻量客户端使用)
├── 本地MCP服务清单.md      # 完整使用手册、架构说明与提问范例
├── 启动AnyTXT.bat          # 随用随开：一键启动 AnyTXT 全文检索
├── 关闭AnyTXT.bat          # 用完即关：一键彻底释放 CPU 与磁盘资源
├── anytxt_mcp.py          # 本地全文检索与 OCR 桥接服务 (带毫秒级健康探针)
├── local_search_mcp.py    # 国内多引擎搜索服务 (搜狗/360/Bing)
├── bilibili_mcp/          # B站长视频字幕提取、热榜与弹幕分析
└── cn-funds-mcp/          # 中国公募基金与 A 股大盘行情服务
```

详情请参阅完整手册：[本地MCP服务清单.md](file:///E:/llama-win-cuda-12.4-x64/mcp/%E6%9C%AC%E5%9C%B0MCP%E6%9C%8D%E5%8A%A1%E6%B8%85%E5%8D%95.md)
