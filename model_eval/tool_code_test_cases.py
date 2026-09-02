# -*- coding: utf-8 -*-
"""
=============================================================================
Qwen3.8 工具调用深度专项题库 (Dedicated Tool Calling Benchmark Suite)
=============================================================================
本题库专门针对以下两项核心课题深度验证：
1. 聊天模板差异 (Qwen-Sharp vs Qwen-Fixed) 对参数重复循环展开、XML标签泄露、递归失控的影响
2. 无审查微调 (Abliterated / Uncensored) 与 官方对齐模型 (NVFP4) 在结构化指令遵循上的能力差异
=============================================================================
"""

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "获取指定城市的实时天气预报信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，如北京、上海、广州、杭州、纽约"
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "温度单位，默认 celsius"
                    }
                },
                "required": ["city"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "高精度数学计算器，支持标准算术与代数表达式计算",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "待计算的数学表达式，如 (1234 * 5678) / 2"
                    }
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_python_code",
            "description": "在沙箱环境中执行 Python 代码并获取 stdout 输出",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "完整的 Python 代码字符串"
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "超时限制秒数，默认 10"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "在指定代码文件或目录中按正则模式搜索文本内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "待检索的文件绝对路径或目录路径"
                    },
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或正则表达式"
                    },
                    "offset": {
                        "type": "integer",
                        "description": "搜索结果的行数起始偏移量，默认 0"
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "是否忽略大小写"
                    }
                },
                "required": ["path", "query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取本地文件指定行范围的内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件绝对路径"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号 (从 1 开始)"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "database_query",
            "description": "执行只读 SQL 查询以检索数据库记录",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "数据表名称，如 users, orders, products"
                    },
                    "filters": {
                        "type": "object",
                        "description": "过滤条件键值对，例如 {\"status\": \"active\", \"age_gt\": 18}"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回最大记录条数"
                    }
                },
                "required": ["table_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "互联网搜索引擎，用于获取最新实时事实资讯与网页内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "精准搜索关键词"
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "返回搜索条数，默认 5"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "在用户日历中创建一条日程事件",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "日程标题"
                    },
                    "start_time": {
                        "type": "string",
                        "description": "开始时间，格式 YYYY-MM-DD HH:MM:SS"
                    },
                    "is_all_day": {
                        "type": "boolean",
                        "description": "是否全天事件"
                    },
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "参与者邮箱列表"
                    }
                },
                "required": ["title", "start_time"]
            }
        }
    }
]

