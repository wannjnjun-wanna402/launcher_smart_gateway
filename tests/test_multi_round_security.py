#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================================
 🛡️ 智能网关多轮实际使用安全、健壮性与看板一致性多轮综合测试套件
 (Multi-Round Security, Robustness & Dashboard Consistency Audit Suite)
====================================================================================
"""

import os
import sys
import unittest
import json
import time
import threading
import re
from http.server import HTTPServer

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from qwen_tool_proxy import (
    TaskType,
    TASK_SAMPLING_MATRIX,
    TaskAdaptiveEngine,
    tracker,
    DASHBOARD_HTML,
    enforce_context_safety_guard,
    ThreadedHTTPServer,
    TransparentProxyHandler
)


class TestMultiTurnSecurityWorkflow(unittest.TestCase):
    """1. 真实多轮对话场景意图演进与安全状态保持测试"""

    def test_multi_turn_development_flow(self):
        """模拟真实的 5 轮软件工程重构会话，验证意图演进和采样参数连续性"""
        conversation = []

        # Round 1: 发起复杂系统架构设计
        conversation.append({"role": "user", "content": "帮我设计一个高并发分布式任务队列系统，包含 Broker、Worker 和心跳机制"})
        p1 = {"messages": list(conversation)}
        t1, preset1, _, effort1, budget1, _ = TaskAdaptiveEngine.apply_adaptive_sampling(p1, TaskAdaptiveEngine.extract_intent_text(conversation))
        self.assertEqual(t1, TaskType.CODE)
        self.assertEqual(p1["temperature"], 0.10)
        self.assertEqual(effort1, "xhigh")
        conversation.append({"role": "assistant", "content": "已为您规划以下架构：..."})

        # Round 2: 要求编写具体核心代码
        conversation.append({"role": "user", "content": "请用 Python 实现其中 Worker 节点的异步心跳发送逻辑，带自动重连"})
        p2 = {"messages": list(conversation)}
        t2, preset2, _, effort2, budget2, _ = TaskAdaptiveEngine.apply_adaptive_sampling(p2, TaskAdaptiveEngine.extract_intent_text(conversation))
        self.assertEqual(t2, TaskType.CODE)
        self.assertEqual(p2["temperature"], 0.10)
        self.assertEqual(p2["min_p"], 0.05)
        conversation.append({"role": "assistant", "content": "这是 Worker 异步代码..."})

        # Round 3: 极简短跟进指令（检验上下文锚点继承）
        conversation.append({"role": "user", "content": "继续写"})
        p3 = {"messages": list(conversation)}
        intent3 = TaskAdaptiveEngine.extract_intent_text(conversation)
        t3, _, _, effort3, _, _ = TaskAdaptiveEngine.apply_adaptive_sampling(p3, intent3)
        self.assertEqual(t3, TaskType.CODE, f"短跟进未能保持代码意图: {intent3}")
        self.assertEqual(p3["temperature"], 0.10)
        conversation.append({"role": "assistant", "content": "继续补全重连逻辑..."})

        # Round 4: 切换为数学逻辑（计算哈希环槽位分布平衡度）
        conversation.append({"role": "user", "content": "请计算一致性哈希在 100 个虚拟节点下的方差与分布平衡度证明"})
        p4 = {"messages": list(conversation)}
        intent4 = TaskAdaptiveEngine.extract_intent_text(conversation)
        t4, _, _, effort4, budget4, _ = TaskAdaptiveEngine.apply_adaptive_sampling(p4, intent4)
        self.assertEqual(t4, TaskType.MATH_LOGIC)
        self.assertEqual(p4["temperature"], 0.25)
        self.assertEqual(effort4, "xhigh")
        conversation.append({"role": "assistant", "content": "数学证明如下：..."})

        # Round 5: 会话自然结束（闲聊致谢）
        conversation.append({"role": "user", "content": "太棒了，非常感谢你的细致解答，辛苦了！"})
        p5 = {"messages": list(conversation)}
        intent5 = TaskAdaptiveEngine.extract_intent_text(conversation)
        t5, _, _, effort5, _, _ = TaskAdaptiveEngine.apply_adaptive_sampling(p5, intent5)
        self.assertEqual(t5, TaskType.GENERAL_CHAT)
        self.assertEqual(p5["temperature"], 0.70)


class TestEdgeCaseAndAdversarialInput(unittest.TestCase):
    """2. 极端边界、恶意畸形与安全降级测试"""

    def test_empty_and_null_messages(self):
        """空列表、含有 None、空字符串、不可见空白字符"""
        edge_payloads = [
            {"messages": []},
            {"messages": [{"role": "user", "content": None}]},
            {"messages": [{"role": "user", "content": ""}]},
            {"messages": [{"role": "user", "content": "   \n\t  \u200b "}]},
            {"messages": [{"role": "user", "content": [{"type": "text", "text": None}]}]}
        ]
        for p in edge_payloads:
            intent = TaskAdaptiveEngine.extract_intent_text(p.get("messages", []))
            # 必须安全降级为通用闲聊，绝不抛出任何异常崩溃
            t, preset, _, effort, _, _ = TaskAdaptiveEngine.apply_adaptive_sampling(p, intent)
            self.assertEqual(t, TaskType.GENERAL_CHAT)
            self.assertEqual(p.get("temperature"), 0.70)

    def test_huge_adversarial_prompt(self):
        """恶意构造超长单轮输入 (10万字符)，测试正则引擎防御 ReDoS"""
        huge_str = ("def foo():\n    pass\n" * 2000) + "帮我审查这段代码是否有漏洞"
        payload = {"messages": [{"role": "user", "content": huge_str}]}
        start_t = time.time()
        intent = TaskAdaptiveEngine.extract_intent_text(payload["messages"])
        t = TaskAdaptiveEngine.classify_task(payload, intent)
        cost_ms = (time.time() - start_t) * 1000
        self.assertEqual(t, TaskType.CODE)
        self.assertLess(cost_ms, 50.0, f"超长正则匹配耗时过长: {cost_ms:.2f}ms (可能存在 ReDoS 风险)")

    def test_invalid_and_malformed_headers(self):
        """测试畸形与未知 x-task-type 请求头"""
        payload = {"messages": [{"role": "user", "content": "你好"}]}
        malformed_headers = [
            {"X-Task-Type": ""},
            {"X-Task-Type": "unknown_task_123456"},
            {"X-Task-Type": "'; DROP TABLE users; --"},
            {"X-Task-Type": "<script>alert(1)</script>"}
        ]
        for h in malformed_headers:
            t = TaskAdaptiveEngine.classify_task(payload, "你好", headers=h)
            self.assertEqual(t, TaskType.GENERAL_CHAT, "畸形 Header 未能安全回退至 GENERAL_CHAT")


class TestConcurrentThreadSafety(unittest.TestCase):
    """3. 多线程高并发调用安全性测试 (Zero Race Condition)"""

    def test_concurrent_sampling_and_tracker_hits(self):
        """20 个线程并发发起自适应采样装配与打点，检验锁安全性"""
        thread_count = 20
        iterations_per_thread = 25
        errors = []

        def worker(thread_idx):
            try:
                task_choices = [
                    (TaskType.CODE, "def worker(): pass"),
                    (TaskType.MATH_LOGIC, "证明素数定理"),
                    (TaskType.CREATIVE, "写一首优美的十四行诗")
                ]
                for i in range(iterations_per_thread):
                    task_expected, text = task_choices[(thread_idx + i) % len(task_choices)]
                    payload = {"messages": [{"role": "user", "content": text}]}
                    intent = TaskAdaptiveEngine.extract_intent_text(payload["messages"])
                    t_res, preset, _, effort, _, _ = TaskAdaptiveEngine.apply_adaptive_sampling(payload, intent)
                    tracker.record_task_adaptive_hit(task_type=t_res, reasoning_effort=effort)
                    # 验证快照完备
                    self.assertIsNotNone(TaskAdaptiveEngine.last_decision)
            except Exception as e:
                errors.append(f"Thread {thread_idx} error: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10.0)

        self.assertEqual(len(errors), 0, f"并发调用发生异常: {errors}")
        st = tracker.get_stats()
        today_adp = st.get("adaptive_sampling", {})
        self.assertGreaterEqual(today_adp.get("today_total", 0), thread_count * iterations_per_thread)


class TestDashboardConsistency(unittest.TestCase):
    """4. 网页看板前后端信息一致性全面核验 (0 悬空 DOM 选择器)"""

    def test_dashboard_html_dom_ids_exist(self):
        """验证前端 JS 中所操作的所有自适应 DOM 元素在 DASHBOARD_HTML 中 100% 存在"""
        required_dom_ids = [
            "adaptive-sampling-panel",
            "adaptive-total-count",
            "adaptive-today-total-badge",
            "adaptive-cnt-code",
            "adaptive-cnt-math",
            "adaptive-cnt-tool",
            "adaptive-cnt-rag",
            "adaptive-cnt-creative",
            "adaptive-cnt-chat",
            "adaptive-last-time",
            "adaptive-last-type",
            "adaptive-last-temp",
            "adaptive-last-minp",
            "adaptive-last-topp",
            "adaptive-last-dry",
            "adaptive-last-effort",
            "banner-vision-today-imgs",
            "banner-vision-today-time",
            "banner-vision-total-imgs",
            "banner-vision-cache-count",
            "banner-vision-cache-hits",
            "kpi-vis-unique-imgs",
            "kpi-vis-disp-tasks",
            "kpi-vis-recv-tasks",
            "kpi-vis-cache-imgs",
            "kpi-vis-sub"
        ]
        for dom_id in required_dom_ids:
            pattern = f'id="{dom_id}"'
            self.assertIn(pattern, DASHBOARD_HTML, f"前端 DOM 悬空缺失: {dom_id} 未在 DASHBOARD_HTML 中找到！")

    def test_stats_api_structure_consistency(self):
        """验证 tracker.get_stats() 输出结构满足看板 JS 数据期望"""
        st = tracker.get_stats()
        self.assertIn("adaptive_sampling", st)
        self.assertIn("task_types", st)
        self.assertIn("vision_summary", st)
        vs = st["vision_summary"]
        for k in ("received_tasks", "received_images", "dispatched_tasks", "dispatched_images", "cached_images", "today_images", "today_duration_s", "cache_count"):
            self.assertIn(k, vs, f"vision_summary 结构缺少必需键: {k}")
        adp = st["adaptive_sampling"]
        self.assertTrue(adp.get("enabled"))
        self.assertIn("today_counts", adp)
        self.assertIn("last_decision", adp)
        last_d = adp["last_decision"]
        for k in ("name_cn", "temperature", "min_p", "top_p", "effort", "timestamp"):
            self.assertIn(k, last_d, f"last_decision 结构缺少必需键: {k}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
