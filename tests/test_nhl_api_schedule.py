from datetime import date, timedelta

import requests

import freshness
from nhl_api.schedule import get_schedule, rolling_form
from nhl_api.team_stats import get_team_summary_asof


def test_get_schedule_parses_scoreboard(monkeypatch):
    payload = {
        "games": [
            {
                "id": 2025020001,
                "season": 20252026,
                "gameType": 2,
                "homeTeam": {"abbrev": "TOR", "score": 3},
                "awayTeam": {"abbrev": "MTL", "score": 2},
                "gameOutcome": {"lastPeriodType": "REG"},
                "gameState": "OFF",
                "startTimeUTC": "2026-03-31T23:00:00Z",
                "venue": {"default": "Scotiabank Arena"},
            },
            {
                "id": 2025020002,
                "season": 20252026,
                "gameType": 1,
                "homeTeam": {"abbrev": "WSH", "score": None},
                "awayTeam": {"abbrev": "PIT", "score": None},
                "gameState": "FUT",
                "startTimeUTC": "2026-03-31T23:30:00Z",
                "venue": {"default": "Capital One Arena"},
            },
        ]
    }
    monkeypatch.setattr("nhl_api.schedule.get_json", lambda url: payload)
    rows = get_schedule(date(2026, 3, 31))
    assert len(rows) == 1
    assert rows[0]["home"] == "TOR"
    assert rows[0]["away"] == "MTL"
    assert rows[0]["home_score"] == 3


def test_get_schedule_refetches_unfinished_cached_board(monkeypatch):
    stale = {
        "games": [
            {
                "id": 2026020002,
                "gameType": 2,
                "homeTeam": {"abbrev": "TOR", "score": None},
                "awayTeam": {"abbrev": "MTL", "score": None},
                "gameState": "FUT",
                "startTimeUTC": "2026-09-29T23:00:00Z",
            }
        ]
    }
    live = {
        "games": [
            {
                "id": 2026020002,
                "gameType": 2,
                "homeTeam": {"abbrev": "TOR", "score": 2},
                "awayTeam": {"abbrev": "MTL", "score": 3},
                "gameOutcome": {"lastPeriodType": "REG"},
                "gameState": "OFF",
                "startTimeUTC": "2026-09-29T23:00:00Z",
            }
        ]
    }
    calls: list[bool] = []

    def fake(_url, use_cache=True):
        calls.append(use_cache)
        return stale if use_cache else live

    monkeypatch.setattr("nhl_api.schedule.get_json", fake)
    rows = get_schedule(date(2026, 9, 29))
    assert calls == [True, False]
    assert rows[0]["home_score"] == 2
    assert rows[0]["away_score"] == 3
    assert rows[0]["state"] == "OFF"


def _club_game(gid: int, day: str, home: str, away: str, hs: int, aus: int, period: str = "REG"):
    return {
        "id": gid,
        "gameDate": day,
        "gameType": 2,
        "gameState": "OFF",
        "homeTeam": {"abbrev": home, "score": hs},
        "awayTeam": {"abbrev": away, "score": aus},
        "gameOutcome": {"lastPeriodType": period},
    }


def test_get_schedule_refetches_cached_tie(monkeypatch):
    stale = {
        "games": [
            {
                "id": 2026020052,
                "gameType": 2,
                "homeTeam": {"abbrev": "LAK", "score": 1},
                "awayTeam": {"abbrev": "FLA", "score": 1},
                "gameState": "OFF",
                "startTimeUTC": "2026-10-07T02:00:00Z",
            }
        ]
    }
    live = {
        "games": [
            {
                "id": 2026020052,
                "gameType": 2,
                "homeTeam": {"abbrev": "LAK", "score": 1},
                "awayTeam": {"abbrev": "FLA", "score": 2},
                "gameOutcome": {"lastPeriodType": "REG"},
                "gameState": "OFF",
                "startTimeUTC": "2026-10-07T02:00:00Z",
            }
        ]
    }
    calls: list[bool] = []

    def fake(_url, use_cache=True):
        calls.append(use_cache)
        return stale if use_cache else live

    monkeypatch.setattr("nhl_api.schedule.get_json", fake)
    rows = get_schedule(date(2026, 10, 6))
    assert calls == [True, False]
    assert rows[0]["away_score"] == 2
    assert rows[0]["home_score"] == 1


