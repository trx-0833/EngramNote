"""
复习记录模型模块

本模块定义了复习记录数据表模型，记录用户每次答题的详细信息，
是间隔重复算法的数据基础。

主要职责：
- 定义复习记录表结构，关联用户、题目和笔记
- 记录用户答案、正误判断、SM-2 评分和答题耗时

设计决策：
- note_id 冗余存储，方便按笔记维度查询复习记录
- quality 字段存储 SM-2 算法的 0-5 评分，便于后续分析
- self_rating 单独成列（而非复用 quality），保留自动判分与用户自评的
  双份信号，供后续校准
- rating / predicted_retention 同样是**单独成列**：它们是 FSRS 口径的量，
  与 SM-2 的 quality 不可互换；而 predicted_retention 只能在复习那一刻
  写下，事后无法重建（阶段 3.6）
- time_spent_ms 记录答题耗时，可用于薄弱点分析
"""

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import String, Integer, Boolean, Text, ForeignKey, JSON, Float
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel, TZDateTime


class ReviewLog(BaseModel):
    """
    复习记录模型

    对应数据库中的 review_logs 表。
    每条记录代表用户对一道题的一次作答。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        user_id: 所属用户 ID，外键关联 users 表
        quiz_id: 关联题目 ID；卡片级复习时为 NULL
        card_id: 关联知识卡片 ID；题目可能被重新生成替换，卡片 ID 才是稳定的归属
        note_id: 来源笔记 ID，外键关联 notes 表（冗余，方便查询）
        user_answer: 用户提交的答案
        is_correct: 是否正确
        quality: 实际进入调度的评分 (0-5)
        self_rating: 用户自评评分 (0-5)，未自评时为 NULL
        grading_method: 本次判分方式（choice/fill_blank/self_rating/ungraded/legacy）
        rating: FSRS 档位 (1-4)；SM-2 路径下为 NULL
        predicted_retention: 复习前模型预测的可回忆概率；SM-2 路径下为 NULL
        item_type: 学习项类型 (quiz/card)；本列引入前的历史行 NULL
        time_spent_ms: 答题耗时（毫秒）
        review_at: 答题时间
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "review_logs"

    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)
    # 关联题目；**允许 NULL**（阶段 3.12 卡片直接复习时没有题目）。
    #
    # 改 nullable 的理由：卡片可以直接复习之后，"一次复习"不再必然对应
    # 一道题。用空 quiz_id 表达"这是卡片级复习"比伪造一个假题目诚实得多。
    # 注意孤儿检查里 `quiz_id IS NOT NULL AND quiz_id NOT IN (...)` 的写法
    # 已经正确处理了 NULL（NULL 不参与 NOT IN 比较）。
    quiz_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("quiz_items.id"), index=True, nullable=True
    )
    # 关联知识卡片（阶段 3.2 事件流化）
    #
    # 为什么必须单独存而不是"从题目反查"：
    # 1. 卡片级复习（阶段 3.12）没有题目，反查无从下手 ——
    #    实测这是当前 schema 的硬伤：卡片复习记录无法归到任何卡片。
    # 2. 题目会被"重新理解"整批替换（`generate_questions`），届时
    #    `quiz_id` 指向的行消失，历史复习记录就再也找不到它属于哪张卡。
    #    而 `card_id` 是稳定的 —— 这正是 overhaul-plan 症状 D-3
    #    「重跑理解丢学习历史」的根因。
    # 3. 度量层（阶段 3.14）需要按卡片聚合复习事件才能算保持率。
    card_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("knowledge_cards.id"), index=True, nullable=True
    )
    # 来源笔记 ID（冗余）；物理删除笔记时置 NULL（悬挂保留），复习历史不因笔记删除而丢失
    note_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("notes.id", ondelete="SET NULL"), index=True, nullable=True
    )
    user_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # quality 是「进入 SM-2 调度的那个分」：自评提交时等于 self_rating；
    # 自动判分占位提交时等于 grade_answer 给出的分。
    quality: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 用户自评（0-5，SM-2 quality 语义）；NULL = 本次未自评。
    #
    # 为什么必须与 quality 分开存：简答题无法用字符匹配可靠判分
    # （见 sm2_service.grade_answer），所以流程是「自动判分占位 → 用户自评确认」。
    # 一次自评会产生两条 ReviewLog：一条自动占位记录（grading_method=ungraded），
    # 一条带 self_rating 的最终记录。只有两条都在，才能统计
    # 「自动判分 vs 用户自评」的不一致率 —— 这正是 overhaul-plan.md
    # 阶段 3.14 校准曲线所需的原始信号。把 self_rating 覆盖到 quality
    # 会不可逆地丢掉这个信号，故而不复用 quality。
    self_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 本次判分方式，取值：
    #   'choice'      选择题自动判分（可靠）
    #   'fill_blank'  填空题自动判分（经归一化+编辑距离容错）
    #   'self_rating' 用户自评（quality == self_rating）
    #   'ungraded'    自动判分不可信，仅占位、**未推进调度**，等用户自评
    #   'legacy'      本列引入前的历史行，判分方式不可考
    #
    # 这一列的用途不只是展示：幂等守卫必须区分「占位提交」与「已判分提交」，
    # 否则用户自评那一次会被当成重复提交挡掉，自评永远写不进库。
    grading_method: Mapped[str] = mapped_column(String(16), nullable=False, default="legacy")
    # 结构化判分明细（阶段 3.5）：LLM 语义判分的结果
    #
    # 形如 {"verdict": "partial", "missing_points": [...],
    #       "misconceptions": [...], "confidence": 0.82, "reason": "..."}
    #
    # 为什么单独存 JSON 而不是拆成列：
    # 1. 这三类信息是**可变的展示内容**，将来还可能加"建议补充"之类的字段，
    #    拆列会变成每次都要迁移；
    # 2. 它们只用于展示与复盘，**不参与任何计算** —— 参与调度的只有
    #    `quality` / `is_correct`。把展示数据与调度数据分开，
    #    可以避免"为了改展示而动了调度口径"。
    #
    # 为什么不能从这里反推 quality：verdict→quality 的映射是**策略**，
    # 会随阈值调整而变；历史记录必须保留当时的判定结果，
    # 否则重算会改写历史（校准曲线正是靠"当时的判分"与"后来的表现"对比）。
    grading_detail: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "grading_detail", JSON, nullable=True
    )
    time_spent_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # ---- 阶段 3.2 / 3.6：把复习记录做成**可分析的不可变事件流** ----
    #
    # 下面三列是阶段 3.2 要求的"事件流化"字段。它们在 3.6（FSRS）时补上
    # 而不是更晚，原因是其中 `predicted_retention` **是一扇单向门**：
    # 它是"调度器在复习发生前预测的可回忆概率"，只能在复习那一刻写下，
    # 事后无法重建（重建需要当时的 S，而 S 已经被这次复习更新了）。
    # 校准曲线（3.14：预测 0.9 的那批卡实际答对多少）完全依赖它，
    # 所以哪怕 3.14 还没做，也必须现在开始积累。
    #
    #: FSRS 档位 1-4（Again/Hard/Good/Easy）。
    #:
    #: 为什么不复用 `quality`：quality 是 SM-2 的 0-5 尺度，二者是
    #: **不同的量**（quality=5 在机器判分与自评下含义不同，见
    #: `scheduler_service.rating_from_quality`）。要在同一列里塞两种尺度，
    #: 就必须再加一列记"这行是哪个尺度"，反而更贵。
    #: FSRS 参数拟合（3.14）直接消费这一列，因此它必须是无歧义的 4 档。
    #:
    #: NULL = 本行产生于 SM-2 时期或 SM-2 回退路径，没有 FSRS 档位。
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: 复习**前**调度器预测的可回忆概率 R ∈ (0,1]。
    #:
    #: NULL = 没有模型预测可记（SM-2 路径）。这里刻意**不用 SM-2 的
    #: 近似公式填数**：SM-2 没有保持率模型，任何填进去的数字都是我们的
    #: 发明而不是那个算法的输出，而校准曲线一旦混入两种来源的数字就失去
    #: 意义（分母里混着不是"预测"的东西）。
    predicted_retention: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    #: 本次复习的学习项类型：'quiz'（答题）| 'card'（卡片直接复习）。
    #:
    #: 卡片级复习的 quiz_id 为 NULL，理论上可以据此区分，但那个推断
    #: 依赖"卡片复习永远没有题目"这一实现细节；显式成列后，
    #: 度量层可以直接 GROUP BY item_type，不必知道调度器的内部约定。
    #: NULL = 本列引入前的历史行。
    item_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    review_at: Mapped[datetime] = mapped_column(TZDateTime(timezone=True), nullable=False, index=True)
