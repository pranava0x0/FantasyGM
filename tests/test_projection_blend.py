"""Tests for multi-source projection blending in analyze.py.

Covers:
- ESPN 2-week rolling average calculation
- Blended per-game projection with 1/2/3/4 sources
- External projection pass-through to rank_free_agents
"""

from __future__ import annotations

import pytest

from pipeline import analyze


def _make_player(
    *,
    espn_proj_avg: float = 0.0,      # preseason projection (statSourceId=1)
    actual_season_avg: float = 0.0,  # actual season avg so far (statSourceId=0, split=0)
    actual_games_played: int = 0,    # used to derive appliedTotal for the season block
    game_totals: list[tuple[int, float]] | None = None,  # (period, total) per-game logs
) -> dict:
    """Build a minimal player dict with the stat blocks we care about."""
    stats = []
    # ESPN preseason projection block (statSourceId=1, statSplitTypeId=0).
    # appliedTotal = avg × 44 (full WNBA season), never updated mid-season.
    if espn_proj_avg:
        stats.append({
            "statSourceId": 1,
            "statSplitTypeId": 0,
            "scoringPeriodId": 0,
            "appliedAverage": espn_proj_avg,
            "appliedTotal": espn_proj_avg * 44,   # full-season preseason forecast
        })
    # Actual season stats block (statSourceId=0, statSplitTypeId=0).
    if actual_season_avg and actual_games_played > 0:
        stats.append({
            "statSourceId": 0,
            "statSplitTypeId": 0,
            "scoringPeriodId": 0,
            "appliedAverage": actual_season_avg,
            "appliedTotal": actual_season_avg * actual_games_played,
        })
    # Per-game actual blocks (statSourceId=0, statSplitTypeId=5)
    for period, total in (game_totals or []):
        stats.append({
            "statSourceId": 0,
            "statSplitTypeId": 5,
            "scoringPeriodId": period,
            "appliedTotal": total,
        })
    return {"id": 1, "stats": stats}


class TestRolling2WeekAvg:
    def test_no_game_blocks(self) -> None:
        player = _make_player(espn_proj_avg=25.0)
        result = analyze._player_rolling_2w_avg(player, current_period=25)
        assert result is None

    def test_games_in_window(self) -> None:
        # Periods 15-25 are within a 14-period window from period 25 (start=11).
        player = _make_player(game_totals=[(15, 20.0), (18, 30.0), (22, 25.0)])
        result = analyze._player_rolling_2w_avg(player, current_period=25)
        assert result == pytest.approx((20.0 + 30.0 + 25.0) / 3)

    def test_games_outside_window_excluded(self) -> None:
        # Period 5 is outside the 14-period window from 25 (start=11).
        player = _make_player(game_totals=[(5, 40.0), (20, 20.0), (23, 30.0)])
        result = analyze._player_rolling_2w_avg(player, current_period=25)
        assert result == pytest.approx((20.0 + 30.0) / 2)

    def test_all_games_outside_window(self) -> None:
        player = _make_player(game_totals=[(1, 10.0), (3, 12.0)])
        result = analyze._player_rolling_2w_avg(player, current_period=25)
        assert result is None

    def test_zero_total_excluded(self) -> None:
        # A game with appliedTotal=0.0 means the player didn't play — skip it.
        player = _make_player(game_totals=[(20, 0.0), (23, 28.0)])
        result = analyze._player_rolling_2w_avg(player, current_period=25)
        assert result == pytest.approx(28.0)

    def test_current_period_included(self) -> None:
        player = _make_player(game_totals=[(25, 35.0)])
        result = analyze._player_rolling_2w_avg(player, current_period=25)
        assert result == pytest.approx(35.0)


