# -*- coding: utf-8 -*-
"""`_migrate_sqlite` 的 `quiz_items.card_id` 重建迁移测试

## 为什么补这条测试

第一版重建**把副本的 schema 搞坏了**，而**没有任何测试拦住它** ——
是靠"跑真实数据"才暴露的。损坏的具体形态（实测）：

| 症状 | 实测 |
|---|---|
| 旧表没删掉 | `_quiz_items_pre013` 仍有 3523 行 |
| 新表没数据 | `quiz_items` 0 行 |
| **外键被改写** | `review_logs.quiz_id` 指向了 `_quiz_items_pre013` |
| 产生悬空 | 178 条 `review_logs` 对不上 `quiz_items` |

根因：SQLite 默认在 `ALTER TABLE ... RENAME TO` 时**改写其它表里指向它的外键**。
所以这条测试必须断言**四件事**，缺一件就漏掉上面某一行症状：

1. `card_id` 变成可空；
2. **行数一条不少**（数据真的拷过去了）；
3. **没有残留的临时表**；
4. **别处的外键仍然指向 `quiz_items`**（这条正是第一版漏掉的）。

## 为什么在真库的**临时副本**上跑

迁移的正确性依赖真实 schema（有多少张表引用 `quiz_items`）。
用 `create_all` 造一个空库**测不出外键被改写** —— 那正是第一版"看起来对"的原因。
所以复制真库文件到临时目录，在那里跑，跑完删掉。真库只被**复制**（不做写打开）。
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine

REAL_DB = Path(__file__).resolve().parents[1] / "data" / "db" / "engramnote.db"

pytestmark = pytest.mark.skipif(not REAL_DB.exists(), reason="真库不存在，跳过")


def _run_migration(scratch: Path) -> None:
    """在 `scratch` 上跑一次 `_migrate_sqlite`（走真实的 SQLAlchemy 连接）"""
    from app.database import _migrate_sqlite

    engine = create_engine(f"sqlite:///{scratch.as_posix()}")

    class _Conn:
        """把 SQLAlchemy 的同步 Connection 喂给 `_migrate_sqlite`

        ⚠️ 必须给**真的 SQLAlchemy Connection**：`_migrate_sqlite` 内部用
        `inspect(sync_conn)`，而裸 `sqlite3.Connection` 会抛
        `NoInspectionAvailable`（第一版就是这么写的，跑不起来）。
        """

        async def run_sync(self, fn):
            with engine.begin() as conn:
                return fn(conn)

    try:
        asyncio.run(_migrate_sqlite(_Conn()))
    finally:
        engine.dispose()


def _table_names(con: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not r[0].startswith("sqlite_")
    }


def _fk_map(con: sqlite3.Connection) -> dict[str, str]:
    """`{表.列: 指向的表}` —— 用于断言外键没被改写"""
    out: dict[str, str] = {}
    for t in _table_names(con):
        for fk in con.execute(f'PRAGMA foreign_key_list("{t}")'):
            out[f"{t}.{fk[3]}"] = fk[2]
    return out


def _nullable(con: sqlite3.Connection, table: str, col: str) -> int:
    """`PRAGMA table_info` 的 `notnull` 标志（1 = NOT NULL）"""
    return {c[1]: c[3] for c in con.execute(f'PRAGMA table_info("{table}")')}[col]


def _snapshot(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {
            "tables": _table_names(con),
            "fks": _fk_map(con),
            "quiz_rows": con.execute("SELECT count(*) FROM quiz_items").fetchone()[0],
            "card_notnull": _nullable(con, "quiz_items", "card_id"),
            "columns": {c[1] for c in con.execute("PRAGMA table_info(quiz_items)")},
        }
    finally:
        con.close()


def test_quiz_items_card_id_migration_on_real_copy():
    """★ 反空转：四项都要查（可空 / 行数 / 残留表 / 外键指向）"""
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "scratch.db"
        shutil.copyfile(REAL_DB, scratch)

        before = _snapshot(scratch)

        # 前置条件：真库当前确实是 NOT NULL，否则这条测试是空转的
        assert before["card_notnull"] == 1, (
            "真库的 quiz_items.card_id 已经是可空的 —— 这条测试无法验证迁移"
        )
        assert before["quiz_rows"] > 0, "真库 quiz_items 是空的，测不出数据拷贝"

        _run_migration(scratch)
        after = _snapshot(scratch)

        # ① 可空
        assert after["card_notnull"] == 0, "card_id 仍然 NOT NULL —— 迁移没生效"
        # ② 行数一条不少
        assert after["quiz_rows"] == before["quiz_rows"], (
            f"行数变了：{before['quiz_rows']} → {after['quiz_rows']} "
            "—— 数据没拷过去或拷重了"
        )
        # ③ 没有残留临时表
        assert after["tables"] == before["tables"], (
            f"表清单变了：多了 {after['tables'] - before['tables']}，"
            f"少了 {before['tables'] - after['tables']}"
        )
        # ④ ★ 别处的外键仍然指向 quiz_items（第一版正是漏了这条）
        changed = {
            k: (before["fks"].get(k), after["fks"].get(k))
            for k in set(before["fks"]) | set(after["fks"])
            if before["fks"].get(k) != after["fks"].get(k)
        }
        assert not changed, (
            f"外键指向被改写了：{changed}\n"
            "  典型原因：`ALTER TABLE ... RENAME TO` 前没开 "
            "`PRAGMA legacy_alter_table=ON`，SQLite 把其它表里指向 quiz_items 的"
            "外键改成了指向临时表名。"
        )
        # ⑤ 列集合不变（重建不得丢列）
        assert after["columns"] == before["columns"], (
            f"列集合变了：多了 {after['columns'] - before['columns']}，"
            f"少了 {before['columns'] - after['columns']}"
        )


def test_migration_is_idempotent():
    """再跑一次不该有任何变化（已经可空了 ⇒ 直接跳过重建）"""
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "scratch.db"
        shutil.copyfile(REAL_DB, scratch)

        _run_migration(scratch)
        first = _snapshot(scratch)
        _run_migration(scratch)
        second = _snapshot(scratch)

        assert first == second, "重复跑迁移改变了数据库状态 —— 不幂等"
