#!/usr/bin/env python
"""Full-season backtest of the NHL predictor with monthly diagnostics.

For every date in the range, predicts the slate using only data available
before that date, grades against actual results, and reports metrics
(accuracy, Brier, total-goals bias/MAE) by month and by confidence bucket.

Usage:
  python scripts/backtest.py                       # 2025-26 season
  python scripts/backtest.py --start 2025-10-01 --end 2026-04-30
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import pandas as pd

from nhl_predict import (
    DATA_DIR,
    get_slate,
    load_context,
    predict_game,
    season_for,
)


def run_backtest(start: date, end: date, *, use_db: bool | None = None) -> pd.DataFrame:
    season = season_for(start)
    print(f"[backtest] season {season}, dates {start} .. {end}", file=sys.stderr)
    if use_db:
        try:
            from nhl_store import db_exists, sync_range

            if not db_exists():
                print("[backtest] initializing SQLite store", file=sys.stderr)
            sync_range(start, end)
        except ImportError:
            print("[backtest] nhl_store unavailable, falling back to API", file=sys.stderr)
            use_db = False

    mp_ctx = load_context(start, use_db=use_db)
    mp = mp_ctx["mp"]
    en = mp_ctx["en"]
    priors = mp_ctx["priors"]

    rows = []
    d = start
    while d <= end:
        slate = [g for g in get_slate(d, use_db=use_db)
                 if g["home_score"] is not None and g["state"] in ("OFF", "FINAL")]
        if slate:
            ctx = load_context(d, use_db=use_db, allow_landing=False)
            ctx["mp"] = mp
            ctx["en"] = en
            ctx["priors"] = priors
            for g in slate:
                r = predict_game(ctx, g["home"], g["away"])
                pred = r["pred"]
                winner = g["home"] if g["home_score"] > g["away_score"] else g["away"]
                p_win = pred["p_home"] if winner == g["home"] else 1 - pred["p_home"]
                rows.append({
                    "date": d.isoformat(),
                    "month": d.strftime("%Y-%m"),
                    "home": g["home"], "away": g["away"],
                    "home_score": g["home_score"], "away_score": g["away_score"],
                    "p_home": round(pred["p_home"], 4),
                    "lean": r["lean"],
                    "confidence": pred["confidence"],
                    "total_pred": round(pred["total"], 2),
                    "total_fact": g["home_score"] + g["away_score"],
                    "hit": int(winner == r["lean"]),
                    "brier": round((1 - p_win) ** 2, 4),
                    "rest_home": r["h"].rest_days,
                    "rest_away": r["a"].rest_days,
                })
            print(f"[backtest] {d}: {len(slate)} games "
                  f"(total {len(rows)})", file=sys.stderr)
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def report(df: pd.DataFrame) -> None:
    df = df.copy()
    df["total_err"] = df["total_fact"] - df["total_pred"]

    def agg(group: pd.DataFrame) -> pd.Series:
        return pd.Series({
            "games": len(group),
            "accuracy": group["hit"].mean(),
            "brier": group["brier"].mean(),
            "avg_fact_total": group["total_fact"].mean(),
            "avg_pred_total": group["total_pred"].mean(),
            "total_bias": group["total_err"].mean(),
            "total_mae": group["total_err"].abs().mean(),
        })

    pd.set_option("display.width", 120)
    pd.set_option("display.float_format", lambda v: f"{v:.3f}")

    print("\n=== BY MONTH ===")
    print(df.groupby("month").apply(agg, include_groups=False))

    print("\n=== BY CONFIDENCE ===")
    order = {"high": 0, "medium": 1, "low": 2}
    by_conf = df.groupby("confidence").apply(agg, include_groups=False)
    print(by_conf.loc[sorted(by_conf.index, key=order.get)])

    print("\n=== GAMES AFTER LONG LAYOFF (either team rested 8+ days) ===")
    layoff = df[(df["rest_home"] >= 8) | (df["rest_away"] >= 8)]
    if len(layoff):
        print(agg(layoff).to_frame("layoff_games").T)
    else:
        print("none in range")

    print("\n=== CALIBRATION (favorite probability buckets) ===")
    df["p_fav"] = df["p_home"].where(df["p_home"] >= 0.5, 1 - df["p_home"])
    df["bucket"] = pd.cut(df["p_fav"], [0.5, 0.55, 0.6, 0.65, 0.7, 1.0])
    cal = df.groupby("bucket", observed=True).agg(
        games=("hit", "size"), predicted=("p_fav", "mean"), actual=("hit", "mean"))
    print(cal)

    print("\n=== SEASON TOTAL ===")
    print(f"games: {len(df)} | accuracy: {df['hit'].mean():.1%} | "
          f"Brier: {df['brier'].mean():.3f} | "
          f"total bias: {df['total_err'].mean():+.2f} | "
          f"total MAE: {df['total_err'].abs().mean():.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Full-season backtest")
    ap.add_argument("--start", default="2025-10-01")
    ap.add_argument("--end", default="2026-04-30")
    ap.add_argument("--out", default=None, help="CSV path for per-game results")
    ap.add_argument("--use-db", action="store_true", help="sync/read slate and team stats from SQLite")
    ap.add_argument("--no-db", action="store_true", help="force NHL API instead of SQLite")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    use_db = False if args.no_db else (True if args.use_db else None)
    df = run_backtest(start, end, use_db=use_db)
    if df.empty:
        print("no graded games in range", file=sys.stderr)
        return
    out = args.out or (DATA_DIR / f"backtest_{season_for(start)}.csv")
    df.to_csv(out, index=False)
    print(f"[backtest] per-game results -> {out}", file=sys.stderr)
    report(df)


if __name__ == "__main__":
    main()
