"""提示词"逐字节不变"护栏（阶段 4.1 收尾：把提示词搬进 `services/llm/prompts.py`）

## 为什么需要这个文件

搬移提示词这件事的风险**不在功能**，而在**缓存键**：阶段 4.7 的响应缓存以
完整输入（含 messages 的每一个字符）做 sha256。搬移时少一个空格、多一个换行，
所有既有缓存条目就全部失效 —— 而失效的表现是"缓存命中率变成 0"，
不是任何显式报错（附录 AF.10 记过同一类"写得进读不出"的坑）。

因此这里在搬移**之前**算好每个场景真正送给模型的那串 messages 的摘要，
搬移**之后**必须一字不差。它同时也是"以后改提示词要 consciously 更新摘要"
的提醒：改提示词不是不能改，而是不该**顺手**改。

## 摘要怎么算

`sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True))`，
与 `llm_cache_service.cache_key` 的口径一致（同一份输入 → 同一个键）。
"""

import hashlib
import json
from typing import Any, Dict, List, Tuple

import pytest

MESSAGES = [{"role": "user", "content": "什么是浮充？"}]

#: 每个场景"送给模型的完整输入"的摘要 + 当时的**提示词版本**；搬移/改动提示词时
#: 两者一起动。
#:
#: ## 为什么摘要与版本必须成对记录（阶段 4.6 之后）
#:
#: - 只改文本 → 本文件的用例失败（摘要对不上）；
#: - 改了文本、更新了摘要，却忘了升版本 → `test_prompt_version.py` 失败；
#: - 改文本 + 更新摘要 + 升版本 → 全绿，而这正是"有意识地换了一版"。
#:
#: 于是"版本忘了改"不再可能发生 —— 这是手工维护版本号能被接受的前提。
#:
#: ## 这些摘要的来历
#:
#: 在**搬移之前**（2026-09-11，提示词还在 `llm_service.py` 里）由本文件自己算出，
#: 因此它们证明的是"搬移没改一个字符"，而不是"搬移之后看起来一样"。
GOLDEN: Dict[str, Tuple[str, str]] = {
    "summarize_chapter": ("1", "1676683d964a95b30d7d0464fac271d3"),
    "extract_knowledge_points": ("1", "8a4ce09e0ed4c678526818d985386ba5"),
    "generate_questions": ("1", "37d12ca3f8c4325495230a5c339c41c0"),
    "generate_questions_batch": ("1", "1a1fff6eeee63720de192767e9d2e8f3"),
    "rag_answer": ("2", "6d061482276d7456301fa503ef994bfd"),
    "understanding_session": ("1", "9577b01fc4cfff48f742b8c7ee3af999"),
    "question_session": ("1", "52bd5b795273d92c2ecbb1c4b7ae5934"),
    "combined_analysis_session": ("1", "ec2a3bc20310061950c771c4fc1d0a3d"),
    "generate_extension_knowledge": ("1", "8738f8fa2e79dfa82427505b18855efa"),
    "infer_card_relations": ("1", "5df70018d23f66731140117942b4c4b9"),
    "grade_short_answer": ("1", "cd253a67acaa8b11618001edcabc51b0"),
}


class _RecordingGateway:
    """记录每次调用收到的 messages，并按场景返回可解析的假响应"""

    def __init__(self):
        self.calls: List[List[Dict[str, str]]] = []

    api_key = "fake-key"
    model = "fake-model"
    base_url = "https://fake.invalid"
    provider = "fake"
    max_retries = 1
    retry_delay = 0.0

    #: 场景 → 假响应（JSON 场景给可解析的最小结构）
    RESPONSES = {
        "summarize_chapter": "本章讲的是浮充。",
        "extract_knowledge": "[]",
        "generate_questions": "[]",
        "rag_answer": "资料中没有找到相关信息。",
        "generate_extension": '{"extensions": []}',
        "infer_relations": '{"relations": []}',
        "grade_short_answer": '{"verdict": "correct", "confidence": 0.9, "reason": "一致"}',
    }

    def _record(self, messages, scene):
        self.calls.append([dict(m) for m in messages])
        return self.RESPONSES.get(scene or "", "OK")

    async def chat(self, messages, temperature=0.7, max_tokens=4096,
                   response_format=None, scene=None) -> str:
        return self._record(messages, scene)

    async def chat_detailed(self, messages, temperature=0.7, max_tokens=4096,
                            response_format=None, scene=None) -> Dict[str, Any]:
        return {
            "content": self._record(messages, scene),
            "finish_reason": "stop", "truncated": False, "usage": {},
        }

    async def chat_stream(self, messages, scene="rag_answer_stream"):
        self._record(messages, scene)
        yield "OK"


