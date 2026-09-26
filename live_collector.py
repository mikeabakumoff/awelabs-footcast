import time
import logging
import requests
import numpy as np
import pandas as pd
import sqlite3
import re
from pathlib import Path
from datetime import datetime, date
from difflib import get_close_matches

log = logging.getLogger(__name__)
DB_PATH = Path("data/epl_target_teams.db")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://www.espn.com",
    "Referer": "https://www.espn.com/soccer/",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


ESPN_LEAGUES = ["eng.1", "esp.1", "ger.1", "ita.1", "fra.1"]


_team_id_cache: dict = {}
_form_cache: dict    = {}
_ctx_cache: dict     = {}
_scorers_cache: dict = {}


_KNOWN_IDS = {

    "Man City": "382",       "Arsenal": "359",       "Liverpool": "364",
    "Crystal Palace": "384", "Man United": "360",    "Brentford": "397",
    "Chelsea": "363",        "Tottenham": "367",     "Newcastle United": "361",
    "Aston Villa": "1",      "Everton": "368",       "Fulham": "370",
    "Brighton and Hove Albion": "331", "West Ham United": "371",
    "Bournemouth": "349",    "Wolverhampton Wanderers": "380",
    "Nottingham Forest": "374", "Leeds United": "357", "Burnley": "349",
    "Sunderland": "376",

    "Real Madrid": "86",     "Barcelona": "83",      "Atlético Madrid": "1068",
    "Athletic Bilbao": "93", "Real Sociedad": "116", "Villarreal": "102",
    "Real Betis": "95",      "Sevilla": "96",        "Valencia": "100",
    "Girona": "9812",        "Celta Vigo": "87",     "CA Osasuna": "112",
    "Mallorca": "97",        "Rayo Vallecano": "91", "Espanyol": "88",
    "Getafe": "9906",        "Levante": "94",        "Alavés": "9913",
    "Oviedo": "108",         "Elche CF": "9801",

    "Bayern Munich": "132",  "Dortmund": "124",      "Bayer Leverkusen": "168",
    "RB Leipzig": "23826",   "Eintracht Frankfurt": "9823", "VfB Stuttgart": "149",
    "SC Freiburg": "143",    "Werder Bremen": "167", "Borussia Monchengladbach": "123",
    "VfL Wolfsburg": "166",  "Augsburg": "16788",    "Union Berlin": "24776",
    "TSG Hoffenheim": "9797","FSV Mainz 05": "162",  "FC St. Pauli": "144",
    "1. FC Köln": "161",     "Hamburger SV": "145",  "1. FC Heidenheim": "30989",

    "Inter Milan": "110",    "AC Milan": "103",      "Juventus": "109",
    "Napoli": "114",         "Roma": "104",          "Lazio": "111",
    "Atalanta BC": "105",    "Fiorentina": "107",    "Bologna": "106",
    "Torino": "118",         "Udinese": "119",       "Genoa": "108",
    "Hellas Verona": "120",  "Lecce": "9807",        "Cagliari": "9816",
    "Sassuolo": "9813",      "Como": "9804",         "Parma": "115",
    "Cremonese": "9808",     "Pisa": "9825",         "AS Roma": "104",

    "PSG": "160",            "Paris Saint Germain": "160", "Marseille": "516",
    "Lyon": "518",           "AS Monaco": "160",    "Lille": "521",
    "Nice": "524",           "Lens": "522",         "RC Lens": "522",
    "Rennes": "525",         "Strasbourg": "527",   "Nantes": "523",
    "Montpellier": "519",    "Reims": "526",        "Brest": "9829",
    "Toulouse": "528",       "Le Havre": "9838",    "Angers": "9809",
    "Metz": "9819",          "Paris FC": "9849",    "Auxerre": "9803",
    "Lorient": "9817",
}


