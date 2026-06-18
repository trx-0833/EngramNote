"""检查 quiz_items 表和最近的错误"""
import sqlite3
conn = sqlite3.connect('D:/test/EngramNote/backend/data/db/engramnote.db')
c = conn.cursor()

# Check quiz_items
c.execute("SELECT COUNT(*) FROM quiz_items")
print(f"Total quiz items: {c.fetchone()[0]}")

# Check if there were any partial writes
c.execute("SELECT id FROM quiz_items LIMIT 5")
rows = c.fetchall()
print(f"Sample quiz IDs: {rows}")

# Check note status
c.execute("SELECT id, status, error_message FROM notes WHERE id='93b47cff-6f38-4b83-a6f3-ab6d34fcc140'")
print(f"Note: {c.fetchone()}")

conn.close()
