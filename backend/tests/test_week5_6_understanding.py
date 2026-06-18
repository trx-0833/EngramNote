"""
Week5-6 AI 理解管道测试

验证开发时间表中 Week5-6 新增功能的可靠性：
1. 数据库模型（KnowledgeCard、QuizItem、NoteStatus.learning_failed）
2. Pydantic Schema（knowledge.py）
3. config.py GLM 配置 + debug 模式 LLM 选择
4. .env GLM_MODEL 大小写修正
5. LLM 服务（API 调用、重试机制）
6. 章节切分服务（split_into_chapters）
7. 理解管道 API（触发理解、查询状态、知识卡片、问答、题目）
8. Celery 任务注册
9. 前端 API 客户端（类型定义和函数签名）
10. 前端路由和导航

运行方式：cd backend && pytest tests/test_week5_6_understanding.py -v
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 路径设置
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"

_backend_dir = str(BACKEND_DIR)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


# ===========================================================================
# 1. 数据库模型测试
# ===========================================================================


class TestKnowledgeCardModel:
    """验证 KnowledgeCard 模型定义正确"""

    def test_card_type_enum_values(self):
        """CardType 枚举应包含 concept/formula/qa/definition"""
        from app.models.knowledge_card import CardType

        assert CardType.concept == "concept"
        assert CardType.formula == "formula"
        assert CardType.qa == "qa"
        assert CardType.definition == "definition"

    def test_knowledge_card_table_name(self):
        """KnowledgeCard 表名应为 knowledge_cards"""
        from app.models.knowledge_card import KnowledgeCard

        assert KnowledgeCard.__tablename__ == "knowledge_cards"

    def test_knowledge_card_has_required_fields(self):
        """KnowledgeCard 应包含所有必需字段"""
        from app.models.knowledge_card import KnowledgeCard

        # 检查字段存在性
        mapper = KnowledgeCard.__table__.columns
        required_fields = [
            "id", "user_id", "note_id", "card_type", "title",
            "content", "summary", "chapter_title", "source_text", "metadata",
            "created_at", "updated_at",
        ]
        for field in required_fields:
            # metadata_ 在数据库中映射为 metadata
            db_field = "metadata" if field == "metadata" else field
            assert db_field in mapper, f"KnowledgeCard 缺少字段: {field}"

    def test_knowledge_card_foreign_keys(self):
        """KnowledgeCard 应有 user_id 和 note_id 外键"""
        from app.models.knowledge_card import KnowledgeCard

        mapper = KnowledgeCard.__table__.columns
        user_id_col = mapper["user_id"]
        note_id_col = mapper["note_id"]

        # 检查外键存在
        assert len(user_id_col.foreign_keys) > 0, "user_id 应有外键约束"
        assert len(note_id_col.foreign_keys) > 0, "note_id 应有外键约束"


class TestQuizItemModel:
    """验证 QuizItem 模型定义正确"""

    def test_question_type_enum_values(self):
        """QuestionType 枚举应包含 choice/fill_blank/short_answer"""
        from app.models.quiz_item import QuestionType

        assert QuestionType.choice == "choice"
        assert QuestionType.fill_blank == "fill_blank"
        assert QuestionType.short_answer == "short_answer"

    def test_difficulty_level_enum_values(self):
        """DifficultyLevel 枚举应包含 easy/medium/hard"""
        from app.models.quiz_item import DifficultyLevel

        assert DifficultyLevel.easy == "easy"
        assert DifficultyLevel.medium == "medium"
        assert DifficultyLevel.hard == "hard"

    def test_quiz_item_table_name(self):
        """QuizItem 表名应为 quiz_items"""
        from app.models.quiz_item import QuizItem

        assert QuizItem.__tablename__ == "quiz_items"

    def test_quiz_item_has_required_fields(self):
        """QuizItem 应包含所有必需字段"""
        from app.models.quiz_item import QuizItem

        mapper = QuizItem.__table__.columns
        required_fields = [
            "id", "user_id", "card_id", "note_id", "question_type",
            "difficulty", "question", "answer", "options", "explanation",
            "metadata", "created_at", "updated_at",
        ]
        for field in required_fields:
            assert field in mapper, f"QuizItem 缺少字段: {field}"

    def test_quiz_item_foreign_keys(self):
        """QuizItem 应有 user_id、card_id 和 note_id 外键"""
        from app.models.quiz_item import QuizItem

        mapper = QuizItem.__table__.columns
        assert len(mapper["user_id"].foreign_keys) > 0
        assert len(mapper["card_id"].foreign_keys) > 0
        assert len(mapper["note_id"].foreign_keys) > 0


class TestNoteStatusLearningFailed:
    """验证 NoteStatus 枚举添加了 learning_failed 状态"""

    def test_learning_failed_exists(self):
        """NoteStatus 应包含 learning_failed"""
        from app.models.note import NoteStatus

        assert hasattr(NoteStatus, "learning_failed")
        assert NoteStatus.learning_failed == "learning_failed"

    def test_learning_exists(self):
        """NoteStatus 应包含 learning"""
        from app.models.note import NoteStatus

        assert hasattr(NoteStatus, "learning")
        assert NoteStatus.learning == "learning"

    def test_archived_exists(self):
        """NoteStatus 应包含 archived"""
        from app.models.note import NoteStatus

        assert hasattr(NoteStatus, "archived")
        assert NoteStatus.archived == "archived"


class TestModelsRegistered:
    """验证新模型已在 __init__.py 中注册"""

    def test_knowledge_card_importable(self):
        """KnowledgeCard 应能从 models 包导入"""
        from app.models import KnowledgeCard
        assert KnowledgeCard is not None

    def test_quiz_item_importable(self):
        """QuizItem 应能从 models 包导入"""
        from app.models import QuizItem
        assert QuizItem is not None

    def test_card_type_importable(self):
        """CardType 应能从 models 包导入"""
        from app.models import CardType
        assert CardType is not None

    def test_question_type_importable(self):
        """QuestionType 应能从 models 包导入"""
        from app.models import QuestionType
        assert QuestionType is not None

    def test_difficulty_level_importable(self):
        """DifficultyLevel 应能从 models 包导入"""
        from app.models import DifficultyLevel
        assert DifficultyLevel is not None


# ===========================================================================
# 2. Pydantic Schema 测试
# ===========================================================================


class TestKnowledgeSchemas:
    """验证 knowledge.py 中的 Schema 定义正确"""

    def test_knowledge_card_response_fields(self):
        """KnowledgeCardResponse 应包含所有字段"""
        from app.schemas.knowledge import KnowledgeCardResponse

        fields = KnowledgeCardResponse.model_fields
        expected = [
            "id", "user_id", "note_id", "card_type", "title", "content",
            "summary", "chapter_title", "source_text", "metadata_",
            "created_at", "updated_at",
        ]
        for field in expected:
            assert field in fields, f"KnowledgeCardResponse 缺少字段: {field}"

    def test_quiz_item_response_fields(self):
        """QuizItemResponse 应包含所有字段"""
        from app.schemas.knowledge import QuizItemResponse

        fields = QuizItemResponse.model_fields
        expected = [
            "id", "user_id", "card_id", "note_id", "question_type",
            "difficulty", "question", "answer", "options", "explanation",
            "metadata_", "created_at", "updated_at",
        ]
        for field in expected:
            assert field in fields, f"QuizItemResponse 缺少字段: {field}"

    def test_understanding_start_response(self):
        """UnderstandingStartResponse 应包含 id/status/message"""
        from app.schemas.knowledge import UnderstandingStartResponse

        fields = UnderstandingStartResponse.model_fields
        assert "id" in fields
        assert "status" in fields
        assert "message" in fields

    def test_question_request(self):
        """QuestionRequest 应包含 question 字段"""
        from app.schemas.knowledge import QuestionRequest

        req = QuestionRequest(question="什么是机器学习？")
        assert req.question == "什么是机器学习？"

    def test_question_answer_response(self):
        """QuestionAnswerResponse 应包含 question/answer/sources/provider"""
        from app.schemas.knowledge import QuestionAnswerResponse

        fields = QuestionAnswerResponse.model_fields
        assert "question" in fields
        assert "answer" in fields
        assert "sources" in fields
        assert "provider" in fields

    def test_chapter_summary(self):
        """ChapterSummary 应包含 chapter_index/chapter_title/summary/card_count"""
        from app.schemas.knowledge import ChapterSummary

        cs = ChapterSummary(
            chapter_index=0,
            chapter_title="第一章",
            summary="这是摘要",
            card_count=5,
        )
        assert cs.chapter_index == 0
        assert cs.chapter_title == "第一章"
        assert cs.summary == "这是摘要"
        assert cs.card_count == 5

    def test_list_responses_have_pagination(self):
        """列表响应应包含分页字段 items/total/page/page_size"""
        from app.schemas.knowledge import (
            KnowledgeCardListResponse,
            QuizItemListResponse,
        )

        for schema in [KnowledgeCardListResponse, QuizItemListResponse]:
            fields = schema.model_fields
            assert "items" in fields
            assert "total" in fields
            assert "page" in fields
            assert "page_size" in fields


# ===========================================================================
# 3. config.py GLM 配置 + debug 模式 LLM 选择
# ===========================================================================


class TestConfigGLMAndLLMSelection:
    """验证 GLM 配置字段和 debug 模式 LLM 自动选择"""

    def test_settings_has_glm_fields(self):
        """Settings 类应包含 glm_api_key/glm_model/glm_base_url 字段"""
        from app.config import Settings

        s = Settings()
        assert hasattr(s, "glm_api_key"), "Settings 缺少 glm_api_key 字段"
        assert hasattr(s, "glm_model"), "Settings 缺少 glm_model 字段"
        assert hasattr(s, "glm_base_url"), "Settings 缺少 glm_base_url 字段"

    def test_glm_model_default_lowercase(self):
        """glm_model 默认值应为全小写 glm-4.7-flash"""
        from app.config import Settings

        s = Settings()
        assert s.glm_model == "glm-4.7-flash", (
            f"glm_model 默认值应为 'glm-4.7-flash'，实际为 '{s.glm_model}'"
        )

    def test_glm_base_url_default(self):
        """glm_base_url 默认值应为智谱 API 地址"""
        from app.config import Settings

        s = Settings()
        assert s.glm_base_url == "https://open.bigmodel.cn/api/paas/v4"

    def test_settings_has_llm_config_fields(self):
        """Settings 类应包含 llm_max_retries/llm_retry_delay 字段"""
        from app.config import Settings

        s = Settings()
        assert hasattr(s, "llm_max_retries"), "Settings 缺少 llm_max_retries 字段"
        assert hasattr(s, "llm_retry_delay"), "Settings 缺少 llm_retry_delay 字段"

    def test_get_llm_config_method_exists(self):
        """Settings 应有 get_llm_config 方法"""
        from app.config import Settings

        s = Settings()
        assert hasattr(s, "get_llm_config"), "Settings 缺少 get_llm_config 方法"
        assert callable(s.get_llm_config), "get_llm_config 应为可调用方法"

    def test_debug_mode_returns_glm(self):
        """debug=True 时 get_llm_config 应返回 GLM 配置"""
        from app.config import Settings

        s = Settings(debug=True, glm_api_key="test-key", glm_model="glm-4.7-flash",
                     glm_base_url="https://open.bigmodel.cn/api/paas/v4",
                     deepseek_api_key="ds-key", deepseek_model="deepseek-v4-flash",
                     deepseek_base_url="https://api.deepseek.com")
        config = s.get_llm_config()
        assert config["provider"] == "glm"
        assert config["api_key"] == "test-key"
        assert config["model"] == "glm-4.7-flash"

    def test_non_debug_mode_returns_deepseek(self):
        """debug=False 时 get_llm_config 应返回 DeepSeek 配置"""
        from app.config import Settings

        s = Settings(debug=False, glm_api_key="glm-key", glm_model="glm-4.7-flash",
                     glm_base_url="https://open.bigmodel.cn/api/paas/v4",
                     deepseek_api_key="ds-key", deepseek_model="deepseek-v4-flash",
                     deepseek_base_url="https://api.deepseek.com")
        config = s.get_llm_config()
        assert config["provider"] == "deepseek"
        assert config["api_key"] == "ds-key"
        assert config["model"] == "deepseek-v4-flash"

    def test_get_llm_config_returns_required_keys(self):
        """get_llm_config 返回值应包含 api_key/model/base_url/provider"""
        from app.config import Settings

        s = Settings()
        config = s.get_llm_config()
        assert "api_key" in config
        assert "model" in config
        assert "base_url" in config
        assert "provider" in config


# ===========================================================================
# 4. .env GLM_MODEL 大小写修正
# ===========================================================================


class TestEnvGLMModelCase:
    """验证 .env 中 GLM_MODEL 为全小写"""

    def test_env_glm_model_is_lowercase(self):
        """.env 中 GLM_MODEL 应为全小写 glm-4.7-flash"""
        env_path = BACKEND_DIR / ".env"
        if not env_path.exists():
            pytest.skip(".env 文件不存在")

        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("GLM_MODEL="):
                value = line.split("=", 1)[1]
                assert value == "glm-4.7-flash", (
                    f".env 中 GLM_MODEL 应为 'glm-4.7-flash'（全小写），"
                    f"实际为 '{value}'"
                )
                return

        pytest.fail(".env 中未找到 GLM_MODEL 配置项")


# ===========================================================================
# 5. LLM 服务测试
# ===========================================================================


class TestLLMService:
    """验证 LLM 服务的基本功能"""

    def test_llm_service_init_with_debug(self):
        """debug 模式下 LLMService 应使用 GLM 配置"""
        from app.services.llm_service import LLMService

        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.get_llm_config.return_value = {
                "api_key": "glm-key",
                "model": "glm-4.7-flash",
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "provider": "glm",
            }
            mock_settings.llm_max_retries = 3
            mock_settings.llm_retry_delay = 1.0

            service = LLMService()
            assert service._provider == "glm"
            assert service._model == "glm-4.7-flash"
            assert service._api_key == "glm-key"

    def test_llm_service_init_with_production(self):
        """非 debug 模式下 LLMService 应使用 DeepSeek 配置"""
        from app.services.llm_service import LLMService

        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.get_llm_config.return_value = {
                "api_key": "ds-key",
                "model": "deepseek-v4-flash",
                "base_url": "https://api.deepseek.com",
                "provider": "deepseek",
            }
            mock_settings.llm_max_retries = 3
            mock_settings.llm_retry_delay = 1.0

            service = LLMService()
            assert service._provider == "deepseek"
            assert service._model == "deepseek-v4-flash"

    @pytest.mark.asyncio
    async def test_chat_success(self):
        """chat 方法应正确解析 API 响应"""
        from app.services.llm_service import LLMService

        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.get_llm_config.return_value = {
                "api_key": "test-key",
                "model": "test-model",
                "base_url": "https://api.test.com",
                "provider": "test",
            }
            mock_settings.llm_max_retries = 1
            mock_settings.llm_retry_delay = 0.01

            service = LLMService()

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "测试回复"}}]
            }

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                result = await service.chat([{"role": "user", "content": "你好"}])
                assert result == "测试回复"

    @pytest.mark.asyncio
    async def test_chat_retry_on_failure(self):
        """chat 方法在 API 失败时应重试"""
        from app.services.llm_service import LLMService

        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.get_llm_config.return_value = {
                "api_key": "test-key",
                "model": "test-model",
                "base_url": "https://api.test.com",
                "provider": "test",
            }
            mock_settings.llm_max_retries = 2
            mock_settings.llm_retry_delay = 0.01

            service = LLMService()

            call_count = 0

            async def mock_post(url, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("API 调用失败")
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.raise_for_status = MagicMock()
                mock_resp.json.return_value = {
                    "choices": [{"message": {"content": "重试成功"}}]
                }
                return mock_resp

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = mock_post
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                result = await service.chat([{"role": "user", "content": "你好"}])
                assert result == "重试成功"
                assert call_count == 2

    @pytest.mark.asyncio
    async def test_chat_all_retries_exhausted(self):
        """chat 方法重试耗尽后应抛出异常"""
        from app.services.llm_service import LLMService

        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.get_llm_config.return_value = {
                "api_key": "test-key",
                "model": "test-model",
                "base_url": "https://api.test.com",
                "provider": "test",
            }
            mock_settings.llm_max_retries = 1
            mock_settings.llm_retry_delay = 0.01

            service = LLMService()

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=Exception("API 持续失败"))
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                with pytest.raises(Exception, match="LLM API 调用失败"):
                    await service.chat([{"role": "user", "content": "你好"}])

    @pytest.mark.asyncio
    async def test_extract_knowledge_points_parses_json(self):
        """extract_knowledge_points 应正确解析 JSON 响应"""
        from app.services.llm_service import LLMService

        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.get_llm_config.return_value = {
                "api_key": "test-key",
                "model": "test-model",
                "base_url": "https://api.test.com",
                "provider": "test",
            }
            mock_settings.llm_max_retries = 1
            mock_settings.llm_retry_delay = 0.01

            service = LLMService()

            # 模拟 chat 方法返回 JSON
            mock_points = {
                "points": [
                    {
                        "card_type": "concept",
                        "title": "机器学习",
                        "content": "机器学习是AI的子领域",
                        "source_text": "原文段落",
                    }
                ]
            }

            with patch.object(service, "chat", new_callable=AsyncMock) as mock_chat:
                mock_chat.return_value = json.dumps(mock_points)
                result = await service.extract_knowledge_points("第一章", "内容...")

                assert len(result) == 1
                assert result[0]["card_type"] == "concept"
                assert result[0]["title"] == "机器学习"

    @pytest.mark.asyncio
    async def test_extract_knowledge_points_handles_invalid_json(self):
        """extract_knowledge_points 在 JSON 解析失败时应返回空列表"""
        from app.services.llm_service import LLMService

        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.get_llm_config.return_value = {
                "api_key": "test-key",
                "model": "test-model",
                "base_url": "https://api.test.com",
                "provider": "test",
            }
            mock_settings.llm_max_retries = 1
            mock_settings.llm_retry_delay = 0.01

            service = LLMService()

            with patch.object(service, "chat", new_callable=AsyncMock) as mock_chat:
                mock_chat.return_value = "这不是有效的JSON"
                result = await service.extract_knowledge_points("第一章", "内容...")
                assert result == []


# ===========================================================================
# 6. 章节切分服务测试
# ===========================================================================


class TestChapterSplitting:
    """验证 Markdown 章节切分功能"""

    def test_split_by_h1(self):
        """按 # 标题切分"""
        from app.services.understanding_service import split_into_chapters

        md = "# 第一章\n第一章内容\n# 第二章\n第二章内容"
        chapters = split_into_chapters(md)

        assert len(chapters) == 2
        assert chapters[0]["chapter_title"] == "第一章"
        assert chapters[1]["chapter_title"] == "第二章"

    def test_split_by_h2_and_h3(self):
        """按 ## 和 ### 标题切分"""
        from app.services.understanding_service import split_into_chapters

        md = "## 第一节\n内容1\n### 第一小节\n内容2\n## 第二节\n内容3"
        chapters = split_into_chapters(md)

        assert len(chapters) == 3
        assert chapters[0]["chapter_title"] == "第一节"
        assert chapters[1]["chapter_title"] == "第一小节"
        assert chapters[2]["chapter_title"] == "第二节"

    def test_text_before_first_heading(self):
        """标题前的文本应归入未命名章节"""
        from app.services.understanding_service import split_into_chapters

        md = "前言内容\n# 第一章\n第一章内容"
        chapters = split_into_chapters(md)

        assert len(chapters) == 2
        assert "未命名章节" in chapters[0]["chapter_title"]
        assert chapters[1]["chapter_title"] == "第一章"

    def test_empty_input(self):
        """空输入应返回空列表"""
        from app.services.understanding_service import split_into_chapters

        assert split_into_chapters("") == []
        assert split_into_chapters("   ") == []

    def test_no_headings(self):
        """无标题的文本应作为单个章节（标题为未命名章节）"""
        from app.services.understanding_service import split_into_chapters

        md = "这是一段没有标题的文本\n有多行内容"
        chapters = split_into_chapters(md)

        assert len(chapters) == 1
        assert "未命名章节" in chapters[0]["chapter_title"]

    def test_chapter_index_sequential(self):
        """章节索引应从0开始递增"""
        from app.services.understanding_service import split_into_chapters

        md = "# A\n内容A\n# B\n内容B\n# C\n内容C"
        chapters = split_into_chapters(md)

        for i, ch in enumerate(chapters):
            assert ch["chapter_index"] == i

    def test_chapter_level_recorded(self):
        """章节应记录标题层级"""
        from app.services.understanding_service import split_into_chapters

        md = "# 一级\n内容1\n## 二级\n内容2\n### 三级\n内容3"
        chapters = split_into_chapters(md)

        assert chapters[0]["level"] == 1
        assert chapters[1]["level"] == 2
        assert chapters[2]["level"] == 3

    def test_content_preserved(self):
        """章节内容应正确保留"""
        from app.services.understanding_service import split_into_chapters

        md = "# 标题\n这是内容第一行\n这是内容第二行"
        chapters = split_into_chapters(md)

        assert "内容第一行" in chapters[0]["content"]
        assert "内容第二行" in chapters[0]["content"]


