"""
复习模块 Pydantic Schema

定义复习调度相关的请求/响应模型。
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models.quiz_item import QuestionType, DifficultyLevel


# --- 到期题目响应模型 ---

class DueQuizResponse(BaseModel):
    """到期题目响应"""
    id: str
    card_id: str
    note_id: str
    question_type: QuestionType
    difficulty: DifficultyLevel
    question: str
    options: Optional[str] = None
    next_review_at: Optional[datetime] = None
    review_count: int = 0
    interval: int = 1
    repetition: int = 0
    easiness_factor: float = 2.5

    model_config = {"from_attributes": True}


class DueQuizListResponse(BaseModel):
    """到期题目列表响应"""
    items: List[DueQuizResponse]
    total: int


# --- 提交答案请求/响应模型 ---

class SubmitAnswerRequest(BaseModel):
    """提交答案请求"""
    quiz_id: str
    user_answer: str
    time_spent_ms: int = 0
    self_rating: Optional[int] = Field(
        default=None,
        ge=0,
        le=5,
        description=(
            "用户自评的 SM-2 质量分（0-5）：0 完全忘记 / 3 勉强想起 / 4 想起 / 5 轻松。"
            "给出时优先于自动判分，并补完此前的占位提交（见 review_service.submit_answer）。"
        ),
    )
    use_semantic_grading: bool = Field(
        default=False,
        description=(
            "是否请求 LLM 语义判分（阶段 3.5，仅简答题有效）。"
            "**默认关闭**：判分在提交的同步路径上调用外部 LLM，"
            "会给每次提交叠加一次往返延迟（实测最坏约 10 秒），"
            "而两阶段流程本来就以用户自评为主评分来源。"
            "想用自动判分替代自评的客户端应显式置 true。"
        ),
    )


class SM2Info(BaseModel):
    """调度结果（**当前生效算法**的输出）

    ⚠️ 字段名 `sm2` 是历史遗留：阶段 3.6 之后调度默认由 FSRS-5 产生
    （`config.review_scheduler` 可回退 SM-2），这里报告的是**实际生效的那个
    算法**算出的间隔/次数/难度系数。改名会破坏前端契约，故保留字段名，
    但不要再把它理解为"SM-2 的输出" —— 它已经是"调度器的输出"。
    """
    interval: int
    repetition: int
    easiness_factor: float
    # 占位提交（自动判分不可信、等待用户自评）时不推进调度，此处为 None
    next_review_at: Optional[str] = None


class SubmitAnswerResponse(BaseModel):
    """提交答案响应"""
    quiz_id: str
    is_correct: bool
    quality: int
    correct_answer: str
    explanation: Optional[str] = None
    options: Optional[List[str]] = None
    question_type: str
    sm2: SM2Info
    self_rating: Optional[int] = Field(
        default=None, description="本次提交携带的自评分；未自评时为 null"
    )
    grading_method: str = Field(
        default="legacy",
        description="本次判分方式：choice/fill_blank/self_rating/ungraded/legacy",
    )
    needs_self_assessment: bool = Field(
        default=False,
        description="是否仍在等待用户自评（自动判分不可信且今日尚未自评）",
    )
    completing_placeholder: bool = Field(
        default=False, description="本次提交是否补完了此前的占位记录"
    )
    grading_reason: Optional[str] = Field(
        default=None, description="判分依据说明，供 UI 展示判分可信度"
    )

    @classmethod
    def from_service_result(cls, result: dict) -> "SubmitAnswerResponse":
        """由 review_service.submit_answer 的返回字典构造响应

        两个提交入口（/review/submit 与 /quick/{note_id}/submit）共用此转换，
        避免各自手抄字段——历史上正是手抄导致新增字段漏掉一个入口。
        """
        return cls(
            quiz_id=result["quiz_id"],
            is_correct=result["is_correct"],
            quality=result["quality"],
            correct_answer=result["correct_answer"],
            explanation=result.get("explanation"),
            options=result.get("options"),
            question_type=result.get("question_type", "choice"),
            sm2=result["sm2"],
            self_rating=result.get("self_rating"),
            grading_method=result.get("grading_method", "legacy"),
            needs_self_assessment=bool(result.get("needs_self_assessment", False)),
            completing_placeholder=bool(result.get("completing_placeholder", False)),
            grading_reason=result.get("grading_reason"),
        )


# --- 卡片直接复习（阶段 3.12）---

class CardReviewItem(BaseModel):
    """到期可复习的卡片"""
    card_id: str
    title: str
    content: str
    summary: Optional[str] = None
    card_type: str
    chapter_title: Optional[str] = None
    note_id: Optional[str] = None
    mastery_level: float = 0.0
    interval_days: int = 1
    repetition: int = 0
    easiness_factor: float = 2.5
    next_review_at: Optional[datetime] = None
    review_count: int = 0
    lapses: int = 0


class CardReviewListResponse(BaseModel):
    """到期卡片列表响应"""
    items: List[CardReviewItem]
    total: int


class CardReviewSubmitRequest(BaseModel):
    """提交卡片复习请求

    卡片复习没有题目，因此**必须**给自评分：没有可自动判分的答案。
    """
    self_rating: int = Field(
        ge=0,
        le=5,
        description="四档自评的 SM-2 质量分：0 完全忘记 / 3 勉强想起 / 4 想起 / 5 轻松",
    )
    user_answer: str = Field(default="", description="用户回忆内容的备注（可选）")
    time_spent_ms: int = Field(default=0, ge=0)


class CardReviewSubmitResponse(BaseModel):
    """卡片复习提交响应"""
    card_id: str
    quality: int
    is_correct: bool
    interval_days: int
    repetition: int
    easiness_factor: float
    next_review_at: Optional[datetime] = None
    mastery_level: float = Field(
        description="刷新后的掌握度（0-100，按时间衰减的新公式计算）"
    )
    stability: Optional[float] = Field(
        default=None,
        description=(
            "FSRS 的记忆强度 S（天）：回忆概率降到 90% 所需的天数。"
            "review_scheduler=sm2 回退时为 null（SM-2 没有这个量）。"
        ),
    )
    difficulty: Optional[float] = Field(
        default=None, description="FSRS 的难度 D（1-10）；SM-2 回退时为 null",
    )
    predicted_retention: Optional[float] = Field(
        default=None,
        description=(
            "复习**前**调度器预测的可回忆概率。它解释「为什么这次给这个间隔」："
            "0.6 表示模型认为你已接近遗忘，所以这次答对后间隔会涨得更多。"
        ),
    )


# --- 复习统计响应模型 ---
class ReviewStatsResponse(BaseModel):
    """复习统计响应"""
    due_count: int = 0
    today_done: int = 0
    today_correct: int = 0
    today_accuracy: float = 0.0
    total_reviews: int = 0
    total_correct: int = 0
    total_accuracy: float = 0.0
    total_quizzes: int = 0
    daily_limit: int = 10  # 每日答题上限（前端据此显示进度，单一来源，见 docs/decisions.md#F-12）


# --- 复习历史响应模型 ---

class ReviewHistoryItem(BaseModel):
    """复习历史条目"""
    id: str
    quiz_id: str
    note_id: str
    user_answer: str
    is_correct: bool
    quality: int
    time_spent_ms: int
    review_at: Optional[str] = None


class ReviewHistoryResponse(BaseModel):
    """复习历史响应"""
    items: List[ReviewHistoryItem]
    total: int
    page: int
    page_size: int


# --- 复习提醒响应模型 ---

class ReminderResponse(BaseModel):
    """
    复习提醒响应

    返回当前用户的复习提醒概览数据，包括到期题目数、
    1 小时内到期题目数、薄弱知识点数和上次提醒时间。

    字段与 notification_service.NotificationService.get_reminders() 返回的字典键一致。
    """
    due_count: int = Field(description="当前到期需要复习的题目数")
    due_in_1h_count: int = Field(description="1小时内到期的题目数")
    weak_point_count: int = Field(description="薄弱知识点数（mastery_level < 60）")
    last_reminded_at: Optional[datetime] = Field(
        default=None, description="上次提醒时间"
    )

    model_config = ConfigDict(from_attributes=True)
