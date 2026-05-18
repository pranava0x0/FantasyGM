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
from pipeline import ingest, news
from pipeline.espn_client import (
    ESPNAPIError,
    ESPNAuthError,
    ESPNClient,
    ESPNCredentials,
)


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

    # News is fetched outside the ESPN client (public endpoint, no auth).
    log.info("refresh: fetching WNBA news feed")
    news_raw = news.fetch_news(limit=50)
    # Stash a copy with the daily snapshot so we can replay analysis
    # without re-fetching.
    (snap.out_dir / "news.json").write_text(json.dumps(news_raw, indent=2) + "\n")

    state = build_state_mod.build_state(
        league_raw=snap.league,
        free_agents_raw=snap.free_agents,
        captured_at=snap.captured_at,
        news_raw=news_raw,
    )

    state_path = build_state_mod.write_state(state, docs_root)
    new_tx = build_state_mod.append_transactions_history(state, data_root / "history")

    # Concise summary for humans + the skill flow.
    print()
    print(f"  league:          {state.meta.league_name}")
    print(f"  season:          {state.meta.season_id}")
    print(f"  scoring period:  {state.meta.scoring_period_id} "
          f"(matchup period {state.meta.matchup_period_id})")
    print(f"  teams:           {len(state.teams)}")
    print(f"  transactions:    {len(state.transactions_recent)} recent / {new_tx} new appended")
    print(f"  waiver targets:  {len(state.waiver_targets_overall)} overall")
    print(f"  snapshot:        {snap.out_dir.relative_to(root)}")
    print(f"  state file:      {state_path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
