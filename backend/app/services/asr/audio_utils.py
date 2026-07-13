from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf
import torch

from .models import (
    CHUNK_DURATION_S,
    OVERLAP_DURATION_S,
    SAMPLE_RATE,
    SUPPORTED_AUDIO_EXTS,
    SUPPORTED_VIDEO_EXTS,
    VADSegment,
)

logger = logging.getLogger(__name__)

# --- VAD model cache ---
_vad_model = None
_vad_utils = None
_vad_lock = __import__("threading").Lock()


def _get_vad_model():
    global _vad_model, _vad_utils
    if _vad_model is None:
        with _vad_lock:
            if _vad_model is None:
                logger.info("正在加载 Silero VAD 模型...")
                model = _load_vad_model_with_fallback()
                _vad_model = model
                _vad_utils = (_get_speech_timestamps, None, None, None, None)
                logger.info("Silero VAD 模型加载完成")
    return _vad_model, _vad_utils


def _load_vad_model_with_fallback():
    """带回退策略的 VAD 模型加载，优先本地，其次 ModelScope，最后 GitHub"""
    # 策略 1: 从项目本地目录加载 (data/models/silero-vad/)
    jit_path = _resolve_vad_model_path()
    if jit_path and jit_path.exists():
        try:
            model = torch.jit.load(str(jit_path), map_location="cpu")
            model.eval()
            logger.info(f"Silero VAD 从本地加载: {jit_path}")
            return model
        except Exception as e:
            logger.warning(f"本地加载失败: {e}")

    # 策略 2: 从 torch.hub 缓存加载（兼容之前通过 GitHub 下载的缓存）
    hub_jit = Path(torch.hub.get_dir()) / "snakers4_silero-vad_master" / "src" / "silero_vad" / "data" / "silero_vad.jit"
    if hub_jit.exists():
        try:
            model = torch.jit.load(str(hub_jit), map_location="cpu")
            model.eval()
            logger.info(f"Silero VAD 从 torch.hub 缓存加载: {hub_jit}")
            _save_vad_to_local(hub_jit)
            return model
        except Exception as e:
            logger.warning(f"缓存加载失败: {e}")

    # 策略 3: 从 ModelScope 下载
    try:
        ms_jit_path = _download_vad_from_modelscope()
        if ms_jit_path and ms_jit_path.exists():
            # 如果下载的是 onnx 文件，需要走 onnx 加载路径
            if ms_jit_path.suffix == ".onnx":
                model = _load_onnx_vad_model(ms_jit_path)
                return model
            model = torch.jit.load(str(ms_jit_path), map_location="cpu")
            model.eval()
            logger.info(f"Silero VAD 从 ModelScope 下载并加载: {ms_jit_path}")
            return model
    except Exception as e:
        logger.warning(f"ModelScope 下载失败: {e}")

    # 策略 4: 回退到 torch.hub.load（GitHub 源，内地网络可能失败）
    logger.warning("所有前置策略失败，尝试从 GitHub 下载（可能因网络问题失败）")
    try:
        hub_model, hub_utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
        # 将 GitHub 下载的模型保存到本地
        hub_dir = Path(torch.hub.get_dir()) / "snakers4_silero-vad_master" / "src" / "silero_vad" / "data" / "silero_vad.jit"
        if hub_dir.exists():
            _save_vad_to_local(hub_dir)
        # 覆盖 _vad_utils 为 torch.hub 返回的原始 utils
        global _vad_utils
        _vad_utils = hub_utils
        return hub_model
    except Exception as e:
        logger.error(
            f"Silero VAD 模型下载失败: {e}\n"
            "请手动下载 silero_vad.jit 文件到 data/models/silero-vad/ 目录。\n"
            "下载方式：在有网络的机器上运行以下 Python 代码：\n"
            "  import torch\n"
            '  model, _ = torch.hub.load("snakers4/silero-vad", "silero_vad", trust_repo=True)\n'
            '  torch.jit.save(model, "silero_vad.jit")\n'
            "然后将 silero_vad.jit 复制到 data/models/silero-vad/ 目录下。"
        )
        raise


def _resolve_vad_model_path() -> Optional[Path]:
    """解析 VAD 模型本地路径"""
    try:
        from ...config import get_settings
        settings = get_settings()
        if getattr(settings, "vad_model_dir", ""):
            return Path(settings.vad_model_dir) / "silero_vad.jit"
        base = Path(settings.data_dir) if getattr(settings, "data_dir", "") else Path(__file__).parent.parent.parent.parent / "data"
    except Exception:
        base = Path(__file__).parent.parent.parent.parent / "data"
    return base / "models" / "silero-vad" / "silero_vad.jit"


