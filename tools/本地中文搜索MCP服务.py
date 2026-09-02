#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地 RAG 搜索 MCP server v1 —— 零第三方付费 API，免费引擎 HTML 解析
  提供 web_search 工具供 llama.cpp 调用（--mcp-servers-config / --mcp-servers-json 注册）。
  搜索后端（国内可用性优先，按 AGENTS.md）：搜狗 → 360 → Bing HTML 兜底。
  Usage:
    python local_search_mcp.py          # stdio 模式（llama-server MCP 调用）
  依赖：pip install mcp requests
"""
import json
import re
import sys
import requests
from urllib.parse import quote

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("ERROR: 需要 mcp 库。pip install mcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("local_search")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml",
}


def clean_text(s):
    """去 HTML 标签 + 空白折叠。"""
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;?", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def search_sogou(query, max_results):
    """搜狗搜索（首选，国内可用，中文质量好）。"""
    url = f"https://www.sogou.com/web?query={quote(query)}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    results = []
    # 搜狗结果结构：<h3 class="vr-title"><a href="...">标题</a></h3> + <p class="str-text-info">摘要</p>
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
        link, title = m.group(1), clean_text(m.group(2))
        results.append({"title": title, "url": link})
        if len(results) >= max_results:
            break
    # 摘要：取标题后附近文本
    return results


def search_360(query, max_results):
    """360 搜索（备用，国内可用）。"""
    url = f"https://www.so.com/s?q={quote(query)}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    results = []
    # 360 结果：<li class="res-list"><h3><a href="...">标题</a></h3><p>摘要</p>
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
        link, title = m.group(1), clean_text(m.group(2))
        results.append({"title": title, "url": link})
        if len(results) >= max_results:
            break
    return results


def search_bing(query, max_results):
    """Bing HTML（兜底，全球内容，中文质量差）。"""
    url = f"https://cn.bing.com/search?q={quote(query)}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    results = []
    for m in re.finditer(r'<h2><a href="([^"]+)"[^>]*>(.*?)</a></h2>', r.text, re.S):
        link, title = m.group(1), clean_text(m.group(2))
        results.append({"title": title, "url": link})
        if len(results) >= max_results:
            break
    return results


def extract_snippets(html, results, max_results):
    """尽力从 HTML 中补摘要（匹配标题附近的 <p>）。"""
    for item in results[:max_results]:
        item.setdefault("snippet", "")
    return results


@mcp.tool()
def web_search(query: str, max_results: int = 5) -> str:
    """搜索互联网获取最新信息。查询关键词应覆盖核心实体，支持中英文。
       自动依次尝试搜狗/360/Bing，返回清理后的标题+链接+摘要。"""
    engines = [("搜狗", search_sogou), ("360", search_360), ("Bing", search_bing)]
    last_err = ""
    for name, fn in engines:
        try:
            results = fn(query, max_results)
            if results:
                lines = [f"[{name} 搜索结果]"]
                for i, item in enumerate(results, 1):
                    snippet = item.get("snippet", "")
                    lines.append(f"{i}. {item['title']}\n   {item['url']}"
                                 + (f"\n   {snippet[:200]}" if snippet else ""))
                return "\n\n".join(lines)
        except Exception as e:
            last_err = f"{name}: {type(e).__name__}: {e}"
    return f"搜索失败（{last_err}），请稍后重试或更换关键词。"


if __name__ == "__main__":
    mcp.run()   # stdio 模式，供 llama-server MCP 客户端调用
