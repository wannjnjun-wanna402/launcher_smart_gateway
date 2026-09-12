#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
anytxt_mcp.py — AnyTXT Searcher 本地全文检索 MCP 服务 (stdio 模式)
  将 AnyTXT Searcher (ATGUI.exe) 的 HTTP JSON-RPC 2.0 API（127.0.0.1:9920）
  封装成标准 MCP 工具集，供 AI 客户端（或网关）调用。

  ⚡ 随用随开架构特性：
  - 本地电脑为防止持续索引占用 CPU 与磁盘 I/O，已默认关闭开机自启（随用随开模式）。
  - 本模块具备 0.8 秒极速健康探针：若 AnyTXT 未启动，立即返回友好提示，绝不卡死大模型！
  - 内置 anytxt_status、anytxt_start_service、anytxt_stop_service 便捷控制工具。
  - 配套桌面脚本：mcp/启动AnyTXT.bat 与 mcp/关闭AnyTXT.bat。
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("ERROR: 需要 mcp 库。请执行 pip install mcp", file=sys.stderr)
    sys.exit(1)

ANYTXT_HOST = "127.0.0.1"
ANYTXT_PORT = 9920
ANYTXT_URL = f"http://{ANYTXT_HOST}:{ANYTXT_PORT}"
ATGUI_PATH = r"E:\AnyTXT Searcher\ATGUI.exe"
SOCKET_TIMEOUT = 0.8
RPC_TIMEOUT = 30


