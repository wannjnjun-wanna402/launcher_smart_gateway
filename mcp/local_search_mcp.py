#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
local_search_mcp.py — 本地多引擎 RAG 检索 MCP 服务 (免 API Key，纯 HTML 直连)
  提供 web_search 工具供 AI 客户端调用。
  采用国内合规高可用引擎：搜狗 (首选) -> 360搜索 (备选) -> 必应 HTML (兜底)。
  严格遵循 AGENTS.md 搜索规范，不依赖受限外部网络。
  Usage:
    python local_search_mcp.py       # stdio 模式，供 MCP 客户端调用
"""
import json
import re
import sys
from urllib.parse import quote
import requests

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("ERROR: 需要 mcp 库。请执行 pip install mcp requests", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("local_search")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml",
}


def clean_text(s: str) -> str:
    """去除 HTML 标签并规范化空白字符"""
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;?", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def search_sogou(query: str, max_results: int):
    """搜狗搜索（国内首选，中文内容质量好）"""
    url = f"https://www.sogou.com/web?query={quote(query)}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    results = []
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
        link, title = m.group(1), clean_text(m.group(2))
        results.append({"title": title, "url": link})
        if len(results) >= max_results:
            break
    return results


def search_360(query: str, max_results: int):
    """360 搜索（国内高可用备选）"""
    url = f"https://www.so.com/s?q={quote(query)}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    results = []
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
        link, title = m.group(1), clean_text(m.group(2))
        results.append({"title": title, "url": link})
        if len(results) >= max_results:
            break
    return results


def search_bing(query: str, max_results: int):
    """Bing HTML 搜索（兜底，覆盖全球内容）"""
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


@mcp.tool()
def web_search(query: str, max_results: int = 5) -> str:
    """获取最新网络信息。查询关键词应是核心实体，支持中英双语。
    自动依次尝试 搜狗 / 360 / 必应，返回格式化标题、链接与摘要。"""
    engines = [("搜狗", search_sogou), ("360", search_360), ("Bing", search_bing)]
    last_err = ""
    for name, fn in engines:
        try:
            results = fn(query, max_results)
            if results:
                lines = [f"[{name} 搜索结果]"]
                for i, item in enumerate(results, 1):
                    snippet = item.get("snippet", "")
                    lines.append(
                        f"{i}. {item['title']}\n   {item['url']}"
                        + (f"\n   {snippet[:200]}" if snippet else "")
                    )
                return "\n\n".join(lines)
        except Exception as e:
            last_err = f"{name}: {type(e).__name__}: {e}"

@mcp.tool()
def read_web_page(url: str, max_chars: int = 6000) -> str:
    """深度阅读并提取网页正文内容（支持新闻、文章、博客、文档等各类网页）。
    去除广告、导航栏与无关脚本，返回纯净的 Markdown 格式正文，便于深入分析与提炼总结。"""
    try:
        import urllib.request
        import gzip
        import zlib
        
        target_url = url.strip()
        if not target_url.startswith("http://") and not target_url.startswith("https://"):
            target_url = "https://" + target_url
            
        req = urllib.request.Request(
            target_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate"
            }
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read()
            ce = resp.info().get("Content-Encoding")
            if ce == "gzip":
                raw = gzip.decompress(raw)
            elif ce == "deflate":
                raw = zlib.decompress(raw)
            charset = resp.info().get_content_charset() or "utf-8"
            html = raw.decode(charset, errors="replace")

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "iframe", "noscript", "header", "svg", "aside"]):
                tag.decompose()
            title = soup.title.string.strip() if (soup.title and soup.title.string) else ""
            if not title:
                h1 = soup.find("h1")
                title = h1.get_text().strip() if h1 else "网页正文"
            main_content = soup.find("article") or soup.find("main") or soup.find(class_=re.compile(r"(content|article|post|detail)", re.I)) or soup.body or soup
            lines = []
            for elem in main_content.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "code"]):
                t = elem.get_text().strip()
                if not t:
                    continue
                name = elem.name
                if name == "h1":
                    lines.append(f"# {t}")
                elif name == "h2":
                    lines.append(f"## {t}")
                elif name == "h3":
                    lines.append(f"### {t}")
                elif name == "li":
                    lines.append(f"- {t}")
                elif name in ("pre", "code"):
                    lines.append(f"```\n{t}\n```")
                else:
                    lines.append(t)
            body_text = "\n\n".join(lines) if lines else clean_text(main_content.get_text())
        except Exception:
            title = "网页提取"
            body_text = clean_text(html)

        if len(body_text) > max_chars:
            body_text = body_text[:max_chars] + f"\n\n[...正文已截断，共 {len(body_text)} 字符，已展示前 {max_chars} 字符...]"

        return f"# {title}\n\n**来源 URL**: {target_url}\n\n---\n\n{body_text}"
    except Exception as e:
        return f"网页阅读提取失败 ({url}): {type(e).__name__}: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
