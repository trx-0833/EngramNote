"""测试批量题目生成的 API 返回格式"""
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
        {"title": "劳动报酬", "content": "甲方应按月以货币形式支付乙方工资，不得克扣或者无故拖欠。", "card_type": "concept"},
    ]

    result = await llm.generate_questions_batch(cards)
    print(f"Result type: {type(result)}")
    print(f"Result length: {len(result)}")
    for i, item in enumerate(result):
        print(f"\n--- Item {i} ---")
        print(f"Type: {type(item)}")
        if isinstance(item, dict):
            print(json.dumps(item, ensure_ascii=False, indent=2))
        else:
            print(f"Value: {item}")

asyncio.run(test())
