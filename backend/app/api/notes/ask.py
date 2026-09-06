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
"""

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...database import get_db
from ...models.user import User
from ...schemas.note_ask import NoteAskRequest
from ...services.llm_service import LLMService
from ...services.note_service import get_note_detail
from ..auth import get_current_user_dependency

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/{note_id}/ask/stream")
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

    async def event_stream():
        try:
            # 校验笔记归属（用户只能访问自己的笔记）
            note = await get_note_detail(db, note_id, current_user.id)
            if not note:
                yield f"event: error\ndata: {json.dumps({'message': '笔记不存在'}, ensure_ascii=False)}\n\n"
                return

            # 拒绝空问题/空选中文本，避免无意义地消耗一次 LLM 调用
            if not req.question or not req.question.strip():
                yield f"event: error\ndata: {json.dumps({'message': '问题不能为空', 'error_code': 'EMPTY_QUESTION'}, ensure_ascii=False)}\n\n"
                return
            if not req.selected_text or not req.selected_text.strip():
                yield f"event: error\ndata: {json.dumps({'message': '选中文本不能为空', 'error_code': 'EMPTY_SELECTED_TEXT'}, ensure_ascii=False)}\n\n"
                return

            # 组装选区局部上下文（参考内容由前端提交，仅来自当前笔记）
            context_parts: list[str] = []
            if req.context_before:
                context_parts.append(req.context_before)
            context_parts.append(f"【选中文本】{req.selected_text}")
            if req.context_after:
                context_parts.append(req.context_after)
            context = "\n".join(context_parts)

            provider = get_settings().get_llm_config()["provider"]

            # 首事件：LLM 提供商标识
            yield f"event: meta\ndata: {json.dumps({'provider': provider}, ensure_ascii=False)}\n\n"

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

            # 流式输出 token
            llm_service = LLMService()
            async for chunk in llm_service.chat_stream(messages, scene="note_ask_stream"):
                yield f"event: token\ndata: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"

            # 结束标记
            yield "event: done\ndata: {}\n\n"
        except Exception as e:
            logger.warning(f"SSE 笔记 AI 提问失败: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )