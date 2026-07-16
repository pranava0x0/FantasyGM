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
