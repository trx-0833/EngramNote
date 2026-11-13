"""
问答历史服务层（docs/qa-history-plan.md §3 / §6）

本模块是**问答历史**的唯一读写入口：会话创建、轮次追加、列表与详情、软删与硬删、
以及保留期清理。端点层（`api/qa.py`）与流式落库（`api/understanding.py` /
`api/notes/ask.py`）都只调这里。

## 为什么把"落库"做成服务而不是散在端点里

两个 `ask/stream` 端点都要落库（全库问答与笔记浮层），而它们的异常分支、
中止处理、字段来源**完全一样**。散在端点里写两遍，迟早出现"一处修了另一处没修"。
本项目的 `pending_embedding_chunks`（`api/notes/list_detail.py`）就是同一思路的先例。

## 三条硬约束

1. **写入失败绝不能穿出去**：问答的主流程是"把答案发出去"，历史只是附加。
   `record_turn` 内部把异常转成 `logger.warning`，返回 `None`。
2. **会话归属必须校验**：`session_id` 来自客户端，可能属于别人 —— 一律按
   `(session_id, user_id)` 查，查不到按"不存在"处理（不泄露 id 是否存在）。
3. **软删的会话不再参与写入**：往已删会话里追加轮次会让"删了却还在长"成为现实。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models.qa_history import (
    SURFACE_GLOBAL,
    SURFACE_NOTE,
    TURN_STATUS_OK,
    QASession,
    QATurn,
)

logger = logging.getLogger(__name__)

#: 标题截断长度（`qa_sessions.title` 是 120，留余量给"…"）
TITLE_MAX_CHARS = 80
#: `last_question` 截断长度（列表副标题，一行放得下）
LAST_QUESTION_MAX_CHARS = 80
#: 软删后多久硬删（天）。与计划 §6.2 一致：给用户"误删后悔"的窗口
SOFT_DELETE_GRACE_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _truncate(text: str, limit: int) -> str:
    """按字符截断（不做词边界处理：中文没有空格，按词切会切在词中间）"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


# ---------------------------------------------------------------------------
# 会话与轮次
# ---------------------------------------------------------------------------


async def get_or_create_session(
    db: AsyncSession,
    *,
    user_id: str,
    surface: str = SURFACE_GLOBAL,
    session_id: Optional[str] = None,
    note_id: Optional[str] = None,
    first_question: Optional[str] = None,
    expect_note_id: Optional[str] = None,
) -> Optional[QASession]:
    """取会话；给了 `session_id` 但取不到（不存在/别人的/已软删/surface 不符/note 不符）返回 `None`

    **不静默新建**：调用方要能区分"续聊"与"会话已消失"，前者该继续追加，
    后者要回 `404`（与计划 §4.4 最后一行一致：删了却不报错会让人以为还在写）。

    ## `surface` 是**校验**而不只是过滤（2026-11-13 审计 M1）

    端点各自声明自己面向哪个入口，续聊时必须对得上：拿一个 `surface=note`
    的会话 id 来全库问答，会让"笔记浮层的对话"里混进不属于任何笔记的轮次，
    之后按 `surface` 过滤出来的数据也不再可信。不匹配一律按"取不到"处理（404 口径），
    而不是静默新建 —— 静默新建会让前端以为续聊成功，`?s=` 指向一个空会话。

    ## `expect_note_id` 同理（2026-11-13 第二轮审计 MINOR-1）

    笔记浮层的会话还绑着 `note_id`。只校验 surface 不够：拿 note A 的会话 id
    去问 note B，会把 B 的内容写进"A 的对话"里，之后 `get_or_create_session_for_note`
    按 `note_id` 复用会话时还会继续往同一条里塞 —— 数据归属错位。
    给了 `expect_note_id` 时一并 `where`，不匹配同样按"取不到"处理。

    不给 `session_id` 时新建一个会话（`title` 取首个问题截断）。
    """
    if session_id:
        stmt = select(QASession).where(
            QASession.id == session_id,
            QASession.user_id == user_id,
            QASession.deleted_at.is_(None),
        )
        if surface:
            stmt = stmt.where(QASession.surface == surface)
        if expect_note_id is not None:
            stmt = stmt.where(QASession.note_id == expect_note_id)
        return (await db.execute(stmt)).scalars().first()

    session = QASession(
        user_id=user_id,
        surface=surface,
        note_id=note_id if surface == SURFACE_NOTE else None,
        title=_truncate(first_question, TITLE_MAX_CHARS) if first_question else None,
        turn_count=0,
        last_question=_truncate(first_question, LAST_QUESTION_MAX_CHARS) if first_question else None,
    )
    db.add(session)
    await db.flush()
    return session


