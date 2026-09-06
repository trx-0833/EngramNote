"""
笔记 API 共用的响应构建辅助函数（内部共享模块）
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.note import Note, SourceType
from ...models.note_project import NoteProject
from ...models.project import Project
from ...schemas.note import NoteResponse


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