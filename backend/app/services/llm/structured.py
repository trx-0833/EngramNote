"""LLM 结构化输出校验（阶段 4.1 原文的一条，阶段 4 收尾时补上）

## 为什么需要它

`parse_json_tolerant` 保证的是"能从响应里抢救出一段 JSON"，**不保证字段符合约定**。
在此之前，字段层面的违规有两种处理方式，都不可取：

| 处理方式 | 例子 | 为什么不可取 |
|---|---|---|
| **静默强制转换** | `card_type` 不认识 → 当 `concept`；`question_type` 不认识 → 当 `choice` | "模型正在乱返回"这件事永远不可见。用户看到的是一堆概念卡，而没人知道其中一部分本该是别的类型 |
| **靠下游兜底** | 缺 `title` 的卡片由 4.9 入库门拦下 | 只覆盖卡片：题目、拓展、联合分析、关系**当时都没有**对应的门（本模块把四条路径全部接上） |

## 本模块的原则：逐条校验 + 报告，**不整批拒绝**

一次响应里有个别坏条目是常态（模型在长批次里偶尔漏字段）。
整批拒绝会把 29 条好的和 1 条坏的一起丢掉 —— 而"重试一次就全好了"是错觉，
代价是**再付一次全额费用**。因此这里的产物永远是
`valid`（可用的条目）+ `issues`（每条的问题与处置），由调用方决定怎么上报。

## 两种处置，必须区分

- `rejected`：**字段级硬伤**（缺标题/缺内容/选项形状不对），这条不能用；
- `coerced`：**枚举值不认识**（`card_type="formula_extra"`）——
  条目仍然可用，按默认值处理，但**必须被报告**。

把两者混成一个"失败数"，就看不出"模型开始在枚举上胡说"这个信号 ——
而那正是提示词需要调整的前兆（与 4.6 的 `prompt_version` 配合使用：
按版本看 coerced 的比例，就能量化"这一版提示词的输出规范度"）。
"""

import logging
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger("engramnote.llm")

#: 卡片类型白名单（与 `models.knowledge_card.CardType` 一致）
CARD_TYPES: Tuple[str, ...] = ("concept", "formula", "qa", "definition")
#: 题目类型白名单
QUESTION_TYPES: Tuple[str, ...] = ("choice", "fill_blank", "short_answer")
#: 难度白名单
DIFFICULTIES: Tuple[str, ...] = ("easy", "medium", "hard")
#: 判分结论白名单
VERDICTS: Tuple[str, ...] = ("correct", "partial", "incorrect")
#: 卡片关系类型白名单
RELATION_TYPES: Tuple[str, ...] = ("prerequisite", "subsequent", "contrast")


class _CoercionAware(BaseModel):
    """带"强制转换记录"的基类

    枚举值不认识时**不报错**（条目仍可用），但把这件事记进
    `coercion_notes`，由 `validate_items` 提取成 issues。
    用 pydantic 的 `exclude=True` 保证它不会混进 `model_dump()` 的结果。
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    #: 形如 `"card_type='формула' 不是合法取值，已按 concept 处理"`
    coercion_notes: List[str] = Field(default_factory=list, exclude=True, repr=False)

    #: 子类声明：字段名 → (合法取值, 缺省值)
    #:
    #: ⚠️ 必须是 `ClassVar`：pydantic 会把"带注解的普通类属性"当成**模型字段**，
    #: 而子类再用无注解的同名属性覆盖就会直接报
    #: `Field 'ENUM_FIELDS' defined on a base class was overridden by a
    #: non-annotated attribute`（本轮实测踩到）。
    ENUM_FIELDS: ClassVar[Dict[str, Tuple[Sequence[str], str]]] = {}
    #: 子类声明：需要"缺失即拒绝"的字段（空白字符串等同于缺失）
    REQUIRED_TEXT_FIELDS: ClassVar[Tuple[str, ...]] = ()

    @model_validator(mode="before")
    @classmethod
    def _coerce_and_note(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        notes: List[str] = []

        for field, (allowed, default) in cls.ENUM_FIELDS.items():
            raw = data.get(field)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                data[field] = default
                continue
            value = str(raw).strip().lower()
            if value in allowed:
                data[field] = value
            else:
                notes.append(f"{field}={raw!r} 不是合法取值，已按 {default!r} 处理")
                data[field] = default

        for field in cls.REQUIRED_TEXT_FIELDS:
            raw = data.get(field)
            # 非字符串（数字/列表）一律当作缺失：它们无法安全地转成文本
            if raw is None or not isinstance(raw, str) or not raw.strip():
                data.pop(field, None)

        # 选项：字符串形态（含 JSON 字符串）统一成列表，形状不对则留空并记一笔
        if "options" in cls.model_fields:
            data["options"] = _normalize_options(data.get("options"), notes)

        if notes:
            data["coercion_notes"] = list(data.get("coercion_notes") or []) + notes
        return data

    @model_validator(mode="after")
    def _require_text(self) -> "_CoercionAware":
        missing = [f for f in self.REQUIRED_TEXT_FIELDS if not getattr(self, f, None)]
        if missing:
            raise ValueError("缺少必填文本字段: " + ", ".join(missing))
        return self


def _normalize_options(raw: Any, notes: List[str]) -> Optional[List[str]]:
    """把 `options` 规范成 `List[str] | None`（选择题选项）"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, list):
        cleaned = [str(v).strip() for v in raw if str(v).strip()]
        return cleaned or None
    if isinstance(raw, str):
        import json

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            notes.append(f"options 是字符串且不是合法 JSON（{raw[:40]!r}），已置空")
            return None
        if isinstance(parsed, list):
            return _normalize_options(parsed, notes)
        notes.append(f"options 解析后是 {type(parsed).__name__}，不是数组，已置空")
        return None
    notes.append(f"options 类型为 {type(raw).__name__}，已置空")
    return None


