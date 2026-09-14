"""
提示词版本效果报表（overhaul-plan 阶段 4.6 的收尾：让 `prompt_version` 真的被读一次）

## 这一层解决什么

4.6 给 `knowledge_cards` / `quiz_items` 加上了 `prompt_version`，写入端也接了线。
但那一列**写了没人读**：库里有了溯源，人却依然回答不了唯一值得问的问题 ——

    「我改了提示词，新一版产出的内容是不是更好？」

列存在而没有任何消费方，等于没做。这与本项目反复出现的「工具做完但没人调用」
是同一个缺陷在数据上的形态。本模块就是那一列的**读取端**：按版本分组，
给出「产出了多少内容」，以及**这些内容后来表现如何**。

## 三个必须分开的「没有数据」

报表最容易骗人的地方，是把三种含义完全不同的「没有数据」混成一团：

| 状态 | 含义 | 人应该据此做什么 |
|---|---|---|
| **已登记但无数据** | 提示词已升到新版本，库里还没有这一版产出的内容 | 这次改动**尚未生效**（最可操作的信号） |
| **数据里有、登记表里没有** | 版本号漂移：数据来自已改名/已删除的登记，或有人手写过 | 这一组无法解释，拿它做版本比较的结论不可信 |
| **NULL（未知）** | 4.6 之前入库的历史行，当时没有任何版本记录 | **不能**当作「第一版」，也不该被静默忽略 |

第三种是当前真库的真实状态：1183 张卡、1058 道题**全部是 NULL**（历史行刻意
不回填，见 `KnowledgeCard.prompt_version` 与 `prompts.prompt_version` 的说明）。
因此本模块在今天的输出必然是「只有一桶：版本未知」—— 那是一个**诚实的答案**，
不是坏掉的页面；`_build_notes` 会把这一点直接讲给使用者听，
而不是留一个空列表让人以为报表坏了。

## 哪些提示词能被这张报表评估（与 4.6 任务书的一处出入）

只有**真的往这两张表里写行**的提示词才有版本可比。在 `app/` 下检索
`prompt_version` 的调用点，一共**四处**写入点，比「只有 understanding_session
与 question_session 两条」多两条：

    understanding_session        → knowledge_cards   （understanding_service）
    combined_analysis_session    → knowledge_cards   （knowledge_link_service 联合分析卡）
    generate_extension_knowledge → knowledge_cards   （knowledge_link_service 拓展卡）
    question_session             → quiz_items        （understand_tasks）

因此「哪些提示词可能出现在这一列上」必须是**显式声明**（`CONTENT_SOURCES`），
不能靠猜：把 11 条登记全部当成「可能出现在卡片上」会产生 9 条永远为真的
「这一版还没生效」假警报，而假警报会让真警报一起被忽略。
`tests/test_prompt_version_report.py` 用源码扫描把这份声明钉在真实写入点上。

## 度量与代理

能真正从这个 schema 里算出来的，只有下面这些（每一项都在 `METRIC_NOTES` 里
写清它**是什么**与**不是什么**）：

- **产出量**：`knowledge_cards` / `quiz_items` 按 `prompt_version` 计数；
- **复习量**：`review_logs` 按 `card_id` / `quiz_id` 归属到产出它的版本；
- **通过率**：`review_logs.quality >= 3` 的比例 —— 与调度器 `PASS_QUALITY`
  同一个阈值，含义是「调度器认为你记住了」；
- **遗忘与间隔**：`review_states.lapses` / `interval_days`（**当前值**，
  不是窗口内的历史值：这张表每个学习项只有一行）；
- **掌握度**：`knowledge_cards.mastery_level`（0-100 的展示量）。

⚠️ 它们全都是**代理指标**，没有任何一项能单独证明「这一版提示词更好」：
同一个人的投入程度、复习时机、内容难度分布都会影响它们。它们只支持
「同一个人、同一时期、不同版本之间」的相对比较，不支持绝对结论。
报表因此把每个数字的来源与局限一起给出去，而不是只给一个漂亮的百分比。
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import and_, case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.knowledge_card import KnowledgeCard
from ..models.quiz_item import QuizItem
from ..models.review_log import ReviewLog
from ..models.review_state import ITEM_TYPE_CARD, ITEM_TYPE_QUIZ, ReviewState
from .llm.prompts import PROMPT_VERSIONS

logger = logging.getLogger(__name__)

#: 内容类别。`cards` / `quizzes` 是两张表、两条写入链，**不能相加** ——
#: 同一张卡片既可能被卡片级复习，也会被它下属的题目复习，两边都会计入。
KIND_CARD = "card"
KIND_QUIZ = "quiz"

#: 通过阈值：SM-2 的 quality >= 3（与 sm2_service / learning_metrics_service 一致）。
#: 刻意不复用「答对（is_correct）」：判分方式有多种（choice/fill_blank/self_rating），
#: 而进入调度的是 quality —— 报表要跟调度器说同一件事，否则「通过率」与
#: 「下次什么时候复习」会互相矛盾。
PASS_QUALITY = 3

#: 默认统计窗口（天）。与 `/api/llm/usage` 的默认值一致：先看「最近一个月」。
DEFAULT_WINDOW_DAYS = 30

#: 「未知版本」在查询参数里的写法。HTTP 查不了 NULL，因此必须有一个显式词
#: （`/prompt-versions?version=unknown`），否则那一桶**根本没法单独查看**。
UNKNOWN_VERSION_TOKEN = "unknown"

#: NULL 版本在报表里的标签。刻意写全「为什么未知」，因为「未知」两个字
#: 太容易被读成「大概是第一版」。
UNKNOWN_VERSION_LABEL = "版本未知（4.6 之前入库、当时没有版本记录）"


@dataclass(frozen=True)
class ContentSource:
    """一条**会往卡片/题目表里写行**的提示词

    `prompt_name` 必须同时存在于 `PROMPT_VERSIONS`（否则它写进列里的是 NULL，
    而那属于「未知」，不属于任何版本）。

    Attributes:
        prompt_name: 登记表里的名字
        kind: 产出哪张表的内容（`card` / `quiz`）
        what: 中文说明，直接出现在报表里（人读的，不是给程序用的）
    """
    prompt_name: str
    kind: str
    what: str


#: 内容生产者的**显式清单**（理由见模块说明：实测四处写入点，不是两处）。
#:
#: ⚠️ 新增会写 `knowledge_cards` / `quiz_items` 的提示词时必须加到这里，
#: 否则它的版本会静默地落在报表之外 —— `test_content_sources_cover_every_write_site`
#: 会在源码里发现新的 `prompt_version` 调用点并让测试失败。
CONTENT_SOURCES: Tuple[ContentSource, ...] = (
    ContentSource(
        prompt_name="understanding_session",
        kind=KIND_CARD,
        what="理解笔记时抽取的知识卡片",
    ),
    ContentSource(
        prompt_name="combined_analysis_session",
        kind=KIND_CARD,
        what="联合分析（笔记 × 学习资料）产出的卡片",
    ),
    ContentSource(
        prompt_name="generate_extension_knowledge",
        kind=KIND_CARD,
        what="拓展知识点卡片",
    ),
    ContentSource(
        prompt_name="question_session",
        kind=KIND_QUIZ,
        what="按卡片生成的练习题",
    ),
)

#: 每个数字的含义与局限。键与报表字段同名，随响应一起返回 ——
#: 这样前端/使用者不需要读源码就知道「通过率」是代理指标。
METRIC_NOTES: Dict[str, str] = {
    "content_in_window": "窗口内新增的内容条数（按行自己的 created_at 计，左闭右开）",
    "content_total": "该版本累计产出的内容条数（**不受窗口限制**）—— 用来区分「这一版停产了」与「这一版从没有过」",
    "reviewed_content": "窗口内至少被复习过一次的内容条数（去重后的卡片/题目数）",
    "reviews": "窗口内这些内容被复习的次数（review_logs 行数，按 review_at 计）",
    "passed": "其中 quality >= 3 的次数。quality 是**进入调度**的那个分，与调度器同一口径",
    "pass_rate_percent": (
        "代理指标：passed / reviews 的百分比，含义是「调度器认为你记住了」的比例。"
        "它同时受内容质量、用户投入、复习时机影响，只能用于同一人同一时期的版本间相对比较，"
        "**不是**「这一版提示词的分数」。一次复习都没有时为 null —— 那不是 0%（见 notes）"
    ),
    "tracked_items": "有 review_states 行的内容数（只有复习过的学习项才会建状态行）",
    "lapses": "代理指标：review_states.lapses 之和（累计遗忘次数，quality < 3 时 +1）",
    "lapses_per_item": "代理指标：lapses / tracked_items，便于条目数不同的版本之间对比",
    "avg_interval_days": (
        "代理指标：review_states.interval_days 的平均值 —— 它是**调度器**根据你的表现算出的"
        "下次间隔，间隔越长说明它认为你记得越牢。注意这是**当前值**，不是窗口内的历史值"
        "（review_states 每个学习项只有一行）"
    ),
    "avg_mastery": (
        "代理指标：knowledge_cards.mastery_level 的平均值（0-100，见 mastery_service）。"
        "**未复习过的卡片按 0 计入**（公式如此），因此它同时受「学了多少」与「记得多牢」影响；"
        "配合 mastery_positive（分数 > 0 的卡片数）一起看才不会被 0 误导。"
        "同样只覆盖卡片，题目没有掌握度字段"
    ),
    "mastery_positive": "mastery_level > 0 的卡片数 —— 用来识别 avg_mastery 被「从未复习」拉低",
    "pass_quality": f"通过阈值 quality >= {PASS_QUALITY}（SM-2 的及格线，与调度器一致）",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_version_sort_key(version: Optional[str]) -> Tuple[int, int, str]:
    """排序键：版本号从新到旧，未知（NULL）永远排最后

    版本号是**可排序的正整数字符串**（见 `prompts.PROMPT_VERSIONS` 的取值规则）。
    这里仍然对非数字做兜底排序而不是抛错：真库里可能出现任何字符串
    （那正是「漂移」状态），报表必须能把它**显示出来**，而不是因为
    一个坏版本号整个崩掉 —— 崩掉的报表等于没有报表。
    """
    if version is None:
        return (2, 0, "")
    if version.isdigit():
        return (0, -int(version), "")
    return (1, 0, version)


def _and(*conditions):
    """把若干条件合成一个（SQLAlchemy 的 `and_` 需要非空参数）"""
    return and_(*conditions)


# ---------------------------------------------------------------------------
# 聚合查询层
# ---------------------------------------------------------------------------

async def _content_stats(
    db: AsyncSession,
    *,
    user_id: str,
    model,
    since: Optional[datetime],
    until: Optional[datetime],
    with_mastery: bool = False,
) -> Dict[Optional[str], Dict[str, Any]]:
    """按 `prompt_version` 统计内容产出量（可选：掌握度）

    Returns:
        `{版本: {...}}`，键 `None` 即「版本未知」那一桶。

    两次计数刻意并列返回：

    - `content_in_window`：窗口内新增（回答「这次改动之后产出了多少」）
    - `content_total`：全量（回答「这一版到底有没有产出过东西」）

    只有前者时，「窗口内 0 条」无法区分「没生效」与「早就停产」。
    """
    window = []
    if since is not None:
        window.append(model.created_at >= since)
    if until is not None:
        window.append(model.created_at < until)

    columns: List[Any] = [func.count().label("content_total")]
    if window:
        columns.append(func.count(case((_and(*window), 1))).label("content_in_window"))
    else:
        columns.append(func.count().label("content_in_window"))
    if with_mastery:
        columns.append(func.coalesce(func.sum(model.mastery_level), 0.0).label("mastery_sum"))
        columns.append(
            func.count(case((model.mastery_level > 0, 1))).label("mastery_positive")
        )

    rows = (await db.execute(
        select(model.prompt_version.label("version"), *columns)
        .where(model.user_id == user_id)
        .group_by(model.prompt_version)
    )).all()

    out: Dict[Optional[str], Dict[str, Any]] = {}
    for row in rows:
        data = dict(row._mapping)
        out[data.pop("version")] = data
    return out


async def _review_stats(
    db: AsyncSession,
    *,
    user_id: str,
    join_model,
    fk_column,
    since: Optional[datetime],
    until: Optional[datetime],
) -> Dict[Optional[str], Dict[str, Any]]:
    """按产出该内容的版本统计复习表现

    归属规则：`review_logs.card_id → knowledge_cards.id`（卡片视图）与
    `review_logs.quiz_id → quiz_items.id`（题目视图）。为什么用 `card_id`
    而不是从题目反查卡片：题目会被「重新理解」整批替换，反查会丢历史
    （见 `ReviewLog.card_id` 的说明）。

    ⚠️ 两个视图**会重叠**：一次答题同时带 `card_id` 与 `quiz_id`，
    于是它在卡片视图与题目视图里各算一次。这是有意的（两个问题不同：
    「这一版卡片好不好」与「这一版题目好不好」），但因此两张表的
    `reviews` **不能相加** —— 响应里的 notes 会显式说明。

    `join_model.user_id == user_id` 是纵深防御：`ReviewLog` 自带 `user_id`，
    但一旦出现跨用户的脏数据（修复/导入），仅按 ID 关联就会把别人的
    复习表现算进我的报表。
    """
    conditions = [ReviewLog.user_id == user_id, join_model.user_id == user_id]
    if since is not None:
        conditions.append(ReviewLog.review_at >= since)
    if until is not None:
        conditions.append(ReviewLog.review_at < until)

    rows = (await db.execute(
        select(
            join_model.prompt_version.label("version"),
            func.count().label("reviews"),
            func.count(case((ReviewLog.quality >= PASS_QUALITY, 1))).label("passed"),
            func.count(distinct(fk_column)).label("reviewed_content"),
        )
        .select_from(ReviewLog)
        .join(join_model, fk_column == join_model.id)
        .where(*conditions)
        .group_by(join_model.prompt_version)
    )).all()
    return {
        row._mapping["version"]: {
            k: v for k, v in dict(row._mapping).items() if k != "version"
        }
        for row in rows
    }


async def _state_stats(
    db: AsyncSession,
    *,
    user_id: str,
    join_model,
    item_type: str,
) -> Dict[Optional[str], Dict[str, Any]]:
    """按版本统计**当前**学习状态（遗忘次数、间隔）

    **不过滤时间窗**：`review_states` 每个学习项只有一行、没有历史，
    「窗口内的间隔」是个不存在的量。把它按窗口切只会得到随窗口跳动的假数字。
    """
    rows = (await db.execute(
        select(
            join_model.prompt_version.label("version"),
            func.count().label("tracked_items"),
            func.coalesce(func.sum(ReviewState.lapses), 0).label("lapses"),
            # 存**总和**而不是平均值：均值无法跨版本再聚合（错的做法是把各版本
            # 的均值直接平均 —— 条目数不同的版本会被同等加权）。总和可以在
            # 组装层按 tracked_items 重新折算，单桶与合计于是走同一条公式。
            func.coalesce(func.sum(ReviewState.interval_days), 0).label("interval_sum"),
        )
        .select_from(ReviewState)
        .join(
            join_model,
            _and(ReviewState.item_id == join_model.id, join_model.user_id == user_id),
        )
        .where(ReviewState.user_id == user_id, ReviewState.item_type == item_type)
        .group_by(join_model.prompt_version)
    )).all()
    return {
        row._mapping["version"]: {
            k: v for k, v in dict(row._mapping).items() if k != "version"
        }
        for row in rows
    }


async def _review_link_quality(
    db: AsyncSession,
    *,
    user_id: str,
    since: Optional[datetime],
    until: Optional[datetime],
) -> Dict[str, int]:
    """窗口内复习记录的**可归属情况**

    报表的分母必须自己交代清楚：既没 `card_id` 也没 `quiz_id` 的复习记录
    **进不了任何桶**（无法判断它属于哪一版产出的内容），不说明的话，
    各桶之和对不上总数，读的人只会以为报表算错了。
    """
    conditions = [ReviewLog.user_id == user_id]
    if since is not None:
        conditions.append(ReviewLog.review_at >= since)
    if until is not None:
        conditions.append(ReviewLog.review_at < until)

    row = (await db.execute(
        select(
            func.count().label("reviews_in_window"),
            func.count(case((
                _and(ReviewLog.card_id.is_(None), ReviewLog.quiz_id.is_(None)), 1,
            ))).label("reviews_unattributable"),
            func.count(case((
                _and(ReviewLog.card_id.is_not(None), ReviewLog.quiz_id.is_not(None)), 1,
            ))).label("reviews_linked_to_both"),
        ).where(*conditions)
    )).one()
    return {key: int(value or 0) for key, value in dict(row._mapping).items()}


# ---------------------------------------------------------------------------
# 组装层
# ---------------------------------------------------------------------------

def _blank_kind_stats(kind: str) -> Dict[str, Any]:
    """一个版本在某一类内容上的统计骨架

    分成两层，避免把「原始计数」与「折算出来的指标」混在一起：

    - **原始计数**（可直接相加）：`content_in_window` / `content_total` /
      `reviewed_content` / `reviews` / `passed` / `tracked_items` /
      `lapses` / `interval_sum` / `mastery_sum` / `mastery_positive`
    - **折算指标**（由 `_finish_kind_stats` 算出，不能相加）：`pass_rate_percent` /
      `lapses_per_item` / `avg_interval_days` / `avg_mastery`

    ⚠️ `pass_rate_percent` / `lapses_per_item` / `avg_interval_days` /
    `avg_mastery` 的「无数据」一律是 **None，不是 0**：
    「没有复习」与「复习了但全错」在报表上必须可区分，
    否则 0% 会被读成「这一版的内容很差」。
    """
    stats: Dict[str, Any] = {
        "content_in_window": 0,
        "content_total": 0,
        "reviewed_content": 0,
        "reviews": 0,
        "passed": 0,
        "pass_rate_percent": None,
        "tracked_items": 0,
        "lapses": 0,
        "lapses_per_item": None,
        "interval_sum": 0,
        "avg_interval_days": None,
    }
    if kind == KIND_CARD:
        # 题目表没有掌握度字段（掌握度是卡片级展示量）
        stats["mastery_sum"] = 0.0
        stats["mastery_positive"] = 0
        stats["avg_mastery"] = None
    return stats


def _finish_kind_stats(stats: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """把原始计数折算成可读指标（比例、均值），并抹掉中间列"""
    reviews = int(stats.get("reviews") or 0)
    stats["reviews"] = reviews
    stats["passed"] = int(stats.get("passed") or 0)
    stats["pass_rate_percent"] = (
        round(stats["passed"] / reviews * 100, 1) if reviews else None
    )

    tracked = int(stats.get("tracked_items") or 0)
    stats["tracked_items"] = tracked
    stats["lapses"] = int(stats.get("lapses") or 0)
    stats["lapses_per_item"] = round(stats["lapses"] / tracked, 2) if tracked else None
    interval_sum = int(stats.pop("interval_sum", 0) or 0)
    stats["avg_interval_days"] = round(interval_sum / tracked, 2) if tracked else None

    if kind == KIND_CARD:
        total = int(stats.get("content_total") or 0)
        mastery_sum = float(stats.pop("mastery_sum", 0.0) or 0.0)
        stats["avg_mastery"] = round(mastery_sum / total, 1) if total else None
        stats["mastery_positive"] = int(stats.get("mastery_positive") or 0)
    return stats


def _registered_versions() -> set:
    """当前登记表里出现过的全部版本号（每次调用现取，便于测试注入）"""
    return set(PROMPT_VERSIONS.values())


def _producers_for(version: Optional[str], kind: str) -> List[str]:
    """可能产出该版本、该类内容的提示词名

    `None` 版本不归属任何提示词 —— 这正是「未知」的定义。
    """
    if version is None:
        return []
    return [
        source.prompt_name for source in CONTENT_SOURCES
        if source.kind == kind and PROMPT_VERSIONS.get(source.prompt_name) == version
    ]


def _registry_section(
    *,
    card_content: Dict[Optional[str], Dict[str, Any]],
    quiz_content: Dict[Optional[str], Dict[str, Any]],
) -> Dict[str, Any]:
    """登记的三个状态里「与登记表有关」的两个，外加覆盖范围说明"""
    without_data: List[Dict[str, Any]] = []
    for source in CONTENT_SOURCES:
        version = PROMPT_VERSIONS.get(source.prompt_name)
        if version is None:
            # 未登记的提示词写进去的是 NULL（`prompt_version()` 的约定），
            # 那属于「未知」，不构成一个「已登记但没数据」的版本。
            continue
        table = card_content if source.kind == KIND_CARD else quiz_content
        entry = table.get(version) or {}
        if int(entry.get("content_total") or 0) > 0:
            continue
        unknown_rows = int((table.get(None) or {}).get("content_total") or 0)
        if unknown_rows:
            note = (
                f"这张表里有 {unknown_rows} 行版本未知（NULL）的历史内容 —— "
                f"它们**可能**就是这条提示词产出的，因此不能据此断定「这一版没有效果」；"
                f"只有带版本号的新内容入库后才能做版本间比较。"
            )
        else:
            note = (
                "这一版还没有产出任何内容。如果刚升级过这条提示词，"
                "说明这次改动**尚未生效**；如果它一直没被调用过，"
                "那说明对应的功能还没被使用。"
            )
        without_data.append({
            "prompt_name": source.prompt_name,
            "version": version,
            "kind": source.kind,
            "what": source.what,
            "unknown_version_rows_in_table": unknown_rows,
            "note": note,
        })

    registered = _registered_versions()
    unregistered: List[Dict[str, Any]] = []
    for version in set(card_content) | set(quiz_content):
        if version is None or version in registered:
            continue
        cards = card_content.get(version) or {}
        quizzes = quiz_content.get(version) or {}
        unregistered.append({
            "version": version,
            "cards_total": int(cards.get("content_total") or 0),
            "cards_in_window": int(cards.get("content_in_window") or 0),
            "quizzes_total": int(quizzes.get("content_total") or 0),
            "quizzes_in_window": int(quizzes.get("content_in_window") or 0),
            "note": (
                "库里有带这个版本号的内容，但 PROMPT_VERSIONS 里没有任何提示词登记为它 —— "
                "通常意味着数据来自已改名/已删除的登记，或者有人手写过版本号。"
                "这一组**无法解释**，拿它做版本比较的结论不可信。"
            ),
        })
    unregistered.sort(key=lambda item: _parse_version_sort_key(item["version"]))

    covered = {source.prompt_name for source in CONTENT_SOURCES}
    not_covered = [
        {
            "prompt_name": name,
            "version": version,
            "reason": (
                "这条提示词不写 knowledge_cards / quiz_items（例如只用于问答、章节摘要或判分），"
                "本报表无法按版本评估它的效果 —— 这是数据层面的限制，不是「它没有效果」。"
            ),
        }
        for name, version in sorted(PROMPT_VERSIONS.items())
        if name not in covered
    ]

    return {
        "content_sources": [
            {"prompt_name": s.prompt_name, "kind": s.kind, "what": s.what}
            for s in CONTENT_SOURCES
        ],
        "registered_without_data": without_data,
        "unregistered_versions_in_data": unregistered,
        "prompts_not_covered": not_covered,
    }


def _build_notes(
    *,
    buckets: Sequence[Dict[str, Any]],
    registry: Dict[str, Any],
    data_quality: Dict[str, int],
    since: Optional[datetime],
    until: Optional[datetime],
    requested_version: Optional[str],
    matched_versions: int,
) -> List[str]:
    """面向人的中文说明（报表必须自己解释「为什么是这样」）

    一条空报表与一条坏报表在界面上长得一样。这里的文字就是为了让
    「今天只有版本未知这一桶」看起来像一个**答案**而不是一个故障。
    """
    notes: List[str] = []

    def _kind_total(key: str) -> int:
        return sum(int((b[key] or {}).get("content_total") or 0) for b in buckets)

    cards_total = _kind_total("cards")
    quizzes_total = _kind_total("quizzes")
    content_total = cards_total + quizzes_total
    unknown_cards = sum(
        int((b["cards"] or {}).get("content_total") or 0)
        for b in buckets if b["prompt_version"] is None
    )
    unknown_quizzes = sum(
        int((b["quizzes"] or {}).get("content_total") or 0)
        for b in buckets if b["prompt_version"] is None
    )
    unknown_total = unknown_cards + unknown_quizzes

    if requested_version is not None and matched_versions == 0:
        notes.append(
            f"指定的版本「{requested_version}」下没有任何内容。"
            "这不一定是错误：它可能确实还没产出过内容（新版本尚未生效），"
            "也可能只是版本号写错了 —— 请对照 registry 一节里的登记版本号。"
        )

    if content_total == 0:
        # 只有**未过滤**时才说"整个库都是空的"：过滤到某个版本时上面那句
        # 已经解释过了，再补一句"没有可比较的东西"会让人以为库里什么都没了。
        if requested_version is None:
            if data_quality.get("reviews_in_window"):
                notes.append(
                    "当前没有任何卡片或题目，但有复习记录 —— "
                    "它们复习的是窗口之前产出、或已被删除的内容。"
                )
            else:
                notes.append(
                    "当前既没有卡片/题目，也没有复习记录 —— 没有可比较的东西。"
                    "完成一次「理解笔记」之后，这里会出现按提示词版本分组的表现对比。"
                )
    else:
        if unknown_total:
            notes.append(
                f"「版本未知」桶里有 {unknown_total} 条内容（卡片 {unknown_cards} / 题目 {unknown_quizzes}）："
                "它们入库于 4.6 之前，当时没有任何版本记录。**未知不等于第一版** —— "
                "项目刻意没有回填版本号，因为回填会把历史数据混进「第一版的表现」，"
                "让按版本比较的结论失真（与记账里「没配价格就记 NULL 而不是 0」是同一条原则）。"
            )
        if unknown_total == content_total:
            notes.append(
                "目前**全部**内容都落在「版本未知」这一桶里，因此这张报表今天还回答不了"
                "「哪一版更好」。这是数据的真实状态（4.6 之前的历史内容没有版本号），"
                "不是报表出错：等新的理解/出题任务跑过之后，带版本号的内容会出现在这里并开始可比。"
            )

        without_data = registry.get("registered_without_data") or []
        if without_data:
            names = "、".join(f"{e['prompt_name']}→v{e['version']}" for e in without_data)
            notes.append(
                f"有 {len(without_data)} 条提示词已登记版本、但库里还没有带该版本号的内容：{names}。"
                "若你刚升级过其中某条提示词，说明这次改动还没有产出任何内容（**尚未生效**）—— "
                "这正是这个报表最该被用来回答的问题。"
            )

        unregistered = registry.get("unregistered_versions_in_data") or []
        if unregistered:
            versions = "、".join(f"v{e['version']}" for e in unregistered)
            notes.append(
                f"数据里出现了登记表里没有的版本号：{versions}。这种**漂移**必须先解释清楚"
                "（谁写的？从哪来的？），否则按版本分组的结论不可信。"
            )

        # 一个版本号可能被**多条提示词**共用（登记表是"名字 → 版本"，不是反过来）。
        # 此时桶里的内容混着这几条提示词的产出，桶内**无法再区分** ——
        # 不说清楚的话，「v1 表现好」会被误读成「understanding_session 那条好」。
        shared = [
            b["label"] for b in buckets
            if len(set(b["producers"]["cards"]) | set(b["producers"]["quizzes"])) > 1
        ]
        if shared:
            notes.append(
                f"这些版本号被**多条提示词共用**：{'、'.join(shared)}。"
                "桶里的内容是这几条提示词共同产出的，**无法**在桶内区分谁贡献了多少 —— "
                "要按提示词分别评估，必须先让它们的版本号各不相同（见 registry.content_sources）。"
            )

    if data_quality.get("reviews_linked_to_both"):
        notes.append(
            f"窗口内有 {data_quality['reviews_linked_to_both']} 次复习同时挂了卡片与题目，"
            "因此它们**在卡片视图与题目视图里各算一次** —— 两张表的 reviews 不能相加。"
            "两个视图回答的是两个不同的问题：「这一版卡片好不好」与「这一版题目好不好」。"
        )
    if data_quality.get("reviews_unattributable"):
        notes.append(
            f"窗口内有 {data_quality['reviews_unattributable']} 次复习既没有 card_id 也没有 quiz_id，"
            "无法归属到任何版本，**不在任何桶里**（历史遗留数据）—— 各桶之和对不上总复习次数时，"
            "差额来自这里。"
        )

    no_review_versions = [
        b["label"] for b in buckets
        if int((b["cards"] or {}).get("content_total") or 0)
        + int((b["quizzes"] or {}).get("content_total") or 0) > 0
        and int((b["cards"] or {}).get("reviews") or 0)
        + int((b["quizzes"] or {}).get("reviews") or 0) == 0
    ]
    if no_review_versions:
        notes.append(
            f"这些版本有内容但一次都没被复习过：{'、'.join(no_review_versions)}。"
            "它们的通过率是 null（**不是 0%**）：没有样本不等于表现差。"
        )

    if data_quality.get("reviews_in_window"):
        notes.append(
            "通过率、遗忘次数、平均间隔、掌握度都是**代理指标**：它们同时受内容质量、"
            "你的投入程度与调度策略影响，只支持「同一个人、同一时期、不同版本之间」的相对比较，"
            "不能当作某一版提示词的绝对分数。各项含义见 metric_notes。"
        )

    return notes


async def get_prompt_version_report(
    db: AsyncSession,
    *,
    user_id: str,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    version: Optional[str] = None,
) -> Dict[str, Any]:
    """按提示词版本汇总「产出了什么」与「表现如何」

    Args:
        db: 数据库会话
        user_id: **必须显式给出**（且所有查询都按它过滤）。不做「查所有人」的
            默认值：本仓库还没有管理员角色，一个默认放宽的口径迟早会被
            某个调用方原样暴露出去（见 `api/llm.py` 的同一条理由）。
        since / until: 统计窗口（左闭右开）。缺省为最近 `DEFAULT_WINDOW_DAYS` 天。
        version: 只看某一版（`"unknown"` 表示 NULL 那一桶）；缺省看全部。
            过滤**只影响 buckets**：登记表一节讲的是登记与数据的全局事实，
            不该因为「我在看 v2」就消失。

    Returns:
        见模块说明与 `tests/test_prompt_version_report.py`。关键结构：

        - `buckets`：每个版本一桶（含 NULL 桶），桶内有 `cards` / `quizzes`
          两组统计与 `state`（`unknown` / `registered` / `unregistered`）
        - `registry`：三个状态里的登记侧两个 + 覆盖范围说明
        - `data_quality`：分母的交代（无法归属的复习记录等）
        - `notes`：中文说明，报表自己解释「为什么是这样」
        - `metric_notes`：每个指标的含义与局限（代理指标在此声明）

    ## 为什么「没有数据」不抛错

    新用户、新版本、空窗口都会走到「一个桶都没有」。那是**信息**
    （「还没有可比的东西」），不是异常；抛 404/500 只会让使用者以为系统坏了。
    真正的错误入口只有参数本身（例如 `version` 写成了非法值），
    那由 API 层校验。
    """
    until = until or _now()
    since = since if since is not None else until - timedelta(days=DEFAULT_WINDOW_DAYS)

    card_content = await _content_stats(
        db, user_id=user_id, model=KnowledgeCard, since=since, until=until,
        with_mastery=True,
    )
    quiz_content = await _content_stats(
        db, user_id=user_id, model=QuizItem, since=since, until=until,
    )
    card_reviews = await _review_stats(
        db, user_id=user_id, join_model=KnowledgeCard,
        fk_column=ReviewLog.card_id, since=since, until=until,
    )
    quiz_reviews = await _review_stats(
        db, user_id=user_id, join_model=QuizItem,
        fk_column=ReviewLog.quiz_id, since=since, until=until,
    )
    card_states = await _state_stats(
        db, user_id=user_id, join_model=KnowledgeCard, item_type=ITEM_TYPE_CARD,
    )
    quiz_states = await _state_stats(
        db, user_id=user_id, join_model=QuizItem, item_type=ITEM_TYPE_QUIZ,
    )
    data_quality = await _review_link_quality(
        db, user_id=user_id, since=since, until=until,
    )

    versions = (
        set(card_content) | set(quiz_content)
        | set(card_reviews) | set(quiz_reviews)
        | set(card_states) | set(quiz_states)
    )
    registered = _registered_versions()

    buckets: List[Dict[str, Any]] = []
    for item_version in versions:
        # 先只放**原始计数**：折算出来的比例/均值不能跨版本再聚合
        # （见 SUMMABLE 的说明），所以顺序必须是「组装 → 合计 → 折算」。
        cards = _blank_kind_stats(KIND_CARD)
        for source in (card_content, card_reviews, card_states):
            cards.update(source.get(item_version) or {})

        quizzes = _blank_kind_stats(KIND_QUIZ)
        for source in (quiz_content, quiz_reviews, quiz_states):
            quizzes.update(source.get(item_version) or {})

        if item_version is None:
            state = "unknown"
            label = UNKNOWN_VERSION_LABEL
        elif item_version in registered:
            state = "registered"
            label = f"版本 {item_version}"
        else:
            # 数据里有、登记表里没有 —— 与「未知」并列的第三种状态
            state = "unregistered"
            label = f"版本 {item_version}（未登记）"

        buckets.append({
            "prompt_version": item_version,
            "label": label,
            "state": state,
            "registered_prompts": sorted(
                name for name, value in PROMPT_VERSIONS.items() if value == item_version
            ) if item_version is not None else [],
            "producers": {
                "cards": _producers_for(item_version, KIND_CARD),
                "quizzes": _producers_for(item_version, KIND_QUIZ),
            },
            "cards": cards,
            "quizzes": quizzes,
        })

    buckets.sort(key=lambda b: _parse_version_sort_key(b["prompt_version"]))

    requested_version = version
    if version is not None:
        if version == UNKNOWN_VERSION_TOKEN:
            buckets = [b for b in buckets if b["prompt_version"] is None]
        else:
            buckets = [b for b in buckets if b["prompt_version"] == version]
    matched_versions = len(buckets)

    #: 可直接相加的原始计数。**折算指标不在其中** —— 跨版本把百分比或均值
    #: 直接平均会让条目数少的版本被同等加权（例如 1 条内容 100% 与 100 条内容
    #: 0%，平均成 50%），因此它们一律由合计后的原始计数重新折算。
    SUMMABLE = (
        "content_in_window", "content_total", "reviewed_content",
        "reviews", "passed", "tracked_items", "lapses", "interval_sum",
        "mastery_positive",
    )

    def _totals(key: str) -> Dict[str, Any]:
        kind = KIND_CARD if key == "cards" else KIND_QUIZ
        combined = _blank_kind_stats(kind)
        for bucket in buckets:
            stats = bucket[key] or {}
            for field in SUMMABLE:
                if field in combined:
                    combined[field] = int(combined[field]) + int(stats.get(field) or 0)
            if kind == KIND_CARD:
                combined["mastery_sum"] = float(combined["mastery_sum"]) + float(
                    stats.get("mastery_sum") or 0.0
                )
        return _finish_kind_stats(combined, kind)

    # 合计必须在折算**之前**算（折算会 pop 掉 mastery_sum / interval_sum）
    totals = {"cards": _totals("cards"), "quizzes": _totals("quizzes")}
    for bucket in buckets:
        bucket["cards"] = _finish_kind_stats(bucket["cards"], KIND_CARD)
        bucket["quizzes"] = _finish_kind_stats(bucket["quizzes"], KIND_QUIZ)

    registry = _registry_section(card_content=card_content, quiz_content=quiz_content)
    data_quality = dict(data_quality)
    data_quality["unknown_version_cards"] = int(
        (card_content.get(None) or {}).get("content_total") or 0
    )
    data_quality["unknown_version_quizzes"] = int(
        (quiz_content.get(None) or {}).get("content_total") or 0
    )

    notes = _build_notes(
        buckets=buckets,
        registry=registry,
        data_quality=data_quality,
        since=since,
        until=until,
        requested_version=requested_version,
        matched_versions=matched_versions,
    )

    return {
        "since": since,
        "until": until,
        "requested_version": requested_version,
        "unknown_version_label": UNKNOWN_VERSION_LABEL,
        "pass_quality": PASS_QUALITY,
        "buckets": buckets,
        "totals": totals,
        "registry": registry,
        "data_quality": data_quality,
        "metric_notes": dict(METRIC_NOTES),
        "notes": notes,
    }


__all__ = [
    "CONTENT_SOURCES",
    "DEFAULT_WINDOW_DAYS",
    "KIND_CARD",
    "KIND_QUIZ",
    "METRIC_NOTES",
    "PASS_QUALITY",
    "UNKNOWN_VERSION_LABEL",
    "UNKNOWN_VERSION_TOKEN",
    "ContentSource",
    "get_prompt_version_report",
]
