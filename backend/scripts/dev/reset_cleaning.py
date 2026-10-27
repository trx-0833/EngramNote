"""重置卡在 cleaning 状态的笔记"""
import sqlite3
from pathlib import Path

#: 仓库根：本文件在 `<repo>/backend/scripts/dev/`，向上三级。
#: 不写死绝对路径 —— 开源仓库里的脚本必须能在别人的克隆里直接跑。
db_path = str(
    Path(__file__).resolve().parents[3] / "backend" / "data" / "db" / "engramnote.db"
)
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
