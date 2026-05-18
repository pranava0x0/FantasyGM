"""Fetch ESPN snapshots and dump them to `data/raw/<date>/*.json`.

Idempotent: re-running on the same calendar day overwrites that day's
snapshots. Each file maps to one ESPN API "view" so we can re-process
without re-fetching.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.espn_client import ESPNClient

log = logging.getLogger(__name__)

# ESPN member IDs are UUIDs wrapped in curly braces — the exact shape
# as the SWID auth cookie. The user's own member ID literally IS their
# SWID value. Scrubbing any string matching this pattern is a defense
# against committing the user's own SWID to git. Discovered 2026-05-17:
# the pre-commit scanner caught members[].id values that were UUID-shaped.
_UUID_BRACE = re.compile(
    r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$"
)
_MEMBER_ID_PLACEHOLDER = "REDACTED-MEMBER-ID"

# Views we always pull. mTransactions2 gives the full transaction log.
# proTeamSchedules_wl gives per-WNBA-team schedules keyed by scoringPeriodId —
# needed for the games-this-week weighting in `analyze.rank_free_agents`.
LEAGUE_VIEWS = [
    "mSettings",
    "mTeam",
    "mRoster",
    "mStandings",
    "mTransactions2",
    "mSchedule",
    "mNav",
    "mStatus",
    "proTeamSchedules_wl",
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

    Two passes:
    1. Drop fields by name (_OWNER_FIELDS): owners[], primaryOwner, memberId,
       displayName, firstName, lastName, userId, isLeagueManager,
       isActingAsTeamOwner.
    2. Drop the `members` array entirely. Each member's `id` is a UUID in
       braces — the *same shape* as the SWID auth cookie. The user's own
       member ID literally IS their SWID value, so even an id-only stub
       leaks identifiable session material into the committed snapshot.
    3. Recursively replace any string value matching the UUID-in-braces
       pattern with a stable placeholder. Catches member IDs that travel
       in other fields (e.g. `transactions[*].source`) and any new
       member-ID surface ESPN adds in the future without us noticing.

    Idempotent.
    """
    def clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items() if k not in _OWNER_FIELDS}
        if isinstance(obj, list):
            return [clean(x) for x in obj]
        if isinstance(obj, str) and _UUID_BRACE.match(obj):
            return _MEMBER_ID_PLACEHOLDER
        return obj

    cleaned = clean(league)
    # Even after key+value scrubbing, drop members[] entirely — we never
    # consume it downstream, so the safest move is "no member list at all".
    cleaned.pop("members", None)
    return cleaned