def _save_vad_to_local(src_path: Path):
    """将 VAD 模型文件复制到本地目录"""
    try:
        target = _resolve_vad_model_path()
        if target and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            import shutil as _shutil
            _shutil.copy2(src_path, target)
            logger.info(f"VAD 模型已保存到本地: {target}")
    except Exception as e:
        logger.warning(f"保存 VAD 模型到本地失败: {e}")


def _download_vad_from_modelscope() -> Optional[Path]:
    """从 ModelScope 下载 Silero VAD 模型文件"""
    from modelscope.hub.file_download import model_file_download

    target_path = _resolve_vad_model_path()
    if not target_path:
        return None
    target_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("正在从 ModelScope 下载 Silero VAD 模型...")

    # 尝试下载 JIT 文件（根路径）
    for file_path in ["silero_vad.jit", "src/silero_vad/data/silero_vad.jit"]:
        try:
            downloaded = model_file_download(
                model_id="manyeyes/silero-vad-onnx",
                file_path=file_path,
                cache_dir=str(target_path.parent),
            )
            if downloaded and Path(downloaded).exists():
                if not target_path.exists():
                    import shutil as _shutil
                    _shutil.copy2(downloaded, target_path)
                return target_path
        except Exception:
            pass

    # JIT 文件不存在，尝试下载 ONNX 文件
    try:
        onnx_path = model_file_download(
            model_id="manyeyes/silero-vad-onnx",
            file_path="silero_vad.onnx",
            cache_dir=str(target_path.parent),
        )
        if onnx_path and Path(onnx_path).exists():
            onnx_target = target_path.parent / "silero_vad.onnx"
            if not onnx_target.exists():
                import shutil as _shutil
                _shutil.copy2(onnx_path, onnx_target)
            logger.info("ModelScope 仅有 ONNX 格式，将尝试使用 ONNX Runtime 加载")
            return onnx_target
    except Exception as e:
        logger.warning(f"ONNX 下载也失败: {e}")

    raise FileNotFoundError("无法从 ModelScope 获取 Silero VAD 模型文件")


def _load_onnx_vad_model(onnx_path: Path):
    """使用 ONNX Runtime 加载 VAD 模型"""
    try:
        import onnxruntime as ort
    except ImportError:
        raise FileNotFoundError(
            "onnxruntime 未安装，无法加载 ONNX 格式的 VAD 模型。"
            "请安装: pip install onnxruntime，或手动下载 silero_vad.jit 到 data/models/silero-vad/"
        )

    class OnnxVadWrapper(torch.nn.Module):
        """包装 ONNX 模型使其兼容 Silero VAD 的 JIT 接口"""
        def __init__(self, onnx_path):
            super().__init__()
            self.session = ort.InferenceSession(str(onnx_path))
            self.input_name = self.session.get_inputs()[0].name
            self.sr_name = self.session.get_inputs()[1].name if len(self.session.get_inputs()) > 1 else None
            self._h = torch.zeros(2, 1, 64) if len(self.session.get_inputs()) > 2 else None
            self._c = torch.zeros(2, 1, 64) if len(self.session.get_inputs()) > 3 else None

        def forward(self, x, sr):
            inputs = {self.input_name: x.numpy()}
            if self.sr_name:
                inputs[self.sr_name] = torch.tensor([sr]).numpy()
            if self._h is not None:
                inputs[self.session.get_inputs()[2].name] = self._h.numpy()
            inputs[self.session.get_inputs()[3].name if self._c is not None else self.sr_name] = self._c.numpy() if self._c is not None else torch.tensor([sr]).numpy()
            out = self.session.run(None, inputs)
            if len(out) > 1:
                self._h = torch.from_numpy(out[1])
                self._c = torch.from_numpy(out[2])
            return torch.from_numpy(out[0])

        def reset_states(self):
            if self._h is not None:
                self._h.zero_()
            if self._c is not None:
                self._c.zero_()

        def __call__(self, x, sr):
            return self.forward(x, sr)

    logger.info(f"Silero VAD 从 ONNX 加载: {onnx_path}")
    return OnnxVadWrapper(onnx_path)


