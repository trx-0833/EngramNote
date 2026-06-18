"""查找已完成的笔记用户"""
import sqlite3
conn = sqlite3.connect("data/db/engramnote.db")
c = conn.cursor()
c.execute("SELECT n.user_id, u.email, n.id, n.title, n.status FROM notes n JOIN users u ON n.user_id=u.id WHERE n.status='archived'")
for r in c.fetchall():
    print(f"Email: {r[1]} | Note: {r[3]} | Status: {r[4]} | NoteID: {r[2]}")
conn.close()
