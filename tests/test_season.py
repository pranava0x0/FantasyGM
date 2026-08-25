"""Tests for the season-result derivation (`pipeline/season.py`).

The bracket is inferred, not read: this league's ESPN response returns
`winner: null` on every game — including finished ones — so rounds and
winners come from `playoffSeed` plus the scores. These tests pin that
inference, and especially the one rule the UI depends on: an unplayed
`0–0` bracket game must not crown anybody.
"""

from __future__ import annotations

from pipeline import season

# Mirrors the real 50-40-90 Club shape: 8 teams, non-dense IDs, 12 regular
# matchup periods, top 4 make a two-round bracket (mp13 semis, mp14 final).
_SEEDS = {1: 3, 2: 6, 5: 2, 6: 4, 7: 5, 8: 1, 9: 8, 10: 7}


def _team(team_id: int, seed: int, wins: int, losses: int, pf: float = 5000.0) -> dict:
    return {
        "id": team_id,
        "playoffSeed": seed,
        "record": {"overall": {
            "wins": wins, "losses": losses, "ties": 0,
            "pointsFor": pf, "pointsAgainst": 5000.0,
        }},
    }


def _game(period: int, home_id: int, away_id: int, home_pts: float, away_pts: float) -> dict:
    return {
        "matchupPeriodId": period,
        "home": {"teamId": home_id, "totalPoints": home_pts},
        "away": {"teamId": away_id, "totalPoints": away_pts},
        "winner": None,  # ESPN leaves this null even on completed games
    }


def _league(schedule: list[dict], *, playoff_teams: int = 4, regular_periods: int = 12) -> dict:
    return {
        "settings": {"scheduleSettings": {
            "matchupPeriodCount": regular_periods,
            "playoffTeamCount": playoff_teams,
        }},
        "teams": [
            _team(1, 3, 7, 5, 5473.0), _team(2, 6, 5, 7, 5674.0),
            _team(5, 2, 8, 4, 5875.0), _team(6, 4, 6, 6, 6070.0),
            _team(7, 5, 6, 6, 5286.0), _team(8, 1, 9, 3, 6101.0),
            _team(9, 8, 2, 10, 4920.0), _team(10, 7, 5, 7, 5279.0),
        ],
        "schedule": schedule,
    }


def _semis() -> list[dict]:
    """mp13: 1v4 and 2v3 in the bracket, 5v6 and 7v8 on the consolation ladder."""
    return [
        _game(13, 8, 6, 1334.0, 887.0),    # #1 KylB beats #4 Nut
        _game(13, 5, 1, 1049.0, 762.0),    # #2 Spda beats #3 KAH
        _game(13, 7, 2, 849.0, 1151.0),    # consolation
        _game(13, 10, 9, 835.0, 987.0),    # consolation
    ]


def _final_round(final_home: float, final_away: float, third_home: float, third_away: float) -> list[dict]:
    return [
        _game(14, 8, 5, final_home, final_away),   # final: semifinal winners
        _game(14, 1, 6, third_home, third_away),   # third place: semifinal losers
        _game(14, 2, 9, 0.0, 0.0),                 # consolation
        _game(14, 7, 10, 0.0, 0.0),                # consolation
    ]


class TestRoundLabelling:
    def test_semifinal_and_consolation_are_distinguished(self) -> None:
        result = season.build_season_result(_league(_semis()), current_matchup_period=13)
        rounds = {(g.home_team_id, g.away_team_id): g.round for g in result.bracket}
        assert rounds[(8, 6)] == "semifinal"
        assert rounds[(5, 1)] == "semifinal"
        assert rounds[(7, 2)] == "consolation"
        assert rounds[(10, 9)] == "consolation"

    def test_final_and_third_place_are_distinguished(self) -> None:
        result = season.build_season_result(
            _league(_semis() + _final_round(0.0, 0.0, 0.0, 0.0)),
            current_matchup_period=14,
        )
        rounds = {(g.home_team_id, g.away_team_id): g.round for g in result.bracket}
        # The two semifinal winners meet in the final; the two losers play for third.
        assert rounds[(8, 5)] == "final"
        assert rounds[(1, 6)] == "third place"
        assert rounds[(2, 9)] == "consolation"


