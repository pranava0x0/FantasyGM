"""Tests for the analysis layer.

The fixtures here are committed:
- tests/fixtures/league_sample.json — slim slice of the real ESPN response
- tests/fixtures/free_agents_sample.json — hand-crafted, exercises ranking

These tests document the *math contract* — if any assertion changes, that's
a flag for a deliberate decision, not an incidental side effect.
"""

from __future__ import annotations

from pipeline import analyze


class TestBuildTeamViews:
    def test_returns_one_view_per_team(self, league_raw: dict) -> None:
        views = analyze.build_team_views(league_raw)
        assert len(views) == len(league_raw["teams"])

    def test_each_view_has_required_keys(self, league_raw: dict) -> None:
        views = analyze.build_team_views(league_raw)
        required = {"team_id", "abbrev", "name", "record", "roster", "bucket_proj"}
        for v in views:
            assert required.issubset(v.keys()), f"missing keys on team {v.get('team_id')}"

    def test_bucket_proj_keys(self, league_raw: dict) -> None:
        views = analyze.build_team_views(league_raw)
        assert set(views[0]["bucket_proj"].keys()) == {"G", "F", "C"}

    def test_record_is_normalized(self, league_raw: dict) -> None:
        views = analyze.build_team_views(league_raw)
        record = views[0]["record"]
        assert {"wins", "losses", "ties", "pct"}.issubset(record.keys())
        assert isinstance(record["wins"], int)

    def test_handles_non_dense_team_ids(self) -> None:
        # Regression for the 50-40-90 Club scar: team IDs are [1,2,5,6,7,8,9,10]
        # (gaps from prior-season drops). Code must not loop 1..N or assume
        # team_id == position-in-array.
        league = {
            "scoringPeriodId": 10,
            "teams": [
                {"id": 1,  "abbrev": "A", "name": "A", "record": {"overall": {"wins": 0, "losses": 0, "ties": 0, "percentage": 0}}, "roster": {"entries": []}},
                {"id": 7,  "abbrev": "G", "name": "G", "record": {"overall": {"wins": 0, "losses": 0, "ties": 0, "percentage": 0}}, "roster": {"entries": []}},
                {"id": 10, "abbrev": "J", "name": "J", "record": {"overall": {"wins": 0, "losses": 0, "ties": 0, "percentage": 0}}, "roster": {"entries": []}},
            ],
            "settings": {"acquisitionSettings": {}},
        }
        views = analyze.build_team_views(league)
        ids = [v["team_id"] for v in views]
        assert ids == [1, 7, 10], "team_id must be preserved verbatim, not re-indexed"
        # Weakness math must use the actual IDs, not 0..len-1.
        weakness = analyze.compute_team_weakness(views)
        assert set(weakness.keys()) == {1, 7, 10}


class TestComputeTeamWeakness:
    def test_keys_per_team(self, league_raw: dict) -> None:
        views = analyze.build_team_views(league_raw)
        weakness = analyze.compute_team_weakness(views)
        assert len(weakness) == len(views)

    def test_weakness_payload_shape(self, league_raw: dict) -> None:
        views = analyze.build_team_views(league_raw)
        weakness = analyze.compute_team_weakness(views)
        sample = next(iter(weakness.values()))
        required = {
            "guard_proj", "forward_proj", "center_proj",
            "guard_gap_vs_league", "forward_gap_vs_league", "center_gap_vs_league",
            "weakest_bucket", "league_avg",
        }
        assert required.issubset(sample.keys())
        assert sample["weakest_bucket"] in ("G", "F", "C")

    def test_gaps_sum_to_zero_across_league(self, league_raw: dict) -> None:
        # Sum of (team_proj - league_avg) across all teams should be ~0 per bucket
        # (modulo rounding to 2 decimals).
        views = analyze.build_team_views(league_raw)
        weakness = analyze.compute_team_weakness(views)
        for bucket_key in ("guard_gap_vs_league", "forward_gap_vs_league", "center_gap_vs_league"):
            total = sum(w[bucket_key] for w in weakness.values())
            assert abs(total) < 0.05, f"{bucket_key} sums to {total}, expected ~0"


