"""Current-vs-optimal lineup diff — the "reset my lineup" answer.

Every other module here asks "who is good?". This one asks the only question
the user acts on daily: **is my lineup already right, and if not, exactly
which swaps fix it?**

The output is deliberately a *minimal swap list*, not two lineups to eyeball:

    2 moves available tonight — 14.6 pts on your bench
      1. Start A. Gray (G) over K. Martin (no game)   +9.2
      2. Start T. Reid (F/C) over D. Carter (OUT)     +5.4

Design notes, in the order they bite:

- **Two horizons.** `tonight` scores a player at their per-game rate if their
  pro team plays in the current scoring period, else 0 — that's the daily
  streaming decision. `week` scores on projected points across the whole
  matchup window. They disagree constantly (that's the point: a 4-game player
  outranks a better 1-game player over a week but not tonight).
- **Locks are real.** WNBA lineups lock per-player at tip-off, so a player
  whose game already started cannot be moved. Locked players are pinned into
  whatever slot they currently occupy rather than dropped — pretending they're
  benchable would generate moves ESPN will refuse.
- **Advice only.** Nothing here writes to ESPN (see docs/gm-console-spec.md
  §5.1 — `lm-api-writes` has no public implementation and direct writes are a
  documented non-goal). The UI stages these moves behind a deep link.

The slot plan and the confirmed-out status set live in `positions.py` because
`docs/assets/app.js` mirrors both; `tests/test_lineups.py` asserts the copies
have not drifted.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Literal

from pipeline.positions import (
    ACTIVE_SLOT_IDS,
    ACTIVE_SLOT_PLAN,
    is_confirmed_out,
)
from pipeline.schedule import ProGame

log = logging.getLogger(__name__)

Horizon = Literal["tonight", "week"]

# A swap must be worth at least this many projected points to be recommended.
# Below it the "gain" is projection noise and the advice is pure churn — the
# user taps through to ESPN, makes two moves, and gains nothing. Ties never
# generate moves.
MIN_SWAP_GAIN_PTS: float = 0.5


def _score_tonight(entry: dict[str, Any], plays_tonight: bool) -> float:
    """Points a player is expected to score in the current period."""
    if not plays_tonight:
        return 0.0
    return float(entry.get("projected_per_game") or 0.0)


def _score_week(entry: dict[str, Any]) -> float:
    """Points a player is expected to score across the matchup week."""
    return float(entry.get("projected_points_this_week") or 0.0)


def optimal_lineup(
    roster: list[dict[str, Any]],
    score_fn: Callable[[dict[str, Any]], float],
    *,
    pinned: dict[int, str] | None = None,
) -> tuple[list[tuple[dict[str, Any], str]], list[dict[str, Any]]]:
    """Fill the nine active slots greedily by `score_fn`, best player first.

    Mirrors `optimalLineupSlots()` in docs/assets/app.js. Returns
    `(active, bench)` where `active` is [(entry, slot_label), ...].

    `pinned` maps player_id -> slot_label for players who *must* occupy a
    given slot (locked games). They consume their slot before anyone else is
    considered, which is what makes the resulting swap list applicable on
    ESPN rather than merely optimal in theory.

    Players projected at zero are never auto-started: a zero score means
    "no game" or "no data", and starting them over an empty slot gains
    nothing while looking like a real recommendation.
    """
    pinned = pinned or {}
    ranked = sorted(roster, key=score_fn, reverse=True)
    assigned: set[int] = set()
    active: list[tuple[dict[str, Any], str]] = []

    # Pinned (locked) players claim their slot first — they are immovable, so
    # the optimizer's job is to fill what's left around them.
    remaining: dict[str, int] = {label: count for label, count, _ in ACTIVE_SLOT_PLAN}
    for entry in ranked:
        pid = int(entry["player_id"])
        slot = pinned.get(pid)
        if slot is None:
            continue
        assigned.add(pid)
        active.append((entry, slot))
        if slot in remaining and remaining[slot] > 0:
            remaining[slot] -= 1

    for label, _count, buckets in ACTIVE_SLOT_PLAN:
        filled = 0
        want = remaining.get(label, 0)
        for entry in ranked:
            if filled >= want:
                break
            pid = int(entry["player_id"])
            if pid in assigned:
                continue
            if entry.get("bucket") not in buckets:
                continue
            if score_fn(entry) <= 0:
                continue
            assigned.add(pid)
            active.append((entry, label))
            filled += 1

    bench = [e for e in ranked if int(e["player_id"]) not in assigned]
    return active, bench


def plays_in_period(
    pro_team_id: int | None,
    period: int,
    games_by_period: dict[int, dict[int, list[ProGame]]],
) -> list[ProGame]:
    """Games this player's pro team plays in `period` (usually 0 or 1)."""
    if pro_team_id is None:
        return []
    return list((games_by_period.get(int(pro_team_id)) or {}).get(period) or [])


