#!/usr/bin/env python
"""NHL game predictor v0.1 (NHL Analytics App).

Collects team stats strictly BEFORE the slate date (as-of snapshot) from:
  * NHL stats REST API  - season aggregates (GF/GA, PP%, PK%, shots, FOW%)
  * NHL api-web         - standings as-of date (record, last-10, playoff race), scores
  * MoneyPuck           - game-by-game team xG data + shot-level empty-net goals

v0.1 model changes vs v0:
  * empty-net goals excluded from scoring rates; expected EN goals added back
    to the total as a league as-of-date average
  * team ratings shrunk toward previous-season prior: w = GP / (GP + 20)
  * recency blend for totals: 70% season rates + 30% last-15 games
  * motivation flag from playoff race (eliminated / fading / clinched)

Usage:
  python scripts/nhl_predict.py --date 2026-03-31
  python scripts/nhl_predict.py --date 2026-03-31 --games BUF-NYI,WSH-PHI --compare
"""

from __future__ import annotations

import argparse
import io
import math
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

import freshness
from nhl_api import (
    DATA_DIR,
    get_slate as api_get_slate,
    get_standings as api_get_standings,
    get_team_summary,
    get_team_summary_asof as api_get_team_summary_asof,
    teams_played_on as api_teams_played_on,
)
from nhl_api.schedule import rolling_form

MP_GBG_URL = "https://moneypuck.com/moneypuck/playerData/careers/gameByGame/all_teams.csv"
MP_TEAM_URL = (
    "https://moneypuck.com/moneypuck/playerData/teamGameByGame/{season}/regular/{team}.csv"
)
MP_SHOTS_URL = "https://moneypuck.com/moneypuck/playerData/shots/shots_{season}.zip"
MP_TEAMS = (
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET", "EDM", "FLA",
    "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT", "PHI", "PIT", "SEA", "SJS",
    "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WPG", "WSH",
)
MP_USECOLS = [
    "season", "team", "opposingTeam", "home_or_away", "gameDate", "gameId", "situation",
    "playoffGame", "xGoalsFor", "xGoalsAgainst",
    "scoreVenueAdjustedxGoalsFor", "scoreVenueAdjustedxGoalsAgainst",
    "goalsFor", "goalsAgainst", "shotsOnGoalFor", "shotsOnGoalAgainst",
]
# Old-style MoneyPuck codes -> standard abbrevs
MP_CODE_FIX = {"T.B": "TBL", "N.J": "NJD", "S.J": "SJS", "L.A": "LAK"}

# --- Model weights (logit scale) - hand-tuned v0.1, see METRICS.md ---
W_XG5V5 = 6.0        # season 5v5 xG% (score/venue adjusted, shrunk)
W_GDIFF = 0.35       # EN-adjusted goal differential per game (shrunk)
W_SPECIAL = 1.5      # special teams index (PP% + PK% - 1, shrunk)
W_SAVE = 8.0         # team save% (shrunk)
W_L10PTS = 0.8       # last-10 point%
W_L10XG = 2.0        # last-10 5v5 xG%
HOME_ADV = 0.18      # ~54.5% baseline for home team
B2B_PEN = 0.15       # back-to-back penalty
LAYOFF_DAYS = 8      # rest of this many days or more = long layoff (rust risk)
RUST_PEN = 0.08      # penalty for the first game after a long layoff
DELTA_SCALE = 0.30   # global logit temperature, fitted on Oct-Dec 2025,
                     # validated on Jan-Apr 2026 (Brier 0.250 -> 0.243)
K_SHRINK = 20        # rating = GP/(GP+K) * current + prior otherwise
MOTIV_PEN = {"out": -0.20, "fading": -0.10, "safe": -0.05, None: 0.0}
EN_PRIOR_ADJ = 0.15  # flat EN goals/game subtracted from prior-season GF/GA rates
LEAGUE_EN_DEFAULT = 0.30  # expected EN goals per game before enough season data
TOTAL_W_SEASON = 0.7      # season vs last-15 blend for scoring rates
TOTAL_W_GOALS = 0.65      # goals-based vs xG-based blend for the total

UA = {"User-Agent": "Mozilla/5.0 (NHL Analytics App v0.1)"}
MP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
    "Referer": "https://moneypuck.com/data.htm",
}


def _read_mp_team_cache(cache: Path) -> pd.DataFrame:
    df = pd.read_csv(cache)
    if "gameId" not in df.columns:
        raise ValueError("cache missing gameId")
    return df


