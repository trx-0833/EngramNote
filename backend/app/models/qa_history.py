"""
问答历史模型模块（documents/qa-history-plan.md §2）

本模块定义 **AI 问答的持久化**：一次会话（`qa_sessions`）与其轮次（`qa_turns`）。

## 为什么需要它们

改造前问答内容**零持久化**：前端 `history` 只是组件内存（`pages/QA.tsx`），
后端没有会话表、没有历史端点，流式问答也不做响应缓存；`llm_calls` 账本只记
token / cost / latency，**不记 prompt 与答案文本**。于是"刷新就没了"不是读取缺陷，
而是根本没有存储位置。

## 会话（session）是**上下文边界**，不只是分组

第二轮起服务端按 `session_id` 查出最近 N 轮（`ok` 状态）作为 `messages` 送给模型，
使"那它的寿命呢"这类省略主语的追问能接上。组装与预算裁剪在
`services/qa_context.py`，落库在 `services/qa_history_service.py`。

## 三个刻意的取舍（与计划 §2.3 一致）

1. **不存组装后的完整 prompt**：体积大、含内部提示词；复现靠
   `(question, 最近 N 轮的 question/answer, chunk_id 列表, prompt_version)` 重放。
2. **不存 token 流中间态**：只存最终答案；中止的轮次存 `status=aborted` + 已生成部分
   —— 用户点「停止生成」的那一轮恰恰最需要回看。
3. **`retrieval_query` 必须存**：多轮里"检索词被上一句改写"是新的失败面，
   不落库就无法回答"这轮为什么没检索到"。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel, TZDateTime

#: `surface` 取值：全库问答（`/qa`）／笔记浮层的选中文本提问
SURFACE_GLOBAL = "global"
SURFACE_NOTE = "note"
SURFACES = (SURFACE_GLOBAL, SURFACE_NOTE)

#: 轮次状态。`failed` / `aborted` **入历史但不进上下文**（计划 §4.4）：
#: 没有答案或只有半截答案的轮次当上下文用，比不给上下文更糟
TURN_STATUS_OK = "ok"
TURN_STATUS_FAILED = "failed"
TURN_STATUS_ABORTED = "aborted"
TURN_STATUSES = (TURN_STATUS_OK, TURN_STATUS_FAILED, TURN_STATUS_ABORTED)


class QASession(BaseModel):
    """
    一次问答会话

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        user_id: 所属用户（索引：列表查询的过滤条件）
        surface: `global`（`/qa`）／`note`（笔记浮层）
        note_id: 仅 `surface=note` 时有值。**刻意不设外键** —— 笔记被 purge 后
            历史仍应可读（那时 `note_id` 只是个再也查不到的 id，而不是一条约束错误）
        title: 首个问题截断后的标题；`None` 时前端显示"未命名对话"
        turn_count: 冗余计数，列表页不必为每行数轮次
        last_question: 列表副标题（"最后一句问的是什么"）
        deleted_at: 软删时刻；非空即对用户不可见
        created_at / updated_at: 继承；`updated_at` 由追加轮次时显式刷新，
            列表按它倒序（= 最近聊过的在最上面）
    """

    __tablename__ = "qa_sessions"

    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False, index=True)
    surface: Mapped[str] = mapped_column(String(16), nullable=False, default=SURFACE_GLOBAL)
    # 不设外键：笔记删除后历史应保留（见类 docstring）
    note_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_question: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 列表页的查询形状：WHERE user_id = ? AND deleted_at IS NULL ORDER BY updated_at DESC
        Index("ix_qa_sessions_user_updated", "user_id", "updated_at"),
        # 清理任务：WHERE deleted_at < cutoff
        Index("ix_qa_sessions_deleted", "deleted_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<QASession {self.id[:8]} {self.surface} turns={self.turn_count}>"


class QATurn(BaseModel):
    """
    会话中的一轮问答

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        session_id: 所属会话，**级联删除**（删会话即删轮次）
        user_id: 冗余一份 —— 列表与"最近 N 轮"查询都只按它过滤，省一次 join
        question: 用户输入原文
        answer: **最终**答案（不含 token 中间态）；`aborted` 时是已生成的部分
        status: `ok` / `failed` / `aborted`
        error_code: 失败时的机器可读码（与 `core/app_error.py` 同源）
        provider / model: 答案由谁产出
        prompt_version: 提示词版本，便于将来做"改一处带来多大改善"的对照
        retrieval_status: 仅 `surface=global`：`full_vector` / `hybrid` 等
        retrieval_query: **实际用于检索的查询词**（追问时是"上一问 + 当前问"拼接的）
        sources_json: 最终引用数组的 JSON 文本（note_id / note_title / chunk_id /
            snippet ≤200 字 / score）
        context_turns: 本轮**实际**带上了几轮历史（预算裁剪后的真实值，
            不是"想带几轮"—— 出问题时要看的是前者）
        source_span_start / source_span_end / source_hash: 笔记场景的选区定位。
            **不存选中文本全文**：正文本来就在库里、同一用户可回查；
            存快照会让"删了笔记，历史里还残留正文副本"
        latency_ms: 整轮耗时（与 `llm_calls.latency_ms` 同口径）
        created_at: 继承（轮次按它升序展示）
    """

    __tablename__ = "qa_turns"

    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("qa_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TURN_STATUS_OK)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    retrieval_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    retrieval_query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sources_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    context_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_span_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_span_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        # 组装上下文：WHERE session_id = ? AND status = 'ok' ORDER BY created_at DESC LIMIT n
        Index("ix_qa_turns_session_created", "session_id", "created_at"),
        # 按用户检索历史（关键词搜索走 question/answer，见服务层）
        Index("ix_qa_turns_user_created", "user_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<QATurn {self.id[:8]} status={self.status} session={self.session_id[:8]}>"
