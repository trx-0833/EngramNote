"""
笔记 API 共用的响应构建辅助函数（内部共享模块）
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...models.note import Note, SourceType
from ...models.note_project import NoteProject
from ...models.project import Project
from ...schemas.note import NoteResponse

from ...tasks.embedding_tasks import _needs_embed_clause

settings = get_settings()


def _fill_project_names(resp: NoteResponse, note: Note, mapping: dict) -> None:
    """为响应填充 project_ids/project_names（来自预查询的标签映射）"""
    ids, names = mapping.get(note.id, ([], []))
    resp.project_ids = ids
    resp.project_names = names


async def _load_project_tags(db: AsyncSession, notes) -> dict:
    """批量查询笔记的项目标签，返回 {note_id: (project_ids, project_names)} 映射"""
    note_ids = [n.id for n in notes]
    if not note_ids:
        return {}
    result = await db.execute(
        select(NoteProject.note_id, Project.id, Project.name)
        .join(Project, Project.id == NoteProject.project_id)
        .where(NoteProject.note_id.in_(note_ids))
    )
    mapping = {nid: ([], []) for nid in note_ids}
    for note_id, pid, pname in result.all():
        mapping[note_id][0].append(pid)
        mapping[note_id][1].append(pname)
    return mapping


async def _count_pending_embedding_chunks(db: AsyncSession, note_id: str) -> Optional[int]:
    """这篇笔记**还没补向量**的 chunk 数（B2 的"可见"那半）

    ## 为什么是 `Optional[int]` 而不是 `int`

    查询失败时返回 `None`（"没测到"），**不是** 0。与 `/ready` 的
    `pending_embeddings` 同一条原则：0 会让"没测到"与"真的都建好了"
    在界面上长得一模一样，而这两个状态对用户的含义完全不同。

    这个函数**不抛异常**：它是笔记详情页的一个附加提示，
    查询失败不该让整个详情页 500。

    ## 为什么不用 `/ready` 那个函数

    `/ready` 的 `_pending_embedding_count` 是**全库**计数（运维视角），
    这里要的是**单篇**（用户视角：这篇笔记能不能问）。
    口径同一个（`has_embedding = false` 的行数），范围不同。
    """
    from sqlalchemy import func

    from ...models.chunk import Chunk

    try:
        total = (
            await db.execute(
                select(func.count())
                .select_from(Chunk)
                .where(
                    Chunk.note_id == note_id,
                    # round5：与调度器同一条判据（模型不符也算待补）。
                    # 只看 `has_embedding` 时，换过模型之后这里会报"待补 0"，
                    # 而检索整批跳过 —— 用户看到的数字与实际状态不符。
                    _needs_embed_clause(
                        settings.embedding_model,
                        Chunk.has_embedding,
                        Chunk.embedding_model,
                    ),
                )
            )
        ).scalar_one()
    except Exception:
        return None
    return int(total)


async def _build_note_response(db: AsyncSession, note: Note) -> NoteResponse:
    """构建单个 NoteResponse（含项目标签数组），视频类型填充 video_url"""
    resp = NoteResponse.model_validate(note)
    tags = await _load_project_tags(db, [note])
    _fill_project_names(resp, note, tags)
    if note.source_type == SourceType.video:
        resp.video_url = f"/api/notes/{note.id}/video"
    return resp


async def _build_note_responses(db: AsyncSession, notes) -> list[NoteResponse]:
    """批量构建 NoteResponse（一次查询所有项目标签，避免 N+1）"""
    tags = await _load_project_tags(db, notes)
    responses = []
    for n in notes:
        resp = NoteResponse.model_validate(n)
        _fill_project_names(resp, n, tags)
        if n.source_type == SourceType.video:
            resp.video_url = f"/api/notes/{n.id}/video"
        responses.append(resp)
    return responses