class TestProjectedPerGameBlending:
    def test_actual_season_avg_is_source_1(self) -> None:
        # Actual season avg (22.375) should be used instead of ESPN preseason proj (18.43).
        # This is the Erica Wheeler case: preseason forecast is stale but observed
        # performance is better. Only the actual avg + rolling should contribute.
        player = _make_player(
            espn_proj_avg=18.43,         # preseason: 18.43 × 44 games (stale)
            actual_season_avg=22.375,    # 8 real games this season
            actual_games_played=8,
            game_totals=[(14, 16.0), (16, 34.0), (22, 39.0), (23, 24.0)],
        )
        result = analyze._player_projected_per_game(player, scoring_period_id=25)
        rolling = (16 + 34 + 39 + 24) / 4  # 28.25
        expected = (22.375 + rolling) / 2   # (22.375 + 28.25) / 2 = 25.3125
        assert result == pytest.approx(expected)
        # Verify the stale ESPN preseason (18.43) is NOT in the blend.
        assert result > 23.0  # would be ~23.34 if stale preseason were included

    def test_espn_preseason_fallback_when_few_games(self) -> None:
        # With fewer than 3 actual games, ESPN preseason projection is kept as anchor.
        player = _make_player(
            espn_proj_avg=30.0,
            actual_season_avg=20.0,
            actual_games_played=2,       # only 2 games — below threshold
        )
        result = analyze._player_projected_per_game(player, scoring_period_id=25)
        # 3 sources: actual_avg=20, espn_preseason=30 (fallback), no rolling
        assert result == pytest.approx((20.0 + 30.0) / 2)

    def test_espn_preseason_dropped_after_3_games(self) -> None:
        # Once 3+ actual games played, ESPN preseason projection is excluded.
        player = _make_player(
            espn_proj_avg=30.0,
            actual_season_avg=20.0,
            actual_games_played=3,       # exactly at threshold
        )
        result = analyze._player_projected_per_game(player, scoring_period_id=25)
        # Only actual_avg contributes (no rolling, preseason excluded)
        assert result == pytest.approx(20.0)

    def test_actual_avg_plus_rolling_plus_ext(self) -> None:
        # actual_avg=20, rolling=30, CBS=25, Yahoo=35 → (20+30+25+35)/4 = 27.5
        player = _make_player(
            actual_season_avg=20.0,
            actual_games_played=8,
            game_totals=[(20, 30.0)],
        )
        ext = {"cbs": 25.0, "yahoo": 35.0}
        result = analyze._player_projected_per_game(
            player, scoring_period_id=25, ext_projections=ext
        )
        assert result == pytest.approx(27.5)

    def test_zero_ext_excluded(self) -> None:
        # An external source returning 0 should not drag the average down.
        player = _make_player(actual_season_avg=30.0, actual_games_played=5)
        ext = {"cbs": 0.0, "yahoo": 30.0}
        result = analyze._player_projected_per_game(
            player, scoring_period_id=25, ext_projections=ext
        )
        assert result == pytest.approx((30.0 + 30.0) / 2)

    def test_only_rolling_no_actual_no_espn(self) -> None:
        # Brand-new player — no season stats, no ESPN projection, only recent games.
        player = _make_player(game_totals=[(20, 18.0), (23, 22.0)])
        result = analyze._player_projected_per_game(player, scoring_period_id=25)
        assert result == pytest.approx(20.0)

    def test_no_sources_returns_zero(self) -> None:
        player = _make_player()
        result = analyze._player_projected_per_game(player, scoring_period_id=25)
        assert result == 0.0


