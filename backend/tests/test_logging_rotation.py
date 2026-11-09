# -*- coding: utf-8 -*-
"""日志轮转配额（2026-11-08）

## 为什么要分层

改造前三份文件共用一套配额：`10MB × 30` ——

- `engramnote.log`（10 MB × 30 = 300 MB）
- `errors.log`（同上）
- `engramnote.json.log`（同上）

合计**上限 900 MB**。而三份的增长速度完全不同：

| 文件 | 实测体量（2 个月） | 该留多少 |
|---|---|---|
| `engramnote.log` | 10 MB / 约 70,229 行 | 7 份足够回溯一周 |
| `errors.log` | 0.94 MB / 11,244 行（95% 是堆栈） | 10 份 |
| `engramnote.json.log` | 10 MB 就轮转了一次 | 30 份（机器排障主通道） |

所以拆成三个独立配额，合计上限降到 **470 MB**，而真正需要长期保留的 JSON
反而留得更多。

## 这一组守 3 件事

1. 默认值就是上表（7 / 30 / 10），且 `LOG_MAX_BYTES` 依旧是三份共用；
2. `setup_logging()` 真的按配额挂 handler（不是只写在配置里）；
3. 老配置只设了 `LOG_BACKUP_COUNT` 时，三份都回落到那个值（不静默变成 7）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest


@pytest.fixture
def restore_root_logger():
    """保存/恢复 root logger 的 handler，避免污染其他用例"""
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    try:
        yield
    finally:
        for handler in root.handlers:
            if handler not in old_handlers:
                handler.close()
        root.handlers = old_handlers
        root.setLevel(old_level)


def _rotating_handlers() -> dict[str, logging.Handler]:
    """把 root 上挂的文件 handler 按文件名索引"""
    out: dict[str, logging.Handler] = {}
    for handler in logging.getLogger().handlers:
        base = getattr(handler, "baseFilename", None)
        if base:
            out[Path(base).name] = handler
    return out


def test_default_quotas_are_layered():
    """默认值：文本 7 / JSON 30 / 错误 10（改造前一律 30）"""
    from app.config import get_settings

    settings = get_settings()
    assert settings.log_text_backup_count == 7
    assert settings.log_json_backup_count == 30
    assert settings.log_error_backup_count == 10


def test_setup_logging_applies_per_file_backup_counts(restore_root_logger, monkeypatch, tmp_path):
    """`setup_logging()` 必须真的把配额挂在对应 handler 上"""
    from app.config import get_settings
    from app.core import logging_config

    settings = get_settings()
    monkeypatch.setattr(settings, "log_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "log_max_bytes", 12345, raising=False)
    monkeypatch.setattr(settings, "log_text_backup_count", 3, raising=False)
    monkeypatch.setattr(settings, "log_json_backup_count", 9, raising=False)
    monkeypatch.setattr(settings, "log_error_backup_count", 5, raising=False)

    logging_config.setup_logging()
    handlers = _rotating_handlers()

    assert handlers["engramnote.log"].backupCount == 3
    assert handlers["engramnote.json.log"].backupCount == 9
    assert handlers["errors.log"].backupCount == 5
    # 单份大小三份共用
    for handler in handlers.values():
        assert handler.maxBytes == 12345


def test_legacy_backup_count_still_applies_to_all(monkeypatch):
    """老配置只写了 `LOG_BACKUP_COUNT`：三份都回落到它，而不是被新默认值盖掉"""
    from app.config import Settings

    settings = Settings(log_backup_count=42)
    assert settings.log_text_backup_count == 42
    assert settings.log_json_backup_count == 42
    assert settings.log_error_backup_count == 42


def test_new_keys_override_legacy(monkeypatch):
    """显式给了新键时，以新键为准（legacy 只当兜底）"""
    from app.config import Settings

    settings = Settings(
        log_backup_count=42,
        log_text_backup_count=2,
        log_json_backup_count=4,
        log_error_backup_count=1,
    )
    assert (settings.log_text_backup_count, settings.log_json_backup_count, settings.log_error_backup_count) == (
        2,
        4,
        1,
    )
