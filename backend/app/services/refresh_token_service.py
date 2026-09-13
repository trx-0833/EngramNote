"""
刷新令牌与会话撤销服务（阶段 6.3：token 可吊销）

## 这个模块解决什么问题

访问令牌是无状态的，服务端不留记录 —— 因此"登出"过去只是前端删掉本地那串
字符串，服务端一无所知，被复制走的令牌照样能用满 `jwt_expire_minutes`。
本模块引入**有状态的刷新令牌**作为会话的可撤销锚点：

    登录        → 签发（access, refresh），refresh 落库（一条新链）
    刷新        → 校验 refresh → 旧行标记撤销并指向新行 → 发新的一对
    登出        → 撤销该 refresh（可选：撤销该用户全部 refresh）
    重放（复用已撤销的 refresh）→ 撤销**整条链**并返回 401

## 四件必须说清楚的事

### 1. 撤销的边界：会话不能再延长，但已发出的访问令牌会自然过期

登出后那枚**访问令牌在 `exp` 之前仍然可用**（无状态是它的定义，不是缺陷）。
本模块保证的是：会话**无法被继续延长**，且泄露的刷新令牌立刻失效。
要连访问令牌一起立即失效，就必须在每个请求上查一次黑名单（本项目
`get_current_user_dependency` 已经有一次 users 查询，再加一次是常驻成本），
并且要和过期行清理的语义耦合（清理掉"链还活着"的证据就会误杀有效会话）。
这是一条独立的、需要单独验证的变更，本轮不做 —— 见 docs/overhaul-plan.md 附录 BA.3。

### 2. 撤销是"按链"而不是"按用户"

`family_id` 在一次登录时生成，轮换沿用同一条链。因此：

- 换令牌只影响**本设备当前那一枚**；
- 重放检测只杀**那一条链**，其他设备（各有一条链）不受影响。

如果按用户撤销，一次误判就会把所有设备踢下线；按链撤销把误判代价压到
"当事人重登一次"。

### 3. 重放检测只认"已撤销"，不区分撤销原因

轮换换掉的、登出撤销的、整链撤销的，只要再被拿来刷新，一律按泄露处理。
理由：这三种情形下这枚令牌都**不应该再被使用**，而"它又出现了"这个事实
本身就是要处理的信号。判别原因会让规则出现分叉（"登出后的重放可以放过"），
而放过的代价是真实的盗用被当成正常流量。

### 4. 事务边界：本模块的函数只 flush，提交由调用方决定

`rotate_refresh_token` 必须把"插入新行"和"撤销旧行"放在**同一个事务**里：
否则中间失败会留下一枚已签发但无法被追溯的令牌。因此
`issue_refresh_token` 只 `flush()`，由调用方 `commit()`。
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models.refresh_token import RefreshToken
from ..models.user import User
from .auth_service import (
    RefreshClaims,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    jti_prefix,
)

settings = get_settings()
logger = logging.getLogger(__name__)

#: 单次清理最多删除的行数。
#:
#: 为什么需要上限：清理任务是**首次运行最危险** —— 一张积累了几个月、
#: 几十万行的表如果一次删干净，会在 SQLite 的单写锁上压住整库（表现为
#: 所有请求 "database is locked"）。有限批次把"一次巨删"拆成"每天的定长删除"，
#: 调用方（cleanup_refresh_tokens 任务）在单次调度里循环若干批把它排干。
DEFAULT_PURGE_LIMIT = 5000


@dataclass(frozen=True)
class RotatedSession:
    """一次成功轮换的结果（新的一对令牌 + 令牌归属的用户）"""

    user: User
    access_token: str
    refresh_token: str


def _utc(value: datetime) -> datetime:
    """
    归一化为带 UTC 时区的 datetime

    SQLite 不存时区（见 `models/base.TZDateTime`）。从库里读回的行已经由
    类型装饰器补上 UTC，但内存中刚构造的对象、以及从 JWT 解出的时间
    未必带时区 —— 两者直接相减会抛 `TypeError: can't compare offset-naive
    and offset-aware datetimes`。这类错误只在特定路径上出现（例如清理任务
    用 `now` 比较一个刚构造的行），因此这里统一收口。
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def _get_by_jti(db: AsyncSession, jti: str) -> Optional[RefreshToken]:
    """按 jti 取令牌行（无则 None）"""
    result = await db.execute(select(RefreshToken).where(RefreshToken.jti == jti))
    return result.scalars().first()


