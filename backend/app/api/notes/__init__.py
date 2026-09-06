"""
笔记 CRUD API 包

由原 api/notes.py 拆分而来，将 4 个子路由模块
（列表与详情/内容/归档、回收站、版本、双链与批注）的 APIRouter
聚合为单个 router。router.py 的挂载方式与 URL 前缀保持不变。
"""

from fastapi import APIRouter

from .trash import router as trash_router
from .list_detail import router as list_detail_router
from .versions import router as versions_router
from .links import router as links_router
from .ask import router as ask_router

router = APIRouter()

# 注意注册顺序：/trash 与 /archive 均为字面量单段路由，
# 必须先于 GET /{note_id} 注册，否则会被当作 note_id 匹配。
# 故回收站模块先于列表与详情模块聚合。
# /notes 前缀在子路由聚合处声明（FastAPI 要求 include 时 prefix 与端点 path
# 不能同时为空——list_notes 的 path 为空串，pref 由这里补齐）。
router.include_router(trash_router, prefix="/notes")
router.include_router(list_detail_router, prefix="/notes")
router.include_router(links_router, prefix="/notes")
router.include_router(versions_router, prefix="/notes")
router.include_router(ask_router, prefix="/notes")