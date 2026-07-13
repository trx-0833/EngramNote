"""
知识图谱 API 模块

本模块提供知识图谱相关的 HTTP 接口，包括获取图谱数据、
查看/确认/拒绝建议关系、手动创建关系和删除关系等操作。

主要职责：
- 获取用户知识图谱数据（GET /api/graph）
- 获取自动建议的关系（GET /api/graph/suggestions）
- 确认建议关系（POST /api/graph/confirm）
- 拒绝建议关系（POST /api/graph/reject）
- 手动创建关系（POST /api/graph/relation）
- 删除关系（DELETE /api/graph/relation/{relation_id}）

设计决策：
- 所有接口需要用户认证，且只能操作自己的数据
- 获取建议时，若无已有建议则自动触发 auto_suggest_relations
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..api.auth import get_current_user_dependency
from ..schemas.graph import (
    GraphData,
    SuggestedRelation,
    ConfirmRelationRequest,
    RejectRelationRequest,
    CreateRelationRequest,
)
from ..services import graph_service
from ..services.graph_service import suggest_semantic_relations

router = APIRouter()


@router.get("", response_model=GraphData)
async def get_graph(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取当前用户的知识图谱数据

    返回用户所有知识卡片作为节点，
    confirmed 和 suggested 状态的关系作为边。
    """
    return await graph_service.get_graph_data(
        user_id=current_user.id,
        db=db,
    )


@router.get("/suggestions", response_model=list[SuggestedRelation])
async def get_suggestions(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取自动建议的关系列表

    若当前无任何建议关系，则自动触发基于嵌入向量的关系建议。
    """
    # 先获取已有建议
    suggestions = await graph_service.get_suggested_relations(
        user_id=current_user.id,
        db=db,
    )

    # 若无建议，自动触发建议生成
    if not suggestions:
        new_count = await graph_service.auto_suggest_relations(
            user_id=current_user.id,
            db=db,
        )
        if new_count > 0:
            suggestions = await graph_service.get_suggested_relations(
                user_id=current_user.id,
                db=db,
            )

    return suggestions


@router.post("/confirm")
async def confirm_relation(
    req: ConfirmRelationRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    确认一条建议关系

    将 suggested 状态更新为 confirmed。
    """
    result = await graph_service.confirm_relation(
        relation_id=req.relation_id,
        user_id=current_user.id,
        db=db,
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "操作失败"))

    return result


@router.post("/reject")
async def reject_relation(
    req: RejectRelationRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    拒绝一条建议关系

    将 suggested 状态更新为 rejected。
    """
    result = await graph_service.reject_relation(
        relation_id=req.relation_id,
        user_id=current_user.id,
        db=db,
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "操作失败"))

    return result


@router.post("/relation")
async def create_relation(
    req: CreateRelationRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    手动创建一条卡片关系

    创建 confirmed 状态的关系，需指定两张卡片和关系类型。
    """
    result = await graph_service.create_relation(
        card_id_1=req.card_id_1,
        card_id_2=req.card_id_2,
        relation_type=req.relation_type,
        user_id=current_user.id,
        db=db,
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "操作失败"))

    return result


@router.delete("/relation/{relation_id}")
async def delete_relation(
    relation_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    删除一条卡片关系

    只能删除属于自己的关系。
    """
    result = await graph_service.delete_relation(
        relation_id=relation_id,
        user_id=current_user.id,
        db=db,
    )

    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "关系不存在"))

    return result


@router.post("/suggest-semantic", response_model=Dict[str, Any])
async def suggest_semantic_relations_api(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    触发语义关系推断（手动）

    基于用户的所有知识卡片（限 100 张），用 LLM 推断 prerequisite/subsequent/contrast 关系。
    已存在任何关系的卡片对会被跳过，不重复推断。
    """
    result = await suggest_semantic_relations(current_user.id, db)
    return result
