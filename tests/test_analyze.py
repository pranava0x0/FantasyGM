"""Tests for the analysis layer.

The fixtures here are committed:
- tests/fixtures/league_sample.json — slim slice of the real ESPN response
- tests/fixtures/free_agents_sample.json — hand-crafted, exercises ranking

These tests document the *math contract* — if any assertion changes, that's
a flag for a deliberate decision, not an incidental side effect.
"""

from __future__ import annotations

import pytest

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
        # Needs math must use the actual IDs, not 0..len-1.
        needs = analyze.compute_team_needs(views)
        assert set(needs.keys()) == {1, 7, 10}

    def test_next_week_fields_default_to_zero_without_games_map(self, league_raw: dict) -> None:
        # Omitting games_by_pro_team_next_week (legacy/tests) must not crash —
        # the UI's start/sit recommendation just sees zero next-week production.
        views = analyze.build_team_views(league_raw)
        roster = views[0]["roster"]
        assert roster, "fixture team should have a roster"
        for p in roster:
            assert p["games_next_week"] == 0
            assert p["projected_points_next_week"] == 0.0

    def test_next_week_fields_use_next_week_games_map(self, league_raw: dict) -> None:
        # Alyssa Thomas (proTeamId 11) is on team 1 in the fixture. 3 games
        # next week at her per-game rate should flow through to the roster
        # entry the same way games_this_week does today.
        views = analyze.build_team_views(
            league_raw,
            games_by_pro_team={11: 2},
            games_by_pro_team_next_week={11: 3},
        )
        team1_roster = next(v["roster"] for v in views if v["team_id"] == 1)
        thomas = next(p for p in team1_roster if p["name"] == "Alyssa Thomas")
        assert thomas["games_next_week"] == 3
        assert thomas["projected_points_next_week"] == round(thomas["projected_per_game"] * 3, 2)


class TestComputeTeamNeeds:
    def test_keys_per_team(self, league_raw: dict) -> None:
        views = analyze.build_team_views(league_raw)
        needs = analyze.compute_team_needs(views)
        assert len(needs) == len(views)

    def test_needs_payload_shape(self, league_raw: dict) -> None:
        views = analyze.build_team_views(league_raw)
        needs = analyze.compute_team_needs(views)
        sample = next(iter(needs.values()))
        required = {
            "guard_proj", "forward_proj", "center_proj", "frontcourt_proj",
            "guard_gap_vs_league", "forward_gap_vs_league",
            "center_gap_vs_league", "frontcourt_gap_vs_league",
            "top_need_bucket", "league_avg",
        }
        assert required.issubset(sample.keys())
        # WNBA fantasy uses shared F/C slots — top need is G vs combined frontcourt.
        assert sample["top_need_bucket"] in ("G", "FC")
        # Frontcourt math must be the sum, not something derived elsewhere.
        assert sample["frontcourt_proj"] == round(sample["forward_proj"] + sample["center_proj"], 2)

    def test_gaps_sum_to_zero_across_league(self, league_raw: dict) -> None:
        # Sum of (team_proj - league_avg) across all teams should be ~0 per bucket
        # (modulo rounding to 2 decimals).
        views = analyze.build_team_views(league_raw)
        needs = analyze.compute_team_needs(views)
        for bucket_key in ("guard_gap_vs_league", "forward_gap_vs_league", "center_gap_vs_league"):
            total = sum(w[bucket_key] for w in needs.values())
            assert abs(total) < 0.05, f"{bucket_key} sums to {total}, expected ~0"


