"""
修复回归测试（对应 fix_improvement_plan.md v2）

覆盖：
- F-01：card_relations 唯一索引 / 完全同键去重 / 方向与类型共存
- F-07：SQLite 外键开启 / delete_note 清理 note_projects
- F-09：goal scope 归属校验（IDOR 拒绝）
- F-14：复习提交到期校验 / 同日幂等
- F-16：跨扩展名同名 base 冲突检测
- F-32：搜索通配符转义（ilike escape）

运行：cd backend && python -m pytest tests/test_fixes.py -v
（测试使用独立临时库，不触碰真实数据）
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def _run(coro):
    """同步包装异步测试体（不依赖 pytest-asyncio 插件）"""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# F-01 / F-07：唯一索引、外键、安全去重
# ---------------------------------------------------------------------------

def test_f01_unique_index_and_direction_preserved(test_db):
    """完全同键重复被唯一索引拒绝；方向相反/不同类型关系可共存"""
    from sqlalchemy import select, text

    async def _run_test():
        from app.models.user import User
        from app.models.note import Note, SourceType, NoteStatus
        from app.models.knowledge_card import KnowledgeCard, CardType
        from app.models.card_relation import CardRelation, RelationType, RelationStatus

        async with test_db() as s:
            # 1. 唯一索引存在
            idx = (await s.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='uq_card_relations_pair_type'"
            ))).scalars().all()
            assert idx, "uq_card_relations_pair_type 索引缺失"

            # 2. 外键开启
            fk = (await s.execute(text("PRAGMA foreign_keys"))).scalar()
            assert fk == 1, "PRAGMA foreign_keys 未开启"

            # 3. 造数据
            s.add(User(id="u1", email="a@b.c", username="a", hashed_password="x"))
            await s.flush()
            s.add_all([
                Note(id="n1", user_id="u1", title="n1", source_type=SourceType.pdf,
                     status=NoteStatus.cleaned, file_size=1,
                     original_file_path="/u1/n1.pdf", original_md_path="/u1/n1.md"),
                Note(id="n2", user_id="u1", title="n2", source_type=SourceType.pdf,
                     status=NoteStatus.cleaned, file_size=1,
                     original_file_path="/u1/n2.pdf", original_md_path="/u1/n2.md"),
            ])
            await s.flush()
            s.add_all([
                KnowledgeCard(id="c1", user_id="u1", note_id="n1", title="t1",
                              content="x", card_type=CardType.concept),
                KnowledgeCard(id="c2", user_id="u1", note_id="n1", title="t2",
                              content="x", card_type=CardType.concept),
            ])
            await s.flush()
            # 正向 prerequisite + 反向 prerequisite（应共存）
            s.add_all([
                CardRelation(id="r1", user_id="u1", card_id_1="c1", card_id_2="c2",
                             relation_type=RelationType.prerequisite,
                             status=RelationStatus.confirmed),
                CardRelation(id="r3", user_id="u1", card_id_1="c2", card_id_2="c1",
                             relation_type=RelationType.prerequisite,
                             status=RelationStatus.confirmed),
            ])
            await s.commit()

            # 4. 完全同键重复插入必须被拒绝
            s.add(CardRelation(id="r2", user_id="u1", card_id_1="c1", card_id_2="c2",
                               relation_type=RelationType.prerequisite,
                               status=RelationStatus.confirmed))
            with pytest.raises(Exception) as exc_info:
                await s.commit()
            assert "unique" in str(exc_info.value).lower()
            await s.rollback()

            # 5. 方向相反的关系确实共存
            rows = (await s.execute(text(
                "SELECT id FROM card_relations ORDER BY id"
            ))).scalars().all()
            assert rows == ["r1", "r3"], f"方向相反关系未保留: {rows}"

    _run(_run_test())


def test_f07_delete_note_cleans_note_projects(test_db):
    """删除笔记后 knowledge_cards 与 note_projects 无残留"""
    from sqlalchemy import select, text

    async def _run_test():
        from app.models.user import User
        from app.models.note import Note, SourceType, NoteStatus
        from app.models.project import Project
        from app.models.note_project import NoteProject
        from app.services.note_service import delete_note

        async with test_db() as s:
            s.add(User(id="u1", email="a@b.c", username="a", hashed_password="x"))
            await s.flush()
            s.add_all([
                Note(id="n1", user_id="u1", title="n1", source_type=SourceType.pdf,
                     status=NoteStatus.cleaned, file_size=1,
                     original_file_path="/u1/n1.pdf", original_md_path="/u1/n1.md"),
                Project(id="p1", user_id="u1", name="proj1"),
            ])
            await s.commit()
            s.add(NoteProject(id="np1", note_id="n1", project_id="p1", user_id="u1"))
            await s.commit()

            note = (await s.execute(
                select(Note).where(Note.id == "n1")
            )).scalar_one()
            await delete_note(s, note)
            await s.commit()

            kc = (await s.execute(text(
                "SELECT COUNT(*) FROM knowledge_cards WHERE note_id='n1'"
            ))).scalar()
            assert kc == 0, f"知识卡片残留: {kc}"
            np = (await s.execute(text(
                "SELECT COUNT(*) FROM note_projects WHERE note_id='n1'"
            ))).scalar()
            assert np == 0, f"note_projects 孤儿残留: {np}"

    _run(_run_test())


# ---------------------------------------------------------------------------
# F-09：goal scope 归属校验（IDOR）
# ---------------------------------------------------------------------------

def test_f09_goal_scope_idor_rejected(test_db):
    from fastapi import HTTPException

    async def _run_test():
        from app.models.user import User
        from app.models.note import Note, SourceType, NoteStatus
        from app.models.folder import Folder
        from datetime import datetime, timezone
        from app.services.goal_service import goal_service

        async with test_db() as s:
            s.add_all([
                User(id="u1", email="a@x.c", username="a", hashed_password="x"),
                User(id="u2", email="b@x.c", username="b", hashed_password="x"),
            ])
            await s.flush()
            s.add_all([
                Note(id="n1", user_id="u1", title="n1", source_type=SourceType.pdf,
                     status=NoteStatus.cleaned, file_size=1,
                     original_file_path="/u1/n1.pdf", original_md_path="/u1/n1.md"),
                Note(id="n2", user_id="u2", title="n2", source_type=SourceType.pdf,
                     status=NoteStatus.cleaned, file_size=1,
                     original_file_path="/u2/n2.pdf", original_md_path="/u2/n2.md"),
            ])
            await s.flush()
            s.add_all([
                Folder(id="f1", user_id="u1", name="f1",
                       folder_date=datetime.now(timezone.utc)),
                Folder(id="f2", user_id="u2", name="f2",
                       folder_date=datetime.now(timezone.utc)),
            ])
            await s.commit()

            # 合法 scope 通过
            class Req:
                name = "目标1"
                type = None
                scope_notes = ["n1"]
                scope_folders = ["f1"]
                target_mastery = 80.0
                deadline = None
            await goal_service.create_goal("u1", Req(), s)

            # 越权 note 拒绝
            class ReqBad:
                name = "目标2"
                type = None
                scope_notes = ["n2"]
                scope_folders = []
                target_mastery = 80.0
                deadline = None
            with pytest.raises(HTTPException) as exc:
                await goal_service.create_goal("u1", ReqBad(), s)
            assert exc.value.status_code == 400

            # 越权 folder 拒绝
            class ReqBad2:
                name = "目标3"
                type = None
                scope_notes = []
                scope_folders = ["f2"]
                target_mastery = 80.0
                deadline = None
            with pytest.raises(HTTPException) as exc2:
                await goal_service.create_goal("u1", ReqBad2(), s)
            assert exc2.value.status_code == 400

    _run(_run_test())


# ---------------------------------------------------------------------------
# F-14：复习到期校验 + 同日幂等
# ---------------------------------------------------------------------------

def test_f14_due_check_and_idempotency(test_db):
    from datetime import datetime, timedelta, timezone

    async def _run_test():
        from app.models.user import User
        from app.models.note import Note, SourceType, NoteStatus
        from app.models.knowledge_card import KnowledgeCard, CardType
        from app.models.quiz_item import QuizItem, QuestionType, DifficultyLevel
        from app.services.review_service import submit_answer

        async with test_db() as s:
            s.add(User(id="u1", email="a@b.c", username="a", hashed_password="x"))
            await s.flush()
            s.add(Note(id="n1", user_id="u1", title="n1", source_type=SourceType.pdf,
                       status=NoteStatus.cleaned, file_size=1,
                       original_file_path="/u1/n1.pdf", original_md_path="/u1/n1.md"))
            await s.flush()
            s.add(KnowledgeCard(id="c1", user_id="u1", note_id="n1", title="t1",
                                content="x", card_type=CardType.concept))
            await s.flush()
            # 未到期题目（下次复习在未来）
            future = datetime.now(timezone.utc) + timedelta(days=3)
            s.add(QuizItem(id="q1", user_id="u1", card_id="c1", note_id="n1",
                           question_type=QuestionType.choice,
                           difficulty=DifficultyLevel.medium,
                           question="1+1=?", answer="2", next_review_at=future))
            # 到期题目（已到期）
            past = datetime.now(timezone.utc) - timedelta(days=1)
            s.add(QuizItem(id="q2", user_id="u1", card_id="c1", note_id="n1",
                           question_type=QuestionType.choice,
                           difficulty=DifficultyLevel.medium,
                           question="2+2=?", answer="4", next_review_at=past))
            await s.commit()

            # 1. 未到期提交被拒（普通复习）
            res = await submit_answer("q1", "u1", "2", 1000, s)
            assert "error" in res and "未到期" in res["error"], f"未到期应被拒: {res}"

            # 2. 快速复习（skip_due_check）允许
            res_q = await submit_answer("q1", "u1", "2", 1000, s,
                                        skip_daily_limit=True, skip_due_check=True)
            assert "error" not in res_q, f"快速复习应通过: {res_q}"

            # 3. 到期题目提交成功
            res2 = await submit_answer("q2", "u1", "4", 1000, s)
            assert "error" not in res2, f"到期提交应成功: {res2}"
            first_review_count = res2["sm2"]["repetition"]

            # 4. 同日重复提交幂等：返回历史结果，SM-2 不叠加
            res3 = await submit_answer("q2", "u1", "4", 1000, s)
            assert "error" not in res3, f"幂等命中不应报错: {res3}"
            assert res3["sm2"]["repetition"] == first_review_count, \
                f"幂等未生效，SM-2 被叠加: {res3['sm2']}"
            from sqlalchemy import select, func
            from app.models.review_log import ReviewLog
            log_count = (await s.execute(
                select(func.count()).select_from(ReviewLog).where(
                    ReviewLog.quiz_id == "q2"
                )
            )).scalar()
            assert log_count == 1, f"同日重复提交产生 {log_count} 条日志（应为 1）"

    _run(_run_test())


# ---------------------------------------------------------------------------
# F-16：跨扩展名同名 base 冲突检测
# ---------------------------------------------------------------------------

def test_f16_cross_extension_base_conflict(test_db):
    async def _run_test():
        from app.models.user import User
        from app.models.note import Note, SourceType, NoteStatus
        from app.api.upload import _resolve_unique_base

        async with test_db() as s:
            s.add(User(id="u1", email="a@b.c", username="a", hashed_password="x"))
            await s.flush()
            # 已有 a.pdf（base=a）
            s.add(Note(id="n1", user_id="u1", title="a", source_type=SourceType.pdf,
                       status=NoteStatus.uploading, file_size=1,
                       original_file_path="u1/inbox/source/a.pdf",
                       original_md_path=""))
            await s.commit()

            # 再上传 a.md：base 必须加后缀，不能仍是 a（F-16 修复）
            base = await _resolve_unique_base(s, "u1", "u1/inbox", "a", ".md")
            assert base != "a", f"跨扩展名冲突未检测到，base 仍为 a: {base}"
            assert base.startswith("a_"), f"base 应为 a_xxx: {base}"

            # 不冲突的名字保持原样
            base2 = await _resolve_unique_base(s, "u1", "u1/inbox", "b", ".md")
            assert base2 == "b"

    _run(_run_test())


# ---------------------------------------------------------------------------
# 回归：understand_tasks._understand_document 变量复用（result 被 execute 覆盖）
# ---------------------------------------------------------------------------

def test_understand_document_no_result_override():
    """
    修复验证：_understand_document 中第 7 步的 session.execute 曾复用变量名 result，
    覆盖 process_note_understanding 返回的 dict，导致日志里 result.get("total_cards")
    对 ChunkedIteratorResult 调用而抛 AttributeError（Celery 任务失败并重试 → 重复卡片）。

    通过 mock 外部依赖（对象存储/LLM/状态更新），调用完整 _understand_document，
    断言：不抛异常、状态更新为 archived、日志路径使用 dict 返回值。
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    async def _run_test():
        from app.tasks import understand_tasks as ut

        # --- mock 会话工厂 ---
        fake_session = MagicMock()
        # session.get(Note, note_id)：第一处"笔记是否已删除"检查
        fake_note = MagicMock()
        fake_note.status = None
        fake_note.error_message = None
        # 第二处 execute 查询 Note：clean_md_path/original_md_path/user_id
        fake_execute = MagicMock()
        fake_scalars = MagicMock()
        fake_scalars.first.return_value = fake_note
        fake_execute.scalars.return_value = fake_scalars
        fake_session.execute = AsyncMock(return_value=fake_execute)
        fake_session.get = AsyncMock(return_value=fake_note)

        fake_factory = MagicMock()
        fake_factory.return_value.__aenter__ = AsyncMock(return_value=fake_session)
        fake_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        # --- mock 各依赖 ---
        fake_md = MagicMock()
        fake_md.decode.return_value = "# 第一章\n内容"
        fake_process = AsyncMock(return_value={"chapter_count": 1, "total_cards": 3})
        fake_update = AsyncMock()

        with patch.object(ut, "_get_understand_session", return_value=fake_factory), \
             patch("app.services.storage_service.get_object_bytes", return_value=fake_md), \
             patch("app.services.storage_service.ensure_buckets_exist", return_value=None), \
             patch("app.services.understanding_service.process_note_understanding", fake_process), \
             patch.object(ut, "_update_note_status", fake_update), \
             patch.object(ut, "generate_questions_task") as fake_gen:
            fake_gen.delay = MagicMock()
            await ut._understand_document("note-1")

        # 断言：状态更新为 archived（走完第 7 步，未被 AttributeError 中断）
        archived_calls = [c for c in fake_update.call_args_list
                          if len(c.args) >= 2 and c.args[1] == ut.NoteStatus.archived]
        assert archived_calls, "未走到 archived 状态更新（可能仍在抛异常）"
        # 断言：题目生成任务被触发
        fake_gen.delay.assert_called_once_with("note-1")
        # 断言：process 返回值被消费（total_cards=3 进入日志——通过未抛异常间接验证）

    _run(_run_test())

    # 兜底静态断言：源码中 execute 不再复用 result 变量名
    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "tasks", "understand_tasks.py",
    )
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    # 第 7 步的 execute 必须用独立变量名（修复点）
    assert "note_result = await session.execute" in src, "修复点丢失：execute 未改用 note_result"
    # result.get 只能出现在 process 返回的 dict 上下文中
    assert 'result.get("total_cards"' in src, "修复点丢失：result.get 调用不存在"


