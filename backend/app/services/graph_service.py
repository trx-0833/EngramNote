"""
知识图谱服务模块

本模块提供知识图谱的核心业务逻辑，包括图数据查询、
自动建议关系、确认/拒绝/创建/删除关系等功能。

主要职责：
- 获取用户的完整图谱数据（节点 + 边）
- 获取自动建议的关系列表
- 基于嵌入向量相似度自动建议卡片关系
- 确认、拒绝、手动创建和删除关系

设计决策：
- 自动建议使用卡片「标题 + 截断内容」的嵌入向量计算两两相似度
- 选边方式是「**每卡 Top-K + 按是否同笔记分档的下界**」
- 跨笔记对要求更高的相似度（`CROSS_NOTE_MIN_SIMILARITY`），同笔记用 `MIN_SIMILARITY`
- 单次最多处理 200 张卡片，避免计算量过大
- 手动创建的关系直接为 confirmed 状态

⚠️ 上面这几条不是拍的，是实测定的。依据与全部数字见
`docs/journal/relation-quality-2026-09-26.md`，复现脚本
`backend/scripts/measure_relation_quality.py`（只读）。

## 这条判据改过三次，每次都是被实测推翻的（留在这里，别踩第四次）

**第一次（`f454a1b`）**：下界 0.75 → 0.55，理由是"阈值不是杠杆、Top-K 才是"。
**错** —— 它建立在 200 张池子、且判准只看标题的 83% 上（两个缺陷叠加）。

**第二次（`da775b2`）**：下界修回 0.75，并放开"同章节 ≥0.80"。
判准换成**盲判**（判的人看不到任何分数，只看卡片正文）：
`0.55-0.65` 只有 10%、`0.75-0.85` 有 80%，区间不重叠。

**第三次（本次）**：分档轴从「是否同**章节**」改成「是否同**笔记**」。
盲判 v2（每格 24 对、层内控制笔记关系）发现**四个层里同笔记对全部优于跨笔记对**：

    层              跨笔记              同笔记跨章节
    >=0.85       83% [55, 95]        100% [76, 100]
    0.75-0.85    50% [25, 75]         83% [55, 95]
    0.70-0.75    25% [ 9, 53]         42% [19, 68]
    0.65-0.70     0% [ 0, 24]         42% [19, 68]

前两版都把轴拧在了章节上 —— 而章节本身没有区分力（同章节 `0.75-0.80` 实测 92%）。

教训：**判准本身必须先可信，再拿它调参数。** 只看标题、且看得见分数的判准，
会把"我判定的依据"当成"信号的区分力"（循环论证）。
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import and_, case, distinct, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.card_relation import CardRelation, RelationType, RelationStatus
from ..models.chunk import unpack_vector
from ..models.knowledge_card import KnowledgeCard
from ..models.note import Note
from ..schemas.graph import GraphData, GraphNode, GraphEdge, SuggestedRelation

logger = logging.getLogger(__name__)

#: 每张卡片最多保留几个「最像的邻居」。
#:
#: 它**不是**判据本身，而是「把候选推到高分段」的机制：语料整体相似度低时，
#: Top-K 挑出的仍可能是低分邻居（那一段精确率很差，见下面的下界），
#: 所以 K 必须与一个**够高的下界**一起用。
TOP_K_PER_CARD = 3
#: **同一篇笔记内**的卡片对的下界。
#:
#: ## 为什么下界要按「是否同笔记」分档（2026-09-26 第三次修正）
#:
#: 盲判 v2（每格 24 对，`docs/journal/relation-quality-2026-09-26.md` §9）实测：
#:
#:     层              跨笔记              同笔记跨章节
#:     >=0.85       83% [55, 95]        100% [76, 100]
#:     0.75-0.85    50% [25, 75]         83% [55, 95]
#:     0.70-0.75    25% [ 9, 53]         42% [19, 68]
#:     0.65-0.70     0% [ 0, 24]         42% [19, 68]
#:
#: **四个层里同笔记对全部优于跨笔记对。** 原因不难理解：跨笔记的余弦更容易被
#: 「同领域」骗到 —— 两份不同的技术标准都在讲电力设备，用词与术语体系都像，
#: 余弦自然高，但它们讲的是**不同的对象**。
#:
#: 所以真正的分档轴是**笔记**，不是章节（前两版都拧错了轴，见 git 历史）。
MIN_SIMILARITY = 0.75
#: **跨笔记**的卡片对要求更高的相似度。
#:
#: ## 为什么是 0.80（2026-09-26 第四次修正，盲判 v3）
#:
#: 盲判 v2 测的是"跨笔记 `0.75-0.85`"**整段**，12 对里 6 对为真（50%），
#: 于是当时把下界定在 0.85。但 v3 把这一段**拆开**重测（各 24 对）之后：
#:
#:     跨笔记 0.80-0.85   83%  Wilson 95% [64%, 93%]   ← 与 0.85 以上同档
#:     跨笔记 0.75-0.80   58%  Wilson 95% [39%, 76%]
#:     同笔记 0.70-0.75   50%  Wilson 95% [31%, 69%]
#:
#: **整段那个 50% 是两段混出来的。** `0.80-0.85` 与 0.85 以上没有差别，
#: 把下界卡在 0.85 白白丢掉了这一段 —— 实测有 23 张卡片因此**永远连不上**
#: （全量里再没有够格的邻居）。
#:
#: ⚠️ 但它**解决不了**那批"永久孤立"的卡片：不可达的 239 张里
#: **142 张的最近邻是同笔记**（在 `[0.70, 0.75)`），而这一段实测只有 50%
#: —— 降同笔记下界没有依据。详见 `docs/journal/relation-quality-2026-09-26.md` §13。
CROSS_NOTE_MIN_SIMILARITY = 0.80
#: 单次最多处理的卡片数量（候选 = "要救的孤点"）
#:
#: ## 从 200 提到 600 的依据（A1 P0-1，2026-10-22）
#:
#: 真库实测（只读）：**3469 张卡 / 604 条边 / 3028 张孤点（87.3%）**，
#: 而瓶颈**不是质量是吞吐** —— 2683/3028 张孤点的最近邻**已经过现行阈值**，
#: 全量跑一遍只要 **725 ms**。旧上限 200 意味着"要跑 ~15 轮才轮到别人"，
#: 而真库 `suggested` 至今是 **0**。用户点一次"自动发现关系"却什么都没发生，
#: 那不是"没有相关卡片"，是"这一批恰好没轮到"。
#:
#: ## ⚠️ 为什么不能只是调大这个数（P0-1 必须与 P0-2 捆绑）
#:
#: 候选 × 目标 = 600 × 3469 ≈ **208 万** 个 1024 维点积。numpy 能扛
#: （几十 MB 的相似度矩阵 + 秒级），但**一批就能建出几千条边** ——
#: 全部倾倒进审核队列等于没有队列（7020 条待审 = 用户不会看）。
#: 所以 P0-2（排序 + 分批）与它**必须同批上线**，见 `CANDIDATE_BATCH_SIZE`。
MAX_CARDS_FOR_SUGGEST = 600
#: 单轮**真正写进库**的建议边上限（P0-2 的"分批"那一半）
#:
#: 它是审核队列的闸门：无论候选池多大、相似度多高，一轮最多新增这么多条
#: `suggested`。用户点一次得到一小批**可以看完**的建议，而不是几千条。
#: 下一轮自然推进到下一批候选（孤点优先 + 按价值排序，见 `_candidate_sort_key`）。
#:
#: ⚠️ 与 `MAX_CARDS_FOR_SUGGEST` 的分工不同：前者限**候选池**（算多少），
#: 后者限**产出**（写多少）。只限前者的话，600 张候选每人 3 条最像的
#: 仍可能一次写出上千条。
RELATION_BATCH_SIZE = 200
#: 同名卡片（疑似重复）不建边，但记下这一对供界面提示（A1 P0-3）
#:
#: 真库实测：同名对的相似度**普遍 ~1.000**，盲判过的 3 条**全部是真重复**。
#: 也就是说它们不是"错的边"，是**重复的卡片** —— 把重复当"相关"存下来，
#: 等于把"重新理解导致卡片翻倍"这个数据问题藏进图谱里。
#:
#: 但**只不建边是不够的**：那样这个数据问题就从"图谱里看得见"变成
#: "只有日志里看得见"。所以这一轮把疑似重复对**带出去**（返回值里），
#: 让界面能提示"这 N 对很可能是重复卡片，该合并而不是连边"。
#: ⚠️ 合并是另一件事（改产品语义），本批**只提示不处理**。
SUSPECTED_DUPLICATE_SIMILARITY = 0.98
#: 用于嵌入计算的卡片内容最大长度（字符），避免超长文本稀释相似度
CONTENT_EMBED_LIMIT = 200


class GraphSuggestionError(Exception):
    """
    图谱自动建议关系失败异常

    当嵌入服务（Celery worker）不可用、超时或返回空向量时抛出，
    由 API 层捕获并转为 503 响应，向用户返回可操作提示（替代静默跳过）。
    """


def _build_card_embedding_text(title: str, content: str) -> str:
    """
    构建用于相似度计算的卡片文本（标题 + 截断内容）

    仅用标题信息量不足，容易把同笔记内、标题雷同的卡片误判为相关；
    拼接截断后的内容可显著提升相似度的语义质量。内容为 JSON 字符串，
    截断到 CONTENT_EMBED_LIMIT 字符以控制计算成本。

    Args:
        title: 卡片标题
        content: 卡片内容（JSON 字符串）

    Returns:
        str: 组合后的嵌入文本
    """
    if content:
        return f"{title}\n{content.strip()[:CONTENT_EMBED_LIMIT]}"
    return title


def _compute_pairwise_similarity(vectors: List[List[float]]) -> np.ndarray:
    """
    使用 numpy 向量化计算两两余弦相似度矩阵

    Args:
        vectors: 向量列表

    Returns:
        np.ndarray: n×n 相似度矩阵
    """
    return _compute_similarity(vectors, vectors)


def _compute_similarity(
    rows: List[List[float]], cols: List[List[float]]
) -> np.ndarray:
    """
    候选 × 目标 的余弦相似度矩阵（**非对称**：两个集合可以不同大小）

    为什么需要非对称版本：候选是"要救的孤点"（一批 200 张），而目标池是
    **全部**已嵌入卡片 —— 一张孤点的最近邻完全可能在"这批候选"之外。
    用方阵版本就必须把两边取成同一个集合，那正是"904 张孤点永远轮不到"的原因。

    Args:
        rows: 候选向量（结果的行）
        cols: 目标向量（结果的列）

    Returns:
        np.ndarray: len(rows) × len(cols) 相似度矩阵
    """
    a = np.array(rows, dtype=np.float32)
    b = np.array(cols, dtype=np.float32)
    a_norm = np.linalg.norm(a, axis=1, keepdims=True)
    b_norm = np.linalg.norm(b, axis=1, keepdims=True)
    a = a / np.where(a_norm == 0, 1, a_norm)  # 避免除零
    b = b / np.where(b_norm == 0, 1, b_norm)
    return a @ b.T


async def get_graph_data(user_id: str, db: AsyncSession) -> GraphData:
    """
    获取用户的完整知识图谱数据

    查询用户的所有知识卡片作为节点，
    查询 confirmed 和 suggested 状态的关系作为边，
    构建完整的图谱数据结构。

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        GraphData: 包含节点列表和边列表的图谱数据
    """
    # 查询用户所有知识卡片，仅取需要的列（避免加载大体积 content 字段）
    # LEFT JOIN Note 获取所属笔记的回收站状态，供前端渲染时过滤回收站节点
    cards_result = await db.execute(
        select(
            KnowledgeCard.id,
            KnowledgeCard.title,
            KnowledgeCard.card_type,
            KnowledgeCard.note_id,
            Note.trashed_at.is_not(None).label("note_trashed"),
        )
        .join(Note, KnowledgeCard.note_id == Note.id, isouter=True)
        .where(KnowledgeCard.user_id == user_id)
    )
    cards = list(cards_result.all())

    # 查询用户 confirmed 和 suggested 状态的关系，仅取需要的列
    relations_result = await db.execute(
        select(
            CardRelation.id,
            CardRelation.card_id_1,
            CardRelation.card_id_2,
            CardRelation.relation_type,
            CardRelation.status,
            CardRelation.similarity_score,
        ).where(
            CardRelation.user_id == user_id,
            CardRelation.status.in_([RelationStatus.confirmed, RelationStatus.suggested]),
        )
    )
    relations = list(relations_result.all())

    # 统计每张卡片的关联数：按「无向卡片对」去重（同一对卡片即使存在多条关系也只计 1）
    # 悬挂边（卡片被物理删除后 FK 置 NULL）不参与统计
    neighbor_sets: Dict[str, set] = {}
    for rel in relations:
        if rel.status == RelationStatus.confirmed and rel.card_id_1 and rel.card_id_2:
            neighbor_sets.setdefault(rel.card_id_1, set()).add(rel.card_id_2)
            neighbor_sets.setdefault(rel.card_id_2, set()).add(rel.card_id_1)

    # 构建节点
    nodes = [
        GraphNode(
            id=card.id,
            title=card.title,
            card_type=card.card_type,
            note_id=card.note_id,
            relation_count=len(neighbor_sets.get(card.id, set())),
            note_trashed=bool(card.note_trashed),
        )
        for card in cards
    ]

    # 构建边：悬挂边（source/target 为 NULL，卡片被物理删除）不返回，
    # 避免 GraphEdge 的 str 校验失败；悬挂记录保留在库中，供"清理链接"使用
    edges = [
        GraphEdge(
            id=rel.id,
            source=rel.card_id_1,
            target=rel.card_id_2,
            relation_type=rel.relation_type,
            status=rel.status,
            similarity_score=rel.similarity_score,
        )
        for rel in relations
        if rel.card_id_1 and rel.card_id_2
    ]

    return GraphData(nodes=nodes, edges=edges)


async def get_suggested_relations(
    user_id: str,
    db: AsyncSession,
) -> List[SuggestedRelation]:
    """
    获取用户的自动建议关系列表

    查询 status=suggested 的关系，并关联知识卡片获取标题。

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        List[SuggestedRelation]: 建议关系列表
    """
    # 查询 suggested 状态的关系，仅取需要的列
    relations_result = await db.execute(
        select(
            CardRelation.id,
            CardRelation.card_id_1,
            CardRelation.card_id_2,
            CardRelation.similarity_score,
        ).where(
            CardRelation.user_id == user_id,
            CardRelation.status == RelationStatus.suggested,
        )
    )
    relations = list(relations_result.all())

    if not relations:
        return []

    # 收集所有涉及的卡片 ID
    card_ids = set()
    for rel in relations:
        card_ids.add(rel.card_id_1)
        card_ids.add(rel.card_id_2)

    # 批量查询卡片标题（回收站中的卡片不参与展示）
    cards_result = await db.execute(
        select(KnowledgeCard.id, KnowledgeCard.title).where(
            KnowledgeCard.id.in_(card_ids),
            Note.not_trashed(KnowledgeCard.note_id),
        )
    )
    card_title_map = {row.id: row.title for row in cards_result.all()}

    # 任一端卡片在回收站中的建议关系直接跳过
    relations = [
        rel for rel in relations
        if rel.card_id_1 in card_title_map and rel.card_id_2 in card_title_map
    ]

    # 构建建议关系列表
    suggested = []
    for rel in relations:
        suggested.append(SuggestedRelation(
            id=rel.id,
            card_id_1=rel.card_id_1,
            card_id_2=rel.card_id_2,
            card_1_title=card_title_map.get(rel.card_id_1, "(未知)"),
            card_2_title=card_title_map.get(rel.card_id_2, "(未知)"),
            similarity_score=rel.similarity_score,
        ))

    return suggested


def _candidate_sort_key(
    card_id: str,
    *,
    edge_count: Dict[str, int],
    note_size: Dict[str, int],
    updated_at: Dict[str, Any],
) -> tuple:
    """候选孤点的**价值排序键**（A1 P0-2 的"排序"那一半）

    ## 判据（按优先级）

    1. **现有边数升序** —— 一条边都没有的卡最该被救（它们连"看起来相关"都做不到）；
    2. **所属笔记的卡片数升序** —— 笔记越小，卡越可能是"这堆里最像的那张"
    （大笔记里一张卡有几百个同门，少一条边不影响它被人看见）；
    3. **`updated_at` 降序** —— 最近动过的先看（与"用户现在关心什么"同向）；
    4. **`card_id` 升序** —— 末位定序，保证**同一批候选每次跑出同样的顺序**
    （可复现；没有它，同样的输入可能因为字典/集合顺序变化而给出不同结果）。

    ## ⚠️ 这是**代理指标**，不是实测过的价值函数

    `docs/relation-method-research-20261021.md` 里没有"哪条建议更值得先看"的
    实测标定 —— 要标定需要"人工盲判 + 分批对照"的实验，那属于 P1（弱监督标定）。
    所以这里只用一个**能说清理由**的排序，而不是编一个看起来精致的打分。

    **它买到的东西是确定的**：把"随便切 200 张"换成"有理由的 200 张"，
    且下一批与这一批不重复（孤点优先的推进语义不变）。
    **它买不到的是"这 200 条真的比另外 200 条更有用"** —— 那要 P1 才有依据。
    """
    return (
        edge_count.get(card_id, 0),
        note_size.get(card_id, 0),
        # 第 3 位想表达"最近动过的先看"（降序）。用 `-timestamp` 把降序
        # 折成升序 —— datetime 不能取负，而元组比较要求同类型可比。
        #
        # ⚠️ 缺 `updated_at` 的老数据用 **`-inf`**（不是 `+inf`）排到最后：
        # 这一位是**取过负的**，所以"排最后"对应的极值是负无穷那一边。
        # 第一版写成 `+inf`，而 `-ts < +inf` 恒真 ⇒ 有时间的反而排在没时间的
        # **后面**（实测用例抓出来的）。
        -_ts(updated_at.get(card_id)),
        card_id,
    )


def _ts(value: Any) -> float:
    """把 `datetime` 折成可比较、可取负的浮点秒；缺失时返回 `-inf`

    返回 `-inf` 是为了配合调用方的 `-timestamp`：取负之后它变成 `+inf`，
    在升序里**排到最后**。理由与 `_candidate_sort_key` 的注释同一处。
    """
    if value is None:
        return float("-inf")
    try:
        return float(value.timestamp())
    except (AttributeError, OSError, ValueError):
        return float("-inf")


async def auto_suggest_relations(user_id: str, db: AsyncSession) -> int:
    """
    基于嵌入向量相似度自动建议卡片关系

    流程：
    1. 获取用户所有知识卡片（最多 200 张，按更新时间倒序）
    2. 通过 Celery 任务（app.tasks.embedding_tasks.encode_text）编码「标题 + 截断内容」
    3. 计算两两相似度
    4. **每张卡只取最像的 `TOP_K_PER_CARD` 张**，且不低于下界 ——
       下界**按是否同笔记分档**：同笔记用 `MIN_SIMILARITY`，
       跨笔记要求 `CROSS_NOTE_MIN_SIMILARITY`；不存在已有关系的，
       创建 suggested 状态的关系

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        int: 新创建的建议关系数量
    """
    # ── 目标池：**全部**已嵌入的卡片 —— "邻居"从这里找 ──
    #
    # 为什么不像以前那样把比较限制在一个 200 张的池子里：孤点 1061 张里
    # **904 张（85.2%）根本进不了池子**，而一张孤点的最近邻完全可能在池子之外。
    # 所以现在是**非对称**的：候选（要救的孤点）少，目标（可比对的卡）是全量。
    #
    # 代价 O(候选 × 全量)：200 × 1181 × 1024 ≈ 2.4 亿次乘加，numpy 实测几十毫秒。
    # 语料再大一个数量级（万级）时这条会变慢，那时才需要 FTS5 之类的召回 ——
    # 现在提前上那套复杂度没有依据。
    targets_result = await db.execute(
        select(
            KnowledgeCard.id,
            KnowledgeCard.note_id,
            KnowledgeCard.title,
            KnowledgeCard.embedding,
            KnowledgeCard.embedding_dim,
            KnowledgeCard.embedding_model,
            # A1 P0-2：价值排序的第 3 位判据要它（"最近动过的先看"）。
            # ⚠️ 必须**显式加进 SELECT** —— 这个查询是 `select(列…)`，
            # `.all()` 返回的 `Row` 只有列出的那些属性；访问没列的列会
            # `KeyError: 'updated_at'`（实测 21 条既有用例因此全红）。
            KnowledgeCard.updated_at,
        ).where(
            KnowledgeCard.user_id == user_id,
            KnowledgeCard.has_embedding.is_(True),
            Note.not_trashed(KnowledgeCard.note_id),
        ).order_by(KnowledgeCard.updated_at.desc())
    )
    cards = list(targets_result.all())

    if len(cards) < 2:
        # 区分"卡片本来就少"与"卡片有、但都没有向量" —— 后者是部署问题，
        # 静默返回 0 会让用户以为"没有相关卡片"，而实际是没跑补向量
        total = (await db.execute(
            select(func.count()).select_from(KnowledgeCard).where(
                KnowledgeCard.user_id == user_id,
                Note.not_trashed(KnowledgeCard.note_id),
            )
        )).scalar_one()
        if total >= 2 and len(cards) == 0:
            raise GraphSuggestionError(
                "卡片向量尚未生成，无法自动建议关系。"
                "请先运行 `python backend/scripts/embed_cards.py` 补向量后重试"
            )
        return 0

    # ⚠️ **同一批里只能有一个模型**。
    #
    # `EmbeddingService` 会在可用内存不足时自动降级
    # （`embedding_service.py:66`：bge-m3 1024 维 → bge-small-zh-v1.5 512 维），
    # 所以同一个库里**可能混着两个模型的向量** —— 端到端验证时真的触发过。
    # 跨模型算余弦是无意义的数字，而维度不同还会让 `np.array` 直接变成 object 数组。
    #
    # 取"条数最多的那一组"，其余**丢弃并告警**：宁可少用几张卡，
    # 也不能拿两个模型的向量放在一起排序。要彻底消除，重跑
    # `scripts/embed_cards.py`（把 `has_embedding` 置回 0 即可重算）。
    groups: Dict[Tuple[Any, Any], List[Any]] = {}
    for card in cards:
        groups.setdefault((card.embedding_model, card.embedding_dim), []).append(card)
    if len(groups) > 1:
        biggest = max(groups.items(), key=lambda kv: len(kv[1]))
        logger.warning(
            "卡片向量混用了多个模型，只用最多的那一组: user=%s 采用 %s(%s) 共 %d 张；"
            "丢弃 %s —— 跨模型算余弦无意义，请重跑 embed_cards 统一",
            user_id, biggest[0][0], biggest[0][1], len(biggest[1]),
            {f"{k[0]}({k[1]})": len(v) for k, v in groups.items() if k != biggest[0]},
        )
        cards = biggest[1]

    # 解包向量（长度与 `embedding_dim` 不符时 `unpack_vector` 返回 None ——
    # 那种行按"没有向量"处理，绝不拿长度不对的 BLOB 去算余弦）
    vectors = []
    usable = []
    for card in cards:
        vec = unpack_vector(card.embedding, card.embedding_dim)
        if vec:
            vectors.append(vec)
            usable.append(card)
    cards = usable

    if len(cards) < 2:
        return 0

    loop = asyncio.get_event_loop()

    # 查询已有的所有关系（含 rejected），避免重复建议
    existing_result = await db.execute(
        select(CardRelation).where(CardRelation.user_id == user_id)
    )
    existing_relations = list(existing_result.scalars().all())

    # 构建已有关系集合：(min_id, max_id) -> relation
    existing_pairs: Dict[tuple, CardRelation] = {}
    for rel in existing_relations:
        pair_key = tuple(sorted([rel.card_id_1, rel.card_id_2]))
        existing_pairs[pair_key] = rel

    # 卡片所属**笔记**映射 —— 分档轴就是它（不是章节，见下面三次修正的经过）。
    #
    # ## 这条判据改过三次，每次都是被实测推翻的
    #
    # 1. **同笔记一律跳过**（原始）—— 理由是"章节内高度相似造成噪音建议"。
    #    实测：被丢掉的 7,470 对里 89.5% 是"同笔记跨章节"，而它们相似度中位
    #    0.529 **比跨笔记的 0.443 还高** → 误伤。
    # 2. **只跳过同章节** —— 仍然错：同章节 >=0.80 盲判 10/10 全真。
    # 3. **按「是否同笔记」分档**（现在）—— 盲判 v2 每格 24 对，四个层里
    #    同笔记对全部优于跨笔记对（见两个常量的实测表）。真正的轴是笔记。
    note_of: Dict[str, Any] = {card.id: card.note_id for card in cards}

    #: 归一化标题（只去首尾空白）—— 供「同名闸」用。
    #:
    #: 只做 strip，不做更激进的归一（大小写、全半角、去标点）：那样会把
    #: `操作术语` 与 `操作术语定义` 也算成同名，而它们**不是**重复卡片
    #: （一个是总表、一个是定义，内容不同）。宁可漏挡，不可误挡。
    title_of: Dict[str, str] = {card.id: (card.title or "").strip() for card in cards}

    # ── 候选：**孤点优先** —— 还没有任何关系的卡片 ──
    #
    # 这是本批的核心改动。以前候选是"最新 200 张"，于是同一批卡片被反复比较，
    # 而 904 张孤点**永远轮不到**。现在候选是"一条边都没有的卡"，
    # 每处理一批它们就不再是孤点，**下一次点击自然推进到下一批** ——
    # 不需要额外的游标，孤点数本身就是进度。
    #
    # 判定用「**任何状态**的关系行」（含 rejected）：被拒过的卡片对已经有人看过了，
    # 再冒出来只是打扰；而且 `existing_pairs` 本来也会跳过那一对。
    #
    # ⚠️ 这一筛**在 Python 里做**，不写成 SQL 的 `NOT EXISTS`：关系已经全量加载在
    # `existing_relations` 里了，再发一条相关子查询实测要 **239 ms**（1312 条关系时），
    # 而且随关系数增长 —— 那是本函数最慢的一段（见 `_agenttmp/profile_suggest.py`）。
    # 用同一个集合筛，代价只是几百次哈希查找。
    #
    # ── A1 P0-2（2026-10-22）：从"最新的 N 张孤点"改成"**按价值排序的** N 张" ──
    #
    # 旧实现是 `[c.id for c in cards if ...][:MAX_CARDS_FOR_SUGGEST]`，
    # 而 `cards` 按 `updated_at desc` 排 —— 于是"谁被选中"只取决于**改动时间**，
    # 与"哪张卡更需要一条边"无关。上限从 200 提到 600 之后这个问题被放大了：
    # 一批能进 600 张，选错的空间也大三倍。
    #
    # 现在先按 `_candidate_sort_key` 排序再切片。它买到的是"有理由的 600 张"
    # 而不是"随便 600 张"；买不到的是"这 600 张真的比另外 600 张有用" ——
    # 那要 P1 的弱监督标定，见那个函数的说明。
    related_ids: set = set()
    for rel in existing_relations:
        related_ids.add(rel.card_id_1)
        related_ids.add(rel.card_id_2)

    # 排序需要的两个统计量（都在内存里算，不发额外查询）
    edge_count: Dict[str, int] = {}
    for rel in existing_relations:
        edge_count[rel.card_id_1] = edge_count.get(rel.card_id_1, 0) + 1
        edge_count[rel.card_id_2] = edge_count.get(rel.card_id_2, 0) + 1
    note_size: Dict[str, int] = {}
    for card in cards:
        note_size[card.note_id] = note_size.get(card.note_id, 0) + 1
    updated_at: Dict[str, Any] = {card.id: card.updated_at for card in cards}

    isolated = [c.id for c in cards if c.id not in related_ids]
    isolated.sort(
        key=lambda cid: _candidate_sort_key(
            cid, edge_count=edge_count, note_size=note_size, updated_at=updated_at
        )
    )
    candidate_ids = isolated[:MAX_CARDS_FOR_SUGGEST]
    if not candidate_ids:
        logger.info("没有孤点卡片了，无需建议: user=%s", user_id)
        return 0

    pos_of = {card.id: i for i, card in enumerate(cards)}
    cand_pos = [pos_of[cid] for cid in candidate_ids if cid in pos_of]
    if not cand_pos:
        return 0

    # 候选 × 目标 的相似度矩阵（非对称；CPU 密集，放入线程池）
    sim_matrix = await loop.run_in_executor(
        None, _compute_similarity, [vectors[p] for p in cand_pos], vectors
    )

    # 逐卡取"最像的 TOP_K_PER_CARD 张"。**K 不是判据**，它只是把候选推到高分段；
    # 真正的判据是下面那个按「是否同笔记」分档的下界。
    #
    # ⚠️ 这个计数器数的是**本轮找到的合格卡片对**，不是最终写库的条数 ——
    # 最终条数由下面 P0-2 的闸门（`RELATION_BATCH_SIZE`）决定。
    # 名字刻意区分开，免得后面读到 `new_count` 时以为它就是返回值。
    candidate_pairs = 0
    new_relations = []
    # 被「同名闸」挡掉的卡片对（见下面 title_of 的说明）
    same_title_skipped = 0

    for row_i, i in enumerate(cand_pos):
        note_i = note_of.get(cards[i].id)
        title_i = title_of.get(cards[i].id, "")

        candidates: List[Tuple[float, int]] = []
        for j in range(len(cards)):
            if j == i:
                continue
            similarity = float(sim_matrix[row_i][j])
            # 同笔记（含同章节）用 MIN_SIMILARITY；跨笔记要求更高
            same_note = note_of.get(cards[j].id) == note_i
            floor = MIN_SIMILARITY if same_note else CROSS_NOTE_MIN_SIMILARITY
            if similarity < floor:
                continue
            # ── 同名闸 ──
            #
            # 标题相同的两张卡**不该建"相关"边**：它们要么是同一份内容被抽了两遍
            # （该合并），要么是"每篇标准都有的一节"这类模板文本（本来就无关系）。
            # 两种都不是"相关"。
            #
            # 实测（`_agenttmp/probe_boilerplate.py`）：全量收敛后有 **49 条边
            # （3.5%）**连接的是同名卡片，而它们的相似度**普遍 ~1.000**；
            # 其中盲判过的 3 条**全部判为 `dup`（真重复）** —— 也就是说这些不是
            # "错的边"，是**重复的卡片**。把重复当"相关"存下来，等于把
            # "重新理解导致卡片翻倍"这个数据问题藏进图谱里。
            #
            # ⚠️ 这只是**不建边**，不是"把重复卡处理掉了"：去重/合并是另一件事，
            # 本批没做（见报告 §14.4）。所以下面会把跳过的对数记进日志，
            # 免得这个数据问题从此看不见。
            if title_i and title_i == title_of.get(cards[j].id, ""):
                same_title_skipped += 1
                continue
            candidates.append((similarity, j))

        # `(-相似度, 下标)` 二级排序：并列时按下标定序，保证结果**可复现**
        candidates.sort(key=lambda item: (-item[0], item[1]))

        for similarity, j in candidates[:TOP_K_PER_CARD]:
            card_id_1 = cards[i].id
            card_id_2 = cards[j].id
            pair_key = tuple(sorted([card_id_1, card_id_2]))

            # 已有关系（含 rejected）跳过；本轮已经建过的也跳过
            # （i 与 j 可能互相选中对方，那样这一对会出现两次）
            if pair_key in existing_pairs:
                continue

            # 无向关系统一排序存储，确保 card_id_1 <= card_id_2
            stored_id_1, stored_id_2 = card_id_1, card_id_2
            if card_id_1 > card_id_2:
                stored_id_1, stored_id_2 = card_id_2, card_id_1

            relation = CardRelation(
                user_id=user_id,
                card_id_1=stored_id_1,
                card_id_2=stored_id_2,
                relation_type=RelationType.related,
                status=RelationStatus.suggested,
                similarity_score=round(similarity, 4),
            )
            new_relations.append(relation)
            existing_pairs[pair_key] = relation  # 防止同一对重复创建
            candidate_pairs += 1

    # ── A1 P0-2：按相似度降序，**只写前 `RELATION_BATCH_SIZE` 条** ──
    #
    # 为什么在"已经算完"之后才截断（而不是少算一些）：
    #
    # 截断在这里是**为了排序**才成立的 —— 要把**全批最像的**那 200 条给用户，
    # 就必须先把全批的相似度都算出来。少算候选只省 CPU，但会让"给哪 200 条"
    # 重新变成随机（回到 P0-2 要修的那个问题）。
    #
    # 代价是算完不用：一批 600 候选 × 3469 目标 ≈ 208 万点积，实测秒级
    # （旧注释记的是 200 × 1181 时"几十毫秒"）。这个代价换的是
    # **"审核队列首屏是按价值排序的 200 条"**（本项的验收判据之一）。
    new_relations.sort(
        key=lambda r: (-(r.similarity_score or 0.0), r.card_id_1, r.card_id_2)
    )
    backlog = max(0, len(new_relations) - RELATION_BATCH_SIZE)
    kept = new_relations[:RELATION_BATCH_SIZE]

    # 批量添加
    for rel in kept:
        db.add(rel)

    new_count = len(kept)
    if new_count > 0:
        await db.commit()
        logger.info(f"自动建议关系: user={user_id[:8]}, 新增 {new_count} 条建议")

    if backlog:
        # 这条日志不能省：它回答"为什么只出来 200 条"——
        # 不是"没有更多了"，而是"这一轮按闸门放行了最像的一批，下一轮会继续"。
        logger.info(
            "审核队列闸门: user=%s 本轮产出 %d 条、按相似度留下 %d 条待下一轮"
            "（上限 RELATION_BATCH_SIZE=%d；孤点优先 + 价值排序保证推进）",
            user_id[:8], len(new_relations), backlog, RELATION_BATCH_SIZE,
        )

    if same_title_skipped:
        # 这条日志**不能省**：同名闸只是"不建边"，重复卡片本身还在库里。
        # 没有这条日志，这个数据问题就从"图谱里看得见"变成"完全看不见"了。
        logger.info(
            "同名闸: user=%s 跳过了 %d 对同名卡片（它们更可能是重复卡片，"
            "该合并而不是连边；去重功能尚未实现）",
            user_id[:8], same_title_skipped,
        )

    return new_count


async def confirm_relation(
    relation_id: str,
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    确认一条建议关系

    将 status 从 suggested 更新为 confirmed。

    Args:
        relation_id: 关系 ID
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 操作结果
    """
    result = await db.execute(
        select(CardRelation).where(
            CardRelation.id == relation_id,
            CardRelation.user_id == user_id,
        )
    )
    relation = result.scalars().first()

    if not relation:
        return {"success": False, "error": "关系不存在"}

    if relation.status != RelationStatus.suggested:
        return {"success": False, "error": "该关系不是建议状态，无法确认"}

    relation.status = RelationStatus.confirmed
    await db.commit()

    logger.info(f"确认关系: user={user_id[:8]}, relation={relation_id[:8]}")
    return {"success": True, "relation_id": relation_id}