def _download_mp_bulk(season: int) -> pd.DataFrame:
    print(f"[data] downloading MoneyPuck game-by-game data (season {season}) ...",
          file=sys.stderr)
    chunks = []
    with requests.get(MP_GBG_URL, headers=MP_HEADERS, timeout=600, stream=True) as r:
        r.raise_for_status()
        r.raw.decode_content = True  # server sends gzip
        for chunk in pd.read_csv(r.raw, usecols=MP_USECOLS, chunksize=200_000):
            part = chunk[(chunk["season"] == season)
                         & (chunk["situation"].isin(["all", "5on5"]))
                         & (chunk["playoffGame"] == 0)]
            if len(part):
                chunks.append(part)
    if not chunks:
        print(
            f"[data] MoneyPuck has no regular-season games for {season} yet",
            file=sys.stderr,
        )
        return pd.DataFrame(columns=MP_USECOLS)
    df = pd.concat(chunks, ignore_index=True)
    df["team"] = df["team"].replace(MP_CODE_FIX)
    return df


def _frame_from_team_csv(text: str, season: int) -> pd.DataFrame:
    frame = pd.read_csv(io.StringIO(text))
    if frame.empty:
        return pd.DataFrame(columns=MP_USECOLS)
    if "position" in frame.columns:
        frame = frame[frame["position"] == "Team Level"]
    if "situation" in frame.columns:
        frame = frame[frame["situation"].isin(["all", "5on5"])]
    frame = frame.copy()
    frame["season"] = season
    frame["playoffGame"] = 0
    for col in MP_USECOLS:
        if col not in frame.columns:
            frame[col] = pd.NA
    return frame.loc[:, MP_USECOLS]


def _download_mp_by_team(season: int) -> pd.DataFrame:
    """Per-team regular-season files. The bulk all_teams.csv is often behind Cloudflare."""
    print(f"[data] downloading MoneyPuck team files (season {season}) ...", file=sys.stderr)
    frames = []
    errors = 0
    for abbrev in MP_TEAMS:
        url = MP_TEAM_URL.format(season=season, team=abbrev)
        try:
            response = requests.get(url, headers=MP_HEADERS, timeout=60)
        except requests.RequestException:
            errors += 1
            continue
        if response.status_code == 404:
            continue
        try:
            response.raise_for_status()
        except requests.HTTPError:
            errors += 1
            if response.status_code == 403:
                break
            continue
        frame = _frame_from_team_csv(response.text, season)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        if errors:
            raise requests.HTTPError(f"MoneyPuck team files unavailable ({errors} errors)")
        return pd.DataFrame(columns=MP_USECOLS)
    df = pd.concat(frames, ignore_index=True)
    df["team"] = df["team"].replace(MP_CODE_FIX)
    return df


def _download_mp_team_games(season: int) -> pd.DataFrame:
    try:
        return _download_mp_bulk(season)
    except requests.RequestException as exc:
        print(
            f"[data] MoneyPuck bulk file failed ({exc}); using team files",
            file=sys.stderr,
        )
        return _download_mp_by_team(season)


def load_moneypuck_season(season: int, refresh: bool = False) -> pd.DataFrame:
    """Game-by-game team data for one season (cached locally after first download)."""
    DATA_DIR.mkdir(exist_ok=True)
    cache = DATA_DIR / f"mp_team_games_{season}.csv"
    if cache.exists() and not refresh:
        try:
            return _read_mp_team_cache(cache)
        except ValueError:
            print("[data] cache is from an older version, re-downloading", file=sys.stderr)
    try:
        df = _download_mp_team_games(season)
    except requests.RequestException as exc:
        if cache.exists():
            cached = _read_mp_team_cache(cache)
            latest = int(cached["gameDate"].max()) if not cached.empty else None
            print(
                f"[data] MoneyPuck download failed ({exc}); using cached {cache.name} "
                f"through {latest}",
                file=sys.stderr,
            )
            return cached
        print(
            f"[data] MoneyPuck download failed ({exc}) and no cache at {cache.name}; "
            "current-season xG will fall back to the prior season",
            file=sys.stderr,
        )
        return pd.DataFrame(columns=MP_USECOLS)
    if df.empty:
        print(
            f"[data] season {season} has no games yet; not caching "
            "(xG comes from the prior season until MoneyPuck publishes rows)",
            file=sys.stderr,
        )
        return df
    df.to_csv(cache, index=False)
    print(f"[data] cached {len(df)} rows -> {cache}", file=sys.stderr)
    return df


EN_COLUMNS = ["gameId", "team", "opposingTeam", "gameDate", "en_for", "en_against"]


def _empty_en_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=EN_COLUMNS)


