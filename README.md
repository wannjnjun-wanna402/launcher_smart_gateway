# 🚀 launcher_smart_gateway (奇迹大模型高能启动器与智能协同网关)

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![llama.cpp](https://img.shields.io/badge/Backend-llama.cpp%20CUDA-green.svg)](https://github.com/ggerganov/llama.cpp)
[![OpenAI Compatible](https://img.shields.io/badge/API-OpenAI%20%2F%20Claude-orange.svg)](http://127.0.0.1:8081/v1)

专为本地大语言模型（Qwen3.8-27B/35B、DeepSeek-V3/R1 等）深度定制的高性能终端启动器与企业级智能协同网关。

---

## 📦 核心文件清单

| 文件名 | 类型 | 功能说明 |
| :--- | :--- | :--- |
| **`launcher_main.py`** | 核心脚本 | 模型终端交互启动器（显存预算自适应、MTP投机加速、双槽位装配） |
| **`qwen_tool_proxy.py`** | 核心脚本 | 8081 智能协同网关（OpenAI/Claude 协议转译、长文本防爆剪枝、TPS 监控大屏） |
| **`chat_template_qwen_fixed.jinja`** | 必备模板 | 定制 Jinja 模板（彻底防止思考过程截断与中途串味） |
| **`personal_memory.py`** | 核心模块 | 长期记忆海马体提取引擎（SQLite 持久化与向量检索） |
| **`stop_ai_services.py`** | 实用工具 | 一键安全关停所有 AI 服务与 GPU 显存物理释放 |
| **`启动AI大模型.bat`** | 双击引导 | 桌面双击入口：呼出模型终端交互启动菜单 |
| **`启动奇迹API网关.bat`** | 双击引导 | 桌面双击入口：拉起后台网关服务并自动打开监控大屏 |
| **`一键安全停止所有AI服务.bat`** | 双击引导 | 桌面双击入口：换模型或关机前一键清空显存与解除端口占用 |
| **`launcher_smart_gateway_core.zip`** | 离线打包 | **一键绿色全套离线压缩包（约 140KB），点击即可直接下载复制** |

---

## ⚡ 快速开始（新电脑 3 步使用）

### 1. 准备 llama.cpp 运行底座
- 下载官方 Windows CUDA 运行包（如 `llama-bxxxx-bin-win-cuda-cu12.4-x64.zip`）；
- 将解压出的 `llama-server.exe` 及所有 `.dll` 动态库与本项目文件放在**同一个目录**。

### 2. 安装 Python 依赖
```bash
pip install psutil requests pyyaml
```

### 3. 一键启动
- 双击 **`启动AI大模型.bat`**：选择对应模型序号，全自动装配显存参数启动！
- 访问 **`http://127.0.0.1:8081/dashboard`** 查看实时硬件遥测、槽位状态与计费流水。

---

## 🛡️ 核心特性
- **永久无限输出预算**：彻底解耦思考预算与真实生成预算，拒绝中途掐断；
- **长文本防护盾 (Context-Guard 4.0)**：针对 100K+ 超大上下文实时智能剪枝，彻底告别 CUDA OOM 爆显存；
- **全模型视觉与工具转译**：零代码改造接入 DeepSeek Harness、Claude Code 等上层客户端。
