# -*- coding: utf-8 -*-
"""`quiz_items.card_id` 改可空 —— 重建逻辑的测试

## 为什么补这条测试

第一版重建**把副本的 schema 搞坏了**，而**没有任何测试拦住它** ——
是靠「跑真实数据」才暴露的。损坏形态（实测）：

| 症状 | 实测 |
|---|---|
| 旧表没删掉 | `_quiz_items_pre013` 仍有 3523 行 |
| 新表没数据 | `quiz_items` 0 行 |
| **外键被改写** | `review_logs.quiz_id` 指向了临时表 |
| 产生悬空 | 178 条 `review_logs` 对不上 `quiz_items` |

根因：SQLite 在 `ALTER TABLE ... RENAME TO` 时**改写其它表里指向它的外键**。

## ★ 样本用**合成 schema**，不用真库（2026-10-22 改）

原先直接拿真库当样本，前置断言「真库当前是 NOT NULL」。
**真库已经迁移过了** ⇒ 那个前置条件不再成立。

与其删掉断言（那就变成空转），改成**自己造样本**。合成 schema 比真库**更好**：
这三个 SQLite 行为只取决于**结构特征**，与业务列无关 ——

1. 一张有 `card_id NOT NULL` 的表；
2. **另一张表有指向它的外键**（触发「外键被改写」）；
3. **表上有索引**（触发「索引撞名」）。

（第一版曾试图「复制真库再把 NOT NULL 加回去」，那条正则把 DDL 改坏了 ——
合成 schema 没有这个问题。）

## 被测的是哪个实现

`_migrate_sqlite`（开发态启动迁移）里那段重建**已经删掉**了 ——
它在真实 schema 上连撞 SQLite 三个特有行为，改成只记 WARNING。
真正的重建实现在 `backend/scripts/make_quiz_card_id_nullable.py`，
这条测试**直接调它的 `migrate()`** —— 被测的就是会跑的那份代码。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

#: 合成样本：结构与真实 schema 的**关键特征**一致
SCHEMA = """
CREATE TABLE knowledge_cards (id VARCHAR NOT NULL PRIMARY KEY, note_id VARCHAR);
CREATE TABLE quiz_items (
    id VARCHAR NOT NULL PRIMARY KEY,
    card_id VARCHAR NOT NULL REFERENCES knowledge_cards(id),
    question TEXT NOT NULL,
    interval INTEGER DEFAULT 0
);
CREATE INDEX ix_quiz_items_card_id ON quiz_items (card_id);
CREATE INDEX ix_quiz_items_question ON quiz_items (question);
CREATE TABLE review_logs (
    id VARCHAR NOT NULL PRIMARY KEY,
    quiz_id VARCHAR REFERENCES quiz_items(id),
    card_id VARCHAR REFERENCES knowledge_cards(id)
);
"""


def _load_script():
    """按路径加载 `scripts/make_quiz_card_id_nullable.py`

    ⚠️ 不能 `import scripts.xxx`：`scripts/` 不是包（没有 `__init__.py`）。
    """
    path = BACKEND / "scripts" / "make_quiz_card_id_nullable.py"
    spec = importlib.util.spec_from_file_location("_mk_nullable", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_mk_nullable"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_sample(dst: Path) -> None:
    """造一份待迁移的样本（`card_id` 是 NOT NULL，且有入向外键与索引）"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(dst)
    try:
        con.executescript(SCHEMA)
        con.execute("INSERT INTO knowledge_cards (id, note_id) VALUES ('k1', 'n1')")
        for i in range(5):
            con.execute(
                "INSERT INTO quiz_items (id, card_id, question, interval) VALUES (?,?,?,?)",
                (f"q{i}", "k1", f"题目{i}", i),
            )
        con.execute("INSERT INTO review_logs (id, quiz_id, card_id) VALUES ('r1','q0','k1')")
        con.commit()
    finally:
        con.close()


def _table_names(con: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not r[0].startswith("sqlite_")
    }


def _fk_map(con: sqlite3.Connection) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in _table_names(con):
        for fk in con.execute(f'PRAGMA foreign_key_list("{t}")'):
            out[f"{t}.{fk[3]}"] = fk[2]
    return out


