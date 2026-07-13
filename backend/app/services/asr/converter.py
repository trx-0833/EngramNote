from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .asr_engine import ASREngine, generate_title, has_punctuation, restore_punctuation
from .audio_utils import (
    check_ffmpeg,
    cleanup_temp_dir,
    extract_audio,
    is_audio_file,
    is_video_file,
    load_audio_as_wav,
    split_audio_by_chunks,
    vad_split_audio,
)
from .cache import ConversionCache
from .models import (
    CHUNK_DURATION_S,
    OVERLAP_DURATION_S,
    SAMPLE_RATE,
    AsrSourceType,
    ConversionResult,
    SUPPORTED_LANGUAGES,
    VADSegment,
)

logger = logging.getLogger(__name__)


def _load_llm_config() -> tuple[Optional[str], str, str]:
    """
    从 EngramNote 配置获取 LLM API 配置。

    优先使用 EngramNote 的 config.py 统一配置体系，
    不再自行解析 .env 文件。
    """
    try:
        from ...config import get_settings
        settings = get_settings()
        llm_config = settings.get_llm_config()
        return (
            llm_config["api_key"],
            llm_config["base_url"],
            llm_config["model"],
        )
    except Exception:
        logger.warning("无法从 EngramNote 配置获取 LLM 配置，回退到环境变量")
        import os
        return (
            os.environ.get("DEEPSEEK_API_KEY"),
            os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        )


def _validate_input(
    file_path: Path,
    language: Optional[str],
) -> Optional[ConversionResult]:
    if not file_path.exists():
        return ConversionResult(
            title="",
            source_path=str(file_path),
            error=f"文件不存在: {file_path}",
            success=False,
        )

    if not is_audio_file(file_path) and not is_video_file(file_path):
        return ConversionResult(
            title="",
            source_path=str(file_path),
            error=f"不支持的文件格式: {file_path.suffix}",
            success=False,
        )

    if is_video_file(file_path) and not check_ffmpeg():
        return ConversionResult(
            title="",
            source_path=str(file_path),
            error="ffmpeg 不可用，无法处理视频文件。请安装 ffmpeg 并确保其在 PATH 中。",
            success=False,
        )

    if language is not None and language not in SUPPORTED_LANGUAGES:
        return ConversionResult(
            title="",
            source_path=str(file_path),
            error=f"不支持的语言: {language}，支持的语言: {', '.join(SUPPORTED_LANGUAGES)}",
            success=False,
        )

    return None


def _get_audio_chunks(
    file_path: Path,
    audio_path: Path,
    chunk_duration_s: int,
    overlap_duration_s: int,
    vad_threshold: float,
) -> tuple[list, list, bool]:
    chunks, vad_segments = vad_split_audio(
        audio_path,
        sr=SAMPLE_RATE,
        chunk_duration_s=chunk_duration_s,
        overlap_duration_s=overlap_duration_s,
        vad_threshold=vad_threshold,
    )

    if vad_segments:
        return chunks, vad_segments, False

    logger.info("VAD 未检测到语音段，降级为固定时长切分")
    audio, audio_sr = load_audio_as_wav(audio_path, target_sr=SAMPLE_RATE)
    chunks = split_audio_by_chunks(
        audio, sr=SAMPLE_RATE,
        chunk_duration_s=chunk_duration_s,
        overlap_duration_s=overlap_duration_s,
    )
    return chunks, [], True


def _post_process(
    raw_text: str,
    file_path: Path,
    enable_punctuation: bool,
    enable_title_generation: bool,
    api_key: Optional[str],
    base_url: str,
    llm_model: str,
) -> tuple[str, str]:
    final_text = raw_text

    if enable_punctuation and not has_punctuation(raw_text) and api_key:
        try:
            final_text = restore_punctuation(raw_text, api_key, base_url, llm_model)
        except Exception as e:
            logger.warning("标点恢复失败，使用原始文本: %s", e)
            final_text = raw_text

    title = file_path.stem
    if enable_title_generation and api_key:
        try:
            title = generate_title(final_text, api_key, base_url, llm_model)
        except Exception as e:
            logger.warning("标题生成失败，使用文件名: %s", e)

    return final_text, title


