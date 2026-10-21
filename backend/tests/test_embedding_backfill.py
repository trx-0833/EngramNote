# -*- coding: utf-8 -*-
"""
自动补嵌入闭环的测试（B2）
==========================

## 这一组盯的是什么

`docs/overhaul-plan.md` 附录 BN.3 把「新导入的笔记在人工补嵌入之前只能用
BM25 检索」列为**仅有的两条「确实影响使用」**之一（丙-2）。成因是：
清洗路径刻意不写向量（要加载 4.3GB 模型），而补嵌入**只有人工脚本** ——
`tasks/celery_app.py` 的 `beat_schedule` 里没有这个任务。

本轮补上 `backfill_embeddings`（周期编排）+ `embed_chunks`（chunk 侧）。
这组用例锁住四件事：

1. **空转保护**：两条都没有待办时**不加载模型**（周期任务最常见的状态就是没活干，
   那时不该去读 4.3GB 权重）；
2. **只补缺的**：`has_embedding=True` 的行**不被重算**（与 `embed_cards` 同口径）；
3. **写回的四列齐全**：`embedding` / `embedding_model` / `embedding_dim` /
   `has_embedding` —— 少任何一列，那一行在检索侧就等于没有向量；
4. **`embedding_model` 记的是实际加载的模型**（降级时不能写成配置值）。

## 为什么这些用例不需要真模型

`encode_text` 是**同一个模块里的 Celery 任务函数**，直接调用会在本进程执行。
测试把 `_get_embedding_service` 换成假实现即可 —— 但**不**替换 `encode_text`
本身：`_embed_chunks_async` 调的就是它，替换掉就等于把"写回"这条路径也桩掉了，
那正是要验的东西。
"""

from __future__ import annotations

import pytest


#: 假向量：固定 4 维，够 `pack_vector` 打包，也不需要任何模型
_FAKE_VEC = [0.1, 0.2, 0.3, 0.4]


class _FakeService:
    """`EmbeddingService` 的最小替身（只需要 `encode` 与 `loaded_model_name`）"""

    loaded_model_name = "BAAI/bge-m3"

    def __init__(self, calls: list | None = None):
        self.calls = calls if calls is not None else []

    def encode(self, texts):
        self.calls.append(list(texts))
        return [list(_FAKE_VEC) for _ in texts]


@pytest.fixture
def fake_embedding(monkeypatch):
    """把嵌入服务换成假实现，并返回它（供断言"有没有被调用"）"""
    from app.tasks import embedding_tasks as et

    service = _FakeService()
    monkeypatch.setattr(et, "_get_embedding_service", lambda: service)
    return service


async def _seed_chunks(test_db, specs):
    """建一篇笔记 + 若干 chunk

    specs: [(id, has_embedding)] —— 已嵌入的会带上四列，未嵌入的四列为空
    """
    from app.models.chunk import Chunk, pack_vector
    from app.models.note import Note, NoteStatus, SourceType
    from app.models.user import User

    async with test_db() as session:
        session.add(User(id="u1", email="a@x.c", username="a", hashed_password="x"))
        await session.flush()
        session.add(
            Note(
                id="n1",
                user_id="u1",
                title="n1",
                source_type=SourceType.pdf,
                status=NoteStatus.cleaned,
                file_size=1,
                original_file_path="/u1/n1.pdf",
                original_md_path="/u1/n1.md",
            )
        )
        await session.flush()
        session.add_all(
            [
                Chunk(
                    id=cid,
                    user_id="u1",
                    note_id="n1",
                    index=i,
                    content=f"第 {i} 段内容",
                    char_start=0,
                    char_end=6,
                    char_count=6,
                    has_embedding=has_emb,
                    embedding=pack_vector(_FAKE_VEC) if has_emb else None,
                    embedding_dim=len(_FAKE_VEC) if has_emb else None,
                    embedding_model="test" if has_emb else None,
                )
                for i, (cid, has_emb) in enumerate(specs)
            ]
        )
        await session.commit()