def _is_locked(games: list[ProGame], now: datetime) -> bool:
    """True when a lockable game for this player has already tipped off.

    An unknown start time counts as *not* locked — see `_game_start_time`:
    we'd rather stage a move the user finds already locked than silently
    withhold one that was still available.
    """
    for g in games:
        if not g["valid_for_locking"]:
            continue
        start = g["start_time"]
        if start is not None and start <= now:
            return True
    return False


def _out_reason(entry: dict[str, Any], plays: bool) -> str:
    """Why the player being benched is the weaker option, in the user's terms."""
    if is_confirmed_out(entry.get("injury_status")):
        return entry.get("injury_status") or "OUT"
    if not plays:
        return "no game"
    return "lower projection"


def check_lineup(
    roster: list[dict[str, Any]],
    *,
    horizon: Horizon,
    period: int,
    games_by_period: dict[int, dict[int, list[ProGame]]],
    now: datetime,
) -> dict[str, Any] | None:
    """Diff a team's actual lineup against the optimal one.

    `roster` rows are `analyze.build_team_views` entries (they carry
    `player_id`, `bucket`, `lineup_slot_id`, `is_active`, `injury_status`,
    `pro_team_id`, and the projections).

    Returns None for the `tonight` horizon when nobody on the roster plays in
    `period` — there is no lineup decision to make on an off day, and an
    empty panel is worse than no panel. The `week` horizon always returns a
    check.

    Otherwise returns a dict shaped like `schema.LineupCheck`.
    """
    if horizon not in ("tonight", "week"):
        raise ValueError(f"unknown horizon {horizon!r}")

    games_for: dict[int, list[ProGame]] = {}
    for entry in roster:
        games_for[int(entry["player_id"])] = plays_in_period(
            entry.get("pro_team_id"), period, games_by_period
        )

    if horizon == "tonight" and not any(games_for.values()):
        return None

    def has_games(entry: dict[str, Any]) -> bool:
        """Does this player have a game in the horizon at all?

        Tonight that's the current period's slate; over a week it's the game
        count `build_team_views` already computed for the whole window —
        checking a single period would call a 3-game player "no game" just
        because she's idle on the window's first day.
        """
        if horizon == "tonight":
            return bool(games_for.get(int(entry["player_id"])))
        return int(entry.get("games_this_week") or 0) > 0

    def score(entry: dict[str, Any]) -> float:
        if is_confirmed_out(entry.get("injury_status")):
            return 0.0
        if horizon == "tonight":
            return _score_tonight(entry, has_games(entry))
        return _score_week(entry)

    # Tonight only: a player whose game already tipped is frozen wherever she
    # currently sits. Over a week horizon there's always a later game to move
    # her for, so nothing is pinned.
    pinned: dict[int, str] = {}
    locked_ids: list[int] = []
    if horizon == "tonight":
        for entry in roster:
            pid = int(entry["player_id"])
            if not _is_locked(games_for.get(pid) or [], now):
                continue
            locked_ids.append(pid)
            if int(entry.get("lineup_slot_id") or -1) in ACTIVE_SLOT_IDS:
                pinned[pid] = str(entry.get("lineup_slot_label") or "UTIL")

    active, _bench = optimal_lineup(roster, score, pinned=pinned)

    by_id = {int(e["player_id"]): e for e in roster}
    optimal_ids = {int(e["player_id"]) for e, _slot in active}
    current_ids = {
        int(e["player_id"]) for e in roster
        if int(e.get("lineup_slot_id") or -1) in ACTIVE_SLOT_IDS
    }

    current_points = round(sum(score(by_id[pid]) for pid in current_ids), 2)
    optimal_points = round(sum(score(e) for e, _slot in active), 2)

    # The minimal move set: pair each player who should come in with the
    # weakest player who should go out. Sorting ins descending and outs
    # ascending pairs the biggest upgrade with the biggest downgrade, which
    # front-loads the gain — a user who only makes the first move still
    # captures most of the points.
    #
    # The two lists need not be the same length. A roster with fewer players
    # than active slots leaves a slot *empty*, and the fix there is a plain
    # "start her" with nobody benched (action="start", gain = her full score).
    # Zipping the lists would silently drop exactly that move — the single
    # most valuable kind, since an empty slot scores zero.
    #
    # Surplus `outs` are the mirror case: a player who shouldn't be starting
    # but has no replacement (everyone else scores zero too). Benching her
    # gains nothing, so we advise nothing.
    slot_by_id = {int(e["player_id"]): slot for e, slot in active}
    ins = sorted(optimal_ids - current_ids, key=lambda p: score(by_id[p]), reverse=True)
    outs = sorted(current_ids - optimal_ids, key=lambda p: score(by_id[p]))

    moves: list[dict[str, Any]] = []
    for i, in_id in enumerate(ins):
        out_id = outs[i] if i < len(outs) else None
        out_entry = by_id[out_id] if out_id is not None else None
        gain = round(score(by_id[in_id]) - (score(out_entry) if out_entry else 0.0), 2)
        if gain < MIN_SWAP_GAIN_PTS:
            continue
        in_entry = by_id[in_id]
        in_games = games_for.get(in_id) or []
        moves.append({
            "action": "swap" if out_entry else "start",
            "player_in_id": in_id,
            "player_in_name": str(in_entry.get("name") or f"#{in_id}"),
            "player_out_id": out_id,
            "player_out_name": str(out_entry.get("name") or f"#{out_id}") if out_entry else None,
            "slot_label": slot_by_id.get(in_id, "UTIL"),
            "gain_pts": gain,
            # Tip-off time is a tonight-horizon affordance ("plays 7:00"); over
            # a week the player has several games and no single time to show.
            "player_in_game_time": in_games[0]["start_time"] if in_games else None,
            "player_out_reason": _out_reason(out_entry, has_games(out_entry)) if out_entry else "empty slot",
        })
    moves.sort(key=lambda m: m["gain_pts"], reverse=True)

    return {
        "horizon": horizon,
        "status": "moves_available" if moves else "set",
        # Only count points a *recommended* move would actually capture.
        # Summing optimal-minus-current would include sub-threshold noise we
        # deliberately don't advise on, so the headline number would promise
        # points the moves list never delivers.
        "points_left_on_bench": round(sum(m["gain_pts"] for m in moves), 2),
        "moves": moves,
        "computed_for_period": period,
        "current_points": current_points,
        "optimal_points": optimal_points,
        "locked_player_ids": sorted(locked_ids),
    }


def check_team(
    roster: list[dict[str, Any]],
    *,
    current_period: int,
    week_start: int,
    week_end: int,
    games_by_period: dict[int, dict[int, list[ProGame]]],
    now: datetime,
) -> dict[str, Any]:
    """Both horizons for one team — shaped like `schema.TeamLineupCheck`.

    `current_period` is today's slate (ESPN's `scoringPeriodId`). The week
    horizon is scored from the roster's precomputed
    `projected_points_this_week`, which `build_team_views` already built from
    the [week_start, week_end] window; the bounds ride along for display so
    the UI can say which window the advice covers.
    """
    tonight = check_lineup(
        roster, horizon="tonight", period=current_period,
        games_by_period=games_by_period, now=now,
    )
    week = check_lineup(
        roster, horizon="week", period=week_start,
        games_by_period=games_by_period, now=now,
    )
    return {
        "tonight": tonight,
        "week": week,
        "week_start_period": week_start,
        "week_end_period": week_end,
    }
