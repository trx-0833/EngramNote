"""
LLM 网关：所有外部模型调用的**唯一出口**（overhaul-plan 阶段 4.1）

## 为什么要有这一层

改造前，`LLMService` 同时承担三件不同的事：

    1. 拼提示词        （业务：理解/出题/判分该问什么）
    2. 调模型          （传输：HTTP、SSE、重试）
    3. 调用策略        （治理：限流、并发、配额、缓存、记账）

第 2、3 件事与"业务要问什么"毫无关系，却和它挤在同一个类里 ——
后果在阶段 4.2/4.3/4.7 这三轮里暴露得很清楚：**每一次要给调用加上一层治理，
都得往 `chat_detailed` 这个 170 行的函数里再插一段。**

到 4.7 结束时，`llm_service.py` 已经 1273 行，其中 **386 行**是纯粹的调用策略。
本模块把它们整体搬出来，于是：

    LLMGateway   传输 + 治理（重试、限流、并发闸门、配额、缓存、记账）
    LLMService   业务（提示词 + 场景方法），只负责"问什么"

## 谁在用、怎么用

`LLMService` 持有一个 `LLMGateway` 并把 `chat` / `chat_detailed` / `chat_stream`
原样转发，**对外 API 完全没有变化** —— 全仓有 15 个模块 import `LLMService`，
这次重构不要求任何一个改一行。这也让这次搬迁可以被既有测试完整覆盖：

    阶段 4.2 的记账测试、4.3 的配额测试、4.7 的缓存测试
    —— 共 90 个用例，全部是在**搬迁之前**写的，正好用来证明搬迁没改行为。

## 一次调用里各步骤的顺序（顺序本身是设计）

    1. 配额检查        —— 超限时连缓存都不该查，这次调用根本不该发生
    2. 缓存查询        —— 命中没有花钱，**不该占用并发额度与限流令牌**
    3. 拿信号量 + 令牌  —— 到这里才真正排队等资源
    4. 带重试地发请求
    5. 记账（成功/失败都要）+ 写缓存（只写成功）

把 2 放在 3 之前是有意的：否则一批重复请求会在信号量上白白排队，
而它们本来可以立刻返回。

## 调用资源按**事件循环**惰性创建（阶段 4.5）

`asyncio.Semaphore` / `asyncio.Lock`（令牌桶内部有锁）都绑定创建它们的事件循环。
阶段 4.5 之前，限流器与信号量是**类级单例、在首次实例化时建一次**，
于是有两个后果：

1. **配置被冻结**在第一次使用的那一刻（与 conftest 里记录的
   "import 时冻结数据库地址"是同一类缺陷），在测试里的表现是
   **依赖执行顺序**（见附录 AE.8）；
2. 在 Celery 这类"一个任务一个 loop"的环境里，`asyncio.Semaphore`
   可能建在 loop A 却在 loop B 里被 await —— 那是 `RuntimeError:
   ... is bound to a different event loop`，而且只在多任务并发时才偶发。

现在改为 `_loop_resources()`：以**运行中的事件循环对象**为键
（`weakref.WeakKeyDictionary`，loop 被回收时资源自动消失），
在第一次真正调用时按**当时的配置**创建。于是：

    API 进程（单 loop）       → 一份资源，进程内所有实例共享（与改造前意图一致）
    Celery（每任务一个 loop） → 每个任务一份，任务结束随 loop 释放
    每个测试（各自新 loop）   → 天然隔离，不再需要"预先建好单例"的 fixture

限流相关的 rpm 由网关**自己从全局配置读**（`get_settings()`），
不接受服务层传入：限流是网关的策略，而且这样"测试把 settings 换成
MagicMock"也不会把一个 MagicMock 塞进令牌桶（那正是附录 AE.8 那个
`TypeError: '<=' not supported between MagicMock and int` 的成因）。
"""

import asyncio
import json
import logging
import random
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from ...config import get_settings
from ..llm_accounting_service import current_context, record_call
from .client import _response_snippet, _truncate_messages, build_llm_headers, get_llm_client
from .json_parse import strip_json_fences
from .rate_limit import KeyedRateLimiter, RateLimiter