# ===========================================================================
# 7. 理解管道 API 测试
# ===========================================================================


class TestUnderstandingAPI:
    """验证理解管道 API 端点定义正确"""

    def test_understanding_api_router_exists(self):
        """understanding.py 应导出 router"""
        from app.api.understanding import router
        assert router is not None

    def test_understanding_api_registered_in_router(self):
        """理解管道路由应注册在 router.py 中"""
        router_py_path = BACKEND_DIR / "app" / "api" / "router.py"
        source = router_py_path.read_text(encoding="utf-8")

        assert "understanding" in source, "router.py 中未注册 understanding 路由"
        assert "understanding_router" in source, "router.py 中未导入 understanding_router"

    def test_understanding_api_has_start_endpoint(self):
        """API 应包含触发理解端点"""
        understanding_py_path = BACKEND_DIR / "app" / "api" / "understanding.py"
        source = understanding_py_path.read_text(encoding="utf-8")

        assert "/{note_id}/start" in source, "缺少触发理解端点"

    def test_understanding_api_has_status_endpoint(self):
        """API 应包含查询状态端点"""
        understanding_py_path = BACKEND_DIR / "app" / "api" / "understanding.py"
        source = understanding_py_path.read_text(encoding="utf-8")

        assert "/{note_id}/status" in source, "缺少查询状态端点"

    def test_understanding_api_has_cards_endpoint(self):
        """API 应包含知识卡片端点"""
        understanding_py_path = BACKEND_DIR / "app" / "api" / "understanding.py"
        source = understanding_py_path.read_text(encoding="utf-8")

        assert "/cards" in source, "缺少知识卡片端点"

    def test_understanding_api_has_ask_endpoint(self):
        """API 应包含问答端点"""
        understanding_py_path = BACKEND_DIR / "app" / "api" / "understanding.py"
        source = understanding_py_path.read_text(encoding="utf-8")

        assert "/ask" in source, "缺少问答端点"

    def test_understanding_api_has_generate_questions_endpoint(self):
        """API 应包含题目生成端点"""
        understanding_py_path = BACKEND_DIR / "app" / "api" / "understanding.py"
        source = understanding_py_path.read_text(encoding="utf-8")

        assert "generate-questions" in source, "缺少题目生成端点"

    def test_understanding_api_has_questions_endpoint(self):
        """API 应包含题目列表端点"""
        understanding_py_path = BACKEND_DIR / "app" / "api" / "understanding.py"
        source = understanding_py_path.read_text(encoding="utf-8")

        assert "/questions" in source, "缺少题目列表端点"