def load_en_goals(season: int, mp: pd.DataFrame, refresh: bool = False) -> pd.DataFrame:
    """Empty-net goals per (gameId, team) with gameDate, from MoneyPuck shot data."""
    DATA_DIR.mkdir(exist_ok=True)
    cache = DATA_DIR / f"en_goals_{season}.csv"
    if mp.empty:
        print(
            f"[data] no MoneyPuck team games for season {season}; skip EN download",
            file=sys.stderr,
        )
        return _empty_en_frame()
    if cache.exists() and not refresh:
        return pd.read_csv(cache)
    print(f"[data] downloading MoneyPuck shot data (season {season}) ...", file=sys.stderr)
    try:
        r = requests.get(MP_SHOTS_URL.format(season=season), headers=MP_HEADERS, timeout=600)
        r.raise_for_status()
    except requests.RequestException as exc:
        if cache.exists():
            print(
                f"[data] MoneyPuck shots download failed ({exc}); using cached {cache.name}",
                file=sys.stderr,
            )
            return pd.read_csv(cache)
        raise RuntimeError(
            f"MoneyPuck shots download failed ({exc}) and no cache at {cache}. "
            f"Copy en_goals_{season}.csv to data/ on the server."
        ) from exc
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    with zf.open(zf.namelist()[0]) as f:
        shots = pd.read_csv(f, usecols=["game_id", "homeTeamCode", "awayTeamCode",
                                        "team", "goal", "shotOnEmptyNet", "isPlayoffGame"])
    en = shots[(shots["goal"] == 1) & (shots["shotOnEmptyNet"] == 1)
               & (shots["isPlayoffGame"] == 0)].copy()
    en["gameId"] = season * 1_000_000 + en["game_id"]
    en["team"] = en["homeTeamCode"].where(en["team"] == "HOME", en["awayTeamCode"])
    en_counts = en.groupby(["gameId", "team"]).size().rename("en_for").reset_index()

    base = mp[mp["situation"] == "all"][["gameId", "team", "opposingTeam", "gameDate"]].copy()
    base["opposingTeam"] = base["opposingTeam"].replace(MP_CODE_FIX)
    base = base.merge(en_counts, on=["gameId", "team"], how="left")
    base = base.merge(
        en_counts.rename(columns={"team": "opposingTeam", "en_for": "en_against"}),
        on=["gameId", "opposingTeam"], how="left")
    base[["en_for", "en_against"]] = base[["en_for", "en_against"]].fillna(0).astype(int)
    base.to_csv(cache, index=False)
    print(f"[data] cached EN goals: {int(base['en_for'].sum())} goals "
          f"in {base['gameId'].nunique()} games -> {cache}", file=sys.stderr)
    return base


def league_en_asof(en: pd.DataFrame, slate_date: date) -> float:
    """League-average EN goals per game (both teams) over games before the slate date."""
    cutoff = int(slate_date.strftime("%Y%m%d"))
    past = en[en["gameDate"] < cutoff]
    n_games = past["gameId"].nunique()
    if n_games < 50:
        return LEAGUE_EN_DEFAULT
    return past["en_for"].sum() / n_games


def mp_features(
    df: pd.DataFrame,
    en: pd.DataFrame,
    team: str,
    slate_date: date,
    prev_df: pd.DataFrame | None = None,
) -> dict:
    cutoff = int(slate_date.strftime("%Y%m%d"))
    t = df[(df["team"] == team) & (df["gameDate"] < cutoff)]
    ev = t[t["situation"] == "5on5"].sort_values("gameDate")
    al = t[t["situation"] == "all"].sort_values("gameDate")
    e = en[(en["team"] == team) & (en["gameDate"] < cutoff)]

    def xg_share(rows: pd.DataFrame) -> float:
        xf = rows["scoreVenueAdjustedxGoalsFor"].sum()
        xa = rows["scoreVenueAdjustedxGoalsAgainst"].sum()
        return xf / (xf + xa) if (xf + xa) else 0.5

    rolling_src = df
    if prev_df is not None and not prev_df.empty:
        rolling_src = pd.concat([prev_df, df], ignore_index=True)
    rolling = rolling_src[
        (rolling_src["team"] == team)
        & (rolling_src["situation"] == "5on5")
        & (rolling_src["gameDate"] < cutoff)
    ].sort_values("gameDate")
    l10 = rolling.tail(10)
    if ev.empty or al.empty:
        return {
            "xg5v5": 0.5,
            "xg5v5_l10": xg_share(l10) if len(l10) else 0.5,
            "xg5v5_l10_gp": int(len(l10)),
            "xgf_pg": 3.0,
            "xga_pg": 3.0,
            "gf_l15": 3.0,
            "ga_l15": 3.0,
            "en_for_pg": 0.0,
            "en_against_pg": 0.0,
            "mp_gp": 0,
            "rest_days": None,
        }
    last_game = datetime.strptime(str(int(al["gameDate"].max())), "%Y%m%d").date()

    al_en = al.merge(e[["gameId", "en_for", "en_against"]], on="gameId", how="left")
    al_en[["en_for", "en_against"]] = al_en[["en_for", "en_against"]].fillna(0)
    tail = al_en.tail(15)
    return {
        "xg5v5": xg_share(ev),
        "xg5v5_l10": xg_share(l10) if len(l10) else 0.5,
        "xg5v5_l10_gp": int(len(l10)),
        "xgf_pg": al["xGoalsFor"].sum() / len(al),
        "xga_pg": al["xGoalsAgainst"].sum() / len(al),
        "gf_l15": (tail["goalsFor"] - tail["en_for"]).mean(),
        "ga_l15": (tail["goalsAgainst"] - tail["en_against"]).mean(),
        "en_for_pg": e["en_for"].sum() / len(al),
        "en_against_pg": e["en_against"].sum() / len(al),
        "mp_gp": len(al),
        "rest_days": (slate_date - last_game).days,
    }


