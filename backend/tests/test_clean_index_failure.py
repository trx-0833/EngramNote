# -*- coding: utf-8 -*-
"""
chunk 索引重建失败的处理（D2-③）
================================

## 这一组盯的是什么

清洗流程第 11 步会重建 chunk 索引，而它此前的失败处理是：

```python
except Exception as exc:
    logger.warning("chunk 索引重建失败（不影响清洗结果）: note_id=%s, %s", note_id, exc)
```

两个问题，各有一条用例：

1. **"不影响清洗结果"把后果说小了。** 索引重建失败意味着该笔记在
   **FTS 与向量两条通道里都没有语料** —— 问答与原文回跳都查不到它。
   走查报告 §5 第 7 条登记了这条并指出"后果被低估"。
2. **失败只进了日志。** 日志只有运维看得到，而用户看到的是
   "这篇笔记问不出来"且不知原因。现在写进 `note.error_message` ——
   那个字段在界面上是可见的（`NoteDetailHeader.tsx:368`、
   `NotesList.tsx:373`、`CleaningPanel.tsx:258`）。

另外锁住一条**不能破的既有约定**：索引失败**不改变笔记状态**
（主产物 clean.md 已经落盘，状态就该是 `cleaned`）。

## 为什么重试是 3 次

实测触发的那次失败（`Instance <Chunk …> has a NULL identity key`）是
事务/主键类的**瞬时**问题 —— 根因已在 `f9f50ca` 修掉，但同类瞬时失败
还会再来。重试一次就过去的错误，不该让用户重新清洗一遍。
"""

from __future__ import annotations

import pytest


async def _seed_note(test_db, note_id: str = "n1", status=None):
    """建一个用户 + 一篇 cleaned 笔记"""
    from app.models.note import Note, NoteStatus, SourceType
    from app.models.user import User

    async with test_db() as session:
        session.add(User(id="u1", email="a@x.c", username="a", hashed_password="x"))
        await session.flush()
        session.add(
            Note(
                id=note_id,
                user_id="u1",
                title=note_id,
                source_type=SourceType.pdf,
                status=status or NoteStatus.cleaned,
                file_size=1,
                original_file_path=f"/u1/{note_id}.pdf",
                original_md_path=f"/u1/{note_id}.md",
            )
        )
        await session.commit()


async def _note_row(test_db, note_id: str = "n1"):
    from sqlalchemy import select

    from app.models.note import Note

    async with test_db() as session:
        return (await session.execute(select(Note).where(Note.id == note_id))).scalar_one()


