"""
agent_collector.py — ML-агенты N1–N4

Каждая модель — автономный агент:
  N1 — ищет форму команды (последние 5 матчей) через web search
  N2 — ищет H2H историю
  N3 — котировки (уже есть через odds-api)
  N4 — ищет контекст: место в таблице, травмы, дней отдыха

Если данных нет в БД или ESPN недоступен:
  → Claude API с web_search ищет актуальные данные
  → Парсит ответ в структурированный dict
  → Сохраняет в БД таблицу live_stats
  → Возвращает данные для ML-моделей

Требует: ANTHROPIC_API_KEY в конфиге
"""

import json
import time
import logging
import sqlite3
import requests
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

log = logging.getLogger(__name__)
DB_PATH = Path("data/epl_target_teams.db")

# ── API ───────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = ""   # вставьте свой ключ или читайте из .env
ANTHROPIC_URL     = "https://api.anthropic.com/v1/messages"
MODEL             = "claude-sonnet-4-20250514"

# ── Схема таблицы живой статистики ───────────────────────────────────────────
LIVE_STATS_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    updated_at   TEXT,
    team         TEXT,
    stat_type    TEXT,
    season       TEXT,
    data_json    TEXT,
    source       TEXT,
    UNIQUE(team, stat_type, season)
)
"""


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(LIVE_STATS_SCHEMA)
    conn.commit()
    return conn


def _save_to_db(conn, team: str, stat_type: str, data: dict, source: str):
    """Сохраняет/обновляет статистику в БД."""
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    season = "2025-26"
    try:
        conn.execute("""
            INSERT INTO live_stats (updated_at, team, stat_type, season, data_json, source)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(team, stat_type, season)
            DO UPDATE SET data_json=excluded.data_json,
                          updated_at=excluded.updated_at,
                          source=excluded.source
        """, (now, team, stat_type, season, json.dumps(data, ensure_ascii=False), source))
        conn.commit()
        log.debug(f"Сохранено в БД: {team} / {stat_type}")
    except Exception as e:
        log.debug(f"DB save error: {e}")


def _load_from_db(conn, team: str, stat_type: str) -> dict | None:
    """Загружает статистику из БД если она свежая (не старше 6 часов)."""
    try:
        row = conn.execute("""
            SELECT data_json, updated_at FROM live_stats
            WHERE team=? AND stat_type=? AND season='2025-26'
        """, (team, stat_type)).fetchone()
        if not row:
            return None
        # Проверяем свежесть
        updated = datetime.fromisoformat(row[1].replace("Z","+00:00"))
        age_h = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
        if age_h > 24:
            log.debug(f"Кеш {team}/{stat_type} устарел ({age_h:.1f}ч)")
            return None
        return json.loads(row[0])
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Claude API с web search
# ═══════════════════════════════════════════════════════════════════════════════

def _claude_search(prompt: str, max_tokens: int = 800) -> str | None:
    """
    Вызывает Claude API с web_search tool.
    Возвращает текстовый ответ или None при ошибке.
    """
    if not ANTHROPIC_API_KEY:
        return None

    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        r = requests.post(
            ANTHROPIC_URL,
            json=payload,
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            timeout=30,
        )
        if r.status_code != 200:
            log.debug(f"Claude API {r.status_code}: {r.text[:200]}")
            return None

        data = r.json()
        # Собираем все текстовые блоки
        texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        return "\n".join(texts) if texts else None

    except Exception as e:
        log.debug(f"Claude API error: {e}")
        return None


def _parse_json_from_text(text: str) -> dict | None:
    """Извлекает JSON из ответа Claude."""
    import re
    # Ищем ```json ... ``` блок
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # Ищем просто { ... }
    m = re.search(r"(\{[^{}]*\})", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  N1-АГЕНТ — Форма команды
# ═══════════════════════════════════════════════════════════════════════════════

def n1_agent_form(team: str, conn=None) -> dict:
    """
    N1-агент: собирает форму команды (последние 5 матчей).
    Порядок: matches_features БД → Claude web search → дефолт.
    """
    if conn is None:
        conn = _init_db()

    # Нормализуем имя команды к виду в БД
    from run_bot import TEAM_NORM
    db_team = TEAM_NORM.get(team, team)

    # 1. Проверяем кеш в live_stats БД
    cached = _load_from_db(conn, db_team, "form")
    if cached:
        log.info(f"  ✅ N1 {team}: форма из кеша БД")
        cached["source"] = "DB_cache"
        return cached

    # 2. Берём из matches_features — всегда надёжно
    from live_collector import _form_from_db
    form = _form_from_db(db_team)
    if form:
        _save_to_db(conn, db_team, "form", form, "matches_features")
        log.info(f"  ✅ N1 {team}: форма из matches_features")
        return form

    # 3. Claude web search — только если в БД совсем нет данных
    log.info(f"  🔍 N1 {team}: нет в БД — ищу через Claude web search...")
    prompt = f"""Find the last 5 completed football matches for {team} in the current 2025-26 season.
