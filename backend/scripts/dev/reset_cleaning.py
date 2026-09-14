"""重置卡在 cleaning 状态的笔记"""
import sqlite3

db_path = r'D:\engramnote\backend\data\db\engramnote.db'
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute('SELECT id, title, status FROM notes')
for row in cur.fetchall():
    print(row)

cur.execute("UPDATE notes SET status='converted', error_message=NULL WHERE status='cleaning'")
print(f'Updated {cur.rowcount} rows')

conn.commit()
conn.close()
print('Done')