async def issue_refresh_token(
    db: AsyncSession,
    user_id: str,
    *,
    family_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[str, RefreshToken]:
    """
    签发一枚刷新令牌并登记到数据库（**只 flush，不 commit**）

    同一枚令牌在数据库里留下恰好一行：JWT 里的 `jti` 就是那一行的键。
    这样"令牌是否还有效"这个问题永远有唯一答案，而不是靠两处状态互相印证。

    Args:
        db: 异步数据库会话
        user_id: 令牌归属用户
        family_id: 轮换链标识；None 表示新开一条链（登录/注册），
            轮换时必须传入原链，否则重放检测会被绕开
        now: 注入当前时间（测试用）；缺省取 UTC 当前时刻

    Returns:
        Tuple[str, RefreshToken]: (原始 JWT 字符串, 落库的行对象)

    Raises:
        ValueError: `refresh_token_expire_days` 配置不是正数
            （非正数会签出"出生即过期"的令牌，属于配置错误而非运行时状况，
            必须显式失败而不是静默签发一堆废令牌）
    """
    if settings.refresh_token_expire_days <= 0:
        raise ValueError(
            "refresh_token_expire_days 必须为正数，当前为 "
            f"{settings.refresh_token_expire_days}"
        )
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(days=settings.refresh_token_expire_days)
    claims = RefreshClaims(
        jti=uuid.uuid4().hex,
        user_id=user_id,
        family_id=family_id or uuid.uuid4().hex,
        expires_at=expires_at,
    )
    row = RefreshToken(
        jti=claims.jti,
        user_id=user_id,
        family_id=claims.family_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    logger.info(
        "签发刷新令牌: user=%s jti=%s family=%s 有效期至 %s",
        user_id[:8], jti_prefix(claims.jti), jti_prefix(claims.family_id),
        expires_at.isoformat(),
    )
    return create_refresh_token(claims), row


async def _revoke_family(
    db: AsyncSession, family_id: str, *, now: Optional[datetime] = None
) -> int:
    """撤销整条轮换链（重放检测的处置动作），返回被撤销行数

    只更新 `revoked_at IS NULL` 的行：已经撤销的行保留**原来的**撤销时刻与
    `replaced_by_jti`。覆盖写会抹掉"它当初是怎么被撤销的"这条证据，
    而那正是排查盗用时唯一能看的东西。
    """
    result = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now or datetime.now(timezone.utc))
    )
    await db.commit()
    return int(result.rowcount or 0)


async def revoke_all_for_user(
    db: AsyncSession, user_id: str, *, now: Optional[datetime] = None
) -> int:
    """撤销某用户**全部**未撤销的刷新令牌（登出全部设备），返回被撤销行数"""
    result = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now or datetime.now(timezone.utc))
    )
    await db.commit()
    return int(result.rowcount or 0)


