"""阶段 6.3：`011_refresh_tokens` 迁移的可执行性与「模型 ↔ 迁移」一致性

## 为什么值得单独测迁移

本项目的 schema 有**两个**来源：

    Base.metadata.create_all()   → 全新库（开发/测试）
    alembic/versions/*.py        → 存量库升级

两者不一致时，症状是"新库正常、老库缺列/缺索引"，而且只在真库上复现
（`database.py` 里那段"新增字段必须同时登记"的警告就是为这类事故写的）。
本文件把 `refresh_tokens` 这张表的两个来源**对起来比**：列名、可空性、
主键、索引名（含 jti 的唯一性）都必须一致。

## 为什么要用 Operations.context 手工执行单个迁移

迁移链（001→011）在本项目里**从未整体生效过**（见 docs/overhaul-plan.md §2.6 M-1：
001 直接在 `ADD COLUMN` 上起步，没有建基础表的基线；且 alembic.ini 指向
PostgreSQL 而 env.py 用同步 API）。因此 `alembic upgrade head` 在空库上
第一步就会失败 —— 那是既有问题，不属于本项。这里改为**只执行 011 这一个
迁移**（它只新建一张表、只依赖 users 表存在），从而真的验证了：
DDL 能跑通、约束/索引真的建出来了、downgrade 能干净回退。
"""

import importlib.util
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import UniqueConstraint, create_engine, inspect
from sqlalchemy.exc import IntegrityError

from app.models.refresh_token import RefreshToken

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "011_refresh_tokens.py"
)


def _load_migration():
    """按**路径**加载迁移模块（`011_refresh_tokens` 不是合法的模块名）"""
    spec = importlib.util.spec_from_file_location("mig_011_refresh_tokens", _MIGRATION)
    assert spec and spec.loader, f"找不到迁移文件: {_MIGRATION}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Scratch:
    """临时 SQLite 库（**绝不用真实库**：建在系统临时目录里，用完即删）"""

    def __init__(self) -> None:
        self.path = os.path.join(
            tempfile.mkdtemp(prefix="engramnote-mig011-"), f"{uuid.uuid4().hex[:8]}.db"
        )
        self.engine = create_engine(f"sqlite:///{self.path}")
        self.conn = self.engine.connect()
        # 011 的外键指向 users：先造一张最小的 users（迁移链没有基线，
        # 只能自己提供前置表）
        self.conn.exec_driver_sql("CREATE TABLE users (id VARCHAR NOT NULL PRIMARY KEY)")
        self.conn.commit()

    def run(self, func) -> None:
        """在 Alembic 迁移上下文里执行迁移函数（`alembic.op` 代理靠它解析）"""
        ctx = MigrationContext.configure(self.conn)
        with Operations.context(ctx):
            func()
        self.conn.commit()

    def execute(self, sql: str, params: dict | None = None) -> None:
        self.conn.exec_driver_sql(sql, params or {})
        self.conn.commit()

    def scalar(self, sql: str):
        return self.conn.exec_driver_sql(sql).scalar()

    def table_exists(self, name: str) -> bool:
        return name in inspect(self.conn).get_table_names()

    def columns(self, table: str) -> dict:
        """{列名: {type, nullable, pk}}"""
        inspector = inspect(self.conn)
        primary = set(inspector.get_pk_constraint(table).get("constrained_columns") or [])
        return {
            col["name"]: {
                "type": str(col["type"]).upper(),
                "nullable": bool(col["nullable"]),
                "pk": col["name"] in primary,
            }
            for col in inspector.get_columns(table)
        }

    def indexes(self, table: str) -> dict:
        """{索引名: {unique, columns}}（不含 SQLite 自动为主键建的内部索引）"""
        return {
            idx["name"]: {
                "unique": bool(idx.get("unique")),
                "columns": list(idx.get("column_names") or []),
            }
            for idx in inspect(self.conn).get_indexes(table)
        }

    def close(self) -> None:
        self.conn.close()
        self.engine.dispose()
        try:
            os.remove(self.path)
        except OSError:  # pragma: no cover - Windows 占用时留给系统清理
            pass


@pytest.fixture
def scratch():
    s = _Scratch()
    yield s
    s.close()


