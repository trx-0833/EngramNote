import sqlite3
conn = sqlite3.connect('data/db/engramnote.db')
c = conn.cursor()
c.execute("SELECT error_message FROM notes ORDER BY created_at DESC LIMIT 1")
row = c.fetchone()
if row and row[0]:
    print(row[0][:1000])
else:
    print("No error message")
conn.close()