# ===========================================================================
# 8. Celery 任务注册测试
# ===========================================================================


class TestCeleryTaskRegistration:
    """验证 Celery 任务正确注册"""

    def test_understand_tasks_in_celery_include(self):
        """celery_app.py 的 include 应包含 understand_tasks"""
        celery_app_path = BACKEND_DIR / "app" / "tasks" / "celery_app.py"
        source = celery_app_path.read_text(encoding="utf-8")

        assert "app.tasks.understand_tasks" in source, (
            "celery_app.py 的 include 中未包含 app.tasks.understand_tasks"
        )

    def test_understand_document_task_exists(self):
        """understand_document_task 应存在于 understand_tasks.py"""
        understand_tasks_path = BACKEND_DIR / "app" / "tasks" / "understand_tasks.py"
        source = understand_tasks_path.read_text(encoding="utf-8")

        assert "understand_document_task" in source, "缺少 understand_document_task 任务"

    def test_generate_questions_task_exists(self):
        """generate_questions_task 应存在于 understand_tasks.py"""
        understand_tasks_path = BACKEND_DIR / "app" / "tasks" / "understand_tasks.py"
        source = understand_tasks_path.read_text(encoding="utf-8")

        assert "generate_questions_task" in source, "缺少 generate_questions_task 任务"

    def test_tasks_have_max_retries(self):
        """Celery 任务应配置 max_retries"""
        understand_tasks_path = BACKEND_DIR / "app" / "tasks" / "understand_tasks.py"
        source = understand_tasks_path.read_text(encoding="utf-8")

        # 统计 max_retries 出现次数（每个任务装饰器中应有1次）
        count = source.count("max_retries")
        assert count >= 2, f"应有至少2个 max_retries 配置，实际只有 {count} 个"

    def test_understand_task_updates_to_learning(self):
        """理解任务应将笔记状态更新为 learning"""
        understand_tasks_path = BACKEND_DIR / "app" / "tasks" / "understand_tasks.py"
        source = understand_tasks_path.read_text(encoding="utf-8")

        assert "NoteStatus.learning" in source, "理解任务未更新状态为 learning"

    def test_understand_task_handles_failure(self):
        """理解任务失败时应更新状态为 learning_failed"""
        understand_tasks_path = BACKEND_DIR / "app" / "tasks" / "understand_tasks.py"
        source = understand_tasks_path.read_text(encoding="utf-8")

        assert "NoteStatus.learning_failed" in source, (
            "理解任务失败时未更新状态为 learning_failed"
        )


