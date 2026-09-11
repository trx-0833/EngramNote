"""
物理删除笔记的文件清理一致性测试（阶段 1.12 / 文档 §2.6 M-10）

## 本轮审计推翻了文档对 M-10 的描述

文档写的是"文件 `move` 失败时仅记 warning，DB 状态照常提交"，并断言有
"5 种磁盘/DB 分歧场景"。逐条实测后，**按文档描述的两个具体机制都不成立**：

1. **`trash_note` 搬家失败**：代码在失败时**把路径字段保留为旧值**
   （`note_service.py:293-299`，注释自陈"失败保留原值避免丢信息"）。
   因此 `purge_note` 按 `note.original_file_path` 删文件时仍指向**原处**，
   文件照样被删掉 —— 不产生孤儿文件。见
   `test_trash_move_failure_still_allows_purge`。

2. **meta 旁载漏删**：一度怀疑 `purge_note` 按 trash 前缀反推
   `{prefix}/output/meta/{base}.json` 会漏掉 inbox 下的 meta。**实测不会** ——
   它用 `parts[:-2]` 从**当前实际路径**取前缀，trash 路径取出的是
   `{user_id}/trash/{note_id}`，正确指向 trash 下的 meta；而 trash 搬家
   已把 meta 一并搬走。见 `test_meta_sidecar_is_removed_after_trash_then_purge`。

## 真正存在的缺陷：删除失败被降级为 warning，文件被永久孤立

`purge_note` 的 7 处 `delete_file` 全部是：

    try:
        delete_file(bucket, path)
    except Exception as e:
        logger.warning(...)          # ← 只记日志
    ...
    await db.delete(note)            # ← 笔记记录照删
    await db.commit()

于是任意**瞬时**故障（Windows 文件被占用、权限不足、网络盘抖动）都会让
文件留在磁盘上，而 DB 记录被删除 —— 之后**再也没有任何机制知道它的存在**，
清理也就无从谈起。这不是"分歧"，是**不可发现、不可恢复的泄漏**，
且恰好命中 M-10 描述的后果："用户看到已彻底删除，但文件仍在磁盘上"。

修复方向（`delete_file` 增加有界重试）与验证见
`test_transient_delete_failure_is_retried`。
"""

import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.note import Note, NoteStatus, SourceType
from app.models.user import User
from app.services import vault_path
from app.services.note_service import purge_note, trash_note


async def _make_user(session_factory) -> str:
    uid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(
            id=uid, email=f"{uid[:8]}@example.com", username=f"u{uid[:8]}",
            hashed_password="x", is_active=True,
        ))
        await db.commit()
    return uid


async def _make_note(session_factory, user_id: str, base: str = "doc") -> tuple[str, str, str]:
    """建一条带文件的笔记，返回 (note_id, 原始文件对象名, md 对象名)"""
    nid = str(uuid.uuid4())
    prefix = f"{user_id}/inbox"
    original = vault_path.source_object(prefix, base, ".pdf")
    markdown = vault_path.markdown_object(prefix, base)
    async with session_factory() as db:
        db.add(Note(
            id=nid, user_id=user_id, title=base,
            source_type=SourceType.pdf, status=NoteStatus.cleaned,
            original_file_path=original, original_md_path=markdown,
        ))
        await db.commit()
    return nid, original, markdown


def _write(bucket: str, object_name: str, data: bytes = b"x") -> Path:
    """在本地存储里写一个对象，返回其磁盘路径"""
    from app.services.storage_service import _resolve_path, ensure_buckets_exist

    ensure_buckets_exist()
    path = _resolve_path(bucket, object_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _exists_on_disk(bucket: str, object_name: str) -> bool:
    from app.services.storage_service import _resolve_path

    return _resolve_path(bucket, object_name).exists()


def _settings():
    from app.config import get_settings

    return get_settings()


@pytest.fixture(autouse=True)
def _isolate_storage():
    """把测试写入的存储目录在用例结束后清理掉

    ## 为什么需要（本轮实测）

    这些用例会真实调用 `storage_service` 落盘（本地后端写
    `backend/data/storage/`）。第一版没有清理，跑一轮就在真实存储目录里
    留下 **26 个以随机 user_id 命名的目录** —— 与用户真实数据混在一起，
    只能靠创建时间人工分辨。真实用户目录（如 `7775422b`）是不可再生的，
    一旦误删就是数据损失。

    ## 做法

    记录用例开始前**已存在**的顶层目录，结束后只删除新增的那些。
    这样即使存储目录里本来就有真实用户数据，也不会被碰到。
    """
    from app.services.storage_service import _get_storage_root

    root = _get_storage_root()
    root.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in root.iterdir() if p.is_dir()}

    yield

    for entry in list(root.iterdir()):
        if entry.is_dir() and entry.name not in before:
            shutil.rmtree(entry, ignore_errors=True)


