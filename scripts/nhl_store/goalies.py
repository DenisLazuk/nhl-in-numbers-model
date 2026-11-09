"""Projected goalies and manual injury flags."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from nhl_api import fetch_landing, get_goalie_summary, parse_starting_goalie

from .db import DATA_DIR, connect

MANUAL_DIR = DATA_DIR / "manual"
MANUAL_GOALIES = MANUAL_DIR / "projected_goalies.csv"
MANUAL_INJURIES = MANUAL_DIR / "injuries.csv"


def _season_for(slate_date: date) -> int:
    return slate_date.year if slate_date.month >= 9 else slate_date.year - 1


def _read_manual_goalies(slate_date: date) -> dict[tuple[int, str], dict]:
    path = MANUAL_GOALIES if MANUAL_GOALIES.exists() else MANUAL_DIR / "projected_goalies.example.csv"
    if not path.exists():
        return {}
    out: dict[tuple[int, str], dict] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("slate_date", "").startswith("#"):
                continue
            if row.get("slate_date") != slate_date.isoformat():
                continue
            game_id = int(row["game_id"])
            team = row["team_abbrev"].strip().upper()
            pid = int(row["player_id"]) if row.get("player_id") else None
            out[(game_id, team)] = {
                "player_id": pid,
                "player_name": row.get("player_name", "").strip(),
                "source": "manual",
            }
    return out


def _read_manual_injuries(slate_date: date) -> list[dict]:
    path = MANUAL_INJURIES if MANUAL_INJURIES.exists() else MANUAL_DIR / "injuries.example.csv"
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("slate_date") or row["slate_date"].startswith("#"):
                continue
            if row["slate_date"] != slate_date.isoformat():
                continue
            rows.append({
                "team_abbrev": row["team_abbrev"].strip().upper(),
                "player_name": row["player_name"].strip(),
                "impact": float(row["impact"]),
                "note": (row.get("note") or "").strip(),
            })
    return rows


def _last_starter(conn, team_abbrev: str, before: date) -> dict | None:
    row = conn.execute(
        """
        SELECT g.player_id, p.name AS player_name
        FROM goalies g
        JOIN games gm ON g.game_id = gm.game_id
        JOIN players p ON p.player_id = g.player_id
        WHERE g.team_abbrev = ? AND g.starter = 1 AND gm.game_date < ?
        ORDER BY gm.game_date DESC, gm.game_id DESC
        LIMIT 1
        """,
        (team_abbrev, before.isoformat()),
    ).fetchone()
    if row is None:
        return None
    return {
        "player_id": row["player_id"],
        "player_name": row["player_name"],
        "source": "last_starter",
    }


def goalie_save_pct(
    player_id: int,
    asof_date: date,
    conn,
    *,
    season: int | None = None,
) -> tuple[float | None, float | None, int]:
    """Return (sv_l10, sv_season, starts_l10) using DB history with REST fallback."""
    rows = conn.execute(
        """
        SELECT g.saves, g.shots_against, g.save_pct
        FROM goalies g
        JOIN games gm ON g.game_id = gm.game_id
        WHERE g.player_id = ? AND g.starter = 1 AND gm.game_date < ?
        ORDER BY gm.game_date DESC, gm.game_id DESC
        LIMIT 10
        """,
        (player_id, asof_date.isoformat()),
    ).fetchall()
    starts = len(rows)
    sv_l10 = None
    if rows:
        saves = sum(r["saves"] or 0 for r in rows)
        sa = sum(r["shots_against"] or 0 for r in rows)
        if sa > 0:
            sv_l10 = saves / sa
    sv_season = None
    if starts >= 3 and sv_l10 is not None:
        return sv_l10, sv_l10, starts
    season = season or _season_for(asof_date)
    summary = get_goalie_summary(player_id, season)
    if summary and summary.get("save_pct") is not None:
        sv_season = float(summary["save_pct"])
    return sv_l10, sv_season, starts


def resolve_projected_goalie(
    team_abbrev: str,
    slate_date: date,
    game_id: int,
    conn,
    *,
    manual: dict[tuple[int, str], dict] | None = None,
    allow_landing: bool = True,
) -> dict | None:
    manual = manual if manual is not None else _read_manual_goalies(slate_date)
    key = (game_id, team_abbrev)
    if key in manual:
        gk = manual[key].copy()
    elif allow_landing:
        try:
            landing = fetch_landing(game_id)
            confirmed = parse_starting_goalie(landing, team_abbrev)
            if confirmed and confirmed.get("player_id"):
                gk = {**confirmed, "source": "confirmed"}
            else:
                gk = _last_starter(conn, team_abbrev, slate_date)
        except Exception:
            gk = _last_starter(conn, team_abbrev, slate_date)
    else:
        gk = _last_starter(conn, team_abbrev, slate_date)
    if not gk or not gk.get("player_id"):
        return None
    asof = slate_date.fromordinal(slate_date.toordinal() - 1)
    sv_l10, sv_season, starts_l10 = goalie_save_pct(gk["player_id"], asof, conn)
    effective = sv_l10 if starts_l10 >= 3 and sv_l10 is not None else sv_season
    return {
        "game_id": game_id,
        "player_id": gk["player_id"],
        "player_name": gk.get("player_name") or gk.get("name"),
        "source": gk["source"],
        "sv_season": sv_season,
        "sv_l10": sv_l10,
        "starts_l10": starts_l10,
        "effective_sv": effective,
    }


def build_projections(
    slate_date: date,
    *,
    path: Path | None = None,
    allow_landing: bool = True,
) -> int:
    """Resolve and persist projected goalies for all games on the slate."""
    manual = _read_manual_goalies(slate_date)
    n = 0
    with connect(path) as conn:
        games = conn.execute(
            """
            SELECT game_id, home_abbrev, away_abbrev
            FROM games WHERE game_date = ? AND game_type = 2
            """,
            (slate_date.isoformat(),),
        ).fetchall()
        for g in games:
            for team in (g["home_abbrev"], g["away_abbrev"]):
                proj = resolve_projected_goalie(
                    team, slate_date, g["game_id"], conn,
                    manual=manual, allow_landing=allow_landing,
                )
                if proj is None:
                    conn.execute(
                        """
                        DELETE FROM projected_goalies
                        WHERE slate_date = ? AND game_id = ? AND team_abbrev = ?
                        """,
                        (slate_date.isoformat(), g["game_id"], team),
                    )
                    continue
                conn.execute(
                    """
                    INSERT INTO projected_goalies (
                        slate_date, game_id, team_abbrev, player_id, player_name,
                        source, sv_season, sv_l10, starts_l10, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(slate_date, game_id, team_abbrev) DO UPDATE SET
                        player_id = excluded.player_id,
                        player_name = excluded.player_name,
                        source = excluded.source,
                        sv_season = excluded.sv_season,
                        sv_l10 = excluded.sv_l10,
                        starts_l10 = excluded.starts_l10,
                        synced_at = datetime('now')
                    """,
                    (
                        slate_date.isoformat(),
                        g["game_id"],
                        team,
                        proj["player_id"],
                        proj["player_name"],
                        proj["source"],
                        proj["sv_season"],
                        proj["sv_l10"],
                        proj["starts_l10"],
                    ),
                )
                n += 1
        conn.commit()
    return n


def load_injuries(slate_date: date, *, path: Path | None = None) -> int:
    rows = _read_manual_injuries(slate_date)
    with connect(path) as conn:
        conn.execute(
            "DELETE FROM injury_flags WHERE slate_date = ?",
            (slate_date.isoformat(),),
        )
        for row in rows:
            conn.execute(
                """
                INSERT INTO injury_flags (
                    slate_date, team_abbrev, player_name, impact, note, synced_at
                ) VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    slate_date.isoformat(),
                    row["team_abbrev"],
                    row["player_name"],
                    row["impact"],
                    row["note"],
                ),
            )
        conn.commit()
    return len(rows)