class TestRankFreeAgents:
    def test_ranks_by_projected_points(self, free_agents_raw: dict) -> None:
        ranked = analyze.rank_free_agents(free_agents_raw, scoring_period_id=10, limit=10)
        scores = [r["base_score"] for r in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_top_pick_is_test_forward_top(self, free_agents_raw: dict) -> None:
        # The fixture sets Test Forward Top to 41.5 projected — highest.
        # With no games_by_pro_team provided we fall back to the single-period
        # projection, so the legacy contract still holds.
        ranked = analyze.rank_free_agents(free_agents_raw, scoring_period_id=10, limit=10)
        assert ranked[0]["name"] == "Test Forward Top"
        assert ranked[0]["base_score"] == 41.5
        assert ranked[0]["bucket"] == "F"

    def test_games_this_week_drives_ranking(self, free_agents_raw: dict) -> None:
        # Fixture players' proTeamIds: 9 (NY), 11 (PHX), 17 (LV), 20 (IND), 14 (SEA).
        # We give NY 4 games and everyone else 1 — Test Guard High (NY) should
        # leap to the top, even though Test Forward Top has a higher base proj.
        games = {9: 4, 11: 1, 14: 1, 17: 1, 20: 1}
        ranked = analyze.rank_free_agents(
            free_agents_raw, scoring_period_id=10, limit=10, games_by_pro_team=games,
        )
        assert ranked[0]["name"] == "Test Guard High"
        assert ranked[0]["games_this_week"] == 4
        # Forward Top still has its per-game number, but multiplied by 1 game
        # it ranks lower than Guard High's 4-game total.
        forward_top = next(r for r in ranked if r["name"] == "Test Forward Top")
        assert forward_top["games_this_week"] == 1
        assert ranked[0]["base_score"] > forward_top["base_score"]

    def test_next_week_games_drive_ranking(self, free_agents_raw: dict) -> None:
        # Sort key is (projected_points_next_week, base_score): next week first,
        # this week as tiebreaker. Give NY the same single game this week as
        # everyone but two games NEXT week — Test Guard High (NY) must lead even
        # though Test Forward Top has the higher single-game base projection.
        games = {9: 1, 11: 1, 14: 1, 17: 1, 20: 1}
        games_nw = {9: 2, 11: 1, 14: 1, 17: 1, 20: 1}
        ranked = analyze.rank_free_agents(
            free_agents_raw, scoring_period_id=10, limit=10,
            games_by_pro_team=games, games_by_pro_team_next_week=games_nw,
        )
        assert ranked[0]["name"] == "Test Guard High"
        assert ranked[0]["games_next_week"] == 2
        # Next-week projection is the primary key, so the leader's must be the max.
        assert ranked[0]["projected_points_next_week"] == max(
            r["projected_points_next_week"] for r in ranked
        )

    def test_zero_games_zeroes_the_week_proj(self, free_agents_raw: dict) -> None:
        # A bye-week player must not be elevated by upstream needs boosts.
        games = {9: 0, 11: 0, 14: 0, 17: 0, 20: 0}
        ranked = analyze.rank_free_agents(
            free_agents_raw, scoring_period_id=10, limit=10, games_by_pro_team=games,
        )
        assert all(r["base_score"] == 0.0 for r in ranked)

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


class TestRankFreeAgentsOutFilter:
    def test_out_players_excluded(self) -> None:
        fas = {
            "players": [
                {
                    "player": {
                        "id": 1, "fullName": "Healthy Guard", "defaultPositionId": 1,
                        "eligibleSlots": [1, 6, 7], "proTeamId": 9,
                        "injuryStatus": "ACTIVE",
                        "ownership": {"percentOwned": 10.0, "percentChange": 0.0},
                        "stats": [{"statSourceId": 1, "statSplitTypeId": 0,
                                   "scoringPeriodId": 0, "appliedTotal": 300, "appliedAverage": 20.0}],
                    }
                },
                {
                    "player": {
                        "id": 2, "fullName": "Injured Center", "defaultPositionId": 3,
                        "eligibleSlots": [3, 5, 6, 7], "proTeamId": 9,
                        "injuryStatus": "OUT",
                        "ownership": {"percentOwned": 40.0, "percentChange": 0.0},
                        "stats": [{"statSourceId": 1, "statSplitTypeId": 0,
                                   "scoringPeriodId": 0, "appliedTotal": 600, "appliedAverage": 40.0}],
                    }
                },
                {
                    "player": {
                        "id": 3, "fullName": "IR Forward", "defaultPositionId": 2,
                        "eligibleSlots": [2, 4, 6, 7], "proTeamId": 9,
                        "injuryStatus": "INJURY_RESERVE",
                        "ownership": {"percentOwned": 5.0, "percentChange": 0.0},
                        "stats": [{"statSourceId": 1, "statSplitTypeId": 0,
                                   "scoringPeriodId": 0, "appliedTotal": 400, "appliedAverage": 25.0}],
                    }
                },
            ]
        }
        ranked = analyze.rank_free_agents(fas, scoring_period_id=10)
        assert len(ranked) == 1
        assert ranked[0]["name"] == "Healthy Guard"

    def test_dtd_players_included(self) -> None:
        fas = {
            "players": [{
                "player": {
                    "id": 1, "fullName": "Day To Day", "defaultPositionId": 1,
                    "eligibleSlots": [1, 6, 7], "proTeamId": 9,
                    "injuryStatus": "DAY_TO_DAY",
                    "ownership": {"percentOwned": 5.0, "percentChange": 0.0},
                    "stats": [{"statSourceId": 1, "statSplitTypeId": 0,
                               "scoringPeriodId": 0, "appliedTotal": 200, "appliedAverage": 15.0}],
                }
            }]
        }
        ranked = analyze.rank_free_agents(fas, scoring_period_id=10)
        assert len(ranked) == 1
        assert ranked[0]["name"] == "Day To Day"


class TestWaiverTargetsForTeam:
    def test_no_boost_when_team_strong_everywhere(self, free_agents_raw: dict) -> None:
        needs = {
            "guard_gap_vs_league": 5.0,
            "frontcourt_gap_vs_league": 5.0,
        }
        ranked = analyze.rank_free_agents(free_agents_raw, scoring_period_id=10, limit=10)
        boosted = analyze.waiver_targets_for_team(needs, ranked)
        assert all(r["team_bonus"] == 0.0 for r in boosted)
        # When no boost AND no saturation, adjusted = base.
        assert all(r["adjusted_score"] == r["base_score"] for r in boosted)

    def test_severe_gap_uses_larger_weight(self, free_agents_raw: dict) -> None:
        # Severe (-15) frontcourt gap: weight 0.20 → bonus = 15 * 0.20 = 3.0
        # (subject to 75% base-score cap).
        needs = {
            "guard_gap_vs_league": 0.0,
            "frontcourt_gap_vs_league": -15.0,
        }
        ranked = analyze.rank_free_agents(free_agents_raw, scoring_period_id=10, limit=10)
        boosted = analyze.waiver_targets_for_team(needs, ranked)
        fc_targets = [r for r in boosted if r["bucket"] in ("F", "C")]
        assert fc_targets, "expected at least one FC target in the FA pool"
        for r in fc_targets:
            expected_raw = 15.0 * 0.20
            expected_capped = min(expected_raw, r["base_score"] * 0.75)
            assert r["team_bonus"] == pytest.approx(round(expected_capped, 2))

    def test_boost_capped_at_75pct_base_score(self) -> None:
        # 100-pt gap must not catapult a 2-pt bench player above legit picks.
        needs = {
            "guard_gap_vs_league": 0.0,
            "frontcourt_gap_vs_league": -100.0,
        }
        ranked = [{
            "player_id": 1, "name": "Tiny Center", "pro_team_id": 9,
            "position": "C", "bucket": "C", "eligible_slots": [3, 5],
            "injury_status": "ACTIVE",
            "projected_points_next_period": 2.0, "season_avg_points": 2.0,
            "percent_owned": 0.0, "percent_change": 0.0,
            "base_score": 2.0,
        }]
        boosted = analyze.waiver_targets_for_team(needs, ranked)
        # bonus capped at base * 0.75 = 1.5.
        assert boosted[0]["team_bonus"] == 1.5
        assert boosted[0]["adjusted_score"] == 3.5

    def test_saturation_penalty_on_overloaded_bucket(self) -> None:
        # Team with 7 active guards should see guard picks penalized.
        needs = {"guard_gap_vs_league": 0.0, "frontcourt_gap_vs_league": 0.0}
        ranked = [
            {"player_id": 1, "name": "G One", "pro_team_id": 9, "position": "G",
             "bucket": "G", "eligible_slots": [1], "injury_status": "ACTIVE",
             "projected_points_next_period": 20.0, "season_avg_points": 20.0,
             "percent_owned": 0.0, "percent_change": 0.0, "base_score": 20.0},
            {"player_id": 2, "name": "F One", "pro_team_id": 9, "position": "F",
             "bucket": "F", "eligible_slots": [4], "injury_status": "ACTIVE",
             "projected_points_next_period": 18.0, "season_avg_points": 18.0,
             "percent_owned": 0.0, "percent_change": 0.0, "base_score": 18.0},
        ]
        boosted = analyze.waiver_targets_for_team(
            needs, ranked, active_counts={"G": 7, "F": 2, "C": 0},
        )
        # F One now ranks above G One (20 base vs 18 base, but G penalized -6).
        assert boosted[0]["name"] == "F One"
        g_row = next(r for r in boosted if r["name"] == "G One")
        assert g_row["saturation_penalty"] == -6.0  # -30% of 20
        assert g_row["adjusted_score"] == 14.0

    def test_no_active_counts_means_no_saturation(self, free_agents_raw: dict) -> None:
        # Legacy callers (no active_counts) get no saturation penalty.
        needs = {"guard_gap_vs_league": 0.0, "frontcourt_gap_vs_league": 0.0}
        ranked = analyze.rank_free_agents(free_agents_raw, scoring_period_id=10, limit=10)
        boosted = analyze.waiver_targets_for_team(needs, ranked)
        assert all(r["saturation_penalty"] == 0.0 for r in boosted)


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
