"""笔记浮层问答的请求体

## `session_id`（2026-11-13）

可选，且**本轮不用于多轮上下文** —— 浮层是"读这段时临时问一句"，把最近几轮
拼进去会改变这个功能的语义（它问的是"这段文本"，不是"这个会话"）。

字段存在的意义只有一个：**落库分组**。前端目前不传，服务端按"同一篇笔记 +
30 分钟内"复用同一个会话（见 `qa_history_service.get_or_create_session_for_note`），
避免用户每问一次就在历史里多出一条孤立会话。
"""

from typing import Optional

from pydantic import BaseModel


class NoteAskRequest(BaseModel):
    question: str
    selected_text: str
    context_before: str = ""
    context_after: str = ""
    view_mode: str = "original"  # original / clean（仅供参考语义，后端不据此读取全文）
    session_id: Optional[str] = None
