import sqlite3
conn = sqlite3.connect('D:/test/EngramNote/backend/data/db/engramnote.db')
c = conn.cursor()

# Check note status
c.execute("SELECT id, title, status, error_message FROM notes WHERE id='93b47cff-6f38-4b83-a6f3-ab6d34fcc140'")
print("Note:", c.fetchone())

# Check cards count by chapter
c.execute("SELECT chapter_title, COUNT(*) FROM knowledge_cards WHERE note_id='93b47cff-6f38-4b83-a6f3-ab6d34fcc140' GROUP BY chapter_title ORDER BY chapter_title")
for row in c.fetchall():
    print(f"  Chapter '{row[0]}': {row[1]} cards")

# Total cards for this note
c.execute("SELECT COUNT(*) FROM knowledge_cards WHERE note_id='93b47cff-6f38-4b83-a6f3-ab6d34fcc140'")
print(f"\nTotal cards: {c.fetchone()[0]}")

# Check if there are 17 chapters (as reported by the split)
c.execute("SELECT COUNT(DISTINCT chapter_title) FROM knowledge_cards WHERE note_id='93b47cff-6f38-4b83-a6f3-ab6d34fcc140'")
print(f"Distinct chapters with cards: {c.fetchone()[0]}")

conn.close()