# --------------------------------------------------------------------- Priors

PRIOR_KEYS = ("xg5v5", "gf_pg", "ga_pg", "pp", "pk", "sv", "xgf_pg", "xga_pg")


def get_priors(prev_season: int, refresh: bool = False) -> dict[str, dict]:
    """Previous-season full-year team metrics used as shrinkage priors."""
    end = date(prev_season + 1, 4, 30)
    # walk back to a date with a full standings table (end of prev regular season)
    prev_standings: dict[str, dict] = {}
    for back in range(0, 30, 5):
        prev_standings = get_standings(end - timedelta(days=back))
        if len(prev_standings) >= 30:
            break
    summary = get_team_summary(f"{prev_season}-09-01", f"{prev_season + 1}-07-01",
                               prev_standings)
    mp = load_moneypuck_season(prev_season, refresh=refresh)
    priors = {}
    for abbrev, sm in summary.items():
        ev = mp[(mp["team"] == abbrev) & (mp["situation"] == "5on5")]
        al = mp[(mp["team"] == abbrev) & (mp["situation"] == "all")]
        xf = ev["scoreVenueAdjustedxGoalsFor"].sum()
        xa = ev["scoreVenueAdjustedxGoalsAgainst"].sum()
        priors[abbrev] = {
            "xg5v5": xf / (xf + xa) if (xf + xa) else 0.5,
            "gf_pg": sm["gf_pg"] - EN_PRIOR_ADJ,
            "ga_pg": sm["ga_pg"] - EN_PRIOR_ADJ,
            "pp": sm["pp"], "pk": sm["pk"], "sv": sm["sv"],
            "xgf_pg": al["xGoalsFor"].sum() / len(al) if len(al) else 3.0,
            "xga_pg": al["xGoalsAgainst"].sum() / len(al) if len(al) else 3.0,
        }
    league = {k: sum(p[k] for p in priors.values()) / len(priors) for k in PRIOR_KEYS}
    priors["__league__"] = league
    return priors


# ---------------------------------------------------------------------- Model

@dataclass
class TeamState:
    abbrev: str
    record: str
    l10_record: str
    l10_pts_pct: float
    gf_adj: float        # season GF/game excl. EN goals, shrunk to prior
    ga_adj: float
    gf_l15: float        # last-15 GF/game excl. EN goals (raw)
    ga_l15: float
    pp: float
    pk: float
    sv: float
    xg5v5: float
    xg5v5_l10: float
    xgf_pg: float
    xga_pg: float
    b2b: bool
    motiv: str | None
    gp: int
    rest_days: int | None
    goalie_name: str | None = None
    goalie_sv: float | None = None
    sv_source: str = "team"
    injury_impact: float = 0.0
    injury_note: str = ""

    @property
    def long_layoff(self) -> bool:
        return self.rest_days is not None and self.rest_days >= LAYOFF_DAYS


def _record_gp(record: str) -> int:
    try:
        return sum(int(part) for part in str(record).split("-"))
    except ValueError:
        return 0


def _form_window(abbrev: str, slate_date: date, standings_row: dict) -> tuple[str, float]:
    fallback = (standings_row.get("l10_record") or "0-0-0", float(standings_row.get("l10_pts_pct") or 0.5))
    try:
        record, pts_pct = rolling_form(abbrev, slate_date)
    except Exception:
        return fallback
    if _record_gp(record) < 5:
        return fallback
    return record, pts_pct