class _FlakyIndexer:
    """前 `fail_times` 次抛异常，之后成功；记录被调了几次"""

    def __init__(self, fail_times: int, exc: Exception | None = None):
        self.fail_times = fail_times
        self.calls = 0
        self._exc = exc or RuntimeError("Instance <Chunk at 0x1> has a NULL identity key")

    async def __call__(self, db, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self._exc
        return 7  # 假装写了 7 个 chunk


@pytest.fixture
def no_sleep(monkeypatch):
    """把退避的 `asyncio.sleep` 换掉 —— 用例不该真等 3.5 秒"""
    import asyncio

    slept: list = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return slept


async def _run_rebuild(test_db, indexer, no_sleep):
    """跑一次 `_rebuild_chunk_index_with_retry`，索引实现换成 `indexer`"""
    from app.tasks import clean_tasks as ct

    # `_rebuild_chunk_index_with_retry` 内部是 `from ..services.chunk_service import
    # index_note_from_storage`（函数内导入），所以要打**服务模块**的属性
    import app.services.chunk_service as cs

    original = cs.index_note_from_storage
    cs.index_note_from_storage = indexer
    try:
        return await ct._rebuild_chunk_index_with_retry(
            note_id="n1",
            user_id="u1",
            clean_md_path="/u1/n1.clean.md",
            original_md_path="/u1/n1.md",
        )
    finally:
        cs.index_note_from_storage = original


class TestRetry:
    @pytest.mark.asyncio
    async def test_first_attempt_success_does_not_retry(self, test_db, no_sleep):
        """一次成功就收工 —— 不引入任何多余的重试"""
        indexer = _FlakyIndexer(fail_times=0)
        ok, err = await _run_rebuild(test_db, indexer, no_sleep)

        assert ok is True
        assert err == ""
        assert indexer.calls == 1, f"成功了还重试了 {indexer.calls} 次"
        assert no_sleep == [], "成功了却睡了退避"

    @pytest.mark.asyncio
    async def test_transient_failure_is_retried_and_recovers(self, test_db, no_sleep):
        """★ 瞬时失败重试一次就过去（实测那次 NULL identity key 就是这一类）"""
        indexer = _FlakyIndexer(fail_times=1)
        ok, err = await _run_rebuild(test_db, indexer, no_sleep)

        assert ok is True, f"重试后仍失败：{err}"
        assert indexer.calls == 2
        assert no_sleep == [0.5], f"退避序列不对：{no_sleep}"

    @pytest.mark.asyncio
    async def test_persistent_failure_gives_up_after_bounded_attempts(self, test_db, no_sleep):
        """★ 一直失败时**有界**收手（不无限重试、不让清洗任务变长）"""
        from app.tasks.clean_tasks import _INDEX_RETRY_DELAYS

        indexer = _FlakyIndexer(fail_times=99)
        ok, err = await _run_rebuild(test_db, indexer, no_sleep)

        assert ok is False
        assert "NULL identity key" in err, f"失败原因没带出来：{err!r}"
        assert indexer.calls == len(_INDEX_RETRY_DELAYS) + 1, (
            f"尝试了 {indexer.calls} 次，期望 {len(_INDEX_RETRY_DELAYS) + 1} 次"
        )
        assert no_sleep == list(_INDEX_RETRY_DELAYS), f"退避序列不对：{no_sleep}"


class TestVisibleFailure:
    @pytest.mark.asyncio
    async def test_failure_is_written_to_error_message(self, test_db):
        """★ 最终失败写进 `note.error_message`（界面上可见的那个字段）"""
        from app.tasks import clean_tasks as ct

        await _seed_note(test_db)

        await ct._mark_index_failure("n1", "RuntimeError: 数据库忙")

        note = await _note_row(test_db)
        assert note.error_message, "失败没有写进 error_message —— 用户看不到任何提示"
        assert "检索索引未建好" in note.error_message
        assert "重新清洗" in note.error_message, "没告诉用户怎么办"
        # ⚠️ 措辞不能再写"不影响清洗结果"（那是被低估的后果）
        assert "不影响" not in note.error_message

    @pytest.mark.asyncio
    async def test_failure_does_not_change_status(self, test_db):
        """★ 写说明**不**改状态：主产物已落盘，状态就该是 cleaned"""
        from app.models.note import NoteStatus
        from app.tasks import clean_tasks as ct

        await _seed_note(test_db)
        await ct._mark_index_failure("n1", "boom")

        note = await _note_row(test_db)
        assert note.status == NoteStatus.cleaned, (
            f"状态被改成了 {note.status} —— 索引失败不该动清洗状态（走查报告的既有约定）"
        )

    @pytest.mark.asyncio
    async def test_write_failure_is_best_effort(self, test_db, monkeypatch):
        """写 `error_message` 本身失败时**不抛异常**（这条路径是"出错才走到"）

        否则会变成"索引失败 → 写说明也失败 → 清洗任务整体失败"，
        把一条可用的清洗结果也赔进去。
        """
        from app.tasks import clean_tasks as ct

        await _seed_note(test_db)

        def _boom():
            raise RuntimeError("会话工厂都拿不到")

        monkeypatch.setattr(ct, "_get_clean_session", _boom)

        # 不抛异常即为通过
        await ct._mark_index_failure("n1", "索引挂了")


class TestWording:
    def test_no_log_call_downplays_the_consequence(self):
        """★ **日志调用里**不能再出现"不影响清洗结果"这句被低估的措辞

        ## 为什么是 AST 而不是 grep 整个文件

        第一版写成 `assert "不影响清洗结果" not in src` —— 它**立刻红了**，
        但红的原因是那句短语出现在**解释这次修改的注释里**（注释里必须引用
        旧文案才能说清改了什么）。整文件 grep 分不清"注释里引用"与
        "日志里真的这么写"。

        所以改成 AST：只看 `logger.warning(...)` / `logger.info(...)` 这类
        **调用实参**里的字符串字面量。走查报告 §5 第 7 条要纠正的是
        "日志把后果说小了"，那就精确地只查日志。
        """
        import ast
        import pathlib

        src = pathlib.Path(ct_path()).read_text(encoding="utf-8")
        tree = ast.parse(src)

        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # 只认 `logger.xxx(...)` 形态
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                continue
            if func.value.id != "logger":
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if "不影响清洗结果" in arg.value:
                        offenders.append(f"line {node.lineno}: {arg.value[:60]!r}")

        assert not offenders, (
            "日志里又出现了「不影响清洗结果」—— 索引缺失会让该笔记在 FTS 与向量"
            f"两条通道里都没有语料，后果不止如此：{offenders}"
        )

    def test_failure_log_states_the_consequence(self):
        """失败日志必须**说清后果**（"在检索通道里没有语料"）"""
        import pathlib

        src = pathlib.Path(ct_path()).read_text(encoding="utf-8")
        assert "在检索通道里没有语料" in src, (
            "失败日志没有说清后果 —— 运维看到的是「索引失败」，"
            "而真正要传达的是「这篇笔记问不出来」"
        )


def ct_path() -> str:
    import app.tasks.clean_tasks as m

    return m.__file__
