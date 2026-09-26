import sqlite3
import logging
import requests
import pandas as pd
from io import StringIO
from pathlib import Path

logging.basicConfig(format="%(asctime)s  %(levelname)s  %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

DB_PATH = Path("data/epl_target_teams.db")


LEAGUES = {
    "E0": "АПЛ",
    "SP1": "Ла Лига",
    "D1":  "Бундеслига",
    "I1":  "Серия А",
    "F1":  "Лига 1",
}

SEASONS = ["1819","1920","2021","2122","2223","2324","2425","2526"]


BASE_COLS = [
    "Div","Date","HomeTeam","AwayTeam",
    "FTHG","FTAG","FTR",
    "HTHG","HTAG","HTR",
    "HS","AS","HST","AST",
    "HF","AF","HC","AC","HY","AY","HR","AR",
]

ODDS_COLS = ["B365H","B365D","B365A","BWH","BWD","BWA","IWH","IWD","IWA"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches_raw (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    league      TEXT,
    season      TEXT,
    date        TEXT,
    home_team   TEXT,
    away_team   TEXT,
    fthg        INTEGER,
    ftag        INTEGER,
    ftr         TEXT,
    hthg        INTEGER,
    htag        INTEGER,
    htr         TEXT,
    hs          REAL, as_  REAL,
    hst         REAL, ast  REAL,
    hf          REAL, af   REAL,
    hc          REAL, ac   REAL,
    hy          REAL, ay   REAL,
    hr          REAL, ar   REAL,
    b365h       REAL, b365d REAL, b365a REAL,
    bwh         REAL, bwd  REAL, bwa   REAL,
    iwh         REAL, iwd  REAL, iwa   REAL,
    UNIQUE(league, date, home_team, away_team)
)
"""


def fetch_csv(league_code: str, season: str) -> pd.DataFrame | None:
    url = f"https://www.football-data.co.uk/mmz4281/{season}/{league_code}.csv"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        try:
            text = r.content.decode("utf-8")
        except UnicodeDecodeError:
            text = r.content.decode("latin-1")

        df = pd.read_csv(StringIO(text), on_bad_lines="skip")
        if df.empty or "HomeTeam" not in df.columns:
            return None


        df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
        df = df.dropna(subset=["Date","HomeTeam","AwayTeam","FTR"])
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        return df

    except Exception as e:
        log.debug(f"Ошибка {url}: {e}")
        return None


def safe_int(val):
    try:
        v = int(float(val))
        return v if 0 <= v <= 20 else None
    except Exception:
        return None


def safe_float(val):
    try:
        v = float(val)
        return round(v, 4) if not pd.isna(v) else None
    except Exception:
        return None


def insert_matches(conn: sqlite3.Connection, df: pd.DataFrame,
                   league_code: str, season: str) -> int:
    saved = 0
    for _, row in df.iterrows():
        try:

            get = lambda col: row.get(col) if col in row.index else None
            as_val = get("AS") if "AS" in row.index else get("AS_")

            conn.execute("""
                INSERT OR IGNORE INTO matches_raw
                (league,season,date,home_team,away_team,
                 fthg,ftag,ftr,hthg,htag,htr,
                 hs,as_,hst,ast,hf,af,hc,ac,hy,ay,hr,ar,
                 b365h,b365d,b365a,bwh,bwd,bwa,iwh,iwd,iwa)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                league_code, season, row["Date"], row["HomeTeam"], row["AwayTeam"],
                safe_int(row.get("FTHG")), safe_int(row.get("FTAG")), str(row.get("FTR","")),
                safe_int(row.get("HTHG")), safe_int(row.get("HTAG")), str(row.get("HTR","")) if row.get("HTR") else None,
                safe_float(get("HS")), safe_float(as_val),
                safe_float(get("HST")), safe_float(get("AST")),
                safe_float(get("HF")),  safe_float(get("AF")),
                safe_float(get("HC")),  safe_float(get("AC")),
                safe_float(get("HY")),  safe_float(get("AY")),
                safe_float(get("HR")),  safe_float(get("AR")),
                safe_float(get("B365H")), safe_float(get("B365D")), safe_float(get("B365A")),
                safe_float(get("BWH")),   safe_float(get("BWD")),   safe_float(get("BWA")),
                safe_float(get("IWH")),   safe_float(get("IWD")),   safe_float(get("IWA")),
            ))
            saved += conn.execute("SELECT changes()").fetchone()[0]
        except Exception as e:
            log.debug(f"Пропуск строки: {e}")
    conn.commit()
    return saved


def run():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(SCHEMA)
    conn.commit()

    total_new = 0
    total_all = 0

    for league_code, league_name in LEAGUES.items():
        log.info(f"\n{'─'*50}")
        log.info(f"Лига: {league_name} ({league_code})")
        log.info(f"{'─'*50}")

        league_total = 0
        for season in SEASONS:
            log.info(f"  Сезон {season}...")
            df = fetch_csv(league_code, season)
            if df is None:
                log.info(f"    → нет данных")
                continue
            n = insert_matches(conn, df, league_code, season)
            log.info(f"    → строк в CSV: {len(df)}, новых в БД: {n}")
            league_total += n
            total_new   += n

        total_league = conn.execute(
            "SELECT COUNT(*) FROM matches_raw WHERE league=?", (league_code,)
        ).fetchone()[0]
        log.info(f"  Итого {league_name}: {total_league} матчей в БД (+{league_total} новых)")
        total_all += total_league

    total_all_check = conn.execute("SELECT COUNT(*) FROM matches_raw").fetchone()[0]
    conn.close()

    log.info("=" * 55)
    log.info("ИТОГ СБОРКИ ДАННЫХ")
    log.info(f"  Всего матчей в БД:  {total_all_check:,}")
    log.info(f"  Добавлено сейчас:   {total_new:,}")
    log.info(f"  Лиг:                {len(LEAGUES)}")
    log.info(f"  Сезонов:            {len(SEASONS)}")
    log.info("=" * 55)


if __name__ == "__main__":
    run()
