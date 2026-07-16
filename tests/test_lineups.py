"""Tests for the current-vs-optimal lineup diff.

The load-bearing properties, in the order a wrong answer would hurt:

1. We never tell the user to start a player who is confirmed OUT.
2. We never tell the user to move a player whose game already tipped off
   (ESPN would refuse the move).
3. `points_left_on_bench` equals what the recommended moves actually deliver
   — a headline that promises points the moves list can't capture is worse
   than no headline.
4. The move list is *minimal*: no churn moves for noise-level gains.
5. The pipeline's slot plan and out-statuses match the frontend's copies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

import pytest

from pipeline import lineups
from pipeline.positions import ACTIVE_SLOT_PLAN, CONFIRMED_OUT_STATUSES
from pipeline.schedule import ProGame

NOW = datetime(2026, 7, 6, 16, 0, tzinfo=timezone.utc)
PERIOD = 59
WEEK = (60, 66)


def _player(
    pid: int,
    name: str,
    bucket: str,
    *,
    slot: int,
    per_game: float = 20.0,
    week_pts: float | None = None,
    games_this_week: int = 3,
    injury: str = "ACTIVE",
    pro_team_id: int = 11,
) -> dict:
    """One `build_team_views`-shaped roster row."""
    return {
        "player_id": pid,
        "name": name,
        "bucket": bucket,
        "lineup_slot_id": slot,
        "lineup_slot_label": {1: "G", 4: "F", 5: "F/C", 6: "UTIL", 7: "BE"}[slot],
        "is_active": slot != 7,
        "injury_status": injury,
        "pro_team_id": pro_team_id,
        "projected_per_game": per_game,
        "projected_points_this_week": per_game * games_this_week if week_pts is None else week_pts,
        "games_this_week": games_this_week,
    }


def _game(period: int = PERIOD, *, start: datetime | None = None, lockable: bool = True) -> ProGame:
    return ProGame(
        scoring_period_id=period,
        start_time=start if start is not None else NOW + timedelta(hours=3),
        opponent_pro_team_id=17,
        is_home=True,
        valid_for_locking=lockable,
    )


def _schedule(*pro_team_ids: int, **kw) -> dict[int, dict[int, list[ProGame]]]:
    """Everyone named plays one game in PERIOD."""
    return {pid: {PERIOD: [_game(**kw)]} for pid in pro_team_ids}


def _full_roster(**overrides) -> list[dict]:
    """Nine players filling the nine active slots, plus a benched star.

    The bench player (Bench Star, 40/g) out-projects every starter, so a
    correct check always wants her in.
    """
    roster = [
        _player(1, "Guard One", "G", slot=1, per_game=30),
        _player(2, "Guard Two", "G", slot=1, per_game=28),
        _player(3, "Fwd One", "F", slot=4, per_game=26),
        _player(4, "Fwd Two", "F", slot=4, per_game=24),
        _player(5, "Fwd Three", "F", slot=4, per_game=22),
        _player(6, "Center One", "C", slot=5, per_game=20),
        _player(7, "Util One", "G", slot=6, per_game=18),
        _player(8, "Util Two", "F", slot=6, per_game=16),
        _player(9, "Util Three", "C", slot=6, per_game=14),
        _player(10, "Bench Star", "F", slot=7, per_game=40),
    ]
    for pid, attrs in (overrides or {}).items():
        for r in roster:
            if r["player_id"] == int(pid):
                r.update(attrs)
    return roster


def _check(roster, horizon="week", schedule=None, now=NOW):
    return lineups.check_lineup(
        roster,
        horizon=horizon,
        period=PERIOD if horizon == "tonight" else WEEK[0],
        games_by_period=schedule if schedule is not None else _schedule(11),
        now=now,
    )


class TestNeverStartsAnOutPlayer:
    def test_out_player_is_not_recommended_in(self) -> None:
        # The bench star is the best player on the roster but is OUT.
        roster = _full_roster()
        roster[-1]["injury_status"] = "OUT"
        res = _check(roster)
        assert res is not None
        assert all(m["player_in_id"] != 10 for m in res["moves"])

    @pytest.mark.parametrize("status", sorted(CONFIRMED_OUT_STATUSES))
    def test_every_out_status_blocks_a_start(self, status: str) -> None:
        roster = _full_roster()
        roster[-1]["injury_status"] = status
        res = _check(roster)
        assert res is not None
        assert all(m["player_in_id"] != 10 for m in res["moves"]), f"{status} was started"

    def test_questionable_player_stays_eligible(self) -> None:
        # DTD / QUESTIONABLE may still play — they must not be filtered out.
        roster = _full_roster()
        roster[-1]["injury_status"] = "QUESTIONABLE"
        res = _check(roster)
        assert res is not None
        assert any(m["player_in_id"] == 10 for m in res["moves"])

    def test_out_starter_is_benched_for_a_healthy_sub(self) -> None:
        # The real case seen in the 2026-07-06 snapshot: an OUT player left
        # in an active slot while a healthy bench player sits.
        roster = _full_roster()
        roster[0]["injury_status"] = "OUT"          # Guard One, starting
        res = _check(roster)
        assert res is not None
        assert res["status"] == "moves_available"
        assert any(m["player_out_id"] == 1 and m["player_out_reason"] == "OUT" for m in res["moves"])


class TestLocks:
    def test_locked_player_is_not_moved(self) -> None:
        # Every game already tipped off an hour ago.
        started = _schedule(11, start=NOW - timedelta(hours=1))
        roster = _full_roster()
        res = _check(roster, horizon="tonight", schedule=started)
        assert res is not None
        assert res["status"] == "set"
        assert res["moves"] == []
        assert 10 in res["locked_player_ids"]

    def test_unlocked_player_is_moved(self) -> None:
        upcoming = _schedule(11, start=NOW + timedelta(hours=2))
        res = _check(_full_roster(), horizon="tonight", schedule=upcoming)
        assert res is not None
        assert res["status"] == "moves_available"
        assert res["locked_player_ids"] == []

    def test_tbd_start_time_counts_as_unlocked(self) -> None:
        # An unknown tip-off must not silently withhold an available move.
        tbd = _schedule(11, start=None)
        res = _check(_full_roster(), horizon="tonight", schedule=tbd)
        assert res is not None
        assert res["locked_player_ids"] == []

    def test_non_lockable_game_never_locks(self) -> None:
        started_but_unlockable = _schedule(11, start=NOW - timedelta(hours=1), lockable=False)
        res = _check(_full_roster(), horizon="tonight", schedule=started_but_unlockable)
        assert res is not None
        assert res["locked_player_ids"] == []

    def test_week_horizon_pins_nobody(self) -> None:
        # Over a week there is always a later game, so locks don't apply.
        started = _schedule(11, start=NOW - timedelta(hours=1))
        res = _check(_full_roster(), horizon="week", schedule=started)
        assert res is not None
        assert res["locked_player_ids"] == []
        assert res["status"] == "moves_available"


class TestMinimality:
    def test_no_moves_when_lineup_is_optimal(self) -> None:
        roster = _full_roster()
        roster.pop()  # drop the bench star — the nine starters are the best nine
        res = _check(roster)
        assert res is not None
        assert res["status"] == "set"
        assert res["moves"] == []
        assert res["points_left_on_bench"] == 0.0

    def test_sub_threshold_gain_generates_no_churn(self) -> None:
        # Bench player is better than the worst starter by less than the
        # MIN_SWAP_GAIN_PTS threshold — advising the swap is pure churn.
        roster = _full_roster()
        roster[-1]["per_game"] = 14.1
        roster[-1].update(_player(10, "Bench Star", "F", slot=7, per_game=14.0, games_this_week=3))
        roster[-1]["projected_points_this_week"] = 42.1  # vs Util Three's 42.0
        res = _check(roster)
        assert res is not None
        assert res["moves"] == []
        assert res["status"] == "set"

    def test_one_upgrade_yields_exactly_one_move(self) -> None:
        res = _check(_full_roster())
        assert res is not None
        assert len(res["moves"]) == 1
        move = res["moves"][0]
        assert move["player_in_id"] == 10
        assert move["player_out_id"] == 9      # the weakest starter
        assert move["action"] == "swap"

    def test_moves_are_sorted_by_gain_descending(self) -> None:
        roster = _full_roster()
        roster.append(_player(11, "Bench Star Two", "G", slot=7, per_game=35))
        res = _check(roster)
        assert res is not None
        gains = [m["gain_pts"] for m in res["moves"]]
        assert gains == sorted(gains, reverse=True)


class TestPointsLeftOnBench:
    @pytest.mark.parametrize("horizon", ["week", "tonight"])
    def test_equals_sum_of_move_gains(self, horizon: str) -> None:
        res = _check(_full_roster(), horizon=horizon)
        assert res is not None
        assert res["points_left_on_bench"] == pytest.approx(
            sum(m["gain_pts"] for m in res["moves"]), abs=0.02
        )

    @pytest.mark.parametrize("horizon", ["week", "tonight"])
    def test_equals_optimal_minus_current(self, horizon: str) -> None:
        # When every gap clears the churn threshold, the advertised number is
        # exactly the lineup improvement — no points quietly unaccounted for.
        res = _check(_full_roster(), horizon=horizon)
        assert res is not None
        assert res["points_left_on_bench"] == pytest.approx(
            res["optimal_points"] - res["current_points"], abs=0.02
        )


class TestEmptySlot:
    """Regression: a roster shorter than nine leaves an active slot empty.

    Found against the real 2026-07-06 snapshot — team Nut carried 9 players
    with 8 starting, so the fix was "start Mabrey" with nobody benched. The
    first implementation paired ins to outs with zip(), so a move with no
    counterpart was silently dropped: the check reported "lineup set" while
    also reporting 95.7 points of headroom.
    """

    def test_empty_slot_yields_a_start_move(self) -> None:
        roster = _full_roster()
        roster.pop(8)  # 9 players, 8 active slots filled, one slot empty
        res = _check(roster)
        assert res is not None
        assert res["status"] == "moves_available"
        assert len(res["moves"]) == 1
        move = res["moves"][0]
        assert move["action"] == "start"
        assert move["player_in_id"] == 10
        assert move["player_out_id"] is None
        assert move["player_out_name"] is None
        assert move["player_out_reason"] == "empty slot"

    def test_start_move_gain_is_the_full_score(self) -> None:
        roster = _full_roster()
        roster.pop(8)
        res = _check(roster)
        assert res is not None
        # Bench Star: 40/g × 3 games = 120 points, gained from nothing.
        assert res["moves"][0]["gain_pts"] == pytest.approx(120.0, abs=0.02)

    def test_status_never_contradicts_headroom(self) -> None:
        # The actual symptom of the bug: "set" alongside non-zero headroom.
        roster = _full_roster()
        roster.pop(8)
        res = _check(roster)
        assert res is not None
        headroom = res["optimal_points"] - res["current_points"]
        assert not (res["status"] == "set" and headroom > lineups.MIN_SWAP_GAIN_PTS)


class TestTonightHorizon:
    def test_returns_none_when_nobody_plays(self) -> None:
        # An off day has no lineup decision; an empty panel is worse than none.
        assert _check(_full_roster(), horizon="tonight", schedule={}) is None

    def test_week_horizon_always_returns_a_check(self) -> None:
        assert _check(_full_roster(), horizon="week", schedule={}) is not None

    def test_player_without_a_game_tonight_scores_zero(self) -> None:
        # Bench Star (40/g) plays; the starters are on a team that doesn't.
        roster = _full_roster()
        for r in roster[:-1]:
            r["pro_team_id"] = 99
        res = _check(roster, horizon="tonight", schedule=_schedule(11))
        assert res is not None
        assert res["current_points"] == 0.0
        assert any(m["player_out_reason"] == "no game" for m in res["moves"])

    def test_week_horizon_uses_week_games_not_one_period(self) -> None:
        # Regression: judging "no game" from a single period would label a
        # 3-games-this-week player as gameless just because she's idle on the
        # window's first day.
        roster = _full_roster()
        res = _check(roster, horizon="week", schedule={})
        assert res is not None
        assert all(m["player_out_reason"] != "no game" for m in res["moves"])

    def test_game_time_is_attached_tonight_only(self) -> None:
        tonight = _check(_full_roster(), horizon="tonight")
        week = _check(_full_roster(), horizon="week", schedule={})
        assert tonight is not None and week is not None
        assert tonight["moves"][0]["player_in_game_time"] is not None
        assert week["moves"][0]["player_in_game_time"] is None


class TestOptimalLineup:
    def test_fills_nine_slots(self) -> None:
        active, bench = lineups.optimal_lineup(_full_roster(), lambda e: e["projected_points_this_week"])
        assert len(active) == 9
        assert len(bench) == 1

    def test_respects_slot_eligibility(self) -> None:
        active, _ = lineups.optimal_lineup(_full_roster(), lambda e: e["projected_points_this_week"])
        by_slot: dict[str, list[str]] = {}
        for entry, slot in active:
            by_slot.setdefault(slot, []).append(entry["bucket"])
        assert all(b == "G" for b in by_slot["G"])
        assert all(b == "F" for b in by_slot["F"])
        assert all(b in ("F", "C") for b in by_slot["F/C"])

    def test_zero_scored_players_are_never_started(self) -> None:
        # A zero means "no game" or "no data"; starting her over an empty slot
        # gains nothing while looking like a real recommendation.
        roster = [_player(i, f"P{i}", "G", slot=7, per_game=0.0) for i in range(1, 5)]
        active, bench = lineups.optimal_lineup(roster, lambda e: e["projected_points_this_week"])
        assert active == []
        assert len(bench) == 4

    def test_pinned_player_keeps_her_slot(self) -> None:
        roster = _full_roster()
        active, _ = lineups.optimal_lineup(
            roster, lambda e: e["projected_points_this_week"], pinned={9: "UTIL"}
        )
        assert (9, "UTIL") in [(e["player_id"], slot) for e, slot in active]


class TestNetGainForAdd:
    """The reframing in spec §3.A1: what she *adds*, not what she projects."""

    def test_saturated_position_collapses_the_gain(self) -> None:
        # The headline case. A 30/g Guard is worth ~nothing to a team whose
        # optimal nine already contains better Guards everywhere she'd fit —
        # raw projection can't express that, a lineup diff can.
        roster = _full_roster()
        weak_guard = _player(99, "Fringe Guard", "G", slot=7, per_game=5)
        assert lineups.net_gain_for_add(roster, weak_guard) == 0.0

    def test_real_upgrade_yields_positive_gain(self) -> None:
        roster = _full_roster()
        star = _player(99, "Star", "F", slot=7, per_game=50)
        # She displaces the weakest *starter* — Util Two at 16/g — not the
        # roster's weakest player (Util Three, 14/g), who was already sitting
        # behind Bench Star. The gain is that difference over 3 games, never
        # her full projection.
        assert lineups.net_gain_for_add(roster, star) == pytest.approx(
            (50 - 16) * 3, abs=0.02
        )

    def test_empty_slot_gains_her_whole_projection(self) -> None:
        roster = _full_roster()[:8]      # 8 players, 9 slots
        add = _player(99, "Add", "F", slot=7, per_game=10)
        assert lineups.net_gain_for_add(roster, add) == pytest.approx(30.0, abs=0.02)

    def test_out_candidate_gains_nothing(self) -> None:
        roster = _full_roster()[:8]
        hurt = _player(99, "Hurt Star", "F", slot=7, per_game=50, injury="OUT")
        assert lineups.net_gain_for_add(roster, hurt) == 0.0

    def test_gain_is_never_negative(self) -> None:
        # Adding a player can only ever leave the optimal lineup alone or
        # improve it — she's optional.
        roster = _full_roster()
        for rate in (0, 1, 14, 15, 100):
            add = _player(99, "X", "G", slot=7, per_game=rate)
            assert lineups.net_gain_for_add(roster, add) >= 0.0, rate


class TestDropCandidate:
    def test_picks_the_player_the_lineup_misses_least(self) -> None:
        roster = _full_roster()
        star = _player(99, "Star", "F", slot=7, per_game=50)
        drop = lineups.drop_candidate(roster, star)
        assert drop is not None
        # Adding the star pushes *two* players out of the optimal nine (Util
        # Two, 16/g and Util Three, 14/g), so dropping either costs 0 this
        # week. The tie must resolve to the lower-rate player — same price
        # now, worse asset later.
        assert drop["player_id"] == 9
        assert drop["net_loss"] == 0.0

    def test_zero_loss_tie_breaks_to_the_lesser_player(self) -> None:
        # Regression: breaking the tie by roster order discarded the better
        # player for nothing.
        roster = _full_roster()
        star = _player(99, "Star", "F", slot=7, per_game=50)
        drop = lineups.drop_candidate(roster, star)
        assert drop is not None
        by_id = {e["player_id"]: e for e in roster}
        assert by_id[drop["player_id"]]["projected_per_game"] == 14

    def test_zero_loss_tie_prefers_an_injured_player(self) -> None:
        roster = _full_roster()
        roster[7]["injury_status"] = "OUT"     # Util Two — costs 0 either way
        star = _player(99, "Star", "F", slot=7, per_game=50)
        drop = lineups.drop_candidate(roster, star)
        assert drop is not None
        assert drop["player_id"] == 8, "kept an OUT player over a healthy one"

    def test_prefers_a_non_core_player_when_one_is_free(self) -> None:
        # Spec §3.A2: never propose dropping a top-6 player without warning.
        # With 7 players and core_rank=6, exactly one is expendable — and the
        # cheapest drop should be precisely that player.
        roster = _full_roster()[:7]
        star = _player(99, "Star", "F", slot=7, per_game=50)
        drop = lineups.drop_candidate(roster, star, core_rank=6)
        assert drop is not None
        assert drop["player_id"] == 7      # Util One, the only non-core player
        assert drop["is_core"] is False

    def test_warns_when_the_only_drop_is_a_core_player(self) -> None:
        roster = _full_roster()[:4]        # every player is top-6 by rate
        star = _player(99, "Star", "F", slot=7, per_game=50)
        drop = lineups.drop_candidate(roster, star, core_rank=6)
        assert drop is not None
        assert drop["is_core"] is True

    def test_core_is_ranked_by_season_rate_not_this_week(self) -> None:
        # A star on a 1-game week has low week value but dropping her is still
        # a mistake — the core flag must key off rate, not the weekly total.
        roster = _full_roster()
        roster[0]["games_this_week"] = 1
        roster[0]["projected_points_this_week"] = 30    # lowest weekly total
        star = _player(99, "Star", "F", slot=7, per_game=50)
        drop = lineups.drop_candidate(roster, star)
        assert drop is not None
        assert drop["player_id"] != 1, "dropped the roster's best player by rate"

    def test_empty_roster_has_no_drop(self) -> None:
        assert lineups.drop_candidate([], _player(99, "X", "G", slot=7)) is None

    def test_reports_availability_and_slate(self) -> None:
        roster = _full_roster()
        drop = lineups.drop_candidate(roster, _player(99, "Star", "F", slot=7, per_game=50))
        assert drop is not None
        assert "injury_status" in drop and "games_this_week" in drop

    def test_no_internal_rank_key_leaks(self) -> None:
        # The sort key is scaffolding; it must not reach the schema.
        drop = lineups.drop_candidate(_full_roster(), _player(99, "S", "F", slot=7, per_game=50))
        assert drop is not None
        assert "_rank" not in drop


class TestTagTarget:
    """Schedule-based, not rate-based — see tag_target's docstring for why."""

    def test_collapsing_slate_is_a_streamer(self) -> None:
        p = _player(1, "P", "G", slot=7, per_game=20, games_this_week=3)
        p["games_next_week"] = 2
        assert lineups.tag_target(p, reference_rate=16.0) == ["streamer"]

    def test_holding_slate_with_good_rate_is_an_anchor(self) -> None:
        p = _player(1, "P", "G", slot=7, per_game=20, games_this_week=3)
        p["games_next_week"] = 3
        assert lineups.tag_target(p, reference_rate=16.0) == ["anchor"]

    def test_holding_slate_with_weak_rate_is_untagged(self) -> None:
        # Saying nothing beats inventing a category for "not interesting".
        p = _player(1, "P", "G", slot=7, per_game=10, games_this_week=3)
        p["games_next_week"] = 3
        assert lineups.tag_target(p, reference_rate=16.0) == []

    def test_streamer_beats_anchor_when_both_could_match(self) -> None:
        # A high rate whose slate evaporates is still a churn candidate.
        p = _player(1, "P", "G", slot=7, per_game=40, games_this_week=4)
        p["games_next_week"] = 1
        assert lineups.tag_target(p, reference_rate=16.0) == ["streamer"]

    def test_thin_slate_both_weeks_is_untagged(self) -> None:
        p = _player(1, "P", "G", slot=7, per_game=40, games_this_week=2)
        p["games_next_week"] = 2
        assert lineups.tag_target(p, reference_rate=16.0) == []

    def test_no_projection_no_tag(self) -> None:
        p = _player(1, "P", "G", slot=7, per_game=0, games_this_week=3)
        p["games_next_week"] = 1
        assert lineups.tag_target(p, reference_rate=16.0) == []

    def test_tags_are_mutually_exclusive(self) -> None:
        for gtw in range(0, 5):
            for gnw in range(0, 5):
                p = _player(1, "P", "G", slot=7, per_game=20, games_this_week=gtw)
                p["games_next_week"] = gnw
                assert len(lineups.tag_target(p, reference_rate=16.0)) <= 1


class TestFrontendParity:
    """docs/assets/app.js mirrors two pipeline constants. Assert they match.

    CLAUDE.md: "If a value is duplicated, write a test that asserts the
    copies match." A drift here would make the page's advice contradict the
    pipeline's — the worst kind of bug, because both look self-consistent.
    """

    APP_JS = Path(__file__).resolve().parents[1] / "docs" / "assets" / "app.js"

    def test_slot_plan_matches(self) -> None:
        js = self.APP_JS.read_text()
        found = re.findall(r'fill\(\s*"([^"]+)"\s*,\s*(\d+)', js)
        assert found, "could not find optimalLineupSlots' fill() calls in app.js"
        assert [(label, int(n)) for label, n in found] == [
            (label, count) for label, count, _ in ACTIVE_SLOT_PLAN
        ]

    def test_out_statuses_match(self) -> None:
        js = self.APP_JS.read_text()
        m = re.search(r"CONFIRMED_OUT_STATUSES\s*=\s*new Set\(\[([^\]]*)\]", js)
        assert m, "could not find CONFIRMED_OUT_STATUSES in app.js"
        assert set(re.findall(r'"([^"]+)"', m.group(1))) == set(CONFIRMED_OUT_STATUSES)
