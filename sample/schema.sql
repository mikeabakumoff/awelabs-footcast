-- FOOTCAST database schema
-- Full DDL for every table. Collected data is not included; see README.

CREATE TABLE api_standings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at  TEXT,
    league_id   INTEGER,
    season      INTEGER,
    team_id     INTEGER,
    team_name   TEXT,
    rank        INTEGER,
    points      INTEGER,
    wins        INTEGER,
    draws       INTEGER,
    losses      INTEGER,
    goals_for   INTEGER,
    goals_against INTEGER,
    form        TEXT,
    UNIQUE(league_id, season, team_id)
);

CREATE TABLE goal_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id   INTEGER,
    league_id    INTEGER,
    season       INTEGER,
    team_name    TEXT,
    player_name  TEXT,
    minute       INTEGER,
    extra_time   INTEGER DEFAULT 0,
    goal_type    TEXT,
    fetched_at   TEXT,
    UNIQUE(fixture_id, player_name, minute)
);

CREATE TABLE injuries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at   TEXT,
    league_id    INTEGER,
    season       INTEGER,
    team_id      INTEGER,
    team_name    TEXT,
    player_id    INTEGER,
    player_name  TEXT,
    injury_type  TEXT,
    reason       TEXT,
    fixture_id   INTEGER,
    fixture_date TEXT,
    UNIQUE(player_id, fixture_id)
);

CREATE TABLE live_odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT, match_date TEXT, league TEXT,
    home_team TEXT, away_team TEXT, bookmaker TEXT,
    odds_home REAL, odds_draw REAL, odds_away REAL,
    prob_home REAL, prob_draw REAL, prob_away REAL,
    UNIQUE(match_date, home_team, away_team, bookmaker)
);

CREATE TABLE live_stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    updated_at   TEXT,
    team         TEXT,
    stat_type    TEXT,
    season       TEXT,
    data_json    TEXT,
    source       TEXT,
    UNIQUE(team, stat_type, season)
);

CREATE TABLE match_lineups (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id      INTEGER,
        team_id         INTEGER,
        team_name       TEXT,
        formation       TEXT,
        coach_name      TEXT,
        lineup_json     TEXT,
        fetched_at      TEXT,
        UNIQUE(fixture_id, team_id)
    );

CREATE TABLE match_predictions (
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
    );

CREATE TABLE matches_features (
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
);

CREATE TABLE matches_raw (
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
    iwh         REAL, iwd  REAL, iwa   REAL, home_xg REAL, away_xg REAL,
    UNIQUE(league, date, home_team, away_team)
);

CREATE TABLE player_season_stats (
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
    );

CREATE TABLE scorer_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    updated_at      TEXT,
    league_id       INTEGER,
    season          INTEGER,
    team_name       TEXT,
    player_name     TEXT,
    total_goals     INTEGER,
    avg_minute      REAL,
    first_goal_rate REAL,
    games_scored    INTEGER,
    UNIQUE(league_id, season, player_name)
);

CREATE TABLE sent_messages (
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
    prediction_ok  INTEGER             -- 1=верно, 0=неверно, NULL=ещё не сыгран
, fg_player TEXT, fg_team TEXT, fg_minute INTEGER, real_fg_player TEXT, real_fg_team TEXT, real_fg_minute INTEGER, fg_ok INTEGER);

CREATE TABLE team_transfers (
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
    );

CREATE TABLE uefa_coefficients (
            team_name   TEXT PRIMARY KEY,
            coefficient REAL,
            updated_at  TEXT
        );

CREATE TABLE upcoming_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT, match_date TEXT, match_time TEXT,
    league TEXT, league_name TEXT,
    home_team TEXT, away_team TEXT,
    UNIQUE(match_date, home_team, away_team)
);

