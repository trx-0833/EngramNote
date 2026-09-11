"""LLM 场景方法（overhaul-plan 阶段 4.1 收尾）

## 这里放什么

`LLMService` 上的 14 个**业务方法**：每个方法负责"把提示词 + 数据组装成一次
请求，再把模型的回答解析成结构化结果"。提示词本身在 `prompts.py`，
调用策略（重试/限流/配额/缓存/记账）在 `gateway.py`。

拆出来的理由是**三类关注点已经在 4.1 里被拆开了**，但业务方法仍和
"接线"挤在同一个文件：`llm_service.py` 里既有构造与转发（读一次就懂），
又有 500 行解析逻辑（改动频繁、需要逐场景对照）。放在一起的后果是
改任何一处都要在两种阅读模式之间切换。

## 为什么用混入（mixin）而不是模块级函数

调用方（15 个模块 + 前端 API）写的是 `service.generate_questions(...)`。
改成 `scenes.generate_questions(service, ...)` 要动所有调用点，
而**收益完全相同** —— 这是 4.1 搬迁时定下的原则：能不动调用方就不动。

## 约束

本模块**不得**出现传输与治理的符号（`httpx`、`get_llm_client`、`record_call`、
`asyncio.sleep`、限流/信号量）。要发请求就 `await self.chat(...)` ——
那条路径上的重试、限流、配额、缓存、记账全部由网关负责。
`tests/test_llm_gateway.py::TestSingleEntryPoint` 会静态检查这一点。
"""

import json
import logging
from typing import Any, Dict, List, Optional

from ...config import get_settings
from .prompts import (
    COMBINED_ANALYSIS_SYSTEM_PROMPT,
    QUESTION_SYSTEM_PROMPT,
    UNDERSTANDING_SYSTEM_PROMPT,
    extract_knowledge_points_messages,
    generate_extension_knowledge_messages,
    generate_questions_batch_messages,
    generate_questions_messages,
    grade_short_answer_messages,
    infer_card_relations_messages,
    rag_answer_messages,
    summarize_chapter_messages,
)
from .sessions import CombinedAnalysisSession, ConversationSession, UnderstandingSession

logger = logging.getLogger("engramnote.llm")
settings = get_settings()


class SceneMethods:
    """LLM 的业务场景方法（由 `LLMService` 混入）

    这些方法只依赖 `self.chat(...)` / `self.chat_detailed(...)`，
    因此任何持有网关的类都可以混入它们（测试里的假服务同理）。
    """

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

        messages = summarize_chapter_messages(chapter_title, chapter_content)
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

        messages = extract_knowledge_points_messages(chapter_title, chapter_content)

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

        messages = generate_questions_messages(card_title, card_content, card_type, types_str)

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

        messages = generate_questions_batch_messages(cards_text, types_str, len(cards))

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

        # 阶段 4.11：这里原有 3 条 `logger.info` 调试日志
        # （打印原始响应类型、每个候选键的值类型与长度、第一个元素的键）。
        # 它们是在这段解析逻辑反复调不通时加的排查脚手架，**每次出题都会打印**，
        # 而内容对生产运维没有任何意义（既不是错误，也不是可聚合的指标）。
        # 保留的是两条 warning：它们指向"模型返回的结构不符合约定"，
        # 那是需要有人知道的真实异常。
        if isinstance(result, dict):
            for key in ["questions", "items", "data"]:
                if key in result:
                    items = result[key]
                    if isinstance(items, list) and len(items) > 0:
                        first = items[0]
                        if isinstance(first, dict):
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
        messages = rag_answer_messages(question, context)
        return await self.chat(messages, temperature=0.2, max_tokens=2048, scene="rag_answer")

    def create_understanding_session(self) -> ConversationSession:
        """
        创建知识卡片提取的多轮对话会话

        所有章节在同一对话窗口内依次送入，LLM 可以参考之前已提取的知识点，
        避免不同章节重复提取相同概念。

        Returns:
            ConversationSession: 知识提取对话会话
        """
        system_prompt = UNDERSTANDING_SYSTEM_PROMPT
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
        system_prompt = QUESTION_SYSTEM_PROMPT
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
        system_prompt = COMBINED_ANALYSIS_SYSTEM_PROMPT
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
        messages = generate_extension_knowledge_messages(card_title, card_content, material_context)

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
        messages = infer_card_relations_messages(cards_summary)

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
        messages = grade_short_answer_messages(question, expected_answer, user_answer)

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
