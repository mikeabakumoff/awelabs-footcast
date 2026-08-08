"""
collect_xg.py - Сбор xG данных с Understat.com
Дополняет matches_raw полями home_xg, away_xg

Установка:
    pip install beautifulsoup4 lxml understatapi
"""

import json
import re
import time
import sqlite3
import logging
import requests
from pathlib import Path

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False

logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

DB_PATH = Path("data/epl_target_teams.db")

LEAGUE_MAP = {
    "E0":  "EPL",
    "SP1": "La_liga",
    "D1":  "Bundesliga",
    "I1":  "Serie_A",
    "F1":  "Ligue_1",
}

SEASONS_TO_LOAD = ["2025", "2024", "2023", "2022", "2021", "2020", "2019", "2018"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def extract_json_from_script(script_text):
    patterns = [
        r"var\s+datesData\s*=\s*JSON\.parse\('(.+?)'\)",
        r'var\s+datesData\s*=\s*JSON\.parse\("(.+?)"\)',
        r"datesData\s*=\s*JSON\.parse\('(.+?)'\)",
    ]
    for pat in patterns:
        m = re.search(pat, script_text, re.DOTALL)
        if m:
            raw = m.group(1)
            raw = raw.replace("\\/", "/")
            try:
                raw2 = raw.encode().decode("unicode_escape", errors="replace")
                return json.loads(raw2)
            except Exception:
                try:
                    return json.loads(raw)
                except Exception:
                    continue
    return None


def fetch_via_bs4(league_slug, season):
    url = f"https://understat.com/league/{league_slug}/{season}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        if r.status_code != 200:
            log.warning(f"  HTTP {r.status_code}")
            return []
        soup = BeautifulSoup(r.content, "lxml")
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string and "datesData" in script.string:
                data = extract_json_from_script(script.string)
                if data:
                    return data
        if len(scripts) > 2 and scripts[2].string:
            data = extract_json_from_script(scripts[2].string)
            if data:
                return data
        log.warning(f"  datesData не найден для {league_slug}/{season}")
        return []
    except Exception as e:
        log.error(f"  BS4 ошибка: {e}")
        return []


def fetch_via_understatapi(league_slug, season):
    try:
        from understatapi import UnderstatClient
        # Точные названия из документации understatapi:
        # one of {EPL, La_Liga, Bundesliga, Serie_A, Ligue_1, RFPL}
        api_names = {
            "EPL":        "EPL",
            "La_liga":    "La_Liga",
            "Bundesliga": "Bundesliga",
            "Serie_A":    "Serie_A",
            "Ligue_1":    "Ligue_1",
        }
        api_league = api_names.get(league_slug, league_slug)
        with UnderstatClient() as understat:
            matches = understat.league(league=api_league).get_match_data(season=season)
            return matches if matches else []
    except ImportError:
        return None
    except Exception as e:
        log.error(f"  understatapi ошибка: {e}")
        return []


def ensure_xg_columns(conn):
    for col in ["home_xg", "away_xg"]:
        try:
            conn.execute(f"ALTER TABLE matches_raw ADD COLUMN {col} REAL")
            conn.commit()
            log.info(f"Добавлена колонка {col}")
        except sqlite3.OperationalError:
            pass


def update_xg(conn, home, away, date_str, home_xg, away_xg):
    n = conn.execute("""
        UPDATE matches_raw
        SET home_xg = ?, away_xg = ?
        WHERE LOWER(home_team) LIKE ?
          AND LOWER(away_team) LIKE ?
          AND date BETWEEN date(?, '-1 day') AND date(?, '+1 day')
          AND home_xg IS NULL
    """, (
        round(home_xg, 4), round(away_xg, 4),
        "%" + home[:6].lower() + "%",
        "%" + away[:6].lower() + "%",
        date_str, date_str,
    )).rowcount
    return n


def parse_match(m):
    try:
        if not m.get("isResult"):
            return None
        home = (m.get("h") or {}).get("title", "")
        away = (m.get("a") or {}).get("title", "")
        dt   = (m.get("datetime") or "")[:10]
        xg   = m.get("xG") or {}
        h_xg = float(xg.get("h") or 0)
        a_xg = float(xg.get("a") or 0)
        if home and away and dt:
            return home, away, dt, h_xg, a_xg
    except Exception:
        pass
    return None


def run():
    conn = sqlite3.connect(DB_PATH)
    ensure_xg_columns(conn)

    has_bs4 = BS4_OK
    has_api = False
    try:
        import understatapi
        has_api = True
    except ImportError:
        pass

    if not has_bs4 and not has_api:
        log.error(
            "Нет зависимостей!\n"
            "Установите: pip install beautifulsoup4 lxml\n"
            "или: pip install understatapi"
        )
        conn.close()
        return

    total = 0

    for league_code, league_slug in LEAGUE_MAP.items():
        for season in SEASONS_TO_LOAD:
            log.info(f"Загружаю {league_slug} / {season}...")
            matches = []

            if has_bs4:
                matches = fetch_via_bs4(league_slug, season)

            if not matches and has_api:
                log.info("  Пробуем understatapi...")
                matches = fetch_via_understatapi(league_slug, season) or []

            log.info(f"  Получено {len(matches)} матчей")

            updated = 0
            for m in matches:
                result = parse_match(m)
                if result:
                    home, away, dt, h_xg, a_xg = result
                    updated += update_xg(conn, home, away, dt, h_xg, a_xg)

            conn.commit()
            log.info(f"  Обновлено: {updated} записей xG")
            total += updated
            time.sleep(3)

    count = conn.execute(
        "SELECT COUNT(*) FROM matches_raw WHERE home_xg IS NOT NULL"
    ).fetchone()[0]

    log.info("=" * 50)
    log.info(f"Готово! Записей с xG в БД: {count}")
    log.info(f"Обновлено сейчас: {total}")

    if total == 0:
        log.warning(
            "\nxG не загружены. Попробуйте:\n"
            "  pip install beautifulsoup4 lxml\n"
            "  pip install understatapi\n"
            "Затем запустите снова."
        )

    conn.close()


if __name__ == "__main__":
    run()
