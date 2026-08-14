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

import logging
import math
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import get_settings, DATA_DIR

settings = get_settings()
logger = logging.getLogger(__name__)

# 模块级单例：缓存已加载的 SentenceTransformer 模型实例
# 避免每次 EmbeddingService() 调用都从磁盘重新加载 ~2.2GB 模型
_cached_model = None
_cached_model_name = None
# 模型加载锁：防止并发 encode() 触发重复加载（同进程双份模型瞬时占用 2× 内存，
# 日志实证：run_in_executor 并发调用时出现过两次 "Loading SentenceTransformer model"）
_load_lock = threading.Lock()

# 内存不足类异常的判定特征（PyTorch CPU 分配器 / Windows 虚拟内存不足）
_MEMORY_ERROR_MARKERS = (
    "not enough memory",
    "defaultcpuallocator",
    "alloc_cpu",
    "out of memory",
    "页面文件太小",
    "os error 1455",
    "memoryerror",
)


def get_available_memory_gb() -> float:
    """获取当前可用物理内存（GB），无法获取时返回 0.0（触发保守降级）"""
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:
        return 0.0


def _is_memory_error(exc: Exception) -> bool:
    """判断异常是否为内存不足导致（PyTorch/Windows 内存分配失败）"""
    text = str(exc).lower()
    return any(marker in text for marker in _MEMORY_ERROR_MARKERS)


def _pick_embedding_model_name() -> str:
    """
    根据可用内存选择实际加载的模型名

    空闲内存低于 embedding_min_free_memory_gb 时，跳过配置的主模型，
    直接使用降级模型（避免加载 bge-m3 这类大模型时 OOM 崩溃）。
    """
    model_name = settings.embedding_model
    fallback = settings.embedding_model_fallback
    if fallback and model_name != fallback:
        available = get_available_memory_gb()
        if available < settings.embedding_min_free_memory_gb:
            logger.warning(
                "可用内存不足（%.1fGB < %.1fGB），跳过主模型 %s，改用降级模型 %s",
                available, settings.embedding_min_free_memory_gb, model_name, fallback,
            )
            return fallback
    return model_name