def _get(url, params=None, source=""):
    try:
        r = requests.get(url, params=params, headers=HEADERS,
                         timeout=15, allow_redirects=True)
        if r.status_code == 200:
            return r.json()
        log.debug(f"{source} HTTP {r.status_code}: {url[:80]}")
        return None
    except Exception as e:
        log.debug(f"{source} error: {e}")
        return None


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def _search_espn_id(team_name: str) -> str | None:
    norm_target = _normalize(team_name)

    for league in ESPN_LEAGUES:
        data = _get(
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/teams",
            source=f"ESPN teams {league}"
        )
        if not data:
            continue
        try:
            teams = data.get("sports",[{}])[0].get("leagues",[{}])[0].get("teams",[])
            names_ids = [(t["team"]["displayName"], t["team"]["id"]) for t in teams]


            for dname, tid in names_ids:
                if _normalize(dname) == norm_target:
                    return str(tid)


            all_names = [n for n, _ in names_ids]
            matches = get_close_matches(team_name, all_names, n=1, cutoff=0.7)
            if matches:
                tid = next(tid for n, tid in names_ids if n == matches[0])
                log.debug(f"ESPN fuzzy: '{team_name}' → '{matches[0]}' id={tid}")
                return str(tid)
        except Exception:
            continue

    return None


def get_espn_id(team: str) -> str | None:
    if team in _KNOWN_IDS:
        return _KNOWN_IDS[team]
    if team in _team_id_cache:
        return _team_id_cache[team]

    tid = _search_espn_id(team)
    _team_id_cache[team] = tid
    if tid:
        _KNOWN_IDS[team] = tid
        log.debug(f"ESPN ID найден: '{team}' → {tid}")
    return tid


def _form_from_espn(team: str) -> dict:
    tid = get_espn_id(team)
    if not tid:
        return {}

    data = _get(
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams/{tid}/schedule",
        source="ESPN schedule"
    )
    if not data:
        return {}

    try:
        events = data.get("events", [])


        def is_completed(ev):
            comp = ev.get("competitions",[{}])[0]
            st   = comp.get("status",{})

            if st.get("type",{}).get("completed"):
                return True

            if st.get("type",{}).get("state","") == "post":
                return True

            if "FULL_TIME" in st.get("type",{}).get("name","").upper():
                return True
            return False

        done = [e for e in events if is_completed(e)]


        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        recent_done = []
        for ev in done:
            ev_date_str = ev.get("date","")[:10]
            try:
                ev_date = datetime.fromisoformat(ev_date_str + "T00:00:00+00:00")
                if (now - ev_date).days <= 365:
                    recent_done.append(ev)
            except Exception:
                recent_done.append(ev)

        recent = recent_done[-5:]
        if not recent:
            return {}

        pts, gf, ga = [], [], []
        for ev in recent:
            comp  = ev.get("competitions",[{}])[0]
            comps = comp.get("competitors", [])


            mine = None
            opp  = None
            for c in comps:
                c_id = str(c.get("id",""))
                if c_id == tid:
                    mine = c
                else:
                    opp = c


            if not mine and len(comps) == 2:

                mine, opp = comps[0], comps[1]

            if not mine or not opp:
                continue

            try:
                f = int(float(mine.get("score", 0)))
                c = int(float(opp.get("score",  0)))
            except Exception:
                continue

            gf.append(f)
            ga.append(c)
            pts.append(3 if mine.get("winner") else (1 if f == c else 0))

        n = len(pts)
        if n == 0:
            return {}

        wf = sum(p*(0.75**(n-1-i)) for i, p in enumerate(pts))
        return {
            "n": n,
            "pts_avg":       round(np.mean(pts), 3),
            "win_rate":      round(sum(1 for p in pts if p==3)/n, 3),
            "draw_rate":     round(sum(1 for p in pts if p==1)/n, 3),
            "loss_rate":     round(sum(1 for p in pts if p==0)/n, 3),
            "gf_avg":        round(np.mean(gf), 3),
            "ga_avg":        round(np.mean(ga), 3),
            "gd_avg":        round(np.mean(gf)-np.mean(ga), 3),
            "sot_avg":       1.2,
            "weighted_form": round(wf, 3),
            "source":        "ESPN",
        }
    except Exception as e:
        log.debug(f"ESPN schedule parse: {e}")
        return {}


