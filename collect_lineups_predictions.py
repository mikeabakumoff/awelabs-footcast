"""
collect_lineups_predictions.py
Сбор составов, прогнозов API и статистики игроков для предстоящих матчей.

Запускать:
  - В составе scheduler.py каждый день в 09:00
  - Можно запустить вручную ближе к матчу для актуальных составов

Что собирает:
  1. /fixtures?next=10       - ближайшие матчи по 5 лигам
  2. /predictions            - прогноз API для каждого матча
  3. /fixtures/lineups       - составы (доступны за ~1ч до матча)
  4. /fixtures/players       - статистика игроков в последних матчах
"""

import json
import time
import sqlite3
import logging
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

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

# ── Схемы таблиц ──────────────────────────────────────────────────────────────

SCHEMAS = [
    """CREATE TABLE IF NOT EXISTS match_predictions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id      INTEGER UNIQUE,
        league_id       INTEGER,
        home_team       TEXT,
        away_team       TEXT,
        match_date      TEXT,
        winner_team     TEXT,
        winner_percent  REAL,
        home_percent    REAL,
        draw_percent    REAL,
        away_percent    REAL,
        under_over      TEXT,
        goals_home      REAL,
        goals_away      REAL,
        advice          TEXT,
        fetched_at      TEXT
    )""",

    """CREATE TABLE IF NOT EXISTS match_lineups (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id      INTEGER,
        team_id         INTEGER,
        team_name       TEXT,
        formation       TEXT,
        coach_name      TEXT,
        lineup_json     TEXT,
        fetched_at      TEXT,
        UNIQUE(fixture_id, team_id)
    )""",

    """CREATE TABLE IF NOT EXISTS player_season_stats (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        updated_at      TEXT,
        league_id       INTEGER,
        season          INTEGER,
        team_id         INTEGER,
        team_name       TEXT,
        player_id       INTEGER,
        player_name     TEXT,
        position        TEXT,
        games           INTEGER,
        goals           INTEGER,
        assists         INTEGER,
        rating          REAL,
        minutes         INTEGER,
        shots_total     INTEGER,
        shots_on        INTEGER,
        passes_key      INTEGER,
        dribbles_succ   INTEGER,
        UNIQUE(league_id, season, player_id)
    )""",

    """CREATE TABLE IF NOT EXISTS team_transfers (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        fetched_at      TEXT,
        team_id         INTEGER,
        team_name       TEXT,
        player_name     TEXT,
        transfer_type   TEXT,
        transfer_date   TEXT,
        from_team       TEXT,
        to_team         TEXT,
        UNIQUE(team_id, player_name, transfer_date)
    )""",
]


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
        log.warning(f"  HTTP {r.status_code}: {endpoint} {params}")
        return None
    except Exception as e:
        log.error(f"  API error {endpoint}: {e}")
        return None


# ── 1. Ближайшие матчи ────────────────────────────────────────────────────────

def fetch_upcoming_fixtures(league_id):
    """Получает ближайшие 10 матчей лиги."""
    data = api_get("/fixtures", {
        "league":  league_id,
        "season":  CURRENT_SEASON,
        "next":    10,
    })
    if not data or "response" not in data:
        return []
    fixtures = []
    for f in data["response"]:
        fix = f.get("fixture", {})
        teams = f.get("teams", {})
        fixtures.append({
            "fixture_id": fix.get("id"),
            "date":       (fix.get("date") or "")[:10],
            "home_id":    teams.get("home", {}).get("id"),
            "home_name":  teams.get("home", {}).get("name"),
            "away_id":    teams.get("away", {}).get("id"),
            "away_name":  teams.get("away", {}).get("name"),
        })
    return fixtures


# ── 2. Прогнозы API ───────────────────────────────────────────────────────────

