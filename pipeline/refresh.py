"""CLI entrypoint: refresh the whole pipeline.

Usage:
    python -m pipeline.refresh                     # default paths
    python -m pipeline.refresh --root . --docs docs
    python -m pipeline.refresh --dry-run           # validate cookies only

Reads .env automatically.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline import build_state as build_state_mod
from pipeline import bluesky as bluesky_mod, ingest, news, reddit as reddit_mod, twitter as twitter_mod
from pipeline.espn_client import (
    ESPNAPIError,
    ESPNAuthError,
    ESPNClient,
    ESPNCredentials,
)
from pipeline.projections_ext import fetch_all_external
from pipeline.scoring_formula import ScoringFormula


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_dotenv(root: Path) -> None:
    """Manual dotenv loader so we don't blow up if python-dotenv isn't installed."""
    env_path = root / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        # Fallback: parse KEY=VALUE lines ourselves.
        import os
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the FantasyGM pipeline.")
    parser.add_argument("--root", default=".", help="Project root (default: cwd)")
    parser.add_argument("--docs", default="docs", help="Docs site root (default: docs)")
    parser.add_argument("--data", default="data", help="Data root (default: data)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate cookies + fetch a single view, do not write anything.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("refresh")

    root = Path(args.root).resolve()
    _load_dotenv(root)

    import os
    try:
        creds = ESPNCredentials.from_env()
    except ESPNAuthError as e:
        log.error(str(e))
        return 2

    league_id = int(os.environ.get("ESPN_LEAGUE_ID", "2043154241"))
    season = int(os.environ.get("ESPN_SEASON", "2026"))
    log.info("league_id=%d season=%d dry_run=%s", league_id, season, args.dry_run)

    data_root = root / args.data
    docs_root = root / args.docs

    with ESPNClient(league_id, season, creds) as client:
        if args.dry_run:
            try:
                league = client.fetch_league(views=["mSettings"])
            except (ESPNAuthError, ESPNAPIError) as e:
                log.error("dry-run failed: %s", e)
                return 3
            name = (league.get("settings") or {}).get("name")
            log.info("dry-run OK: league=%r scoringPeriod=%s", name, league.get("scoringPeriodId"))
            return 0

        try:
            snap = ingest.snapshot(client, data_root)
        except (ESPNAuthError, ESPNAPIError) as e:
            log.error("ingest failed: %s", e)
            return 3

    # News + Reddit are fetched outside the ESPN client (public, no auth).
    log.info("refresh: fetching WNBA news feed")
    news_raw = news.fetch_news(limit=50)
    (snap.out_dir / "news.json").write_text(json.dumps(news_raw, indent=2) + "\n")

    log.info("refresh: loading Reddit posts (r/wnba + r/fantasywnba)")
    reddit_posts = reddit_mod.load_reddit(raw_dir=snap.out_dir, limit=50)
    log.info("refresh: %d Reddit posts loaded", len(reddit_posts))

    log.info("refresh: loading Twitter/X WNBA posts")
    twitter_posts = twitter_mod.load_tweets(raw_dir=snap.out_dir, query="wnba", limit=50)
    log.info("refresh: %d Twitter posts loaded", len(twitter_posts))

    log.info("refresh: loading Bluesky posts")
    bluesky_posts = bluesky_mod.load_bluesky(raw_dir=snap.out_dir)
    log.info("refresh: %d Bluesky posts loaded", len(bluesky_posts))

    # AI "why pick them up" summaries, authored out-of-band and committed at
    # data/ai_summaries.json. Optional — absent file just means no summaries.
    ai_path = data_root / "ai_summaries.json"
    ai_summaries: dict[str, str] = {}
    if ai_path.exists():
        try:
            ai_summaries = json.loads(ai_path.read_text()).get("summaries") or {}
        except (json.JSONDecodeError, OSError) as e:
            log.warning("refresh: failed to read %s (%s)", ai_path, e)
    log.info("refresh: %d AI summaries loaded", len(ai_summaries))

    # External projections (CBS Sports, Yahoo Sports). Fail gracefully —
    # network errors or changed page structure return empty dicts and the
    # pipeline continues with ESPN-only projections.
    log.info("refresh: fetching external projections (CBS Sports, Yahoo Sports)")
    scoring_formula = ScoringFormula.from_league_raw(snap.league)
    ext_projections_by_source = fetch_all_external(
        scoring_formula,
        cache_dir=snap.out_dir,
    )
    if ext_projections_by_source:
        counts = {src: len(proj) for src, proj in ext_projections_by_source.items()}
        log.info("refresh: external projection sources loaded: %s", counts)
    else:
        log.info("refresh: no external projections available — using ESPN only")

    state = build_state_mod.build_state(
        league_raw=snap.league,
        free_agents_raw=snap.free_agents,
        captured_at=snap.captured_at,
        news_raw=news_raw,
        reddit_posts=reddit_posts,
        twitter_posts=twitter_posts,
        bluesky_posts=bluesky_posts,
        ai_summaries=ai_summaries,
        ext_projections_by_source=ext_projections_by_source or None,
    )

    state_path = build_state_mod.write_state(state, docs_root)
    new_tx = build_state_mod.append_transactions_history(state, data_root / "history")

    # Concise summary for humans + the skill flow.
    ext_sources = list(ext_projections_by_source.keys()) if ext_projections_by_source else []
    proj_sources = ["espn-proj", "espn-2w-rolling"] + ext_sources
    print()
    print(f"  league:          {state.meta.league_name}")
    print(f"  season:          {state.meta.season_id}")
    print(f"  scoring period:  {state.meta.scoring_period_id} "
          f"(matchup period {state.meta.matchup_period_id})")
    print(f"  teams:           {len(state.teams)}")
    print(f"  transactions:    {len(state.transactions_recent)} recent / {new_tx} new appended")
    print(f"  waiver targets:  {len(state.waiver_targets_overall)} overall")
    print(f"  projection srcs: {', '.join(proj_sources)}")
    print(f"  reddit posts:    {sum(len(v) for v in state.reddit_posts_by_player.values())} matched")
    print(f"  twitter posts:   {sum(len(v) for v in state.twitter_posts_by_player.values())} matched")
    print(f"  bluesky posts:   {sum(len(v) for v in state.bluesky_posts_by_player.values())} matched")
    print(f"  snapshot:        {snap.out_dir.relative_to(root)}")
    print(f"  state file:      {state_path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
