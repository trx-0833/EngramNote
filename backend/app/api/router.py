"""
API 总路由注册模块

本模块负责将所有 API 子路由统一注册到一个总路由器上，
实现路由的模块化组织和管理。

主要职责：
- 创建总路由器 api_router
- 注册认证相关路由（/api/auth）
- 注册笔记 CRUD 路由（/api/notes）
- 注册文件上传路由（/api/upload）

设计决策：
- 每个功能模块有独立的路由文件，便于维护和扩展
- 通过 prefix 为每个模块设置 URL 前缀，避免路由冲突
- 通过 tags 为 OpenAPI 文档自动分组
"""

from fastapi import APIRouter

from .auth import router as auth_router
from .notes import router as notes_router
from .upload import router as upload_router
from .cleaning import router as cleaning_router
from .understanding import router as understanding_router
from .review import router as review_router
from .report import router as report_router

# 创建总路由器，所有子路由将挂载到此路由器
api_router = APIRouter()

# 注册认证模块路由，前缀 /auth，OpenAPI 标签为"认证"
api_router.include_router(auth_router, prefix="/auth", tags=["认证"])
# 注册笔记模块路由，前缀 /notes，OpenAPI 标签为"笔记"
api_router.include_router(notes_router, prefix="/notes", tags=["笔记"])
# 注册上传模块路由，前缀 /upload，OpenAPI 标签为"上传"
api_router.include_router(upload_router, prefix="/upload", tags=["上传"])
# 注册清洗模块路由，前缀 /cleaning，OpenAPI 标签为"清洗"
api_router.include_router(cleaning_router, prefix="/cleaning", tags=["清洗"])
# 注册理解模块路由，前缀 /understanding，OpenAPI 标签为"理解"
api_router.include_router(understanding_router, prefix="/understanding", tags=["理解"])
# 注册复习模块路由，前缀 /review，OpenAPI 标签为"复习"
api_router.include_router(review_router, prefix="/review", tags=["复习"])
# 注册学习报告路由，前缀 /report，OpenAPI 标签为"学习报告"
api_router.include_router(report_router, prefix="/report", tags=["学习报告"])