# ---------------------------------------------------------------------------
# 回归：理解响应 JSON 解析容错（max_tokens 截断 / markdown 包裹）
# ---------------------------------------------------------------------------

def test_parse_understanding_response_robust():
    """代码块包裹剥离 + 截断 JSON 安全降级 + 合法 JSON 正常解析"""
    from app.services.understanding_service import _parse_understanding_response

    # 1. markdown 代码块包裹
    r1 = _parse_understanding_response(
        '```json\n{"chapters": [{"chapter_title": "c1", "summary": "s1", '
        '"points": [{"card_type": "concept", "title": "t", "content": "x"}]}]}\n```'
    )
    assert r1.get("chapters") and r1["chapters"][0]["chapter_title"] == "c1", r1

    # 2. 截断 JSON（max_tokens 截断的典型形态）→ 不崩溃、返回空结构
    truncated = ('{"chapters": [{"chapter_title": "1 范围", "summary": "摘要", '
                 '"points": [{"card_type": "conc')
    r2 = _parse_understanding_response(truncated)
    assert r2 == {"summary": "", "points": []}, r2

    # 3. 合法 JSON 正常解析
    r3 = _parse_understanding_response(
        '{"chapters": [{"chapter_title": "c1", "summary": "s", '
        '"points": [{"card_type": "concept", "title": "t"}]}]}'
    )
    assert len(r3["chapters"]) == 1

    # 4. 空响应不崩溃
    r4 = _parse_understanding_response("")
    assert r4 == {"summary": "", "points": []}


