from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import requests

import freshness
import nhl_predict as np


def test_load_moneypuck_uses_cache_on_403(tmp_path, monkeypatch):
    cache = tmp_path / "mp_team_games_2025.csv"
    cache.write_text(
        "season,team,opposingTeam,home_or_away,gameDate,gameId,situation,playoffGame,"
        "xGoalsFor,xGoalsAgainst,scoreVenueAdjustedxGoalsFor,scoreVenueAdjustedxGoalsAgainst,"
        "goalsFor,goalsAgainst,shotsOnGoalFor,shotsOnGoalAgainst\n"
        "2025,WSH,PHI,H,20250331,2025020001,5on5,0,3,2,3,2,3,2,30,28\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(np, "DATA_DIR", tmp_path)

    def boom(*_a, **_k):
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.HTTPError("403")
        raise requests.HTTPError("403", response=resp)

    monkeypatch.setattr(np, "_download_mp_team_games", boom)
    df = np.load_moneypuck_season(2025, refresh=True)
    assert len(df) == 1
    assert df.iloc[0]["team"] == "WSH"


def test_load_moneypuck_empty_when_403_and_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(np, "DATA_DIR", tmp_path)

    def boom(*_a, **_k):
        raise requests.HTTPError("403 Client Error")

    monkeypatch.setattr(np, "_download_mp_team_games", boom)
    df = np.load_moneypuck_season(2026, refresh=True)
    assert df.empty
    assert "team" in df.columns
    assert not (tmp_path / "mp_team_games_2026.csv").exists()


def test_load_moneypuck_empty_season_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(np, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        np,
        "_download_mp_team_games",
        lambda _season: pd.DataFrame(columns=np.MP_USECOLS),
    )
    df = np.load_moneypuck_season(2026)
    assert df.empty
    assert not (tmp_path / "mp_team_games_2026.csv").exists()


def test_download_uses_team_files_when_bulk_is_blocked(monkeypatch):
    csv = (
        "team,opposingTeam,home_or_away,gameDate,gameId,situation,position,"
        "xGoalsFor,xGoalsAgainst,scoreVenueAdjustedxGoalsFor,scoreVenueAdjustedxGoalsAgainst,"
        "goalsFor,goalsAgainst,shotsOnGoalFor,shotsOnGoalAgainst\n"
        "MIN,NSH,A,20261001,2026020012,5on5,Team Level,2.082,0.913,2.2,0.9,2,1,20,13\n"
        "MIN,NSH,A,20261001,2026020012,4on5,Team Level,0.1,0.5,0.1,0.5,0,1,2,5\n"
    )

    def fake_get(url, **_kwargs):
        response = MagicMock()
        if "all_teams.csv" in url:
            raise requests.HTTPError("403 Client Error")
        response.status_code = 200
        response.text = csv
        response.raise_for_status.return_value = None
        return response

    monkeypatch.setattr(np.requests, "get", fake_get)
    monkeypatch.setattr(np, "MP_TEAMS", ("MIN",))
    df = np._download_mp_team_games(2026)
    assert list(df["team"]) == ["MIN"]
    assert list(df["situation"]) == ["5on5"]
    assert int(df.iloc[0]["season"]) == 2026
    assert int(df.iloc[0]["playoffGame"]) == 0
    assert set(df.columns) == set(np.MP_USECOLS)


def test_mp_cache_is_fresh_only_through_the_day_before_the_slate(tmp_path, monkeypatch):
    monkeypatch.setattr(np, "DATA_DIR", tmp_path)
    cache = tmp_path / "mp_team_games_2026.csv"
    cache.write_text("gameDate\n20261018\n", encoding="utf-8")
    assert np._mp_cache_is_fresh(2026, date(2026, 10, 25)) is False
    cache.write_text("gameDate\n20261024\n", encoding="utf-8")
    assert np._mp_cache_is_fresh(2026, date(2026, 10, 25)) is True


def test_live_slate_is_today_and_yesterday():
    today = date(2026, 10, 25)
    assert np._live_slate(date(2026, 10, 25), today) is True
    assert np._live_slate(date(2026, 10, 24), today) is True
    assert np._live_slate(date(2026, 10, 23), today) is False


def test_load_context_refreshes_moneypuck_when_cache_is_behind(tmp_path, monkeypatch):
    cache = tmp_path / "mp_team_games_2026.csv"
    cache.write_text("gameDate\n20261018\n", encoding="utf-8")
    monkeypatch.setattr(np, "DATA_DIR", tmp_path)
    flags: list[tuple[int, bool]] = []
    en_flags: list[bool] = []

    monkeypatch.setattr(np, "_resolve_use_db", lambda *_a, **_k: False)
    monkeypatch.setattr(np, "get_standings", lambda *_a, **_k: {"WSH": {"gp": 10}})
    monkeypatch.setattr(np, "get_team_summary_asof", lambda *_a, **_k: {})

    def fake_mp(season, refresh=False):
        flags.append((season, refresh))
        return pd.DataFrame(columns=np.MP_USECOLS)

    def fake_en(*_a, refresh=False, **_k):
        en_flags.append(refresh)
        return pd.DataFrame(columns=np.EN_COLUMNS)

    monkeypatch.setattr(np, "load_moneypuck_season", fake_mp)
    monkeypatch.setattr(np, "load_en_goals", fake_en)
    monkeypatch.setattr(np, "get_priors", lambda *_a, **_k: {})
    monkeypatch.setattr(np, "_load_goalie_injury_ctx", lambda *_a, **_k: ({}, {}))
    monkeypatch.setattr(np, "teams_played_on", lambda *_a, **_k: set())

    np.load_context(date(2026, 10, 25), refresh=False, use_db=False)
    assert (2026, True) in flags
    assert (2025, False) in flags
    assert en_flags == [True]


def test_load_context_notes_stale_moneypuck_without_raising(tmp_path, monkeypatch):
    cache = tmp_path / "mp_team_games_2026.csv"
    cache.write_text("gameDate\n20261018\n", encoding="utf-8")
    monkeypatch.setattr(np, "DATA_DIR", tmp_path)
    monkeypatch.setattr(np, "_resolve_use_db", lambda *_a, **_k: False)
    monkeypatch.setattr(np, "get_standings", lambda *_a, **_k: {"WSH": {"gp": 10}})
    monkeypatch.setattr(np, "get_team_summary_asof", lambda *_a, **_k: {})
    monkeypatch.setattr(np, "load_moneypuck_season", lambda *_a, **_k: pd.DataFrame(columns=np.MP_USECOLS))
    monkeypatch.setattr(np, "load_en_goals", lambda *_a, **_k: pd.DataFrame(columns=np.EN_COLUMNS))
    monkeypatch.setattr(np, "get_priors", lambda *_a, **_k: {})
    monkeypatch.setattr(np, "_load_goalie_injury_ctx", lambda *_a, **_k: ({}, {}))
    monkeypatch.setattr(np, "teams_played_on", lambda *_a, **_k: set())

    freshness.arm()
    try:
        np.load_context(date(2026, 10, 25), refresh=False, use_db=False)
    finally:
        notes = freshness.drain()
    assert any("20261018" in item and "2026-10-24" in item for item in notes)


def test_load_en_goals_skips_download_when_season_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(np, "DATA_DIR", tmp_path)

    def boom(*_a, **_k):
        raise AssertionError("shots download should be skipped")

    monkeypatch.setattr(np.requests, "get", boom)
    en = np.load_en_goals(2026, pd.DataFrame(columns=np.MP_USECOLS))
    assert list(en.columns) == np.EN_COLUMNS
    assert en.empty
    assert np.league_en_asof(en, np.date(2026, 9, 29)) == np.LEAGUE_EN_DEFAULT
