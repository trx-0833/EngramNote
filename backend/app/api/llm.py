"""
LLM 用量、成本与提示词版本接口（overhaul-plan 阶段 4.2 / 4.6）

## 为什么要有这个接口

`llm_calls` 表落库之后，"成本可按用户/笔记/场景聚合"这句验收才算成立 ——
但只有表没有查询入口，等于把数据埋起来（这正是附录 Z/AA 反复踩到的
"做完了但用不上"）。

## 为什么只返回**当前用户**的数据

仓库里还没有管理员/多租户角色（`User` 表没有 is_admin），因此这里
**不做**"查所有人"的开关 —— 那会变成一个任何登录用户都能看到别人
消费额度的接口。要看全局成本属于阶段 6 的可观测性工作面（Prometheus
指标 + 内部看板），不是靠把这个接口放宽权限来实现。

## 为什么同时返回 token 与 cost，而且 cost 可能为 0 但不可信

价格是配置项（见 `config.llm_price_*`）。没配价格时 `cost` 记 NULL、聚合为 0，
因此响应里**必须**带上 `cost_known_calls` 与 `calls` 两个计数 ——
只看 `total_cost = 0` 会把"没配价格"读成"这个月没花钱"。

## `/prompt-versions`（阶段 4.6 的收尾）

第二个端点，形状、鉴权、错误约定与 `/usage` 完全一致：同样只看**当前用户**，
同样把"分母/口径"一起返回。它回答的是另一个问题：

    「改了提示词之后，新一版产出的卡片/题目是不是更好？」

4.6 把 `prompt_version` 写进了库，但**没有人读**——列写了没人用等于没做。
这个端点就是那个读取端。它与用量接口放在一起而不是新开一个路由模块，
理由是两者共用同一个前提（都是"让已经落库的元数据真的能被回答一个业务问题"），
且都要面对同一类风险：把"未知（NULL）"渲染成 0。
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import get_current_user_dependency
from ..core.app_error import (
    LLM_USAGE_GROUP_BY_INVALID,
    PROMPT_VERSION_INVALID,
    AppError,
)
from ..database import get_db
from ..models.user import User
from ..services import llm_accounting_service, prompt_version_report_service

logger = logging.getLogger(__name__)
router = APIRouter()

#: 允许的聚合维度。`user` **不在其中**：单用户接口按用户分组只有一个分组，
#: 放进来只会让人以为能查别人。
GROUP_BY_CHOICES = ("scene", "note", "model", "task", "day", "none")


class UsageGroup(BaseModel):
    """一个分组的用量"""
    key: Optional[str] = Field(default=None, description="分组值；`day` 为日期字符串")
    calls: int = Field(description="调用次数（含失败）")
    failed_calls: int = Field(description="其中的失败次数")
    cost_known_calls: int = Field(
        description="**有价格**的调用数。它小于 calls 时，cost 是不完整的"
    )
    cached_calls: int = Field(
        default=0,
        description=(
            "命中响应缓存的次数（阶段 4.7）。这些调用**没有花钱**："
            "它们的 total_tokens 与 cost 都记 0 —— 若把原始用量记下来，"
            "配额会把没花的钱算进去，变成「开了缓存反而更快被限流」"
        ),
    )
    saved_tokens: int = Field(
        default=0, description="命中缓存省下的 token 数 —— 判断缓存值不值得继续开"
    )
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int = Field(
        description="命中**供应商侧提示词缓存**的 token 数（与上面的响应缓存是两回事）"
    )
    cost: float = Field(description="折算金额；见 cost_known_calls")


class QuotaInfo(BaseModel):
    """当前业务日的配额状态（阶段 4.3）"""
    token_limit: int = Field(default=0, description="每日 token 上限；0 = 不限")
    cost_limit: float = Field(default=0.0, description="每日金额上限；0 = 不限")
    tokens_used: int = 0
    cost_used: float = 0.0
    exceeded: bool = False
    reason: Optional[str] = Field(
        default=None, description="超限原因（中文，可直接展示）"
    )
    cost_enforceable: bool = Field(
        default=True,
        description=(
            "金额上限是否**真的**在生效。为 false 表示配了金额上限但没配单价 ——"
            "此时它静默失效，只按 token 配额拦截。界面必须据此提示"
        ),
    )


class UsageResponse(BaseModel):
    since: datetime
    until: datetime
    group_by: str
    currency: Optional[str] = Field(
        default=None, description="未配置价格时为 null（此时 cost 恒为 0，不可当真）"
    )
    totals: UsageGroup
    groups: List[UsageGroup] = Field(
        default_factory=list, description="group_by=none 时为空列表"
    )
    price_configured: bool = Field(
        description=(
            "是否配置了单价。为 false 时 `cost` 一律为 0，"
            "**不代表没花钱** —— 界面必须据此提示，不能直接显示 0 元"
        )
    )
    quota: QuotaInfo = Field(
        default_factory=QuotaInfo,
        description="**今日**配额状态；与上面的 `days` 窗口是两个不同口径",
    )
    cache: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "响应缓存自身的情况（阶段 4.7）：`{enabled, entries, hits, "
            "stored_tokens, entries_ever_hit}`。不按用户分 —— 缓存是全局的"
            "（key 已含完整输入，同一输入本就该得到同一份回答）"
        ),
    )


@router.get("/usage", response_model=UsageResponse)
async def get_llm_usage(
    days: int = Query(30, ge=1, le=3650, description="统计最近多少天"),
    group_by: str = Query("scene", description=f"聚合维度：{'/'.join(GROUP_BY_CHOICES)}"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """当前用户的 LLM 用量与花费（阶段 4.2）

    返回总量 +（可选）按维度分组的明细。默认看最近 30 天、按场景分组 ——
    这是最常见的问题形态："我这个月在哪些场景上花得最多"。
    """
    if group_by not in GROUP_BY_CHOICES:
        raise AppError(
            LLM_USAGE_GROUP_BY_INVALID,
            f"group_by 只能是 {'/'.join(GROUP_BY_CHOICES)} 之一",
            400,
        )

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)

    try:
        result = await llm_accounting_service.summarize_usage(
            db, user_id=current_user.id, since=since, until=until, group_by=group_by,
        )
    except ValueError as exc:
        # 服务层的防御性重复校验（同一个维度白名单），语义与上面一致
        raise AppError(LLM_USAGE_GROUP_BY_INVALID, str(exc), 400) from exc

    from ..config import get_settings

    settings = get_settings()
    price_configured = bool(
        getattr(settings, "llm_price_input_per_1m", 0)
        and getattr(settings, "llm_price_output_per_1m", 0)
    )
    currency = (getattr(settings, "llm_price_currency", None) or None) if price_configured else None

    # 配额是**当日**口径，与请求里的 `days` 窗口不同 —— 两个数字并列出现时
    # 必须各自带说明，否则"用了 100 万 token / 上限 50 万"会让人以为
    # 是拿整个窗口去比当天的额度。
    quota_status = await llm_accounting_service.check_quota(current_user.id, settings=settings)

    # 缓存统计是**全局**的（见字段说明），与上面的用户维度数据并列返回时
    # 必须带上 `enabled`，否则"命中 0 次"会被读成"缓存没起作用"，
    # 而真实原因可能是开关是关的。
    try:
        from ..services import llm_cache_service

        cache_stats = await llm_cache_service.stats(db)
        cache_stats["enabled"] = bool(getattr(settings, "llm_cache_enabled", True))
    except Exception as exc:  # noqa: BLE001 - 统计失败不该让用量接口整个失败
        logger.warning("读取缓存统计失败（不影响用量报表）: %s", exc)
        cache_stats = {"enabled": bool(getattr(settings, "llm_cache_enabled", True))}

    def _to_group(row: Dict[str, Any]) -> UsageGroup:
        return UsageGroup(
            key=row.get("key"),
            calls=int(row.get("calls") or 0),
            failed_calls=int(row.get("failed_calls") or 0),
            cost_known_calls=int(row.get("cost_known_calls") or 0),
            cached_calls=int(row.get("cached_calls") or 0),
            saved_tokens=int(row.get("saved_tokens") or 0),
            prompt_tokens=int(row.get("prompt_tokens") or 0),
            completion_tokens=int(row.get("completion_tokens") or 0),
            total_tokens=int(row.get("total_tokens") or 0),
            cached_tokens=int(row.get("cached_tokens") or 0),
            cost=float(row.get("cost") or 0.0),
        )

    return UsageResponse(
        since=since,
        until=until,
        group_by=group_by,
        currency=currency,
        totals=_to_group(result["totals"]),
        groups=[_to_group(g) for g in result["groups"]],
        price_configured=price_configured,
        quota=QuotaInfo(
            token_limit=quota_status.token_limit,
            cost_limit=quota_status.cost_limit,
            tokens_used=quota_status.tokens_used,
            cost_used=quota_status.cost_used,
            exceeded=quota_status.exceeded,
            reason=quota_status.reason,
            cost_enforceable=quota_status.cost_enforceable,
        ),
        cache=cache_stats,
    )


# ---------------------------------------------------------------------------
# 提示词版本效果（阶段 4.6 的收尾）
# ---------------------------------------------------------------------------

#: `version` 查询参数的合法形状：`unknown`（NULL 那一桶）或正整数字符串。
#:
#: 用 `[0-9]` 而不是 `\d`：`\d` 在 Python 里匹配 Unicode 数字（如 "١٢"），
#: 那种"数字"永远不可能出现在版本号列里，放行只会得到一个静默的空报表。
_VERSION_PATTERN = re.compile(r"(?:unknown|[0-9]+)")


class VersionContentStats(BaseModel):
    """某一版内容在**一张表**上的产出与表现

    ⚠️ 每个比例/均值的"无数据"都是 `null`，不是 0：
    「没有复习」与「复习了但全错」必须能分开，否则 0% 会被读成"这一版很差"。
    """
    content_in_window: int = Field(description="窗口内新增的内容条数（左闭右开）")
    content_total: int = Field(
        description="该版本累计产出的内容条数（**不受窗口限制**）—— 用来区分「停产」与「从没有过」"
    )
    reviewed_content: int = Field(description="窗口内至少被复习过一次的内容条数（去重）")
    reviews: int = Field(description="窗口内的复习次数")
    passed: int = Field(description="其中 quality >= pass_quality 的次数")
    pass_rate_percent: Optional[float] = Field(
        default=None,
        description=(
            "**代理指标**：passed / reviews × 100，含义是「调度器认为你记住了」的比例。"
            "一次复习都没有时为 null（**不是 0%**）"
        ),
    )
    tracked_items: int = Field(description="有 review_states 行的内容数（复习过才会建状态行）")
    lapses: int = Field(description="**代理指标**：review_states.lapses 之和（累计遗忘次数）")
    lapses_per_item: Optional[float] = Field(
        default=None, description="**代理指标**：lapses / tracked_items"
    )
    avg_interval_days: Optional[float] = Field(
        default=None,
        description=(
            "**代理指标**：调度器给出的复习间隔均值（越长说明它认为你记得越牢）。"
            "是**当前值**，不是窗口内的历史值"
        ),
    )


class CardVersionStats(VersionContentStats):
    """卡片统计（比题目多掌握度：`knowledge_cards.mastery_level`）"""
    avg_mastery: Optional[float] = Field(
        default=None,
        description=(
            "**代理指标**：mastery_level 均值（0-100）。**未复习过的卡片按 0 计入**"
            "（公式如此），因此要配合 mastery_positive 一起看"
        ),
    )
    mastery_positive: int = Field(
        default=0, description="mastery_level > 0 的卡片数 —— 用来识别均值被「从未复习」拉低"
    )


class QuizVersionStats(VersionContentStats):
    """题目统计（题目表没有掌握度字段，因此这里没有 avg_mastery）"""


class PromptVersionBucket(BaseModel):
    """一个版本号一桶（`prompt_version=null` 即「版本未知」那一桶）"""
    prompt_version: Optional[str] = Field(
        default=None, description="null = 4.6 之前的历史行，版本**未知**（不等于「第一版」）"
    )
    label: str = Field(description="中文标签，可直接展示")
    state: Literal["unknown", "registered", "unregistered"] = Field(
        description=(
            "三种必须分开的状态：`unknown`（NULL 历史）｜"
            "`registered`（该版本号在 PROMPT_VERSIONS 里）｜"
            "`unregistered`（数据里有、登记表里没有 = 漂移）"
        )
    )
    registered_prompts: List[str] = Field(
        default_factory=list, description="登记为该版本号的提示词名（可能不止一个）"
    )
    producers: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="`{cards: [...], quizzes: [...]}`：**可能产出该内容**的提示词名（空列表 = 没有提示词能解释它）",
    )
    cards: CardVersionStats
    quizzes: QuizVersionStats


class ContentSourceInfo(BaseModel):
    """一条会往卡片/题目表写行的提示词（报表的覆盖范围）"""
    prompt_name: str
    kind: Literal["card", "quiz"]
    what: str


class RegisteredWithoutData(BaseModel):
    """状态一：**已登记但还没有数据**（刚升版本时最可操作的信号）"""
    prompt_name: str
    version: str
    kind: Literal["card", "quiz"]
    what: str
    unknown_version_rows_in_table: int = Field(
        description=(
            "同一张表里版本未知（NULL）的行数。>0 时**不能**断定「这一版没有效果」——"
            "那些历史行可能正是它产出的"
        )
    )
    note: str = Field(description="中文说明，可直接展示")


class UnregisteredVersion(BaseModel):
    """状态二：**数据里有、登记表里没有**（版本号漂移）"""
    version: str
    cards_total: int
    cards_in_window: int
    quizzes_total: int
    quizzes_in_window: int
    note: str


class UncoveredPrompt(BaseModel):
    """登记了、但**不产出卡片/题目**的提示词（本报表无法评估它的版本效果）"""
    prompt_name: str
    version: str
    reason: str


class PromptVersionRegistry(BaseModel):
    """登记表的三个状态里「登记侧」的两个，外加覆盖范围的自我交代"""
    content_sources: List[ContentSourceInfo] = Field(
        description="会往 knowledge_cards / quiz_items 写行的提示词（只有这些能被本报表评估）"
    )
    registered_without_data: List[RegisteredWithoutData] = Field(
        default_factory=list, description="已登记但（在本账号范围内）还没有数据"
    )
    unregistered_versions_in_data: List[UnregisteredVersion] = Field(
        default_factory=list, description="数据里出现、登记表里没有的版本号"
    )
    prompts_not_covered: List[UncoveredPrompt] = Field(
        default_factory=list,
        description="不写这两张表的提示词 —— 它们的效果**无法**用本报表衡量（是限制，不是「没效果」）",
    )


class PromptVersionTotals(BaseModel):
    """合计（按原始计数重新折算，**不是**把各版本的百分比平均）"""
    cards: CardVersionStats
    quizzes: QuizVersionStats


class PromptVersionDataQuality(BaseModel):
    """分母的交代：哪些数据进不了任何桶，以及桶之间的重叠"""
    reviews_in_window: int = Field(description="窗口内的复习记录总数（当前用户）")
    reviews_unattributable: int = Field(
        description="其中既无 card_id 也无 quiz_id 的条数 —— **进不了任何版本桶**，各桶之和对不上总数时差额在这里"
    )
    reviews_linked_to_both: int = Field(
        description="同时挂了卡片与题目的条数 —— 卡片视图与题目视图会**各算一次**，因此两张表的 reviews 不能相加"
    )
    unknown_version_cards: int = Field(description="版本未知（NULL）的卡片总数")
    unknown_version_quizzes: int = Field(description="版本未知（NULL）的题目总数")


class PromptVersionReportResponse(BaseModel):
    """提示词版本效果报表（阶段 4.6）"""
    since: datetime
    until: datetime
    days: int = Field(description="窗口天数（since 由它算出）")
    requested_version: Optional[str] = Field(
        default=None, description="请求里指定的版本过滤；null = 未过滤"
    )
    unknown_version_label: str = Field(description="NULL 版本的中文标签（**不等于第一版**）")
    pass_quality: int = Field(description="通过阈值（quality >= 该值算通过）")
    buckets: List[PromptVersionBucket] = Field(
        default_factory=list,
        description=(
            "每个版本一桶，版本号从新到旧，**版本未知永远排最后**。"
            "空列表是**信息**（还没有可比的内容），不是错误"
        ),
    )
    totals: PromptVersionTotals = Field(description="**受 version 过滤影响**的合计")
    registry: PromptVersionRegistry
    data_quality: PromptVersionDataQuality
    metric_notes: Dict[str, str] = Field(
        default_factory=dict, description="每个指标的含义与局限（代理指标在此声明）"
    )
    notes: List[str] = Field(
        default_factory=list,
        description="中文说明：报表自己解释「为什么是这样」（空库、全未知、漂移、代理指标…）",
    )


@router.get("/prompt-versions", response_model=PromptVersionReportResponse)
async def get_prompt_version_report(
    days: int = Query(30, ge=1, le=3650, description="统计最近多少天"),
    version: Optional[str] = Query(
        None,
        description=(
            "只看某一版；`unknown` 表示版本未知（NULL）那一桶。"
            "缺省看全部。HTTP 查不了 NULL，因此 NULL 必须有一个显式词"
        ),
    ),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """当前用户的「提示词版本 → 内容表现」报表（阶段 4.6）

    回答的问题是：**改了提示词之后，新一版产出的内容是不是更好？**

    内容产出量按 `created_at` 落在窗口内计；复习表现按 `review_logs.review_at`
    落在窗口内计（内容是窗口之前产出的也照样算 —— 「这些内容后来表现如何」
    正是要看的部分）。两个时间口径都写在字段说明里，不让人猜。

    ⚠️ 今天真库里的内容**全部没有版本号**，因此这里会返回**一个**
    `state="unknown"` 的桶 —— 那是数据的真实状态（历史行刻意不回填），
    `notes` 会直接把这件事讲清楚；调用方**不要**把 NULL 渲染成"第一版"。
    """
    if version is not None and not _VERSION_PATTERN.fullmatch(version):
        raise AppError(
            PROMPT_VERSION_INVALID,
            (
                "version 只能是 `unknown`（版本未知那一桶）或纯数字版本号"
                f"（如 1 / 2），收到 {version!r}"
            ),
            400,
        )

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)

    result = await prompt_version_report_service.get_prompt_version_report(
        db, user_id=current_user.id, since=since, until=until, version=version,
    )

    return PromptVersionReportResponse(
        since=result["since"],
        until=result["until"],
        days=days,
        requested_version=result["requested_version"],
        unknown_version_label=result["unknown_version_label"],
        pass_quality=result["pass_quality"],
        buckets=[PromptVersionBucket(**bucket) for bucket in result["buckets"]],
        totals=PromptVersionTotals(
            cards=CardVersionStats(**result["totals"]["cards"]),
            quizzes=QuizVersionStats(**result["totals"]["quizzes"]),
        ),
        registry=PromptVersionRegistry(**result["registry"]),
        data_quality=PromptVersionDataQuality(**result["data_quality"]),
        metric_notes=result["metric_notes"],
        notes=result["notes"],
    )
