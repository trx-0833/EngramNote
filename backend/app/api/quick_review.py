"""
快速复习 API 模块

本模块提供按笔记维度快速复习的 HTTP 接口，
用户上传笔记并完成理解后，可立即复习该笔记关联的所有题目。

主要职责：
- 获取指定笔记的所有题目（GET /api/review/quick/{note_id}）
- 提交快速复习答案（POST /api/review/quick/{note_id}/submit）

设计决策：
- 所有接口需要用户认证，且只能操作自己的数据
- 返回指定笔记下的所有 QuizItem，不区分是否到期
- 快速复习提交答案不受每日复习上限限制
- 如果没有题目，返回空列表（前端处理"暂无题目"提示）
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..models.note import Note
from ..models.quiz_item import QuizItem
from ..api.auth import get_current_user_dependency
from ..schemas.review import (
    DueQuizResponse,
    DueQuizListResponse,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
)
from ..services import review_service

router = APIRouter()


@router.get("/quick/{note_id}", response_model=DueQuizListResponse)
async def get_quick_review(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取指定笔记的所有题目，用于快速复习

    返回该笔记下当前用户的所有 QuizItem，
    不区分是否到期，方便用户上传后立即复习。
    回收站笔记不可快速复习（D1 决策：404 语义与资料不存在一致）。
    """
    result = await db.execute(
        select(QuizItem).join(Note, Note.id == QuizItem.note_id).where(
            QuizItem.note_id == note_id,
            QuizItem.user_id == current_user.id,
            Note.trashed_at.is_(None),
        )
    )
    quizzes = list(result.scalars().all())

    return DueQuizListResponse(
        items=[DueQuizResponse.model_validate(q) for q in quizzes],
        total=len(quizzes),
    )


@router.post("/quick/{note_id}/submit", response_model=SubmitAnswerResponse)
async def submit_quick_review_answer(
    note_id: str,
    req: SubmitAnswerRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    提交快速复习答案

    与普通复习提交不同，快速复习不受每日答题上限限制。
    用于"立即学习"场景，用户上传新文件后可立即复习。
    """
    # 校验题目属于指定笔记且属于当前用户
    quiz_result = await db.execute(
        select(QuizItem).where(
            QuizItem.id == req.quiz_id,
            QuizItem.note_id == note_id,
            QuizItem.user_id == current_user.id,
        )
    )
    if not quiz_result.scalars().first():
        raise HTTPException(status_code=404, detail="题目不存在或不属于该笔记")

    result = await review_service.submit_answer(
        quiz_id=req.quiz_id,
        user_id=current_user.id,
        user_answer=req.user_answer,
        time_spent_ms=req.time_spent_ms,
        db=db,
        skip_daily_limit=True,
        skip_due_check=True,  # F-14 修复：快速复习保留免到期校验
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return SubmitAnswerResponse(
        quiz_id=result["quiz_id"],
        is_correct=result["is_correct"],
        quality=result["quality"],
        correct_answer=result["correct_answer"],
        explanation=result.get("explanation"),
        options=result.get("options"),
        question_type=result.get("question_type", "choice"),
        sm2=result["sm2"],
    )
