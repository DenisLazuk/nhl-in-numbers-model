"""Season team aggregates (GF/GA, PP%, PK%, shots) from stats REST."""

from __future__ import annotations

import difflib
import sys
from datetime import date, timedelta

import requests

import freshness

from .client import STATS_REST, get_json

NAME_ALIASES = {"Utah Hockey Club": "UTA", "Utah Mammoth": "UTA"}


def _map_franchise(fname: str, name_to_abbrev: dict[str, str]) -> str | None:
    if fname in name_to_abbrev:
        return name_to_abbrev[fname]
    if fname in NAME_ALIASES:
        return NAME_ALIASES[fname]
    match = difflib.get_close_matches(fname, list(name_to_abbrev), n=1, cutoff=0.4)
    return name_to_abbrev[match[0]] if match else None


def get_team_summary(
    date_from: str,
    date_to: str,
    standings: dict[str, dict],
    *,
    use_cache: bool = True,
) -> dict[str, dict]:
    """Team summary over regular-season games in [date_from, date_to]."""
    params = {
        "isAggregate": "true",
        "isGame": "true",
        "limit": "-1",
        "cayenneExp": (
            f"gameDate<='{date_to} 23:59:59' and gameDate>='{date_from}' and gameTypeId=2"
        ),
    }
    url = f"{STATS_REST}/team/summary"
    try:
        d = get_json(url, params=params, use_cache=use_cache)
    except requests.RequestException as exc:
        if use_cache:
            raise
        print(f"[data] team summary refresh failed ({exc}); using cache", file=sys.stderr)
        freshness.note(f"статистика команд по {date_to} не обновилась, взята из кэша")
        d = get_json(url, params=params)
    name_to_abbrev = {v["name"]: k for k, v in standings.items()}
    out = {}
    for row in d.get("data", []):
        abbrev = _map_franchise(row.get("franchiseName", ""), name_to_abbrev)
        if abbrev is None:
            continue
        sa_pg = row.get("shotsAgainstPerGame") or 0.0
        ga_pg = row.get("goalsAgainstPerGame") or 0.0
        out[abbrev] = {
            "gp": row.get("gamesPlayed", 0),
            "gf_pg": row.get("goalsForPerGame", 0.0),
            "ga_pg": ga_pg,
            "sf_pg": row.get("shotsForPerGame", 0.0),
            "sa_pg": sa_pg,
            "pp": row.get("powerPlayPct", 0.0),
            "pk": row.get("penaltyKillPct", 0.0),
            "fow": row.get("faceoffWinPct", 0.0),
            "sv": (1.0 - ga_pg / sa_pg) if sa_pg else 0.9,
        }
    return out


def _season_start(slate_date: date) -> str:
    """1 September of the NHL season that contains the slate (not the previous calendar year)."""
    year = slate_date.year if slate_date.month >= 9 else slate_date.year - 1
    return f"{year}-09-01"


def get_team_summary_asof(slate_date: date, standings: dict[str, dict]) -> dict[str, dict]:
    cutoff_date = slate_date - timedelta(days=1)
    live = cutoff_date >= date.today() - timedelta(days=1)
    return get_team_summary(
        _season_start(slate_date),
        cutoff_date.isoformat(),
        standings,
        use_cache=not live,
    )