# ===========================================================================
# 9. 前端 API 客户端测试
# ===========================================================================


class TestFrontendAPIClient:
    """验证前端 API 客户端新增的类型和函数"""

    def test_knowledge_card_type_defined(self):
        """client.ts 应定义 KnowledgeCard 类型"""
        client_ts_path = FRONTEND_DIR / "src" / "api" / "client.ts"
        if not client_ts_path.exists():
            pytest.skip("前端 client.ts 不存在")

        source = client_ts_path.read_text(encoding="utf-8")
        assert "KnowledgeCard" in source, "client.ts 中未定义 KnowledgeCard 类型"

    def test_quiz_item_type_defined(self):
        """client.ts 应定义 QuizItem 类型"""
        client_ts_path = FRONTEND_DIR / "src" / "api" / "client.ts"
        if not client_ts_path.exists():
            pytest.skip("前端 client.ts 不存在")

        source = client_ts_path.read_text(encoding="utf-8")
        assert "QuizItem" in source, "client.ts 中未定义 QuizItem 类型"

    def test_start_understanding_function_defined(self):
        """client.ts 应定义 startUnderstanding 函数"""
        client_ts_path = FRONTEND_DIR / "src" / "api" / "client.ts"
        if not client_ts_path.exists():
            pytest.skip("前端 client.ts 不存在")

        source = client_ts_path.read_text(encoding="utf-8")
        assert "startUnderstanding" in source, "client.ts 中未定义 startUnderstanding 函数"

    def test_ask_question_function_defined(self):
        """client.ts 应定义 askQuestion 函数"""
        client_ts_path = FRONTEND_DIR / "src" / "api" / "client.ts"
        if not client_ts_path.exists():
            pytest.skip("前端 client.ts 不存在")

        source = client_ts_path.read_text(encoding="utf-8")
        assert "askQuestion" in source, "client.ts 中未定义 askQuestion 函数"

    def test_get_knowledge_cards_function_defined(self):
        """client.ts 应定义 getKnowledgeCards 函数"""
        client_ts_path = FRONTEND_DIR / "src" / "api" / "client.ts"
        if not client_ts_path.exists():
            pytest.skip("前端 client.ts 不存在")

        source = client_ts_path.read_text(encoding="utf-8")
        assert "getKnowledgeCards" in source, "client.ts 中未定义 getKnowledgeCards 函数"

    def test_generate_questions_function_defined(self):
        """client.ts 应定义 generateQuestions 函数"""
        client_ts_path = FRONTEND_DIR / "src" / "api" / "client.ts"
        if not client_ts_path.exists():
            pytest.skip("前端 client.ts 不存在")

        source = client_ts_path.read_text(encoding="utf-8")
        assert "generateQuestions" in source, "client.ts 中未定义 generateQuestions 函数"

    def test_api_paths_use_understanding_prefix(self):
        """API 路径应使用 /understanding/ 前缀"""
        client_ts_path = FRONTEND_DIR / "src" / "api" / "client.ts"
        if not client_ts_path.exists():
            pytest.skip("前端 client.ts 不存在")

        source = client_ts_path.read_text(encoding="utf-8")
        assert "/understanding/" in source, "API 路径未使用 /understanding/ 前缀"


