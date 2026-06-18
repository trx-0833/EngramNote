"""
ConversationSession 多轮对话模式单元测试

测试内容：
1. ConversationSession 消息累积
2. ConversationSession 上下文裁剪
3. ConversationSession 对话轮次计数
4. _parse_understanding_response JSON 解析容错
5. _parse_questions_response JSON 解析容错
6. 知识提取会话的 system prompt 验证
7. 题目生成会话的 system prompt 验证
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ---- ConversationSession 测试 ----


def test_conversation_session_init():
    """测试 ConversationSession 初始化"""
    from app.services.llm_service import ConversationSession

    mock_llm = MagicMock()
    session = ConversationSession(
        mock_llm,
        system_prompt="你是助手",
        temperature=0.5,
        max_tokens=2048,
        max_context_pairs=10,
    )

    assert len(session._messages) == 1
    assert session._messages[0]["role"] == "system"
    assert session._messages[0]["content"] == "你是助手"
    assert session._temperature == 0.5
    assert session._max_tokens == 2048
    assert session.message_count == 1
    assert session.turn_count == 0


@pytest.mark.asyncio
async def test_conversation_session_ask_accumulates_messages():
    """测试 ask() 方法累积消息"""
    from app.services.llm_service import ConversationSession

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value='{"points": []}')

    session = ConversationSession(mock_llm, system_prompt="你是助手")

    # 第一次 ask
    result1 = await session.ask("第一个问题")
    assert result1 == '{"points": []}'
    assert session.message_count == 3  # system + user + assistant
    assert session.turn_count == 1

    # 第二次 ask
    result2 = await session.ask("第二个问题")
    assert session.message_count == 5  # system + 2*(user + assistant)
    assert session.turn_count == 2

    # 验证 chat 被调用时传入了累积的 messages
    calls = mock_llm.chat.call_args_list
    assert len(calls) == 2

    # 注意：chat 接收的是 _messages 的引用，mock 记录的是最终状态
    # 所以两次调用记录的都是同一个列表（最终5条消息）
    # 验证核心行为：第二次调用时 messages 包含第一轮的对话历史
    second_messages = calls[1][0][0]
    assert len(second_messages) == 5  # system + 2轮对话
    # 验证历史消息存在
    user_contents = [m["content"] for m in second_messages if m["role"] == "user"]
    assert "第一个问题" in user_contents
    assert "第二个问题" in user_contents


@pytest.mark.asyncio
async def test_conversation_session_trim():
    """测试上下文裁剪：超过 max_context_pairs 时裁剪中间对话"""
    from app.services.llm_service import ConversationSession

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value="回复")

    session = ConversationSession(
        mock_llm, system_prompt="你是助手", max_context_pairs=3
    )

    # 进行4轮对话（超过 max_context_pairs=3）
    for i in range(4):
        await session.ask(f"问题{i}")

    # 裁剪后应保留 system + 最近3轮对话
    assert session.message_count == 1 + 3 * 2  # system + 3轮
    assert session.turn_count == 3  # 注意：turn_count 基于当前消息数计算

    # 验证 system 消息保留
    assert session._messages[0]["role"] == "system"

    # 验证最近3轮对话保留
    recent_user_contents = [
        m["content"] for m in session._messages if m["role"] == "user"
    ]
    assert "问题1" in recent_user_contents
    assert "问题2" in recent_user_contents
    assert "问题3" in recent_user_contents
    # 问题0 应该被裁剪掉了
    assert "问题0" not in recent_user_contents


@pytest.mark.asyncio
async def test_conversation_session_no_trim_when_under_limit():
    """测试未超过限制时不裁剪"""
    from app.services.llm_service import ConversationSession

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value="回复")

    session = ConversationSession(
        mock_llm, system_prompt="你是助手", max_context_pairs=10
    )

    # 进行3轮对话（未超过 max_context_pairs=10）
    for i in range(3):
        await session.ask(f"问题{i}")

    # 不应裁剪
    assert session.message_count == 1 + 3 * 2  # system + 3轮
    assert session.turn_count == 3


def test_conversation_session_message_and_turn_count():
    """测试 message_count 和 turn_count 属性"""
    from app.services.llm_service import ConversationSession

    mock_llm = MagicMock()
    session = ConversationSession(mock_llm, system_prompt="你是助手")

    # 初始状态
    assert session.message_count == 1  # system
    assert session.turn_count == 0

    # 模拟添加消息
    session._messages.append({"role": "user", "content": "问题"})
    session._messages.append({"role": "assistant", "content": "回答"})
    assert session.message_count == 3
    assert session.turn_count == 1


# ---- _parse_understanding_response 测试 ----


def test_parse_understanding_response_normal():
    """测试正常 JSON 解析"""
    from app.services.understanding_service import _parse_understanding_response

    response = json.dumps({
        "summary": "这是摘要",
        "points": [
            {"card_type": "concept", "title": "概念1", "content": "内容1"},
            {"card_type": "definition", "title": "定义1", "content": "内容2"},
        ]
    })
    result = _parse_understanding_response(response)
    assert result["summary"] == "这是摘要"
    assert len(result["points"]) == 2


def test_parse_understanding_response_with_extra_text():
    """测试带多余文字的 JSON 解析"""
    from app.services.understanding_service import _parse_understanding_response

    response = '好的，以下是结果：\n{"summary": "摘要", "points": [{"card_type": "concept", "title": "t", "content": "c"}]}\n希望对你有帮助！'
    result = _parse_understanding_response(response)
    assert result["summary"] == "摘要"
    assert len(result["points"]) == 1


def test_parse_understanding_response_invalid_json():
    """测试无效 JSON 返回空结果"""
    from app.services.understanding_service import _parse_understanding_response

    result = _parse_understanding_response("这不是JSON")
    assert result["summary"] == ""
    assert result["points"] == []


def test_parse_understanding_response_alternative_keys():
    """测试替代键名（knowledge_points, items, data）"""
    from app.services.understanding_service import _parse_understanding_response

    # knowledge_points 键
    response1 = json.dumps({
        "summary": "摘要",
        "knowledge_points": [{"card_type": "concept", "title": "t", "content": "c"}]
    })
    result1 = _parse_understanding_response(response1)
    assert len(result1["points"]) == 1

    # items 键
    response2 = json.dumps({
        "summary": "摘要",
        "items": [{"card_type": "concept", "title": "t", "content": "c"}]
    })
    result2 = _parse_understanding_response(response2)
    assert len(result2["points"]) == 1


def test_parse_understanding_response_no_summary():
    """测试缺少 summary 字段"""
    from app.services.understanding_service import _parse_understanding_response

    response = json.dumps({
        "points": [{"card_type": "concept", "title": "t", "content": "c"}]
    })
    result = _parse_understanding_response(response)
    assert result["summary"] == ""
    assert len(result["points"]) == 1


# ---- _parse_questions_response 测试 ----


def test_parse_questions_response_normal():
    """测试正常 JSON 解析"""
    from app.tasks.understand_tasks import _parse_questions_response

    response = json.dumps({
        "questions": [
            {"card_index": 1, "question_type": "choice", "question": "题目1", "answer": "A"},
            {"card_index": 2, "question_type": "choice", "question": "题目2", "answer": "B"},
        ]
    })
    result = _parse_questions_response(response)
    assert len(result) == 2
    assert result[0]["card_index"] == 1


def test_parse_questions_response_with_extra_text():
    """测试带多余文字的 JSON 解析"""
    from app.tasks.understand_tasks import _parse_questions_response

    response = '以下是生成的题目：\n{"questions": [{"card_index": 1, "question": "题目", "answer": "A"}]}'
    result = _parse_questions_response(response)
    assert len(result) == 1


def test_parse_questions_response_invalid_json():
    """测试无效 JSON 返回空列表"""
    from app.tasks.understand_tasks import _parse_questions_response

    result = _parse_questions_response("这不是JSON")
    assert result == []


def test_parse_questions_response_alternative_keys():
    """测试替代键名"""
    from app.tasks.understand_tasks import _parse_questions_response

    # items 键
    response = json.dumps({
        "items": [{"card_index": 1, "question": "题目", "answer": "A"}]
    })
    result = _parse_questions_response(response)
    assert len(result) == 1


def test_parse_questions_response_list_response():
    """测试直接返回列表格式"""
    from app.tasks.understand_tasks import _parse_questions_response

    response = json.dumps([
        {"card_index": 1, "question": "题目", "answer": "A"}
    ])
    result = _parse_questions_response(response)
    assert len(result) == 1


# ---- LLMService 会话工厂方法测试 ----


def test_create_understanding_session():
    """测试 create_understanding_session() 返回正确配置的会话"""
    from app.services.llm_service import LLMService, ConversationSession

    llm_service = LLMService()
    session = llm_service.create_understanding_session()

    assert isinstance(session, ConversationSession)
    assert session._temperature == 0.3
    assert session._max_tokens == 4096
    assert session._response_format == {"type": "json_object"}
    assert session._max_context_pairs == 30
    # system prompt 包含关键指令
    system_content = session._messages[0]["content"]
    assert "知识提取" in system_content
    assert "不要与之前已提取的知识点重复" in system_content
    assert "summary" in system_content
    assert "points" in system_content


def test_create_question_session():
    """测试 create_question_session() 返回正确配置的会话"""
    from app.services.llm_service import LLMService, ConversationSession

    llm_service = LLMService()
    session = llm_service.create_question_session()

    assert isinstance(session, ConversationSession)
    assert session._temperature == 0.5
    assert session._max_tokens == 8192
    assert session._response_format == {"type": "json_object"}
    assert session._max_context_pairs == 30
    # system prompt 包含关键指令
    system_content = session._messages[0]["content"]
    assert "出题助手" in system_content
    assert "不要与之前已生成的题目重复或雷同" in system_content
    assert "questions" in system_content


@pytest.mark.asyncio
async def test_understanding_session_full_flow():
    """测试知识提取会话的完整流程（模拟3个章节）"""
    from app.services.llm_service import LLMService, ConversationSession

    mock_llm = MagicMock()
    # 模拟3个章节的 LLM 响应
    responses = [
        json.dumps({"summary": "章节1摘要", "points": [{"card_type": "concept", "title": "概念1", "content": "内容1"}]}),
        json.dumps({"summary": "章节2摘要", "points": [{"card_type": "definition", "title": "定义1", "content": "内容2"}]}),
        json.dumps({"summary": "章节3摘要", "points": [{"card_type": "qa", "title": "问答1", "content": "内容3"}]}),
    ]
    mock_llm.chat = AsyncMock(side_effect=responses)

    session = ConversationSession(
        mock_llm,
        system_prompt="你是知识提取助手",
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    # 模拟3个章节的处理
    for i in range(3):
        result = await session.ask(f"章节{i+1}内容")
        parsed = json.loads(result)
        assert "summary" in parsed
        assert "points" in parsed

    # 验证对话轮次
    assert session.turn_count == 3

    # 验证第3次调用时 LLM 能看到前2轮的对话
    third_call_messages = mock_llm.chat.call_args_list[2][0][0]
    # 注意：chat 接收的是 _messages 的引用，mock 记录的是最终状态
    # 最终状态：system + 3轮对话 = 7条消息
    assert len(third_call_messages) == 7  # system + 3*(user + assistant)
    # 验证历史消息存在
    user_messages = [m for m in third_call_messages if m["role"] == "user"]
    assert len(user_messages) == 3  # 3个 user 消息


@pytest.mark.asyncio
async def test_question_session_full_flow():
    """测试题目生成会话的完整流程（模拟3个批次）"""
    from app.services.llm_service import LLMService, ConversationSession

    mock_llm = MagicMock()
    responses = [
        json.dumps({"questions": [{"card_index": 1, "question": "题目1", "answer": "A", "options": ["A.1", "B.2"]}]}),
        json.dumps({"questions": [{"card_index": 1, "question": "题目2", "answer": "B", "options": ["A.1", "B.2"]}]}),
        json.dumps({"questions": [{"card_index": 1, "question": "题目3", "answer": "C", "options": ["A.1", "B.2", "C.3"]}]}),
    ]
    mock_llm.chat = AsyncMock(side_effect=responses)

    session = ConversationSession(
        mock_llm,
        system_prompt="你是出题助手",
        temperature=0.5,
        response_format={"type": "json_object"},
    )

    # 模拟3个批次的处理
    for i in range(3):
        result = await session.ask(f"批次{i+1}知识点")
        parsed = json.loads(result)
        assert "questions" in parsed

    # 验证对话轮次
    assert session.turn_count == 3

    # 验证第3次调用时 LLM 能看到前2轮的对话
    third_call_messages = mock_llm.chat.call_args_list[2][0][0]
    # 注意：chat 接收的是 _messages 的引用，mock 记录的是最终状态
    # 最终状态：system + 3轮对话 = 7条消息
    assert len(third_call_messages) == 7  # system + 3*(user + assistant)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