For each match provide: date, opponent, goals scored, goals conceded, result (W/D/L).

Then calculate these statistics:
- pts_avg: average points per game (W=3, D=1, L=0)
- win_rate: fraction of wins (0 to 1)
- draw_rate: fraction of draws
- loss_rate: fraction of losses
- gf_avg: average goals scored per game
- ga_avg: average goals conceded per game
- gd_avg: average goal difference per game
- weighted_form: sum of pts * 0.75^(n-1-i) for i=0..n-1 (most recent gets highest weight)

Return ONLY a JSON object like:
```json
{{"pts_avg": 1.8, "win_rate": 0.4, "draw_rate": 0.2, "loss_rate": 0.4,
  "gf_avg": 1.6, "ga_avg": 1.2, "gd_avg": 0.4, "sot_avg": 1.2,
  "weighted_form": 4.2, "n": 5, "source": "web_search"}}
```"""

    response = _claude_search(prompt)
    if response:
        parsed = _parse_json_from_text(response)
        if parsed and "pts_avg" in parsed:
            parsed["source"] = "claude_web"
            parsed["n"] = parsed.get("n", 5)
            _save_to_db(conn, team, "form", parsed, "claude_web")
            log.info(f"  ✅ N1 {team}: форма найдена через Claude web search → сохранено в БД")
            return parsed
        log.warning(f"  ❗ N1 {team}: Claude ответил, но JSON не распарсился")

    # Финальный fallback — нейтральные значения
    log.warning(f"  ❗ N1 {team}: нет данных, используем нейтральные")
    return {
        "pts_avg": 1.2, "win_rate": 0.33, "draw_rate": 0.25, "loss_rate": 0.42,
        "gf_avg": 1.3, "ga_avg": 1.3, "gd_avg": 0.0, "sot_avg": 1.2,
        "weighted_form": 2.8, "n": 5, "source": "neutral_default"
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  N2-АГЕНТ — H2H история
# ═══════════════════════════════════════════════════════════════════════════════

def n2_agent_h2h(home: str, away: str, conn=None) -> dict:
    """
    N2-агент: собирает H2H статистику.
    Порядок: БД (кеш) → Claude web search.
    """
    if conn is None:
        conn = _init_db()

def n2_agent_h2h(home: str, away: str, conn=None) -> dict:
    """
    N2-агент: H2H история из matches_features (все встречи за 8 сезонов).
    """
    if conn is None:
        conn = _init_db()

    # Нормализуем имена
    from run_bot import TEAM_NORM
    db_home = TEAM_NORM.get(home, home)
    db_away = TEAM_NORM.get(away, away)

    key = f"{db_home}_vs_{db_away}"
    cached = _load_from_db(conn, key, "h2h")
    if cached:
        log.info(f"  ✅ N2 {home} vs {away}: H2H из кеша БД")
        return cached

    # Берём ВСЕ встречи из matches_features
    try:
        import sqlite3 as _sq
        import pandas as pd
        c2 = _sq.connect(DB_PATH)
        df = pd.read_sql("""
            SELECT date, home_team, away_team, y_result, y_home_goals, y_away_goals
            FROM matches_features
            WHERE (home_team=? AND away_team=?)
               OR (home_team=? AND away_team=?)
            ORDER BY date DESC LIMIT 15
        """, c2, params=(db_home, db_away, db_away, db_home))
        c2.close()

        if len(df) >= 2:
            home_wins = draws = away_wins = 0
            total_goals = []
            for _, row in df.iterrows():
                is_home = row["home_team"] == db_home
                ftr = row["y_result"]
                if (is_home and ftr == "H") or (not is_home and ftr == "A"):
                    home_wins += 1
                elif ftr == "D":
                    draws += 1
                else:
                    away_wins += 1
                total_goals.append(float(row["y_home_goals"]) + float(row["y_away_goals"]))

            n = len(df)
            result = {
                "h2h_n":        n,
                "h2h_home_wr":  round(home_wins / n, 3),
                "h2h_away_wr":  round(away_wins / n, 3),
                "h2h_draw_r":   round(draws / n, 3),
                "h2h_avg_goals":round(float(np.mean(total_goals)), 2),
                "source":       "matches_features",
            }
            _save_to_db(conn, key, "h2h", result, "matches_features")
            log.info(f"  ✅ N2 {home} vs {away}: H2H из БД ({n} встреч) "
                     f"→ {home_wins}W/{draws}D/{away_wins}L")
            return result
    except Exception as e:
        log.debug(f"N2 H2H DB error: {e}")

    # Дефолт
    log.info(f"  ⚠ N2 {home} vs {away}: нет H2H в БД, используем нейтральные")
    return {"h2h_n":5,"h2h_home_wr":0.4,"h2h_away_wr":0.3,"h2h_draw_r":0.3,
            "h2h_avg_goals":2.6,"source":"default"}


# ═══════════════════════════════════════════════════════════════════════════════
#  N4-АГЕНТ — Контекст (таблица, травмы, отдых)
# ═══════════════════════════════════════════════════════════════════════════════

def n4_agent_context(team: str, match_date: str, league_key: str = "soccer_epl",
                     conn=None) -> dict:
    """
    N4-агент: позиция в таблице — из api_standings (API-Football).
    Дни отдыха — из последнего матча в matches_features.
    """
    if conn is None:
        conn = _init_db()

    from run_bot import TEAM_NORM
    db_team = TEAM_NORM.get(team, team)

    cached = _load_from_db(conn, db_team, "context")
    if cached:
        from datetime import date
        try:
            last = cached.get("last_match_date", "")
            if last:
                ld = date.fromisoformat(last[:10])
                md = date.fromisoformat(match_date[:10])
                cached["days_rest"] = float(max(1, min(21, (md - ld).days)))
        except Exception:
            pass
        log.info(f"  ✅ N4 {team}: контекст из кеша (поз={cached.get('table_position',10)})")
        return cached

    table_position = 10
    days_rest = 7.0
    last_match_date = ""
    injured_count = 0

    try:
        import sqlite3 as _sq
        import pandas as pd
        from datetime import date

        c2 = _sq.connect(DB_PATH)

        # 1. Позиция из api_standings (API-Football) — самый точный источник
        # Используем полное API имя для поиска в таблицах
        _API_NAMES = {
            "Man City": "Manchester City", "Man United": "Manchester United",
            "Newcastle": "Newcastle United", "Wolves": "Wolverhampton",
            "Tottenham": "Tottenham", "Nott'm Forest": "Nottingham",
            "Ein Frankfurt": "Eintracht", "M'gladbach": "Borussia M",
            "Ath Madrid": "Atletico", "Ath Bilbao": "Athletic",
            "Paris SG": "Paris Saint",
        }
        _api_name = _API_NAMES.get(db_team, db_team)
        _search   = _api_name[:6].lower()
        api_pos = c2.execute("""
            SELECT rank FROM api_standings
            WHERE LOWER(team_name) LIKE ?
            ORDER BY fetched_at DESC LIMIT 1
        """, ("%" + _search + "%",)).fetchone()

        if api_pos:
            table_position = int(api_pos[0])
            log.info(f"  ✅ N4 {team}: позиция из API-Football = {table_position}")
        else:
            # Fallback: считаем из matches_raw
            season = "2025-2026"
            league_map = {
                "soccer_epl": "E0", "soccer_spain_la_liga": "SP1",
                "soccer_germany_bundesliga": "D1",
                "soccer_italy_serie_a": "I1", "soccer_france_ligue_one": "F1",
            }
            league_code = league_map.get(league_key, "E0")
            all_matches = pd.read_sql("""
                SELECT home_team, away_team, fthg, ftag, ftr
                FROM matches_raw
                WHERE season=? AND league=? AND ftr IS NOT NULL
            """, c2, params=(season, league_code))

            if len(all_matches) > 5:
                standings = {}
                for _, row in all_matches.iterrows():
                    h, a, ftr = row["home_team"], row["away_team"], row["ftr"]
                    if h not in standings: standings[h] = 0
                    if a not in standings: standings[a] = 0
                    if ftr == "H": standings[h] += 3
                    elif ftr == "A": standings[a] += 3
                    else: standings[h] += 1; standings[a] += 1
                sorted_teams = sorted(standings.items(), key=lambda x: x[1], reverse=True)
                for i, (t, _) in enumerate(sorted_teams, 1):
                    if t == db_team:
                        table_position = i
                        break

        # 2. Дата последнего матча из matches_features
        last_row = c2.execute("""
            SELECT date FROM matches_features
            WHERE home_team=? OR away_team=?
            ORDER BY date DESC LIMIT 1
        """, (db_team, db_team)).fetchone()

        if last_row:
            last_match_date = str(last_row[0])[:10]
            try:
                ld = date.fromisoformat(last_match_date)
                md = date.fromisoformat(match_date[:10])
                days_rest = float(max(1, min(21, (md - ld).days)))
            except Exception:
                days_rest = 7.0

        # 3. Количество травмированных из injuries (API-Football)
        inj = c2.execute("""
            SELECT COUNT(*) FROM injuries
            WHERE LOWER(team_name) LIKE ?
              AND fixture_date >= date('now', '-7 days')
        """, ("%" + _api_name[:6].lower() + "%",)).fetchone()
        if inj:
            injured_count = int(inj[0])

        c2.close()

    except Exception as e:
        log.debug(f"N4 context error for {team}: {e}")

    result = {
        "table_position":  table_position,
        "days_rest":       days_rest,
        "last_match_date": last_match_date,
        "injured_count":   injured_count,
        "home_advantage":  1,
        "source":          "api_football+matches_raw",
    }

    _save_to_db(conn, db_team, "context", result, "api_football")
    log.info(f"  ✅ N4 {team}: поз={table_position}, отдых={days_rest:.0f}д, травм={injured_count}")
    return result

    try:
        import sqlite3 as _sq
        import pandas as pd
        from datetime import date

        c2 = _sq.connect(DB_PATH)

        # Текущий сезон
        season = "2025-2026"

        # Все матчи команды в текущем сезоне
        df = pd.read_sql("""
            SELECT date, home_team, away_team, fthg, ftag, ftr
            FROM matches_raw
            WHERE season=? AND ftr IS NOT NULL
              AND (home_team=? OR away_team=?)
            ORDER BY date DESC
        """, c2, params=(season, db_team, db_team))

        if not df.empty:
            # Дата последнего матча
            last_match_date = str(df.iloc[0]["date"])[:10]
            try:
                ld = date.fromisoformat(last_match_date)
                md = date.fromisoformat(match_date[:10])
                days_rest = float(max(1, min(21, (md - ld).days)))
            except Exception:
                days_rest = 7.0

        # Считаем таблицу — все команды в лиге текущего сезона
        league_map = {
            "soccer_epl":               "E0",
            "soccer_spain_la_liga":     "SP1",
            "soccer_germany_bundesliga":"D1",
            "soccer_italy_serie_a":     "I1",
            "soccer_france_ligue_one":  "F1",
        }
        league_code = league_map.get(league_key, "E0")

        all_matches = pd.read_sql("""
            SELECT home_team, away_team, fthg, ftag, ftr
            FROM matches_raw
            WHERE season=? AND league=? AND ftr IS NOT NULL
        """, c2, params=(season, league_code))
        c2.close()

        if len(all_matches) > 5:
            # Считаем очки для каждой команды
            standings = {}
            for _, row in all_matches.iterrows():
                h, a = row["home_team"], row["away_team"]
                ftr = row["ftr"]
                if h not in standings: standings[h] = 0
                if a not in standings: standings[a] = 0
                if ftr == "H":
                    standings[h] += 3
                elif ftr == "A":
                    standings[a] += 3
                else:
                    standings[h] += 1
                    standings[a] += 1

            # Сортируем и находим позицию нашей команды
            sorted_teams = sorted(standings.items(), key=lambda x: x[1], reverse=True)
            for i, (t, pts) in enumerate(sorted_teams, 1):
                if t == db_team:
                    table_position = i
                    break

    except Exception as e:
        log.debug(f"N4 context DB error for {team}: {e}")

    result = {
        "table_position":  table_position,
        "days_rest":       days_rest,
        "last_match_date": last_match_date,
        "injured_count":   0,
        "home_advantage":  1,
        "source":          "matches_raw",
    }

    _save_to_db(conn, db_team, "context", result, "matches_raw")
    log.info(f"  ✅ N4 {team}: поз={table_position}, отдых={days_rest}д (из БД)")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  ГЛАВНАЯ ФУНКЦИЯ — заменяет collect_match_features из live_collector
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
#  N5-АГЕНТ НОВОСТЕЙ — травмы, дисквалификации, скандалы, форс-мажор
# ═══════════════════════════════════════════════════════════════════════════════

_news_cache: dict = {}   # "team" → news dict, TTL 12 часов


def _news_cache_valid(team: str) -> bool:
    if team not in _news_cache:
        return False
    age_h = (datetime.now(timezone.utc) - _news_cache.get(team, {}).get("_ts", datetime.now(timezone.utc))).total_seconds() / 3600
    return age_h < 12


def n5_agent_news(team: str, match_date: str) -> dict:
    """
    Агент новостей: сначала берёт реальные травмы из injuries (API-Football),
    затем дополняет через Claude web search если нет данных.
    """
    # Кеш включает дату матча чтобы разные матчи не мешали друг другу
    cache_key = team + "_" + match_date
    if cache_key in _news_cache:
        log.info(f"  📰 Новости {team}: из кеша")
        return _news_cache[cache_key]

    try:
        conn = _init_db()
        cached = _load_from_db(conn, team, "news")
        conn.close()
        if cached:
            cached["_ts"] = datetime.now(timezone.utc)
            _news_cache[cache_key] = cached
            log.info(f"  📰 Новости {team}: из кеша БД")
            return cached
    except Exception:
        pass

    from run_bot import TEAM_NORM
    db_team = TEAM_NORM.get(team, team)

    # Обратный маппинг: краткое имя -> полное для поиска в API таблицах
    API_TEAM_NAMES = {
        "Man City":    "Manchester City",
        "Man United":  "Manchester United",
        "Newcastle":   "Newcastle United",
        "Wolves":      "Wolverhampton",
        "Spurs":       "Tottenham",
        "Tottenham":   "Tottenham",
        "Nott'm Forest": "Nottingham",
        "Ein Frankfurt": "Eintracht Frankfurt",
        "M'gladbach":  "Borussia M",
        "Ath Madrid":  "Atletico",
        "Ath Bilbao":  "Athletic",
        "Paris SG":    "Paris Saint",
        "Paris FC":    "Paris FC",
    }
    # Используем полное имя для поиска в API таблицах
    api_team = API_TEAM_NAMES.get(db_team, db_team)
    # Берём первые 6 символов для LIKE поиска
    search_term = api_team[:6].lower()

    # 1. Реальные травмы из injuries таблицы (API-Football)
    real_injuries = []
    injury_penalty = 0.0
    try:
        import sqlite3 as _sq
        c2 = _sq.connect(DB_PATH)
        rows = c2.execute("""
            SELECT player_name, injury_type, reason FROM injuries
            WHERE LOWER(team_name) LIKE ?
              AND fixture_date >= date('now', '-14 days')
            ORDER BY fixture_date DESC LIMIT 10
        """, ("%" + search_term + "%",)).fetchall()
        c2.close()

        # Убираем дубли по имени игрока
        seen = set()
        for player_name, injury_type, reason in rows:
            if player_name and player_name not in seen:
                seen.add(player_name)
                itype   = (injury_type or "").strip()
                ireason = (reason or "").strip()
                # Словарь перевода причин травм
                INJURY_RU = {
                    # Типы
                    "suspended":          "дисквалификация",
                    "missing":            "отсутствует",
                    "questionable":       "под вопросом",
                    "injured":            "травма",
                    # Части тела и типы травм
                    "knee injury":        "травма колена",
                    "knee":               "травма колена",
                    "hamstring injury":   "травма бедра",
                    "hamstring":          "травма бедра",
                    "muscle injury":      "мышечная травма",
                    "muscle":             "мышечная травма",
                    "back injury":        "травма спины",
                    "back":               "травма спины",
                    "ankle injury":       "травма лодыжки",
                    "ankle":              "травма лодыжки",
                    "foot injury":        "травма стопы",
                    "foot":               "травма стопы",
                    "thigh injury":       "травма бедра",
                    "thigh":              "травма бедра",
                    "calf injury":        "травма икры",
                    "calf":               "травма икры",
                    "groin injury":       "паховая травма",
                    "groin":              "паховая травма",
                    "shoulder injury":    "травма плеча",
                    "shoulder":           "травма плеча",
                    "arm injury":         "травма руки",
                    "arm":                "травма руки",
                    "head injury":        "травма головы",
                    "head":               "травма головы",
                    "hip injury":         "травма бедра",
                    "hip":                "травма тазобедренного сустава",
                    "rib injury":         "травма ребра",
                    "rib":                "травма ребра",
                    "leg injury":         "травма ноги",
                    "leg":                "травма ноги",
                    "achilles":           "травма ахилла",
                    "achilles injury":    "травма ахилла",
                    "wrist":              "травма запястья",
                    "wrist injury":       "травма запястья",
                    "concussion":         "сотрясение мозга",
                    "hernia":             "грыжа",
                    "illness":            "болезнь",
                    "sick":               "болезнь",
                    "flu":                "грипп",
                    "covid":              "COVID-19",
                    "knock":              "ушиб",
                    "strain":             "растяжение",
                    "sprain":             "растяжение",
                    "fracture":           "перелом",
                    "broken":             "перелом",
                    "surgery":            "операция",
                    "red card":           "красная карточка",
                    "yellow card":        "жёлтые карточки",
                    "personal reasons":   "личные причины",
                    "international duty": "вызов в сборную",
                    "fatigue":            "усталость",
                    "fitness":            "не в форме",
                    "not match fit":      "не в форме",
                    "precaution":         "профилактика",
                    "inactive":           "вне заявки",
                    "not in squad":       "вне заявки",
                    "out":                "травма",
                    "doubtful":           "под вопросом",
                    "day to day":         "под наблюдением",
                    "left foot":          "травма левой ноги",
                    "right foot":         "травма правой ноги",
                    "hand":               "травма руки",
                    "hand injury":        "травма руки",
                    "knee":               "травма колена",
                    "eye":                "травма глаза",
                    "nose":               "травма носа",
                    "toe":                "травма пальца",
                    "pelvis":             "травма таза",
                    "cartilage":          "травма хряща",
                    "ligament":           "разрыв связок",
                    "meniscus":           "травма мениска",
                    "tendon":             "травма сухожилия",
                }

                def translate_injury(text):
                    if not text:
                        return "травма"
                    key = text.lower().strip()
                    # Точное совпадение
                    if key in INJURY_RU:
                        return INJURY_RU[key]
                    # Частичное совпадение (ищем подстроку)
                    for eng, rus in INJURY_RU.items():
                        if eng in key:
                            return rus
                    # Если слово заканчивается на "injury" — общая травма
                    if key.endswith("injury") or key == "injury":
                        # Пробуем извлечь часть тела
                        part = key.replace("injury", "").strip()
                        if part in INJURY_RU:
                            return INJURY_RU[part]
                        if part:
                            return "травма (" + part + ")"
                        return "травма"
                    # Любое слово содержащее "injur"
                    if "injur" in key:
                        return "травма"
                    # Карточки
                    if "card" in key or "ban" in key or "suspen" in key:
                        return "дисквалификация"
                    # Если ничего не нашли — оставляем но делаем первую букву маленькой
                    return text[0].lower() + text[1:] if text else "травма"

                if itype.lower() == "suspended" or ireason.lower() == "red card":
                    label = "дисквалификация"
                elif itype.lower() in ("missing", "questionable"):
                    label = translate_injury(ireason) if ireason else "под вопросом"
                elif ireason:
                    label = translate_injury(ireason)
                elif itype:
                    label = translate_injury(itype)
                else:
                    label = "травма"
                real_injuries.append(player_name + " (" + label + ")")

        if real_injuries:
            injury_penalty = -min(0.20, len(real_injuries) * 0.05)
            log.info(f"  🏥 {team}: {len(real_injuries)} травм из API-Football → штраф {injury_penalty:+.2f}")
    except Exception as e:
        log.debug(f"injuries DB: {e}")

    # 2. Claude web search — ищем НОВОСТИ (не травмы, они уже из API)
    if not ANTHROPIC_API_KEY:
        result = {
            "injury_penalty": injury_penalty,
            "morale_factor":  0.0,
            "news_summary":   "",
            "key_absences":   real_injuries[:5],
            "source":         "api_football",
            "_ts":            datetime.now(timezone.utc),
        }
        _news_cache[cache_key] = result
        return result

    log.info(f"  🔍 Новости {team}: ищу свежие новости через Claude...")

    injuries_known = ", ".join(real_injuries[:3]) if real_injuries else "нет данных"

    prompt = f"""Search for the latest news specifically about {team} football club (NOT any other team) from the last 7 days before {match_date}.

