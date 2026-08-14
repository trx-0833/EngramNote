# -*- coding: utf-8 -*-
"""
旧版存储 → 新版 Vault 结构迁移脚本（一次性运维脚本）

把旧版 bucket 结构下的存量文件迁移到新版项目隔离 Vault 结构，并重写数据库路径字段：

- 原始文件：original-files/{user_id}/{uuid}/{filename}（或 {user_id}/{uuid}/{filename}）
            → {user_id}/inbox/source/{base}{ext}
- 原始 Markdown：markdown/{user_id}/{uuid|base}/original.md → {P}/output/markdown/{base}.md
- 清洗 Markdown：markdown/{user_id}/{uuid|base}/clean.md   → {P}/output/markdown/{base}.clean.md
- 版本快照：markdown/{user_id}/{uuid}/versions/v{N}.md     → {P}/history/versions/v{N}.md（重编号）

设计（对应 .trae/documents/旧版存储迁移至新Vault结构方案.md）：
- 只读 `--dry-run` / 可执行 `--apply`；幂等，已迁移笔记自动跳过，可安全重跑
- 先复制并校验，成功后才更新 DB；单条笔记失败回滚本次复制文件，不阻塞整体
- 全部成功后把旧 bucket 目录归档到 {vault}/_legacy_backup_{timestamp}/（可回滚）
- 关联数据（卡片/问题/标注等）按 note_id 关联，不受路径迁移影响，无需处理

用法（backend 目录下）：
    python scripts/migrate_legacy_storage.py --dry-run   # 仅预览计划，不改任何东西
    python scripts/migrate_legacy_storage.py --apply     # 执行迁移
"""

import argparse
import asyncio
import logging
import os
import re
import secrets
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 允许作为独立脚本运行（backend 目录在 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import or_, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import async_session  # noqa: E402
from app.models.note import Note  # noqa: E402
from app.models.note_version import NoteVersion  # noqa: E402
from app.services import vault_meta, vault_path  # noqa: E402
from app.services.storage_service import (  # noqa: E402
    _get_minio_client,
    _resolve_path,
    settings as storage_settings,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_legacy_storage")
settings = get_settings()

# Windows 文件名非法字符（目标路径中的 base 段清洗用）
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def _sanitize_base(name: str) -> str:
    """清洗为文件系统安全的 base（保留中文/字母/数字/常见符号，去尾部点与空格）"""
    cleaned = _INVALID_CHARS.sub("_", name).strip().rstrip(". ")
    return cleaned[:200]


def _is_migrated(note: Note) -> bool:
    """判断笔记是否已是新版 vault 路径（幂等判据）"""
    def ok(path: str, marker: str) -> bool:
        return (not path) or marker in path
    return (
        ok(note.original_file_path, "/source/")
        and ok(note.original_md_path, "/output/")
        and ok(note.clean_md_path or "", "/output/")
    )


def _compute_base(note: Note) -> str | None:
    """从旧路径推导目标 base：优先源文件名主干，回退 md 目录名"""
    if note.original_file_path:
        name = note.original_file_path.split("/")[-1]
        stem = os.path.splitext(name)[0]
    elif note.original_md_path:
        parts = note.original_md_path.split("/")
        stem = parts[-2] if len(parts) >= 2 else "note"
    elif note.clean_md_path:
        parts = note.clean_md_path.split("/")
        stem = parts[-2] if len(parts) >= 2 else "note"
    else:
        return None
    return _sanitize_base(stem) or None


def _resolve(bucket: str, obj: str) -> Path:
    """把对象名映射为物理路径（含旧路径 bucket 前缀补全），非法路径抛 ValueError"""
    return Path(_resolve_path(bucket, obj))


def _file_missing(path: Path) -> bool:
    return not path.is_file()


# ---------------- 本地后端 ----------------

def _copy_file_local(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))


def _archive_local(timestamp: str) -> Path:
    """把旧 bucket 目录移动到 _legacy_backup_{ts}/ 下"""
    root = settings.get_vault_dir()
    backup_dir = root / f"_legacy_backup_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for bucket_name in ("original-files", "markdown"):
        src_dir = root / bucket_name
        if src_dir.is_dir() and any(src_dir.iterdir()):
            shutil.move(str(src_dir), str(backup_dir / bucket_name))
            moved.append(bucket_name)
    return backup_dir


# ---------------- MinIO 后端（未在真实环境实测，标注为实验性） ----------------

def _copy_file_minio(bucket: str, src_obj: str, dst_obj: str) -> None:
    from minio import CopySource
    client = _get_minio_client()
    client.copy_object(bucket, dst_obj, CopySource(bucket, src_obj))


