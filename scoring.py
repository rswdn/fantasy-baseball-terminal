"""Scoring helpers for Fantasy Baseball Terminal."""

from collections.abc import Mapping

import pandas as pd


def min_max_scale(series: pd.Series, *, higher_is_better: bool = True) -> pd.Series:
    """Scale a numeric series to 0-100, optionally rewarding lower values."""
    numeric = pd.to_numeric(series, errors="coerce")
    minimum = numeric.min()
    maximum = numeric.max()

    if pd.isna(minimum) or pd.isna(maximum) or minimum == maximum:
        scaled = pd.Series(50.0, index=series.index)
    else:
        scaled = (numeric - minimum) / (maximum - minimum) * 100

    if not higher_is_better:
        scaled = 100 - scaled

    return scaled.fillna(0)


def weighted_score(
    dataframe: pd.DataFrame,
    weights: Mapping[str, float],
    *,
    lower_is_better: set[str] | None = None,
) -> pd.Series:
    """Return a 0-100 weighted score for the supplied metrics."""
    lower_is_better = lower_is_better or set()
    total_weight = sum(abs(weight) for weight in weights.values())

    if total_weight == 0:
        raise ValueError("At least one scoring weight must be non-zero.")

    score = pd.Series(0.0, index=dataframe.index)
    for metric, weight in weights.items():
        if metric not in dataframe.columns:
            raise KeyError(f"Missing scoring metric: {metric}")

        normalized = min_max_scale(
            dataframe[metric],
            higher_is_better=metric not in lower_is_better,
        )
        score += normalized * weight

    return (score / total_weight).round(1).clip(0, 100)


def score_hitters(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Score hitters using recent production and plate-discipline metrics."""
    scored = dataframe.copy()
    scored["Score"] = weighted_score(
        scored,
        {
            "wRC+": 0.30,
            "Barrel%": 0.22,
            "HardHit%": 0.18,
            "SB": 0.12,
            "BB%": 0.10,
            "K%": 0.08,
        },
        lower_is_better={"K%"},
    )
    return scored.sort_values("Score", ascending=False)


def score_pitchers(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Score pitchers using run prevention, strikeouts, and contact quality."""
    scored = dataframe.copy()
    scored["Score"] = weighted_score(
        scored,
        {
            "K-BB%": 0.30,
            "SwStr%": 0.20,
            "ERA": 0.20,
            "WHIP": 0.17,
            "HardHit%": 0.13,
        },
        lower_is_better={"ERA", "WHIP", "HardHit%"},
    )
    return scored.sort_values("Score", ascending=False)


def score_streamers(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Score short-term pitching options with matchup context."""
    scored = dataframe.copy()
    scored["Score"] = weighted_score(
        scored,
        {
            "K-BB%": 0.24,
            "SwStr%": 0.16,
            "Opp wRC+": 0.22,
            "Park Factor": 0.13,
            "Projected IP": 0.15,
            "Roster%": 0.10,
        },
        lower_is_better={"Opp wRC+", "Park Factor", "Roster%"},
    )
    return scored.sort_values("Score", ascending=False)
