"""
笔记版本历史业务逻辑模块

本模块封装笔记 Markdown 内容版本化的核心业务逻辑，包括版本创建、
列表查询、内容读取、版本对比与历史版本恢复。

主要职责：
- 在覆盖现有 Markdown 文件前创建版本快照
- 按笔记维度查询版本历史列表
- 读取指定版本的内容
- 对比两个版本的行级 diff
- 将历史版本内容恢复为当前内容（同时为当前内容创建快照）
- 按来源限制版本数量，FIFO 删除最旧版本及其存储文件

设计决策：
- 版本号 version_number 按 note_id 维度从 1 递增，通过 MAX(version_number) + 1 计算
- 版本内容存放在对象存储的 markdown bucket，路径形如 {user_id}/inbox/history/versions/{note_id}/v{N}.md（Vault 版本区）
- diff 使用 difflib.ndiff 生成行级差异，过滤掉 "? " 内联提示行
- 版本上限按来源区分：USER_EDIT 保留 50 个，AUTO_CLEAN 保留 10 个
- 恢复操作先为当前内容创建快照（USER_EDIT），再用目标版本覆盖当前文件
- 数据库与对象存储操作相互独立，存储删除失败不影响记录删除
"""

from __future__ import annotations

import difflib
import logging
from typing import Dict, List, Optional

from sqlalchemy import select, func, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models.note import Note
from ..models.note_version import NoteVersion, VersionSource
from .storage_service import upload_bytes, get_object_bytes, delete_file
from . import vault_path

settings = get_settings()
logger = logging.getLogger(__name__)


# 各来源版本保留上限
_MAX_VERSIONS = {
    VersionSource.USER_EDIT.value: 50,
    VersionSource.AUTO_CLEAN.value: 10,
    VersionSource.SYSTEM.value: 50,
}