def _archive_minio(timestamp: str) -> str:
    """把桶内 original-files/ 与 markdown/ 前缀对象改存为 _legacy_backup/{ts}/ 前缀"""
    from minio import DeleteObject
    client = _get_minio_client()
    for prefix in ("original-files/", "markdown/"):
        objects = list(client.list_objects(storage_settings.minio_bucket_original, prefix=prefix, recursive=True))
        for obj in objects:
            if obj.object_name.endswith("/") or obj.object_name.endswith(".gitkeep"):
                continue
            new_name = f"_legacy_backup/{timestamp}/{obj.object_name}"
            client.copy_object(
                storage_settings.minio_bucket_original,
                new_name,
                __import__("minio").CopySource(storage_settings.minio_bucket_original, obj.object_name),
            )
        if objects:
            client.remove_objects(
                storage_settings.minio_bucket_original,
                [DeleteObject(o.object_name) for o in objects if not o.object_name.endswith("/")],
            )
    return f"_legacy_backup/{timestamp}/"


def _copy_file(bucket: str, src_obj: str, src_abs: Path, dst_obj: str, dst_abs: Path) -> None:
    if settings.storage_backend == "minio":
        _copy_file_minio(bucket, src_obj, dst_obj)
    else:
        _copy_file_local(src_abs, dst_abs)


def _copy_and_verify(bucket: str, src_obj: str, src_abs: Path, dst_obj: str, dst_abs: Path) -> None:
    """复制并校验目标存在且大小与源一致，不一致抛 RuntimeError"""
    _copy_file(bucket, src_obj, src_abs, dst_obj, dst_abs)
    if settings.storage_backend == "minio":
        dst_size = _get_minio_client().stat_object(bucket, dst_obj).size
    else:
        dst_size = dst_abs.stat().st_size
    if dst_size != src_abs.stat().st_size:
        raise RuntimeError(f"复制校验失败(大小不一致): {src_obj} -> {dst_obj}")


