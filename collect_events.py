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
    39:  "АПЛ",
    140: "Ла Лига",
    78:  "Бундеслига",
    135: "Серия А",
    61:  "Лига 1",
}

CURRENT_SEASON = 2025

SCHEMA_EVENTS = """
CREATE TABLE IF NOT EXISTS goal_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id   INTEGER,
    league_id    INTEGER,
    season       INTEGER,
    team_name    TEXT,
    player_name  TEXT,
    minute       INTEGER,
    extra_time   INTEGER DEFAULT 0,
    goal_type    TEXT,
    fetched_at   TEXT,
    UNIQUE(fixture_id, player_name, minute)
)
"""

SCHEMA_SCORER_STATS = """
CREATE TABLE IF NOT EXISTS scorer_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    updated_at      TEXT,
    league_id       INTEGER,
    season          INTEGER,
    team_name       TEXT,
    player_name     TEXT,
    total_goals     INTEGER,
    avg_minute      REAL,
    first_goal_rate REAL,
    games_scored    INTEGER,
    UNIQUE(league_id, season, player_name)
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
        r = requests.get(BASE_URL + endpoint, headers=HEADERS,
                         params=params, timeout=15)
        if r.status_code == 200:
            return r.json()
        log.warning(f"  HTTP {r.status_code}: {endpoint}")
        return None
    except Exception as e:
        log.error(f"  API error: {e}")
        return None


def get_fixture_ids(league_id, season):
    data = api_get("/fixtures", {
        "league": league_id,
        "season": season,
        "status": "FT",
    })
    if not data or "response" not in data:
        return []
    ids = [f["fixture"]["id"] for f in data["response"]]
    log.info(f"  Найдено {len(ids)} сыгранных матчей")
    return ids


def get_fixture_events(fixture_id):
    data = api_get("/fixtures/events", {
        "fixture": fixture_id,
        "type": "Goal",
    })
    if not data or "response" not in data:
        return []
    return data["response"]


def save_events(conn, fixture_id, league_id, season, events):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved = 0
    for ev in events:
        try:
            team     = (ev.get("team") or {}).get("name", "")
            player   = (ev.get("player") or {}).get("name", "")
            time_    = ev.get("time") or {}
            minute   = time_.get("elapsed") or 0
            extra    = time_.get("extra") or 0
            detail   = ev.get("detail", "")


            if "own" in detail.lower():
                continue

            if player and minute:
                conn.execute("""
                    INSERT OR IGNORE INTO goal_events
                    (fixture_id, league_id, season, team_name,
                     player_name, minute, extra_time, goal_type, fetched_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (fixture_id, league_id, season, team,
                      player, minute, extra, detail, now))
                saved += 1
        except Exception as e:
            log.debug(f"  Событие пропущено: {e}")
    return saved


def compute_scorer_stats(conn, league_id, season):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


    rows = conn.execute("""
        SELECT fixture_id, team_name, player_name, minute, extra_time
        FROM goal_events
        WHERE league_id=? AND season=?
        ORDER BY fixture_id, minute, extra_time
    """, (league_id, season)).fetchall()

    if not rows:
        return


    from collections import defaultdict
    fixtures = defaultdict(list)
    for fix_id, team, player, minute, extra in rows:
        fixtures[fix_id].append((minute + (extra or 0), player, team))


    player_goals   = defaultdict(list)
    player_firsts  = defaultdict(int)
    player_team    = {}

    for fix_id, goal_list in fixtures.items():
        sorted_goals = sorted(goal_list, key=lambda x: x[0])
        first_player = sorted_goals[0][1] if sorted_goals else None

        for minute, player, team in goal_list:
            player_goals[player].append(minute)
            player_team[player] = team
            if player == first_player:
                player_firsts[player] += 1


    saved = 0
    for player, minutes in player_goals.items():
        total = len(minutes)
        avg_min = round(sum(minutes) / total, 1)
        games = total
        firsts = player_firsts.get(player, 0)
        first_rate = round(firsts / total, 3) if total > 0 else 0.0
        team = player_team.get(player, "")

        conn.execute("""
            INSERT OR REPLACE INTO scorer_stats
            (updated_at, league_id, season, team_name, player_name,
             total_goals, avg_minute, first_goal_rate, games_scored)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (now, league_id, season, team, player,
              total, avg_min, first_rate, games))
        saved += 1

    conn.commit()
    log.info(f"  Статистика бомбардиров: {saved} игроков")


def run():
    if not API_KEY:
        log.error("API_FOOTBALL_KEY не найден в env!")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute(SCHEMA_EVENTS)
    conn.execute(SCHEMA_SCORER_STATS)
    conn.commit()


    status = api_get("/status")
    if status and "response" in status:
        req = status["response"].get("requests", {})
        remaining = int(req.get("limit_day", 7500)) - int(req.get("current", 0))
        log.info(f"Запросов API осталось сегодня: {remaining}")
        if remaining < 200:
            log.warning("Мало запросов API — пропускаю сбор событий")
            conn.close()
            return

    total_events = 0
    total_fixtures = 0

    for league_id, league_name in LEAGUES.items():
        log.info(f"\n{'='*45}")
        log.info(f"{league_name} (id={league_id})")


        already = set(
            r[0] for r in conn.execute(
                "SELECT DISTINCT fixture_id FROM goal_events WHERE league_id=? AND season=?",
                (league_id, CURRENT_SEASON)
            ).fetchall()
        )

        all_ids = get_fixture_ids(league_id, CURRENT_SEASON)
        new_ids = [fid for fid in all_ids if fid not in already]
        log.info(f"  Новых матчей для загрузки: {len(new_ids)} (уже есть: {len(already)})")

        if not new_ids:
            log.info(f"  Все матчи уже загружены, пересчитываю статистику...")
            compute_scorer_stats(conn, league_id, CURRENT_SEASON)
            continue


        batch_size = 20
        league_events = 0

        for i in range(0, len(new_ids), batch_size):
            batch = new_ids[i:i + batch_size]
            for fixture_id in batch:
                events = get_fixture_events(fixture_id)
                n = save_events(conn, fixture_id, league_id, CURRENT_SEASON, events)
                league_events += n
                total_fixtures += 1
                time.sleep(0.3)

            conn.commit()
            log.info(f"  Обработано матчей: {min(i+batch_size, len(new_ids))}/{len(new_ids)}")
            time.sleep(1)

        log.info(f"  Голов сохранено: {league_events}")
        total_events += league_events


        compute_scorer_stats(conn, league_id, CURRENT_SEASON)
        time.sleep(2)

    log.info(f"\n{'='*45}")
    log.info(f"ИТОГО:")
    log.info(f"  Матчей обработано: {total_fixtures}")
    log.info(f"  Голов сохранено:   {total_events}")


    total_in_db = conn.execute("SELECT COUNT(*) FROM goal_events").fetchone()[0]
    scorers_in_db = conn.execute("SELECT COUNT(*) FROM scorer_stats").fetchone()[0]
    log.info(f"  Голов в БД всего:  {total_in_db}")
    log.info(f"  Бомбардиров в БД:  {scorers_in_db}")

    conn.close()


if __name__ == "__main__":
    run()
