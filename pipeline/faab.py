"""FAAB bid guidance, mined from the league's own executed waiver claims.

We hold both sides of this market: every team's `faab_remaining` and the full
history of what winning claims actually cost (`data/history/transactions.jsonl`).
Nobody has to guess what a player "should" go for — this league has already
revealed its prices.

**What the data said, and how it changed the design.** The spec (§3.A3)
proposed percentile bands bucketed by the added player's trailing per-game
value, in `<10 / 10-20 / 20+` ppg tiers, over a "$1-8 market". Measured against
the real 338-row history, all three assumptions broke:

- **The market is not $1-8.** 26 paid claims range $1-$28. Five exceeded $8.
  Quoting "$1-8" would understate a contested player by ~3.5x — precisely the
  case where a GM consults bid guidance at all.
- **The `<10` tier is empty by construction.** Every claimed player's trailing
  average sits between 15 and 35 fpts/g. Nobody spends a waiver claim on a
  sub-10 player, so two of the three proposed tiers never populate.
- **Bid barely tracks player value.** correlation(bid, trailing per-game) =
  0.30 over n=27 — not significant. Splitting 26 bids into value tiers on that
  basis would manufacture precision the sample cannot support.

So this module does the honest thing instead: report the league's actual
winning-bid distribution *with its sample size*, and scale the suggestion by
the one signal we can defend — our own net-gain estimate for this roster
(`lineups.net_gain_for_add`), which is computed, not correlated.

A third of executed claims cost $0 — an uncontested claim is genuinely free —
so zero-bid wins are kept in the distribution. Dropping them would inflate the
apparent price of an average add.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

log = logging.getLogger(__name__)

# Minimum sample before we're willing to quote the league's market at all.
# Below this the "distribution" is anecdote; the UI shows no band rather than
# a confident-looking number drawn from three data points.
MIN_SAMPLE = 8

# Never suggest bidding zero when a bid is warranted — $0 loses to any
# contested claim, and the whole point of guidance is winning the player.
MIN_BID = 1


def _percentile(sorted_vals: Sequence[int], p: float) -> int:
    """Nearest-rank percentile. Integer dollars in, integer dollars out."""
    if not sorted_vals:
        raise ValueError("percentile of an empty sample")
    idx = int(round(p * (len(sorted_vals) - 1)))
    return int(sorted_vals[max(0, min(len(sorted_vals) - 1, idx))])


def winning_bids(transactions: Sequence[dict[str, Any]]) -> list[int]:
    """Every executed waiver claim's winning price, oldest first.

    Only EXECUTED claims count. A FAILED or CANCELED claim's bid never cleared
    the market, so it says nothing about what a player costs; PENDING ones
    haven't resolved yet.
    """
    out: list[int] = []
    for tx in transactions:
        if tx.get("type") != "WAIVER" or tx.get("status") != "EXECUTED":
            continue
        # A waiver claim without an ADD is a drop-only or malformed row.
        if not any((it or {}).get("type") == "ADD" for it in (tx.get("items") or [])):
            continue
        bid = tx.get("bid_amount")
        if bid is None:
            continue
        try:
            out.append(max(0, int(bid)))
        except (TypeError, ValueError):
            log.warning("faab: non-numeric bid_amount %r on %s", bid, tx.get("transaction_id"))
    return out


def build_market(transactions: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Summarize what winning claims have actually cost in this league.

    Returns None when the sample is too thin to quote honestly (< MIN_SAMPLE).
    """
    bids = sorted(winning_bids(transactions))
    if len(bids) < MIN_SAMPLE:
        log.info("faab: only %d executed claim(s) — too few to quote a market", len(bids))
        return None
    free = sum(1 for b in bids if b == 0)
    market = {
        "sample_n": len(bids),
        "free_claims": free,
        "min_bid": bids[0],
        "p25": _percentile(bids, 0.25),
        "median": _percentile(bids, 0.50),
        "p75": _percentile(bids, 0.75),
        "p90": _percentile(bids, 0.90),
        "max_bid": bids[-1],
    }
    log.info(
        "faab: market from %d executed claim(s) — %d free, median $%d, p90 $%d, max $%d",
        market["sample_n"], free, market["median"], market["p90"], market["max_bid"],
    )
    return market


def suggest_bid(
    market: dict[str, Any] | None,
    *,
    net_gain: float,
    reference_gain: float,
    faab_remaining: int | None,
) -> dict[str, Any] | None:
    """A bid band for one target, scaled by how much it improves *this* roster.

    `net_gain` is the points this add would add to the team's optimal lineup;
    `reference_gain` is the median net gain across this week's top targets. A
    player worth twice the median upgrade is worth roughly twice the median
    price — that ratio, not a value tier, is what moves the band (see the
    module docstring for why).

    The band spans median->p75 at a typical gain and stretches toward p90 for
    standout adds. It is clamped to the team's actual budget, because advice to
    spend $12 with $6 left is worse than useless.
    """
    if market is None:
        return None
    if faab_remaining is not None and faab_remaining <= 0:
        return None

    ratio = 1.0
    if reference_gain > 0 and net_gain > 0:
        # Cap the multiplier: one exceptional target shouldn't imply a bid
        # beyond anything this league has ever actually paid.
        ratio = min(3.0, net_gain / reference_gain)

    lo = market["median"] * ratio
    hi = market["p75"] * ratio
    # A genuinely standout add is a p90-type claim, but never beyond the
    # league's observed ceiling — we quote this market, we don't invent one.
    hi = min(max(hi, market["p90"] if ratio >= 2.0 else hi), market["max_bid"])

    lo_i = max(MIN_BID, int(round(lo)))
    hi_i = max(lo_i, int(round(hi)))

    if faab_remaining is not None:
        if faab_remaining < MIN_BID:
            return None
        lo_i = min(lo_i, faab_remaining)
        hi_i = min(hi_i, faab_remaining)

    return {
        "suggested_lo": lo_i,
        "suggested_hi": hi_i,
        "league_median": market["median"],
        "league_max": market["max_bid"],
        "sample_n": market["sample_n"],
        "free_claims": market["free_claims"],
        "faab_remaining": faab_remaining,
    }
