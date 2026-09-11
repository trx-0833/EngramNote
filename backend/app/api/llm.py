"""
LLM 用量与成本接口（overhaul-plan 阶段 4.2）

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
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import get_current_user_dependency
from ..database import get_db
from ..models.user import User
from ..services import llm_accounting_service

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
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int = Field(
        description="命中提示词缓存的 token 数 —— 省钱的主要杠杆"
    )
    cost: float = Field(description="折算金额；见 cost_known_calls")


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
        raise HTTPException(
            status_code=400,
            detail=f"group_by 只能是 {'/'.join(GROUP_BY_CHOICES)} 之一",
        )

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)

    try:
        result = await llm_accounting_service.summarize_usage(
            db, user_id=current_user.id, since=since, until=until, group_by=group_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from ..config import get_settings

    settings = get_settings()
    price_configured = bool(
        getattr(settings, "llm_price_input_per_1m", 0)
        and getattr(settings, "llm_price_output_per_1m", 0)
    )
    currency = (getattr(settings, "llm_price_currency", None) or None) if price_configured else None

    def _to_group(row: Dict[str, Any]) -> UsageGroup:
        return UsageGroup(
            key=row.get("key"),
            calls=int(row.get("calls") or 0),
            failed_calls=int(row.get("failed_calls") or 0),
            cost_known_calls=int(row.get("cost_known_calls") or 0),
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
    )