async def get_session_by_id(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
) -> Optional[QASession]:
    """只按 `(id, user_id, 未删)` 取会话，**不过滤 surface / note_id**

    存在的意义只有一个（2026-11-13 第二轮审计 MINOR-4）：端点在"取不到"时
    要能区分两种原因 —— 会话真的不在（该说"已被删除"）与**会话在、但不是这个入口的**
    （该说"属于另一个入口"）。两种都回 404 不泄露存在性，但文案不同，
    否则用户会被告知"对话已被删除"，转头却在另一个入口看见它。
    """
    stmt = select(QASession).where(
        QASession.id == session_id,
        QASession.user_id == user_id,
        QASession.deleted_at.is_(None),
    )
    return (await db.execute(stmt)).scalars().first()


async def record_turn(
    db: AsyncSession,
    *,
    user_id: str,
    session: QASession,
    question: str,
    answer: Optional[str],
    status: str = TURN_STATUS_OK,
    error_code: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    prompt_version: Optional[str] = None,
    retrieval_status: Optional[str] = None,
    retrieval_query: Optional[str] = None,
    sources: Optional[Sequence[Dict[str, Any]]] = None,
    context_turns: int = 0,
    source_span_start: Optional[int] = None,
    source_span_end: Optional[int] = None,
    source_hash: Optional[str] = None,
    latency_ms: Optional[int] = None,
) -> Optional[QATurn]:
    """追加一轮并刷新会话的 `updated_at` / `turn_count` / `last_question`

    ## 失败只 warning，不抛

    历史是附加功能：写不进去不该让用户拿不到已经生成好的答案
    （与 `pending_embedding_chunks` 同一条原则）。所以这里 `try/except` 全包，
    异常转成 `logger.warning` 并返回 `None`。

    ## `sources` 的片段在这里截断

    `snippet` 限 200 字：历史里的引用是"提示是哪一段"，不是"把笔记复制一份"。
    截断放在服务层而不是端点层，是为了让所有调用方口径一致。
    """
    try:
        clipped_sources = None
        if sources:
            # 检索层给的字段名是 `relevant_text`（见 api/understanding.py 里
            # `AnswerSource(relevant_text=s["relevant_text"])`）；早期草稿以为叫
            # `snippet`，只截那一个键等于**什么都没截**。两个都处理，前端不必关心来源。
            clipped_sources = []
            for item in sources:
                entry = dict(item)
                for key in ("relevant_text", "snippet"):
                    if entry.get(key):
                        entry[key] = _truncate(str(entry[key]), 200)
                clipped_sources.append(entry)
        turn = QATurn(
            session_id=session.id,
            user_id=user_id,
            # `created_at` 由**应用侧**写入（第二轮审计"疑点 2"：SQLite 的
            # `CURRENT_TIMESTAMP` 只有秒级精度，同一秒内的多轮 `created_at` 完全相同，
            # 于是 `order_by(created_at, id)` 退化成按 UUID 排 —— 等价于随机）。
            # 这一列是"轮次顺序"的唯一依据：`recent_ok_turns` 取最近 N 轮、
            # 详情接口的 `turns_limit` 取最近 N 轮都靠它。微秒精度记下来，
            # 同一秒内的顺序才是确定的；历史行仍可能是秒级值，混排时秒级更旧，符合直觉。
            created_at=_now(),
            question=question,
            answer=answer,
            status=status,
            error_code=error_code,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            retrieval_status=retrieval_status,
            retrieval_query=retrieval_query,
            sources_json=json.dumps(clipped_sources, ensure_ascii=False) if clipped_sources else None,
            context_turns=max(0, int(context_turns)),
            source_span_start=source_span_start,
            source_span_end=source_span_end,
            source_hash=source_hash,
            latency_ms=latency_ms,
        )
        db.add(turn)
        # 只用**成功**的轮次推进"最后聊过什么"：失败的一轮没有答案，
        # 拿它当副标题会让人以为那次问成功了
        session.updated_at = _now()
        if status == TURN_STATUS_OK:
            session.turn_count = (session.turn_count or 0) + 1
            session.last_question = _truncate(question, LAST_QUESTION_MAX_CHARS)
            if not session.title:
                session.title = _truncate(question, TITLE_MAX_CHARS)
        await db.flush()
        return turn
    except Exception as exc:  # noqa: BLE001 - 附加功能不该拖垮主流程
        # 必须 rollback（2026-11-13 审计 n3）：异常后 session 处于 need-rollback 状态，
        # 调用方接着 `commit()` 会再抛一次 `PendingRollbackError`，把真正的约束/锁错误
        # 淹成第二条 warning —— 排障时看到的永远是那条没信息量的。
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001 - 连回滚都失败就只能记日志
            logger.debug("问答历史写入失败后的 rollback 也失败", exc_info=True)
        logger.warning(
            "问答历史写入失败（忽略继续）: session=%s | %s: %s", (session.id or "")[:8], type(exc).__name__, exc
        )
        return None


