"""
LLM 响应缓存服务（overhaul-plan 阶段 4.7）

## 命中判定是**逐字节相同**，不是"相似"

key = sha256(provider + base_url + model + messages + temperature
             + max_tokens + response_format)

也就是说：输入里有任何一个字符不同，就是不同的 key。这不是保守，
而是唯一能保证正确的做法 —— 相似度匹配意味着"用 A 问题的答案回答 B 问题"，
那比多花一次钱严重得多。

反过来说，这也意味着**命中率取决于调用方是否稳定地构造相同输入**。
理解管道里的 messages 是由提示词模板 + 资料原文拼出来的，两次重跑逐字节相同，
所以那里命中率会很高；而 RAG 问答的上下文每次都不同，命中率天然接近零 ——
这不是缓存没生效，而是那些请求本来就没有重复。

## 失败一律"当作未命中"

缓存是**加速与省钱**的旁路，不是正确性的一部分。因此：

- 读缓存失败（表不存在、连接问题、JSON 损坏）→ 记 debug 日志，照常调模型；
- 写缓存失败 → 记 warning，不影响本次返回。

**绝不**因为缓存出问题而让调用失败。这与记账（4.2）是同一条原则。

## 只缓存成功响应

失败（超时、4xx/5xx、重试耗尽）不写缓存 —— 那会把一次偶发故障
固化成"这个输入永远失败"。只有拿到完整成功响应体之后才写。

## 一个必须写下来的残余风险

缓存在 `chat_detailed` 这一层，也就是说它在**调用方校验之前**落库。
若模型某次返回了一份通过 JSON 解析、但内容不合格的东西，
这份东西会被缓存下来，之后每次相同输入都拿到它 —— 而 TTL 之前没人会发现。

缓解手段有三条，都不是"消除"：
1. `llm_cache_ttl_days` 到期即失效；
2. `purge_expired()` 可主动清理；
3. `llm_calls.cached=True` 的行在报表里可见，异常高的命中率能被人注意到。

要做到"只缓存合格结果"，得把缓存挪到每个调用方（在它校验之后）——
那意味着每个场景各写一遍，且漏一个就少省一份钱。目前的取舍是
**统一在出口缓存 + 三条缓解**，并把风险写在这里。
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.llm_cache import LLMCache

logger = logging.getLogger(__name__)

#: key 的十六进制长度（sha256 前 32 字符 = 128 位）
KEY_LEN = 32


def cache_key(
    *,
    provider: Optional[str],
    base_url: Optional[str],
    model: Optional[str],
    messages: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
    response_format: Optional[Dict] = None,
) -> str:
    """由完整输入算出缓存键

    ## 为什么要带 `base_url`

    同一个模型名可以挂在不同的网关后面（本地调试指向 GLM、生产指向 DeepSeek，
    见 `get_llm_config`）。只按模型名做键会让两条链路的响应互相串。

    ## 为什么用 `sort_keys` 与固定分隔符

    字典的迭代顺序在 Python 里是稳定的，但**跨进程/跨版本不保证**。
    不排序的话，同一份输入在不同进程里可能算出不同的键 —— 那是"缓存偶尔
    命中"这种最难查的现象（表现为命中率忽高忽低，而不是不命中）。

    ## 为什么 `ensure_ascii=False` 后要按 utf-8 编码

    中文在 `ensure_ascii=True` 下会变成 `\\uXXXX` 转义，虽然也能算出稳定键，
    但会让"同一段中文的两种转义写法"变成两个键 —— 直接用 utf-8 字节最省事。
    """
    payload = {
        "provider": provider or "",
        "base_url": base_url or "",
        "model": model or "",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": response_format or None,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:KEY_LEN]


@dataclass
class CacheHit:
    """一次命中（足以让调用方组装出与真实调用等价的返回）"""
    response: Dict[str, Any]
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int


async def lookup(db: AsyncSession, key: str) -> Optional[CacheHit]:
    """查缓存；未命中/过期/损坏都返回 None

    损坏（`response_json` 解析失败）当作未命中，并把那一行删掉 ——
    留着它只会让之后每一次相同输入都白读一遍。
    """
    try:
        row = (await db.execute(
            select(LLMCache).where(LLMCache.key == key)
        )).scalars().first()
    except Exception as exc:  # noqa: BLE001 - 缓存读失败不该影响调用
        logger.debug("读 LLM 缓存失败（当作未命中）: %s", exc)
        return None

    if row is None:
        return None

    now = datetime.now(timezone.utc)
    expires = row.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires is not None and expires <= now:
        logger.debug("LLM 缓存已过期，按未命中处理: %s", key[:12])
        return None

    try:
        response = json.loads(row.response_json)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("LLM 缓存内容损坏，删除该行并按未命中处理: %s (%s)", key[:12], exc)
        try:
            await db.execute(delete(LLMCache).where(LLMCache.key == key))
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()
        return None

    # 命中计数用 UPDATE 直接自增，不走"读出来 +1 再写回"：
    # 后者在并发下会丢计数，而命中率是判断"缓存值不值得留"的依据。
    try:
        await db.execute(
            update(LLMCache)
            .where(LLMCache.key == key)
            .values(hit_count=LLMCache.hit_count + 1, last_hit_at=now)
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - 统计失败不影响本次命中
        logger.debug("更新缓存命中计数失败（不影响本次命中）: %s", exc)
        await db.rollback()

    return CacheHit(
        response=response,
        total_tokens=int(row.total_tokens or 0),
        prompt_tokens=int(row.prompt_tokens or 0),
        completion_tokens=int(row.completion_tokens or 0),
    )


async def store(
    db: AsyncSession,
    key: str,
    *,
    provider: Optional[str],
    model: Optional[str],
    response: Dict[str, Any],
    usage: Optional[Dict[str, Any]],
    finish_reason: Optional[str] = None,
    ttl_days: int = 0,
    session_factory=None,
) -> None:
    """写入缓存（已存在则忽略）

    Args:
        ttl_days: 有效期；<=0 表示不过期

    并发下两个请求可能同时未命中并同时写 —— 用"先查后插"会有竞态。
    这里靠主键冲突兜底：捕获 `IntegrityError` 并当作"别人已经写好了"，
    而不是覆盖（覆盖会把先写入的那份响应换掉，而两者都是有效响应，
    换掉毫无意义还可能引入不一致）。
    """
    try:
        if session_factory is not None:
            # 缓存写入用**自己的会话**：与记账同理，它不该被调用方的事务
            # 回滚掉（一次失败的业务调用不该让"这个输入已经算过"这件事消失）。
            async with session_factory() as own_db:
                await _store_inner(
                    own_db, key, provider=provider, model=model, response=response,
                    usage=usage, finish_reason=finish_reason, ttl_days=ttl_days,
                )
            return
        await _store_inner(
            db, key, provider=provider, model=model, response=response,
            usage=usage, finish_reason=finish_reason, ttl_days=ttl_days,
        )
    except Exception as exc:  # noqa: BLE001 - 写缓存失败不该影响调用
        logger.warning("写 LLM 缓存失败（不影响本次调用）: %s", exc)


async def _store_inner(
    db: AsyncSession,
    key: str,
    *,
    provider: Optional[str],
    model: Optional[str],
    response: Dict[str, Any],
    usage: Optional[Dict[str, Any]],
    finish_reason: Optional[str],
    ttl_days: int,
) -> None:
    from .llm_accounting_service import _usage_int  # 复用同一套字段名兼容

    existing = (await db.execute(
        select(LLMCache.key).where(LLMCache.key == key)
    )).scalars().first()
    if existing is not None:
        return

    usage = usage or {}
    now = datetime.now(timezone.utc)
    row = LLMCache(
        key=key,
        provider=provider,
        model=model,
        response_json=json.dumps(response, ensure_ascii=False),
        finish_reason=finish_reason,
        prompt_tokens=_usage_int(usage, "prompt_tokens", "input_tokens"),
        completion_tokens=_usage_int(usage, "completion_tokens", "output_tokens"),
        total_tokens=_usage_int(usage, "total_tokens"),
        hit_count=0,
        expires_at=(now + timedelta(days=ttl_days)) if ttl_days > 0 else None,
    )
    db.add(row)
    await db.commit()


async def purge_expired(db: AsyncSession, *, now: Optional[datetime] = None) -> int:
    """删掉已过期的缓存行，返回删除条数

    刻意**不**顺手删"很久没命中的行"：命中率低可能是"这个输入本来就只出现
    一次"（正常），也可能是"键算错了"（异常）。自动清理会把后者的证据一起
    抹掉，于是问题永远查不出来。要不要删由人决定。
    """
    now = now or datetime.now(timezone.utc)
    result = await db.execute(
        delete(LLMCache).where(
            LLMCache.expires_at.is_not(None), LLMCache.expires_at <= now,
        )
    )
    await db.commit()
    return int(result.rowcount or 0)


async def stats(db: AsyncSession) -> Dict[str, Any]:
    """缓存自身的规模与命中情况（不按用户分，缓存本来就是全局的）"""
    from sqlalchemy import func

    row = (await db.execute(
        select(
            func.count().label("entries"),
            func.coalesce(func.sum(LLMCache.hit_count), 0).label("hits"),
            func.coalesce(func.sum(LLMCache.total_tokens), 0).label("stored_tokens"),
        ).select_from(LLMCache)
    )).one()
    ever_hit = (await db.execute(
        select(func.count()).select_from(LLMCache).where(LLMCache.hit_count > 0)
    )).scalar() or 0
    return {
        "entries": int(row.entries or 0),
        "hits": int(row.hits or 0),
        "stored_tokens": int(row.stored_tokens or 0),
        "entries_ever_hit": int(ever_hit),
    }


__all__ = [
    "KEY_LEN",
    "CacheHit",
    "cache_key",
    "lookup",
    "purge_expired",
    "stats",
    "store",
]
