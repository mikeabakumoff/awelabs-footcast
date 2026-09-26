import sqlite3
import logging
import requests
import json
import time
from pathlib import Path
from datetime import datetime

logging.basicConfig(format="%(asctime)s  %(levelname)s  %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

DB_PATH = Path("data/epl_target_teams.db")


def load_env():
    env = {}
    try:
        with open("env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except Exception:
        pass
    return env

ENV = load_env()
API_KEY = ENV.get("API_FOOTBALL_KEY", "")


UCL_LEAGUE_ID = 2
UEL_LEAGUE_ID = 3
UECL_LEAGUE_ID = 848

UCL_SEASONS = [2018, 2019, 2020, 2021, 2022, 2023, 2024]


UEFA_COEFFICIENTS = {

    "Real Madrid":       150.0,
    "Manchester City":   138.0,
    "Bayern Munich":     132.0,
    "Barcelona":         124.0,
    "Paris Saint Germain": 118.0,
    "Paris SG":          118.0,
    "Liverpool":         116.0,
    "Atletico Madrid":   113.0,
    "Ath Madrid":        113.0,
    "Arsenal":           108.0,
    "Chelsea":           104.0,
    "Juventus":          102.0,
    "Inter":             101.0,
    "Inter Milan":       101.0,
    "Borussia Dortmund": 98.0,
    "Dortmund":          98.0,
    "Napoli":            95.0,
    "Tottenham":         92.0,
    "Tottenham Hotspur": 92.0,
    "Bayer Leverkusen":  90.0,
    "Leverkusen":        90.0,
    "RB Leipzig":        88.0,
    "Eintracht Frankfurt": 82.0,
    "Ein Frankfurt":     82.0,
    "Benfica":           85.0,
    "Porto":             83.0,
    "Sporting CP":       78.0,
    "PSV":               75.0,
    "Ajax":              74.0,
    "Atalanta":          72.0,
    "Roma":              70.0,
    "AS Roma":           70.0,
    "Lazio":             65.0,
    "Sevilla":           68.0,
    "Villarreal":        67.0,
    "Marseille":         64.0,
    "Lyon":              62.0,
    "Monaco":            60.0,
    "AS Monaco":         60.0,
    "Milan":             82.0,
    "AC Milan":          82.0,
    "Newcastle":         55.0,
    "Newcastle United":  55.0,
    "Aston Villa":       52.0,
    "Man United":        88.0,
    "Manchester United": 88.0,
    "Athletic Bilbao":   58.0,
    "Ath Bilbao":        58.0,
    "Real Sociedad":     55.0,
    "Sociedad":          55.0,
    "Feyenoord":         62.0,
    "Celtic":            48.0,
    "Rangers":           45.0,
    "Galatasaray":       52.0,
    "Fenerbahce":        48.0,
    "Shakhtar Donetsk":  55.0,
    "Club Brugge":       52.0,
    "Red Bull Salzburg": 50.0,
}

DEFAULT_COEFF = 30.0


def get_ucl_coeff(team_name: str) -> float:

    if team_name in UEFA_COEFFICIENTS:
        return UEFA_COEFFICIENTS[team_name]

    team_lower = team_name.lower()
    for known, coeff in UEFA_COEFFICIENTS.items():
        if known.lower() in team_lower or team_lower in known.lower():
            return coeff

        if known.lower()[:5] == team_lower[:5]:
            return coeff
    return DEFAULT_COEFF


def fetch_ucl_season(league_id: int, season: int, conn: sqlite3.Connection) -> int:
    if not API_KEY:
        log.error("API_FOOTBALL_KEY не задан в env файле")
        return 0

    url = "https://v3.football.api-sports.io/fixtures"
    headers = {"x-apisports-key": API_KEY}
    params = {
        "league": league_id,
        "season": season,
        "status": "FT",
    }

    league_names = {2: "ЛЧ", 3: "ЛЕ", 848: "ЛК"}
    league_name = league_names.get(league_id, str(league_id))

    log.info(f"Загружаю {league_name} {season}/{season+1}...")

    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            log.warning(f"HTTP {r.status_code} для {league_name} {season}")
            return 0
        data = r.json()
        fixtures = data.get("response", [])
        log.info(f"  {len(fixtures)} матчей")
    except Exception as e:
        log.error(f"Ошибка запроса: {e}")
        return 0

    saved = 0
    for fix in fixtures:
        try:
            f = fix["fixture"]
            teams = fix["teams"]
            goals = fix["goals"]
            score = fix["score"]

            home = teams["home"]["name"]
            away = teams["away"]["name"]
            date_str = f["date"][:10]
            fthg = goals.get("home")
            ftag = goals.get("away")

            if fthg is None or ftag is None:
                continue


            if fthg > ftag:
                ftr = "H"
            elif fthg < ftag:
                ftr = "A"
            else:
                ftr = "D"


            ht = score.get("halftime", {})
            hthg = ht.get("home")
            htag = ht.get("away")
            htr = None
            if hthg is not None and htag is not None:
                htr = "H" if hthg > htag else ("A" if hthg < htag else "D")


            season_str = f"{season}-{str(season+1)[-2:]}"


            home_coeff = get_ucl_coeff(home)
            away_coeff = get_ucl_coeff(away)

            conn.execute("""
                INSERT OR IGNORE INTO matches_raw
                (league, season, date, home_team, away_team,
                 fthg, ftag, ftr, hthg, htag, htr,
                 home_xg, away_xg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                league_name, season_str, date_str, home, away,
                int(fthg), int(ftag), ftr,
                int(hthg) if hthg is not None else None,
                int(htag) if htag is not None else None,
                htr,
                None, None,
            ))
            saved += conn.execute("SELECT changes()").fetchone()[0]
        except Exception as e:
            log.debug(f"Пропуск матча: {e}")

    conn.commit()
    log.info(f"  Сохранено: {saved} новых матчей")
    return saved


def add_uefa_coefficients_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS uefa_coefficients (
            team_name   TEXT PRIMARY KEY,
            coefficient REAL,
            updated_at  TEXT
        )
    """)
    conn.commit()

    now = datetime.now().isoformat()
    for team, coeff in UEFA_COEFFICIENTS.items():
        conn.execute("""
            INSERT OR REPLACE INTO uefa_coefficients
            (team_name, coefficient, updated_at)
            VALUES (?, ?, ?)
        """, (team, coeff, now))
    conn.commit()
    log.info(f"UEFA коэффициенты: {len(UEFA_COEFFICIENTS)} команд сохранено")


def add_coeff_to_features(conn: sqlite3.Connection):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(matches_features)").fetchall()]
    for col in ["home_uefa_coeff", "away_uefa_coeff", "coeff_diff"]:
        if col not in cols:
            conn.execute(f"ALTER TABLE matches_features ADD COLUMN {col} REAL")
            log.info(f"Добавлена колонка: {col}")
    conn.commit()


def main():
    conn = sqlite3.connect(DB_PATH)


    log.info("═══ Шаг 1: UEFA коэффициенты ═══")
    add_uefa_coefficients_table(conn)


    log.info("═══ Шаг 2: Обновляем схему matches_features ═══")
    add_coeff_to_features(conn)


    log.info("═══ Шаг 3: Исторические данные ЛЧ ═══")
    total = 0
    for season in UCL_SEASONS:
        n = fetch_ucl_season(UCL_LEAGUE_ID, season, conn)
        total += n
        time.sleep(1.5)


    log.info("═══ Шаг 4: Исторические данные ЛЕ ═══")
    for season in UCL_SEASONS:
        n = fetch_ucl_season(UEL_LEAGUE_ID, season, conn)
        total += n
        time.sleep(1.5)

    log.info(f"Итого новых матчей ЛЧ+ЛЕ: {total}")


    log.info("═══ Шаг 5: Обновляем UEFA коэффициенты в признаках ═══")
    rows = conn.execute(
        "SELECT id, home_team, away_team FROM matches_features"
    ).fetchall()

    updated = 0
    for row_id, home, away in rows:
        hc = get_ucl_coeff(home)
        ac = get_ucl_coeff(away)
        conn.execute("""
            UPDATE matches_features
            SET home_uefa_coeff=?, away_uefa_coeff=?, coeff_diff=?
            WHERE id=?
        """, (hc, ac, round(hc - ac, 1), row_id))
        updated += 1

    conn.commit()
    log.info(f"Коэффициенты обновлены для {updated} матчей")

    conn.close()
    log.info("✅ Готово! Теперь запустите: python reset_features.py && python setup.py")


if __name__ == "__main__":
    main()
