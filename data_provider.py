"""Live MLB data retrieval and disk caching for Fantasy Baseball Terminal."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypeAlias
from zoneinfo import ZoneInfo

import pandas as pd
import requests

# pybaseball otherwise writes to ~/.pybaseball, which may be unavailable in
# containers and hosted Streamlit environments.
os.environ.setdefault(
    "PYBASEBALL_CACHE",
    str(Path(__file__).resolve().parent / "data" / "cache" / "pybaseball"),
)
os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(__file__).resolve().parent / "data" / "cache" / "matplotlib"),
)

from pybaseball import cache as pybaseball_cache
from pybaseball import statcast


MLB_STATS_API = "https://statsapi.mlb.com/api/v1"
EASTERN = ZoneInfo("America/New_York")
CACHE_MAX_AGE = timedelta(hours=12)
REQUEST_TIMEOUT = 30
TimeWindow: TypeAlias = int | Literal["season"]
SUPPORTED_WINDOWS: tuple[TimeWindow, ...] = (7, 14, 30, "season")

HITTER_COLUMNS = [
    "Player", "Team", "Pos", "Roster%", "PA", "wRC+", "Barrel%",
    "HardHit%", "BB%", "K%", "SB",
]
PITCHER_COLUMNS = [
    "Player", "Team", "Role", "Roster%", "IP", "ERA", "WHIP",
    "K-BB%", "SwStr%", "HardHit%",
]
STREAMER_COLUMNS = [
    "Player", "Team", "Matchup", "Roster%", "Projected IP",
    "K-BB%", "SwStr%", "Opp wRC+", "Park Factor",
]

# Approximate run environment by venue. Unknown venues remain neutral (100).
PARK_FACTORS = {
    2: 101,    # Yankee Stadium
    3: 99,     # Fenway Park
    4: 100,    # Guaranteed Rate Field
    5: 98,     # Progressive Field
    7: 101,    # Kauffman Stadium
    10: 98,    # Oakland Coliseum
    12: 100,   # Tropicana Field
    14: 97,    # Rogers Centre
    15: 103,   # Chase Field
    17: 102,   # Wrigley Field
    19: 101,   # Coors Field (venue IDs can change; neutral fallback applies)
    22: 101,   # Dodger Stadium
    31: 101,   # PNC Park
    32: 100,   # T-Mobile Park
    2392: 99,  # Daikin Park
    2394: 100, # Citizens Bank Park
    2395: 97,  # Oracle Park
    2397: 99,  # loanDepot park
    2399: 101, # American Family Field
    2602: 101, # Great American Ball Park
    2680: 100, # Petco Park
    2889: 100, # Busch Stadium
    3289: 101, # Globe Life Field
    3312: 99,  # Target Field
}


@dataclass(frozen=True)
class DataSnapshot:
    """Frames and provenance returned to the Streamlit app."""

    hitters: pd.DataFrame
    pitchers: pd.DataFrame
    streamers: pd.DataFrame
    source: str
    fetched_at: datetime | None
    message: str


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _innings(value: Any) -> float:
    """Convert baseball innings notation (e.g. 12.2) to decimal innings."""
    text = str(value or "0")
    whole, _, partial = text.partition(".")
    outs = int(partial[:1] or 0)
    return int(whole) + min(outs, 2) / 3


def _mlb_get(path: str, **params: Any) -> dict[str, Any]:
    response = requests.get(
        f"{MLB_STATS_API}/{path.lstrip('/')}",
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _date_range_stats(group: str, start: date, end: date) -> list[dict[str, Any]]:
    payload = _mlb_get(
        "stats",
        stats="byDateRange",
        group=group,
        startDate=start.strftime("%m/%d/%Y"),
        endDate=end.strftime("%m/%d/%Y"),
        sportIds=1,
        hydrate="team",
        limit=5000,
    )
    stats = payload.get("stats", [])
    return stats[0].get("splits", []) if stats else []


def _regular_season_start(year: int, through: date) -> date:
    """Return the first MLB regular-season game date for a calendar year."""
    payload = _mlb_get(
        "schedule",
        sportId=1,
        gameTypes="R",
        startDate=date(year, 1, 1).isoformat(),
        endDate=through.isoformat(),
    )
    dates = [
        date.fromisoformat(schedule_date["date"])
        for schedule_date in payload.get("dates", [])
        if schedule_date.get("date")
    ]
    if not dates:
        raise RuntimeError(f"No MLB regular-season games found for {year}.")
    return min(dates)


def _load_roster_percentages(data_dir: Path) -> dict[str, float]:
    path = data_dir / "roster_percentages.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    if not {"Player", "Roster%"}.issubset(frame.columns):
        return {}
    return dict(zip(frame["Player"], frame["Roster%"], strict=False))


def _fetch_statcast(start: date, end: date) -> pd.DataFrame:
    pybaseball_cache.enable()
    frame = statcast(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if frame is None or frame.empty:
        raise RuntimeError("Baseball Savant returned no Statcast records.")
    frame["game_date"] = pd.to_datetime(frame["game_date"], errors="coerce")
    return frame


def _contact_rates(
    statcast_data: pd.DataFrame,
    player_column: str,
) -> pd.DataFrame:
    batted = statcast_data[
        statcast_data[player_column].notna()
        & statcast_data["launch_speed"].notna()
    ].copy()
    if batted.empty:
        return pd.DataFrame(columns=[player_column, "Barrel%", "HardHit%"])

    batted["is_barrel"] = batted["launch_speed_angle"].eq(6)
    batted["is_hard_hit"] = pd.to_numeric(
        batted["launch_speed"], errors="coerce"
    ).ge(95)
    return (
        batted.groupby(player_column, as_index=False)
        .agg(
            **{
                "Barrel%": ("is_barrel", lambda values: values.mean() * 100),
                "HardHit%": ("is_hard_hit", lambda values: values.mean() * 100),
            }
        )
    )


def _pitcher_statcast_rates(statcast_data: pd.DataFrame) -> pd.DataFrame:
    pitches = statcast_data[
        statcast_data["pitcher"].notna() & statcast_data["pitch_type"].notna()
    ].copy()
    misses = {
        "swinging_strike",
        "swinging_strike_blocked",
        "foul_tip",
        "missed_bunt",
    }
    pitches["is_swinging_strike"] = pitches["description"].isin(misses)
    swinging = (
        pitches.groupby("pitcher", as_index=False)["is_swinging_strike"]
        .mean()
        .rename(columns={"is_swinging_strike": "SwStr%"})
    )
    swinging["SwStr%"] *= 100

    contact = _contact_rates(statcast_data, "pitcher").drop(columns="Barrel%")
    return swinging.merge(contact, on="pitcher", how="outer")


def _estimated_wrc_plus(statcast_data: pd.DataFrame) -> pd.Series:
    plate_appearances = statcast_data[
        statcast_data["batter"].notna()
        & pd.to_numeric(statcast_data["woba_denom"], errors="coerce").gt(0)
    ].copy()
    if plate_appearances.empty:
        return pd.Series(dtype=float)

    # One terminal row per plate appearance contains the wOBA values.
    plate_appearances = plate_appearances.drop_duplicates(
        subset=["game_pk", "at_bat_number"], keep="last"
    )
    plate_appearances["woba_value"] = pd.to_numeric(
        plate_appearances["woba_value"], errors="coerce"
    ).fillna(0)
    plate_appearances["woba_denom"] = pd.to_numeric(
        plate_appearances["woba_denom"], errors="coerce"
    ).fillna(0)
    totals = plate_appearances.groupby("batter").agg(
        woba_value=("woba_value", "sum"),
        woba_denom=("woba_denom", "sum"),
    )
    league_woba = totals["woba_value"].sum() / totals["woba_denom"].sum()
    player_woba = totals["woba_value"] / totals["woba_denom"]
    return (player_woba / league_woba * 100).replace([float("inf")], pd.NA)


def _build_hitters(
    splits: list[dict[str, Any]],
    statcast_data: pd.DataFrame,
    roster_percentages: dict[str, float],
) -> pd.DataFrame:
    contact = _contact_rates(statcast_data, "batter").set_index("batter")
    estimated_wrc = _estimated_wrc_plus(statcast_data)
    rows = []

    for split in splits:
        stat = split.get("stat", {})
        player = split.get("player", {})
        player_id = player.get("id")
        pa = int(stat.get("plateAppearances", 0))
        if not player_id or pa == 0:
            continue

        name = player.get("fullName", "Unknown")
        rows.append(
            {
                "Player": name,
                "Team": split.get("team", {}).get("abbreviation", ""),
                "Pos": split.get("position", {}).get("abbreviation", ""),
                "Roster%": _number(roster_percentages.get(name)),
                "PA": pa,
                "wRC+": round(_number(estimated_wrc.get(player_id), 100), 0),
                "Barrel%": round(
                    _number(contact["Barrel%"].get(player_id) if not contact.empty else 0),
                    1,
                ),
                "HardHit%": round(
                    _number(contact["HardHit%"].get(player_id) if not contact.empty else 0),
                    1,
                ),
                "BB%": round(_number(stat.get("baseOnBalls")) / pa * 100, 1),
                "K%": round(_number(stat.get("strikeOuts")) / pa * 100, 1),
                "SB": int(stat.get("stolenBases", 0)),
            }
        )

    return pd.DataFrame(rows, columns=HITTER_COLUMNS)


def _build_pitchers(
    splits: list[dict[str, Any]],
    statcast_data: pd.DataFrame,
    roster_percentages: dict[str, float],
) -> tuple[pd.DataFrame, dict[int, dict[str, Any]]]:
    rates = _pitcher_statcast_rates(statcast_data).set_index("pitcher")
    rows = []
    details: dict[int, dict[str, Any]] = {}

    for split in splits:
        stat = split.get("stat", {})
        player = split.get("player", {})
        player_id = player.get("id")
        batters_faced = int(stat.get("battersFaced", 0))
        if not player_id or batters_faced == 0:
            continue

        name = player.get("fullName", "Unknown")
        ip = _innings(stat.get("inningsPitched"))
        games_started = int(stat.get("gamesStarted", 0))
        rows.append(
            {
                "Player": name,
                "Team": split.get("team", {}).get("abbreviation", ""),
                "Role": "SP" if games_started else "RP",
                "Roster%": _number(roster_percentages.get(name)),
                "IP": round(ip, 1),
                "ERA": _number(stat.get("era"), 99),
                "WHIP": _number(stat.get("whip"), 9),
                "K-BB%": round(
                    (
                        _number(stat.get("strikeOuts"))
                        - _number(stat.get("baseOnBalls"))
                    )
                    / batters_faced
                    * 100,
                    1,
                ),
                "SwStr%": round(
                    _number(rates["SwStr%"].get(player_id) if not rates.empty else 0),
                    1,
                ),
                "HardHit%": round(
                    _number(rates["HardHit%"].get(player_id) if not rates.empty else 0),
                    1,
                ),
            }
        )
        details[player_id] = {
            "name": name,
            "team": split.get("team", {}).get("abbreviation", ""),
            "games_started": games_started,
            "projected_ip": round(ip / games_started, 1) if games_started else 0,
        }

    return pd.DataFrame(rows, columns=PITCHER_COLUMNS), details


def _team_offense(hitters: pd.DataFrame) -> dict[str, float]:
    if hitters.empty:
        return {}
    weighted = hitters.assign(weighted=hitters["wRC+"] * hitters["PA"])
    grouped = weighted.groupby("Team").agg(
        weighted=("weighted", "sum"),
        pa=("PA", "sum"),
    )
    return (grouped["weighted"] / grouped["pa"]).to_dict()


def _schedule(today: date) -> list[dict[str, Any]]:
    payload = _mlb_get(
        "schedule",
        sportId=1,
        startDate=today.isoformat(),
        endDate=(today + timedelta(days=3)).isoformat(),
        hydrate="probablePitcher,team,venue",
    )
    return [
        game
        for schedule_date in payload.get("dates", [])
        for game in schedule_date.get("games", [])
    ]


def _build_streamers(
    hitters: pd.DataFrame,
    pitchers: pd.DataFrame,
    pitcher_details: dict[int, dict[str, Any]],
    roster_percentages: dict[str, float],
    today: date,
) -> pd.DataFrame:
    pitcher_metrics = pitchers.set_index("Player")
    offense = _team_offense(hitters)
    rows = []

    for game in _schedule(today):
        venue_id = game.get("venue", {}).get("id")
        for side, opponent_side in (("away", "home"), ("home", "away")):
            team_info = game.get("teams", {}).get(side, {})
            opponent_info = game.get("teams", {}).get(opponent_side, {})
            probable = team_info.get("probablePitcher", {})
            player_id = probable.get("id")
            details = pitcher_details.get(player_id)
            if not player_id or not details or details["games_started"] == 0:
                continue

            name = probable.get("fullName", details["name"])
            if name not in pitcher_metrics.index:
                continue

            opponent = opponent_info.get("team", {}).get("abbreviation", "")
            marker = "@" if side == "away" else "vs"
            metrics = pitcher_metrics.loc[name]
            rows.append(
                {
                    "Player": name,
                    "Team": team_info.get("team", {}).get(
                        "abbreviation", details["team"]
                    ),
                    "Matchup": f"{marker} {opponent}",
                    "Roster%": _number(roster_percentages.get(name)),
                    "Projected IP": details["projected_ip"],
                    "K-BB%": metrics["K-BB%"],
                    "SwStr%": metrics["SwStr%"],
                    "Opp wRC+": round(_number(offense.get(opponent), 100), 0),
                    "Park Factor": PARK_FACTORS.get(venue_id, 100),
                }
            )

    return (
        pd.DataFrame(rows, columns=STREAMER_COLUMNS)
        .drop_duplicates(subset=["Player"], keep="first")
    )


def _cache_directory(data_dir: Path, window: TimeWindow) -> Path:
    return data_dir / "cache" / str(window)


def _read_cache(data_dir: Path, window: TimeWindow) -> DataSnapshot | None:
    cache_dir = _cache_directory(data_dir, window)
    metadata_path = cache_dir / "metadata.json"
    paths = {
        "hitters": cache_dir / "hitters.csv",
        "pitchers": cache_dir / "pitchers.csv",
        "streamers": cache_dir / "streamers.csv",
    }
    if not metadata_path.exists() or not all(path.exists() for path in paths.values()):
        return None

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    fetched_at = datetime.fromisoformat(metadata["fetched_at"])
    return DataSnapshot(
        hitters=pd.read_csv(paths["hitters"]),
        pitchers=pd.read_csv(paths["pitchers"]),
        streamers=pd.read_csv(paths["streamers"]),
        source="live cache",
        fetched_at=fetched_at,
        message=metadata.get("message", "Loaded cached MLB data."),
    )


def _write_cache(
    data_dir: Path,
    window: TimeWindow,
    hitters: pd.DataFrame,
    pitchers: pd.DataFrame,
    streamers: pd.DataFrame,
    fetched_at: datetime,
) -> None:
    cache_dir = _cache_directory(data_dir, window)
    cache_dir.mkdir(parents=True, exist_ok=True)
    frames = {
        "hitters.csv": hitters,
        "pitchers.csv": pitchers,
        "streamers.csv": streamers,
    }
    for filename, frame in frames.items():
        temporary = cache_dir / f"{filename}.tmp"
        frame.to_csv(temporary, index=False)
        temporary.replace(cache_dir / filename)

    metadata = {
        "fetched_at": fetched_at.isoformat(),
        "window": window,
        "source": "pybaseball Statcast + MLB Stats API",
        "message": "Live MLB data cached successfully.",
    }
    temporary_metadata = cache_dir / "metadata.json.tmp"
    temporary_metadata.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    temporary_metadata.replace(cache_dir / "metadata.json")


def _fallback(data_dir: Path, error: Exception) -> DataSnapshot:
    return DataSnapshot(
        hitters=pd.read_csv(data_dir / "hitters.csv"),
        pitchers=pd.read_csv(data_dir / "pitchers.csv"),
        streamers=pd.read_csv(data_dir / "streamers.csv"),
        source="demo fallback",
        fetched_at=None,
        message=f"Live refresh failed; using demo data. {error}",
    )


def refresh_live_data(data_dir: Path, window: TimeWindow) -> DataSnapshot:
    """Fetch, normalize, and cache one selected window of live MLB data."""
    now = datetime.now(EASTERN)
    end = now.date() - timedelta(days=1)
    start = (
        _regular_season_start(now.year, end)
        if window == "season"
        else end - timedelta(days=window - 1)
    )
    roster_percentages = _load_roster_percentages(data_dir)

    statcast_data = _fetch_statcast(start, end)
    hitting_splits = _date_range_stats("hitting", start, end)
    pitching_splits = _date_range_stats("pitching", start, end)
    hitters = _build_hitters(hitting_splits, statcast_data, roster_percentages)
    pitchers, pitcher_details = _build_pitchers(
        pitching_splits,
        statcast_data,
        roster_percentages,
    )
    streamers = _build_streamers(
        hitters,
        pitchers,
        pitcher_details,
        roster_percentages,
        now.date(),
    )

    if hitters.empty or pitchers.empty:
        raise RuntimeError("The live sources returned incomplete player data.")

    _write_cache(data_dir, window, hitters, pitchers, streamers, now)
    return DataSnapshot(
        hitters=hitters,
        pitchers=pitchers,
        streamers=streamers,
        source="live",
        fetched_at=now,
        message="Live MLB data refreshed successfully.",
    )


def load_data(
    data_dir: Path,
    window: TimeWindow,
    *,
    force_refresh: bool = False,
    auto_refresh: bool = True,
) -> DataSnapshot:
    """Load fresh cache, refresh stale data, or fall back to demo CSVs."""
    cached = _read_cache(data_dir, window)
    now = datetime.now(EASTERN)
    cache_is_fresh = (
        cached is not None
        and cached.fetched_at is not None
        and now - cached.fetched_at <= CACHE_MAX_AGE
    )
    auto_refresh_disabled = os.getenv("FBT_DISABLE_AUTO_REFRESH") == "1"

    if cache_is_fresh and not force_refresh:
        return cached
    if not force_refresh and (auto_refresh_disabled or not auto_refresh):
        if cached is not None:
            return DataSnapshot(
                hitters=cached.hitters,
                pitchers=cached.pitchers,
                streamers=cached.streamers,
                source="stale live cache",
                fetched_at=cached.fetched_at,
                message="Loaded cached MLB data. Click Refresh live data to update.",
            )
        reason = (
            "Automatic refresh disabled."
            if auto_refresh_disabled
            else "No cache exists for this window."
        )
        return _fallback(data_dir, RuntimeError(reason))

    try:
        return refresh_live_data(data_dir, window)
    except Exception as error:
        if cached is not None:
            return DataSnapshot(
                hitters=cached.hitters,
                pitchers=cached.pitchers,
                streamers=cached.streamers,
                source="stale live cache",
                fetched_at=cached.fetched_at,
                message=f"Live refresh failed; using stale cache. {error}",
            )
        return _fallback(data_dir, error)


def refresh_live_data_windows(
    data_dir: Path,
    windows: tuple[TimeWindow, ...] = SUPPORTED_WINDOWS,
) -> dict[TimeWindow, DataSnapshot]:
    """Refresh every requested window and return each resulting snapshot."""
    return {
        window: load_data(
            data_dir,
            window,
            force_refresh=True,
            auto_refresh=False,
        )
        for window in windows
    }
