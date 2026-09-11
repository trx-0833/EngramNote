"""
FTS5 词法检索测试（overhaul-plan 阶段 2.5′）

## 为什么这个模块需要重点测试

FTS5 的失效模式**极其静默**：索引建好了、表也在、`MATCH` 语句能跑，
但查询返回 0 条。开发过程中实测踩到两次，两次都不会抛异常：

1. `content_rowid='rowid'` 指向 VARCHAR 主键的别名 → JOIN 两边类型不同 → 0 条
2. 用 `INSERT INTO fts(rowid, col) SELECT ...` 代替官方 `'rebuild'` 命令
   → 只写了倒排索引、不认内容表 → 0 条

两种情况下"检索质量下降"会表现为"某个通道没结果"，而排查成本极高。
因此下面既测**行为**（能搜到、参数正确），也测**接线**（rowid 指向正确）。
"""

import pytest
from sqlalchemy import text

from app.services.fts_search_service import (
    FTS_TABLE,
    search_chunks_fts,
    to_bigrams,
    to_match_expression,
)


class TestBigrams:
    """分词：与 `RAGService._tokenize` 的中文口径保持一致（都取 2-gram）"""

    def test_chinese_bigram(self):
        assert to_bigrams("水电站") == "水电 电站"

    def test_whitespace_is_removed_before_slicing(self):
        """空白必须先去掉再滑窗

        否则"含 空白"会切出跨空白的二元组，而索引侧不存在这种词元 ——
        查询与索引口径不一致就会静默漏召回。
        """
        assert to_bigrams("水 电") == "水电"
        assert to_bigrams("a b c") == "ab bc"

    def test_short_input(self):
        assert to_bigrams("水") == "水"
        assert to_bigrams("") == ""

    def test_migration_and_service_bigrams_agree(self):
        """`database._bigrams_for_migration` 与服务的实现必须逐字一致

        迁移路径刻意不 import 服务模块（`database.py` 是最底层模块，
        反向依赖 services 会形成循环导入风险），代价是同一算法有两处实现。
        这条断言就是那个代价的守卫 —— 两处漂移会让**迁移回填的历史行**
        与**新写入的行**用不同的分词口径，检索结果变得无法解释。
        """
        from app.database import _bigrams_for_migration

        samples = [
            "拉哇水电站装设多台水轮发电机组",
            "含 空白 与\n换行",
            "英文 mixed 中文 123",
            "", "甲",
            "500kV 配电装置运行技术标准",
        ]
        for s in samples:
            assert _bigrams_for_migration(s) == to_bigrams(s), f"两处实现不一致: {s!r}"


class TestMatchExpression:
    """查询表达式构造（踩过两次 0 结果的坑）"""

    def test_uses_or_not_and(self):
        """必须用 OR 组合，**不能**用 AND

        实测：把查询的全部 bigram 用 AND 连接会返回 0 条 —— 那等于要求
        问句的每个字都出现在同一段资料里，而问句含"是多少"、"如何处理"
        这类疑问语气词，资料中不会有。召回交给 OR、排序交给 bm25()。
        """
        expr = to_match_expression("水电站装机容量是多少")
        assert " OR " in expr
        assert " AND " not in expr

    def test_terms_are_quoted_and_deduplicated(self):
        """重复出现的 bigram 只保留一次

        `水电水电` 的二元组是 `水电` / `电水` / `水电` —— 去重后是两个，
        不是把整串当成一个词。
        """
        expr = to_match_expression("水电水电")
        assert expr == '"水电" OR "电水"'
        # 单个词只出现一次
        assert expr.count('"水电"') == 1

    def test_special_characters_do_not_break_syntax(self):
        """标点与 FTS5 运算符字符都必须被安全包住

        FTS5 语法里 `"` `*` `(` `)` `:` `^` `-` 与 `AND/OR/NOT` 都有特殊含义。
        未加引号时用户问题里的标点会直接抛 `OperationalError`
        （是**报错**，不是返回空）。
        """
        for q in ['带"引号"', "括号(测试)", "星号*", "连字符-a", "a AND b", "冒号:x", "尖号^y"]:
            expr = to_match_expression(q)
            # 内部双引号必须转义成两个，否则会提前闭合短语
            inner = expr.replace('"', "")
            assert '"' not in inner
            assert expr.count('"') % 2 == 0, f"引号不成对: {expr!r}"

    def test_empty_input(self):
        assert to_match_expression("") == ""
        assert to_match_expression("   ") == ""