async def _chunk_state(test_db):
    """{id: (has_embedding, embedding_dim, embedding_model)}"""
    from sqlalchemy import select

    from app.models.chunk import Chunk

    async with test_db() as session:
        rows = (await session.execute(select(Chunk))).scalars().all()
        return {c.id: (c.has_embedding, c.embedding_dim, c.embedding_model) for c in rows}


class TestEmbedChunks:
    """`embed_chunks` 单任务侧"""

    @pytest.mark.asyncio
    async def test_only_pending_rows_are_embedded(self, test_db, fake_embedding):
        """★ 只补 `has_embedding=False` 的行，已嵌入的不重算"""
        from app.tasks import embedding_tasks as et

        await _seed_chunks(test_db, [("c1", False), ("c2", True), ("c3", False)])

        result = await et._embed_chunks_async(limit=100)

        assert result["scanned"] == 2, f"扫到了不该扫的行：{result}"
        assert result["embedded"] == 2
        assert result["failed"] == 0
        assert result["remaining"] == 0

        state = await _chunk_state(test_db)
        assert state["c1"][0] is True and state["c3"][0] is True, "缺向量的行没被补上"
        assert state["c1"][1] == len(_FAKE_VEC), "embedding_dim 没写"
        assert state["c2"][2] == "test", "已嵌入的行被重算了（embedding_model 被覆盖）"

        # 只编码了那两条（不是三条）—— 这是"不重算"的直接证据。
        # ⚠️ 口径：`encode_text` 是**逐条**调的（它内部对每条文本查 lru_cache），
        # 所以假实现收到的是 2 次单元素调用，不是 1 次两元素调用。
        assert fake_embedding.calls == [["第 0 段内容"], ["第 2 段内容"]], (
            f"实际编码的文本不对：{fake_embedding.calls}"
        )

    @pytest.mark.asyncio
    async def test_no_pending_returns_zero_and_does_not_encode(self, test_db, fake_embedding):
        """没有待办时**一次编码都不发**（也不该加载模型）"""
        from app.tasks import embedding_tasks as et

        await _seed_chunks(test_db, [("c1", True)])

        result = await et._embed_chunks_async(limit=100)

        assert result["scanned"] == 0
        assert result["embedded"] == 0
        assert fake_embedding.calls == [], "没有待办却调用了模型"

    @pytest.mark.asyncio
    async def test_limit_is_respected_and_remaining_is_reported(self, test_db, fake_embedding):
        """`limit` 是硬上限，且 `remaining` 如实报出还剩多少（可续跑）"""
        from app.tasks import embedding_tasks as et

        await _seed_chunks(test_db, [(f"c{i}", False) for i in range(5)])

        first = await et._embed_chunks_async(limit=2)
        assert first["embedded"] == 2
        assert first["remaining"] == 3, f"剩余数不对：{first}"

        second = await et._embed_chunks_async(limit=100)
        assert second["embedded"] == 3
        assert second["remaining"] == 0


