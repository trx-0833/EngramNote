"""学习评估模块 Pydantic Schema

## 阶段 5.1 的修复范围

本模块的 3 个端点（`compare` / `generate-quiz` / `submit-answer`）**一直有**
`response_model=AssessmentResponse`，所以它们在 OpenAPI 里不是"空 schema"。
真正的问题在**内层**：

| 字段 | 修复前 | 前端按什么解析 |
|---|---|---|
| `scores` | `Dict[str, Any]` → `additionalProperties: true` | `AssessmentScores`（6 个可选字段） |
| `quiz_questions[]` | `List[Dict[str, Any]]` → 同上 | `{index, question, key_points}` |
| `quiz_answers[]` | `List[Dict[str, Any]]` → 同上 | `{answer, judgment: {accuracy_score, completeness_score, depth_score, feedback}}` |

也就是说 schema 只说"这是个对象"，而前端手写了完整结构 ——
**正是 5.1 想消灭的那类风险**：后端改字段，前端不会知道。

本轮把这三处按**运行时真实形状**补全。形状的来源见各模型的"出处"注释，
不是照抄前端类型。
"""

from pydantic import BaseModel, Field, model_serializer
from typing import List, Optional
from datetime import datetime


class CompareRequest(BaseModel):
    material_note_ids: List[str]
    personal_note_ids: List[str]


class QuizGenerateRequest(BaseModel):
    material_note_ids: List[str]
    personal_note_id: Optional[str] = None


class AnswerSubmitRequest(BaseModel):
    assessment_id: str
    answers: List[dict]  # [{question_index: int, answer: str}]


class AssessmentScores(BaseModel):
    """评估评分明细

    ## 出处与"为什么是一个并集模型"

    `AssessmentResult.scores` 是一个 JSON 列，**同一个字段承载两种互斥形状**，
    由 `mode` 决定（`services/assessment_service.py`）：

    - `mode == "compare"`（`compare_assessment`，第 102-108 行）恒有 5 个键：
      `coverage_score` / `depth_score` / `clarity_score` /
      `covered_points` / `uncovered_points`
    - `mode == "quiz"` 且**已提交答案**（`submit_answers`，第 349-352 行）恒有 2 个键：
      `total_questions` / `average_score`
    - `mode == "quiz"` 且**尚未作答**（`generate_quiz`，第 154 / 233 行）是 **`{}`**
      —— 空字典，不是"缺字段"

    ## 为什么全部字段可选

    三种形态必须共用同一个模型（OpenAPI 的 `$ref` 无法按兄弟字段 `mode` 分流，
    pydantic 也没有"判别式联合"能表达"看 mode 决定 scores 的形状"）。
    全部可选是**如实**的：任何单个字段都不是"任何模式下恒在"。
    代价是前端拿不到"compare 模式一定有 coverage_score"的保证 ——
    要拿到它就得把 `scores` 拆成两个端点专属字段，那是接口形状变更（产品决策），
    不在"补类型"的范围内。

    ## ⚠️ 为什么需要 `_omit_unset_none`（否则响应体会变）

    pydantic 默认把**有默认值但输入里没出现的字段**也序列化出来。于是
    `generate_quiz` 的 `scores={}` 会从 `{}` 变成

        {"covered_points": null, "uncovered_points": null, ..., "average_score": null}

    这是响应体形状变化，本阶段禁止。而 `exclude_none=True` 是**过粗**的工具：
    它会连"输入里显式为 null"的字段一起删（`covered_points: null` 本来是存在的）。
    正确判据是"**输入里出现过这个键吗**"，也就是 pydantic 的
    `__pydantic_fields_set__`：没出现过 + 值为 None → 省略；出现过 → 原样输出
    （含显式 null）。探针实测（`scripts/_tmp_ser_probe.py`，已删）：
    嵌套位置也会走这个序列化器，`Outer(inner={})` 得到 `{"inner": {}}`。

    同理，compare 模式里 `covered_points` 显式为 `[]` 必须保持 `[]`（它"出现过"），
    不会被这条规则碰到。
    """

    # --- compare 模式 ---
    covered_points: Optional[List[str]] = Field(
        default=None, description="compare：笔记已覆盖的知识点"
    )
    uncovered_points: Optional[List[str]] = Field(
        default=None, description="compare：资料里有、笔记未覆盖的知识点"
    )
    coverage_score: Optional[float] = Field(
        default=None, description="compare：内容覆盖度 0-100"
    )
    # 注意：**没有** completeness_score。
    # 前端 `AssessmentScores` 声明了 `completeness_score?: number`，
    # 但后端 compare 分支从来不产出这个键（提示词要的是 coverage/depth/clarity
    # 三个维度）。属于"前端类型里有、后端不给"的一处 —— 与 7.4 的 file_size 同类。
    depth_score: Optional[float] = Field(
        default=None, description="compare：思考深度 0-100"
    )
    clarity_score: Optional[float] = Field(
        default=None, description="compare：结构清晰度 0-100"
    )
    # --- quiz 模式（提交答案后）---
    total_questions: Optional[int] = Field(
        default=None, description="quiz：本次作答的题目数"
    )
    average_score: Optional[float] = Field(
        default=None, description="quiz：各题 question_score 的算术平均"
    )

    @model_serializer(mode="wrap")
    def _omit_unset_none(self, handler):
        """省略"输入里没出现且值为 None"的字段（保住 `{}` / 部分键等形态）

        判据是 `__pydantic_fields_set__`（输入里真实出现过的键），
        不是"值是不是 None" —— 后者会把显式 null 一并删掉。
        详见类 docstring 的「为什么需要 `_omit_unset_none`」。
        """
        data = handler(self)
        provided = self.__pydantic_fields_set__
        return {k: v for k, v in data.items() if v is not None or k in provided}


