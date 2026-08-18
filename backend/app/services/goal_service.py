"""
学习目标服务模块

本模块提供学习目标的 CRUD、进度计算和每日任务推荐等业务逻辑。

主要职责：
- 创建/查询/更新/归档/删除学习目标
- 计算目标进度（基于范围内卡片的平均掌握度）
- 生成每日计划（薄弱点 + 到期复习 + 新资料三类任务）
- 定时刷新所有活跃目标的进度缓存（由 Celery Beat 调用）

设计决策：
- 每用户最多 5 个活跃目标，超过则拒绝创建
- 软删除：delete_goal 仅将 status 置为 DELETED，不物理删除
- 每日计划按用户+日期唯一，重复请求返回已有计划
- 任务优先级：weak_point (3) > review (2) > new_material (1)
- refresh_goal_progress 自建异步会话，独立于请求生命周期
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from fastapi import HTTPException

from ..config import get_settings
from ..models.folder import Folder
from ..models.learning_goal import LearningGoal, DailyPlan, GoalType, GoalStatus
from ..models.knowledge_card import KnowledgeCard
from ..models.note import Note, NoteStatus
from ..models.quiz_item import QuizItem
from ..models.review_log import ReviewLog

logger = logging.getLogger(__name__)

# 每日计划任务总数上限（F-12：改名为 DAILY_PLAN_LIMIT 与复习答题限额 DAILY_REVIEW_LIMIT=10 区分语义）
DAILY_PLAN_LIMIT = 50

# 薄弱点掌握度阈值（低于此值视为薄弱点）
WEAK_POINT_MASTERY_THRESHOLD = 60.0

# 每日计划各类任务数量上限
WEAK_POINT_TASK_LIMIT = 10
REVIEW_TASK_LIMIT = 20
NEW_MATERIAL_TASK_LIMIT = 5

# 每用户活跃目标上限
MAX_ACTIVE_GOALS = 5


async def _validate_goal_scopes(
    db: AsyncSession, user_id: str, scope_notes: List[str], scope_folders: List[str]
) -> None:
    """校验目标范围内的笔记/文件夹均属于当前用户（F-09 修复：防止 IDOR）"""
    note_ids = list(dict.fromkeys(scope_notes or []))
    folder_ids = list(dict.fromkeys(scope_folders or []))

    if note_ids:
        count_result = await db.execute(
            select(func.count(Note.id)).where(
                Note.user_id == user_id,
                Note.id.in_(note_ids),
            )
        )
        if (count_result.scalar() or 0) != len(note_ids):
            raise HTTPException(status_code=400, detail="目标范围包含不存在或无权访问的笔记")

    if folder_ids:
        count_result = await db.execute(
            select(func.count(Folder.id)).where(
                Folder.user_id == user_id,
                Folder.id.in_(folder_ids),
            )
        )
        if (count_result.scalar() or 0) != len(folder_ids):
            raise HTTPException(status_code=400, detail="目标范围包含不存在或无权访问的文件夹")


class GoalService:
    """
    学习目标服务

    使用方式：
        service = GoalService()
        goal = await service.create_goal(user_id, data, db)
        plan = await service.generate_daily_plan(user_id, db)
    """

    # ------------------------------------------------------------------
    # 1. 创建目标
    # ------------------------------------------------------------------
    async def create_goal(
        self,
        user_id: str,
        data: Any,
        db: AsyncSession,
    ) -> LearningGoal:
        """
        创建学习目标

        每用户最多 5 个活跃目标，超过则拒绝创建。

        Args:
            user_id: 用户 ID
            data: 创建请求数据（GoalCreateRequest 或等价对象），需含
                  name / type / scope_notes / scope_folders / target_mastery / deadline
            db: 异步数据库会话

        Returns:
            LearningGoal: 新创建的目标

        Raises:
            HTTPException(400): 活跃目标数已达上限
        """
        # 统计当前活跃目标数
        count_result = await db.execute(
            select(func.count()).select_from(LearningGoal).where(
                LearningGoal.user_id == user_id,
                LearningGoal.status == GoalStatus.ACTIVE.value,
            )
        )
        active_count = count_result.scalar() or 0
        if active_count >= MAX_ACTIVE_GOALS:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum {MAX_ACTIVE_GOALS} active goals",
            )

        # 兼容 pydantic 模型与普通对象
        goal_type = getattr(data, "type", None)
        goal_type_value = goal_type.value if hasattr(goal_type, "value") else goal_type
        if goal_type_value is None:
            goal_type_value = GoalType.WEEKLY.value

        # F-09 修复：校验 scope_notes / scope_folders 归属，防止引用他人笔记/文件夹（IDOR）
        await _validate_goal_scopes(
            db,
            user_id,
            list(getattr(data, "scope_notes", []) or []),
            list(getattr(data, "scope_folders", []) or []),
        )

        goal = LearningGoal(
            user_id=user_id,
            name=data.name,
            type=goal_type_value,
            scope_notes=list(getattr(data, "scope_notes", []) or []),
            scope_folders=list(getattr(data, "scope_folders", []) or []),
            target_mastery=float(getattr(data, "target_mastery", 80.0)),
            deadline=getattr(data, "deadline", None),
            status=GoalStatus.ACTIVE.value,
            progress_cache=0.0,
        )
        db.add(goal)
        await db.commit()
        await db.refresh(goal)

        logger.info(
            "学习目标创建成功: user_id=%s, goal_id=%s, name=%s",
            user_id, goal.id, goal.name,
        )
        return goal

    # ------------------------------------------------------------------
    # 2. 列表查询
    # ------------------------------------------------------------------
    async def list_goals(
        self,
        user_id: str,
        status_filter: Optional[str],
        db: AsyncSession,
    ) -> List[LearningGoal]:
        """
        查询用户的学习目标列表

        Args:
            user_id: 用户 ID
            status_filter: 状态过滤；为 None 时排除 DELETED
            db: 异步数据库会话

        Returns:
            List[LearningGoal]: 目标列表，按 created_at 降序
        """
        stmt = select(LearningGoal).where(LearningGoal.user_id == user_id)
        if status_filter is not None:
            stmt = stmt.where(LearningGoal.status == status_filter)
        else:
            # 默认排除已删除
            stmt = stmt.where(LearningGoal.status != GoalStatus.DELETED.value)
        stmt = stmt.order_by(LearningGoal.created_at.desc())

        result = await db.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # 3. 获取单个目标
    # ------------------------------------------------------------------
    async def get_goal(
        self,
        goal_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> LearningGoal:
        """
        获取指定目标（带权限校验）

        Args:
            goal_id: 目标 ID
            user_id: 用户 ID
            db: 异步数据库会话

        Returns:
            LearningGoal: 目标对象

        Raises:
            HTTPException(404): 目标不存在或不属于该用户
        """
        result = await db.execute(
            select(LearningGoal).where(
                LearningGoal.id == goal_id,
                LearningGoal.user_id == user_id,
            )
        )
        goal = result.scalars().first()
        if not goal:
            raise HTTPException(status_code=404, detail="Goal not found")
        return goal

    # ------------------------------------------------------------------
    # 4. 获取目标进度
    # ------------------------------------------------------------------
    async def get_goal_progress(
        self,
        goal_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """
        计算目标的实时进度

        进度计算：
        - 范围内知识卡片的平均掌握度（mastery_level，0-100）
        - 今日已复习题目数 / 范围内总题目数
        - progress_percentage = min(100, avg_mastery / target_mastery * 100)
        - days_remaining = (deadline - now).days，无截止时间则为 None

        Args:
            goal_id: 目标 ID
            user_id: 用户 ID
            db: 异步数据库会话

        Returns:
            Dict: goal_id / progress_percentage / avg_mastery /
                  reviewed_count / total_count / days_remaining
        """
        goal = await self.get_goal(goal_id, user_id, db)

        now = datetime.now(timezone.utc)
        # F-32 修复：日界按 Asia/Shanghai（北京时间零点），而非 UTC 零点
        from ..utils.timeutil import today_start_utc
        today_start = today_start_utc(now)

        scope_notes = list(goal.scope_notes or [])

        # 范围内卡片平均掌握度（F-09 修复：叠加 user_id 过滤，防止旧数据跨用户渗漏；
        # 回收站笔记的卡片不计入统计）
        avg_mastery = 0.0
        if scope_notes:
            avg_result = await db.execute(
                select(func.avg(KnowledgeCard.mastery_level)).where(
                    KnowledgeCard.note_id.in_(scope_notes),
                    KnowledgeCard.user_id == user_id,
                    Note.not_trashed(KnowledgeCard.note_id),
                )
            )
            avg_value = avg_result.scalar()
            avg_mastery = float(avg_value) if avg_value is not None else 0.0
        else:
            avg_result = await db.execute(
                select(func.avg(KnowledgeCard.mastery_level)).where(
                    KnowledgeCard.user_id == user_id,
                    Note.not_trashed(KnowledgeCard.note_id),
                )
            )
            avg_value = avg_result.scalar()
            avg_mastery = float(avg_value) if avg_value is not None else 0.0

        # 范围内总题目数（F-09 修复：叠加 user_id 过滤；回收站笔记的题目不计入）
        total_count = 0
        if scope_notes:
            total_result = await db.execute(
                select(func.count()).select_from(QuizItem).where(
                    QuizItem.note_id.in_(scope_notes),
                    QuizItem.user_id == user_id,
                    Note.not_trashed(QuizItem.note_id),
                )
            )
            total_count = total_result.scalar() or 0

        # 今日已复习题目数（范围内）（F-09 修复：叠加 user_id 过滤；回收站笔记不计入）
        reviewed_count = 0
        if scope_notes:
            reviewed_result = await db.execute(
                select(func.count()).select_from(ReviewLog).where(
                    ReviewLog.note_id.in_(scope_notes),
                    ReviewLog.user_id == user_id,
                    ReviewLog.review_at >= today_start,
                    Note.not_trashed(ReviewLog.note_id),
                )
            )
            reviewed_count = reviewed_result.scalar() or 0

        # 剩余天数
        days_remaining: Optional[int] = None
        if goal.deadline is not None:
            # 统一时区处理：若 deadline 无时区信息，按 UTC 处理
            deadline = goal.deadline
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            days_remaining = (deadline - now).days

        # 进度百分比
        target = float(goal.target_mastery) if goal.target_mastery else 80.0
        if target > 0:
            progress_percentage = min(100.0, avg_mastery / target * 100.0)
        else:
            progress_percentage = 0.0

        return {
            "goal_id": goal.id,
            "progress_percentage": round(progress_percentage, 2),
            "avg_mastery": round(avg_mastery, 2),
            "reviewed_count": reviewed_count,
            "total_count": total_count,
            "days_remaining": days_remaining,
        }

    # ------------------------------------------------------------------
    # 5. 生成每日计划
    # ------------------------------------------------------------------
    async def generate_daily_plan(
        self,
        user_id: str,
        db: AsyncSession,
    ) -> DailyPlan:
        """
        生成每日任务推荐计划

        流程：
        1. 若今日已有计划，直接返回
        2. 收集所有活跃目标的 scope_notes
        3. 三类任务：
           - 薄弱点：mastery_level < 60 的卡片对应的 quiz_items（top 10）
           - 到期复习：next_review_at <= now 或为 None 的 quiz_items（top 20）
           - 新资料：status in (converted, cleaned) 的笔记（top 5）
        4. 按 priority 排序：weak_point (3) > review (2) > new_material (1)
        5. 总数上限 DAILY_PLAN_LIMIT=50

        Args:
            user_id: 用户 ID
            db: 异步数据库会话

        Returns:
            DailyPlan: 每日计划

        Raises:
            HTTPException(400): 用户无活跃目标
        """
        now = datetime.now(timezone.utc)
        # F-32 修复：日界按 Asia/Shanghai（北京时间零点），而非 UTC 零点
        from ..utils.timeutil import today_start_utc
        today_start = today_start_utc(now)

        # 1. 检查今日是否已有计划
        existing_result = await db.execute(
            select(DailyPlan).where(
                DailyPlan.user_id == user_id,
                DailyPlan.plan_date >= today_start,
            ).order_by(DailyPlan.plan_date.desc()).limit(1)
        )
        existing_plan = existing_result.scalars().first()
        if existing_plan is not None:
            return existing_plan

        # 2. 获取活跃目标，收集 scope_notes
        goals_result = await db.execute(
            select(LearningGoal).where(
                LearningGoal.user_id == user_id,
                LearningGoal.status == GoalStatus.ACTIVE.value,
            ).order_by(LearningGoal.created_at.desc())
        )
        active_goals = list(goals_result.scalars().all())
        if not active_goals:
            raise HTTPException(
                status_code=400,
                detail="No active goals, please create a goal first",
            )

        scope_notes: List[str] = []
        for goal in active_goals:
            for note_id in (goal.scope_notes or []):
                if note_id not in scope_notes:
                    scope_notes.append(note_id)

        recommended_tasks: Dict[str, Any] = {
            "review": [],
            "new_materials": [],
            "weak_points": [],
        }

        if scope_notes:
            # a. 薄弱点：mastery_level < 60 的卡片 → 关联 quiz_items（top 10）
            #    （F-09 修复：叠加 user_id 过滤；回收站笔记不进计划）
            weak_cards_result = await db.execute(
                select(KnowledgeCard.id, KnowledgeCard.title).where(
                    KnowledgeCard.note_id.in_(scope_notes),
                    KnowledgeCard.user_id == user_id,
                    KnowledgeCard.mastery_level < WEAK_POINT_MASTERY_THRESHOLD,
                    Note.not_trashed(KnowledgeCard.note_id),
                ).limit(WEAK_POINT_TASK_LIMIT)
            )
            weak_card_rows = weak_cards_result.all()
            weak_card_ids = [row[0] for row in weak_card_rows]
            card_title_map = {row[0]: row[1] for row in weak_card_rows}

            if weak_card_ids:
                weak_quizzes_result = await db.execute(
                    select(QuizItem).where(
                        QuizItem.card_id.in_(weak_card_ids),
                        QuizItem.user_id == user_id,
                    ).limit(WEAK_POINT_TASK_LIMIT)
                )
                for quiz in weak_quizzes_result.scalars().all():
                    title = card_title_map.get(quiz.card_id) or "薄弱点练习"
                    recommended_tasks["weak_points"].append({
                        "task_type": "weak_point",
                        "quiz_id": quiz.id,
                        "note_id": quiz.note_id,
                        "card_id": quiz.card_id,
                        "priority": 3,
                        "title": f"薄弱点: {title}",
                    })

            # b. 到期复习：next_review_at <= now 或为 None（top 20）
            #    （F-09 修复：叠加 user_id 过滤；回收站笔记不进计划）
            due_quizzes_result = await db.execute(
                select(QuizItem).where(
                    QuizItem.note_id.in_(scope_notes),
                    QuizItem.user_id == user_id,
                    (QuizItem.next_review_at <= now) | (QuizItem.next_review_at.is_(None)),
                    Note.not_trashed(QuizItem.note_id),
                ).order_by(
                    QuizItem.next_review_at.asc().nullsfirst(),
                ).limit(REVIEW_TASK_LIMIT)
            )
            for quiz in due_quizzes_result.scalars().all():
                question_text = (quiz.question or "")[:200]
                recommended_tasks["review"].append({
                    "task_type": "review",
                    "quiz_id": quiz.id,
                    "note_id": quiz.note_id,
                    "card_id": quiz.card_id,
                    "priority": 2,
                    "title": f"复习: {question_text}" if question_text else "复习题目",
                })

            # c. 新资料：status in (converted, cleaned)（top 5）
            #    （F-09 修复：叠加 user_id 过滤；回收站笔记不进计划）
            new_notes_result = await db.execute(
                select(Note).where(
                    Note.id.in_(scope_notes),
                    Note.user_id == user_id,
                    Note.status.in_([NoteStatus.converted, NoteStatus.cleaned]),
                    Note.trashed_at.is_(None),
                ).order_by(Note.created_at.desc()).limit(NEW_MATERIAL_TASK_LIMIT)
            )
            for note in new_notes_result.scalars().all():
                recommended_tasks["new_materials"].append({
                    "task_type": "new_material",
                    "quiz_id": None,
                    "note_id": note.id,
                    "card_id": None,
                    "priority": 1,
                    "title": f"学习新资料: {note.title}",
                })

        # 计算总数（上限 DAILY_PLAN_LIMIT）
        total_count = (
            len(recommended_tasks["weak_points"])
            + len(recommended_tasks["review"])
            + len(recommended_tasks["new_materials"])
        )
        if total_count > DAILY_PLAN_LIMIT:
            # 按优先级裁剪：保留 weak_points → review → new_materials
            self._trim_tasks(recommended_tasks, DAILY_PLAN_LIMIT)
            total_count = DAILY_PLAN_LIMIT

        # 关联到第一个活跃目标（计划聚合所有目标，但 FK 需要一个 goal_id）
        plan = DailyPlan(
            goal_id=active_goals[0].id,
            user_id=user_id,
            plan_date=now,
            recommended_tasks=recommended_tasks,
            completed_count=0,
            total_count=total_count,
        )
        db.add(plan)
        await db.commit()
        await db.refresh(plan)

        logger.info(
            "每日计划生成: user_id=%s, plan_id=%s, total=%d "
            "(weak=%d, review=%d, new=%d)",
            user_id, plan.id, total_count,
            len(recommended_tasks["weak_points"]),
            len(recommended_tasks["review"]),
            len(recommended_tasks["new_materials"]),
        )
        return plan

    @staticmethod
    def _trim_tasks(tasks: Dict[str, Any], limit: int) -> None:
        """按优先级（weak_points → review → new_materials）裁剪任务到 limit 条"""
        order = ["weak_points", "review", "new_materials"]
        kept = 0
        for key in order:
            if kept >= limit:
                tasks[key] = []
                continue
            remaining = limit - kept
            if len(tasks[key]) > remaining:
                tasks[key] = tasks[key][:remaining]
            kept += len(tasks[key])

    # ------------------------------------------------------------------
    # 6. 刷新所有活跃目标进度（Celery Beat 调用）
    # ------------------------------------------------------------------
    async def refresh_goal_progress(self) -> None:
        """
        定时刷新所有活跃目标的进度缓存

        由 Celery Beat 调用，自建异步会话独立于请求生命周期。
        - 更新 progress_cache 与 last_progress_refresh
        - 若 progress >= target_mastery：status 置为 COMPLETED
        - 若 deadline < now：status 置为 EXPIRED
        """
        settings = get_settings()
        engine = create_async_engine(settings.get_database_url(), echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        now = datetime.now(timezone.utc)
        completed_count = 0
        expired_count = 0
        updated_count = 0

        try:
            async with session_factory() as session:
                result = await session.execute(
                    select(LearningGoal).where(
                        LearningGoal.status == GoalStatus.ACTIVE.value,
                    )
                )
                active_goals = list(result.scalars().all())

                for goal in active_goals:
                    scope_notes = list(goal.scope_notes or [])

                    # 计算平均掌握度（F-09 修复：叠加 user_id 过滤；回收站笔记不计入）
                    avg_mastery = 0.0
                    if scope_notes:
                        avg_result = await session.execute(
                            select(func.avg(KnowledgeCard.mastery_level)).where(
                                KnowledgeCard.note_id.in_(scope_notes),
                                KnowledgeCard.user_id == goal.user_id,
                                Note.not_trashed(KnowledgeCard.note_id),
                            )
                        )
                        avg_value = avg_result.scalar()
                        avg_mastery = float(avg_value) if avg_value is not None else 0.0

                    target = float(goal.target_mastery) if goal.target_mastery else 80.0
                    if target > 0:
                        progress = min(100.0, avg_mastery / target * 100.0)
                    else:
                        progress = 0.0

                    goal.progress_cache = round(progress, 2)
                    goal.last_progress_refresh = now

                    # 状态流转
                    if progress >= target and target > 0:
                        goal.status = GoalStatus.COMPLETED.value
                        completed_count += 1
                    elif goal.deadline is not None:
                        deadline = goal.deadline
                        if deadline.tzinfo is None:
                            deadline = deadline.replace(tzinfo=timezone.utc)
                        if deadline < now:
                            goal.status = GoalStatus.EXPIRED.value
                            expired_count += 1

                    updated_count += 1

                await session.commit()

            logger.info(
                "目标进度刷新完成: total=%d, completed=%d, expired=%d",
                updated_count, completed_count, expired_count,
            )
        except Exception as e:
            logger.error(f"刷新目标进度失败: {e}")
            raise
        finally:
            await engine.dispose()

    # ------------------------------------------------------------------
    # 7. 归档目标
    # ------------------------------------------------------------------
    async def archive_goal(
        self,
        goal_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> LearningGoal:
        """
        归档目标（status 置为 ARCHIVED）

        Args:
            goal_id: 目标 ID
            user_id: 用户 ID
            db: 异步数据库会话

        Returns:
            LearningGoal: 更新后的目标
        """
        goal = await self.get_goal(goal_id, user_id, db)
        goal.status = GoalStatus.ARCHIVED.value
        await db.commit()
        await db.refresh(goal)

        logger.info("目标归档: user_id=%s, goal_id=%s", user_id, goal_id)
        return goal

    # ------------------------------------------------------------------
    # 8. 删除目标（软删除）
    # ------------------------------------------------------------------
    async def delete_goal(
        self,
        goal_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> LearningGoal:
        """
        软删除目标（status 置为 DELETED）

        Args:
            goal_id: 目标 ID
            user_id: 用户 ID
            db: 异步数据库会话

        Returns:
            LearningGoal: 更新后的目标
        """
        goal = await self.get_goal(goal_id, user_id, db)
        goal.status = GoalStatus.DELETED.value
        await db.commit()
        await db.refresh(goal)

        logger.info("目标软删除: user_id=%s, goal_id=%s", user_id, goal_id)
        return goal


# 模块级单例，便于直接导入使用
goal_service = GoalService()
