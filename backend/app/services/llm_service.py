"""
LLM API 调用服务模块

本模块封装了 DeepSeek 和 GLM 的 API 调用，遵循 Karpathy 风格：
from-scratch, minimal dependencies, 不引入 LangChain。

直接使用 httpx 调用 OpenAI 兼容 API（DeepSeek 和 GLM 都兼容），
保持最小依赖，代码清晰可控。

主要职责：
- 通用聊天接口（OpenAI 兼容格式）
- 章节摘要生成
- 知识点提取（结构化 JSON 输出）
- 题目生成
- RAG 问答

设计决策：
- 根据 debug 模式自动选择 LLM 提供商（debug=GLM, 非 debug=DeepSeek）
- 使用 httpx.AsyncClient 直接调用 API，不引入 openai SDK
- 提示词模板内置在服务中，支持 JSON 结构化输出
- 重试机制：API 调用失败时重试，指数退避
- 速率限制：控制 API 调用频率，避免超限
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_settings

logger = logging.getLogger("engramnote.llm")
settings = get_settings()


class RateLimiter:
    """令牌桶速率限制器

    控制 API 请求速率，避免触发 429 Too Many Requests。
    使用令牌桶算法：以恒定速率补充令牌，请求前消耗一个令牌，
    无可用令牌时等待。

    Attributes:
        max_rpm: 每分钟最大请求数
        _tokens: 当前可用令牌数
        _last_refill: 上次补充令牌的时间戳
        _lock: 异步锁，保证线程安全
    """

    def __init__(self, max_rpm: int = 10):
        self.max_rpm = max_rpm
        self._tokens = float(max_rpm)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """获取一个令牌，等待直到有可用令牌"""
        if self.max_rpm <= 0:
            return
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self.max_rpm, self._tokens + elapsed * (self.max_rpm / 60.0))
                self._last_refill = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_time = (1.0 - self._tokens) * (60.0 / self.max_rpm)
            await asyncio.sleep(wait_time)


class ConversationSession:
    """
    多轮对话会话，在同一窗口内累积消息

    使用方式：
        session = llm_service.create_understanding_session()
        result1 = await session.ask("章节1内容...")
        result2 = await session.ask("章节2内容...")  # LLM 能看到章节1的结果
    """

    def __init__(
        self,
        llm_service: "LLMService",
        system_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        max_context_pairs: int = 30,
        scene: Optional[str] = None,
    ):
        """
        Args:
            llm_service: LLM 服务实例
            system_prompt: 系统提示词
            temperature: 采样温度
            max_tokens: 最大生成 token 数
            response_format: 响应格式约束
            max_context_pairs: 保留的最大对话轮次（1轮=1 user + 1 assistant）
            scene: 场景标识，用于日志记录
        """
        self._llm = llm_service
        self._messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._response_format = response_format
        self._max_context_pairs = max_context_pairs
        self._scene = scene

    async def ask(self, user_content: str) -> str:
        """
        在当前对话窗口中追问，返回助手回复

        将用户消息追加到对话历史，调用 LLM，再将助手回复追加回历史。
        下次调用 ask() 时，LLM 能看到之前的完整对话上下文。

        Args:
            user_content: 用户消息内容

        Returns:
            str: 助手回复内容
        """
        self._messages.append({"role": "user", "content": user_content})
        response = await self._llm.chat(
            self._messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            response_format=self._response_format,
            scene=self._scene,
        )
        self._messages.append({"role": "assistant", "content": response})
        self._trim_if_needed()
        return response

    def _trim_if_needed(self):
        """如果消息数量超过限制，裁剪中间的对话轮次，保留 system + 最近的对话"""
        # 每轮 = 1 user + 1 assistant = 2 条消息，加上 1 条 system
        max_msg_count = self._max_context_pairs * 2 + 1
        if len(self._messages) > max_msg_count:
            system_msg = self._messages[0]
            recent = self._messages[-(self._max_context_pairs * 2):]
            self._messages = [system_msg] + recent
            logger.info(
                f"对话上下文裁剪：保留 system + 最近 {self._max_context_pairs} 轮对话"
            )

    @property
    def message_count(self) -> int:
        """当前消息数量（含 system）"""
        return len(self._messages)

    @property
    def turn_count(self) -> int:
        """当前对话轮次(1轮 = 1次 ask 调用)"""
        return (len(self._messages) - 1) // 2


class UnderstandingSession(ConversationSession):
    """
    知识提取专用会话:用轻量级标题列表替代完整历史原文

    与基类 ConversationSession 的区别:
    - 不在 _messages 中累积历史 user/assistant 消息(避免 Token 浪费)
    - 维护 _extracted_titles 列表,每次 ask() 后从容错解析响应中提取新标题
    - 下次 ask() 时,在 user_content 末尾追加"[已提取知识点标题(请勿重复)]"提示
    - 第N轮请求只包含 system + 当前章节 + 之前所有标题,不包含历史原文

    属性语义:
    - turn_count: ask() 调用次数(与基类语义一致,用于"对话轮次"日志)
    - extracted_titles_count: 已提取标题总数(用于业务统计,可能大于 turn_count)

    Token 节省:第N轮请求的 input tokens 从 O(N×章节长度) 降为 O(章节长度 + N×标题长度)
    实测 17 章文档第6轮:从 ~72000 tokens 降为 ~8100 tokens(节省 89%)
    """

    MAX_TITLES = 200  # 标题列表上限,防止极端长文档导致列表本身过长

    def __init__(self, llm_service: "LLMService", system_prompt: str, **kwargs):
        super().__init__(llm_service, system_prompt, **kwargs)
        self._extracted_titles: List[str] = []
        self._ask_count: int = 0  # 真实 ask() 调用次数,与基类 turn_count 语义对齐

    async def ask(self, user_content: str) -> str:
        """
        知识提取专用 ask:不累积历史原文,只追加已提取标题列表

        Args:
            user_content: 当前批次的章节合并内容(由调用方构建)

        Returns:
            str: LLM 响应(JSON 字符串)
        """
        # 构建去重提示:当前内容 + 已提取标题(如有)
        context_hint = ""
        if self._extracted_titles:
            titles = self._extracted_titles[-self.MAX_TITLES:]
            context_hint = (
                "\n\n[已提取知识点标题(请勿重复提取以下知识点)]:\n"
                + "\n".join(f"- {t}" for t in titles)
            )

        # 关键优化:每次只用 system + 当前 user 消息,不累积历史
        messages = [
            self._messages[0],  # system
            {"role": "user", "content": user_content + context_hint},
        ]

        response = await self._llm.chat(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            response_format=self._response_format,
            scene=self._scene,
        )

        # 解析响应,提取新标题加入轻量级列表(容错,失败不阻塞主流程)
        self._ask_count += 1
        self._extract_new_titles(response)

        return response

    def _extract_new_titles(self, response: str) -> None:
        """
        从多章节 JSON 响应中提取知识点标题,追加到 _extracted_titles

        支持的响应格式(与 _parse_understanding_response 对齐):
        - 多章节: {"chapters": [{"points": [{"title": "..."}, ...]}, ...]}
        - 单章节(兼容): {"summary": "...", "points": [{"title": "..."}, ...]}

        解析失败时记 warning 日志,不抛异常(降级为本轮无去重提示)。
        """
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            logger.warning(
                f"UnderstandingSession 标题提取:JSON 解析失败,本轮降级为无去重: {response[:200]}"
            )
            return

        if not isinstance(data, dict):
            return

        # 多章节格式
        chapters = data.get("chapters")
        if isinstance(chapters, list):
            for ch in chapters:
                if isinstance(ch, dict):
                    for p in ch.get("points", []) or []:
                        if isinstance(p, dict) and p.get("title"):
                            self._extracted_titles.append(str(p["title"]))
            return

        # 单章节格式(兼容)
        for key in ["points", "knowledge_points", "items", "data"]:
            points = data.get(key)
            if isinstance(points, list):
                for p in points:
                    if isinstance(p, dict) and p.get("title"):
                        self._extracted_titles.append(str(p["title"]))
                return

    @property
    def turn_count(self) -> int:
        """ask() 调用次数(与基类语义一致,基类按 _messages 长度推算,子类不累积消息故单独计数)"""
        return self._ask_count

    @property
    def extracted_titles_count(self) -> int:
        """已提取的标题总数(用于日志)"""
        return len(self._extracted_titles)


class CombinedAnalysisSession(ConversationSession):
    """
    联合分析专用会话:用轻量级标题列表替代完整历史原文

    与 UnderstandingSession 类似的轻量级模式:
    - 不在 _messages 中累积历史 user/assistant 消息(避免 Token 浪费)
    - 维护 _extracted_titles 列表,每次 ask() 后从容错解析响应中提取新标题
    - 下次 ask() 时,在 user_content 末尾追加"[已提取知识点标题(请勿重复)]"提示
    - 第N轮请求只包含 system + 当前章节资料 + 用户笔记 + 之前所有标题

    与 UnderstandingSession 的区别:
    - ask() 接收双参数:material_chapter_content(章节资料) + personal_note_content(用户笔记全文)
    - 响应结构包含 regular_points(已掌握) 和 blind_spots(盲点) 两类
    - _extract_new_titles 同时从两个键提取标题

    Token 节省:与 UnderstandingSession 同思路,从 O(N×章节长度) 降为 O(章节长度 + N×标题长度)
    """

    MAX_TITLES = 200  # 标题列表上限,防止极端长文档导致列表本身过长

    def __init__(self, llm_service: "LLMService", system_prompt: str, **kwargs):
        super().__init__(llm_service, system_prompt, **kwargs)
        self._extracted_titles: List[str] = []
        self._ask_count: int = 0  # 真实 ask() 调用次数,与基类 turn_count 语义对齐

    async def ask(self, material_chapter_content: str, personal_note_content: str) -> str:
        """
        联合分析专用 ask:不累积历史原文,只追加已提取标题列表

        Args:
            material_chapter_content: 当前章节学习资料
            personal_note_content: 用户笔记全文

        Returns:
            str: LLM 响应(JSON 字符串)
        """
        # 构建去重提示:已提取标题(如有)
        context_hint = ""
        if self._extracted_titles:
            titles = self._extracted_titles[-self.MAX_TITLES:]
            context_hint = (
                "\n\n[已提取知识点标题(请勿重复提取以下知识点)]:\n"
                + "\n".join(f"- {t}" for t in titles)
            )

        # 关键优化:每次只用 system + 当前 user 消息,不累积历史
        user_content = (
            f"## 本章节学习资料：\n{material_chapter_content}\n\n"
            f"## 用户笔记（全文）：\n{personal_note_content}\n\n"
            f"请针对本章节资料与用户笔记做联合分析。"
            + context_hint
        )
        messages = [
            self._messages[0],  # system
            {"role": "user", "content": user_content},
        ]

        response = await self._llm.chat(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            response_format=self._response_format,
            scene=self._scene,
        )

        # 解析响应,提取新标题加入轻量级列表(容错,失败不阻塞主流程)
        self._ask_count += 1
        self._extract_new_titles(response)

        return response

    def _extract_new_titles(self, response: str) -> None:
        """
        从联合分析 JSON 响应中提取知识点标题,追加到 _extracted_titles

        支持的响应格式:
        - 联合分析: {"chapter_title": "...", "regular_points": [{"title": "..."}], "blind_spots": [{"title": "..."}]}
        - 多章节(兼容): {"chapters": [{"points": [{"title": "..."}, ...]}, ...]}
        - 单章节(兼容): {"points": [{"title": "..."}, ...]}

        解析失败时记 warning 日志,不抛异常(降级为本轮无去重提示)。
        """
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            logger.warning(
                f"CombinedAnalysisSession 标题提取:JSON 解析失败,本轮降级为无去重: {response[:200]}"
            )
            return

        if not isinstance(data, dict):
            return

        # 多章节格式(兼容 UnderstandingSession)
        chapters = data.get("chapters")
        if isinstance(chapters, list):
            for ch in chapters:
                if isinstance(ch, dict):
                    for p in ch.get("points", []) or []:
                        if isinstance(p, dict) and p.get("title"):
                            self._extracted_titles.append(str(p["title"]))
            return

        # 联合分析格式:同时提取 regular_points 和 blind_spots 中的标题
        # 兼容单章节格式:遍历所有 points-like 键
        for key in ["regular_points", "blind_spots", "points", "knowledge_points", "items", "data"]:
            points = data.get(key)
            if isinstance(points, list):
                for p in points:
                    if isinstance(p, dict) and p.get("title"):
                        self._extracted_titles.append(str(p["title"]))

    @property
    def turn_count(self) -> int:
        """ask() 调用次数(与基类语义一致,基类按 _messages 长度推算,子类不累积消息故单独计数)"""
        return self._ask_count

    @property
    def extracted_titles_count(self) -> int:
        """已提取的标题总数(用于日志)"""
        return len(self._extracted_titles)


class LLMService:
    """
    LLM API 调用服务

    根据 debug 模式自动选择提供商：
    - debug=True → GLM-4.7-flash（免费，适合开发调试）
    - debug=False → DeepSeek v4-flash（生产环境，效果更稳定）

    使用方式：
        service = LLMService()
        result = await service.chat([{"role": "user", "content": "你好"}])
        summary = await service.summarize_chapter("第一章", "内容...")
    """

    def __init__(self):
        llm_config = settings.get_llm_config()
        self._api_key = llm_config["api_key"]
        self._model = llm_config["model"]
        self._base_url = llm_config["base_url"]
        self._provider = llm_config["provider"]
        self._max_retries = settings.llm_max_retries
        self._retry_delay = settings.llm_retry_delay
        self._rate_limiter = RateLimiter(max_rpm=settings.llm_max_rpm)
        self._semaphore = asyncio.Semaphore(3)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        scene: Optional[str] = None,
    ) -> str:
        """
        通用聊天接口（OpenAI 兼容格式）

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            temperature: 采样温度，0-2，越高越随机
            max_tokens: 最大生成 token 数
            response_format: 响应格式约束，如 {"type": "json_object"}

        Returns:
            str: 模型生成的文本内容

        Raises:
            Exception: API 调用失败且重试耗尽
        """
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
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
            f"messages={json.dumps(messages, ensure_ascii=False, indent=2)}"
        )

        start_time = time.monotonic()

        async with self._semaphore:
            await self._rate_limiter.acquire()

            last_error = None
            for attempt in range(self._max_retries):
                try:
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        resp = await client.post(url, json=payload, headers=headers)
                        resp.raise_for_status()
                        data = resp.json()
                        elapsed_ms = (time.monotonic() - start_time) * 1000
                        usage = data.get("usage", {})
                        content = data["choices"][0]["message"]["content"]
                        logger.info(
                            f"LLM 响应 | scene={scene} | provider={self._provider} | model={self._model} | "
                            f"prompt_tokens={usage.get('prompt_tokens')} | completion_tokens={usage.get('completion_tokens')} | "
                            f"total_tokens={usage.get('total_tokens')} | elapsed={elapsed_ms:.0f}ms\n"
                            f"response={content}"
                        )
                        return content
                except Exception as e:
                    last_error = e
                    if attempt < self._max_retries - 1:
                        if "429" in str(e):
                            delay = min(30 * (attempt + 1), 120)
                            delay = delay * (0.5 + random.random() * 0.5)
                        else:
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

        raise Exception(
            f"LLM API 调用失败，重试 {self._max_retries} 次后仍出错 "
            f"(scene={scene}, provider={self._provider}, model={self._model}): {last_error}"
        )

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        scene: str = "rag_answer_stream",
    ):
        """
        流式聊天接口（OpenAI 兼容 SSE 流式响应）

        通过 SSE 流式接收 LLM 响应，逐 token 返回内容，适合需要实时
        展示生成过程的前端场景（如 RAG 问答流式回答）。

        与 chat() 的区别：
        - 使用 stream=True 接收 SSE 响应
        - 不做重试（流式重试语义复杂，由调用方处理）
        - 不支持 response_format / max_tokens 参数（流式场景一般不需要）
        - httpx 错误时记录 warning 并原样抛出

        调用方应使用与 chat() 一致的 system prompt 前缀以命中 DeepSeek 提示词缓存。

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            scene: 场景标识，用于日志记录

        Yields:
            str: 模型生成的文本内容片段（token 粒度）

        Raises:
            httpx.HTTPError: HTTP 调用失败时抛出，由调用方处理
        """
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "temperature": 0.3,
        }

        logger.debug(
            f"LLM 流式请求 | scene={scene} | provider={self._provider} | model={self._model} | "
            f"temperature=0.3 | url={url}\n"
            f"messages={json.dumps(messages, ensure_ascii=False, indent=2)}"
        )

        start_time = time.monotonic()
        total_content: List[str] = []
        usage: Dict[str, Any] = {}

        async with self._semaphore:
            await self._rate_limiter.acquire()
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
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
            f"elapsed={elapsed_ms:.0f}ms\n"
            f"response={full_response}"
        )

    async def summarize_chapter(self, chapter_title: str, chapter_content: str) -> str:
        """
        章节摘要生成

        Args:
            chapter_title: 章节标题
            chapter_content: 章节内容

        Returns:
            str: 章节摘要文本
        """
        # 限制输入长度，避免超出上下文窗口
        max_content = 8000
        if len(chapter_content) > max_content:
            chapter_content = chapter_content[:max_content] + "\n...(内容过长已截断)"

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个专业的学术助手。请为给定章节生成简洁准确的摘要。"
                    "摘要应包含：1) 章节核心主题 2) 关键论点或发现 3) 重要结论。"
                    "摘要长度控制在200字以内。"
                ),
            },
            {
                "role": "user",
                "content": f"章节标题：{chapter_title}\n\n章节内容：\n{chapter_content}",
            },
        ]
        return await self.chat(messages, temperature=0.3, max_tokens=1024, scene="summarize_chapter")

    async def extract_knowledge_points(
        self, chapter_title: str, chapter_content: str
    ) -> List[Dict[str, Any]]:
        """
        从章节中提取知识点（返回结构化 JSON）

        Args:
            chapter_title: 章节标题
            chapter_content: 章节内容

        Returns:
            List[Dict]: 知识点列表，每个包含：
                - card_type: 类型（concept/formula/qa/definition）
                - title: 知识点标题
                - content: 知识点内容
                - source_text: 原始出处文本
        """
        max_content = 8000
        if len(chapter_content) > max_content:
            chapter_content = chapter_content[:max_content] + "\n...(内容过长已截断)"

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个专业的知识提取助手。请从给定章节中提取关键知识点。\n\n"
                    "知识点类型说明：\n"
                    "- concept: 概念类，需要理解记忆的知识点\n"
                    "- formula: 公式类，数学公式、化学方程式等\n"
                    "- qa: 问答对，以问答形式呈现的知识\n"
                    "- definition: 定义类，需要精确记忆的定义\n\n"
                    "请以 JSON 数组格式返回，每个元素包含：\n"
                    '- card_type: 类型（concept/formula/qa/definition）\n'
                    "- title: 知识点标题（简洁明了）\n"
                    "- content: 知识点内容（详细描述）\n"
                    "- source_text: 原始出处文本（原文中对应的段落）\n\n"
                    "要求：\n"
                    "1. 每个知识点应独立完整，不依赖上下文也能理解\n"
                    "2. source_text 应尽量引用原文\n"
                    "3. 提取5-15个知识点\n"
                    "4. 只返回 JSON 数组，不要其他文字"
                ),
            },
            {
                "role": "user",
                "content": f"章节标题：{chapter_title}\n\n章节内容：\n{chapter_content}",
            },
        ]

        response = await self.chat(
            messages,
            temperature=0.3,
            max_tokens=4096,
            response_format={"type": "json_object"},
            scene="extract_knowledge",
        )

        try:
            # 尝试解析 JSON
            result = json.loads(response)
            # 如果返回的是 {"points": [...]} 格式，提取数组
            if isinstance(result, dict):
                # 尝试常见的键名
                for key in ["points", "knowledge_points", "items", "data"]:
                    if key in result:
                        return result[key]
                # 如果只有一个键且值是数组
                for v in result.values():
                    if isinstance(v, list):
                        return v
                return []
            if isinstance(result, list):
                return result
            return []
        except json.JSONDecodeError:
            logger.warning(f"知识点提取结果 JSON 解析失败: {response[:200]}")
            return []

    async def generate_questions(
        self,
        card_title: str,
        card_content: str,
        card_type: str,
        question_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        根据知识点生成题目

        Args:
            card_title: 知识点标题
            card_content: 知识点内容
            card_type: 知识点类型
            question_types: 题目类型列表，默认生成选择题和简答题

        Returns:
            List[Dict]: 题目列表，每个包含：
                - question_type: 题目类型
                - difficulty: 难度等级
                - question: 题目内容
                - answer: 正确答案
                - options: 选择题选项（JSON 字符串）
                - explanation: 解析
        """
        if question_types is None:
            question_types = ["choice", "short_answer"]

        type_desc = {
            "choice": "选择题（4个选项，1个正确答案，3个干扰项）",
            "fill_blank": "填空题（关键概念留空）",
            "short_answer": "简答题（要求简明扼要回答）",
        }
        types_str = "、".join(type_desc.get(t, t) for t in question_types)

        messages = [
            {
                "role": "system",
                "content": (
                    f"你是一个专业的出题助手。请根据给定知识点生成{types_str}。\n\n"
                    "请以 JSON 数组格式返回，每个元素包含：\n"
                    '- question_type: 题目类型（choice/fill_blank/short_answer）\n'
                    "- difficulty: 难度（easy/medium/hard）\n"
                    "- question: 题目内容\n"
                    "- answer: 正确答案\n"
                    "- options: 选择题选项（仅选择题需要，JSON 数组格式，如 "
                    '[\"A. 选项1\", \"B. 选项2\", \"C. 选项3\", \"D. 选项4\"]，其他类型为 null）\n'
                    "- explanation: 题目解析（解释为什么这个答案是对的）\n\n"
                    "要求：\n"
                    "1. 题目应准确考察知识点，不超出给定内容范围\n"
                    "2. 选择题的干扰项应合理，不能明显错误\n"
                    "3. 每种类型生成1-2道题\n"
                    "4. 只返回 JSON 数组，不要其他文字"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"知识点标题：{card_title}\n"
                    f"知识点类型：{card_type}\n"
                    f"知识点内容：{card_content}"
                ),
            },
        ]

        response = await self.chat(
            messages,
            temperature=0.5,
            max_tokens=4096,
            response_format={"type": "json_object"},
            scene="generate_questions",
        )

        try:
            result = json.loads(response)
            if isinstance(result, dict):
                for key in ["questions", "items", "data"]:
                    if key in result:
                        return result[key]
                for v in result.values():
                    if isinstance(v, list):
                        return v
                return []
            if isinstance(result, list):
                return result
            return []
        except json.JSONDecodeError:
            logger.warning(f"题目生成结果 JSON 解析失败: {response[:200]}")
            return []

    async def generate_questions_batch(
        self,
        cards: List[Dict[str, str]],
        question_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        批量根据知识点生成题目（减少 API 调用次数）

        将多个卡片合并到一个请求中，每个卡片生成1道选择题，
        减少 API 调用次数，避免触发速率限制。

        Args:
            cards: 知识点列表，每个包含 title, content, card_type
            question_types: 题目类型列表

        Returns:
            List[Dict]: 题目列表，每个包含 card_index 和题目信息
        """
        if not cards:
            return []

        if question_types is None:
            question_types = ["choice"]

        type_desc = {
            "choice": "选择题（4个选项，1个正确答案，3个干扰项）",
            "fill_blank": "填空题（关键概念留空）",
            "short_answer": "简答题（要求简明扼要回答）",
        }
        types_str = "、".join(type_desc.get(t, t) for t in question_types)

        # 构建批量知识点文本
        cards_text = ""
        for i, card in enumerate(cards):
            cards_text += f"\n--- 知识点 {i+1} ---\n"
            cards_text += f"标题：{card['title']}\n"
            cards_text += f"类型：{card['card_type']}\n"
            cards_text += f"内容：{card['content'][:500]}\n"

        messages = [
            {
                "role": "system",
                "content": (
                    f"你是一个专业的出题助手。请根据给定的 {len(cards)} 个知识点，"
                    f"为每个知识点生成1道{types_str}。\n\n"
                    '请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n'
                    '{"questions": [\n'
                    '  {"card_index": 1, "question_type": "choice", "difficulty": "easy", '
                    '"question": "题目内容", "answer": "正确答案", '
                    '"options": ["A.选项1", "B.选项2", "C.选项3", "D.选项4"], '
                    '"explanation": "解析"},\n'
                    '  {"card_index": 2, ...}\n'
                    ']}\n\n'
                    "要求：\n"
                    "1. 每个知识点生成1道题，共生成" + str(len(cards)) + "道题\n"
                    "2. 题目应准确考察知识点，不超出给定内容范围\n"
                    "3. 选择题的干扰项应合理\n"
                    "4. card_index 从1开始，对应知识点编号\n"
                    "5. options 必须是字符串数组，每个元素格式为 '字母.内容'\n"
                    "6. 只返回 JSON 对象，不要其他文字"
                ),
            },
            {
                "role": "user",
                "content": cards_text,
            },
        ]

        response = await self.chat(
            messages,
            temperature=0.5,
            max_tokens=8192,
            response_format={"type": "json_object"},
            scene="generate_questions",
        )

        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            # 尝试从响应中提取 JSON
            import re
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                except json.JSONDecodeError:
                    logger.warning(f"批量题目生成结果 JSON 解析失败: {response[:200]}")
                    return []
            else:
                logger.warning(f"批量题目生成结果 JSON 解析失败: {response[:200]}")
                return []

        logger.info(f"批量题目生成原始响应类型: {type(result)}, 键: {list(result.keys()) if isinstance(result, dict) else 'N/A'}")
        if isinstance(result, dict):
            for key in ["questions", "items", "data"]:
                if key in result:
                    items = result[key]
                    logger.info(f"键 '{key}' 的值类型: {type(items)}, 长度: {len(items) if isinstance(items, list) else 'N/A'}")
                    if isinstance(items, list) and len(items) > 0:
                        first = items[0]
                        if isinstance(first, dict):
                            logger.info(f"第一个元素是字典，键: {list(first.keys())}")
                            return items
                        else:
                            logger.warning(f"第一个元素不是字典，类型: {type(first)}, 值: {str(first)[:100]}")
                            return []
                    return items
            # 尝试找到第一个列表值
            for v in result.values():
                if isinstance(v, list) and len(v) > 0:
                    if isinstance(v[0], dict):
                        return v
            logger.warning(f"未找到有效的题目列表，响应键: {list(result.keys())}")
            return []
        if isinstance(result, list):
            if len(result) > 0 and isinstance(result[0], dict):
                return result
            return []
        return []

    async def rag_answer(self, question: str, context: str) -> str:
        """
        RAG 问答

        基于检索到的上下文生成回答，在上下文不足时可用自身知识补充。

        Args:
            question: 用户问题
            context: 检索到的相关上下文

        Returns:
            str: 回答文本
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个知识渊博的学习助手。请根据提供的参考资料回答用户问题。\n\n"
                    "回答原则：\n"
                    "1. 优先使用参考资料：如果参考资料中包含相关信息，请以其为主要依据\n"
                    "2. 自主知识补充：如果参考资料不足或没有相关信息，可以结合你自己的知识来回答，"
                    "但请说明这部分是基于你的知识补充的\n"
                    "3. 诚实标注：如果回答中既有参考资料的内容，也有你自己的知识，请尽量区分\n"
                    "4. 回答应详细有用：不要简单地回复没有相关信息，而是尽力提供有价值的回答\n"
                    "5. 适当引用：回答中可引用参考资料中的原文来增强可信度"
                ),
            },
            {
                "role": "user",
                "content": f"参考资料：\n{context}\n\n问题：{question}",
            },
        ]
        return await self.chat(messages, temperature=0.5, max_tokens=2048, scene="rag_answer")

    def create_understanding_session(self) -> ConversationSession:
        """
        创建知识卡片提取的多轮对话会话

        所有章节在同一对话窗口内依次送入，LLM 可以参考之前已提取的知识点，
        避免不同章节重复提取相同概念。

        Returns:
            ConversationSession: 知识提取对话会话
        """
        system_prompt = (
            "你是一个专业的知识提取助手。我将一次给你一个或多个章节的内容，"
            "请为每个章节生成摘要并提取关键知识点。\n\n"
            "知识点类型说明：\n"
            "- concept: 概念类，需要理解记忆的知识点\n"
            "- formula: 公式类，数学公式、化学方程式等\n"
            "- qa: 问答对，以问答形式呈现的知识\n"
            "- definition: 定义类，需要精确记忆的定义\n\n"
            "请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n"
            '{"chapters": [\n'
            '  {\n'
            '    "chapter_title": "章节标题",\n'
            '    "summary": "章节摘要（200字以内）",\n'
            '    "points": [\n'
            '      {"card_type": "concept", "title": "知识点标题", '
            '"content": "知识点内容", "source_text": "原始出处文本"},\n'
            '      ...\n'
            '    ]\n'
            '  },\n'
            '  ...\n'
            "]}\n\n"
            "要求：\n"
            "1. 每个知识点应独立完整，不依赖上下文也能理解\n"
            "2. source_text 应尽量引用原文\n"
            "3. 每个章节提取5-15个知识点\n"
            "4. 不要与之前已提取的知识点重复\n"
            "5. 只返回 JSON 对象，不要其他文字"
        )
        return UnderstandingSession(
            self,
            system_prompt,
            temperature=0.3,
            max_tokens=4096,
            response_format={"type": "json_object"},
            max_context_pairs=30,
            scene="extract_knowledge",
        )

    def create_question_session(self) -> ConversationSession:
        """
        创建题目生成的多轮对话会话

        所有批次在同一对话窗口内依次送入，LLM 可以参考之前已生成的题目，
        避免不同批次的题目重复或雷同。

        Returns:
            ConversationSession: 题目生成对话会话
        """
        system_prompt = (
            "你是一个专业的出题助手。我将依次给你多组知识点，"
            "请为每组知识点生成选择题。\n\n"
            "请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n"
            '{"questions": [\n'
            '  {"card_index": 1, "question_type": "choice", "difficulty": "easy", '
            '"question": "题目内容", "answer": "正确答案", '
            '"options": ["A.选项1", "B.选项2", "C.选项3", "D.选项4"], '
            '"explanation": "解析"},\n'
            '  {"card_index": 2, ...}\n'
            "]}\n\n"
            "要求：\n"
            "1. 每个知识点生成1道选择题\n"
            "2. 题目应准确考察知识点，不超出给定内容范围\n"
            "3. 选择题的干扰项应合理\n"
            "4. card_index 从1开始，对应本组知识点编号\n"
            "5. options 必须是字符串数组，每个元素格式为 '字母.内容'\n"
            "6. 不要与之前已生成的题目重复或雷同\n"
            "7. 只返回 JSON 对象，不要其他文字"
        )
        return ConversationSession(
            self,
            system_prompt,
            temperature=0.5,
            max_tokens=8192,
            response_format={"type": "json_object"},
            max_context_pairs=30,
        )

    def create_combined_analysis_session(self) -> ConversationSession:
        """
        创建联合分析的多轮对话会话

        将学习资料各章节 + 用户笔记全文依次送入,LLM 对每个章节做联合分析:
        - regular_points: 资料和用户笔记都覆盖到的知识点
        - blind_spots: 资料中有但用户笔记未覆盖到的知识点

        Returns:
            CombinedAnalysisSession: 联合分析对话会话
        """
        system_prompt = (
            "你是一个专业的学习分析助手。我将依次给你学习资料的各个章节以及用户的完整笔记，请对每个章节做联合分析。\n\n"
            "知识点类型说明：\n"
            "- concept: 概念类，需要理解记忆的知识点\n"
            "- formula: 公式类，数学公式、化学方程式等\n"
            "- qa: 问答对，以问答形式呈现的知识\n"
            "- definition: 定义类，需要精确记忆的定义\n\n"
            "请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n"
            "{\n"
            '  "chapter_title": "章节标题",\n'
            '  "regular_points": [\n'
            '    {"card_type": "concept", "title": "知识点标题", "content": "知识点内容", '
            '"source_text": "原始出处文本", "is_key_point": false, "is_difficulty": false}\n'
            "  ],\n"
            '  "blind_spots": [\n'
            '    {"card_type": "concept", "title": "盲点知识点标题", "content": "盲点内容", '
            '"source_text": "原始出处文本", "is_key_point": false, "is_difficulty": false}\n'
            "  ]\n"
            "}\n\n"
            "要求：\n"
            "1. regular_points：资料和用户笔记都覆盖到的知识点\n"
            "2. blind_spots：资料中有但用户笔记未覆盖到的知识点\n"
            "3. is_key_point/is_difficulty：根据知识点重要性和难度给出 true/false 建议\n"
            "4. 每个知识点应独立完整，不依赖上下文也能理解\n"
            "5. source_text 应尽量引用资料原文\n"
            "6. 不要与之前已提取的知识点重复\n"
            "7. 只返回 JSON 对象，不要其他文字"
        )
        return CombinedAnalysisSession(
            self,
            system_prompt,
            temperature=0.3,
            max_tokens=4096,
            response_format={"type": "json_object"},
            max_context_pairs=30,
            scene="extract_combined",
        )

    async def generate_extension_knowledge(
        self,
        card_title: str,
        card_content: str,
        material_context: str = "",
    ) -> List[Dict[str, Any]]:
        """
        基于已掌握的父卡片 + 关联资料生成进阶拓展知识点

        Args:
            card_title: 父卡片标题
            card_content: 父卡片内容
            material_context: 关联资料上下文(可选)

        Returns:
            List[Dict]: 拓展知识点列表,每个包含:
                - card_type: 类型(默认 concept)
                - title: 拓展知识点标题
                - content: 拓展知识点内容
                - source_text: 原始出处文本或空
        """
        system_prompt = (
            "你是一个专业的知识拓展助手。我将给你一个已掌握的知识点及其关联资料，请生成1-3个进阶拓展知识点。\n\n"
            "请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n"
            "{\n"
            '  "extensions": [\n'
            '    {"card_type": "concept", "title": "拓展知识点标题", "content": "拓展知识点内容", '
            '"source_text": "原始出处文本或空"}\n'
            "  ]\n"
            "}\n\n"
            "要求：\n"
            "1. 拓展知识点应在原知识点基础上有进阶、关联或深化\n"
            "2. 每个知识点应独立完整\n"
            "3. 只返回 JSON 对象，不要其他文字"
        )
        user_prompt = (
            f"## 已掌握知识点：\n标题：{card_title}\n内容：{card_content}\n\n"
            f"## 关联资料：\n{material_context}\n\n"
            f"请生成1-3个进阶拓展知识点。"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self.chat(
            messages,
            temperature=0.5,
            max_tokens=4096,
            response_format={"type": "json_object"},
            scene="generate_extension",
        )

        try:
            result = json.loads(response)
            if isinstance(result, dict):
                return result.get("extensions", [])
            return []
        except json.JSONDecodeError:
            logger.warning(f"拓展知识点生成结果 JSON 解析失败: {response[:200]}")
            return []

    async def infer_card_relations(
        self,
        cards_summary: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        批量推断卡片间语义关系(前置/后续/对比)

        Args:
            cards_summary: 卡片摘要列表,每个含 {id, title, card_type, content}
                (content 将被截断到 300 字)

        Returns:
            List[Dict]: 关系列表,每个包含:
                - card_id_a: 卡片A的id
                - card_id_b: 卡片B的id
                - relation_type: 关系类型(prerequisite/subsequent/contrast)
                - reason: 推断理由
        """
        system_prompt = (
            "你是一个专业的知识图谱构建助手。我将给你若干知识卡片的摘要，请推断它们之间的语义关系。\n\n"
            "关系类型说明：\n"
            "- prerequisite: card_a 是 card_b 的前置知识（学 a 才能懂 b）\n"
            "- subsequent: card_a 是 card_b 的后续知识（b 的延伸是 a）\n"
            "- contrast: 两张卡片内容形成对比\n\n"
            "请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n"
            "{\n"
            '  "relations": [\n'
            '    {"card_id_a": "卡片id1", "card_id_b": "卡片id2", '
            '"relation_type": "prerequisite", "reason": "推断理由"}\n'
            "  ]\n"
            "}\n\n"
            "要求：\n"
            "1. 只推断确实存在的关系，不要强行关联\n"
            "2. relation_type 必须是 prerequisite/subsequent/contrast 之一\n"
            "3. card_id_a 和 card_id_b 必须是给定卡片列表中的 id\n"
            "4. 每对卡片最多一种关系\n"
            "5. 只返回 JSON 对象，不要其他文字"
        )
        # 把 cards_summary 格式化为文本,每个卡片一行(content 截断到 300 字)
        cards_text_lines = []
        for c in cards_summary:
            cards_text_lines.append(
                f"- [id={c['id']}] {c['title']} ({c['card_type']}): {c['content'][:300]}"
            )
        user_prompt = "\n".join(cards_text_lines)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self.chat(
            messages,
            temperature=0.3,
            max_tokens=4096,
            response_format={"type": "json_object"},
            scene="infer_relations",
        )

        try:
            result = json.loads(response)
            if isinstance(result, dict):
                return result.get("relations", [])
            return []
        except json.JSONDecodeError:
            logger.warning(f"卡片关系推断结果 JSON 解析失败: {response[:200]}")
            return []