def convert(
    file_path: str | Path,
    language: Optional[str] = None,
    use_cache: bool = True,
    enable_punctuation: bool = True,
    enable_title_generation: bool = True,
    vad_threshold: float = 0.5,
    chunk_duration_s: int = CHUNK_DURATION_S,
    overlap_duration_s: int = OVERLAP_DURATION_S,
    cache_dir: Optional[str | Path] = None,
) -> ConversionResult:
    """
    将音视频文件转换为结构化 Markdown 文本。

    完整流程：
    1. 依赖检查（ffmpeg）
    2. 缓存查询
    3. 音视频分离（视频文件）
    4. VAD 语音检测与分块（失败时降级为固定时长切分）
    5. 逐片 ASR 转录与拼接
    6. 标点恢复（可选）
    7. 标题生成（可选）
    8. 缓存写入
    """
    file_path = Path(file_path)

    validation_error = _validate_input(file_path, language)
    if validation_error:
        return validation_error

    source_type = AsrSourceType.VIDEO if is_video_file(file_path) else AsrSourceType.AUDIO

    cache = ConversionCache(cache_dir=cache_dir) if use_cache else None
    if cache:
        cached = cache.get(file_path)
        if cached is not None:
            return cached

    temp_wav_path: Optional[Path] = None
    audio_path: Path = file_path

    try:
        if is_video_file(file_path):
            temp_wav_path = extract_audio(file_path)
            audio_path = temp_wav_path

        chunks, vad_segments, used_fallback = _get_audio_chunks(
            file_path, audio_path, chunk_duration_s, overlap_duration_s, vad_threshold,
        )

        if not chunks:
            return ConversionResult(
                title="",
                source_type=source_type,
                source_path=str(file_path),
                error="未检测到语音内容",
                success=False,
            )

        engine = ASREngine.get_instance()
        raw_text, detected_lang = engine.transcribe_chunks(
            chunks,
            language=language,
        )

        if not raw_text.strip():
            return ConversionResult(
                title="",
                source_type=source_type,
                source_path=str(file_path),
                error="转录结果为空",
                success=False,
            )

        api_key, base_url, llm_model = _load_llm_config()

        final_text, title = _post_process(
            raw_text, file_path,
            enable_punctuation, enable_title_generation,
            api_key, base_url, llm_model,
        )

        markdown_content = _format_markdown(title, final_text, source_type, detected_lang)

        metadata = {
            "detected_language": detected_lang,
            "vad_segments": [
                {"start_ms": s.start_ms, "end_ms": s.end_ms} for s in vad_segments
            ],
            "chunk_count": len(chunks),
            "chunk_duration_s": chunk_duration_s,
            "overlap_duration_s": overlap_duration_s,
            "transcribed_at": datetime.now().isoformat(),
            "punctuation_restored": enable_punctuation and not has_punctuation(raw_text),
            "title_generated": enable_title_generation and bool(api_key),
            "used_vad_fallback": used_fallback,
        }

        result = ConversionResult(
            title=title,
            markdown_content=markdown_content,
            source_type=source_type,
            source_path=str(file_path),
            metadata=metadata,
        )

        if cache:
            cache.put(file_path, result)

        return result

    except Exception as e:
        logger.error("转换失败: %s", e, exc_info=True)
        return ConversionResult(
            title="",
            source_type=source_type,
            source_path=str(file_path),
            error=f"转换失败: {str(e)}",
            success=False,
        )

    finally:
        if temp_wav_path is not None:
            cleanup_temp_dir(temp_wav_path.parent)


def _format_markdown(
    title: str,
    content: str,
    source_type: AsrSourceType,
    language: str,
) -> str:
    lines = [f"# {title}", ""]
    lines.append(f"> 来源类型: {source_type.value} | 语言: {language}")
    lines.append("")
    lines.append("---")
    lines.append("")

    paragraphs = content.split("\n")
    for para in paragraphs:
        para = para.strip()
        if para:
            lines.append(para)
            lines.append("")

    return "\n".join(lines)