def is_anytxt_alive(timeout: float = SOCKET_TIMEOUT) -> bool:
    """快速探测 AnyTXT 9920 端口是否存活，毫秒级响应，防卡死"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ANYTXT_HOST, ANYTXT_PORT))
        s.close()
        return True
    except Exception:
        s.close()
        return False


def rpc(method: str, input_params: dict):
    """调用 AnyTXT HTTP JSON-RPC，返回 result 里的 data（dict 或 None）。"""
    if not is_anytxt_alive():
        return {
            "status": "offline",
            "error": "AnyTXT 全文检索服务当前未运行（处于随用随开节能模式）。",
            "tip": (
                "为避免常驻后台监控消耗电脑 CPU 与磁盘资源，AnyTXT 设定为随用随开。"
                "请双击运行 E:\\llama-win-cuda-12.4-x64\\mcp\\启动AnyTXT.bat 或打开 ATGUI.exe；"
                "亦可调用 anytxt_start_service 工具按需自动拉起。"
            ),
        }

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
        with urllib.request.urlopen(req, timeout=RPC_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"error": f"AnyTXT 接口响应异常: {e}"}

    if "error" in body:
        return {"error": body["error"]}
    result = body.get("result", {})
    data = result.get("data") or {}
    if data.get("output") is not None:
        return data["output"]
    return data if data else {"note": "无输出"}


mcp = FastMCP("anytxt")


@mcp.tool()
def anytxt_status() -> str:
    """检查 AnyTXT 检索服务当前运行状态（是否开启随用随开模式）。"""
    alive = is_anytxt_alive()
    info = {
        "is_running": alive,
        "port": ANYTXT_PORT,
        "status": "online" if alive else "offline",
        "message": (
            "AnyTXT 检索服务在线，可直接执行全文搜索与 OCR。"
            if alive
            else "AnyTXT 检索服务已关闭（随用随开状态，节省系统资源）。"
        ),
    }
    return json.dumps(info, ensure_ascii=False, indent=2)


@mcp.tool()
def anytxt_start_service() -> str:
    """按需一键启动 AnyTXT 检索服务 (ATGUI.exe)。
    启动后将开始监听 9920 端口，供全文搜索工具调用。"""
    if is_anytxt_alive():
        return json.dumps({"status": "already_running", "message": "AnyTXT 已经在运行中！"}, ensure_ascii=False)

    if not os.path.exists(ATGUI_PATH):
        return json.dumps({"status": "not_found", "error": f"未找到可执行文件: {ATGUI_PATH}"}, ensure_ascii=False)

    try:
        subprocess.Popen([ATGUI_PATH], creationflags=subprocess.DETACHED_PROCESS)
        # 等待端口就绪 (最多等待 5 秒)
        for i in range(10):
            time.sleep(0.5)
            if is_anytxt_alive():
                return json.dumps({
                    "status": "success",
                    "message": f"AnyTXT 服务启动成功 (耗时 {(i+1)*0.5:.1f}s)，9920 端口已就绪！",
                }, ensure_ascii=False)
        return json.dumps({
            "status": "started_waiting",
            "message": "ATGUI.exe 已拉起，但 9920 端口尚未就绪，请稍后重试或在 AnyTXT 界面开启 HTTP 搜索服务。",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@mcp.tool()
def anytxt_stop_service() -> str:
    """用完即关：结束 AnyTXT 进程，彻底释放 CPU/内存/磁盘 I/O 占用。"""
    try:
        res = subprocess.run(["taskkill", "/IM", "ATGUI.exe", "/F"], capture_output=True, text=True)
        subprocess.run(["taskkill", "/IM", "ATService.exe", "/F"], capture_output=True, text=True)
        return json.dumps({
            "status": "stopped",
            "message": "AnyTXT 进程已退出，系统资源已全部释放！",
            "detail": res.stdout.strip(),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@mcp.tool()
def anytxt_search(pattern: str, filter_dir: str = "", filter_ext: str = "*",
                  last_modify_begin: int = 0, last_modify_end: int = 2147483647) -> str:
    """统计本地文件中包含关键词的文件数量（秒级全文检索）。
    参数:
      pattern: 搜索关键词（支持引号精确短语、!排除 等语法）
      filter_dir: 限定文件夹路径（留空表示全盘检索）
      filter_ext: 扩展名过滤（例如 'pdf;docx;txt'，'*' 表示全部）
      last_modify_begin/end: 时间戳范围（可选）
    返回: 命中文件数量。数量>0 时可调用 anytxt_get_result 获取文件列表。"""
    out = rpc("Search", {
        "pattern": pattern, "filterDir": filter_dir, "filterExt": filter_ext,
        "lastModifyBegin": last_modify_begin, "lastModifyEnd": last_modify_end,
    })
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def anytxt_get_result(pattern: str, filter_dir: str = "", filter_ext: str = "*",
                      limit: int = 300, offset: int = 0, order: int = 0,
                      last_modify_begin: int = 0, last_modify_end: int = 2147483647) -> str:
    """列出匹配关键词的文件列表（含路径、大小、修改时间、FID）。
    参数:
      pattern: 关键词
      filter_dir: 限定文件夹
      filter_ext: 扩展名
      limit: 最多返回条数（默认 300）
      offset: 分页偏移
      order: 排序方式 (0默认, 1时间升序, 2时间降序, 3目录升序, 4目录降序)
    返回: 匹配文件的 JSON 数组，含 fid、path、size、mtime。"""
    out = rpc("GetResult", {
        "pattern": pattern, "filterDir": filter_dir, "filterExt": filter_ext,
        "limit": str(limit), "offset": offset, "order": order,
        "lastModifyBegin": last_modify_begin, "lastModifyEnd": last_modify_end,
    })
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def anytxt_get_fragment(fid: str, pattern: str) -> str:
    """获取某个文件的单段匹配文本片段（含关键词上下文）。
    参数: fid: 文件ID (来自 anytxt_get_result), pattern: 关键词。"""
    out = rpc("GetFragment", {"fid": fid, "pattern": pattern})
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def anytxt_get_fragment_all(fid: str, pattern: str) -> str:
    """获取某个文件的全部匹配文本片段（含关键词上下文）。
    参数: fid: 文件ID, pattern: 关键词。"""
    out = rpc("GetFragmentAll", {"fid": fid, "pattern": pattern})
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def anytxt_get_raw_text(fid: str) -> str:
    """按 FID 读取文件的完整纯文本内容（用于总结、长文分析、翻译等）。
    参数: fid: 文件ID。注意大文件文本较长，建议优先使用 anytxt_get_fragment。"""
    out = rpc("GetRawTextByFID", {"fid": fid})
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def anytxt_sync_index(folder: str) -> str:
    """手动同步指定文件夹到 AnyTXT 索引（新加入或修改的文件更新后可搜到）。
    参数: folder: 要索引的绝对路径（如 'E:/llama-win-cuda-12.4-x64'）。"""
    out = rpc("SyncIndex", {"folder": folder})
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def anytxt_ocr(file_path: str) -> str:
    """离线 OCR 识别本地图片中的文本（截图、扫描件、图表文字等）。
    参数: file_path: 本地图片文件的绝对路径。"""
    out = rpc("OCR", {"file": file_path})
    return json.dumps(out, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