def _form_from_db(team: str) -> dict:
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql("""
            SELECT date, home_team, away_team,
                   y_result, y_home_goals, y_away_goals
            FROM matches_features
            WHERE (home_team=? OR away_team=?)
              AND y_result IN ('H','D','A')
            ORDER BY date DESC LIMIT 6
        """, conn, params=(team, team))
        conn.close()

        if df.empty:
            return {}

        pts, gf, ga = [], [], []
        for _, row in df.iterrows():
            is_home = row["home_team"] == team
            ftr  = row["y_result"]
            gf_v = float(row["y_home_goals"] if is_home else row["y_away_goals"])
            ga_v = float(row["y_away_goals"] if is_home else row["y_home_goals"])
            p = (3 if (is_home and ftr=="H") or (not is_home and ftr=="A")
                 else 1 if ftr=="D" else 0)
            pts.append(p); gf.append(gf_v); ga.append(ga_v)

        n = len(pts)
        if n == 0:
            return {}

        wf = sum(p * (0.75 ** (n-1-i)) for i, p in enumerate(pts))
        last_date = str(df.iloc[0]["date"])[:10]

        return {
            "pts_avg":         round(float(np.mean(pts)), 3),
            "win_rate":        round(sum(1 for p in pts if p==3) / n, 3),
            "draw_rate":       round(sum(1 for p in pts if p==1) / n, 3),
            "loss_rate":       round(sum(1 for p in pts if p==0) / n, 3),
            "gf_avg":          round(float(np.mean(gf)), 3),
            "ga_avg":          round(float(np.mean(ga)), 3),
            "gd_avg":          round(float(np.mean(gf)) - float(np.mean(ga)), 3),
            "sot_avg":         1.2,
            "weighted_form":   round(wf, 3),
            "last_match_date": last_date,
            "n":               n,
            "source":          "DB",
        }
    except Exception as e:
        log.debug(f"_form_from_db {team}: {e}")
        return {}


def get_team_form_live(team: str) -> dict:
    key = f"form_{team}"
    if key in _form_cache:
        return _form_cache[key]


    form = _form_from_db(team)
    if form:
        log.info(f"  ✅ N1 {team}: форма из БД "
                 f"(pts={form['pts_avg']}, gd={form['gd_avg']})")
        _form_cache[key] = form
        return form


    log.warning(f"  ❗ N1 {team}: нет в БД, пробую ESPN...")
    form = _form_from_espn(team)
    if form:
        log.info(f"  ✅ N1 {team}: форма из ESPN")
        _form_cache[key] = form
        return form

    log.warning(f"  ❗ N1 {team}: нет данных нигде, нейтральные значения")
    form = {
        "pts_avg": 1.2, "win_rate": 0.33, "draw_rate": 0.25, "loss_rate": 0.42,
        "gf_avg": 1.3, "ga_avg": 1.3, "gd_avg": 0.0, "sot_avg": 1.2,
        "weighted_form": 2.8, "n": 5, "source": "neutral_default"
    }
    _form_cache[key] = form
    return form


ESPN_LEAGUE_SLUGS = {
    "soccer_epl":              "eng.1",
    "soccer_spain_la_liga":    "esp.1",
    "soccer_germany_bundesliga":"ger.1",
    "soccer_italy_serie_a":    "ita.1",
    "soccer_france_ligue_one": "fra.1",
}


_standings_cache: dict = {}


def _get_standings(league_slug: str) -> dict:
    if league_slug in _standings_cache:
        return _standings_cache[league_slug]

    data = _get(
        f"https://site.api.espn.com/apis/v2/sports/soccer/{league_slug}/standings",
        source=f"ESPN standings {league_slug}"
    )
    result = {}
    if data:
        try:
            entries = (data.get("standings",{}).get("entries",[]) or
                       data.get("children",[{}])[0].get("standings",{}).get("entries",[]))
            for i, e in enumerate(entries, 1):
                tid = str(e.get("team",{}).get("id",""))
                if tid:
                    result[tid] = i
        except Exception:
            pass

    _standings_cache[league_slug] = result
    return result


def get_context_live(team: str, match_date: str, league_key: str = "soccer_epl") -> dict:
    key = f"ctx_{team}"
    if key in _ctx_cache:
        return _ctx_cache[key]

    ctx = {"home_advantage": 1, "days_rest": 7.0,
           "injured_count": 0, "table_position": 10, "source": "default"}

    tid = get_espn_id(team)
    if tid:

        league_slug = ESPN_LEAGUE_SLUGS.get(league_key, "eng.1")
        standings = _get_standings(league_slug)
        if tid in standings:
            ctx["table_position"] = standings[tid]
            ctx["source"] = "ESPN"


        data = _get(
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams/{tid}/schedule",
            source="ESPN schedule ctx"
        )
        if data:
            try:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                events = data.get("events", [])

                def is_completed(ev):
                    st = ev.get("competitions",[{}])[0].get("status",{})
                    if st.get("type",{}).get("completed"):   return True
                    if st.get("type",{}).get("state","") == "post": return True
                    if "FULL_TIME" in st.get("type",{}).get("name","").upper(): return True
                    return False

                done = []
                for ev in events:
                    if not is_completed(ev):
                        continue
                    ev_date_str = ev.get("date","")[:10]
                    try:
                        ev_date = datetime.fromisoformat(ev_date_str + "T00:00:00+00:00")
                        if (now - ev_date).days <= 365:
                            done.append(ev)
                    except Exception:
                        done.append(ev)

                if done:
                    last_date_str = done[-1].get("date","")[:10]
                    if last_date_str:
                        last_d = date.fromisoformat(last_date_str)
                        match_d = date.fromisoformat(match_date)
                        raw_days = (match_d - last_d).days

                        ctx["days_rest"] = float(max(1, min(raw_days, 14)))
            except Exception:
                pass

    log.info(f"  ✅ N4 {team}: поз={ctx['table_position']}, "
             f"отдых={ctx['days_rest']}д")
    _ctx_cache[key] = ctx
    return ctx