@pytest.mark.asyncio
class TestPurgeFileConsistency:
    """purge 必须把该笔记的**全部**磁盘痕迹清干净"""

    async def test_meta_sidecar_is_removed_after_trash_then_purge(self, test_db):
        """**核心**：进过回收站再彻底删除后，inbox 下的 meta 旁载也必须消失

        这是 M-10 在当前代码下的真实载体：
          - 回收站搬家把文件路径改成 trash 前缀（成功路径）
          - purge 按新路径反推 prefix → 只删 trash 下的 meta
          - inbox/output/meta/{base}.json 成为永久残留
        """
        settings = _settings()
        md_bucket = settings.minio_bucket_markdown
        uid = await _make_user(test_db)
        note_id, original, markdown = await _make_note(test_db, uid, base="leak")

        prefix = f"{uid}/inbox"
        inbox_meta = vault_path.meta_object(prefix, "leak")

        # 落盘：原始文件 + markdown + inbox 下的 meta 旁载
        _write(settings.minio_bucket_original, original)
        _write(md_bucket, markdown)
        _write(md_bucket, inbox_meta, b'{"title":"leak"}')
        assert _exists_on_disk(md_bucket, inbox_meta), "测试前提：meta 未写入"

        # 1) 移入回收站（文件应被搬到 trash 目录）
        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
            await trash_note(db, note)

        # 2) 彻底删除
        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
            assert note is not None
            await purge_note(db, note)
            await db.commit()

        assert not _exists_on_disk(md_bucket, inbox_meta), (
            "物理删除后 inbox 下的 meta 旁载仍留在磁盘上 —— "
            "用户看到『已彻底删除』，但笔记元数据文件还在（M-10 隐私泄漏）"
        )

    async def test_main_files_are_removed(self, test_db):
        """主文件（原文 + markdown）必须被删除（基线，防回归）"""
        settings = _settings()
        uid = await _make_user(test_db)
        note_id, original, markdown = await _make_note(test_db, uid, base="main")

        _write(settings.minio_bucket_original, original)
        _write(settings.minio_bucket_markdown, markdown)

        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
            await trash_note(db, note)
        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
            await purge_note(db, note)
            await db.commit()

        for bucket, obj in (
            (settings.minio_bucket_original, original),
            (settings.minio_bucket_markdown, markdown),
        ):
            assert not _exists_on_disk(bucket, obj), f"文件残留: {obj}"

    async def test_purge_without_trash_cleans_everything(self, test_db):
        """未进回收站直接物理删除：主文件与 meta 都应清干净"""
        settings = _settings()
        md_bucket = settings.minio_bucket_markdown
        uid = await _make_user(test_db)
        note_id, original, markdown = await _make_note(test_db, uid, base="direct")
        inbox_meta = vault_path.meta_object(f"{uid}/inbox", "direct")

        _write(settings.minio_bucket_original, original)
        _write(md_bucket, markdown)
        _write(md_bucket, inbox_meta)

        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
            await purge_note(db, note)
            await db.commit()

        assert not _exists_on_disk(md_bucket, inbox_meta), "meta 未清理"

    async def test_purge_survives_missing_files(self, test_db):
        """文件本就不存在时 purge 不应报错（幂等）"""
        uid = await _make_user(test_db)
        note_id, _, _ = await _make_note(test_db, uid, base="nofile")

        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
            await purge_note(db, note)
            await db.commit()

        async with test_db() as db:
            left = (await db.execute(select(Note.id).where(Note.id == note_id))).scalars().first()
        assert left is None

    async def test_trash_move_failure_still_allows_purge(self, test_db):
        """回收站搬家失败（文件缺失等）后，purge 仍须成功且不留残留

        模拟真实故障：文件在 trash 阶段不可移动（这里用"文件根本不存在"
        代表最坏情况 —— 搬家静默失败）。
        purge 不应因文件层故障而失败，也不应留下任何磁盘痕迹。

        这条同时**证伪**了文档 M-10 的一个说法：搬家失败时路径字段保留旧值，
        所以 purge 仍能找到并删除文件。
        """
        settings = _settings()
        md_bucket = settings.minio_bucket_markdown
        uid = await _make_user(test_db)
        note_id, original, markdown = await _make_note(test_db, uid, base="broken")
        inbox_meta = vault_path.meta_object(f"{uid}/inbox", "broken")

        # 只写 meta，不写主文件（模拟主文件缺失导致搬家失败）
        _write(md_bucket, inbox_meta)

        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
            await trash_note(db, note)
        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
            await purge_note(db, note)
            await db.commit()

        assert not _exists_on_disk(md_bucket, inbox_meta), (
            "回收站搬家失败后，inbox 下的 meta 仍残留"
        )


