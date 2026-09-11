"""
FTS5 词法检索（overhaul-plan 阶段 2.5′）
=======================================

用 SQLite 内置的 FTS5 倒排索引替换"每次查询在 Python 里全量建 BM25 索引"。
这是 A-5 在 SQLite 路线下的正解（PG 路线的等价物是 `pg_bigm`，本项目不执行）。

## 为什么用 **bigram 预切词** 而不是内置的 `trigram`

SQLite 内置的 `trigram` 分词器是唯一原生支持中文的，但实测在本项目语料上
**更差**（同一套 1058 条评测集、608 个 chunk 语料、严格 Recall@5）：

| 实现 | Recall@5 | MRR | 无结果比例 |
|---|---|---|---|
| FTS5(`trigram`) + `bm25()` | 57.84% | 0.5052 | 0.76% |
| **FTS5(bigram 预切词) + `bm25()`** | **60.21%** | **0.5258** | **0.00%** |
| 现有 Python BM25（bigram） | 59.74% | 0.5183 | — |

原因：中文三元组重叠严重、区分度弱，而现有实现本来就用 **2-gram**
（`rag_service._tokenize`）。因此这里保持 2-gram 分词口径不变，
只把"索引与排序"下沉到 SQLite —— 这样比较的是**实现**而不是顺带改了分词策略。

做法：写入时把内容切成 bigram 空格连接，用内置 `unicode61` 分词（按空白切）；
查询侧同样切 bigram，用 **OR** 组合召回、由 FTS5 的 `bm25()` 排序。

## 查询里为什么必须用 OR（实测踩过两次）

1. **整句加引号 → 0 结果**：FTS5 里引号表示**短语匹配**，要求整串逐字出现，
   而自然语言问句不会逐字出现在资料里。
2. **全部 bigram 用 AND → 0 结果**：那等于要求问句的每个字都出现在同一段资料，
   而问句本身含疑问语气词（"是多少"、"如何处理"），资料中不会有。

召回交给 OR，排序交给 `bm25()` —— 这才是词法检索的标准形态（召回与排序分离）。

## 索引的维护契约

FTS 表是 `chunks` 的**派生索引**，用 `content='chunks'` 外部内容表模式，
只存倒排索引、正文仍从 `chunks` 读。同步由
`chunk_service.index_note_chunks` 负责（写入后调用 `reindex_note`）。

**不做触发器**：`content=` 模式下触发器需要额外的 delete/insert 命令表，
最容易出的问题是"索引与正文不一致而无人察觉"。显式在写入后同步、
并提供 `rebuild_all` 兜底，比隐式触发器更容易验证。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: FTS 虚拟表名
FTS_TABLE = "chunks_fts"


def to_bigrams(text_value: str) -> str:
    """把文本切成空白分隔的 bigram（与 `RAGService._tokenize` 的中文口径一致）

    先去掉所有空白，再取相邻两字符滑窗 —— 这样"含空白的三元组"不存在，
    索引与查询两侧的切法也就完全一致。
    """
    cleaned = "".join(ch for ch in (text_value or "") if not ch.isspace())
    if len(cleaned) < 2:
        return cleaned
    return " ".join(cleaned[i:i + 2] for i in range(len(cleaned) - 1))


def to_match_expression(question: str) -> str:
    """把自然语言问题转成 FTS5 MATCH 表达式（OR 组合的 bigram）

    每个 bigram 单独加引号：FTS5 语法里 `"` `*` `(` `)` `:`
    `^` `-` 与 `AND/OR/NOT` 都有特殊含义，用户问题里的标点会直接抛
    `OperationalError`（是**报错**，不是返回空）。内部双引号转义成两个。
    """
    cleaned = "".join(ch for ch in (question or "") if not ch.isspace())
    if not cleaned:
        return ""
    if len(cleaned) < 2:
        return '"' + cleaned.replace('"', '""') + '"'
    grams, seen = [], set()
    for i in range(len(cleaned) - 1):
        gram = cleaned[i:i + 2]
        if gram not in seen:
            seen.add(gram)
            grams.append(gram)
    return " OR ".join('"' + g.replace('"', '""') + '"' for g in grams)


async def fts5_available(db: AsyncSession) -> bool:
    """当前 SQLite 构建是否支持 FTS5

    不假设它一定可用：FTS5 是编译期选项，精简构建可能没有。
    调用方据此决定是否退化为 Python BM25 —— 检索通道不该因为
    一个可选扩展缺失就整体不可用。
    """
    try:
        row = await db.execute(text(
            f"SELECT 1 FROM sqlite_master WHERE type='table' AND name='{FTS_TABLE}'"
        ))
        return row.first() is not None
    except Exception:
        return False


async def reindex_note(db: AsyncSession, note_id: str) -> None:
    """同步 FTS 索引（`chunk_service` 写入 chunks 后调用）

    ## 实现：整表 `rebuild`（实测选定的唯一可靠方式）

    FTS5 外部内容表的单条同步命令形式都不可用或不安全，逐条实测过：

    | 做法 | 结果 |
    |---|---|
    | `INSERT INTO fts(fts, grams) SELECT 'delete', grams ...` | `SQL logic error` |
    | `INSERT INTO fts(fts, rowid, grams) SELECT 'delete', chunk_rowid, ...` | `delete` 成功但 `insert` 报 `SQL logic error` |
    | `DELETE FROM fts WHERE rowid IN (...)` + `INSERT INTO fts(rowid, grams) ...` | **`database disk image is malformed`** —— 直接改倒排索引破坏了索引结构 |
    | `INSERT INTO fts(fts) VALUES('rebuild')` | ✅ 正确：删除与新增都生效 |

    因此这里用 `rebuild`。**参数 `note_id` 因而不再使用** —— 保留它是因为
    调用方语义上仍以"某篇笔记的 chunk 变了"为触发点，且将来若找到真正的
    增量同步方式，签名不必再改。

    ## 代价（如实说明）

    每篇笔记清洗完成后重建**全表**索引。当前规模 608 行、重建是毫秒级，
    而清洗本身是秒级以上的重任务，因此相对开销可忽略。
    语料上到十万级时应重新评估（届时可考虑改回带触发器、
    或把检索换到 `sqlite-vec` + FTS5 之外的方案）。

    这个取舍是为了**语义正确**：整表重建不可能留下指向已删除内容的
    悬空索引项，而"索引与正文漂移"是最难发现的一类检索缺陷。
    """
    await rebuild_all(db)


async def rebuild_all(db: AsyncSession) -> None:
    """全量重建 FTS 索引

    `'rebuild'` 是 FTS5 外部内容表的官方命令：它会丢弃现有索引、
    重新从内容表（`chunks`）读取并索引。因此它同时**清掉悬空项** ——
    这正是"删除的 chunk 不该再被搜到"所需要的语义。
    """
    await db.execute(text(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')"))


async def search_chunks_fts(
    db: AsyncSession,
    question: str,
    user_id: str,
    *,
    top_k: int = 20,
    exclude_note_ids: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """用 FTS5 检索 chunk，返回与 `BM25Index.search` 同构的结果

    返回字段刻意与 Python BM25 一致（`note_id`/`content`/`similarity`/
    `block_index` + 定位字段），这样 `_rrf_fusion` 与前端都无需区分来源。

    `similarity` 取 FTS5 `bm25()` 的**负值**：`bm25()` 越小越相关（是距离），
    而融合层期望"越大越相关"。取负后量纲仍与 Python BM25 不同，但
    **不影响正确性** —— RRF 只用名次，不用分数值（这正是 RRF 的用途）。
    """
    expr = to_match_expression(question)
    if not expr:
        return []

    sql = (
        f"SELECT c.id, c.note_id, c.content, c.[index], c.char_start, c.char_end, "
        f"c.heading_path, c.line_start, c.line_end, bm25({FTS_TABLE}) AS score "
        f"FROM {FTS_TABLE} JOIN chunks c ON c.chunk_rowid = {FTS_TABLE}.rowid "
        f"WHERE {FTS_TABLE} MATCH :expr AND c.user_id = :uid"
    )
    params: Dict[str, Any] = {"expr": expr, "uid": user_id, "limit": top_k}
    if exclude_note_ids:
        # 回收站笔记必须排除，否则已删除的资料仍会出现在回答里
        placeholders = ", ".join(f":ex{i}" for i in range(len(exclude_note_ids)))
        sql += f" AND c.note_id NOT IN ({placeholders})"
        for i, nid in enumerate(exclude_note_ids):
            params[f"ex{i}"] = nid
    sql += " ORDER BY score LIMIT :limit"

    try:
        rows = (await db.execute(text(sql), params)).all()
    except Exception as exc:
        logger.warning("FTS5 检索失败（交由调用方降级）: %s", exc)
        return []

    return [
        {
            "chunk_id": r[0],
            "note_id": r[1],
            "content": r[2],
            "index": r[3],
            "char_start": r[4],
            "char_end": r[5],
            "heading_path": r[6],
            "line_start": r[7],
            "line_end": r[8],
            # 取负：bm25() 越小越相关，融合层期望越大越相关
            "similarity": -float(r[9]),
            "block_index": r[3] or 0,
        }
        for r in rows
    ]