def build_state(abbrev: str, standings: dict, summary: dict, mp: pd.DataFrame,
                en: pd.DataFrame, priors: dict, slate_date: date,
                played_yesterday: set[str],
                goalie_info: dict | None = None,
                injury_info: dict | None = None,
                prev_mp: pd.DataFrame | None = None) -> TeamState:
    st = standings.get(abbrev) or {
        "record": "0-0-0", "l10_record": "0-0-0", "l10_pts_pct": 0.5, "motiv": None}
    prior = priors.get(abbrev, priors["__league__"])
    f = mp_features(mp, en, abbrev, slate_date, prev_df=prev_mp)
    l10_record, l10_pts_pct = _form_window(abbrev, slate_date, st)
    sm = summary.get(abbrev)
    gp = sm["gp"] if sm else 0
    w = gp / (gp + K_SHRINK)

    def shrink(current: float, key: str) -> float:
        return w * current + (1 - w) * prior[key]

    if sm:
        cur = {
            "gf_pg": sm["gf_pg"] - f["en_for_pg"],
            "ga_pg": sm["ga_pg"] - f["en_against_pg"],
            "pp": sm["pp"], "pk": sm["pk"], "sv": sm["sv"],
        }
    else:  # season not started for this team: pure prior
        cur = {k: prior[k] for k in ("gf_pg", "ga_pg", "pp", "pk", "sv")}

    gk = goalie_info or {}
    sv_source = "team"
    goalie_name = None
    goalie_sv = None
    if gk.get("effective_sv") is not None:
        cur["sv"] = gk["effective_sv"]
        sv_source = "goalie"
        goalie_name = gk.get("player_name")
        goalie_sv = gk.get("sv_l10") if (gk.get("starts_l10") or 0) >= 3 else gk.get("sv_season")

    inj = injury_info or {}

    return TeamState(
        abbrev=abbrev, record=st["record"], l10_record=l10_record,
        l10_pts_pct=l10_pts_pct,
        gf_adj=shrink(cur["gf_pg"], "gf_pg"), ga_adj=shrink(cur["ga_pg"], "ga_pg"),
        gf_l15=f["gf_l15"] if f["mp_gp"] else prior["gf_pg"],
        ga_l15=f["ga_l15"] if f["mp_gp"] else prior["ga_pg"],
        pp=shrink(cur["pp"], "pp"), pk=shrink(cur["pk"], "pk"),
        sv=shrink(cur["sv"], "sv"),
        xg5v5=shrink(f["xg5v5"] if f["mp_gp"] else prior["xg5v5"], "xg5v5"),
        xg5v5_l10=f["xg5v5_l10"] if f["xg5v5_l10_gp"] else prior["xg5v5"],
        xgf_pg=shrink(f["xgf_pg"] if f["mp_gp"] else prior["xgf_pg"], "xgf_pg"),
        xga_pg=shrink(f["xga_pg"] if f["mp_gp"] else prior["xga_pg"], "xga_pg"),
        b2b=abbrev in played_yesterday,
        motiv=st.get("motiv"),
        gp=gp,
        rest_days=f["rest_days"],
        goalie_name=goalie_name,
        goalie_sv=goalie_sv,
        sv_source=sv_source,
        injury_impact=float(inj.get("impact", 0.0)),
        injury_note=str(inj.get("note", "")),
    )


def predict(h: TeamState, a: TeamState, league_en: float) -> dict:
    contribs = {
        "xg5v5": W_XG5V5 * (h.xg5v5 - a.xg5v5),
        "gdiff": W_GDIFF * ((h.gf_adj - h.ga_adj) - (a.gf_adj - a.ga_adj)),
        "special": W_SPECIAL * ((h.pp + h.pk - 1.0) - (a.pp + a.pk - 1.0)),
        "save": W_SAVE * (h.sv - a.sv),
        "l10pts": W_L10PTS * (h.l10_pts_pct - a.l10_pts_pct),
        "l10xg": W_L10XG * (h.xg5v5_l10 - a.xg5v5_l10),
        "home": HOME_ADV,
        "b2b": -B2B_PEN * h.b2b + B2B_PEN * a.b2b,
        "rust": -RUST_PEN * h.long_layoff + RUST_PEN * a.long_layoff,
        "motiv": MOTIV_PEN[h.motiv] - MOTIV_PEN[a.motiv],
        "injury": h.injury_impact - a.injury_impact,
    }
    delta = DELTA_SCALE * sum(contribs.values())
    p_home = 1.0 / (1.0 + math.exp(-delta))

    h_off = TOTAL_W_SEASON * h.gf_adj + (1 - TOTAL_W_SEASON) * h.gf_l15
    h_def = TOTAL_W_SEASON * h.ga_adj + (1 - TOTAL_W_SEASON) * h.ga_l15
    a_off = TOTAL_W_SEASON * a.gf_adj + (1 - TOTAL_W_SEASON) * a.gf_l15
    a_def = TOTAL_W_SEASON * a.ga_adj + (1 - TOTAL_W_SEASON) * a.ga_l15
    goals_based = 0.5 * (h_off + a_def) + 0.5 * (a_off + h_def)
    xg_based = 0.5 * (h.xgf_pg + a.xga_pg) + 0.5 * (a.xgf_pg + h.xga_pg)
    total = (TOTAL_W_GOALS * goals_based + (1 - TOTAL_W_GOALS) * xg_based
             + league_en - 0.15 * (h.b2b + a.b2b))

    margin = abs(p_home - 0.5)
    confidence = "high" if margin >= 0.08 else "medium" if margin >= 0.03 else "low"
    if min(h.gp, a.gp) < 10:  # early season: never claim high confidence
        confidence = "low" if confidence == "high" else confidence
    if (h.long_layoff or a.long_layoff) and confidence == "high":
        confidence = "medium"  # first game after a long break is a risk game
    return {"p_home": p_home, "total": total, "confidence": confidence,
            "contribs": contribs}