class QuizQuestion(BaseModel):
    """开放性问题（`AssessmentResult.quiz_questions` 的元素）

    出处：`services/assessment_service.py` 第 205-209 行 ——
    **原样透传 LLM 返回的 `result_data["questions"]`**：

        result_data = json.loads(response)
        questions = result_data.get("questions", [])

    提示词（第 180-189 行）要求每项为
    `{"index": int, "question": str, "key_points": [str]}`。

    ⚠️ 三个字段都是**必填**：模型从这里往下游用，缺一个就是脏数据。
    之前是 `List[Dict[str, Any]]`，也就是说脏数据可以一路传到前端才炸
    （`getQuiz` 的页面按 `q.key_points.map(...)` 渲染）。
    这里收紧到必填的行为后果**仅限于"早失败"**：LLM 返回缺字段时，
    `/assessment/generate-quiz` 会抛 ResponseValidationError（500）而不是
    把半成品交给前端。这是有意为之，但**属于新引入的运行时风险**，
    见 `frontend/docs/openapi-client.md` 的记录。若实测触发，
    正确做法是给 `key_points` 一个 `default_factory=list` 而不是退回 `Any`。
    """

    index: int
    question: str
    key_points: List[str]


class QuizJudgment(BaseModel):
    """单题评判明细（`quiz_answers[].judgment`）

    出处：`services/assessment_service.py` 第 316-336 行 —— 同样是
    **LLM 返回的 JSON 原样透传**（`judgment = json.loads(response)`），
    解析失败时用一份全 0 的兜底字典（第 328-336 行），
    键为 `accuracy_score` / `completeness_score` / `depth_score` /
    `question_score` / `feedback` / `covered_key_points` / `missed_key_points`。

    提示词（第 294-303 行）只要求前 5 个键 + 两个列表；
    `question_score` 被 `submit_answers` 用来算总分（第 343 行
    `judgment.get("question_score", 0)`）。

    ⚠️ 与 `QuizQuestion` 不同，这里**全部字段可选**：兜底字典与提示词
    并不完全一致，而且 `covered_key_points` / `missed_key_points`
    在提示词里是"要求"而非"保证"。判分缺失是**可恢复**状态
    （总分按 0 计），不该让整个响应 500。

    ⚠️ 同样需要 `_omit_unset_none`：LLM 没返回某个键时，
    响应里不该多出一个 `null`（那会改变响应体形状）。
    """

    accuracy_score: Optional[float] = None
    completeness_score: Optional[float] = None
    depth_score: Optional[float] = None
    question_score: Optional[float] = None
    feedback: Optional[str] = None
    covered_key_points: Optional[List[str]] = None
    missed_key_points: Optional[List[str]] = None

    @model_serializer(mode="wrap")
    def _omit_unset_none(self, handler):
        """同 `AssessmentScores._omit_unset_none`：省略输入里没出现的 None 字段"""
        data = handler(self)
        provided = self.__pydantic_fields_set__
        return {k: v for k, v in data.items() if v is not None or k in provided}


class QuizAnswer(BaseModel):
    """一条作答及其评判（`AssessmentResult.quiz_answers` 的元素）

    出处：`services/assessment_service.py` 第 338-342 行：

        judged_answers.append({"question_index": q_idx,
                               "answer": user_answer,
                               "judgment": judgment})

    `question_index` 取自**请求体**里用户提交的 `question_index`
    （`answer.get("question_index", 0)`），因此它可能与
    `quiz_questions` 里的 `index` 不一致（例如用户改过题序）。
    前端 `QuizAnswerItem` 只声明了 `answer` / `judgment`，**没有**
    `question_index` —— 于是"这份答案对应哪道题"在前端类型里是缺失信息。
    """

    question_index: int
    answer: str
    judgment: Optional[QuizJudgment] = None


class AssessmentResponse(BaseModel):
    id: str
    mode: str
    #: 评分明细。**可空**：`AssessmentResult.scores` 在库里就是 nullable
    #: （`models/assessment.py:23` 是 `Mapped[Optional[Dict]]`）。
    #: 修复前的 `Dict[str, Any]` 同样允许 null，因此这里保持 `Optional`
    #: 是为了**不改变**"scores 可能是 null"这一契约事实。
    scores: Optional[AssessmentScores] = None
    overall_score: float
    suggestions: str
    quiz_questions: Optional[List[QuizQuestion]] = None
    quiz_answers: Optional[List[QuizAnswer]] = None
    link_signature: Optional[str] = None
    is_stale: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class AssessmentHistoryItem(BaseModel):
    id: str
    mode: str
    overall_score: float
    created_at: datetime

    model_config = {"from_attributes": True}