class VersionService:
    """笔记版本历史服务，提供版本创建、查询、对比与恢复能力"""

    # --------------------------------------------------------------
    # 1. 创建版本
    # --------------------------------------------------------------
    async def create_version(
        self,
        note_id: str,
        user_id: str,
        content: str,
        source: str,
        db: AsyncSession,
        change_summary: Optional[str] = None,
    ) -> NoteVersion:
        """
        为指定笔记创建一个新版本快照

        流程：
        1. 查询该笔记当前最大 version_number，新版本号 = max + 1（首版本为 1）
        2. 计算 content_size（UTF-8 编码字节数）
        3. 通过 StorageService 将内容写入 Vault 版本区 history/versions/{note_id}/v{N}.md
        4. 插入 NoteVersion 记录
        5. 调用 prune_versions 按来源上限清理最旧版本

        Args:
            note_id: 笔记 ID
            user_id: 用户 ID
            content: 版本 Markdown 文本内容
            source: 版本来源，取 VersionSource 枚举值
            db: 异步数据库会话
            change_summary: 变更摘要（可选）

        Returns:
            NoteVersion: 新创建的版本记录
        """
        # 查询当前最大版本号
        max_result = await db.execute(
            select(func.max(NoteVersion.version_number)).where(
                NoteVersion.note_id == note_id
            )
        )
        max_version = max_result.scalar() or 0
        version_number = max_version + 1

        # 计算内容大小
        content_bytes = content.encode("utf-8")
        content_size = len(content_bytes)

        # 存储路径：Vault 版本区 {user_id}/inbox/history/versions/{note_id}/v{N}.md
        # 以 note_id 子目录隔离版本文件，避免不同笔记的同号版本互相覆盖
        note_result = await db.execute(select(Note).where(Note.id == note_id))
        note = note_result.scalars().first()
        prefix = vault_path.derive_prefix(note) if note else f"{user_id}/default"
        storage_path = vault_path.history_object(prefix, note_id, version_number)

        # 写入对象存储
        upload_bytes(
            settings.minio_bucket_markdown,
            storage_path,
            content_bytes,
            content_type="text/markdown; charset=utf-8",
        )

        # 插入版本记录
        version = NoteVersion(
            note_id=note_id,
            user_id=user_id,
            version_number=version_number,
            source=source,
            content_size=content_size,
            change_summary=change_summary,
            storage_path=storage_path,
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)

        logger.info(
            f"已创建笔记版本: note_id={note_id[:8]}, v{version_number}, source={source}, size={content_size} bytes"
        )

        # 按来源上限清理旧版本
        await self.prune_versions(note_id, source, db)

        return version

    # --------------------------------------------------------------
    # 2. 查询版本列表
    # --------------------------------------------------------------
    async def list_versions(
        self, note_id: str, user_id: str, db: AsyncSession
    ) -> List[NoteVersion]:
        """
        查询指定笔记的版本历史列表（按版本号倒序）

        Args:
            note_id: 笔记 ID
            user_id: 用户 ID，用于权限校验
            db: 异步数据库会话

        Returns:
            List[NoteVersion]: 版本列表，最新版本在前
        """
        result = await db.execute(
            select(NoteVersion)
            .where(
                NoteVersion.note_id == note_id,
                NoteVersion.user_id == user_id,
            )
            .order_by(NoteVersion.version_number.desc())
        )
        return list(result.scalars().all())

    # --------------------------------------------------------------
    # 3. 读取版本内容
    # --------------------------------------------------------------
    async def get_version_content(
        self,
        note_id: str,
        version_number: int,
        user_id: str,
        db: AsyncSession,
    ) -> str:
        """
        读取指定版本的 Markdown 内容

        Args:
            note_id: 笔记 ID
            version_number: 版本号
            user_id: 用户 ID，用于权限校验
            db: 异步数据库会话

        Returns:
            str: 版本 Markdown 文本内容

        Raises:
            ValueError: 版本记录不存在
        """
        version = await self._get_version(note_id, version_number, user_id, db)
        if version is None:
            raise ValueError(
                f"版本不存在: note_id={note_id}, version_number={version_number}"
            )
        try:
            data = get_object_bytes(
                settings.minio_bucket_markdown, version.storage_path
            )
        except FileNotFoundError:
            # 版本内容文件已被外部删除（如用户手动清理历史版本），
            # 该版本已无可恢复内容：清理 DB 记录避免幽灵版本继续报错
            logger.warning(
                "版本内容文件缺失，清理版本记录: note_id=%s, v%d, path=%s",
                note_id[:8], version_number, version.storage_path,
            )
            await db.delete(version)
            await db.commit()
            raise ValueError(
                f"版本内容文件已被删除，版本记录已清理: v{version_number}"
            )
        return data.decode("utf-8")

    # --------------------------------------------------------------
    # 4. 版本对比
    # --------------------------------------------------------------
    async def diff_versions(
        self,
        note_id: str,
        v1: int,
        v2: int,
        user_id: str,
        db: AsyncSession,
    ) -> Dict:
        """
        对比两个版本的行级 diff

        使用 difflib.ndiff 生成差异，过滤 '? ' 内联提示行，
        将每行标注为 added / removed / unchanged。

        Args:
            note_id: 笔记 ID
            v1: 旧版本号
            v2: 新版本号
            user_id: 用户 ID，用于权限校验
            db: 异步数据库会话

        Returns:
            Dict: {
                "v1_number": v1,
                "v2_number": v2,
                "diff_lines": [{"type": "added"|"removed"|"unchanged", "content": line}, ...]
            }

        Raises:
            ValueError: 任一版本记录不存在
        """
        v1_content = await self.get_version_content(note_id, v1, user_id, db)
        v2_content = await self.get_version_content(note_id, v2, user_id, db)

        v1_lines = v1_content.splitlines()
        v2_lines = v2_content.splitlines()

        diff_lines: List[Dict] = []
        # ndiff 以 v1 为基准，对比 v2，标记 '+' 为新增，'-' 为删除
        for line in difflib.ndiff(v1_lines, v2_lines):
            if line.startswith("+ "):
                diff_lines.append({"type": "added", "content": line[2:]})
            elif line.startswith("- "):
                diff_lines.append({"type": "removed", "content": line[2:]})
            elif line.startswith("? "):
                # ndiff 内联提示行，跳过
                continue
            else:
                # "  " 开头或空行视为未变更
                # 去除前两个字符的 ndiff 前缀
                content = line[2:] if len(line) >= 2 else line
                diff_lines.append({"type": "unchanged", "content": content})

        return {
            "v1_number": v1,
            "v2_number": v2,
            "diff_lines": diff_lines,
        }

    # --------------------------------------------------------------
    # 5. 恢复历史版本
    # --------------------------------------------------------------
    async def restore_version(
        self,
        note_id: str,
        version_number: int,
        user_id: str,
        db: AsyncSession,
    ) -> NoteVersion:
        """
        将指定历史版本的内容恢复为当前内容

        流程：
        1. 读取笔记当前 Markdown 文件内容（优先 clean_md_path，回退 original_md_path）
        2. 为当前内容创建新版本快照（source=USER_EDIT）
        3. 读取目标版本内容
        4. 用目标版本内容覆盖笔记当前的 Markdown 文件
        5. 刷新笔记 updated_at

        Args:
            note_id: 笔记 ID
            version_number: 要恢复的目标版本号
            user_id: 用户 ID
            db: 异步数据库会话

        Returns:
            NoteVersion: 为恢复前内容创建的新版本记录

        Raises:
            ValueError: 笔记或目标版本不存在，或笔记无可写入的 Markdown 路径
        """
        # 获取笔记
        result = await db.execute(
            select(Note).where(Note.id == note_id, Note.user_id == user_id)
        )
        note = result.scalars().first()
        if note is None:
            raise ValueError(f"笔记不存在: note_id={note_id}")

        # 确定当前 Markdown 文件路径，优先 clean_md_path
        target_path = note.clean_md_path or note.original_md_path
        if not target_path:
            raise ValueError(f"笔记无可写入的 Markdown 路径: note_id={note_id}")

        # 读取当前内容
        current_content = ""
        try:
            data = get_object_bytes(settings.minio_bucket_markdown, target_path)
            current_content = data.decode("utf-8")
        except Exception as e:
            logger.warning(
                f"读取笔记当前内容失败，将以空字符串创建版本快照: note_id={note_id[:8]}, err={e}"
            )
            current_content = ""

        # 为当前内容创建版本快照（USER_EDIT）
        new_version = await self.create_version(
            note_id=note_id,
            user_id=user_id,
            content=current_content,
            source=VersionSource.USER_EDIT.value,
            db=db,
            change_summary=f"restore from v{version_number}",
        )

        # 读取目标版本内容
        target_content = await self.get_version_content(
            note_id, version_number, user_id, db
        )

        # 用目标版本内容覆盖当前 Markdown 文件
        upload_bytes(
            settings.minio_bucket_markdown,
            target_path,
            target_content.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )

        # 刷新笔记 updated_at（onupdate 不会在对象存储写入时触发）
        from sqlalchemy import func as sa_func
        note.updated_at = sa_func.now()
        await db.commit()
        await db.refresh(note)

        logger.info(
            f"已恢复笔记版本: note_id={note_id[:8]}, target=v{version_number}, "
            f"snapshot=v{new_version.version_number}"
        )
        return new_version

    # --------------------------------------------------------------
    # 6. 版本数量裁剪
    # --------------------------------------------------------------
    async def prune_versions(self, note_id: str, source: str, db: AsyncSession) -> None:
        """
        按来源上限裁剪版本数量，FIFO 删除最旧版本及其存储文件

        - USER_EDIT: 保留最新 50 个
        - AUTO_CLEAN: 保留最新 10 个
        - SYSTEM: 保留最新 50 个

        Args:
            note_id: 笔记 ID
            source: 版本来源
            db: 异步数据库会话
        """
        max_keep = _MAX_VERSIONS.get(source, 50)

        # 查询该来源下的版本总数
        count_result = await db.execute(
            select(func.count()).select_from(
                select(NoteVersion)
                .where(
                    NoteVersion.note_id == note_id,
                    NoteVersion.source == source,
                )
                .subquery()
            )
        )
        total = count_result.scalar() or 0

        if total <= max_keep:
            return

        # 计算需要删除的数量，按 version_number 升序取最旧的若干条
        delete_count = total - max_keep
        result = await db.execute(
            select(NoteVersion)
            .where(
                NoteVersion.note_id == note_id,
                NoteVersion.source == source,
            )
            .order_by(NoteVersion.version_number.asc())
            .limit(delete_count)
        )
        to_delete = list(result.scalars().all())

        for version in to_delete:
            # 先删除对象存储文件，失败仅记录日志
            try:
                delete_file(settings.minio_bucket_markdown, version.storage_path)
            except Exception as e:
                logger.warning(
                    f"删除版本存储文件失败: path={version.storage_path}, err={e}"
                )
            # 再删除数据库记录
            await db.delete(version)

        await db.commit()
        logger.info(
            f"已裁剪版本: note_id={note_id[:8]}, source={source}, "
            f"deleted={len(to_delete)}, remaining={total - len(to_delete)}"
        )

    # --------------------------------------------------------------
    # 内部辅助方法
    # --------------------------------------------------------------
    async def _get_version(
        self,
        note_id: str,
        version_number: int,
        user_id: str,
        db: AsyncSession,
    ) -> Optional[NoteVersion]:
        """按 note_id + version_number + user_id 查询单个版本记录"""
        result = await db.execute(
            select(NoteVersion).where(
                NoteVersion.note_id == note_id,
                NoteVersion.version_number == version_number,
                NoteVersion.user_id == user_id,
            )
        )
        return result.scalars().first()


# 模块级单例，方便上层直接 import 后调用
version_service = VersionService()
