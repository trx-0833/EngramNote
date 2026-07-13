from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import List, Optional

import numpy as np
import torch
from openai import OpenAI
from qwen_asr import Qwen3ASRModel

from .models import SAMPLE_RATE, SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

PUNCTUATION_PROMPT = (
    "请为以下文本添加合适的标点符号并分段。"
    "只输出添加标点后的文本，不要添加任何解释或额外内容。\n\n"
)

TITLE_GENERATION_PROMPT = (
    "请根据以下转录文本内容，生成一个简短的标题（不超过20个字）。"
    "只输出标题文本，不要添加引号或其他标记。\n\n"
)

# --- OpenAI client cache ---
_client_cache: dict[str, OpenAI] = {}
_client_lock = threading.Lock()


def _get_openai_client(api_key: str, base_url: str) -> OpenAI:
    cache_key = f"{api_key}:{base_url}"
    if cache_key not in _client_cache:
        with _client_lock:
            if cache_key not in _client_cache:
                _client_cache[cache_key] = OpenAI(api_key=api_key, base_url=base_url)
    return _client_cache[cache_key]


def _get_model_path() -> str:
    """获取 ASR 模型路径，优先从 EngramNote 配置读取。"""
    try:
        from ...config import get_settings
        settings = get_settings()
        if settings.asr_model_path:
            return settings.asr_model_path
    except Exception:
        pass
    return os.environ.get(
        "ASR_MODEL_PATH",
        os.path.expanduser("~/.cache/modelscope/hub/models/Qwen/Qwen3-ASR-0___6B"),
    )


class ASREngine:
    """
    Qwen3-ASR 引擎，采用单例模式加载模型。

    使用方式:
        engine = ASREngine.get_instance()
        result = engine.transcribe(audio_path)

    语言参数说明:
        - language=None: 自动检测语言（默认）
        - language="Chinese": 强制中文识别，提高中文准确率
        - language="English": 强制英文识别，提高英文准确率
        - 其他支持的语言见 SUPPORTED_LANGUAGES 列表
    """

    _instance: Optional[ASREngine] = None
    _lock = threading.Lock()
    _model: Optional[Qwen3ASRModel] = None

    def __init__(self):
        raise RuntimeError("请使用 ASREngine.get_instance() 获取单例实例")

    @classmethod
    def get_instance(cls) -> ASREngine:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = object.__new__(cls)
                    instance._model = None
                    instance._model_lock = threading.Lock()
                    cls._instance = instance
        return cls._instance

    @classmethod
    def reset_instance(cls):
        with cls._lock:
            if cls._instance is not None:
                cls._instance._model = None
                cls._instance = None

    def _load_model(self) -> Qwen3ASRModel:
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    model_path = _get_model_path()
                    logger.info("正在加载 Qwen3-ASR-0.6B 模型（CPU 模式，首次加载较慢）...")
                    logger.warning("使用 CPU 推理，速度较慢。如有 GPU 可修改 device_map 参数。")
                    self._model = Qwen3ASRModel.from_pretrained(
                        model_path,
                        dtype=torch.float32,
                        device_map="cpu",
                        max_inference_batch_size=1,
                        max_new_tokens=512,
                    )
                    logger.info("模型加载完成！")
        return self._model

    def transcribe_file(
        self,
        audio_path: str | Path,
        language: Optional[str] = None,
    ) -> tuple[str, str]:
        model = self._load_model()
        results = model.transcribe(audio=[str(audio_path)], language=language)
        result = results[0]
        return result.text, result.language

    def transcribe_array(
        self,
        audio: np.ndarray,
        sr: int = SAMPLE_RATE,
        language: Optional[str] = None,
    ) -> tuple[str, str]:
        model = self._load_model()
        results = model.transcribe(audio=[(audio, sr)], language=language)
        result = results[0]
        return result.text, result.language

    def transcribe_chunks(
        self,
        chunks: List[tuple],
        language: Optional[str] = None,
    ) -> tuple[str, str]:
        if not chunks:
            return "", ""
        if len(chunks) == 1:
            audio, _, _ = chunks[0]
            return self.transcribe_array(audio, language=language)

        all_texts: List[str] = []
        detected_lang = ""

        for i, (chunk_audio, start_s, end_s) in enumerate(chunks):
            if chunk_audio is None or len(chunk_audio) == 0:
                continue  # 跳过空片段
            text, lang = self.transcribe_array(chunk_audio, language=language)
            if not detected_lang and lang:
                detected_lang = lang
            if text.strip():
                all_texts.append(text.strip())

        full_text = _merge_overlapping_texts(all_texts)
        return full_text, detected_lang


def _merge_overlapping_texts(texts: List[str]) -> str:
    if not texts:
        return ""
    if len(texts) == 1:
        return texts[0]

    merged = texts[0]
    for i in range(1, len(texts)):
        overlap = _find_overlap(merged, texts[i])
        if overlap:
            overlap_len = len(overlap)
            merged = merged + texts[i][overlap_len:]
        else:
            merged = merged + texts[i]

    return merged


def _find_overlap(text_a: str, text_b: str, min_len: int = 4) -> str:
    """二分搜索查找最长公共子串，O(n log n)。"""
    max_search = min(len(text_a), len(text_b), 200)
    if max_search < min_len:
        return ""

    best = ""
    lo, hi = min_len, max_search
    while lo <= hi:
        mid = (lo + hi) // 2
        if text_a[-mid:] == text_b[:mid]:
            best = text_a[-mid:]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def restore_punctuation(raw_text: str, api_key: str, base_url: str, model: str) -> str:
    if not raw_text.strip():
        return raw_text

    client = _get_openai_client(api_key, base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": PUNCTUATION_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    return response.choices[0].message.content.strip()


def generate_title(transcribed_text: str, api_key: str, base_url: str, model: str) -> str:
    snippet = transcribed_text[:200]
    if not snippet.strip():
        return "未命名"

    client = _get_openai_client(api_key, base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": TITLE_GENERATION_PROMPT},
            {"role": "user", "content": snippet},
        ],
        temperature=0.3,
        max_tokens=50,
    )
    title = response.choices[0].message.content.strip()
    title = re.sub(r'^["""\']+|["""\']+$', "", title)
    return title if title else "未命名"


def has_punctuation(text: str) -> bool:
    punctuation_pattern = r'[。，！？；：、,.!?]'
    return bool(re.search(punctuation_pattern, text))
