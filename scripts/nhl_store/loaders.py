"""Load NHL API payloads into SQLite."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from nhl_api import (
    get_game_boxscore,
    get_schedule,
    get_standings,
    get_team_summary_asof,
)

from .db import connect, init_schema
from .goalies import build_projections, load_injuries

_FINAL_STATES = frozenset({"OFF", "FINAL"})


def _upsert_team(conn, abbrev: str, name: str | None = None, conference: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO teams (abbrev, name, conference, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(abbrev) DO UPDATE SET
            name = COALESCE(excluded.name, teams.name),
            conference = COALESCE(excluded.conference, teams.conference),
            updated_at = datetime('now')
        """,
        (abbrev, name, conference),
    )


def _upsert_game(conn, slate_date: date, g: dict) -> None:
    conn.execute(
        """
        INSERT INTO games (
            game_id, season, game_type, game_date,
            home_abbrev, away_abbrev, home_score, away_score,
            state, outcome, start_time_utc, venue, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(game_id) DO UPDATE SET
            season = excluded.season,
            game_type = excluded.game_type,
            game_date = excluded.game_date,
            home_abbrev = excluded.home_abbrev,
            away_abbrev = excluded.away_abbrev,
            home_score = excluded.home_score,
            away_score = excluded.away_score,
            state = excluded.state,
            outcome = excluded.outcome,
            start_time_utc = excluded.start_time_utc,
            venue = excluded.venue,
            synced_at = datetime('now')
        """,
        (
            g["game_id"],
            g.get("season"),
            g.get("game_type", 2),
            slate_date.isoformat(),
            g["home"],
            g["away"],
            g.get("home_score"),
            g.get("away_score"),
            g.get("state"),
            g.get("outcome"),
            g.get("start_time_utc"),
            g.get("venue"),
        ),
    )


def _upsert_team_form(
    conn,
    slate_date: date,
    asof_date: date,
    standings: dict[str, dict],
    summary: dict[str, dict],
) -> int:
    n = 0
    for abbrev, st in standings.items():
        sm = summary.get(abbrev, {})
        _upsert_team(conn, abbrev, st.get("name"), st.get("conference"))
        conn.execute(
            """
            INSERT INTO team_form (
                slate_date, team_abbrev, asof_date, name,
                gp, points, point_pct, conference, clinch, record,
                l10_record, l10_pts_pct, motiv,
                gf_pg, ga_pg, sf_pg, sa_pg, pp, pk, fow, sv,
                synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(slate_date, team_abbrev) DO UPDATE SET
                asof_date = excluded.asof_date,
                name = excluded.name,
                gp = excluded.gp,
                points = excluded.points,
                point_pct = excluded.point_pct,
                conference = excluded.conference,
                clinch = excluded.clinch,
                record = excluded.record,
                l10_record = excluded.l10_record,
                l10_pts_pct = excluded.l10_pts_pct,
                motiv = excluded.motiv,
                gf_pg = excluded.gf_pg,
                ga_pg = excluded.ga_pg,
                sf_pg = excluded.sf_pg,
                sa_pg = excluded.sa_pg,
                pp = excluded.pp,
                pk = excluded.pk,
                fow = excluded.fow,
                sv = excluded.sv,
                synced_at = datetime('now')
            """,
            (
                slate_date.isoformat(),
                abbrev,
                asof_date.isoformat(),
                st.get("name"),
                st.get("gp"),
                st.get("points"),
                st.get("point_pct"),
                st.get("conference"),
                st.get("clinch", ""),
                st.get("record"),
                st.get("l10_record"),
                st.get("l10_pts_pct"),
                st.get("motiv"),
                sm.get("gf_pg"),
                sm.get("ga_pg"),
                sm.get("sf_pg"),
                sm.get("sa_pg"),
                sm.get("pp"),
                sm.get("pk"),
                sm.get("fow"),
                sm.get("sv"),
            ),
        )
        n += 1
    return n


