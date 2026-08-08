"""
train_models.py — Обучение ML-ансамбля N1–N6
Вход:  matches_features (SQLite)
Выход: models/*.pkl
"""

import sqlite3
import pickle
import logging
import numpy as np
import pandas as pd
from pathlib import Path

import os
os.environ["LOKY_MAX_CPU_COUNT"] = "4"

from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.linear_model  import LogisticRegression
from sklearn.model_selection import cross_val_score, TimeSeriesSplit
from sklearn.metrics import accuracy_score, mean_absolute_error
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

logging.basicConfig(format="%(asctime)s  %(levelname)s  %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

DB_PATH    = Path("data/epl_target_teams.db")
MODELS_DIR = Path("models")

# ── Наборы признаков ──────────────────────────────────────────────────────────
FEAT_N1 = [
    "home_pts_avg","home_win_rate","home_loss_rate",
    "home_gf_avg","home_ga_avg","home_gd_avg",
    "home_sot_avg","home_weighted_form",
    "away_pts_avg","away_win_rate","away_loss_rate",
    "away_gf_avg","away_ga_avg","away_gd_avg",
    "away_sot_avg","away_weighted_form",
    "pts_diff","gd_diff","form_diff",
]

FEAT_N2 = [
    "h2h_n","h2h_home_wr","h2h_away_wr","h2h_draw_r","h2h_avg_goals",
    "home_advantage",
]

FEAT_N3 = ["odds_h","odds_d","odds_a"]

FEAT_MOTIVATION = [
    "home_position", "away_position",
    "home_relegation_gap", "away_relegation_gap",
    "home_title_gap", "away_title_gap",
    "home_rest_days", "away_rest_days",
    "home_home_win_rate", "away_away_win_rate",
    "home_pts_total", "away_pts_total",
]

FEAT_UEFA = [
    "home_uefa_coeff", "away_uefa_coeff", "coeff_diff",
]

FEAT_XG = ["home_xg_avg", "away_xg_avg", "home_xg_diff"]

FEAT_N4 = FEAT_N1 + FEAT_N2 + FEAT_N3 + FEAT_MOTIVATION + FEAT_XG + FEAT_UEFA

FEAT_GOALS = FEAT_N1 + ["odds_h","odds_a","h2h_avg_goals"] + FEAT_XG + FEAT_MOTIVATION + FEAT_UEFA


def load_features(conn) -> pd.DataFrame:
    df = pd.read_sql("""
        SELECT * FROM matches_features
        WHERE home_games_played >= 2 AND away_games_played >= 2
        ORDER BY date ASC
    """, conn)
    log.info(f"Загружено {len(df):,} строк признаков")
    return df


def make_pipe(model) -> Pipeline:
    return Pipeline([("imp", SimpleImputer(strategy="median")), ("m", model)])


def evaluate(name, pipe, X, y, cv):
    sc = cross_val_score(pipe, X, y, cv=cv, scoring="accuracy", n_jobs=2)
    log.info(f"  {name}: CV = {sc.mean():.3f} ± {sc.std():.3f}")
    return sc.mean()


def train_result_models(df):
    log.info("─── Результат матча (H/D/A) ─────────────────────────")
    y    = df["y_result"]
    tscv = TimeSeriesSplit(n_splits=5)

    log.info("N1 — Форма (GradientBoosting)...")
    X1 = df[FEAT_N1]
    n1 = make_pipe(GradientBoostingClassifier(
        n_estimators=300, max_depth=3, learning_rate=0.04,
        subsample=0.75, min_samples_leaf=8, random_state=42))
    evaluate("N1", n1, X1, y, tscv)
    n1.fit(X1, y)

    log.info("N2 — H2H (GradientBoosting)...")
    X2 = df[FEAT_N2]
    n2 = make_pipe(GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05,
        subsample=0.75, min_samples_leaf=8, random_state=42))
    evaluate("N2", n2, X2, y, tscv)
    n2.fit(X2, y)

    log.info("N3 — Котировки (Logistic Regression)...")
    X3 = df[FEAT_N3]
    n3 = make_pipe(LogisticRegression(C=1.0, max_iter=1000, random_state=42))
    evaluate("N3", n3, X3, y, tscv)
    n3.fit(X3, y)

    log.info("N4 — Всё вместе (GradientBoosting)...")
    X4 = df[FEAT_N4]
    n4 = make_pipe(GradientBoostingClassifier(
        n_estimators=400, max_depth=3, learning_rate=0.03,
        subsample=0.75, min_samples_leaf=10, random_state=42))
    evaluate("N4", n4, X4, y, tscv)
    n4.fit(X4, y)

    log.info("N5 — Агрегатор (стекинг N1–N4)...")
    classes = n1.classes_
    def proba_df(pipe, X, prefix):
        return pd.DataFrame(
            pipe.predict_proba(X),
            columns=[f"{prefix}_{c}" for c in classes]
        )
    stack_X = pd.concat([
        proba_df(n1,X1,"n1"), proba_df(n2,X2,"n2"),
        proba_df(n3,X3,"n3"), proba_df(n4,X4,"n4"),
    ], axis=1)
    n5 = make_pipe(LogisticRegression(C=0.5, max_iter=2000, random_state=42))
    sc5 = cross_val_score(n5, stack_X, y, cv=tscv, scoring="accuracy")
    log.info(f"  N5: CV = {sc5.mean():.3f} ± {sc5.std():.3f}")
    n5.fit(stack_X, y)

    return n1, n2, n3, n4, n5, classes, stack_X.columns.tolist()


