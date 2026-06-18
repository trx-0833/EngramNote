import sqlite3
conn = sqlite3.connect('data/db/engramnote.db')
c = conn.cursor()
note_id = '3dd66854-fced-4abe-8222-e41576e642c3'
c.execute('UPDATE notes SET status=?, error_message=NULL WHERE id=?', ('cleaned', note_id))
c.execute('DELETE FROM knowledge_cards WHERE note_id=?', (note_id,))
conn.commit()
print(f'Reset note {note_id} to cleaned, deleted old cards')
conn.close()
