"""ESPN WNBA position + lineup-slot decoding.

ESPN's API uses small integer codes for positions and lineup slots. These
codes are not documented publicly; mappings here are derived empirically
from the 50-40-90 Club league response (see scripts/probe_positions.py if
we ever need to re-verify).

Keep the raw IDs alongside the labels in stored data so a wrong mapping
here is fixable without re-fetching anything.
"""

from __future__ import annotations

from typing import Iterable, Literal

PositionBucket = Literal["G", "F", "C"]

# Default position ID -> short position label.
# WNBA only uses G / F / C; mapping is based on observed `defaultPositionId`
# values for known players in the 50-40-90 Club league.
DEFAULT_POSITION_LABEL: dict[int, str] = {
    1: "G",
    2: "F",
    3: "C",
}

# Lineup slot ID -> label. WNBA leagues on ESPN use a small subset.
# Verified empirically from 50-40-90 Club rosters:
#   slot 1  (count 2)  — Guards. Confirmed: Copper, Sykes both in slot 1.
#   slot 4  (count 3)  — Forwards. Confirmed: Alyssa Thomas in slot 4.
#   slot 5  (count 1)  — Center-eligible. Forwards can fill (Napheesa Collier
#                        in slot 5). Best label: F/C.
#   slot 6  (count 3)  — Utility flex (any position). Jewell Loyd, a Guard,
#                        in slot 6.
#   slot 7  (count 1)  — Bench. `isBenchUnlimited: true` overrides the count,
#                        so unbounded in practice.
# Slots 0/2/3/8 are 0-count in this league; left in the map so unexpected
# values don't crash. Re-verify with scripts/probe_positions.py if ESPN's
# slot numbering changes between seasons.
LINEUP_SLOT_LABEL: dict[int, str] = {
    0: "BE",
    1: "G",
    2: "F",
    3: "G/F",
    4: "F",
    5: "F/C",
    6: "UTIL",
    7: "BE",
    8: "BE",
}

# Which lineup slots count as "active" vs bench for weekly production math.
ACTIVE_SLOT_IDS: frozenset[int] = frozenset({1, 2, 3, 4, 5, 6})
BENCH_SLOT_IDS: frozenset[int] = frozenset({0, 7, 8})
IR_SLOT_IDS: frozenset[int] = frozenset()  # No dedicated IR slot in this league.


def position_label(default_position_id: int | None) -> str:
    """Return a short position label for a player's defaultPositionId."""
    if default_position_id is None:
        return "?"
    return DEFAULT_POSITION_LABEL.get(default_position_id, f"P{default_position_id}")


def slot_label(lineup_slot_id: int | None) -> str:
    """Return a short slot label for a player's current lineupSlotId."""
    if lineup_slot_id is None:
        return "?"
    return LINEUP_SLOT_LABEL.get(lineup_slot_id, f"S{lineup_slot_id}")


def position_bucket(default_position_id: int | None, eligible_slots: Iterable[int] | None = None) -> PositionBucket:
    """Bucket a player into G / F / C for weakness analysis.

    Uses defaultPositionId as the primary signal. Falls back to scanning
    eligibleSlots if the default position is unknown. Defaults to F for
    truly unclassifiable players — F is the largest bucket and the least
    distortion if we guess wrong.
    """
    label = DEFAULT_POSITION_LABEL.get(default_position_id or 0)
    if label in {"G", "F", "C"}:
        return label  # type: ignore[return-value]

    if eligible_slots:
        for slot in eligible_slots:
            if slot in (1, 2):
                return "G"
            if slot in (3, 4):
                return "F"
            if slot == 5:
                return "C"
    return "F"