logger = logging.getLogger("engramnote.llm")

#: 并发闸门上限（每个事件循环一份）
MAX_CONCURRENCY = 3

#: 无用户上下文时的桶名。**不是免检**：否则"没接上下文"就成了绕过限流的办法。
ANONYMOUS_BUCKET = "__anonymous__"


@dataclass
class _LoopResources:
    """一个事件循环内共享的调用资源（信号量 + 三层令牌桶）

    三层桶的顺序（先问"是谁"，再问"问谁"，最后过总闸门）有意如此：
    在前两层等待时**不占用**总闸门的令牌 —— 否则一个被个人限额拖住的用户
    会把全局令牌一起扣住，反而更容易饿死别人。
    """
    semaphore: asyncio.Semaphore
    global_limiter: RateLimiter
    user_limiters: KeyedRateLimiter
    provider_limiters: KeyedRateLimiter
    max_rpm: int

    async def acquire_slots(self, *, user_id: Optional[str], provider: Optional[str]) -> None:
        cfg = get_settings()
        # 1) 按用户：谁在问
        await self.user_limiters.acquire(
            user_id or ANONYMOUS_BUCKET,
            int(getattr(cfg, "llm_user_max_rpm", 0) or 0),
        )
        # 2) 按供应商：问的是谁
        await self.provider_limiters.acquire(
            provider or "__unknown__",
            int(getattr(cfg, "llm_provider_max_rpm", 0) or 0),
        )
        # 3) 总闸门
        await self.global_limiter.acquire()