@pytest.mark.asyncio
class TestFtsSearch:
    """端到端：真实建表 + 写入 + 检索"""

    async def test_finds_matching_chunk(self, test_db):
        uid, nid = await _make_note_with_chunks(
            test_db, ["拉哇水电站装设多台水轮发电机组", "变电站直流系统浮充电压"]
        )
        async with test_db() as db:
            hits = await search_chunks_fts(db, "水轮发电机组", uid, top_k=5)

        assert hits, "FTS5 未检索到任何结果（索引或 rowid 接线有问题）"
        assert "水轮发电机组" in hits[0]["content"]
        assert hits[0]["note_id"] == nid

    async def test_returns_positional_fields(self, test_db):
        """词法结果必须带定位字段（与向量路一致，融合层无需区分来源）"""
        uid, _ = await _make_note_with_chunks(test_db, ["拉哇水电站装设机组"])
        async with test_db() as db:
            hits = await search_chunks_fts(db, "水电站", uid, top_k=5)

        assert hits
        for key in ("chunk_id", "note_id", "content", "similarity",
                    "char_start", "char_end", "heading_path", "line_start", "line_end"):
            assert key in hits[0], f"缺少字段 {key}"

    async def test_rowid_join_is_correct(self, test_db):
        """**关键接线**：FTS 的 rowid 必须与 `chunks.chunk_rowid` 对得上

        用 `content_rowid='rowid'` 时（VARCHAR 主键的别名）JOIN 会静默
        返回 0 条。这条断言把"索引与正文真的关联上了"固定下来 ——
        只测"能搜到"不够，因为库小时可能碰巧对上。
        """
        uid, _ = await _make_note_with_chunks(
            test_db, [f"第{i}段内容包含关键词甲乙丙" for i in range(5)]
        )
        async with test_db() as db:
            # 直接验证 JOIN 计数与 FTS 自身计数一致
            fts_only = (await db.execute(text(
                f"SELECT COUNT(*) FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH '\"甲乙\"'"
            ))).scalar()
            joined = (await db.execute(text(
                f"SELECT COUNT(*) FROM {FTS_TABLE} "
                f"JOIN chunks c ON c.chunk_rowid = {FTS_TABLE}.rowid "
                f"WHERE {FTS_TABLE} MATCH '\"甲乙\"'"
            ))).scalar()
            rowids = (await db.execute(text(
                "SELECT chunk_rowid, COUNT(*) FROM chunks GROUP BY chunk_rowid"
            ))).all()

        assert fts_only and fts_only > 0, "FTS 索引里没有数据"
        assert joined == fts_only, (
            f"JOIN 后行数({joined}) 与 FTS 自身({fts_only}) 不一致 —— "
            f"content_rowid 接线有问题"
        )
        # chunk_rowid 必须唯一（重复会让 JOIN 出笛卡尔积）
        for rid, cnt in rowids:
            assert cnt == 1, f"chunk_rowid={rid} 出现 {cnt} 次"

    async def test_isolated_per_user(self, test_db):
        """跨用户必须隔离"""
        uid_a, _ = await _make_note_with_chunks(test_db, ["甲用户的资料内容"])
        uid_b, _ = await _make_note_with_chunks(test_db, ["乙用户的资料内容"])

        async with test_db() as db:
            hits_a = await search_chunks_fts(db, "资料内容", uid_a, top_k=5)
            hits_b = await search_chunks_fts(db, "资料内容", uid_b, top_k=5)

        assert all("甲用户" in h["content"] for h in hits_a)
        assert all("乙用户" in h["content"] for h in hits_b)

    async def test_excludes_trashed_notes(self, test_db):
        """回收站笔记必须排除，否则已删除的资料仍会出现在回答里"""
        from datetime import datetime

        from sqlalchemy import select

        from app.models.note import Note

        uid, nid = await _make_note_with_chunks(test_db, ["将被移入回收站的内容"])
        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == nid))).scalar_one()
            note.trashed_at = datetime.utcnow()
            await db.commit()

        async with test_db() as db:
            hits = await search_chunks_fts(
                db, "回收站", uid, top_k=5, exclude_note_ids={nid}
            )
        assert hits == []

    async def test_no_match_returns_empty_not_error(self, test_db):
        uid, _ = await _make_note_with_chunks(test_db, ["拉哇水电站装设机组"])
        async with test_db() as db:
            hits = await search_chunks_fts(db, "完全不相干的外星词汇", uid, top_k=5)
        assert hits == []

    async def test_reindex_drops_stale_rows(self, test_db):
        """重建索引后不得残留旧行（否则会命中已删除的内容）"""
        from app.services.chunk_service import index_note_chunks

        uid, nid = await _make_note_with_chunks(test_db, ["原始内容甲乙丙丁"])
        async with test_db() as db:
            assert await search_chunks_fts(db, "甲乙丙丁", uid, top_k=5)

        # 用完全不同的内容重建
        async with test_db() as db:
            await index_note_chunks(
                db, note_id=nid, user_id=uid,
                text="替换后的内容戊己庚辛", source_path="x.md", chunk_size=500,
            )
            await db.commit()

        async with test_db() as db:
            old = await search_chunks_fts(db, "甲乙丙丁", uid, top_k=5)
            new = await search_chunks_fts(db, "戊己庚辛", uid, top_k=5)

        assert new, "重建后新内容检索不到"
        assert old == [], f"重建后仍能搜到旧内容（索引残留）: {len(old)} 条"


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

async def _make_note_with_chunks(
    session_factory, contents: list[str],
) -> tuple[str, str]:
    """建用户+笔记，并写入若干 chunk（含 FTS 索引）"""
    import uuid

    from sqlalchemy import insert

    from app.models.note import Note, NoteStatus, SourceType
    from app.models.user import User
    from app.services.chunk_service import index_note_chunks

    uid = str(uuid.uuid4())
    nid = str(uuid.uuid4())
    async with session_factory() as db:
        await db.execute(insert(User).values(
            id=uid, email=f"{uid[:8]}@example.com", username=f"u{uid[:8]}",
            hashed_password="x", is_active=True,
        ))
        await db.execute(insert(Note).values(
            id=nid, user_id=uid, title=f"笔记 {nid[:6]}",
            source_type=SourceType.pdf.value, status=NoteStatus.cleaned.value,
        ))
        await db.commit()

    # 各段之间空行分隔，保证被切成多个 chunk
    text_content = "\n\n".join(contents)
    async with session_factory() as db:
        await index_note_chunks(
            db, note_id=nid, user_id=uid, text=text_content,
            source_path="x.md", chunk_size=200,
        )
        await db.commit()
    return uid, nid
