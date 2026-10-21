"""
MinerU 配置加载模块

负责从 YAML 配置文件、环境变量和 .env 文件中加载配置，
合并为最终的配置字典。配置优先级：环境变量 > YAML 文件 > 内置默认值。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

import logging

logger = logging.getLogger(__name__)


DEFAULT_CONFIG: Dict[str, Any] = {
    # ── 4.x：本地解析走 `mineru-kit parse`，档位与后端组合由这几个决定 ──
    # tier: flash / basic / standard / advanced —— 4.x 的"解析档位"，取代 3.x 的 backend
    "tier": "standard",
    # small_backend: auto / onnx / torch（auto 在无 CUDA 的机器上会选 onnx）
    "small_backend": "auto",
    # vlm_engine: auto / llama-cpp / vllm / lmdeploy（auto 会选 llama-cpp）
    "vlm_engine": "auto",
    # MINERU_HOME：4.x 的配置与模型根（config.yaml 与 models/ 都在它下面）
    "mineru_home": "",
    # model_source: 空=auto / huggingface / modelscope / local
    "model_source": "",
    # ocr_mode: auto / txt / ocr
    "ocr_mode": "auto",
    # cli_path: 显式指定 mineru-kit 可执行文件；空则自动探测（见 model_loader）
    "cli_path": "",
    # ── 保留项（与 MinerU 版本无关）──
    "timeout": 300,
    "formula_enable": True,
    "table_enable": True,
    "make_mode": "mm_md",
    "lang": None,
    "output_dir": None,
    "log_level": "INFO",
    "log_dir": "logs",
    "max_workers": 1,
    "chunk_size": 200,
    "chunk_overlap": 10,
    "watermark_patterns": [
        r"©\s*\d{4}",
        "Copyright",
        "版权所有",
    ],
    "max_blank_lines": 2,
    "server_url": None,
}


#: 允许从 `.env` 文件读取的键。
#:
#: ⚠️ 为什么必须显式列出来：pydantic-settings 把 `.env` 读进 **Settings 对象**，
#: 但**不会**把它们写进 `os.environ`。本模块读的是 `os.environ`，
#: 所以"只写进 .env"的键在这里是**看不见**的 —— 必须自己解析一遍。
#:
#: 2026-10-21（4.x 迁移）：由原来只认 API_TOKEN / SERVER_URL 两个键，
#: 扩到全部 MINERU_* 配置键，否则 `MINERU_TIER=basic` 写在 `.env` 里会静默失效。
_ENV_FILE_KEYS = (
    "MINERU_API_TOKEN",
    "MINERU_SERVER_URL",
    "MINERU_BACKEND",
    "MINERU_TIER",
    "MINERU_MODEL_SMALL_BACKEND",
    "MINERU_MODEL_VLM_ENGINE",
    "MINERU_MODEL_SOURCE",
    "MINERU_HOME",
    "MINERU_CLI",
    "MINERU_OCR_MODE",
    "MINERU_TIMEOUT",
    "MINERU_LOG_LEVEL",
    "MINERU_ENV_FILE",
)


def _load_env_config() -> Dict[str, str]:
    """
    从环境变量和 .env 文件中加载 MINERU_* 配置

    优先级：系统环境变量 > .env 文件
    """
    result = {}

    for env_key in _ENV_FILE_KEYS:
        value = os.environ.get(env_key)
        if value:
            result[env_key] = value

    # .env 文件路径：优先使用 MINERU_ENV_FILE 环境变量指定路径，
    # 否则使用 EngramNote 后端目录下的 .env 文件
    # __file__ = backend/app/services/mineru/config_loader.py
    # 向上 4 级 = backend/
    env_path = Path(os.environ.get("MINERU_ENV_FILE", str(Path(__file__).resolve().parent.parent.parent.parent / ".env")))
    logger.debug(f"MinerU .env 文件路径: {env_path}, 存在: {env_path.exists()}")
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k in _ENV_FILE_KEYS and k not in result:
                        result[k] = v

    return result


def load_config(config_path: Optional[str | Path] = None) -> Dict[str, Any]:
    """
    加载配置

    合并顺序：内置默认值 → YAML 配置文件 → 环境变量

    Args:
        config_path: 配置文件路径，None 则使用子包内的 config.yaml

    Returns:
        合并后的配置字典
    """
    config = dict(DEFAULT_CONFIG)

    if config_path is None:
        config_path = Path(__file__).parent / "config.yaml"

    config_path = Path(config_path)

    if not config_path.exists():
        logger.warning(f"配置文件不存在: {config_path}，使用内置默认配置")
    else:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            if raw and "document" in raw:
                doc_config = raw["document"]
                for k, v in doc_config.items():
                    if v is not None:
                        config[k] = v
            logger.debug(f"从 {config_path} 加载配置完成")
        except Exception as e:
            logger.warning(f"加载配置文件失败: {e}，使用内置默认配置")

    # 环境变量覆盖
    #
    # 2026-10-21（4.x 迁移）：`device` ← MINERU_DEVICE_MODE 已删除 ——
    # 4.0 不再有 device 这个概念，小模型后端与 VLM 引擎各自独立选择
    # （见 migration_4：`model.stack` / `MINERU_MODEL_STACK` / `--stack` 已移除）。
    env_overrides = {
        "timeout": ("MINERU_TIMEOUT", int),
        "backend": ("MINERU_BACKEND", str),
        "log_level": ("MINERU_LOG_LEVEL", str),
        "server_url": ("MINERU_SERVER_URL", str),
        "tier": ("MINERU_TIER", str),
        "small_backend": ("MINERU_MODEL_SMALL_BACKEND", str),
        "vlm_engine": ("MINERU_MODEL_VLM_ENGINE", str),
        "mineru_home": ("MINERU_HOME", str),
        "model_source": ("MINERU_MODEL_SOURCE", str),
        "ocr_mode": ("MINERU_OCR_MODE", str),
        "cli_path": ("MINERU_CLI", str),
    }

    for config_key, (env_var, cast_type) in env_overrides.items():
        env_val = os.environ.get(env_var)
        if env_val is not None:
            try:
                if cast_type is int:
                    config[config_key] = int(env_val)
                else:
                    config[config_key] = env_val
            except (ValueError, TypeError):
                logger.warning(f"环境变量 {env_var}={env_val} 类型转换失败，跳过")

    env_config = _load_env_config()
    if "MINERU_API_TOKEN" in env_config:
        config["api_token"] = env_config["MINERU_API_TOKEN"]
    if "MINERU_SERVER_URL" in env_config and not config.get("server_url"):
        config["server_url"] = env_config["MINERU_SERVER_URL"]

    # `.env` 里的其余 MINERU_* 键：只在**系统环境变量没给值**时兜底。
    # 不这样做的话，写在 `.env` 里的 `MINERU_TIER=basic` 会静默失效 ——
    # 因为 pydantic-settings 只把 .env 读进 Settings 对象，不进 os.environ。
    _ENV_FILE_TO_CONFIG = {
        "MINERU_BACKEND": "backend",
        "MINERU_TIER": "tier",
        "MINERU_MODEL_SMALL_BACKEND": "small_backend",
        "MINERU_MODEL_VLM_ENGINE": "vlm_engine",
        "MINERU_MODEL_SOURCE": "model_source",
        "MINERU_HOME": "mineru_home",
        "MINERU_CLI": "cli_path",
        "MINERU_OCR_MODE": "ocr_mode",
        "MINERU_LOG_LEVEL": "log_level",
    }
    for env_key, cfg_key in _ENV_FILE_TO_CONFIG.items():
        if env_key in os.environ:
            continue  # 系统环境变量优先级更高，已在上面处理过
        value = env_config.get(env_key)
        if value:
            config[cfg_key] = value
    if "MINERU_TIMEOUT" in env_config and "MINERU_TIMEOUT" not in os.environ:
        try:
            config["timeout"] = int(env_config["MINERU_TIMEOUT"])
        except (TypeError, ValueError):
            logger.warning("MINERU_TIMEOUT=%s 不是整数，忽略", env_config["MINERU_TIMEOUT"])

    # 2026-10-21：原先这里会把 `MINERU_DEVICE_MODE` 与 `MINERU_MODEL_SOURCE=local`
    # 写回 os.environ。前者 4.x 已无此概念；后者是 3.x 的"强制用本地缓存模型"开关，
    # 4.x 的对应物是 `MINERU_MODEL_SOURCE`（auto/huggingface/modelscope/local），
    # 由调用方按需显式设置（见 model_loader.mineru_env），此处不再偷偷改全局环境。
    return config
