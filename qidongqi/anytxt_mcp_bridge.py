# -*- coding: utf-8 -*-
"""
Anytxt MCP Bridge (stdio MCP server)
=====================================
把 AnyTxt Searcher 的 HTTP JSON-RPC 2.0 API 包装成 MCP tools，
供 llama.cpp (--mcp-servers-config) 在启动时一起挂载。

Anytxt API 宿主在 ATGUI 进程内，监听 127.0.0.1:9920。
所有方法均为 ATRpcServer.Searcher.V1.<Method>，POST JSON-RPC 2.0。

启动方式（由 mcp-servers.json 调用）：
    <venv>/python.exe anytxt_mcp_bridge.py

依赖：mcp[cli] + requests（已装于隔离 venv）。
"""

import json
import sys
import urllib.request
import urllib.error

from mcp.server.fastmcp import FastMCP

ANYTXT_URL = "http://127.0.0.1:9920"
USER_AGENT = {"Content-Type": "application/json", "Accept": "application/json"}

mcp = FastMCP("anytxt")


def _rpc(method: str, params_input: dict) -> dict:
    """调用 Anytxt HTTP JSON-RPC 2.0，返回 result.data 或抛出可读错误。"""
    payload = {
        "id": 1,
        "jsonrpc": "2.0",
        "method": f"ATRpcServer.Searcher.V1.{method}",
        "params": {"input": params_input},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ANYTXT_URL, data=data, headers=USER_AGENT, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"无法连接 Anytxt API ({ANYTXT_URL})：{e}。"
            "请确认 AnyTxt 已启动且 ATGUI 进程正在运行（API 由 ATGUI 托管）。"
        )
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Anytxt 返回非 JSON：{e}\n原始返回：{body[:500]}")
    if "error" in parsed:
        raise RuntimeError(f"Anytxt 返回错误：{parsed['error']}")
    result = parsed.get("result", {})
    errno = result.get("errno")
    if errno not in (None, 0):
        # errno != 0 时 data 可能仍含信息，直接返回供调用方判断
        return result
    return result.get("data", result)


@mcp.tool()
def anytxt_search(pattern: str, filter_dir: str = "C:/", filter_ext: str = "*",
                  last_modify_begin: int = 0, last_modify_end: int = 2147483647) -> str:
    """搜索匹配关键词的文件数量。

    Args:
        pattern: 搜索关键词，支持高级语法，如 "this is"、!that、(See Help -> Advanced Search Syntax)
        filter_dir: 限定搜索目录，如 C:/ 或 C:/some path
        filter_ext: 文件扩展名过滤，* 表示全部；多个用分号分隔如 doc;pdf;ppt
        last_modify_begin: 修改时间下限（Unix 时间戳，0=不限制）
        last_modify_end: 修改时间上限（Unix 时间戳，2147483647=不限制）
    """
    out = _rpc("Search", {
        "pattern": pattern,
        "filterDir": filter_dir,
        "filterExt": filter_ext,
        "lastModifyBegin": last_modify_begin,
        "lastModifyEnd": last_modify_end,
    })
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def anytxt_get_context(pattern: str, filter_dir: str = "C:/", filter_ext: str = "*",
                       last_modify_begin: int = 0, last_modify_end: int = 2147483647,
                       limit: int = 300, offset: int = 0, order: int = 0) -> str:
    """获取匹配关键词的文件列表与上下文（社区标准名 anytxt_get_context，等价于 get_result）。

    Args:
        pattern: 搜索关键词
        filter_dir: 限定搜索目录
        filter_ext: 扩展名过滤，* 表示全部
        last_modify_begin: 修改时间下限（Unix 时间戳）
        last_modify_end: 修改时间上限（Unix 时间戳）
        limit: 返回条数上限
        offset: 偏移量（分页）
        order: 排序方式 0默认 1修改时间升序 2修改时间降序 3目录升序 4目录降序
    """
    out = _rpc("GetResult", {
        "pattern": pattern,
        "filterDir": filter_dir,
        "filterExt": filter_ext,
        "lastModifyBegin": last_modify_begin,
        "lastModifyEnd": last_modify_end,
        "limit": str(limit),
        "offset": offset,
        "order": order,
    })
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def anytxt_get_fragment(fid: str, pattern: str) -> str:
    """获取某文件中包含搜索关键词的一个文本片段（用于快速预览命中上下文）。

    Args:
        fid: 文件 ID，从 anytxt_get_context 的返回中获取
        pattern: 搜索关键词（需与 get_context 时一致）
    """
    out = _rpc("GetFragment", {"fid": fid, "pattern": pattern})
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def anytxt_get_fragment_all(fid: str, pattern: str) -> str:
    """获取某文件中所有包含搜索关键词的文本片段。

    Args:
        fid: 文件 ID，从 anytxt_get_context 的返回中获取
        pattern: 搜索关键词
    """
    out = _rpc("GetFragmentAll", {"fid": fid, "pattern": pattern})
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def anytxt_read_file(fid: str) -> str:
    """通过文件 ID (FID) 读取某文件完整原始文本（社区标准名 anytxt_read_file，等价于 get_raw_text）。

    Args:
        fid: 文件 ID。可直接传字符串 FID，也可传 anytxt_get_context 返回的
             files 数组元素（[fid, mtime, size, path]）——会自动取第一项作为 FID。
    """
    # 容错：anytxt_get_context 的 files 元素是 [fid, mtime, size, path] 数组
    if isinstance(fid, (list, tuple)):
        if not fid:
            return json.dumps({"error": "fid 为空"}, ensure_ascii=False)
        fid = fid[0]
    out = _rpc("GetRawTextByFID", {"fid": str(fid)})
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def anytxt_sync_index(folder: str) -> str:
    """将指定文件夹的文件索引与内容同步（强制重建该目录索引）。

    Args:
        folder: 要同步的文件夹路径，如 C:/some folder/
    """
    out = _rpc("SyncIndex", {"folder": folder})
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def anytxt_ocr(file: str) -> str:
    """对图片做离线 OCR 提取文字（仅 OCR 版本 AnyTxt 支持）。

    Args:
        file: 图片文件路径，如 G:/1.png
    """
    out = _rpc("OCR", {"file": file})
    return json.dumps(out, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    # 日志写到 stderr（stdio MCP 要求 stdout 仅用于协议帧）
    print(f"[anytxt-mcp] bridge starting, target={ANYTXT_URL}", file=sys.stderr)
    mcp.run()