def fetch_prediction(conn, fixture_id, league_id, home_name, away_name, match_date):
    """Получает прогноз API для матча."""
    data = api_get("/predictions", {"fixture": fixture_id})
    if not data or not data.get("response"):
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        p = data["response"][0]
        pred    = p.get("predictions", {})
        winner  = pred.get("winner", {})
        pct     = pred.get("percent", {})
        goals   = pred.get("goals", {})
        uo      = pred.get("under_over", "")
        advice  = pred.get("advice", "")

        # Парсим проценты "45%"
        def parse_pct(s):
            try:
                return float(str(s).replace("%", "").strip()) / 100
            except Exception:
                return 0.0

        home_p = parse_pct(pct.get("home", "0%"))
        draw_p = parse_pct(pct.get("draw", "0%"))
        away_p = parse_pct(pct.get("away", "0%"))

        # Победитель
        w_name = (winner.get("name") or "").strip()
        w_pct  = parse_pct(winner.get("percent") or "0%") if isinstance(
            winner.get("percent"), str) else home_p

        conn.execute("""
            INSERT OR REPLACE INTO match_predictions
            (fixture_id, league_id, home_team, away_team, match_date,
             winner_team, winner_percent, home_percent, draw_percent, away_percent,
             under_over, goals_home, goals_away, advice, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            fixture_id, league_id, home_name, away_name, match_date,
            w_name, w_pct, home_p, draw_p, away_p,
            uo,
            float(goals.get("home") or 0),
            float(goals.get("away") or 0),
            advice, now,
        ))
        conn.commit()
        log.info(f"  Прогноз {home_name} vs {away_name}: "
                 f"{w_name} {w_pct*100:.0f}% | совет: {advice[:50]}")
    except Exception as e:
        log.debug(f"  Прогноз ошибка: {e}")


# ── 3. Составы ────────────────────────────────────────────────────────────────

def fetch_lineups(conn, fixture_id, home_name, away_name):
    """Получает составы обеих команд."""
    data = api_get("/fixtures/lineups", {"fixture": fixture_id})
    if not data or not data.get("response"):
        return False

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved = 0
    for team_data in data["response"]:
        try:
            team     = team_data.get("team", {})
            coach    = team_data.get("coach", {})
            players  = team_data.get("startXI", [])
            subs     = team_data.get("substitutes", [])
            form     = team_data.get("formation", "")

            lineup = {
                "start": [
                    {
                        "name": p.get("player", {}).get("name"),
                        "pos":  p.get("player", {}).get("pos"),
                        "num":  p.get("player", {}).get("number"),
                    }
                    for p in players
                ],
                "subs": [
                    p.get("player", {}).get("name")
                    for p in subs
                ],
            }

            conn.execute("""
                INSERT OR REPLACE INTO match_lineups
                (fixture_id, team_id, team_name, formation,
                 coach_name, lineup_json, fetched_at)
                VALUES (?,?,?,?,?,?,?)
            """, (
                fixture_id,
                team.get("id"),
                team.get("name"),
                form,
                coach.get("name", ""),
                json.dumps(lineup, ensure_ascii=False),
                now,
            ))
            saved += 1
        except Exception as e:
            log.debug(f"  Состав ошибка: {e}")

    if saved:
        conn.commit()
        log.info(f"  Состав {home_name} vs {away_name}: загружен ({saved} команды)")
        return True
    return False


# ── 4. Статистика игроков ─────────────────────────────────────────────────────

def fetch_player_stats(conn, league_id, team_id, team_name):
    """Получает статистику всех игроков команды за сезон."""
    data = api_get("/players", {
        "team":   team_id,
        "season": CURRENT_SEASON,
    })
    if not data or not data.get("response"):
        return 0

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved = 0
    for item in data["response"]:
        try:
            player = item.get("player", {})
            stats  = (item.get("statistics") or [{}])[0]
            games  = stats.get("games", {})
            goals  = stats.get("goals", {})
            passes = stats.get("passes", {})
            shots  = stats.get("shots", {})
            drib   = stats.get("dribbles", {})

            conn.execute("""
                INSERT OR REPLACE INTO player_season_stats
                (updated_at, league_id, season, team_id, team_name,
                 player_id, player_name, position,
                 games, goals, assists, rating, minutes,
                 shots_total, shots_on, passes_key, dribbles_succ)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                now, league_id, CURRENT_SEASON, team_id, team_name,
                player.get("id"),
                player.get("name"),
                games.get("position", ""),
                games.get("appearences") or 0,
                goals.get("total") or 0,
                goals.get("assists") or 0,
                float(games.get("rating") or 0),
                games.get("minutes") or 0,
                shots.get("total") or 0,
                shots.get("on") or 0,
                passes.get("key") or 0,
                (drib.get("success") or 0),
            ))
            saved += 1
        except Exception as e:
            log.debug(f"  Игрок ошибка: {e}")

    conn.commit()
    return saved


