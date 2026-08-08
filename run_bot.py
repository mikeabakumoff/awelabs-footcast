"""
run_bot.py — Sports Analytics Bot
Версия 2.0: все крупные лиги, дообучение, эмодзи в логах
"""

import re
import json
import sqlite3
import pickle
import logging
import requests
import numpy as np
import pandas as pd
from math import exp, factorial
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── Эмодзи-логгер ─────────────────────────────────────────────────────────────
class EmojiFormatter(logging.Formatter):
    ICONS = {
        logging.DEBUG:   "🔹",
        logging.INFO:    "✅",
        logging.WARNING: "❗",
        logging.ERROR:   "❌",
        logging.CRITICAL:"🔥",
    }
    def format(self, record):
        icon = self.ICONS.get(record.levelno, "  ")
        record.msg = f"{icon} {record.msg}"
        return super().format(record)

handler = logging.StreamHandler()
handler.setFormatter(EmojiFormatter("%(asctime)s  %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[handler])
log = logging.getLogger(__name__)

# ── Конфигурация — читаем из файла env ────────────────────────────────────────
def _load_env(path="env") -> dict:
    """Читает файл env формата KEY=VALUE (по одному на строку)."""
    env = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env

_env = _load_env()

TELEGRAM_TOKEN    = _env.get("TELEGRAM_TOKEN",    "")
CHAT_ID           = _env.get("CHAT_ID",           "")  # канал
OWNER_CHAT_ID     = _env.get("OWNER_CHAT_ID", "")  # личный ID для служебных уведомлений
ODDS_API_KEY      = _env.get("ODDS_API_KEY",      "")
WEB_APP_URL     = _env.get("WEB_APP_URL", "")
ANTHROPIC_API_KEY = _env.get("ANTHROPIC_API_KEY", "")

DB_PATH    = Path("data/epl_target_teams.db")
MODELS_DIR = Path("models")

MIN_SEND_PROB = 0.60
CONFIDENCE_THR = {"high": 0.65, "medium": 0.60}

# ── Все крупные лиги для the-odds-api ─────────────────────────────────────────
LEAGUES_API = {
    # Еврокубки
    "soccer_uefa_champs_league":   "🏆 Лига Чемпионов",
    "soccer_uefa_europa_league":   "🥈 Лига Европы",
    "soccer_uefa_europa_conference_league": "🥉 Лига Конференций",
    # Топ лиги
    "soccer_epl":              "🏴󠁧󠁢󠁥󠁮󠁧󠁿 АПЛ",
    "soccer_spain_la_liga":    "🇪🇸 Ла Лига",
    "soccer_italy_serie_a":    "🇮🇹 Серия А",
    "soccer_france_ligue_one": "🇫🇷 Лига 1",
}

# Топ команды — показываем прогнозы только для них в клубных лигах
# В ЛЧ/ЛЕ — все матчи интересны
TOP_CLUBS = {
    # АПЛ
    "Man City", "Liverpool", "Arsenal", "Chelsea", "Man United",
    "Tottenham", "Newcastle", "Aston Villa",
    # Ла Лига
    "Real Madrid", "Barcelona",
    # Серия А
    "Inter", "Juventus", "Milan", "Roma",
    # Лига 1
    "Paris SG", "Marseille", "Monaco",
    # Бундеслига
    "Bayern Munich", "Dortmund", "Leverkusen", "RB Leipzig", "Ein Frankfurt",
    "Eintracht Frankfurt",
    # Серия А
    "Inter", "Napoli", "Juventus", "Milan", "Roma", "Lazio", "Atalanta",
    # Лига 1
    "Paris SG", "Marseille", "Monaco", "Lyon", "Lille",
}


# Лиги где показываем ВСЕ матчи (еврокубки)
SHOW_ALL_LEAGUES = {
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
}

# Нормализация имён команд из API → имена в matches_features
TEAM_NORM = {
    # АПЛ
    "Manchester City":              "Man City",
    "Manchester United":            "Man United",
    "Brentford FC":                 "Brentford",
    "Newcastle United":             "Newcastle",
    "Newcastle Utd":                "Newcastle",
    "Tottenham Hotspur":            "Tottenham",
    "Spurs":                        "Tottenham",
    "Brighton & Hove Albion":       "Brighton",
    "Brighton and Hove Albion":     "Brighton",
    "West Ham United":              "West Ham",
    "Wolverhampton Wanderers":      "Wolves",
    "Wolverhampton":                "Wolves",
    "Nottingham Forest":            "Nott'm Forest",
    "Nottm Forest":                 "Nott'm Forest",
    "AFC Bournemouth":              "Bournemouth",
    "Leeds United":                 "Leeds",
    "Leeds Utd":                    "Leeds",
    "Sunderland AFC":               "Sunderland",
    "Burnley FC":                   "Burnley",
    "Ipswich Town":                 "Ipswich",
    "Leicester City":               "Leicester",
    "Sheffield Utd":                "Sheffield United",
    "West Bromwich Albion":         "West Brom",
    "Huddersfield Town":            "Huddersfield",
    "Cardiff City":                 "Cardiff",
    "Norwich City":                 "Norwich",
    "Watford FC":                   "Watford",
    "Luton Town":                   "Luton",
    "Southampton FC":               "Southampton",
    # Ла Лига
    "Atletico Madrid":              "Ath Madrid",
    "Atlético Madrid":              "Ath Madrid",
    "Athletic Bilbao":              "Ath Bilbao",
    "Athletic Club":                "Ath Bilbao",
    "Alavés":                       "Alaves",
    "Deportivo Alaves":             "Alaves",
    "CA Osasuna":                   "Osasuna",
    "Real Sociedad":                "Sociedad",
    "Rayo Vallecano":               "Vallecano",
    "Espanyol":                     "Espanol",
    "RCD Espanyol":                 "Espanol",
    "Celta Vigo":                   "Celta",
    "RC Celta":                     "Celta",
    "Elche CF":                     "Elche",
    "Real Betis":                   "Betis",
    "Real Valladolid":              "Valladolid",
    "UD Las Palmas":                "Las Palmas",
    "Cadiz CF":                     "Cadiz",
    "Granada CF":                   "Granada",
    "Leganes":                      "Leganes",
    # Бундеслига
    "Borussia Dortmund":            "Dortmund",
    "Borussia Monchengladbach":     "M'gladbach",
    "Bayer Leverkusen":             "Leverkusen",
    "Eintracht Frankfurt":          "Ein Frankfurt",
    "VfB Stuttgart":                "Stuttgart",
    "SC Freiburg":                  "Freiburg",
    "Werder Bremen":                "Werder Bremen",
    "VfL Wolfsburg":                "Wolfsburg",
    "FC Augsburg":                  "Augsburg",
    "1. FC Union Berlin":           "Union Berlin",
    "TSG Hoffenheim":               "Hoffenheim",
    "FSV Mainz 05":                 "Mainz",
    "FC St. Pauli":                 "St Pauli",
    "1. FC Köln":                   "FC Koln",
    "1. FC Heidenheim":             "Heidenheim",
    "Hamburger SV":                 "Hamburg",
    "RB Leipzig":                   "RB Leipzig",
    "Hertha BSC":                   "Hertha",
    "Hannover 96":                  "Hannover",
    "Fortuna Düsseldorf":           "Fortuna Dusseldorf",
    "SpVgg Greuther Fürth":         "Greuther Furth",
    "Arminia Bielefeld":            "Bielefeld",
    "Schalke 04":                   "Schalke 04",
    "FC Nürnberg":                  "Nurnberg",
    "SC Paderborn":                 "Paderborn",
    "Holstein Kiel":                "Holstein Kiel",
    "Darmstadt 98":                 "Darmstadt",
    "VfL Bochum":                   "Bochum",
    # Серия А
    "AC Milan":                     "Milan",
    "Inter Milan":                  "Inter",
    "AS Roma":                      "Roma",
    "Atalanta BC":                  "Atalanta",
    "Hellas Verona":                "Verona",
    "Udinese Calcio":               "Udinese",
    "Bologna FC":                   "Bologna",
    "Torino FC":                    "Torino",
    "Fiorentina":                   "Fiorentina",
    "SS Lazio":                     "Lazio",
    "Napoli":                       "Napoli",
    "Juventus":                     "Juventus",
    "Genoa CFC":                    "Genoa",
    "Cagliari Calcio":              "Cagliari",
    "Lecce":                        "Lecce",
    "Empoli FC":                    "Empoli",
    "Parma Calcio":                 "Parma",
    "Venezia FC":                   "Venezia",
    "Spezia Calcio":                "Spezia",
    "Salernitana":                  "Salernitana",
    "US Cremonese":                 "Cremonese",
    "Frosinone Calcio":             "Frosinone",
    "Monza":                        "Monza",
    "Benevento Calcio":             "Benevento",
    "Brescia Calcio":               "Brescia",
    "Chievo":                       "Chievo",
    "SPAL":                         "Spal",
    "Crotone":                      "Crotone",
    # Лига 1
    "Paris Saint-Germain":          "Paris SG",
    "Paris Saint Germain":          "Paris SG",
    "PSG":                          "Paris SG",
    "AS Monaco":                    "Monaco",
    "RC Lens":                      "Lens",
    "Olympique Marseille":          "Marseille",
    "Olympique Lyonnais":           "Lyon",
    "LOSC Lille":                   "Lille",
    "OGC Nice":                     "Nice",
    "Stade Rennais":                "Rennes",
    "RC Strasbourg":                "Strasbourg",
    "FC Nantes":                    "Nantes",
    "Montpellier HSC":              "Montpellier",
    "Stade de Reims":               "Reims",
    "Stade Brestois":               "Brest",
    "Toulouse FC":                  "Toulouse",
    "Le Havre AC":                  "Le Havre",
    "Angers SCO":                   "Angers",
    "FC Metz":                      "Metz",
    "Lorient":                      "Lorient",
    "Clermont Foot":                "Clermont",
    "Dijon FCO":                    "Dijon",
    "Nimes Olympique":              "Nimes",
    "Girondins Bordeaux":           "Bordeaux",
    "SM Caen":                      "Caen",
    "Guingamp":                     "Guingamp",
    "AC Ajaccio":                   "Ajaccio",
    "Troyes AC":                    "Troyes",
    "St Etienne":                   "St Etienne",
    "AS Saint-Etienne":             "St Etienne",
    "Amiens SC":                    "Amiens",
}

# Схема БД
ODDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT, match_date TEXT, league TEXT,
    home_team TEXT, away_team TEXT, bookmaker TEXT,
    odds_home REAL, odds_draw REAL, odds_away REAL,
    prob_home REAL, prob_draw REAL, prob_away REAL,
    UNIQUE(match_date, home_team, away_team, bookmaker)
)
"""

UPCOMING_SCHEMA = """
CREATE TABLE IF NOT EXISTS upcoming_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT, match_date TEXT, match_time TEXT,
    league TEXT, league_name TEXT,
    home_team TEXT, away_team TEXT,
    UNIQUE(match_date, home_team, away_team)
)
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  1. МАТЧИ НА БЛИЖАЙШИЕ 2 НЕДЕЛИ + КОТИРОВКИ
# ═══════════════════════════════════════════════════════════════════════════════

