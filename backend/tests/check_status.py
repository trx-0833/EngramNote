import sqlite3
conn = sqlite3.connect('data/db/engramnote.db')
c = conn.cursor()
c.execute("SELECT id, status, error_message FROM notes ORDER BY created_at DESC LIMIT 3")
for row in c.fetchall():
    err = row[2][:200] if row[2] else None
    print(f"  id={row[0][:8]}... status={row[1]} error={err}")
c.execute("SELECT COUNT(*) FROM knowledge_cards")
print(f"Knowledge cards: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM quiz_items")
print(f"Quiz items: {c.fetchone()[0]}")
conn.close()
