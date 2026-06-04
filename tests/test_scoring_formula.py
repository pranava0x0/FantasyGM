"""Tests for the scoring formula extraction and application.

Verified against real Jewell Loyd game logs from the 2026-06-01 snapshot:
- Period 21: stats {0:9, 1:1, 2:0, 3:1, 6:2, 17:2, 43:0} → appliedTotal=16.0
- Period 16: stats {0:10, 1:0, 2:0, 3:2, 6:0, 17:3, 43:0} → appliedTotal=15.0
- Period 10: stats {0:7, 1:0, 2:1, 3:1, 6:5, 17:2, 43:1} → appliedTotal=22.0

Formula for the 50-40-90 Club (7 scoring items):
  FGM(0)×1.0  3PM(1)×2.0  FTM(2)×2.0  REB(3)×1.0
  AST(6)×1.0  STL(17)×1.0  TD(43)×5.0
"""

from __future__ import annotations

import pytest

from pipeline.scoring_formula import ScoringFormula


_CLUB_SCORING_ITEMS = [
    {"statId": 6,  "points": 1.0, "isReverseItem": False},
    {"statId": 43, "points": 5.0, "isReverseItem": False},
    {"statId": 0,  "points": 1.0, "isReverseItem": False},
    {"statId": 17, "points": 1.0, "isReverseItem": False},
    {"statId": 1,  "points": 2.0, "isReverseItem": False},
    {"statId": 2,  "points": 2.0, "isReverseItem": False},
    {"statId": 3,  "points": 1.0, "isReverseItem": False},
]

_LEAGUE_RAW = {
    "settings": {
        "scoringSettings": {
            "scoringType": "H2H_POINTS",
            "scoringItems": _CLUB_SCORING_ITEMS,
        }
    }
}


class TestScoringFormulaExtraction:
    def test_from_league_raw(self) -> None:
        f = ScoringFormula.from_league_raw(_LEAGUE_RAW)
        assert len(f.weights) == 7
        assert f.weights[0] == 1.0   # FGM
        assert f.weights[1] == 2.0   # 3PM
        assert f.weights[2] == 2.0   # FTM
        assert f.weights[3] == 1.0   # REB
        assert f.weights[6] == 1.0   # AST
        assert f.weights[17] == 1.0  # STL
        assert f.weights[43] == 5.0  # TD

    def test_empty_league_raw(self) -> None:
        f = ScoringFormula.from_league_raw({})
        assert f.is_empty()
        assert f.compute_from_espn_stats({"0": 10.0}) == 0.0

    def test_reverse_item_negated(self) -> None:
        league = {
            "settings": {
                "scoringSettings": {
                    "scoringItems": [
                        {"statId": 9, "points": 1.0, "isReverseItem": True}
                    ]
                }
            }
        }
        f = ScoringFormula.from_league_raw(league)
        assert f.weights[9] == -1.0


class TestComputeFromEspnStats:
    def setup_method(self) -> None:
        self.f = ScoringFormula.from_league_raw(_LEAGUE_RAW)

    def test_loyd_period_21(self) -> None:
        raw = {"0": 9.0, "1": 1.0, "2": 0.0, "3": 1.0, "6": 2.0, "17": 2.0, "43": 0.0}
        assert self.f.compute_from_espn_stats(raw) == 16.0

    def test_loyd_period_16(self) -> None:
        raw = {"0": 10.0, "1": 0.0, "2": 0.0, "3": 2.0, "6": 0.0, "17": 3.0, "43": 0.0}
        assert self.f.compute_from_espn_stats(raw) == 15.0

    def test_loyd_period_10(self) -> None:
        raw = {"0": 7.0, "1": 0.0, "2": 1.0, "3": 1.0, "6": 5.0, "17": 2.0, "43": 1.0}
        assert self.f.compute_from_espn_stats(raw) == 22.0

    def test_skips_infinity_string(self) -> None:
        # ESPN ships "Infinity" for some ratio stats — should not crash.
        raw = {"0": 5.0, "35": "Infinity"}
        result = self.f.compute_from_espn_stats(raw)
        assert result == 5.0

    def test_empty_stats(self) -> None:
        assert self.f.compute_from_espn_stats({}) == 0.0

    def test_keys_as_strings(self) -> None:
        # ESPN stat dicts use str keys like "0", "1" — not int keys.
        raw = {"6": 4.0}  # 4 assists × 1.0
        assert self.f.compute_from_espn_stats(raw) == 4.0


class TestComputeFromBoxStats:
    def setup_method(self) -> None:
        self.f = ScoringFormula.from_league_raw(_LEAGUE_RAW)

    def test_standard_box(self) -> None:
        # FGM=8 × 1 + 3PM=2 × 2 + FTM=3 × 2 + REB=5 × 1 + AST=4 × 1 + STL=2 × 1
        #  = 8 + 4 + 6 + 5 + 4 + 2 = 29
        box = {"FGM": 8.0, "3PM": 2.0, "FTM": 3.0, "REB": 5.0, "AST": 4.0, "STL": 2.0}
        assert self.f.compute_from_box_stats(box) == 29.0

    def test_partial_box(self) -> None:
        # Only AST provided; formula still applies for known keys.
        box = {"AST": 6.0}
        assert self.f.compute_from_box_stats(box) == 6.0

    def test_empty_box(self) -> None:
        assert self.f.compute_from_box_stats({}) == 0.0
