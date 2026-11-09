#!/usr/bin/env python
"""CLI for NHL SQLite ETL store.

Usage (from repo root):
  python scripts/nhl_etl_cli.py init
  python scripts/nhl_etl_cli.py sync --date 2026-03-31
  python scripts/nhl_etl_cli.py sync --from 2026-03-01 --to 2026-03-31
  python scripts/nhl_etl_cli.py status
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nhl_store import (  # noqa: E402
    build_projections,
    db_path,
    get_projected_goalies,
    init_schema,
    load_injuries,
    sync_date,
    sync_range,
    table_counts,
)


def main() -> None:
    p = argparse.ArgumentParser(description="NHL SQLite ETL store")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create schema in data/nhl.db")

    sync_p = sub.add_parser("sync", help="load schedule/standings/boxscores from NHL API")
    sync_p.add_argument("--date", type=date.fromisoformat, help="single slate date")
    sync_p.add_argument("--from", dest="date_from", type=date.fromisoformat, help="range start")
    sync_p.add_argument("--to", dest="date_to", type=date.fromisoformat, help="range end")

    sub.add_parser("status", help="row counts per table")

    gk_p = sub.add_parser("goalies", help="show projected starters for a slate")
    gk_p.add_argument("--date", type=date.fromisoformat, required=True)

    args = p.parse_args()

    if args.cmd == "init":
        path = init_schema()
        print(f"[etl] schema ready -> {path}")
        return

    if args.cmd == "status":
        counts = table_counts()
        print(f"[etl] database: {db_path()}")
        for table, n in counts.items():
            print(f"  {table}: {n}")
        return

    if args.cmd == "sync":
        if args.date:
            stats = sync_date(args.date)
            print(f"[etl] synced {args.date}: {stats}")
            return
        if args.date_from and args.date_to:
            stats = sync_range(args.date_from, args.date_to)
            print(f"[etl] synced {args.date_from} .. {args.date_to}: {stats}")
            return
        sync_p.error("pass --date or --from and --to")

    if args.cmd == "goalies":
        load_injuries(args.date)
        build_projections(args.date, allow_landing=True)
        rows = get_projected_goalies(args.date)
        print(f"[goalies] {args.date}: {len(rows)} teams")
        for team, g in sorted(rows.items()):
            sv = g.get("sv_l10") if (g.get("starts_l10") or 0) >= 3 else g.get("sv_season")
            sv_s = f"{sv:.3f}" if sv is not None else "n/a"
            print(
                f"  {team}: {g.get('player_name')} ({g.get('source')}) "
                f"Sv% {sv_s} starts_l10={g.get('starts_l10')}"
            )
        return


if __name__ == "__main__":
    main()