class LLMGateway:
    """LLM 调用的唯一出口

    使用方式::

        gateway = LLMGateway.from_settings()
        meta = await gateway.chat_detailed(messages, scene="extract")
    """

    #: 每个事件循环一份资源；loop 被回收时自动清理（见模块说明里的 4.5 一节）
    _loop_resources: "weakref.WeakKeyDictionary[Any, _LoopResources]" = weakref.WeakKeyDictionary()
    #: 保护 `_loop_resources` 的**线程**锁（不同线程可能各建自己的 loop）
    _resources_lock = threading.Lock()

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        provider: str,
        max_retries: int,
        retry_delay: float,
        cache_enabled: Optional[bool] = None,
        cache_ttl_days: Optional[int] = None,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._provider = provider
        self._max_retries = max_retries
        self._retry_delay = retry_delay

        # 缓存开关与 TTL 在**构造时**取值，与 provider/model/api_key 一致。
        #
        # `None` 表示"没指定，按当前配置来"（`from_settings` 走这条路）；
        # `LLMService` 则总是显式传入它自己那份 `settings` 的取值 ——
        # 因为既有测试是通过替换 `llm_service.settings` 来关缓存的，
        # 若这里改成直接读 `get_settings()`，那些测试会静默失效（缓存照开）。
        # 换句话说：显式传参既保住了旧行为，也让"关掉缓存"这件事变得可测。
        if cache_enabled is None:
            cache_enabled = bool(getattr(get_settings(), "llm_cache_enabled", True))
        if cache_ttl_days is None:
            cache_ttl_days = int(getattr(get_settings(), "llm_cache_ttl_days", 30) or 0)
        self._cache_enabled = cache_enabled
        self._cache_ttl_days = cache_ttl_days

        # 注意：限流器与信号量**不在这里创建**（阶段 4.5）。
        # `asyncio.Semaphore`/`asyncio.Lock` 绑定创建它们的事件循环，而
        # 构造函数既可能在无 loop 的上下文里被调用，也可能在一个"用完就关"的
        # Celery loop 里被调用。因此改为在真正发请求时按当前 loop 惰性创建，
        # 见 `_resources()`。

    @classmethod
    def _resources(cls) -> _LoopResources:
        """取当前事件循环的资源（不存在则按当前配置创建）

        ⚠️ 必须在**运行中的**事件循环里调用（`get_running_loop`）。
        以 loop 对象本身为键而不是 `id(loop)`：loop 被 GC 后 id 会被复用，
        用 id 做键会让新 loop 误用旧 loop 的资源（附录 W 里 `id()` 复用的
        那个坑是同一类）。
        """
        loop = asyncio.get_running_loop()
        max_rpm = int(getattr(get_settings(), "llm_max_rpm", 0) or 0)
        with cls._resources_lock:
            resources = cls._loop_resources.get(loop)
            if resources is None or resources.max_rpm != max_rpm:
                # rpm 改了就整体重建：旧桶的余量与补充速率都属于旧配置，
                # 继续用会让"调小限额"在桶耗尽之前不生效。
                resources = _LoopResources(
                    semaphore=asyncio.Semaphore(MAX_CONCURRENCY),
                    global_limiter=RateLimiter(max_rpm=max_rpm),
                    user_limiters=KeyedRateLimiter(),
                    provider_limiters=KeyedRateLimiter(),
                    max_rpm=max_rpm,
                )
                cls._loop_resources[loop] = resources
            return resources

    @classmethod
    def from_settings(cls) -> "LLMGateway":
        """按当前配置构造（`LLMService` 用的就是这个入口）"""
        settings = get_settings()
        llm_config = settings.get_llm_config()
        return cls(
            api_key=llm_config["api_key"],
            model=llm_config["model"],
            base_url=llm_config["base_url"],
            provider=llm_config["provider"],
            max_retries=settings.llm_max_retries,
            retry_delay=settings.llm_retry_delay,
        )

    # ---- 只读属性：调用方（如 rag_service 回填 provider）需要读它们 ----
    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def retry_delay(self) -> float:
        return self._retry_delay

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        scene: Optional[str] = None,
    ) -> str:
        """返回助手文本（兼容旧调用方）；需要 finish_reason 时用 `chat_detailed`"""
        meta = await self.chat_detailed(
            messages, temperature=temperature, max_tokens=max_tokens,
            response_format=response_format, scene=scene,
        )
        return meta["content"]

    async def chat_detailed(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        scene: Optional[str] = None,
    ) -> Dict[str, Any]:
        """通用聊天接口（结构化返回，见 docs/decisions.md#F-33）

        Returns:
            dict: {"content", "finish_reason", "truncated", "usage"}
        """
        # 阶段 4.3：配额检查放在**最前面**（连信号量都还没拿）——
        # 被配额拒绝的调用不该占用并发额度，也不该产生任何网络请求。
        await self._enforce_quota(scene)

        url = f"{self._base_url}/chat/completions"
        # OpenCode 网关要求 x-opencode-session 头，缺失即 400 MissingSessionID，
        # 见 services/llm/client.py#build_llm_headers
        headers = build_llm_headers(self._api_key, self._base_url)
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        logger.debug(
            f"LLM 请求 | scene={scene} | provider={self._provider} | model={self._model} | "
            f"temperature={temperature} | max_tokens={max_tokens} | url={url}\n"
            f"messages={json.dumps(_truncate_messages(messages), ensure_ascii=False)}"
        )

        start_time = time.monotonic()

        # 阶段 4.7：查缓存放在配额之后、拿信号量之前（顺序理由见模块说明）
        cache_key_value = None
        if self._cache_enabled:
            from ..llm_cache_service import cache_key

            cache_key_value = cache_key(
                provider=self._provider, base_url=self._base_url, model=self._model,
                messages=messages, temperature=temperature, max_tokens=max_tokens,
                response_format=response_format,
            )
            hit = await self._lookup_cache(cache_key_value)
            if hit is not None:
                return hit

        # 阶段 4.5：资源按**当前事件循环**取（信号量 + 三层令牌桶）
        resources = self._resources()
        user_id = current_context().user_id

        async with resources.semaphore:
            # 阶段 4.4：先按用户、再按供应商、最后过总闸门（顺序理由见 _LoopResources）
            await resources.acquire_slots(user_id=user_id, provider=self._provider)

            last_error = None
            for attempt in range(self._max_retries):
                try:
                    # 复用模块级共享客户端，避免每次新建连接，见 docs/decisions.md#F-05
                    resp = await get_llm_client().post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    elapsed_ms = (time.monotonic() - start_time) * 1000
                    usage = data.get("usage", {})
                    choice: Dict[str, Any] = data["choices"][0]
                    finish_reason = (choice.get("finish_reason") or "").strip() or "stop"
                    content_raw = (choice["message"].get("content")) or ""
                    # JSON 场景剥离代码围栏，避免 json.loads 直接失败，见 docs/decisions.md#F-33
                    if response_format and isinstance(response_format, dict) and response_format.get("type") == "json_object":
                        content = strip_json_fences(content_raw)
                    else:
                        content = content_raw
                    truncated = (finish_reason == "length")
                    logger.info(
                        f"LLM 响应 | scene={scene} | provider={self._provider} | model={self._model} | "
                        f"prompt_tokens={usage.get('prompt_tokens')} | completion_tokens={usage.get('completion_tokens')} | "
                        f"total_tokens={usage.get('total_tokens')} | finish_reason={finish_reason} | "
                        f"truncated={truncated} | elapsed={elapsed_ms:.0f}ms"
                    )
                    if truncated:
                        logger.warning(
                            f"LLM 输出被 max_tokens 截断 | scene={scene} | max_tokens={max_tokens} | "
                            f"elapsed={elapsed_ms:.0f}ms | 建议提大 max_tokens 或减小单次输出体量"
                        )
                    # 阶段 4.2：记账。**成功也要记** —— 只记失败会得到
                    # "花了多少钱"这个最重要的数字为零。
                    await record_call(
                        scene=scene, provider=self._provider, model=self._model,
                        usage=usage, latency_ms=elapsed_ms,
                    )
                    result = {
                        "content": content,
                        "finish_reason": finish_reason,
                        "truncated": truncated,
                        "usage": usage,
                    }
                    # 阶段 4.7：只有**成功**响应才写缓存 ——
                    # 把失败缓存下来会把一次偶发故障固化成"这个输入永远失败"。
                    if cache_key_value is not None:
                        await self._store_cache(cache_key_value, result, usage)
                    return result
                except httpx.HTTPStatusError as e:
                    # 4xx 客户端错误（400/401/403/404）不重试，直接抛出；
                    # 429/5xx 视为可重试，见 docs/decisions.md#F-20
                    last_error = e
                    if e.response is not None and 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                        # 必须打印响应体：网关的真实原因只在 body 里
                        # （如 OpenCode 的 {"error":{"type":"MissingSessionID",...}}），
                        # 只打 str(e) 会得到无信息量的 "Client error '400 Bad Request'"，
                        # 曾使一个全链路 400 故障难以定位。
                        logger.warning(
                            f"LLM 客户端错误不重试 | scene={scene} | provider={self._provider} | "
                            f"status={e.response.status_code} | error={e} | "
                            f"body={_response_snippet(e.response)}"
                        )
                        raise
                    if attempt < self._max_retries - 1:
                        delay = min(30 * (attempt + 1), 120)
                        delay = delay * (0.5 + random.random() * 0.5)
                        logger.warning(
                            f"LLM 调用失败 | scene={scene} | attempt {attempt + 1}/{self._max_retries} | "
                            f"provider={self._provider} | model={self._model} | error={e} | "
                            f"next_delay={delay:.1f}s"
                        )
                        await asyncio.sleep(delay)
                except Exception as e:
                    last_error = e
                    if attempt < self._max_retries - 1:
                        delay = min(self._retry_delay * (2 ** attempt), 60)
                        delay = delay * (0.5 + random.random() * 0.5)
                        logger.warning(
                            f"LLM 调用失败 | scene={scene} | attempt {attempt + 1}/{self._max_retries} | "
                            f"provider={self._provider} | model={self._model} | error={e} | "
                            f"next_delay={delay:.1f}s"
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            f"LLM 调用失败 | scene={scene} | attempt {attempt + 1}/{self._max_retries} | "
                            f"provider={self._provider} | model={self._model} | error={e}"
                        )

        # 阶段 4.2：失败的调用也要记账。
        #
        # 最烧钱的形态恰恰是**失败的重试风暴**：`llm_max_retries=5`，
        # 每次重试都可能已经把 prompt token 发出去并被计费，
        # 而只记成功等于把最该被看见的那部分成本藏起来。
        # 这里记的是**整次调用**（含全部重试）的耗时与最终结果。
        await record_call(
            scene=scene, provider=self._provider, model=self._model,
            latency_ms=(time.monotonic() - start_time) * 1000,
            success=False,
            error=str(last_error) if last_error else "未知错误",
        )
        raise Exception(
            f"LLM API 调用失败，重试 {self._max_retries} 次后仍出错 "
            f"(scene={scene}, provider={self._provider}, model={self._model}): {last_error}"
        )

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        scene: str = "rag_answer_stream",
    ) -> AsyncIterator[str]:
        """流式聊天接口（OpenAI 兼容 SSE 流式响应）

        - 使用 stream=True 接收 SSE 响应，逐 token 产出
        - 不做重试（流式重试语义复杂，由调用方处理）
        - 不支持 response_format / max_tokens（流式场景一般不需要）
        - httpx 错误时记录 warning 并原样抛出

        ⚠️ **不做响应缓存**（阶段 4.7）：SSE 的 usage 在最后一个 chunk 才到，
        缓存它要在生成器里处理"部分消费即关闭"的语义；而问答的上下文每次都不同，
        收益本就接近零。这是取舍，不是遗漏。
        """
        # 阶段 4.3：异步生成器的第一句，在**产出任何 token 之前**检查配额 ——
        # 否则用户会先看到半截回答再断掉，比一开始就拒绝更糟。
        await self._enforce_quota(scene)

        url = f"{self._base_url}/chat/completions"
        # 流式路径同样需要 OpenCode 网关会话头（否则 400 MissingSessionID）
        headers = build_llm_headers(self._api_key, self._base_url)
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "temperature": 0.3,
        }

        logger.debug(
            f"LLM 流式请求 | scene={scene} | provider={self._provider} | model={self._model} | "
            f"temperature=0.3 | url={url}\n"
            f"messages={json.dumps(_truncate_messages(messages), ensure_ascii=False)}"
        )

        start_time = time.monotonic()
        total_content: List[str] = []
        usage: Dict[str, Any] = {}

        # 阶段 4.5 / 4.4：与 chat_detailed 同一套资源与同一套顺序
        resources = self._resources()
        user_id = current_context().user_id

        async with resources.semaphore:
            await resources.acquire_slots(user_id=user_id, provider=self._provider)
            try:
                # 复用模块级共享客户端，见 docs/decisions.md#F-05
                client = get_llm_client()
                async with client.stream(
                    "POST", url, json=payload, headers=headers
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            logger.warning(
                                f"LLM 流式响应 JSON 解析失败 | scene={scene} | "
                                f"line={data_str[:200]}"
                            )
                            continue
                        # 收集 usage（DeepSeek 可能在末尾 chunk 返回）
                        chunk_usage = chunk.get("usage")
                        if isinstance(chunk_usage, dict):
                            usage = chunk_usage
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        content = delta.get("content")
                        if content:
                            total_content.append(content)
                            yield content
            except httpx.HTTPError as e:
                logger.warning(
                    f"LLM 流式调用失败 | scene={scene} | provider={self._provider} | "
                    f"model={self._model} | error={e}"
                )
                raise

        elapsed_ms = (time.monotonic() - start_time) * 1000
        full_response = "".join(total_content)
        logger.info(
            f"LLM 流式响应完成 | scene={scene} | provider={self._provider} | model={self._model} | "
            f"prompt_tokens={usage.get('prompt_tokens')} | completion_tokens={usage.get('completion_tokens')} | "
            f"total_tokens={usage.get('total_tokens')} | "
            f"prompt_cache_hit_tokens={usage.get('prompt_cache_hit_tokens')} | "
            f"prompt_cache_miss_tokens={usage.get('prompt_cache_miss_tokens')} | "
            f"elapsed={elapsed_ms:.0f}ms | chars={len(full_response)}"
        )
        # 阶段 4.2：流式路径同样记账。usage 只在最后一个 chunk 里，
        # 所以必须在**流读完之后**记，不能在一开始记。
        await record_call(
            scene=scene, provider=self._provider, model=self._model,
            usage=usage, latency_ms=elapsed_ms,
        )

    # ------------------------------------------------------------------
    # 调用策略
    # ------------------------------------------------------------------

    async def _lookup_cache(self, key: str) -> Optional[Dict[str, Any]]:
        """查响应缓存（阶段 4.7）；未命中或缓存不可用时返回 None

        命中时**照样记一行 `llm_calls`**（`cached=True`、`cost=0`、
        `saved_tokens=N`）—— 记账表要能回答"这个月本可以花多少"，
        只记真实支出的话，缓存省下的钱在任何报表上都看不见。
        """
        from ...database import get_session_factory
        from ..llm_cache_service import lookup

        try:
            factory = get_session_factory()
            async with factory() as db:
                hit = await lookup(db, key)
        except Exception as exc:  # noqa: BLE001 - 缓存是不可靠的旁路
            logger.debug("查 LLM 缓存失败（当作未命中）: %s", exc)
            return None

        if hit is None:
            return None

        logger.info("LLM 缓存命中 | model=%s | saved_tokens=%d", self._model, hit.total_tokens)
        await record_call(
            scene="cache_hit", provider=self._provider, model=self._model,
            latency_ms=0, cached=True, saved_tokens=hit.total_tokens,
        )
        return hit.response

    async def _store_cache(
        self, key: str, response: Dict[str, Any], usage: Optional[Dict[str, Any]],
    ) -> None:
        """把成功响应写入缓存（阶段 4.7）

        ⚠️ 写在**调用方校验之前**：这里拿到的是"HTTP 200 + JSON 可解析"，
        不代表内容合格。残余风险与三条缓解见 `llm_cache_service` 的模块说明。
        """
        from ...database import get_session_factory
        from ..llm_cache_service import store

        try:
            # ⚠️ 传的是**已解析出来的** sessionmaker，不是 `get_session_factory`
            # 这个函数本身：`store` 的契约是 `async with session_factory()`。
            # 实测症状：传函数进去会让写缓存静默失败（日志里只有一句
            # `__aenter__`），于是缓存"看起来开了"却永远不命中。
            factory = get_session_factory()
            await store(
                None,  # 用独立会话（见 store 的 session_factory 说明）
                key,
                provider=self._provider, model=self._model,
                response=response, usage=usage,
                finish_reason=response.get("finish_reason"),
                ttl_days=self._cache_ttl_days,
                session_factory=factory,
            )
        except Exception as exc:  # noqa: BLE001 - 写缓存失败不影响本次调用
            logger.warning("写 LLM 缓存失败（不影响本次调用）: %s", exc)

    async def _enforce_quota(self, scene: Optional[str]) -> None:
        """发起调用前检查配额，超限则抛 `LLMQuotaExceeded`（阶段 4.3）

        放在这里而不是每个调用方各自查：这是**唯一**真正花钱的地方，
        拦在这里意味着无论从哪条路径进来（理解任务、问答、语义判分、
        将来新增的场景），配额都自动生效。让每个调用方各自记得查一次，
        必然会有漏的，而漏掉的那个恰恰是没人想到的昂贵路径。

        `user_id` 为空时**放行**：无法归属的调用算不到任何人头上，
        拦不住也不该拦（见 `record_call` 的说明）。这是已知缺口。

        不限配额时零开销：`check_quota` 在两项配额都为 0（默认）时第一行就返回，
        不碰数据库。
        """
        # ⚠️ `check_quota` 是**函数内** import，不是随手写的：这样每次调用都
        # 重新取 `llm_accounting_service.check_quota`，测试才能用
        # `monkeypatch.setattr(acc, "check_quota", ...)` 注入阈值
        # （见 tests/test_llm_accounting.py::_patch_quota）。
        # 改成模块级 import 会让那个 patch 静默失效。
        from ..llm_accounting_service import LLMQuotaExceeded, check_quota

        user_id = current_context().user_id
        if not user_id:
            return
        status = await check_quota(user_id)
        if status.exceeded:
            logger.warning(
                f"LLM 配额已用完，拒绝调用 | user={user_id[:8]} | scene={scene} | "
                f"tokens={status.tokens_used}/{status.token_limit} | "
                f"cost={status.cost_used:.4f}/{status.cost_limit:.2f}"
            )
            raise LLMQuotaExceeded(status)


__all__ = ["MAX_CONCURRENCY", "LLMGateway"]