#: 笔记浮层复用会话的时间窗（分钟）
#:
#: 浮层不做历史 UI，如果每问一次都新建会话，用户的历史列表会被一串
#: "只剩一句问答"的碎片会话淹没。30 分钟是"还在读同一篇资料"的合理窗口。
NOTE_SESSION_REUSE_MINUTES = 30


async def get_or_create_session_for_note(
    db: AsyncSession,
    *,
    user_id: str,
    note_id: str,
    question: str,
    now: Optional[datetime] = None,
) -> QASession:
    """笔记浮层用：同一篇笔记 30 分钟内复用上一个会话，否则新建

    与 `get_or_create_session` 的区别是"没有 session_id 时该不该新建"：
    全局问答每次不带 id 都是一次全新的对话（用户明确点了"新建对话"或刚进来），
    而浮层是随手问，30 分钟内归到同一会话更符合直觉。
    """
    when = now or _now()
    cutoff = when - timedelta(minutes=NOTE_SESSION_REUSE_MINUTES)
    stmt = (
        select(QASession)
        .where(
            QASession.user_id == user_id,
            QASession.surface == "note",
            QASession.note_id == note_id,
            QASession.deleted_at.is_(None),
            QASession.updated_at >= cutoff,
        )
        .order_by(QASession.updated_at.desc())
        .limit(1)
    )
    recent = (await db.execute(stmt)).scalars().first()
    if recent is not None:
        return recent
    return await get_or_create_session(db, user_id=user_id, surface="note", note_id=note_id, first_question=question)


async def recent_ok_turns(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: str,
    limit: int,
) -> List[QATurn]:
    """取最近 `limit` 条**成功**轮次（按时间升序返回，便于直接拼 `messages`）

    只取 `status='ok'`：失败轮次没有答案、中止轮次只有半截答案
    （计划 §4.4 —— 把半截答案当事实继承，比不给上下文更糟）。
    """
    if limit <= 0:
        return []
    stmt = (
        select(QATurn)
        .where(
            QATurn.session_id == session_id,
            QATurn.user_id == user_id,
            QATurn.status == TURN_STATUS_OK,
        )
        .order_by(QATurn.created_at.desc(), QATurn.id.desc())
        .limit(int(limit))
    )
    rows = (await db.execute(stmt)).scalars().all()
    return list(reversed(list(rows)))