class TestRankFreeAgents:
    def test_ranks_by_projected_points(self, free_agents_raw: dict) -> None:
        ranked = analyze.rank_free_agents(free_agents_raw, scoring_period_id=10, limit=10)
        scores = [r["base_score"] for r in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_top_pick_is_test_forward_top(self, free_agents_raw: dict) -> None:
        # The fixture sets Test Forward Top to 41.5 projected — highest.
        ranked = analyze.rank_free_agents(free_agents_raw, scoring_period_id=10, limit=10)
        assert ranked[0]["name"] == "Test Forward Top"
        assert ranked[0]["base_score"] == 41.5
        assert ranked[0]["bucket"] == "F"

    def test_falls_back_to_season_avg_when_no_period_projection(self) -> None:
        # Player with only season projection should still appear with that avg.
        fas = {
            "players": [{
                "player": {
                    "id": 1,
                    "fullName": "Season Only",
                    "defaultPositionId": 2,
                    "eligibleSlots": [2, 3, 4],
                    "proTeamId": 9,
                    "ownership": {"percentOwned": 0.0, "percentChange": 0.0},
                    "stats": [
                        # Season-level projection only.
                        {"statSourceId": 1, "statSplitTypeId": 0, "scoringPeriodId": 0,
                         "appliedTotal": 200, "appliedAverage": 20.0}
                    ],
                }
            }]
        }
        ranked = analyze.rank_free_agents(fas, scoring_period_id=10)
        assert len(ranked) == 1
        assert ranked[0]["base_score"] == 20.0

    def test_limit_caps_result(self, free_agents_raw: dict) -> None:
        ranked = analyze.rank_free_agents(free_agents_raw, scoring_period_id=10, limit=2)
        assert len(ranked) == 2


class TestWaiverTargetsForTeam:
    def test_no_boost_when_team_strong_everywhere(self, free_agents_raw: dict) -> None:
        weakness = {
            "guard_gap_vs_league": 5.0,
            "forward_gap_vs_league": 5.0,
            "center_gap_vs_league": 5.0,
        }
        ranked = analyze.rank_free_agents(free_agents_raw, scoring_period_id=10, limit=10)
        boosted = analyze.waiver_targets_for_team(weakness, ranked)
        assert all(r["team_bonus"] == 0.0 for r in boosted)
        assert all(r["adjusted_score"] == r["base_score"] for r in boosted)

    def test_boost_lifts_weak_position_players(self, free_agents_raw: dict) -> None:
        # Team is 20 points behind at center — center pickups should boost.
        weakness = {
            "guard_gap_vs_league": 0.0,
            "forward_gap_vs_league": 0.0,
            "center_gap_vs_league": -20.0,
        }
        ranked = analyze.rank_free_agents(free_agents_raw, scoring_period_id=10, limit=10)
        boosted = analyze.waiver_targets_for_team(weakness, ranked)
        for r in boosted:
            if r["bucket"] == "C":
                assert r["team_bonus"] > 0
                assert r["adjusted_score"] > r["base_score"]
            else:
                assert r["team_bonus"] == 0.0

    def test_boost_capped_at_half_base_score(self) -> None:
        # Make sure a 100-point gap doesn't elevate a 1-point bench center
        # above legitimate top picks.
        weakness = {
            "guard_gap_vs_league": 0.0,
            "forward_gap_vs_league": 0.0,
            "center_gap_vs_league": -100.0,
        }
        ranked = [{
            "player_id": 1, "name": "Tiny Center", "pro_team_id": 9,
            "position": "C", "bucket": "C", "eligible_slots": [3, 5],
            "injury_status": "ACTIVE",
            "projected_points_next_period": 2.0, "season_avg_points": 2.0,
            "percent_owned": 0.0, "percent_change": 0.0,
            "base_score": 2.0,
        }]
        boosted = analyze.waiver_targets_for_team(weakness, ranked)
        # bonus capped at 2.0 * 0.5 = 1.0 even though raw bonus would be 5.0.
        assert boosted[0]["team_bonus"] == 1.0
        assert boosted[0]["adjusted_score"] == 3.0


class TestNormalizeTransactions:
    def test_returns_sorted_descending(self, league_raw: dict) -> None:
        txns = analyze.normalize_transactions(league_raw)
        if len(txns) >= 2:
            assert txns[0]["occurred_at"] >= txns[1]["occurred_at"]

    def test_no_owner_fields_present(self, league_raw: dict) -> None:
        # Even if ESPN sent memberId, our normalizer never extracts it.
        txns = analyze.normalize_transactions(league_raw)
        for tx in txns:
            assert "memberId" not in tx
            assert "isActingAsTeamOwner" not in tx
            assert "isLeagueManager" not in tx
