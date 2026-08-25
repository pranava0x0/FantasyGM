"""Final standings and the playoff bracket — the season's ending, derived.

Everything the UI needs to answer "how did this season end?" lives in the
raw league response already, but scattered across three places that don't
agree on vocabulary:

- `settings.scheduleSettings.matchupPeriodCount` says how many matchup
  periods are *regular season*. Anything past it is playoffs.
- `teams[*].playoffSeed` is the final regular-season seeding. It is set for
  every team, not just the ones that qualified — a team is in the
  championship bracket only when its seed is `<= playoffTeamCount`.
- `schedule[*]` carries the bracket itself, but with no round labels and no
  `winner` field we can trust (this league returns `winner: null` on every
  game, including finished ones). Rounds and winners are inferred from the
  scores.

The single fact that drives everything downstream: a playoff game with
`0–0` has not been played, so its winner is `None` and the round is still
open. That's what keeps the UI from crowning a champion mid-bracket.

`ESPN scar tissue` note: team IDs are not dense, so every lookup here keys
on `team.id`, never on position in a list.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from pipeline import schema

log = logging.getLogger(__name__)

# Round names by how many rounds are left to play, so the labels stay right
# whether the bracket is 2, 4, or 8 teams wide.
_ROUNDS_REMAINING_LABEL: dict[int, str] = {
    1: "final",
    2: "semifinal",
    3: "quarterfinal",
}

# Ordinal finishes for the championship bracket's last round. Consolation
# placements are deliberately not ranked — ESPN's consolation ladder doesn't
# produce a meaningful 5th-through-8th order in this league's settings.
_PLACEMENT_LABEL: dict[int, str] = {
    1: "Champion",
    2: "Runner-up",
    3: "3rd place",
    4: "4th place",
}


def _round_label(rounds_remaining: int, index: int) -> str:
    return _ROUNDS_REMAINING_LABEL.get(rounds_remaining, f"round {index + 1}")


def _winner(home_pts: float, away_pts: float, home_id: int, away_id: int) -> int | None:
    """Winner of a played game, or None when it hasn't been decided.

    `0–0` means "not played yet" — ESPN pre-creates every bracket game with
    zeroed totals as soon as the matchup is known. A genuine 0–0 tie is not
    reachable in a points league where nine slots are scored, so treating it
    as undecided is safe.
    """
    if home_pts == 0.0 and away_pts == 0.0:
        return None
    if home_pts > away_pts:
        return home_id
    if away_pts > home_pts:
        return away_id
    return None  # a real tie — no winner advances


def build_season_result(
    league_raw: dict[str, Any],
    current_matchup_period: int,
) -> schema.SeasonResult | None:
    """Derive final standings + the playoff bracket from the raw league.

    Returns None when the league response is missing the schedule settings
    this needs — callers treat that as "no season summary available" rather
    than failing the whole refresh.
    """
    settings = (league_raw.get("settings") or {}).get("scheduleSettings") or {}
    regular_season_periods = settings.get("matchupPeriodCount")
    playoff_team_count = settings.get("playoffTeamCount")
    if not regular_season_periods or not playoff_team_count:
        log.warning("season: scheduleSettings missing matchupPeriodCount/playoffTeamCount — skipping")
        return None
    regular_season_periods = int(regular_season_periods)
    playoff_team_count = int(playoff_team_count)

    teams_raw = league_raw.get("teams") or []
    seed_by_team: dict[int, int | None] = {}
    record_by_team: dict[int, dict[str, Any]] = {}
    for t in teams_raw:
        tid = t.get("id")
        if tid is None:
            continue
        tid = int(tid)
        seed = t.get("playoffSeed")
        seed_by_team[tid] = int(seed) if seed else None
        record_by_team[tid] = (t.get("record") or {}).get("overall") or {}

    # ---- Bracket ----------------------------------------------------------
    by_period: dict[int, list[dict[str, Any]]] = {}
    for m in league_raw.get("schedule") or []:
        period = m.get("matchupPeriodId")
        if period is None:
            continue
        by_period.setdefault(int(period), []).append(m)

    playoff_periods = sorted(p for p in by_period if p > regular_season_periods)
    # Round count comes from the bracket's *size*, not from how many playoff
    # periods happen to be in the schedule yet: mid-bracket, ESPN may only
    # have materialized the rounds played so far, and counting those would
    # label the semifinal "final".
    total_rounds = max(1, math.ceil(math.log2(playoff_team_count))) if playoff_team_count > 1 else 1
    total_rounds = max(total_rounds, len(playoff_periods))

    bracket: list[schema.BracketGame] = []
    # Teams still alive in the championship bracket, round by round. Seeded
    # with everyone who qualified; each round narrows it to that round's
    # winners, which is what tells the next round's game apart from the
    # third-place game (losers) and the consolation ladder (never qualified).
    alive = {tid for tid, seed in seed_by_team.items() if seed and seed <= playoff_team_count}
    eliminated_this_round: set[int] = set()

    for index, period in enumerate(playoff_periods):
        rounds_remaining = total_rounds - index
        label = _round_label(rounds_remaining, index)
        next_alive: set[int] = set()
        next_eliminated: set[int] = set()
        for m in by_period[period]:
            home = m.get("home") or {}
            away = m.get("away") or {}
            home_id = int(home.get("teamId") or 0)
            away_id = int(away.get("teamId") or 0)
            if not home_id or not away_id:
                continue
            home_pts = float(home.get("totalPoints") or 0.0)
            away_pts = float(away.get("totalPoints") or 0.0)
            winner_id = _winner(home_pts, away_pts, home_id, away_id)
            played = not (home_pts == 0.0 and away_pts == 0.0)

            sides = {home_id, away_id}
            if sides <= alive:
                round_name = label
            elif sides <= eliminated_this_round and rounds_remaining == 1:
                # Both sides lost the previous championship round — this is
                # the third-place game, not another consolation rung.
                round_name = "third place"
            else:
                round_name = "consolation"

            bracket.append(schema.BracketGame(
                matchup_period_id=period,
                round=round_name,
                home_team_id=home_id,
                away_team_id=away_id,
                home_points=home_pts,
                away_points=away_pts,
                winner_team_id=winner_id,
                played=played,
            ))

            if round_name == label and sides <= alive:
                if winner_id is not None:
                    next_alive.add(winner_id)
                    next_eliminated.add(home_id if winner_id == away_id else away_id)
                else:
                    # Undecided — both sides stay alive so nothing downstream
                    # claims a result the bracket hasn't produced.
                    next_alive |= sides
        if next_alive:
            alive = next_alive
        eliminated_this_round = next_eliminated

    bracket.sort(key=lambda g: (g.matchup_period_id, g.home_team_id))

    # ---- Placements -------------------------------------------------------
    final_game = next((g for g in bracket if g.round == "final"), None)
    third_game = next((g for g in bracket if g.round == "third place"), None)

    placement_by_team: dict[int, int] = {}
    champion_id: int | None = None
    runner_up_id: int | None = None
    if final_game and final_game.winner_team_id is not None:
        champion_id = final_game.winner_team_id
        runner_up_id = (
            final_game.away_team_id if champion_id == final_game.home_team_id
            else final_game.home_team_id
        )
        placement_by_team[champion_id] = 1
        placement_by_team[runner_up_id] = 2
    if third_game and third_game.winner_team_id is not None:
        third_id = third_game.winner_team_id
        fourth_id = (
            third_game.away_team_id if third_id == third_game.home_team_id
            else third_game.home_team_id
        )
        placement_by_team[third_id] = 3
        placement_by_team[fourth_id] = 4

    # Which championship round each team lost in — the "your season ended
    # here" fact the banner leads with, and the only one available while the
    # bracket is still running.
    eliminated_round_by_team: dict[int, str] = {}
    for g in bracket:
        if g.round in ("consolation", "third place") or g.winner_team_id is None:
            continue
        loser = g.away_team_id if g.winner_team_id == g.home_team_id else g.home_team_id
        eliminated_round_by_team[loser] = g.round

    standings: list[schema.SeasonStanding] = []
    for tid, seed in seed_by_team.items():
        rec = record_by_team.get(tid) or {}
        made_playoffs = bool(seed and seed <= playoff_team_count)
        placement = placement_by_team.get(tid)
        standings.append(schema.SeasonStanding(
            team_id=tid,
            seed=seed,
            wins=int(rec.get("wins") or 0),
            losses=int(rec.get("losses") or 0),
            ties=int(rec.get("ties") or 0),
            points_for=float(rec.get("pointsFor") or 0.0),
            points_against=float(rec.get("pointsAgainst") or 0.0),
            made_playoffs=made_playoffs,
            placement=placement,
            placement_label=_PLACEMENT_LABEL.get(placement) if placement else None,
            eliminated_round=eliminated_round_by_team.get(tid),
        ))
    standings.sort(key=lambda s: (s.seed if s.seed else 99, -s.points_for))

    playoffs_complete = bool(
        final_game and final_game.winner_team_id is not None
        and (third_game is None or third_game.winner_team_id is not None)
    )
    return schema.SeasonResult(
        regular_season_complete=current_matchup_period > regular_season_periods,
        playoffs_complete=playoffs_complete,
        regular_season_periods=regular_season_periods,
        playoff_team_count=playoff_team_count,
        final_matchup_period_id=playoff_periods[-1] if playoff_periods else regular_season_periods,
        champion_team_id=champion_id,
        runner_up_team_id=runner_up_id,
        standings=standings,
        bracket=bracket,
    )
