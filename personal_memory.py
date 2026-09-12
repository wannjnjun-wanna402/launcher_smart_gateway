# -*- coding: utf-8 -*-
"""
personal_memory.py — 个人长期向量记忆与知识库 RAG 核心引擎
  • 基于 SQLite3 原生持久化存储 (logs/personal_memory.db)，零丢数据、跨重启累加
  • 提供 save_memory, recall_memory, list_memories, delete_memory 接口
  • 内建混合双层检索：字符 N-gram 语义余弦相似度 + 关键词倒排匹配
  • 自动提取上下文注入片段 (get_relevant_context)，赋予大模型海马体
"""

import os
import sqlite3
import json
import time
import math
import re
from datetime import datetime
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
DB_PATH = os.path.join(LOGS_DIR, "personal_memory.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL DEFAULT 'general',
                content TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                embedding TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        try:
            conn.execute("ALTER TABLE memories ADD COLUMN embedding TEXT DEFAULT ''")
        except Exception:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON memories(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at)")
    conn.close()


def get_bge_embedding(text: str) -> list:
    """从 8086 端口请求 BGE-M3 1024 维密集高精度向量 (支持 8192 长上下文)"""
    try:
        import urllib.request, json
        data = json.dumps({"input": text[:8000]}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8086/v1/embeddings",
            headers={"Content-Type": "application/json", "Authorization": "Bearer llamacpp"},
            data=data
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return res["data"][0]["embedding"]
    except Exception:
        return []


def _dense_cosine_similarity(v1: list, v2: list) -> float:
    """计算两个 1024 维密集向量之间的精确余弦相似度 (< 0.05ms)"""
    try:
        import numpy as np
        a = np.array(v1, dtype=np.float32)
        b = np.array(v2, dtype=np.float32)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
    except Exception:
        return 0.0


def _text_to_vector(text: str) -> Counter:
    """生成汉字单字(1-gram)与词组(2-gram)混合频率向量，中文语义覆盖率高且毫秒级响应 (< 0.1ms)"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fa5]+", "", text.lower())
    if not cleaned:
        return Counter()
    words = re.findall(r"[\u4e00-\u9fa5]|[a-zA-Z0-9]+", cleaned)
    tokens = list(words)
    # 连续二元词组提升语境权重
    for i in range(len(words) - 1):
        tokens.append(words[i] + words[i + 1])
    return Counter(tokens)


def _cosine_similarity(vec1: Counter, vec2: Counter) -> float:
    """计算两个稀疏 N-gram 向量之间的余弦相似度"""
    if not vec1 or not vec2:
        return 0.0
    common = set(vec1.keys()) & set(vec2.keys())
    dot = sum(vec1[k] * vec2[k] for k in common)
    mag1 = math.sqrt(sum(v * v for v in vec1.values()))
    mag2 = math.sqrt(sum(v * v for v in vec2.values()))
    if mag1 == 0.0 or mag2 == 0.0:
        return 0.0
    return dot / (mag1 * mag2)


def save_memory(content: str, category: str = "general", tags: str = "") -> dict:
    """持久化保存一条个人偏好、项目背景或关键备忘 (自动计算 BGE-M3 1024 维向量)"""
    text = content.strip()
    if not text:
        return {"status": "error", "message": "记忆内容不能为空"}

    init_db()
    conn = get_connection()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 异步/即时计算 BGE-M3 密集向量
    emb = get_bge_embedding(text)
    emb_json = json.dumps(emb) if emb else ""
    
    with conn:
        cur = conn.execute(
            "INSERT INTO memories (category, content, tags, embedding, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (category.strip() or "general", text, tags.strip(), emb_json, now_str, now_str)
        )
        mem_id = cur.lastrowid
    conn.close()
    return {
        "status": "success",
        "id": mem_id,
        "category": category,
        "content": text,
        "tags": tags,
        "embedding_dim": len(emb) if emb else 0,
        "model": "BGE-M3" if emb else "sparse-ngram",
        "created_at": now_str
    }


def recall_memory(query: str, limit: int = 3, category: str = None) -> list:
    """根据查询词或口语问题语义检索相关的历史个人记忆 (BGE-M3 密集向量 + 混合加权)"""
    init_db()
    q = query.strip()
    if not q:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    if category:
        cursor.execute("SELECT id, category, content, tags, embedding, created_at FROM memories WHERE category = ?", (category,))
    else:
        cursor.execute("SELECT id, category, content, tags, embedding, created_at FROM memories")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return []

    # 尝试获取查询词的 BGE-M3 向量
    query_dense = get_bge_embedding(q)
    query_vec = _text_to_vector(q)
    scored = []
    
    # 提取查询核心词进行匹配增强
    keywords = [w for w in re.split(r"[^\w\u4e00-\u9fa5]+", q) if len(w) >= 2]

    for r in rows:
        content = r["content"]
        tags = r["tags"]
        doc_text = f"{r['category']} {tags} {content}"
        
        # 1. BGE-M3 密集向量相似度
        dense_sim = 0.0
        doc_emb_raw = r["embedding"] if "embedding" in r.keys() else ""
        if query_dense and doc_emb_raw:
            try:
                doc_dense = json.loads(doc_emb_raw)
                if doc_dense:
                    dense_sim = _dense_cosine_similarity(query_dense, doc_dense)
            except Exception:
                dense_sim = 0.0

        # 2. 稀疏 N-gram 相似度
        doc_vec = _text_to_vector(doc_text)
        sparse_sim = _cosine_similarity(query_vec, doc_vec)
        
        # 3. 关键词精确命中加权
        kw_bonus = 0.0
        for kw in keywords:
            if kw in doc_text:
                kw_bonus += 0.20
                
        if query_dense and dense_sim > 0.0:
            # 融合得分：BGE-M3 占 70% + 稀疏补充 15% + 关键词 15%
            total_score = min(1.0, dense_sim * 0.7 + sparse_sim * 0.15 + kw_bonus)
        else:
            total_score = min(1.0, sparse_sim + kw_bonus)

        if total_score > 0.08:
            scored.append({
                "id": r["id"],
                "category": r["category"],
                "content": content,
                "tags": tags,
                "created_at": r["created_at"],
                "score": round(total_score, 4),
                "match_type": "BGE-M3-Dense" if (query_dense and dense_sim > 0.0) else "Sparse-Ngram"
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def list_memories(limit: int = 10, category: str = None) -> list:
    """列出最近存储的个人记忆"""
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    if category:
        cursor.execute(
            "SELECT id, category, content, tags, created_at FROM memories WHERE category = ? ORDER BY id DESC LIMIT ?",
            (category, limit)
        )
    else:
        cursor.execute(
            "SELECT id, category, content, tags, created_at FROM memories ORDER BY id DESC LIMIT ?",
            (limit,)
        )
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "category": r["category"],
            "content": r["content"],
            "tags": r["tags"],
            "created_at": r["created_at"]
        }
        for r in rows
    ]


def delete_memory(memory_id: int) -> bool:
    """根据 ID 删除指定记忆"""
    init_db()
    conn = get_connection()
    with conn:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_relevant_context(query: str, max_items: int = 2) -> str:
    """生成静默注入系统提示词的长期记忆片段 (若无相关记忆则返回空字符串)"""
    results = recall_memory(query, limit=max_items)
    if not results:
        return ""
    
    # 只有相关度 (score >= 0.12) 才自动注入，避免噪声干扰
    high_rel = [r for r in results if r.get("score", 0) >= 0.12]
    if not high_rel:
        return ""

    lines = ["【个人长期记忆与用户偏好 (海马体自动召回)】:"]
    for r in high_rel:
        lines.append(f"• [{r['category']}] {r['content']}")
    return "\n".join(lines)


# 模块自测
if __name__ == "__main__":
    init_db()
    res = save_memory("本机显卡为 Tesla V100 32GB 显存，优先保证单模型满血推理速度", category="hardware", tags="显卡,硬件")
    print("Save result:", res)
    recalled = recall_memory("我的电脑显存多大？配置是什么")
    print("Recalled:", json.dumps(recalled, ensure_ascii=False, indent=2))
    context = get_relevant_context("显存多大")
    print("Auto-injected Context:\n", context)
