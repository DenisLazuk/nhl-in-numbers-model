"""Goalie landing page and season stats from NHL public APIs."""

from __future__ import annotations

from .client import API_WEB, STATS_REST, get_json


def fetch_landing(game_id: int) -> dict:
    return get_json(f"{API_WEB}/gamecenter/{game_id}/landing")


def _roster_side(landing: dict, team_abbrev: str) -> dict | None:
    home = landing.get("homeTeam") or {}
    away = landing.get("awayTeam") or {}
    if home.get("abbrev") == team_abbrev:
        return home
    if away.get("abbrev") == team_abbrev:
        return away
    return None


def parse_starting_goalie(landing: dict, team_abbrev: str) -> dict | None:
    """Confirmed starter from landing rosters when available."""
    rosters = landing.get("rosters")
    if isinstance(rosters, dict):
        for key in ("homeTeam", "awayTeam", "home", "away"):
            block = rosters.get(key)
            if not isinstance(block, dict):
                continue
            abbrev = (block.get("teamAbbrev") or block.get("abbrev") or {}).get("default")
            if abbrev and abbrev != team_abbrev:
                if key in ("homeTeam", "home") and (landing.get("homeTeam") or {}).get("abbrev") != team_abbrev:
                    continue
                if key in ("awayTeam", "away") and (landing.get("awayTeam") or {}).get("abbrev") != team_abbrev:
                    continue
            for player in block.get("dressedPlayers") or []:
                if player.get("position") != "G":
                    continue
                if player.get("startingLineup"):
                    return {
                        "player_id": player.get("playerId") or player.get("id"),
                        "name": (player.get("name") or {}).get("default") or player.get("name", ""),
                    }
    side = _roster_side(landing, team_abbrev)
    if side:
        for player in side.get("dressedPlayers") or []:
            if player.get("position") == "G" and player.get("startingLineup"):
                return {
                    "player_id": player.get("playerId") or player.get("id"),
                    "name": (player.get("name") or {}).get("default") or player.get("name", ""),
                }
    return None


def get_goalie_summary(player_id: int, season: int) -> dict | None:
    """Season aggregate Sv% for one goalie (stats REST)."""
    season_id = season * 10000 + (season + 1)
    params = {
        "cayenneExp": f"playerId={player_id} and gameTypeId=2 and seasonId={season_id}",
        "limit": "1",
    }
    d = get_json(f"{STATS_REST}/goalie/summary", params=params)
    rows = d.get("data") or []
    if not rows:
        return None
    row = rows[0]
    return {
        "player_id": player_id,
        "name": row.get("goalieFullName") or row.get("lastName"),
        "save_pct": row.get("savePct"),
        "games_started": row.get("gamesStarted", 0),
        "team": row.get("teamAbbrevs"),
    }
