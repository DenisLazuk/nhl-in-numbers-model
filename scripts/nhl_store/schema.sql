-- NHL Analytics App — SQLite schema (ETL-2)
-- Postgres migration: same tables; swap AUTOINCREMENT / types as needed.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS teams (
    abbrev      TEXT PRIMARY KEY,
    name        TEXT,
    conference  TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS games (
    game_id         INTEGER PRIMARY KEY,
    season          INTEGER,
    game_type       INTEGER NOT NULL DEFAULT 2,
    game_date       TEXT NOT NULL,
    home_abbrev     TEXT NOT NULL REFERENCES teams(abbrev),
    away_abbrev     TEXT NOT NULL REFERENCES teams(abbrev),
    home_score      INTEGER,
    away_score      INTEGER,
    state           TEXT,
    outcome         TEXT,
    start_time_utc  TEXT,
    venue           TEXT,
    home_sog        INTEGER,
    away_sog        INTEGER,
    home_pp_goals   INTEGER,
    home_pp_opps    INTEGER,
    home_pp_pct     REAL,
    away_pp_goals   INTEGER,
    away_pp_opps    INTEGER,
    away_pp_pct     REAL,
    home_fow_pct    REAL,
    away_fow_pct    REAL,
    home_pim        INTEGER,
    away_pim        INTEGER,
    synced_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_games_date ON games(game_date);
CREATE INDEX IF NOT EXISTS idx_games_state ON games(state);

CREATE TABLE IF NOT EXISTS players (
    player_id   INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS goalies (
    game_id         INTEGER NOT NULL REFERENCES games(game_id),
    team_abbrev     TEXT NOT NULL REFERENCES teams(abbrev),
    player_id       INTEGER NOT NULL REFERENCES players(player_id),
    sweater         INTEGER,
    starter         INTEGER NOT NULL DEFAULT 0,
    toi             TEXT,
    shots_against   INTEGER,
    saves           INTEGER,
    goals_against   INTEGER,
    save_pct        REAL,
    pp_ga           INTEGER,
    ev_ga           INTEGER,
    sh_ga           INTEGER,
    PRIMARY KEY (game_id, team_abbrev, player_id)
);

CREATE TABLE IF NOT EXISTS team_form (
    slate_date      TEXT NOT NULL,
    team_abbrev     TEXT NOT NULL REFERENCES teams(abbrev),
    asof_date       TEXT NOT NULL,
    name            TEXT,
    gp              INTEGER,
    points          INTEGER,
    point_pct       REAL,
    conference      TEXT,
    clinch          TEXT,
    record          TEXT,
    l10_record      TEXT,
    l10_pts_pct     REAL,
    motiv           TEXT,
    gf_pg           REAL,
    ga_pg           REAL,
    sf_pg           REAL,
    sa_pg           REAL,
    pp              REAL,
    pk              REAL,
    fow             REAL,
    sv              REAL,
    synced_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (slate_date, team_abbrev)
);

CREATE INDEX IF NOT EXISTS idx_team_form_asof ON team_form(asof_date);

CREATE TABLE IF NOT EXISTS projected_goalies (
    slate_date      TEXT NOT NULL,
    game_id         INTEGER NOT NULL REFERENCES games(game_id),
    team_abbrev     TEXT NOT NULL REFERENCES teams(abbrev),
    player_id       INTEGER,
    player_name     TEXT,
    source          TEXT NOT NULL,
    sv_season       REAL,
    sv_l10          REAL,
    starts_l10      INTEGER,
    synced_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (slate_date, game_id, team_abbrev)
);

CREATE INDEX IF NOT EXISTS idx_projected_goalies_slate ON projected_goalies(slate_date);

CREATE TABLE IF NOT EXISTS injury_flags (
    slate_date      TEXT NOT NULL,
    team_abbrev     TEXT NOT NULL REFERENCES teams(abbrev),
    player_name     TEXT NOT NULL,
    impact          REAL NOT NULL,
    note            TEXT,
    synced_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (slate_date, team_abbrev, player_name)
);