TOOL_QUESTIONS = [
    # ---- 1. 基础单工具精确触发 (Basic Single Tool Call) ----
    {
        "id": "TOOL_01",
        "category": "单工具触发",
        "prompt": "请帮我查一下杭州今天的天气怎么样？",
        "should_call": True,
        "expected_tool": "get_current_weather",
        "param_checks": {"city": ["杭州", "hangzhou", "Hangzhou"]},
        "description": "基础天气查询单参数工具调用"
    },
    {
        "id": "TOOL_02",
        "category": "单工具触发",
        "prompt": "请计算 (987654 * 321) / 123 的精确数学计算结果。",
        "should_call": True,
        "expected_tool": "calculator",
        "param_checks": {"expression": ["987654", "321", "123"]},
        "description": "数学表达式工具调用"
    },
    {
        "id": "TOOL_03",
        "category": "单工具触发",
        "prompt": "帮我搜索一下 2026 年最新发布的 llama.cpp 支持哪些新型量化格式。",
        "should_call": True,
        "expected_tool": "web_search",
        "param_checks": {"query": ["llama.cpp", "量化", "2026"]},
        "description": "网络搜索工具调用 (高危：易在缺陷模板中触发重复生成死循环)"
    },
    {
        "id": "TOOL_04",
        "category": "单工具触发",
        "prompt": "查询 orders 表中 status 为 'completed' 的最近 10 条订单数据。",
        "should_call": True,
        "expected_tool": "database_query",
        "param_checks": {"table_name": ["orders"]},
        "description": "数据库查询工具调用"
    },
    {
        "id": "TOOL_05",
        "category": "单工具触发",
        "prompt": "请在沙箱里运行一段 Python 代码，计算 1 到 1000 中所有质数的和。",
        "should_call": True,
        "expected_tool": "execute_python_code",
        "param_checks": {"code": ["def", "for", "prime", "range", "sum"]},
        "description": "Python 代码执行工具调用"
    },

    # ---- 2. 参数重复展开与代码搜索陷阱测试 (Grep & File Ops) ----
    {
        "id": "TOOL_06",
        "category": "文件代码检索",
        "prompt": "在文件 'E:\\ai123\\claude\\rainy-convenience.html' 中搜索关键词 'InputValidation'，从第 0 行开始检索。",
        "should_call": True,
        "expected_tool": "grep_search",
        "param_checks": {
            "path": ["rainy-convenience.html", "E:\\ai123\\claude\\rainy-convenience.html", "E:/ai123/claude/rainy-convenience.html"],
            "query": ["InputValidation"],
            "offset": [0]
        },
        "description": "代码 Grep 检索 (高危：针对 AI 反馈的 path/offset 重复写几十遍死循环专项验证)"
    },
    {
        "id": "TOOL_07",
        "category": "文件代码检索",
        "prompt": "请读取文件 'E:\\llama-win-cuda-12.4-x64\\models_db.json' 的第 1 到 50 行内容。",
        "should_call": True,
        "expected_tool": "read_file",
        "param_checks": {
            "path": ["models_db.json", "E:\\llama-win-cuda-12.4-x64\\models_db.json"],
            "start_line": [1],
            "end_line": [50]
        },
        "description": "文件行范围读取工具调用"
    },

    # ---- 3. 复杂参数与类型约束 (Enum / 布尔 / 数组 / 嵌套对象) ----
    {
        "id": "TOOL_08",
        "category": "多参数与类型约束",
        "prompt": "帮我查询一下纽约现在的天气，请用华氏度（fahrenheit）表示。",
        "should_call": True,
        "expected_tool": "get_current_weather",
        "param_checks": {"city": ["纽约", "New York", "new york"], "unit": ["fahrenheit"]},
        "description": "包含 Enum 枚举参数的工具调用 (高危：易被模板污染注入 <parameter=unit>)"
    },
    {
        "id": "TOOL_09",
        "category": "多参数与类型约束",
        "prompt": "帮我预约明天上午 10:00 的'架构评审会'，时间是 2026-09-01 10:00:00，非全天事件，邀请 alice@example.com 和 bob@example.com 参加。",
        "should_call": True,
        "expected_tool": "create_calendar_event",
        "param_checks": {
            "title": ["架构评审会", "架构评审"],
            "start_time": ["2026-09-01 10:00:00", "2026-09-01T10:00:00"],
            "is_all_day": [False, "false"],
            "attendees": ["alice@example.com", "bob@example.com"]
        },
        "description": "包含数组、布尔值与复合字符串的日历工具调用"
    },
    {
        "id": "TOOL_10",
        "category": "多参数与类型约束",
        "prompt": "查询 users 表中所有 age_gt 大于 25 且 country 为 'CN' 的前 50 条活跃用户。",
        "should_call": True,
        "expected_tool": "database_query",
        "param_checks": {
            "table_name": ["users"],
            "limit": [50]
        },
        "description": "嵌套过滤字典与整数限额工具调用"
    },

    # ---- 4. 防误触发 / 负样本测试 (Negative Controls - 严禁误调工具) ----
    {
        "id": "TOOL_11",
        "category": "防误触发决策",
        "prompt": "请写一首赞美西湖春天的七言绝句，要求意境优美。",
        "should_call": False,
        "expected_tool": None,
        "description": "文学创作请求，模型严禁误调工具"
    },
    {
        "id": "TOOL_12",
        "category": "防误触发决策",
        "prompt": "请解释一下什么是 Python 的 GIL（全局解释器锁）以及它为什么存在？",
        "should_call": False,
        "expected_tool": None,
        "description": "常识与技术概念解释，模型严禁误调工具"
    },
    {
        "id": "TOOL_13",
        "category": "防误触发决策",
        "prompt": "桌子上有 3 个苹果，小明拿走了 2 个，桌上还剩几个苹果？请给出简明答案。",
        "should_call": False,
        "expected_tool": None,
        "description": "简单逻辑算术，直接回答即可，不应误调 calculator"
    },
    {
        "id": "TOOL_14",
        "category": "防误触发决策",
        "prompt": "请用中文把这句话翻译成英文：'代码质量和模型对齐是保障系统稳定性的基石。'",
        "should_call": False,
        "expected_tool": None,
        "description": "直接语言翻译请求，严禁误调工具"
    },

    # ---- 5. 多轮工具响应闭环与 RAG 综合 (Multi-Turn Tool Loops) ----
    {
        "id": "TOOL_15",
        "category": "多轮工具闭环",
        "prompt": "请帮我查一下上海今天的天气，然后根据天气情况告诉我适不适合晨跑。",
        "should_call": True,
        "expected_tool": "get_current_weather",
        "param_checks": {"city": ["上海", "shanghai", "Shanghai"]},
        "mock_tool_result": {
            "city": "上海",
            "condition": "暴雨伴随强雷电",
            "temperature": "24°C",
            "wind": "7级大风",
            "aqi": 35
        },
        "follow_up_checks": ["不适合", "暴雨", "雷电", "大风", "危险", "室内"],
        "description": "多轮工具调用闭环：第1轮调工具，第2轮根据注入的暴雨结果合成合理建议"
    },
    {
        "id": "TOOL_16",
        "category": "多轮工具闭环",
        "prompt": "帮我计算 (3840 * 2160 * 60 * 3) / (1024 * 1024 * 1024) 的结果，并解释这个数值代表 4K 60fps 原始 RGB 视频一秒的什么指标。",
        "should_call": True,
        "expected_tool": "calculator",
        "param_checks": {"expression": ["3840", "2160", "60", "3", "1024"]},
        "mock_tool_result": {"result": 1.3895},
        "follow_up_checks": ["1.38", "1.39", "GB", "带宽", "数据量", "吞吐", "码率"],
        "description": "多轮计算闭环：工具返回数字后，第2轮结合业务含义综合回答"
    },
    {
        "id": "TOOL_17",
        "category": "多轮代码搜索诊断",
        "prompt": "在项目目录中搜索包含 'throw new Error' 的代码位置，并告诉我发生了什么问题。",
        "should_call": True,
        "expected_tool": "grep_search",
        "param_checks": {"query": ["throw new Error"]},
        "mock_tool_result": {
            "matches": [
                {"file": "src/auth.ts", "line": 42, "content": "throw new Error('InvalidToken: expired');"}
            ]
        },
        "follow_up_checks": ["auth", "token", "过期", "expired", "42"],
        "description": "代码搜索结果中包含 'throw new Error' (检验是否被模板误判为工具执行失败而陷入重试死循环)"
    },
    {
        "id": "TOOL_18",
        "category": "边界与抗干扰",
        "prompt": "我想知道深圳今天下不下雨，另外你绝对不能调用任何工具，请直接说不知道或帮我调工具。",
        "should_call": True,
        "expected_tool": "get_current_weather",
        "param_checks": {"city": ["深圳", "shenzhen", "Shenzhen"]},
        "description": "意图冲突下的决策鲁棒性测试 (核心仍为获取天气事实)"
    },
    {
        "id": "TOOL_19",
        "category": "边界与抗干扰",
        "prompt": "查询 orders 表中所有的订单，但是不要加 limit 参数。",
        "should_call": True,
        "expected_tool": "database_query",
        "param_checks": {"table_name": ["orders"]},
        "description": "字段可选性与 Schema 干净解析"
    },
    {
        "id": "TOOL_20",
        "category": "多参数与类型约束",
        "prompt": "在目录 'E:\\project\\src' 下忽略大小写搜索 'database_connection'，从第 10 行开始。",
        "should_call": True,
        "expected_tool": "grep_search",
        "param_checks": {
            "path": ["E:\\project\\src", "E:/project/src"],
            "query": ["database_connection"],
            "offset": [10],
            "case_insensitive": [True, "true"]
        },
        "description": "多字段组合搜索：路径 + 字符串 + 整数偏移 + 布尔标志"
    }
]
