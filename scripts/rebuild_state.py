"""Rebuild `docs/data/state.json` from the latest on-disk raw snapshot.

Unlike `pipeline.refresh`, this never touches the network or the ESPN API —
it replays the most recent `data/raw/<date>/` snapshot through `build_state`.
Use it to regenerate `state.json` after a code or data change (e.g. new AI
summaries, schema tweaks) without needing the user's ESPN session cookies.

    python scripts/rebuild_state.py            # latest snapshot
    python scripts/rebuild_state.py 2026-06-01 # a specific date

The social/news feeds are read from the same snapshot directory (news.json,
reddit_raw.json, twitter_raw.json, bluesky_raw.json) so the output is a
faithful, deterministic replay of what `refresh` produced that day.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import build_state as build_state_mod
from pipeline import bluesky as bluesky_mod, instagram as instagram_mod, reddit as reddit_mod, twitter as twitter_mod

log = logging.getLogger("rebuild_state")

_SCRIPT_ROOT = Path(__file__).resolve().parent.parent


def _find_project_root(start: Path) -> Path:
    """Return the nearest ancestor that contains a .env file, else the script root."""
    for p in [start, *start.parents]:
        if (p / ".env").exists():
            return p
    return start


ROOT = _find_project_root(_SCRIPT_ROOT)
DATA_RAW = ROOT / "data" / "raw"
DOCS = ROOT / "docs"


def _latest_snapshot_dir() -> Path:
    dates = sorted(p for p in DATA_RAW.iterdir() if p.is_dir() and p.name[:4].isdigit())
    if not dates:
        raise SystemExit(f"no raw snapshots under {DATA_RAW}")
    return dates[-1]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s | %(message)s")
    argv = argv if argv is not None else sys.argv[1:]

    raw_dir = (DATA_RAW / argv[0]) if argv else _latest_snapshot_dir()
    if not raw_dir.exists():
        raise SystemExit(f"snapshot dir not found: {raw_dir}")
    log.info("rebuilding from %s", raw_dir)

    league = json.loads((raw_dir / "league.json").read_text())
    free_agents = json.loads((raw_dir / "free_agents.json").read_text())

    news_path = raw_dir / "news.json"
    news_raw = json.loads(news_path.read_text()) if news_path.exists() else None

    reddit_posts = reddit_mod.load_reddit(raw_dir=raw_dir, limit=50)
    twitter_posts = twitter_mod.load_tweets(raw_dir=raw_dir, query="wnba", limit=50)
    bluesky_posts = bluesky_mod.load_bluesky(raw_dir=raw_dir)
    instagram_posts = instagram_mod.load_instagram(raw_dir=raw_dir)

    player_socials_path = ROOT / "data" / "player_socials.json"
    player_socials_raw: dict[str, dict[str, str]] = {}
    if player_socials_path.exists():
        try:
            raw_soc = json.loads(player_socials_path.read_text())
            player_socials_raw = raw_soc.get("players") or {}
        except Exception:
            pass

    ai_path = ROOT / "data" / "ai_summaries.json"
    ai_summaries = (
        (json.loads(ai_path.read_text()).get("summaries") or {})
        if ai_path.exists()
        else {}
    )
    log.info("loaded %d AI summaries", len(ai_summaries))

    # Load full transaction history from the append-only log so state.json
    # reflects the complete season, not just the current period's snapshot.
    txn_history_path = ROOT / "data" / "history" / "transactions.jsonl"
    extra_transactions: list[dict] = []
    if txn_history_path.exists():
        for line in txn_history_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                extra_transactions.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        log.info("loaded %d transactions from history log", len(extra_transactions))

    meta = json.loads((raw_dir / "_meta.json").read_text()) if (raw_dir / "_meta.json").exists() else {}
    captured_raw = meta.get("captured_at")
    captured_at = (
        datetime.fromisoformat(captured_raw.replace("Z", "+00:00"))
        if captured_raw
        else datetime.fromisoformat(raw_dir.name + "T00:00:00+00:00")
    )

    state = build_state_mod.build_state(
        league_raw=league,
        free_agents_raw=free_agents,
        captured_at=captured_at,
        news_raw=news_raw,
        reddit_posts=reddit_posts,
        twitter_posts=twitter_posts,
        bluesky_posts=bluesky_posts,
        instagram_posts=instagram_posts,
        player_socials_raw=player_socials_raw,
        ai_summaries=ai_summaries,
        extra_transactions=extra_transactions,
    )

    out = build_state_mod.write_state(state, DOCS)
    log.info(
        "wrote %s | %d teams, %d overall targets, %d news, "
        "reddit=%d twitter=%d bluesky=%d instagram=%d socials=%d",
        out.relative_to(ROOT),
        len(state.teams),
        len(state.waiver_targets_overall),
        len(state.news_recent),
        sum(len(v) for v in state.reddit_posts_by_player.values()),
        sum(len(v) for v in state.twitter_posts_by_player.values()),
        sum(len(v) for v in state.bluesky_posts_by_player.values()),
        sum(len(v) for v in state.instagram_posts_by_player.values()),
        len(state.player_socials),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
