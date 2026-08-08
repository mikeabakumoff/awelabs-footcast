import sqlite3
from pathlib import Path

DB_PATH = Path("data/epl_target_teams.db")
conn = sqlite3.connect(DB_PATH)

print("Удаляю старую таблицу matches_features...")
conn.execute("DROP TABLE IF EXISTS matches_features")
conn.commit()
print("Готово — теперь запустите python setup.py")
conn.close()
