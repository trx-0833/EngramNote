# -*- coding: utf-8 -*-
"""TEMP verify: print live-DB state (READ ONLY)."""
import sqlite3
from pathlib import Path

DB = Path(r"D:\engramnote\backend\data\db\engramnote.db")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.cursor()
for t in ("users", "folders", "learning_goals", "notes", "knowledge_cards", "quiz_items",
          "review_logs", "review_states", "chunks", "projects", "assessment_results",
          "card_relations", "note_material_links", "note_annotations", "note_projects",
          "note_versions", "task_runs", "daily_plans", "llm_calls", "llm_cache"):
    try:
        print("  %-20s %d" % (t, cur.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]))
    except sqlite3.Error as exc:
        print("  %-20s ERR %s" % (t, exc))
print("u1/u2 rows in users:", cur.execute("SELECT COUNT(*) FROM users WHERE id IN ('u1','u2')").fetchone()[0])
print("fixture ids present anywhere ->")
for tid in ("u1", "u2", "f1", "f2", "35a1f6b5-dd20-4d9d-91bd-b73c74783f9f"):
    rows = []
    for t in [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]:
        for c in [r[1] for r in cur.execute("PRAGMA table_info('%s')" % t)]:
            try:
                n = cur.execute('SELECT COUNT(*) FROM "%s" WHERE "%s" = ?' % (t, c), (tid,)).fetchone()[0]
            except sqlite3.Error:
                continue
            if n:
                rows.append("%s.%s" % (t, c))
    print("  %-40s %s" % (tid, rows or "（无）"))
print("users:", cur.execute("SELECT id, username, email FROM users").fetchall())
print("foreign_key_check:", cur.execute("PRAGMA foreign_key_check").fetchall())
print("integrity_check:", cur.execute("PRAGMA integrity_check").fetchone()[0])
print("quick_check:", cur.execute("PRAGMA quick_check").fetchone()[0])
print("journal_mode:", cur.execute("PRAGMA journal_mode").fetchone()[0])
con.close()