class TestBackfillEmbeddings:
    """周期编排任务侧"""

    @pytest.mark.asyncio
    async def test_vacuous_round_does_not_touch_the_model(self, test_db, fake_embedding):
        """★ 空转保护：没有待补内容时**不加载模型**、直接返回 skipped

        周期任务最常见的状态就是"没有活干"（每 30 分钟一次）。那时若仍去读
        4.3GB 权重，等于每天白白加载几十次 —— 这条用例是它的守卫。
        """
        from app.tasks import embedding_tasks as et

        await _seed_chunks(test_db, [("c1", True)])  # 全部已嵌入

        result = await et._backfill_embeddings_async(limit=100)

        assert result == {"chunks": {"skipped": True}, "cards": {"skipped": True}}
        assert fake_embedding.calls == [], "空转的一轮加载了模型"

    @pytest.mark.asyncio
    async def test_embeds_pending_chunks_and_skips_cards_when_none(self, test_db, fake_embedding):
        """有待补 chunk 时补 chunk；没有待补卡片时卡片那半报 skipped"""
        from app.tasks import embedding_tasks as et

        await _seed_chunks(test_db, [("c1", False), ("c2", False)])

        result = await et._backfill_embeddings_async(limit=100)

        assert result["chunks"]["embedded"] == 2
        assert result["cards"] == {"skipped": True}

        state = await _chunk_state(test_db)
        assert all(v[0] for v in state.values()), "编排任务没把 chunk 补上"

    @pytest.mark.asyncio
    async def test_model_name_recorded_is_the_loaded_one(self, test_db, monkeypatch):
        """★ 写进 `embedding_model` 的是**实际加载的**模型，不是配置值

        与 `test_graph_relation_suggestion.py::test_model_name_reports_the_actually_loaded_model`
        同一条口径：内存不足时 `EmbeddingService` 会自动降级到
        `BAAI/bge-small-zh-v1.5`（512 维），若把降级向量标成主模型名，
        那一列就废了（它存在的意义就是"将来按模型重算"）。
        """
        from app.tasks import embedding_tasks as et

        class _Degraded:
            loaded_model_name = "BAAI/bge-small-zh-v1.5"

            def encode(self, texts):
                return [list(_FAKE_VEC) for _ in texts]

        monkeypatch.setattr(et, "_get_embedding_service", lambda: _Degraded())
        await _seed_chunks(test_db, [("c1", False)])

        result = await et._embed_chunks_async(limit=100)

        assert result["model"] == "BAAI/bge-small-zh-v1.5"
        state = await _chunk_state(test_db)
        assert state["c1"][2] == "BAAI/bge-small-zh-v1.5", (
            f"embedding_model 记成了 {state['c1'][2]!r} —— 降级向量被标成主模型"
        )


class TestBeatScheduleRegistration:
    """任务必须**真的挂在 Beat 上** —— 写好了没人调用是本仓库反复踩过的坑

    `docs/overhaul-plan.md` 记过至少三处同类问题（附录 AF.10 的账本清理、
    AU.1 的限流规则、`vault_audit` 只有手工脚本调用）。
    这条用例是"这次没有重犯"的证据。
    """

    def test_backfill_embeddings_is_in_beat_schedule(self):
        from app.tasks.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        entry = schedule.get("backfill-embeddings")
        assert entry is not None, (
            f"beat_schedule 里没有 backfill-embeddings —— "
            f"现有 {sorted(schedule)}；那正是 BN.3 丙-2 的成因（补嵌入只有人工脚本）"
        )
        assert entry["task"] == "app.tasks.embedding_tasks.backfill_embeddings", (
            "任务名写错 ⇒ Beat 会在运行期报 'Received unregistered task'"
        )

    def test_scheduled_task_is_importable(self):
        """任务名能被解析到真实函数（写错名字的第二种形态）"""
        from app.tasks.embedding_tasks import backfill_embeddings

        assert callable(backfill_embeddings)
        assert backfill_embeddings.name == "app.tasks.embedding_tasks.backfill_embeddings"

    def test_schedule_avoids_the_backup_windows(self):
        """⚠️ 排期必须避开 03:30 备份 / 00:30 目标进度 / 04:15-04:30 清理

        理由与 `daily-database-backup` 的注释同源：备份会持读事务，
        与写任务错开可减少 SQLite 单写者争用（`docs/sqlite-single-writer.md`）。
        """
        from app.tasks.celery_app import celery_app

        entry = celery_app.conf.beat_schedule["backfill-embeddings"]
        minute_spec = str(entry["schedule"])
        # crontab(minute="5,35") 的字符串形态里含 "5,35"；这里只断言它**不是**
        # 那几个整点任务的分钟值（30 会撞 00:30 与 03:30）
        assert "30" not in minute_spec, (
            f"排期 {minute_spec!r} 落在 :30 —— 会与 00:30 目标进度 / 03:30 备份重叠"
        )