def norm(name: str) -> str:
    return TEAM_NORM.get(name, name)


def implied_no_margin(h, d, a):
    ph, pd_, pa = 1/h, 1/d, 1/a
    t = ph+pd_+pa
    return ph/t, pd_/t, pa/t


def fetch_all_leagues(conn: sqlite3.Connection) -> list:
    """
    Скачивает матчи и котировки по всем 5 лигам за ближайшие 14 дней.
    Возвращает список матчей для анализа.
    """
    conn.execute(ODDS_SCHEMA)
    conn.execute(UPCOMING_SCHEMA)
    conn.commit()

    now     = datetime.now(timezone.utc)
    cutoff       = now + timedelta(days=14)  # анонсы на 14 дней
    predict_cutoff = now + timedelta(days=4)  # прогнозы только на 4 дня
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    matches = []

    log.info(f"Загружаю матчи на ближайшие 14 дней по {len(LEAGUES_API)} лигам...")

    for sport_key, league_name in LEAGUES_API.items():
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
        try:
            r = requests.get(url, params={
                "apiKey": ODDS_API_KEY, "regions": "eu",
                "markets": "h2h", "oddsFormat": "decimal",
            }, timeout=15)
            rem = r.headers.get("x-requests-remaining", "?")
            if r.status_code != 200:
                log.warning(f"{league_name}: HTTP {r.status_code}")
                continue
            games = r.json()
            log.info(f"{league_name}: {len(games)} матчей (осталось кредитов: {rem})")
        except Exception as e:
            log.error(f"{league_name}: {e}")
            continue

        for game in games:
            home = norm(game.get("home_team",""))
            away = norm(game.get("away_team",""))
            start_str = game.get("commence_time","")

            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z","+00:00"))
                if start_dt > cutoff or start_dt < now:
                    continue
                # Время по Бангкоку (UTC+7)
                bkk_dt  = start_dt + timedelta(hours=7)
                date_str = bkk_dt.strftime("%Y-%m-%d")
                time_str = bkk_dt.strftime("%H:%M")
            except Exception:
                continue

            # Фильтр: в клубных лигах — только топ команды
            if sport_key not in SHOW_ALL_LEAGUES:
                home_norm = TEAM_NORM.get(home, home)
                away_norm = TEAM_NORM.get(away, away)
                home_is_top = any(t.lower() in home_norm.lower() or home_norm.lower() in t.lower() for t in TOP_CLUBS)
                away_is_top = any(t.lower() in away_norm.lower() or away_norm.lower() in t.lower() for t in TOP_CLUBS)
                if not home_is_top and not away_is_top:
                    continue

            # Помечаем матч как "прогнозируемый" если в ближайшие 3 дня
            is_predictable = start_dt <= predict_cutoff

            # Сохраняем матч
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO upcoming_matches
                    (fetched_at,match_date,match_time,league,league_name,home_team,away_team)
                    VALUES (?,?,?,?,?,?,?)
                """, (now_str, date_str, time_str, sport_key, league_name, home, away))
            except Exception:
                pass

            # Сохраняем котировки
            for bm in game.get("bookmakers",[]):
                for mkt in bm.get("markets",[]):
                    if mkt.get("key") != "h2h":
                        continue
                    oc = {o["name"]: o["price"] for o in mkt.get("outcomes",[])}
                    oh = oc.get(game.get("home_team",""))
                    od = oc.get("Draw")
                    oa = oc.get(game.get("away_team",""))
                    if not all([oh, od, oa]):
                        continue
                    try:
                        ph, pd_, pa = implied_no_margin(oh, od, oa)
                        conn.execute("""
                            INSERT OR REPLACE INTO live_odds
                            (fetched_at,match_date,league,home_team,away_team,
                             bookmaker,odds_home,odds_draw,odds_away,
                             prob_home,prob_draw,prob_away)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (now_str,date_str,sport_key,home,away,
                              bm.get("key"),oh,od,oa,ph,pd_,pa))
                    except Exception:
                        pass

            matches.append({
                "home": home, "away": away,
                "date": date_str, "time_bkk": time_str,
                "league": sport_key, "league_name": league_name,
                "is_predictable": is_predictable,
            })

        import time; time.sleep(0.3)

    conn.commit()
    log.info(f"Итого матчей для анализа: {len(matches)}")
    return matches


def get_avg_odds(conn, home, away):
    try:
        df = pd.read_sql("""
            SELECT AVG(prob_home) ph, AVG(prob_draw) pd_, AVG(prob_away) pa,
                   AVG(odds_home) oh, AVG(odds_draw) od, AVG(odds_away) oa,
                   COUNT(DISTINCT bookmaker) bms
            FROM live_odds WHERE home_team=? AND away_team=?
        """, conn, params=(home, away))
        if df.empty or pd.isna(df["ph"].iloc[0]):
            return {}
        r = df.iloc[0]
        return {"odds_h": r["ph"], "odds_d": r["pd_"], "odds_a": r["pa"],
                "raw_oh": r["oh"], "raw_od": r["od"], "raw_oa": r["oa"],
                "bookmakers": int(r["bms"])}
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
#  2. ML-ПРОГНОЗ
# ═══════════════════════════════════════════════════════════════════════════════

def load_models():
    models = {}
    for name in ["n1_form","n2_h2h","n3_odds","n4_full","n5_agg",
                 "n6h_goals","n6a_goals","meta"]:
        p = MODELS_DIR / f"{name}.pkl"
        if not p.exists():
            raise FileNotFoundError(f"Нет {p} — запустите train_models.py")
        with open(p,"rb") as f:
            models[name] = pickle.load(f)
    return models


