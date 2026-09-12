# -*- coding: utf-8 -*-
"""
sqlite_mcp.py — 轻量安全本地 SQLite 数据库操作 MCP 服务
  • 纯 Python 原生驱动 (sqlite3 标准库，零安装开箱即用)
  • 提供 read_query, write_query, list_tables, describe_table 工具
  • 默认连接 logs/personal_memory.db，同时支持工作区内任意 SQLite 数据库
"""

import os
import sys
import json
import sqlite3

try:
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("sqlite_db")
except ImportError:
    mcp = None

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(WORKSPACE_ROOT, "logs", "personal_memory.db")


def _resolve_db_path(db_path: str = "") -> str:
    """解析并安全校验数据库路径"""
    if not db_path or db_path.strip().lower() in ("default", "memory"):
        return DEFAULT_DB_PATH
    
    clean_path = db_path.strip()
    if not os.path.isabs(clean_path):
        clean_path = os.path.join(WORKSPACE_ROOT, clean_path)
    
    # 安全边界检查：必须为 .db 或 .sqlite 文件
    norm = os.path.normpath(clean_path)
    if not (norm.endswith(".db") or norm.endswith(".sqlite") or norm.endswith(".sqlite3")):
        raise ValueError(f"安全限制：仅允许操作 .db / .sqlite 数据库文件 ({norm})")
    
    return norm


def list_tables(db_path: str = "") -> str:
    """列出数据库中的所有数据表、视图及其结构概览"""
    try:
        path = _resolve_db_path(db_path)
        if not os.path.exists(path):
            return json.dumps({"error": f"数据库文件不存在: {path}"}, ensure_ascii=False)

        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        cursor.execute("SELECT type, name, tbl_name FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'")
        rows = cursor.fetchall()
        
        tables = []
        for r_type, name, tbl_name in rows:
            # 统计表行数
            count = 0
            try:
                c_cur = conn.cursor()
                c_cur.execute(f"SELECT COUNT(*) FROM `{name}`")
                count = c_cur.fetchone()[0]
            except Exception:
                count = -1
            tables.append({"name": name, "type": r_type, "row_count": count})
            
        conn.close()
        return json.dumps({"database": os.path.basename(path), "tables": tables, "total": len(tables)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"列出数据表失败: {str(e)}"}, ensure_ascii=False)


def describe_table(table_name: str, db_path: str = "") -> str:
    """查看指定数据表的字段结构（字段名、数据类型、是否非空、主键）"""
    try:
        path = _resolve_db_path(db_path)
        if not os.path.exists(path):
            return json.dumps({"error": f"数据库文件不存在: {path}"}, ensure_ascii=False)

        clean_table = table_name.strip("`'\" \t\r\n")
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info(`{clean_table}`)")
        cols = cursor.fetchall()
        conn.close()

        if not cols:
            return json.dumps({"error": f"未找到数据表: {clean_table}"}, ensure_ascii=False)

        fields = [
            {
                "cid": c[0],
                "name": c[1],
                "type": c[2],
                "notnull": bool(c[3]),
                "default_value": c[4],
                "pk": bool(c[5])
            }
            for c in cols
        ]
        return json.dumps({"table": clean_table, "fields": fields}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"获取表结构失败: {str(e)}"}, ensure_ascii=False)


def read_query(query: str, db_path: str = "") -> str:
    """安全执行只读 SQL 查询语句（SELECT, PRAGMA, EXPLAIN），返回行记录 JSON 数组"""
    try:
        q = query.strip()
        # 基础防误写校验
        first_word = q.split()[0].upper() if q.split() else ""
        if first_word not in ("SELECT", "PRAGMA", "EXPLAIN", "WITH"):
            return json.dumps({"error": f"只读查询限制：仅允许执行 SELECT/PRAGMA 查询，如需修改请使用 write_query。当前语句开头为 '{first_word}'"}, ensure_ascii=False)

        path = _resolve_db_path(db_path)
        if not os.path.exists(path):
            return json.dumps({"error": f"数据库文件不存在: {path}"}, ensure_ascii=False)

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(q)
        rows = cursor.fetchall()
        res = [dict(r) for r in rows[:200]]  # 限制最多200条避免上下文爆仓
        conn.close()

        return json.dumps({"query": q, "count": len(res), "rows": res}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"SQL 查询执行失败: {str(e)}"}, ensure_ascii=False)


def write_query(query: str, db_path: str = "") -> str:
    """执行写入或修改 SQL 语句（INSERT, UPDATE, DELETE, CREATE, DROP 等），返回受影响行数"""
    try:
        q = query.strip()
        path = _resolve_db_path(db_path)
        
        # 确保父级目录存在
        os.makedirs(os.path.dirname(path), exist_ok=True)

        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        cursor.execute(q)
        conn.commit()
        affected = cursor.rowcount
        conn.close()

        return json.dumps({
            "status": "success",
            "query": q,
            "rows_affected": affected,
            "database": os.path.basename(path)
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"SQL 写入执行失败: {str(e)}"}, ensure_ascii=False)


# FastMCP 工具注册
if mcp:
    @mcp.tool()
    def mcp_read_query(query: str, db_path: str = "") -> str:
        """执行只读 SQL 查询语句（如 SELECT），支持查看数据、聚合统计与过滤筛选"""
        return read_query(query, db_path)

    @mcp.tool()
    def mcp_write_query(query: str, db_path: str = "") -> str:
        """执行数据修改或结构变更 SQL 语句（如 INSERT, UPDATE, CREATE TABLE）"""
        return write_query(query, db_path)

    @mcp.tool()
    def mcp_list_tables(db_path: str = "") -> str:
        """查看指定 SQLite 数据库中的所有数据表名称与行数概览"""
        return list_tables(db_path)

    @mcp.tool()
    def mcp_describe_table(table_name: str, db_path: str = "") -> str:
        """查看指定数据表的完整字段结构定义"""
        return describe_table(table_name, db_path)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("List tables:", list_tables())
        print("Describe table:", describe_table("memories"))
        print("Read query:", read_query("SELECT id, category, content FROM memories LIMIT 2"))
    elif mcp:
        mcp.run(transport="stdio")
    else:
        print("FastMCP 未安装，仅作为纯 Python 模块运行。")
