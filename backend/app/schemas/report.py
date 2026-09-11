"""
学习报告 Pydantic Schema

定义学习报告相关的响应模型。
"""

from typing import List, Optional

from pydantic import BaseModel, Field


# --- 今日学习报告 ---

class QuestionTypeAccuracy(BaseModel):
    """各题型正确率"""
    question_type: str
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0


class DailyReportResponse(BaseModel):
    """今日学习报告响应"""
    date: str
    new_mastered: int = 0
    total_review_time_ms: int = 0
    total_reviews: int = 0
    today_accuracy: float = 0.0
    weak_point_count: int = 0
    question_type_accuracy: List[QuestionTypeAccuracy] = []


# --- 7天趋势 ---

class WeeklyTrendItem(BaseModel):
    """单日趋势数据"""
    date: str
    review_count: int = 0
    correct_count: int = 0
    accuracy: float = 0.0


class WeeklyTrendResponse(BaseModel):
    """7天趋势响应"""
    items: List[WeeklyTrendItem]
    total_reviews: int = 0
    avg_accuracy: float = 0.0


# --- 薄弱点 ---

class WeakPointItem(BaseModel):
    """薄弱点条目"""
    card_id: str
    card_title: str
    card_type: str
    note_id: str
    note_title: str = ""
    error_count: int = 0
    total_reviews: int = 0
    accuracy: float = 0.0


class WeakPointsResponse(BaseModel):
    """薄弱点列表响应"""
    items: List[WeakPointItem]
    total: int


# --- 学习度量（阶段 3.14）---
#
# 每个度量都带 `insufficient_data` 与 `min_sample`。这是刻意的：
# 现场实测当前库里「间隔 > 0 的相邻复习对」为 0、自评样本为 0，
# 若只返回曲线数据，前端会画出空图或误导性的图；带上这两个字段，
# 前端可以明确显示"还需要多少样本"。

class RetentionBucket(BaseModel):
    """保持率分桶"""
    label: str = Field(description="可读区间名，如 '3-7天'")
    lower_days: int
    upper_days: Optional[int] = None
    total: int = Field(description="该桶的样本数")
    passed: int = Field(description="其中后续复习通过的次数")
    retention: float = Field(description="保持率百分比")


class RetentionCurve(BaseModel):
    """保持率曲线"""
    buckets: List[RetentionBucket] = Field(default_factory=list)
    sample_size: int = 0
    insufficient_data: bool = True
    min_sample: int = 20


class CalibrationTier(BaseModel):
    """校准曲线的一档"""
    self_rating: int = Field(description="自评分（0/3/4/5）")
    predicted_accuracy: float = Field(description="自评隐含的预期正确率")
    actual_accuracy: float = Field(description="实际观测到的正确率")
    total: int = 0
    passed: int = 0


class CalibrationCurve(BaseModel):
    """校准曲线：说"想起来了"时是否真的想起来了"""
    tiers: List[CalibrationTier] = Field(default_factory=list)
    sample_size: int = 0
    insufficient_data: bool = True
    min_sample: int = 20


class LeechCandidate(BaseModel):
    """顽固卡片候选"""
    item_key: str
    consecutive_lapses: int
    total_reviews: int


class LapseDistribution(BaseModel):
    """遗忘分布"""
    leech_candidates: List[LeechCandidate] = Field(default_factory=list)
    max_consecutive_lapses: int = 0
    items: int = 0
    threshold: int = 8


class ReviewLoad(BaseModel):
    """复习负载（未来时间窗）"""
    due_now: int = 0
    next_24h: int = 0
    next_7d: int = 0
    next_30d: int = 0
    beyond_30d: int = 0


class ForecastDay(BaseModel):
    """某一天的到期量"""
    date: str
    count: int


class Forecast(BaseModel):
    """未来 30 天逐日到期量"""
    daily: List[ForecastDay] = Field(default_factory=list)
    overdue: int = 0
    beyond_30d: int = 0


class DataQuality(BaseModel):
    """数据质量与局限说明"""
    total_reviews: int = 0
    distinct_items: int = 0
    review_pairs: int = 0
    pairs_with_time_gap: int = 0
    self_rated_reviews: int = 0
    card_level_reviews: int = 0
    tracked_items: int = 0
    min_sample: int = 20
    notes: List[str] = Field(
        default_factory=list,
        description="面向用户的说明：为什么某些曲线还没有数据、缺什么",
    )


class LearningMetricsResponse(BaseModel):
    """学习度量汇总（阶段 3.14）"""
    retention: RetentionCurve
    calibration: CalibrationCurve
    lapses: LapseDistribution
    load: ReviewLoad
    forecast: Forecast
    data_quality: DataQuality