def _sync_boxscore(conn, game_id: int) -> None:
    box = get_game_boxscore(game_id)
    st = box["special_teams"]
    home_st, away_st = st["home"], st["away"]
    conn.execute(
        """
        UPDATE games SET
            home_score = ?,
            away_score = ?,
            state = ?,
            outcome = ?,
            home_sog = ?,
            away_sog = ?,
            home_pp_goals = ?,
            home_pp_opps = ?,
            away_pp_goals = ?,
            away_pp_opps = ?,
            home_pp_pct = ?,
            away_pp_pct = ?,
            home_fow_pct = ?,
            away_fow_pct = ?,
            home_pim = ?,
            away_pim = ?,
            synced_at = datetime('now')
        WHERE game_id = ?
        """,
        (
            box.get("home_score"),
            box.get("away_score"),
            box.get("state"),
            box.get("outcome"),
            home_st.get("sog"),
            away_st.get("sog"),
            home_st.get("pp_goals"),
            home_st.get("pp_opportunities"),
            away_st.get("pp_goals"),
            away_st.get("pp_opportunities"),
            home_st.get("pp_pct"),
            away_st.get("pp_pct"),
            home_st.get("fow_pct"),
            away_st.get("fow_pct"),
            home_st.get("pim"),
            away_st.get("pim"),
            game_id,
        ),
    )
    for side, abbrev in (("home", box["home"]), ("away", box["away"])):
        for gk in box["goalies"].get(side, []):
            pid = gk.get("player_id")
            if pid is None:
                continue
            conn.execute(
                """
                INSERT INTO players (player_id, name, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(player_id) DO UPDATE SET
                    name = excluded.name,
                    updated_at = datetime('now')
                """,
                (pid, gk["name"]),
            )
            conn.execute(
                """
                INSERT INTO goalies (
                    game_id, team_abbrev, player_id, sweater, starter, toi,
                    shots_against, saves, goals_against, save_pct,
                    pp_ga, ev_ga, sh_ga
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id, team_abbrev, player_id) DO UPDATE SET
                    sweater = excluded.sweater,
                    starter = excluded.starter,
                    toi = excluded.toi,
                    shots_against = excluded.shots_against,
                    saves = excluded.saves,
                    goals_against = excluded.goals_against,
                    save_pct = excluded.save_pct,
                    pp_ga = excluded.pp_ga,
                    ev_ga = excluded.ev_ga,
                    sh_ga = excluded.sh_ga
                """,
                (
                    game_id,
                    abbrev,
                    pid,
                    gk.get("sweater"),
                    int(bool(gk.get("starter"))),
                    gk.get("toi"),
                    gk.get("shots_against"),
                    gk.get("saves"),
                    gk.get("goals_against"),
                    gk.get("save_pct"),
                    gk.get("pp_ga"),
                    gk.get("ev_ga"),
                    gk.get("sh_ga"),
                ),
            )


def refresh_game_results(slate_date: date, *, path: Path | None = None) -> int:
    """Upsert one slate's scoreboard into SQLite. No-op when the database is absent."""
    from .db import db_exists

    if not db_exists(path):
        return 0
    games = get_schedule(slate_date)
    with connect(path) as conn:
        for game in games:
            _upsert_team(conn, game["home"])
            _upsert_team(conn, game["away"])
            _upsert_game(conn, slate_date, game)
        conn.commit()
    return len(games)


def sync_date(slate_date: date, *, path: Path | None = None) -> dict[str, int]:
    """Fetch one slate from NHL API and upsert into the database."""
    init_schema(path)
    stats = {"games": 0, "team_form": 0, "boxscores": 0}
    games = get_schedule(slate_date)
    asof = slate_date - timedelta(days=1)
    standings = get_standings(asof)
    summary = get_team_summary_asof(slate_date, standings) if standings else {}

    with connect(path) as conn:
        for g in games:
            _upsert_team(conn, g["home"])
            _upsert_team(conn, g["away"])
            _upsert_game(conn, slate_date, g)
            stats["games"] += 1
            if g.get("state") in _FINAL_STATES:
                _sync_boxscore(conn, g["game_id"])
                stats["boxscores"] += 1
        if standings:
            stats["team_form"] = _upsert_team_form(conn, slate_date, asof, standings, summary)
        conn.commit()
    load_injuries(slate_date, path=path)
    stats["projections"] = build_projections(slate_date, path=path)
    stats["injuries"] = len(_injury_rows(slate_date))
    return stats


def _injury_rows(slate_date: date) -> list:
    from .goalies import _read_manual_injuries
    return _read_manual_injuries(slate_date)


def sync_range(start: date, end: date, *, path: Path | None = None) -> dict[str, int]:
    """Sync every calendar day in [start, end] inclusive."""
    totals = {"days": 0, "games": 0, "team_form": 0, "boxscores": 0, "projections": 0, "injuries": 0}
    d = start
    while d <= end:
        day = sync_date(d, path=path)
        totals["days"] += 1
        totals["games"] += day["games"]
        totals["team_form"] += day["team_form"]
        totals["boxscores"] += day["boxscores"]
        totals["projections"] += day.get("projections", 0)
        totals["injuries"] += day.get("injuries", 0)
        d += timedelta(days=1)
    return totals
