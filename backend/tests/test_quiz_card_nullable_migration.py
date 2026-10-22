# -*- coding: utf-8 -*-
"""`quiz_items.card_id` 改可空 —— 重建逻辑的测试

## 为什么补这条测试

第一版重建**把副本的 schema 搞坏了**，而**没有任何测试拦住它** ——
是靠"跑真实数据"才暴露的。损坏形态（实测）：

| 症状 | 实测 |
|---|---|
| 旧表没删掉 | `_quiz_items_pre013` 仍有 3523 行 |
| 新表没数据 | `quiz_items` 0 行 |
| **外键被改写** | `review_logs.quiz_id` 指向了临时表 |
| 产生悬空 | 178 条 `review_logs` 对不上 `quiz_items` |

根因：SQLite 在 `ALTER TABLE ... RENAME TO` 时**改写其它表里指向它的外键**。
所以必须断言**六件事**，缺一件就漏掉上面某一行症状。

## 被测的是哪个实现

`_migrate_sqlite`（开发态启动迁移）里那段重建**已经删掉**了 ——
它在真实 schema 上连撞 SQLite 三个特有行为（外键改写 / 索引撞名 / 临时表删不掉），
改成只记 WARNING。真正的重建实现在
`backend/scripts/make_quiz_card_id_nullable.py`，这条测试**直接调它** ——
被测的就是会跑的那份代码，不是一份"测试专用"的副本。

## 为什么在真库的**临时副本**上跑

正确性依赖真实 schema（有多少张表引用 `quiz_items`）。
用 `create_all` 造空库**测不出外键被改写** —— 那正是第一版"看起来对"的原因。
所以复制真库文件到临时目录，在那里跑，跑完删掉。真库只被**复制**。
"""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REAL_DB = BACKEND / "data" / "db" / "engramnote.db"

pytestmark = pytest.mark.skipif(not REAL_DB.exists(), reason="真库不存在，跳过")


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
        }
    finally:
        con.close()


def _run_rebuild(scratch: Path) -> int:
    """调用仓库脚本里的 `migrate()`（不经过 argparse）"""
    mod = _load_script()
    assert hasattr(mod, "migrate"), "脚本没有可复用的 migrate() 入口"
    return mod.migrate(scratch)


def test_rebuild_makes_card_id_nullable_without_damage():
    """★ 反空转：六项都要查（可空 / 行数 / 列 / 残留表 / 索引 / 外键指向）"""
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "_scratch_copy" / "db" / "engram.db"
        scratch.parent.mkdir(parents=True)
        shutil.copyfile(REAL_DB, scratch)

        before = _snapshot(scratch)
        assert before["card_notnull"] == 1, (
            "真库的 quiz_items.card_id 已是可空 —— 这条测试无法验证重建"
        )
        assert before["rows"] > 0, "真库 quiz_items 是空的，测不出数据拷贝"

        rc = _run_rebuild(scratch)
        assert rc == 0, f"重建脚本返回 {rc}（非 0 表示它自己的自检没通过）"

        after = _snapshot(scratch)

        assert after["card_notnull"] == 0, "card_id 仍然 NOT NULL —— 重建没生效"
        assert after["rows"] == before["rows"], (
            f"行数变了：{before['rows']} → {after['rows']} —— 数据没拷过去或拷重了"
        )
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
        scratch.parent.mkdir(parents=True)
        shutil.copyfile(REAL_DB, scratch)

        assert _run_rebuild(scratch) == 0
        first = _snapshot(scratch)
        assert _run_rebuild(scratch) == 0
        second = _snapshot(scratch)
        assert first == second, "重复跑重建改变了数据库状态 —— 不幂等"


def test_script_refuses_real_db():
    """★ 守卫：指向真库必须拒绝（这是唯一会改 schema 的脚本）"""
    mod = _load_script()
    with pytest.raises(SystemExit):
        mod.migrate(REAL_DB)
