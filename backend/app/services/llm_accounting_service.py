"""
LLM 调用记账服务（overhaul-plan 阶段 4.2）

## 这一层解决什么

`llm_service` 每次调用完都会打一行日志，但日志**不能聚合**：
"这个月花了多少""哪篇笔记最贵""上缓存之后省了多少"都答不出来。
本模块把每次调用落成一行 `llm_calls`，让成本可以按用户/笔记/场景/模型聚合。

## 三个关键设计

### 1. 记账写自己的会话，不蹭调用方的事务

`record_call` 用**独立的短生命周期会话**写入。理由不是性能，而是语义：

- LLM 调用**已经发生**、钱**已经花掉**。若记账挂在调用方事务上，
  调用方一旦回滚（比如答题因为别的原因失败），这笔开销就凭空消失，
  账目变成"只统计成功业务"的数字 —— 而成本管理的意义恰恰在于
  **知道钱花在哪，包括白花的那些**。
- 反向的耦合同样要避免：记账失败**不得**让用户的请求失败。
  因此这里吞掉自身异常并打 WARNING。

⚠️ 这不是"静默降级"：记账是**旁路**，它的失败会在日志里以 WARNING 出现，
且聚合接口永远不会替它编数字（没记上就是没记上）。
把用户的答题失败掉，只是因为账本写不进去 —— 那是更糟的取舍。

### 2. 价格是配置，不是代码里的常量

`cost` 的计算依赖供应商单价，而单价会变、也因模型而异。本项目**不内置价格表**：

    llm_price_input_per_1m  / llm_price_output_per_1m

都没配（默认）时，`cost` 记 **NULL**。"0 元"与"价格未知"在报表里必须可区分 ——
把未知记成 0，会让"没配价格"看起来像"完全免费"。

### 3. 上下文是环境变量式的，而不是层层传参

`user_id` / `note_id` 不是 LLM 调用的语义参数（模型不需要知道给谁用），
把两个参数加到 `chat()` / `chat_detailed()` 及其 15 个包装方法上，
会让每次签名变更都波及一大片调用方，而且很容易漏。

改用 `contextvars`：

    with llm_context(user_id=uid, note_id=nid, task="understand_note"):
        await service.extract_knowledge_points(...)

它在 `async` 与线程里都能正确隔离，且**没设置上下文时照样记账**
（`user_id` 为 NULL）—— 宁可少一个维度，也不能漏记一笔。
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.llm_call import LLMCall

logger = logging.getLogger(__name__)

#: 错误信息写库前的截断长度。堆栈可能很长，而记账表的用途是聚合与定位，
#: 不是完整日志 —— 完整堆栈已经在 logger 里了。
ERROR_MAX_LEN = 500


@dataclass(frozen=True)
class LLMContext:
    """一次 LLM 调用的来源上下文（谁、为了哪篇笔记、由什么触发）"""
    user_id: Optional[str] = None
    note_id: Optional[str] = None
    task: Optional[str] = None


#: 当前上下文。默认 None（表示"没人声明过"），而不是空对象 ——
#: 两者在排查时含义不同：None 意味着调用点还没接线。
_current: ContextVar[Optional[LLMContext]] = ContextVar("llm_call_context", default=None)


@contextmanager
def llm_context(
    *,
    user_id: Optional[str] = None,
    note_id: Optional[str] = None,
    task: Optional[str] = None,
) -> Iterator[None]:
    """在这段作用域内声明的 LLM 调用上下文

    可以嵌套：内层未提供的字段会**继承外层**。这样"整个任务属于谁"与
    "这一步属于哪篇笔记"能分开声明，而不必在每个调用点重复三遍。

    用法::

        with llm_context(user_id=uid, task="understand_note"):
            ...
            with llm_context(note_id=nid):
                await service.extract_knowledge_points(...)
    """
    outer = _current.get()
    merged = LLMContext(
        user_id=user_id if user_id is not None else (outer.user_id if outer else None),
        note_id=note_id if note_id is not None else (outer.note_id if outer else None),
        task=task if task is not None else (outer.task if outer else None),
    )
    token = _current.set(merged)
    try:
        yield
    finally:
        _current.reset(token)


def current_context() -> LLMContext:
    """取当前上下文；未声明时返回全空（**不抛错**）"""
    return _current.get() or LLMContext()


def _usage_int(usage: Dict[str, Any], *keys: str) -> Optional[int]:
    """从 usage 里取第一个存在的整数值

    不同供应商的字段名不一样（`prompt_tokens` / `input_tokens`，
    `prompt_cache_hit_tokens` / `cached_tokens`），逐个尝试比写死一个更稳。
    """
    for key in keys:
        value = usage.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def estimate_cost(
    *,
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
    settings=None,
) -> tuple[Optional[float], Optional[str]]:
    """按配置的单价折算金额

    Returns:
        `(cost, currency)`；任一单价未配置（为 None 或 <= 0）时返回 `(None, None)`
        —— **不返回 0**，理由见模块说明。
    """
    if settings is None:
        from ..config import get_settings
        settings = get_settings()

    in_price = getattr(settings, "llm_price_input_per_1m", None)
    out_price = getattr(settings, "llm_price_output_per_1m", None)
    if not in_price or not out_price:
        return None, None

    prompt = prompt_tokens or 0
    completion = completion_tokens or 0
    cost = (prompt / 1_000_000) * float(in_price) + (completion / 1_000_000) * float(out_price)
    currency = getattr(settings, "llm_price_currency", None) or None
    return round(cost, 6), currency


async def record_call(
    *,
    scene: Optional[str],
    provider: Optional[str],
    model: Optional[str],
    usage: Optional[Dict[str, Any]] = None,
    latency_ms: Optional[float] = None,
    success: bool = True,
    error: Optional[str] = None,
    context: Optional[LLMContext] = None,
    session_factory=None,
    settings=None,
    cached: bool = False,
    saved_tokens: Optional[int] = None,
) -> None:
    """记下一次 LLM 调用

    **本函数不抛异常**：记账是旁路，写不进去只打 WARNING（理由见模块说明）。

    Args:
        scene / provider / model: 来自 `llm_service` 的调用信息
        usage: 供应商返回的 usage 字典（字段名各家不同，见 `_usage_int`）
        latency_ms: 调用耗时
        success: 成功与否；失败同样记账
        error: 失败原因（会截断）
        context: 覆盖上下文；缺省取 `current_context()`
        session_factory: 覆盖会话工厂（测试用）
        settings: 覆盖配置（决定单价；测试用）
        cached: 本次是否命中响应缓存（阶段 4.7）。命中时**没有花钱**，
            因此无论 `usage` 传什么，`total_tokens` 与 `cost` 都记 0 / NULL ——
            否则配额（按 `total_tokens` 求和）会把没花的钱算进去，
            变成"开了缓存反而更快被限流"
        saved_tokens: 命中时省下的 token 数（记在单独一列）
    """
    try:
        usage = usage or {}
        if cached:
            # 命中缓存 = 没有向供应商发请求 = 没有产生任何用量与费用。
            # 这里**不看 usage**，因为调用方出于方便很可能把原始用量传进来，
            # 而记下它会让配额与成本报表双双失真。
            prompt_tokens = completion_tokens = total_tokens = 0
            cached_tokens = None
            cost, currency = None, None
        else:
            prompt_tokens = _usage_int(usage, "prompt_tokens", "input_tokens")
            completion_tokens = _usage_int(usage, "completion_tokens", "output_tokens")
            total_tokens = _usage_int(usage, "total_tokens")
            if total_tokens is None and (prompt_tokens or completion_tokens):
                total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
            # 命中缓存的 token：DeepSeek 用 prompt_cache_hit_tokens，
            # 其他供应商可能叫 cached_tokens
            cached_tokens = _usage_int(
                usage, "prompt_cache_hit_tokens", "cached_tokens",
            )
            cost, currency = estimate_cost(
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                settings=settings,
            )

        ctx = context if context is not None else current_context()

        if session_factory is None:
            from ..database import get_session_factory
            session_factory = get_session_factory()

        row = LLMCall(
            user_id=ctx.user_id,
            note_id=ctx.note_id,
            task=ctx.task,
            scene=scene,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            cost=cost,
            currency=currency,
            latency_ms=int(latency_ms) if latency_ms is not None else None,
            success=bool(success),
            error=(str(error)[:ERROR_MAX_LEN] if error else None),
            cached=bool(cached),
            saved_tokens=saved_tokens if cached else None,
            created_at=datetime.now(timezone.utc),
        )
        async with session_factory() as db:
            db.add(row)
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - 记账绝不能影响调用本身
        logger.warning(
            "LLM 记账写入失败（不影响调用本身）: scene=%s model=%s error=%s",
            scene, model, exc,
        )


async def purge_old_calls(
    db: AsyncSession,
    *,
    retention_days: int,
    now: Optional[datetime] = None,
) -> int:
    """删除超过保留期的记账行，返回删除行数（附录 AF.10 那个"没人调用"的清理）

    ## 为什么需要它

    `llm_calls` 只增不减：每次调用写一行，一天几百行就是一年十几万行。
    表本身不会让程序出错，但它会让"按时间聚合花费"越来越慢，
    而且磁盘是有限的（本项目曾因空间不足放弃 PG/Redis）。

    ## `retention_days <= 0` 表示**永久保留**

    这是默认以外的显式选择：账本是"花了多少钱"的唯一记录，
    删掉就再也算不出来。想无限保留就配 0 —— 但要知道代价是表会一直长。

    ## ⚠️ 删的是**历史成本数据**，不是缓存

    与 `llm_cache_service.purge_expired`（删的是可再生的派生数据）不同，
    这里删掉的每一行都不可恢复。因此：

    - 默认保留 365 天（`llm_call_retention_days`），够回答"去年同期花了多少"；
    - 调用方（定时任务）必须把删除行数打进日志 ——
      "某天突然少了一年的账"是必须能从日志里看出来的事。
    """
    if retention_days <= 0:
        return 0

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    result = await db.execute(
        delete(LLMCall).where(LLMCall.created_at < cutoff)
    )
    await db.commit()
    return int(result.rowcount or 0)


async def summarize_usage(
    db: AsyncSession,
    *,
    user_id: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    group_by: str = "scene",
) -> Dict[str, Any]:
    """聚合用量与花费

    Args:
        db: 数据库会话
        user_id: 只看该用户；None 表示全部（调用方负责权限）
        since / until: 时间范围（左闭右开）
        group_by: `scene` | `note` | `model` | `task` | `day` | `none`

    Returns:
        `{"totals": {...}, "groups": [{"key": ..., "calls": ..., ...}]}`

    ## cost 的聚合必须能表达"部分未知"

    `cost` 为 NULL 的行按 0 求和，但同时返回 `cost_known_calls`（有价格的行数）
    与 `calls`（总行数）。只看 `total_cost` 会把"价格未知"读成"没花钱"，
    两个计数并列才说得清。
    """
    filters = []
    if user_id:
        filters.append(LLMCall.user_id == user_id)
    if since:
        filters.append(LLMCall.created_at >= since)
    if until:
        filters.append(LLMCall.created_at < until)

    key_map = {
        "scene": LLMCall.scene,
        "note": LLMCall.note_id,
        "model": LLMCall.model,
        "task": LLMCall.task,
    }

    def _base_columns():
        return [
            func.count().label("calls"),
            func.count(case((LLMCall.success.is_(False), 1))).label("failed_calls"),
            func.count(case((LLMCall.cost.is_not(None), 1))).label("cost_known_calls"),
            # 缓存命中单独计数：命中次数与"省下的 token"是判断缓存值不值得
            # 继续开下去的全部依据（阶段 4.7）
            func.count(case((LLMCall.cached.is_(True), 1))).label("cached_calls"),
            func.coalesce(func.sum(LLMCall.saved_tokens), 0).label("saved_tokens"),
            func.coalesce(func.sum(LLMCall.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(LLMCall.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(LLMCall.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(LLMCall.cached_tokens), 0).label("cached_tokens"),
            func.coalesce(func.sum(LLMCall.cost), 0.0).label("cost"),
        ]

    totals_row = (await db.execute(
        select(*_base_columns()).select_from(LLMCall).where(*filters)
    )).one()
    totals = dict(totals_row._mapping)

    groups: List[Dict[str, Any]] = []
    if group_by != "none":
        if group_by == "day":
            # SQLite 与 PostgreSQL 都能做的日粒度：把时间戳截到日期。
            # `func.date()` 在两边都可用，且不受时区设置影响（存的是 UTC）。
            key = func.date(LLMCall.created_at)
        else:
            column = key_map.get(group_by)
            if column is None:
                raise ValueError(
                    f"不支持的 group_by={group_by!r}，可选："
                    f"{', '.join(list(key_map) + ['day', 'none'])}"
                )
            key = column
        rows = (await db.execute(
            select(key.label("key"), *_base_columns())
            .select_from(LLMCall).where(*filters)
            .group_by(key)
            .order_by(func.count().desc())
        )).all()
        groups = [dict(r._mapping) for r in rows]

    return {"totals": totals, "groups": groups}


def default_since(days: int = 30) -> datetime:
    """默认统计窗口：最近 N 天（UTC 起算）"""
    return datetime.now(timezone.utc) - timedelta(days=days)


# ---------------------------------------------------------------------------
# 配额（overhaul-plan 阶段 4.3）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QuotaStatus:
    """当前用户在**当前业务日**内的配额状态

    Attributes:
        token_limit: 每日 token 上限；0 = 不限
        cost_limit: 每日金额上限；0 = 不限
        tokens_used / cost_used: 已用量
        cost_enforceable: 是否**真的**能按金额拦截（价格未配置时为 False）
        exceeded: 是否已超限
        reason: 超限原因（中文，可直接展示给用户）
    """
    token_limit: int = 0
    cost_limit: float = 0.0
    tokens_used: int = 0
    cost_used: float = 0.0
    cost_enforceable: bool = True
    exceeded: bool = False
    reason: Optional[str] = None

    @property
    def unlimited(self) -> bool:
        return self.token_limit <= 0 and self.cost_limit <= 0

    @property
    def ratio(self) -> float:
        """用量占配额的比例（取两个维度里更大的那个）；不限时为 0"""
        ratios = []
        if self.token_limit > 0:
            ratios.append(self.tokens_used / self.token_limit)
        if self.cost_limit > 0 and self.cost_enforceable:
            ratios.append(self.cost_used / self.cost_limit)
        return max(ratios) if ratios else 0.0


class LLMQuotaExceeded(Exception):
    """配额耗尽 —— 拒绝发起这次 LLM 调用（阶段 4.3）

    ## 为什么是一个**有类型**的异常，而不是返回 None 或空字符串

    "配额用完了"必须与"模型答不出来"区分开：

    - 前者是**用户的账单状态**，应当提示"今天额度已用完，明天再来"；
    - 后者是**系统/模型问题**，应当提示"稍后重试"。

    如果这里返回一个空答案，用户看到的是"AI 什么都没说"，
    而真正的原因是额度用完了 —— 他会一直重试，每次都被拒，
    却永远得不到解释。这正是原则 P7（失败必须响亮）要防的形态。

    `code` 是**稳定错误码**（不随文案变化），供客户端判断；
    与 `main.py` 的 `{detail, error_code, request_id}` 契约一致。
    """
    code = "LLM_QUOTA_EXCEEDED"
    status_code = 429

    def __init__(self, status: QuotaStatus):
        self.status = status
        super().__init__(status.reason or "LLM 配额已用完")

    @property
    def detail(self) -> str:
        return self.status.reason or "LLM 配额已用完"


#: 已就"成本配额无法执行"告过警的配置组合（进程内去重，避免每次调用刷屏）
_COST_QUOTA_WARNED = False


def reset_quota_warnings() -> None:
    """清空告警去重状态（测试用）"""
    global _COST_QUOTA_WARNED
    _COST_QUOTA_WARNED = False


async def check_quota(
    user_id: Optional[str],
    *,
    settings=None,
    session_factory=None,
) -> QuotaStatus:
    """查当前用户在**当前业务日**的配额状态

    ## 不限配额时**不碰数据库**

    默认配置是两项都为 0（不限），此时函数在第一行就返回 ——
    热路径上不产生任何查询。只有真的配了配额才会去统计用量，
    因此"没开这个功能"不会让每次 LLM 调用多一次 DB 往返。

    ## 成本配额在价格未配置时**无法执行**，而且必须说出来

    只配了 `llm_daily_cost_quota` 而没配单价时，金额永远算不出来，
    这个上限会变成**一个静默失效的保护**：用户以为设了上限，
    实际毫无作用。这里的选择是：照常统计 token，把
    `cost_enforceable` 置为 False，并打一次 WARNING
    —— 而不是假装它在生效。

    ## 没有 user_id 时返回"不限"

    无法归属的调用（见 `record_call` 的说明）**不能**被配额拦住 ——
    按谁算都不知道。这是已知缺口，`current_context()` 没接线的调用点
    因此不受配额保护。
    """
    if not user_id:
        return QuotaStatus()

    if settings is None:
        from ..config import get_settings
        settings = get_settings()

    token_limit = int(getattr(settings, "llm_daily_token_quota", 0) or 0)
    cost_limit = float(getattr(settings, "llm_daily_cost_quota", 0) or 0)
    if token_limit <= 0 and cost_limit <= 0:
        return QuotaStatus()

    in_price = getattr(settings, "llm_price_input_per_1m", None)
    out_price = getattr(settings, "llm_price_output_per_1m", None)
    price_configured = bool(in_price and out_price)

    cost_enforceable = True
    if cost_limit > 0 and not price_configured:
        global _COST_QUOTA_WARNED
        cost_enforceable = False
        if not _COST_QUOTA_WARNED:
            _COST_QUOTA_WARNED = True
            logger.warning(
                "配置了 llm_daily_cost_quota=%.2f 但未配置单价"
                "（llm_price_input_per_1m / llm_price_output_per_1m）——"
                "金额上限**无法执行**，本次只按 token 配额拦截。"
                "要么补上单价，要么把这个上限设为 0，不要留一个静默失效的保护。",
                cost_limit,
            )

    from ..utils.timeutil import today_start_utc

    if session_factory is None:
        from ..database import get_session_factory
        session_factory = get_session_factory()

    async with session_factory() as db:
        usage = await summarize_usage(
            db, user_id=user_id, since=today_start_utc(), group_by="none",
        )
    totals = usage["totals"]
    tokens_used = int(totals.get("total_tokens") or 0)
    cost_used = float(totals.get("cost") or 0.0)

    status = QuotaStatus(
        token_limit=token_limit,
        cost_limit=cost_limit if cost_enforceable else 0.0,
        tokens_used=tokens_used,
        cost_used=cost_used,
        cost_enforceable=cost_enforceable,
    )

    reasons = []
    if token_limit > 0 and tokens_used >= token_limit:
        reasons.append(f"今日 token 已用 {tokens_used}/{token_limit}")
    if cost_enforceable and cost_limit > 0 and cost_used >= cost_limit:
        reasons.append(f"今日花费已用 {cost_used:.4f}/{cost_limit:.2f}")
    if not reasons:
        return status

    return QuotaStatus(
        token_limit=status.token_limit,
        cost_limit=status.cost_limit,
        tokens_used=tokens_used,
        cost_used=cost_used,
        cost_enforceable=cost_enforceable,
        exceeded=True,
        reason="；".join(reasons) + "。配额按北京时间的自然日重置，明天可继续使用。",
    )


__all__ = [
    "ERROR_MAX_LEN",
    "LLMContext",
    "LLMQuotaExceeded",
    "QuotaStatus",
    "check_quota",
    "current_context",
    "default_since",
    "estimate_cost",
    "llm_context",
    "purge_old_calls",
    "record_call",
    "reset_quota_warnings",
    "summarize_usage",
]