class EmbeddingService:
    """
    文本嵌入服务

    使用 sentence-transformers 模型将文本转换为向量表示。
    模型通过 ModelScope 下载（国内镜像，无需翻墙），首次调用 encode() 时延迟加载。
    多次创建 EmbeddingService 实例共享同一个模型对象，避免重复加载。

    内存友好设计（低内存机器不因模型加载而崩溃）：
    - 加载前检查可用内存，低于阈值自动切换降级模型（见 _pick_embedding_model_name）
    - 主模型加载失败（内存不足等）时自动重试降级模型，全部失败才抛出带指引的错误
    - 加锁防止并发 encode() 触发重复加载，避免瞬时 2× 内存占用

    使用方式：
        service = EmbeddingService()
        vectors = service.encode(["文本1", "文本2"])
        similarity = service.compute_similarity(vectors[0], vectors[1])
    """

    def __init__(self):
        global _cached_model, _cached_model_name
        # 复用已加载的模型实例（进程内只加载一次，即使实际加载的是降级模型）
        if _cached_model is not None:
            self._model = _cached_model
            # 同步实际加载的模型名（可能是降级模型），保证 loaded_model_name 准确
            self._model_name = _cached_model_name
        else:
            self._model = None
            # 期望加载的模型名；实际加载成功后更新为真实加载的模型名
            self._model_name = settings.embedding_model

    @property
    def loaded_model_name(self) -> Optional[str]:
        """当前实际加载的模型名（尚未加载时返回 None）"""
        return self._model_name if self._model is not None else None

    def _ensure_model(self):
        """
        延迟加载嵌入模型，首次使用时初始化

        优先通过 ModelScope 下载模型到本地缓存目录，
        然后用 sentence-transformers 从本地路径加载。
        ModelScope 是国内镜像，下载速度快且稳定。
        加载后缓存到模块级单例，后续实例直接复用。

        在锁内执行加载，并发调用时只有第一个线程真正加载，
        其余线程复用已加载的模型实例。
        """
        global _cached_model, _cached_model_name
        if self._model is not None:
            return

        with _load_lock:
            if self._model is not None:
                return
            if _cached_model is not None:
                # 复用其他实例已加载的模型（含降级模型）
                self._model = _cached_model
                self._model_name = _cached_model_name
                return

            # 根据可用内存选择实际加载的模型
            model_name = _pick_embedding_model_name()
            try:
                self._load_model_with_fallback(model_name)
            except Exception as e:
                available = get_available_memory_gb()
                if _is_memory_error(e):
                    hint = (
                        f"当前可用内存约 {available:.1f}GB。"
                        f"请关闭部分程序释放内存、增大 Windows 页面文件后重试；"
                        f"或将 EMBEDDING_MODEL 配置为更轻量的模型（如 BAAI/bge-small-zh-v1.5）。"
                    )
                else:
                    hint = f"请检查模型文件是否完整、网络是否可用（当前可用内存约 {available:.1f}GB）。"
                raise RuntimeError(f"嵌入模型加载失败（{type(e).__name__}: {e}）。{hint}") from e

            # 缓存到模块级单例
            _cached_model = self._model
            _cached_model_name = self._model_name

    def _load_model_with_fallback(self, model_name: str):
        """
        按降级链尝试加载模型：主模型失败时自动重试降级模型

        Args:
            model_name: 首选模型名（可能已由内存预检替换为降级模型）
        """
        candidates = [model_name]
        fallback = settings.embedding_model_fallback
        if fallback and fallback != model_name:
            candidates.append(fallback)

        last_exc: Optional[Exception] = None
        for candidate in candidates:
            try:
                self._load_model_candidate(candidate)
                if candidate != settings.embedding_model:
                    logger.warning("嵌入模型已降级加载: %s（配置主模型为 %s）", candidate, settings.embedding_model)
                return
            except Exception as e:
                last_exc = e
                logger.warning("嵌入模型 %s 加载失败: %s", candidate, e)
        raise last_exc  # type: ignore[misc]

    def _load_model_candidate(self, model_name: str):
        """加载单个候选模型（ModelScope 本地路径优先，回退 HuggingFace）"""
        from sentence_transformers import SentenceTransformer

        # 尝试从 ModelScope 下载模型到本地
        model_path = self._download_from_modelscope(model_name)
        if model_path:
            # 从本地路径加载时，设置 HF_HUB_OFFLINE=1 阻止 SentenceTransformer
            # 尝试连接 HuggingFace 下载 modules.json 等额外文件
            os.environ["HF_HUB_OFFLINE"] = "1"
            try:
                self._model = SentenceTransformer(model_path)
            finally:
                os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            # ModelScope 下载失败，回退到 HuggingFace（需网络）
            self._model = SentenceTransformer(model_name)

        self._model_name = model_name
        logger.info(
            "嵌入模型加载成功: %s（可用内存 %.1fGB）",
            model_name,
            get_available_memory_gb(),
        )

    @staticmethod
    def _download_from_modelscope(model_name: str) -> Optional[str]:
        """通过 ModelScope 下载模型到本地缓存目录

        ModelScope 的模型名格式与 HuggingFace 不同：
        HuggingFace: "BAAI/bge-m3"
        ModelScope:  "Xorbits/bge-m3"（ModelScope 上的镜像）

        优化：先检查本地缓存是否已存在，避免每次都调用 snapshot_download
        扫描目录和检查远程更新，减少文件 I/O 和网络请求。

        Args:
            model_name: HuggingFace 格式的模型名

        Returns:
            本地模型路径，下载失败返回 None
        """
        # HuggingFace 模型名 → ModelScope 模型名映射
        modelscope_mapping = {
            "BAAI/bge-m3": "Xorbits/bge-m3",
            "BAAI/bge-small-zh-v1.5": "BAAI/bge-small-zh-v1.5",
            "paraphrase-multilingual-MiniLM-L12-v2": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        }

        ms_model_name = modelscope_mapping.get(model_name)
        if not ms_model_name:
            return None

        # 先检查本地缓存是否已存在，避免每次都调用 snapshot_download
        cache_dir = str(DATA_DIR / "models")
        local_model_dir = os.path.join(cache_dir, ms_model_name.replace("/", os.sep))
        config_file = os.path.join(local_model_dir, "config.json")
        if os.path.isfile(config_file):
            return local_model_dir

        # modelscope 缓存目录命名：模型名中的 "." 会替换为 "___"。
        # 无管理员权限时符号链接创建失败，文件实际落在该目录下（如 bge-small-zh-v1___5）
        ms_cache_dir = os.path.join(
            cache_dir, ms_model_name.replace("/", os.sep).replace(".", "___")
        )
        ms_config_file = os.path.join(ms_cache_dir, "config.json")
        if os.path.isfile(ms_config_file):
            return ms_cache_dir

        try:
            # 兼容不同版本的 modelscope 导入路径
            try:
                from modelscope import snapshot_download
            except ImportError:
                from modelscope.hub.snapshot_download import snapshot_download
            # 下载到项目的 data/models/ 目录，避免占用 C 盘空间
            model_dir = snapshot_download(
                ms_model_name,
                cache_dir=cache_dir,
            )
            return model_dir
        except Exception as e:
            logger.warning(
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
        embeddings = self._model.encode(
            texts,
            batch_size=settings.embedding_batch_size,
            show_progress_bar=False,
        )
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
        embedding_model: Optional[str] = None,
    ):
        """
        添加文本分块及其嵌入向量到向量存储

        如果未提供 embeddings，则自动使用 EmbeddingService 生成。
        collection 元数据记录生成向量所用的模型名与维度，
        供检索方识别并跳过模型不匹配的 collection（不同模型向量不可比）。

        Args:
            note_id: 笔记 ID
            chunks: 分块列表，每个包含 index、content、start_line、end_line
            embeddings: 嵌入向量列表（可选，不提供则自动生成）
            embedding_model: 生成向量所用的模型名（可选，不提供则取当前加载模型）
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

        # 如果没有提供嵌入向量，自动生成
        if embeddings is None:
            service = EmbeddingService()
            texts = [chunk["content"] for chunk in chunks]
            embeddings = service.encode(texts)
            if embedding_model is None:
                embedding_model = service.loaded_model_name
        elif embedding_model is None:
            embedding_model = EmbeddingService().loaded_model_name

        # 记录生成向量所用的模型（跨模型一致性：不同模型维度不同，不可混查）
        collection_metadata = {"note_id": note_id}
        if embedding_model:
            collection_metadata["embedding_model"] = embedding_model
        if embeddings:
            collection_metadata["embedding_dim"] = len(embeddings[0])

        collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata=collection_metadata,
        )

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

                # 跳过行范围有重叠的 chunk 对（重叠是分块策略的正常结果，非真正重复）
                i_start = metadatas[i].get("start_line", 0)
                i_end = metadatas[i].get("end_line", 0)
                j_start = metadatas[j].get("start_line", 0)
                j_end = metadatas[j].get("end_line", 0)
                if i_start <= j_end and j_start <= i_end:
                    continue

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
