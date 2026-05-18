"""Tests for the WNBA pro-team schedule helpers."""

from __future__ import annotations

import pytest

from pipeline.schedule import (
    game_count_multiplier,
    games_per_team,
    tier_for_game_count,
    upcoming_week_periods,
)


def _league_with_schedules(schedules: dict[int, dict[int, int]]) -> dict:
    """Build a slim league_raw with `settings.proTeams[*].proGamesByScoringPeriod`."""
    pro_teams = []
    for pid, periods in schedules.items():
        games_map = {str(sp): [{"id": i, "scoringPeriodId": sp} for i in range(n)] for sp, n in periods.items()}
        pro_teams.append({"id": pid, "abbrev": f"T{pid}", "proGamesByScoringPeriod": games_map})
    return {
        "scoringPeriodId": 10,
        "status": {"transactionScoringPeriod": 11},
        "settings": {"proTeams": pro_teams},
    }


class TestGamesPerTeam:
    def test_counts_within_window(self) -> None:
        league = _league_with_schedules({
            11: {10: 1, 11: 1, 12: 1, 13: 1},  # 4 games periods 10-13
            17: {10: 0, 11: 1, 12: 0, 13: 1},  # 2 games periods 10-13
        })
        result = games_per_team(league, 11, 13)
        # Team 11 has games in periods 11,12,13 → 3
        # Team 17 has games in periods 11,13 → 2
        assert result == {11: 3, 17: 2}

    def test_period_outside_window_ignored(self) -> None:
        league = _league_with_schedules({11: {1: 1, 100: 1}})
        assert games_per_team(league, 11, 13) == {11: 0}

    def test_empty_league(self) -> None:
        assert games_per_team({"settings": {"proTeams": []}}, 1, 5) == {}

    def test_reverse_range_raises(self) -> None:
        with pytest.raises(ValueError):
            games_per_team({"settings": {"proTeams": []}}, 5, 1)


class TestUpcomingWeekPeriods:
    def test_uses_transaction_scoring_period(self) -> None:
        league = {"scoringPeriodId": 10, "status": {"transactionScoringPeriod": 11}}
        assert upcoming_week_periods(league) == (11, 17)

    def test_falls_back_to_scoring_period_plus_one(self) -> None:
        league = {"scoringPeriodId": 10, "status": {}}
        assert upcoming_week_periods(league) == (11, 17)

    def test_custom_days(self) -> None:
        league = {"scoringPeriodId": 10, "status": {"transactionScoringPeriod": 11}}
        assert upcoming_week_periods(league, days=3) == (11, 13)


class TestTierForGameCount:
    @pytest.mark.parametrize("games,expected", [
        (0, "BYE"),
        (1, "Tough"),
        (2, "Light"),
        (3, "Average"),
        (4, "Heavy"),
        (5, "Heavy"),
    ])
    def test_tier(self, games: int, expected: str) -> None:
        assert tier_for_game_count(games) == expected


class TestGameCountMultiplier:
    def test_zero_games(self) -> None:
        assert game_count_multiplier(0) == 0.0

    def test_proportional(self) -> None:
        assert game_count_multiplier(4) == 4.0
        assert game_count_multiplier(2) == 2.0

    def test_negative_clamped(self) -> None:
        # Shouldn't happen in practice but guard against bad data.
        assert game_count_multiplier(-1) == 0.0
