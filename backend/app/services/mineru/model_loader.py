"""
ModelScope 模型加载模块

负责配置本地 ModelScope 模型路径，使 MinerU pipeline 后端
能够正确找到本地缓存的模型文件。

## 2026-10-21：三处修正（依据 docs/mineru-backend-decision.md）

1. **假就绪**：原实现只要**目录存在**就返回 True。实测出现
   `configure_modelscope_models()` 返回 True、紧接着 `do_parse` 就以
   `ModuleNotFoundError: No module named 'torchvision'` 失败 ——
   "模型找得到"与"后端跑得起来"是两件事，这里补上运行期能力探测。
2. **强制两份模型**：原实现要求 pipeline 与 VLM 模型**同时**存在，否则返回 False。
   而 `converter.py` 只在 `backend == "pipeline"` 时调用本模块 ——
   只用一种后端的人被迫多下 2.2GB。现按 `backend` 只校验真正需要的那一份。
3. **模糊匹配会挑错版本**：原 fallback 用 `model_name.split("-")[0]` 做子串匹配，
   对 `MinerU2.5-Pro-2604-1.2B` 得到 `"MinerU2.5"`，于是**旧的
   `MinerU2.5-2509-1.2B` 也会命中**，可能静默加载到旧版权重。现要求更具体的匹配，
   并在走 fallback 时把**实际选中的目录名**打进日志。
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Dict, Any

import logging

logger = logging.getLogger(__name__)

MODELSCOPE_PIPELINE_MODEL = "PDF-Extract-Kit-1.0"
MODELSCOPE_VLM_MODEL = "MinerU2.5-Pro-2604-1.2B"

#: 本地 pipeline 后端的**运行期**依赖（来源：mineru 3.4.5 的 `[pipeline]` extra）。
#: 只探这些"缺了就在 import 阶段炸"的包；`torch` / `transformers` /
#: `safetensors` / `onnxruntime` / `PyYAML` 由主依赖保证，不重复探。
PIPELINE_RUNTIME_MODULES = ("torchvision", "ftfy", "shapely", "pyclipper")


def pipeline_runtime_available() -> tuple[bool, str]:
    """
    探测本地 pipeline 后端的运行期依赖是否真的可导入。

    为什么不能只看模型目录：模型齐了但 `torchvision` 没装时，
    `do_parse(backend="pipeline")` 会在 `mineru/model/layout/pp_doclayoutv2.py`
    的模块层 import 处抛 `ModuleNotFoundError` —— 报错点在 mineru 内部，
    与"你少装了一个包"看起来毫无关系。

    Returns:
        (是否可用, 不可用原因)；可用时原因为空串
    """
    missing: list[str] = []
    for name in PIPELINE_RUNTIME_MODULES:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - 任何导入失败都算缺
            missing.append(name)
    if missing:
        return False, "缺少 Python 依赖: " + ", ".join(missing)
    return True, ""


def _required_model_names(backend: str) -> list[tuple[str, str]]:
    """
    给定后端需要哪几份模型。

    Args:
        backend: MinerU 后端名

    Returns:
        [(模型名, 人类可读标签), ...]；云端后端返回空列表（不需要本地模型）
    """
    if backend == "pipeline":
        return [(MODELSCOPE_PIPELINE_MODEL, "Pipeline")]
    if backend.startswith("vlm-") or backend.startswith("hybrid-"):
        # 目前 converter 对这两个后端一律走远程 API（`_process_with_remote_api`），
        # 因此**不需要**本地权重；将来接本地 VLM 时把这里改成 VLM 模型即可。
        return []
    return []


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

    # fallback: 扫目录做部分匹配。
    #
    # ⚠️ 这里刻意**不再**用 `model_name.split("-")[0]`：
    # 对 `MinerU2.5-Pro-2604-1.2B` 它只得到 `"MinerU2.5"`，于是
    # `MinerU2.5-2509-1.2B`（旧版）同样命中 —— 可能静默加载到旧权重。
    # 改成"编码名或原始名的前缀"匹配，并把**实际选中的目录**打进日志，
    # 让"匹配到了但匹配错了"至少留下痕迹。
    for candidate_key in (encoded_name, model_name):
        for item in sorted(modelscope_path.iterdir()):
            if not item.is_dir():
                continue
            if item.name == candidate_key:
                return str(item)
            if item.name.startswith(candidate_key):
                logger.warning(
                    "模型 %s 未精确命中，模糊匹配到 %s（请确认版本是否正确）",
                    model_name,
                    item.name,
                )
                return str(item)

    return ""


def configure_modelscope_models(config: Dict[str, Any], backend: str = "pipeline") -> bool:
    """
    配置 ModelScope 模型路径

    根据配置中的 modelscope_model_dir 查找**当前后端需要的**模型，
    设置 MINERU_MODEL_SOURCE 环境变量为 "local"。

    Args:
        config: 配置字典，需包含 modelscope_model_dir
        backend: 当前解析后端；只有 `pipeline` 需要本地模型

    Returns:
        配置是否成功。**失败不等于后端不可用** —— 云端后端本就不需要本地模型，
        因此调用方应先判断 backend（见 converter.py 的用法）。
    """
    required = _required_model_names(backend)
    if not required:
        logger.debug("后端 %s 不需要本地 ModelScope 模型，跳过配置", backend)
        return True

    modelscope_dir = config.get("modelscope_model_dir", "")
    if not modelscope_dir:
        logger.error("modelscope_model_dir 未配置")
        return False

    modelscope_path = Path(modelscope_dir)
    if not modelscope_path.exists():
        logger.error(f"modelscope 模型目录不存在: {modelscope_dir}")
        logger.error("请先通过 modelscope 下载 MinerU 模型到本地缓存路径")
        return False

    # 运行期依赖探测：模型齐了但缺包时，早一点、以可读的方式说出来
    runtime_ok, runtime_reason = pipeline_runtime_available()
    if not runtime_ok:
        logger.error(
            "本地 pipeline 后端不可用：%s。"
            "补齐：pip install -r requirements-pdf-local.txt；"
            "或改用云端：MINERU_BACKEND=vlm-http-client（见 docs/mineru-backend-decision.md）",
            runtime_reason,
        )
        return False

    resolved: Dict[str, str] = {}
    for model_name, label in required:
        path = _find_model_dir(modelscope_path, model_name)
        if not path:
            logger.error(f"{label} 模型未找到，搜索目录: {modelscope_path}")
            available = sorted(p.name for p in modelscope_path.iterdir() if p.is_dir())
            logger.error(f"可用的模型目录: {available}")
            return False
        resolved[label] = path
        logger.info(f"{label} 模型路径: {path}")

    os.environ["MINERU_MODEL_SOURCE"] = "local"
    return True


def verify_modelscope_models(config: Dict[str, Any], backend: str = "pipeline") -> Dict[str, str]:
    """
    验证 ModelScope 模型是否可用

    Args:
        config: 配置字典
        backend: 当前解析后端；决定需要哪几份模型

    Returns:
        包含 pipeline/vlm 路径和状态的字典
    """
    modelscope_dir = config.get("modelscope_model_dir", "")
    result: Dict[str, str] = {"pipeline": "", "vlm": "", "status": "ok"}

    modelscope_path = Path(modelscope_dir) if modelscope_dir else Path()
    if not modelscope_path.exists():
        result["status"] = "modelscope目录不存在"
        return result

    pipeline_path = _find_model_dir(modelscope_path, MODELSCOPE_PIPELINE_MODEL)
    vlm_path = _find_model_dir(modelscope_path, MODELSCOPE_VLM_MODEL)
    result["pipeline"] = pipeline_path
    result["vlm"] = vlm_path

    required = _required_model_names(backend)
    for _model_name, label in required:
        key = "pipeline" if label == "Pipeline" else "vlm"
        if not result.get(key):
            available = sorted(p.name for p in modelscope_path.iterdir() if p.is_dir())
            result["status"] = f"{label}模型缺失，可用的: {available}"
            return result

    if backend == "pipeline":
        runtime_ok, runtime_reason = pipeline_runtime_available()
        if not runtime_ok:
            result["status"] = f"运行期依赖不满足（{runtime_reason}）"
            return result

    return result