class TestInjurySignal:
    """Validate the returning-from-injury detection and base_score boost."""

    def _make_player_with_season(
        self,
        *,
        season_avg: float,
        season_games: int,
        game_totals: list[tuple[int, float]] | None = None,
        pro_team_id: int = 9,
    ) -> dict:
        stats = []
        if season_avg and season_games:
            stats.append({
                "statSourceId": 0, "statSplitTypeId": 0,
                "appliedAverage": season_avg,
                "appliedTotal": season_avg * season_games,
                "scoringPeriodId": 0,
            })
        for period, total in (game_totals or []):
            stats.append({
                "statSourceId": 0, "statSplitTypeId": 5,
                "scoringPeriodId": period, "appliedTotal": total,
            })
        return {"id": 1, "proTeamId": pro_team_id, "stats": stats}

    def test_returning_signal_fires(self) -> None:
        # Team played 5 games, player played 0 — classic return from injury.
        # season_avg=17, 4 games of history → should fire.
        player = self._make_player_with_season(season_avg=17.0, season_games=4)
        signal, boost = analyze._injury_signal(
            player,
            pro_team_id=9,
            scoring_period_id=25,
            window_games_map={9: 5},
        )
        assert signal == "returning"
        assert boost == pytest.approx(1.15)

    def test_no_signal_when_player_played_enough(self) -> None:
        # Team 5 games, player played 3 (60% attendance) — below absence threshold.
        player = self._make_player_with_season(
            season_avg=17.0, season_games=4,
            game_totals=[(15, 18.0), (20, 16.0), (22, 19.0)],
        )
        signal, boost = analyze._injury_signal(
            player,
            pro_team_id=9,
            scoring_period_id=25,
            window_games_map={9: 5},
        )
        assert signal is None
        assert boost == pytest.approx(1.0)

    def test_no_signal_when_team_too_few_games(self) -> None:
        # Team only had 2 games in window — can't make a reliable absence call.
        player = self._make_player_with_season(season_avg=20.0, season_games=5)
        signal, boost = analyze._injury_signal(
            player,
            pro_team_id=9,
            scoring_period_id=25,
            window_games_map={9: 2},
        )
        assert signal is None
        assert boost == pytest.approx(1.0)

    def test_no_signal_when_season_avg_too_low(self) -> None:
        # Player missed games but their season avg is too low to matter.
        player = self._make_player_with_season(season_avg=7.0, season_games=5)
        signal, boost = analyze._injury_signal(
            player,
            pro_team_id=9,
            scoring_period_id=25,
            window_games_map={9: 5},
        )
        assert signal is None

    def test_no_signal_when_too_few_season_games(self) -> None:
        # 83% absent but only 1 real game — season avg not reliable (Joyner Holmes case).
        player = self._make_player_with_season(season_avg=19.0, season_games=1)
        signal, boost = analyze._injury_signal(
            player,
            pro_team_id=9,
            scoring_period_id=25,
            window_games_map={9: 6},
        )
        assert signal is None

    def test_boost_applied_to_base_score_in_ranker(self) -> None:
        # End-to-end: injury signal boost should raise the ranked player's base_score.
        fa_raw = {
            "players": [{
                "player": {
                    "id": 99,
                    "fullName": "Return Queen",
                    "defaultPositionId": 1,
                    "proTeamId": 9,
                    "stats": [
                        # Season avg 18.0 over 5 games
                        {"statSourceId": 0, "statSplitTypeId": 0,
                         "appliedAverage": 18.0, "appliedTotal": 90.0, "scoringPeriodId": 0},
                        # 0 games in rolling window (team has 5) → absent
                    ],
                    "ownership": {},
                    "eligibleSlots": [0, 11],
                }
            }]
        }
        ranked = analyze.rank_free_agents(
            fa_raw,
            scoring_period_id=25,
            games_by_pro_team={9: 3},           # 3 games this week
            games_in_rolling_window_by_team={9: 5},  # team had 5 games in window
        )
        assert len(ranked) == 1
        r = ranked[0]
        assert r["injury_signal"] == "returning"
        # raw = 18.0 × 3 = 54.0; boosted = 54.0 × 1.15 = 62.1
        assert r["base_score"] == pytest.approx(62.1)


class TestRankFreeAgentsWithExt:
    """Ensure ext_projections_by_player flows through rank_free_agents."""

    def _make_fa_raw(self, player_id: int, actual_avg: float, games: int = 8) -> dict:
        return {
            "players": [{
                "player": {
                    "id": player_id,
                    "fullName": "Test Player",
                    "defaultPositionId": 1,
                    "proTeamId": 9,
                    "stats": [{
                        "statSourceId": 0, "statSplitTypeId": 0,
                        "appliedAverage": actual_avg,
                        "appliedTotal": actual_avg * games,
                        "scoringPeriodId": 0,
                    }],
                    "ownership": {},
                    "eligibleSlots": [0, 11],
                }
            }]
        }

    def test_ext_projection_included(self) -> None:
        fa_raw = self._make_fa_raw(player_id=42, actual_avg=20.0)
        # External source says 40 → blend = (20+40)/2 = 30
        ext = {42: {"cbs": 40.0}}
        ranked = analyze.rank_free_agents(
            fa_raw,
            scoring_period_id=25,
            ext_projections_by_player=ext,
        )
        assert len(ranked) == 1
        assert ranked[0]["projected_per_game"] == pytest.approx(30.0)

    def test_without_ext_baseline(self) -> None:
        fa_raw = self._make_fa_raw(player_id=42, actual_avg=20.0)
        ranked = analyze.rank_free_agents(fa_raw, scoring_period_id=25)
        assert len(ranked) == 1
        assert ranked[0]["projected_per_game"] == pytest.approx(20.0)
