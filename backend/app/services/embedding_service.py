"""
嵌入模型与向量存储服务模块

本模块提供文本嵌入向量生成和向量存储功能，用于 AI 清洗管道中的去重检测。
采用 Chroma 嵌入式向量数据库，与项目"零依赖启动"理念一致。

主要职责：
- 加载 sentence-transformers 模型生成文本嵌入向量
- 计算向量间的余弦相似度
- 使用 Chroma 存储和检索文本块的嵌入向量
- 查找同一篇笔记中的重复块

设计决策：
- 嵌入模型延迟加载，首次使用时才初始化，避免启动时加载大模型
- Chroma 持久化到 data/chroma/ 目录，无需额外部署服务
- 余弦相似度阈值可配置（默认 0.85），平衡去重精度和召回率
- 向量存储按 note_id 组织，便于按笔记查询和删除
"""

import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import get_settings, DATA_DIR

settings = get_settings()


class EmbeddingService:
    """
    文本嵌入服务

    使用 sentence-transformers 模型将文本转换为向量表示。
    模型通过 ModelScope 下载（国内镜像，无需翻墙），首次调用 encode() 时延迟加载。

    使用方式：
        service = EmbeddingService()
        vectors = service.encode(["文本1", "文本2"])
        similarity = service.compute_similarity(vectors[0], vectors[1])
    """

    def __init__(self):
        self._model = None
        self._model_name = settings.embedding_model

    def _ensure_model(self):
        """
        延迟加载嵌入模型，首次使用时初始化

        优先通过 ModelScope 下载模型到本地缓存目录，
        然后用 sentence-transformers 从本地路径加载。
        ModelScope 是国内镜像，下载速度快且稳定。
        """
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            # 尝试从 ModelScope 下载模型到本地
            model_path = self._download_from_modelscope(self._model_name)
            if model_path:
                self._model = SentenceTransformer(model_path)
            else:
                # ModelScope 下载失败，回退到 HuggingFace（需网络）
                self._model = SentenceTransformer(self._model_name)

    @staticmethod
    def _download_from_modelscope(model_name: str) -> Optional[str]:
        """通过 ModelScope 下载模型到本地缓存目录

        ModelScope 的模型名格式与 HuggingFace 不同：
        HuggingFace: "BAAI/bge-m3"
        ModelScope:  "Xorbits/bge-m3"（ModelScope 上的镜像）

        Args:
            model_name: HuggingFace 格式的模型名

        Returns:
            本地模型路径，下载失败返回 None
        """
        # HuggingFace 模型名 → ModelScope 模型名映射
        modelscope_mapping = {
            "BAAI/bge-m3": "Xorbits/bge-m3",
            "paraphrase-multilingual-MiniLM-L12-v2": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        }

        ms_model_name = modelscope_mapping.get(model_name)
        if not ms_model_name:
            return None

        try:
            from modelscope import snapshot_download
            # 下载到项目的 data/models/ 目录，避免占用 C 盘空间
            cache_dir = str(DATA_DIR / "models")
            model_dir = snapshot_download(
                ms_model_name,
                cache_dir=cache_dir,
            )
            return model_dir
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                f"ModelScope 下载模型失败 ({ms_model_name}): {e}，将回退到 HuggingFace"
            )
            return None

    def encode(self, texts: List[str]) -> List[List[float]]:
        """
        将文本列表转换为嵌入向量列表

        Args:
            texts: 待编码的文本列表

        Returns:
            List[List[float]]: 嵌入向量列表，每个向量是一个浮点数列表
        """
        if not texts:
            return []
        self._ensure_model()
        embeddings = self._model.encode(texts, show_progress_bar=False)
        # numpy 数组转为 Python 列表，便于 JSON 序列化
        return embeddings.tolist()

    @staticmethod
    def compute_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """
        计算两个向量的余弦相似度

        余弦相似度衡量两个向量方向的相似程度，取值范围 [-1, 1]。
        值越接近 1 表示越相似，接近 0 表示无关，接近 -1 表示相反。

        Args:
            vec_a: 向量 A
            vec_b: 向量 B

        Returns:
            float: 余弦相似度，范围 [-1, 1]
        """
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)