class TestUndecidedBracket:
    """The rule the season banner leans on: 0–0 means *not played*."""

    def test_unplayed_final_crowns_nobody(self) -> None:
        result = season.build_season_result(
            _league(_semis() + _final_round(0.0, 0.0, 0.0, 0.0)),
            current_matchup_period=14,
        )
        assert result.champion_team_id is None
        assert result.runner_up_team_id is None
        assert result.playoffs_complete is False
        final = next(g for g in result.bracket if g.round == "final")
        assert final.played is False
        assert final.winner_team_id is None

    def test_semifinal_losers_are_still_marked_eliminated(self) -> None:
        # A team can be out of the running before a champion exists — that is
        # exactly the state the banner has to describe.
        result = season.build_season_result(
            _league(_semis() + _final_round(0.0, 0.0, 0.0, 0.0)),
            current_matchup_period=14,
        )
        by_team = {s.team_id: s for s in result.standings}
        assert by_team[1].eliminated_round == "semifinal"
        assert by_team[6].eliminated_round == "semifinal"
        assert by_team[8].eliminated_round is None
        assert by_team[5].eliminated_round is None

    def test_no_placements_before_the_bracket_finishes(self) -> None:
        result = season.build_season_result(
            _league(_semis() + _final_round(0.0, 0.0, 0.0, 0.0)),
            current_matchup_period=14,
        )
        assert all(s.placement is None for s in result.standings)


class TestCompletedBracket:
    def test_champion_runner_up_and_placements(self) -> None:
        result = season.build_season_result(
            _league(_semis() + _final_round(1200.0, 1100.0, 900.0, 950.0)),
            current_matchup_period=14,
        )
        assert result.champion_team_id == 8
        assert result.runner_up_team_id == 5
        assert result.playoffs_complete is True
        by_team = {s.team_id: s for s in result.standings}
        assert by_team[8].placement_label == "Champion"
        assert by_team[5].placement_label == "Runner-up"
        # Third-place game: home 1 scored 900, away 6 scored 950 — away wins.
        assert by_team[6].placement_label == "3rd place"
        assert by_team[1].placement_label == "4th place"

    def test_consolation_teams_get_no_placement(self) -> None:
        result = season.build_season_result(
            _league(_semis() + _final_round(1200.0, 1100.0, 900.0, 950.0)),
            current_matchup_period=14,
        )
        by_team = {s.team_id: s for s in result.standings}
        for tid in (2, 7, 9, 10):
            assert by_team[tid].made_playoffs is False
            assert by_team[tid].placement is None


class TestStandings:
    def test_sorted_by_seed_and_carries_the_regular_season_line(self) -> None:
        result = season.build_season_result(_league(_semis()), current_matchup_period=13)
        assert [s.seed for s in result.standings] == [1, 2, 3, 4, 5, 6, 7, 8]
        top = result.standings[0]
        assert (top.team_id, top.wins, top.losses) == (8, 9, 3)
        assert top.points_for == 6101.0

    def test_non_dense_team_ids_survive(self) -> None:
        # 50-40-90 Club scar: IDs are [1,2,5,6,7,8,9,10] — gaps at 3 and 4.
        result = season.build_season_result(_league(_semis()), current_matchup_period=13)
        assert {s.team_id for s in result.standings} == set(_SEEDS)

    def test_playoff_qualification_follows_seed_not_record(self) -> None:
        # Nut (id 6) and C9 (id 7) are both 6–6; only the #4 seed is in.
        result = season.build_season_result(_league(_semis()), current_matchup_period=13)
        by_team = {s.team_id: s for s in result.standings}
        assert by_team[6].made_playoffs is True
        assert by_team[7].made_playoffs is False


class TestGuards:
    def test_missing_schedule_settings_returns_none(self) -> None:
        assert season.build_season_result({"teams": [], "schedule": []}, 14) is None

    def test_regular_season_flag_tracks_the_current_period(self) -> None:
        during = season.build_season_result(_league([]), current_matchup_period=9)
        after = season.build_season_result(_league(_semis()), current_matchup_period=13)
        assert during.regular_season_complete is False
        assert after.regular_season_complete is True

    def test_tie_leaves_the_game_undecided(self) -> None:
        result = season.build_season_result(
            _league(_semis() + _final_round(1100.0, 1100.0, 900.0, 950.0)),
            current_matchup_period=14,
        )
        final = next(g for g in result.bracket if g.round == "final")
        assert final.played is True
        assert final.winner_team_id is None
        assert result.champion_team_id is None
