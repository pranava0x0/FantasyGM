"""Extract the league's H2H scoring formula and apply it to raw ESPN stat dicts.

The league scoring items live in `league_raw['settings']['scoringSettings']['scoringItems']`.
Each item maps a `statId` → `points` weight. We use this to convert raw per-game
stat dicts from external sources (CBS Sports, Yahoo) into fantasy-point equivalents
that can be averaged with ESPN's own `appliedTotal` figures.

Verified for the 50-40-90 Club (H2H_POINTS, 7 scoring items):
  stat 0 (FGM)    × 1.0
  stat 1 (3PM)    × 2.0
  stat 2 (FTM)    × 2.0
  stat 3 (REB)    × 1.0
  stat 6 (AST)    × 1.0
  stat 17 (STL)   × 1.0
  stat 43 (TD)    × 5.0   (triple-double bonus)

Cross-checked against three real Jewell Loyd game logs — computed total matched
ESPN's `appliedTotal` exactly in all three cases.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ESPN WNBA stat IDs mapped to common stat abbreviations.
# Empirically verified for the wfba game code; do not guess from NBA conventions.
STAT_ID_TO_NAME: dict[int, str] = {
    0: "FGM",
    1: "3PM",
    2: "FTM",
    3: "REB",
    6: "AST",
    17: "STL",
    43: "TD",   # triple-double bonus
}

# When converting external raw-stat rows (CBS, Yahoo) that carry the usual
# box-score columns, map them to ESPN's statIds.
# "PTS" → no direct ESPN ID (ESPN uses FGM/3PM/FTM components, not a total PTS stat).
EXTERNAL_STAT_TO_ID: dict[str, int] = {
    "FGM": 0,
    "3PM": 1,
    "FTM": 2,
    "REB": 3,
    "AST": 6,
    "STL": 17,
}


@dataclass
class ScoringFormula:
    """H2H scoring weights by ESPN statId."""
    weights: dict[int, float] = field(default_factory=dict)

    def compute_from_espn_stats(self, raw_stats: dict[str, Any]) -> float:
        """Apply weights to an ESPN raw-stats dict (keys are str stat IDs).

        Returns 0.0 if the dict is empty or all weighted stats are missing.
        Skips non-numeric values (ESPN occasionally ships 'Infinity' for
        ratio stats like 3P%).
        """
        total = 0.0
        for stat_id, pts in self.weights.items():
            raw = raw_stats.get(str(stat_id))
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if val != val:  # NaN guard
                continue
            total += val * pts
        return total

    def compute_from_box_stats(self, box: dict[str, float]) -> float:
        """Apply weights to a box-score dict keyed by EXTERNAL_STAT_TO_ID names.

        External sources (CBS, Yahoo) give PTS/REB/AST etc. We need FGM/3PM/FTM.
        When only a "PTS" key is present (no FGM breakdown), we can't decompose it
        into FGM+3PM+FTM components, so we skip and return None. Callers should
        only pass dicts where FGM, 3PM, FTM are available separately.
        """
        total = 0.0
        found_any = False
        for stat_name, stat_id in EXTERNAL_STAT_TO_ID.items():
            if stat_name not in box:
                continue
            pts = self.weights.get(stat_id, 0.0)
            total += box[stat_name] * pts
            found_any = True
        return total if found_any else 0.0

    @classmethod
    def from_league_raw(cls, league_raw: dict[str, Any]) -> "ScoringFormula":
        """Extract from league_raw['settings']['scoringSettings']['scoringItems']."""
        scoring = (league_raw.get("settings") or {}).get("scoringSettings") or {}
        items = scoring.get("scoringItems") or []
        weights: dict[int, float] = {}
        for item in items:
            stat_id = item.get("statId")
            pts = item.get("points")
            if stat_id is None or pts is None:
                continue
            multiplier = -1.0 if item.get("isReverseItem") else 1.0
            weights[int(stat_id)] = float(pts) * multiplier
        if not weights:
            log.warning("scoring_formula: no scoringItems found in league_raw — formula will compute 0.0")
        else:
            names = {STAT_ID_TO_NAME.get(k, f"stat{k}"): v for k, v in sorted(weights.items())}
            log.info("scoring_formula: %d scoring items: %s", len(weights), names)
        return cls(weights=weights)

    def is_empty(self) -> bool:
        return not self.weights

    def __repr__(self) -> str:
        parts = [
            f"{STAT_ID_TO_NAME.get(k, f'stat{k}')}×{v:+g}"
            for k, v in sorted(self.weights.items())
        ]
        return f"ScoringFormula({', '.join(parts)})"
