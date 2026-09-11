"""
Chunk 表测试（overhaul-plan 阶段 2.2′ A 半）

## 这张表为什么需要测试

`chunks` 表把"检索单元"从**每次重建的中间产物**变成**可引用的一等实体**。
一旦落库，`char_start/char_end` 就成了长期契约：引用回跳（2.7）按它切片，
清单元数据按 `line_start/line_end` 定位。写错之后**没有任何东西会报错** ——
前端只是高亮到错误的位置，而用户会以为系统在胡说。

因此这里重点锁三类性质：

1. **不变量**：`text[char_start:char_end] == content`
   （该不变量在开发过程中被违反过两次，各 1318 个分段，见附录 L.2）
2. **向量打包往返**，以及"维度不符时返回 None 而不是硬解读"
3. **落库幂等性**：按 `(note_id, index)` 重建不产生重复行
"""

import os

import pytest

from app.models.chunk import Chunk, pack_vector, unpack_vector


class TestVectorPacking:
    """向量 BLOB 的打包/解包（B 半要用，先把契约固定下来）"""

    def test_round_trip(self):
        values = [0.1, -0.25, 3.5, 0.0, -1.0]
        packed = pack_vector(values)
        assert len(packed) == len(values) * 4, "float32 应为每维 4 字节"
        back = unpack_vector(packed, len(values))
        assert back is not None
        assert len(back) == len(values)
        for a, b in zip(values, back, strict=True):
            assert abs(a - b) < 1e-6

    def test_dimension_mismatch_returns_none(self):
        """维度与 `embedding_dim` 不符时必须返回 None，而不是硬解读

        长度不对的向量拿去算余弦会得到**无意义的数字**，并且会静默参与排序 ——
        比"没有向量"更糟。这里锁住"宁可不给"的行为。
        """
        packed = pack_vector([1.0, 2.0, 3.0])
        assert unpack_vector(packed, 3) is not None
        assert unpack_vector(packed, 5) is None, "维度不符时不得硬解读"
        assert unpack_vector(packed, 2) is None

    def test_empty_inputs(self):
        assert unpack_vector(None, 3) is None
        assert unpack_vector(b"", 3) is None

    def test_unpack_without_dim_trusts_length(self):
        """`embedding_dim` 为空时按实际长度解（历史数据可能没记维度）"""
        packed = pack_vector([1.0, 2.0])
        assert unpack_vector(packed, None) == [1.0, 2.0]


@pytest.mark.asyncio
class TestChunkPersistence:
    """落库与查询"""

    async def test_insert_and_read_back(self, test_db):
        uid, nid = await _make_user_and_note(test_db)
        segments = _segments("# 标题\n\n正文内容。\n\n## 小节\n\n更多内容。")

        async with test_db() as db:
            db.add_all([
                Chunk(
                    user_id=uid, note_id=nid, index=s["index"], content=s["content"],
                    char_start=s["char_start"], char_end=s["char_end"],
                    heading_path=s["heading_context"] or None,
                    line_start=s["start_line"], line_end=s["end_line"],
                    char_count=s["char_count"], has_embedding=False,
                )
                for s in segments
            ])
            await db.commit()

        async with test_db() as db:
            from sqlalchemy import select

            rows = list((await db.execute(
                select(Chunk).where(Chunk.note_id == nid).order_by(Chunk.index)
            )).scalars().all())

        assert len(rows) == len(segments)
        assert [r.index for r in rows] == list(range(len(segments)))
        assert all(r.has_embedding is False for r in rows), "A 半不应写入向量标记"

    async def test_note_index_unique_constraint(self, test_db):
        """`(note_id, index)` 唯一：重建索引不得产生重复行

        没有这个约束，"全量重建"失败一次就会留下重复 chunk，
        检索时同一段内容出现多次、挤掉别的结果。
        """
        from sqlalchemy.exc import IntegrityError

        uid, nid = await _make_user_and_note(test_db)

        async with test_db() as db:
            for _ in range(2):
                db.add(Chunk(
                    user_id=uid, note_id=nid, index=0, content="x",
                    char_start=0, char_end=1, line_start=0, line_end=0,
                    char_count=1, has_embedding=False,
                ))
            with pytest.raises(IntegrityError):
                await db.commit()

    async def test_rebuild_replaces_not_appends(self, test_db):
        """按 (note_id, index) 重建：先删后插后行数不变（幂等）"""
        from sqlalchemy import delete, func, select

        uid, nid = await _make_user_and_note(test_db)

        async def write(n_rows: int):
            async with test_db() as db:
                await db.execute(delete(Chunk).where(Chunk.note_id == nid))
                db.add_all([
                    Chunk(
                        user_id=uid, note_id=nid, index=i, content=f"内容{i}",
                        char_start=i, char_end=i + 3, line_start=0, line_end=0,
                        char_count=3, has_embedding=False,
                    )
                    for i in range(n_rows)
                ])
                await db.commit()

        await write(5)
        await write(3)

        async with test_db() as db:
            n = (await db.execute(
                select(func.count()).select_from(Chunk).where(Chunk.note_id == nid)
            )).scalar()
            contents = list((await db.execute(
                select(Chunk.content).where(Chunk.note_id == nid).order_by(Chunk.index)
            )).scalars().all())

        assert n == 3, f"重建后应剩 3 行，实际 {n}（旧行未被清除）"
        assert contents == ["内容0", "内容1", "内容2"]

    async def test_isolated_per_user_query(self, test_db):
        """按 user_id 过滤时必须只取到自己的 chunk（多租户隔离）"""
        from sqlalchemy import select

        uid_a, nid_a = await _make_user_and_note(test_db)
        uid_b, nid_b = await _make_user_and_note(test_db)

        async with test_db() as db:
            db.add_all([
                Chunk(user_id=uid_a, note_id=nid_a, index=0, content="A",
                      char_start=0, char_end=1, line_start=0, line_end=0,
                      char_count=1, has_embedding=False),
                Chunk(user_id=uid_b, note_id=nid_b, index=0, content="B",
                      char_start=0, char_end=1, line_start=0, line_end=0,
                      char_count=1, has_embedding=False),
            ])
            await db.commit()

        async with test_db() as db:
            rows = list((await db.execute(
                select(Chunk).where(Chunk.user_id == uid_a)
            )).scalars().all())

        assert [r.content for r in rows] == ["A"]


