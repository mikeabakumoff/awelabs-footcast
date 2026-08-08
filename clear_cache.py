import sqlite3
conn = sqlite3.connect('data/epl_target_teams.db')
conn.execute('DELETE FROM live_stats')
conn.commit()
conn.close()
print('Кеш агентов очищен')