def get_h2h_live(home: str, away: str) -> dict:
    key = f"h2h_{home}_{away}"
    if key in _form_cache:
        return _form_cache[key]
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql(f"""
            SELECT * FROM matches_features
            WHERE (home_team='{home}' AND away_team='{away}')
               OR (home_team='{away}' AND away_team='{home}')
            ORDER BY date DESC LIMIT 1
        """, conn)
        conn.close()
        if df.empty:
            r = {"h2h_n":5,"h2h_home_wr":0.5,"h2h_away_wr":0.3,
                 "h2h_draw_r":0.2,"h2h_avg_goals":2.7}
        else:
            row = df.iloc[0]
            r = {k: float(row[k]) for k in
                 ["h2h_n","h2h_home_wr","h2h_away_wr","h2h_draw_r","h2h_avg_goals"]}
        _form_cache[key] = r
        return r
    except Exception:
        return {"h2h_n":5,"h2h_home_wr":0.5,"h2h_away_wr":0.3,
                "h2h_draw_r":0.2,"h2h_avg_goals":2.7}


def collect_match_features(home, away, match_date, odds, league_key="soccer_epl"):
    log.info(f"  📡 Данные: {home} vs {away}")
    hf  = get_team_form_live(home)
    af  = get_team_form_live(away)
    h2h = get_h2h_live(home, away)
    hc  = get_context_live(home, match_date, league_key)
    ac  = get_context_live(away, match_date, league_key)
    time.sleep(0.1)

    def v(d, k): return float(d.get(k, np.nan))

    return {
        "home_pts_avg":       v(hf,"pts_avg"),
        "home_win_rate":      v(hf,"win_rate"),
        "home_loss_rate":     v(hf,"loss_rate"),
        "home_gf_avg":        v(hf,"gf_avg"),
        "home_ga_avg":        v(hf,"ga_avg"),
        "home_gd_avg":        v(hf,"gd_avg"),
        "home_sot_avg":       v(hf,"sot_avg"),
        "home_weighted_form": v(hf,"weighted_form"),
        "away_pts_avg":       v(af,"pts_avg"),
        "away_win_rate":      v(af,"win_rate"),
        "away_loss_rate":     v(af,"loss_rate"),
        "away_gf_avg":        v(af,"gf_avg"),
        "away_ga_avg":        v(af,"ga_avg"),
        "away_gd_avg":        v(af,"gd_avg"),
        "away_sot_avg":       v(af,"sot_avg"),
        "away_weighted_form": v(af,"weighted_form"),
        "pts_diff":    v(hf,"pts_avg")       - v(af,"pts_avg"),
        "gd_diff":     v(hf,"gd_avg")        - v(af,"gd_avg"),
        "form_diff":   v(hf,"weighted_form") - v(af,"weighted_form"),
        "h2h_n":         v(h2h,"h2h_n"),
        "h2h_home_wr":   v(h2h,"h2h_home_wr"),
        "h2h_away_wr":   v(h2h,"h2h_away_wr"),
        "h2h_draw_r":    v(h2h,"h2h_draw_r"),
        "h2h_avg_goals": v(h2h,"h2h_avg_goals"),
        "odds_h": odds.get("odds_h", np.nan),
        "odds_d": odds.get("odds_d", np.nan),
        "odds_a": odds.get("odds_a", np.nan),
        "home_advantage":  1,
        "home_days_rest":  v(hc,"days_rest"),
        "away_days_rest":  v(ac,"days_rest"),
        "home_table_pos":  v(hc,"table_position"),
        "away_table_pos":  v(ac,"table_position"),
        "_sources": {
            "home_form": hf.get("source","?"),
            "away_form": af.get("source","?"),
        }
    }


