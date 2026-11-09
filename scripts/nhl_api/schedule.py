"""Schedule and scores from api-web NHL scoreboard."""

from __future__ import annotations

import sys
from datetime import date, timedelta

import requests

import freshness

from .client import API_WEB, get_json


def _parse_game(g: dict) -> dict:
    home = g["homeTeam"]
    away = g["awayTeam"]
    return {
        "game_id": g["id"],
        "season": g.get("season"),
        "game_type": g.get("gameType", 2),
        "home": home["abbrev"],
        "away": away["abbrev"],
        "home_score": home.get("score"),
        "away_score": away.get("score"),
        "outcome": (g.get("gameOutcome") or {}).get("lastPeriodType"),
        "state": g.get("gameState"),
        "start_time_utc": g.get("startTimeUTC"),
        "venue": (g.get("venue") or {}).get("default"),
    }


_SETTLED = frozenset({"OFF", "FINAL", "OFFICIAL"})


def _scheduled(payload: dict, game_type: int) -> list[dict]:
    games = []
    for raw in payload.get("games", []):
        if raw.get("gameType", 2) != game_type:
            continue
        games.append(_parse_game(raw))
    return games


def _board_is_stale(games: list[dict]) -> bool:
    """Кэш табло устарел, если матч ещё идёт или «финал» записан вничью.

    Регулярка НХЛ не заканчивается равным счётом. Такой снимок — середина матча.
    """
    for game in games:
        state = str(game.get("state") or "").upper()
        if state not in _SETTLED:
            return True
        home_score = game.get("home_score")
        away_score = game.get("away_score")
        if home_score is not None and away_score is not None and home_score == away_score:
            return True
    return False


def get_schedule(slate_date: date, *, game_type: int = 2) -> list[dict]:
    """Regular-season games on the NHL calendar date (api-web scoreboard).

    A cached board that still has an unfinished game, or a settled tie, is
    fetched again. Final days stay in the file cache.
    """
    url = f"{API_WEB}/score/{slate_date.isoformat()}"
    games = _scheduled(get_json(url), game_type)
    if not _board_is_stale(games):
        return games
    return _scheduled(get_json(url, use_cache=False), game_type)


def get_slate(slate_date: date) -> list[dict]:
    """Backward-compatible alias: schedule rows without extra metadata keys."""
    return [
        {k: v for k, v in g.items()
         if k in ("home", "away", "home_score", "away_score", "outcome", "state")}
        for g in get_schedule(slate_date)
    ]


def teams_played_on(d: date) -> set[str]:
    games = get_schedule(d)
    return {g["home"] for g in games} | {g["away"] for g in games}


def nhl_season_id(season: int) -> int:
    return season * 10000 + season + 1


def _game_day(raw: dict) -> date | None:
    value = str(raw.get("gameDate") or "")[:10]
    if len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _latest_settled(payload: dict | None) -> date | None:
    latest: date | None = None
    for raw in (payload or {}).get("games") or []:
        if raw.get("gameType", 2) != 2:
            continue
        if str(raw.get("gameState") or "").upper() not in _SETTLED:
            continue
        day = _game_day(raw)
        if day is not None and (latest is None or day > latest):
            latest = day
    return latest


def _schedule_covers(payload: dict | None, before: date) -> bool:
    """True when the cached season already includes a settled game on the eve of `before`."""
    latest = _latest_settled(payload)
    return latest is not None and latest >= before - timedelta(days=1)


def _club_results(abbrev: str, season: int, *, before: date | None = None) -> list[dict]:
    url = f"{API_WEB}/club-schedule-season/{abbrev}/{nhl_season_id(season)}"
    payload = get_json(url)
    if before is not None and not _schedule_covers(payload, before):
        try:
            payload = get_json(url, use_cache=False)
        except requests.RequestException as exc:
            print(
                f"[data] club schedule {abbrev} {season} refresh failed ({exc}); using cache",
                file=sys.stderr,
            )
            freshness.note(
                f"расписание {abbrev} {season} не обновилось, форма взята из кэша"
            )
    rows: list[dict] = []
    for raw in (payload or {}).get("games") or []:
        if raw.get("gameType", 2) != 2:
            continue
        if str(raw.get("gameState") or "").upper() not in _SETTLED:
            continue
        day = _game_day(raw)
        if day is None:
            continue
        home = (raw.get("homeTeam") or {}).get("abbrev")
        away = (raw.get("awayTeam") or {}).get("abbrev")
        home_score = (raw.get("homeTeam") or {}).get("score")
        away_score = (raw.get("awayTeam") or {}).get("score")
        if home is None or away is None or home_score is None or away_score is None:
            continue
        rows.append(
            {
                "game_id": raw.get("id") or 0,
                "date": day,
                "home": home,
                "away": away,
                "home_score": int(home_score),
                "away_score": int(away_score),
                "outcome": (raw.get("gameOutcome") or {}).get("lastPeriodType"),
            }
        )
    return rows


def rolling_form(abbrev: str, before: date, n: int = 10) -> tuple[str, float]:
    """W-L-OTL and point% over the last n regular-season games, across seasons."""
    season = before.year - 1 if before.month < 9 else before.year
    collected: list[dict] = []
    for year in (season, season - 1):
        # The season that contains `before` is still growing; the previous one is finished.
        collected.extend(_club_results(abbrev, year, before=before if year == season else None))
        done = [row for row in collected if row["date"] < before]
        if len(done) >= n:
            break
    recent = sorted(
        (row for row in collected if row["date"] < before),
        key=lambda row: (row["date"], row["game_id"]),
    )[-n:]
    wins = losses = otl = 0
    for row in recent:
        team_score = row["home_score"] if row["home"] == abbrev else row["away_score"]
        opp_score = row["away_score"] if row["home"] == abbrev else row["home_score"]
        if team_score > opp_score:
            wins += 1
        elif str(row.get("outcome") or "").upper() in {"OT", "SO"}:
            otl += 1
        else:
            losses += 1
    games = wins + losses + otl
    pts_pct = (2 * wins + otl) / (2 * games) if games else 0.5
    return f"{wins}-{losses}-{otl}", pts_pct
