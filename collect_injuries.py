import time
import sqlite3
import logging
import requests
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

DB_PATH = Path("data/epl_target_teams.db")


LEAGUES = {
    39:  "🏴󠁧󠁢󠁥󠁮󠁧󠁿 АПЛ",
    140: "🇪🇸 Ла Лига",
    78:  "🇩🇪 Бундеслига",
    135: "🇮🇹 Серия А",
    61:  "🇫🇷 Лига 1",
}

CURRENT_SEASON = 2025

SCHEMA = """
CREATE TABLE IF NOT EXISTS injuries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at   TEXT,
    league_id    INTEGER,
    season       INTEGER,
    team_id      INTEGER,
    team_name    TEXT,
    player_id    INTEGER,
    player_name  TEXT,
    injury_type  TEXT,
    reason       TEXT,
    fixture_id   INTEGER,
    fixture_date TEXT,
    UNIQUE(player_id, fixture_id)
)
"""

SCHEMA_STANDINGS = """
CREATE TABLE IF NOT EXISTS api_standings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at  TEXT,
    league_id   INTEGER,
    season      INTEGER,
    team_id     INTEGER,
    team_name   TEXT,
    rank        INTEGER,
    points      INTEGER,
    wins        INTEGER,
    draws       INTEGER,
    losses      INTEGER,
    goals_for   INTEGER,
    goals_against INTEGER,
    form        TEXT,
    UNIQUE(league_id, season, team_id)
)
"""


def load_env():
    env = {}
    try:
        with open("env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


_env = load_env()
API_KEY = _env.get("API_FOOTBALL_KEY", "")
BASE_URL = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io",
}


def api_get(endpoint, params=None):
    if not API_KEY:
        return None
    try:
        r = requests.get(
            BASE_URL + endpoint,
            headers=HEADERS,
            params=params,
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            remaining = r.headers.get("x-ratelimit-requests-remaining", "?")
            log.debug(f"  API requests remaining: {remaining}")
            return data
        log.warning(f"  API HTTP {r.status_code}: {endpoint}")
        return None
    except Exception as e:
        log.error(f"  API error: {e}")
        return None


def fetch_injuries(conn, league_id, season):
    log.info(f"  Загружаю травмы league={league_id} season={season}...")
    data = api_get("/injuries", {"league": league_id, "season": season})
    if not data or "response" not in data:
        log.warning(f"  Нет данных о травмах для league={league_id}")
        return 0

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved = 0
    for item in data["response"]:
        try:
            player  = item.get("player", {})
            team    = item.get("team", {})
            fixture = item.get("fixture", {})

            conn.execute("""
                INSERT OR REPLACE INTO injuries
                (fetched_at, league_id, season, team_id, team_name,
                 player_id, player_name, injury_type, reason,
                 fixture_id, fixture_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                now, league_id, season,
                team.get("id"), team.get("name"),
                player.get("id"), player.get("name"),
                player.get("type"), player.get("reason"),
                fixture.get("id"), (fixture.get("date") or "")[:10],
            ))
            saved += 1
        except Exception as e:
            log.debug(f"  Травма пропущена: {e}")

    conn.commit()
    log.info(f"  Сохранено травм: {saved}")
    return saved


def fetch_standings(conn, league_id, season):
    log.info(f"  Загружаю таблицу league={league_id} season={season}...")
    data = api_get("/standings", {"league": league_id, "season": season})
    if not data or "response" not in data:
        log.warning(f"  Нет данных таблицы для league={league_id}")
        return 0

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved = 0
    for item in data["response"]:
        try:
            league_data = item.get("league", {})
            standings = league_data.get("standings", [[]])[0]
            for entry in standings:
                team = entry.get("team", {})
                all_ = entry.get("all", {})
                goals = all_.get("goals", {})
                conn.execute("""
                    INSERT OR REPLACE INTO api_standings
                    (fetched_at, league_id, season, team_id, team_name,
                     rank, points, wins, draws, losses,
                     goals_for, goals_against, form)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    now, league_id, season,
                    team.get("id"), team.get("name"),
                    entry.get("rank"), entry.get("points"),
                    all_.get("win"), all_.get("draw"), all_.get("lose"),
                    goals.get("for"), goals.get("against"),
                    entry.get("form"),
                ))
                saved += 1
        except Exception as e:
            log.debug(f"  Таблица пропущена: {e}")

    conn.commit()
    log.info(f"  Сохранено позиций: {saved}")
    return saved


def fetch_top_scorers(conn, league_id, season):
    log.info(f"  Загружаю бомбардиров league={league_id}...")
    data = api_get("/players/topscorers", {"league": league_id, "season": season})
    if not data or "response" not in data:
        return 0


    scorers = []
    for item in data["response"][:10]:
        player = item.get("player", {})
        stats  = (item.get("statistics") or [{}])[0]
        goals  = (stats.get("goals") or {})
        scorers.append({
            "player_id":   player.get("id"),
            "player_name": player.get("name"),
            "goals":       goals.get("total", 0),
            "team":        (stats.get("team") or {}).get("name", ""),
        })

    if scorers:
        import json
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        key = f"topscorers_{league_id}"
        conn.execute("""
            CREATE TABLE IF NOT EXISTS live_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                updated_at TEXT, team TEXT, stat_type TEXT,
                season TEXT, data_json TEXT, source TEXT,
                UNIQUE(team, stat_type, season)
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO live_stats
            (updated_at, team, stat_type, season, data_json, source)
            VALUES (?,?,?,?,?,?)
        """, (now, key, "topscorers", str(season),
              json.dumps(scorers), "api_football"))
        conn.commit()
        log.info(f"  Сохранено бомбардиров: {len(scorers)}")
        return len(scorers)
    return 0


def run():
    if not API_KEY:
        log.error("API_FOOTBALL_KEY не найден в файле env!")
        log.error("Получите ключ на api-football.com и добавьте в env")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute(SCHEMA)
    conn.execute(SCHEMA_STANDINGS)
    conn.commit()

    total_injuries  = 0
    total_standings = 0
    total_scorers   = 0

    for league_id, league_name in LEAGUES.items():
        log.info(f"\n{'='*45}")
        log.info(f"{league_name} (id={league_id})")
        log.info(f"{'='*45}")


        total_injuries += fetch_injuries(conn, league_id, CURRENT_SEASON)
        time.sleep(1)


        total_standings += fetch_standings(conn, league_id, CURRENT_SEASON)
        time.sleep(1)


        total_scorers += fetch_top_scorers(conn, league_id, CURRENT_SEASON)
        time.sleep(1)

    log.info(f"\n{'='*45}")
    log.info(f"ИТОГО:")
    log.info(f"  Травм сохранено:    {total_injuries}")
    log.info(f"  Позиций в таблице:  {total_standings}")
    log.info(f"  Бомбардиров:        {total_scorers}")


    data = api_get("/status")
    if data and "response" in data:
        sub = data["response"].get("subscription", {})
        req = data["response"].get("requests", {})
        log.info(f"  План: {sub.get('plan', '?')}")
        log.info(f"  Запросов использовано: {req.get('current', '?')} / {req.get('limit_day', '?')}")

    conn.close()


if __name__ == "__main__":
    run()
