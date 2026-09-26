import sqlite3
from pathlib import Path

DB_PATH = Path("data/epl_target_teams.db")
conn = sqlite3.connect(DB_PATH)

print("=== СТАТИСТИКА БД ===")


matches = conn.execute("SELECT COUNT(*) FROM matches_raw").fetchone()[0]
print(f"Матчей в БД: {matches:,}")


teams = conn.execute("""
    SELECT COUNT(DISTINCT team_name) FROM (
        SELECT home_team as team_name FROM matches_raw
        UNION
        SELECT away_team FROM matches_raw
    )
""").fetchone()[0]
print(f"Уникальных команд: {teams}")


try:
    players = conn.execute("SELECT COUNT(DISTINCT player_name) FROM player_season_stats").fetchone()[0]
    print(f"Игроков в статистике: {players:,}")
except:
    players = 0
    print("player_season_stats: нет данных")


try:
    injuries = conn.execute("SELECT COUNT(*) FROM injuries").fetchone()[0]
    print(f"Записей травм: {injuries:,}")
    injured_players = conn.execute("SELECT COUNT(DISTINCT player_name) FROM injuries").fetchone()[0]
    print(f"Уникальных травмированных игроков: {injured_players:,}")
except:
    print("injuries: нет данных")


try:
    goals = conn.execute("SELECT COUNT(*) FROM goal_events").fetchone()[0]
    print(f"Голов в БД: {goals:,}")
    scorers = conn.execute("SELECT COUNT(DISTINCT player_name) FROM scorer_stats").fetchone()[0]
    print(f"Бомбардиров: {scorers:,}")
except:
    print("goal_events: нет данных")


try:
    lineups = conn.execute("SELECT COUNT(*) FROM match_lineups").fetchone()[0]
    print(f"Составов: {lineups}")
except:
    print("match_lineups: нет данных")


try:
    xg = conn.execute("SELECT COUNT(*) FROM matches_raw WHERE home_xg IS NOT NULL").fetchone()[0]
    print(f"Матчей с xG данными: {xg:,}")
except:
    print("xG: нет данных")


try:
    preds = conn.execute("SELECT COUNT(*) FROM match_predictions").fetchone()[0]
    print(f"Прогнозов API: {preds}")
except:
    print("predictions: нет данных")

conn.close()