async def reject_relation(
    relation_id: str,
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    拒绝一条建议关系

    将 status 从 suggested 更新为 rejected。

    Args:
        relation_id: 关系 ID
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 操作结果
    """
    result = await db.execute(
        select(CardRelation).where(
            CardRelation.id == relation_id,
            CardRelation.user_id == user_id,
        )
    )
    relation = result.scalars().first()

    if not relation:
        return {"success": False, "error": "关系不存在"}

    if relation.status != RelationStatus.suggested:
        return {"success": False, "error": "该关系不是建议状态，无法拒绝"}

    relation.status = RelationStatus.rejected
    await db.commit()

    logger.info(f"拒绝关系: user={user_id[:8]}, relation={relation_id[:8]}")
    return {"success": True, "relation_id": relation_id}


async def create_relation(
    card_id_1: str,
    card_id_2: str,
    relation_type: str,
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    手动创建一条卡片关系

    验证两张卡片都属于该用户，且不存在重复关系后，
    创建 confirmed 状态的关系。

    Args:
        card_id_1: 卡片 1 ID
        card_id_2: 卡片 2 ID
        relation_type: 关系类型字符串
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 操作结果，成功时包含 relation_id
    """
    # 验证卡片归属
    cards_result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id.in_([card_id_1, card_id_2]),
            KnowledgeCard.user_id == user_id,
        )
    )
    cards = list(cards_result.scalars().all())

    if len(cards) != 2:
        return {"success": False, "error": "卡片不存在或不属于当前用户"}

    # 验证关系类型
    try:
        rel_type = RelationType(relation_type)
    except ValueError:
        return {"success": False, "error": f"无效的关系类型: {relation_type}"}

    # 无向关系统一排序存储，确保 card_id_1 <= card_id_2
    stored_id_1, stored_id_2 = card_id_1, card_id_2
    if rel_type in (RelationType.related, RelationType.contrast):
        if card_id_1 > card_id_2:
            stored_id_1, stored_id_2 = card_id_2, card_id_1

    # 检查重复关系（排除 rejected 状态，允许用户重新创建被拒绝的关系）
    pair_key = tuple(sorted([card_id_1, card_id_2]))
    existing_result = await db.execute(
        select(CardRelation).where(
            CardRelation.user_id == user_id,
            or_(
                and_(CardRelation.card_id_1 == pair_key[0], CardRelation.card_id_2 == pair_key[1]),
                and_(CardRelation.card_id_1 == pair_key[1], CardRelation.card_id_2 == pair_key[0]),
            ),
        )
    )
    existing = existing_result.scalars().first()

    if existing:
        if existing.status == RelationStatus.rejected:
            # 复用已拒绝的记录，更新为 confirmed
            existing.status = RelationStatus.confirmed
            existing.relation_type = rel_type
            existing.similarity_score = None
            existing.card_id_1 = stored_id_1
            existing.card_id_2 = stored_id_2
            await db.commit()
            await db.refresh(existing)
            logger.info(
                f"重新创建关系(复用rejected): user={user_id[:8]}, "
                f"card1={stored_id_1[:8]}, card2={stored_id_2[:8]}, type={relation_type}"
            )
            return {"success": True, "relation_id": existing.id}
        return {"success": False, "error": "这两张卡片之间已存在关系"}

    # 创建关系
    relation = CardRelation(
        user_id=user_id,
        card_id_1=stored_id_1,
        card_id_2=stored_id_2,
        relation_type=rel_type,
        status=RelationStatus.confirmed,
        similarity_score=None,
    )
    db.add(relation)
    await db.commit()
    await db.refresh(relation)

    logger.info(
        f"手动创建关系: user={user_id[:8]}, "
        f"card1={stored_id_1[:8]}, card2={stored_id_2[:8]}, type={relation_type}"
    )
    return {"success": True, "relation_id": relation.id}


