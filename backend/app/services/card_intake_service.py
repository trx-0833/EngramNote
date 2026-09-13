"""
卡片入库门（overhaul-plan 阶段 4.8 / 4.9）

## 这一层解决两个问题

### 4.8 重跑理解的幂等：不产生重复卡片（症状 A-10）

改造前的 `save_knowledge_cards` **只看输入、不看库里**：

    for point in knowledge_points:
        db.add(KnowledgeCard(...))       # 无条件插入
    await db.commit()

于是"重新理解这篇笔记"这个按钮按一次，这篇笔记的卡片就**翻一倍**。
而卡片是复习与图谱的锚点 —— 重复卡片会让复习队列里出现两道一模一样的题，
让知识图谱长出一堆孪生节点，而用户很难理解为什么。

**本项目的做法：内容寻址复用，只补差集，永不删除。**

- 新卡先算 `content_hash`；
- 该笔记下已有同哈希的卡 → **复用**（不插入），返回已存在的行；
- 没有 → 插入；
- 库里存在但这次没抽出来的卡 → **保留**（"只补差集"）。

⚠️ 最后一条是刻意的：**不做删除**。旧卡片上挂着 `review_states`
（唯一约束是 `(user_id, item_type, item_id)`），删掉卡片就等于
把用户的复习进度变成孤儿行 —— 而"重跑一次理解，复习进度全没了"
正是 overhaul-plan 症状 D-3 的形态。宁可留下几张用户已经不再需要的卡，
也不能让学习记录凭空消失。

### 4.9 质量门：脏卡片不入库

`content=""`、`title` 缺失、没有 `source_text` 的卡片一旦入库，
就会进入复习队列与知识图谱 —— 用户会看到一张写着"未命名知识点"、
正文空白的卡片，而它**没有任何办法被自动清理掉**（我们刚刚才承诺不删卡片）。
所以必须在入口挡住。判据见 `reject_reason`。

## 真库实测（2026-09-11）：这两条目前都是**预防性**的

| 事实 | 实测值 |
|---|---|
| 卡片总数 | 1183（分布在 17 篇笔记上） |
| **跨多天创建过卡片的笔记** | **0 篇** —— 重跑理解从未发生过 |
| 完全重复的 `(title, content)` | **0 行** |
| 会被质量门拦下的存量卡片 | 内容 < 10 字 4 张（0.3%）、无 `source_text` 2 张 |

也就是说：**这次没有清理掉任何存量数据**。价值在于
"谁都可以按那个按钮" —— 按下去之后卡片不会翻倍、脏卡片不会入库。
把这条说成"清理了 N 条重复数据"是不诚实的，所以写在这里。
"""

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.knowledge_card import CardType, KnowledgeCard
from .llm.structured import CardPoint, validate_items

logger = logging.getLogger(__name__)

#: 指纹长度（sha256 前 32 个十六进制字符 = 128 位）
#:
#: 不用完整 64 位十六进制：碰撞概率在这个量级上早已可以忽略，
#: 而短一半的列宽更省索引空间。真要碰撞，代价也只是"两张几乎相同的卡被当成一张"，
#: 而这本来就是这里想要的效果。
_HASH_LEN = 32

_WS_RE = re.compile(r"\s+")


