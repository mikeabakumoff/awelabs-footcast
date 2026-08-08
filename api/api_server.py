"""
api_server.py — Flask API для Telegram Web App
Запуск: python api_server.py
Порт: 5000
"""

import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request
from flask_cors import CORS

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

@app.after_request
def add_ngrok_header(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

DB_PATH = Path(__file__).parent.parent / "data" / "epl_target_teams.db"
BANGKOK = timedelta(hours=7)

LEAGUE_NAMES = {
    "soccer_epl":               "🏴󠁧󠁢󠁥󠁮󠁧󠁿 АПЛ",
    "soccer_spain_la_liga":     "🇪🇸 Ла Лига",
    "soccer_germany_bundesliga":"🇩🇪 Бундеслига",
    "soccer_italy_serie_a":     "🇮🇹 Серия А",
    "soccer_france_ligue_one":  "🇫🇷 Лига 1",
}

LEAGUE_ORDER = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
]

MIN_PROB = 0.65


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/api/matches")
def get_matches():
    """Возвращает матчи за текущий и следующий месяц."""
    now = datetime.now(timezone.utc) + BANGKOK
    month_param = request.args.get("month", "current")

    if month_param == "next":
        # Следующий месяц
        if now.month == 12:
            year, month = now.year + 1, 1
        else:
            year, month = now.year, now.month + 1
    else:
        year, month = now.year, now.month

    # Первый и последний день месяца
    first_day = f"{year}-{month:02d}-01"
    if month == 12:
        last_day = f"{year + 1}-01-01"
    else:
        last_day = f"{year}-{month + 1:02d}-01"

    conn = get_db()

    # Берём предстоящие матчи из sent_messages (прогнозы уже готовы)
    # Расширяем диапазон — берём прогнозы за ±7 дней от месяца
    from datetime import datetime as _dt, timedelta as _td
    ext_first = (_dt.strptime(first_day, "%Y-%m-%d") - _td(days=7)).strftime("%Y-%m-%d")
    ext_last  = (_dt.strptime(last_day,  "%Y-%m-%d") + _td(days=7)).strftime("%Y-%m-%d")

    sent_rows = conn.execute("""
        SELECT home_team, away_team, match_date,
               winner_name, winner_prob,
               score_h, score_a,
               fg_player, fg_team, fg_minute,
               real_score_h, real_score_a, prediction_ok
        FROM sent_messages
        WHERE match_date >= ? AND match_date < ?
        ORDER BY match_date ASC, home_team ASC
    """, (ext_first, ext_last)).fetchall()

    log.info(f"sent_messages за период {ext_first}..{ext_last}: {len(sent_rows)} записей")
    if sent_rows:
        log.info(f"Первые: {[(r['home_team'], r['away_team'], r['match_date']) for r in sent_rows[:3]]}")

    # Берём все матчи из upcoming_matches (включая те без прогноза)
    # Проверяем колонки таблицы
    cols = [r[1] for r in conn.execute("PRAGMA table_info(upcoming_matches)").fetchall()]
    date_col = "date" if "date" in cols else "match_date" if "match_date" in cols else cols[0]
    time_col = "time_utc" if "time_utc" in cols else "match_time" if "match_time" in cols else None
    league_col = "league_key" if "league_key" in cols else "league" if "league" in cols else None

    time_sel = time_col if time_col else "NULL"
    league_sel = league_col if league_col else "NULL"

    upcoming_rows = conn.execute(f"""
        SELECT home_team, away_team, {date_col} as date, {time_sel} as time_utc, {league_sel} as league_key
        FROM upcoming_matches
        WHERE {date_col} >= ? AND {date_col} < ?
        ORDER BY {date_col} ASC
    """, (first_day, last_day)).fetchall()

    # Травмы для команд
    injury_cache = {}

    def get_injuries(team):
        if team in injury_cache:
            return injury_cache[team]
        rows = conn.execute("""
            SELECT player_name, reason, injury_type FROM injuries
            WHERE LOWER(team_name) LIKE ?
              AND fixture_date >= date('now', '-21 days')
            ORDER BY fixture_date DESC LIMIT 5
        """, ("%" + team[:5].lower() + "%",)).fetchall()

        result = []
        INJURY_RU = {
            "knee injury": "травма колена", "hamstring injury": "травма бедра",
            "muscle injury": "мышечная травма", "back injury": "травма спины",
            "ankle injury": "травма лодыжки", "suspended": "дисквалификация",
            "red card": "красная карточка", "leg injury": "травма ноги",
            "thigh injury": "травма бедра", "calf injury": "травма икры",
            "foot injury": "травма стопы", "illness": "болезнь",
            "inactive": "вне заявки", "fitness": "не в форме",
        }
        seen = set()
        for r in rows:
            name = r["player_name"]
            if name in seen:
                continue
            seen.add(name)
            raw = (r["reason"] or r["injury_type"] or "травма").lower().strip()
            label = "травма"
            for eng, rus in INJURY_RU.items():
                if eng in raw:
                    label = rus
                    break
            result.append({"player": name, "type": label})

        injury_cache[team] = result
        return result

    # Строим индекс прогнозов — по полному ключу И по нормализованному (первые 5 букв)
    pred_index = {}
    pred_index_norm = {}  # нормализованный ключ для нечёткого поиска
    for row in sent_rows:
        key = f"{row['home_team']}|{row['away_team']}|{row['match_date']}"
        pred_index[key] = dict(row)
        # Нормализованный ключ
        norm = f"{row['home_team'][:5].lower()}|{row['away_team'][:5].lower()}|{row['match_date']}"
        pred_index_norm[norm] = dict(row)

    # Debug: log prediction keys
    log.info(f"Прогнозов в индексе: {len(pred_index)}, примеры: {list(pred_index.keys())[:3]}")

    # Строим итоговый список матчей
    matches_by_date = {}

    # Добавляем все upcoming матчи
    for row in upcoming_rows:
        date_str = row["date"]
        league_key = row["league_key"] or "soccer_epl"

        # Время Bangkok
        time_bkk = "—"
        if row["time_utc"]:
            try:
                t = datetime.fromisoformat(row["time_utc"].replace("Z", "+00:00"))
                t_bkk = t + BANGKOK
                time_bkk = t_bkk.strftime("%H:%M")
            except Exception:
                pass

        key = f"{row['home_team']}|{row['away_team']}|{date_str}"
        pred = pred_index.get(key)
        if not pred:
            # Пробуем нормализованный поиск (5 букв)
            norm = f"{row['home_team'][:5].lower()}|{row['away_team'][:5].lower()}|{date_str}"
            pred = pred_index_norm.get(norm)
        if not pred:
            # Пробуем 4 буквы
            norm4 = f"{row['home_team'][:4].lower()}|{row['away_team'][:4].lower()}|{date_str}"
            # Ищем в pred_index_norm по 4 буквам
            for k, v in pred_index_norm.items():
                parts = k.split("|")
                if (len(parts) == 3 and parts[2] == date_str and
                    parts[0][:4] == row["home_team"][:4].lower() and
                    parts[1][:4] == row["away_team"][:4].lower()):
                    pred = v
                    break

        match = {
            "id": key.replace("|", "_").replace(" ", "_"),
            "date": date_str,
            "time": time_bkk,
            "league_key": league_key,
            "league": LEAGUE_NAMES.get(league_key, "⚽"),
            "home": row["home_team"],
            "away": row["away_team"],
            "prob": None,
            "winner": None,
            "score_h": None,
            "score_a": None,
            "fg_player": None,
            "fg_team": None,
            "fg_minute": None,
            "injuries_home": [],
            "injuries_away": [],
            "result": None,
        }

        if pred:
            prob = pred["winner_prob"] or 0
            match["prob"] = prob
            match["winner"] = pred["winner_name"]
            match["score_h"] = pred["score_h"]
            match["score_a"] = pred["score_a"]
            match["fg_player"] = pred["fg_player"]
            match["fg_team"] = pred["fg_team"]
            match["fg_minute"] = pred["fg_minute"]

            # Полная аналитика только при 65%+
            if prob >= 65:
                match["injuries_home"] = get_injuries(row["home_team"])
                match["injuries_away"] = get_injuries(row["away_team"])

            # Реальный результат если матч уже сыгран
            if pred["real_score_h"] is not None:
                match["result"] = {
                    "score_h": pred["real_score_h"],
                    "score_a": pred["real_score_a"],
                    "ok": pred["prediction_ok"],
                }

        if date_str not in matches_by_date:
            matches_by_date[date_str] = []
        matches_by_date[date_str].append(match)

    # Дедупликация — убираем дубли (Leeds/Leeds United, Ein Frankfurt/Eintracht Frankfurt)
    for date_str, matches in matches_by_date.items():
        seen = {}  # norm_key -> index in deduped
        deduped = []
        for m in matches:
            norm_key = (m["home"][:4].lower().strip(), m["away"][:4].lower().strip())
            if norm_key not in seen:
                seen[norm_key] = len(deduped)
                deduped.append(m)
            else:
                # Если новая запись имеет прогноз а старая нет — заменяем
                existing_idx = seen[norm_key]
                if m["prob"] is not None and deduped[existing_idx]["prob"] is None:
                    deduped[existing_idx] = m
        matches_by_date[date_str] = deduped

    # Также добавляем матчи из sent_messages которых нет в upcoming
    for key, pred in pred_index.items():
        date_str = pred["match_date"]
        existing_keys = [
            f"{m['home']}|{m['away']}|{m['date']}"
            for m in matches_by_date.get(date_str, [])
        ]
        if key not in existing_keys:
            prob = pred["winner_prob"] or 0
            match = {
                "id": key.replace("|", "_").replace(" ", "_"),
                "date": date_str,
                "time": "—",
                "league_key": "soccer_epl",
                "league": "⚽",
                "home": pred["home_team"],
                "away": pred["away_team"],
                "prob": prob,
                "winner": pred["winner_name"],
                "score_h": pred["score_h"],
                "score_a": pred["score_a"],
                "fg_player": pred["fg_player"],
                "fg_team": pred["fg_team"],
                "fg_minute": pred["fg_minute"],
                "injuries_home": get_injuries(pred["home_team"]) if prob >= 65 else [],
                "injuries_away": get_injuries(pred["away_team"]) if prob >= 65 else [],
                "result": {
                    "score_h": pred["real_score_h"],
                    "score_a": pred["real_score_a"],
                    "ok": pred["prediction_ok"],
                } if pred["real_score_h"] is not None else None,
            }
            if date_str not in matches_by_date:
                matches_by_date[date_str] = []
            matches_by_date[date_str].append(match)

    # Сортируем матчи внутри дня по лиге и времени
    def sort_key(m):
        league_order = LEAGUE_ORDER.index(m["league_key"]) if m["league_key"] in LEAGUE_ORDER else 99
        return (league_order, m["time"])

    result = []
    for date_str in sorted(matches_by_date.keys()):
        matches = sorted(matches_by_date[date_str], key=sort_key)
        result.append({
            "date": date_str,
            "matches": matches,
        })

    conn.close()

    return jsonify({
        "month": month_param,
        "year": year,
        "month_num": month,
        "days": result,
    })


@app.route("/api/stats")
def get_stats():
    """Статистика точности прогнозов."""
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM sent_messages WHERE result_checked=1").fetchone()[0]
    correct = conn.execute("SELECT COUNT(*) FROM sent_messages WHERE prediction_ok=1").fetchone()[0]
    conn.close()
    accuracy = round(correct / total * 100, 1) if total > 0 else 0
    return jsonify({"total": total, "correct": correct, "accuracy": accuracy})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