def predict_one(models, conn, home, away, match_date, league_key="soccer_epl"):
    from live_collector import collect_match_features
    meta    = models["meta"]
    classes = meta["classes"]

    odds = get_avg_odds(conn, home, away)
    if odds:
        log.info(f"Котировки ({odds['bookmakers']} букм.): "
                 f"Х={odds['raw_oh']:.2f} Н={odds['raw_od']:.2f} Г={odds['raw_oa']:.2f}")

    # Выбираем источник данных:
    # если ANTHROPIC_API_KEY задан — используем агентов (Claude web search)
    # иначе — обычный live_collector (ESPN + DB)
    if ANTHROPIC_API_KEY:
        from agent_collector import collect_features_with_agents, set_api_key, apply_news_adjustment
        set_api_key(ANTHROPIC_API_KEY)
        live = collect_features_with_agents(home, away, match_date, odds or {}, league_key)
    else:
        from live_collector import collect_match_features
        live = collect_match_features(home, away, match_date, odds or {}, league_key)

    # Извлекаем новости до удаления мета-ключей
    news_home = live.pop("_news_home", {})
    news_away = live.pop("_news_away", {})
    src = live.pop("_sources", {})
    log.info(f"Источники: форма={src.get('home_form')}/{src.get('away_form')}, "
             f"H2H={src.get('h2h','DB')}, ctx={src.get('context_h','?')}")

    # ── Добавляем признаки мотивации и UEFA из БД ────────────────────────────
    try:
        _fc = sqlite3.connect(DB_PATH)
        home_db = TEAM_NORM.get(home, home)
        away_db = TEAM_NORM.get(away, away)

        # Берём последние известные признаки мотивации из matches_features
        def get_motivation(team):
            row = _fc.execute("""
                SELECT home_position, home_relegation_gap, home_title_gap,
                       home_rest_days, home_home_win_rate, home_pts_total,
                       home_uefa_coeff
                FROM matches_features
                WHERE home_team=? ORDER BY date DESC LIMIT 1
            """, (team,)).fetchone()
            if row:
                return {
                    "position": row[0], "relegation_gap": row[1],
                    "title_gap": row[2], "rest_days": row[3],
                    "home_win_rate": row[4], "pts_total": row[5],
                    "uefa_coeff": row[6],
                }
            row2 = _fc.execute("""
                SELECT away_position, away_relegation_gap, away_title_gap,
                       away_rest_days, away_away_win_rate, away_pts_total,
                       away_uefa_coeff
                FROM matches_features
                WHERE away_team=? ORDER BY date DESC LIMIT 1
            """, (team,)).fetchone()
            if row2:
                return {
                    "position": row2[0], "relegation_gap": row2[1],
                    "title_gap": row2[2], "rest_days": row2[3],
                    "home_win_rate": row2[4], "pts_total": row2[5],
                    "uefa_coeff": row2[6],
                }
            return {}

        hm = get_motivation(home_db)
        am = get_motivation(away_db)
        _fc.close()

        # Заполняем недостающие признаки
        from build_features import get_uefa_coeff as _get_coeff
        hcoeff = hm.get("uefa_coeff") or _get_coeff(home)
        acoeff = am.get("uefa_coeff") or _get_coeff(away)

        live.setdefault("home_position",       hm.get("position", 10))
        live.setdefault("away_position",       am.get("position", 10))
        live.setdefault("home_relegation_gap", hm.get("relegation_gap", 5))
        live.setdefault("away_relegation_gap", am.get("relegation_gap", 5))
        live.setdefault("home_title_gap",      hm.get("title_gap", 5))
        live.setdefault("away_title_gap",      am.get("title_gap", 5))
        live.setdefault("home_rest_days",      hm.get("rest_days", 7))
        live.setdefault("away_rest_days",      am.get("rest_days", 7))
        live.setdefault("home_home_win_rate",  hm.get("home_win_rate", 0.4))
        live.setdefault("away_away_win_rate",  am.get("home_win_rate", 0.25))
        live.setdefault("home_pts_total",      hm.get("pts_total", 30))
        live.setdefault("away_pts_total",      am.get("pts_total", 30))
        live.setdefault("home_uefa_coeff",     hcoeff)
        live.setdefault("away_uefa_coeff",     acoeff)
        live.setdefault("coeff_diff",          round(hcoeff - acoeff, 1))
        live.setdefault("home_xg_avg",         live.get("home_xg_avg", 1.3))
        live.setdefault("away_xg_avg",         live.get("away_xg_avg", 1.1))
        live.setdefault("home_xg_diff",        live.get("home_xg_avg", 1.3) - live.get("away_xg_avg", 1.1))

    except Exception as _me:
        log.debug(f"Мотивация/UEFA: {_me}")

    # Заполняем ВСЕ возможные признаки значениями по умолчанию
    FEAT_DEFAULTS = {
        "home_pts_avg": 1.5, "away_pts_avg": 1.2,
        "home_win_rate": 0.4, "away_win_rate": 0.3,
        "home_draw_rate": 0.25, "away_draw_rate": 0.25,
        "home_loss_rate": 0.35, "away_loss_rate": 0.45,
        "home_gf_avg": 1.5, "away_gf_avg": 1.2,
        "home_ga_avg": 1.2, "away_ga_avg": 1.4,
        "home_gd_avg": 0.3, "away_gd_avg": -0.2,
        "home_sot_avg": 5.0, "away_sot_avg": 4.0,
        "home_weighted_form": 1.5, "away_weighted_form": 1.2,
        "home_games_played": 10, "away_games_played": 10,
        "pts_diff": 0.3, "gd_diff": 0.2, "form_diff": 0.3,
        "h2h_n": 5, "h2h_home_wr": 0.4, "h2h_away_wr": 0.3,
        "h2h_draw_r": 0.3, "h2h_avg_goals": 2.5,
        "odds_h": 0.45, "odds_d": 0.27, "odds_a": 0.28,
        "home_advantage": 1,
        "home_xg_avg": 1.3, "away_xg_avg": 1.1, "home_xg_diff": 0.2,
        "home_position": 10, "away_position": 12,
        "home_pts_total": 40, "away_pts_total": 32,
        "home_relegation_gap": 7, "away_relegation_gap": 5,
        "home_title_gap": 8, "away_title_gap": 10,
        "home_rest_days": 7, "away_rest_days": 7,
        "home_home_win_rate": 0.45, "away_away_win_rate": 0.28,
        "home_uefa_coeff": 60.0, "away_uefa_coeff": 45.0, "coeff_diff": 15.0,
    }
    for k, v in FEAT_DEFAULTS.items():
        live.setdefault(k, v)

    df_r = pd.DataFrame([live])

    def safe_X(feat_list):
        # Добавляем недостающие колонки со значением NaN (SimpleImputer заполнит)
        import numpy as np
        missing = [c for c in feat_list if c not in df_r.columns]
        for c in missing:
            df_r[c] = FEAT_DEFAULTS.get(c, np.nan)
        return df_r[feat_list]

    def proba(pipe, X):
        return dict(zip(classes, pipe.predict_proba(X)[0]))

    p1 = proba(models["n1_form"], safe_X(meta["feat_n1"]))
    p2 = proba(models["n2_h2h"],  safe_X(meta["feat_n2"]))
    p3 = proba(models["n3_odds"], safe_X(meta["feat_n3"]))
    p4 = proba(models["n4_full"], safe_X(meta["feat_n4"]))

    stack = pd.DataFrame([[p[c] for p in [p1,p2,p3,p4] for c in classes]],
                         columns=meta["stack_cols"])
    p5 = proba(models["n5_agg"], stack)

    W = {"n1":0.20,"n2":0.08,"n3":0.22,"n4":0.15,"n5":0.35}
    final = {c: sum(W[k]*p.get(c,0) for k,p in
                    [("n1",p1),("n2",p2),("n3",p3),("n4",p4),("n5",p5)])
             for c in classes}

    # ── N7: Прогноз API-Football ──────────────────────────────────────────────
    try:
        _c = sqlite3.connect(DB_PATH)
        api_pred = _c.execute("""
            SELECT home_percent, draw_percent, away_percent, winner_team,
                   goals_home, goals_away, advice
            FROM match_predictions
            WHERE (LOWER(home_team) LIKE ? AND LOWER(away_team) LIKE ?)
              AND match_date >= date('now', '-1 day')
            ORDER BY fetched_at DESC LIMIT 1
        """, ("%" + home[:5].lower() + "%", "%" + away[:5].lower() + "%")).fetchone()
        _c.close()

        if api_pred and api_pred[0]:
            h_p, d_p, a_p, w_team, g_h, g_a, advice = api_pred
            # Нормализуем
            total_p = (h_p or 0) + (d_p or 0) + (a_p or 0)
            if total_p > 0.1:
                api_signal = {
                    "H": (h_p or 0) / total_p,
                    "D": (d_p or 0) / total_p,
                    "A": (a_p or 0) / total_p,
                }
                # Добавляем API прогноз с весом 12%
                W7 = 0.12
                scale = 1.0 - W7
                for c in classes:
                    final[c] = final[c] * scale + api_signal[c] * W7
                log.info(f"  N7 API-pred {home} vs {away}: "
                         f"H={h_p:.0%} D={d_p:.0%} A={a_p:.0%} | {advice[:40] if advice else ''}")
    except Exception as _e:
        log.debug(f"  N7 API-pred error: {_e}")

    # ── Корректировка на основе состава ──────────────────────────────────────
    try:
        _c = sqlite3.connect(DB_PATH)
        # Проверяем наличие состава (признак что все игроки в наличии)
        for team_name, result_key in [(home, "H"), (away, "A")]:
            lineup_row = _c.execute("""
                SELECT lineup_json, formation FROM match_lineups ml
                JOIN (SELECT fixture_id FROM match_predictions
                      WHERE LOWER(home_team) LIKE ? OR LOWER(away_team) LIKE ?
                      ORDER BY fetched_at DESC LIMIT 1) mp
                ON ml.fixture_id = mp.fixture_id
                WHERE LOWER(ml.team_name) LIKE ?
                ORDER BY ml.fetched_at DESC LIMIT 1
            """, (
                "%" + team_name[:5].lower() + "%",
                "%" + team_name[:5].lower() + "%",
                "%" + team_name[:5].lower() + "%",
            )).fetchone()

            if lineup_row:
                lineup_data = json.loads(lineup_row[0])
                formation   = lineup_row[1]
                starters    = lineup_data.get("start", [])
                n_starters  = len(starters)

                # Если меньше 11 стартовых — команда ослаблена (резервный состав)
                if n_starters < 11:
                    adj = -0.04 * (11 - n_starters)
                    final[result_key] = max(0.05, final[result_key] + adj)
                    log.info(f"  Состав {team_name}: только {n_starters} игроков → {adj:+.2f}")
                else:
                    log.info(f"  Состав {team_name}: {n_starters} игроков, схема {formation}")
        _c.close()
    except Exception as _e:
        log.debug(f"  Lineup adj error: {_e}")

    if odds:
        market = {"H":odds["odds_h"],"D":odds["odds_d"],"A":odds["odds_a"]}
        for c in classes:
            final[c] = 0.65*final[c] + 0.35*market.get(c,final[c])

    wc = max(final, key=final.get)
    wp = final[wc]
    wn = home if wc=="H" else (away if wc=="A" else "Ничья")

    Xg = safe_X(meta["feat_goals"])
    lh = max(0.3, float(models["n6h_goals"].predict(Xg)[0]))
    la = max(0.1, float(models["n6a_goals"].predict(Xg)[0]))

    bsh, bsa, bp = 0, 0, -1.0
    for gh in range(6):
        for ga in range(6):
            p = (exp(-lh)*lh**gh/factorial(gh))*(exp(-la)*la**ga/factorial(ga))
            if p > bp:
                bp, bsh, bsa = p, gh, ga

    # ── Согласование счёта и победителя ──────────────────────────────────────
    # Если счёт равный но победитель не "Ничья" — исправляем
    if bsh == bsa and wc != "D":
        # Берём ничью если она достаточно вероятна (>20%)
        if final.get("D", 0) > 0.20:
            wc = "D"
            wp = final["D"]
            wn = "Ничья"
        else:
            # Иначе сдвигаем счёт в пользу победителя
            if wc == "H":
                bsh = bsa + 1
            else:
                bsa = bsh + 1

    # Если счёт разный но победитель "Ничья" — исправляем счёт
    elif bsh != bsa and wc == "D":
        if bsh > bsa:
            bsa = bsh  # выравниваем
        else:
            bsh = bsa  # выравниваем

    fg = round(90/(lh+la)) if lh+la > 0 else None
    conf = ("high" if wp>=CONFIDENCE_THR["high"] else
            "medium" if wp>=CONFIDENCE_THR["medium"] else "low")

    # ── Поправка новостей N5 ──────────────────────────────────────────────────
    wp_original = wp
    if news_home or news_away:
        if ANTHROPIC_API_KEY:
            from agent_collector import apply_news_adjustment
            if wc == "H" and news_home:
                wp = apply_news_adjustment(wp, news_home, is_home=True)
            elif wc == "A" and news_away:
                wp = apply_news_adjustment(wp, news_away, is_home=False)
            elif wc == "D":
                if news_home:
                    wp = apply_news_adjustment(wp, news_home, is_home=True)
                if news_away:
                    wp = apply_news_adjustment(wp, news_away, is_home=False)

            if abs(wp - wp_original) >= 0.01:
                log.info(f"  📰 Новостная поправка: {wp_original:.2f} → {wp:.2f}")

    # ── Поправка на форму и отдых (из аналитики highlights) ──────────────────
    try:
        from run_bot import TEAM_NORM
        _DB = sqlite3.connect(DB_PATH)

        def form_adjustment(team_name, is_winner):
            """Считает поправку на основе реальной формы и отдыха."""
            db_team = TEAM_NORM.get(team_name, team_name)
            adj = 0.0

            # Форма последних 6 матчей
            rows = _DB.execute("""
                SELECT ftr, home_team FROM matches_features
                WHERE (home_team=? OR away_team=?)
                ORDER BY date DESC LIMIT 6
            """, (db_team, db_team)).fetchall()
            if rows and len(rows) >= 4:
                wins = sum(1 for ftr, ht in rows
                           if (ftr=="H" and ht==db_team) or (ftr=="A" and ht!=db_team))
                win_rate = wins / len(rows)
                # Отклонение от средней формы (0.4 = среднее)
                form_delta = (win_rate - 0.40) * 0.08
                adj += form_delta

            # Дни отдыха
            last = _DB.execute("""
                SELECT MAX(date) FROM matches_features
                WHERE home_team=? OR away_team=?
            """, (db_team, db_team)).fetchone()
            if last and last[0]:
                try:
                    from datetime import date as _d
                    days = (_d.fromisoformat(match_date) -
                            _d.fromisoformat(last[0][:10])).days
                    # Оптимум 5-10 дней. Меньше 3 — усталость. Больше 14 — застой.
                    if days <= 2:
                        adj -= 0.03
                    elif days >= 14:
                        adj -= 0.01
                except Exception:
                    pass
            return round(max(-0.08, min(0.08, adj)), 3)

        adj_h = form_adjustment(home, wc == "H")
        adj_a = form_adjustment(away, wc == "A")
        _DB.close()

        # Применяем к победителю
        if wc == "H" and abs(adj_h) >= 0.01:
            wp = max(0.05, min(0.95, wp + adj_h))
            log.info(f"  📊 Поправка формы {home}: {adj_h:+.3f} → wp={wp:.2f}")
        elif wc == "A" and abs(adj_a) >= 0.01:
            wp = max(0.05, min(0.95, wp + adj_a))
            log.info(f"  📊 Поправка формы {away}: {adj_a:+.3f} → wp={wp:.2f}")

    except Exception as _fe:
        log.debug(f"  Form adj error: {_fe}")

    news_summary_h = news_home.get("news_summary", "") if news_home else ""
    news_summary_a = news_away.get("news_summary", "") if news_away else ""
    key_absences_h = news_home.get("key_absences", []) if news_home else []
    key_absences_a = news_away.get("key_absences", []) if news_away else []

    return {
        "home":home,"away":away,"winner_name":wn,"winner_code":wc,
        "winner_prob":wp,"ph":final.get("H",0),"pd":final.get("D",0),
        "pa":final.get("A",0),"score_h":bsh,"score_a":bsa,
        "lambda_h":lh,"lambda_a":la,"first_goal":fg,
        "confidence":conf,"odds":odds,
        "league_key":league_key,
        # Новости
        "news_home": news_summary_h,
        "news_away": news_summary_a,
        "absences_home": key_absences_h,
        "absences_away": key_absences_a,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  3. ДООБУЧЕНИЕ МОДЕЛЕЙ на новых данных
# ═══════════════════════════════════════════════════════════════════════════════

def update_models_with_new_data(models):
    """
    Проверяет есть ли новые завершённые матчи в БД,
    которые ещё не вошли в обучение. Если есть — дообучает модели.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        # Смотрим последнюю дату в features
        last = pd.read_sql("SELECT MAX(date) as d FROM matches_features", conn).iloc[0]["d"]
        # Смотрим есть ли новые матчи в matches после этой даты
        new = pd.read_sql(f"""
            SELECT COUNT(*) as cnt FROM matches_raw
            WHERE date > '{last}' AND FTR IS NOT NULL
        """, conn).iloc[0]["cnt"]
        conn.close()

        if new == 0:
            log.info(f"Новых матчей для дообучения нет (последняя дата: {last})")
            return models

        log.info(f"Найдено {new} новых матчей — запускаю дообучение...")

        # Пересчитываем features и переобучаем
        import subprocess, sys
        subprocess.run([sys.executable, "build_features.py"], check=True)
        subprocess.run([sys.executable, "train_models.py"],   check=True)

        # Перезагружаем свежие модели
        new_models = load_models()
        log.info("Модели успешно дообучены и перезагружены")
        return new_models

    except Exception as e:
        log.warning(f"Дообучение пропущено: {e}")
        return models


# ═══════════════════════════════════════════════════════════════════════════════
#  4. ФОРМАТИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════════════════════

def format_message(r, time_bkk, match_date, league_name):
    from live_collector import get_first_goal_prediction

    pct = int(r["winner_prob"] * 100)

    def goals_word(n):
        if n == 1:        return "1 гол"
        if n in (2,3,4):  return f"{n} гола"
        return f"{n} голов"

    # Аналитика первого гола из последних 5 матчей
    league_key = r.get("league_key", "soccer_epl")
    fg_result = get_first_goal_prediction(
        r["home"], r["away"],
        r["lambda_h"], r["lambda_a"],
        league_key
    )

    # ── Позитивная аналитика команд ──────────────────────────────────────────
    def get_team_highlights(team_name, is_winner):
        """Собирает позитивные факты о команде из БД."""
        highlights = []
        try:
            from run_bot import TEAM_NORM
            db_team = TEAM_NORM.get(team_name, team_name)
            _c = sqlite3.connect(DB_PATH)

            # Дни отдыха
            last_match = _c.execute("""
                SELECT MAX(date) FROM matches_features
                WHERE home_team=? OR away_team=?
            """, (db_team, db_team)).fetchone()
            if last_match and last_match[0]:
                from datetime import date as _date
                try:
                    days = (_date.fromisoformat(match_date) -
                            _date.fromisoformat(last_match[0][:10])).days
                    if days >= 7:
                        highlights.append(f"  💤 {days} дней отдыха перед матчем")
                    elif days <= 3:
                        highlights.append(f"  ⚡ только {days} дня после последней игры")
                except Exception:
                    pass

            # Форма — последние 6 матчей
            rows = _c.execute("""
                SELECT ftr, home_team FROM matches_features
                WHERE (home_team=? OR away_team=?)
                ORDER BY date DESC LIMIT 6
            """, (db_team, db_team)).fetchall()
            if rows:
                wins = sum(1 for ftr, ht in rows
                           if (ftr=="H" and ht==db_team) or (ftr=="A" and ht!=db_team))
                total = len(rows)
                if wins >= 5:
                    highlights.append(f"  🔥 {wins} побед в последних {total} матчах")
                elif wins >= 4:
                    highlights.append(f"  📈 хорошая форма: {wins}/{total} побед")
                elif wins <= 1:
                    highlights.append(f"  📉 слабая форма: {wins}/{total} побед")

            # Позиция в таблице
            _API_NAMES = {
                "Man City": "Manchester City", "Man United": "Manchester United",
                "Newcastle": "Newcastle United", "Wolves": "Wolverhampton",
                "Tottenham": "Tottenham", "Nott'm Forest": "Nottingham",
                "Ein Frankfurt": "Eintracht", "M'gladbach": "Borussia M",
                "Ath Madrid": "Atletico", "Ath Bilbao": "Athletic",
                "Paris SG": "Paris Saint",
            }
            api_name = _API_NAMES.get(db_team, db_team)
            pos_row = _c.execute("""
                SELECT rank, wins, draws, losses FROM api_standings
                WHERE LOWER(team_name) LIKE ?
                ORDER BY fetched_at DESC LIMIT 1
            """, ("%" + api_name[:6].lower() + "%",)).fetchone()
            if pos_row:
                rank, w, d, l = pos_row
                total_g = (w or 0) + (d or 0) + (l or 0)
                if rank and rank <= 4:
                    highlights.append(f"  🏆 {rank}-е место в таблице")
                elif rank and rank >= 16:
                    highlights.append(f"  ⚠️ {rank}-е место в таблице")
                if total_g > 0 and (w or 0) > 0:
                    highlights.append(f"  📊 сезон: {w}п / {d}н / {l}п ({total_g} матчей)")

            _c.close()
        except Exception:
            pass
        return highlights

    # Собираем аналитику для обеих команд
    home_highlights = get_team_highlights(r["home"], r["winner_name"] == r["home"])
    away_highlights = get_team_highlights(r["away"], r["winner_name"] == r["away"])

    lines = [
        f"",
        f"📅  <b>{match_date}</b>  🕐  <b>{time_bkk}</b> (Bangkok)",
        f"",
        f"🆚  <b>{r['home']}</b>  vs  <b>{r['away']}</b>",
        f"",
        f"📈  Вероятность победы  <b>{r['winner_name']}</b>:  <b>{pct}%</b>",
        f"",
        f"📊  Счёт:  <b>{r['score_h']}:{r['score_a']}</b>  "
        f"({r['home']} — {goals_word(r['score_h'])}, {r['away']} — {goals_word(r['score_a'])})",
    ]

    # Первый гол
    if fg_result:
        player, fg_team, minute, conf_pct = fg_result
        lines += [
            f"",
            f"⏱ <b>Первый гол:</b>  <b>{player}</b> ({fg_team})  ~{minute}'",
        ]
    else:
        lines += [f"", f"⏱ <b>Первый гол:</b>  нет данных"]

    # Новости — травмы и аналитика
    news_h = r.get("news_home","")
    news_a = r.get("news_away","")
    abs_h  = r.get("absences_home",[])
    abs_a  = r.get("absences_away",[])

    news_lines = []

    def format_team_news(team_name, absences, highlights, news_summary):
        """Форматирует блок новостей команды: травмы + аналитика + новости."""
        block = []
        # Травмы
        if absences:
            block.append(f"  🏥 <b>{team_name}:</b>")
            for entry in absences[:4]:
                block.append(f"    — {entry}")
        else:
            block.append(f"  ✅ <b>{team_name}:</b> нет травм и дисквалификаций")
        # Позитивная аналитика
        for h in highlights[:3]:
            block.append(h)
        # Новости из Claude — только если это НЕ дубль травм
        if news_summary and news_summary not in ("Нет значимых новостей", "Нет данных", ""):
            # Пропускаем если новость это просто список травм (дубль)
            is_injury_dup = news_summary.lower().startswith(("травмирован", "injured"))
            if not is_injury_dup:
                block.append(f"  📰 {news_summary[:200]}")
        return "\n".join(block)

    news_lines.append(format_team_news(r["home"], abs_h, home_highlights, news_h))
    news_lines.append(format_team_news(r["away"], abs_a, away_highlights, news_a))

    # ── Новости и травмы ─────────────────────────────────────────────────────
    lines += [f"", f"🗞 <b>Аналитика команд:</b>"] + news_lines

    # Прогноз API учитывается в расчёте (N7), но не показывается пользователю

    # ── Составы ───────────────────────────────────────────────────────────────
    try:
        _c = sqlite3.connect(DB_PATH)
        lineup_found = False
        for team_name, label, side in [
            (r["home"], "🏠", "Хозяева"),
            (r["away"], "✈️", "Гости")
        ]:
            lineup_row = _c.execute("""
                SELECT lineup_json, formation, coach_name FROM match_lineups
                WHERE LOWER(team_name) LIKE ?
                ORDER BY fetched_at DESC LIMIT 1
            """, ("%" + team_name[:5].lower() + "%",)).fetchone()
            if lineup_row:
                ldata     = json.loads(lineup_row[0])
                formation = lineup_row[1] or "?"
                coach     = lineup_row[2] or ""
                starters  = [p["name"] for p in ldata.get("start", []) if p.get("name")]
                subs      = [s for s in ldata.get("subs", []) if s]
                if starters:
                    if not lineup_found:
                        lines += [f"", f"👕 <b>Составы на матч:</b>"]
                        lineup_found = True
                    lines += [f""]
                    lines += [f"  {label} <b>{team_name}</b>  [{formation}]"
                              + (f"  · тренер: {coach}" if coach else "")]
                    # Разбиваем по позициям
                    gk  = [p["name"] for p in ldata["start"] if p.get("pos") == "G"]
                    df  = [p["name"] for p in ldata["start"] if p.get("pos") == "D"]
                    mid = [p["name"] for p in ldata["start"] if p.get("pos") == "M"]
                    fw  = [p["name"] for p in ldata["start"] if p.get("pos") == "F"]
                    if gk:  lines += [f"  🧤 Вратарь:    {gk[0]}"]
                    if df:  lines += [f"  🛡 Защита:     {', '.join(df)}"]
                    if mid: lines += [f"  ⚙️ Полузащита: {', '.join(mid)}"]
                    if fw:  lines += [f"  ⚡ Нападение:  {', '.join(fw)}"]
                    if subs:
                        lines += [f"  🔄 Запасные:   {', '.join(subs[:5])}"]
        _c.close()
    except Exception:
        pass

    lines += [
        f"",
        f"▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  5. TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

# ── Схема таблицы отправленных сообщений ──────────────────────────────────────
SENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at      TEXT,
    match_key    TEXT UNIQUE,   -- "home|away|date"
    home_team    TEXT,
    away_team    TEXT,
    match_date   TEXT,
    winner_name  TEXT,
    winner_prob  INTEGER,       -- %
    score_h      INTEGER,
    score_a      INTEGER,
    message_id   INTEGER,
    views        INTEGER DEFAULT 0,
    result_checked INTEGER DEFAULT 0,  -- 1 если результат уже проверен
    real_score_h   INTEGER,
    real_score_a   INTEGER,
    prediction_ok  INTEGER,            -- 1=верно, 0=неверно, NULL=ещё не сыгран
    -- Прогноз первого гола
    fg_player      TEXT,
    fg_team        TEXT,
    fg_minute      INTEGER,
    -- Реальный первый гол
    real_fg_player TEXT,
    real_fg_team   TEXT,
    real_fg_minute INTEGER,
    fg_ok          INTEGER             -- 1=верно, 0=неверно, NULL=нет данных
)
"""

def _init_sent_db(conn):
    conn.execute(SENT_SCHEMA)
    conn.commit()


def _match_key(home: str, away: str, date: str) -> str:
    return f"{home}|{away}|{date}"


def _already_sent(conn, home: str, away: str, date: str,
                  winner_name: str, winner_prob: int,
                  score_h: int, score_a: int) -> bool:
    """
    Возвращает True если сообщение уже отправлялось И данные не изменились.
    Если данные изменились — удаляет старую запись (будет отправлено заново).
    """
    key = _match_key(home, away, date)
    row = conn.execute(
        "SELECT winner_name, winner_prob, score_h, score_a FROM sent_messages WHERE match_key=?",
        (key,)
    ).fetchone()
    if not row:
        return False
    # Данные совпадают — не отправлять
    if (row[0] == winner_name and row[1] == winner_prob
            and row[2] == score_h and row[3] == score_a):
        log.info(f"  ⏭ Уже отправлено без изменений: {home} vs {away} — пропуск")
        return True
    # Данные изменились — удаляем, отправим заново
    conn.execute("DELETE FROM sent_messages WHERE match_key=?", (key,))
    conn.commit()
    log.info(f"  🔄 Данные изменились: {home} vs {away} — обновляем")
    return False


def _save_sent(conn, home: str, away: str, date: str,
               winner_name: str, winner_prob: int,
               score_h: int, score_a: int, message_id: int | None,
               fg_player: str = None, fg_team: str = None, fg_minute: int = None):
    key = _match_key(home, away, date)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("""
        INSERT OR REPLACE INTO sent_messages
        (sent_at, match_key, home_team, away_team, match_date,
         winner_name, winner_prob, score_h, score_a, message_id,
         fg_player, fg_team, fg_minute)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (now, key, home, away, date,
          winner_name, winner_prob, score_h, score_a, message_id,
          fg_player, fg_team, fg_minute))
    conn.commit()


def send_to_channel(text: str, add_webapp_btn: bool = False) -> int | None:
    """Отправляет в канал, возвращает message_id или None."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
            if add_webapp_btn and WEB_APP_URL:
                payload["reply_markup"] = {
                    "inline_keyboard": [[{
                        "text": "📅 Открыть календарь",
                        "web_app": {"url": WEB_APP_URL}
                    }]]
                }
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                log.info("Сообщение отправлено в канал")
                return r.json().get("result", {}).get("message_id")
            elif r.status_code == 429:
                # Too Many Requests — ждём сколько сказал Telegram
                retry_after = r.json().get("parameters", {}).get("retry_after", 20)
                log.warning(f"Telegram 429: ждём {retry_after}с...")
                time.sleep(retry_after + 1)
                continue
            log.error(f"Telegram {r.status_code}: {r.text[:120]}")
            return None
        except Exception as e:
            log.error(f"Telegram ошибка: {e}")
            return None
    return None


def send_telegram(text: str) -> bool:
    """Обратная совместимость."""
    return send_to_channel(text) is not None


def send_dm(text: str):
    """Отправляет личное сообщение владельцу."""
    if not OWNER_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": OWNER_CHAT_ID, "text": text, "parse_mode": "HTML",
        }, timeout=15)
    except Exception:
        pass


