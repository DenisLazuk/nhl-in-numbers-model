"""Standings snapshots from api-web."""

from __future__ import annotations

import sys
from datetime import date, timedelta

import requests

import freshness

from .client import API_WEB, get_json


def add_motivation(standings: dict[str, dict]) -> None:
    """Playoff-race status per team: out / fading / safe / None (in the race)."""
    by_conf: dict[str, list[int]] = {}
    for st in standings.values():
        by_conf.setdefault(st["conference"], []).append(st["points"])
    for st in standings.values():
        pts_sorted = sorted(by_conf[st["conference"]], reverse=True)
        line = pts_sorted[7] if len(pts_sorted) >= 8 else 0
        ninth = pts_sorted[8] if len(pts_sorted) >= 9 else 0
        remaining = max(82 - st["gp"], 0)
        behind = line - st["points"]
        lead = st["points"] - ninth
        if st["clinch"] == "e" or behind > 2 * remaining:
            st["motiv"] = "out"
        elif behind > 1.2 * remaining:
            st["motiv"] = "fading"
        elif st["clinch"] in ("x", "y", "p", "z") or lead > 2 * remaining:
            st["motiv"] = "safe"
        else:
            st["motiv"] = None


def get_standings(asof: date) -> dict[str, dict]:
    """Standings after all games of `asof`, with playoff-race motivation."""
    url = f"{API_WEB}/standings/{asof.isoformat()}"
    # Yesterday can still be incomplete in a snapshot taken during the day.
    if asof >= date.today() - timedelta(days=1):
        try:
            d = get_json(url, use_cache=False)
        except requests.RequestException as exc:
            print(f"[data] standings {asof} refresh failed ({exc}); using cache", file=sys.stderr)
            freshness.note(f"таблица на {asof.isoformat()} не обновилась, взята из кэша")
            d = get_json(url)
    else:
        d = get_json(url)
    out = {}
    for row in d.get("standings", []):
        abbrev = row["teamAbbrev"]["default"]
        l10_gp = row.get("l10Wins", 0) + row.get("l10Losses", 0) + row.get("l10OtLosses", 0)
        l10_pts = 2 * row.get("l10Wins", 0) + row.get("l10OtLosses", 0)
        out[abbrev] = {
            "name": row["teamName"]["default"],
            "gp": row.get("gamesPlayed", 0),
            "points": row.get("points", 0),
            "conference": row.get("conferenceAbbrev", "?"),
            "clinch": row.get("clinchIndicator", ""),
            "record": f"{row.get('wins', 0)}-{row.get('losses', 0)}-{row.get('otLosses', 0)}",
            "point_pct": row.get("pointPctg", 0.5),
            "l10_record": (
                f"{row.get('l10Wins', 0)}-{row.get('l10Losses', 0)}-{row.get('l10OtLosses', 0)}"
            ),
            "l10_pts_pct": (l10_pts / (2 * l10_gp)) if l10_gp else 0.5,
        }
    add_motivation(out)
    return out
