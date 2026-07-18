"""Focused tests for player detail data retrieval."""

from unittest import TestCase
from unittest.mock import patch

from data_provider import fetch_player_season_stats


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