def get_message_views(message_id: int) -> int:
    """Получает количество просмотров сообщения в канале."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/forwardMessage"
        # Для каналов используем getChat + getChatMember не работает для просмотров
        # Используем getMessage через channel forward trick
        # Просто возвращаем -1 (Telegram не даёт просмотры через Bot API для обычных каналов)
        return -1
    except Exception:
        return -1


def send_header(total, passed, leagues_str):
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    send_to_channel(
        f"📡 <b>Sports Analytics Bot</b>\n"
        f"{now} (BKK)\n"
        f"{'─'*32}\n"
        f"Лиги: {leagues_str}\n"
        f"Матчей проверено: {total}  |  прошли 60%+: <b>{passed}</b> 👇"
    )


def send_no_matches():
    """Если прогнозов нет — ищем свежие футбольные новости через Claude."""

    # Пробуем найти новости через Claude
    if ANTHROPIC_API_KEY:
        try:
            from agent_collector import _claude_search, _parse_json_from_text
            from datetime import date
            today = date.today().strftime("%d %B %Y")

            prompt = f"""Search for the most important football news today {today}.
Find 3-4 biggest stories from top European leagues (Premier League, La Liga, Bundesliga, Serie A, Ligue 1).
Focus on: transfers, injuries of key players, manager changes, big match results, upcoming big matches.

