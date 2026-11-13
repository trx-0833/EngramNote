"""
问答历史的接口契约（docs/qa-history-plan.md §3）

## 为什么这些模型都显式列字段而不是复用 ORM

端点响应就是**契约**：`dump_openapi.py` 把这里生成到 `openapi.json`，
前端再由 `gen:api` 生成 `schema.ts`。直接用 ORM 对象会让契约跟着表结构漂移 ——
表里加一列就悄悄出现在 API 里，而前端类型不会知道该不该用。

## `sources` 为什么是 `List[Dict]` 而不是强类型

它来自检索层（`rag_service.retrieve_context` 的 `sources`），字段集合由检索实现决定
（`note_id` / `note_title` / `chunk_id` / `snippet` / `score`，且未来可能加 `page`）。
钉成强类型会让"检索层加一个字段"变成一次契约变更；而前端只把它当"展示用的引用卡片"。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class QATurnResponse(BaseModel):
    """一轮问答（历史详情里的条目）"""

    id: str
    question: str
    answer: Optional[str] = None
    #: `ok` ｜ `failed` ｜ `aborted`。前端的失败态展示与"是否进了上下文"都靠它
    status: str
    error_code: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    retrieval_status: Optional[str] = None
    #: 本轮实际用于检索的查询词（追问时是"上一问 + 当前问"）
    retrieval_query: Optional[str] = None
    #: 引用来源（检索层原样返回；`snippet` 已截到 200 字）
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    #: 本轮实际带上了几轮历史（预算裁剪后的真实值）
    context_turns: int = 0
    latency_ms: Optional[int] = None
    created_at: datetime


class QASessionResponse(BaseModel):
    """会话摘要（列表页用，不含轮次）"""

    id: str
    surface: str
    note_id: Optional[str] = None
    title: Optional[str] = None
    turn_count: int = 0
    last_question: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class QALimitsResponse(BaseModel):
    """问答功能当前生效的两个数值上限

    ## 为什么让列表接口顺带下发（2026-11-13 第二轮审计 MINOR-9）

    前端三句提示里的数字（最近 N 轮 / 保留 N 天）原本是**编译期镜像常量**：
    部署侧在 `.env` 里把 `QA_CONTEXT_MAX_TURNS` 改成 3，界面仍写"最近 6 轮"，
    而守卫用例只比 `Settings()` 默认值 —— CI 永远是 6，门禁照绿。
    这与计划 §5.4「数字从配置来，写死就会在某天调参后变成谎言」直接冲突。

    最小修法是**不新增端点**、只在已有的列表响应里多两个字段（计划当初否决
    `/api/qa/config` 的理由就是"为一个纯展示数字多一处契约"，这里是它的最小心形态）。
    前端拿不到（老后端 / 请求失败）时回退到常量，不至于让提示空掉。
    """

    context_turns: int = 0
    retention_days: int = 0


class QASessionListResponse(BaseModel):
    """会话列表（分页）"""

    items: List[QASessionResponse] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    #: 当前生效的数值上限（见 `QALimitsResponse` 的说明）
    limits: Optional[QALimitsResponse] = None


class QASessionDetailResponse(BaseModel):
    """单个会话 + 其轮次（按时间升序）」"""

    session: QASessionResponse
    turns: List[QATurnResponse] = Field(default_factory=list)


class QAClearAllResponse(BaseModel):
    """「清空全部问答历史」的结果

    为什么不返回 204：用户点完需要看到"清掉了几条"，而那正是这次操作唯一的反馈
    （列表会变空，但"本来就空"与"清掉了 12 条"在界面上一样）。
    """

    deleted: int = 0
