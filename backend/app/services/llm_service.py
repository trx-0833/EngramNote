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

共享 httpx 客户端、JSON 容错解析、速率限制与会话类已拆分至 services/llm/
子包；本模块仅保留 LLMService 并 re-export 相关公共名称，外部 import 路径不变。
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_settings
from .llm.client import (
    _response_snippet,
    _truncate_messages,
    build_llm_headers,
    close_llm_client,  # noqa: F401  re-export：main.py 经 llm_service 导入
    get_llm_client,
)
from .llm.json_parse import parse_json_tolerant, strip_json_fences  # noqa: F401  parse_json_tolerant 为 re-export
from .llm.rate_limit import RateLimiter
from .llm.sessions import CombinedAnalysisSession, ConversationSession, UnderstandingSession

logger = logging.getLogger("engramnote.llm")
settings = get_settings()


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

    # 类级共享限流器与并发闸门（所有实例共享同一令牌桶/信号量，见 docs/decisions.md#F-05）
    # 注意：_rate_limiter/_semaphore 在类定义后初始化（依赖 settings），见类下方
    _rate_limiter: Optional["RateLimiter"] = None
    _semaphore: Optional[asyncio.Semaphore] = None

    def __init__(self):
        llm_config = settings.get_llm_config()
        self._api_key = llm_config["api_key"]
        self._model = llm_config["model"]
        self._base_url = llm_config["base_url"]
        self._provider = llm_config["provider"]
        self._max_retries = settings.llm_max_retries
        self._retry_delay = settings.llm_retry_delay
        # 实例化时确保类级限流/信号量已就绪（线程安全：先创建后赋值，重复创建无害）
        if LLMService._rate_limiter is None:
            LLMService._rate_limiter = RateLimiter(max_rpm=settings.llm_max_rpm)
        if LLMService._semaphore is None:
            LLMService._semaphore = asyncio.Semaphore(3)
        self._rate_limiter = LLMService._rate_limiter
        self._semaphore = LLMService._semaphore

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
            scene: 场景标识，用于日志

        Returns:
            str: 模型生成的文本内容（JSON 场景已剥离代码围栏）
        """
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
        """
        通用聊天接口（结构化返回，见 docs/decisions.md#F-33）

        在 chat() 基础上额外返回：
        - content: 助手文本（JSON 场景已剥离代码围栏）
        - finish_reason: LLM 停止原因（"stop"=正常结束；"length"=被 max_tokens 截断）
        - truncated: finish_reason == "length" 的布尔便捷位
        - usage: token 用量

        Args:
            同 chat()

        Returns:
            dict: {"content", "finish_reason", "truncated", "usage"}
        """
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

        async with self._semaphore:
            await self._rate_limiter.acquire()

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
                    return {
                        "content": content,
                        "finish_reason": finish_reason,
                        "truncated": truncated,
                        "usage": usage,
                    }
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

        async with self._semaphore:
            await self._rate_limiter.acquire()
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
            # 不要盲目设 200000——它只是"上限"不是"目标"，超出模型硬上限部分无效，
            # 且会放大超时/成本；此处使用截断重试放大上限，实测多数场景 16K 内即可完成，见 docs/decisions.md#F-33。
            max_tokens=settings.llm_json_max_tokens_ceiling,
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
            max_tokens=settings.llm_json_max_tokens,
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
            max_tokens=settings.llm_json_max_tokens,
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
        RAG 问答：**仅依据给定资料**回答

        ## 为什么重写了提示词（overhaul-plan 阶段 2.8）

        原提示词的第 4 条写的是：

            4. 回答应详细有用：不要简单地回复没有相关信息，
               而是尽力提供有价值的回答

        这一条**在明确指示模型编造**。后果是产品最核心的承诺
        ——"基于你的资料回答"—— 无法成立：资料里没有的内容会被
        以流畅、自信的语气补出来，而用户无从分辨哪句来自自己的资料。
        对一个学习工具来说，这比"回答我没找到"有害得多。

        新提示词把"无据不答"设为默认行为，并要求逐条标注引用编号，
        使每句话都能回溯到检索到的段落。

        Args:
            question: 用户问题
            context: 检索到的相关上下文（已按 [1] [2] … 编号）

        Returns:
            str: 回答文本
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个严谨的学习助手。你的回答**只能**依据下面提供的参考资料。\n\n"
                    "硬性规则（必须遵守）：\n"
                    "1. **只依据参考资料**：不得使用参考资料之外的任何知识，"
                    "即使你确信那些知识是正确的。\n"
                    "2. **无据则明说**：如果参考资料中没有足以回答问题的信息，"
                    "直接回答「资料中没有找到相关信息」，并说明资料里实际涵盖了"
                    "什么。**不要猜测、不要补充、不要泛泛而谈**。\n"
                    "3. **逐条标注来源**：每个结论后面用 [编号] 标注它来自哪段资料，"
                    "编号对应参考资料中的段落序号。\n"
                    "4. **忠实转述**：不得改变原意，不得把资料中的条件、范围、"
                    "前提省略掉。若资料之间互相矛盾，指出矛盾而不是替用户裁决。\n"
                    "5. **区分事实与推断**：如果某句是你的推断（而非资料原文），"
                    "必须显式标注「（推断）」。\n\n"
                    "回答格式：先直接回答问题，再列出依据的段落编号。"
                ),
            },
            {
                "role": "user",
                "content": f"参考资料：\n{context}\n\n问题：{question}",
            },
        ]
        return await self.chat(messages, temperature=0.2, max_tokens=2048, scene="rag_answer")

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
            max_tokens=settings.llm_json_max_tokens,
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
            max_tokens=settings.llm_json_max_tokens,
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
            max_tokens=settings.llm_json_max_tokens,
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
            max_tokens=settings.llm_json_max_tokens,
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
            max_tokens=settings.llm_json_max_tokens,
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


    async def grade_short_answer(
        self,
        question: str,
        expected_answer: str,
        user_answer: str,
    ) -> Optional[Dict[str, Any]]:
        """简答题语义判分（overhaul-plan 阶段 3.5）

        ## 为什么需要它（L-1 的根因）

        简答题原先"不判分"：`sm2_service.grade_answer` 对 short_answer 一律返回
        `ungraded` 占位，靠用户自评兜底。更早的版本则用**字符集合重叠**打分 ——
        实测"学器"被判为"机器学习"的正确答案（见 §2.4 L-1 与附录 A.4）。

        结果是整个产品**无法验证自己是否有效**（L-3）：没有可信的判分，
        就没有可信的保持率与校准曲线。

        ## 输出为什么不是 0-100 分

        计划明确要求输出 `verdict + missing_points + misconceptions`。
        理由：一个 0-100 的数字**没有可校准的语义** ——
        模型给 62 分和 58 分意味着什么？没人能说清，也无法据它改进；
        而"缺了哪一点、误解了哪一点"是**可展示给用户、且可核对**的信息。

        ## 三档 verdict 而不是"对/错"

        - `correct`：核心含义一致（允许措辞、语序、详略差异）
        - `partial`：说对了部分，但有遗漏或不够准确
        - `incorrect`：与标准答案矛盾，或答的是别的东西

        `partial` 单独存在是必要的：二档会把"说对一半"强行归到某一侧，
        而它对"该不该缩短间隔"的决策有实质影响。

        ## 要求模型如实自查置信度

        `confidence` 低于阈值时调用方会**退回用户自评占位**，而不是勉强采信。
        这一点很关键：LLM 判分也有把握不准的时候（例如用户答案在标准答案
        之外但同样正确）。把不确定的判分当确定用，会引入系统性偏差，
        而偏差一旦进入调度就**无法事后纠正**（它已经改变了间隔）。

        Args:
            question: 题目
            expected_answer: 标准答案
            user_answer: 用户作答

        Returns:
            Optional[Dict]: 成功时含 verdict / missing_points / misconceptions
            / confidence / reason。**调用失败或解析失败时返回 None**，
            调用方必须把 None 当作"未判分"处理，而**不是**当作答错。
        """
        system_prompt = (
            "你是一个严格的阅卷老师，负责判断学生的**简答题**作答在语义上"
            "是否与标准答案一致。\n\n"
            "只做**语义等价判断**：不要引入标准答案之外的知识，"
            "也不要因为表述风格不同就判错。\n\n"
            "请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n"
            '{"verdict": "correct|partial|incorrect",\n'
            ' "missing_points": ["学生答案漏掉的关键点"],\n'
            ' "misconceptions": ["学生答案中与标准答案矛盾的说法"],\n'
            ' "confidence": 0.0,\n'
            ' "reason": "一句话说明判分理由"}\n\n'
            "判定标准：\n"
            "1. correct：核心含义与标准答案一致。**允许**措辞不同、"
            "语序不同、更简略或更详细\n"
            "2. partial：说对了部分内容，但有明显遗漏或不够准确\n"
            "3. incorrect：与标准答案矛盾，或答的是另一件事\n\n"
            "confidence 必须如实反映你的把握：\n"
            "- 含义明显一致或不一致时给高分（>=0.8）\n"
            "- 学生答案在标准答案之外但可能同样正确、或表述含糊难判时给低分\n"
            "- **不要**为了显得确定而虚报高置信度"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"题目：{question}\n\n"
                    f"标准答案：{expected_answer}\n\n"
                    f"学生作答：{user_answer}"
                ),
            },
        ]

        response = None
        try:
            response = await self.chat(
                messages,
                temperature=0.1,  # 判分要稳定，不要发挥
                max_tokens=settings.llm_json_max_tokens,
                response_format={"type": "json_object"},
                scene="grade_short_answer",
            )
            result = json.loads(response)
        except json.JSONDecodeError:
            logger.warning("简答判分结果 JSON 解析失败: %s", (response or "")[:200])
            return None
        except Exception as exc:
            logger.warning("简答判分调用失败: %s", exc)
            return None

        if not isinstance(result, dict):
            return None

        verdict = str(result.get("verdict") or "").strip().lower()
        if verdict not in ("correct", "partial", "incorrect"):
            # 模型没按约定返回三档之一 —— 不猜、也不兜底成某一档
            logger.warning("简答判分返回了未约定的 verdict: %r", result.get("verdict"))
            return None

        def _str_list(key: str) -> list:
            value = result.get(key)
            if not isinstance(value, list):
                return []
            return [str(v).strip() for v in value if str(v).strip()]

        try:
            confidence = float(result.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))  # 裁剪：模型偶尔给 1.5 或 -0.2

        return {
            "verdict": verdict,
            "missing_points": _str_list("missing_points"),
            "misconceptions": _str_list("misconceptions"),
            "confidence": confidence,
            "reason": str(result.get("reason") or "").strip(),
        }
