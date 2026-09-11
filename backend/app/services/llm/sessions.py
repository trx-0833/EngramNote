"""LLM 多轮对话会话类

- ConversationSession：多轮对话会话，在同一窗口内累积消息
- UnderstandingSession：知识提取专用会话，用轻量级标题列表替代完整历史原文
- CombinedAnalysisSession：联合分析专用会话，用轻量级标题列表替代完整历史原文
"""

import json
import logging
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    # 仅供类型标注使用（避免 llm_service ←→ sessions 的循环导入）。
    # 三个会话类的 __init__ 都把 llm_service 标注为 "LLMService"（前向引用字符串），
    # 不导入该名字会让静态检查报 F821 undefined name；
    # 放在 TYPE_CHECKING 下即可让类型检查器看到，又不产生运行期依赖。
    from ..llm_service import LLMService

logger = logging.getLogger("engramnote.llm")


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
        # 记录最近一次调用的截断信号(finish_reason=length)，见 docs/decisions.md#F-33
        self._last_truncated: bool = False
        self._last_finish_reason: str = ""

    @property
    def last_truncated(self) -> bool:
        """最近一次 ask() 是否因 max_tokens 截断（见 docs/decisions.md#F-33）"""
        return self._last_truncated

    async def ask(self, user_content: str, max_tokens: Optional[int] = None) -> str:
        """
        知识提取专用 ask:不累积历史原文,只追加已提取标题列表

        Args:
            user_content: 当前批次的章节合并内容(由调用方构建)
            max_tokens: 本次调用的输出上限覆盖值(截断重试时可提大,默认用会话配置，见 docs/decisions.md#F-33)

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

        # 使用 chat_detailed 捕获 finish_reason，便于调用方感知截断并放大重试，见 docs/decisions.md#F-33
        meta = await self._llm.chat_detailed(
            messages,
            temperature=self._temperature,
            max_tokens=(max_tokens or self._max_tokens),
            response_format=self._response_format,
            scene=self._scene,
        )
        response = meta["content"]
        self._last_truncated = meta.get("truncated", False)
        self._last_finish_reason = meta.get("finish_reason", "")

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
        # 记录最近一次调用的截断信号(finish_reason=length)，见 docs/decisions.md#F-33
        self._last_truncated: bool = False
        self._last_finish_reason: str = ""

    @property
    def last_truncated(self) -> bool:
        """最近一次 ask() 是否因 max_tokens 截断（见 docs/decisions.md#F-33）"""
        return self._last_truncated

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