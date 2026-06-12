"""Backfill all season transaction history from ESPN.

ESPN's mTransactions2 view only returns transactions for the current scoring
period. To get full-season history, this script iterates scoring periods 1..N
and fetches mTransactions2 for each, then deduplicates and writes the combined
result to data/history/transactions.jsonl.

Run once from the project root:
    python scripts/backfill_transactions.py

The script is idempotent — existing entries in transactions.jsonl are never
overwritten; only genuinely new transaction_ids are appended.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.analyze import normalize_transactions
from pipeline.espn_client import ESPNClient, ESPNCredentials

log = logging.getLogger("backfill_transactions")
logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")

LEAGUE_ID = 2043154241
SEASON = 2026
ROOT = Path(__file__).resolve().parent.parent


def _find_dotenv(start: Path) -> Path | None:
    """Walk up from `start` to find the nearest .env file."""
    for p in [start, *start.parents]:
        candidate = p / ".env"
        if candidate.exists():
            return candidate
    return None


def main() -> int:
    env_file = _find_dotenv(ROOT)
    if env_file:
        load_dotenv(env_file)
        # Data lives next to the .env (main project root, not the worktree)
        data_root = env_file.parent
    else:
        load_dotenv()
        data_root = ROOT
    creds = ESPNCredentials.from_env()

    history_path = data_root / "data" / "history" / "transactions.jsonl"
    raw_data_root = data_root / "data" / "raw"

    # Determine current scoring period from latest snapshot
    raw_dirs = sorted(p for p in raw_data_root.iterdir() if p.is_dir()) if raw_data_root.exists() else []
    max_period = 1
    if raw_dirs:
        try:
            league_path = raw_dirs[-1] / "league.json"
            league_data = json.loads(league_path.read_text())
            max_period = int(league_data.get("scoringPeriodId") or 1)
        except Exception as e:
            log.warning("Could not read latest snapshot period: %s", e)

    log.info("Backfilling periods 1..%d", max_period)

    # Load already-seen IDs
    seen: set[str] = set()
    if history_path.exists():
        for line in history_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                seen.add(json.loads(line)["transaction_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    log.info("Pre-existing unique transactions: %d", len(seen))

    all_new: list[dict] = []

    with ESPNClient(LEAGUE_ID, SEASON, creds) as client:
        for period in range(1, max_period + 1):
            try:
                data = client.fetch_league(
                    ["mTransactions2"],
                    scoring_period_id=period,
                )
                txns = normalize_transactions(data)
                new_for_period = [t for t in txns if t.get("transaction_id") and t["transaction_id"] not in seen]
                for t in new_for_period:
                    seen.add(t["transaction_id"])
                    all_new.append(t)
                log.info("Period %2d: %d transactions (%d new)", period, len(txns), len(new_for_period))
            except Exception as e:
                log.warning("Period %d failed: %s", period, e)
            time.sleep(1.5)

    if not all_new:
        log.info("No new transactions found.")
        return 0

    # Sort newest first before writing
    all_new.sort(
        key=lambda r: r["occurred_at"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # Write to transactions.jsonl — we need to serialize datetime to string
    # and match the schema.Transaction JSON format.
    import pipeline.schema as schema_mod

    history_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with history_path.open("a") as fp:
        for tx in all_new:
            items = [
                schema_mod.TransactionItem(
                    player_id=int(it["player_id"]),
                    player_name=it.get("player_name"),
                    from_team_id=it.get("from_team_id"),
                    to_team_id=it.get("to_team_id"),
                    from_slot_id=it.get("from_slot_id"),
                    to_slot_id=it.get("to_slot_id"),
                    type=it.get("type") or "UNKNOWN",
                )
                for it in (tx.get("items") or [])
                if it.get("player_id") is not None
            ]
            record = schema_mod.Transaction(
                transaction_id=str(tx["transaction_id"]),
                occurred_at=tx["occurred_at"],
                scoring_period_id=int(tx.get("scoring_period_id") or 0),
                team_id=tx.get("team_id"),
                type=tx.get("type") or "UNKNOWN",
                bid_amount=int(tx.get("bid_amount") or 0),
                status=tx.get("status") or "UNKNOWN",
                items=items,
            )
            fp.write(record.model_dump_json() + "\n")
            written += 1

    log.info("Wrote %d new transactions to %s", written, history_path)
    log.info("Run `python scripts/rebuild_state.py` to update state.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
