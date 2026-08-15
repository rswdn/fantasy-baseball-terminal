"""Unit tests for MLB data normalization, caching, and player details."""

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pandas as pd

from data_provider import (
    HITTER_COLUMNS,
    PITCHER_COLUMNS,
    STREAMER_COLUMNS,
    _build_hitters,
    _build_pitchers,
    _build_streamers,
    _contact_rates,
    _estimated_wrc_plus,
    _innings,
    _read_cache,
    _write_cache,
    fetch_player_season_stats,
    load_data,
)


class FetchPlayerSeasonStatsTests(TestCase):
    def test_uses_existing_id_and_hitting_group(self) -> None:
        payload = {
            "stats": [
                {
                    "splits": [
                        {
                            "stat": {
                                "gamesPlayed": 91,
                                "homeRuns": 18,
                                "avg": ".284",
                            }
                        }
                    ]
                }
            ]
        }

        with patch("data_provider._mlb_get", return_value=payload) as mlb_get:
            result = fetch_player_season_stats(
                "Test Hitter",
                123,
                "hitter",
                2026,
            )

        self.assertEqual(result["player_id"], 123)
        self.assertEqual(result["stats"]["homeRuns"], 18)
        mlb_get.assert_called_once_with(
            "people/123/stats",
            stats="season",
            group="hitting",
            season=2026,
            sportIds=1,
        )

    def test_resolves_legacy_row_and_requests_pitching_stats(self) -> None:
        search_payload = {
            "people": [
                {"id": 111, "fullName": "Different Player"},
                {"id": 456, "fullName": "Test Pitcher"},
            ]
        }
        stats_payload = {
            "stats": [
                {
                    "splits": [
                        {
                            "stat": {
                                "gamesPlayed": 20,
                                "inningsPitched": "112.2",
                                "era": "3.12",
                            }
                        }
                    ]
                }
            ]
        }

        with patch(
            "data_provider._mlb_get",
            side_effect=[search_payload, stats_payload],
        ) as mlb_get:
            result = fetch_player_season_stats(
                "Test Pitcher",
                None,
                "pitcher",
                2026,
            )

        self.assertEqual(result["player_id"], 456)
        self.assertEqual(result["stats"]["era"], "3.12")
        self.assertEqual(mlb_get.call_count, 2)
        mlb_get.assert_any_call(
            "people/search",
            names="Test Pitcher",
            sportIds=1,
            hydrate="currentTeam",
        )
        mlb_get.assert_any_call(
            "people/456/stats",
            stats="season",
            group="pitching",
            season=2026,
            sportIds=1,
        )

    def test_returns_empty_stats_when_player_has_no_season_split(self) -> None:
        with patch("data_provider._mlb_get", return_value={"stats": []}):
            result = fetch_player_season_stats(
                "Inactive Player",
                789,
                "hitter",
                2026,
            )

        self.assertEqual(result["stats"], {})


class StatcastNormalizationTests(TestCase):
    def test_innings_notation_converts_outs_to_decimal_innings(self) -> None:
        self.assertEqual(_innings("12.0"), 12.0)
        self.assertAlmostEqual(_innings("12.1"), 12 + 1 / 3)
        self.assertAlmostEqual(_innings("12.2"), 12 + 2 / 3)

    def test_contact_rates_use_batted_ball_rows_only(self) -> None:
        statcast_data = pd.DataFrame(
            {
                "batter": [101, 101, 101, None],
                "launch_speed": [100, 90, None, 110],
                "launch_speed_angle": [6, 7, 6, 6],
            }
        )

        result = _contact_rates(statcast_data, "batter").set_index("batter")

        self.assertEqual(result.loc[101, "Barrel%"], 50.0)
        self.assertEqual(result.loc[101, "HardHit%"], 50.0)

    def test_estimated_wrc_plus_deduplicates_plate_appearance_rows(self) -> None:
        statcast_data = pd.DataFrame(
            {
                "batter": [1, 1, 1, 2],
                "game_pk": [10, 10, 10, 10],
                "at_bat_number": [1, 1, 2, 3],
                "woba_value": [0.1, 0.5, 0.5, 0.1],
                "woba_denom": [1, 1, 1, 1],
            }
        )

        result = _estimated_wrc_plus(statcast_data)

        self.assertAlmostEqual(result[1], 136.3636, places=3)
        self.assertAlmostEqual(result[2], 27.2727, places=3)

    def test_build_hitters_normalizes_rates_and_roster_percentage(self) -> None:
        statcast_data = pd.DataFrame(
            {
                "batter": [101, 101],
                "launch_speed": [100, 90],
                "launch_speed_angle": [6, 7],
                "game_pk": [10, 10],
                "at_bat_number": [1, 2],
                "woba_value": [0.5, 0.1],
                "woba_denom": [1, 1],
            }
        )
        splits = [
            {
                "player": {"id": 101, "fullName": "Test Hitter"},
                "team": {"abbreviation": "TST"},
                "position": {"abbreviation": "OF"},
                "stat": {
                    "plateAppearances": 20,
                    "baseOnBalls": 3,
                    "strikeOuts": 4,
                    "stolenBases": 2,
                },
            },
            {"player": {"id": 999}, "stat": {"plateAppearances": 0}},
        ]

        result = _build_hitters(splits, statcast_data, {"Test Hitter": 35})

        self.assertEqual(list(result.columns), HITTER_COLUMNS)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["Roster%"], 35)
        self.assertEqual(result.iloc[0]["BB%"], 15.0)
        self.assertEqual(result.iloc[0]["K%"], 20.0)
        self.assertEqual(result.iloc[0]["Barrel%"], 50.0)

    def test_build_pitchers_converts_ip_and_exposes_projection_details(self) -> None:
        statcast_data = pd.DataFrame(
            {
                "pitcher": [202, 202, 202, 202],
                "pitch_type": ["FF", "SL", "FF", "CH"],
                "description": [
                    "swinging_strike",
                    "called_strike",
                    "foul",
                    "swinging_strike_blocked",
                ],
                "launch_speed": [None, None, 100, 90],
                "launch_speed_angle": [None, None, 6, 7],
            }
        )
        splits = [
            {
                "player": {"id": 202, "fullName": "Test Pitcher"},
                "team": {"abbreviation": "TST"},
                "stat": {
                    "battersFaced": 20,
                    "inningsPitched": "12.2",
                    "gamesStarted": 3,
                    "strikeOuts": 6,
                    "baseOnBalls": 2,
                    "era": "3.00",
                    "whip": "1.10",
                },
            }
        ]

        result, details = _build_pitchers(splits, statcast_data, {})

        self.assertEqual(list(result.columns), PITCHER_COLUMNS)
        self.assertEqual(result.iloc[0]["IP"], 12.7)
        self.assertEqual(result.iloc[0]["K-BB%"], 20.0)
        self.assertEqual(result.iloc[0]["SwStr%"], 50.0)
        self.assertEqual(result.iloc[0]["HardHit%"], 50.0)
        self.assertEqual(details[202]["games_started"], 3)
        self.assertEqual(details[202]["projected_ip"], 4.2)


