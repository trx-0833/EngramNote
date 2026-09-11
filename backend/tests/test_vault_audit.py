"""
Vault 一致性校验测试（overhaul-plan 阶段 1.11）

## 校验器要解决什么

存储层是"DB 记路径 + 文件系统存内容"的双写结构，两侧都可能单独出问题：

- **DB 有、磁盘无**：界面上看得到笔记，点开是空的
- **磁盘有、DB 无**：占用空间且**无人知晓**（看不到也删不掉）
- **内容不符**：文件被截断或替换

三种在**没有校验机制**时都是静默的，只能等用户报障。

## 本文件重点

重点测**判定边界**，而不是"能跑"：校验器最危险的失效模式是
**漏报**（说"一致"而实际不一致）与**误报**（把正常状态报成问题）。
尤其要测"回收站里的文件不是孤儿"—— 那些文件在 DB 里仍有记录，
误报会诱导运维去删用户回收站里的数据。
"""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.note import Note, NoteStatus, SourceType
from app.models.user import User
from app.services import vault_path
from app.services.vault_audit_service import audit_vault


def _settings():
    from app.config import get_settings

    return get_settings()


def _storage_root() -> Path:
    from app.services.storage_service import _get_storage_root

    return _get_storage_root()


def _write(bucket: str, object_name: str, data: bytes = b"x") -> Path:
    from app.services.storage_service import _resolve_path, ensure_buckets_exist

    ensure_buckets_exist()
    path = _resolve_path(bucket, object_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _remove(bucket: str, object_name: str) -> None:
    from app.services.storage_service import _resolve_path

    path = _resolve_path(bucket, object_name)
    if path.exists():
        path.unlink()


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path):
    """把 Vault 根重定向到临时目录（测试绝不读写真实 data/storage）

    ## 为什么必须重定向，而不是"测完清掉新增目录"

    第一版用"记录进入时的顶层目录，退出时删掉新增的"来保护真实数据。
    实测发现它**根本不起作用**：这些用例的断言依赖真实的
    `audit_vault()` 返回，而 `audit_vault` 比较的是"DB 记录的对象名"与
    "磁盘枚举到的对象名"—— 磁盘侧读的是 `storage_service._get_storage_root()`。
    只要用例确实落盘、校验器确实扫盘，写入就发生在真实 Vault 里，
    清理逻辑再小心也只是事后补救。

    ## 重定向机制

    `Settings.vault_dir` 从环境变量 `VAULT_DIR` 读取（无 env 前缀），
    且 `get_vault_dir()` 的优先级是 `vault_dir > storage_dir > 默认`。
    因此：清 settings 缓存 → 设 `VAULT_DIR` → 重建 settings →
    把 `storage_service` 的模块级 `settings` 重新指向新实例即可。

    为什么必须重绑 `storage_service.settings`：它是**模块级冻结引用**
    （`settings = get_settings()` 在 import 时求值），
    `conftest._refresh_module_settings()` 的刷新名单里没有它。
    只清缓存不重绑，`_get_storage_root()` 会继续读旧实例。
    """
    import os

    from app.config import get_settings
    from app.services import storage_service

    tmp_vault = tmp_path / "vault"
    tmp_vault.mkdir(parents=True, exist_ok=True)

    old_env = os.environ.get("VAULT_DIR")
    old_settings = storage_service.settings

    os.environ["VAULT_DIR"] = str(tmp_vault)
    get_settings.cache_clear()
    storage_service.settings = get_settings()

    yield

    if old_env is None:
        os.environ.pop("VAULT_DIR", None)
    else:
        os.environ["VAULT_DIR"] = old_env
    get_settings.cache_clear()
    storage_service.settings = old_settings


async def _make_user(session_factory) -> str:
    uid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(
            id=uid, email=f"{uid[:8]}@example.com", username=f"u{uid[:8]}",
            hashed_password="x", is_active=True,
        ))
        await db.commit()
    return uid


