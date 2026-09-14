# -*- coding: utf-8 -*-
"""学习目标进度：空 scope 的语义（回归测试）

## 为什么需要这个文件

`app/services/goal_service.py::get_goal_progress` 的三个聚合里有**两个**
（total_count / reviewed_count）写在 `if scope_notes:` 之内，而 avg_mastery
曾有一个 `else` 分支：scope 为空时按 `user_id` 聚合同一用户的**全部**卡片。
于是同一个函数对"范围为空"给出两种互相矛盾的答案：题目数、今日复习数是 0，
平均掌握度却是"全部卡片"的均值。progress_percentage 随之从 0 跳到几十甚至 100。

## 这不是假想状态，是应用自己会造出来的

`app/services/note_service.py::purge_note` 步骤 5.2 会**把被删笔记从
`goal.scope_notes` 里摘掉并保留目标本身**：

    goal.scope_notes = [n for n in (goal.scope_notes or []) if n != note_id]

所以"目标还在、范围空了"是正常操作的结果。真库上确实存在这样一个目标
（u1 的 35a1f6b5-…，scope_notes 已是 []），它当时之所以没有暴露，
只是因为它所属的夹具用户名下恰好没有卡片 —— 现场记录见
`backend/data/db/cleanup-notes-n1-n2.log` §7.3。

## 这里刻意**不**调用 purge_note 本身

purge_note 还会删磁盘上的 vault 文件与版本历史，而 `test_db` fixture 只把
DATABASE_URL 指向临时库、**不换 vault 目录** —— 在单元测试里调它等于去动真实
存储。因此第 3 个用例用同一行规则（逐字复制步骤 5.2）模拟那个状态，
并把"范围外还剩一张高分卡片"显式建出来，让"错误地统计全部卡片"必然可见。
"""

import pytest


@pytest.mark.asyncio
class TestGoalProgressEmptyScope:
    """空 scope = 范围内什么都没有，不是"全部卡片"（三个聚合必须一致）"""

    async def _seed(self, test_db):
        """u1 / n1(卡片 40,60) / n2(卡片 100) + 两个目标

        目标 g-empty  : scope_notes = []       （范围已空）
        目标 g-scoped : scope_notes = ["n1"]   （范围指向 n1）
        """
        from datetime import datetime, timezone

        from app.models.user import User
        from app.models.note import Note, SourceType, NoteStatus
        from app.models.knowledge_card import KnowledgeCard, CardType
        from app.models.learning_goal import LearningGoal

        now = datetime.now(timezone.utc)
        async with test_db() as s:
            s.add(User(id="u1", email="a@x.c", username="a", hashed_password="x"))
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
                KnowledgeCard(id="c1", user_id="u1", note_id="n1", card_type=CardType.concept,
                              title="c1", content="x", mastery_level=40.0),
                KnowledgeCard(id="c2", user_id="u1", note_id="n1", card_type=CardType.concept,
                              title="c2", content="x", mastery_level=60.0),
                # 范围外的卡片：任何"按 user_id 聚合全部卡片"的实现都会把它算进去
                KnowledgeCard(id="c9", user_id="u1", note_id="n2", card_type=CardType.concept,
                              title="c9", content="x", mastery_level=100.0),
            ])
            s.add_all([
                LearningGoal(id="g-empty", user_id="u1", name="空范围", type="weekly",
                             scope_notes=[], scope_folders=[], target_mastery=80.0,
                             status="active", progress_cache=0.0),
                LearningGoal(id="g-scoped", user_id="u1", name="有范围", type="weekly",
                             scope_notes=["n1"], scope_folders=[], target_mastery=80.0,
                             status="active", progress_cache=0.0),
            ])
            await s.commit()
        return now

    async def test_empty_scope_reports_nothing_not_all_cards(self, test_db):
        """**核心回归**：范围为空时 avg_mastery 必须是 0.0，而不是全部卡片的均值"""
        from sqlalchemy import func, select

        from app.models.knowledge_card import KnowledgeCard
        from app.services.goal_service import goal_service

        await self._seed(test_db)

        # 反空洞：先证明这个用户**确实**有卡片，否则下面的 0 可能只是"库里没数据"
        async with test_db() as s:
            owned = (await s.execute(
                select(func.count()).select_from(KnowledgeCard)
                .where(KnowledgeCard.user_id == "u1")
            )).scalar()
            assert owned == 3, f"夹具没建出卡片（{owned} 张），本用例会变成空洞断言"

        async with test_db() as s:
            progress = await goal_service.get_goal_progress("g-empty", "u1", s)

        assert progress["avg_mastery"] == 0.0, (
            "空 scope 的目标报告了平均掌握度 %.2f —— 统计到了范围外的卡片。"
            "三个聚合必须对'范围为空'给出一致答案：范围内没有任何东西。"
            % progress["avg_mastery"]
        )
        assert progress["progress_percentage"] == 0.0
        assert progress["total_count"] == 0
        assert progress["reviewed_count"] == 0

    async def test_non_empty_scope_still_filters_to_the_scope(self, test_db):
        """非空 scope 的行为不得改变：只统计范围内的卡片（40/60 → 50，不是 66.67）

        这个用例防的是"把 avg_mastery 恒置 0"这种假修复：它同时钉住
        "范围过滤仍然生效"，即 n2 的 100 分卡片不能进入均值。
        """
        from app.services.goal_service import goal_service

        await self._seed(test_db)

        async with test_db() as s:
            progress = await goal_service.get_goal_progress("g-scoped", "u1", s)

        assert progress["avg_mastery"] == 50.0
        # 50 / 80 * 100 = 62.5（target_mastery 为 80 时）
        assert progress["progress_percentage"] == 62.5
        assert progress["total_count"] == 0
        assert progress["reviewed_count"] == 0

    async def test_scope_emptied_by_purge_rule_reports_nothing(self, test_db):
        """范围因笔记被删而清空后（purge_note 步骤 5.2），进度必须是 0

        复现真实触发路径：范围本来是 n1（进度 50），n1 被物理删除、
        规则把 "n1" 从 scope_notes 摘掉。此时用户名下只剩 n2 的 100 分卡片 ——
        旧实现会报告 avg_mastery=100 / progress_percentage=100，等于
        "目标因为删掉资料而突然完成了"。
        """
        from sqlalchemy import delete, select

        from app.models.note import Note
        from app.models.knowledge_card import KnowledgeCard
        from app.models.learning_goal import LearningGoal
        from app.services.goal_service import goal_service

        await self._seed(test_db)

        async with test_db() as s:
            before = await goal_service.get_goal_progress("g-scoped", "u1", s)
        assert before["avg_mastery"] == 50.0

        # ---- 模拟 purge_note：删卡片、删笔记、按步骤 5.2 摘掉 scope 引用 ----
        async with test_db() as s:
            await s.execute(delete(KnowledgeCard).where(KnowledgeCard.note_id == "n1"))
            goal = (await s.execute(
                select(LearningGoal).where(LearningGoal.id == "g-scoped")
            )).scalars().first()
            # 逐字复制 app/services/note_service.py::purge_note 步骤 5.2
            goal.scope_notes = [n for n in (goal.scope_notes or []) if n != "n1"]
            await s.execute(delete(Note).where(Note.id == "n1"))
            await s.commit()

        async with test_db() as s:
            after = await goal_service.get_goal_progress("g-scoped", "u1", s)

        assert after["avg_mastery"] == 0.0, (
            "范围清空后平均掌握度变成了 %.2f —— 统计到了范围外剩下的卡片"
            % after["avg_mastery"]
        )
        assert after["progress_percentage"] == 0.0
        assert after["total_count"] == 0