MOTIV_TEXT = {"out": "out of playoff race", "fading": "fading from playoff race",
              "safe": "playoff spot locked"}


def why_factors(h: TeamState, a: TeamState, contribs: dict) -> list[str]:
    """Human-readable top factors, ordered by absolute contribution."""
    def side(c: float) -> TeamState:
        return h if c > 0 else a

    texts = {}
    c = contribs["xg5v5"]
    t, o = (h, a) if c > 0 else (a, h)
    texts["xg5v5"] = (f"{t.abbrev} stronger 5v5 xG "
                      f"({t.xg5v5:.1%} vs {o.xg5v5:.1%} season)")
    c = contribs["l10xg"]
    t, o = (h, a) if c > 0 else (a, h)
    texts["l10xg"] = (f"{t.abbrev} better 5v5 xG trend last 10 "
                      f"({t.xg5v5_l10:.1%} vs {o.xg5v5_l10:.1%})")
    c = contribs["special"]
    t = side(c)
    texts["special"] = (f"{t.abbrev} better special teams "
                        f"(PP {t.pp:.1%} / PK {t.pk:.1%})")
    c = contribs["save"]
    t, o = (h, a) if c > 0 else (a, h)
    if t.sv_source == "goalie" and t.goalie_name:
        sv_show = t.goalie_sv if t.goalie_sv is not None else t.sv
        texts["save"] = (
            f"{t.abbrev} projected starter {t.goalie_name} "
            f"(Sv% {sv_show:.3f})"
        )
    else:
        texts["save"] = (f"{t.abbrev} goaltending edge "
                         f"(team Sv% {t.sv:.3f} vs {o.sv:.3f})")
    c = contribs["l10pts"]
    t, o = (h, a) if c > 0 else (a, h)
    if min(_record_gp(h.l10_record), _record_gp(a.l10_record)) >= 5:
        texts["l10pts"] = f"{t.abbrev} hotter form ({t.l10_record} vs {o.l10_record} last 10)"
    c = contribs["gdiff"]
    t = side(c)
    texts["gdiff"] = (f"{t.abbrev} better EN-adjusted goal differential "
                      f"({t.gf_adj:.2f} GF / {t.ga_adj:.2f} GA per game)")

    ranked = sorted(
        (k for k in texts if abs(contribs[k]) > 0.02),
        key=lambda k: abs(contribs[k]), reverse=True)
    out = [texts[k] for k in ranked[:4]]
    for t in (h, a):
        if abs(t.injury_impact) >= 0.03:
            out.append(f"{t.abbrev} injury flag ({t.injury_note or 'manual'})")
        if t.motiv:
            out.append(f"{t.abbrev} {MOTIV_TEXT[t.motiv]}")
        if t.b2b:
            out.append(f"{t.abbrev} on back-to-back")
        if t.long_layoff:
            out.append(f"{t.abbrev} first game after a {t.rest_days}-day break")
    out.append(f"home ice {h.abbrev}")
    return out


# ----------------------------------------------------------------- Slate glue

def _resolve_use_db(slate_date: date, use_db: bool | None) -> bool:
    if use_db is False:
        return False
    if _live_slate(slate_date):
        return False
    try:
        from nhl_store import db_exists, has_team_form
    except ImportError:
        return False
    if not db_exists():
        return False
    if use_db is True:
        return True
    return has_team_form(slate_date)


def _ensure_db_snapshot(slate_date: date) -> None:
    from nhl_store import has_team_form, sync_date
    from nhl_store.readers import get_slate as store_get_slate

    if has_team_form(slate_date) and store_get_slate(slate_date):
        return
    print(f"[data] auto-sync SQLite snapshot for {slate_date}", file=sys.stderr)
    sync_date(slate_date)


def get_slate(slate_date: date, *, use_db: bool | None = None) -> list[dict]:
    if _resolve_use_db(slate_date, use_db):
        from nhl_store.readers import get_slate as store_get_slate

        _ensure_db_snapshot(slate_date)
        rows = store_get_slate(slate_date)
        if rows:
            return rows
    return api_get_slate(slate_date)


def get_standings(asof: date, *, slate_date: date | None = None, use_db: bool | None = None) -> dict[str, dict]:
    sd = slate_date or (asof + timedelta(days=1))
    if _resolve_use_db(sd, use_db):
        from nhl_store.readers import get_standings as store_get_standings

        _ensure_db_snapshot(sd)
        rows = store_get_standings(sd)
        if rows:
            return rows
    return api_get_standings(asof)