async def list_sessions(
    db: AsyncSession,
    *,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    keyword: Optional[str] = None,
    surface: Optional[str] = None,
    now: Optional[datetime] = None,
) -> tuple[List[QASession], int]:
    """列表（按 `updated_at` 倒序），返回 `(items, total)`

    `keyword` 同时匹配 `title` / `last_question` 与**该会话内任意轮次的问答正文**
    —— 用户记住的往往是"答案里的那句话"，只搜标题等于搜不到。
    """
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))

    stmt = select(QASession).where(
        QASession.user_id == user_id,
        QASession.deleted_at.is_(None),
    )
    if surface:
        stmt = stmt.where(QASession.surface == surface)
    if keyword:
        # LIKE 通配符必须转义（2026-11-13 审计 MINOR-3）：`keyword="%"` 或 `"_"`
        # 原本会命中**全部**会话（`%%` / `__`），用户看到"搜索像是失效了"；
        # 纯空白条 `strip()` 后同样是 `%%`，所以先判空。
        raw = keyword.strip()
        if not raw:
            keyword = None
        else:
            escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            # 子查询：该会话里有轮次命中 question/answer
            matched_ids = select(QATurn.session_id).where(
                QATurn.user_id == user_id,
                or_(
                    QATurn.question.ilike(like, escape="\\"),
                    QATurn.answer.ilike(like, escape="\\"),
                ),
            )
            stmt = stmt.where(
                or_(
                    QASession.title.ilike(like, escape="\\"),
                    QASession.last_question.ilike(like, escape="\\"),
                    QASession.id.in_(matched_ids),
                )
            )

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(QASession.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)))
        .scalars()
        .all()
    )
    return list(rows), int(total)


async def get_session_with_turns(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    turns_limit: Optional[int] = None,
) -> Optional[tuple[QASession, List[QATurn]]]:
    """取会话 + 其轮次（升序）。别人的/已删的返回 `None`（端点转 404）

    `turns_limit` 给了就只取**最近**这么多轮（仍按升序返回）：会话没有自动切分，
    长会话可达上千轮，一次性载入会把内存与响应体都撑大（审计 MINOR-6）。
    实现方式是"倒序取 N 条再反转"，这样不会先扫全表再切片。
    """
    stmt = select(QASession).where(
        QASession.id == session_id,
        QASession.user_id == user_id,
        QASession.deleted_at.is_(None),
    )
    session = (await db.execute(stmt)).scalars().first()
    if session is None:
        return None
    turn_stmt = select(QATurn).where(QATurn.session_id == session.id)
    if turns_limit is not None and turns_limit > 0:
        recent = (
            (await db.execute(turn_stmt.order_by(QATurn.created_at.desc(), QATurn.id.desc()).limit(int(turns_limit))))
            .scalars()
            .all()
        )
        turns = list(reversed(list(recent)))
        return session, turns
    turns = (await db.execute(turn_stmt.order_by(QATurn.created_at.asc(), QATurn.id.asc()))).scalars().all()
    return session, list(turns)


async def soft_delete_session(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    now: Optional[datetime] = None,
) -> bool:
    """软删（`deleted_at` 置当前时间）。返回是否真的删掉了一条"""
    stmt = select(QASession).where(
        QASession.id == session_id,
        QASession.user_id == user_id,
        QASession.deleted_at.is_(None),
    )
    session = (await db.execute(stmt)).scalars().first()
    if session is None:
        return False
    session.deleted_at = now or _now()
    await db.flush()
    return True