_INSERT_ROW = (
    "INSERT INTO refresh_tokens "
    "(id, jti, user_id, family_id, issued_at, expires_at, created_at, updated_at) "
    "VALUES (:id, :jti, :user_id, 'f-1', '2026-01-01', '2026-02-01', "
    "'2026-01-01', '2026-01-01')"
)


class TestMigrationRuns:
    def test_upgrade_creates_table_and_indexes(self, scratch):
        """★ 011 能真的执行出这张表（不是"看起来写得对"）"""
        migration = _load_migration()
        assert migration.revision == "011_refresh_tokens"
        assert migration.down_revision == "010_user_reminder_settings", (
            "down_revision 必须接在最新迁移之后，否则 Alembic 会认为链断裂"
        )

        scratch.run(migration.upgrade)

        assert scratch.table_exists("refresh_tokens")
        cols = scratch.columns("refresh_tokens")
        for expected in (
            "id", "jti", "user_id", "family_id", "issued_at", "expires_at",
            "revoked_at", "replaced_by_jti", "created_at", "updated_at",
        ):
            assert expected in cols, f"迁移没有建出 {expected} 列"
        # 可空性直接决定语义：revoked_at/replaced_by_jti 必须可为 NULL
        assert cols["revoked_at"]["nullable"] is True
        assert cols["replaced_by_jti"]["nullable"] is True
        assert cols["jti"]["nullable"] is False
        assert cols["id"]["pk"] is True

        indexes = scratch.indexes("refresh_tokens")
        assert indexes["ix_refresh_tokens_jti"]["unique"] is True, "jti 缺少唯一索引"
        for name in ("ix_refresh_tokens_user_id", "ix_refresh_tokens_family_id",
                     "ix_refresh_tokens_expires_at"):
            assert name in indexes, f"迁移缺少索引 {name}"

    def test_jti_uniqueness_is_enforced_by_the_database(self, scratch):
        """★ 唯一性是**数据库**在保证，不是"服务层会检查"

        服务层检查挡不住并发插入；而 jti 撞车的后果是"两枚令牌指向同一行"，
        撤销其中一枚会连带另一枚。这里直接插两行相同 jti，必须报错。
        """
        migration = _load_migration()
        scratch.run(migration.upgrade)
        scratch.execute("INSERT INTO users (id) VALUES ('u-1')")
        # 第一行正常插入（否则下面的报错可能只是别的原因）
        scratch.execute(_INSERT_ROW, {"id": uuid.uuid4().hex, "jti": "same-jti", "user_id": "u-1"})
        # 第二行同 jti 必须被数据库拒绝
        with pytest.raises(IntegrityError):
            scratch.conn.exec_driver_sql(
                _INSERT_ROW, {"id": uuid.uuid4().hex, "jti": "same-jti", "user_id": "u-1"}
            )
            scratch.conn.commit()
        scratch.conn.rollback()
        assert scratch.scalar("SELECT COUNT(*) FROM refresh_tokens") == 1

    def test_fk_cascades_when_user_is_deleted(self, scratch):
        """删除用户时其令牌记录一并消失（外键 ON DELETE CASCADE）

        不级联的后果是被删用户留下永远无法使用的令牌行（还占着 jti 唯一索引），
        并且"用户已删除但会话好像还在"会让排查者误判。
        """
        migration = _load_migration()
        scratch.run(migration.upgrade)
        fks = inspect(scratch.conn).get_foreign_keys("refresh_tokens")
        assert any(
            fk.get("referred_table") == "users"
            and (fk.get("options") or {}).get("ondelete") == "CASCADE"
            for fk in fks
        ), f"refresh_tokens.user_id 缺少 ON DELETE CASCADE 外键: {fks}"

        scratch.execute("PRAGMA foreign_keys=ON")
        scratch.execute("INSERT INTO users (id) VALUES ('u-1')")
        scratch.execute(_INSERT_ROW, {"id": "t-1", "jti": "j-1", "user_id": "u-1"})
        assert scratch.scalar("SELECT COUNT(*) FROM refresh_tokens") == 1

        scratch.execute("DELETE FROM users WHERE id='u-1'")
        assert scratch.scalar("SELECT COUNT(*) FROM refresh_tokens") == 0, (
            "用户删除后令牌行仍在（外键没有 ON DELETE CASCADE 生效）"
        )

    def test_downgrade_drops_the_table(self, scratch):
        """回退必须干净（否则回滚版本会留下孤儿表，下次升级又建一遍）"""
        migration = _load_migration()
        scratch.run(migration.upgrade)
        assert scratch.table_exists("refresh_tokens")

        scratch.run(migration.downgrade)
        assert not scratch.table_exists("refresh_tokens")


