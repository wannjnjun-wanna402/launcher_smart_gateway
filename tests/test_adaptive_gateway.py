#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================================
 🧪 任务自适应智能动态采样与网关全自动化测试套件 (TestAdaptiveGateway)
 验证环境: Tesla V100 + llama.cpp b10917 + Qwen3.8-27B 约束模板 + 上下文防爆剪枝
====================================================================================
"""

import os
import sys
import unittest
import json
import jinja2

# 将当前根目录添加到 sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from qwen_tool_proxy import (
    TaskType,
    TASK_SAMPLING_MATRIX,
    TaskAdaptiveEngine,
    translate_anthropic_to_openai,
    enforce_context_safety_guard,
    backend_manager
)


class TestTaskClassification(unittest.TestCase):
    """1. 意图特征提取与六维任务分类测试"""

    def test_code_classification(self):
        prompts = [
            "帮我用 Python 写一个支持泛型的 LRU 缓存类，要求包含 get 和 put 方法",
            "```python\ndef test(): pass\n``` 这段代码为什么会报 SyntaxError？帮我修复并重构",
            "写一条复杂 SQL，联合查询 orders 和 users 表，统计上月交易总额",
            "分析这个 Segmentation Fault 的原因，并写一个单元测试覆盖边界"
        ]
        for p in prompts:
            payload = {"messages": [{"role": "user", "content": p}]}
            task = TaskAdaptiveEngine.classify_task(payload, p)
            self.assertEqual(task, TaskType.CODE, f"Failed to classify as CODE: {p}")

    def test_math_logic_classification(self):
        prompts = [
            "求解方程组：2x + 3y = 7 且 5x - y = 9，给出详细推导过程",
            "用动态规划 dp[i][j] 解决 LeetCode 300 最长递增子序列，分析时间复杂度与空间复杂度",
            "证明素数有无穷多个，使用反证法并写出欧几里得的经典证明",
            "根据贝叶斯定理计算在已知假阳性率情况下的患病后验概率"
        ]
        for p in prompts:
            payload = {"messages": [{"role": "user", "content": p}]}
            task = TaskAdaptiveEngine.classify_task(payload, p)
            self.assertEqual(task, TaskType.MATH_LOGIC, f"Failed to classify as MATH_LOGIC: {p}")

    def test_structured_tool_classification(self):
        # A. 带 tools 字段
        payload_with_tools = {
            "messages": [{"role": "user", "content": "今天天气怎么样"}],
            "tools": [{"type": "function", "function": {"name": "get_weather", "description": "Get weather"}}]
        }
        task = TaskAdaptiveEngine.classify_task(payload_with_tools, "今天天气怎么样")
        self.assertEqual(task, TaskType.STRUCTURED_TOOL)

        # B. 声明 response_format 为 json_object
        payload_json_fmt = {
            "messages": [{"role": "user", "content": "提取用户信息"}],
            "response_format": {"type": "json_object"}
        }
        task = TaskAdaptiveEngine.classify_task(payload_json_fmt, "提取用户信息")
        self.assertEqual(task, TaskType.STRUCTURED_TOOL)

        # C. 文本包含输出 JSON 约束
        payload_text_json = {"messages": [{"role": "user", "content": "提取上述简历中的姓名和手机号，输出格式为 json 键值对"}]}
        task = TaskAdaptiveEngine.classify_task(payload_text_json, "提取上述简历中的姓名和手机号，输出格式为 json 键值对")
        self.assertEqual(task, TaskType.STRUCTURED_TOOL)

    def test_factual_rag_classification(self):
        prompts = [
            "根据以上资料内容，总结该公司的 Q2 财报净利润增长率是多少？",
            "参考上下文背景资料：小明在2024年入职。问：小明入职多久了？",
            "从上述检索结果和参考信息中找出该产品的三项核心技术指标"
        ]
        for p in prompts:
            payload = {"messages": [{"role": "user", "content": p}]}
            task = TaskAdaptiveEngine.classify_task(payload, p)
            self.assertEqual(task, TaskType.FACTUAL_RAG, f"Failed to classify as FACTUAL_RAG: {p}")

    def test_creative_classification(self):
        prompts = [
            "写一首关于星际穿越与孤独的现代诗歌，语气生动而深沉",
            "以赛博朋克为背景，写一个关于仿生人觉醒的短篇科幻故事",
            "帮我润色这篇求职自荐信，使其语言更加地道生动、充满感染力",
            "设定你是一个中世纪吟游诗人，帮我们的新公会起五个霸气的名字"
        ]
        for p in prompts:
            payload = {"messages": [{"role": "user", "content": p}]}
            task = TaskAdaptiveEngine.classify_task(payload, p)
            self.assertEqual(task, TaskType.CREATIVE, f"Failed to classify as CREATIVE: {p}")

    def test_general_chat_classification(self):
        prompts = [
            "你好，今天过得怎么样？",
            "早上好！",
            "哈哈确实很有趣"
        ]
        for p in prompts:
            payload = {"messages": [{"role": "user", "content": p}]}
            task = TaskAdaptiveEngine.classify_task(payload, p)
            self.assertEqual(task, TaskType.GENERAL_CHAT, f"Failed to classify as GENERAL_CHAT: {p}")

    def test_explicit_override_channels(self):
        # Header 强制覆盖
        payload = {"messages": [{"role": "user", "content": "你好"}]}
        task = TaskAdaptiveEngine.classify_task(payload, "你好", headers={"X-Task-Type": "code"})
        self.assertEqual(task, TaskType.CODE)

        # Prompt 内联强制覆盖
        payload = {"messages": [{"role": "user", "content": "<|task_creative|> 写一个计算器"}]}
        task = TaskAdaptiveEngine.classify_task(payload, "<|task_creative|> 写一个计算器")
        self.assertEqual(task, TaskType.CREATIVE)


class TestContextAnchorAndFollowup(unittest.TestCase):
    """2. 多轮对话上下文追溯与首轮锚点测试"""

    def test_short_followup_inherits_initial_goal(self):
        messages = [
            {"role": "user", "content": "帮我用 C++ 实现一个高性能多线程网络调度器，包含 epoll 与线程池"},
            {"role": "assistant", "content": "好的，这是基础架构代码..."},
            {"role": "user", "content": "继续写"}  # 极其简短的跟进
        ]
        intent = TaskAdaptiveEngine.extract_intent_text(messages)
        self.assertIn("高性能多线程网络调度器", intent)
        task = TaskAdaptiveEngine.classify_task({"messages": messages}, intent)
        self.assertEqual(task, TaskType.CODE, "短跟进提问未能从首轮意图锚点继承 CODE 任务类型")


class TestAdaptiveSamplingInjection(unittest.TestCase):
    """3. 动态采样矩阵装配与客户端参数保护测试"""

    def test_code_sampling_defaults(self):
        payload = {
            "messages": [{"role": "user", "content": "实现一个红黑树的插入与旋转逻辑"}],
            "temperature": 0.7  # 模拟客户端传了通用默认值
        }
        task_type, preset, applied, effort, budget, tag = TaskAdaptiveEngine.apply_adaptive_sampling(
            payload, intent_text="实现一个红黑树的插入与旋转逻辑", estimated_tokens=1000
        )
        self.assertEqual(task_type, TaskType.CODE)
        self.assertEqual(payload["temperature"], 0.10, "代码任务温度未自动收敛为 0.10")
        self.assertEqual(payload["top_p"], 0.85)
        self.assertEqual(payload["min_p"], 0.05, "未注入 llama.cpp b10917 min_p 采样")
        self.assertEqual(payload["dry_multiplier"], 0.80, "未注入 DRY 防循环重复采样")
        self.assertEqual(effort, "xhigh")
        self.assertEqual(budget, 8192)
        self.assertEqual(payload["chat_template_kwargs"]["reasoning_effort"], "xhigh")

    def test_large_code_promoted_to_xhigh(self):
        payload = {"messages": [{"role": "user", "content": "重构整个系统架构设计"}]}
        _, _, _, effort, budget, _ = TaskAdaptiveEngine.apply_adaptive_sampling(
            payload, intent_text="重构整个系统架构设计", estimated_tokens=4500
        )
        self.assertEqual(effort, "xhigh", "长代码/超长工程任务未提档为 xhigh")
        self.assertEqual(budget, 8192)

    def test_tool_sampling_defaults(self):
        payload = {
            "messages": [{"role": "user", "content": "查询北京天气"}],
            "tools": [{"type": "function", "function": {"name": "query_weather"}}],
            "temperature": 0.8  # 通用默认值
        }
        task_type, _, _, effort, budget, _ = TaskAdaptiveEngine.apply_adaptive_sampling(
            payload, intent_text="查询北京天气"
        )
        self.assertEqual(task_type, TaskType.STRUCTURED_TOOL)
        self.assertEqual(payload["temperature"], 0.05, "工具调用未收敛为超稳定温度 0.05")
        self.assertEqual(payload["min_p"], 0.02)
        self.assertEqual(effort, "none", "工具调用未关闭冗长思考预算")
        self.assertEqual(budget, 0)
        self.assertFalse(payload["enable_thinking"])

    def test_creative_sampling_defaults(self):
        payload = {
            "messages": [{"role": "user", "content": "写一篇优美的抒情散文"}],
        }
        task_type, _, _, effort, budget, _ = TaskAdaptiveEngine.apply_adaptive_sampling(
            payload, intent_text="写一篇优美的抒情散文"
        )
        self.assertEqual(task_type, TaskType.CREATIVE)
        self.assertEqual(payload["temperature"], 0.88, "文学创作未适度激发多样性温度")
        self.assertEqual(payload["top_p"], 0.98)
        self.assertEqual(payload["min_p"], 0.03)
        self.assertEqual(payload["dry_multiplier"], 0.50)
        self.assertEqual(effort, "low")
        self.assertEqual(budget, 512)

    def test_client_explicit_custom_param_preserved(self):
        # 客户端显式指定 temperature = 0.0 (严苛贪婪模式)，网关必须予以保留
        payload = {
            "messages": [{"role": "user", "content": "写一个贪吃蛇游戏"}],
            "temperature": 0.0,
            "min_p": 0.08
        }
        TaskAdaptiveEngine.apply_adaptive_sampling(
            payload, intent_text="写一个贪吃蛇游戏"
        )
        self.assertEqual(payload["temperature"], 0.0, "客户端显式定制 temperature=0.0 被意外覆盖")
        self.assertEqual(payload["min_p"], 0.08, "客户端显式定制 min_p=0.08 被意外覆盖")


class TestAnthropicTranslation(unittest.TestCase):
    """4. Anthropic 协议双向转译与自适应无缝衔接测试"""

    def test_anthropic_preserves_none_temperature(self):
        anthropic_body = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "def fib(n): pass 补全代码"}]
            # 未传 temperature
        }
        openai_payload = translate_anthropic_to_openai(anthropic_body)
        self.assertNotIn("temperature", openai_payload, "Anthropic 转译层过早写死了 0.7")

        # 送入自适应引擎
        intent = TaskAdaptiveEngine.extract_intent_text(openai_payload["messages"])
        TaskAdaptiveEngine.apply_adaptive_sampling(openai_payload, intent)
        self.assertEqual(openai_payload["temperature"], 0.10, "Anthropic 请求未能自动自适应代码温度 0.10")


class TestJinjaTemplateIntegration(unittest.TestCase):
    """5. 与本机 Qwen3.8-27B Jinja 约束模板 (v22.5) 渲染一致性测试"""

    @classmethod
    def setUpClass(cls):
        template_path = os.path.join(ROOT_DIR, "chat_template_qwen_fixed.jinja")
        with open(template_path, "r", encoding="utf-8") as f:
            cls.template_str = f.read()
        cls.env = jinja2.Environment()
        cls.jinja_template = cls.env.from_string(cls.template_str)

    def test_jinja_renders_xhigh_reasoning(self):
        rendered = self.jinja_template.render(
            messages=[{"role": "user", "content": "复杂算法推导"}],
            tools=None,
            add_generation_prompt=True,
            enable_thinking=True,
            reasoning_effort="xhigh"
        )
        self.assertIn("Reasoning effort is set to xhigh", rendered)

    def test_jinja_renders_none_reasoning(self):
        rendered = self.jinja_template.render(
            messages=[{"role": "user", "content": "提取为 JSON 键值对"}],
            tools=None,
            add_generation_prompt=True,
            enable_thinking=False,
            reasoning_effort="none"
        )
        self.assertIn("<think>\n\n</think>", rendered)


class TestContextGuardCoordination(unittest.TestCase):
    """6. 智能上下文安全防爆剪枝协同测试"""

    def test_guard_preserves_sampling_parameters(self):
        # 构造一条带有自适应采样参数的大请求
        large_content = "def process_data():\n" + ("    x = 1\n" * 2000)
        payload = {
            "messages": [
                {"role": "system", "content": "You are an expert coder."},
                {"role": "user", "content": "首轮目标：构建完整交易系统"},
                {"role": "assistant", "content": "收到，我将严格遵循。"},
                {"role": "user", "content": f"请看这段代码并重构:\n{large_content}"}
            ],
            "temperature": 0.10,
            "min_p": 0.05,
            "dry_multiplier": 0.80,
            "reasoning_effort": "high",
            "chat_template_kwargs": {"reasoning_effort": "high", "enable_thinking": True}
        }

        # 触发防爆剪枝 (设置一个较紧凑的 token 安全线)
        guarded, triggered, saved = enforce_context_safety_guard(payload, max_safe_tokens=1000, target_safe_tokens=800)

        # 断言采样参数完好无损
        self.assertEqual(guarded["temperature"], 0.10)
        self.assertEqual(guarded["min_p"], 0.05)
        self.assertEqual(guarded["dry_multiplier"], 0.80)
        self.assertEqual(guarded["reasoning_effort"], "high")
        self.assertIn("chat_template_kwargs", guarded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
