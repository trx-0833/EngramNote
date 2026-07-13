from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .models import ConversionResult

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(os.path.expanduser("~/.cache/asr_converter"))


def _file_fingerprint(file_path: str | Path) -> str:
    file_path = Path(file_path)
    stat_info = file_path.stat()
    header_hash = ""
    file_size = stat_info.st_size
    if file_size > 0:
        read_size = min(1024 * 1024, file_size)
        with open(file_path, "rb") as f:
            header = f.read(read_size)
        header_hash = hashlib.sha256(header).hexdigest()[:32]

    raw = f"{file_path.resolve()}|{stat_info.st_mtime}|{stat_info.st_size}|{header_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class ConversionCache:
    """基于文件系统的转换结果缓存。"""

    def __init__(self, cache_dir: Optional[str | Path] = None):
        if cache_dir is None:
            cache_dir = DEFAULT_CACHE_DIR
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, fingerprint: str) -> Path:
        return self.cache_dir / f"{fingerprint}.json"

    def get(self, file_path: str | Path) -> Optional[ConversionResult]:
        try:
            fingerprint = _file_fingerprint(file_path)
        except (FileNotFoundError, OSError):
            return None

        cache_file = self._cache_path(fingerprint)
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not data.get("success", False):
                return None

            return ConversionResult.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning("缓存文件解析失败 %s: %s", cache_file, e)
            return None

    def put(self, file_path: str | Path, result: ConversionResult) -> None:
        if not result.success:
            return

        fingerprint = _file_fingerprint(file_path)
        cache_file = self._cache_path(fingerprint)

        data = result.to_dict()
        data["cached_at"] = datetime.now().isoformat()
        data["fingerprint"] = fingerprint

        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.cache_dir), suffix=".json.tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                os.unlink(tmp_path)
                raise
            os.replace(tmp_path, str(cache_file))
        except OSError as e:
            logger.warning("缓存写入失败: %s", e)
            return

        try:
            cache_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def invalidate(self, file_path: str | Path) -> bool:
        fingerprint = _file_fingerprint(file_path)
        cache_file = self._cache_path(fingerprint)
        if cache_file.exists():
            cache_file.unlink()
            return True
        return False

    def clear(self) -> int:
        count = 0
        for f in self.cache_dir.glob("*.json"):
            f.unlink()
            count += 1
        return count