async def rotate_refresh_token(
    db: AsyncSession,
    raw_token: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[RotatedSession]:
    """
    用一枚刷新令牌换取新的一对令牌（轮换），失败返回 None

    失败（返回 None）覆盖四种情形，且**每一种都不告诉客户端具体原因** ——
    调用方统一回 401，避免把"这枚令牌曾经存在过"变成可探测的信息：

    | 情形 | 处置 |
    |---|---|
    | 不是刷新令牌 / 签名错 / 已过期 | 直接拒绝（连表都不用查） |
    | 库里查不到 jti | 拒绝，记 warning（可能被清理或伪造） |
    | **已撤销（重放）** | **撤销整条链** + 拒绝（盗用信号） |
    | 令牌有效但用户不存在/已禁用 | 撤销该枚令牌 + 拒绝 |

    成功时：新令牌沿用原 `family_id`（同一条链），旧行标记
    `revoked_at` + `replaced_by_jti=新 jti`。

    ## 并发：两个请求同时用同一枚令牌刷新

    条件更新（`WHERE jti=? AND revoked_at IS NULL`）保证只有一个能改到行，
    另一个的 `rowcount` 是 0 —— 那说明这枚令牌在我们读取之后被别人用掉了，
    与"重放已撤销令牌"是同一件事，因此按同样的处置：撤销整条链、拒绝。
    先插入的新行会被 `rollback()` 撤掉，不留下一枚无人知晓的令牌。

    Args:
        db: 异步数据库会话
        raw_token: 客户端提交的刷新令牌原文
        now: 注入当前时间（测试用）

    Returns:
        Optional[RotatedSession]: 成功返回用户与新令牌对，失败返回 None
    """
    claims = decode_refresh_token(raw_token)
    if claims is None:
        logger.warning("刷新被拒：不是有效的刷新令牌（类型不符/签名错/已过期）")
        return None

    moment = now or datetime.now(timezone.utc)
    row = await _get_by_jti(db, claims.jti)
    if row is None:
        logger.warning(
            "刷新被拒：jti 在库中不存在（已被清理或并非本服务签发）jti=%s",
            jti_prefix(claims.jti),
        )
        return None

    # ⚠️ 先把要用的字段取到局部变量：下面的竞态分支里有 `db.rollback()`，
    # 而回滚会让 session 里的对象**全部过期**；在异步 session 上访问已过期
    # 属性会触发懒加载并抛 MissingGreenlet（不是拿到旧值）。用局部变量后，
    # 回滚路径与正常路径读的都是同一份已知值。
    old_jti = row.jti
    family_id = row.family_id
    owner_id = row.user_id

    if row.revoked_at is not None:
        # ★ 重放/盗用信号：这枚令牌已经被换掉或已登出，却还在被使用。
        revoked = await _revoke_family(db, family_id, now=moment)
        logger.warning(
            "★ 刷新令牌重放：撤销整条轮换链 | user=%s jti=%s family=%s 本次撤销 %d 行",
            owner_id[:8], jti_prefix(old_jti), jti_prefix(family_id), revoked,
        )
        return None

    if _utc(row.expires_at) <= _utc(moment):
        # JWT 的 exp 已经在解码时校验过；走到这里说明库里的过期时刻更早
        # （例如配置被调小过）。以库为准，不续期。
        logger.warning("刷新被拒：库中记录的过期时刻已到 jti=%s", jti_prefix(old_jti))
        return None

    result = await db.execute(select(User).where(User.id == owner_id))
    user = result.scalars().first()
    if user is None or not user.is_active:
        # 用户被删除/禁用：这枚令牌立刻作废，且不签发新的。
        # 其余链上的令牌不需要在这里撤销 —— 它们每次刷新都会走到同一个判断。
        row.revoked_at = moment
        await db.commit()
        logger.warning("刷新被拒：用户不存在或已禁用 user=%s", owner_id[:8])
        return None

    new_raw, new_row = await issue_refresh_token(
        db, owner_id, family_id=family_id, now=moment
    )
    new_jti = new_row.jti
    # 条件更新：只有"仍然有效"的旧令牌才能被本次轮换消费掉
    consumed = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.jti == old_jti, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=moment, replaced_by_jti=new_jti)
    )
    if int(consumed.rowcount or 0) != 1:
        # 竞态：同一枚令牌被并发使用。撤掉刚插入的新行，按重放处置。
        await db.rollback()
        revoked = await _revoke_family(db, family_id, now=moment)
        logger.warning(
            "★ 刷新令牌并发使用：撤销整条轮换链 | user=%s jti=%s family=%s 本次撤销 %d 行",
            owner_id[:8], jti_prefix(old_jti), jti_prefix(family_id), revoked,
        )
        return None

    await db.commit()
    logger.info(
        "刷新令牌轮换成功: user=%s 旧 jti=%s → 新 jti=%s family=%s",
        user.id[:8], jti_prefix(old_jti), jti_prefix(new_jti), jti_prefix(family_id),
    )
    return RotatedSession(
        user=user,
        access_token=create_access_token(user.id),
        refresh_token=new_raw,
    )


