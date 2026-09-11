"""
LLM API 调用服务模块（**接线层**）

本模块封装了 DeepSeek 和 GLM 的 API 调用，遵循 Karpathy 风格：
from-scratch, minimal dependencies, 不引入 LangChain。

## 阶段 4.1 结束时的分工

改造前，本模块同时承担三件事：拼提示词、调模型、以及调用策略
（重试/限流/并发/配额/缓存/记账）。三者挤在一个类里，后果是
4.2/4.3/4.7 每一轮都要往 `chat_detailed` 那个 170 行的函数里再插一段 ——
到 4.7 结束时本文件已 **1273 行**。

现在四件事各有归属：

    LLMGateway    传输 + 治理     services/llm/gateway.py
    prompts       提示词文本      services/llm/prompts.py
    SceneMethods  业务场景方法    services/llm/scenes.py
    LLMService    接线 + 转发     本模块（**约 230 行**）

本模块只做三件事：按配置构造网关、把 `chat`/`chat_detailed`/`chat_stream`
转发给它、re-export 历史公共名称。

## 为什么调用方一行都不用改

全仓 15 个模块 `from ...llm_service import LLMService`，用的是
`service.chat(...)` / `service.generate_questions(...)` 这些**同名方法**：

- `chat` / `chat_detailed` / `chat_stream` 仍是本类的转发包装；
- 场景方法通过 `class LLMService(SceneMethods)` 混入，方法解析顺序不变；
- re-export 保留了 `ConversationSession` / `parse_json_tolerant` /
  `RateLimiter` / `close_llm_client` 等历史 import 路径。

因此这次拆分可以被既有测试完整覆盖：4.2/4.3/4.7 的 90 个治理用例、
以及 `tests/test_prompt_golden.py` 里 11 个"提示词逐字节不变"的摘要，
都是**拆分之前**写的。
"""

import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from ..config import get_settings
from .llm.client import close_llm_client  # noqa: F401  re-export：main.py 经 llm_service 导入
from .llm.gateway import LLMGateway
from .llm.json_parse import parse_json_tolerant  # noqa: F401  re-export
from .llm.rate_limit import RateLimiter  # noqa: F401  re-export
from .llm.scenes import SceneMethods
from .llm.sessions import (  # noqa: F401  re-export：sessions.py 反向引用 LLMService
    CombinedAnalysisSession,
    ConversationSession,
    UnderstandingSession,
)

logger = logging.getLogger("engramnote.llm")
settings = get_settings()


