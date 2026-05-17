"""Tests for the WNBA position + lineup-slot decoding tables."""

from __future__ import annotations

from pipeline.positions import (
    ACTIVE_SLOT_IDS,
    BENCH_SLOT_IDS,
    DEFAULT_POSITION_LABEL,
    LINEUP_SLOT_LABEL,
    position_bucket,
    position_label,
    slot_label,
)


class TestDefaultPositionLabel:
    def test_known_position_ids(self) -> None:
        assert DEFAULT_POSITION_LABEL[1] == "G"
        assert DEFAULT_POSITION_LABEL[2] == "F"
        assert DEFAULT_POSITION_LABEL[3] == "C"

    def test_position_label_known(self) -> None:
        assert position_label(1) == "G"
        assert position_label(2) == "F"
        assert position_label(3) == "C"

    def test_position_label_unknown(self) -> None:
        assert position_label(99) == "P99"

    def test_position_label_none(self) -> None:
        assert position_label(None) == "?"


class TestLineupSlotLabel:
    def test_active_slots_have_labels(self) -> None:
        # Slots actually used in the 50-40-90 Club league must all be labeled.
        for slot in (1, 4, 5, 6):
            assert slot in LINEUP_SLOT_LABEL
            assert LINEUP_SLOT_LABEL[slot] != ""

    def test_bench_label(self) -> None:
        # Slot 7 is bench despite isBenchUnlimited overriding count.
        assert LINEUP_SLOT_LABEL[7] == "BE"

    def test_slot_label_unknown_falls_back(self) -> None:
        assert slot_label(42) == "S42"
        assert slot_label(None) == "?"


class TestPositionBucket:
    def test_default_position_g(self) -> None:
        assert position_bucket(1) == "G"

    def test_default_position_f(self) -> None:
        assert position_bucket(2) == "F"

    def test_default_position_c(self) -> None:
        assert position_bucket(3) == "C"

    def test_falls_back_to_eligible_slots_guard(self) -> None:
        # Unknown defaultPositionId but eligible for slot 1 → G.
        assert position_bucket(None, [1, 6, 7]) == "G"

    def test_falls_back_to_eligible_slots_center(self) -> None:
        assert position_bucket(None, [5, 6, 7]) == "C"

    def test_returns_forward_on_empty(self) -> None:
        # F is the safe default — largest bucket means smallest distortion.
        assert position_bucket(None, []) == "F"


class TestActiveBenchPartition:
    def test_no_overlap(self) -> None:
        assert ACTIVE_SLOT_IDS.isdisjoint(BENCH_SLOT_IDS)

    def test_bench_includes_7(self) -> None:
        assert 7 in BENCH_SLOT_IDS

    def test_active_includes_guard_slot(self) -> None:
        assert 1 in ACTIVE_SLOT_IDS