async def soft_delete_all_sessions(
    db: AsyncSession,
    *,
    user_id: str,
    now: Optional[datetime] = None,
) -> int:
    """清空该用户的全部会话（软删），返回行数 —— "清空全部问答历史"入口用

    ## 一条 `UPDATE` 而不是"全量加载 + 逐行赋值"（2026-11-13 审计 MINOR-7）

    原写法 `select(...).scalars().all()` 会把这个用户的**每个**会话都读进内存再改，
    并发 N 条 `UPDATE`，且每条都被 `BaseModel.updated_at` 的 `onupdate` 顺带刷新 ——
    在 SQLite 单写者下是一条很长的写事务，还会把"最后活跃时间"改成删除时间。
    改成单条 `UPDATE ... WHERE deleted_at IS NULL` 后：一次往返、行数取 `rowcount`、
    `updated_at` 也能显式沿用同一次赋值口径（这里刻意不写它 ——
    删除时间记在 `deleted_at` 上，`updated_at` 保留"最后一次真的聊过"的语义）。
    """
    compact = now or _now()
    result = await db.execute(
        update(QASession).where(QASession.user_id == user_id, QASession.deleted_at.is_(None)).values(deleted_at=compact)
    )
    await db.flush()
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# 保留期清理（Beat 任务调用，见 tasks/maintenance_tasks.py）
# ---------------------------------------------------------------------------


async def purge_soft_deleted(
    db: AsyncSession,
    *,
    grace_days: int = SOFT_DELETE_GRACE_DAYS,
    now: Optional[datetime] = None,
) -> int:
    """硬删"软删已超过宽限期"的会话（轮次由外键级联删除）

    宽限期存在的原因：删会话是**用户可见的破坏性动作**，留 30 天给"后悔"；
    而超过宽限期还留着，只是把敏感文本无限期留在库里。
    """
    cutoff = (now or _now()) - timedelta(days=max(0, int(grace_days)))
    # 一条 Core DELETE（2026-11-13 审计 MINOR-7）：原写法先把**全部**命中 id 读成
    # Python list 再拼 `IN (...)`。`IN` 的参数个数有驱动级上限（标准 SQLite 构建是
    # 32766），超了就整批失败，而任务只记 error 日志 —— 表现为"清理从此静默不生效"。
    # 轮次由外键 `ON DELETE CASCADE` 带走（`PRAGMA foreign_keys=ON`，已被用例钉住）。
    result = await db.execute(delete(QASession).where(QASession.deleted_at.is_not(None), QASession.deleted_at < cutoff))
    await db.flush()
    return int(result.rowcount or 0)


async def purge_expired_sessions(
    db: AsyncSession,
    *,
    retention_days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> int:
    """硬删超过保留期的会话（含未软删的），返回行数

    `retention_days <= 0` 表示**永久保留**（与 `purge_old_calls` 同一口径）：
    删掉的是用户自己的对话，默认值必须保守，想永久留就配 0。
    """
    if retention_days is None:
        settings = get_settings()
        retention_days = int(getattr(settings, "qa_history_retention_days", 90) or 0)
    if retention_days <= 0:
        return 0
    cutoff = (now or _now()) - timedelta(days=int(retention_days))
    # 同上：一条 Core DELETE，不把 id 读进内存（审计 MINOR-7）。
    result = await db.execute(delete(QASession).where(QASession.updated_at < cutoff))
    await db.flush()
    return int(result.rowcount or 0)


async def strip_snippets_for_note(db: AsyncSession, *, note_id: str) -> int:
    """笔记被 purge 后，清空历史里引用它的 `snippet`（保留 `chunk_id` 作墓碑）

    为什么不连轮次一起删：那是**用户自己的问与答**，不该因为笔记被删而消失；
    但片段是笔记正文的副本，笔记已经彻底删除时它必须跟着走（计划 §6.3）。
    """
    stmt = select(QATurn).where(QATurn.sources_json.is_not(None), QATurn.sources_json.like(f"%{note_id}%"))
    turns = (await db.execute(stmt)).scalars().all()
    changed = 0
    for turn in turns:
        try:
            items = json.loads(turn.sources_json or "[]")
        except (TypeError, ValueError):
            continue
        touched = False
        for item in items:
            if not isinstance(item, dict) or item.get("note_id") != note_id:
                continue
            # 两个键都清：检索层用 `relevant_text`，早期实现用 `snippet`
            for key in ("relevant_text", "snippet"):
                if item.get(key):
                    item[key] = ""
                    touched = True
        if touched:
            turn.sources_json = json.dumps(items, ensure_ascii=False)
            changed += 1
    if changed:
        await db.flush()
    return changed
