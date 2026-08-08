"""
build_features.py — Feature engineering без утечки данных
Вход:  matches_raw (SQLite)
Выход: matches_features — ~26 000 строк, ~35 признаков

Принцип: каждый матч видит ТОЛЬКО данные, известные ДО его начала.
Признаки считаются по скользящему окну из предыдущих матчей команды.
"""

import sqlite3
import logging
import numpy as np
import pandas as pd
from pathlib import Path

logging.basicConfig(format="%(asctime)s  %(levelname)s  %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

DB_PATH = Path("data/epl_target_teams.db")
WINDOW  = 6   # последние N матчей для расчёта формы

# UEFA коэффициенты встроены для быстрого доступа
UEFA_COEFFICIENTS = {
    "Real Madrid": 150.0, "Man City": 138.0, "Bayern Munich": 138.0,
    "Barcelona": 124.0, "Paris SG": 118.0, "Liverpool": 116.0,
    "Atletico": 113.0, "Ath Madrid": 113.0, "Arsenal": 108.0,
    "Chelsea": 104.0, "Juventus": 102.0, "Inter": 101.0,
    "Dortmund": 98.0, "Napoli": 95.0, "Tottenham": 92.0,
    "Leverkusen": 90.0, "RB Leipzig": 88.0, "Man United": 88.0,
    "Milan": 82.0, "Ein Frankfurt": 82.0, "Eintracht Frankfurt": 82.0,
    "Benfica": 85.0, "Porto": 83.0, "Roma": 70.0, "Atalanta": 72.0,
    "Lazio": 65.0, "Sevilla": 68.0, "Villarreal": 67.0,
    "Marseille": 64.0, "Lyon": 62.0, "Monaco": 60.0,
    "Newcastle": 55.0, "Aston Villa": 52.0, "Athletic Bilbao": 58.0,
    "Ath Bilbao": 58.0, "Real Sociedad": 55.0,
}

def get_uefa_coeff(team: str) -> float:
    if team in UEFA_COEFFICIENTS:
        return UEFA_COEFFICIENTS[team]
    tl = team.lower()
    for k, v in UEFA_COEFFICIENTS.items():
        if k.lower() in tl or tl in k.lower() or k.lower()[:5] == tl[:5]:
            return v
    return 30.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches_features (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    league          TEXT,
    season          TEXT,
    date            TEXT,
    home_team       TEXT,
    away_team       TEXT,

    -- Целевые переменные
    y_result        TEXT,      -- H/D/A
    y_home_goals    INTEGER,
    y_away_goals    INTEGER,

    -- N1: форма хозяев (последние WINDOW матчей)
    home_pts_avg        REAL,
    home_win_rate       REAL,
    home_draw_rate      REAL,
    home_loss_rate      REAL,
    home_gf_avg         REAL,
    home_ga_avg         REAL,
    home_gd_avg         REAL,
    home_sot_avg        REAL,
    home_weighted_form  REAL,
    home_games_played   INTEGER,

    -- N1: форма гостей
    away_pts_avg        REAL,
    away_win_rate       REAL,
    away_draw_rate      REAL,
    away_loss_rate      REAL,
    away_gf_avg         REAL,
    away_ga_avg         REAL,
    away_gd_avg         REAL,
    away_sot_avg        REAL,
    away_weighted_form  REAL,
    away_games_played   INTEGER,

    -- Разница форм
    pts_diff        REAL,
    gd_diff         REAL,
    form_diff       REAL,

    -- N2: H2H (только матчи этих двух команд до текущего)
    h2h_n           INTEGER,
    h2h_home_wr     REAL,
    h2h_away_wr     REAL,
    h2h_draw_r      REAL,
    h2h_avg_goals   REAL,

    -- N3: котировки
    odds_h          REAL,
    odds_d          REAL,
    odds_a          REAL,

    -- N4: домашнее преимущество
    home_advantage  INTEGER DEFAULT 1,

    -- xG (из Understat, если доступно)
    home_xg_avg     REAL,
    away_xg_avg     REAL,
    home_xg_diff    REAL,

    -- UEFA коэффициенты
    home_uefa_coeff     REAL,      -- UEFA club coefficient хозяев
    away_uefa_coeff     REAL,      -- UEFA club coefficient гостей
    coeff_diff          REAL,      -- разница коэффициентов

    -- N4+: мотивация и давление
    home_position       INTEGER,   -- место в таблице
    away_position       INTEGER,
    home_pts_total      INTEGER,   -- очков в сезоне всего
    away_pts_total      INTEGER,
    home_relegation_gap INTEGER,   -- очков до зоны вылета (отрицательное = уже в зоне)
    away_relegation_gap INTEGER,
    home_title_gap      INTEGER,   -- очков до лидера
    away_title_gap      INTEGER,
    home_rest_days      INTEGER,   -- дней отдыха перед матчем
    away_rest_days      INTEGER,
    home_home_win_rate  REAL,      -- процент побед именно дома
    away_away_win_rate  REAL,      -- процент побед именно в гостях

    UNIQUE(league, date, home_team, away_team)
)
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_mf_date ON matches_features(date)",
    "CREATE INDEX IF NOT EXISTS idx_mr_home ON matches_raw(home_team, date)",
    "CREATE INDEX IF NOT EXISTS idx_mr_away ON matches_raw(away_team, date)",
]