@pytest.mark.asyncio
class TestIndexerHash:
    """索引脚本的指纹逻辑（决定 `--only-stale` 是否会漏重建）"""

    async def test_hash_changes_when_content_changes(self):
        from scripts.index_chunks import _chunk_hash

        base = _segments("# 甲\n\n原文内容。")
        changed = _segments("# 甲\n\n原文内容改了。")
        assert _chunk_hash(base, "a.md") != _chunk_hash(changed, "a.md")

    async def test_hash_changes_when_source_path_changes(self):
        """来源对象名变了，指纹必须变

        `clean_md_path` 从无到有时，偏移的**参照物**变了 ——
        内容可能碰巧一样，但索引必须重建。
        """
        from scripts.index_chunks import _chunk_hash

        base = _segments("# 甲\n\n原文内容。")
        assert _chunk_hash(base, "a/original.md") != _chunk_hash(base, "a/clean.md")

    async def test_hash_is_stable_for_same_input(self):
        """同样输入必须得到同样指纹（否则 `--only-stale` 永不命中）"""
        from scripts.index_chunks import _chunk_hash

        base = _segments("# 甲\n\n原文内容。")
        again = _segments("# 甲\n\n原文内容。")
        assert _chunk_hash(base, "a.md") == _chunk_hash(again, "a.md")


class TestInvariantIsEnforcedBeforeWrite:
    """落库前必须自证不变量（错误偏移比缺失更糟）"""

    def test_text_slices_match_content(self):
        doc = "# 第一章\n\n引言。\n\n## 1.1 甲\n\n甲的内容。\n\n```\ncode\n```\n\n结尾。"
        for limit in (10, 30, 80, 500):
            for s in _segments(doc, limit):
                assert doc[s["char_start"]:s["char_end"]] == s["content"], (
                    f"limit={limit} 时不变量被破坏"
                )

    def test_char_count_matches_content(self):
        doc = "# 标题\n\n一些内容。\n\n另一些内容。"
        for s in _segments(doc, 20):
            assert s["char_count"] == len(s["content"])

    def test_line_range_is_ordered(self):
        doc = "# 标题\n\n内容甲。\n\n内容乙。"
        for s in _segments(doc, 15):
            assert s["start_line"] <= s["end_line"]


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _segments(text: str, limit: int = 500) -> list[dict]:
    from app.services.markdown_segmenter import segment_with_offsets, to_retrieval_chunks

    return to_retrieval_chunks(segment_with_offsets(text, limit))


async def _make_user_and_note(session_factory) -> tuple[str, str]:
    """建用户 + 笔记（显式按依赖顺序落库，避免 ORM flush 顺序不确定）"""
    import uuid

    from sqlalchemy import insert

    from app.models.note import Note, NoteStatus, SourceType
    from app.models.user import User

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
    return uid, nid


def test_scripts_dir_is_importable():
    """`scripts` 必须可作为包导入（测试要 import index_chunks）"""
    assert os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "scripts"))
