"""笔记浮层问答的请求体

## `session_id`（2026-11-13）

可选，且**本轮不用于多轮上下文** —— 浮层是"读这段时临时问一句"，把最近几轮
拼进去会改变这个功能的语义（它问的是"这段文本"，不是"这个会话"）。

字段存在的意义只有一个：**落库分组**。前端目前不传，服务端按"同一篇笔记 +
30 分钟内"复用同一个会话（见 `qa_history_service.get_or_create_session_for_note`），
避免用户每问一次就在历史里多出一条孤立会话。
"""

from typing import Optional

from pydantic import BaseModel, Field


class NoteAskRequest(BaseModel):
    # 上限（第二轮审计 MINOR-13）：这四个字段都会进 prompt 与 `qa_turns.question`。
    # 此前全部无 `max_length`，一次超长粘贴就能把上下文顶爆。
    question: str = Field(..., max_length=4000)
    selected_text: str = Field(..., max_length=8000)
    context_before: str = Field("", max_length=4000)
    context_after: str = Field("", max_length=4000)
    view_mode: str = "original"  # original / clean（仅供参考语义，后端不据此读取全文）
    session_id: Optional[str] = Field(None, max_length=64)
