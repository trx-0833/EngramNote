"""
ModelScope 模型加载模块

负责配置本地 ModelScope 模型路径，使 MinerU pipeline 后端
能够正确找到本地缓存的模型文件。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any

import logging

logger = logging.getLogger(__name__)

MODELSCOPE_PIPELINE_MODEL = "PDF-Extract-Kit-1.0"
MODELSCOPE_VLM_MODEL = "MinerU2.5-Pro-2604-1.2B"


def _find_model_dir(modelscope_path: Path, model_name: str) -> str:
    """
    在 ModelScope 缓存目录中查找模型路径

    支持三种匹配策略：精确编码名、原始名、模糊匹配。

    Args:
        modelscope_path: ModelScope 缓存根目录
        model_name: 模型名称

    Returns:
        模型目录的绝对路径，未找到返回空字符串
    """
    if not modelscope_path.exists():
        return ""

    # modelscope cache naming: '.' -> '___', other chars preserved
    encoded_name = model_name.replace(".", "___")
    direct_path = modelscope_path / encoded_name
    if direct_path.exists() and direct_path.is_dir():
        return str(direct_path)

    original_path = modelscope_path / model_name
    if original_path.exists() and original_path.is_dir():
        return str(original_path)

    # fallback: scan directory for partial match
    for item in modelscope_path.iterdir():
        if item.is_dir() and model_name.replace(".", "___") in item.name:
            return str(item)
        if item.is_dir() and model_name.split("-")[0] in item.name:
            return str(item)

    return ""


def configure_modelscope_models(config: Dict[str, Any]) -> bool:
    """
    配置 ModelScope 模型路径

    根据配置中的 modelscope_model_dir 查找 Pipeline 和 VLM 模型，
    设置 MINERU_MODEL_SOURCE 环境变量为 "local"。

    Args:
        config: 配置字典，需包含 modelscope_model_dir

    Returns:
        配置是否成功
    """
    modelscope_dir = config.get("modelscope_model_dir", "")
    if not modelscope_dir:
        logger.error("modelscope_model_dir 未配置")
        return False

    modelscope_path = Path(modelscope_dir)
    if not modelscope_path.exists():
        logger.error(f"modelscope 模型目录不存在: {modelscope_dir}")
        logger.error("请先通过 modelscope 下载 MinerU 模型到本地缓存路径")
        return False

    resolved_pipeline = _find_model_dir(modelscope_path, MODELSCOPE_PIPELINE_MODEL)
    resolved_vlm = _find_model_dir(modelscope_path, MODELSCOPE_VLM_MODEL)

    if not resolved_pipeline:
        logger.error(f"Pipeline模型未找到，搜索目录: {modelscope_path}")
        available = [str(p.name) for p in modelscope_path.iterdir() if p.is_dir()]
        logger.error(f"可用的模型目录: {available}")
        return False

    if not resolved_vlm:
        logger.error(f"VLM模型未找到，搜索目录: {modelscope_path}")
        available = [str(p.name) for p in modelscope_path.iterdir() if p.is_dir()]
        logger.error(f"可用的模型目录: {available}")
        return False

    os.environ["MINERU_MODEL_SOURCE"] = "local"

    logger.info(f"Pipeline模型路径: {resolved_pipeline}")
    logger.info(f"VLM模型路径: {resolved_vlm}")

    return True


def verify_modelscope_models(config: Dict[str, Any]) -> Dict[str, str]:
    """
    验证 ModelScope 模型是否可用

    Args:
        config: 配置字典

    Returns:
        包含 pipeline/vlm 路径和状态的字典
    """
    modelscope_dir = config.get("modelscope_model_dir", "")
    result = {"pipeline": "", "vlm": "", "status": "ok"}

    modelscope_path = Path(modelscope_dir) if modelscope_dir else Path()
    if not modelscope_path.exists():
        result["status"] = "modelscope目录不存在"
        return result

    pipeline_path = _find_model_dir(modelscope_path, MODELSCOPE_PIPELINE_MODEL)
    vlm_path = _find_model_dir(modelscope_path, MODELSCOPE_VLM_MODEL)

    if not pipeline_path:
        result["status"] = f"Pipeline模型缺失"
        available = [str(p.name) for p in modelscope_path.iterdir() if p.is_dir()]
        result["status"] += f"，可用的: {available}"
        return result

    if not vlm_path:
        result["status"] = f"VLM模型缺失"
        available = [str(p.name) for p in modelscope_path.iterdir() if p.is_dir()]
        result["status"] += f"，可用的: {available}"
        return result

    result["pipeline"] = pipeline_path
    result["vlm"] = vlm_path
    return result
