"""Tests for the auto-generated per-team summary bullets."""

from __future__ import annotations

from pipeline.summary import build_team_summaries


def _team_view(
    team_id: int,
    *,
    g_active: int = 5,
    fc_active: int = 4,
    top_scorer: tuple[str, str, float, int] = ("Star Player", "G", 100.0, 4),
    record: tuple[int, int, int] = (0, 0, 0),
) -> dict:
    roster = []
    name, bucket, week, games = top_scorer
    # Add the top scorer as one active player; pad the rest so the active_counts match.
    roster.append({
        "name": name, "bucket": bucket, "pro_team_id": 9,
        "is_active": True, "projected_points_this_week": week,
        "games_this_week": games, "lineup_slot_id": 1,
        "player_id": 100,
    })
    for i in range(1, g_active):
        roster.append({"name": f"G{i}", "bucket": "G", "pro_team_id": 9, "is_active": True,
                       "projected_points_this_week": 10.0, "games_this_week": 3,
                       "lineup_slot_id": 1, "player_id": 100 + i})
    for i in range(fc_active):
        roster.append({"name": f"F{i}", "bucket": "F", "pro_team_id": 9, "is_active": True,
                       "projected_points_this_week": 10.0, "games_this_week": 3,
                       "lineup_slot_id": 4, "player_id": 200 + i})
    wins, losses, ties = record
    return {
        "team_id": team_id, "abbrev": "TST", "name": f"Team {team_id}",
        "record": {"wins": wins, "losses": losses, "ties": ties},
        "roster": roster,
        "active_counts": {"G": g_active, "F": fc_active, "C": 0},
    }


def _weakness(weakest: str = "FC", g_gap: float = 0.0, fc_gap: float = 0.0) -> dict:
    return {
        "weakest_bucket": weakest,
        "guard_gap_vs_league": g_gap,
        "frontcourt_gap_vs_league": fc_gap,
    }


class TestBuildTeamSummaries:
    def test_severe_fc_weakness_makes_first_bullet(self) -> None:
        out = build_team_summaries(
            [_team_view(1)], {1: _weakness("FC", fc_gap=-25.0)}, [],
        )
        assert "Frontcourt is the structural weak spot" in out[1][0]
        assert "25" in out[1][0]

    def test_top_scorer_bullet_includes_position_and_games(self) -> None:
        out = build_team_summaries(
            [_team_view(1, top_scorer=("A'ja Wilson", "C", 120.5, 4))],
            {1: _weakness()}, [],
        )
        joined = " ".join(out[1])
        assert "A'ja Wilson" in joined
        assert "(C," in joined
        assert "4 games" in joined

    def test_record_bullet_skipped_before_matchup_2(self) -> None:
        out = build_team_summaries(
            [_team_view(1, record=(5, 3, 0))], {1: _weakness()}, [],
            matchup_period_id=1,
        )
        # Matchup 1 = season hasn't really started; record is 0-0 by definition.
        # Even if we synthetically pass 5-3, we should not include the record.
        joined = " ".join(out[1])
        assert "5–3" not in joined and "5-3" not in joined

    def test_record_bullet_included_from_matchup_2(self) -> None:
        out = build_team_summaries(
            [_team_view(1, record=(5, 3, 0))], {1: _weakness()}, [],
            matchup_period_id=4,
        )
        joined = " ".join(out[1])
        assert "5–3" in joined

    def test_guard_heavy_only_at_6_plus_actives(self) -> None:
        # 5 G / 4 FC is the normal league lineup — should NOT trigger.
        out_normal = build_team_summaries(
            [_team_view(1, g_active=5, fc_active=4)], {1: _weakness()}, [],
        )
        assert all("Guard-heavy" not in b for b in out_normal[1])
        # 6+ G should trigger.
        out_heavy = build_team_summaries(
            [_team_view(1, g_active=6, fc_active=3)], {1: _weakness()}, [],
        )
        assert any("Guard-heavy" in b for b in out_heavy[1])

    def test_transactions_bullet_counts_correctly(self) -> None:
        txns = [
            {"transaction_id": "a", "team_id": 1, "type": "WAIVER"},
            {"transaction_id": "b", "team_id": 1, "type": "TRADE_ACCEPT"},
            {"transaction_id": "c", "team_id": 2, "type": "WAIVER"},  # other team
        ]
        out = build_team_summaries([_team_view(1)], {1: _weakness()}, txns)
        joined = " ".join(out[1])
        assert "2 recent transactions" in joined

    def test_capped_at_5_bullets(self) -> None:
        out = build_team_summaries(
            [_team_view(1, g_active=6, fc_active=3, record=(5, 3, 0))],
            {1: _weakness("FC", fc_gap=-30.0)},
            [{"transaction_id": "a", "team_id": 1, "type": "WAIVER"}],
            matchup_period_id=4,
        )
        assert len(out[1]) <= 5
