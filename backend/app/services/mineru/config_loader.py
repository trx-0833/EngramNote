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
    "backend": "pipeline",
    "device": "cpu",
    "timeout": 300,
    "formula_enable": True,
    "table_enable": True,
    "make_mode": "mm_md",
    "lang": None,
    "output_dir": None,
    "log_level": "INFO",
    "log_dir": "logs",
    "max_workers": 1,
    "modelscope_model_dir": str(Path.home() / ".cache" / "modelscope" / "hub" / "models" / "OpenDataLab"),
    "chunk_size": 200,
    "chunk_overlap": 10,
    "watermark_patterns": [
        r"©\s*\d{4}",
        "Copyright",
        "版权所有",
    ],
    "max_blank_lines": 2,
    "server_url": None,
    "model_version": None,
}


def _load_env_config() -> Dict[str, str]:
    """
    从环境变量和 .env 文件中加载 MINERU_API_TOKEN 和 MINERU_SERVER_URL

    优先级：系统环境变量 > .env 文件
    """
    result = {}

    for env_key in ["MINERU_API_TOKEN", "MINERU_SERVER_URL"]:
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
                    if k in ("MINERU_API_TOKEN", "MINERU_SERVER_URL") and k not in result:
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
    env_overrides = {
        "device": ("MINERU_DEVICE_MODE", str),
        "timeout": ("MINERU_TIMEOUT", int),
        "backend": ("MINERU_BACKEND", str),
        "log_level": ("MINERU_LOG_LEVEL", str),
        "server_url": ("MINERU_SERVER_URL", str),
    }

    for config_key, (env_var, cast_type) in env_overrides.items():
        env_val = os.environ.get(env_var)
        if env_val is not None:
            try:
                if cast_type == int:
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

    os.environ["MINERU_DEVICE_MODE"] = config.get("device", "cpu")

    modelscope_dir = config.get("modelscope_model_dir", "")
    if modelscope_dir:
        os.environ["MINERU_MODEL_SOURCE"] = "local"

    return config
