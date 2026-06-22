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
from pipeline import bluesky as bluesky_mod, instagram as instagram_mod, ingest, news, reddit as reddit_mod, twitter as twitter_mod
from pipeline.espn_client import (
    ESPNAPIError,
    ESPNAuthError,
    ESPNClient,
    ESPNCredentials,
)
from pipeline.ai_summary import generate_summaries
from pipeline.game_log import append_game_logs, backfill_from_snapshots, load_game_log
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
    parser.add_argument("--social-only", action="store_true",
                        help="Refresh social media + public stats without ESPN API auth (uses latest snapshot as baseline).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("refresh")

    root = Path(args.root).resolve()
    _load_dotenv(root)

    import os
    creds = None
    try:
        creds = ESPNCredentials.from_env()
    except ESPNAuthError as e:
        if not args.social_only:
            log.error(str(e))
            return 2
        log.warning("ESPN credentials missing; running in social-only mode: %s", e)

    league_id = int(os.environ.get("ESPN_LEAGUE_ID", "2043154241"))
    season = int(os.environ.get("ESPN_SEASON", "2026"))
    log.info("league_id=%d season=%d dry_run=%s social_only=%s", league_id, season, args.dry_run, args.social_only)

    data_root = root / args.data
    docs_root = root / args.docs

    # Full ESPN refresh if credentials available
    if creds:
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
    # Social-only mode: load latest snapshot from disk
    else:
        if args.dry_run:
            log.error("--dry-run requires ESPN credentials")
            return 2
        log.info("social-only mode: loading latest snapshot from disk")
        from pipeline import ingest as ingest_mod
        snap = ingest_mod.load_latest_snapshot(data_root)
        if snap is None:
            log.error("no previous snapshot found in data/raw/ — cannot proceed in social-only mode")
            return 3
        log.info("loaded snapshot from %s", snap.out_dir)

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

    log.info("refresh: loading Instagram posts")
    instagram_posts = instagram_mod.load_instagram(raw_dir=snap.out_dir)
    log.info("refresh: %d Instagram posts loaded", len(instagram_posts))

    player_socials_path = data_root / "player_socials.json"
    player_socials_raw: dict[str, dict[str, str]] = {}
    if player_socials_path.exists():
        try:
            raw_soc = json.loads(player_socials_path.read_text())
            player_socials_raw = raw_soc.get("players") or {}
            log.info("refresh: loaded %d player social profiles", len(player_socials_raw))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("refresh: failed to read player_socials.json (%s)", e)

    # Game log: accumulate per-game actual scores before building state.
    # On first run (no log file yet) this auto-backfills from all existing
    # raw snapshots so history starts from day one of the season.
    history_root = data_root / "history"
    game_log_path = history_root / "game_logs.jsonl"
    if not game_log_path.exists():
        log.info("refresh: game_logs.jsonl absent — backfilling from existing snapshots")
        backfilled = backfill_from_snapshots(data_root / "raw", history_root)
        log.info("refresh: backfill complete (%d entries)", backfilled)
    else:
        new_games = append_game_logs(snap.free_agents, snap.league, history_root)
        log.info("refresh: game_log appended %d new entries", new_games)

    season_id = int(snap.league.get("seasonId") or 0)
    player_game_log = load_game_log(history_root, season_id=season_id)
    log.info(
        "refresh: game_log loaded %d players (season %d)",
        len(player_game_log), season_id,
    )

    # External projections (CBS, Yahoo) — cached per-day to snap.out_dir.
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

    # Build a preliminary ranked FA list (no AI summaries yet) so the AI
    # prompt can include accurate blended projections and full game history.
    from pipeline import analyze, schedule as schedule_mod
    from pipeline.projections_ext import resolve_external_projections
    _player_name_idx = {
        int((e.get("player") or {}).get("id") or 0): str((e.get("player") or {}).get("fullName") or "")
        for e in (snap.free_agents.get("players") or [])
    }
    _ext_by_player = resolve_external_projections(_player_name_idx, ext_projections_by_source) if ext_projections_by_source else {}
    _current_period = int(snap.league.get("scoringPeriodId") or 0)
    _week_start, _week_end = schedule_mod.upcoming_week_periods(snap.league)
    _games_map = schedule_mod.games_per_team(snap.league, _week_start, _week_end)
    _rolling_window = schedule_mod.games_per_team(snap.league, max(1, _current_period - 14), _current_period)
    ranked_fas_for_ai = analyze.rank_free_agents(
        snap.free_agents,
        scoring_period_id=_current_period,
        limit=30,
        games_by_pro_team=_games_map,
        games_in_rolling_window_by_team=_rolling_window,
        ext_projections_by_player=_ext_by_player,
        player_game_log=player_game_log,
    )

    # AI "GM take" summaries — auto-generated for top N targets, cached and
    # invalidated only when a player's score changes meaningfully (±5 pts).
    ai_path = data_root / "ai_summaries.json"
    log.info("refresh: generating AI summaries for top waiver targets")
    ai_summaries = generate_summaries(
        ranked_fas_for_ai,
        summaries_path=ai_path,
        league_name=(snap.league.get("settings") or {}).get("name") or "the league",
        dry_run=args.dry_run,
    )
    log.info("refresh: %d AI summaries available", len(ai_summaries))
    summary_dates: dict[str, str] = {}
    try:
        if ai_path.exists():
            summary_dates = json.loads(ai_path.read_text()).get("summary_dates") or {}
    except (json.JSONDecodeError, OSError):
        pass

    state = build_state_mod.build_state(
        league_raw=snap.league,
        free_agents_raw=snap.free_agents,
        captured_at=snap.captured_at,
        news_raw=news_raw,
        reddit_posts=reddit_posts,
        twitter_posts=twitter_posts,
        bluesky_posts=bluesky_posts,
        instagram_posts=instagram_posts,
        player_socials_raw=player_socials_raw,
        ai_summaries=ai_summaries,
        summary_dates=summary_dates,
        ext_projections_by_source=ext_projections_by_source or None,
        player_game_log=player_game_log,
    )

    state_path = build_state_mod.write_state(state, docs_root)
    new_tx = build_state_mod.append_transactions_history(state, data_root / "history")

    # Concise summary for humans + the skill flow.
    ext_sources = list(ext_projections_by_source.keys()) if ext_projections_by_source else []
    proj_sources = ["espn-proj", "espn-2w-rolling"] + ext_sources
    total_game_log_entries = sum(len(v) for v in player_game_log.values())
    print()
    print(f"  league:          {state.meta.league_name}")
    print(f"  season:          {state.meta.season_id}")
    print(f"  scoring period:  {state.meta.scoring_period_id} "
          f"(matchup period {state.meta.matchup_period_id})")
    print(f"  teams:           {len(state.teams)}")
    print(f"  transactions:    {len(state.transactions_recent)} recent / {new_tx} new appended")
    print(f"  waiver targets:  {len(state.waiver_targets_overall)} overall")
    print(f"  game log:        {total_game_log_entries} entries / {len(player_game_log)} players")
    print(f"  projection srcs: {', '.join(proj_sources)}")
    print(f"  reddit posts:    {sum(len(v) for v in state.reddit_posts_by_player.values())} matched")
    print(f"  twitter posts:   {sum(len(v) for v in state.twitter_posts_by_player.values())} matched")
    print(f"  bluesky posts:   {sum(len(v) for v in state.bluesky_posts_by_player.values())} matched")
    print(f"  snapshot:        {snap.out_dir.relative_to(root)}")
    print(f"  state file:      {state_path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
