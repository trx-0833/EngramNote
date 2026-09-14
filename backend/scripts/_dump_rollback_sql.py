# -*- coding: utf-8 -*-
"""TEMP: generate exact rollback INSERTs for the 5 deleted rows from the snapshot (READ ONLY)."""
import sqlite3
from pathlib import Path

SNAP = Path(r"D:\engramnote\_backup\20260914-132932-pre-fixture-user-cleanup\engramnote.db")
con = sqlite3.connect(f"file:{SNAP}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()


def quote(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


JOBS = [
    ("users", "id IN ('u1','u2')"),
    ("folders", "user_id IN ('u1','u2')"),
    ("learning_goals", "user_id IN ('u1','u2')"),
]
for table, where in JOBS:
    rows = cur.execute('SELECT * FROM "%s" WHERE %s' % (table, where)).fetchall()
    print("-- %s: %d row(s)" % (table, len(rows)))
    for row in rows:
        cols = list(row.keys())
        vals = ", ".join(quote(row[c]) for c in cols)
        print(
            'INSERT INTO "%s" (%s) VALUES (%s);'
            % (table, ", ".join('"%s"' % c for c in cols), vals)
        )
    print()
con.close()