def _get_speech_timestamps(
    audio: torch.Tensor,
    model,
    sampling_rate: int = 16000,
    min_speech_duration_ms: int = 250,
    min_silence_duration_ms: int = 100,
    threshold: float = 0.5,
    window_size_samples: int = 512,
    speech_pad_ms: int = 30,
    return_seconds: bool = False,
    visualize_probs: bool = False,
):
    """内嵌的 get_speech_timestamps 实现，从 Silero VAD 源码提取

    避免依赖 torch.hub.load 从 GitHub 下载整个 repo。
    原始来源: https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py
    """
    # 与原始实现一致：确保 audio 为 1D 张量
    if not torch.is_tensor(audio):
        try:
            audio = torch.Tensor(audio)
        except Exception:
            raise TypeError("Audio cannot be casted to tensor")
    if len(audio.shape) > 1:
        for _ in range(len(audio.shape)):
            audio = audio.squeeze(0)
        if len(audio.shape) > 1:
            raise ValueError("More than one dimension in audio. Are you trying to process audio with 2 channels?")

    # 支持采样率为 16000 倍数时自动降采样
    if sampling_rate > 16000 and (sampling_rate % 16000 == 0):
        step = sampling_rate // 16000
        sampling_rate = 16000
        audio = audio[::step]

    if sampling_rate not in (16000, 8000):
        raise ValueError("sampling_rate 必须是 8000 或 16000（或其倍数）")

    # 根据采样率设置窗口大小
    window_size_samples = 512 if sampling_rate == 16000 else 256

    model.reset_states()
    audio_length_samples = len(audio)

    speech_probs = []
    for current_start in range(0, audio_length_samples, window_size_samples):
        chunk = audio[current_start:current_start + window_size_samples]
        if len(chunk) < window_size_samples:
            chunk = torch.nn.functional.pad(chunk, (0, window_size_samples - len(chunk)))
        speech_prob = model(chunk, sampling_rate).item()
        speech_probs.append(speech_prob)

    min_speech_samples = sampling_rate * min_speech_duration_ms / 1000
    min_silence_samples = sampling_rate * min_silence_duration_ms / 1000
    speech_pad_samples = sampling_rate * speech_pad_ms / 1000
    neg_threshold = max(threshold - 0.15, 0.01)

    triggered = False
    speeches = []
    current_speech = {}
    temp_end = 0
    for i, prob in enumerate(speech_probs):
        cur_sample = window_size_samples * i

        # 如果语音在 temp_end 后恢复，记录可能的静音段
        if (prob >= threshold) and temp_end:
            temp_end = 0

        # 语音开始
        if (prob >= threshold) and not triggered:
            triggered = True
            current_speech["start"] = cur_sample
            continue

        # 语音结束
        if triggered and (prob < neg_threshold):
            if not temp_end:
                temp_end = cur_sample
            if (cur_sample - temp_end) > min_silence_samples:
                current_speech["end"] = temp_end
                if (current_speech["end"] - current_speech["start"]) > min_speech_samples:
                    speeches.append(current_speech)
                current_speech = {}
                temp_end = 0
                triggered = False
                model.reset_states()
            continue

    if current_speech and triggered:
        current_speech["end"] = audio_length_samples
        speeches.append(current_speech)

    if speech_pad_ms > 0:
        for speech in speeches:
            speech["start"] = max(0, int(speech["start"] - speech_pad_samples))
            speech["end"] = min(audio_length_samples, int(speech["end"] + speech_pad_samples))

    if return_seconds:
        for speech in speeches:
            speech["start"] = round(speech["start"] / sampling_rate, 1)
            speech["end"] = round(speech["end"] / sampling_rate, 1)

    return speeches


def check_ffmpeg() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def extract_audio(video_path: str | Path, output_wav_path: Optional[str | Path] = None) -> Path:
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    if output_wav_path is None:
        tmp_dir = tempfile.mkdtemp(prefix="asr_")
        output_wav_path = Path(tmp_dir) / f"{video_path.stem}.wav"
    else:
        output_wav_path = Path(output_wav_path)
        output_wav_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", "1",
        "-y", str(output_wav_path),
        "-loglevel", "error",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (FileNotFoundError, OSError) as e:
        raise RuntimeError(
            f"ffmpeg 不可用（{e}），请确保 ffmpeg 已安装且在 PATH 中。"
            "Windows 可从 https://www.gyan.dev/ffmpeg/builds/ 下载，"
            "或通过 conda install ffmpeg 安装。"
        ) from e
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 音频提取失败: {result.stderr}")

    if not output_wav_path.exists() or output_wav_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg 输出文件为空或不存在: {output_wav_path}")

    return output_wav_path


def is_video_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_VIDEO_EXTS


def is_audio_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_AUDIO_EXTS


def is_supported_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in (SUPPORTED_AUDIO_EXTS | SUPPORTED_VIDEO_EXTS)


