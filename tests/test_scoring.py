"""Unit tests for player scoring and normalization."""

import unittest

import pandas as pd

from scoring import (
    min_max_scale,
    score_hitters,
    score_pitchers,
    score_streamers,
    weighted_score,
)


class ScoringTests(unittest.TestCase):
    def test_min_max_scale_rewards_high_values(self) -> None:
        result = min_max_scale(pd.Series([10, 20, 30]))

        pd.testing.assert_series_equal(
            result,
            pd.Series([0.0, 50.0, 100.0]),
            check_names=False,
        )

    def test_min_max_scale_rewards_low_values(self) -> None:
        result = min_max_scale(pd.Series([1.0, 2.0, 3.0]), higher_is_better=False)

        pd.testing.assert_series_equal(
            result,
            pd.Series([100.0, 50.0, 0.0]),
            check_names=False,
        )

    def test_min_max_scale_handles_constant_and_missing_values(self) -> None:
        result = min_max_scale(pd.Series([5.0, None, 5.0]))

        pd.testing.assert_series_equal(
            result,
            pd.Series([50.0, 50.0, 50.0]),
            check_names=False,
        )

    def test_weighted_score_applies_lower_is_better_metrics(self) -> None:
        players = pd.DataFrame(
            {
                "skill": [10, 20],
                "era": [4.0, 2.0],
            }
        )

        result = weighted_score(
            players,
            {"skill": 0.5, "era": 0.5},
            lower_is_better={"era"},
        )

        self.assertEqual(result.tolist(), [0.0, 100.0])

    def test_weighted_score_rejects_invalid_weights_or_metrics(self) -> None:
        players = pd.DataFrame({"skill": [10, 20]})

        with self.assertRaisesRegex(ValueError, "non-zero"):
            weighted_score(players, {"skill": 0})
        with self.assertRaisesRegex(KeyError, "Missing scoring metric"):
            weighted_score(players, {"missing": 1})

    def test_category_scoring_adds_score_and_orders_best_player_first(self) -> None:
        hitter_columns = {
            "wRC+": [100, 150],
            "Barrel%": [5, 15],
            "HardHit%": [30, 50],
            "SB": [1, 5],
            "BB%": [5, 10],
            "K%": [25, 15],
        }
        pitcher_columns = {
            "K-BB%": [10, 25],
            "SwStr%": [8, 14],
            "ERA": [4.5, 2.5],
            "WHIP": [1.4, 1.0],
            "HardHit%": [45, 30],
        }
        streamer_columns = {
            "K-BB%": [10, 25],
            "SwStr%": [8, 14],
            "Opp wRC+": [110, 80],
            "Park Factor": [105, 95],
            "Projected IP": [5, 7],
            "Roster%": [40, 10],
        }

        hitter_result = score_hitters(pd.DataFrame(hitter_columns))
        pitcher_result = score_pitchers(pd.DataFrame(pitcher_columns))
        streamer_result = score_streamers(pd.DataFrame(streamer_columns))

        for result in (hitter_result, pitcher_result, streamer_result):
            self.assertIn("Score", result.columns)
            self.assertTrue(result["Score"].between(0, 100).all())
            self.assertTrue(result["Score"].is_monotonic_decreasing)
            self.assertEqual(result.iloc[0]["Score"], 100.0)


if __name__ == "__main__":
    unittest.main()
