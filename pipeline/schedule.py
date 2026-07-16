"""WNBA pro-team schedule helpers.

The `proTeamSchedules_wl` view on the league response carries each WNBA
team's schedule as `settings.proTeams[*].proGamesByScoringPeriod` — a
map of `scoringPeriodId -> [games]`. Scoring periods in WNBA fantasy are
1-day windows, so counting entries gives games-per-team-per-day.

Three surfaces:
- `games_per_team(league_raw, start, end)` for any closed range
  [start, end].
- `games_by_period(league_raw, start, end)` for the same range but keeping
  each game's tip-off time and opponent — the per-day granularity the
  lineup checker needs to answer "does she play *tonight*, and has that
  game already locked?"
- `upcoming_week_periods(league_raw)` for the conventional "next 7 days"
  window used by `analyze.rank_free_agents`. We start at
  `transactionScoringPeriod` (the first period a new pickup actually plays)
  rather than `scoringPeriodId + 1` so the window is correct even when
  today's slate is partly complete.

The game-count signal lets the ranker reward players whose pro team has
4 games next week (amazing) and penalize players stuck on a 1-game team.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, TypedDict

log = logging.getLogger(__name__)


class ProGame(TypedDict):
    """One pro game, normalized from `proGamesByScoringPeriod`.

    `start_time` is ESPN's `date` (epoch milliseconds, UTC) — present even
    for future games, which is what makes per-game lineup locks knowable.
    It is None when ESPN hasn't scheduled a tip-off yet (`startTimeTBD`).
    """
    scoring_period_id: int
    start_time: datetime | None
    opponent_pro_team_id: int | None
    is_home: bool
    valid_for_locking: bool

# Game-count tiers used downstream for both UI labelling and the
# weight multiplier in `rank_free_agents`. Keep `tier_label` and
# `tier_color` aligned with `docs/assets/style.css`.
TIER_LABEL: dict[int, str] = {
    0: "BYE",
    1: "Tough",
    2: "Light",
    3: "Average",
    4: "Heavy",
}


def tier_for_game_count(games: int) -> str:
    """Bucket a game count into a human-readable tier."""
    if games <= 0:
        return TIER_LABEL[0]
    if games == 1:
        return TIER_LABEL[1]
    if games == 2:
        return TIER_LABEL[2]
    if games == 3:
        return TIER_LABEL[3]
    return TIER_LABEL[4]  # 4+ games


def games_per_team(
    league_raw: dict[str, Any],
    start_period: int,
    end_period: int,
) -> dict[int, int]:
    """Return {proTeamId: game_count} for the inclusive [start, end] range.

    A `proTeamId` absent from the result has zero games in the window. The
    caller can decide whether to default to 0 or omit.
    """
    if end_period < start_period:
        raise ValueError(f"end_period {end_period} precedes start_period {start_period}")
    out: dict[int, int] = {}
    pro_teams = ((league_raw.get("settings") or {}).get("proTeams") or [])
    for t in pro_teams:
        pid = t.get("id")
        if pid is None:
            continue
        schedule = t.get("proGamesByScoringPeriod") or {}
        count = 0
        for k, games in schedule.items():
            try:
                sp = int(k)
            except (TypeError, ValueError):
                continue
            if start_period <= sp <= end_period:
                count += len(games or [])
        out[int(pid)] = count
    return out


def _game_start_time(game: dict[str, Any]) -> datetime | None:
    """Parse ESPN's epoch-millisecond `date` into an aware UTC datetime.

    Returns None for TBD tip-offs or unparseable values — callers treat an
    unknown start time as "not locked yet", which is the safe default: we'd
    rather suggest a move the user finds already locked on ESPN than hide a
    move that was still available.
    """
    if game.get("startTimeTBD"):
        return None
    raw = game.get("date")
    if not isinstance(raw, (int, float)) or raw <= 0:
        return None
    try:
        return datetime.fromtimestamp(raw / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        log.warning("schedule: unparseable game date %r on game %s", raw, game.get("id"))
        return None


def games_by_period(
    league_raw: dict[str, Any],
    start_period: int,
    end_period: int,
) -> dict[int, dict[int, list[ProGame]]]:
    """Return {proTeamId: {scoringPeriodId: [ProGame, ...]}} for [start, end].

    Same source as `games_per_team`, but keeps each game rather than
    collapsing to a count — `pipeline.lineups` needs tip-off times to decide
    which players are already locked tonight. A proTeamId with no games in
    the window is absent from the result.
    """
    if end_period < start_period:
        raise ValueError(f"end_period {end_period} precedes start_period {start_period}")
    out: dict[int, dict[int, list[ProGame]]] = {}
    pro_teams = ((league_raw.get("settings") or {}).get("proTeams") or [])
    for t in pro_teams:
        pid = t.get("id")
        if pid is None:
            continue
        pid = int(pid)
        schedule = t.get("proGamesByScoringPeriod") or {}
        by_period: dict[int, list[ProGame]] = {}
        for k, games in schedule.items():
            try:
                sp = int(k)
            except (TypeError, ValueError):
                continue
            if not (start_period <= sp <= end_period):
                continue
            for g in games or []:
                is_home = g.get("homeProTeamId") == pid
                opponent = g.get("awayProTeamId") if is_home else g.get("homeProTeamId")
                by_period.setdefault(sp, []).append(ProGame(
                    scoring_period_id=sp,
                    start_time=_game_start_time(g),
                    opponent_pro_team_id=int(opponent) if opponent is not None else None,
                    is_home=bool(is_home),
                    valid_for_locking=bool(g.get("validForLocking", True)),
                ))
        if by_period:
            out[pid] = by_period
    return out


def upcoming_week_periods(
    league_raw: dict[str, Any],
    *,
    days: int = 7,
) -> tuple[int, int]:
    """Return the (start, end) inclusive scoring-period range for the next
    `days` days of fantasy play.

    Start = `status.transactionScoringPeriod` (the first period a new
    pickup will count for). Falls back to `scoringPeriodId + 1`.
    End = start + days - 1.
    """
    status = league_raw.get("status") or {}
    sp = league_raw.get("scoringPeriodId")
    start = status.get("transactionScoringPeriod")
    if not isinstance(start, int) or start <= 0:
        start = (int(sp) + 1) if isinstance(sp, int) else 1
    end = start + max(1, days) - 1
    return (int(start), int(end))


def next_week_periods(
    league_raw: dict[str, Any],
    *,
    days: int = 7,
) -> tuple[int, int]:
    """Return the (start, end) scoring-period range for the week *after* the
    upcoming week — i.e. the window immediately following `upcoming_week_periods`.
    """
    _, this_end = upcoming_week_periods(league_raw, days=days)
    start = this_end + 1
    return (start, start + max(1, days) - 1)


def game_count_multiplier(games: int) -> float:
    """Multiplier applied to per-game projection to estimate week production.

    Direct: multiply by games. A player whose team plays 4 games next week
    contributes 4x a 1-game projection; 0 games contributes nothing. This
    is the model the user described ("4 is amazing, 1-2 is low") expressed
    as a continuous scalar.
    """
    return max(0.0, float(games))