# ===========================================================================
# 10. 前端路由和导航测试
# ===========================================================================


class TestFrontendRoutesAndNav:
    """验证前端路由和导航栏更新"""

    def test_cards_route_in_app(self):
        """App.tsx 应包含 /cards 路由"""
        app_tsx_path = FRONTEND_DIR / "src" / "App.tsx"
        if not app_tsx_path.exists():
            pytest.skip("前端 App.tsx 不存在")

        source = app_tsx_path.read_text(encoding="utf-8")
        assert 'path="/cards"' in source, "App.tsx 中未包含 /cards 路由"

    def test_card_detail_route_in_app(self):
        """App.tsx 应包含 /cards/:cardId 路由"""
        app_tsx_path = FRONTEND_DIR / "src" / "App.tsx"
        if not app_tsx_path.exists():
            pytest.skip("前端 App.tsx 不存在")

        source = app_tsx_path.read_text(encoding="utf-8")
        assert "/cards/:cardId" in source, "App.tsx 中未包含 /cards/:cardId 路由"

    def test_qa_route_in_app(self):
        """App.tsx 应包含 /qa 路由"""
        app_tsx_path = FRONTEND_DIR / "src" / "App.tsx"
        if not app_tsx_path.exists():
            pytest.skip("前端 App.tsx 不存在")

        source = app_tsx_path.read_text(encoding="utf-8")
        assert 'path="/qa"' in source, "App.tsx 中未包含 /qa 路由"

    def test_knowledge_cards_page_imported(self):
        """App.tsx 应导入 KnowledgeCards 页面"""
        app_tsx_path = FRONTEND_DIR / "src" / "App.tsx"
        if not app_tsx_path.exists():
            pytest.skip("前端 App.tsx 不存在")

        source = app_tsx_path.read_text(encoding="utf-8")
        assert "KnowledgeCards" in source, "App.tsx 中未导入 KnowledgeCards"

    def test_qa_page_imported(self):
        """App.tsx 应导入 QA 页面"""
        app_tsx_path = FRONTEND_DIR / "src" / "App.tsx"
        if not app_tsx_path.exists():
            pytest.skip("前端 App.tsx 不存在")

        source = app_tsx_path.read_text(encoding="utf-8")
        assert "QA" in source, "App.tsx 中未导入 QA"

    def test_navbar_has_knowledge_link(self):
        """导航栏应包含知识库链接"""
        navbar_path = FRONTEND_DIR / "src" / "components" / "Navbar.tsx"
        if not navbar_path.exists():
            pytest.skip("前端 Navbar.tsx 不存在")

        source = navbar_path.read_text(encoding="utf-8")
        assert "知识库" in source, "Navbar.tsx 中未包含知识库链接"
        assert "/cards" in source, "Navbar.tsx 中未包含 /cards 路径"

    def test_navbar_has_qa_link(self):
        """导航栏应包含问答链接"""
        navbar_path = FRONTEND_DIR / "src" / "components" / "Navbar.tsx"
        if not navbar_path.exists():
            pytest.skip("前端 Navbar.tsx 不存在")

        source = navbar_path.read_text(encoding="utf-8")
        assert "问答" in source, "Navbar.tsx 中未包含问答链接"
        assert "/qa" in source, "Navbar.tsx 中未包含 /qa 路径"

    def test_note_detail_has_start_learning(self):
        """NoteDetail.tsx 应包含开始学习功能"""
        note_detail_path = FRONTEND_DIR / "src" / "pages" / "NoteDetail.tsx"
        if not note_detail_path.exists():
            pytest.skip("前端 NoteDetail.tsx 不存在")

        source = note_detail_path.read_text(encoding="utf-8")
        assert "startUnderstanding" in source, "NoteDetail.tsx 中未导入 startUnderstanding"
        assert "开始学习" in source, "NoteDetail.tsx 中未包含开始学习按钮"

    def test_note_detail_has_learning_failed_status(self):
        """NoteDetail.tsx 应包含 learning_failed 状态标签"""
        note_detail_path = FRONTEND_DIR / "src" / "pages" / "NoteDetail.tsx"
        if not note_detail_path.exists():
            pytest.skip("前端 NoteDetail.tsx 不存在")

        source = note_detail_path.read_text(encoding="utf-8")
        assert "learning_failed" in source, "NoteDetail.tsx 中未包含 learning_failed 状态"

    def test_knowledge_cards_page_exists(self):
        """KnowledgeCards.tsx 页面文件应存在"""
        page_path = FRONTEND_DIR / "src" / "pages" / "KnowledgeCards.tsx"
        assert page_path.exists(), "KnowledgeCards.tsx 页面文件不存在"

    def test_card_detail_page_exists(self):
        """CardDetail.tsx 页面文件应存在"""
        page_path = FRONTEND_DIR / "src" / "pages" / "CardDetail.tsx"
        assert page_path.exists(), "CardDetail.tsx 页面文件不存在"

    def test_qa_page_exists(self):
        """QA.tsx 页面文件应存在"""
        page_path = FRONTEND_DIR / "src" / "pages" / "QA.tsx"
        assert page_path.exists(), "QA.tsx 页面文件不存在"