class VectorStore:
    """
    向量存储服务

    使用 Chroma 嵌入式向量数据库存储文本块的嵌入向量，
    支持按笔记 ID 组织、查询重复块和删除数据。

    数据组织：
    - Collection 名称：note_{note_id}，每篇笔记一个独立的 collection
    - 每个 document 存储分块内容，metadata 包含块索引和行号信息
    - embedding 为分块的嵌入向量

    使用方式：
        store = VectorStore()
        store.add_chunks("note_123", chunks, embeddings)
        duplicates = store.find_duplicates("note_123")
        store.delete_note_chunks("note_123")
    """

    def __init__(self):
        self._client = None
        self._persist_dir = self._get_persist_dir()

    def _get_persist_dir(self) -> str:
        """获取 Chroma 持久化目录路径"""
        if settings.chroma_dir:
            return settings.chroma_dir
        chroma_dir = DATA_DIR / "chroma"
        chroma_dir.mkdir(parents=True, exist_ok=True)
        return str(chroma_dir)

    def _ensure_client(self):
        """延迟初始化 Chroma 客户端"""
        if self._client is None:
            import chromadb
            self._client = chromadb.PersistentClient(path=self._persist_dir)

    def _get_collection_name(self, note_id: str) -> str:
        """生成 collection 名称，确保合法（Chroma 要求名称为 3-63 字母数字字符）"""
        # 将 note_id 中的非字母数字字符替换为下划线
        safe_name = "".join(c if c.isalnum() else "_" for c in note_id)
        # 确保长度在 3-63 之间
        if len(safe_name) < 3:
            safe_name = safe_name + "_" * (3 - len(safe_name))
        return f"note_{safe_name}"

    def add_chunks(
        self,
        note_id: str,
        chunks: List[Dict],
        embeddings: Optional[List[List[float]]] = None,
    ):
        """
        添加文本分块及其嵌入向量到向量存储

        如果未提供 embeddings，则自动使用 EmbeddingService 生成。

        Args:
            note_id: 笔记 ID
            chunks: 分块列表，每个包含 index、content、start_line、end_line
            embeddings: 嵌入向量列表（可选，不提供则自动生成）
        """
        if not chunks:
            return

        self._ensure_client()
        collection_name = self._get_collection_name(note_id)

        # 如果 collection 已存在，先删除（支持重新清洗）
        try:
            self._client.delete_collection(collection_name)
        except Exception:
            pass

        collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"note_id": note_id},
        )

        # 如果没有提供嵌入向量，自动生成
        if embeddings is None:
            service = EmbeddingService()
            texts = [chunk["content"] for chunk in chunks]
            embeddings = service.encode(texts)

        # 准备数据
        ids = [f"chunk_{chunk['index']}" for chunk in chunks]
        documents = [chunk["content"] for chunk in chunks]
        metadatas = [
            {
                "block_index": chunk["index"],
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "char_count": chunk["char_count"],
            }
            for chunk in chunks
        ]

        # 分批添加（Chroma 单次添加有数量限制）
        batch_size = 100
        for i in range(0, len(ids), batch_size):
            end = min(i + batch_size, len(ids))
            collection.add(
                ids=ids[i:end],
                documents=documents[i:end],
                embeddings=embeddings[i:end],
                metadatas=metadatas[i:end],
            )

    def find_duplicates(
        self,
        note_id: str,
        threshold: float = 0.0,
    ) -> List[Dict]:
        """
        查找同一篇笔记中的重复块

        对笔记中的每个块，查询与其最相似的其他块。
        如果相似度超过阈值，则标记为重复。

        去重策略：
        - 保留首次出现的块（block_index 较小者）
        - 后续出现的相似块标记为重复
        - 避免重复标记（A 与 B 重复，B 与 C 重复，只标记 B 和 C）

        Args:
            note_id: 笔记 ID
            threshold: 相似度阈值，0 表示使用配置默认值

        Returns:
            List[Dict]: 重复块列表，每个包含：
                - block_index: 重复块的索引
                - duplicate_of: 首次出现的块索引
                - similarity: 相似度分数
        """
        threshold = threshold or settings.similarity_threshold

        self._ensure_client()
        collection_name = self._get_collection_name(note_id)

        try:
            collection = self._client.get_collection(collection_name)
        except Exception:
            return []

        # 获取所有块的数据
        all_data = collection.get(include=["embeddings", "metadatas", "documents"])
        if not all_data["ids"]:
            return []

        embeddings = all_data["embeddings"]
        metadatas = all_data["metadatas"]
        n = len(embeddings)

        if n < 2:
            return []

        # 计算两两相似度
        duplicates = []
        already_duplicate = set()  # 已被标记为重复的块索引

        for i in range(n):
            if metadatas[i]["block_index"] in already_duplicate:
                continue  # 已被标记为重复的块不再作为"首次出现"

            for j in range(i + 1, n):
                if metadatas[j]["block_index"] in already_duplicate:
                    continue  # 已被标记为重复的块跳过

                similarity = EmbeddingService.compute_similarity(
                    embeddings[i], embeddings[j]
                )

                if similarity >= threshold:
                    # j 是 i 的重复块
                    duplicates.append({
                        "block_index": metadatas[j]["block_index"],
                        "duplicate_of": metadatas[i]["block_index"],
                        "similarity": similarity,
                    })
                    already_duplicate.add(metadatas[j]["block_index"])

        # 按相似度降序排列
        duplicates.sort(key=lambda x: x["similarity"], reverse=True)
        return duplicates

    def delete_note_chunks(self, note_id: str):
        """
        删除笔记的所有向量数据

        Args:
            note_id: 笔记 ID
        """
        self._ensure_client()
        collection_name = self._get_collection_name(note_id)

        try:
            self._client.delete_collection(collection_name)
        except Exception:
            pass  # collection 可能不存在，静默忽略
