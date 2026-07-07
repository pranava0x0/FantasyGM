"""One-time backfill: recover full news/social history from raw snapshots.

Every `data/raw/<date>/` snapshot already on disk (and tracked in git — none
have ever been deleted) carries that day's news.json + reddit/twitter/bluesky
raw feeds. Until now `build_state` only looked at the *latest* day's feed and
kept the top 5 per player, so anything older silently scrolled away each
refresh. This script replays every snapshot offline (no network, no ESPN
cookies needed) through the same normalizers `build_state` uses, and appends
whatever's new to `data/history/{news,social}.jsonl` — the persistent
archives that `pipeline.refresh` / `scripts/rebuild_state.py` now merge in on
every run.

Idempotent — safe to re-run; existing entries are never duplicated (deduped
by article id / post url inside append_news_history / append_social_history).

    python scripts/backfill_news_social_history.py

After it finishes, run `python scripts/rebuild_state.py` to regenerate
`docs/data/state.json` with the recovered history.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import build_state as build_state_mod
from pipeline import bluesky as bluesky_mod, instagram as instagram_mod, reddit as reddit_mod, twitter as twitter_mod

log = logging.getLogger("backfill_news_social_history")

ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
HISTORY_ROOT = ROOT / "data" / "history"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s | %(message)s")

    raw_dirs = sorted(p for p in DATA_RAW.iterdir() if p.is_dir() and p.name[:4].isdigit())
    if not raw_dirs:
        log.error("no raw snapshots under %s", DATA_RAW)
        return 1

    total_news = 0
    total_social = 0
    for raw_dir in raw_dirs:
        news_path = raw_dir / "news.json"
        news_raw = json.loads(news_path.read_text()) if news_path.exists() else None
        new_news = build_state_mod.append_news_history(news_raw, HISTORY_ROOT)
        total_news += new_news

        # reddit/twitter loaders fall back to a *live* network fetch when
        # their raw file is missing for a given day — wrong for an offline
        # replay of history, so we only call them when the file is present.
        posts_by_source = {
            "reddit": reddit_mod.load_reddit(raw_dir=raw_dir, limit=50) if (raw_dir / "reddit_raw.json").exists() else [],
            "twitter": twitter_mod.load_tweets(raw_dir=raw_dir, query="wnba", limit=50) if (raw_dir / "twitter_raw.json").exists() else [],
            "bluesky": bluesky_mod.load_bluesky(raw_dir=raw_dir),
            "instagram": instagram_mod.load_instagram(raw_dir=raw_dir),
        }
        new_social = build_state_mod.append_social_history(posts_by_source, HISTORY_ROOT)
        total_social += new_social

        log.info("%s: +%d news, +%d social", raw_dir.name, new_news, new_social)

    log.info("done: %d new news items, %d new social posts recovered", total_news, total_social)
    log.info("run `python scripts/rebuild_state.py` to regenerate state.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
