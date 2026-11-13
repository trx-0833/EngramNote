"""
选中文本 AI 提问 API 模块

本模块提供笔记阅读页「选中文本 → AI 提问」的流式接口：
用户选中笔记中的一段文本后，可基于当前笔记的选区局部上下文
向 LLM 提问，回答以 SSE 逐 token 推送。

主要职责：
- POST /api/notes/{note_id}/ask/stream  基于当前笔记选区上下文流式回答

设计决策：
- 仅校验笔记归属（防 IDOR），不读取整篇笔记内容：
  参考上下文由前端在笔记渲染容器内截取（选中文本 + 前后各 1500 字符），
  天然仅来自当前笔记，避免对象存储读取开销。
- 复用 LLMService.chat_stream（scene=note_ask_stream）流式生成，
  SSE 事件格式与 /api/understanding/ask/stream 保持一致：
  meta（provider）→ token... → done / error。

## SSE 事件契约（阶段 5.1：为什么这里**不写** response_model）

`response_model=` 只描述"一个 JSON 文档"，而本端点返回的是 **`text/event-stream`
的帧序列**，两者不是同一种东西：

1. `response_model=` 会让 FastAPI 把处理函数的返回值当作**待校验的 JSON 数据**；
   本函数返回的是 `StreamingResponse` 对象，套上 `response_model` 要么校验失败，
   要么被 FastAPI 包装成 `JSONResponse` —— 那会直接**改变运行时行为**（流变成一次性 JSON）。
2. OpenAPI 3.1 能表达 `text/event-stream` 这个 **media type**，
   但**表达不了 SSE 的事件名与每种事件的 data 结构**（没有 `events` 关键字）。
   硬塞一个 JSON schema 只会得到一个"看起来有类型、实际对不上"的契约。

因此这里的做法是：
- 用 `EventStreamResponse` 把 **media type 如实声明出去**
  （此前 schema 里它是 `application/json` + 空 schema = `unknown`，是**错的**：
  客户端照着生成的类型去 `response.json()` 会直接抛错）；
- 把事件契约写在代码里（下面这个 docstring）与 `frontend/docs/openapi-client.md`，
  并明确记录"OpenAPI 表达不了它"。

事件契约（两种事件名共用 `data` 为 JSON 的形态，`\n\n` 分隔帧）：

| event   | data                                                     | 出现时机 |
|---------|----------------------------------------------------------|----------|
| `meta`  | `{"provider": "<LLM 提供商>"}`                            | 首个事件，校验通过之后 |
| `token` | `{"content": "<片段>"}`                                   | 每个流式片段一个 |
| `done`  | `{}`                                                     | 正常结束 |
| `error` | `{"message": "<原因>"}`，部分分支另有 `error_code`        | 校验失败或流中断，**之后流即结束** |

⚠️ `error` 事件的两种形态：笔记不存在/问题为空/选中文本为空时带
`error_code`（`EMPTY_QUESTION` / `EMPTY_SELECTED_TEXT`），
未预期异常时**只有** `message`（异常文本，不保证有 error_code）。
"""

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...core.app_error import (
    EMPTY_QUESTION,
    EMPTY_SELECTED_TEXT,
    NOTE_NOT_FOUND,
    QA_SESSION_NOT_FOUND,
    QA_SESSION_SURFACE_MISMATCH,
    AppError,
    classify_exception,
)
from ...models.qa_history import SURFACE_NOTE
from ...database import get_db
from ...models.user import User
from ...schemas.common import EventStreamResponse
from ...schemas.note_ask import NoteAskRequest
from ...database import get_session_factory
from ...models.qa_history import TURN_STATUS_ABORTED, TURN_STATUS_FAILED, TURN_STATUS_OK
from ...services import qa_history_service as qa_service
from ...services.llm_service import LLMService
from ...services.note_service import get_note_detail
from ..auth import get_current_user_dependency

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/{note_id}/ask/stream", response_class=EventStreamResponse)
async def ask_note_stream(
    note_id: str,
    req: NoteAskRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    基于当前笔记选区局部上下文，对选中文本流式提问（SSE）

    SSE 事件格式：
    - event: meta    data: {"provider": "..."}              首事件，LLM 提供商标识
    - event: token   data: {"content": "..."}               每个 token 片段
    - event: done    data: {}                               结束标记
    - event: error   data: {"message": "..."}               异常情况
    """

    # ── 会话（2026-11-13）：浮层**只落库、不做多轮上下文**（计划 §5.3）──
    #
    # 复用一个"同一篇笔记 30 分钟内"的会话，而不是每问一次新建一条：
    # 否则用户的历史列表会被一串只剩一句问答的碎片会话淹没。
    settings = get_settings()
    history_enabled = bool(getattr(settings, "qa_history_enabled", True))

    # ── 三条校验提到**建会话之前**（2026-11-13 审计 m3）──
    #
    # 它们都只依赖请求体与 `current_user`，搬上来没有副作用；而留在生成器里时，
    # 一次"空问题"或"对别人的 note_id 提问"就已经在库里留下一条 `turn_count=0`
    # 的会话（还挂着一个用户并不拥有的 note_id）。
    if not req.question or not req.question.strip():
        raise AppError(EMPTY_QUESTION, "问题不能为空", 422)
    if not req.selected_text or not req.selected_text.strip():
        raise AppError(EMPTY_SELECTED_TEXT, "选中文本不能为空", 422)
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    session = None
    if history_enabled:
        if req.session_id:
            # `surface` + `note_id` 都是**校验**（审计 M1 / 第二轮 MINOR-1）：不能把浮层轮次
            # 追加进全库问答会话，也不能把 note B 的内容写进「note A 的对话」。
            session = await qa_service.get_or_create_session(
                db,
                user_id=current_user.id,
                session_id=req.session_id,
                surface=SURFACE_NOTE,
                expect_note_id=note_id,
            )
            if session is None:
                # 与全局端点同一语义（第二轮 MINOR-1）：给了 id 取不到就 404，
                # **不静默新建**。原写法会悄悄长出一条新会话，客户端还以为续上了。
                still_there = await qa_service.get_session_by_id(db, user_id=current_user.id, session_id=req.session_id)
                if still_there is not None:
                    raise AppError(QA_SESSION_SURFACE_MISMATCH, "这个对话属于另一个入口", 404)
                raise AppError(QA_SESSION_NOT_FOUND, "对话不存在或已被删除", 404)
        if session is None:
            session = await qa_service.get_or_create_session_for_note(
                db, user_id=current_user.id, note_id=note_id, question=req.question
            )
        # 把连接还回去（审计 m7）：流的整个期间不再需要请求作用域的 session
        await db.commit()
    session_id = session.id if session is not None else None

    async def _persist_completed_turn(
        *,
        status: str,
        answer_text: str,
        error_code: Optional[str] = None,
        provider: Optional[str] = None,
        latency_ms: Optional[int] = None,
    ) -> None:
        """用独立 DB 会话落库（不依赖请求作用域的 `db`，理由同全局问答端点）"""
        if not history_enabled or session_id is None:
            return
        try:
            factory = get_session_factory()
            async with factory() as write_db:
                # 显式传 surface（审计 M1：默认值会把 note 会话过滤掉 → 静默不落库）
                target = await qa_service.get_or_create_session(
                    write_db, user_id=current_user.id, session_id=session_id, surface=SURFACE_NOTE
                )
                if target is None:
                    return
                await qa_service.record_turn(
                    write_db,
                    user_id=current_user.id,
                    session=target,
                    question=req.question,
                    answer=answer_text or None,
                    status=status,
                    error_code=error_code,
                    provider=provider,
                    sources=None,
                    context_turns=0,
                    source_span_start=None,
                    source_span_end=None,
                    latency_ms=latency_ms,
                )
                await write_db.commit()
        except Exception as exc:  # noqa: BLE001 - 附加功能绝不打断回答
            logger.warning("笔记问答历史落库失败（忽略继续）: %s: %s", type(exc).__name__, exc)

    async def event_stream():
        answer_parts: list[str] = []
        started_at = time.monotonic()
        try:
            # 笔记归属与输入校验已在**建会话之前**完成（审计 m3），这里不再重复

            # 组装选区局部上下文（参考内容由前端提交，仅来自当前笔记）
            context_parts: list[str] = []
            if req.context_before:
                context_parts.append(req.context_before)
            context_parts.append(f"【选中文本】{req.selected_text}")
            if req.context_after:
                context_parts.append(req.context_after)
            context = "\n".join(context_parts)

            provider = get_settings().get_llm_config()["provider"]

            # 首事件：LLM 提供商 + 会话（前端据 session_id 续聊、据 history_used 提示）
            yield f"event: meta\ndata: {json.dumps({'provider': provider, 'session_id': session_id, 'history_used': 0}, ensure_ascii=False)}\n\n"

            # 构建 messages
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一个知识渊博的学习助手。用户正在阅读其笔记并选中了其中一段文本向你提问。\n"
                        "回答原则：\n"
                        "1. 优先依据提供的笔记片段回答\n"
                        "2. 如果片段信息不足，可结合你自己的知识补充，但请说明这部分是基于你的知识补充的\n"
                        "3. 回答应聚焦于用户选中的文本及其上下文，详细有用"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"笔记《{note.title}》中用户选中的文本片段：\n{context}\n\n"
                        f"用户选中的文本：{req.selected_text}\n\n问题：{req.question}"
                    ),
                },
            ]

            # 流式输出 token（累积起来供落库：中止时也要保留已生成的部分）
            llm_service = LLMService()
            async for chunk in llm_service.chat_stream(messages, scene="note_ask_stream"):
                answer_parts.append(chunk)
                yield f"event: token\ndata: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"

            # 结束标记
            yield "event: done\ndata: {}\n\n"

            await _persist_completed_turn(
                status=TURN_STATUS_OK,
                answer_text="".join(answer_parts),
                provider=provider,
                latency_ms=int((time.monotonic() - started_at) * 1000),
            )
        except asyncio.CancelledError:
            # 客户端断开 / 用户停止：这一轮最需要回看，用 shield 写库（理由见全局问答端点）
            await asyncio.shield(
                _persist_completed_turn(
                    status=TURN_STATUS_ABORTED,
                    answer_text="".join(answer_parts),
                    latency_ms=int((time.monotonic() - started_at) * 1000),
                )
            )
            raise
        except Exception as e:
            logger.warning(f"SSE 笔记 AI 提问失败: {e}", exc_info=True)
            # 同全局端点：只下发稳定码 + 固定文案，不把异常文本送到界面上（MINOR-5）
            yield f"event: error\ndata: {json.dumps({'message': '生成回答时出错，请稍后重试', 'error_code': classify_exception(e)}, ensure_ascii=False)}\n\n"
            await _persist_completed_turn(
                status=TURN_STATUS_FAILED,
                answer_text="".join(answer_parts),
                error_code=classify_exception(e),
                latency_ms=int((time.monotonic() - started_at) * 1000),
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