class CardPoint(_CoercionAware):
    """知识点卡片（理解管道 / 联合分析 / 拓展生成的输出条目）"""

    ENUM_FIELDS = {"card_type": (CARD_TYPES, "concept")}
    REQUIRED_TEXT_FIELDS = ("title", "content")

    card_type: str = "concept"
    title: str = ""
    content: str = ""
    source_text: str = ""
    is_key_point: bool = False
    is_difficulty: bool = False


class QuizQuestion(_CoercionAware):
    """练习题（出题管道的输出条目）"""

    ENUM_FIELDS = {
        "question_type": (QUESTION_TYPES, "choice"),
        "difficulty": (DIFFICULTIES, "medium"),
    }
    REQUIRED_TEXT_FIELDS = ("question", "answer")

    card_index: int = 1
    question_type: str = "choice"
    difficulty: str = "medium"
    question: str = ""
    answer: str = ""
    options: Optional[List[str]] = None
    explanation: str = ""


class ExtensionPoint(_CoercionAware):
    """进阶拓展知识点（`generate_extension_knowledge` 的输出条目）"""

    ENUM_FIELDS = {"card_type": (CARD_TYPES, "concept")}
    REQUIRED_TEXT_FIELDS = ("title", "content")

    card_type: str = "concept"
    title: str = ""
    content: str = ""
    source_text: str = ""


class CardRelationPoint(_CoercionAware):
    """卡片关系（`infer_card_relations` 的输出条目）

    ⚠️ `relation_type` **不**走"修正"路径：把它改成 `contrast` 会凭空造出一条
    错误的有向/无向边，而图谱里的错误边会被用户当成事实。
    因此这里把三个取值都放进白名单、但**不设默认值**的思路行不通 ——
    pydantic 的必填字段在缺失时会直接拒绝，这正是想要的：
    认不出来的关系**宁可丢弃**（与 `GradeResult.verdict` 同类）。
    """

    ENUM_FIELDS: ClassVar[Dict[str, Tuple[Sequence[str], str]]] = {}
    REQUIRED_TEXT_FIELDS: ClassVar[Tuple[str, ...]] = ("card_id_a", "card_id_b")

    card_id_a: str = ""
    card_id_b: str = ""
    relation_type: str = ""
    reason: str = ""

    @model_validator(mode="after")
    def _check_relation_type(self) -> "CardRelationPoint":
        if self.relation_type not in RELATION_TYPES:
            raise ValueError(
                f"relation_type={self.relation_type!r} 不是 "
                f"{'/'.join(RELATION_TYPES)} 之一"
            )
        return self