async def revoke_refresh_token(
    db: AsyncSession,
    raw_token: Optional[str],
    *,
    all_devices: bool = False,
    now: Optional[datetime] = None,
) -> int:
    """
    登出：撤销客户端提交的刷新令牌，返回实际撤销的行数

    ## 为什么"令牌无效"不是错误

    登出是用户**意图**的表达，不是一次需要凭证校验的业务操作。因此：

    - 完全不带令牌、令牌格式错、签名错、已过期、已被换掉 —— 一律返回 0，
      **不抛异常、不返回 4xx**。客户端本地状态必须能在任何情况下清干净；
      如果登出会因为"令牌已经无效"而失败，那它恰好会在最需要它的场景
      （令牌泄露、被轮换、已过期）失效。
    - 与之对应，服务端**只**根据"签名可验证的令牌"里的 jti / sub 动手，
      绝不接受客户端直接传 `user_id`：否则这个接口就成了"任何人可以注销
      他人会话"的入口。校验签名保证了持令牌者即会话所有者。

    ## `all_devices=True`（登出全部设备）

    按令牌里的 `sub` 撤销该用户所有未撤销的刷新令牌，各设备的下一次刷新都会
    401 → 前端清本地状态回登录页。**已签发的访问令牌仍会用到各自过期为止**
    （见模块头部 §1）。

    ## 已撤销的令牌再拿来登出

    返回 0 且**不**触发整链撤销：登出不是"使用令牌"，可能是多标签页里
    落后的那一个在收尾。把收尾动作当成盗用信号会造成误杀。

    Args:
        db: 异步数据库会话
        raw_token: 客户端提交的刷新令牌（可为 None）
        all_devices: True 时撤销该用户全部刷新令牌
        now: 注入当前时间（测试用）

    Returns:
        int: 实际被撤销的行数（0 表示服务端没有需要清理的状态）
    """
    claims = decode_refresh_token(raw_token) if raw_token else None
    if claims is None:
        logger.info("登出：未携带可验证的刷新令牌，服务端无状态需要清理")
        return 0

    if all_devices:
        revoked = await revoke_all_for_user(db, claims.user_id, now=now)
        logger.info(
            "登出全部设备: user=%s 撤销 %d 枚刷新令牌", claims.user_id[:8], revoked
        )
        return revoked

    row = await _get_by_jti(db, claims.jti)
    if row is None or row.revoked_at is not None:
        # 已经是撤销状态（重复登出、或别的标签页刚轮换过）→ 幂等返回 0
        logger.info(
            "登出：令牌已无效或已撤销，无操作 jti=%s", jti_prefix(claims.jti)
        )
        return 0

    row.revoked_at = now or datetime.now(timezone.utc)
    # 同样先把日志要用的值取出来（提交后 ORM 属性在 expire_on_commit=True 的
    # 会话里会过期，异步下访问会抛 MissingGreenlet；这里不依赖会话配置）
    revoked_jti = jti_prefix(row.jti)
    owner_id = row.user_id
    await db.commit()
    logger.info("登出：已撤销刷新令牌 jti=%s user=%s", revoked_jti, owner_id[:8])
    return 1


async def purge_expired(
    db: AsyncSession,
    *,
    limit: int = DEFAULT_PURGE_LIMIT,
    now: Optional[datetime] = None,
) -> int:
    """
    删除**已过期**的刷新令牌行（每次最多 limit 行），返回删除行数

    ## 只删过期的，绝不删"已撤销但还没过期"的

    后者是重放检测唯一的证据：删掉它，重放就只会得到"查无此 jti"的普通 401，
    整链撤销这条处置就静默失效了。而过期的令牌连签名校验都过不去
    （`decode_refresh_token` 直接返回 None），本来就到不了查表这一步 ——
    删它们不损失任何安全性。

    ## 为什么要 limit

    见 `DEFAULT_PURGE_LIMIT` 的说明：把"一次巨删"拆成定长批次，
    调用方循环若干批即可，最坏情况也不会长时间占住 SQLite 的写锁。

    Args:
        db: 异步数据库会话
        limit: 单次最多删除行数
        now: 注入当前时间（测试用）

    Returns:
        int: 删除的行数（等于 limit 说明还有积压，调用方可再调一次）
    """
    cutoff = now or datetime.now(timezone.utc)
    if limit <= 0:
        return 0
    result = await db.execute(
        delete(RefreshToken).where(
            RefreshToken.jti.in_(
                select(RefreshToken.jti)
                .where(RefreshToken.expires_at <= cutoff)
                .limit(limit)
            )
        )
    )
    await db.commit()
    removed = int(result.rowcount or 0)
    if removed:
        logger.info("刷新令牌清理: 删除 %d 行过期记录（阈值 %s）", removed, cutoff.isoformat())
    return removed


__all__ = [
    "DEFAULT_PURGE_LIMIT",
    "RotatedSession",
    "issue_refresh_token",
    "purge_expired",
    "revoke_all_for_user",
    "revoke_refresh_token",
    "rotate_refresh_token",
]
