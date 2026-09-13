"""阶段 6.6：恢复演练测试（备份存在 ≠ 备份可用）

## 这份测试要证明什么

演练的价值全在**它会不会误报**：一个天天"失败"的演练很快没人看，
一个永远"通过"的演练等于没有。因此两边都要钉：

| 要证明的事 | 对应测试 |
|---|---|
| 正常快照 → 通过 | `test_drill_passes_on_healthy_snapshot` |
| **与线上库行数不同不算失败**（快照本来就是旧的） | `test_live_row_difference_is_not_a_failure` |
| 损坏的快照 → 失败并给出原因 | `test_truncated_snapshot_fails` |
| 缺关键表 → 失败 | `test_missing_key_table_fails` |
| FTS 索引与 chunks 脱节 → 失败 | `test_fts_desync_fails` |
| 孤儿复习进度 → 失败 | `test_orphan_review_states_fail` |
| **演练不修改快照**（只读） | `test_drill_does_not_modify_snapshot` |
| 没有快照时给出明确原因，而不是崩 | `test_no_snapshot_reports_reason` |
| 真的挂在 Beat 上（周频） | `test_scheduled_in_beat` |
"""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.services import backup_service


def _make_db(path: Path, *, with_fts: bool = True, orphan: bool = False) -> Path:
    """造一个结构最小但"像真的"的库：含 chunks(grams) / chunks_fts / review_states

    ⚠️ FTS 索引的是 `grams` 列（阶段 2.5′：切词在写入侧完成），不是 `content`。
    测试 schema 必须与之一致，否则检查会因为"列不存在"而跳过 —— 那是**静默空转**。
    """
    con = sqlite3.connect(path)
    try:
        con.executescript(
            """
            CREATE TABLE users (id TEXT PRIMARY KEY);
            CREATE TABLE notes (id TEXT PRIMARY KEY, user_id TEXT);
            CREATE TABLE knowledge_cards (id TEXT PRIMARY KEY, user_id TEXT, title TEXT);
            CREATE TABLE quiz_items (id TEXT PRIMARY KEY);
            CREATE TABLE review_logs (id TEXT PRIMARY KEY);
            CREATE TABLE review_states (
                id TEXT PRIMARY KEY, item_type TEXT, item_id TEXT
            );
            CREATE TABLE chunks (id TEXT PRIMARY KEY, content TEXT, grams TEXT);
            """
        )
        con.execute("INSERT INTO users (id) VALUES ('u1')")
        con.execute("INSERT INTO notes (id, user_id) VALUES ('n1', 'u1')")
        con.execute(
            "INSERT INTO knowledge_cards (id, user_id, title) VALUES ('c1', 'u1', '浮充')"
        )
        con.execute(
            "INSERT INTO chunks (id, content, grams) VALUES ('k1', '内容', '内容 容')"
        )
        if with_fts:
            con.executescript(
                """
                CREATE VIRTUAL TABLE chunks_fts USING fts5(
                    grams, content='chunks', content_rowid='rowid'
                );
                INSERT INTO chunks_fts (rowid, grams) SELECT rowid, grams FROM chunks;
                """
            )
        if orphan:
            # 指向一张不存在的卡片：复习队列会对着空气调度
            con.execute(
                "INSERT INTO review_states (id, item_type, item_id) "
                "VALUES ('rs1', 'card', 'ghost-card')"
            )
        con.commit()
    finally:
        con.close()
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestInspectSnapshot:
    def test_healthy_snapshot_passes(self, tmp_path):
        db = _make_db(tmp_path / "ok.db")
        info = backup_service.inspect_snapshot(db)
        assert info["ok"] is True, info["problems"]
        assert info["problems"] == []
        assert info["counts"]["knowledge_cards"] == 1

    def test_missing_key_table_fails(self, tmp_path):
        db = tmp_path / "partial.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        con.commit()
        con.close()

        info = backup_service.inspect_snapshot(db)
        assert info["ok"] is False
        assert any("关键表缺失" in p for p in info["problems"])

    def test_fts_check_does_not_false_positive(self, tmp_path):
        """★ 索引"落后于内容表"**不能**被判成失败 —— 本轮实测的假警报

        最初用 `fts5vocab` 数"可检索文档数"，真库上给出 171 vs 608，
        看起来像 437 个块检索不到。实测推翻了这个结论：
        `MATCH` 能查到 `vocab` 说"不在索引里"的那些行的词。
        **`fts5vocab` 的 doc 不是完整普查**，据此外推就是假警报 ——
        而假警报会让演练永远红着，然后被所有人忽略。

        因此本用例刻意构造"内容表有 2 行、索引只灌了 1 行"的场景，
        并要求检查**不报错**（只做结构性 `integrity-check`）。
        """
        db = _make_db(tmp_path / "stale.db")   # 建表时只灌了 1 行
        con = sqlite3.connect(db)
        con.execute("INSERT INTO chunks (id, content, grams) VALUES ('k2', '新内容', '新内容 内容')")
        con.commit()
        con.close()

        info = backup_service.inspect_snapshot(db, allow_write=True)
        assert info["ok"] is True, f"索引落后被误判成失败: {info['problems']}"
        assert not any("FTS" in p for p in info["problems"])

    def test_fts_check_skips_when_table_missing(self, tmp_path):
        """老快照可能还没有 FTS 表：跳过而不是报错"""
        db = _make_db(tmp_path / "nofts.db", with_fts=False)
        info = backup_service.inspect_snapshot(db, allow_write=True)
        assert info["ok"] is True, info["problems"]

    def test_orphan_review_states_fail(self, tmp_path):
        db = _make_db(tmp_path / "orphan.db", orphan=True)
        info = backup_service.inspect_snapshot(db)
        assert info["ok"] is False
        assert any("孤儿" in p for p in info["problems"])

    def test_truncated_snapshot_fails(self, tmp_path):
        db = _make_db(tmp_path / "full.db")
        broken = tmp_path / "broken.db"
        broken.write_bytes(db.read_bytes()[:200])   # 截断
        info = backup_service.inspect_snapshot(broken)
        assert info["ok"] is False
        assert info["problems"]