def get_team_summary_asof(
    slate_date: date,
    standings: dict[str, dict],
    *,
    use_db: bool | None = None,
) -> dict[str, dict]:
    if _resolve_use_db(slate_date, use_db):
        from nhl_store.readers import get_team_summary as store_get_team_summary

        _ensure_db_snapshot(slate_date)
        rows = store_get_team_summary(slate_date)
        if rows:
            return rows
    return api_get_team_summary_asof(slate_date, standings)


def teams_played_on(d: date, *, use_db: bool | None = None) -> set[str]:
    sd = d + timedelta(days=1)
    if _resolve_use_db(sd, use_db):
        from nhl_store.readers import teams_played_on as store_teams_played_on

        _ensure_db_snapshot(sd)
        teams = store_teams_played_on(d)
        if teams:
            return teams
    return api_teams_played_on(d)


def season_for(slate_date: date) -> int:
    return slate_date.year - 1 if slate_date.month < 9 else slate_date.year


def _mp_cache_max_date(season: int) -> int | None:
    cache = DATA_DIR / f"mp_team_games_{season}.csv"
    if not cache.is_file():
        return None
    try:
        frame = pd.read_csv(cache, usecols=["gameDate"])
    except (OSError, ValueError):
        return None
    if frame.empty:
        return None
    return int(frame["gameDate"].max())


def _mp_cache_is_fresh(season: int, slate_date: date) -> bool:
    """True when the file already includes games through the day before the slate."""
    latest = _mp_cache_max_date(season)
    if latest is None:
        return False
    need = int((slate_date - timedelta(days=1)).strftime("%Y%m%d"))
    return latest >= need


def _live_slate(slate_date: date, today: date | None = None) -> bool:
    """Today and yesterday are still moving; do not serve them from a morning snapshot."""
    today = today or date.today()
    return slate_date >= today - timedelta(days=1)


def _load_goalie_injury_ctx(
    slate_date: date,
    use_db: bool | None,
    *,
    allow_landing: bool = True,
) -> tuple[dict, dict]:
    try:
        from nhl_store import build_projections, load_injuries
        from nhl_store.readers import get_injuries, get_projected_goalies

        if _resolve_use_db(slate_date, use_db):
            _ensure_db_snapshot(slate_date)
        load_injuries(slate_date)
        build_projections(slate_date, allow_landing=allow_landing)
        return get_projected_goalies(slate_date), get_injuries(slate_date)
    except ImportError:
        return {}, {}


def load_context(
    slate_date: date,
    refresh: bool = False,
    use_db: bool | None = None,
    *,
    allow_landing: bool = True,
) -> dict:
    """Everything needed to predict a slate on the given date."""
    season = season_for(slate_date)
    asof = slate_date - timedelta(days=1)
    if _resolve_use_db(slate_date, use_db):
        _ensure_db_snapshot(slate_date)
    standings = get_standings(asof, slate_date=slate_date, use_db=use_db)
    summary = get_team_summary_asof(slate_date, standings, use_db=use_db) if standings else {}
    season_started = not standings or any((row.get("gp") or 0) > 0 for row in standings.values())
    mp_refresh = refresh or not _mp_cache_is_fresh(season, slate_date)
    if season_started and mp_refresh and not refresh:
        print(
            f"[data] MoneyPuck {season} cache ends {_mp_cache_max_date(season)}; "
            f"refreshing through {asof.isoformat()}",
            file=sys.stderr,
        )
    if season_started:
        mp = load_moneypuck_season(season, refresh=mp_refresh)
    else:
        print(
            f"[data] no regular-season games before {slate_date}; "
            "skip MoneyPuck current-season download (xG from prior season)",
            file=sys.stderr,
        )
        mp = pd.DataFrame(columns=MP_USECOLS)
    if season_started and not _mp_cache_is_fresh(season, slate_date):
        latest = _mp_cache_max_date(season)
        freshness.note(
            f"MoneyPuck {season} заканчивается на {latest if latest is not None else 'пустом файле'}, "
            f"для слайта {slate_date.isoformat()} нужны матчи по {asof.isoformat()}"
        )
    en = load_en_goals(season, mp, refresh=mp_refresh)
    priors = get_priors(season - 1, refresh=False)
    prev_mp = load_moneypuck_season(season - 1, refresh=False)
    goalies, injuries = _load_goalie_injury_ctx(
        slate_date, use_db, allow_landing=allow_landing,
    )
    return {
        "slate_date": slate_date,
        "standings": standings,
        "summary": summary,
        "mp": mp,
        "mp_prev": prev_mp,
        "en": en,
        "priors": priors,
        "league_en": league_en_asof(en, slate_date),
        "played_yesterday": teams_played_on(asof, use_db=use_db),
        "goalies": goalies,
        "injuries": injuries,
    }