class LLMService(SceneMethods):
    """
    LLM API 调用服务

    根据 debug 模式自动选择提供商：
    - debug=True → GLM-4.7-flash（免费，适合开发调试）
    - debug=False → DeepSeek v4-flash（生产环境，效果更稳定）

    使用方式：
        service = LLMService()
        result = await service.chat([{"role": "user", "content": "你好"}])
        summary = await service.summarize_chapter("第一章", "内容...")

    阶段 4.1 之后本类**不再自己实现**重试/限流/并发/配额/缓存/记账，
    这些统一由 `self.gateway`（services/llm/gateway.py）负责。
    """

    def __init__(self, *, gateway: Optional[LLMGateway] = None) -> None:
        """构造服务

        Args:
            gateway: 可选注入（测试可用它整体替换调用链；默认按当前配置构造）

        ⚠️ 配置在**构造时**读取，与改造前一致：provider / model / api_key /
        缓存开关都取自构造那一刻的 `settings`。"配置被冻结在首次使用"
        这个已知缺陷归属阶段 4.5，本次搬迁不改变它。
        """
        if gateway is None:
            llm_config = settings.get_llm_config()
            gateway = LLMGateway(
                api_key=llm_config["api_key"],
                model=llm_config["model"],
                base_url=llm_config["base_url"],
                provider=llm_config["provider"],
                max_retries=settings.llm_max_retries,
                retry_delay=settings.llm_retry_delay,
                # 缓存开关**显式传入**而不是让网关自己去读 `get_settings()`：
                # 既有测试是通过替换本模块的 `settings` 来关缓存的，
                # 网关若绕过它去读全局配置，那些测试会静默失效（缓存照开）。
                cache_enabled=getattr(settings, "llm_cache_enabled", True),
                cache_ttl_days=int(getattr(settings, "llm_cache_ttl_days", 30) or 0),
            )
        self.gateway = gateway

        # 向后兼容的只读快照：`rag_service` 会读 `llm_service._provider`，
        # 既有测试也断言 `_provider/_model/_api_key`。
        # 它们**只是快照** —— 改它们不会影响真实调用（真实调用读 gateway）。
        self._api_key = gateway.api_key
        self._model = gateway.model
        self._base_url = gateway.base_url
        self._provider = gateway.provider
        self._max_retries = gateway.max_retries
        self._retry_delay = gateway.retry_delay

    # ------------------------------------------------------------------
    # 以下三个方法是**转发**：实现在 services/llm/gateway.py
    #
    # 保留它们（而不是让调用方直接用 gateway）的理由：
    #   1. 全仓 15 个模块 import 的是 LLMService，改调用方是纯粹的噪音；
    #   2. 提示词与场景方法仍在本类上，调用方 `service.chat(...)` 读起来是一条链；
    #   3. 将来要加"场景级默认参数"（如某场景强制 max_tokens）时，
    #      有这一层就不必再改所有调用方。
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        scene: Optional[str] = None,
    ) -> str:
        """
        通用聊天接口（OpenAI 兼容格式，返回 content 字符串）

        与 chat_detailed() 的区别：只返回助手文本内容（兼容旧调用方）；
        需要判断是否截断（finish_reason=length）时请用 chat_detailed()。

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            temperature: 采样温度，0-2，越高越随机
            max_tokens: 最大生成 token 数
            response_format: 响应格式约束，如 {"type": "json_object"}
            scene: 场景标识，用于日志、记账与缓存

        Returns:
            str: 模型生成的文本内容（JSON 场景已剥离代码围栏）

        Raises:
            LLMQuotaExceeded: 配额已用完时抛出（阶段 4.3）
        """
        return await self.gateway.chat(
            messages, temperature=temperature, max_tokens=max_tokens,
            response_format=response_format, scene=scene,
        )

    async def chat_detailed(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        scene: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        通用聊天接口（结构化返回）

        与 chat() 的区别：额外返回 finish_reason 与 truncated，
        供调用方判断"输出是否被 max_tokens 截断"（截断的 JSON 不能当成功用）。

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            temperature: 采样温度，0-2
            max_tokens: 最大生成 token 数
            response_format: 响应格式约束，如 {"type": "json_object"}
            scene: 场景标识，用于日志、记账与缓存

        Returns:
            dict: {"content", "finish_reason", "truncated", "usage"}

        Raises:
            LLMQuotaExceeded: 配额已用完时抛出（阶段 4.3）
            Exception: 重试耗尽后抛出（消息含 "LLM API 调用失败"）
        """
        return await self.gateway.chat_detailed(
            messages, temperature=temperature, max_tokens=max_tokens,
            response_format=response_format, scene=scene,
        )

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        scene: str = "rag_answer_stream",
    ) -> AsyncIterator[str]:
        """
        流式聊天接口（OpenAI 兼容 SSE 流式响应）

        逐 token 产出内容，适合需要实时展示生成过程的前端场景（RAG 问答）。

        与 chat() 的区别：
        - 使用 stream=True 接收 SSE 响应
        - 不做重试（流式重试语义复杂，由调用方处理）
        - 不支持 response_format / max_tokens 参数（流式场景一般不需要）
        - **不做响应缓存**（理由见网关模块内 `chat_stream` 的说明）

        调用方应使用与 chat() 一致的 system prompt 前缀以命中 DeepSeek 提示词缓存。

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            scene: 场景标识，用于日志记录

        Yields:
            str: 模型生成的文本内容片段（token 粒度）

        Raises:
            httpx.HTTPError: HTTP 调用失败时抛出，由调用方处理
            LLMQuotaExceeded: 配额已用完时抛出（阶段 4.3）
        """
        # 本方法保持**异步生成器**形态（而不是 `return self.gateway.chat_stream(...)`）：
        # 调用方写的是 `async for chunk in service.chat_stream(...)`，
        # 而且配额检查必须发生在**产出第一个 token 之前** ——
        # 这一点由网关生成器的第一句话保证，转发不改变时序。
        async for chunk in self.gateway.chat_stream(messages, scene=scene):
            yield chunk
