"""SQLite connection and schema bootstrap."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_DB_PATH = DATA_DIR / "nhl.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Future: DATABASE_URL=postgresql://... on VPS
DATABASE_URL = os.environ.get("DATABASE_URL")


def db_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    if DATABASE_URL and DATABASE_URL.startswith("sqlite:///"):
        return Path(DATABASE_URL.removeprefix("sqlite:///"))
    return DEFAULT_DB_PATH


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(path: Path | None = None) -> Path:
    """Create tables if missing. Returns path to the database file."""
    p = db_path(path)
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect(p) as conn:
        conn.executescript(sql)
        conn.commit()
    return p


def table_counts(path: Path | None = None) -> dict[str, int]:
    tables = ("teams", "games", "players", "goalies", "team_form", "projected_goalies", "injury_flags")
    out: dict[str, int] = {}
    with connect(path) as conn:
        for t in tables:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()
            out[t] = int(row["n"])
    return out


def db_exists(path: Path | None = None) -> bool:
    return db_path(path).exists()