def _snapshot(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {
            "tables": _table_names(con),
            "fks": _fk_map(con),
            "rows": con.execute("SELECT count(*) FROM quiz_items").fetchone()[0],
            "card_notnull": {
                r[1]: r[3] for r in con.execute("PRAGMA table_info(quiz_items)")
            }["card_id"],
            "columns": {r[1] for r in con.execute("PRAGMA table_info(quiz_items)")},
            "indexes": {
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='quiz_items' AND name NOT LIKE 'sqlite_%'"
                )
            },
            # 数据内容也要在（不只行数 —— 拷错列时行数是对的）
            "questions": sorted(
                r[0] for r in con.execute("SELECT question FROM quiz_items")
            ),
        }
    finally:
        con.close()


def test_rebuild_makes_card_id_nullable_without_damage():
    """★ 反空转：七项都要查（可空 / 行数 / 内容 / 列 / 残留表 / 索引 / 外键指向）"""
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "_scratch_copy" / "db" / "engram.db"
        _make_sample(scratch)

        before = _snapshot(scratch)
        # 前置：样本确实具备会触发那三个 bug 的结构（否则测试空转）
        assert before["card_notnull"] == 1, "样本不是 NOT NULL —— 会空转"
        assert before["rows"] == 5
        assert before["fks"].get("review_logs.quiz_id") == "quiz_items", (
            "样本缺少「别的表指向 quiz_items」的外键 —— 测不出外键被改写那个 bug"
        )
        assert len(before["indexes"]) == 2, "样本索引数不对"

        rc = _load_script().migrate(scratch)
        assert rc == 0, f"重建脚本返回 {rc}（非 0 表示它自己的自检没通过）"

        after = _snapshot(scratch)

        assert after["card_notnull"] == 0, "card_id 仍然 NOT NULL —— 重建没生效"
        assert after["rows"] == before["rows"], "行数变了 —— 数据没拷过去或拷重了"
        assert after["questions"] == before["questions"], "题目内容变了（拷错列？）"
        assert after["columns"] == before["columns"], (
            f"列集合变了：多了 {after['columns'] - before['columns']}，"
            f"少了 {before['columns'] - after['columns']}"
        )
        assert after["tables"] == before["tables"], (
            f"表清单变了：多了 {after['tables'] - before['tables']}，"
            f"少了 {before['tables'] - after['tables']}"
        )
        assert after["indexes"] == before["indexes"], (
            f"索引集合变了：多了 {after['indexes'] - before['indexes']}，"
            f"少了 {before['indexes'] - after['indexes']}"
        )
        changed = {
            k: (before["fks"].get(k), after["fks"].get(k))
            for k in set(before["fks"]) | set(after["fks"])
            if before["fks"].get(k) != after["fks"].get(k)
        }
        assert not changed, (
            f"外键指向被改写了：{changed}\n"
            "  典型原因：`ALTER TABLE ... RENAME TO` 前没开 "
            "`PRAGMA legacy_alter_table=ON`。"
        )


def test_rebuild_is_idempotent():
    """已经可空时再跑一次不该有任何变化"""
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "_scratch_copy" / "db" / "engram.db"
        _make_sample(scratch)
        mod = _load_script()

        assert mod.migrate(scratch) == 0
        first = _snapshot(scratch)
        assert mod.migrate(scratch) == 0
        second = _snapshot(scratch)
        assert first == second, "重复跑重建改变了数据库状态 —— 不幂等"


def test_script_refuses_real_db_without_confirmation():
    """★ 守卫：不显式确认时，指向真库必须拒绝（这是唯一会改 schema 的脚本）"""
    mod = _load_script()
    if not mod.REAL_DB.exists():
        pytest.skip("真库不存在")
    with pytest.raises(SystemExit):
        mod.migrate(mod.REAL_DB)
    with pytest.raises(SystemExit):
        mod.migrate(mod.REAL_DB, confirm_real_db="D:/some/wrong/path.db")


def test_script_refuses_non_copy_dir():
    """守卫第二道：不在 `_` 开头的可弃目录里也要拒绝"""
    mod = _load_script()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "not_a_copy" / "db" / "x.db"
        _make_sample(target)
        with pytest.raises(SystemExit):
            mod.migrate(target)
