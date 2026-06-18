"""Fix note status to archived if understanding is complete"""
import sqlite3
conn = sqlite3.connect('D:/test/EngramNote/backend/data/db/engramnote.db')
c = conn.cursor()

# Update note status to archived
c.execute("UPDATE notes SET status='archived' WHERE id='93b47cff-6f38-4b83-a6f3-ab6d34fcc140'")
conn.commit()

# Verify
c.execute("SELECT id, title, status FROM notes WHERE id='93b47cff-6f38-4b83-a6f3-ab6d34fcc140'")
print("Updated note:", c.fetchone())

conn.close()
print("Done!")