Return ONLY JSON:
```json
{{
  "news": [
    {{"title": "Короткий заголовок на русском", "body": "2-3 предложения на русском с деталями"}},
    {{"title": "...", "body": "..."}}
  ]
}}
```"""

            response = _claude_search(prompt, max_tokens=800)
            if response:
                parsed = _parse_json_from_text(response)
                if parsed and parsed.get("news"):
                    from datetime import datetime, timezone, timedelta
                    now_bkk = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d.%m.%Y")
                    lines = [
                        f"⚽ <b>Футбольные новости</b> · {now_bkk}",
                        f"{'─'*34}",
                        f"",
                    ]
                    for item in parsed["news"][:4]:
                        title = item.get("title", "")
                        body  = item.get("body", "")
                        if title:
                            lines.append(f"📰 <b>{title}</b>")
                        if body:
                            lines.append(f"{body}")
                        lines.append("")

                    lines += [
                        f"{'─'*34}",
                        f"🤖 Прогнозов на сегодня нет — ни один матч не достиг уверенности 60%+",
                    ]
                    send_to_channel("\n".join(lines))
                    log.info("Новости футбола отправлены в канал")
                    return
        except Exception as e:
            log.debug(f"Новости не получены: {e}")

    # Fallback если Claude недоступен
    send_to_channel(
        f"⚽ <b>Sports Analytics Bot</b>\n{'─'*32}\n\n"
        f"📅 Новых прогнозов на сегодня нет.\n\n"
        f"Все актуальные матчи уже были опубликованы ранее.\n"
        f"Следующие прогнозы — завтра в 09:00."
    )


def send_footer(sent, skipped):
    send_to_channel(
        f"{'─'*32}\n"
        f"✅ Отправлено: <b>{sent}</b>  |  Пропущено: <b>{skipped}</b>\n"
        f"⚠ Прогноз — аналитика, не гарантия результата"
    )


def _get_real_result(home: str, away: str) -> tuple | None:
    """
    Ищет реальный результат матча в matches_raw.
    Возвращает (real_score_h, real_score_a, real_winner) или None.
    """
    try:
        conn2 = sqlite3.connect(DB_PATH)
        # Ищем матч в matches_raw с любым похожим именем
        row = conn2.execute("""
            SELECT fthg, ftag, ftr FROM matches_raw
            WHERE (
                (LOWER(home_team) LIKE ? AND LOWER(away_team) LIKE ?)
                OR (LOWER(home_team) LIKE ? AND LOWER(away_team) LIKE ?)
            )
            AND fthg IS NOT NULL
            ORDER BY date DESC LIMIT 1
        """, (
            f"%{home[:5].lower()}%", f"%{away[:5].lower()}%",
            f"%{away[:5].lower()}%", f"%{home[:5].lower()}%",
        )).fetchone()
        conn2.close()
        if not row:
            return None
        fthg, ftag, ftr = row
        if ftr == "H":
            real_winner = "home"
        elif ftr == "A":
            real_winner = "away"
        else:
            real_winner = "draw"
        return int(fthg), int(ftag), real_winner
    except Exception:
        return None


def check_and_report_results(conn):
    """
    Проверяет все ранее отправленные прогнозы — сыгран ли матч.
    Если сыгран → сравнивает с прогнозом и отправляет владельцу в ЛС.
    """
    from datetime import date as date_type

    today = date_type.today()

    # Берём все прогнозы, которые ещё не проверены и дата матча уже прошла
    rows = conn.execute("""
        SELECT match_key, home_team, away_team, match_date,
               winner_name, score_h, score_a
        FROM sent_messages
        WHERE result_checked = 0
          AND match_date IS NOT NULL
          AND match_date < ?
    """, (today.strftime("%Y-%m-%d"),)).fetchall()

    if not rows:
        return

    lines = ["🏆 <b>Результаты прогнозов</b>", "─" * 32]
    checked = 0

    for row in rows:
        key, home, away, match_date, winner_name, score_h, score_a = row

        if not home or not away:
            # Старые записи без имён команд — пропускаем
            conn.execute(
                "UPDATE sent_messages SET result_checked=1 WHERE match_key=?", (key,)
            )
            continue

        result = _get_real_result(home, away)
        if not result:
            # Результата ещё нет в БД — пропускаем до следующего запуска
            continue

        real_h, real_a, real_winner = result

        # Определяем кто был наш прогноз
        if winner_name == home:
            pred_winner = "home"
        elif winner_name == away:
            pred_winner = "away"
        else:
            pred_winner = "draw"

        prediction_ok = 1 if pred_winner == real_winner else 0

        # Сохраняем результат
        conn.execute("""
            UPDATE sent_messages
            SET result_checked=1, real_score_h=?, real_score_a=?, prediction_ok=?
            WHERE match_key=?
        """, (real_h, real_a, prediction_ok, key))

        icon = "✅" if prediction_ok else "❌"
        try:
            dt = datetime.strptime(match_date, "%Y-%m-%d").strftime("%d.%m")
        except Exception:
            dt = match_date

        lines.append(
            f"{icon} <b>{dt}</b>  {home} <b>{real_h}:{real_a}</b> {away}\n"
            f"   Прогноз: {winner_name} → "
            f"{'верно' if prediction_ok else 'не верно'}"
        )
        checked += 1

    conn.commit()

    if checked > 0:
        # Общая точность за всё время
        stats = conn.execute("""
            SELECT
                SUM(CASE WHEN prediction_ok=1 THEN 1 ELSE 0 END) as correct,
                COUNT(*) as total
            FROM sent_messages
            WHERE result_checked=1 AND prediction_ok IS NOT NULL
        """).fetchone()
        if stats and stats[1] > 0:
            acc = round(stats[0] / stats[1] * 100, 1)
            lines.append(f"─" * 32)
            lines.append(f"📊 Общая точность: <b>{stats[0]}/{stats[1]} ({acc}%)</b>")

        send_dm("\n".join(lines))
        log.info(f"Отправлены результаты {checked} матчей владельцу")


def send_owner_stats(total_matches: int, total_sent: int,
                     new_sent: int, skipped: int,
                     predictions: list):
    """Отправляет статистику работы владельцу в ЛС."""
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [
        f"📊 <b>Статистика запуска</b>",
        f"{now} (Bangkok)",
        f"{'─'*32}",
        f"📋 Матчей обработано:   <b>{total_matches}</b>",
        f"✅ Прогнозов вынесено:  <b>{total_sent}</b>",
        f"🆕 Новых отправлено:    <b>{new_sent}</b>",
        f"⏭ Пропущено (дубли):   <b>{skipped}</b>",
        f"",
        f"<b>Прогнозы сегодня:</b>",
    ]
    for r in predictions[:10]:  # не более 10
        m = r.get("match", {})
        pct = int(r["winner_prob"] * 100)
        lines.append(
            f"  • {m.get('home','?')} vs {m.get('away','?')} "
            f"→ {r['winner_name']} {pct}%"
        )
    lines += [
        f"",
        f"👁 Просмотры в канале: <b>недоступно через Bot API</b>",
        f"{'─'*32}",
        f"💡 Следующий запуск — завтра в 09:00 Bangkok",
    ]
    send_dm("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run():
    import time
    from live_collector import clear_cache, prefetch_all_scorers

    clear_cache()
    log.info("=" * 55)
    log.info("SPORTS ANALYTICS BOT v2.0 — запуск")
    log.info("=" * 55)
    if ANTHROPIC_API_KEY:
        log.info("🤖 Режим: ML-агенты с Claude web search (N1–N4)")
    else:
        log.info("📡 Режим: ESPN + локальная БД (добавьте ANTHROPIC_API_KEY для агентов)")

    conn = sqlite3.connect(DB_PATH)
    _init_sent_db(conn)

    # 0. Проверяем результаты прошлых прогнозов
    log.info("Проверяю результаты прошлых прогнозов...")
    check_and_report_results(conn)
    all_matches = fetch_all_leagues(conn)
    if not all_matches:
        log.error("Нет матчей — проверьте ODDS_API_KEY")
        conn.close()
        return

    # 2. Модели + дообучение
    log.info("Загружаю ML-модели...")
    models = load_models()
    models = update_models_with_new_data(models)

    # 3. Прогнозы — только для матчей в ближайшие 3 дня
    results = []
    announce_only = []  # матчи на 4-14 дней — только анонс

    def is_top_match(m):
        """Проверяет что хотя бы одна из команд входит в топ список."""
        league = m.get("league", "")
        if league in SHOW_ALL_LEAGUES:
            return True  # В еврокубках все матчи интересны
        home = TEAM_NORM.get(m["home"], m["home"])
        away = TEAM_NORM.get(m["away"], m["away"])
        for t in TOP_CLUBS:
            if t.lower() in home.lower() or home.lower() in t.lower():
                return True
            if t.lower() in away.lower() or away.lower() in t.lower():
                return True
        return False

    for m in all_matches:
        if not m.get("is_predictable", True):
            # Анонсируем только топ матчи
            if is_top_match(m):
                announce_only.append(m)
                log.info(f"📅 Анонс: {m['home']} vs {m['away']} {m['date']} — прогноз не готов")
            continue

        log.info(f"Прогноз: {m['home']} vs {m['away']} ({m['league_name']})")
        try:
            r = predict_one(models, conn, m["home"], m["away"], m["date"],
                        m.get("league", "soccer_epl"))
            r["match"] = m
            results.append(r)
            status = "✅ ПРОЙДЁТ" if r["winner_prob"] >= MIN_SEND_PROB else "❌ отфильтрован"
            log.info(f"{r['winner_name']} {int(r['winner_prob']*100)}% — {status}")
        except Exception as e:
            log.error(f"Ошибка прогноза {m['home']} vs {m['away']}: {e}")
        time.sleep(0.2)

    log.info(f"Прогнозов: {len(results)}, анонсов без прогноза: {len(announce_only)}")

    # 4. Фильтр 60%+
    def smart_filter(r):
        """Прогноз отправляем только если:
        1. Вероятность >= 72%
        2. Котировки букмекеров подтверждают фаворита (коэф < 2.0)
        3. Фаворит играет дома ИЛИ вероятность >= 75%
        """
        if r["winner_prob"] < MIN_SEND_PROB:
            return False

        m = r["match"]
        winner = r["winner_name"]

        # Проверяем котировки — фаворит должен иметь коэф < 2.5
        ph = r.get("ph", 0)  # вероятность победы хозяев по котировкам
        pa = r.get("pa", 0)  # вероятность победы гостей по котировкам
        is_home_win = winner == m["home"]

        odds_prob = ph if is_home_win else pa
        if odds_prob < 0.40:  # котировки дают фавориту меньше 40% — пропускаем
            log.info(f"⚠️ {winner} отфильтрован — котировки не подтверждают ({odds_prob:.0%})")
            return False

        # Дополнительный фильтр: гостевой фаворит требует 72%+
        if not is_home_win and r["winner_prob"] < 0.65:
            log.info(f"⚠️ {winner} отфильтрован — гостевой фаворит с низкой уверенностью")
            return False

        return True

    to_send = sorted(
        [r for r in results if smart_filter(r)],
        key=lambda x: x["match"]["date"]
    )
    skipped_filter = len(results) - len(to_send)
    log.info(f"Итого: {len(to_send)} прошли фильтр, {skipped_filter} пропущено")

    # 5. Дедупликация — убираем уже отправленные без изменений
    # И дедупликация внутри одного запуска (если матч встречается дважды)
    to_send_new = []
    skipped_dedup = 0
    seen_keys = set()  # защита от дублей внутри одного запуска

    for r in to_send:
        m = r["match"]
        run_key = _match_key(m["home"], m["away"], m["date"])

        # Дубль внутри одного запуска
        if run_key in seen_keys:
            log.info(f"  ⏭ Дубль в списке: {m['home']} vs {m['away']} — пропуск")
            skipped_dedup += 1
            continue
        seen_keys.add(run_key)

        # Уже отправлено в прошлых запусках
        already = _already_sent(
            conn,
            m["home"], m["away"], m["date"],
            r["winner_name"], int(r["winner_prob"] * 100),
            r["score_h"], r["score_a"]
        )
        if already:
            skipped_dedup += 1
        else:
            to_send_new.append(r)

    log.info(f"Дубли пропущено: {skipped_dedup}, новых к отправке: {len(to_send_new)}")

    # 5б. Загружаем новости только для матчей которые будем отправлять
    if to_send_new and ANTHROPIC_API_KEY:
        log.info(f"📰 Загружаю новости для {len(to_send_new)} матчей...")
        from agent_collector import n5_agent_news, set_api_key
        set_api_key(ANTHROPIC_API_KEY)
        for r in to_send_new:
            m = r["match"]
            try:
                hn = n5_agent_news(m["home"], m["date"])
                an = n5_agent_news(m["away"], m["date"])
                r["news_home"]     = hn.get("news_summary", "")
                r["news_away"]     = an.get("news_summary", "")
                r["absences_home"] = hn.get("key_absences", [])
                r["absences_away"] = an.get("key_absences", [])
            except Exception as e:
                log.debug(f"Новости {m['home']} vs {m['away']}: {e}")
                r.setdefault("news_home", "")
                r.setdefault("news_away", "")
                r.setdefault("absences_home", [])
                r.setdefault("absences_away", [])

    # 6. Telegram — канал
    leagues_str = " | ".join(set(r["match"]["league_name"] for r in to_send)) or "—"

    if not to_send_new and not to_send:
        # Совсем нет матчей с 60%+
        send_no_matches()
        send_owner_stats(len(results), 0, 0, skipped_filter, [])
        conn.close()
        return

    if not to_send_new and to_send:
        # Прогнозы есть, но все уже были отправлены — шлём новости
        log.info("Все прогнозы уже отправлены — публикую новости дня")
        send_no_matches()
        conn.close()
        return

    TG_LIMIT = 4096

    # Порядок лиг
    LEAGUE_ORDER = [
        "🏆 Лига Чемпионов",
        "🥈 Лига Европы",
        "🥉 Лига Конференций",
        "🏴󠁧󠁢󠁥󠁮󠁧󠁿 АПЛ",
        "🇪🇸 Ла Лига",
        "🇮🇹 Серия А",
        "🇫🇷 Лига 1",
    ]

    # Группируем по лигам, внутри — по дате
    from collections import defaultdict
    by_league = defaultdict(list)
    for r in to_send_new:
        m = r["match"]
        msg = format_message(r, m["time_bkk"], m["date"], m["league_name"])
        log.info(re.sub(r"<[^>]+>", "", msg[:200]))
        by_league[m["league_name"]].append((r, m, msg))

    # Сортируем лиги в нужном порядке
    sorted_leagues = sorted(
        by_league.keys(),
        key=lambda x: LEAGUE_ORDER.index(x) if x in LEAGUE_ORDER else 99
    )

    # Каждая пара = отдельное сообщение, с заголовком лиги
    sent = 0
    all_blocks = []
    now_bkk = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d.%m.%Y")

    for league_name in sorted_leagues:
        league_blocks = by_league[league_name]
        # Сортируем матчи лиги по дате
        league_blocks.sort(key=lambda x: x[1]["date"])

        for r, m, msg in league_blocks:
            # Заголовок лиги в каждом сообщении
            league_header = (
                f"⚽ <b>{league_name}</b>\n"
                f"{'─'*34}"
            )
            # format_message начинается с пустой строки — берём всё сообщение целиком
            full_msg = league_header + "\n" + msg.lstrip("\n")

            # Если сообщение слишком длинное — обрезаем составы
            if len(full_msg) > TG_LIMIT:
                full_msg = full_msg[:TG_LIMIT - 10] + "\n..."

            # Кнопка календаря только на последнем сообщении дня
            is_last = (league_blocks.index((r, m, msg)) == len(league_blocks) - 1
                       and sorted_leagues.index(league_name) == len(sorted_leagues) - 1)
            msg_id = send_to_channel(full_msg, add_webapp_btn=is_last)
            if msg_id is not None:
                sent += 1
            time.sleep(3)

        all_blocks.extend(league_blocks)

    # Сохраняем отправленные прогнозы (65%+) в БД
    for r, m, msg in all_blocks:
        fg = r.get("first_goal")
        fg_player = fg[0] if fg and isinstance(fg, (list, tuple)) and len(fg) >= 1 else None
        fg_team   = fg[1] if fg and isinstance(fg, (list, tuple)) and len(fg) >= 2 else None
        fg_minute = fg[2] if fg and isinstance(fg, (list, tuple)) and len(fg) >= 3 else None
        _save_sent(
            conn,
            m["home"], m["away"], m["date"],
            r["winner_name"], int(r["winner_prob"] * 100),
            r["score_h"], r["score_a"],
            None,
            fg_player, fg_team, fg_minute
        )

    # Сохраняем ВСЕ прогнозы в БД (включая ниже 65%) — для Web App
    for r in results:
        m = r["match"]
        if r["winner_prob"] < MIN_SEND_PROB:
            fg = r.get("first_goal")
            fg_player = fg[0] if fg and isinstance(fg, (list, tuple)) and len(fg) >= 1 else None
            fg_team   = fg[1] if fg and isinstance(fg, (list, tuple)) and len(fg) >= 2 else None
            fg_minute = fg[2] if fg and isinstance(fg, (list, tuple)) and len(fg) >= 3 else None
            # Проверяем не записан ли уже этот матч
            key = f"{m['home']}|{m['away']}|{m['date']}"
            existing = conn.execute(
                "SELECT 1 FROM sent_messages WHERE match_key=?", (key,)
            ).fetchone()
            if not existing:
                _save_sent(
                    conn,
                    m["home"], m["away"], m["date"],
                    r["winner_name"], int(r["winner_prob"] * 100),
                    r["score_h"], r["score_a"],
                    None,
                    fg_player, fg_team, fg_minute
                )

    # 7. Личная статистика владельцу
    send_owner_stats(
        total_matches=len(results),
        total_sent=len(to_send),
        new_sent=sent,
        skipped=skipped_dedup,
        predictions=to_send
    )

    conn.close()
    log.info(f"Готово! Отправлено сообщений: {sent}, прогнозов: {len(all_blocks)}")


if __name__ == "__main__":
    run()