async def _make_note(
    session_factory, user_id: str, base: str = "doc",
    *, with_files: bool = True, clean: bool = True,
) -> tuple[str, dict[str, str]]:
    """建笔记（可选落盘），返回 (note_id, {字段: 对象名})"""
    settings = _settings()
    nid = str(uuid.uuid4())
    prefix = f"{user_id}/inbox"
    paths = {
        "original_file_path": vault_path.source_object(prefix, base, ".pdf"),
        "original_md_path": vault_path.markdown_object(prefix, base),
        "clean_md_path": vault_path.clean_object(prefix, base) if clean else "",
    }
    async with session_factory() as db:
        db.add(Note(
            id=nid, user_id=user_id, title=base,
            source_type=SourceType.pdf, status=NoteStatus.cleaned,
            original_file_path=paths["original_file_path"],
            original_md_path=paths["original_md_path"],
            clean_md_path=paths["clean_md_path"] or None,
        ))
        await db.commit()

    if with_files:
        _write(settings.minio_bucket_original, paths["original_file_path"])
        _write(settings.minio_bucket_markdown, paths["original_md_path"])
        if paths["clean_md_path"]:
            _write(settings.minio_bucket_markdown, paths["clean_md_path"])
    return nid, paths


@pytest.mark.asyncio
class TestVaultAudit:
    """一致性判定的边界"""

    async def test_consistent_vault_reports_ok(self, test_db):
        """文件齐全时必须报"一致"（否则校验器会被忽略）"""
        uid = await _make_user(test_db)
        await _make_note(test_db, uid, base="good")

        async with test_db() as db:
            result = await audit_vault(db, user_id=uid)

        assert result.ok, f"误报: {[i.to_dict() for i in result.issues]}"
        assert result.notes_scanned == 1
        assert result.db_objects == 3

    async def test_source_file_is_not_an_orphan(self, test_db):
        """**关键误报防线**：原文件在 DB 里有记录，绝不能报成孤儿

        ## 这个用例锁的是一个实测过的真实缺陷

        本地模式下 bucket **没有区分能力**：`_resolve_path` 对已含
        `source/output/history/cache` 段的 Vault 路径不加 bucket 前缀，
        整棵 `data/storage` 是**一个命名空间**。

        第一版校验器却按 MinIO 的语义工作：用 `(bucket, name)` 建"应存在"
        集合，而孤儿扫描只遍历 **markdown 桶**。于是每篇笔记的原文件
        （存在 `original-files` 桶的键下）在枚举 markdown 桶时都匹配不上，
        **全部被报成孤儿**。真实数据实测：用户 `7775422b…` 的 20 篇笔记
        报出 41 条孤儿，其中 20 条正是这些原文件。

        危害不在于数字难看，而在于 `verify_vault.py` 的输出会被用来
        指导清理 —— 误报的原文件是**用户不可再生的原始资料**。
        所以这里断言的是"零孤儿"，而不是"孤儿数量正确"。

        注意 MinIO 模式下桶确有区分能力，此时原文件本就该只在
        original 桶的枚举里出现，本用例的落盘方式也随之不同。
        """
        uid = await _make_user(test_db)
        _, paths = await _make_note(test_db, uid, base="src")

        async with test_db() as db:
            result = await audit_vault(db, user_id=uid)

        orphans = [i.object_name for i in result.issues if i.kind == "orphan_file"]
        assert paths["original_file_path"] not in orphans, (
            f"DB 有记录的原始文件被误报为孤儿: {paths['original_file_path']}"
        )
        assert not orphans, f"误报孤儿: {orphans}"

    async def test_note_meta_mirror_is_not_an_orphan(self, test_db):
        """**关键误报防线**：`output/meta/` 写穿镜像不是孤儿

        `vault_meta.write_note_meta()` 在每次状态变更时把笔记全量状态写到
        `{P}/output/meta/{base}.json`，`write_project_meta()` 写用户级
        `projects.json`。这些镜像**有意不进数据库**（DB 才是状态权威源，
        镜像只是让 Vault 脱离 DB 也能读懂），因此永远不可能被 DB 引用。

        若按"无 DB 引用即孤儿"判定，**每一篇笔记都会多报一个孤儿**：
        实测真实数据里 21 条这样的噪声把 0 条真孤儿淹没了。
        校验器的价值全在信噪比，噪声即失效。
        """
        from app.services import vault_meta

        uid = await _make_user(test_db)
        note_id, paths = await _make_note(test_db, uid, base="mirrored")

        # 走真实写穿路径产出镜像
        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalar_one()
            vault_meta.write_note_meta(note)
        vault_meta.write_project_meta(uid, [])

        async with test_db() as db:
            result = await audit_vault(db, user_id=uid)

        orphans = [i.object_name for i in result.issues if i.kind == "orphan_file"]
        assert not orphans, f"output/meta 写穿镜像被误报为孤儿: {orphans}"

        # 前提校验：镜像确实落盘了，否则上面的断言是空转
        mirror = _storage_root() / f"{uid}/inbox/output/meta/mirrored.json"
        assert mirror.exists(), "写穿镜像未落盘，本用例没有真正验证到镜像路径"

    async def test_audit_never_touches_the_real_vault(self, test_db):
        """测试隔离自检：校验器读写的是临时 Vault，不是真实 data/storage

        这条断言存在的理由：这些用例此前**静默地跑在真实 Vault 上**
        （`storage_service.settings` 是模块级冻结引用，conftest 的
        `_refresh_module_settings()` 不覆盖它）。一旦隔离失效，
        用例就会往用户不可再生的资料目录里写测试文件。
        """
        uid = await _make_user(test_db)
        await _make_note(test_db, uid, base="isolated")

        root = _storage_root()
        assert root == Path(_settings().get_vault_dir())
        assert "tmp" in str(root).lower() or "pytest" in str(root).lower(), (
            f"Vault 根未重定向到临时目录，测试会写真实数据: {root}"
        )

    async def test_detects_missing_file(self, test_db):
        """DB 有记录、磁盘无文件 → missing_file"""
        uid = await _make_user(test_db)
        _, paths = await _make_note(test_db, uid, base="lost")
        _remove(_settings().minio_bucket_markdown, paths["clean_md_path"])

        async with test_db() as db:
            result = await audit_vault(db, user_id=uid)

        assert not result.ok
        assert result.counts.get("missing_file") == 1
        assert result.issues[0].object_name == paths["clean_md_path"]

    async def test_absent_clean_path_is_not_an_issue(self, test_db):
        """`clean_md_path` 为空是合法状态（尚未清洗），不能报缺失"""
        uid = await _make_user(test_db)
        await _make_note(test_db, uid, base="uncleaned", clean=False)

        async with test_db() as db:
            result = await audit_vault(db, user_id=uid)

        assert result.ok, f"把未清洗的笔记误报为缺失: {[i.to_dict() for i in result.issues]}"

    async def test_detects_orphan_file(self, test_db):
        """磁盘有文件、DB 无记录 → orphan_file"""
        uid = await _make_user(test_db)
        await _make_note(test_db, uid, base="ok")
        orphan = f"{uid}/inbox/output/markdown/never-registered.md"
        _write(_settings().minio_bucket_markdown, orphan)

        async with test_db() as db:
            result = await audit_vault(db, user_id=uid)

        orphans = [i for i in result.issues if i.kind == "orphan_file"]
        assert len(orphans) == 1, f"未检出孤儿文件: {[i.to_dict() for i in result.issues]}"
        assert orphans[0].object_name == orphan

    async def test_trash_files_are_not_orphans(self, test_db):
        """**关键误报防线**：回收站里的文件不是孤儿

        回收站中的笔记在 DB 里**仍有记录**（只是 `trashed_at` 置位），
        其文件被搬到 `{user}/trash/{note_id}/` 下。若校验器把 trash 下的
        文件报成孤儿，运维据此清理就会**删掉用户回收站里的数据**。
        """
        uid = await _make_user(test_db)
        trash_path = f"{uid}/trash/{uuid.uuid4()}/source/doc.pdf"
        _write(_settings().minio_bucket_original, trash_path)

        async with test_db() as db:
            result = await audit_vault(db, user_id=uid)

        orphans = [i for i in result.issues if i.kind == "orphan_file"]
        assert not orphans, (
            f"把回收站里的文件误报为孤儿: {[i.object_name for i in orphans]}"
        )

    async def test_gitkeep_is_not_an_orphan(self, test_db):
        """`.gitkeep` 之类的占位文件不是业务对象，不应报孤儿"""
        uid = await _make_user(test_db)
        _write(_settings().minio_bucket_markdown, f"{uid}/inbox/source/.gitkeep")

        async with test_db() as db:
            result = await audit_vault(db, user_id=uid)

        assert not [i for i in result.issues if i.kind == "orphan_file"], (
            ".gitkeep 被报成孤儿文件"
        )

    async def test_version_file_missing_is_detected(self, test_db):
        """版本文件的缺失也要检出（版本历史里的内容可能已丢）"""
        from app.models.note_version import NoteVersion, VersionSource

        uid = await _make_user(test_db)
        note_id, _ = await _make_note(test_db, uid, base="ver")
        version_path = vault_path.history_object(f"{uid}/inbox", note_id, 1)
        async with test_db() as db:
            db.add(NoteVersion(
                note_id=note_id, user_id=uid, version_number=1,
                source=VersionSource.USER_EDIT.value, content_size=10,
                storage_path=version_path,
            ))
            await db.commit()
        # 故意不落盘该版本文件

        async with test_db() as db:
            result = await audit_vault(db, user_id=uid)

        missing = [i for i in result.issues if i.kind == "missing_file"]
        assert any(i.object_name == version_path for i in missing), (
            "版本文件缺失未被检出"
        )

    async def test_hash_mismatch_detected_in_deep_mode(self, test_db):
        """深度模式：原文内容被替换（大小相同）必须检出

        大小相同但内容不同是最难发现的一类损坏 —— 只比大小的常规模式
        会放过它，这正是深度模式存在的理由。
        """
        uid = await _make_user(test_db)
        settings = _settings()
        nid = str(uuid.uuid4())
        prefix = f"{uid}/inbox"
        original = vault_path.source_object(prefix, "hash", ".pdf")

        payload = b"A" * 64
        import hashlib

        async with test_db() as db:
            db.add(Note(
                id=nid, user_id=uid, title="hash",
                source_type=SourceType.pdf, status=NoteStatus.cleaned,
                original_file_path=original, original_md_path="",
                metadata_={"file_hash": hashlib.sha256(payload).hexdigest()},
            ))
            await db.commit()

        # 写入**同样长度**但内容不同的数据
        _write(settings.minio_bucket_original, original, b"B" * 64)

        async with test_db() as db:
            shallow = await audit_vault(db, user_id=uid)
            deep = await audit_vault(db, user_id=uid, deep=True)

        assert not [i for i in shallow.issues if i.kind == "hash_mismatch"], (
            "常规模式不应做哈希比对（否则大库上会非常慢）"
        )
        assert [i for i in deep.issues if i.kind == "hash_mismatch"], (
            "深度模式未检出内容替换（大小相同）—— 这类损坏只会静默留存"
        )

    async def test_unknown_hash_baseline_does_not_report_mismatch(self, test_db):
        """没有可信哈希基线时不得下"内容被改过"的结论

        markdown 字段在元数据里没有记录哈希。若校验器拿"缺失的基线"
        去比对，会对**每一篇**笔记都报 hash_mismatch —— 噪声淹没信号。
        """
        uid = await _make_user(test_db)
        await _make_note(test_db, uid, base="nohash")

        async with test_db() as db:
            result = await audit_vault(db, user_id=uid, deep=True)

        assert not [i for i in result.issues if i.kind == "hash_mismatch"], (
            "在无哈希基线的情况下报了 hash_mismatch"
        )

    async def test_include_orphans_false_skips_scan(self, test_db):
        """`include_orphans=False` 时只做 DB→磁盘方向（快）"""
        uid = await _make_user(test_db)
        await _make_note(test_db, uid, base="quick")
        _write(_settings().minio_bucket_markdown, f"{uid}/inbox/output/markdown/orphan.md")

        async with test_db() as db:
            result = await audit_vault(db, user_id=uid, include_orphans=False)

        assert result.ok
        assert result.disk_objects == 0

    async def test_user_scope_is_respected(self, test_db):
        """`user_id` 限定生效：不得把别人的问题算到自己头上"""
        uid_a = await _make_user(test_db)
        uid_b = await _make_user(test_db)
        _, paths_b = await _make_note(test_db, uid_b, base="b")
        _remove(_settings().minio_bucket_markdown, paths_b["clean_md_path"])

        async with test_db() as db:
            result_a = await audit_vault(db, user_id=uid_a, include_orphans=False)

        assert result_a.ok, "校验他人笔记的问题被算到了本次范围内"
        assert result_a.notes_scanned == 0