def load_audio_as_wav(audio_path: str | Path, target_sr: int = SAMPLE_RATE) -> Tuple[np.ndarray, int]:
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    data, sr = sf.read(str(audio_path), dtype="float32")

    if data.ndim > 1:
        data = data.mean(axis=1)

    if sr != target_sr:
        import librosa
        data = librosa.resample(data, orig_sr=sr, target_sr=target_sr)
        sr = target_sr

    return data.astype(np.float32), sr


def detect_speech_segments(
    audio: np.ndarray,
    sr: int = SAMPLE_RATE,
    min_speech_duration_ms: int = 250,
    min_silence_duration_ms: int = 100,
    threshold: float = 0.5,
) -> List[VADSegment]:
    model, utils = _get_vad_model()
    (get_speech_timestamps, _, _, _, _) = utils

    audio_tensor = torch.from_numpy(audio).unsqueeze(0)

    speech_ts = get_speech_timestamps(
        audio_tensor,
        model,
        sampling_rate=sr,
        min_speech_duration_ms=min_speech_duration_ms,
        min_silence_duration_ms=min_silence_duration_ms,
        threshold=threshold,
    )

    segments = []
    for ts in speech_ts:
        segments.append(VADSegment(
            start_ms=int(ts["start"] / sr * 1000),
            end_ms=int(ts["end"] / sr * 1000),
        ))

    return segments


def merge_segments_into_chunks(
    segments: List[VADSegment],
    chunk_duration_s: int = CHUNK_DURATION_S,
    overlap_duration_s: int = OVERLAP_DURATION_S,
) -> List[VADSegment]:
    if not segments:
        return []

    merged = []
    current_start = segments[0].start_ms
    current_end = segments[0].end_ms

    for seg in segments[1:]:
        if seg.start_ms - current_start < chunk_duration_s * 1000:
            current_end = max(current_end, seg.end_ms)
        else:
            merged.append(VADSegment(start_ms=current_start, end_ms=current_end))
            overlap_start = max(current_start, current_end - overlap_duration_s * 1000)
            current_start = overlap_start
            current_end = seg.end_ms

    merged.append(VADSegment(start_ms=current_start, end_ms=current_end))

    return merged


def split_audio_by_chunks(
    audio: np.ndarray,
    sr: int = SAMPLE_RATE,
    chunk_duration_s: int = CHUNK_DURATION_S,
    overlap_duration_s: int = OVERLAP_DURATION_S,
) -> List[Tuple[np.ndarray, float, float]]:
    total_samples = len(audio)
    chunk_samples = chunk_duration_s * sr
    overlap_samples = overlap_duration_s * sr
    step = chunk_samples - overlap_samples

    if total_samples <= chunk_samples:
        return [(audio, 0.0, total_samples / sr)]

    chunks = []
    start = 0
    while start < total_samples:
        end = min(start + chunk_samples, total_samples)
        chunk = audio[start:end]
        chunks.append((chunk, start / sr, end / sr))
        if end >= total_samples:
            break
        start += step

    return chunks


def vad_split_audio(
    audio_path: str | Path,
    sr: int = SAMPLE_RATE,
    chunk_duration_s: int = CHUNK_DURATION_S,
    overlap_duration_s: int = OVERLAP_DURATION_S,
    vad_threshold: float = 0.5,
) -> Tuple[List[Tuple[np.ndarray, float, float]], List[VADSegment]]:
    audio, audio_sr = load_audio_as_wav(audio_path, target_sr=sr)
    vad_segments = detect_speech_segments(audio, sr=sr, threshold=vad_threshold)

    if not vad_segments:
        return [], []

    chunks_info = merge_segments_into_chunks(
        vad_segments,
        chunk_duration_s=chunk_duration_s,
        overlap_duration_s=overlap_duration_s,
    )

    chunks = []
    for seg in chunks_info:
        start_sample = int(seg.start_s * sr)
        end_sample = int(seg.end_s * sr)
        start_sample = max(0, start_sample)
        end_sample = min(len(audio), end_sample)
        if start_sample >= end_sample:
            continue  # 跳过空片段
        chunk_audio = audio[start_sample:end_sample]
        chunks.append((chunk_audio, seg.start_s, seg.end_s))

    return chunks, vad_segments


def save_temp_wav(audio: np.ndarray, sr: int = SAMPLE_RATE, prefix: str = "chunk") -> Path:
    tmp_dir = tempfile.mkdtemp(prefix="asr_chunk_")
    tmp_path = Path(tmp_dir) / f"{prefix}.wav"
    sf.write(str(tmp_path), audio, sr)
    return tmp_path


def cleanup_temp_dir(path: Path) -> None:
    """递归清理临时目录及其内容。"""
    try:
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()
    except OSError:
        pass
