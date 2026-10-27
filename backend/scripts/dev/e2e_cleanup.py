# -*- coding: utf-8 -*-
"""清理 E2E 测试遗留数据（测试用户及其笔记/项目/文件夹/vault）"""
import os
import shutil
import sqlite3
from pathlib import Path

#: 仓库根：本文件在 `<repo>/backend/scripts/dev/`，向上三级到仓库根。
#: 此前这里写死的是作者本机的绝对路径，
#: 别人 clone 下来只会连到一个不存在的库 —— 改成按文件位置推导。
_REPO_ROOT = Path(__file__).resolve().parents[3]

DB = str(_REPO_ROOT / "backend" / "data" / "db" / "engramnote.db")
VAULT = str(_REPO_ROOT / "backend" / "data" / "storage")

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

users = db.execute(
    "SELECT id, username FROM users WHERE username LIKE 'e2e%' OR username LIKE 'e2eb%'"
).fetchall()
print("测试用户:", [(u["username"], u["id"]) for u in users])

for u in users:
    uid = u["id"]
    for t in ["note_material_links"]:
        try:
            db.execute(
                f"DELETE FROM {t} WHERE material_note_id IN (SELECT id FROM notes WHERE user_id=?) "
                f"OR personal_note_id IN (SELECT id FROM notes WHERE user_id=?)", (uid, uid))
        except Exception as e:
            print(f"{t}: {e}")
    for t in ["knowledge_cards", "quiz_items", "note_versions", "notes", "note_projects"]:
        try:
            db.execute(f"DELETE FROM {t} WHERE note_id IN (SELECT id FROM notes WHERE user_id=?)", (uid,))
        except Exception as e:
            print(f"{t} by note: {e}")
    for t in ["review_logs", "assessment_results", "card_relations", "note_annotations",
              "learning_goals", "daily_plans"]:
        try:
            db.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
        except Exception as e:
            print(f"{t} by user: {e}")
    try:
        db.execute("DELETE FROM notes WHERE user_id=?", (uid,))
        db.execute("DELETE FROM projects WHERE user_id=?", (uid,))
        db.execute("DELETE FROM folders WHERE user_id=?", (uid,))
        db.execute("DELETE FROM users WHERE id=?", (uid,))
    except Exception as e:
        print("final:", e)
    p = os.path.join(VAULT, uid)
    if os.path.exists(p):
        try:
            shutil.rmtree(p)
            print("removed vault dir:", p)
        except Exception as e:
            print("vault rmtree:", e)
db.commit()
print("cleanup done")
