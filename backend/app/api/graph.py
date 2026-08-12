"""知识图谱 API 模块

本模块提供知识图谱相关的 HTTP 接口，包括获取图谱数据、
查看/确认/拒绝建议关系、手动创建关系和删除关系等操作。

主要职责：
- 获取用户知识图谱数据（GET /api/graph）
- 获取自动建议的关系（GET /api/graph/suggestions）
- 手动触发相关关系建议（POST /api/graph/suggest）
- 触发语义关系推断（POST /api/graph/suggest-semantic）
- 确认/拒绝建议关系（POST /api/graph/confirm、/reject）
- 手动创建关系（POST /api/graph/relation）
- 删除关系（DELETE /api/graph/relation/{relation_id}）

设计决策：
- 所有接口需要用户认证，且只能操作自己的数据
- 获取建议时不自动触发建议生成，避免嵌入编码等耗时操作阻塞页面加载，
  由用户通过 POST /suggest 显式触发
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
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
    BatchConfirmRequest,
    BatchRejectRequest,
    GraphSearchResponse,
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


@router.get("/stats")
async def get_graph_stats(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取知识图谱统计数据

    返回节点数、边数、关系类型分布、孤立节点数等统计信息。
    """
    return await graph_service.get_graph_stats(
        user_id=current_user.id,
        db=db,
    )


@router.get("/search", response_model=GraphSearchResponse)
async def search_graph(
    q: str = Query("", description="搜索关键词"),
    limit: int = Query(20, description="最大返回数量"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    搜索知识卡片

    按标题和内容模糊匹配用户的卡片，返回搜索结果。
    """
    if not q.strip():
        return {"items": [], "total": 0}

    items = await graph_service.search_nodes(
        user_id=current_user.id,
        keyword=q.strip(),
        db=db,
        limit=limit,
    )
    return {"items": items, "total": len(items)}


@router.get("/node/{node_id}/subgraph")
async def get_node_subgraph(
    node_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取某个节点及其直接邻居的子图

    返回中心节点详情和所有与之直接相连的邻居节点及边。
    """
    result = await graph_service.get_node_subgraph(
        node_id=node_id,
        user_id=current_user.id,
        db=db,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="节点不存在或无权访问")
    return result


@router.get("/suggestions", response_model=list[SuggestedRelation])
async def get_suggestions(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取自动建议的关系列表

    仅返回现有的 suggested 关系，不自动触发建议生成，
    避免嵌入模型编码等耗时操作阻塞页面加载。
    需要生成新建议时，由前端显式调用 POST /suggest。
    """
    return await graph_service.get_suggested_relations(
        user_id=current_user.id,
        db=db,
    )


@router.post("/suggest")
async def suggest_relations_api(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    手动触发基于嵌入向量相似度的相关关系建议

    基于「标题 + 内容」编码全部卡片并计算两两相似度，
    卡片较多时可能耗时数十秒（首次还需加载嵌入模型），
    因此由用户显式触发，不在页面加载时自动执行。
    """
    new_count = await graph_service.auto_suggest_relations(
        user_id=current_user.id,
        db=db,
    )
    return {"success": True, "new_count": new_count}


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


@router.post("/batch-confirm")
async def batch_confirm(
    req: BatchConfirmRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    批量确认建议关系

    一次性确认多条建议关系。
    """
    if not req.relation_ids:
        raise HTTPException(status_code=400, detail="关系 ID 列表不能为空")

    return await graph_service.batch_confirm_suggestions(
        relation_ids=req.relation_ids,
        user_id=current_user.id,
        db=db,
    )


@router.post("/batch-reject")
async def batch_reject(
    req: BatchRejectRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    批量拒绝建议关系

    一次性拒绝多条建议关系。
    """
    if not req.relation_ids:
        raise HTTPException(status_code=400, detail="关系 ID 列表不能为空")

    return await graph_service.batch_reject_suggestions(
        relation_ids=req.relation_ids,
        user_id=current_user.id,
        db=db,
    )


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