def load_raw(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("""
        SELECT * FROM matches_raw
        WHERE ftr IN ('H','D','A')
          AND fthg IS NOT NULL AND ftag IS NOT NULL
        ORDER BY date ASC
    """, conn)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    log.info(f"Загружено {len(df):,} матчей из matches_raw")
    return df


def team_form(team_matches: pd.DataFrame, before_idx: int, window: int) -> dict:
    """
    Считает форму команды по последним window матчам ДО индекса before_idx.
    team_matches — все матчи команды, отсортированные по дате.
    """
    past = team_matches[team_matches.index < before_idx].tail(window)
    n = len(past)

    if n == 0:
        return {
            "pts_avg": np.nan, "win_rate": np.nan, "draw_rate": np.nan,
            "loss_rate": np.nan, "gf_avg": np.nan, "ga_avg": np.nan,
            "gd_avg": np.nan, "sot_avg": np.nan, "weighted_form": np.nan,
            "games_played": 0,
        }

    pts_list, gf_list, ga_list, sot_list = [], [], [], []
    for _, row in past.iterrows():
        is_home = row["is_home"]
        gf = row["fthg"] if is_home else row["ftag"]
        gc = row["ftag"] if is_home else row["fthg"]
        sot = row["hst"] if is_home else row["ast"]
        result = row["ftr"]

        if is_home:
            pts = 3 if result == "H" else (1 if result == "D" else 0)
        else:
            pts = 3 if result == "A" else (1 if result == "D" else 0)

        pts_list.append(pts)
        gf_list.append(float(gf) if pd.notna(gf) else 1.2)
        ga_list.append(float(gc) if pd.notna(gc) else 1.2)
        sot_list.append(float(sot) if pd.notna(sot) else 1.2)

    wf = sum(p * (0.75 ** (n-1-i)) for i, p in enumerate(pts_list))

    return {
        "pts_avg":       round(np.mean(pts_list), 4),
        "win_rate":      round(sum(1 for p in pts_list if p == 3) / n, 4),
        "draw_rate":     round(sum(1 for p in pts_list if p == 1) / n, 4),
        "loss_rate":     round(sum(1 for p in pts_list if p == 0) / n, 4),
        "gf_avg":        round(np.mean(gf_list), 4),
        "ga_avg":        round(np.mean(ga_list), 4),
        "gd_avg":        round(np.mean(gf_list) - np.mean(ga_list), 4),
        "sot_avg":       round(np.mean(sot_list), 4),
        "weighted_form": round(wf, 4),
        "games_played":  n,
    }


def h2h_stats(df: pd.DataFrame, home: str, away: str, before_date) -> dict:
    """H2H статистика между двумя командами до before_date."""
    mask = (
        ((df["home_team"] == home) & (df["away_team"] == away)) |
        ((df["home_team"] == away) & (df["away_team"] == home))
    ) & (df["date"] < before_date)

    h2h = df[mask].tail(15)  # последние 15 встреч
    n = len(h2h)
    if n == 0:
        return {"h2h_n": 0, "h2h_home_wr": np.nan, "h2h_away_wr": np.nan,
                "h2h_draw_r": np.nan, "h2h_avg_goals": np.nan}

    hw = sum(1 for _, r in h2h.iterrows()
             if (r["home_team"] == home and r["ftr"] == "H") or
                (r["away_team"] == home and r["ftr"] == "A"))
    aw = sum(1 for _, r in h2h.iterrows()
             if (r["home_team"] == away and r["ftr"] == "H") or
                (r["away_team"] == away and r["ftr"] == "A"))
    dr = n - hw - aw
    goals = [(r["fthg"] + r["ftag"]) for _, r in h2h.iterrows()
             if pd.notna(r["fthg"]) and pd.notna(r["ftag"])]

    return {
        "h2h_n":         n,
        "h2h_home_wr":   round(hw / n, 4),
        "h2h_away_wr":   round(aw / n, 4),
        "h2h_draw_r":    round(dr / n, 4),
        "h2h_avg_goals": round(np.mean(goals), 4) if goals else np.nan,
    }


def avg_odds(row: pd.Series) -> tuple:
    """Средние котировки из нескольких букмекеров."""
    oh_vals = [row.get(c) for c in ["b365h","bwh","iwh"] if pd.notna(row.get(c))]
    od_vals = [row.get(c) for c in ["b365d","bwd","iwd"] if pd.notna(row.get(c))]
    oa_vals = [row.get(c) for c in ["b365a","bwa","iwa"] if pd.notna(row.get(c))]

    def prob(odds_list):
        if not odds_list:
            return np.nan
        raw = [1/o for o in odds_list if o and o > 1]
        if not raw:
            return np.nan
        total = sum(raw) + 0.001  # убираем маржу приближённо
        return round(np.mean(raw) / (total / len(raw)), 4)

    # Возвращаем вероятности без маржи
    oh_p = prob(oh_vals) if oh_vals else np.nan
    od_p = prob(od_vals) if od_vals else np.nan
    oa_p = prob(oa_vals) if oa_vals else np.nan

    # Нормализуем чтобы сумма = 1
    total = sum(x for x in [oh_p, od_p, oa_p] if not np.isnan(x))
    if total > 0:
        oh_p = round(oh_p / total, 4) if not np.isnan(oh_p) else np.nan
        od_p = round(od_p / total, 4) if not np.isnan(od_p) else np.nan
        oa_p = round(oa_p / total, 4) if not np.isnan(oa_p) else np.nan

    return oh_p, od_p, oa_p


def build_team_index(df: pd.DataFrame) -> dict:
    """
    Строит индекс: team → DataFrame со всеми матчами команды
    с флагом is_home и оригинальными индексами.
    """
    teams = set(df["home_team"]) | set(df["away_team"])
    index = {}
    for team in teams:
        home_mask = df["home_team"] == team
        away_mask = df["away_team"] == team
        home_df = df[home_mask].copy(); home_df["is_home"] = True
        away_df = df[away_mask].copy(); away_df["is_home"] = False
        combined = pd.concat([home_df, away_df]).sort_values("date")
        index[team] = combined
    return index


def build_features(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Строит признаки для каждого матча без утечки данных."""
    team_idx = build_team_index(df)
    saved = 0

    for i, (idx, row) in enumerate(df.iterrows()):
        if i % 500 == 0:
            log.info(f"  {i:,} / {len(df):,} матчей...")

        home = row["home_team"]
        away = row["away_team"]
        date = row["date"]

        # Форма хозяев — только матчи ДО текущего
        home_matches = team_idx.get(home, pd.DataFrame())
        home_f = team_form(home_matches, idx, WINDOW)

        # Форма гостей
        away_matches = team_idx.get(away, pd.DataFrame())
        away_f = team_form(away_matches, idx, WINDOW)

        # H2H
        h2h = h2h_stats(df, home, away, date)

        # Котировки
        oh_p, od_p, oa_p = avg_odds(row)

        # xG — средние за последние WINDOW матчей
        def xg_avg(team_matches, before_idx, is_home_col):
            past = team_matches[team_matches.index < before_idx].tail(WINDOW)
            xg_vals = []
            for _, r in past.iterrows():
                col = "home_xg" if r["is_home"] else "away_xg"
                if col in r and pd.notna(r[col]) and r[col] > 0:
                    xg_vals.append(float(r[col]))
            return round(float(np.mean(xg_vals)), 4) if xg_vals else np.nan

        home_xg_a = xg_avg(home_matches, idx, True)
        away_xg_a = xg_avg(away_matches, idx, False)
        xg_diff = (
            round(home_xg_a - away_xg_a, 4)
            if not np.isnan(home_xg_a) and not np.isnan(away_xg_a)
            else np.nan
        )

        # ── UEFA коэффициенты ─────────────────────────────────────────────────
        home_coeff = get_uefa_coeff(home)
        away_coeff = get_uefa_coeff(away)
        coeff_diff = round(home_coeff - away_coeff, 1)

        # ── Мотивация: позиция, давление, отдых ──────────────────────────────
        def season_standing(team_matches, before_idx):
            """Считает позицию и очки команды в текущем сезоне до этого матча."""
            past = team_matches[team_matches.index < before_idx]
            if len(past) == 0:
                return None, None
            wins   = sum(1 for _, r in past.iterrows()
                        if (r["is_home"] and r["ftr"]=="H") or (not r["is_home"] and r["ftr"]=="A"))
            draws  = sum(1 for _, r in past.iterrows() if r["ftr"]=="D")
            pts    = wins * 3 + draws
            return pts, len(past)

        home_pts_total, home_gp = season_standing(home_matches, idx)
        away_pts_total, away_gp = season_standing(away_matches, idx)

        # Среднее очков на матч для оценки положения
        home_pts_pm = (home_pts_total / home_gp) if home_gp and home_gp > 0 else 1.5
        away_pts_pm = (away_pts_total / away_gp) if away_gp and away_gp > 0 else 1.5

        # Примерная позиция: чем меньше очков/матч — тем ниже (20 команд)
        # Нормализуем: 0=лидер, 19=последний
        # Используем относительную оценку
        home_position = max(1, min(20, int(round(20 - home_pts_pm * 5.5))))
        away_position = max(1, min(20, int(round(20 - away_pts_pm * 5.5))))

        # Давление вылета: команды в нижней части (позиция 16-20) играют под огромным давлением
        # Это УСИЛИВАЕТ их мотивацию (команды борющиеся за выживание часто переигрывают фаворитов)
        home_relegation_gap = 17 - home_position  # отрицательное = в зоне вылета
        away_relegation_gap = 17 - away_position

        # Борьба за чемпионство (позиции 1-4)
        home_title_gap = home_position - 1   # 0 = лидер
        away_title_gap = away_position - 1

        # Дни отдыха
        def rest_days(team_matches, before_idx, current_date):
            past = team_matches[team_matches.index < before_idx]
            if len(past) == 0:
                return 7  # нет данных — нейтрально
            last_date = past.iloc[-1]["date"] if hasattr(past.iloc[-1]["date"], "days") else pd.to_datetime(past.iloc[-1]["date"])
            try:
                diff = (current_date - pd.to_datetime(last_date)).days
                return max(1, min(30, diff))
            except Exception:
                return 7

        home_rest = rest_days(home_matches, idx, date)
        away_rest = rest_days(away_matches, idx, date)

        # Процент побед дома/в гостях отдельно
        def home_win_rate_calc(team_matches, before_idx):
            past = team_matches[(team_matches.index < before_idx) & (team_matches["is_home"] == True)].tail(10)
            if len(past) == 0: return 0.4
            wins = sum(1 for _, r in past.iterrows() if r["ftr"] == "H")
            return round(wins / len(past), 3)

        def away_win_rate_calc(team_matches, before_idx):
            past = team_matches[(team_matches.index < before_idx) & (team_matches["is_home"] == False)].tail(10)
            if len(past) == 0: return 0.25
            wins = sum(1 for _, r in past.iterrows() if r["ftr"] == "A")
            return round(wins / len(past), 3)

        home_hwr = home_win_rate_calc(home_matches, idx)
        away_awr = away_win_rate_calc(away_matches, idx)

        try:
            conn.execute("""
                INSERT OR IGNORE INTO matches_features
                (league, season, date, home_team, away_team,
                 y_result, y_home_goals, y_away_goals,
                 home_pts_avg, home_win_rate, home_draw_rate, home_loss_rate,
                 home_gf_avg, home_ga_avg, home_gd_avg, home_sot_avg,
                 home_weighted_form, home_games_played,
                 away_pts_avg, away_win_rate, away_draw_rate, away_loss_rate,
                 away_gf_avg, away_ga_avg, away_gd_avg, away_sot_avg,
                 away_weighted_form, away_games_played,
                 pts_diff, gd_diff, form_diff,
                 h2h_n, h2h_home_wr, h2h_away_wr, h2h_draw_r, h2h_avg_goals,
                 odds_h, odds_d, odds_a, home_advantage,
                 home_xg_avg, away_xg_avg, home_xg_diff,
                 home_position, away_position,
                 home_pts_total, away_pts_total,
                 home_relegation_gap, away_relegation_gap,
                 home_title_gap, away_title_gap,
                 home_rest_days, away_rest_days,
                 home_home_win_rate, away_away_win_rate,
                 home_uefa_coeff, away_uefa_coeff, coeff_diff)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                row["league"], row["season"],
                date.strftime("%Y-%m-%d"), home, away,
                row["ftr"], int(row["fthg"]), int(row["ftag"]),
                # хозяева
                home_f["pts_avg"], home_f["win_rate"], home_f["draw_rate"], home_f["loss_rate"],
                home_f["gf_avg"], home_f["ga_avg"], home_f["gd_avg"], home_f["sot_avg"],
                home_f["weighted_form"], home_f["games_played"],
                # гости
                away_f["pts_avg"], away_f["win_rate"], away_f["draw_rate"], away_f["loss_rate"],
                away_f["gf_avg"], away_f["ga_avg"], away_f["gd_avg"], away_f["sot_avg"],
                away_f["weighted_form"], away_f["games_played"],
                # разница
                (home_f["pts_avg"] or 0) - (away_f["pts_avg"] or 0),
                (home_f["gd_avg"]  or 0) - (away_f["gd_avg"]  or 0),
                (home_f["weighted_form"] or 0) - (away_f["weighted_form"] or 0),
                # H2H
                h2h["h2h_n"], h2h["h2h_home_wr"], h2h["h2h_away_wr"],
                h2h["h2h_draw_r"], h2h["h2h_avg_goals"],
                # котировки
                oh_p, od_p, oa_p,
                1,  # home_advantage
                # xG
                home_xg_a, away_xg_a, xg_diff,
                # мотивация
                home_position, away_position,
                home_pts_total, away_pts_total,
                home_relegation_gap, away_relegation_gap,
                home_title_gap, away_title_gap,
                home_rest, away_rest,
                home_hwr, away_awr,
                home_coeff, away_coeff, coeff_diff,
            ))
            saved += conn.execute("SELECT changes()").fetchone()[0]
        except Exception as e:
            log.debug(f"Пропуск {home} vs {away} {date}: {e}")

        if i % 5000 == 0 and i > 0:
            conn.commit()

    conn.commit()
    return saved