def test_rolling_form_uses_previous_season_plus_new_game(monkeypatch):
    prev = [_club_game(10 + i, f"2026-04-{i:02d}", "NYI", "BOS", 3, 2) for i in range(1, 10)]
    prev.append(_club_game(20, "2026-04-10", "NYI", "TOR", 1, 2, "OT"))
    current = [_club_game(30, "2026-09-29", "NYI", "PIT", 4, 1)]

    def fake(url, use_cache=True):
        if "20262027" in url:
            return {"games": current}
        if "20252026" in url:
            return {"games": prev}
        raise AssertionError(url)

    monkeypatch.setattr("nhl_api.schedule.get_json", fake)
    record, pts = rolling_form("NYI", date(2026, 10, 1))
    assert record == "9-0-1"
    assert pts == 0.95


def test_rolling_form_skips_tonight_and_preseason(monkeypatch):
    games = [
        _club_game(1, "2026-09-20", "TOR", "MTL", 2, 1),
        {
            "id": 2,
            "gameDate": "2026-09-25",
            "gameType": 1,
            "gameState": "OFF",
            "homeTeam": {"abbrev": "TOR", "score": 5},
            "awayTeam": {"abbrev": "OTT", "score": 0},
        },
        _club_game(3, "2026-10-01", "TOR", "NYI", 3, 0),
    ]

    def fake(url, use_cache=True):
        if "20262027" in url:
            return {"games": games}
        return {"games": []}

    monkeypatch.setattr("nhl_api.schedule.get_json", fake)
    record, pts = rolling_form("TOR", date(2026, 10, 1))
    assert record == "1-0-0"
    assert pts == 1.0


def test_rolling_form_refetches_when_current_season_cache_is_behind(monkeypatch):
    stale = [_club_game(1, "2026-10-18", "FLA", "CHI", 4, 1)]
    live = stale + [
        _club_game(2, "2026-10-21", "CHI", "FLA", 3, 1),
        _club_game(3, "2026-10-22", "TBL", "FLA", 2, 1),
    ]
    calls: list[tuple[str, bool]] = []

    def fake(url, use_cache=True):
        calls.append((url, use_cache))
        if "20252026" in url:
            return {"games": []}
        return {"games": stale if use_cache else live}

    monkeypatch.setattr("nhl_api.schedule.get_json", fake)
    record, pts = rolling_form("FLA", date(2026, 10, 25))
    assert record == "1-2-0"
    assert pts == 1 / 3
    current = [use_cache for url, use_cache in calls if "20262027" in url]
    previous = [use_cache for url, use_cache in calls if "20252026" in url]
    assert current == [True, False]
    assert previous == [True]


def test_rolling_form_keeps_fresh_current_season_cache(monkeypatch):
    fresh = [_club_game(1, "2026-10-24", "FLA", "CHI", 2, 1)]
    calls: list[tuple[str, bool]] = []

    def fake(url, use_cache=True):
        calls.append((url, use_cache))
        if "20262027" in url:
            return {"games": fresh}
        return {"games": []}

    monkeypatch.setattr("nhl_api.schedule.get_json", fake)
    record, _pts = rolling_form("FLA", date(2026, 10, 25))
    assert record == "1-0-0"
    current = [use_cache for url, use_cache in calls if "20262027" in url]
    assert current == [True]


def test_rolling_form_keeps_cache_when_refresh_fails(monkeypatch):
    stale = [_club_game(1, "2026-10-18", "FLA", "CHI", 4, 1)]

    def fake(url, use_cache=True):
        if "20252026" in url:
            return {"games": []}
        if not use_cache:
            raise requests.HTTPError("403")
        return {"games": stale}

    monkeypatch.setattr("nhl_api.schedule.get_json", fake)
    freshness.arm()
    try:
        record, _pts = rolling_form("FLA", date(2026, 10, 25))
    finally:
        notes = freshness.drain()
    assert record == "1-0-0"
    assert notes == ["расписание FLA 2026 не обновилось, форма взята из кэша"]


def test_team_summary_asof_starts_at_current_season(monkeypatch):
    seen: dict = {}

    def fake(date_from, date_to, standings, use_cache=True):
        seen["from"] = date_from
        seen["to"] = date_to
        seen["cache"] = use_cache
        return {}

    monkeypatch.setattr("nhl_api.team_stats.get_team_summary", fake)
    get_team_summary_asof(date(2026, 10, 25), {})
    assert seen["from"] == "2026-09-01"
    assert seen["to"] == "2026-10-24"
    assert seen["cache"] is (date(2026, 10, 24) < date.today() - timedelta(days=1))

    get_team_summary_asof(date(2026, 3, 31), {})
    assert seen["from"] == "2025-09-01"
    assert seen["to"] == "2026-03-30"
    assert seen["cache"] is (date(2026, 3, 30) < date.today() - timedelta(days=1))
