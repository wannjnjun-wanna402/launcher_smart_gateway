#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
anytxt_mcp.py — AnyTXT Searcher 桥接 MCP server（stdio 版，随 llama-server 一起启动）
  把 ATGUI.exe 的 HTTP JSON-RPC 2.0 API（127.0.0.1:9920）封装成 llama.cpp 认识的 stdio MCP 工具。
  由 launcher_main.ps1 经 mcp_servers.json 在启动 llama-server 时自动加载（warmup），无需手动启动。
  前置条件：ATGUI.exe（AnyTXT Searcher）必须正在运行，否则工具调用返回连接失败。
  Usage: python anytxt_mcp.py          （由 llama-server 经 stdio 自动启动）
"""
import json
import sys
import urllib.request

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("ERROR: 需要 mcp 库。pip install mcp", file=sys.stderr)
    sys.exit(1)

ANYTXT_URL = "http://127.0.0.1:9920"
TIMEOUT = 60


def rpc(method, input_params):
    """调用 AnyTXT HTTP JSON-RPC，返回 result 里的 data（dict 或 None）。"""
    payload = {
        "id": 1,
        "jsonrpc": "2.0",
        "method": f"ATRpcServer.Searcher.V1.{method}",
        "params": {"input": input_params},
    }
    req = urllib.request.Request(
        ANYTXT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"error": f"AnyTXT 连接失败（请确认 ATGUI.exe 已启动）: {e}"}
    if "error" in body:
        return {"error": body["error"]}
    result = body.get("result", {})
    data = result.get("data") or {}
    if data.get("output") is not None:
        return data["output"]
    return data if data else {"note": "无输出"}


mcp = FastMCP("anytxt")


@mcp.tool()
def anytxt_search(pattern: str, filter_dir: str = "", filter_ext: str = "*",
                  last_modify_begin: int = 0, last_modify_end: int = 2147483647) -> str:
    """统计本地文件中包含关键词的文件数量（全文搜索，秒级）。
    参数: pattern=搜索关键词(支持引号精确短语/!排除等高级语法), filter_dir=限定文件夹(空=全部),
          filter_ext=扩展名过滤(如 doc;pdf;xlsx, * =全部), 时间戳可选。
    返回: 命中文件数量。数量>0 后应调用 anytxt_get_result 取文件列表。"""
    out = rpc("Search", {
        "pattern": pattern, "filterDir": filter_dir, "filterExt": filter_ext,
        "lastModifyBegin": last_modify_begin, "lastModifyEnd": last_modify_end,
    })
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def anytxt_get_result(pattern: str, filter_dir: str = "", filter_ext: str = "*",
                      limit: int = 300, offset: int = 0, order: int = 0,
                      last_modify_begin: int = 0, last_modify_end: int = 2147483647) -> str:
    """列出匹配关键词的文件列表（含路径/大小/修改时间/FID）。
    参数: pattern=关键词, filter_dir=限定文件夹, filter_ext=扩展名, limit=最多条数(默认300),
          offset=偏移, order=排序(0默认/1时间升/2时间降/3目录升/4目录降)。
    返回: JSON 数组，每项含 fid(后续 GetFragment/GetRawText 需要)、路径、大小、修改时间。"""
    out = rpc("GetResult", {
        "pattern": pattern, "filterDir": filter_dir, "filterExt": filter_ext,
        "limit": str(limit), "offset": offset, "order": order,
        "lastModifyBegin": last_modify_begin, "lastModifyEnd": last_modify_end,
    })
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def anytxt_get_fragment(fid: str, pattern: str) -> str:
    """获取某个文件的一段匹配文本片段（含关键词上下文）。
    参数: fid=来自 anytxt_get_result 的文件ID, pattern=关键词。
    返回: 片段文本。"""
    out = rpc("GetFragment", {"fid": fid, "pattern": pattern})
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def anytxt_get_fragment_all(fid: str, pattern: str) -> str:
    """获取某个文件的全部匹配文本片段（含关键词上下文）。
    参数: fid=文件ID, pattern=关键词。
    返回: 所有片段。"""
    out = rpc("GetFragmentAll", {"fid": fid, "pattern": pattern})
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def anytxt_get_raw_text(fid: str) -> str:
    """按 FID 获取文件的完整原始文本内容（可用于总结/问答/翻译等）。
    参数: fid=来自 anytxt_get_result 的文件ID。
    返回: 全文文本。注意大文件可能很长，建议先 GetFragment 确认命中再取全文。"""
    out = rpc("GetRawTextByFID", {"fid": fid})
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def anytxt_sync_index(folder: str) -> str:
    """同步指定文件夹到 AnyTXT 索引（新文件/修改后需要重新索引才能搜到）。
    参数: folder=要索引的文件夹路径（如 C:/Users/xxx/Documents）。
    返回: 同步结果。"""
    out = rpc("SyncIndex", {"folder": folder})
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def anytxt_ocr(file_path: str) -> str:
    """用离线 OCR 识别图片中的文字（截图/扫描件/图片表格）。
    参数: file_path=图片文件路径（如 G:/1.png）。
    返回: 识别出的文字。"""
    out = rpc("OCR", {"file": file_path})
    return json.dumps(out, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
