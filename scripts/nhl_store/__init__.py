"""SQLite persistence for NHL ETL data."""

from .db import (
    DATA_DIR,
    DEFAULT_DB_PATH,
    connect,
    db_exists,
    db_path,
    init_schema,
    table_counts,
)
from .goalies import build_projections, load_injuries
from .loaders import sync_date, sync_range
from .readers import (
    get_injuries,
    get_projected_goalies,
    get_slate,
    get_standings,
    get_team_summary,
    has_team_form,
    teams_played_on,
)

__all__ = [
    "DATA_DIR",
    "DEFAULT_DB_PATH",
    "build_projections",
    "connect",
    "db_exists",
    "db_path",
    "get_injuries",
    "get_projected_goalies",
    "get_slate",
    "get_standings",
    "get_team_summary",
    "has_team_form",
    "init_schema",
    "load_injuries",
    "sync_date",
    "sync_range",
    "table_counts",
    "teams_played_on",
]
