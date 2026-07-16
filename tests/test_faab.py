"""Tests for FAAB bid guidance.

The properties that matter, in the order a wrong answer costs money:

1. We never suggest a bid the team can't afford.
2. We never quote a market from a sample too thin to mean anything.
3. The band reflects the *real* distribution — including the $28 tail the
   spec's "$1-8 market" assumption would have hidden.
4. Only executed claims count; a failed bid never cleared the market.
"""

from __future__ import annotations

import pytest

from pipeline import faab


def _claim(bid: int, *, status: str = "EXECUTED", type_: str = "WAIVER", add: bool = True) -> dict:
    return {
        "transaction_id": f"tx-{bid}-{status}",
        "type": type_,
        "status": status,
        "bid_amount": bid,
        "items": [{"player_id": 1, "type": "ADD" if add else "DROP"}],
    }


# The real 2026-07-06 distribution: 39 executed claims, 13 free, $1-$28.
REAL_BIDS = [0] * 13 + [1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 5, 5, 6, 7, 8, 8, 10, 15, 17, 17, 28]


def _real_market() -> dict:
    m = faab.build_market([_claim(b) for b in REAL_BIDS])
    assert m is not None
    return m


class TestWinningBids:
    def test_only_executed_claims_count(self) -> None:
        txs = [
            _claim(5, status="EXECUTED"),
            _claim(99, status="FAILED_ROSTERLIMIT"),
            _claim(88, status="PENDING"),
            _claim(77, status="CANCELED"),
        ]
        # A bid that never cleared says nothing about what a player costs.
        assert faab.winning_bids(txs) == [5]

    def test_only_waiver_type_counts(self) -> None:
        txs = [_claim(5), _claim(9, type_="ROSTER"), _claim(7, type_="TRADE_ACCEPT")]
        assert faab.winning_bids(txs) == [5]

    def test_drop_only_claim_ignored(self) -> None:
        assert faab.winning_bids([_claim(5, add=False)]) == []

    def test_zero_bids_are_kept(self) -> None:
        # A third of real claims are uncontested and genuinely cost $0.
        # Dropping them would inflate the apparent price of an average add.
        assert faab.winning_bids([_claim(0), _claim(2)]) == [0, 2]

    def test_non_numeric_bid_is_skipped_not_fatal(self) -> None:
        bad = {**_claim(1), "bid_amount": "lots"}
        assert faab.winning_bids([bad, _claim(3)]) == [3]

    def test_missing_bid_is_skipped(self) -> None:
        no_bid = {**_claim(1)}
        del no_bid["bid_amount"]
        assert faab.winning_bids([no_bid, _claim(3)]) == [3]


class TestBuildMarket:
    def test_thin_sample_quotes_nothing(self) -> None:
        # Three data points are an anecdote, not a market.
        assert faab.build_market([_claim(b) for b in (1, 2, 3)]) is None

    def test_empty_history_quotes_nothing(self) -> None:
        assert faab.build_market([]) is None

    def test_real_distribution(self) -> None:
        m = _real_market()
        assert m["sample_n"] == 39
        assert m["free_claims"] == 13
        assert m["median"] == 2
        assert m["max_bid"] == 28

    def test_tail_is_not_clipped(self) -> None:
        # The spec assumed a "$1-8 market". Five real claims exceeded $8 and
        # the top was $28 — quoting $8 as the ceiling would understate a
        # contested player ~3.5x, exactly when guidance matters most.
        assert _real_market()["max_bid"] > 8


class TestSuggestBid:
    def test_no_market_no_suggestion(self) -> None:
        assert faab.suggest_bid(None, net_gain=10, reference_gain=10, faab_remaining=50) is None

    def test_never_exceeds_budget(self) -> None:
        # Advice to spend $12 with $3 left is worse than useless.
        g = faab.suggest_bid(_real_market(), net_gain=100, reference_gain=1, faab_remaining=3)
        assert g is not None
        assert g["suggested_hi"] <= 3
        assert g["suggested_lo"] <= 3

    def test_broke_team_gets_no_band(self) -> None:
        assert faab.suggest_bid(_real_market(), net_gain=10, reference_gain=10, faab_remaining=0) is None

    def test_never_suggests_zero(self) -> None:
        # $0 loses to any contested claim; the point of guidance is winning.
        g = faab.suggest_bid(_real_market(), net_gain=0.1, reference_gain=100, faab_remaining=50)
        assert g is not None
        assert g["suggested_lo"] >= faab.MIN_BID

    def test_lo_never_exceeds_hi(self) -> None:
        for gain in (0.1, 1, 5, 20, 500):
            g = faab.suggest_bid(_real_market(), net_gain=gain, reference_gain=10, faab_remaining=85)
            assert g is not None
            assert g["suggested_lo"] <= g["suggested_hi"], gain

    def test_bigger_upgrade_bids_more(self) -> None:
        m = _real_market()
        typical = faab.suggest_bid(m, net_gain=10, reference_gain=10, faab_remaining=85)
        standout = faab.suggest_bid(m, net_gain=30, reference_gain=10, faab_remaining=85)
        assert typical is not None and standout is not None
        assert standout["suggested_hi"] > typical["suggested_hi"]

    def test_never_exceeds_what_the_league_has_ever_paid(self) -> None:
        # We quote this market; we don't invent one.
        g = faab.suggest_bid(_real_market(), net_gain=10_000, reference_gain=1, faab_remaining=None)
        assert g is not None
        assert g["suggested_hi"] <= _real_market()["max_bid"]

    def test_zero_reference_does_not_crash(self) -> None:
        # Every target nets zero (a saturated roster) — no division by zero.
        g = faab.suggest_bid(_real_market(), net_gain=0, reference_gain=0, faab_remaining=85)
        assert g is not None
        assert g["suggested_lo"] >= faab.MIN_BID

    def test_carries_provenance(self) -> None:
        # A band without its n reads like a market rate. With it, it reads as
        # what it is: this league's revealed prices.
        g = faab.suggest_bid(_real_market(), net_gain=10, reference_gain=10, faab_remaining=85)
        assert g is not None
        assert g["sample_n"] == 39
        assert g["free_claims"] == 13
        assert g["league_max"] == 28
        assert g["faab_remaining"] == 85

    @pytest.mark.parametrize("budget", [1, 2, 5, 85, None])
    def test_band_is_always_payable(self, budget: int | None) -> None:
        g = faab.suggest_bid(_real_market(), net_gain=50, reference_gain=5, faab_remaining=budget)
        assert g is not None
        if budget is not None:
            assert g["suggested_hi"] <= budget
