"""
RAG 问答服务模块

本模块提供检索增强生成（RAG）问答功能，基于用户所有笔记的内容回答问题。

主要职责：
- 通过 Celery worker 调用嵌入模型将问题向量化（隔离模型加载，避免主进程段错误）
- 从 Chroma 向量数据库中检索相关文本块（通过 Celery 任务）
- 使用 BM25 算法从知识卡片中检索相关内容（纯 Python 实现）
- 使用 RRF（Reciprocal Rank Fusion）融合两路检索结果
- 拼接上下文，调用 LLM 生成回答
- 返回回答 + 引用来源

设计决策：
- 嵌入模型加载隔离到 Celery worker 进程，避免在 FastAPI 主进程中
  加载 BGE-M3 导致段错误（0xC0000005）
- 混合检索策略：向量检索（语义）+ BM25（关键词）
- RRF 融合两路检索结果，互补提升召回率
- 嵌入任务超时或失败时，自动降级为仅 BM25 检索
- 返回结果包含引用来源（笔记标题、章节、相关段落）

## 为什么只有两路（n-gram 通道已删除，阶段 2.6）

原实现有第三路"字符 n-gram 匹配"（`_search_relevant_cards`）。它与 BM25 的
语料**完全相同**（都是 `_get_user_cards` 拉到的知识卡片），只是打分方式更粗糙：

- BM25 有 IDF 加权与长度归一化；n-gram 只是"命中子串就累加子串长度"，
  即一个**未归一化的词频计数**，没有任何区分度校准
- 而 RRF 给三路**同等权重**（都乘 1/(k+rank)），于是一路明显更弱的检索器
  与 BM25、向量通道拥有相同的投票权 —— 它主要在做的是**把噪声顶进 top-5**

两路语料相同、其中一路纯噪声，属于"看起来更强、实际更弱"的典型。
删除后 RRF 只融合向量与 BM25，两者语料不同（原文块 vs 卡片），互补性才是真的。
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from ..config import get_settings
from ..models.note import Note
from ..services.llm_service import LLMService

logger = logging.getLogger(__name__)
settings = get_settings()


class BM25Index:
    """可复用的 BM25 索引（分词与 df/IDF 只算一次，然后支持多次查询）

    ## 为什么需要它（阶段 2.9 / A-5）

    原实现把"建索引"和"查询"揉在一个函数里：`_search_bm25(question, cards)`
    每次调用都对**全量语料重新分词**并重算 df/IDF。两个后果：

    1. **线上开销随语料线性增长且每次提问都付**（A-5）。实测本库
       1183 张卡片、英文+中文 2-gram 分词，每次提问都要重算一遍 ——
       而语料在两分钟内根本没变。
    2. **离线评测事实上不可行**：评测要在 ~1000 条问题上跑指标，
       逐条重建索引意味着把同一份语料重复分词 1000 次。

    拆开之后线上保留一份索引（按语料 hash 失效），评测也能对同一份索引
    跑完全部问题。**两边跑的是同一段打分代码** —— 这是评测结论可信的前提。

    ## 索引的失效策略

    索引持有一个 `signature`（语料指纹）。调用方在语料变化后重新构建即可；
    这里不做 TTL 缓存，避免重蹈 `_kb_cache` 的覆辙（见 `_get_user_cards` 的说明）。

    Args:
        cards: 卡片语料 dict 列表（需含 title/content/note_id/card_id/chapter_title）
        tokenize: 分词函数（注入以便与 `RAGService._tokenize` 保持同一实现）
        k1, b: BM25 参数
    """

    #: BM25 参数。k1 控制词频饱和速度，b 控制长度归一化强度。
    K1 = 1.5
    B = 0.75

    def __init__(
        self,
        cards: List[Dict[str, Any]],
        *,
        tokenize,
        k1: float = K1,
        b: float = B,
    ) -> None:
        self._cards = cards
        self._tokenize = tokenize
        self._k1 = k1
        self._b = b

        #: 每篇文档的 token 词频（一次算好）
        self._tf: List[Dict[str, int]] = []
        #: 每篇文档的 token 总数
        self._dl: List[int] = []
        self._idf: Dict[str, float] = {}

        for card in cards:
            tokens = tokenize(f"{card.get('title', '')} {card.get('content', '')}")
            freq: Dict[str, int] = {}
            for token in tokens:
                freq[token] = freq.get(token, 0) + 1
            self._tf.append(freq)
            self._dl.append(len(tokens))

        n = len(self._tf)
        self._avgdl = (sum(self._dl) / n) if n else 0.0

        # df → IDF，只算一次
        df: Dict[str, int] = {}
        for freq in self._tf:
            for token in freq:
                df[token] = df.get(token, 0) + 1
        for token, freq in df.items():
            self._idf[token] = math.log((n - freq + 0.5) / (freq + 0.5) + 1)

    def __len__(self) -> int:
        return len(self._cards)

    @staticmethod
    def signature_of(cards: List[Dict[str, Any]]) -> str:
        """语料指纹（卡片 id 的稳定摘要）

        只遍历 id，不做分词 —— 它必须比建索引**便宜得多**，
        否则"先算指纹再决定要不要建"就没有意义。
        """
        import hashlib

        digest = hashlib.sha256()
        for card in cards:
            digest.update(str(card.get("card_id") or card.get("note_id") or "").encode())
            digest.update(b"\x00")
        return digest.hexdigest()[:16]

    @property
    def signature(self) -> str:
        """本索引对应的语料指纹"""
        return BM25Index.signature_of(self._cards)

    def search(self, question: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """检索与问题最相关的 top_k 篇文档（BM25）

        score(D, Q) = sum_t IDF(t) * (f(t,D) * (k1+1)) /
                      (f(t,D) + k1 * (1 - b + b * |D| / avgdl))
        IDF(t) = log((N - df(t) + 0.5) / (df(t) + 0.5) + 1)

        Returns:
            List[Dict]: 与旧实现同构的结果（note_id/note_title/content/similarity/
            block_index，以及卡片特有的 card_id/title/chapter_title）
        """
        query_tokens = self._tokenize(question)
        if not query_tokens or not self._tf:
            return []

        k1, b, avgdl = self._k1, self._b, self._avgdl
        scored: List[Dict[str, Any]] = []

        for idx, freq in enumerate(self._tf):
            score = 0.0
            dl = self._dl[idx]
            for query_token in query_tokens:
                tf = freq.get(query_token)
                if not tf:
                    continue
                idf_val = self._idf.get(query_token, 0.0)
                numerator = tf * (k1 + 1)
                if avgdl > 0:
                    denominator = tf + k1 * (1 - b + b * dl / avgdl)
                else:
                    denominator = tf + k1
                if denominator > 0:
                    score += idf_val * numerator / denominator

            if score > 0:
                card = self._cards[idx]
                scored.append({
                    "note_id": card.get("note_id"),
                    "note_title": None,
                    "content": card.get("content"),
                    "similarity": score,
                    "block_index": 0,
                    # 保留卡片特有字段，便于后续构建 sources
                    "card_id": card.get("card_id"),
                    "title": card.get("title"),
                    "chapter_title": card.get("chapter_title"),
                })

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_k]


def build_context_and_sources(
    ordered_items: List[Dict[str, Any]],
    note_titles: Optional[Dict[str, str]] = None,
) -> tuple[str, List[Dict[str, Any]]]:
    """由**已排序**的检索结果产出 (上下文文本, 引用来源列表)

    抽成纯函数是为了可测：这段逻辑原先内嵌在 `retrieve_context` 里，
    而那条路径要调用 LLM 才能跑完，于是"编号与引用是否对得上"这类
    关键一致性**无法被任何测试覆盖**。

    ## 两条硬约束

    1. **上下文编号 = sources 列表下标 + 1**
       提示词（阶段 2.8）要求回答逐条标注 `[编号]`。若上下文按融合排名编号、
       而 sources 按原文位置排序，回答里的 `[1]` 会指向 sources 的**另一项** ——
       用户点第一条引用，跳到的是别处的资料。这种错位不会报错，
       只会让人以为系统在胡说。因此两者必须由同一次遍历产出。

    2. **按 chunk 去重，而不是按 note_id**
       按 note_id 去重会让同一篇笔记只保留第一个 chunk，
       而用户看到的可能是该笔记的第 3 段 —— 点过去跳到另一段。

    Args:
        ordered_items: 已按 `(note_id, char_start)` 排好序的检索结果
        note_titles: `note_id -> 标题`（补 BM25 结果缺失的标题）

    Returns:
        (context, sources)。`sources[i]` 对应上下文里的 `[i+1]`。
    """
    titles = note_titles or {}
    sources: List[Dict[str, Any]] = []
    parts: List[str] = []
    seen: set = set()

    if ordered_items:
        parts.append("=== 相关文档片段 ===")

    for item in ordered_items:
        # 没有 note_id 的结果无法定位到任何笔记，不能进 sources
        # （`AnswerSource.note_id` 是非可空 str）。这里显式跳过而不是依赖
        # 调用方先过滤 —— 约束应当由函数自己保证，否则换个调用方就漏。
        if not item.get("note_id"):
            continue

        content = item.get("content") or ""
        # 无 chunk_id 时退化为「(笔记, 首 200 字)」：降级路径下也不出现完全重复
        dedupe_key = item.get("chunk_id") or (item.get("note_id"), content[:200])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        note_title = (
            item.get("note_title") or item.get("title")
            or titles.get(item.get("note_id")) or "未知笔记"
        )
        chapter_title = item.get("chapter_title")
        chapter_info = f" (章节: {chapter_title})" if chapter_title else ""

        # 编号与 sources 下标严格一致（约束 1）
        index = len(sources) + 1
        parts.append(f"[{index}] 来源: {note_title}{chapter_info}\n{content}")

        sources.append({
            "note_id": item.get("note_id"),
            "note_title": note_title,
            "chapter_title": chapter_title,
            # 展示用摘要；**不是**切片依据（`relevant_text` 比区间短，见 AnswerSource）
            "relevant_text": content[:200],
            # 定位字段（阶段 2.7）
            "chunk_id": item.get("chunk_id"),
            "chunk_index": item.get("index"),
            "char_start": item.get("char_start"),
            "char_end": item.get("char_end"),
            "heading_path": item.get("heading_path") or chapter_title,
            "line_start": item.get("line_start"),
            "line_end": item.get("line_end"),
        })

    return "\n\n".join(parts), sources


class RAGService:
    """
    检索增强生成服务

    使用混合检索策略（向量 + BM25）从用户笔记中召回相关内容，
    通过 RRF 融合后作为上下文调用 LLM 生成回答。

    使用方式：
        service = RAGService()
        result = await service.answer_question("什么是机器学习？", user_id="xxx")
    """

    def __init__(self):
        self._session_factory = None
        #: 上一次构建的 BM25 索引与它对应的 (user_id, 语料指纹)
        #:
        #: **只保留一份**，不是 per-user 字典 —— 这是与已删除的 `_kb_cache`
        #: 的关键区别（见 `_get_user_cards` 的说明）：
        #:   - `_kb_cache` 是 `Dict[user_id, ...]`，多用户即线性增长、无上界
        #:   - 这里是单个槽位，换用户即整体替换，内存有硬上界（一份索引）
        #: 而且槽位里存的是**派生的统计量**（词频/IDF），不是 ORM 实例，
        #: 因此不存在 `DetachedInstanceError` 一类与 session 生命周期绑定的问题。
        self._bm25_slot: Optional[tuple[str, str, BM25Index]] = None

    def _get_bm25_index(
        self, user_id: str, cards: List[Dict[str, Any]],
    ) -> BM25Index:
        """取得该用户语料的 BM25 索引，语料未变时复用

        A-5：原实现每次提问都对全量语料重新分词并重算 df/IDF。
        语料在连续提问之间通常完全没变，这份工作纯属重复。

        失效靠**语料指纹**而不是 TTL：指纹覆盖全部 card_id，
        卡片新增/删除/替换都会改变它。这比 TTL 更准（TTL 到期前语料变了
        会用到旧索引；TTL 到期时语料没变又要白重建一次）。

        **注意指纹必须先于索引构建算出**：若先 `BM25Index(cards,...)`
        再比较指纹，构建（也就是全量分词）已经发生了，优化等于没做。
        第一版就是这么写的，实测无效。
        """
        signature = BM25Index.signature_of(cards)

        slot = self._bm25_slot
        if slot is not None and slot[0] == user_id and slot[1] == signature:
            return slot[2]

        index = BM25Index(cards, tokenize=self._tokenize)
        self._bm25_slot = (user_id, signature, index)
        return index

    def _get_session_factory(self):
        """获取数据库会话工厂（复用主应用会话工厂，避免私有 engine 泄漏，见 docs/decisions.md#F-06）"""
        if self._session_factory is None:
            from ..database import async_session
            self._session_factory = async_session
        return self._session_factory

    async def _get_user_chunks(self, user_id: str) -> List[Dict[str, Any]]:
        """
        获取指定用户的 chunk 语料（回收站笔记除外）

        ## 为什么改用 chunk 语料（阶段 2.3）

        此前 BM25 路的语料是**知识卡片**（LLM 抽取的产物），
        而向量路的语料是**原文 chunk** —— 两路粒度不同，
        融合去重与引用回跳都无法自洽（A-17）。

        实测（同一套 1058 条评测集，BM25 通道，附录 K）：

            chunk 语料 严格 Recall@5 = 60.30%
            卡片语料   严格 Recall@5 = 37.52%

        卡片是 LLM 的二次加工（`card.content` 与 `source_text` 的长度比中位数
        0.93，接近摘录而非概括，但**覆盖范围**窄），而 chunk 就是原文本身。
        产品承诺是"基于你的资料回答"，那就该检索资料本身。

        ## 为什么不再做进程内缓存（阶段 2.10）

        原 `_kb_cache` 是 `Dict[user_id, ...]`、无容量上界、缓存 ORM 实例，
        且正确性靠 5 处调用方记得调 `invalidate_kb_cache()`。
        这类"靠约定维持正确性"的设计在本项目已经出过事
        （见 conftest 里 `test_db` 隔离曾导致测试写真实库的记录）。

        代价：每次问答查一次 `chunks`（单用户千级行，带索引）。
        这是有意取舍 —— 本项目定位本地单用户自托管。
        语料上到十万级时正解是 FTS5（2.5′）让 BM25 下沉到数据库。

        注意：**不过滤 `has_embedding`** —— BM25 是纯词法检索，
        不需要向量。这样"清洗完成但尚未跑嵌入"的窗口期仍可检索（降级而非不可用）。
        """
        from .chunk_service import get_user_chunks

        session_factory = self._get_session_factory()
        async with session_factory() as session:
            return await get_user_chunks(session, user_id)

    async def _search_chunk_vectors(
        self,
        question_embedding: List[float],
        user_id: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """在 `chunks` 表上做向量检索（一次 SQL，取代遍历 N 个 collection）

        模型加载仍隔离在 Celery worker（`_encode_via_celery`），
        但**检索本身不需要模型** —— 向量已存在库里，只需点积。
        因此这里不再走 Celery：省掉一次 worker 往返与
        24 个 Chroma collection 的遍历（D-9 / 2.4′）。

        回收站笔记的 chunk 在此排除（`get_user_chunks` 已排除，
        但向量路直接查表，需要单独处理）。
        """
        from sqlalchemy import select as _select

        from ..models.note import Note as _Note
        from .chunk_search_service import search_chunks

        session_factory = self._get_session_factory()
        async with session_factory() as session:
            trashed = list((await session.execute(
                _select(_Note.id).where(
                    _Note.user_id == user_id, _Note.trashed_at.is_not(None)
                )
            )).scalars().all())
            return await search_chunks(
                session, question_embedding, user_id,
                top_k=top_k, exclude_note_ids=set(trashed) or None,
            )

    async def _encode_via_celery(self, text: str) -> Optional[List[float]]:
        """
        通过 Celery worker 编码文本，返回嵌入向量

        将嵌入模型加载隔离到 Celery worker 进程中，避免在 FastAPI 主进程中
        加载 BGE-M3 模型导致段错误。任务超时或失败时返回 None，调用方应降级处理。

        Args:
            text: 待编码的文本

        Returns:
            Optional[List[float]]: 嵌入向量，失败时返回 None
        """
        try:
            from ..tasks.celery_app import celery_app
            task = celery_app.send_task(
                "app.tasks.embedding_tasks.encode_text",
                args=[[text]],
            )
            # task.get() 是阻塞调用，放入线程池避免卡死事件循环（见 docs/decisions.md#F-06）
            result = await asyncio.to_thread(task.get, 10)
            if result:
                return result[0]
            return None
        except Exception as e:
            logger.warning(f"Celery 嵌入编码失败，将降级为仅 BM25 检索: {e}")
            return None

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """
        分词：按空白和标点分割，中文字符作为 2-gram

        用于 BM25 检索的分词器，兼顾中英文：
        - 中文：提取连续中文字符的 2-gram（bigram），平衡召回率和精度
        - 英文/数字：按空白和标点分割，小写化

        Args:
            text: 待分词的文本

        Returns:
            List[str]: token 列表
        """
        if not text:
            return []
        tokens: List[str] = []
        # 提取中文字符的 2-gram
        chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
        for i in range(len(chinese_chars) - 1):
            tokens.append(chinese_chars[i] + chinese_chars[i + 1])
        # 分割非中文部分（英文/数字），小写化
        non_chinese = re.sub(r"[\u4e00-\u9fff]", " ", text)
        words = re.findall(r"[a-zA-Z0-9]+", non_chinese.lower())
        tokens.extend(words)
        return tokens

    @staticmethod
    def build_bm25_index(cards: List[Dict[str, Any]]) -> "BM25Index":
        """从卡片语料构建 BM25 索引（分词与 df/IDF 只算一次）

        阶段 2.9 引入的**唯一** BM25 入口。详见 `BM25Index` 的说明。

        ## 为什么不再保留 `_search_bm25` 这个薄包装

        它曾是"给一次调用建一次索引"的便捷入口，但线上与评测都已改为
        `build_bm25_index(...).search(...)`（线上还要靠 `_get_bm25_index`
        复用索引，见 A-5），于是它只剩测试在用。一个**只为测试存在**的
        包装函数会掩盖真实调用路径 —— 等价性测试改为直接走
        `build_bm25_index().search()` 之后，测的就是线上真正跑的那条路径。
        """
        return BM25Index(cards, tokenize=RAGService._tokenize)

    @staticmethod
    def _rrf_fusion(
        vector_results: List[Dict[str, Any]],
        bm25_results: List[Dict[str, Any]],
        k: Optional[int] = None,
        bm25_weight: Optional[float] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        加权 Reciprocal Rank Fusion（RRF）融合两路检索结果

        公式：score(d) = (1-w) · 1/(k + rank_vec(d)) + w · 1/(k + rank_bm25(d))
        其中 w = `bm25_weight`，只对**出现过**的那一路计分。

        ## 为什么必须加权、且必须调小 k（阶段 2.6 遗留项，2026-09-11 实测）

        原实现是照搬来的 `k=60` 等权。在本项目语料上实测（1058 条评测集，
        608 个 chunk 语料，BM25 与向量在同语料上）：

        | 配置 | 严格 Recall@5 |
        |---|---|
        | 单通道 BM25 | 59.74% |
        | 单通道向量 | 48.39% |
        | 等权 k=60（原实现） | 57.37% |
        | **加权 k=1 w=0.65** | **60.87%** |

        等权 k=60 比 BM25 单通道**还低**：`k` 越大名次差异被压得越平
        （`k=60` 时第 1 名 `1/61` 与第 5 名 `1/65` 只差 4%），
        于是分数主要由"是否两路同时出现"决定 —— RRF 退化为奖励**共识**。
        两路强弱悬殊时（59.74% vs 48.39%），共识偏向等于把强通道拉向弱通道。

        `k=1, w=0.65` 是在评测集上扫出来的**稳健区域**
        （k∈[1,10]、w∈[0.6,0.8] 均在 60% 以上，不是孤立的尖点）。
        参数由 `rag_rrf_k` / `rag_rrf_bm25_weight` 配置，语料或模型变化后应重扫。

        注意这组权重**绑定当前的相对强弱**：若将来向量质量提升
        （例如换更好的嵌入模型），`w` 必须重新标定，否则会反过来压制向量。
        评测脚本：`scripts/eval_retrieval.py`。

        融合策略：
        - 以 (note_id, content 前 200 字符) 为键去重 —— **键只用于识别同一段
          内容，不作为返回值**（早期测量脚本误把截断后的键当作文档返回，
          导致"融合比单通道差 28%"的假结论，见附录 P.4）
        - 累加各路加权 RRF 分数
        - 按融合分数降序排列，取 top_k

        Args:
            vector_results: 向量检索结果列表
            bm25_results: BM25 检索结果列表
            k: RRF 平滑常数；None 时取配置 `rag_rrf_k`
            bm25_weight: BM25 路权重 ∈ [0,1]；None 时取配置 `rag_rrf_bm25_weight`
            top_k: 返回的最终结果数，默认 5

        Returns:
            List[Dict]: 融合后的结果列表。除两路共有的
                `note_id/note_title/content/similarity/block_index` 外，
                **原样保留各路的附加字段**（卡片的 `card_id/title/chapter_title`、
                chunk 的 `char_start/char_end/heading_path/line_*`）。

                保留附加字段是阶段 2.7 回跳的前提：旧实现用一张**白名单**
                （只有 card_id/title/chapter_title）挑字段，于是 chunk 的
                定位信息在这里被丢掉，回跳链路断在第一层。
        """
        if k is None:
            k = getattr(settings, "rag_rrf_k", 1)
        if bm25_weight is None:
            bm25_weight = getattr(settings, "rag_rrf_bm25_weight", 0.65)

        #: 白名单之外的字段也要带过去（定位信息在这里曾经被丢掉）
        carried_keys = (
            "card_id", "title", "chapter_title",
            "chunk_id", "index",
            "char_start", "char_end", "heading_path", "line_start", "line_end",
        )

        fused: Dict[tuple, Dict[str, Any]] = {}

        for weight, result_list in ((1.0 - bm25_weight, vector_results),
                                    (bm25_weight, bm25_results)):
            if weight <= 0:
                continue
            for rank_idx, item in enumerate(result_list):
                note_id = item.get("note_id")
                content = item.get("content", "") or ""
                # 以 (note_id, content 前 200 字符) 为去重键
                dedupe_key = (note_id, content[:200])

                # rank 从 1 开始；加权 RRF
                rrf_score = weight / (k + rank_idx + 1)

                if dedupe_key not in fused:
                    merged_item = {
                        "note_id": note_id,
                        "note_title": item.get("note_title"),
                        # 存**完整内容**，不是去重键（键是截断过的）
                        "content": content,
                        "similarity": 0.0,
                        "block_index": item.get("block_index", 0),
                    }
                    for extra_key in carried_keys:
                        if extra_key in item and item[extra_key] is not None:
                            merged_item[extra_key] = item[extra_key]
                    fused[dedupe_key] = merged_item
                fused[dedupe_key]["similarity"] += rrf_score

        sorted_results = sorted(
            fused.values(), key=lambda x: x["similarity"], reverse=True
        )
        return sorted_results[:top_k]

    async def retrieve_context(
        self,
        question: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        仅执行检索阶段，返回上下文和引用来源（不调用 LLM）

        完整流程：
        1. 通过 Celery worker 编码问题（隔离嵌入模型加载）
        2. 通过 Celery worker 执行向量检索
        3. 执行 BM25 检索（纯 Python）
        4. 使用 RRF 融合两路结果，取 top 5 作为上下文
        5. 拼接上下文字符串
        6. 构建引用来源（回查笔记标题）

        降级策略：
        - 向量编码/检索失败：仅使用 BM25
        - BM25 也失败：返回空上下文与空来源（调用方据此如实回答"没找到"）

        Args:
            question: 用户问题
            user_id: 用户 ID

        Returns:
            Dict: {"context": str, "sources": list, "provider": str, "retrieval_status": str}
                - context: 拼接好的上下文字符串（可能为空）
                - sources: 引用来源列表，每个含 note_id/note_title/chapter_title/relevant_text
                - provider: LLM 提供商标识（deepseek/glm）
                - retrieval_status: 检索降级状态（full_vector / hybrid / bm25_only）
        """
        provider = settings.get_llm_config()["provider"]

        #: 每路候选池（融合前）。要大于最终 top_k ——
        #: 池=5 时融合只能在那 10 条里排序，答错就出局（实测池=20 更高）。
        pool = getattr(settings, "rag_candidate_pool", 20)

        # 0. 统一语料：两路都跑 **chunk 语料**（阶段 2.3）
        #
        # 在此之前，向量路跑 chunk、BM25 路跑知识卡片 —— 两路语料不同粒度
        # （A-17「三路混合检索实际是两套不同粒度的语料」）。
        # 实测（附录 K）：同一套 1058 条评测集、BM25 通道，
        #   chunk 语料 严格 Recall@5 **60.30%**  ＞  卡片语料 37.52%
        # 语料统一后两路结果才可比、去重才有意义、定位字段才能贯通。
        chunks = await self._get_user_chunks(user_id)

        # 1. 通过 Celery 编码问题（模型加载仍隔离在 worker 进程）
        question_embedding = await self._encode_via_celery(question)

        # 2. 向量检索
        #
        # **不再走 Celery**：向量已存在本地 `chunks` 表（阶段 2.2′ B 半），
        # 检索只需拿 query 向量做点积，不需要模型。旧路径要起一个 worker
        # 任务、遍历 24 个 Chroma collection（D-9），现在是一次 SQL。
        vector_results: List[Dict[str, Any]] = []
        if question_embedding is not None:
            try:
                vector_results = await self._search_chunk_vectors(
                    question_embedding, user_id, top_k=pool
                )
            except Exception as e:
                logger.warning(f"向量检索失败，降级为仅 BM25: {e}")
                vector_results = []
        else:
            logger.warning("嵌入编码失败，跳过向量检索，使用仅 BM25")

        # 检索降级状态：编码失败 -> 仅关键词；编码成功但无向量命中 -> 混合（关键词为主）；
        # 向量通道完整返回 -> 全向量两路融合
        if question_embedding is None:
            retrieval_status = "bm25_only"
        elif not vector_results:
            retrieval_status = "hybrid"
        else:
            retrieval_status = "full_vector"

        # 3. BM25 检索（同一份 chunk 语料；打分为纯函数，评测脚本复用同一实现）
        bm25_results: List[Dict[str, Any]] = []
        try:
            bm25_results = self._get_bm25_index(user_id, chunks).search(
                question, top_k=pool
            )
        except Exception as e:
            logger.warning(f"BM25 检索失败: {e}")

        # 4. 加权 RRF 融合两路结果（阶段 2.6：n-gram 通道已删除）
        fused_results = self._rrf_fusion(
            vector_results, bm25_results, top_k=5
        )

        # 5+6. 同一次遍历产出「上下文编号」与「引用来源」
        #
        # ## 为什么必须同一次遍历（阶段 2.7 踩到的坑）
        #
        # 提示词（阶段 2.8）要求回答**逐条标注 [编号]**，编号来自上下文里的
        # `[N]` 前缀。若上下文按**融合排名**编号、而 sources 按**原文位置**排序，
        # 回答里的 `[1]` 就会指向 sources 列表的另一项 —— 用户点"第 1 条引用"
        # 跳到的是别处的资料。这类错位不会报错，只会让人以为系统在胡说。
        #
        # 因此两者由同一次遍历产出：上下文顺序即 sources 顺序。
        # 排序键用 `(note_id, char_start)`：按原文顺序读，用户核对时不必来回跳。
        #
        # ## 为什么按 chunk 去重（而不是按 note_id）
        #
        # 原先按 `note_id` 去重，于是**同一篇笔记的多个 chunk 只保留一个**。
        # 在"引用能回跳"的目标下这不成立：用户看到的是"第 3 段来自某笔记"，
        # 而 sources 里只剩该笔记的第 1 段，点过去跳到**另一段**。
        # 统一语料（阶段 2.3）后每个结果天然带 `chunk_id`，按 chunk 去重既无重复
        # 又能逐段对应；同一笔记保留多个引用是**有意的** ——
        # 一段长资料里不同位置各自回答了问题的不同侧面。
        ordered = sorted(
            (it for it in fused_results if it.get("note_id") is not None),
            key=lambda it: (it["note_id"], it.get("char_start") or 0),
        )

        # 标题缺失时回查（BM25 结果不带 note_title）
        missing = {it["note_id"] for it in ordered if not (it.get("note_title") or it.get("title"))}
        titles: Dict[str, str] = {}
        if missing:
            session_factory = self._get_session_factory()
            async with session_factory() as session:
                rows = await session.execute(
                    select(Note.id, Note.title).where(Note.id.in_(missing))
                )
                titles = {nid: (title or "未知笔记") for nid, title in rows.all()}

        context, sources = build_context_and_sources(ordered, titles)

        return {
            "context": context,
            "sources": sources,
            "provider": provider,
            "retrieval_status": retrieval_status,
        }

    async def answer_question(
        self,
        question: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        RAG 问答完整流程（混合检索 + RRF 融合）

        在 retrieve_context() 检索结果基础上调用 LLM 生成回答：
        1. 调用 retrieve_context() 完成检索阶段（向量 + BM25 + RRF 融合）
        2. 上下文为空时**如实说明"没找到"，不调用 LLM 兜底**（阶段 2.8）
        3. 上下文非空时调用 llm_service.rag_answer() 基于上下文回答
        4. 返回回答 + 引用来源 + 提供商

        降级策略：
        - 向量编码/检索失败：仅使用 BM25
        - BM25 也失败：上下文为空 → 如实回答"资料中没有找到"

        Args:
            question: 用户问题
            user_id: 用户 ID

        Returns:
            Dict: {"answer": str, "sources": list, "provider": str, "retrieval_status": str}
        """
        llm_service = LLMService()

        # 1. 检索阶段（不调用 LLM）
        retrieval = await self.retrieve_context(question, user_id)
        context = retrieval["context"]
        sources = retrieval["sources"]
        retrieval_status = retrieval.get("retrieval_status", "bm25_only")

        # 2. 检索不到任何资料时：**如实说明，不调用 LLM 兜底**
        #
        # 原实现在这里换一套提示词让模型"用你自己的知识来回答"。
        # 那与阶段 2.8 的目标直接冲突：产品承诺是"基于你的资料回答"，
        # 而这条分支会让一个**没有任何资料依据**的问题得到一段自信、
        # 流畅、看起来像来自用户笔记的回答 —— 恰恰是最该避免的形态。
        #
        # 改为直接返回固定文案。这是有意的取舍：
        #   - 好处：用户永远不会把模型知识误当成自己的资料；
        #     而且省掉一次 LLM 调用（无资料时本来就没有信息可给）
        #   - 代价：用户想问通用问题时得不到回答
        # 若将来确实需要"通用知识"模式，应当是**用户显式选择**的另一个入口
        # （界面上明确标注"以下回答不来自你的资料"），而不是静默降级。
        if not context.strip():
            return {
                "answer": (
                    "在你的资料中没有找到与这个问题相关的内容。\n\n"
                    "可以尝试：\n"
                    "- 换一种问法，或使用更接近资料原文的关键词\n"
                    "- 确认相关资料已经上传并完成「理解」流程\n"
                    "- 若资料确实未覆盖该主题，先补充资料再提问"
                ),
                "sources": [],
                "provider": llm_service._provider,
                "retrieval_status": retrieval_status,
                "no_context": True,
            }

        # 3. 调用 LLM 基于 context 生成回答
        answer = await llm_service.rag_answer(question, context)

        return {
            "answer": answer,
            "sources": sources,
            "provider": llm_service._provider,
            "retrieval_status": retrieval_status,
        }
