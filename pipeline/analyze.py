"""Derive analysis surfaces from raw ESPN snapshots.

Inputs:
- `league_raw`: the full ESPN league response (with mTeam/mRoster/mStandings views)
- `free_agents_raw`: the kona_player_info response (filterStatus=FREEAGENT|WAIVERS)

Outputs (pure data — `build_state.py` wraps them into the LeagueState schema):
- per-team production projection split by G / F / C
- team weakness gaps vs league average
- ranked waiver targets (overall + per-team adjusted)
- a cleaned transaction list

We intentionally use ESPN's own projected/applied points (`appliedTotal` from
the stat blocks) rather than re-deriving from raw stats. ESPN's scoring
formula is in the league settings; replicating it is on BACKLOG.md.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal

from pipeline.positions import (
    ACTIVE_SLOT_IDS,
    LINEUP_SLOT_LABEL,
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
) -> list[dict[str, Any]]:
    """Normalize each team into a roster + weekly production summary.

    `bucket_proj` is the team's projected production for the upcoming
    week, summed by bucket. Per-player contribution is
    `per_game_projection × games_this_week`. When `games_by_pro_team`
    is None (tests / legacy) we fall back to the single-period
    projection so the math stays defined.
    """
    scoring_period = int(league_raw.get("scoringPeriodId") or 0)
    games_map = games_by_pro_team or {}
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
            proj_period = _player_projected_points(player, scoring_period)
            proj_per_game = _player_projected_per_game(player)
            season_avg = _player_season_avg_actual(player)
            per_game = proj_per_game or season_avg or proj_period or 0.0
            games_this_week = int(games_map.get(int(player.get("proTeamId") or 0), 0)) if games_map else 0
            if games_map:
                week_proj = round(float(per_game) * games_this_week, 2)
            else:
                week_proj = round(proj_period, 2)
            actual = (e.get("playerPoolEntry") or {}).get("appliedStatTotal")

            flat["projected_points"] = proj_period
            flat["projected_per_game"] = round(float(per_game), 2)
            flat["games_this_week"] = games_this_week
            flat["projected_points_this_week"] = week_proj
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
            "faab_remaining": ((t.get("transactionCounter") or {}).get("acquisitionBudgetSpent") is not None) and _faab_remaining(t, league_raw) or None,
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


def compute_team_weakness(teams: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """For each team, compute bucket projection + gap to league average.

    Returns a payload per team. The structural decision: WNBA fantasy uses
    shared F/C lineup slots, so we treat F+C as a single 'frontcourt' bucket
    for weakness reasoning. A team with zero pure-Center players isn't
    really weak at 'C' if they fill slot 5 with a Forward — the data above
    shows three teams in the 50-40-90 Club where this happens.

    `weakest_bucket` is therefore the worse of two combined buckets:
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

        weakest: Literal["G", "FC"] = "G" if guard_gap < frontcourt_gap else "FC"

        out[t["team_id"]] = {
            "guard_proj": guard_proj,
            "forward_proj": forward_proj,
            "center_proj": center_proj,
            "frontcourt_proj": frontcourt_proj,
            "guard_gap_vs_league": guard_gap,
            "forward_gap_vs_league": forward_gap,
            "center_gap_vs_league": center_gap,
            "frontcourt_gap_vs_league": frontcourt_gap,
            "weakest_bucket": weakest,
            "league_avg": {**avg, "FC": avg_fc},
        }
    return out