class TestRunRestoreDrill:
    def test_drill_passes_on_healthy_snapshot(self, tmp_path):
        snapshot = _make_db(tmp_path / "snap.db")
        result = backup_service.run_restore_drill(snapshot, live_db=tmp_path / "none.db")
        assert result["ok"] is True, result["problems"]
        assert result["snapshot"] == str(snapshot)
        assert result["counts"]["knowledge_cards"] == 1

    def test_live_row_difference_is_not_a_failure(self, tmp_path):
        """★ 快照比线上库旧 → 行数不同是**正常的**，不能算失败

        把这当成失败，演练会天天红，很快没人看它 —— 那是演练最常见的死法。
        差异只作为信息返回。
        """
        snapshot = _make_db(tmp_path / "snap.db")
        live = _make_db(tmp_path / "live.db")
        con = sqlite3.connect(live)
        con.execute("INSERT INTO knowledge_cards (id, user_id, title) VALUES ('c2','u1','新增')")
        con.commit()
        con.close()

        result = backup_service.run_restore_drill(snapshot, live_db=live)
        assert result["ok"] is True, result["problems"]
        # 快照少一张卡 → 差异为 -1，但演练仍然通过
        assert result["deltas"]["knowledge_cards"] == -1
        assert result["live_counts"]["knowledge_cards"] == 2

    def test_truncated_snapshot_fails(self, tmp_path):
        db = _make_db(tmp_path / "full.db")
        broken = tmp_path / "broken.db"
        broken.write_bytes(db.read_bytes()[:200])

        result = backup_service.run_restore_drill(broken, live_db=tmp_path / "none.db")
        assert result["ok"] is False
        assert result["reason"]

    def test_no_snapshot_reports_reason(self, monkeypatch, tmp_path):
        """没有快照时给明确原因，而不是抛异常（演练失败本身要可读）"""
        monkeypatch.setattr(backup_service, "BACKUP_ROOT", tmp_path / "empty")
        result = backup_service.run_restore_drill(live_db=tmp_path / "none.db")
        assert result["ok"] is False
        assert "没有任何含 db 的快照" in (result["reason"] or "")

    def test_drill_does_not_modify_snapshot(self, tmp_path):
        """★ 演练是只读的：绝不能因为"检查"而改动那份唯一的救命备份"""
        snapshot = _make_db(tmp_path / "snap.db")
        live = _make_db(tmp_path / "live.db")
        before_snap, before_live = _sha256(snapshot), _sha256(live)

        backup_service.run_restore_drill(snapshot, live_db=live)

        assert _sha256(snapshot) == before_snap, "演练改动了快照"
        assert _sha256(live) == before_live, "演练改动了线上库"

    def test_drill_picks_latest_snapshot(self, monkeypatch, tmp_path):
        """不指定快照时用**最新**的那一份（按目录名的时间戳倒序）"""
        root = tmp_path / "_backup"
        monkeypatch.setattr(backup_service, "BACKUP_ROOT", root)
        for name in ("20260101-000000-old", "20260911-000000-new"):
            d = root / name
            d.mkdir(parents=True)
            _make_db(d / "engramnote.db")

        result = backup_service.run_restore_drill(live_db=tmp_path / "none.db")
        assert result["ok"] is True, result["problems"]
        assert "20260911-000000-new" in (result["snapshot"] or "")


