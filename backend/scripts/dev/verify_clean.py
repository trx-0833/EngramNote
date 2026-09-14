"""验证清洗结果"""
import json
import os
import sqlite3

db = sqlite3.connect(r'D:\engramnote\backend\data\db\engramnote.db')
cur = db.cursor()
cur.execute("SELECT id, title, status, clean_md_path, metadata FROM notes WHERE id LIKE '08e6102%'")
row = cur.fetchone()
if row:
    print('id:', row[0])
    print('title:', row[1])
    print('status:', row[2])
    print('clean_md_path:', row[3])
    meta = json.loads(row[4]) if row[4] else {}
    print('metadata:', json.dumps(meta, indent=2, ensure_ascii=False))

# 检查清洗文件是否存在
if row and row[3]:
    clean_path = os.path.join(r'D:\engramnote\backend\data\storage\markdown', row[3].replace('/', os.sep))
    if os.path.exists(clean_path):
        with open(clean_path, 'r', encoding='utf-8') as f:
            content = f.read()
        print(f'\nclean.md exists, {len(content)} chars')
        print('前300字:', content[:300])
    else:
        print(f'\nclean.md NOT found at: {clean_path}')

db.close()