def clear_cache():
    _form_cache.clear()
    _ctx_cache.clear()
    _standings_cache.clear()
    _first_goal_cache.clear()


_first_goal_cache: dict = {}


def _fetch_match_events(event_id: str, league_slug: str = "eng.1") -> list:

    for slug in [league_slug, "eng.1", "esp.1", "ger.1", "ita.1", "fra.1"]:
        data = _get(
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary",
            params={"event": event_id},
            source=f"ESPN events {slug}"
        )
        if not data:
            continue
        try:
            goals = []
            scoring_plays = data.get("scoringPlays", [])
            for play in scoring_plays:

                if play.get("type", {}).get("text", "").lower() not in ("goal", "penalty"):
                    continue
                minute = play.get("clock", {}).get("displayValue", "")
                try:
                    min_int = int(str(minute).replace("'","").split("+")[0].strip())
                except Exception:
                    min_int = None

                scorer_name = play.get("athletesInvolved", [{}])[0].get("displayName","")
                team_id = str(play.get("team", {}).get("id",""))
                if scorer_name:
                    goals.append({
                        "scorer": scorer_name,
                        "minute": min_int,
                        "team_id": team_id,
                    })
            if goals:
                return goals
        except Exception:
            continue
    return []


_LEAGUE_IDS = {
    "soccer_epl":               39,
    "soccer_spain_la_liga":      140,
    "soccer_germany_bundesliga": 78,
    "soccer_italy_serie_a":      135,
    "soccer_france_ligue_one":   61,
}


