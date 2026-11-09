"""NHL public API client for ETL and model features."""

from .boxscore import (
    fetch_boxscore,
    fetch_right_rail,
    get_game_boxscore,
    parse_goalies,
    parse_special_teams,
    starter_goalies,
)
from .client import API_CACHE_DIR, API_WEB, DATA_DIR, STATS_REST, UA, get_json
from .goalies import fetch_landing, get_goalie_summary, parse_starting_goalie
from .schedule import get_schedule, get_slate, teams_played_on
from .standings import add_motivation, get_standings
from .team_stats import get_team_summary, get_team_summary_asof

__all__ = [
    "API_CACHE_DIR",
    "API_WEB",
    "DATA_DIR",
    "STATS_REST",
    "UA",
    "add_motivation",
    "fetch_boxscore",
    "fetch_landing",
    "fetch_right_rail",
    "get_goalie_summary",
    "get_game_boxscore",
    "get_json",
    "get_schedule",
    "get_slate",
    "get_standings",
    "get_team_summary",
    "get_team_summary_asof",
    "parse_goalies",
    "parse_special_teams",
    "parse_starting_goalie",
    "starter_goalies",
    "teams_played_on",
]
