import sqlite3
from pathlib import Path

DB_PATH = Path("data/epl_target_teams.db")
conn = sqlite3.connect(DB_PATH)

cols = {
    "fg_player":      "TEXT",
    "fg_team":        "TEXT",
    "fg_minute":      "INTEGER",
    "real_fg_player": "TEXT",
    "real_fg_team":   "TEXT",
    "real_fg_minute": "INTEGER",
    "fg_ok":          "INTEGER",
}

for col, ctype in cols.items():
    try:
        conn.execute(f"ALTER TABLE sent_messages ADD COLUMN {col} {ctype}")
        conn.commit()
        print(f"Добавлена колонка: {col}")
    except sqlite3.OperationalError:
        print(f"Уже есть: {col}")

conn.close()
print("Готово!")