def print_report(conn: sqlite3.Connection):
    df = pd.read_sql("SELECT * FROM matches_features", conn)
    sep = "=" * 55
    log.info(f"\n{sep}")
    log.info("  ПРИЗНАКИ  ИТОГОВЫЙ ОТЧЁТ")
    log.info(sep)
    log.info(f"  Строк:        {len(df):,}")
    log.info(f"  Признаков:    {len(df.columns)}")

    if not df.empty:
        log.info(f"  Период:       {df['date'].min()}  →  {df['date'].max()}")
        vc = df["y_result"].value_counts()
        total = len(df)
        log.info(f"\n  Результаты:")
        log.info(f"    Победа хозяев: {vc.get('H',0):,} ({vc.get('H',0)/total*100:.1f}%)")
        log.info(f"    Победа гостей: {vc.get('A',0):,} ({vc.get('A',0)/total*100:.1f}%)")
        log.info(f"    Ничья:         {vc.get('D',0):,} ({vc.get('D',0)/total*100:.1f}%)")

        log.info(f"\n  Среднее голов: {(df['y_home_goals']+df['y_away_goals']).mean():.2f}")
        odds_cov = df["odds_h"].notna().mean()
        h2h_cov  = df["h2h_n"].gt(0).mean()
        form_cov = df["home_pts_avg"].notna().mean()
        log.info(f"\n  Покрытие котировок: {odds_cov*100:.1f}%")
        log.info(f"  Покрытие H2H:       {h2h_cov*100:.1f}%")
        log.info(f"  Покрытие формы:     {form_cov*100:.1f}%")

        log.info(f"\n  Матчей по лигам:")
        for league, cnt in df["league"].value_counts().items():
            log.info(f"    {league}: {cnt:,}")

        log.info(f"\n  Матчей по сезонам:")
        for season, cnt in df["season"].value_counts().sort_index().items():
            log.info(f"    {season}: {cnt:,}")

    log.info(sep)


