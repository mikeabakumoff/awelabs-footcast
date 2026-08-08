import sqlite3
from pathlib import Path

DB_PATH = Path("data/epl_target_teams.db")
conn = sqlite3.connect(DB_PATH)

count = conn.execute("SELECT COUNT(*) FROM sent_messages").fetchone()[0]
print(f"Записей в sent_messages: {count}")

conn.execute("DELETE FROM sent_messages")
conn.commit()

count2 = conn.execute("SELECT COUNT(*) FROM sent_messages").fetchone()[0]
print(f"После очистки: {count2}")
conn.close()
print("Готово! Теперь запустите: python run_bot.py")