class StreamerAndCacheTests(TestCase):
    def test_build_streamers_uses_schedule_matchup_and_park_factor(self) -> None:
        hitters = pd.DataFrame(
            [
                {"Team": "OPP", "wRC+": 80, "PA": 100},
                {"Team": "OPP", "wRC+": 100, "PA": 100},
            ]
        )
        pitchers = pd.DataFrame(
            [
                {
                    "Player": "Test Pitcher",
                    "K-BB%": 22.0,
                    "SwStr%": 12.0,
                }
            ]
        )
        schedule = [
            {
                "venue": {"id": 19},
                "teams": {
                    "away": {
                        "team": {"abbreviation": "TST"},
                        "probablePitcher": {
                            "id": 202,
                            "fullName": "Test Pitcher",
                        },
                    },
                    "home": {"team": {"abbreviation": "OPP"}},
                },
            }
        ]

        with patch("data_provider._schedule", return_value=schedule):
            result = _build_streamers(
                hitters,
                pitchers,
                {
                    202: {
                        "name": "Test Pitcher",
                        "team": "TST",
                        "games_started": 2,
                        "projected_ip": 5.5,
                    }
                },
                {"Test Pitcher": 12},
                date(2026, 8, 15),
            )

        self.assertEqual(list(result.columns), STREAMER_COLUMNS)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["Matchup"], "@ OPP")
        self.assertEqual(result.iloc[0]["Opp wRC+"], 90)
        self.assertEqual(result.iloc[0]["Park Factor"], 101)

    def test_cache_round_trip_preserves_frames_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            fetched_at = datetime(2026, 8, 15, 12, 0)
            frames = {
                "hitters": pd.DataFrame({"Player": ["Hitter"]}),
                "pitchers": pd.DataFrame({"Player": ["Pitcher"]}),
                "streamers": pd.DataFrame({"Player": ["Streamer"]}),
            }

            _write_cache(data_dir, 7, fetched_at=fetched_at, **frames)
            result = _read_cache(data_dir, 7)

            self.assertIsNotNone(result)
            self.assertEqual(result.source, "live cache")
            self.assertEqual(result.fetched_at, fetched_at)
            pd.testing.assert_frame_equal(result.hitters, frames["hitters"])
            pd.testing.assert_frame_equal(result.pitchers, frames["pitchers"])
            pd.testing.assert_frame_equal(result.streamers, frames["streamers"])

    def test_load_data_uses_demo_files_when_auto_refresh_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            for filename in ("hitters.csv", "pitchers.csv", "streamers.csv"):
                pd.DataFrame({"Player": [filename]}).to_csv(
                    data_dir / filename,
                    index=False,
                )

            result = load_data(data_dir, 7, auto_refresh=False)

            self.assertEqual(result.source, "demo fallback")
            self.assertIsNone(result.fetched_at)
            self.assertIn("No cache exists", result.message)

    def test_load_data_returns_stale_cache_when_refresh_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            frames = {
                "hitters": pd.DataFrame({"Player": ["Hitter"]}),
                "pitchers": pd.DataFrame({"Player": ["Pitcher"]}),
                "streamers": pd.DataFrame({"Player": ["Streamer"]}),
            }
            fetched_at = datetime.now().astimezone() - timedelta(days=1)
            _write_cache(data_dir, 7, fetched_at=fetched_at, **frames)

            with patch(
                "data_provider.refresh_live_data",
                side_effect=RuntimeError("API unavailable"),
            ):
                result = load_data(data_dir, 7, force_refresh=True)

            self.assertEqual(result.source, "stale live cache")
            self.assertIn("API unavailable", result.message)
            pd.testing.assert_frame_equal(result.hitters, frames["hitters"])


if __name__ == "__main__":
    unittest.main()