# ===========================================================================
# 11. RAG 服务测试
# ===========================================================================


class TestRAGService:
    """验证 RAG 服务的基本结构"""

    def test_rag_service_class_exists(self):
        """RAGService 类应存在"""
        from app.services.rag_service import RAGService
        assert RAGService is not None

    def test_rag_service_has_answer_question_method(self):
        """RAGService 应有 answer_question 方法"""
        from app.services.rag_service import RAGService

        service = RAGService()
        assert hasattr(service, "answer_question"), "RAGService 缺少 answer_question 方法"
        assert callable(service.answer_question), "answer_question 应为可调用方法"

    def test_rag_service_answer_question_is_async(self):
        """answer_question 应为异步方法"""
        from app.services.rag_service import RAGService
        import asyncio

        service = RAGService()
        assert asyncio.iscoroutinefunction(service.answer_question), (
            "answer_question 应为异步方法（async def）"
        )


# ===========================================================================
# 12. 集成测试 — API 端点可达性
# ===========================================================================


class TestAPIEndpointAccessibility:
    """验证 API 端点可通过 HTTP 访问（需认证）"""

    @pytest.mark.asyncio
    async def test_understanding_endpoints_require_auth(self):
        """理解管道端点应要求认证"""
        from httpx import ASGITransport, AsyncClient
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 未认证访问应返回 401
            response = await client.post("/api/understanding/fake-id/start")
            assert response.status_code == 401, (
                f"未认证访问理解管道应返回 401，实际返回 {response.status_code}"
            )

    @pytest.mark.asyncio
    async def test_ask_endpoint_requires_auth(self):
        """问答端点应要求认证"""
        from httpx import ASGITransport, AsyncClient
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/understanding/ask",
                json={"question": "测试问题"},
            )
            assert response.status_code == 401, (
                f"未认证访问问答端点应返回 401，实际返回 {response.status_code}"
            )

    @pytest.mark.asyncio
    async def test_cards_endpoint_requires_auth(self):
        """知识卡片端点应要求认证"""
        from httpx import ASGITransport, AsyncClient
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/understanding/cards")
            assert response.status_code == 401, (
                f"未认证访问知识卡片端点应返回 401，实际返回 {response.status_code}"
            )

    @pytest.mark.asyncio
    async def test_questions_endpoint_requires_auth(self):
        """题目端点应要求认证"""
        from httpx import ASGITransport, AsyncClient
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/understanding/questions")
            assert response.status_code == 401, (
                f"未认证访问题目端点应返回 401，实际返回 {response.status_code}"
            )