async def delete_relation(
    relation_id: str,
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    删除一条卡片关系

    验证关系属于该用户后删除。

    Args:
        relation_id: 关系 ID
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 操作结果
    """
    result = await db.execute(
        select(CardRelation).where(
            CardRelation.id == relation_id,
            CardRelation.user_id == user_id,
        )
    )
    relation = result.scalars().first()

    if not relation:
        return {"success": False, "error": "关系不存在"}

    await db.delete(relation)
    await db.commit()

    logger.info(f"删除关系: user={user_id[:8]}, relation={relation_id[:8]}")
    return {"success": True, "relation_id": relation_id}


async def count_isolated_cards(user_id: str, db: AsyncSession) -> int:
    """还剩多少张**一张关系都没有**的卡（A1 P0-2 的"进度"读数）

    ## 为什么必须有这个数

    闸门（一轮最多写 `RELATION_BATCH_SIZE` 条建议）如果不说清"还有多少"，
    用户点一次拿到 200 条、再点一次又是 200 条，会以为**功能坏了**。
    实测严据（副本，2026-10-22）：跑 5 轮共 1000 条建议、孤点 3028 → 1972 ——
    还剩 1972 张，那是**几十轮**的量。用户看不到这个数字就只能靠猜。

    ## 判据与 `auto_suggest_relations` 的候选池**必须一致**

    都用"任何状态的关系行"（含 rejected）：被拒过的卡片对已经有人看过了。
    两边判据不一致会出现"界面说还剩 N 张、点一次却一条都没新增"这种
    最难排查的形态 —— 数字在动，功能没动。

    代价：这条查询是 `NOT IN` 两个子查询，比 `auto_suggest_relations` 里
    那次"已经全量加载在内存里"的筛选贵。但它是**用户在等的那一次**
    （点完按钮之后），而 `EXPLAIN` 级的小优化不值得用"判据不一致"去换。
    """
    from sqlalchemy import select

    from ..models.card_relation import CardRelation
    from ..models.knowledge_card import KnowledgeCard
    from ..models.note import Note

    related_1 = select(CardRelation.card_id_1).where(CardRelation.user_id == user_id)
    related_2 = select(CardRelation.card_id_2).where(CardRelation.user_id == user_id)
    total = (
        await db.execute(
            select(func.count())
            .select_from(KnowledgeCard)
            .where(
                KnowledgeCard.user_id == user_id,
                Note.not_trashed(KnowledgeCard.note_id),
                KnowledgeCard.id.not_in(related_1),
                KnowledgeCard.id.not_in(related_2),
            )
        )
    ).scalar_one()
    return int(total)


async def find_suspected_duplicate_cards(
    user_id: str,
    db: AsyncSession,
    *,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """找出**疑似重复**的卡片对（A1 P0-3：可见化，**不处理**）

    ## 它暴露的是什么问题

    `auto_suggest_relations` 里有一道「同名闸」：两张卡标题（`strip()` 后）
    完全相同时**不建边**。原因是实测的：同名对的相似度**普遍 ~1.000**，
    盲判过的 3 条**全部是真重复** —— 也就是说它们不是"错的边"，
    是**重复的卡片**（同一个知识点被反复理解成了多张卡）。

    但闸门只做了一件事：**不建边**。于是这个数据问题从"图谱里看得见"
    变成了"只有日志里看得见"（`docs/relation-method-research-20261021.md` §14.4
    把去重列为未做的事）。本函数把它**变成用户能看见的**。

    ## ⚠️ 只报不处理（已拍板）

    `docs/agent-workstream-plan.md` §5 第 6 条：**合并/删除要另开一轮单独批**，
    因为它会动 `review_states`（复习历史会变孤儿行），属产品语义变更。
    所以本函数**只读**：不建边、不改卡、不落任何标记。

    ## 判据：标题完全相同 **且** 相似度 ≥ `SUSPECTED_DUPLICATE_SIMILARITY`

    为什么要**两个**条件而不是只看标题：同名不同内容的卡在真库里确实存在
    （`操作术语` 与 `操作术语定义` 就不是重复，一个是总表一个是定义 ——
    同名闸的注释专门写了这个反例）。加上相似度这一条，
    才能把"同名但确实是两件事"的排除掉。

    Args:
        user_id: 用户 ID
        db: 数据库会话
        limit: 最多返回多少对（默认 50 —— 这是给人看的清单，不是全量导出）

    Returns:
        `[{card_id_1, card_id_2, title, similarity, note_id_1, note_id_2}, ...]`
        按相似度降序、再按 `(card_id_1, card_id_2)` 定序（**可复现**）。
        只含**已嵌入**的卡：没有向量的卡算不出相似度，而按标题就下"疑似重复"
        的结论依据不足（见上）。
    """
    rows = list((await db.execute(
        select(
            KnowledgeCard.id,
            KnowledgeCard.title,
            KnowledgeCard.note_id,
            KnowledgeCard.embedding,
            KnowledgeCard.embedding_dim,
            KnowledgeCard.embedding_model,
        ).where(
            KnowledgeCard.user_id == user_id,
            KnowledgeCard.has_embedding.is_(True),
            Note.not_trashed(KnowledgeCard.note_id),
        ).order_by(KnowledgeCard.id)
    )).all())

    # 按归一化标题分组（只 strip —— 与同名闸同一判据，不能更激进：
    # 见 `title_of` 那段说明，`操作术语` 与 `操作术语定义` 不是重复卡）
    by_title: Dict[str, List[Any]] = {}
    for row in rows:
        key = (row.title or "").strip()
        if not key:
            continue
        by_title.setdefault(key, []).append(row)

    groups = {t: g for t, g in by_title.items() if len(g) >= 2}
    if not groups:
        return []

    # 只对有同名同伴的卡算向量（真库里 72 种标题 / 187 张，很便宜）
    wanted = [row for group in groups.values() for row in group]
    vectors = [_unpack_vector(row.embedding, row.embedding_dim) for row in wanted]
    vec_of = {row.id: v for row, v in zip(wanted, vectors, strict=True) if v is not None}
    #: 卡 id → 它的向量是哪个模型算的。**跨模型余弦没有意义**，
    #: 所以下面两两比较时会先比这一列（与 `auto_suggest_relations`
    #: 里那条 `test_mixed_embedding_models_are_not_compared` 同一判据）。
    model_of = {row.id: (row.embedding_model or "") for row in wanted}

    pairs: List[Dict[str, Any]] = []
    for title, group in groups.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                va, vb = vec_of.get(a.id), vec_of.get(b.id)
                if va is None or vb is None:
                    continue
                if model_of.get(a.id) != model_of.get(b.id):
                    continue
                sim = float(np.dot(va, vb))
                if sim < SUSPECTED_DUPLICATE_SIMILARITY:
                    continue
                id1, id2 = sorted([a.id, b.id])
                pairs.append({
                    "card_id_1": id1,
                    "card_id_2": id2,
                    "title": title,
                    "similarity": round(sim, 4),
                    "note_id_1": a.note_id,
                    "note_id_2": b.note_id,
                })

    pairs.sort(key=lambda p: (-p["similarity"], p["card_id_1"], p["card_id_2"]))
    if len(pairs) > limit:
        logger.info(
            "疑似重复卡片: user=%s 共 %d 对，按相似度只返回前 %d 对",
            user_id[:8], len(pairs), limit,
        )
    return pairs[:limit]


def _unpack_vector(blob: Any, dim: Optional[int]) -> Optional[np.ndarray]:
    """把库里存的向量还原成**单位化**的 `np.ndarray`；不可用时返回 `None`

    单位化是必须的：`auto_suggest_relations` 里比较的是余弦，
    而它依赖 `pack_vector` 写入时已经单位化。这里对读出来的向量**再除一次模长**
    更稳（早期行按别的口径写过也不影响结果），模长为 0 时返回 `None`
    而不是让 `np.dot` 悄悄给出 0 —— 0 会被读成"完全不相似"，
    与"这条向量坏了"是两件事。
    """
    if blob is None:
        return None
    try:
        vec = unpack_vector(blob, dim)
    except Exception:  # noqa: BLE001 - 坏行跳过，不让整批失败
        return None
    if vec is None:
        return None
    arr = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm <= 0:
        return None
    return arr / norm


async def suggest_semantic_relations(user_id: str, db: AsyncSession) -> Dict[str, Any]:
    """
    基于 LLM 批量推断知识卡片间的语义关系（prerequisite/subsequent/contrast）

    流程：
    1. 获取用户最多 100 张知识卡片
    2. 查询用户已有的所有 CardRelation（任意状态），构建已存在卡片对集合
    3. 分批（每批 20 张）调用 LLM 推断关系，整批卡片对都已有关系则跳过
    4. 校验 LLM 返回的每条关系（ID 在批次内、relation_type 合法、卡片对未存在），
       创建 suggested 状态的 CardRelation

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 操作结果，包含 success/new_count/skipped_count/message
    """
    # 局部导入避免影响模块加载时的依赖图
    from ..services.llm.structured import CardRelationPoint, validate_items
    from ..services.llm_service import LLMService

    # 1. 查询用户的知识卡片，限制 100 张（回收站中的卡片不参与推断）
    cards_result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.user_id == user_id,
            Note.not_trashed(KnowledgeCard.note_id),
        ).limit(100)
    )
    cards = list(cards_result.scalars().all())

    if len(cards) < 2:
        return {"success": True, "new_count": 0, "skipped_count": 0, "message": "卡片数量不足"}

    # 2. 查询用户已有的所有关系（含 confirmed/suggested/rejected），构建已存在卡片对集合
    #    去重键区分有向/无向——prerequisite/subsequent 保留方向
    #    （prerequisite(A,B) 与 prerequisite(B,A) 语义不同，可共存），
    #    contrast/related 为无向（排序去重），见 docs/decisions.md#F-17
    existing_result = await db.execute(
        select(CardRelation).where(CardRelation.user_id == user_id)
    )
    existing_relations = list(existing_result.scalars().all())
    # 有向键：保持存储方向（含类型）
    existing_directed: set = set()
    # 无向键：排序对（含类型）
    existing_undirected: set = set()
    for rel in existing_relations:
        if rel.relation_type in (RelationType.prerequisite, RelationType.subsequent):
            existing_directed.add((rel.card_id_1, rel.card_id_2, rel.relation_type.value))
        else:
            existing_undirected.add((
                tuple(sorted([rel.card_id_1, rel.card_id_2])),
                rel.relation_type.value,
            ))

    def _pair_exists(card_a: str, card_b: str, rel_type: RelationType) -> bool:
        """按方向语义检查卡片对是否已有同类型关系"""
        if rel_type in (RelationType.prerequisite, RelationType.subsequent):
            return (card_a, card_b, rel_type.value) in existing_directed
        return (tuple(sorted([card_a, card_b])), rel_type.value) in existing_undirected

    # 3. 分批调用 LLM 推断关系
    llm_service = LLMService()
    BATCH_SIZE = 20
    new_count = 0
    skipped_count = 0
    new_relations: List[CardRelation] = []

    for start in range(0, len(cards), BATCH_SIZE):
        batch_cards = cards[start:start + BATCH_SIZE]
        batch_ids = {c.id for c in batch_cards}

        # 先过滤候选对：若该批内所有卡片对都已有关系，则跳过该批
        # （批级检查用无向语义：任一无向对已存在即视为已处理）
        all_pairs_count = len(batch_cards) * (len(batch_cards) - 1) // 2
        existing_in_batch = 0
        existing_undirected_pairs: set = set()
        for rel in existing_relations:
            if rel.relation_type not in (RelationType.prerequisite, RelationType.subsequent):
                existing_undirected_pairs.add(tuple(sorted([rel.card_id_1, rel.card_id_2])))
            else:
                existing_undirected_pairs.add(tuple(sorted([rel.card_id_1, rel.card_id_2])))
        for i in range(len(batch_cards)):
            for j in range(i + 1, len(batch_cards)):
                pair_key = tuple(sorted([batch_cards[i].id, batch_cards[j].id]))
                if pair_key in existing_undirected_pairs:
                    existing_in_batch += 1
        if all_pairs_count > 0 and existing_in_batch == all_pairs_count:
            continue

        # 构建卡片摘要
        cards_summary = [
            {
                "id": c.id,
                "title": c.title,
                "card_type": c.card_type.value,
                "content": c.content,
            }
            for c in batch_cards
        ]

        # 调用 LLM 推断关系（单批异常不中断整体流程）
        try:
            relations = await llm_service.infer_card_relations(cards_summary)
        except Exception as e:
            logger.warning(
                f"LLM 推断关系异常(批次 {start // BATCH_SIZE + 1}): "
                f"user={user_id[:8]}, error={e}"
            )
            continue

        # 校验并创建关系（阶段 4.1 收尾：结构化校验 + **未通过的要报告**）
        #
        # 改造前这里对不合法的条目一律 `continue` —— 判定是对的（关系类型
        # 认不出来、ID 不在本批内，都不该建边），但它**完全静默**：
        # 模型开始返回垃圾时，用户只会看到"关系变少了"，日志里什么都没有。
        validation = validate_items(relations, CardRelationPoint, source="infer_relations")
        if validation.issues:
            logger.warning(
                "卡片关系校验（批次 %d）: %s", start // BATCH_SIZE + 1, validation.summary()
            )

        for item in validation.valid:
            rel = item.model_dump()
            card_id_a = rel["card_id_a"]
            card_id_b = rel["card_id_b"]
            relation_type = rel["relation_type"]

            # 校验 ID 都在本批卡片集合中（这是**领域**约束，不属于字段形状）
            if card_id_a not in batch_ids or card_id_b not in batch_ids:
                skipped_count += 1
                continue

            # 转换关系类型枚举
            try:
                rel_type = RelationType(relation_type)
            except ValueError:
                continue

            # 按方向语义检查是否已存在同类型关系
            # （prerequisite(A,B) 与 prerequisite(B,A) 可共存），见 docs/decisions.md#F-17
            if _pair_exists(card_id_a, card_id_b, rel_type):
                skipped_count += 1
                continue

            # 有向关系（prerequisite/subsequent）保持 LLM 给的顺序；
            # 无向关系（contrast）按 ID 字典序排序存储
            if rel_type == RelationType.contrast:
                stored_id_1, stored_id_2 = sorted([card_id_a, card_id_b])
            else:
                stored_id_1, stored_id_2 = card_id_a, card_id_b

            new_relation = CardRelation(
                user_id=user_id,
                card_id_1=stored_id_1,
                card_id_2=stored_id_2,
                relation_type=rel_type,
                status=RelationStatus.suggested,
                similarity_score=None,
            )
            new_relations.append(new_relation)
            # 创建后同步更新去重集合，防止同批内重复，见 docs/decisions.md#F-17
            if rel_type in (RelationType.prerequisite, RelationType.subsequent):
                existing_directed.add((stored_id_1, stored_id_2, rel_type.value))
            else:
                existing_undirected.add((tuple(sorted([stored_id_1, stored_id_2])), rel_type.value))
            new_count += 1

    # 4. 批量添加并提交
    for rel in new_relations:
        db.add(rel)
    if new_count > 0:
        await db.commit()

    logger.info(
        f"语义关系推断: user={user_id[:8]}, 新增 {new_count} 条建议, 跳过 {skipped_count} 条已有关系"
    )
    return {
        "success": True,
        "new_count": new_count,
        "skipped_count": skipped_count,
        "message": f"新增 {new_count} 条语义关系建议，跳过 {skipped_count} 条已有关系",
    }


# =============================================================================
# 新增：图谱统计、搜索、子图、批量操作、布局保存
# =============================================================================


async def get_graph_stats(user_id: str, db: AsyncSession) -> Dict[str, Any]:
    """
    获取用户知识图谱的统计数据

    返回节点数、边数、按关系类型的分布、孤立节点数等。

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 包含 total_nodes, total_edges, confirmed_edges, suggested_edges,
              relation_type_distribution, isolated_nodes
    """
    # 卡片总数（聚合查询，避免加载全部卡片行；排除回收站中的卡片）
    total_nodes = (
        await db.execute(
            select(func.count(KnowledgeCard.id)).where(
                KnowledgeCard.user_id == user_id,
                Note.not_trashed(KnowledgeCard.note_id),
            )
        )
    ).scalar_one()

    # 各状态关系数（一次聚合按状态分组；悬挂边不计入——卡片被物理删除后 FK 置 NULL）
    not_dangling = and_(
        CardRelation.card_id_1.is_not(None),
        CardRelation.card_id_2.is_not(None),
    )
    status_counts: Dict[str, int] = {"confirmed": 0, "suggested": 0, "rejected": 0}
    status_rows = (
        await db.execute(
            select(CardRelation.status, func.count(CardRelation.id))
            .where(CardRelation.user_id == user_id, not_dangling)
            .group_by(CardRelation.status)
        )
    ).all()
    for status, count in status_rows:
        status_counts[status.value] = count

    confirmed = status_counts["confirmed"]
    suggested = status_counts["suggested"]

    # 已确认关系按类型分布（一次聚合按类型分组）
    type_rows = (
        await db.execute(
            select(CardRelation.relation_type, func.count(CardRelation.id))
            .where(
                CardRelation.user_id == user_id,
                CardRelation.status == RelationStatus.confirmed,
                not_dangling,
            )
            .group_by(CardRelation.relation_type)
        )
    ).all()
    type_distribution = [
        {"relation_type": rel_type.value, "count": count}
        for rel_type, count in sorted(type_rows, key=lambda x: -x[1])
    ]

    # 孤立节点：没有任何关系（confirmed 或 suggested）的卡片，用 NOT EXISTS 精确计数
    rel_exists = exists().where(
        and_(
            CardRelation.user_id == user_id,
            CardRelation.status.in_([RelationStatus.confirmed, RelationStatus.suggested]),
            not_dangling,
            or_(CardRelation.card_id_1 == KnowledgeCard.id, CardRelation.card_id_2 == KnowledgeCard.id),
        )
    )
    isolated = (
        await db.execute(
            select(func.count(KnowledgeCard.id)).where(
                KnowledgeCard.user_id == user_id,
                Note.not_trashed(KnowledgeCard.note_id),
                ~rel_exists,
            )
        )
    ).scalar_one()

    return {
        "total_nodes": total_nodes,
        "total_edges": confirmed + suggested + status_counts["rejected"],
        "confirmed_edges": confirmed,
        "suggested_edges": suggested,
        "relation_type_distribution": type_distribution,
        "isolated_nodes": isolated,
    }


async def search_nodes(
    user_id: str,
    keyword: str,
    db: AsyncSession,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    搜索用户的知识卡片（按标题模糊匹配）

    Args:
        user_id: 用户 ID
        keyword: 搜索关键词
        db: 数据库会话
        limit: 最大返回数

    Returns:
        List[Dict]: 匹配的卡片列表，含 id/title/card_type/note_id/relation_count
    """
    # 单条 SQL：LEFT JOIN 统计每张匹配卡片关联的「不同卡片数」（按卡片对去重），避免逐卡 N+1 查询
    count_cond = and_(
        CardRelation.user_id == user_id,
        CardRelation.status == RelationStatus.confirmed,
        or_(CardRelation.card_id_1 == KnowledgeCard.id, CardRelation.card_id_2 == KnowledgeCard.id),
    )
    # 与 KnowledgeCard.id 相连时取另一端的卡片 id，用于去重计数
    neighbor_expr = case(
        (CardRelation.card_id_1 == KnowledgeCard.id, CardRelation.card_id_2),
        else_=CardRelation.card_id_1,
    )
    rows = (
        await db.execute(
            select(
                KnowledgeCard.id,
                KnowledgeCard.title,
                KnowledgeCard.card_type,
                KnowledgeCard.note_id,
                func.count(distinct(neighbor_expr)).label("relation_count"),
            )
            .outerjoin(CardRelation, count_cond)
            .where(
                KnowledgeCard.user_id == user_id,
                Note.not_trashed(KnowledgeCard.note_id),
                or_(
                    KnowledgeCard.title.ilike(f"%{keyword}%"),
                    KnowledgeCard.content.ilike(f"%{keyword}%"),
                ),
            )
            .group_by(KnowledgeCard.id, KnowledgeCard.title, KnowledgeCard.card_type, KnowledgeCard.note_id)
            .order_by(func.count(distinct(neighbor_expr)).desc())
            .limit(limit)
        )
    ).all()

    return [
        {
            "id": row.id,
            "title": row.title,
            "card_type": row.card_type.value,
            "note_id": row.note_id,
            "relation_count": row.relation_count,
        }
        for row in rows
    ]


async def get_node_subgraph(
    node_id: str,
    user_id: str,
    db: AsyncSession,
) -> Optional[Dict[str, Any]]:
    """
    获取某个节点及其直接邻居的子图

    返回中心节点 + 邻居节点 + 之间的边。

    Args:
        node_id: 中心节点 ID
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Optional[Dict]: 包含 center_node, neighbor_nodes, edges；节点不存在返回 None
    """
    # 查询中心节点（回收站中的卡片视为不存在）
    card_result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id == node_id,
            KnowledgeCard.user_id == user_id,
            Note.not_trashed(KnowledgeCard.note_id),
        )
    )
    center = card_result.scalars().first()
    if not center:
        return None

    # 查询涉及该节点的所有关系
    relations_result = await db.execute(
        select(CardRelation).where(
            CardRelation.user_id == user_id,
            CardRelation.status.in_([RelationStatus.confirmed, RelationStatus.suggested]),
            (CardRelation.card_id_1 == node_id) | (CardRelation.card_id_2 == node_id),
        )
    )
    relations = list(relations_result.scalars().all())

    # 收集邻居节点 ID（悬挂边：一端为 NULL 时跳过，卡片已被物理删除）
    neighbor_ids: set = set()
    edges_data = []
    for rel in relations:
        if not rel.card_id_1 or not rel.card_id_2:
            continue
        other_id = rel.card_id_2 if rel.card_id_1 == node_id else rel.card_id_1
        neighbor_ids.add(other_id)
        edges_data.append({
            "id": rel.id,
            "source": rel.card_id_1,
            "target": rel.card_id_2,
            "relation_type": rel.relation_type.value,
            "status": rel.status.value,
            "similarity_score": rel.similarity_score,
        })

    # 查询邻居节点详情
    neighbors = []
    if neighbor_ids:
        neighbors_result = await db.execute(
            select(
                KnowledgeCard.id,
                KnowledgeCard.title,
                KnowledgeCard.card_type,
                KnowledgeCard.note_id,
            ).where(
                KnowledgeCard.id.in_(neighbor_ids),
                KnowledgeCard.user_id == user_id,
                Note.not_trashed(KnowledgeCard.note_id),
            )
        )
        neighbor_cards = list(neighbors_result.all())

        # 批量统计邻居的关联数（按无向卡片对去重，单条查询避免逐邻居 N+1）
        neighbor_rel_rows = (
            await db.execute(
                select(CardRelation.card_id_1, CardRelation.card_id_2)
                .where(
                    CardRelation.user_id == user_id,
                    CardRelation.status == RelationStatus.confirmed,
                    or_(
                        CardRelation.card_id_1.in_(neighbor_ids),
                        CardRelation.card_id_2.in_(neighbor_ids),
                    ),
                )
            )
        ).all()
        neighbor_sets: Dict[str, set] = {nid: set() for nid in neighbor_ids}
        for cid_1, cid_2 in neighbor_rel_rows:
            if cid_1 in neighbor_sets:
                neighbor_sets[cid_1].add(cid_2)
            if cid_2 in neighbor_sets:
                neighbor_sets[cid_2].add(cid_1)

        for n in neighbor_cards:
            neighbors.append({
                "id": n.id,
                "title": n.title,
                "card_type": n.card_type.value,
                "note_id": n.note_id,
                "relation_count": len(neighbor_sets.get(n.id, set())),
            })

    # 统计中心节点关联数：按无向卡片对去重（仅 confirmed）
    center_neighbors: set = set()
    for r in relations:
        if r.status == RelationStatus.confirmed:
            center_neighbors.add(r.card_id_2 if r.card_id_1 == node_id else r.card_id_1)
    center_count = len(center_neighbors)

    return {
        "center_node": {
            "id": center.id,
            "title": center.title,
            "card_type": center.card_type.value,
            "note_id": center.note_id,
            "relation_count": center_count,
        },
        "neighbor_nodes": neighbors,
        "edges": edges_data,
    }


