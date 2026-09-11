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
from typing import List, Optional

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
            # 保存并恢复原值，避免误删调用方预设的 HF_HUB_OFFLINE（见 docs/decisions.md#F-19）
            prev_offline = os.environ.get("HF_HUB_OFFLINE")
            os.environ["HF_HUB_OFFLINE"] = "1"
            try:
                self._model = SentenceTransformer(model_path)
            finally:
                if prev_offline is None:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                else:
                    os.environ["HF_HUB_OFFLINE"] = prev_offline
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
    def similarity_from_l2_distance(distance: float) -> float:
        """
        把 Chroma 返回的 L2 **平方**距离换算为余弦相似度

        背景：collection 创建时未指定 `hnsw:space`，Chroma 默认使用
        **平方欧氏距离**（L2²）。BGE-M3 与 bge-small-zh-v1.5 的 modules.json
        都带尾部 Normalize 模块、输出单位向量，因此有恒等关系：

            ||a - b||² = 2 - 2·cos(a, b)      (a、b 为单位向量)
            => cos(a, b) = 1 - distance / 2

        旧实现用 `1 / (1 + distance)`，那不是余弦：距离 0→1.0 正确，
        但语义**完全无关**（正交，cos=0，distance=2）只得 0.333，
        而"完全相反"（cos=-1，distance=4）得 0.2 ——
        整个 [0.33, 0.2] 区间挤在一起，既不可解释也无法用于阈值过滤。

        Args:
            distance: Chroma 返回的 L2 平方距离（单位向量下范围 [0, 4]）

        Returns:
            float: 余弦相似度，裁剪到 [-1, 1]
        """
        try:
            d = float(distance)
        except (TypeError, ValueError):
            return 0.0
        cos = 1.0 - d / 2.0
        # 数值噪声可能略微越界（d 因浮点误差略小于 0 或略大于 4）
        return max(-1.0, min(1.0, cos))

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
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)

