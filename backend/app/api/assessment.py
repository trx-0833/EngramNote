from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.app_error import (
    ASSESSMENT_ANSWER_FAILED,
    ASSESSMENT_ANSWER_REJECTED,
    ASSESSMENT_COMPARE_FAILED,
    ASSESSMENT_QUIZ_GENERATE_FAILED,
    AppError,
)
from ..database import get_db
from ..api.auth import get_current_user_dependency
from ..models.user import User
from ..schemas.assessment import (
    CompareRequest,
    QuizGenerateRequest,
    AnswerSubmitRequest,
    AssessmentResponse,
    AssessmentHistoryItem,
)
from ..services.assessment_service import AssessmentService
from ..services.llm_service import LLMService

router = APIRouter()


def get_assessment_service(db: AsyncSession = Depends(get_db)) -> AssessmentService:
    llm_service = LLMService()
    return AssessmentService(llm_service, db)


@router.post("/compare", response_model=AssessmentResponse)
async def compare_assessment(
    request: CompareRequest,
    current_user: User = Depends(get_current_user_dependency),
    service: AssessmentService = Depends(get_assessment_service),
):
    """笔记-资料比对评估"""
    try:
        result = await service.compare_assessment(
            user_id=current_user.id,
            material_note_ids=request.material_note_ids,
            personal_note_ids=request.personal_note_ids,
        )
        return result
    except Exception as e:
        # 文案保持原样（str(e)）：本次迁移只加 error_code，不改用户可见内容。
        # 注意这条出口把内部异常文本回给了客户端（计划里的 H5 项），
        # 属于既有行为，改它会是行为变更，因此留给后续单独处理。
        raise AppError(ASSESSMENT_COMPARE_FAILED, str(e), 500) from e


@router.post("/generate-quiz", response_model=AssessmentResponse)
async def generate_quiz(
    request: QuizGenerateRequest,
    current_user: User = Depends(get_current_user_dependency),
    service: AssessmentService = Depends(get_assessment_service),
):
    """生成开放性问题"""
    try:
        result = await service.generate_quiz(
            user_id=current_user.id,
            material_note_ids=request.material_note_ids,
            personal_note_id=request.personal_note_id,
        )
        return result
    except Exception as e:
        raise AppError(ASSESSMENT_QUIZ_GENERATE_FAILED, str(e), 500) from e


@router.post("/submit-answer", response_model=AssessmentResponse)
async def submit_answer(
    request: AnswerSubmitRequest,
    current_user: User = Depends(get_current_user_dependency),
    service: AssessmentService = Depends(get_assessment_service),
):
    """提交答案并评判"""
    try:
        result = await service.submit_answers(
            user_id=current_user.id,
            assessment_id=request.assessment_id,
            answers=request.answers,
        )
        return result
    except ValueError as e:
        raise AppError(ASSESSMENT_ANSWER_REJECTED, str(e), 400) from e
    except Exception as e:
        raise AppError(ASSESSMENT_ANSWER_FAILED, str(e), 500) from e


@router.get("/history/{note_id}", response_model=list[AssessmentHistoryItem])
async def get_assessment_history(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    service: AssessmentService = Depends(get_assessment_service),
):
    """获取评估历史"""
    results = await service.get_history(
        user_id=current_user.id,
        note_id=note_id,
    )
    return results
