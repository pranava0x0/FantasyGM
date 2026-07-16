"""Derive analysis surfaces from raw ESPN snapshots.

Inputs:
- `league_raw`: the full ESPN league response (with mTeam/mRoster/mStandings views)
- `free_agents_raw`: the kona_player_info response (filterStatus=FREEAGENT|WAIVERS)

Outputs (pure data — `build_state.py` wraps them into the LeagueState schema):
- per-team production projection split by G / F / C
- team-needs gaps vs league average (positive framing — biggest upgrade area)
- ranked waiver targets (overall + per-team adjusted)
- a cleaned transaction list

Per-game projection blends up to four sources (average of available values):
  1. Actual season average so far (statSourceId=0, statSplitTypeId=0 appliedAverage).
     ESPN's preseason projection (statSourceId=1) is a full-season forecast made
     before the season opens and never updated; the actual average is used instead.
     The preseason projection is only kept as a fallback when fewer than 3 games
     have been played (sample too small to trust alone).
  2. ESPN 2-week rolling actual average (per-game actual blocks, statSplitTypeId=5)
  3. CBS Sports per-game projection (external scrape, optional)
  4. Yahoo Sports per-game stats (external scrape, optional)

Sources 3 and 4 come pre-converted to fantasy points using the league's
ScoringFormula (extracted from mSettings). Missing or zero sources are
excluded from the average so a single missing source doesn't dilute the result.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal

from pipeline.positions import (
    ACTIVE_SLOT_IDS,
    LINEUP_SLOT_LABEL,
    is_confirmed_out,
    position_bucket,
    position_label,
    slot_label,
)

log = logging.getLogger(__name__)

Bucket = Literal["G", "F", "C"]
BUCKETS: tuple[Bucket, ...] = ("G", "F", "C")

# ESPN stat block types:
#   statSourceId 0 = actual
#   statSourceId 1 = projected
#   statSplitTypeId 0 = season, 1 = scoring period, 2 = matchup period, ...
PROJECTED_SOURCE = 1
ACTUAL_SOURCE = 0


def _player_projected_points(player: dict[str, Any], scoring_period_id: int) -> float:
    """Pull the player's projected fantasy points for the current scoring period.

    Falls back to season-projection average if the period-level number is missing.
    """
    stats = player.get("stats") or []
    proj_period = 0.0
    proj_season_avg = 0.0
    for s in stats:
        if s.get("statSourceId") != PROJECTED_SOURCE:
            continue
        applied = float(s.get("appliedTotal") or 0.0)
        split = s.get("statSplitTypeId")
        if s.get("scoringPeriodId") == scoring_period_id and split == 1:
            proj_period = applied
        elif split == 0:  # season projection
            avg = float(s.get("appliedAverage") or 0.0)
            if avg:
                proj_season_avg = avg
            else:
                proj_season_avg = applied
    if proj_period:
        return proj_period
    return proj_season_avg


def _player_season_avg_actual(player: dict[str, Any]) -> float | None:
    """Player's actual season average so far (None if unplayed)."""
    for s in player.get("stats") or []:
        if s.get("statSourceId") != ACTUAL_SOURCE:
            continue
        if s.get("statSplitTypeId") == 0:  # season
            return float(s.get("appliedAverage") or 0.0) or None
    return None


def _player_actual_game_count(player: dict[str, Any]) -> int:
    """Number of actual games played this season (from season totals block).

    Used to decide whether to include the ESPN preseason projection as a
    fallback anchor: if fewer than 3 games have been played the observed
    sample is too small to stand alone.
    """
    for s in player.get("stats") or []:
        if s.get("statSourceId") != ACTUAL_SOURCE:
            continue
        if s.get("statSplitTypeId") == 0:  # season
            avg = float(s.get("appliedAverage") or 0.0)
            total = float(s.get("appliedTotal") or 0.0)
            if avg and total:
                return round(total / avg)
    return 0