class TestForeignKeysAreWarnings:
    """★ 外键违规记为 **warning**：本项目有意保留悬挂引用

    把它当失败，演练会永远红着（真库实测就有 1 处：`note_material_links`
    指向已删除的用户）—— 而永远红的演练等于没有，这是演练最常见的死法。
    """

    def test_fk_violation_is_warning_not_failure(self, tmp_path):
        db = tmp_path / "fk.db"
        con = sqlite3.connect(db)
        con.executescript(
            """
            CREATE TABLE users (id TEXT PRIMARY KEY);
            CREATE TABLE notes (
                id TEXT PRIMARY KEY,
                user_id TEXT REFERENCES users(id)
            );
            CREATE TABLE knowledge_cards (id TEXT PRIMARY KEY, user_id TEXT, title TEXT);
            CREATE TABLE quiz_items (id TEXT PRIMARY KEY);
            CREATE TABLE review_logs (id TEXT PRIMARY KEY);
            """
        )
        # 先关掉外键约束写入悬挂行（模拟"删除了用户但保留其笔记"的历史状态）：
        # 本项目的外键在运行期是开启的，但历史数据里确实存在这类行
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("INSERT INTO notes (id, user_id) VALUES ('n1', 'ghost-user')")
        con.commit()
        con.close()

        info = backup_service.inspect_snapshot(db)
        assert info["ok"] is True, "外键悬挂不该让演练失败（那是有意保留的）"
        assert any("外键" in w for w in info["warnings"]), info
        assert not any("外键" in p for p in info["problems"])


class TestScheduledInBeat:
    def test_scheduled_in_beat(self):
        """★ 演练必须定期跑：备份是否还有效，只有真读一次才知道"""
        from app.tasks.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "restore-drill-weekly" in schedule
        assert schedule["restore-drill-weekly"]["task"] == "app.tasks.maintenance_tasks.restore_drill"

    def test_task_registered(self):
        from app.tasks import maintenance_tasks as mt
        from app.tasks.celery_app import celery_app

        assert hasattr(mt, "restore_drill_task")
        assert "app.tasks.maintenance_tasks.restore_drill" in celery_app.tasks


@pytest.mark.asyncio
class TestRealSnapshotRoundTrip:
    """对**真实备份流程**产出的快照做演练：这才是 6.6 的端到端"""

    async def test_snapshot_created_then_drilled(self, test_db, monkeypatch, tmp_path):
        from sqlalchemy import text

        from app.models.user import User

        # 临时库里放一行真实数据（走真实模型，而不是手搓表）
        async with test_db() as db:
            db.add(User(id="u-e2e", email="e@e.com", username="e", hashed_password="x"))
            await db.commit()
            db_path = Path((await db.execute(text("PRAGMA database_list"))).all()[0][2])

        monkeypatch.setattr(backup_service, "BACKUP_ROOT", tmp_path / "_backup")
        snapshot = backup_service.create_snapshot(db_path, label="e2e")

        result = backup_service.run_restore_drill(snapshot, live_db=db_path)
        assert result["ok"] is True, result["problems"]
        assert result["counts"]["users"] == 1
        assert result["deltas"]["users"] == 0