class GradeResult(BaseModel):
    """简答判分结果（`grade_short_answer` 的输出）

    ⚠️ 这个模型**不**沿用 `_CoercionAware`：判分的 `verdict` 认不出来时
    **必须拒绝整条结果**，不能"按 correct 处理" —— 猜一个判分结论会直接
    改变复习间隔，而间隔一旦写进调度就**无法事后纠正**
    （见 `grade_short_answer` 的说明）。这是本模块唯一一处"宁可丢弃"。
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    verdict: str
    missing_points: List[str] = Field(default_factory=list)
    misconceptions: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_lists(cls, data: Any) -> Any:
        """`missing_points` / `misconceptions` 形状不对时**置空**，不牵连整条判分

        这两个字段是展示用的补充信息；因为它们写成字符串就丢掉一条可用的判分，
        代价是用户回去做自评（多一步操作），而收益为零。
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for field in ("missing_points", "misconceptions"):
            value = data.get(field)
            if value is None:
                data[field] = []
            elif isinstance(value, str):
                data[field] = [value.strip()] if value.strip() else []
            elif isinstance(value, list):
                data[field] = [str(v).strip() for v in value if str(v).strip()]
            else:
                data[field] = []
        return data

    @model_validator(mode="after")
    def _check(self) -> "GradeResult":
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict={self.verdict!r} 不是约定取值之一")
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))
        return self


class Issue(BaseModel):
    """一条校验发现"""

    model_config = ConfigDict(frozen=True)

    index: int
    field: str
    reason: str
    #: `rejected`（条目不可用）或 `coerced`（条目可用但被修正过）
    action: str

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return f"[{self.action}] #{self.index} {self.field}: {self.reason}"


class ValidationOutcome(BaseModel):
    """校验结果：可用条目 + 全部发现"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    valid: List[Any] = Field(default_factory=list)
    issues: List[Issue] = Field(default_factory=list)
    total: int = 0

    @property
    def rejected_count(self) -> int:
        return sum(1 for i in self.issues if i.action == "rejected")

    @property
    def coerced_count(self) -> int:
        return sum(1 for i in self.issues if i.action == "coerced")

    def summary(self) -> str:
        if not self.issues:
            return f"{len(self.valid)}/{self.total} 条通过校验，无异常"
        return (
            f"{len(self.valid)}/{self.total} 条可用"
            f"（拒绝 {self.rejected_count}，修正 {self.coerced_count}）: "
            + "; ".join(str(i) for i in self.issues[:5])
            + (" …" if len(self.issues) > 5 else "")
        )


def validate_items(
    raw_items: Any,
    model: Type[BaseModel],
    *,
    source: str = "llm",
) -> ValidationOutcome:
    """逐条校验 LLM 输出的条目列表

    Args:
        raw_items: LLM 解析出的原始条目（理论上应是 list[dict]，实际什么都有）
        model: 目标模型（`CardPoint` / `QuizQuestion` / `ExtensionPoint`）
        source: 日志里标识来源场景

    Returns:
        `ValidationOutcome`：`valid` 是**模型实例**列表（调用方用 `model_dump()`），
        `issues` 记录每条的处置。**永不抛异常** —— 校验层不该把调用搞挂。

    ## 为什么不是"整批校验、失败即整批丢弃"

    长批次里个别条目坏掉是常态。整批丢弃意味着 29 条好的陪 1 条坏的殉葬，
    而重试一次的代价是**再付一次全额费用**（这个项目的成本治理刚做完，
    不该在这里破功）。
    """
    outcome = ValidationOutcome()
    if not isinstance(raw_items, list):
        outcome.issues.append(Issue(
            index=-1, field="<batch>", action="rejected",
            reason=f"期望 list，实际是 {type(raw_items).__name__}",
        ))
        return outcome

    outcome.total = len(raw_items)
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            outcome.issues.append(Issue(
                index=index, field="<item>", action="rejected",
                reason=f"期望对象，实际是 {type(raw).__name__}",
            ))
            continue
        try:
            item = model.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            field = ".".join(str(p) for p in first.get("loc", ())) or "<item>"
            outcome.issues.append(Issue(
                index=index, field=field, action="rejected",
                reason=first.get("msg", "校验失败"),
            ))
            continue

        for note in getattr(item, "coercion_notes", []) or []:
            field, _, reason = note.partition("=")
            outcome.issues.append(Issue(
                index=index, field=field.strip(), action="coerced", reason=reason.strip(),
            ))
        outcome.valid.append(item)

    if outcome.issues:
        # 逐条打日志会淹没控制台：汇总一行，细节交给调用方决定是否展开
        logger.warning("结构化输出校验（%s）: %s", source, outcome.summary())
    return outcome


__all__ = [
    "CARD_TYPES",
    "DIFFICULTIES",
    "QUESTION_TYPES",
    "RELATION_TYPES",
    "VERDICTS",
    "CardPoint",
    "CardRelationPoint",
    "ExtensionPoint",
    "GradeResult",
    "Issue",
    "QuizQuestion",
    "ValidationOutcome",
    "validate_items",
]
