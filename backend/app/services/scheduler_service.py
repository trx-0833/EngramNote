"""调度器门面（overhaul-plan 阶段 3.6）

## 为什么要有这一层

换算法时最容易犯的错，是在**每个调用点**各改一次：
`review_service.submit_answer` 与 `api/review.py::submit_card_review`
各自算一次 SM-2（这正是改造前的状态 —— 两处都直接调
`calculate_sm2`），于是任何调度改动都要改两遍，漏一处就出现
"卡片复习用新算法、答题复习用旧算法"的分叉。

本模块把"一次复习 → 一次调度结果"收敛成一个入口：

    advance(state, quality, method, now) -> ScheduleOutcome

调用方只负责把结果落库，不关心背后是 FSRS 还是 SM-2。

## 为什么保留 SM-2 这条路径

`config.review_scheduler` 默认 `"fsrs"`，但可以随时切回 `"sm2"`。
这不是为了"两个都要维护"，而是**回退开关**：FSRS 会改变每个用户的
复习节奏，如果线上出现"间隔暴涨/卡片再也不出现"这类问题，
一个配置项就能退回已验证多年的旧行为，而不必回滚整个版本。
代价是 `calculate_sm2` 与它的一致性测试要保留 —— 那部分代码本来就在，
且已被 `test_week8_sm2.py` 覆盖。

## quality 与 rating 的区别（本模块最关键的一处口径转换）

项目内部沿用 SM-2 的 0-5 分（`review_logs.quality`，自评/选择/填空/
语义判分共用），而 FSRS 用 4 档（Again/Hard/Good/Easy）。
换算不是简单的除法，因为它取决于**这个分是谁给的**：

    quality  SM-2 语义              rating
    -------  ---------------------  ------
    0,1,2    没想起来（quality<3）  Again
    3        勉强正确，很费力       Hard
    4        正确但有些犹豫         Good
    5        完美、毫不费力         Easy —— **仅当用户自评时**

⚠️ **机器判分得到的 quality=5 不等于 Easy**。选择题答对时
`_grade_choice` 一律给 5，但"选对了"完全没有"毫不费力"这层信息
（可能是蒙对的）。若照搬成 Easy，新卡第一次答对就会按 S0(Easy)=15.7
天排到两周后 —— 用户只见了这张卡一次。所以机器判分封顶 Good，
理由与 3.5 里"不给 LLM 判 0-100 分"是同一个：**只使用信号里真正
存在的信息**。

（用答题耗时推断"轻松程度"是可能的改进方向，但那是新的启发式，
需要单独验证，不能顺手塞进口径转换里。）
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..models.review_state import ReviewState, ReviewStateKind
from ..utils.timeutil import align_to_hour_of_day, days_between_business_days
from . import fsrs_service
from .fsrs_service import (
    RATING_AGAIN,
    RATING_EASY,
    RATING_GOOD,
    RATING_HARD,
)

logger = logging.getLogger(__name__)

#: SM-2 的成功阈值（quality >= 3）；与 `sm2_service.calculate_sm2` 一致
PASS_QUALITY = 3

#: 调度算法名
ALGORITHM_FSRS = "fsrs"
ALGORITHM_SM2 = "sm2"

#: 到期时刻默认锚定的整点（业务时区）。与 Anki 的 rollover hour 同源：
#: 取在凌晨是为了"今天到期"与用户的日历一致 —— 无论用户几点开始学习，
#: 当天到期的卡片都已经是到期的。
DEFAULT_DUE_HOUR = 4

#: 自评分 → FSRS 档位。前端四档按钮的取值恰好是 0/3/4/5，
#: 与 Again/Hard/Good/Easy 一一对应（见 frontend/src/utils/labels.ts 的
#: selfRatingOptions）；1/2 是 SM-2 尺度上的"错得轻一点"，
#: 在 FSRS 里同属 Again（FSRS 没有"错得比较轻"这一档）。
_SELF_RATING_TO_RATING = {
    0: RATING_AGAIN,
    1: RATING_AGAIN,
    2: RATING_AGAIN,
    3: RATING_HARD,
    4: RATING_GOOD,
    5: RATING_EASY,
}

#: 机器判分（choice / fill_blank / semantic）→ FSRS 档位，**封顶 Good**
_MACHINE_QUALITY_TO_RATING = {
    0: RATING_AGAIN,
    1: RATING_AGAIN,
    2: RATING_AGAIN,
    3: RATING_HARD,
    4: RATING_GOOD,
    5: RATING_GOOD,
}


def rating_from_quality(quality: int, method: str = "") -> int:
    """把 SM-2 的 0-5 quality 换算成 FSRS 的 1-4 rating

    Args:
        quality: 0-5（越界会被夹紧）
        method: 判分方式；`"self_rating"` 表示这一分来自用户自评，
            只有它才可能得到 Easy。其余（choice/fill_blank/semantic/
            ungraded/legacy）一律封顶 Good。
    """
    q = max(0, min(5, int(quality)))
    table = (
        _SELF_RATING_TO_RATING if method == "self_rating" else _MACHINE_QUALITY_TO_RATING
    )
    return table[q]


def derive_kind(repetition: int, last_quality: Optional[int]) -> ReviewStateKind:
    """由连续成功次数与最近一次成绩推导学习阶段（迁移/补建用）

    ⚠️ **不要**用它推导"一次复习之后的阶段"，用 `sm2_kind`：
    本函数在"失败后 repetition 归零"时无法区分"从没学过"与"学过又忘了"
    （只能靠 `last_quality` 猜），而这正是 `sm2_kind` 显式处理的差异。
    它现在的用途是从 `quiz_items` 旧字段补建 ReviewState
    （`review_state_service._bootstrap_state`），那里确实没有"最近一次成绩"。
    """
    if repetition <= 0:
        return ReviewStateKind.learning if last_quality is not None else ReviewStateKind.new
    if last_quality is not None and last_quality < PASS_QUALITY:
        return ReviewStateKind.relearning
    return ReviewStateKind.review


def sm2_kind(repetition: int, passed: bool) -> ReviewStateKind:
    """SM-2 路径"一次复习之后"的学习阶段

    这是改造前 `review_state_service._apply_result_to_state` 的规则，
    在这里**逐字保留**：成功 → `review`，失败 → `relearning`。
    回退开关的意义是行为完全不变，而不是"顺便变得更合理"。
    """
    if not passed:
        return ReviewStateKind.relearning
    return ReviewStateKind.review if repetition > 0 else ReviewStateKind.learning


@dataclass
class ScheduleOutcome:
    """一次复习的调度结果（算法无关的统一口径）

    调用方（`review_state_service.apply_schedule_result`）只依赖这里声明的
    字段，因此切换算法不会波及落库逻辑。

    Attributes:
        interval_days: 距下次复习的天数
        repetition: 连续成功次数（rating/quality >= 及格线才算成功）
        easiness_factor: SM-2 兼容列的值（FSRS 下由难度桥接而来）
        next_review_at: 下次到期时间
        state: 复习后的学习阶段
        algorithm: 'fsrs' | 'sm2'
        rating: FSRS 档位 1-4；SM-2 路径下为 None（该算法没有这一档）
        predicted_retention: **复习前**模型预测的可回忆概率；
            SM-2 路径下为 None（见下）
        stability / difficulty: FSRS 状态；SM-2 路径下为 None
    """
    interval_days: int
    repetition: int
    easiness_factor: float
    next_review_at: datetime
    state: ReviewStateKind
    algorithm: str
    rating: Optional[int] = None
    predicted_retention: Optional[float] = None
    stability: Optional[float] = None
    difficulty: Optional[float] = None
    #: 复习前的 S（用于日志与测试断言；不落库）
    previous_stability: Optional[float] = None
    #: 复习前的 R 无从计算时为 True（缺少 last_reviewed_at）
    elapsed_known: bool = True


def elapsed_business_days(last_reviewed_at: Optional[datetime], now: datetime) -> int:
    """距上次复习隔了几个**业务日**（同一业务日为 0）

    ## 为什么是整天数而不是小时差

    见 `timeutil.business_day_index` 的长注释。一句话：
    FSRS 用 `elapsed < 1` 判断"同日复习"并据此走短时公式，
    而"同日"是日历概念、不是 24 小时 —— 晚上 23:00 复习、次日早上 08:00
    再看到它，连续差是 0.375 天（会被误判成"同日"），业务日差是 1（正确）。

    时区归一化在这里做而不是相信数据库：SQLite 不存时区，
    历史行取出来可能是 naive 的（见 `review_state_service._as_aware`）。
    """
    if last_reviewed_at is None:
        return 0
    return max(0, days_between_business_days(last_reviewed_at, now))


def apply_scheduling_policy(
    outcome: ScheduleOutcome,
    *,
    now: datetime,
    settings=None,
    rand: Optional[float] = None,
) -> ScheduleOutcome:
    """把算法给出的间隔变成真正的到期时刻（抖动 + 时段对齐）

    ⚠️ 这一步**与算法无关**：FSRS 与 SM-2 都要做，因为"同批卡片挤在同一天"
    和"到期时刻漂到凌晨"是 SM-2 时期就存在的老问题（见 overhaul-plan §2.4 L-4
    的第 4 条），不是换算法带来的。

    顺序不能颠倒：
      1. 先抖动间隔 —— 抖的是"几天后"；
      2. 再对齐到期时刻 —— 抖完的那一天要落在哪个整点。
    反过来先对齐再抖动，抖动会把时刻重新拉回随机钟点，对齐白做。

    Args:
        outcome: 算法给出的结果（会被就地修改并返回）
        now: 本次复习时间
        settings: 配置覆盖
        rand: 抖动用的 [0,1) 随机数（测试用）

    Returns:
        更新了 `interval_days` 与 `next_review_at` 的同一个 outcome
    """
    if settings is None:
        from ..config import get_settings
        settings = get_settings()

    ratio = float(getattr(settings, "review_fuzz_ratio", fsrs_service.FUZZ_RATIO))
    max_interval = int(
        getattr(settings, "fsrs_max_interval_days", fsrs_service.MAX_INTERVAL_DAYS)
    )
    interval = fsrs_service.fuzz_interval(
        outcome.interval_days,
        rand=rand,
        ratio=ratio,
        max_interval_days=max_interval,
    )

    due_hour = int(getattr(settings, "review_due_hour", DEFAULT_DUE_HOUR))
    if due_hour >= 0:
        next_review_at = align_to_hour_of_day(
            now + timedelta(days=interval), due_hour, not_before=now,
        )
    else:
        # 负值 = 关闭对齐（保留"上次复习的钟点"这一旧行为）。
        # 留这个开关是因为对齐会改变"今天该不该复习"的判断边界，
        # 万一与用户的作息冲突，要能一键退回。
        next_review_at = now + timedelta(days=interval)

    if interval != outcome.interval_days:
        logger.debug(
            "间隔抖动: %d → %d 天（ratio=%.3f）", outcome.interval_days, interval, ratio,
        )
    outcome.interval_days = interval
    outcome.next_review_at = next_review_at
    return outcome


def advance(
    state: ReviewState,
    quality: int,
    *,
    method: str = "",
    now: Optional[datetime] = None,
    algorithm: Optional[str] = None,
    settings=None,
    rand: Optional[float] = None,
) -> ScheduleOutcome:
    """跑一次调度，返回统一口径的结果（纯计算，不碰数据库）

    Args:
        state: 复习前的 `ReviewState` 行。读取 `stability`/`difficulty`/
            `interval_days`/`repetition`/`easiness_factor`/`state`/
            `last_reviewed_at`，**不修改它**。
        quality: 本次评分 0-5
        method: 判分方式（决定 quality=5 能不能算 Easy，见模块说明）
        now: 本次复习时间；默认当前 UTC
        algorithm: 覆盖配置里的算法（测试用）
        settings: 覆盖配置对象（测试用）
        rand: 间隔抖动用的 [0,1) 随机数（测试用；None 时取真随机）

    Returns:
        ScheduleOutcome（已施加抖动与到期时刻对齐，见 `apply_scheduling_policy`）
    """
    if settings is None:
        from ..config import get_settings
        settings = get_settings()
    algo = (algorithm or getattr(settings, "review_scheduler", ALGORITHM_FSRS) or "").lower()
    now = now or datetime.now(timezone.utc)

    quality = max(0, min(5, int(quality)))
    passed = quality >= PASS_QUALITY

    if algo == ALGORITHM_SM2:
        outcome = _advance_sm2(state, quality, passed=passed, now=now)
    else:
        if algo != ALGORITHM_FSRS:
            # 配置写错时**响亮地**退回默认并留下日志，而不是静默用某个算法 ——
            # 静默的默认值会让"我明明配了 sm2，怎么间隔变了"变成无从排查的问题。
            logger.warning(
                "未知的 review_scheduler=%r，按 %s 处理", algo, ALGORITHM_FSRS,
            )
        outcome = _advance_fsrs(
            state, quality, passed=passed, method=method, now=now, settings=settings,
        )

    return apply_scheduling_policy(outcome, now=now, settings=settings, rand=rand)


def _advance_sm2(
    state: ReviewState, quality: int, *, passed: bool, now: datetime,
) -> ScheduleOutcome:
    """SM-2 路径（回退开关用）

    阶段推导见 `sm2_kind`（与改造前逐字一致）。
    """
    from .sm2_service import calculate_sm2

    result = calculate_sm2(
        quality=quality,
        interval=state.interval_days,
        repetition=state.repetition,
        easiness_factor=state.easiness_factor,
    )
    return ScheduleOutcome(
        interval_days=result.interval,
        repetition=result.repetition,
        easiness_factor=result.easiness_factor,
        next_review_at=result.next_review_at,
        state=sm2_kind(result.repetition, passed),
        algorithm=ALGORITHM_SM2,
    )


def _advance_fsrs(
    state: ReviewState, quality: int, *, passed: bool, method: str,
    now: datetime, settings,
) -> ScheduleOutcome:
    rating = rating_from_quality(quality, method)
    elapsed = elapsed_business_days(state.last_reviewed_at, now)

    result = fsrs_service.schedule(
        rating=rating,
        state=state.state,
        stability=state.stability,
        difficulty=state.difficulty,
        elapsed_days=elapsed,
        interval_days=state.interval_days or 1,
        easiness_factor=state.easiness_factor or fsrs_service.EF_NEUTRAL,
        now=now,
        request_retention=float(
            getattr(settings, "fsrs_request_retention", fsrs_service.DEFAULT_REQUEST_RETENTION)
        ),
        max_interval_days=int(
            getattr(settings, "fsrs_max_interval_days", fsrs_service.MAX_INTERVAL_DAYS)
        ),
    )

    # repetition 是 SM-2 遗留列，语义仍保持"连续成功次数"：
    # 成功 +1、失败归零。它不再参与 FSRS 的任何计算，但到期列表、
    # 卡片复习页与回滚后的 SM-2 都还会读它。
    repetition = (state.repetition or 0) + 1 if passed else 0

    return ScheduleOutcome(
        interval_days=result.interval_days,
        repetition=repetition,
        # EF 由难度桥接：见 fsrs_service.easiness_from_difficulty 的长注释
        #（保留这一列的"越大越容易"语义，而不是让它悄悄停更）。
        easiness_factor=fsrs_service.easiness_from_difficulty(result.difficulty),
        next_review_at=result.next_review_at,
        state=result.state,
        algorithm=ALGORITHM_FSRS,
        rating=rating,
        predicted_retention=result.retrievability,
        stability=result.stability,
        difficulty=result.difficulty,
        previous_stability=state.stability,
        elapsed_known=state.last_reviewed_at is not None,
    )


__all__ = [
    "ALGORITHM_FSRS",
    "ALGORITHM_SM2",
    "PASS_QUALITY",
    "ScheduleOutcome",
    "advance",
    "apply_scheduling_policy",
    "derive_kind",
    "sm2_kind",
    "elapsed_business_days",
    "rating_from_quality",
    "DEFAULT_DUE_HOUR",
]
