"""
ASR 语音转写模块

将音视频文件转录为结构化的 Markdown 文本。

主要功能:
    - 支持 .mp4, .mkv, .mov, .mp3, .wav, .m4a 等格式
    - 自动音视频分离（ffmpeg）
    - VAD 语音检测与长音频切分（silero-vad）
    - Qwen3-ASR-0.6B 本地模型转录（CPU 模式）
    - 标点恢复与标题生成（通过 EngramNote 配置的 LLM API）
    - 文件缓存避免重复处理

使用示例:
    >>> from app.services.asr import convert
    >>> result = convert("lecture.mp4", language="Chinese")
    >>> print(result.title)
    >>> print(result.markdown_content)
"""

from .audio_utils import check_ffmpeg, extract_audio
from .converter import convert
from .models import (
    CHUNK_DURATION_S,
    OVERLAP_DURATION_S,
    SAMPLE_RATE,
    SUPPORTED_AUDIO_EXTS,
    SUPPORTED_EXTS,
    SUPPORTED_LANGUAGES,
    SUPPORTED_VIDEO_EXTS,
    AsrSourceType,
    ConversionResult,
    VADSegment,
)


def check_dependencies() -> list[str]:
    """检查所有依赖是否可用，返回缺失依赖列表。"""
    missing = []

    if not check_ffmpeg():
        missing.append("ffmpeg: 未安装或不在 PATH 中，无法处理视频文件")

    try:
        import torch
    except ImportError:
        missing.append("torch: 未安装，ASR 模型无法运行")

    try:
        from qwen_asr import Qwen3ASRModel
    except ImportError:
        missing.append("qwen_asr: 未安装，ASR 模型无法加载")

    try:
        import soundfile
    except ImportError:
        missing.append("soundfile: 未安装，音频处理不可用")

    try:
        import numpy
    except ImportError:
        missing.append("numpy: 未安装")

    return missing


__all__ = [
    "convert",
    "extract_audio",
    "check_ffmpeg",
    "check_dependencies",
    "ConversionResult",
    "AsrSourceType",
    "VADSegment",
    "SUPPORTED_EXTS",
    "SUPPORTED_AUDIO_EXTS",
    "SUPPORTED_VIDEO_EXTS",
    "SUPPORTED_LANGUAGES",
    "SAMPLE_RATE",
    "CHUNK_DURATION_S",
    "OVERLAP_DURATION_S",
]
