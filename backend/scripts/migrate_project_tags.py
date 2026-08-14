# -*- coding: utf-8 -*-
"""
项目标签化迁移脚本（一次性运维脚本）

背景：项目从「Vault 第一层目录（slug）」演化为「纯标签归属」，单篇笔记可属于
多个项目（note_projects 多对多）。本脚本完成：

1. 把 notes.project_id 单值搬运到 note_projects 关联表
2. 把 {user_id}/{slug}/... 路径重写为 {user_id}/inbox/...（original/clean/versions）
3. 物理移动对应文件到 inbox 前缀下，删除空的项目 slug 目录
4. DROP COLUMN：projects.slug、notes.project_id
5. 重写状态旁载 meta（笔记级 + 用户级 projects.json）

设计（沿用旧迁移脚本模式）：
- 只读 `--dry-run` / 可执行 `--apply`；幂等，可安全重跑
- apply 前完整备份数据库文件；文件先复制并校验，成功后更新 DB，失败回滚单条
- DROP COLUMN 需要 SQLite >= 3.35（启动时会自动检查）
- 注意：commit 会使 session 中所有实例过期（expire_on_commit），apply 循环内
  逐条重新查询，避免在同步上下文触发 lazy load（MissingGreenlet）

用法（backend 目录下）：
    python scripts/migrate_project_tags.py --dry-run   # 仅预览计划，不改任何东西
    python scripts/migrate_project_tags.py --apply     # 执行迁移
"""

import argparse
import asyncio
import logging
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 允许作为独立脚本运行（backend 目录在 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

from app.config import DATA_DIR, get_settings  # noqa: E402
from app.database import async_session  # noqa: E402
from app.models.note import Note  # noqa: E402
from app.models.note_project import NoteProject  # noqa: E402
from app.models.note_version import NoteVersion  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import vault_meta, vault_path  # noqa: E402
from app.services.storage_service import _resolve_path  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_project_tags")
settings = get_settings()

# 受影响路径字段（值均为对象名或空串）
_PATH_FIELDS = ("original_file_path", "original_md_path", "clean_md_path")


def _is_rewritten(path: str, user_id: str) -> bool:
    """判断对象名是否已重写到 inbox 前缀（幂等判据）"""
    if not path:
        return True
    return path.startswith(f"{user_id}/inbox/")