async def migrate() -> None:
    parser = argparse.ArgumentParser(description="旧版存储迁移至新版 Vault 结构")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="仅预览迁移计划，不做任何修改")
    group.add_argument("--apply", action="store_true", help="执行迁移")
    args = parser.parse_args()

    is_minio = settings.storage_backend == "minio"
    bucket_original = settings.minio_bucket_original
    bucket_markdown = settings.minio_bucket_markdown
    vault_root = settings.get_vault_dir()

    stats = {"ok": 0, "skipped": 0, "failed": 0, "missing": 0}
    failed_notes: list[str] = []

    async with async_session() as db:
        # 预载全库已占用的对象路径，用于 base 冲突去重
        rows = (await db.execute(select(Note.original_file_path, Note.original_md_path, Note.clean_md_path))).all()
        used_objects = {p for row in rows for p in row if p}

        notes = (
            await db.execute(
                select(Note).where(
                    or_(
                        Note.original_file_path != "",
                        Note.original_md_path != "",
                        Note.clean_md_path.isnot(None),
                    )
                ).order_by(Note.created_at)
            )
        ).scalars().all()

        for note in notes:
            if _is_migrated(note):
                stats["skipped"] += 1
                continue

            prefix = vault_path.inbox_prefix(note.user_id)
            base = _compute_base(note)
            if base is None:
                stats["skipped"] += 1
                logger.info("[跳过] 无任何可迁移文件: note=%s", note.id)
                continue

            # 目标对象名（可能因冲突追加后缀）
            ext = os.path.splitext(note.original_file_path.split("/")[-1])[1].lower() if note.original_file_path else ""
            src_obj = vault_path.source_object(prefix, base, ext)
            md_obj = vault_path.markdown_object(prefix, base)
            clean_obj = vault_path.clean_object(prefix, base)
            while {src_obj, md_obj, clean_obj} & used_objects:
                base = f"{base}_{secrets.token_hex(2)}"
                src_obj = vault_path.source_object(prefix, base, ext)
                md_obj = vault_path.markdown_object(prefix, base)
                clean_obj = vault_path.clean_object(prefix, base)

            # 版本：目标重编号起点 = 该笔记已有新版版本最大号 + 1
            versions = (
                await db.execute(
                    select(NoteVersion)
                    .where(NoteVersion.note_id == note.id)
                    .order_by(NoteVersion.version_number)
                )
            ).scalars().all()
            legacy_versions = [v for v in versions if "/history/" not in (v.storage_path or "")]
            next_no = max([v.version_number for v in versions if "/history/" in (v.storage_path or "")], default=0) + 1
            version_plan = []  # (旧storage, 旧abs, 新storage, 新abs, 新版本号)
            for v in legacy_versions:
                old_abs = _resolve(bucket_markdown, v.storage_path)
                new_no = next_no
                next_no += 1
                new_storage = vault_path.history_object(prefix, note.id, new_no)
                new_abs = _resolve(bucket_markdown, new_storage)
                version_plan.append((v, v.storage_path, old_abs, new_storage, new_abs, new_no))

            # 预占目标对象名，供后续笔记 base 去重（dry-run 与 apply 行为一致）
            used_objects.update({src_obj, md_obj, clean_obj})

            # 打印/记录计划
            logger.info("=" * 70)
            logger.info("笔记 %s  %s", note.id, note.title)
            logger.info("  源文件: %r -> %s", note.original_file_path or None, src_obj if note.original_file_path else "（无）")
            logger.info("  original.md: %r -> %s", note.original_md_path or None, md_obj if note.original_md_path else "（无）")
            logger.info("  clean.md: %r -> %s", note.clean_md_path or None, clean_obj if note.clean_md_path else "（无）")
            for v, old_st, _o, new_st, _n, no in version_plan:
                logger.info("  版本 v%d: %s -> %s", v.version_number, old_st, new_st)

            if args.dry_run:
                stats["ok"] += 1
                continue

            # ---- 执行：复制 → 校验 → 更新 DB；失败回滚本次复制 ----
            created: list[Path] = []
            new_fields = {}
            try:
                # 1. 源文件
                if note.original_file_path:
                    src_abs = _resolve(bucket_original, note.original_file_path)
                    if _file_missing(src_abs):
                        logger.warning("  [缺失] 源文件不存在: %s", note.original_file_path)
                        stats["missing"] += 1
                        new_fields["original_file_path"] = ""
                    else:
                        dst_abs = _resolve(bucket_original, src_obj)
                        _copy_and_verify(bucket_original, note.original_file_path, src_abs, src_obj, dst_abs)
                        created.append(dst_abs)
                        new_fields["original_file_path"] = src_obj

                # 2. original.md
                if note.original_md_path:
                    src_abs = _resolve(bucket_markdown, note.original_md_path)
                    if _file_missing(src_abs):
                        logger.warning("  [缺失] original.md 不存在: %s", note.original_md_path)
                        stats["missing"] += 1
                        new_fields["original_md_path"] = ""
                    else:
                        dst_abs = _resolve(bucket_markdown, md_obj)
                        _copy_and_verify(bucket_markdown, note.original_md_path, src_abs, md_obj, dst_abs)
                        created.append(dst_abs)
                        new_fields["original_md_path"] = md_obj

                # 3. clean.md
                if note.clean_md_path:
                    src_abs = _resolve(bucket_markdown, note.clean_md_path)
                    if _file_missing(src_abs):
                        logger.warning("  [缺失] clean.md 不存在: %s", note.clean_md_path)
                        stats["missing"] += 1
                        new_fields["clean_md_path"] = ""
                    else:
                        dst_abs = _resolve(bucket_markdown, clean_obj)
                        _copy_and_verify(bucket_markdown, note.clean_md_path, src_abs, clean_obj, dst_abs)
                        created.append(dst_abs)
                        new_fields["clean_md_path"] = clean_obj

                # 4. 版本快照
                for v, old_st, old_abs, new_st, new_abs, new_no in version_plan:
                    if _file_missing(old_abs):
                        logger.warning("  [缺失] 版本文件不存在: %s", old_st)
                        stats["missing"] += 1
                        v.storage_path = ""
                        continue
                    _copy_and_verify(bucket_markdown, old_st, old_abs, new_st, new_abs)
                    created.append(new_abs)
                    v.storage_path = new_st
                    v.version_number = new_no

                # 5. 更新笔记路径字段并提交
                for field, value in new_fields.items():
                    setattr(note, field, value)
                await db.commit()
                # commit 后实例属性会过期，refresh 后再写穿 meta，避免读到过期状态
                await db.refresh(note)

                # 6. 状态旁载镜像（写穿到新位置；无源文件的笔记不写）
                vault_meta.write_note_meta(note)

                stats["ok"] += 1
                logger.info("  [OK] 迁移完成")
            except Exception as e:  # noqa: BLE001
                await db.rollback()
                for p in created:
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass
                stats["failed"] += 1
                failed_notes.append(note.id)
                logger.error("  [FAILED] %s: %s", note.id, e)

    # ---- 收尾：全部成功才归档旧目录 ----
    if args.apply:
        if stats["failed"] == 0:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            if is_minio:
                backup = _archive_minio(ts)
            else:
                backup = _archive_local(ts)
            logger.info("旧 bucket 目录已归档到: %s", backup)
        else:
            logger.warning("存在 %d 条失败笔记，未归档旧目录（可修复后重跑，幂等）", stats["failed"])

    logger.info("=" * 70)
    logger.info(
        "迁移报告: 成功=%d 跳过=%d 失败=%d 缺失文件=%d",
        stats["ok"], stats["skipped"], stats["failed"], stats["missing"],
    )
    if failed_notes:
        logger.info("失败笔记: %s", failed_notes)
    if args.dry_run:
        logger.info("（dry-run 仅预览，未做任何修改）")
    sys.exit(1 if stats["failed"] else 0)


if __name__ == "__main__":
    asyncio.run(migrate())