@pytest.mark.asyncio
class TestTransientDeleteFailure:
    """瞬时删除失败必须被重试，而不是把文件永久孤立

    ## 缺陷

    `purge_note` 的 7 处 `delete_file` 都是"失败只记 warning"，随后照常
    `db.delete(note)`。任意瞬时故障（Windows 文件被占用、权限抖动）
    都会留下一个**再无人知晓**的孤儿文件 —— DB 里已经没有这条笔记了。

    这与"删除失败就该让用户重试"的直觉相反：删除是幂等的，重试很便宜；
    而孤立文件是不可发现、不可恢复的。

    ## 为什么用 monkeypatch 注入失败

    真实触发"文件被占用"需要另一个进程持有句柄，在测试里不可靠。
    这里直接让 `delete_file` 前 N 次抛 OSError，模拟瞬时故障 ——
    我们验证的是**调用方的重试逻辑**，不是操作系统行为。
    """

    async def test_transient_delete_failure_is_retried(self, test_db, monkeypatch):
        """前两次删除失败、第三次成功 → 文件最终必须被删掉

        注入点是**底层实现** `_delete_file_local`，不是 `delete_file` 本身 ——
        重试逻辑就在 `delete_file` 内部，替换它等于把被测代码换掉
        （第一版测试正是这么写的，于是"没有重试"这个断言永远测不到真实行为）。
        """
        from app.services import storage_service

        settings = _settings()
        md_bucket = settings.minio_bucket_markdown
        uid = await _make_user(test_db)
        note_id, original, markdown = await _make_note(test_db, uid, base="retry")

        _write(settings.minio_bucket_original, original)
        _write(md_bucket, markdown)

        real_delete_local = storage_service._delete_file_local
        calls: dict[str, int] = {}

        def flaky_delete_local(bucket: str, object_name: str):
            """对 markdown 的底层删除前两次抛错，其余放行"""
            if object_name == markdown:
                calls[object_name] = calls.get(object_name, 0) + 1
                if calls[object_name] <= 2:
                    raise OSError(11, "文件被其他进程占用（模拟瞬时故障）")
            return real_delete_local(bucket, object_name)

        monkeypatch.setattr(
            storage_service, "_delete_file_local", flaky_delete_local,
        )

        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
            await purge_note(db, note)
            await db.commit()

        assert calls.get(markdown, 0) >= 3, (
            f"删除失败后没有重试（底层只被调用 {calls.get(markdown, 0)} 次）—— "
            "文件会被永久孤立，DB 里再也查不到它"
        )
        assert not _exists_on_disk(md_bucket, markdown), (
            "重试后文件仍在磁盘上 —— 孤儿文件未清理"
        )

    async def test_retry_does_not_fire_when_file_absent(self, test_db, monkeypatch):
        """文件本就不存在时不得白白重试（`_delete_file_local` 是空操作）

        重试只应对**真实失败**生效。若把"文件不存在"也当失败重试，
        每次 purge 都会多出无谓的 sleep。
        """
        from app.services import storage_service

        uid = await _make_user(test_db)
        note_id, _, markdown = await _make_note(test_db, uid, base="absent")

        real_delete_local = storage_service._delete_file_local
        calls = {"n": 0}

        def counting_delete_local(bucket: str, object_name: str):
            if object_name == markdown:
                calls["n"] += 1
            return real_delete_local(bucket, object_name)

        monkeypatch.setattr(storage_service, "_delete_file_local", counting_delete_local)

        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
            await purge_note(db, note)
            await db.commit()

        assert calls["n"] == 1, (
            f"文件不存在时也重试了（调用了 {calls['n']} 次）—— 无谓延迟"
        )

    async def test_persistent_failure_does_not_block_purge(self, test_db, monkeypatch):
        """持续失败时 purge 仍须完成（不能让用户永远删不掉笔记）

        与上一条是一对：重试是有界的，耗尽后**必须继续删除 DB 记录**。
        否则一次权限问题就会让用户卡在"删不掉"—— 那正是我们要避免的另一面。
        """
        from app.services import storage_service

        settings = _settings()
        uid = await _make_user(test_db)
        note_id, original, markdown = await _make_note(test_db, uid, base="stubborn")
        _write(settings.minio_bucket_original, original)

        real_delete_local = storage_service._delete_file_local

        def always_fail(bucket: str, object_name: str):
            if object_name == markdown:
                raise OSError(13, "权限不足（模拟持续故障）")
            return real_delete_local(bucket, object_name)

        monkeypatch.setattr(storage_service, "_delete_file_local", always_fail)

        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
            await purge_note(db, note)
            await db.commit()

        async with test_db() as db:
            left = (await db.execute(select(Note.id).where(Note.id == note_id))).scalars().first()
        assert left is None, (
            "文件删除持续失败导致笔记删不掉 —— 用户被卡死，"
            "这比留下一个孤儿文件更糟"
        )

