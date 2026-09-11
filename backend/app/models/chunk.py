"""
Chunk 模型（overhaul-plan 阶段 2.2′）
====================================

把"检索单元"从**隐式的、每次重建的**中间产物，变成**一等实体**。

## 为什么必须建这张表

在这之前，chunk 只以两种形态存在，两种都不可引用：

1. **Chroma 里的向量 + 元数据**：只存了 `block_index / char_count /
   start_line / end_line`，**没有** `char_start / char_end`，
   而 Chroma 里根本发不出"按 chunk_id 取回"的查询语义。
2. **知识卡片**（BM25 通道的语料）：卡片有 `card_id` 与 `source_text`，
   但**没有原文位置** —— 它是 LLM 的产物，不是原文的切片。

于是引用回跳（2.7）无处可跳。实测的丢失链路（附录 N.4）：

    chunk 有 char_start/char_end/line_start/line_end
      └→ Chroma 元数据只留 block_index/char_count/start_line/end_line
           └→ search_vectors 只往上传 block_index
                └→ _rrf_fusion 只保留固定字段
                     └→ AnswerSource 无任何定位字段

四层里丢了三层。在表里落库之后，回跳变成"按 chunk_id 查偏移"，
不再需要从卡片反推位置，也不再依赖 Chroma 的元数据能否穿过每一层。

## 核心不变量

    text[char_start:char_end] == content

`char_start / char_end` 是**原文（notes.clean_md_path 指向的 Markdown）**里的
字符下标。这条不变量由 `markdown_segmenter.segment_with_offsets` 保证，
并且在开发过程中被违反过**两次**（各 1318 个分段，见附录 L.2）——
所以落库时也要校验（见 `scripts/index_chunks.py --verify`）。

## 向量列先留空

`embedding` 列允许为 NULL，本轮（A 半）不写入：重新嵌入 1872 个 chunk
需要加载 `bge-m3`（4356MB），而本机可用内存仅 4.51GB（门槛 4.0GB），
余量过低、中途失败会留下半新半旧的索引（附录 N.5）。
B 半单独执行。

## 为什么保留 `line_start / line_end`

检索的历史消费方按**行**定位（Chroma 元数据、前端按 `block_index`
恢复重复块），而回跳按**字符偏移**定位。两者描述同一个区间，
在过渡期都要保留，否则老链路会断。
"""

from typing import Optional

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class Chunk(BaseModel):
    """
    检索 chunk（笔记原文的一个连续切片）

    Attributes:
        user_id: 所属用户（多租户隔离，检索必须按它过滤）
        note_id: 来源笔记；笔记被物理删除时本行随之删除
        index: 该 chunk 在所属笔记中的序号（从 0 开始，与 `block_index` 同源）
        content: chunk 文本，满足 `原文[char_start:char_end] == content`
        char_start: 在笔记 Markdown 中的起始字符下标（含）
        char_end: 在笔记 Markdown 中的结束字符下标（不含）
        heading_path: 标题层级路径（如 "第一章 > 1.2 保护配置"），用于展示与定位
        line_start: 起始行号（0 基，含）—— 过渡期兼容按行定位的消费方
        line_end: 结束行号（0 基，含）
        char_count: `len(content)`，冗余但便于排序与统计
        source_md_path: 这段内容来自哪个 Markdown 对象名
            （`clean_md_path` 优先；用于判断索引是否已过期）
        content_hash: `content` 的 sha256，用于检测"原文变了但索引没重建"
        embedding_model: 生成 `embedding` 的模型名；NULL 表示尚未嵌入
        embedding_dim: 向量维度；NULL 表示尚未嵌入
        embedding: 向量 BLOB（float32 小端拼接）
    """
    __tablename__ = "chunks"

    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    note_id: Mapped[str] = mapped_column(
        String, ForeignKey("notes.id"), nullable=False, index=True
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    line_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    line_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    source_md_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # 向量列：本轮（A 半）留空，B 半填入（见模块 docstring）
    embedding_model: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    embedding_dim: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    # 检索热路径要按用户过滤 + 只取已嵌入的行，这里冗余一个布尔标记，
    # 避免每次检索都对 BLOB 列做 IS NOT NULL 判断
    has_embedding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    __table_args__ = (
        # 同一笔记内 index 唯一：重建索引时按 (note_id, index) 幂等覆盖
        Index("ix_chunks_note_index", "note_id", "index", unique=True),
        # 检索按用户过滤 + 只取已嵌入的行
        Index("ix_chunks_user_embed", "user_id", "has_embedding"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<Chunk note={self.note_id} idx={self.index} "
            f"chars={self.char_start}-{self.char_end}>"
        )


def pack_vector(values) -> bytes:
    """把浮点序列打包为 float32 小端 BLOB（与 unpack_vector 成对）

    维度不写进 BLOB —— 它单独存在 `embedding_dim` 列里。
    把维度混进二进制会让"维度对不上"这类问题变成难以察觉的静默错误；
    放在列里可以直接 SQL 过滤、也能被肉眼核对。
    """
    import struct

    return struct.pack(f"<{len(values)}f", *values)


def unpack_vector(blob: Optional[bytes], dim: Optional[int]):
    """把 BLOB 还原为浮点列表；为空或长度不符时返回 None

    长度与 `embedding_dim` 不符说明数据被截断或维度记录有误 ——
    此时**返回 None 而不是尽力解读**：一个长度不对的向量拿去算余弦，
    结果是无意义的数字，比"没有向量"更糟（它会静默参与排序）。
    """
    import struct

    if not blob:
        return None
    if dim is not None and len(blob) != dim * 4:
        return None
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))
