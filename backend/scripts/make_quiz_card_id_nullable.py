# -*- coding: utf-8 -*-
"""把副本的 `quiz_items.card_id` 改成可空（从**原表 DDL 派生**，不手抄列）

## 第一版错在哪

手写 `CREATE TABLE quiz_items_new (...)` 抄了 20 列，实测真实表有
`interval` 这一列没抄到：

    sqlite3.OperationalError: table quiz_items_new has no column named interval

**这正是我写在注释里的那个风险**（"手写要抄 20 多个列，抄错一列就是静默丢数据"）——
脚本当场把它变成了硬错误，而不是静默丢数据。

## 改成派生

从 `sqlite_master` 取原表的 `CREATE TABLE` 语句，**只把 `card_id` 那一行的
`NOT NULL` 去掉**，再把表名换掉。列、类型、默认值、其它约束全部原样保留 ——
没有可抄错的东西。

## 三个 SQLite 坑都要堵上

1. `PRAGMA foreign_keys=OFF` —— `DROP TABLE` 不被入向外键挡住；
2. `PRAGMA legacy_alter_table=ON` —— `RENAME TO` 只改表名、**不改写别处的外键**；
3. 先建**新名字**的表、拷数据、`DROP` 旧表、最后才 `RENAME` 成 `quiz_items`
   —— 这样"别的表指向 quiz_items"的外键在改名后依然正确；
   旧索引随旧表一起 DROP，不会撞名。
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REAL_DB = (BACKEND / "data" / "db" / "engramnote.db").resolve()

TMP_NAME = "_quiz_items_new"
OLD_NAME = "_quiz_items_old"


def derive_ddl(c: sqlite3.Cursor, table: str, new_name: str) -> str:
    """取原表 DDL，去掉 `card_id` 的 NOT NULL，并换成新表名"""
    row = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    assert row and row[0], f"拿不到 {table} 的 DDL"
    ddl = row[0]

    # 换表名（只替换第一处 CREATE TABLE 后面的名字）
    ddl = re.sub(
        r'(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)("?)' + re.escape(table) + r'\2',
        lambda m: f"{m.group(1)}{m.group(2)}{new_name}{m.group(2)}",
        ddl,
        count=1,
        flags=re.I,
    )

    # 只动 card_id 那一行：去掉它的 NOT NULL
    def _strip_notnull(m: re.Match) -> str:
        line = m.group(0)
        return re.sub(r"\s+NOT\s+NULL", "", line, flags=re.I)

    ddl, n = re.subn(
        r"^[^\n]*\bcard_id\b[^\n]*$", _strip_notnull, ddl, count=1, flags=re.M | re.I
    )
    assert n == 1, "没找到 card_id 那一行，DDL 形态与预期不符"
    assert not re.search(
        r"\bcard_id\b[^\n]*NOT\s+NULL", ddl, re.I
    ), "card_id 的 NOT NULL 没去干净"
    return ddl


def migrate(db: Path, *, confirm_real_db: str = "") -> int:
    """把 `db` 的 `quiz_items.card_id` 改成可空（**会改 schema**）

    Args:
        db: 目标数据库路径。**必须**是副本 —— 指向真库会抛 `SystemExit`。

    Returns:
        0 表示成功（且脚本自检六项全过）；1 表示自检没过。
        守卫拒绝时**抛 `SystemExit`** 而不是返回码 —— 拒绝执行用异常表达，
        调用方不可能忽略。
    """
    db = Path(db).resolve()
    if db == REAL_DB or db.name == REAL_DB.name:
        # `--confirm-real-db` 必须**原样**等于真库路径才放行。
        # 用 `--force` 那种布尔开关会被肌肉记忆敲出来；要求打全路径，
        # 执行者就必须知道自己在动哪个文件。
        if confirm_real_db.strip() and Path(confirm_real_db.strip()).resolve() == REAL_DB:
            print(f"[guard] 已显式确认，允许对真库执行：{db}")
        else:
            raise SystemExit(
                f"[guard] 拒绝执行：{db} 看着像真库（{REAL_DB}）。\n"
                f"        确实要动真库请加：--confirm-real-db \"{REAL_DB}\""
            )
    # 第二道守卫（"必须在 `_` 开头的可弃目录里"）同样认那个显式确认 ——
    # 否则真库永远过不去，而"确认开关存在但没用"比没有开关更糟。
    confirmed_real = bool(confirm_real_db.strip()) and (
        Path(confirm_real_db.strip()).resolve() == REAL_DB
    )
    if not db.parent.parent.name.startswith("_") and not confirmed_real:
        raise SystemExit(
            f"[guard] 拒绝执行：{db.parent.parent} 不是以 `_` 开头的可弃副本目录"
        )

    con = sqlite3.connect(db)
    try:
        c = con.cursor()
        before_rows = c.execute("SELECT count(*) FROM quiz_items").fetchone()[0]
        before_cols = [r[1] for r in c.execute("PRAGMA table_info(quiz_items)")]
        before_nullable = {r[1]: r[3] for r in c.execute("PRAGMA table_info(quiz_items)")}[
            "card_id"
        ]
        if before_nullable == 0:
            print("已经是可空的，无需迁移")
            return 0
        # 旧索引：随旧表 DROP 会一起消失，但把 DDL 记下来在新表上重建
        idx_ddl = [
            r[0]
            for r in c.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='quiz_items' "
                "AND sql IS NOT NULL"
            )
        ]
        print(f"迁移前：{before_rows} 行，card_id NOT NULL，{len(before_cols)} 列，{len(idx_ddl)} 个索引")

        new_ddl = derive_ddl(c, "quiz_items", TMP_NAME)
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("PRAGMA legacy_alter_table=ON")
        c.execute(new_ddl)
        col_list = ", ".join(before_cols)
        c.execute(f"INSERT INTO {TMP_NAME} ({col_list}) SELECT {col_list} FROM quiz_items")
        c.execute("DROP TABLE quiz_items")
        c.execute(f"ALTER TABLE {TMP_NAME} RENAME TO quiz_items")
        for ddl in idx_ddl:
            c.execute(ddl)
        c.execute("PRAGMA legacy_alter_table=OFF")
        c.execute("PRAGMA foreign_keys=ON")
        con.commit()

        after_rows = c.execute("SELECT count(*) FROM quiz_items").fetchone()[0]
        after_cols = [r[1] for r in c.execute("PRAGMA table_info(quiz_items)")]
        after_nullable = {r[1]: r[3] for r in c.execute("PRAGMA table_info(quiz_items)")}[
            "card_id"
        ]
        tables = [
            r[0]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%quiz%'"
            )
        ]
        idx_after = [
            r[0]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='quiz_items' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        fk_ok = True
        for fk in c.execute('PRAGMA foreign_key_list("review_logs")'):
            if fk[3] == "quiz_id":
                fk_ok = fk[2] == "quiz_items"
                print(f"  review_logs.quiz_id → {fk[2]}.{fk[4]}")

        checks = {
            "行数一致": after_rows == before_rows,
            "列集合一致": set(after_cols) == set(before_cols),
            "card_id 可空": after_nullable == 0,
            "无残留表": tables == ["quiz_items"],
            "索引已重建": len(idx_after) == len(idx_ddl),
            "外键未改写": fk_ok,
        }
        print(f"迁移后：{after_rows} 行，notnull={after_nullable}，表={tables}，索引={len(idx_after)}")
        for k, v in checks.items():
            print(f"  {'✔' if v else '✘'} {k}")
        return 0 if all(checks.values()) else 1
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="把副本的 quiz_items.card_id 改成可空")
    ap.add_argument("--db", required=True, help="数据库路径（默认拒绝真库）")
    ap.add_argument(
        "--confirm-real-db",
        default="",
        help=f"确实要动真库时，把真库路径原样传进来（{REAL_DB}）",
    )
    args = ap.parse_args()
    return migrate(Path(args.db), confirm_real_db=args.confirm_real_db)


if __name__ == "__main__":
    sys.exit(main())
