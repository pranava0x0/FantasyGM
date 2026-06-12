"""Trade scenario evaluator.

For each team, identifies their best active player by per-game projection,
then finds 1–3 player packages from each other team that fall within ±25%
of that player's ppg. Packages are ranked by a composite score that blends
value fairness and positional fit for the receiving team's top need.

Intended to answer: "If I trade away my best player, what's a fair return
from each other team in the league?"
"""
from __future__ import annotations

import itertools
import logging
from typing import Literal

from pipeline import schema

log = logging.getLogger(__name__)

_TOLERANCE = 0.25    # ±25% of target ppg is considered "fair value"
_MAX_PKG_SIZE = 3    # consider combos up to 3 players


def _active_with_ppg(team: schema.TeamState) -> list[tuple[schema.PlayerRef, float]]:
    """Return (player, ppg) for active (non-bench, non-IR) slots with a projection."""
    result = []
    for e in team.roster:
        if not e.is_active:
            continue
        ppg = e.projected_per_game
        if ppg and ppg > 0:
            result.append((e.player, ppg))
    return result


def _need_fit(bucket: Literal["G", "F", "C"], top_need: Literal["G", "FC"]) -> float:
    """Score how well a player's bucket fills a team's top need (0–1)."""
    if top_need == "G":
        return 1.0 if bucket == "G" else 0.15
    # FC need: forwards and centers both fit
    return 1.0 if bucket in ("F", "C") else 0.15


def _pkg_fit(combo: list[tuple[schema.PlayerRef, float]], top_need: Literal["G", "FC"]) -> float:
    """Weighted-average need fit for a package, weighted by each player's ppg."""
    total = sum(ppg for _, ppg in combo)
    if not total:
        return 0.0
    return sum(_need_fit(p.bucket, top_need) * ppg for p, ppg in combo) / total


def build_trade_scenarios(teams: list[schema.TeamState]) -> list[schema.TradeScenario]:
    """Build one TradeScenario per team.

    For each team:
    - Identify the best active player (highest projected_per_game).
    - For every other team, find the 1–3 player combo with the best composite
      score (fairness × fit) whose total ppg is within ±25% of the best player's ppg.
    - Return one scenario per team with offers sorted by composite_score descending.
    """
    scenarios: list[schema.TradeScenario] = []

    for recv in teams:
        active = sorted(_active_with_ppg(recv), key=lambda x: x[1], reverse=True)
        if not active:
            log.debug("trades: %s has no active players with ppg — skipping", recv.abbrev)
            continue

        best_ref, best_ppg = active[0]
        best_player = schema.TradePlayer(
            player_id=best_ref.player_id,
            name=best_ref.name,
            fantasy_team_id=recv.team_id,
            fantasy_team_abbrev=recv.abbrev,
            projected_per_game=round(best_ppg, 1),
            bucket=best_ref.bucket,
        )

        lo = best_ppg * (1 - _TOLERANCE)
        hi = best_ppg * (1 + _TOLERANCE)
        top_need = recv.needs.top_need_bucket

        # For each other team, find their best-scoring valid package.
        best_by_team: dict[int, schema.TradeOffer] = {}

        for offr in teams:
            if offr.team_id == recv.team_id:
                continue

            # Filter to candidates who could plausibly contribute to a fair package.
            candidates = [
                (p, ppg)
                for p, ppg in _active_with_ppg(offr)
                if ppg >= lo * 0.2  # prune players too small to ever matter in a 3-pack
            ]

            for size in range(1, min(_MAX_PKG_SIZE, len(candidates)) + 1):
                for combo in itertools.combinations(candidates, size):
                    total = sum(ppg for _, ppg in combo)
                    if total < lo or total > hi:
                        continue

                    ratio = total / best_ppg
                    fairness = 1.0 - abs(1.0 - ratio)   # 1.0 at exact match, approaches 0 at tolerance edge
                    fit = _pkg_fit(list(combo), top_need)
                    composite = 0.6 * fairness + 0.4 * fit

                    offer = schema.TradeOffer(
                        from_team_id=offr.team_id,
                        from_team_abbrev=offr.abbrev,
                        pkg_received=schema.TradePkg(
                            players=[
                                schema.TradePlayer(
                                    player_id=p.player_id,
                                    name=p.name,
                                    fantasy_team_id=offr.team_id,
                                    fantasy_team_abbrev=offr.abbrev,
                                    projected_per_game=round(ppg, 1),
                                    bucket=p.bucket,
                                )
                                for p, ppg in combo
                            ],
                            total_ppg=round(total, 1),
                        ),
                        value_ratio=round(ratio, 3),
                        need_fit_score=round(fit, 3),
                        composite_score=round(composite, 3),
                    )

                    prev = best_by_team.get(offr.team_id)
                    if prev is None or composite > prev.composite_score:
                        best_by_team[offr.team_id] = offer

        offers = sorted(best_by_team.values(), key=lambda o: o.composite_score, reverse=True)

        log.info(
            "trades: %s best=%s (%.1f/g), %d/%d teams have a fair package",
            recv.abbrev, best_player.name, best_ppg, len(offers), len(teams) - 1,
        )

        scenarios.append(schema.TradeScenario(
            team_id=recv.team_id,
            team_abbrev=recv.abbrev,
            team_name=recv.name,
            best_player=best_player,
            top_need_bucket=top_need,
            offers=offers,
        ))

    return scenarios