def run():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(SCHEMA)
    conn.commit()
    for idx_sql in INDEX_SQL:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass
    conn.commit()

    # Проверяем есть ли matches_raw
    count = conn.execute("SELECT COUNT(*) FROM matches_raw").fetchone()[0]
    if count == 0:
        log.error("Таблица matches_raw пуста! Сначала запустите: python collect_data.py")
        conn.close()
        return

    # Инкрементальное обновление — только новые матчи
    last_feat = conn.execute(
        "SELECT MAX(date) FROM matches_features"
    ).fetchone()[0]

    if last_feat:
        log.info(f"Последняя дата в matches_features: {last_feat}")
        new_count = conn.execute(
            "SELECT COUNT(*) FROM matches_raw WHERE date > ?", (last_feat,)
        ).fetchone()[0]

        if new_count == 0:
            log.info("Новых матчей нет — пересчёт не нужен")
            print_report(conn)
            conn.close()
            log.info("\nСледующий шаг: python train_models.py")
            return

        log.info(f"Найдено {new_count} новых матчей после {last_feat} — обновляю...")
    else:
        log.info("matches_features пуста — полный пересчёт...")

    log.info(f"Загружаю матчи из matches_raw...")
    df = load_raw(conn)

    log.info(f"Строим признаки...")
    saved = build_features(conn, df)

    log.info(f"\nГотово! Добавлено/обновлено строк: {saved:,}")
    print_report(conn)
    conn.close()

    log.info("\nСледующий шаг: python train_models.py")


if __name__ == "__main__":
    run()