def rank_free_agents(
    free_agents_raw: dict[str, Any],
    scoring_period_id: int,
    *,
    limit: int = 25,
    games_by_pro_team: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Sort the free-agent pool by *projected points this week*.

    The base score is the player's per-game projection multiplied by how
    many games their pro team plays in the upcoming window. A player on
    a 4-game team should outrank an identically-projected player on a
    1-game team — that's the signal the user flagged ("4 games is amazing,
    1-2 is low"). When `games_by_pro_team` is None (legacy or tests), we
    fall back to the single-period projection so the ranker still works.

    Per-game projection priority:
    1. `appliedAverage` from the season-projection stat block (ESPN's own
       per-game estimate — most reliable when present).
    2. `season_avg_points` (actual per-game so far this season).
    3. `projected_points_next_period` (single-period projection — least
       informative once we know the schedule).
    """
    games_map = games_by_pro_team or {}
    out = []
    for entry in free_agents_raw.get("players") or []:
        player = entry.get("player") or {}
        proj_period = _player_projected_points(player, scoring_period_id)
        proj_per_game = _player_projected_per_game(player)
        season_avg = _player_season_avg_actual(player)
        ownership = player.get("ownership") or {}
        per_game = proj_per_game or season_avg or proj_period
        per_game = float(per_game or 0.0)
        games = int(games_map.get(int(player.get("proTeamId") or 0), 0)) if games_map else 0
        if games_map:
            week_proj = round(per_game * games, 2)
        else:
            week_proj = round(proj_period, 2)
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
            "season_avg_points": round(season_avg, 2) if season_avg is not None else None,
            "percent_owned": ownership.get("percentOwned"),
            "percent_change": ownership.get("percentChange"),
            "base_score": week_proj,
        })
    out.sort(key=lambda r: r["base_score"], reverse=True)
    return out[:limit]


def _player_projected_per_game(player: dict[str, Any]) -> float:
    """ESPN's season-projection per-game average (statSourceId=1, split=0).

    Returns 0.0 if unavailable. We prefer this over single-period projections
    because the ranker multiplies it by games-this-week downstream.
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


# WNBA roster: 2 G slots + 3 F slots + 1 F/C slot + 3 UTIL (any) = 9 active.
# "Practical maximum" per bucket = bucket-specific slots + UTIL flex:
#   G:  2 + 3 = 5
#   FC: 3 + 1 + 3 = 7
# Beyond these counts, additional pickups warm the bench and contribute
# nothing to weekly production.
SATURATION_THRESHOLD_G = 5
SATURATION_THRESHOLD_FC = 7

# Top-K positions in the per-team list we want to *guarantee* include at
# least one player at the team's weakest bucket. Drives visible variance
# across teams even when the FA pool is dominated by one bucket.
NEEDS_PICK_TOP_K = 3


def waiver_targets_for_team(
    team_weakness: dict[str, Any],
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
       picks must include at least one player from the team's weakest
       bucket (G if `weakest_bucket == 'G'`, F or C if 'FC'). If none
       naturally surfaced (because the FA pool is dominated by the
       opposite bucket), promote the highest-scoring weak-bucket player
       into the K-th slot. This guarantees the per-team picks look
       visibly different across teams.

    Per-bucket gap uses the combined frontcourt gap (F + C share slots);
    see `compute_team_weakness` for the rationale.
    """
    gap_g = float(team_weakness["guard_gap_vs_league"])
    gap_fc = float(team_weakness["frontcourt_gap_vs_league"])
    bucket_to_gap = {"G": gap_g, "F": gap_fc, "C": gap_fc}
    weakest = team_weakness.get("weakest_bucket")  # "G" or "FC"
    weakest_buckets = {"G"} if weakest == "G" else {"F", "C"}

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
                0.15 if gap <= -10.0 else
                0.08 if gap <= -5.0 else
                0.04
            )
            raw = (-gap) * weight
            bonus = min(raw, base * 0.5)

        penalty = -base * 0.30 if _saturated(bucket) else 0.0

        boosted.append({
            **fa,
            "team_bonus": round(bonus, 2),
            "saturation_penalty": round(penalty, 2),
            "adjusted_score": round(base + bonus + penalty, 2),
        })
    boosted.sort(key=lambda r: r["adjusted_score"], reverse=True)

    # Needs guarantee — only when we have an explicit weakest bucket and
    # the natural top-K already excludes it.
    if weakest and not any(r["bucket"] in weakest_buckets for r in boosted[:NEEDS_PICK_TOP_K]):
        # Highest-scoring weak-bucket pick that didn't make the top K.
        for i, r in enumerate(boosted[NEEDS_PICK_TOP_K:], start=NEEDS_PICK_TOP_K):
            if r["bucket"] in weakest_buckets:
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
