"""Per-game boxscore, goalies, and special-teams lines."""

from __future__ import annotations

from .client import API_WEB, get_json


def fetch_boxscore(game_id: int) -> dict:
    return get_json(f"{API_WEB}/gamecenter/{game_id}/boxscore")


def fetch_right_rail(game_id: int) -> dict:
    return get_json(f"{API_WEB}/gamecenter/{game_id}/right-rail")


def _player_name(player: dict) -> str:
    name = player.get("name") or {}
    return name.get("default", "")


def parse_goalies(boxscore: dict) -> dict[str, list[dict]]:
    """Normalize goalie lines from a boxscore payload."""
    pg = boxscore.get("playerByGameStats", {})
    out: dict[str, list[dict]] = {"home": [], "away": []}
    for side, key in (("home", "homeTeam"), ("away", "awayTeam")):
        for g in pg.get(key, {}).get("goalies", []):
            sa = g.get("shotsAgainst") or 0
            saves = g.get("saves") or 0
            out[side].append({
                "player_id": g.get("playerId"),
                "name": _player_name(g),
                "sweater": g.get("sweaterNumber"),
                "starter": bool(g.get("starter")),
                "toi": g.get("toi"),
                "shots_against": sa,
                "saves": saves,
                "goals_against": g.get("goalsAgainst", 0),
                "save_pct": (saves / sa) if sa else None,
                "pp_ga": g.get("powerPlayGoalsAgainst", 0),
                "ev_ga": g.get("evenStrengthGoalsAgainst", 0),
                "sh_ga": g.get("shorthandedGoalsAgainst", 0),
            })
    return out


def starter_goalies(boxscore: dict) -> dict[str, dict | None]:
    parsed = parse_goalies(boxscore)
    return {
        side: next((g for g in goalies if g["starter"]), None)
        for side, goalies in parsed.items()
    }


def _parse_pp_fraction(value: str | None) -> tuple[int, int] | None:
    if not value or "/" not in value:
        return None
    goals_s, opps_s = value.split("/", 1)
    return int(goals_s), int(opps_s)


def parse_special_teams(right_rail: dict) -> dict[str, dict]:
    """Per-game PP lines from right-rail teamGameStats."""
    by_cat = {row["category"]: row for row in right_rail.get("teamGameStats", [])}
    pp = by_cat.get("powerPlay", {})
    pp_pct = by_cat.get("powerPlayPctg", {})
    home_pp = _parse_pp_fraction(pp.get("homeValue"))
    away_pp = _parse_pp_fraction(pp.get("awayValue"))
    return {
        "home": {
            "pp_goals": home_pp[0] if home_pp else None,
            "pp_opportunities": home_pp[1] if home_pp else None,
            "pp_pct": pp_pct.get("homeValue"),
            "pk_pct": away_pp[1] and away_pp[0] is not None and (1 - away_pp[0] / away_pp[1])
            if away_pp and away_pp[1] else None,
            "sog": by_cat.get("sog", {}).get("homeValue"),
            "fow_pct": by_cat.get("faceoffWinningPctg", {}).get("homeValue"),
            "pim": by_cat.get("pim", {}).get("homeValue"),
        },
        "away": {
            "pp_goals": away_pp[0] if away_pp else None,
            "pp_opportunities": away_pp[1] if away_pp else None,
            "pp_pct": pp_pct.get("awayValue"),
            "pk_pct": home_pp[1] and home_pp[0] is not None and (1 - home_pp[0] / home_pp[1])
            if home_pp and home_pp[1] else None,
            "sog": by_cat.get("sog", {}).get("awayValue"),
            "fow_pct": by_cat.get("faceoffWinningPctg", {}).get("awayValue"),
            "pim": by_cat.get("pim", {}).get("awayValue"),
        },
    }


def get_game_boxscore(game_id: int) -> dict:
    """Boxscore + goalies + per-game special teams for one game."""
    raw = fetch_boxscore(game_id)
    rr = fetch_right_rail(game_id)
    home = raw.get("homeTeam", {})
    away = raw.get("awayTeam", {})
    return {
        "game_id": game_id,
        "season": raw.get("season"),
        "game_type": raw.get("gameType"),
        "state": raw.get("gameState"),
        "game_date": raw.get("gameDate"),
        "home": home.get("abbrev"),
        "away": away.get("abbrev"),
        "home_score": home.get("score"),
        "away_score": away.get("score"),
        "outcome": (raw.get("gameOutcome") or {}).get("lastPeriodType"),
        "goalies": parse_goalies(raw),
        "starters": starter_goalies(raw),
        "special_teams": parse_special_teams(rr),
    }
