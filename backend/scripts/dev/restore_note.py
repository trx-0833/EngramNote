import sqlite3
conn = sqlite3.connect('data/db/engramnote.db')
c = conn.cursor()
c.execute("UPDATE notes SET status='cleaned', error_message=NULL WHERE id='2900ffa6-35cc-42df-8cf9-fed60c6de23a'")
conn.commit()
conn.close()
print('已恢复')
