"""
问答历史的只读与删除端点（docs/qa-history-plan.md §3）

## 这个模块只做三件事

列表、详情、删除（单个 + 全部）。**写入不在这里** —— 一轮问答的落库发生在
流式回答的收尾处（`api/understanding.py` / `api/notes/ask.py`），因为那里才拥有
完整要素（累积的答案、sources、provider、latency），而且这样"客户端关页面"也不会丢。

## 归属校验一律 404

`session_id` 来自客户端，可能是别人的、可能已被删。三种情况**共用一个错误码**
（`QA_SESSION_NOT_FOUND`），与 `NOTE_NOT_FOUND` 同一口径：不泄露"这个 id 存在"。

## 为什么删除是软删

删对话是用户可见的破坏性动作，留 30 天给"后悔"（`qa_history_service.SOFT_DELETE_GRACE_DAYS`），
之后由 Beat 任务硬删。用户视角是立刻消失（列表按 `deleted_at IS NULL` 过滤）。
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.app_error import QA_SESSION_NOT_FOUND, AppError
from ..database import get_db
from ..models.qa_history import SURFACES
from ..models.user import User
from ..schemas.qa import (
    QAClearAllResponse,
    QASessionDetailResponse,
    QASessionListResponse,
    QASessionResponse,
    QATurnResponse,
)
from ..services import qa_history_service as qa_service
from .auth import get_current_user_dependency

router = APIRouter()

# 由常量生成正则（审计 n4）：加一个 surface 时只改 `models/qa_history.py`，
# 这里与前端的选择器都跟着走，不会出现"常量改了、正则没改"。
_SURFACE_PATTERN = "^(" + "|".join(SURFACES) + ")$"


def _session_payload(session) -> QASessionResponse:
    return QASessionResponse(
        id=session.id,
        surface=session.surface,
        note_id=session.note_id,
        title=session.title,
        turn_count=session.turn_count or 0,
        last_question=session.last_question,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _turn_payload(turn) -> QATurnResponse:
    sources = []
    if turn.sources_json:
        try:
            parsed = json.loads(turn.sources_json)
            if isinstance(parsed, list):
                sources = parsed
        except (TypeError, ValueError):
            # 历史行的 JSON 坏了不该让整个详情 500：当作"没有来源"展示
            sources = []
    return QATurnResponse(
        id=turn.id,
        question=turn.question,
        answer=turn.answer,
        status=turn.status,
        error_code=turn.error_code,
        provider=turn.provider,
        model=turn.model,
        prompt_version=turn.prompt_version,
        retrieval_status=turn.retrieval_status,
        retrieval_query=turn.retrieval_query,
        sources=sources,
        context_turns=turn.context_turns or 0,
        latency_ms=turn.latency_ms,
        created_at=turn.created_at,
    )


@router.get("/sessions", response_model=QASessionListResponse)
async def list_qa_sessions(
    page: int = Query(1, ge=1, description="页码（从 1 开始）"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数（上限 100）"),
    keyword: Optional[str] = Query(None, description="搜索关键词（匹配标题、最后问题与任意轮次的问答正文）"),
    # 用法用 `pattern` 交给 pydantic 校验（而不是手工 raise AppError）：
    # ① 错误契约用例要求 `AppError` 的 code 必须是常量名，而"参数不合法"没有语义化 code；
    # ② 前端对 pydantic 的 `string_pattern_mismatch` 已有本地化映射
    #    （`api/client.ts` 的 VALIDATION_KEYS），用户看到的是中文而不是英文校验串；
    # ③ 校验失败时响应里带的是 OpenAPI 声明的 422 schema，契约更准。
    surface: Optional[str] = Query(
        None, pattern=_SURFACE_PATTERN, description="按入口过滤：global（/qa）或 note（笔记浮层）"
    ),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户的问答会话（按最近聊过排序）

    「最近聊过」= `updated_at` 倒序：会话在被追加轮次时刷新它，
    所以"昨天问过、今天又追问"的会话会顶到最上面 —— 这是用户预期。
    """

    items, total = await qa_service.list_sessions(
        db,
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        keyword=keyword,
        surface=surface,
    )
    return QASessionListResponse(
        items=[_session_payload(s) for s in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/sessions/{session_id}", response_model=QASessionDetailResponse)
async def get_qa_session(
    session_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """单个会话 + 其轮次（升序）

    前端点开历史条目时调它：轮次按 `created_at` 升序，与"聊天的阅读顺序"一致。
    """
    found = await qa_service.get_session_with_turns(db, user_id=current_user.id, session_id=session_id)
    if found is None:
        raise AppError(QA_SESSION_NOT_FOUND, "对话不存在或已被删除", 404)
    session, turns = found
    return QASessionDetailResponse(
        session=_session_payload(session),
        turns=[_turn_payload(t) for t in turns],
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_qa_session(
    session_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """删除一个会话（软删；30 天后由 Beat 硬删）

    再删一次 → 404：与"删除成功"区分开，前端据此提示"对话已被删除"而不是假装成功。
    """
    deleted = await qa_service.soft_delete_session(db, user_id=current_user.id, session_id=session_id)
    if not deleted:
        raise AppError(QA_SESSION_NOT_FOUND, "对话不存在或已被删除", 404)
    await db.commit()


@router.delete("/sessions", response_model=QAClearAllResponse)
async def clear_qa_sessions(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """清空当前用户的**全部**问答历史（软删），返回删掉的会话数

    为什么返回条数而不是 204：清空之后列表会变空，而"本来就空"与"清掉了 12 个"
    在界面上完全一样 —— 条数是这次操作唯一的反馈。
    """
    deleted = await qa_service.soft_delete_all_sessions(db, user_id=current_user.id)
    await db.commit()
    return QAClearAllResponse(deleted=deleted)
