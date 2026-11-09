#!/usr/bin/env python
"""Smoke test for the NHL API module.

Usage (from repo root):
  python scripts/nhl_api_cli.py --date 2026-03-31
  python scripts/nhl_api_cli.py --game-id 2025021178
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# Allow `python scripts/nhl_api_cli.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nhl_api import (  # noqa: E402
    get_game_boxscore,
    get_schedule,
    get_standings,
    get_team_summary_asof,
)


def main() -> None:
    p = argparse.ArgumentParser(description="NHL API module smoke test")
    p.add_argument("--date", type=date.fromisoformat, help="Slate date (YYYY-MM-DD)")
    p.add_argument("--game-id", type=int, help="Single game boxscore")
    args = p.parse_args()
    if not args.date and not args.game_id:
        p.error("pass --date and/or --game-id")

    if args.date:
        games = get_schedule(args.date)
        print(f"[schedule] {args.date}: {len(games)} games")
        for g in games:
            print(
                f"  {g['away']} @ {g['home']}  "
                f"{g.get('away_score')}-{g.get('home_score')}  "
                f"({g['state']}) id={g['game_id']}"
            )
        asof = args.date.fromordinal(args.date.toordinal() - 1)
        standings = get_standings(asof)
        summary = get_team_summary_asof(args.date, standings) if standings else {}
        if summary:
            sample = next(iter(summary))
            st = summary[sample]
            print(
                f"[team_stats] sample {sample}: "
                f"PP {st['pp']:.1%} PK {st['pk']:.1%} Sv {st['sv']:.3f}"
            )

    if args.game_id:
        box = get_game_boxscore(args.game_id)
        print(f"[boxscore] {box['away']} @ {box['home']}  {box['away_score']}-{box['home_score']}")
        for side in ("home", "away"):
            starter = box["starters"].get(side)
            if starter:
                sv = starter["save_pct"]
                sv_s = f"{sv:.3f}" if sv is not None else "n/a"
                print(f"  {side} starter: {starter['name']}  Sv% {sv_s}")
        st = box["special_teams"]
        print(
            f"  PP home {st['home']['pp_goals']}/{st['home']['pp_opportunities']} "
            f"({st['home']['pp_pct']:.1%})  "
            f"away {st['away']['pp_goals']}/{st['away']['pp_opportunities']} "
            f"({st['away']['pp_pct']:.1%})"
        )
        if args.game_id and not args.date:
            print(json.dumps(box, indent=2, default=str)[:1500])


if __name__ == "__main__":
    main()
