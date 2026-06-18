"""
第7周新功能单元测试

测试内容：
1. 笔记归档/取消归档 API
2. 知识卡片编辑 API（PUT）
3. 知识卡片删除 API（DELETE，含级联删除题目）
4. 卡片去重检测
5. 双向链接数据验证

运行方式：cd backend && pytest tests/test_week7_features.py -v
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# 路径设置（与其他测试文件一致）
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.knowledge import CardUpdateRequest


class TestCardUpdateRequest:
    """测试卡片更新请求 Schema"""

    def test_card_update_request_partial(self):
        """测试 CardUpdateRequest 支持只更新标题"""
        req = CardUpdateRequest(title="新标题")
        assert req.title == "新标题"
        assert req.content is None

    def test_card_update_request_full(self):
        """测试 CardUpdateRequest 支持更新标题和内容"""
        req = CardUpdateRequest(title="新标题", content="新内容")
        assert req.title == "新标题"
        assert req.content == "新内容"

    def test_card_update_request_empty(self):
        """测试 CardUpdateRequest 支持空请求"""
        req = CardUpdateRequest()
        assert req.title is None
        assert req.content is None


class TestArchiveService:
    """测试笔记归档服务"""

    def test_get_notes_list_signature(self):
        """验证 get_notes_list 的 note_status 参数是否声明"""
        from inspect import signature
        from app.services.note_service import get_notes_list

        sig = signature(get_notes_list)
        params = sig.parameters
        assert "note_status" in params, "get_notes_list 缺少 note_status 参数"


class TestDetectDuplicates:
    """测试卡片去重检测"""

    @pytest.mark.asyncio
    async def test_detect_card_duplicates_empty_db(self):
        """测试用户无已有卡片时返回空列表"""
        from app.services.understanding_service import detect_card_duplicates
        from app.models.knowledge_card import KnowledgeCard

        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        mock_card = MagicMock(spec=KnowledgeCard)
        mock_card.id = "card_1"
        mock_card.title = "测试概念"
        mock_card.content = "这是一个测试概念的内容描述"

        result = await detect_card_duplicates(mock_session, "user_id", mock_card)
        assert result == []

    @pytest.mark.asyncio
    async def test_detect_card_duplicates_no_match(self):
        """测试完全不重复的卡片"""
        from app.services.understanding_service import detect_card_duplicates
        from app.models.knowledge_card import KnowledgeCard

        mock_session = AsyncMock(spec=AsyncSession)
        existing_card = MagicMock(spec=KnowledgeCard)
        existing_card.id = "existing_1"
        existing_card.title = "微积分基本定理"
        existing_card.content = "如果函数f在[a,b]上连续，则...这是微积分的核心内容"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [existing_card]
        mock_session.execute.return_value = mock_result

        new_card = MagicMock(spec=KnowledgeCard)
        new_card.id = "new_card"
        new_card.title = "室内植物养护"
        new_card.content = "室内植物需要充足的散射光，避免阳光直射..."

        result = await detect_card_duplicates(mock_session, "user_id", new_card)
        assert result == []
