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

logger = logging.getLogger(__name__)
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
        await self.acquire()


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
    ):
        """
        Args:
            llm_service: LLM 服务实例
            system_prompt: 系统提示词
            temperature: 采样温度
            max_tokens: 最大生成 token 数
            response_format: 响应格式约束
            max_context_pairs: 保留的最大对话轮次（1轮=1 user + 1 assistant）
        """
        self._llm = llm_service
        self._messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._response_format = response_format
        self._max_context_pairs = max_context_pairs

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
        """当前对话轮次（1轮 = 1次 ask 调用）"""
        return (len(self._messages) - 1) // 2


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

        async with self._semaphore:
            await self._rate_limiter.acquire()

            last_error = None
            for attempt in range(self._max_retries):
                try:
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        resp = await client.post(url, json=payload, headers=headers)
                        resp.raise_for_status()
                        data = resp.json()
                        return data["choices"][0]["message"]["content"]
                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"LLM API 调用失败 (attempt {attempt + 1}/{self._max_retries}, "
                        f"provider={self._provider}, model={self._model}): {e}"
                    )
                    if attempt < self._max_retries - 1:
                        if "429" in str(e):
                            delay = min(30 * (attempt + 1), 120)
                            delay = delay * (0.5 + random.random() * 0.5)
                        else:
                            delay = min(self._retry_delay * (2 ** attempt), 60)
                            delay = delay * (0.5 + random.random() * 0.5)
                        await asyncio.sleep(delay)

        raise Exception(
            f"LLM API 调用失败，重试 {self._max_retries} 次后仍出错 "
            f"(provider={self._provider}, model={self._model}): {last_error}"
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
        return await self.chat(messages, temperature=0.3, max_tokens=1024)

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
        return await self.chat(messages, temperature=0.5, max_tokens=2048)

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
        return ConversationSession(
            self,
            system_prompt,
            temperature=0.3,
            max_tokens=4096,
            response_format={"type": "json_object"},
            max_context_pairs=30,
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