def predict_game(ctx: dict, home: str, away: str) -> dict:
    gk = ctx.get("goalies") or {}
    inj = ctx.get("injuries") or {}
    h = build_state(home, ctx["standings"], ctx["summary"], ctx["mp"], ctx["en"],
                    ctx["priors"], ctx["slate_date"], ctx["played_yesterday"],
                    goalie_info=gk.get(home), injury_info=inj.get(home),
                    prev_mp=ctx.get("mp_prev"))
    a = build_state(away, ctx["standings"], ctx["summary"], ctx["mp"], ctx["en"],
                    ctx["priors"], ctx["slate_date"], ctx["played_yesterday"],
                    goalie_info=gk.get(away), injury_info=inj.get(away),
                    prev_mp=ctx.get("mp_prev"))
    pred = predict(h, a, ctx["league_en"])
    lean = h.abbrev if pred["p_home"] >= 0.5 else a.abbrev
    return {"h": h, "a": a, "pred": pred, "lean": lean}


# ----------------------------------------------------------------------- Main

def parse_games_arg(arg: str) -> list[tuple[str, str]]:
    """'BUF-NYI,WSH-PHI' -> [(home, away), ...]"""
    pairs = []
    for item in arg.split(","):
        home, away = item.strip().upper().split("-")
        pairs.append((home, away))
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser(description="NHL game predictor v0.1")
    ap.add_argument("--date", required=True, help="slate date YYYY-MM-DD (NHL/ET calendar)")
    ap.add_argument("--games", help="HOME-AWAY pairs, e.g. BUF-NYI,WSH-PHI (default: full slate)")
    ap.add_argument("--compare", action="store_true", help="compare with actual results")
    ap.add_argument("--refresh-cache", action="store_true", help="re-download MoneyPuck data")
    ap.add_argument("--use-db", action="store_true", help="read standings/slate from SQLite")
    ap.add_argument("--no-db", action="store_true", help="force NHL API instead of SQLite")
    args = ap.parse_args()

    slate_date = date.fromisoformat(args.date)
    use_db = False if args.no_db else (True if args.use_db else None)
    print(f"[data] snapshot of team stats as of end of {slate_date - timedelta(days=1)} "
          f"(games strictly before {slate_date})", file=sys.stderr)
    slate = get_slate(slate_date, use_db=use_db)
    ctx = load_context(slate_date, refresh=args.refresh_cache, use_db=use_db)

    if args.games:
        wanted = parse_games_arg(args.games)
        by_pair = {(g["home"], g["away"]): g for g in slate}
        games = []
        for home, away in wanted:
            g = by_pair.get((home, away)) or by_pair.get((away, home))
            if g is None:
                print(f"[warn] {home}-{away}: game not found on {slate_date}, skipping",
                      file=sys.stderr)
                continue
            games.append(g)
    else:
        games = slate

    results = []
    for g in games:
        r = predict_game(ctx, g["home"], g["away"])
        r["g"] = g
        results.append(r)
        h, a, pred = r["h"], r["a"], r["pred"]
        print(f"\n> {h.abbrev} vs {a.abbrev}")
        print(f"> Win probability: {h.abbrev} {pred['p_home']:.0%} / "
              f"{a.abbrev} {1 - pred['p_home']:.0%}")
        print(f"> Model lean: {r['lean']}")
        print(f"> Total goals: {pred['total']:.1f}")
        print(f"> Why: {', '.join(why_factors(h, a, pred['contribs']))}.")
        print(f"> Confidence: {pred['confidence']}.")

    if args.compare:
        print("\n" + "=" * 78)
        print("COMPARISON: prediction vs actual result")
        print("=" * 78)
        correct = 0
        graded = 0
        brier = 0.0
        mae_total = 0.0
        for r in results:
            g, pred = r["g"], r["pred"]
            if g["home_score"] is None or g["state"] not in ("OFF", "FINAL"):
                print(f"{g['home']} vs {g['away']}: game not finished yet")
                continue
            graded += 1
            actual_total = g["home_score"] + g["away_score"]
            winner = g["home"] if g["home_score"] > g["away_score"] else g["away"]
            hit = winner == r["lean"]
            correct += hit
            p_win = pred["p_home"] if winner == g["home"] else 1 - pred["p_home"]
            brier += (1 - p_win) ** 2
            mae_total += abs(pred["total"] - actual_total)
            print(f"{g['home']} {g['home_score']} : {g['away_score']} {g['away']} "
                  f"({g['outcome']}) | lean {r['lean']} "
                  f"({max(pred['p_home'], 1 - pred['p_home']):.0%}) -> "
                  f"{'HIT' if hit else 'MISS'} | total pred {pred['total']:.1f} "
                  f"vs fact {actual_total} | confidence {pred['confidence']}")
        if graded:
            print("-" * 78)
            print(f"Moneyline accuracy: {correct}/{graded} ({correct / graded:.0%}) | "
                  f"Brier: {brier / graded:.3f} | "
                  f"Total goals MAE: {mae_total / graded:.2f}")


if __name__ == "__main__":
    main()
