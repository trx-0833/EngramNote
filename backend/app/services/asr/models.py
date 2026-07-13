from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class AsrSourceType(enum.Enum):
    """ASR 来源类型（与 EngramNote 的 SourceType 区分）."""
    VIDEO = "video"
    AUDIO = "audio"


@dataclass
class ConversionResult:
    title: str = ""
    markdown_content: str = ""
    source_type: AsrSourceType = AsrSourceType.AUDIO
    source_path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    success: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "markdown_content": self.markdown_content,
            "source_type": self.source_type.value,
            "source_path": self.source_path,
            "metadata": self.metadata,
            "error": self.error,
            "success": self.success,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversionResult":
        return cls(
            title=data.get("title", ""),
            markdown_content=data.get("markdown_content", ""),
            source_type=AsrSourceType(data.get("source_type", "audio")),
            source_path=data.get("source_path", ""),
            metadata=data.get("metadata", {}),
            error=data.get("error"),
            success=data.get("success", True),
        )


@dataclass
class VADSegment:
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def start_s(self) -> float:
        return self.start_ms / 1000.0

    @property
    def end_s(self) -> float:
        return self.end_ms / 1000.0


SUPPORTED_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"}
SUPPORTED_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".rmvb"}
SUPPORTED_EXTS = SUPPORTED_AUDIO_EXTS | SUPPORTED_VIDEO_EXTS

CHUNK_DURATION_S = 180
OVERLAP_DURATION_S = 15
SAMPLE_RATE = 16000

SUPPORTED_LANGUAGES = [
    "Chinese", "English", "Cantonese", "Arabic", "German", "French",
    "Spanish", "Portuguese", "Indonesian", "Italian", "Korean", "Russian",
    "Thai", "Vietnamese", "Japanese", "Turkish", "Hindi", "Malay",
    "Dutch", "Swedish", "Danish", "Finnish", "Polish", "Czech",
    "Filipino", "Persian", "Greek", "Romanian", "Hungarian", "Macedonian",
]