def _digest(all_calls: List[Any]) -> str:
    payload = json.dumps(all_calls, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 各场景的调用方式（参数固定，才能得到稳定的摘要）
# ---------------------------------------------------------------------------

BATCH_CARDS = [
    {"title": "浮充", "content": "浮充是蓄电池的一种运行方式。", "card_type": "concept"},
    {"title": "均充", "content": "均充是均衡充电。", "card_type": "concept"},
]

RELATION_CARDS = [
    {"id": "c1", "title": "浮充", "card_type": "concept", "content": "浮充内容"},
    {"id": "c2", "title": "均充", "card_type": "concept", "content": "均充内容"},
]


async def _summarize_chapter(service):
    await service.summarize_chapter("第一章 绪论", "章节正文内容")


async def _extract_knowledge_points(service):
    await service.extract_knowledge_points("第一章 绪论", "章节正文内容")


async def _generate_questions(service):
    await service.generate_questions("浮充", "浮充内容", "concept")


async def _generate_questions_batch(service):
    await service.generate_questions_batch(BATCH_CARDS)


async def _rag_answer(service):
    await service.rag_answer("什么是浮充？", "[1] 浮充是蓄电池的一种运行方式。")


async def _generate_extension_knowledge(service):
    await service.generate_extension_knowledge("浮充", "浮充内容", "关联资料")


async def _infer_card_relations(service):
    await service.infer_card_relations(RELATION_CARDS)


async def _grade_short_answer(service):
    await service.grade_short_answer("什么是浮充？", "一种运行方式", "浮充是一种运行方式")


async def _understanding_session(service):
    return service.create_understanding_session()


async def _question_session(service):
    return service.create_question_session()


async def _combined_analysis_session(service):
    return service.create_combined_analysis_session()


CASES = {
    "summarize_chapter": _summarize_chapter,
    "extract_knowledge_points": _extract_knowledge_points,
    "generate_questions": _generate_questions,
    "generate_questions_batch": _generate_questions_batch,
    "rag_answer": _rag_answer,
    "generate_extension_knowledge": _generate_extension_knowledge,
    "infer_card_relations": _infer_card_relations,
    "grade_short_answer": _grade_short_answer,
    "understanding_session": _understanding_session,
    "question_session": _question_session,
    "combined_analysis_session": _combined_analysis_session,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(CASES))
async def test_prompt_sent_to_model_is_byte_identical(name):
    """★ 每个场景送给模型的输入必须与搬移前**逐字节**相同

    失败时的正确反应不是"更新 GOLDEN 让它变绿"，而是先确认这次改动
    **是否有意**修改了提示词 —— 有意改就同时接受"缓存全部失效"这个代价。
    """
    from app.services.llm_service import LLMService

    fake = _RecordingGateway()
    service = LLMService(gateway=fake)
    result = await CASES[name](service)

    if result is not None and hasattr(result, "_messages"):
        # 会话类场景：system prompt 就是它携带的第一条消息
        calls = [result._messages]
    else:
        calls = fake.calls

    actual = _digest(calls)
    expected_version, expected = GOLDEN[name]
    if not expected:  # 首次生成（或新增场景）时把实际值打印出来
        pytest.fail(f"{name} 的摘要未记录，请填入 GOLDEN：('1', {actual!r})")
    assert actual == expected, (
        f"{name} 送给模型的输入变了 —— 缓存键会全部失效，"
        f"并且这属于**换了一版提示词**（当前版本 {expected_version}）。"
        f"\n  期望 {expected}\n  实际 {actual}"
        f"\n  有意改动请同时：更新本表摘要 + 升 prompts.PROMPT_VERSIONS 里的版本号"
    )
