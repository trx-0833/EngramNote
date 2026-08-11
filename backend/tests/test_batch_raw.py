"""测试批量题目生成的 API 返回格式 - 直接打印原始响应"""
import asyncio
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def test():
    from app.services.llm_service import LLMService
    llm = LLMService()

    # 模拟3个卡片
    cards = [
        {"title": "劳动合同期限", "content": "劳动合同分为固定期限、无固定期限和以完成一定工作任务为期限三种类型。", "card_type": "concept"},
        {"title": "试用期", "content": "试用期是指包含在劳动合同期限内，用人单位与劳动者为相互了解、考察对方而约定的不超过六个月的考察期。", "card_type": "definition"},
    ]

    # 直接调用 chat 方法，获取原始响应
    from app.config import Settings
    settings = Settings()
    config = settings.get_llm_config()

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个专业的出题助手。请根据给定的 2 个知识点，"
                "为每个知识点生成1道选择题。\n\n"
                "请以 JSON 格式返回，格式如下：\n"
                '{"questions": [{"card_index": 1, "question_type": "choice", "difficulty": "easy", '
                '"question": "题目内容", "answer": "正确答案", '
                '"options": ["A. 选项1", "B. 选项2", "C. 选项3", "D. 选项4"], '
                '"explanation": "解析"}]}\n\n'
                "要求：\n"
                "1. 每个知识点生成1道题，共2道题\n"
                "2. 题目应准确考察知识点\n"
                "3. 只返回 JSON，不要其他文字"
            ),
        },
        {
            "role": "user",
            "content": "--- 知识点 1 ---\n标题：劳动合同期限\n类型：concept\n内容：劳动合同分为固定期限、无固定期限和以完成一定工作任务为期限三种类型。\n\n--- 知识点 2 ---\n标题：试用期\n类型：definition\n内容：试用期是指包含在劳动合同期限内，用人单位与劳动者为相互了解、考察对方而约定的不超过六个月的考察期。",
        },
    ]

    response = await llm.chat(
        messages,
        temperature=0.5,
        max_tokens=4096,
        response_format={"type": "json_object"},
    )

    print("=== 原始响应 ===")
    print(response[:2000])
    print("\n=== 解析后 ===")
    result = json.loads(response)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])

asyncio.run(test())