def train_goals_models(df):
    log.info("─── Голы (регрессия) ────────────────────────────────")
    X    = df[FEAT_GOALS]
    tscv = TimeSeriesSplit(n_splits=5)

    log.info("N6h — Голы хозяев...")
    yh  = df["y_home_goals"]
    n6h = make_pipe(GradientBoostingRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, min_samples_leaf=5, random_state=42))
    sc_h = cross_val_score(n6h, X, yh, cv=tscv, scoring="neg_mean_absolute_error")
    log.info(f"  N6h MAE = {-sc_h.mean():.3f}")
    n6h.fit(X, yh)

    log.info("N6a — Голы гостей...")
    ya  = df["y_away_goals"]
    n6a = make_pipe(GradientBoostingRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, min_samples_leaf=5, random_state=42))
    sc_a = cross_val_score(n6a, X, ya, cv=tscv, scoring="neg_mean_absolute_error")
    log.info(f"  N6a MAE = {-sc_a.mean():.3f}")
    n6a.fit(X, ya)

    return n6h, n6a


def save_models(models: dict):
    MODELS_DIR.mkdir(exist_ok=True)
    for name, obj in models.items():
        path = MODELS_DIR / f"{name}.pkl"
        with open(path, "wb") as f:
            pickle.dump(obj, f)
        log.info(f"  Сохранено: {path}")


def print_report(df, n1, n2, n3, n4, n5, n6h, n6a, classes, stack_cols):
    y = df["y_result"]
    X1 = df[FEAT_N1]; X2 = df[FEAT_N2]; X3 = df[FEAT_N3]; X4 = df[FEAT_N4]

    def proba_df(pipe, X, prefix):
        return pd.DataFrame(
            pipe.predict_proba(X),
            columns=[f"{prefix}_{c}" for c in classes]
        )
    stack_X = pd.concat([
        proba_df(n1,X1,"n1"), proba_df(n2,X2,"n2"),
        proba_df(n3,X3,"n3"), proba_df(n4,X4,"n4"),
    ], axis=1)

    sep = "=" * 55
    log.info(f"\n{sep}")
    log.info("  ИТОГОВЫЕ МЕТРИКИ МОДЕЛЕЙ (train accuracy)")
    log.info(sep)
    for name, pipe, X_test in [
        ("N1 (форма)",       n1, X1),
        ("N2 (H2H)",         n2, X2),
        ("N3 (котировки)",   n3, X3),
        ("N4 (всё)",         n4, X4),
        ("N5 (агрегатор)",   n5, stack_X),
    ]:
        acc = accuracy_score(y, pipe.predict(X_test))
        log.info(f"  {name:<22} {acc:.3f}  ({acc*100:.1f}%)")

    X_g = df[FEAT_GOALS]
    mh = mean_absolute_error(df["y_home_goals"], n6h.predict(X_g))
    ma = mean_absolute_error(df["y_away_goals"], n6a.predict(X_g))
    log.info(f"  {'N6h (голы хозяев)':<22} MAE = {mh:.3f}")
    log.info(f"  {'N6a (голы гостей)':<22} MAE = {ma:.3f}")
    base = accuracy_score(y, ["H"]*len(y))
    log.info(f"\n  Базовая точность (всегда хозяева): {base:.3f}")
    log.info(sep)


def run():
    conn = sqlite3.connect(DB_PATH)
    df = load_features(conn)
    conn.close()

    if len(df) < 500:
        log.error("Слишком мало данных! Сначала запустите collect_data.py и build_features.py")
        return

    n1, n2, n3, n4, n5, classes, stack_cols = train_result_models(df)
    n6h, n6a = train_goals_models(df)

    save_models({
        "n1_form":   n1,   "n2_h2h":  n2,
        "n3_odds":   n3,   "n4_full": n4,
        "n5_agg":    n5,
        "n6h_goals": n6h,  "n6a_goals": n6a,
        "meta": {
            "classes":    list(classes),
            "stack_cols": stack_cols,
            "feat_n1":    FEAT_N1,
            "feat_n2":    FEAT_N2,
            "feat_n3":    FEAT_N3,
            "feat_n4":    FEAT_N4,
            "feat_goals": FEAT_GOALS,
        }
    })

    print_report(df, n1, n2, n3, n4, n5, n6h, n6a, classes, stack_cols)
    log.info(f"Модели сохранены в: {MODELS_DIR.resolve()}")
    log.info("Следующий шаг: python run_bot.py")


if __name__ == "__main__":
    run()
