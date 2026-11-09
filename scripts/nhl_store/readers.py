"""Read model-compatible dicts from SQLite."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .db import connect, db_exists


def has_team_form(slate_date: date, *, path: Path | None = None) -> bool:
    if not db_exists(path):
        return False
    with connect(path) as conn:
        row = conn.execute(
            "SELECT 1 FROM team_form WHERE slate_date = ? LIMIT 1",
            (slate_date.isoformat(),),
        ).fetchone()
        return row is not None


def get_slate(slate_date: date, *, path: Path | None = None) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT home_abbrev, away_abbrev, home_score, away_score, outcome, state
            FROM games
            WHERE game_date = ? AND game_type = 2
            ORDER BY start_time_utc, game_id
            """,
            (slate_date.isoformat(),),
        ).fetchall()
    return [
        {
            "home": r["home_abbrev"],
            "away": r["away_abbrev"],
            "home_score": r["home_score"],
            "away_score": r["away_score"],
            "outcome": r["outcome"],
            "state": r["state"],
        }
        for r in rows
    ]


def get_standings(slate_date: date, *, path: Path | None = None) -> dict[str, dict]:
    """Standings as-of day before slate (stored under slate_date key)."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM team_form WHERE slate_date = ?",
            (slate_date.isoformat(),),
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        motiv = r["motiv"]
        out[r["team_abbrev"]] = {
            "name": r["name"],
            "gp": r["gp"],
            "points": r["points"],
            "conference": r["conference"],
            "clinch": r["clinch"] or "",
            "record": r["record"],
            "point_pct": r["point_pct"],
            "l10_record": r["l10_record"],
            "l10_pts_pct": r["l10_pts_pct"],
            "motiv": motiv if motiv != "" else None,
        }
    return out


def get_team_summary(slate_date: date, *, path: Path | None = None) -> dict[str, dict]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM team_form WHERE slate_date = ?",
            (slate_date.isoformat(),),
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        if r["gf_pg"] is None:
            continue
        out[r["team_abbrev"]] = {
            "gp": r["gp"],
            "gf_pg": r["gf_pg"],
            "ga_pg": r["ga_pg"],
            "sf_pg": r["sf_pg"],
            "sa_pg": r["sa_pg"],
            "pp": r["pp"],
            "pk": r["pk"],
            "fow": r["fow"],
            "sv": r["sv"],
        }
    return out


def teams_played_on(d: date, *, path: Path | None = None) -> set[str]:
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT home_abbrev, away_abbrev FROM games
            WHERE game_date = ? AND game_type = 2
            """,
            (d.isoformat(),),
        ).fetchall()
    teams: set[str] = set()
    for r in rows:
        teams.add(r["home_abbrev"])
        teams.add(r["away_abbrev"])
    return teams


def get_projected_goalies(slate_date: date, *, path: Path | None = None) -> dict[str, dict]:
    """Keyed by team_abbrev; latest projection if multiple games same day."""
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM projected_goalies
            WHERE slate_date = ? AND player_id IS NOT NULL
            ORDER BY game_id
            """,
            (slate_date.isoformat(),),
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        sv_l10, sv_season = r["sv_l10"], r["sv_season"]
        effective = sv_l10 if (r["starts_l10"] or 0) >= 3 and sv_l10 is not None else sv_season
        out[r["team_abbrev"]] = {
            "game_id": r["game_id"],
            "player_id": r["player_id"],
            "player_name": r["player_name"],
            "source": r["source"],
            "sv_season": sv_season,
            "sv_l10": sv_l10,
            "starts_l10": r["starts_l10"],
            "effective_sv": effective,
        }
    return out


def get_injuries(slate_date: date, *, path: Path | None = None) -> dict[str, dict]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM injury_flags WHERE slate_date = ?",
            (slate_date.isoformat(),),
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        team = r["team_abbrev"]
        if team not in out or abs(r["impact"]) > abs(out[team]["impact"]):
            out[team] = {
                "player_name": r["player_name"],
                "impact": r["impact"],
                "note": r["note"] or "",
            }
    return out