def _backup_db(db_path: Path) -> Path:
    """备份 SQLite 数据库文件到 data/backup/ 下"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = DATA_DIR / "backup" / f"pre_tag_migration_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    # 复制 .db 及可能的 -wal/-shm 副件
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db_path) + suffix)
        if src.is_file():
            shutil.copy2(str(src), str(backup_dir / src.name))
    return backup_dir


def _resolve(obj: str) -> Path:
    """把对象名映射为物理路径（本地模式忽略 bucket，直接映射到 Vault 根）"""
    return Path(_resolve_path("", obj))


def _copy_and_verify(src_abs: Path, dst_abs: Path) -> None:
    """复制并校验目标存在且大小与源一致，不一致抛 RuntimeError"""
    dst_abs.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src_abs), str(dst_abs))
    if dst_abs.stat().st_size != src_abs.stat().st_size:
        raise RuntimeError(f"复制校验失败(大小不一致): {src_abs} -> {dst_abs}")


async def migrate() -> None:
    parser = argparse.ArgumentParser(description="项目标签化迁移：project_id/slug → note_projects + inbox 路径")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="仅预览迁移计划，不做任何修改")
    group.add_argument("--apply", action="store_true", help="执行迁移")
    args = parser.parse_args()

    if settings.storage_backend == "minio":
        logger.error("本脚本暂未实现 MinIO 后端（文件搬移为本地实现），请先切换 local 模式或手动迁移")
        sys.exit(1)

    db_url = settings.get_database_url()
    if not db_url.startswith("sqlite"):
        logger.error("当前仅支持 SQLite 数据库迁移（实际: %s）", db_url)
        sys.exit(1)
    db_path = Path(db_url.replace("sqlite+aiosqlite:///", ""))

    stats = {"notes": 0, "links": 0, "rewritten": 0, "moved": 0, "skipped": 0, "failed": 0}
    failed_items: list[str] = []

    async with async_session() as db:
        # ---- 0. 确保 note_projects 表存在（应用未启动时防御性建表，IF NOT EXISTS 幂等） ----
        await db.execute(text(
            """
            CREATE TABLE IF NOT EXISTS note_projects (
                id VARCHAR NOT NULL PRIMARY KEY,
                note_id VARCHAR NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                project_id VARCHAR NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                UNIQUE (note_id, project_id)
            )
            """
        ))
        await db.execute(text("CREATE INDEX IF NOT EXISTS ix_note_projects_note_id ON note_projects (note_id)"))
        await db.execute(text("CREATE INDEX IF NOT EXISTS ix_note_projects_project_id ON note_projects (project_id)"))
        await db.execute(text("CREATE INDEX IF NOT EXISTS ix_note_projects_user_id ON note_projects (user_id)"))
        await db.commit()

        # ---- 1. 检查当前 schema 状态（幂等判据） ----
        # PRAGMA table_info 返回 (cid, name, type, notnull, dflt_value, pk)
        notes_columns = {c[1] for c in (await db.execute(text("PRAGMA table_info(notes)"))).all()}
        projects_columns = {c[1] for c in (await db.execute(text("PRAGMA table_info(projects)"))).all()}
        has_project_id = "project_id" in notes_columns
        has_slug = "slug" in projects_columns
        if not has_project_id and not has_slug:
            logger.info("已是标签化后的 schema（无 notes.project_id / projects.slug），无需迁移")
            return

        # ---- 2. 读取旧归属与 slug ----
        old_links = {}
        if has_project_id:
            for row in (await db.execute(text("SELECT id, project_id FROM notes"))).all():
                if row.project_id:
                    old_links[str(row.id)] = str(row.project_id)
        slug_by_project = {}
        if has_slug:
            for row in (await db.execute(text("SELECT id, slug FROM projects"))).all():
                slug_by_project[str(row.id)] = str(row.slug)

        notes = (await db.execute(select(Note).order_by(Note.created_at))).scalars().all()

        # ---- 3. 逐条笔记制定迁移计划 ----
        plan = []  # (note, project_id_old, fields_to_rewrite, versions_to_rewrite)
        all_user_ids = set()
        for note in notes:
            user_id = note.user_id
            all_user_ids.add(user_id)
            old_project_id = old_links.get(note.id)
            need_link = bool(old_project_id) and has_project_id
            path_fields = [f for f in _PATH_FIELDS if getattr(note, f) and not _is_rewritten(getattr(note, f), user_id)]

            # 版本对象（旧 slug 前缀的 history 路径）
            versions = (
                await db.execute(
                    select(NoteVersion).where(
                        NoteVersion.note_id == note.id,
                        NoteVersion.storage_path != "",
                    )
                )
            ).scalars().all()
            versions_to_rewrite = [
                v for v in versions
                if not v.storage_path.startswith(f"{user_id}/inbox/")
            ]

            if not need_link and not path_fields and not versions_to_rewrite:
                stats["skipped"] += 1
                continue

            plan.append((note, old_project_id, path_fields, versions_to_rewrite))
            stats["notes"] += 1
            if need_link:
                stats["links"] += 1
            stats["rewritten"] += len(path_fields) + len(versions_to_rewrite)

        if not plan:
            logger.info("没有需要迁移的笔记（数据可能已迁移，继续处理残留列等收尾步骤）")

        # ---- 4. 打印/记录计划 ----
        for note, old_pid, path_fields, versions in plan:
            logger.info("笔记 %s  %s", note.id, note.title)
            if old_pid:
                logger.info("  旧 project_id=%s -> 写 note_projects 关联", old_pid)
            for f in path_fields:
                old = getattr(note, f)
                new = old.replace(f"{note.user_id}/{slug_by_project.get(old_pid, '?')}/", f"{vault_path.inbox_prefix(note.user_id)}/", 1)
                logger.info("  %s: %s -> %s", f, old, new)
            for v in versions:
                new = v.storage_path.replace(f"{note.user_id}/{slug_by_project.get(old_pid, '?')}/", f"{vault_path.inbox_prefix(note.user_id)}/", 1)
                logger.info("  版本 v%d: %s -> %s", v.version_number, v.storage_path, new)

        if args.dry_run:
            logger.info("（dry-run 仅预览，未做任何修改）")
            logger.info("计划: 笔记=%d 关联=%d 路径重写=%d", stats["notes"], stats["links"], stats["rewritten"])
            return

        # ---- 5. apply：备份 DB ----
        backup_dir = _backup_db(db_path)
        logger.info("数据库已备份到: %s", backup_dir)

        # ---- 6. 执行迁移 ----
        for note_ref, old_pid, path_fields, _old_versions in plan:
            # commit 会过期 session 中所有实例，重新查询获取干净对象，避免同步 lazy load（MissingGreenlet）
            note = (
                await db.execute(select(Note).where(Note.id == note_ref.id))
            ).scalars().first()
            if not note:
                stats["failed"] += 1
                failed_items.append(note_ref.id)
                logger.error("  [FAILED] 笔记不存在: %s", note_ref.id)
                continue
            versions = (
                await db.execute(
                    select(NoteVersion).where(
                        NoteVersion.note_id == note.id,
                        NoteVersion.storage_path != "",
                        NoteVersion.storage_path.not_like(f"{vault_path.inbox_prefix(note.user_id)}/%"),
                    )
                )
            ).scalars().all()
            user_id = note.user_id
            inbox = vault_path.inbox_prefix(user_id)
            old_dir_seg = f"{user_id}/{slug_by_project.get(old_pid, '')}/"
            created: list[Path] = []
            try:
                # 6.1 路径重写：复制 → 校验 → 更新字段
                for f in path_fields:
                    old_obj = getattr(note, f)
                    new_obj = old_obj.replace(old_dir_seg, f"{inbox}/", 1)
                    src_abs = _resolve(old_obj)
                    dst_abs = _resolve(new_obj)
                    if not src_abs.is_file():
                        logger.warning("  [缺失] %s 源文件不存在，置空: %s", f, old_obj)
                        setattr(note, f, "")
                        continue
                    _copy_and_verify(src_abs, dst_abs)
                    created.append(dst_abs)
                    setattr(note, f, new_obj)
                    stats["moved"] += 1

                # 6.2 版本重写
                for v in versions:
                    new_storage = v.storage_path.replace(old_dir_seg, f"{inbox}/", 1)
                    src_abs = _resolve(v.storage_path)
                    dst_abs = _resolve(new_storage)
                    if not src_abs.is_file():
                        logger.warning("  [缺失] 版本文件不存在，置空: %s", v.storage_path)
                        v.storage_path = ""
                        continue
                    _copy_and_verify(src_abs, dst_abs)
                    created.append(dst_abs)
                    v.storage_path = new_storage
                    stats["moved"] += 1

                # 6.3 项目归属搬运（唯一约束防重复）
                if old_pid:
                    exists = (
                        await db.execute(
                            select(NoteProject.id).where(
                                NoteProject.note_id == note.id,
                                NoteProject.project_id == old_pid,
                            )
                        )
                    ).scalars().first()
                    if not exists:
                        db.add(NoteProject(note_id=note.id, project_id=old_pid, user_id=user_id))

                await db.commit()
                # commit 后实例属性过期，refresh 后再写 meta
                await db.refresh(note)

                # 6.4 状态旁载镜像（注入标签后写穿到新位置）
                note._project_ids = [old_pid] if old_pid else []
                if old_pid:
                    pname = (await db.execute(select(Project.name).where(Project.id == old_pid))).scalar_one_or_none()
                    note._project_names = [pname] if pname else []
                else:
                    note._project_names = []
                vault_meta.write_note_meta(note)

                logger.info("  [OK] 笔记迁移完成")
            except Exception as e:  # noqa: BLE001
                await db.rollback()
                for p in created:
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass
                stats["failed"] += 1
                failed_items.append(note_ref.id)
                logger.error("  [FAILED] %s: %s", note.id, e)

        # ---- 7. DROP COLUMN（SQLite >= 3.35） ----
        if stats["failed"] == 0:
            sqlite_version = sqlite3.sqlite_version_info
            if sqlite_version >= (3, 35, 0):
                if has_project_id:
                    await db.execute(text("ALTER TABLE notes DROP COLUMN project_id"))
                    logger.info("已删除 notes.project_id 列")
                if has_slug:
                    # SQLite DROP COLUMN 需先删除引用该列的索引（历史残留 ix_projects_slug）
                    await db.execute(text("DROP INDEX IF EXISTS ix_projects_slug"))
                    await db.execute(text("ALTER TABLE projects DROP COLUMN slug"))
                    logger.info("已删除 projects.slug 列")
                await db.commit()
            else:
                logger.warning(
                    "SQLite 版本 %s 不支持 DROP COLUMN（需 >=3.35），请手动执行: "
                    "ALTER TABLE notes DROP COLUMN project_id; ALTER TABLE projects DROP COLUMN slug;",
                    ".".join(map(str, sqlite_version)),
                )
        else:
            logger.warning("存在 %d 条失败笔记，跳过 DROP COLUMN（可修复后重跑，幂等）", stats["failed"])

        # ---- 8. 重写用户级项目清单 ----
        for uid in all_user_ids:
            projects = (await db.execute(select(Project).where(Project.user_id == uid))).scalars().all()
            vault_meta.write_project_meta(uid, list(projects))

        # ---- 9. 清理空的项目 slug 目录（只删空目录树，不影响任何文件） ----
        if stats["failed"] == 0:
            removed_dirs = []
            vault_root = settings.get_vault_dir()
            for uid in all_user_ids:
                for slug in set(slug_by_project.values()):
                    if not slug:
                        continue
                    proj_dir = vault_root / uid / slug
                    if proj_dir.is_dir() and not any(proj_dir.iterdir()):
                        proj_dir.rmdir()
                        removed_dirs.append(str(proj_dir))
            if removed_dirs:
                logger.info("已清理空的项目目录: %s", ", ".join(removed_dirs))

    logger.info("=" * 70)
    logger.info(
        "迁移报告: 笔记=%d 关联=%d 路径重写=%d 文件移动=%d 跳过=%d 失败=%d",
        stats["notes"], stats["links"], stats["rewritten"], stats["moved"], stats["skipped"], stats["failed"],
    )
    if failed_items:
        logger.info("失败笔记: %s", failed_items)
    if args.dry_run:
        logger.info("（dry-run 仅预览，未做任何修改）")
    sys.exit(1 if stats["failed"] else 0)


if __name__ == "__main__":
    asyncio.run(migrate())