def get_first_goal_analytics(team: str, league_key: str = "soccer_epl") -> dict:
    key = f"fg_{team}"
    if key in _first_goal_cache:
        return _first_goal_cache[key]

    try:
        from run_bot import TEAM_NORM
        db_team = TEAM_NORM.get(team, team)
        league_id = _LEAGUE_IDS.get(league_key, 39)
        conn_sc = sqlite3.connect(DB_PATH)


        row = conn_sc.execute("""
            SELECT player_name, total_goals, avg_minute, first_goal_rate
            FROM scorer_stats
            WHERE league_id=? AND season=?
              AND LOWER(team_name) LIKE ?
            ORDER BY total_goals DESC, first_goal_rate DESC
            LIMIT 1
        """, (league_id, 2025, "%" + db_team[:5].lower() + "%")).fetchone()

        if row:
            player_name, total_goals, avg_minute, first_goal_rate = row
            confidence = min(0.90, 0.40 + total_goals * 0.025 + first_goal_rate * 0.3)
            result = {
                "player":      player_name,
                "avg_min":     int(round(avg_minute)),
                "confidence":  round(confidence, 3),
                "freq":        round(first_goal_rate, 3),
                "goals":       total_goals,
                "is_our_team": True,
                "source":      "scorer_stats",
            }
            _first_goal_cache[key] = result
            log.info(f"  ✅ Первый гол {team}: {player_name} "
                     f"(ср.мин={int(avg_minute)}, голов={total_goals}, "
                     f"первый в {first_goal_rate*100:.0f}% матчей)")
            conn_sc.close()
            return result


        row2 = conn_sc.execute(
            "SELECT data_json FROM live_stats WHERE team=? AND stat_type='topscorers'",
            (f"topscorers_{league_id}",)
        ).fetchone()
        conn_sc.close()

        if row2:
            import json as _json
            scorers = _json.loads(row2[0])
            for sc in scorers:
                sc_team = (sc.get("team") or "").lower()
                if db_team[:5].lower() in sc_team or sc_team[:5] in db_team.lower():
                    goals = sc.get("goals", 0) or 0
                    if goals > 0:
                        result = {
                            "player":     sc["player_name"],
                            "avg_min":    28,
                            "confidence": min(0.85, 0.45 + goals * 0.02),
                            "freq":       min(0.85, 0.45 + goals * 0.02),
                            "goals":      goals,
                            "is_our_team": True,
                            "source":     "api_football_topscorers",
                        }
                        _first_goal_cache[key] = result
                        log.info(f"  ✅ Первый гол {team}: {result['player']} "
                                 f"({goals} голов) из топ-бомбардиров")
                        return result
    except Exception as e:
        log.debug(f"scorer_stats lookup error: {e}")


    try:
        conn_fg = sqlite3.connect(DB_PATH)
        conn_fg.execute("""
            CREATE TABLE IF NOT EXISTS live_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                updated_at TEXT, team TEXT, stat_type TEXT,
                season TEXT, data_json TEXT, source TEXT,
                UNIQUE(team, stat_type, season)
            )
        """)
        row = conn_fg.execute(
            "SELECT data_json, updated_at FROM live_stats WHERE team=? AND stat_type=?",
            (team, "first_goal")
        ).fetchone()
        if row:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            updated = _dt.fromisoformat(row[1].replace("Z","+00:00"))
            age_days = (_dt.now(_tz.utc) - updated).total_seconds() / 86400
            if age_days < 7:
                cached = _json.loads(row[0])
                conn_fg.close()
                _first_goal_cache[key] = cached
                log.info(f"  ✅ Первый гол {team}: {cached.get('player','?')} из кеша БД")
                return cached
        conn_fg.close()
    except Exception:
        pass


    from agent_collector import ANTHROPIC_API_KEY as _ak, _claude_search, _parse_json_from_text
    if not _ak:
        _first_goal_cache[key] = None
        return None

    log.info(f"  🔍 Первый гол {team}: ищу бомбардиров через Claude...")
    prompt = f"""Who are the top scorers for {team} in the 2025-26 season?
Find the player most likely to score the first goal in their next match.
Consider: goals scored, position (striker/forward preferred), recent form.

Return ONLY JSON:
```json
{{"player": "Player Name", "avg_min": 28, "confidence": 0.65, "goals_season": 12}}
```
If no clear candidate, return:
```json
{{"player": null, "avg_min": 35, "confidence": 0.3, "goals_season": 0}}
```"""

    response = _claude_search(prompt, max_tokens=300)
    result = None
    if response:
        parsed = _parse_json_from_text(response)
        if parsed and parsed.get("player") and float(parsed.get("confidence", 0)) >= 0.40:
            result = {
                "player":      parsed["player"],
                "avg_min":     int(parsed.get("avg_min", 30)),
                "confidence":  float(parsed.get("confidence", 0.5)),
                "freq":        float(parsed.get("confidence", 0.5)),
                "is_our_team": True,
            }

            try:
                import json as _json
                from datetime import datetime as _dt, timezone as _tz
                conn_fg = sqlite3.connect(DB_PATH)
                now_str = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                conn_fg.execute("""
                    INSERT OR REPLACE INTO live_stats
                    (updated_at, team, stat_type, season, data_json, source)
                    VALUES (?,?,?,?,?,?)
                """, (now_str, team, "first_goal", "2025-2026",
                      _json.dumps(result), "claude_web"))
                conn_fg.commit()
                conn_fg.close()
            except Exception:
                pass
            log.info(f"  ✅ Первый гол {team}: {result['player']} "
                     f"(уверенность={result['confidence']:.0%})")

    _first_goal_cache[key] = result
    return result


def get_first_goal_prediction(home: str, away: str,
                               lambda_h: float, lambda_a: float,
                               league_key: str = "soccer_epl") -> tuple:
    total = lambda_h + lambda_a
    p_home_first = lambda_h / total if total > 0 else 0.5
    p_away_first = lambda_a / total if total > 0 else 0.5

    fg_home = get_first_goal_analytics(home, league_key)
    fg_away = get_first_goal_analytics(away, league_key)

    candidates = []

    if fg_home and fg_home.get("is_our_team", True):
        conf = fg_home["confidence"] * p_home_first
        candidates.append((fg_home["player"], home,
                            fg_home["avg_min"], conf))

    if fg_away and fg_away.get("is_our_team", True):
        conf = fg_away["confidence"] * p_away_first
        candidates.append((fg_away["player"], away,
                            fg_away["avg_min"], conf))

    if not candidates:
        return None


    best = max(candidates, key=lambda x: x[3])
    player, team, minute, confidence = best
    confidence_pct = int(confidence * 100)


    if confidence_pct < 45:
        return None

    return player, team, minute, confidence_pct


def prefetch_all_scorers(teams: list):
    log.info("📊 Загружаю аналитику первого гола...")
    for team in set(teams):
        if f"fg_{team}" not in _first_goal_cache:
            get_first_goal_analytics(team)
            time.sleep(0.2)