def _flatten_player(entry: dict[str, Any]) -> dict[str, Any]:
    """Pull what we need from a roster `entry`."""
    pool = entry.get("playerPoolEntry") or {}
    player = pool.get("player") or {}
    return {
        "lineup_slot_id": entry.get("lineupSlotId"),
        "lineup_slot_label": slot_label(entry.get("lineupSlotId")),
        "player_id": player.get("id"),
        "name": player.get("fullName"),
        "pro_team_id": player.get("proTeamId"),
        "default_position_id": player.get("defaultPositionId"),
        "position": position_label(player.get("defaultPositionId")),
        "bucket": position_bucket(player.get("defaultPositionId"), player.get("eligibleSlots") or []),
        "eligible_slots": list(player.get("eligibleSlots") or []),
        "injury_status": player.get("injuryStatus"),
        "percent_owned": (player.get("ownership") or {}).get("percentOwned"),
        "percent_change": (player.get("ownership") or {}).get("percentChange"),
        "raw_player": player,
    }


def build_team_views(
    league_raw: dict[str, Any],
    *,
    games_by_pro_team: dict[int, int] | None = None,
    games_by_pro_team_next_week: dict[int, int] | None = None,
    ext_projections_by_player: dict[int, dict[str, float]] | None = None,
    player_game_log: dict[int, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Normalize each team into a roster + weekly production summary.

    `bucket_proj` is the team's projected production for the upcoming
    week, summed by bucket. Per-player contribution is
    `per_game_projection × games_this_week`. When `games_by_pro_team`
    is None (tests / legacy) we fall back to the single-period
    projection so the math stays defined.

    `games_by_pro_team_next_week` drives each roster entry's
    `games_next_week` / `projected_points_next_week` — used by the UI to
    recommend next week's start/sit lineup. Optional; defaults to 0 when
    omitted so tests/legacy callers don't need to change.

    `ext_projections_by_player` maps player_id → {source_name: per_game_fpts}
    from CBS Sports / Yahoo Sports. When present these are averaged in with
    the ESPN projection and 2-week rolling average.

    `player_game_log` is the accumulated game history from game_log.py:
    {player_id → [{scoring_period_id, fantasy_points}]}. Extends the rolling
    average beyond ESPN's current snapshot window.
    """
    scoring_period = int(league_raw.get("scoringPeriodId") or 0)
    games_map = games_by_pro_team or {}
    games_map_nw = games_by_pro_team_next_week or {}
    ext_map = ext_projections_by_player or {}
    log_map = player_game_log or {}
    teams = []
    for t in league_raw.get("teams") or []:
        record = (t.get("record") or {}).get("overall") or {}
        roster = []
        bucket_proj: dict[Bucket, float] = {"G": 0.0, "F": 0.0, "C": 0.0}
        active_counts: dict[Bucket, int] = {"G": 0, "F": 0, "C": 0}
        total_counts: dict[Bucket, int] = {"G": 0, "F": 0, "C": 0}
        for e in (t.get("roster") or {}).get("entries") or []:
            flat = _flatten_player(e)
            player = flat["raw_player"]
            player_id = int(player.get("id") or 0)
            ext_proj = ext_map.get(player_id)
            hist_games = log_map.get(player_id)
            proj_period = _player_projected_points(player, scoring_period)
            proj_per_game = _player_projected_per_game(
                player,
                scoring_period_id=scoring_period,
                ext_projections=ext_proj,
                historical_games=hist_games,
            )
            season_avg = _player_season_avg_actual(player)
            per_game = proj_per_game or season_avg or proj_period or 0.0
            pro_team_id = int(player.get("proTeamId") or 0)
            games_this_week = int(games_map.get(pro_team_id, 0)) if games_map else 0
            if games_map:
                week_proj = round(float(per_game) * games_this_week, 2)
            else:
                week_proj = round(proj_period, 2)
            games_next_week = int(games_map_nw.get(pro_team_id, 0)) if games_map_nw else 0
            week_proj_nw = round(float(per_game) * games_next_week, 2) if games_map_nw else 0.0
            actual = (e.get("playerPoolEntry") or {}).get("appliedStatTotal")

            flat["projected_points"] = proj_period
            flat["projected_per_game"] = round(float(per_game), 2)
            flat["games_this_week"] = games_this_week
            flat["projected_points_this_week"] = week_proj
            flat["games_next_week"] = games_next_week
            flat["projected_points_next_week"] = week_proj_nw
            flat["actual_points"] = float(actual) if actual is not None else None
            flat["is_active"] = flat["lineup_slot_id"] in ACTIVE_SLOT_IDS
            roster.append(flat)
            total_counts[flat["bucket"]] += 1
            if flat["is_active"]:
                bucket_proj[flat["bucket"]] += week_proj
                active_counts[flat["bucket"]] += 1
        teams.append({
            "team_id": t.get("id"),
            "abbrev": t.get("abbrev"),
            "name": _team_display_name(t),
            "logo": t.get("logo"),
            "division_id": t.get("divisionId"),
            "waiver_position": t.get("waiverRank"),
            # Straight call: `_faab_remaining` already returns None for every
            # unknown case. Guarding it with `... and _faab_remaining(...) or None`
            # collapsed a real remaining balance of *zero* to None — a team that
            # had spent its whole budget read as "unknown", which reads as
            # "no limit" downstream and let `faab.suggest_bid` quote bids it
            # could not pay.
            "faab_remaining": _faab_remaining(t, league_raw),
            "record": {
                "wins": int(record.get("wins") or 0),
                "losses": int(record.get("losses") or 0),
                "ties": int(record.get("ties") or 0),
                "pct": float(record.get("percentage") or 0.0),
            },
            "roster": roster,
            "bucket_proj": bucket_proj,
            "active_counts": active_counts,
            "total_counts": total_counts,
        })
    return teams


def _team_display_name(t: dict[str, Any]) -> str:
    """ESPN sometimes ships team name in location+nickname, sometimes in name."""
    if t.get("name"):
        return str(t["name"]).strip()
    parts = [t.get("location") or "", t.get("nickname") or ""]
    return " ".join(p for p in parts if p).strip() or f"Team {t.get('id')}"


def _faab_remaining(t: dict[str, Any], league_raw: dict[str, Any]) -> int | None:
    """FAAB remaining = budget - spent. Settings vary by league."""
    settings = (league_raw.get("settings") or {}).get("acquisitionSettings") or {}
    budget = settings.get("acquisitionBudget")
    spent = (t.get("transactionCounter") or {}).get("acquisitionBudgetSpent")
    if budget is None or spent is None:
        return None
    try:
        return int(budget) - int(spent)
    except (TypeError, ValueError):
        return None


def league_bucket_averages(teams: list[dict[str, Any]]) -> dict[Bucket, float]:
    """Mean projected points per bucket across all teams' active slots."""
    if not teams:
        return {"G": 0.0, "F": 0.0, "C": 0.0}
    totals: dict[Bucket, float] = {"G": 0.0, "F": 0.0, "C": 0.0}
    for t in teams:
        for b in BUCKETS:
            totals[b] += float(t["bucket_proj"][b])
    return {b: round(totals[b] / len(teams), 2) for b in BUCKETS}


def compute_team_needs(teams: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """For each team, compute bucket projection + gap to league average.

    Framed proactively as *needs*: `top_need_bucket` is the bucket with the
    largest negative gap — the team's biggest upgrade opportunity.

    The structural decision: WNBA fantasy uses shared F/C lineup slots, so
    we treat F+C as a single 'frontcourt' bucket. A team with zero
    pure-Center players doesn't have a structural C need if they fill slot
    5 with a Forward — three teams in the 50-40-90 Club do exactly that.

    `top_need_bucket` is therefore the worse of two combined buckets:
    'G' (backcourt) or 'FC' (frontcourt). The granular F / C numbers stay
    in the payload for display.
    """
    avg = league_bucket_averages(teams)
    avg_fc = round(avg["F"] + avg["C"], 2)
    out: dict[int, dict[str, Any]] = {}
    for t in teams:
        guard_proj = round(t["bucket_proj"]["G"], 2)
        forward_proj = round(t["bucket_proj"]["F"], 2)
        center_proj = round(t["bucket_proj"]["C"], 2)
        frontcourt_proj = round(forward_proj + center_proj, 2)

        guard_gap = round(guard_proj - avg["G"], 2)
        forward_gap = round(forward_proj - avg["F"], 2)
        center_gap = round(center_proj - avg["C"], 2)
        frontcourt_gap = round(frontcourt_proj - avg_fc, 2)

        top_need: Literal["G", "FC"] = "G" if guard_gap < frontcourt_gap else "FC"

        out[t["team_id"]] = {
            "guard_proj": guard_proj,
            "forward_proj": forward_proj,
            "center_proj": center_proj,
            "frontcourt_proj": frontcourt_proj,
            "guard_gap_vs_league": guard_gap,
            "forward_gap_vs_league": forward_gap,
            "center_gap_vs_league": center_gap,
            "frontcourt_gap_vs_league": frontcourt_gap,
            "top_need_bucket": top_need,
            "league_avg": {**avg, "FC": avg_fc},
        }
    return out


def rank_free_agents(
    free_agents_raw: dict[str, Any],
    scoring_period_id: int,
    *,
    limit: int = 25,
    games_by_pro_team: dict[int, int] | None = None,
    games_by_pro_team_next_week: dict[int, int] | None = None,
    games_in_rolling_window_by_team: dict[int, int] | None = None,
    ext_projections_by_player: dict[int, dict[str, float]] | None = None,
    player_game_log: dict[int, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Sort the free-agent pool by *projected points this week*.

    The base score is the player's per-game projection multiplied by how
    many games their pro team plays in the upcoming window. A player on
    a 4-game team should outrank an identically-projected player on a
    1-game team — that's the signal the user flagged ("4 games is amazing,
    1-2 is low"). When `games_by_pro_team` is None (legacy or tests), we
    fall back to the single-period projection so the ranker still works.

    Per-game projection is a blend of (available sources only):
    1. Actual season average so far (statSourceId=0, statSplitTypeId=0).
    2. Rolling 2-week average drawn from accumulated game log + snapshot.
    3. CBS Sports per-game projection (pre-converted via ScoringFormula).
    4. Yahoo Sports per-game stats (pre-converted via ScoringFormula).

    `games_in_rolling_window_by_team` maps proTeamId → game count in the
    current rolling window. Used to detect players who missed most of their
    team's recent games (potential returners from injury/rest).

    `ext_projections_by_player` maps player_id → {source_name: per_game_fpts}.

    `player_game_log` is the accumulated game history {player_id → games list}
    from game_log.py. Extends the rolling average beyond ESPN's snapshot window.
    """
    games_map = games_by_pro_team or {}
    games_map_nw = games_by_pro_team_next_week or {}
    window_games_map = games_in_rolling_window_by_team or {}
    ext_map = ext_projections_by_player or {}
    log_map = player_game_log or {}
    out = []
    for entry in free_agents_raw.get("players") or []:
        player = entry.get("player") or {}
        if _is_out(player):
            continue
        player_id = int(player.get("id") or 0)
        ext_proj = ext_map.get(player_id)
        hist_games = log_map.get(player_id)
        proj_period = _player_projected_points(player, scoring_period_id)
        proj_per_game = _player_projected_per_game(
            player,
            scoring_period_id=scoring_period_id,
            ext_projections=ext_proj,
            historical_games=hist_games,
        )
        season_avg = _player_season_avg_actual(player)
        ownership = player.get("ownership") or {}
        per_game = proj_per_game or season_avg or proj_period
        per_game = float(per_game or 0.0)
        pro_team_id = int(player.get("proTeamId") or 0)
        games = int(games_map.get(pro_team_id, 0)) if games_map else 0
        games_nw = int(games_map_nw.get(pro_team_id, 0)) if games_map_nw else 0
        if games_map:
            week_proj = round(per_game * games, 2)
            week_proj_nw = round(per_game * games_nw, 2)
        else:
            week_proj = round(proj_period, 2)
            week_proj_nw = 0.0

        # Injury / return signal: compare how many games the player played
        # in the rolling window vs how many their team played.
        inj_signal, boost = _injury_signal(
            player,
            pro_team_id=pro_team_id,
            scoring_period_id=scoring_period_id,
            window_games_map=window_games_map,
        )
        base_score = round(week_proj * boost, 2)
        recent = _player_recent_games(
            player, scoring_period_id, historical_games=hist_games
        )

        out.append({
            "player_id": player.get("id"),
            "name": player.get("fullName"),
            "pro_team_id": player.get("proTeamId"),
            "position": position_label(player.get("defaultPositionId")),
            "bucket": position_bucket(player.get("defaultPositionId"), player.get("eligibleSlots") or []),
            "eligible_slots": list(player.get("eligibleSlots") or []),
            "injury_status": player.get("injuryStatus"),
            "projected_points_next_period": round(proj_period, 2),
            "projected_per_game": round(per_game, 2),
            "projected_points_this_week": week_proj,
            "games_this_week": games,
            "projected_points_next_week": week_proj_nw,
            "games_next_week": games_nw,
            "season_avg_points": round(season_avg, 2) if season_avg is not None else None,
            "percent_owned": ownership.get("percentOwned"),
            "percent_change": ownership.get("percentChange"),
            "base_score": base_score,
            "injury_signal": inj_signal,
            "recent_games": recent,
        })
    # Sort by next week's projection first, then this week's score as tiebreaker.
    out.sort(key=lambda r: (r["projected_points_next_week"], r["base_score"]), reverse=True)
    return out[:limit]


def _is_out(player: dict[str, Any]) -> bool:
    """True when the player is confirmed unavailable (OUT or IR).

    DTD and QUESTIONABLE stay in the pool — they may play. The status set
    lives in positions.CONFIRMED_OUT_STATUSES so the ranker, the lineup
    checker, and the UI all read from one list.
    """
    return is_confirmed_out(player.get("injuryStatus"))


def _player_rolling_game_count(player: dict[str, Any], scoring_period_id: int) -> int:
    """Count how many games the player actually played in the rolling window.

    Counts per-game actual blocks (statSourceId=0, statSplitTypeId=5) where
    appliedTotal > 0. Zero-total blocks (DNP / no-game) are not counted.
    """
    window_start = max(1, scoring_period_id - 14)
    return sum(
        1
        for s in player.get("stats") or []
        if s.get("statSourceId") == ACTUAL_SOURCE
        and s.get("statSplitTypeId") == 5
        and window_start <= int(s.get("scoringPeriodId") or 0) <= scoring_period_id
        and float(s.get("appliedTotal") or 0.0) > 0
    )


# How many of their team's rolling-window games a player must have missed
# before we call them a return candidate. 0.5 = missed more than half.
_ABSENCE_THRESHOLD = 0.50
# Minimum team games in the window before we can make an absence call.
# Avoids false positives when a team simply has a very light schedule.
_MIN_TEAM_GAMES_IN_WINDOW = 3
# Minimum actual season average (fpts/game) for a player to be considered
# a meaningful return target. Filters out fringe/end-of-bench players.
_RETURN_MIN_SEASON_AVG = 10.0
# Minimum number of actual games played (overall) for the season average
# to be trustworthy enough to use as the return signal.
_RETURN_MIN_GAMES_PLAYED = 3
# Score multiplier applied to returning players.
_RETURN_BOOST = 1.15


def _injury_signal(
    player: dict[str, Any],
    *,
    pro_team_id: int,
    scoring_period_id: int,
    window_games_map: dict[int, int],
) -> tuple[str | None, float]:
    """Return (signal_label, score_multiplier) for a player.

    Signal:
      "returning" — player missed ≥50% of their team's rolling-window games
                    while having a meaningful season history. Base score is
                    boosted ×1.15 to surface them above players whose rolling
                    average is fully populated.
      None        — no anomaly detected; multiplier is 1.0.

    ESPN's WNBA injury system is binary (ACTIVE / OUT). There is no DTD or
    QUESTIONABLE status. Players marked OUT are already removed by _is_out(),
    so this function only sees ACTIVE players. The absence signal is the only
    reliable way to detect a player who was recently unavailable and has now
    returned to the pool.
    """
    team_games = int(window_games_map.get(pro_team_id, 0))
    if team_games < _MIN_TEAM_GAMES_IN_WINDOW:
        return None, 1.0

    player_games = _player_rolling_game_count(player, scoring_period_id)
    absence_rate = 1.0 - (player_games / team_games)
    if absence_rate < _ABSENCE_THRESHOLD:
        return None, 1.0

    # Enough absence — check if the player's season history makes them worth
    # flagging (filters out injured bench players who don't matter anyway).
    season_avg = _player_season_avg_actual(player) or 0.0
    actual_games = _player_actual_game_count(player)
    if season_avg < _RETURN_MIN_SEASON_AVG or actual_games < _RETURN_MIN_GAMES_PLAYED:
        return None, 1.0

    log.debug(
        "injury_signal: %s flagged 'returning' "
        "(team_games=%d player_games=%d absence=%.0f%% season_avg=%.1f)",
        player.get("fullName"), team_games, player_games,
        absence_rate * 100, season_avg,
    )
    return "returning", _RETURN_BOOST


def _player_recent_games(
    player: dict[str, Any],
    current_period: int,
    *,
    historical_games: list[dict[str, Any]] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return individual game entries for the last ~2 weeks, newest first.

    Merges the full accumulated `historical_games` log (from game_log.py)
    with any per-game stat blocks present in the current snapshot. Historical
    data takes precedence for duplicate periods; snapshot data fills in
    very-recent games not yet committed to the log.

    Each entry: {scoring_period_id, fantasy_points}. Zero-total entries
    (DNP / no game) are excluded. Capped at `limit` games.
    """
    window_start = max(1, current_period - 14)

    # Build a period → fpts map starting from history (full accumulated record).
    period_to_fpts: dict[int, float] = {}
    for g in (historical_games or []):
        p = int(g.get("scoring_period_id") or 0)
        f = float(g.get("fantasy_points") or 0.0)
        if p > 0 and f > 0:
            period_to_fpts[p] = f

    # Overlay current snapshot stat blocks — they may contain games too new
    # to be in the log yet (today's games before tonight's refresh commit).
    for s in player.get("stats") or []:
        if s.get("statSourceId") != ACTUAL_SOURCE:
            continue
        if s.get("statSplitTypeId") != 5:
            continue
        period = int(s.get("scoringPeriodId") or 0)
        total = float(s.get("appliedTotal") or 0.0)
        if period > 0 and total > 0:
            period_to_fpts.setdefault(period, total)  # don't overwrite history

    games = [
        {"scoring_period_id": p, "fantasy_points": f}
        for p, f in period_to_fpts.items()
        if window_start <= p <= current_period
    ]
    games.sort(key=lambda g: g["scoring_period_id"], reverse=True)
    return games[:limit]


def _player_projected_per_game(
    player: dict[str, Any],
    *,
    scoring_period_id: int = 0,
    ext_projections: dict[str, float] | None = None,
    historical_games: list[dict[str, Any]] | None = None,
) -> float:
    """Blended per-game projection from up to four sources.

    Sources averaged (only those with a positive value contribute):
      1. Actual season average so far (statSourceId=0, statSplitTypeId=0,
         appliedAverage). Preferred over the ESPN preseason projection because
         ESPN's statSourceId=1 block encodes a full-season preseason forecast
         (e.g. 44 games × 18.43 = 811 total) that never updates mid-season.
         The actual average reflects real observed performance.
      2. Rolling 2-week actual average — drawn from the full accumulated game
         log (historical_games) merged with the current snapshot's stat blocks.
         Using the log extends this beyond ESPN's API window (which caps at
         filterStatsForTopScoringPeriodIds, currently 14 entries).
      3. CBS Sports per-game fantasy points (pre-converted, optional)
      4. Yahoo Sports per-game fantasy points (pre-converted, optional)

    When the season is very early and fewer than 3 actual games exist, the
    ESPN preseason projection is included as a fallback anchor so we don't
    rank entirely on a tiny sample.

    Returns 0.0 if no source is available.
    """
    sources: list[float] = []

    # Source 1: actual season-average so far.
    season_avg = _player_season_avg_actual(player)
    actual_game_count = _player_actual_game_count(player)
    if season_avg and season_avg > 0:
        sources.append(season_avg)

    # Fallback: ESPN preseason projection. Only used when the player has
    # fewer than 3 actual games — at that point the observed sample is too
    # small to trust alone, so the preseason projection anchors the estimate.
    if actual_game_count < 3:
        espn_proj = _espn_projected_avg(player)
        if espn_proj > 0:
            sources.append(espn_proj)

    # Source 2: rolling 2-week actual average (merged history + snapshot).
    rolling = _player_rolling_2w_avg(
        player, scoring_period_id, historical_games=historical_games
    )
    if rolling is not None and rolling > 0:
        sources.append(rolling)

    # Sources 3 & 4: external per-game projections (CBS, Yahoo) pre-keyed by
    # source name.
    if ext_projections:
        for val in ext_projections.values():
            if val and float(val) > 0:
                sources.append(float(val))

    if not sources:
        return 0.0
    return sum(sources) / len(sources)


def _espn_projected_avg(player: dict[str, Any]) -> float:
    """ESPN's season-projection per-game average (statSourceId=1, split=0).

    Returns 0.0 if unavailable. Extracted as a separate helper so both
    `_player_projected_per_game` and `build_team_views` can call it directly.
    """
    for s in player.get("stats") or []:
        if s.get("statSourceId") != PROJECTED_SOURCE:
            continue
        if s.get("statSplitTypeId") == 0:  # season-level
            avg = float(s.get("appliedAverage") or 0.0)
            if avg:
                return avg
            total = float(s.get("appliedTotal") or 0.0)
            # ESPN sometimes ships total only; ~36-game season rough divisor.
            return total / 36 if total else 0.0
    return 0.0


def _player_rolling_2w_avg(
    player: dict[str, Any],
    current_period: int,
    *,
    historical_games: list[dict[str, Any]] | None = None,
) -> float | None:
    """Average fantasy points over the player's most recent games (~2 weeks).

    Merges the full accumulated game log (`historical_games`, sorted asc)
    with per-game stat blocks from the current snapshot. Using the log
    extends the rolling window beyond ESPN's API cap so early-season games
    are not lost as the season progresses.

    Only counts games with fantasy_points > 0 (zero means DNP / no game).
    Returns None if no qualifying games are found.
    """
    # Build the same period_to_fpts map as _player_recent_games uses.
    window_start = max(1, current_period - 14)
    period_to_fpts: dict[int, float] = {}

    for g in (historical_games or []):
        p = int(g.get("scoring_period_id") or 0)
        f = float(g.get("fantasy_points") or 0.0)
        if p > 0 and f > 0:
            period_to_fpts[p] = f

    for s in player.get("stats") or []:
        if s.get("statSourceId") != ACTUAL_SOURCE:
            continue
        if s.get("statSplitTypeId") != 5:
            continue
        period = int(s.get("scoringPeriodId") or 0)
        total = float(s.get("appliedTotal") or 0.0)
        if period > 0 and total > 0:
            period_to_fpts.setdefault(period, total)

    game_totals = [
        f for p, f in period_to_fpts.items()
        if window_start <= p <= current_period
    ]
    if not game_totals:
        return None
    return sum(game_totals) / len(game_totals)


# WNBA roster: 2 G slots + 3 F slots + 1 F/C slot + 3 UTIL (any) = 9 active.
# "Practical maximum" per bucket = bucket-specific slots + UTIL flex:
#   G:  2 + 3 = 5
#   FC: 3 + 1 + 3 = 7
# Beyond these counts, additional pickups warm the bench and contribute
# nothing to weekly production.
SATURATION_THRESHOLD_G = 5
SATURATION_THRESHOLD_FC = 7

# Top-K positions in the per-team list we want to *guarantee* include at
# least one player at the team's top-need bucket. Drives visible variance
# across teams even when the FA pool is dominated by one bucket.
NEEDS_PICK_TOP_K = 3


def waiver_targets_for_team(
    team_needs: dict[str, Any],
    ranked_fas: list[dict[str, Any]],
    *,
    active_counts: dict[str, int] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Re-rank the FA pool for a single team. Three layered adjustments
    on top of `base_score` (= projected points this week):

    1. **Gap bonus.** When the team is below league average for a bucket,
       boost FAs in that bucket. Weight scales with severity:
         - Severe gap (≤ -10):  0.15 per negative point
         - Moderate (-10..-5):  0.08
         - Mild      (-5..0):   0.04
       Caps at 50% of base so a 2-point bench center doesn't leap top-10.

    2. **Saturation penalty.** When a team already carries more than the
       practical-max active players in a bucket (G ≥ 5 OR FC ≥ 7),
       additional pickups in that bucket lose 30% of their base score.

    3. **Needs guarantee.** After scoring, the top `NEEDS_PICK_TOP_K`
       picks must include at least one player from the team's top-need
       bucket (G if `top_need_bucket == 'G'`, F or C if 'FC'). If none
       naturally surfaced (because the FA pool is dominated by the
       opposite bucket), promote the highest-scoring top-need-bucket
       player into the K-th slot. This guarantees the per-team picks
       look visibly different across teams.

    Per-bucket gap uses the combined frontcourt gap (F + C share slots);
    see `compute_team_needs` for the rationale.
    """
    gap_g = float(team_needs["guard_gap_vs_league"])
    gap_fc = float(team_needs["frontcourt_gap_vs_league"])
    bucket_to_gap = {"G": gap_g, "F": gap_fc, "C": gap_fc}
    top_need = team_needs.get("top_need_bucket")  # "G" or "FC"
    top_need_buckets = {"G"} if top_need == "G" else {"F", "C"}

    counts = active_counts or {"G": 0, "F": 0, "C": 0}
    g_active = int(counts.get("G", 0))
    fc_active = int(counts.get("F", 0)) + int(counts.get("C", 0))

    def _saturated(bucket: str) -> bool:
        if bucket == "G":
            return g_active >= SATURATION_THRESHOLD_G
        return fc_active >= SATURATION_THRESHOLD_FC  # F and C share frontcourt slots

    boosted = []
    for fa in ranked_fas:
        bucket = fa["bucket"]
        gap = bucket_to_gap.get(bucket, 0.0)
        base = float(fa["base_score"])

        bonus = 0.0
        if gap < 0:
            weight = (
                0.20 if gap <= -10.0 else
                0.12 if gap <= -5.0 else
                0.06
            )
            raw = (-gap) * weight
            bonus = min(raw, base * 0.75)

        penalty = -base * 0.30 if _saturated(bucket) else 0.0

        boosted.append({
            **fa,
            "team_bonus": round(bonus, 2),
            "saturation_penalty": round(penalty, 2),
            "adjusted_score": round(base + bonus + penalty, 2),
        })
    boosted.sort(key=lambda r: r["adjusted_score"], reverse=True)

    # Needs guarantee — only when we have an explicit top-need bucket and
    # the natural top-K already excludes it.
    if top_need and not any(r["bucket"] in top_need_buckets for r in boosted[:NEEDS_PICK_TOP_K]):
        # Highest-scoring top-need-bucket pick that didn't make the top K.
        for i, r in enumerate(boosted[NEEDS_PICK_TOP_K:], start=NEEDS_PICK_TOP_K):
            if r["bucket"] in top_need_buckets:
                promoted = {**r, "promoted_for_need": True}
                # Insert at position K-1 (the bottom of the top K), demoting
                # the displaced entry by one slot.
                boosted.pop(i)
                boosted.insert(NEEDS_PICK_TOP_K - 1, promoted)
                break

    return boosted[:limit]


def normalize_transactions(league_raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Strip transactions of any owner-personal fields; keep team + items."""
    out = []
    for t in league_raw.get("transactions") or []:
        # `proposedDate` is a Unix ms timestamp.
        ts = t.get("proposedDate")
        when = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc) if ts else None
        items = []
        for it in t.get("items") or []:
            items.append({
                "player_id": it.get("playerId"),
                "player_name": None,  # filled in by `build_state` from roster map
                "from_team_id": it.get("fromTeamId"),
                "to_team_id": it.get("toTeamId"),
                "from_slot_id": it.get("fromLineupSlotId"),
                "to_slot_id": it.get("toLineupSlotId"),
                "type": it.get("type") or "UNKNOWN",
            })
        out.append({
            "transaction_id": t.get("id"),
            "occurred_at": when,
            "scoring_period_id": int(t.get("scoringPeriodId") or 0),
            "team_id": t.get("teamId"),
            "type": t.get("type") or "UNKNOWN",
            "bid_amount": int(t.get("bidAmount") or 0),
            "status": t.get("status") or "UNKNOWN",
            "items": items,
        })
    out.sort(key=lambda r: r["occurred_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out
