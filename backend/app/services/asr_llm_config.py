"""ASR 的 LLM 配置读取（不依赖音频/模型运行时）

## 为什么单独一个模块

`app/services/asr/converter.py` 在模块顶层 import `torch`、`qwen_asr`、
`soundfile` —— 这些是 GB 级可选依赖，测试子集刻意不安装。但
`_load_llm_config()` 本身只读 `config.py`，与音频运行时无关；把它留在
converter 里会让 `test_asr_llm_config.py` 在**收集阶段**就 ImportError。

把这段纯配置逻辑放在包外，测试可以独立导入；converter 继续复用同一份实现，
生产行为不变。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _load_llm_config() -> tuple[Optional[str], str, str]:
    """
    从 EngramNote 配置获取 LLM API 配置（供标点恢复与标题生成用）。

    使用 EngramNote 的 config.py 统一配置体系，**不自行解析 .env 文件**。

    ## 拿不到配置时返回 `(None, "", "")`，不再"回退到环境变量"

    2026-10-19 修：原来这里有一个 `except` 分支读
    `os.environ.get("DEEPSEEK_API_KEY")` 等三个变量，但那是**永远拿不到值**的 ——
    pydantic-settings 读 `.env` 是读进 `Settings` 对象，**不会导出到进程环境**
    （实测：`os.environ` 里没有这三个名字）。所以那个分支的实际效果是
    "key 为 None + base_url 落到官方 `https://api.deepseek.com`"，
    而它还在日志里说"回退到环境变量"—— 一句无法兑现的话。

    ⚠️ 现在**刻意不提供任何替代值**：给一个"官方 base_url + 空 key"的组合，
    将来某天有人补上 key 的来源，就会变成**用 OpenCode 的密钥打官方端点** ——
    这正是本项目 `backend/.env` 里那两块共用 `DEEPSEEK_API_KEY` 变量名
    最容易踩的坑（认准 `BASE_URL`，不要看变量名）。
    宁可不给，也不给一个可能配错的组合。

    Returns:
        (api_key, base_url, model)。`api_key` 为 `None` 表示"本次没有可用的
        LLM"，调用方应跳过依赖 LLM 的步骤（标点恢复 / 标题生成），
        转写本身不受影响。
    """
    try:
        from ..config import get_settings

        settings = get_settings()
        llm_config = settings.get_llm_config()
        return (
            llm_config["api_key"],
            llm_config["base_url"],
            llm_config["model"],
        )
    except Exception as exc:
        # 文案要如实：这里**没有**任何兜底来源，受影响的是两项增强功能，
        # 不是整个转写（所以是 warning 而不是 error，也不重抛）。
        logger.warning(
            "无法读取 EngramNote 的 LLM 配置（%s: %s）：本次转写将跳过"
            "「标点恢复」与「标题生成」，转写本身不受影响。"
            "请检查 backend/.env 的 DEEPSEEK_API_KEY 等配置。",
            type(exc).__name__,
            exc,
        )
        return (None, "", "")