class TestModelMigrationParity:
    """模型与迁移必须描述同一张表（新库与老库不能有分歧）"""

    @staticmethod
    def _model_indexes() -> set:
        """从模型对象算出它期望的索引名（与 create_all 的产物一致）"""
        names = {idx.name for idx in RefreshToken.__table__.indexes if idx.name}
        for column in RefreshToken.__table__.columns:
            if column.index or column.unique:
                names.add(f"ix_{RefreshToken.__tablename__}_{column.name}")
        return names

    def test_column_sets_match(self, scratch):
        migration = _load_migration()
        scratch.run(migration.upgrade)

        from_migration = set(scratch.columns("refresh_tokens"))
        from_model = {c.name for c in RefreshToken.__table__.columns}
        assert from_migration == from_model, (
            "迁移与模型描述的列不一致 —— 新库与老库会分叉: "
            f"仅迁移有 {sorted(from_migration - from_model)} / "
            f"仅模型有 {sorted(from_model - from_migration)}"
        )

    def test_nullability_matches(self, scratch):
        migration = _load_migration()
        scratch.run(migration.upgrade)
        migrated = scratch.columns("refresh_tokens")
        for column in RefreshToken.__table__.columns:
            assert migrated[column.name]["nullable"] is bool(column.nullable), (
                f"{column.name} 的可空性与模型不一致"
            )

    def test_indexes_match(self, scratch):
        """迁移建的索引必须覆盖模型声明的索引（尤其是 jti 唯一）

        只比较"模型要求的都在"这个方向：迁移多建索引不算缺陷（只是冗余），
        少建才是（查询会全表扫、唯一性会丢失）。
        """
        migration = _load_migration()
        scratch.run(migration.upgrade)

        actual = scratch.indexes("refresh_tokens")
        expected = self._model_indexes()
        assert expected, "模型没有声明任何索引 —— 这条断言会空转"
        missing = {name for name in expected if name not in actual}
        assert not missing, f"迁移缺少模型声明的索引: {sorted(missing)}"
        assert actual["ix_refresh_tokens_jti"]["unique"] is True, (
            "jti 的唯一性必须落到数据库"
        )
        assert actual["ix_refresh_tokens_jti"]["columns"] == ["jti"]

    def test_unique_constraints_match(self, scratch):
        """模型若声明了 UniqueConstraint/unique 列，迁移也必须建出来"""
        migration = _load_migration()
        scratch.run(migration.upgrade)

        declared = {
            tuple(sorted(c.name for c in uc.columns))
            for uc in RefreshToken.__table__.constraints
            if isinstance(uc, UniqueConstraint)
        }
        # 本模型把唯一性表达在 jti 列上（unique=True），因此断言
        # "要么有 UniqueConstraint、要么该列有唯一索引"，不允许两者都缺
        unique_columns = {
            name for name, meta in scratch.indexes("refresh_tokens").items()
            if meta["unique"]
        }
        assert declared == set() or declared == {("jti",)}
        assert unique_columns == {"ix_refresh_tokens_jti"}, (
            f"唯一索引不止/不及 jti 一个: {unique_columns}"
        )


def test_migration_does_not_touch_the_real_db():
    """★ 守卫：本文件的验证只发生在系统临时目录里

    真实库路径一旦被迁移测试碰到，就是一次不可回退的 schema 变更。
    这里显式断言临时库路径与真实库不同 —— 便宜，且能挡住"有人把
    fixture 改成用真实库"这种改动。
    """
    real_db = os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "db", "engramnote.db",
        )
    )
    scratch = _Scratch()
    try:
        assert os.path.abspath(scratch.path) != real_db
        assert os.path.dirname(os.path.abspath(scratch.path)) != os.path.dirname(real_db)
    finally:
        scratch.close()


def test_sqlite_scratch_is_really_sqlite(scratch):
    """守卫：上面所有断言都建立在"这是 SQLite"之上（别被换成别的方言）"""
    assert scratch.conn.dialect.name == "sqlite"
    assert isinstance(scratch.conn.connection.dbapi_connection, sqlite3.Connection)
