"""Fetch ESPN snapshots and dump them to `data/raw/<date>/*.json`.

Idempotent: re-running on the same calendar day overwrites that day's
snapshots. Each file maps to one ESPN API "view" so we can re-process
without re-fetching.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.espn_client import ESPNClient

log = logging.getLogger(__name__)

# Views we always pull. mTransactions2 gives the full transaction log.
LEAGUE_VIEWS = [
    "mSettings",
    "mTeam",
    "mRoster",
    "mStandings",
    "mTransactions2",
    "mSchedule",
    "mNav",
    "mStatus",
]


@dataclass
class Snapshot:
    """A single day's pull from ESPN."""
    captured_at: datetime
    out_dir: Path
    league: dict[str, Any]
    free_agents: dict[str, Any]


def snapshot(client: ESPNClient, root: Path, *, today: datetime | None = None) -> Snapshot:
    """Run a full fetch and write raw JSON files under `root/raw/<date>/`.

    PII is stripped before writing — `members[]`, `owners[]`, `primaryOwner`,
    and `memberId` are dropped so committed snapshots can't leak owner identity.

    `today` is injectable for tests.
    """
    captured_at = (today or datetime.now(timezone.utc)).replace(microsecond=0)
    out_dir = root / "raw" / captured_at.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("ingest: fetching league views=%s", LEAGUE_VIEWS)
    league = client.fetch_league(views=LEAGUE_VIEWS)
    league = _redact_owners(league)
    _dump(out_dir / "league.json", league)

    scoring_period = int(league.get("scoringPeriodId") or 0)
    log.info("ingest: fetching free agents for scoringPeriod=%d", scoring_period)
    free_agents = client.fetch_free_agents(scoring_period_id=scoring_period, limit=200)
    _dump(out_dir / "free_agents.json", free_agents)

    # Capture a tiny metadata file so consumers know provenance + when.
    meta = {
        "league_id": client.league_id,
        "season": client.season,
        "scoring_period_id": scoring_period,
        "captured_at": captured_at.isoformat(),
        "views": LEAGUE_VIEWS,
    }
    _dump(out_dir / "_meta.json", meta)

    log.info("ingest: wrote %s", out_dir)
    return Snapshot(captured_at=captured_at, out_dir=out_dir, league=league, free_agents=free_agents)


def _dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Compact-ish: indent=2 for readability + git diff sanity.
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


# Fields that identify a league member (a real person). Stripped at ingest so
# raw snapshots committed to the repo never carry them. Keeping a denylist here
# (not on the team objects directly) makes the policy obvious in one place.
_OWNER_FIELDS: tuple[str, ...] = (
    "owners",
    "primaryOwner",
    "memberId",
    "displayName",
    "firstName",
    "lastName",
    "userId",
    "isActingAsTeamOwner",
    "isLeagueManager",
)


def _redact_owners(league: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with all owner-identifying fields removed.

    Idempotent. Operates recursively on dict values + lists of dicts.
    """
    def clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items() if k not in _OWNER_FIELDS}
        if isinstance(obj, list):
            return [clean(x) for x in obj]
        return obj

    cleaned = clean(league)
    # `members` is the league-wide roster of owners; replace with id-only stubs
    # so anything keyed off member ID still works.
    if "members" in cleaned:
        cleaned["members"] = [
            {"id": m.get("id")}
            for m in (cleaned["members"] or [])
            if isinstance(m, dict)
        ]
    return cleaned