We already know about injuries from official data: {injuries_known}
DO NOT repeat injury information. Focus ONLY on:
1. Manager statements, team motivation, pre-match mood
2. Key player returns from injury or suspension
3. Recent transfer news or squad changes
4. Internal conflicts, fan protests, financial issues
5. Winning/losing streak and team confidence
6. Any important context for the upcoming match

Write news_summary in RUSSIAN, max 1-2 sentences, only if something significant found.
Estimate morale_factor: -0.05 (bad news), 0.0 (neutral), +0.03 (very motivated)

Return ONLY JSON:
```json
{{
  "injury_penalty": 0.0,
  "morale_factor": 0.0,
  "news_summary": "Краткие новости на русском или пустая строка если ничего важного",
  "key_absences": ["Player1", "Player2"],
  "source": "web_search"
}}
```

If no significant news found, return:
```json
{{"injury_penalty": 0.0, "morale_factor": 0.0, "news_summary": "Нет значимых новостей", "key_absences": [], "source": "web_search"}}
```"""

    response = _claude_search(prompt, max_tokens=500)

    # Базовый результат — травмы из API + пустые новости
    base_result = {
        "injury_penalty": injury_penalty,
        "morale_factor":  0.0,
        "news_summary":   "",
        "key_absences":   real_injuries[:5],
        "source":         "api_football+claude",
        "_ts":            datetime.now(timezone.utc),
    }

    if response:
        parsed = _parse_json_from_text(response)
        if parsed and "morale_factor" in parsed:
            morale  = max(-0.10, min(0.05, float(parsed.get("morale_factor", 0))))
            # Штраф от Claude только если нет данных API
            extra_penalty = float(parsed.get("injury_penalty", 0)) if not real_injuries else 0.0
            summary = (parsed.get("news_summary") or "").strip()

            # Объединяем: травмы из API + новости от Claude
            base_result["morale_factor"]  = morale
            base_result["injury_penalty"] = injury_penalty + extra_penalty
            if summary and summary not in ("Нет данных", "нет данных", ""):
                base_result["news_summary"] = summary
            base_result["source"] = "api_football+claude"

            log.info(f"  📰 Новости {team}: {summary[:80] if summary else 'нет новостей'} "
                     f"(настрой={morale:+.2f})")

    # Сохраняем в БД
    try:
        import json as _j
        conn = _init_db()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute("""
            INSERT OR REPLACE INTO live_stats
            (updated_at, team, stat_type, season, data_json, source)
            VALUES (?,?,?,?,?,?)
        """, (now_str, team, "news", "2025-2026",
              _j.dumps(base_result, default=str), "api_football+claude"))
        conn.commit(); conn.close()
    except Exception:
        pass

    _news_cache[cache_key] = base_result
    return base_result


def apply_news_adjustment(base_prob: float, team_news: dict,
                           is_home: bool) -> float:
    """
    Применяет поправку новостей к базовой вероятности.
    base_prob: исходная вероятность победы команды (0..1)
    team_news: результат n5_agent_news()
    is_home:   True если команда — хозяева
    """
    penalty = float(team_news.get("injury_penalty", 0.0))
    morale  = float(team_news.get("morale_factor",  0.0))

    # Домашнее поле немного смягчает негативный эффект
    if is_home:
        penalty *= 0.85

    adjusted = base_prob + penalty + morale
    # Не уходим за пределы разумного
    return max(0.05, min(0.95, adjusted))


# ═══════════════════════════════════════════════════════════════════════════════
#  ГЛАВНАЯ ФУНКЦИЯ — заменяет collect_match_features из live_collector
# ═══════════════════════════════════════════════════════════════════════════════

def collect_features_with_agents(home: str, away: str, match_date: str,
                                  odds: dict, league_key: str = "soccer_epl") -> dict:
    """
    Собирает признаки для матча используя агентов N1–N5.
    Каждый агент сам находит данные если их нет.
    """
    conn = _init_db()

    log.info(f"  🤖 Агенты N1–N4: {home} vs {away}")

    # N1 — форма обеих команд
    hf  = n1_agent_form(home, conn)
    af  = n1_agent_form(away, conn)

    # N2 — H2H
    h2h = n2_agent_h2h(home, away, conn)

    # N4 — контекст
    hc  = n4_agent_context(home, match_date, league_key, conn)
    ac  = n4_agent_context(away, match_date, league_key, conn)

    # N5 новости — НЕ вызываем здесь, вызывается отдельно только для матчей 60%+
    hn = {}
    an = {}

    conn.close()
    time.sleep(0.1)

    def v(d, k, default=np.nan):
        val = d.get(k, default)
        try:
            return float(val) if val is not None else default
        except (TypeError, ValueError):
            return default

    return {
        # N1 — форма
        "home_pts_avg":       v(hf,"pts_avg"),
        "home_win_rate":      v(hf,"win_rate"),
        "home_loss_rate":     v(hf,"loss_rate"),
        "home_gf_avg":        v(hf,"gf_avg"),
        "home_ga_avg":        v(hf,"ga_avg"),
        "home_gd_avg":        v(hf,"gd_avg"),
        "home_sot_avg":       v(hf,"sot_avg", 1.2),
        "home_weighted_form": v(hf,"weighted_form"),
        "away_pts_avg":       v(af,"pts_avg"),
        "away_win_rate":      v(af,"win_rate"),
        "away_loss_rate":     v(af,"loss_rate"),
        "away_gf_avg":        v(af,"gf_avg"),
        "away_ga_avg":        v(af,"ga_avg"),
        "away_gd_avg":        v(af,"gd_avg"),
        "away_sot_avg":       v(af,"sot_avg", 1.2),
        "away_weighted_form": v(af,"weighted_form"),
        "pts_diff":    v(hf,"pts_avg",0)       - v(af,"pts_avg",0),
        "gd_diff":     v(hf,"gd_avg",0)        - v(af,"gd_avg",0),
        "form_diff":   v(hf,"weighted_form",0) - v(af,"weighted_form",0),
        # N2 — H2H
        "h2h_n":         v(h2h,"h2h_n",5),
        "h2h_home_wr":   v(h2h,"h2h_home_wr",0.4),
        "h2h_away_wr":   v(h2h,"h2h_away_wr",0.3),
        "h2h_draw_r":    v(h2h,"h2h_draw_r",0.3),
        "h2h_avg_goals": v(h2h,"h2h_avg_goals",2.6),
        # N3 — котировки
        "odds_h": odds.get("odds_h", np.nan),
        "odds_d": odds.get("odds_d", np.nan),
        "odds_a": odds.get("odds_a", np.nan),
        # N4 — контекст
        "home_advantage":  1,
        "home_days_rest":  v(hc,"days_rest",7),
        "away_days_rest":  v(ac,"days_rest",7),
        "home_table_pos":  v(hc,"table_position",10),
        "away_table_pos":  v(ac,"table_position",10),
        # N5 — новости (передаём как мета для корректировки в run_bot)
        "_news_home": hn,
        "_news_away": an,
        # Мета
        "_sources": {
            "home_form": hf.get("source","?"),
            "away_form": af.get("source","?"),
            "h2h":       h2h.get("source","?"),
            "context_h": hc.get("source","?"),
            "news_h":    hn.get("source","?"),
            "news_a":    an.get("source","?"),
        }
    }


def set_api_key(key: str):
    """Устанавливает Anthropic API ключ."""
    global ANTHROPIC_API_KEY
    ANTHROPIC_API_KEY = key