def normalize_card_text(text: Optional[str]) -> str:
    """卡片的规范化文本（指纹的输入）

    三步，都是为了让"同一次抽取的同一张卡"稳定得到同一个指纹：

    1. **NFKC 归一**：全角/半角、兼容字符统一（LLM 两次输出可能混用）；
    2. **空白压缩**：换行/多空格折叠成一个空格（排版差异不该算不同卡）；
    3. **首尾去空白**。

    ⚠️ 刻意**不做**模糊匹配（不改标点、不做同义替换）：
    那属于 `detect_card_duplicates` 的职责（向量/文本相似度）。
    这里的语义是"**这是同一张卡**"，不是"这两张卡很像" ——
    把两者混为一谈会让重跑时**误删**掉内容相近但不同的卡。
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text))
    return _WS_RE.sub(" ", normalized).strip()


def card_content_hash(title: Optional[str], content: Optional[str]) -> str:
    """卡片的稳定指纹（阶段 4.8 的去重键）

    输入是 `标题 + 分隔符 + 正文`：只用正文会让"同一段正文配不同标题"的两张卡
    互相顶掉；只用标题则会撞上大量同名不同义的卡。
    """
    payload = f"{normalize_card_text(title)}\n{normalize_card_text(content)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_HASH_LEN]


# ---------------------------------------------------------------------------
# 质量门（4.9）
# ---------------------------------------------------------------------------

@dataclass
class GateConfig:
    """质量门阈值（来自 `config`，这里显式列出便于测试与阅读）"""
    #: 正文最少字符数（规范化后）
    min_content_chars: int = 10
    #: 正文最少字符数（标题）
    min_title_chars: int = 2
    #: 是否要求有原文出处
    require_source_text: bool = True
    #: 单次抽取最多入库多少张**新**卡（0 = 不限）。防一次跑飞的抽取灌进来几千张。
    max_new_cards_per_run: int = 500

    @classmethod
    def from_settings(cls, settings=None) -> "GateConfig":
        if settings is None:
            from ..config import get_settings
            settings = get_settings()
        return cls(
            min_content_chars=int(getattr(settings, "card_min_content_chars", 10)),
            min_title_chars=int(getattr(settings, "card_min_title_chars", 2)),
            require_source_text=bool(getattr(settings, "card_require_source_text", True)),
            max_new_cards_per_run=int(getattr(settings, "card_max_new_per_run", 500)),
        )


def reject_reason(point: Dict[str, Any], config: GateConfig) -> Optional[str]:
    """这张卡该不该被拒；`None` 表示通过

    返回值是**给日志与统计用的一句话原因**，不是一个布尔 —— 因为
    "被拒了多少张"没有用，"因为什么被拒"才有用（才能判断是提示词退化
    还是模型偶发抽风）。

    刻意**不**在这里兜底造数据：`title` 缺失就拒，而不是填一个
    "未命名知识点"。占位标题会让一张本该被丢弃的脏卡片看起来像正常卡片，
    而它一旦入库就再也清理不掉了（本模块承诺不删卡片，见模块说明）。
    """
    title = normalize_card_text(point.get("title"))
    content = normalize_card_text(point.get("content"))
    source = normalize_card_text(point.get("source_text"))

    if len(content) < config.min_content_chars:
        return f"正文过短（{len(content)} < {config.min_content_chars} 字）"
    if len(title) < config.min_title_chars:
        return f"标题缺失或过短（{len(title)} < {config.min_title_chars} 字）"
    if config.require_source_text and not source:
        return "缺少原文出处（source_text）"
    return None


# ---------------------------------------------------------------------------
# 入库 / 去重
# ---------------------------------------------------------------------------

@dataclass
class SaveOutcome:
    """一次入库的结果

    四个计数缺一不可：只报 `created` 会让人以为"这次抽到 50 张 = 新增 50 张"，
    而重跑一次理解时正确答案通常是"复用 50、新增 0"。
    被拒的卡片必须出现在这里（而不是只进日志），否则"脏卡片不入库"
    会变成"静默少了几张卡"。

    `coerced`（阶段 4.1 收尾：结构化校验）是第五类，与 `rejected` **必须分开**：
    被拒的是"这条不能用"，被修正的是"这条能用，但模型写的枚举值不认识，
    已按默认值处理"。混在一起就看不出"模型开始在 card_type 上胡说"这个信号 ——
    而那正是提示词需要调整的前兆。
    """
    created: List[KnowledgeCard] = field(default_factory=list)
    reused: List[KnowledgeCard] = field(default_factory=list)
    rejected: List[Tuple[str, str]] = field(default_factory=list)   # (标题, 原因)
    truncated: int = 0
    coerced: List[Tuple[str, str]] = field(default_factory=list)    # (标题, 说明)

    @property
    def rejected_reasons(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for _title, reason in self.rejected:
            counts[reason] = counts.get(reason, 0) + 1
        return counts


async def _existing_hashes(db: AsyncSession, user_id: str, note_id: str) -> Dict[str, KnowledgeCard]:
    """该笔记下已有的 卡片指纹 → 卡片 映射

    只查 `id/title/content/content_hash` 四列：这里要的是判定依据，
    把整行（含 5000 字的 `source_text` 与向量）读出来纯属浪费。
    `content_hash IS NULL` 的历史行不参与判定（见模型里的说明）。
    """
    rows = (await db.execute(
        select(
            KnowledgeCard.id,
            KnowledgeCard.title,
            KnowledgeCard.content,
            KnowledgeCard.content_hash,
        ).where(
            KnowledgeCard.user_id == user_id,
            KnowledgeCard.note_id == note_id,
            KnowledgeCard.content_hash.is_not(None),
        )
    )).all()
    existing: Dict[str, KnowledgeCard] = {}
    for card_id, title, content, content_hash in rows:
        # 用 `content_hash` 作键，值只放 id（够用来"复用"：调用方需要的是
        # 这张卡确实存在且是哪一张）。需要整行时再按 id 取。
        existing.setdefault(content_hash, _Stub(id=card_id, title=title, content=content))
    return existing


@dataclass
class _Stub:
    """复用时的轻量占位（避免为每张已存在的卡读整行）"""
    id: str
    title: str
    content: str


async def save_cards_idempotent(
    db: AsyncSession,
    user_id: str,
    note_id: str,
    chapter: Dict[str, Any],
    summary: str,
    knowledge_points: List[Dict[str, Any]],
    *,
    config: Optional[GateConfig] = None,
    already_created: int = 0,
    prompt_version: Optional[str] = None,
) -> SaveOutcome:
    """质量门 + 内容寻址去重之后的入库（阶段 4.8 / 4.9）

    Args:
        knowledge_points: 本次抽取出的知识点（LLM 原始输出）
        config: 质量门配置；缺省从 settings 读
        already_created: 本次理解流程**已经**新建了多少张卡（用于跨章节累计
            单次上限；见 `GateConfig.max_new_cards_per_run`）
        prompt_version: 产出这批卡的提示词版本（阶段 4.6）；
            缺省 None 表示"未知"，**不要**在这里兜底成某一版

    Returns:
        `SaveOutcome`
    """
    config = config or GateConfig.from_settings()
    outcome = SaveOutcome()
    existing = await _existing_hashes(db, user_id, note_id)
    # 同一批里也要去重：LLM 偶尔会在一个批次里把同一个知识点写两遍
    seen_in_batch: Dict[str, Dict[str, Any]] = {}

    # 阶段 4.1 收尾：**先过结构化校验**（Pydantic），再进质量门。
    #
    # 顺序有意义：校验管的是"字段形状/枚举取值"，质量门管的是"内容够不够格"
    # （正文过短、无 source_text）。一条缺 title 的条目本来就过不了门，
    # 但让校验先拒它，日志里给出的原因是"缺 title"而不是"标题过短"——
    # 后者会把排查方向指错。
    validation = validate_items(knowledge_points, CardPoint, source="card_intake")
    for issue in validation.issues:
        if issue.action == "rejected":
            outcome.rejected.append(("<无标题>" if issue.index < 0 else "<校验未通过>",
                                     f"schema:{issue.field} {issue.reason}"))
        else:
            outcome.coerced.append(("<校验修正>", f"{issue.field} {issue.reason}"))

    for item in validation.valid:
        point = item.model_dump()
        reason = reject_reason(point, config)
        if reason:
            outcome.rejected.append((str(point.get("title") or "")[:60], reason))
            continue

        digest = card_content_hash(point.get("title"), point.get("content"))
        if digest in seen_in_batch:
            outcome.reused.append(_Stub(id="", title=point.get("title", ""), content=""))
            continue
        if digest in existing:
            outcome.reused.append(existing[digest])
            continue

        if config.max_new_cards_per_run > 0:
            if already_created + len(outcome.created) >= config.max_new_cards_per_run:
                outcome.truncated += 1
                continue

        seen_in_batch[digest] = point

        card_type_str = point.get("card_type", "concept")
        try:
            card_type = CardType(card_type_str)
        except ValueError:
            card_type = CardType.concept

        source_text = point.get("source_text", "") or ""
        if len(source_text) > 5000:
            source_text = source_text[:5000]

        card = KnowledgeCard(
            user_id=user_id,
            note_id=note_id,
            card_type=card_type,
            title=point.get("title", ""),
            content=point.get("content", ""),
            summary=summary,
            chapter_title=chapter.get("chapter_title", ""),
            source_text=source_text,
            content_hash=digest,
            prompt_version=prompt_version,
        )
        db.add(card)
        outcome.created.append(card)

    await db.commit()
    for card in outcome.created:
        await db.refresh(card)

    if outcome.rejected:
        logger.warning(
            "卡片质量门拦下 %d 张（笔记 %s，章节 %r）: %s",
            len(outcome.rejected), note_id[:8], chapter.get("chapter_title"),
            outcome.rejected_reasons,
        )
    if outcome.coerced:
        # 被修正的条目**仍然入库**，但必须可见：枚举值开始被模型写错
        # 是提示词需要调整的前兆（配合 4.6 的 prompt_version 可按版本量化）
        logger.warning(
            "卡片字段被校验修正 %d 处（笔记 %s，章节 %r）: %s",
            len(outcome.coerced), note_id[:8], chapter.get("chapter_title"),
            outcome.coerced[:3],
        )
    if outcome.truncated:
        logger.warning(
            "单次抽取数量上限 %d 已达，另有 %d 张新卡未入库（笔记 %s）——"
            "这不是数据丢失，重跑理解会按内容寻址补上；"
            "但若频繁出现，应检查分段是否过粗或提示词是否失控",
            config.max_new_cards_per_run, outcome.truncated, note_id[:8],
        )
    return outcome


__all__ = [
    "GateConfig",
    "SaveOutcome",
    "card_content_hash",
    "normalize_card_text",
    "reject_reason",
    "save_cards_idempotent",
]
