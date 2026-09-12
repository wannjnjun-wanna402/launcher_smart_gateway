# -*- coding: utf-8 -*-
"""
sequential_thinking_mcp.py — 渐进式多步深度思维链 MCP 服务
  • 纯 Python 原生实现，零依赖，极速响应 (< 0.1ms)
  • 为大模型提供严谨的草稿思考本，支持假设检验、分支推演与步数动态修订
  • 既可独立作为 stdio MCP 服务供 Cursor/Claude 接入，也可由网关直接调用
"""

import sys
import json
from typing import Optional

try:
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("sequential_thinking")
except ImportError:
    mcp = None


class ThinkingSession:
    def __init__(self):
        self.thoughts = []
        self.branches = {}

    def add_thought(
        self,
        thought: str,
        thought_number: int,
        total_thoughts: int,
        next_thought_needed: bool,
        is_revision: bool = False,
        revises_thought: Optional[int] = None,
        branch_from_thought: Optional[int] = None,
        branch_id: Optional[str] = None,
        needs_more_thoughts: bool = False,
    ) -> dict:
        entry = {
            "thought": thought,
            "thought_number": thought_number,
            "total_thoughts": total_thoughts,
            "next_thought_needed": next_thought_needed,
            "is_revision": is_revision,
            "revises_thought": revises_thought,
            "branch_from_thought": branch_from_thought,
            "branch_id": branch_id,
        }
        self.thoughts.append(entry)

        if branch_id:
            if branch_id not in self.branches:
                self.branches[branch_id] = []
            self.branches[branch_id].append(entry)

        status_msg = f"思考步骤 {thought_number}/{total_thoughts} 已记录。"
        if is_revision:
            status_msg += f" (已修订第 {revises_thought} 步假设)"
        if branch_id:
            status_msg += f" [分支: {branch_id}]"
        if needs_more_thoughts:
            status_msg += " (已动态增加思考预算)"
        if not next_thought_needed:
            status_msg += " 思考链路闭环，可以输出最终结论。"

        return {
            "status": "success",
            "message": status_msg,
            "current_step": thought_number,
            "total_steps": total_thoughts,
            "next_step_needed": next_thought_needed,
            "recorded_steps": len(self.thoughts),
        }

    def reset(self):
        self.thoughts.clear()
        self.branches.clear()


# 全局单例会话
_default_session = ThinkingSession()


def sequential_thinking(
    thought: str,
    thought_number: int,
    total_thoughts: int,
    next_thought_needed: bool,
    is_revision: bool = False,
    revises_thought: Optional[int] = None,
    branch_from_thought: Optional[int] = None,
    branch_id: Optional[str] = None,
    needs_more_thoughts: bool = False,
) -> str:
    """供外部或工具调用直接调用的标准函数"""
    res = _default_session.add_thought(
        thought=thought,
        thought_number=thought_number,
        total_thoughts=total_thoughts,
        next_thought_needed=next_thought_needed,
        is_revision=is_revision,
        revises_thought=revises_thought,
        branch_from_thought=branch_from_thought,
        branch_id=branch_id,
        needs_more_thoughts=needs_more_thoughts,
    )
    return json.dumps(res, ensure_ascii=False)


# FastMCP 工具注册
if mcp:
    @mcp.tool()
    def process_thought(
        thought: str,
        thought_number: int,
        total_thoughts: int,
        next_thought_needed: bool,
        is_revision: bool = False,
        revises_thought: Optional[int] = None,
        branch_from_thought: Optional[int] = None,
        branch_id: Optional[str] = None,
        needs_more_thoughts: bool = False,
    ) -> str:
        """渐进式记录与验证思维链步骤。用于复杂算法设计、系统架构推演、多步逻辑推导等深度任务。"""
        return sequential_thinking(
            thought=thought,
            thought_number=thought_number,
            total_thoughts=total_thoughts,
            next_thought_needed=next_thought_needed,
            is_revision=is_revision,
            revises_thought=revises_thought,
            branch_from_thought=branch_from_thought,
            branch_id=branch_id,
            needs_more_thoughts=needs_more_thoughts,
        )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print(sequential_thinking("分析问题核心边界", 1, 3, True))
        print(sequential_thinking("验证第二步解决方案", 2, 3, True))
        print(sequential_thinking("确认最优解并形成结论", 3, 3, False))
    elif mcp:
        mcp.run(transport="stdio")
    else:
        print("FastMCP 未安装，仅作为纯 Python 模块运行。")