# ── 5. Трансферы ──────────────────────────────────────────────────────────────

def fetch_transfers(conn, team_id, team_name):
    """Получает последние трансферы команды."""
    data = api_get("/transfers", {
        "team":   team_id,
        "season": CURRENT_SEASON,
    })
    if not data or not data.get("response"):
        return 0

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved = 0
    for item in data["response"]:
        try:
            player    = (item.get("player") or {}).get("name", "")
            transfers = item.get("transfers") or []
            for t in transfers:
                date     = (t.get("date") or "")[:10]
                ttype    = t.get("type", "")
                from_t   = (t.get("teams") or {}).get("out", {}).get("name", "")
                to_t     = (t.get("teams") or {}).get("in", {}).get("name", "")

                # Только трансферы этого сезона (после июня 2025)
                if date < "2025-06-01":
                    continue

                conn.execute("""
                    INSERT OR IGNORE INTO team_transfers
                    (fetched_at, team_id, team_name, player_name,
                     transfer_type, transfer_date, from_team, to_team)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (now, team_id, team_name, player,
                      ttype, date, from_t, to_t))
                saved += 1
        except Exception as e:
            log.debug(f"  Трансфер ошибка: {e}")

    conn.commit()
    return saved


# ── MAIN ──────────────────────────────────────────────────────────────────────

def run():
    if not API_KEY:
        log.error("API_FOOTBALL_KEY не найден в env!")
        return

    conn = sqlite3.connect(DB_PATH)
    for schema in SCHEMAS:
        conn.execute(schema)
    conn.commit()

    # Проверяем лимит запросов
    status = api_get("/status")
    if status and "response" in status:
        req = status["response"].get("requests", {})
        remaining = int(req.get("limit_day", 7500)) - int(req.get("current", 0))
        log.info(f"Запросов API осталось: {remaining}/7500")
        if remaining < 100:
            log.warning("Мало запросов — пропускаю")
            conn.close()
            return

    total_predictions = 0
    total_lineups     = 0
    total_players     = 0
    total_transfers   = 0

    # Собираем все команды для статистики игроков
    processed_teams = set()

    for league_id, league_name in LEAGUES.items():
        log.info(f"\n{'='*45}")
        log.info(f"{league_name} (id={league_id})")

        # 1. Ближайшие матчи
        fixtures = fetch_upcoming_fixtures(league_id)
        log.info(f"  Ближайших матчей: {len(fixtures)}")
        time.sleep(0.5)

        for fix in fixtures:
            fid       = fix["fixture_id"]
            home_name = fix["home_name"]
            away_name = fix["away_name"]
            home_id   = fix["home_id"]
            away_id   = fix["away_id"]
            date      = fix["date"]

            # 2. Прогноз API
            fetch_prediction(conn, fid, league_id, home_name, away_name, date)
            total_predictions += 1
            time.sleep(0.4)

            # 3. Составы (доступны за ~1ч до матча)
            ok = fetch_lineups(conn, fid, home_name, away_name)
            if ok:
                total_lineups += 1
            time.sleep(0.4)

            # 4. Статистика игроков команды (раз на команду)
            for team_id, team_name in [(home_id, home_name), (away_id, away_name)]:
                if team_id and team_id not in processed_teams:
                    n = fetch_player_stats(conn, league_id, team_id, team_name)
                    if n:
                        log.info(f"  Игроки {team_name}: {n} записей")
                        total_players += n
                    processed_teams.add(team_id)
                    time.sleep(0.4)

                    # 5. Трансферы команды
                    n_t = fetch_transfers(conn, team_id, team_name)
                    if n_t:
                        log.info(f"  Трансферы {team_name}: {n_t}")
                        total_transfers += n_t
                    time.sleep(0.4)

    log.info(f"\n{'='*45}")
    log.info(f"ИТОГО:")
    log.info(f"  Прогнозов API:       {total_predictions}")
    log.info(f"  Составов загружено:  {total_lineups}")
    log.info(f"  Игроков (статистика):{total_players}")
    log.info(f"  Трансферов:          {total_transfers}")

    conn.close()


if __name__ == "__main__":
    run()