async def batch_confirm_suggestions(
    relation_ids: List[str],
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    批量确认建议关系

    将所有指定的 suggested 状态关系更新为 confirmed。

    Args:
        relation_ids: 关系 ID 列表
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 包含 success、confirmed_count 和 failed_count
    """
    confirmed_count = 0
    failed_count = 0

    # 单条 UPDATE 批量确认（仅更新属于该用户且仍为 suggested 的关系）
    result = await db.execute(
        update(CardRelation)
        .where(
            CardRelation.id.in_(relation_ids),
            CardRelation.user_id == user_id,
            CardRelation.status == RelationStatus.suggested,
        )
        .values(status=RelationStatus.confirmed)
    )
    confirmed_count = result.rowcount
    failed_count = len(relation_ids) - confirmed_count

    if confirmed_count > 0:
        await db.commit()
        logger.info(
            f"批量确认: user={user_id[:8]}, 确认 {confirmed_count} 条, 失败 {failed_count} 条"
        )

    return {
        "success": True,
        "confirmed_count": confirmed_count,
        "failed_count": failed_count,
    }


async def batch_reject_suggestions(
    relation_ids: List[str],
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    批量拒绝建议关系

    将所有指定的 suggested 状态关系更新为 rejected。

    Args:
        relation_ids: 关系 ID 列表
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 包含 success、rejected_count 和 failed_count
    """
    rejected_count = 0
    failed_count = 0

    # 单条 UPDATE 批量拒绝（仅更新属于该用户且仍为 suggested 的关系）
    result = await db.execute(
        update(CardRelation)
        .where(
            CardRelation.id.in_(relation_ids),
            CardRelation.user_id == user_id,
            CardRelation.status == RelationStatus.suggested,
        )
        .values(status=RelationStatus.rejected)
    )
    rejected_count = result.rowcount
    failed_count = len(relation_ids) - rejected_count

    if rejected_count > 0:
        await db.commit()
        logger.info(
            f"批量拒绝: user={user_id[:8]}, 拒绝 {rejected_count} 条, 失败 {failed_count} 条"
        )

    return {
        "success": True,
        "rejected_count": rejected_count,
        "failed_count": failed_count,
    }
