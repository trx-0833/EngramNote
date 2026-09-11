"""
复习调度服务模块

本模块提供复习调度核心业务逻辑，包括到期题目查询、答案提交判分、
SM-2 参数更新和复习统计等功能。

主要职责：
- 查询今日到期复习题目
- 处理用户答案提交（判分 + SM-2 更新 + 记录日志）
- 计算复习统计数据

设计决策：
- 到期题目按 next_review_at 升序排列（最过期的优先）
- 新题目（next_review_at 为 None）视为立即可复习
- 答题后即时更新 SM-2 参数，无需异步任务
- 复习统计按 UTC 日期计算
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func, case, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models.note import Note
from ..models.quiz_item import QuizItem
from ..models.review_log import ReviewLog
from ..services import scheduler_service
from ..services.sm2_service import (
    grade_answer,
    grade_short_answer_semantically,
)
from . import review_state_service
from .review_state_service import ITEM_TYPE_QUIZ

logger = logging.getLogger(__name__)
settings = get_settings()


DAILY_REVIEW_LIMIT = settings.daily_review_limit  # 每日最大答题数（单一来源 config，见 docs/decisions.md#F-12）


async def _count_settled_today(db: AsyncSession, user_id: str, today_start) -> int:
    """今日**已结算**的题目数（即额度已消耗数）

    为什么必须排除 `grading_method='ungraded'`：占位记录只记了一条"自动判分
    不可信、等待用户自评"的中间态，它**不推进 SM-2 调度**，答题尚未完成。
    把它计入"今日已完成 N 道题"，既虚报进度（用户看到做了 10 道其实只结算了 9 道），
    又会让额度提前耗尽。

    这个口径原先散落在 get_due_quizzes / get_review_stats / submit_answer 三处
    各自手写 SQL，已经漂移过一次（提交路径排除了 ungraded，另两处没有），
    故收敛为本函数 —— 限额与展示必须用同一个定义。
    """
    result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
            ReviewLog.review_at >= today_start,
            ReviewLog.grading_method != "ungraded",
        )
    )
    return result.scalar() or 0


async def get_due_quizzes(
    user_id: str,
    db: AsyncSession,
    limit: int = 50,
    daily_max: int = DAILY_REVIEW_LIMIT,
) -> List[QuizItem]:
    """
    获取用户今日到期的复习题目

    查询条件：
    - next_review_at <= 当前时间（已到期）
    - next_review_at 为 None（新题目，尚未设置复习时间）
    - 每日最多返回 daily_max 道题（按今日已答题数扣减）

    排序策略：
    1. 最过期的优先（next_review_at 升序，nullsfirst）
    2. 薄弱点优先：关联卡片的错误次数越多，排序越靠前

    Args:
        user_id: 用户 ID
        db: 数据库会话
        limit: 最大返回数量
        daily_max: 每日最大答题数

    Returns:
        List[QuizItem]: 到期题目列表
    """
    now = datetime.now(timezone.utc)
    # 日界按 Asia/Shanghai（北京时间零点），而非 UTC 零点（见 docs/decisions.md#F-32）
    from ..utils.timeutil import today_start_utc
    today_start = today_start_utc(now)

    # 计算今日已结算题数（口径见 _count_settled_today）
    today_done = await _count_settled_today(db, user_id, today_start)

    # 今日已达到上限，返回空列表
    remaining = daily_max - today_done
    if remaining <= 0:
        return []

    # 实际返回数量 = min(剩余配额, limit)
    actual_limit = min(remaining, limit)

    # 子查询：统计每张卡片的错误次数，用于薄弱点优先排序
    # （与主查询一致地排除回收站笔记的题目，保证排序口径一致）
    error_subq = (
        select(
            QuizItem.card_id,
            func.coalesce(func.sum(case((ReviewLog.is_correct.is_(False), 1), else_=0)), 0).label("error_count"),
        )
        .join(ReviewLog, ReviewLog.quiz_id == QuizItem.id, isouter=True)
        .where(
            QuizItem.user_id == user_id,
            or_(
                QuizItem.note_id.is_(None),
                select(Note.id).where(
                    Note.id == QuizItem.note_id, Note.trashed_at.is_(None)
                ).exists(),
            ),
        )
        .group_by(QuizItem.card_id)
        .subquery()
    )

    # 查询到期题目，关联薄弱点排序（回收站笔记的题目暂不可见）
    result = await db.execute(
        select(QuizItem).where(
            QuizItem.user_id == user_id,
            (QuizItem.next_review_at <= now) | (QuizItem.next_review_at.is_(None)),
            or_(
                QuizItem.note_id.is_(None),
                select(Note.id).where(
                    Note.id == QuizItem.note_id, Note.trashed_at.is_(None)
                ).exists(),
            ),
        )
        .outerjoin(error_subq, error_subq.c.card_id == QuizItem.card_id)
        .order_by(
            QuizItem.next_review_at.asc().nullsfirst(),
            func.coalesce(error_subq.c.error_count, 0).desc(),
        )
        .limit(actual_limit)
    )
    return list(result.scalars().all())


async def submit_answer(
    quiz_id: str,
    user_id: str,
    user_answer: str,
    time_spent_ms: int,
    db: AsyncSession,
    skip_daily_limit: bool = False,
    skip_due_check: bool = False,
    self_rating: Optional[int] = None,
    use_semantic_grading: bool = False,
) -> Dict[str, Any]:
    """
    提交答案并更新 SM-2 调度参数

    完整流程：
    1. 查询题目，校验权限
    2. 校验题目是否到期（普通复习；快速复习可跳过）
    3. 同日重复提交幂等判定（区分「占位提交」与「已判分提交」，见下）
    4. 判断正误，计算 SM-2 评分（用户自评优先）
    5. 调用 SM-2 算法更新调度参数
    6. 更新 QuizItem 的 SM-2 字段
    7. 创建 ReviewLog 记录
    8. 返回判分结果

    两阶段提交（简答题/自评场景）：
    第一次提交不带 self_rating，自动判分不可信 → 落一条 grading_method='ungraded'
    的占位记录、**不推进调度**；第二次提交带 self_rating，补完该记录并推进调度。
    幂等守卫放行第二次提交，也放行「无任何今日记录时直接带自评提交」的合法路径。

    Args:
        quiz_id: 题目 ID
        user_id: 用户 ID
        user_answer: 用户答案
        time_spent_ms: 答题耗时（毫秒）
        db: 数据库会话
        skip_daily_limit: 是否跳过每日答题限额检查（快速复习场景使用）
        skip_due_check: 是否跳过到期校验（快速复习保留免校验，
                        普通复习必须到期才能提交，见 docs/decisions.md#F-14）
        self_rating: 用户自评的 SM-2 质量分（0-5）。给出时优先于自动判分；
                    简答题等 needs_self_assessment 的场景应始终传入。
                    不传且自动判分不可信时，本次提交**不推进调度**（保留复习进度）。

    Returns:
        Dict: 判分结果，包含 is_correct, quality, correct_answer, explanation,
              next_review_at，以及 self_rating / grading_method /
              needs_self_assessment / completing_placeholder / grading_reason
              （供 UI 决定是否请用户自评）
    """
    # 1. 查询题目
    result = await db.execute(
        select(QuizItem).where(
            QuizItem.id == quiz_id,
            QuizItem.user_id == user_id,
        )
    )
    quiz = result.scalars().first()
    if not quiz:
        return {"error": "题目不存在"}

    # 题目类型在限额校验之前就要用到（决定本次提交是否会产生占位记录）
    question_type = quiz.question_type.value

    # 1.5 同日同题幂等——今日已提交过则不重复创建 ReviewLog、不重复叠加 SM-2
    #     （防双击/连点/API 重放）。但这里必须区分「占位提交」与「已判分提交」：
    #
    #     简答题的自动判分是占位值、不推进调度（见下方第 3 步），真正推进调度的
    #     是用户随后带 self_rating 的第二次提交。若把第二次也当成重复提交挡掉，
    #     自评就永远写不进库、调度永远不推进——这正是本次改造要修的核心缺陷。
    #
    #     因此：今日若存在「自动判分不可信且尚未自评」的占位记录，本次提交
    #     （无论带不带 self_rating）都应当继续执行，由它来完成判分与调度。
    #     幂等检查须在**限额与到期校验之前**：同日已提交后 SM-2 已把
    #     next_review_at 推到未来，若先查到期会误报"未到期"而非命中幂等；
    #     若先查限额，用户答满今日额度后连最后一道题的自评都提交不了
    #     （见 docs/decisions.md#F-14）。
    now = datetime.now(timezone.utc)
    from ..utils.timeutil import today_start_utc
    today_start = today_start_utc(now)
    existing_logs_result = await db.execute(
        select(ReviewLog).where(
            ReviewLog.quiz_id == quiz_id,
            ReviewLog.user_id == user_id,
            ReviewLog.review_at >= today_start,
        ).order_by(ReviewLog.review_at.desc())
    )
    existing_logs = list(existing_logs_result.scalars().all())

    # 占位记录：自动判分不可信，既未自评也未推进调度，是"未完成"的一次提交
    pending_placeholder = next(
        (log for log in existing_logs if log.grading_method == "ungraded"), None
    )
    # 已判分记录：真正决定了当前调度参数的那一条
    scored_log = next(
        (log for log in existing_logs if log.grading_method != "ungraded"), None
    )

    if self_rating is not None:
        # 带自评提交：只能"补完"一条占位记录，不能被占位记录挡回。
        # 若今日已有一条已判分记录：
        #   - 它已经就是本次同样的自评 → 真重复提交，返回原结果
        #   - 否则视为用户改判，允许重新判分（改判是用户的正当权利）
        #
        # 注意这里刻意**没有** else 分支：今日无任何记录却带自评提交
        # （例如客户端先答题后补评、但占位提交失败）是合法路径，
        # 应当照常落一条 self_rating 记录，而不是被幂等逻辑挡掉。
        if scored_log is not None and scored_log.self_rating == self_rating:
            logger.info(
                f"答题提交幂等命中: user={user_id[:8]}, quiz={quiz_id[:8]}, "
                f"同日同自评已提交，返回历史结果"
            )
            return _build_submit_result(quiz, scored_log, existing_logs=existing_logs)
    elif existing_logs:
        # 不带自评的重复提交（双击/重放）：返回今日最新结果，不重复推进调度
        logger.info(
            f"答题提交幂等命中: user={user_id[:8]}, quiz={quiz_id[:8]}, "
            f"同日已提交，返回历史结果"
        )
        return _build_submit_result(quiz, existing_logs[0], existing_logs=existing_logs)

    # 1.55 普通复习提交校验题目是否到期（未到期拒绝；快速复习跳过），见 docs/decisions.md#F-14
    #
    # 到期校验放在限额校验之前：两者的错误语义不同（404「未到期」vs 429「超限额」），
    # 而"未到期"是更具体、更可操作的原因。若反过来，用户对一道已排到明天的题
    # 重复提交时会看到"今日已达上限"这种与事实无关的提示。
    if not skip_due_check and quiz.next_review_at is not None and quiz.next_review_at > now:
        return {"error": "题目尚未到期，请按复习计划进行"}

    # 1.6 每日答题限额（快速复习场景跳过此检查）
    #
    # 规则只有一条：**补完已存在的占位记录时不检查限额**，其余提交一律检查。
    #
    # 为什么不按题目类型豁免：曾经写成"简答题豁免"，负向测试立刻证明那是错的
    # ——简答题因此完全绕过每日限额，用户能无限作答，限额形同虚设。
    # 正确的判据是"这次要不要结算一道题"，而不是"这道题是什么类型"。
    #
    # 为什么补完占位必须豁免：占位记录有 1/DAILY_REVIEW_LIMIT 的概率正好落在
    # 当天第 N 道题上。若补完也检查限额，那一刻额度恰好用尽，用户会卡在
    # "答了但结不了账"，该题永远停在未推进状态。豁免的代价只是当天多结算一道，
    # 而卡死的代价是永久性的，两害相权取豁免。
    #
    # 占位记录本身也不计入 today_done：它未推进调度，算作"已完成"是虚报。
    if pending_placeholder is None and not skip_daily_limit:
        today_done = await _count_settled_today(db, user_id, today_start)
        if today_done >= DAILY_REVIEW_LIMIT:
            return {"error": f"今日已完成 {today_done} 道题，已达每日上限 {DAILY_REVIEW_LIMIT}"}

    if pending_placeholder is not None:
        logger.info(
            f"补完待自评的占位记录: user={user_id[:8]}, quiz={quiz_id[:8]}, "
            f"self_rating={self_rating}"
        )

    # 2. 判断正误，计算 SM-2 评分
    correct_answer = quiz.answer

    # 用户自评优先：记忆是主观现象，"你觉得自己想起来没有"比任何自动判分都准，
    # 且是唯一能覆盖简答题的信号（见 sm2_service.grade_answer 与 §2.4 L-1）。
    grade = grade_answer(question_type, user_answer, correct_answer)

    # 2.1 简答题：**仅在被显式请求时**才试 LLM 语义判分（阶段 3.5）
    #
    # 为什么默认关闭而不是默认尝试：
    # 判分调用外部 LLM，而这是**用户提交答案的同步路径** ——
    # 全局 `llm_timeout_seconds=600` / `llm_max_retries=5`（每次 1s 退避），
    # 实测即使网络立刻失败也要 ~10 秒（5 次退避），生产里若网关慢则更久。
    # 而两阶段流程本来就以用户自评为主评分来源，自动判分是增强而非前提，
    # 为它让每次提交都等一次往返是明显的得不偿失。
    #
    # 实测证据：无条件尝试时，测试套件从 70 秒涨到 183 秒 ——
    # `test_self_rating.py` 每个用例 10~14 秒，正是这个延迟。
    #
    # 触发条件同时限定为「简答题 + 未自评」：自评存在时它优先级最高，
    # 此时调用 LLM 既浪费额度又可能与用户判断冲突。
    semantic_detail = None
    if (
        use_semantic_grading
        and grade.get("needs_self_assessment")
        and self_rating is None
        and question_type == "short_answer"
    ):
        semantic = await grade_short_answer_semantically(
            question=quiz.question or "",
            expected_answer=correct_answer or "",
            user_answer=user_answer or "",
        )
        if semantic:
            grade = semantic
            semantic_detail = semantic.get("detail")
            logger.info(
                "简答语义判分: user=%s quiz=%s verdict=%s conf=%.2f quality=%d",
                user_id[:8], quiz_id[:8],
                (semantic_detail or {}).get("verdict"),
                (semantic_detail or {}).get("confidence", 0),
                semantic["quality"],
            )

    if self_rating is not None:
        quality = max(0, min(5, int(self_rating)))
        grade = {
            "quality": quality,
            "method": "self_rating",
            "needs_self_assessment": False,
            "reason": "用户自评",
        }
    else:
        quality = grade["quality"]
    is_correct = quality >= 3
    # 占位提交（本次仍无自评且自动判分不可信）记 'ungraded'，
    # 它是「未完成」的证据；补完占位的那次提交才记真实判分方式。
    if grade["needs_self_assessment"]:
        grading_method = "ungraded"
    elif grade["method"] == "self_rating":
        grading_method = "self_rating"
    else:
        grading_method = grade["method"]
    # 本次提交完成的是哪一条记录：补完占位（completing）还是新建（fresh）。
    completing_placeholder = pending_placeholder is not None

    # 3. 跑一次调度（阶段 3.6：默认 FSRS-5，`config.review_scheduler` 可回退 SM-2）
    #
    # 占位分不参与调度：简答题的自动判分是占位值（quality=1），
    # 若照此推进调度，会把"未自评"错误地变成"答错并重置间隔"，
    # 销毁已有的复习进度。此时保持调度参数不变，等用户给出自评再更新。
    #
    # 调度必须**先读后写**：`advance` 需要 review_states 上的 S/D 与
    # last_reviewed_at 才能算出"复习前预测的可回忆概率"，而这两个值
    # 会在 `apply_schedule_result` 里被覆盖。顺序颠倒会让预测值变成
    # 用复习后的状态算出来的事后数字，校准曲线随即失效。
    schedule_outcome = None
    if grade["needs_self_assessment"]:
        logger.info(
            f"答题待自评，暂不推进调度: user={user_id[:8]}, quiz={quiz_id[:8]}, type={question_type}"
        )
    else:
        state = await review_state_service.get_state(
            db, user_id, ITEM_TYPE_QUIZ, quiz_id,
        )
        if state is None:
            # 题目明明刚查出来存在，却拿不到复习状态 —— 这是数据不一致。
            # 不能"当作没调度"继续写一条 ReviewLog：那会让用户看到
            # "已复习、已判分"，而调度其实没动，且没有任何痕迹表明这一点。
            # 按原则 P7（失败必须响亮）在这里失败。
            raise RuntimeError(
                f"无法读取复习状态，拒绝静默不推进调度: quiz={quiz_id}"
            )
        schedule_outcome = scheduler_service.advance(
            state, quality, method=grade["method"], now=now,
        )

        # 4. 更新 QuizItem 的旧调度字段（阶段 3.1 渐进迁移的双写）
        #
        # ⚠️ 这些字段**仍在被读取**：到期队列（`_get_due_quizzes`）、
        # 今日待复习数、复习提醒、目标建议、掌握度都还在查
        # `quiz_items.next_review_at`。所以它们必须是权威调度的
        # **镜像**，而不是"SM-2 会怎么说"的平行推演 ——
        # 两份不同的排期比一份更能骗人。
        quiz.interval = schedule_outcome.interval_days
        quiz.repetition = schedule_outcome.repetition
        quiz.easiness_factor = schedule_outcome.easiness_factor
        quiz.next_review_at = schedule_outcome.next_review_at
        quiz.last_reviewed_at = now
        quiz.review_count += 1

    # 5. 创建 ReviewLog 记录
    review_log = ReviewLog(
        user_id=user_id,
        quiz_id=quiz_id,
        # 冗余记录卡片归属：题目会被"重新理解"整批替换，届时 quiz_id
        # 指向的行消失，只有 card_id 能保住历史记录的归属（症状 D-3）
        card_id=quiz.card_id,
        note_id=quiz.note_id,
        user_answer=user_answer,
        is_correct=is_correct,
        quality=quality,
        self_rating=self_rating,
        grading_method=grading_method,
        # 阶段 3.5：结构化判分明细（verdict/缺失点/误解点/置信度）。
        # 只有语义判分成功时才有值；自评与选择题等场景保持 NULL —
        # 那时确实没有这份明细，用空对象冒充会让人误以为"判分过但没发现问题"。
        grading_detail=semantic_detail,
        # 阶段 3.6：FSRS 口径的评分档位与**复习前**的可回忆概率预测。
        # 占位提交（未推进调度）时两者都是 NULL —— 那一次确实没有调度发生，
        # 记一个"预测保持率"会让校准曲线的分母混进非预测值。
        rating=schedule_outcome.rating if schedule_outcome else None,
        predicted_retention=(
            schedule_outcome.predicted_retention if schedule_outcome else None
        ),
        item_type="quiz",
        time_spent_ms=time_spent_ms,
        review_at=now,
    )
    db.add(review_log)

    # 5.5 同步写入 ReviewState（阶段 3.1 双写）
    #
    # 为什么在 commit 之前写：两者必须落在**同一个事务**里。分开提交的话，
    # 中间崩溃会留下"QuizItem 说复习过了、ReviewState 说没有"的分叉，
    # 而这类分叉没有任何自愈机制。
    #
    # 只在真正推进调度时写（schedule_outcome 非 None）：占位提交不影响调度状态。
    if schedule_outcome is not None:
        try:
            await review_state_service.apply_schedule_result(
                db, user_id, ITEM_TYPE_QUIZ, quiz_id,
                outcome=schedule_outcome, quality=quality, now=now,
            )
        except Exception as state_err:
            # 双写失败不应让答题本身失败：题目维度的旧字段仍是权威来源，
            # 且惰性补建会在下次读取时补齐。
            logger.warning(
                f"写入 ReviewState 失败（旧字段已更新，不影响答题）: "
                f"quiz={quiz_id[:8]}, {state_err}"
            )

    await db.commit()
    await db.refresh(quiz)

    logger.info(
        f"答题提交: user={user_id[:8]}, quiz={quiz_id[:8]}, "
        f"correct={is_correct}, quality={quality}, method={grading_method}, "
        f"self_rating={self_rating}, "
        f"algo={schedule_outcome.algorithm if schedule_outcome else '-'}, "
        f"rating={schedule_outcome.rating if schedule_outcome else '-'}, "
        f"R={schedule_outcome.predicted_retention if schedule_outcome else '-'}, "
        f"S={schedule_outcome.stability if schedule_outcome else '-'}, "
        f"interval={schedule_outcome.interval_days if schedule_outcome else '未推进(待自评)'}"
    )

    # 答题后非阻塞刷新关联卡片掌握度（失败不影响答题响应）
    try:
        from .mastery_service import refresh_card_mastery
        await refresh_card_mastery(quiz.card_id, db)
    except Exception as mastery_err:
        logger.warning(
            f"刷新卡片掌握度失败 (card_id={quiz.card_id}): {mastery_err}"
        )

    # 一并把今日已有的记录传给结果构造器：占位记录是「已提交但未完成」的证据，
    # 前端需要它在刷新/重放后仍能正确显示"待自评"，而不是误判为已完成。
    result = _build_submit_result(
        quiz, review_log, existing_logs=existing_logs + [review_log],
        completing_placeholder=completing_placeholder,
    )
    # 透出判分可信度，供 UI 决定是否请用户自评（简答题必为 True）
    result["needs_self_assessment"] = grade["needs_self_assessment"]
    result["grading_method"] = grading_method
    result["grading_reason"] = grade["reason"]
    return result


def _build_submit_result(
    quiz,
    review_log,
    existing_logs: Optional[List[ReviewLog]] = None,
    completing_placeholder: bool = False,
) -> Dict[str, Any]:
    """构造提交结果字典（普通提交与幂等命中共用）

    Args:
        quiz: 题目对象
        review_log: 本次（或幂等命中时今日已存在的）复习记录
        existing_logs: 今日该题已有的全部复习记录（含 review_log 本身）。
            用于判断「今日这道题是否已经自评过」——幂等命中路径下，
            review_log 可能是一条占位记录，此时仍需请用户自评。
        completing_placeholder: 本次提交是否补完了一条占位记录

    注意：这里的 needs_self_assessment 只依据**已落库的事实**（有没有
    self_rating、是不是占位），不依据本次调用的入参，因此刷新页面后
    重新拉取结果也能得到一致答案。
    """
    # 解析选项（选择题）
    options = None
    if quiz.options:
        try:
            options = json.loads(quiz.options) if isinstance(quiz.options, str) else quiz.options
        except (json.JSONDecodeError, TypeError):
            options = None

    logs = list(existing_logs) if existing_logs else [review_log]
    has_self_rating = any(log.self_rating is not None for log in logs)
    has_unscored_placeholder = any(log.grading_method == "ungraded" for log in logs)
    # 今日已自评 → 不再请求；仍有未自评的占位记录 → 需要请求
    needs_self_assessment = (not has_self_rating) and has_unscored_placeholder

    return {
        "quiz_id": quiz.id,
        "is_correct": review_log.is_correct,
        "quality": review_log.quality,
        "correct_answer": quiz.answer,
        "explanation": quiz.explanation,
        "options": options,
        "question_type": quiz.question_type.value,
        "self_rating": review_log.self_rating,
        "grading_method": review_log.grading_method,
        "needs_self_assessment": needs_self_assessment,
        "completing_placeholder": completing_placeholder,
        "sm2": {
            "interval": quiz.interval,
            "repetition": quiz.repetition,
            "easiness_factor": quiz.easiness_factor,
            "next_review_at": quiz.next_review_at.isoformat() if quiz.next_review_at else None,
            # 阶段 3.6：解释"为什么给这个间隔"。
            # 取自 **review_log** 而不是本次调用的内存变量 —— 幂等命中
            # （重复提交）时根本没有内存变量，而用户刷新后重新拉取结果
            # 也必须看到同样的解释。
            "rating": review_log.rating,
            "predicted_retention": review_log.predicted_retention,
        },
        # 阶段 3.5：结构化判分明细同样**从落库的那一份读**。
        #
        # 这里修掉一个真实缺陷：改造前 service 会在返回前补一句
        # `result["grading_detail"] = grade["detail"]`，但
        # `SubmitAnswerResponse` 没有声明该字段，Pydantic 静默丢弃 ——
        # LLM 判分明细算好了、存库了，前端却永远拿不到。
        # 现在字段已在响应模型里，且只以"落库的事实"为准。
        "grading_detail": review_log.grading_detail,
    }


async def get_review_stats(
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    获取用户复习统计数据

    统计维度：
    - 今日待复习数
    - 今日已完成数
    - 今日正确率
    - 累计复习次数
    - 累计正确率

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 复习统计数据
    """
    now = datetime.now(timezone.utc)
    # 日界按 Asia/Shanghai（北京时间零点），而非 UTC 零点（见 docs/decisions.md#F-32）
    from ..utils.timeutil import today_start_utc
    today_start = today_start_utc(now)

    # 今日已完成数（先查，后面 due_count 需要用）；口径见 _count_settled_today
    today_done = await _count_settled_today(db, user_id, today_start)

    # 今日待复习数（next_review_at <= now 或为 None），考虑每日限额
    remaining_quota = max(0, DAILY_REVIEW_LIMIT - today_done)
    if remaining_quota <= 0:
        due_count = 0
    else:
        # 使用子查询加 LIMIT，避免扫描全表（回收站笔记的题目暂不可见）
        due_subq = (
            select(func.count())
            .select_from(
                select(QuizItem.id)
                .where(
                    QuizItem.user_id == user_id,
                    (QuizItem.next_review_at <= now) | (QuizItem.next_review_at.is_(None)),
                    or_(
                        QuizItem.note_id.is_(None),
                        select(Note.id).where(
                            Note.id == QuizItem.note_id, Note.trashed_at.is_(None)
                        ).exists(),
                    ),
                )
                .limit(remaining_quota)
                .subquery()
            )
        )
        due_count_result = await db.execute(due_subq)
        due_count = due_count_result.scalar() or 0

    # 今日正确数
    today_correct_result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
            ReviewLog.review_at >= today_start,
            ReviewLog.is_correct.is_(True),
        )
    )
    today_correct = today_correct_result.scalar() or 0

    # 累计复习次数
    total_reviews_result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
        )
    )
    total_reviews = total_reviews_result.scalar() or 0

    # 累计正确数
    total_correct_result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
            ReviewLog.is_correct.is_(True),
        )
    )
    total_correct = total_correct_result.scalar() or 0

    # 总题目数（回收站笔记的题目暂不可见，不计入）
    total_quizzes_result = await db.execute(
        select(func.count()).select_from(QuizItem).where(
            QuizItem.user_id == user_id,
            or_(
                QuizItem.note_id.is_(None),
                select(Note.id).where(
                    Note.id == QuizItem.note_id, Note.trashed_at.is_(None)
                ).exists(),
            ),
        )
    )
    total_quizzes = total_quizzes_result.scalar() or 0

    return {
        "due_count": due_count,
        "today_done": today_done,
        "today_correct": today_correct,
        "today_accuracy": round(today_correct / today_done * 100, 1) if today_done > 0 else 0,
        "total_reviews": total_reviews,
        "total_correct": total_correct,
        "total_accuracy": round(total_correct / total_reviews * 100, 1) if total_reviews > 0 else 0,
        "total_quizzes": total_quizzes,
        "daily_limit": DAILY_REVIEW_LIMIT,  # 每日答题上限下发给前端，见 docs/decisions.md#F-12
    }


async def get_review_history(
    user_id: str,
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """
    获取用户复习历史（分页）

    Args:
        user_id: 用户 ID
        db: 数据库会话
        page: 页码
        page_size: 每页数量

    Returns:
        Dict: 复习历史列表和分页信息
    """
    # 计算总数
    count_result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
        )
    )
    total = count_result.scalar() or 0

    # 分页查询
    result = await db.execute(
        select(ReviewLog).where(
            ReviewLog.user_id == user_id,
        ).order_by(
            ReviewLog.review_at.desc()
        ).offset((page - 1) * page_size).limit(page_size)
    )
    logs = list(result.scalars().all())

    items = []
    for log in logs:
        items.append({
            "id": log.id,
            "quiz_id": log.quiz_id,
            "note_id": log.note_id,
            "user_answer": log.user_answer,
            "is_correct": log.is_correct,
            "quality": log.quality,
            "time_spent_ms": log.time_spent_ms,
            "review_at": log.review_at.isoformat() if log.review_at else None,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
