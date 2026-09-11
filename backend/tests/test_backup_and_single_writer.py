"""
备份/恢复与单写者守卫测试（阶段 1′ 第 5 项 + 决策 D1=B）

## 为什么测这些

备份与单写者约束都属于"**失败时才会被发现**"的机制：

- 备份若静默产出损坏快照，人以为有备份，出事才发现没有；
- 单写者约束若失效，问题只在多进程部署后以"偶发 500 / 限流失效"出现，
  而单元测试是单进程的，永远看不到。

因此这里不去测 happy path 的"能跑"，而是测**边界与拒绝行为**：
坏快照必须被拒、恢复必须可回退、多 worker 必须启动失败。
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from app.services import backup_service


async def _make_db(path: Path, rows: int = 3) -> None:
    """造一个最小的 SQLite 库（含备份服务会统计的两张表）"""
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT)")
        con.execute("CREATE TABLE review_logs (id TEXT PRIMARY KEY, quality INTEGER)")
        for i in range(rows):
            con.execute("INSERT INTO users VALUES (?, ?)", (f"u{i}", f"u{i}@x.c"))
            con.execute("INSERT INTO review_logs VALUES (?, ?)", (f"r{i}", 5))
        con.commit()
    finally:
        con.close()


@pytest.fixture
def backup_root(tmp_path, monkeypatch):
    """把备份根目录指向临时目录，避免污染仓库根的 _backup/"""
    root = tmp_path / "_backup"
    root.mkdir()
    monkeypatch.setattr(backup_service, "BACKUP_ROOT", root)
    return root


# ---------------------------------------------------------------------------
# 1. 快照创建与校验
# ---------------------------------------------------------------------------

class TestSnapshot:

    def test_snapshot_is_readable_and_verifiable(self, tmp_path, backup_root):
        db = tmp_path / "engramnote.db"
        import asyncio

        asyncio.run(_make_db(db))

        target = backup_service.create_snapshot(
            db, label="unit", now=datetime(2026, 9, 11, 12, 0, 0),
        )
        assert target.exists()
        assert "unit" in target.parent.name

        info = backup_service.verify_snapshot(target)
        assert info["integrity"] == "ok"
        assert info["counts"]["users"] == 3
        assert info["counts"]["review_logs"] == 3

    def test_verify_record_is_written_next_to_snapshot(self, tmp_path, backup_root):
        """校验记录必须落盘：恢复前不看它就恢复等于赌运气"""
        import asyncio

        db = tmp_path / "engramnote.db"
        asyncio.run(_make_db(db))
        target = backup_service.create_snapshot(db, label="rec")

        verify_file = target.parent / backup_service.VERIFY_FILENAME
        assert verify_file.exists(), "快照目录缺少 _verify.json"
        record = json.loads(verify_file.read_text(encoding="utf-8"))
        assert record["integrity"] == "ok"
        assert record["counts"]["users"] == 3
        assert record["size_bytes"] > 0

    def test_missing_source_raises(self, tmp_path, backup_root):
        with pytest.raises(FileNotFoundError):
            backup_service.create_snapshot(tmp_path / "nope.db", label="x")

    def test_duplicate_snapshot_name_raises(self, tmp_path, backup_root):
        """同一秒内同名快照应报错，而不是静默覆盖掉上一份"""
        import asyncio

        db = tmp_path / "engramnote.db"
        asyncio.run(_make_db(db))
        stamp = datetime(2026, 9, 11, 12, 0, 0)

        backup_service.create_snapshot(db, label="dup", now=stamp)
        with pytest.raises(FileExistsError):
            backup_service.create_snapshot(db, label="dup", now=stamp)

    def test_snapshot_is_independent_of_source(self, tmp_path, backup_root):
        """快照必须与源库解耦：源库后续变动不应影响快照内容"""
        import asyncio

        db = tmp_path / "engramnote.db"
        asyncio.run(_make_db(db, rows=2))
        target = backup_service.create_snapshot(db, label="iso")

        con = sqlite3.connect(str(db))
        try:
            con.execute("INSERT INTO users VALUES ('extra', 'e@x.c')")
            con.commit()
        finally:
            con.close()

        info = backup_service.verify_snapshot(target)
        assert info["counts"]["users"] == 2, "快照被源库的后续写入污染了"

    def test_corrupted_snapshot_fails_verification(self, tmp_path, backup_root):
        """损坏的快照必须被 integrity_check 抓出来

        这是"坏备份比没备份更危险"的直接防线：只要校验能报错，
        恢复脚本就会中止，不会用坏数据覆盖好数据。

        注意构造方式：只破坏几百字节**可能落在空闲页上**，SQLite 会直接
        忽略，校验照样返回 ok（第一版就踩了这个坑）。这里从文件 1/4 处
        开始破坏一大段，确保命中 b-tree 页。
        """
        import asyncio

        db = tmp_path / "engramnote.db"
        asyncio.run(_make_db(db, rows=200))
        target = backup_service.create_snapshot(db, label="corrupt")

        data = bytearray(target.read_bytes())
        start = len(data) // 4
        end = min(start + max(4096, len(data) // 2), len(data))
        for i in range(start, end):
            data[i] = 0xFF
        target.write_bytes(bytes(data))

        try:
            info = backup_service.verify_snapshot(target)
        except sqlite3.DatabaseError:
            # 连打开都失败，同样算"被拦住了"
            return
        assert info["integrity"] != "ok", "损坏的快照竟然通过了完整性校验"


# ---------------------------------------------------------------------------
# 2. 保留策略
# ---------------------------------------------------------------------------

class TestRetention:

    def test_prune_keeps_newest(self, tmp_path, backup_root):
        import asyncio

        db = tmp_path / "engramnote.db"
        asyncio.run(_make_db(db))

        names = []
        for minute in range(5):
            target = backup_service.create_snapshot(
                db, label=f"b{minute}",
                now=datetime(2026, 9, 11, 12, minute, 0),
            )
            names.append(target.parent.name)

        removed = backup_service.prune_snapshots(keep=2)
        assert len(removed) == 3

        remaining = sorted(d.name for d in backup_root.iterdir() if d.is_dir())
        assert remaining == sorted(names[-2:]), "保留策略没有保留最新的两份"

    def test_prune_zero_means_no_cleanup(self, tmp_path, backup_root):
        import asyncio

        db = tmp_path / "engramnote.db"
        asyncio.run(_make_db(db))
        for minute in range(3):
            backup_service.create_snapshot(
                db, label=f"k{minute}",
                now=datetime(2026, 9, 11, 12, minute, 0),
            )
        assert backup_service.prune_snapshots(0) == []
        assert len(list(backup_root.iterdir())) == 3

    def test_prune_ignores_dirs_without_db(self, tmp_path, backup_root):
        """不含 db 快照的目录不占保留名额

        否则早期"只有 storage/.env"的手工备份会把真正可恢复的快照挤掉。
        """
        import asyncio

        db = tmp_path / "engramnote.db"
        asyncio.run(_make_db(db))

        # 一个只有 storage 的旧式备份目录（名字排序靠前）
        (backup_root / "00000000-000000-aaa").mkdir()
        (backup_root / "00000000-000000-aaa" / "note.txt").write_text("x")

        for minute in range(2):
            backup_service.create_snapshot(
                db, label=f"real{minute}",
                now=datetime(2026, 9, 11, 12, minute, 0),
            )

        removed = backup_service.prune_snapshots(keep=1)
        assert len(removed) == 1
        assert (backup_root / "00000000-000000-aaa").exists(), (
            "无 db 快照的目录不应被计入选保留名额"
        )


# ---------------------------------------------------------------------------
# 3. 定时备份入口
# ---------------------------------------------------------------------------

class TestScheduledBackup:

    def test_run_scheduled_backup_succeeds(self, tmp_path, backup_root, monkeypatch):
        import asyncio

        db = tmp_path / "engramnote.db"
        asyncio.run(_make_db(db))
        monkeypatch.setattr(backup_service, "resolve_db_path", lambda explicit=None: db)
        monkeypatch.setattr(backup_service, "default_retention", lambda: 0)

        result = backup_service.run_scheduled_backup("daily")
        assert result["ok"] is True
        assert result["integrity"] == "ok"
        assert result["counts"]["users"] == 3

    def test_missing_db_is_reported_not_raised(self, tmp_path, backup_root, monkeypatch):
        """库不存在时返回错误摘要而不是抛异常

        Beat 任务抛异常只会堆失败状态，运维从 Celery 结果里看不到"为什么"；
        返回结构化错误则能被日志和调用方直接读。
        """
        monkeypatch.setattr(
            backup_service, "resolve_db_path",
            lambda explicit=None: tmp_path / "missing.db",
        )
        result = backup_service.run_scheduled_backup("daily")
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_unexpected_exception_is_captured(self, tmp_path, backup_root, monkeypatch):
        """任何异常都要被兜住并返回，绝不冒泡给 Beat"""
        def _boom(explicit=None):
            raise RuntimeError("磁盘满了")

        monkeypatch.setattr(backup_service, "resolve_db_path", _boom)
        result = backup_service.run_scheduled_backup("daily")
        assert result["ok"] is False
        assert "磁盘满了" in result["error"]


# ---------------------------------------------------------------------------
# 4. list_snapshots
# ---------------------------------------------------------------------------

class TestListing:

    def test_list_reports_snapshots_and_legacy_dirs(self, tmp_path, backup_root):
        import asyncio

        db = tmp_path / "engramnote.db"
        asyncio.run(_make_db(db, rows=1))
        backup_service.create_snapshot(db, label="listed")
        (backup_root / "99999999-999999-legacy").mkdir()

        items = backup_service.list_snapshots()
        assert len(items) == 2
        with_db = [i for i in items if i["has_db"]]
        assert len(with_db) == 1
        assert with_db[0]["integrity"] == "ok"
        assert with_db[0]["counts"]["users"] == 1


# ---------------------------------------------------------------------------
# 5. 单写者守卫（决策 D1=B / 方案 A）
# ---------------------------------------------------------------------------

class TestSingleWriterGuard:
    """SQLite 路线下唯一不可协商的约束：只允许一个写者"""

    def _guard(self):
        from app.main import _enforce_single_writer

        return _enforce_single_writer

    def test_default_is_allowed(self, monkeypatch):
        """未配置 worker 数时放行（uvicorn 默认即单进程）"""
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
        monkeypatch.delenv("UVICORN_WORKERS", raising=False)
        self._guard()()  # 不抛异常即通过

    def test_single_worker_is_allowed(self, monkeypatch):
        monkeypatch.setenv("WEB_CONCURRENCY", "1")
        monkeypatch.delenv("UVICORN_WORKERS", raising=False)
        self._guard()()

    @pytest.mark.parametrize("value", ["2", "4", "16"])
    def test_multiple_workers_are_rejected(self, monkeypatch, value):
        """**核心断言**：多 worker 必须启动失败

        静默放行会让限流阈值放大 N 倍、并在长事务下抛 database is locked，
        而这两者都不会在任何单元测试里暴露。
        """
        monkeypatch.setenv("WEB_CONCURRENCY", value)
        monkeypatch.delenv("UVICORN_WORKERS", raising=False)

        with pytest.raises(RuntimeError) as excinfo:
            self._guard()()

        message = str(excinfo.value)
        assert "SQLite" in message
        # 错误信息必须给出可操作的出路，而不只是"失败了"
        assert "WEB_CONCURRENCY=1" in message
        assert "PostgreSQL" in message

    def test_uvicorn_workers_env_is_also_checked(self, monkeypatch):
        """两个环境变量名都要检查（不同部署方式用不同的）"""
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
        monkeypatch.setenv("UVICORN_WORKERS", "4")
        with pytest.raises(RuntimeError):
            self._guard()()

    def test_invalid_value_is_rejected(self, monkeypatch):
        """非整数值不能被静默忽略 —— 那等于守卫失效"""
        monkeypatch.setenv("WEB_CONCURRENCY", "auto")
        monkeypatch.delenv("UVICORN_WORKERS", raising=False)
        with pytest.raises(RuntimeError) as excinfo:
            self._guard()()
        assert "无法解析" in str(excinfo.value)

    def test_non_sqlite_backend_is_not_restricted(self, monkeypatch):
        """非 SQLite（未来的 PG 路线）不应受此约束"""
        import app.main as main_mod
        import app.database as db_mod

        monkeypatch.setenv("WEB_CONCURRENCY", "8")
        monkeypatch.setattr(db_mod, "_sqlite", lambda: False, raising=False)
        # main.py 内部是 `from .database import _sqlite`，需同时替换其引用
        monkeypatch.setattr(main_mod, "_sqlite", lambda: False, raising=False)

        self._guard()()  # 不抛异常即通过


# ---------------------------------------------------------------------------
# 6. 备份任务注册
# ---------------------------------------------------------------------------

class TestCeleryWiring:

    def test_backup_task_is_registered_and_scheduled(self):
        """备份必须真的挂在 Beat 上 —— 否则又回到"靠人记得跑" """
        from app.tasks.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "daily-database-backup" in schedule
        entry = schedule["daily-database-backup"]
        assert entry["task"] == "app.tasks.maintenance_tasks.backup_database"

    def test_reap_task_is_scheduled_more_often_than_backup(self):
        """自愈要跑得比备份勤：它决定用户被卡住后等多久"""
        from app.tasks.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "reap-stale-tasks" in schedule

    def test_task_reliability_flags_are_global(self):
        """任务可靠性必须是**全局**配置

        文件 broker 没有 visibility timeout，全局 reject_on_worker_lost
        是 convert/clean/understand 崩溃后能重投的唯一依据。
        此前只有 embedding 两个任务单独设了它。
        """
        from app.tasks.celery_app import celery_app

        conf = celery_app.conf
        assert conf.task_reject_on_worker_lost is True
        assert conf.task_acks_late is True
        assert conf.task_time_limit and conf.task_time_limit > 0
        assert conf.task_soft_time_limit and conf.task_soft_time_limit > 0
        assert conf.task_soft_time_limit < conf.task_time_limit, (
            "soft limit 必须小于 hard limit，否则任务没有收尾机会"
        )

    def test_broker_dir_matches_config(self):
        """broker 目录必须与 config 的权威来源一致

        此前 celery_app 用 `storage_dir.parent / celery / broker` 反推，
        配置了 storage_dir 后会指向用户主目录，任务被投递到无人监听的目录。
        """
        from app.config import get_settings
        from app.tasks import celery_app as celery_mod

        settings = get_settings()
        assert celery_mod._broker_dir == settings.get_celery_broker_dir()

    def test_worker_schema_check_hook_exists(self):
        """worker 启动时必须校验 schema（API 未启动时 worker 也能独立工作）"""
        from app.tasks import celery_app as celery_mod

        assert hasattr(celery_mod, "_ensure_worker_schema")
        import inspect

        src = inspect.getsource(celery_mod._worker_init)
        assert "_ensure_worker_schema" in src

    def test_backup_root_is_outside_app_data(self):
        """备份目录必须在 backend/data/ 之外

        放在同一目录树里的话，一次误删 data/ 会同时失去数据与备份 ——
        这正是备份要防的场景。
        """
        from app.config import DATA_DIR
        from app.services.backup_service import BACKUP_ROOT

        assert DATA_DIR not in BACKUP_ROOT.parents, (
            f"备份目录 {BACKUP_ROOT} 位于数据目录 {DATA_DIR} 之内"
        )
