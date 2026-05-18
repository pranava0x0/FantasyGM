"""WNBA pro-team schedule helpers.

The `proTeamSchedules_wl` view on the league response carries each WNBA
team's schedule as `settings.proTeams[*].proGamesByScoringPeriod` — a
map of `scoringPeriodId -> [games]`. Scoring periods in WNBA fantasy are
1-day windows, so counting entries gives games-per-team-per-day.

Two surfaces:
- `games_per_team(league_raw, start, end)` for any closed range
  [start, end].
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
from typing import Any

log = logging.getLogger(__name__)

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


def game_count_multiplier(games: int) -> float:
    """Multiplier applied to per-game projection to estimate week production.

    Direct: multiply by games. A player whose team plays 4 games next week
    contributes 4x a 1-game projection; 0 games contributes nothing. This
    is the model the user described ("4 is amazing, 1-2 is low") expressed
    as a continuous scalar.
    """
    return max(0.0, float(games))