def test_llm_client_loop_rebuild():
    """共享 LLM 客户端跨事件循环自动重建（修 Event loop is closed）"""
    import asyncio
    from app.services import llm_service as ls

    async def use_in_loop():
        return id(ls.get_llm_client())

    loop1_id = asyncio.run(use_in_loop())
    loop2_id = asyncio.run(use_in_loop())
    assert loop1_id != loop2_id, "跨事件循环未重建客户端"
    ls.close_llm_client()


def test_max_tokens_no_4096():
    """提取/出题场景 max_tokens 已全部提升到 8192（静态断言，防回退）"""
    import re
    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "services", "llm_service.py",
    )
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    assert "max_tokens=4096" not in src, "存在 max_tokens=4096（截断风险）"


# ---------------------------------------------------------------------------
# F-32：搜索通配符转义
# ---------------------------------------------------------------------------

def test_f32_search_escape(test_db):
    async def _run_test():
        from app.models.user import User
        from app.models.note import Note, SourceType, NoteStatus
        from app.services.note_service import get_notes_list

        async with test_db() as s:
            s.add(User(id="u1", email="a@b.c", username="a", hashed_password="x"))
            await s.flush()
            s.add_all([
                Note(id="n1", user_id="u1", title="进度100%达成", source_type=SourceType.pdf,
                     status=NoteStatus.cleaned, file_size=1,
                     original_file_path="/u1/n1.pdf", original_md_path="/u1/n1.md"),
                Note(id="n2", user_id="u1", title="进度100达成", source_type=SourceType.pdf,
                     status=NoteStatus.cleaned, file_size=1,
                     original_file_path="/u1/n2.pdf", original_md_path="/u1/n2.md"),
            ])
            await s.commit()

            # 搜索字面量 "100%"：只应匹配 n1（F-32 修复前 % 会被当通配符匹配任意）
            notes, total = await get_notes_list(s, user_id="u1", keyword="100%")
            assert total == 1, f"搜索 100% 应只匹配 1 条（转义生效），实际 {total}"
            assert notes[0].id == "n1"

            # 普通关键词正常命中
            notes2, total2 = await get_notes_list(s, user_id="u1", keyword="进度100")
            assert total2 == 2, f"普通关键词应匹配 2 条: {total2}"

    _run(_run_test())
