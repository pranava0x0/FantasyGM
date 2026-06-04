"""Append-only per-game actual fantasy score log.

Follows the same pattern as data/history/transactions.jsonl.

Each line is one game a player actually played:
  {"player_id": 12345, "player_name": "...", "scoring_period_id": 21,
   "season_id": 2026, "fantasy_points": 30.0, "captured_at": "2026-06-01"}

Dedup key: (player_id, scoring_period_id, season_id).
A game score never changes once played — only new (key, value) pairs are
appended; re-running is safe.

Why this exists
---------------
ESPN's API returns per-game stat blocks via `filterStatsForTopScoringPeriodIds`
(currently value=14). As the season grows past ~14 games per player, early-
season entries fall off the API window permanently. Without this log:
  - Rolling averages degrade (small/biased sample)
  - The "Last 2 weeks" modal row loses data
  - No full-season performance record exists

With this log, each refresh appends the new games it sees; old entries are
preserved. The full history is loaded back into the analysis pipeline so
rolling averages and game displays draw from the complete accumulated record,
not just the current snapshot window.

Performance note (CLAUDE.md guideline):
  At ~500 entries/refresh × 3 refreshes/week × 36-week season ≈ 54k lines,
  ~3 MB. load_game_log() reads and parses this in < 0.3s. Re-evaluate if
  mid-season performance degrades.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

GAME_LOG_FILE = "game_logs.jsonl"

# ESPN stat block constants (mirrored from analyze.py to avoid circular import)
_ACTUAL_SOURCE = 0
_PER_GAME_SPLIT = 5  # statSplitTypeId=5 → individual game log entry


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------

def append_game_logs(
    free_agents_raw: dict[str, Any],
    league_raw: dict[str, Any],
    history_root: Path,
    *,
    captured_at: str | None = None,
) -> int:
    """Extract per-game actual blocks from snapshot and append new entries.

    Returns the count of newly appended entries.
    `captured_at` defaults to today (UTC). Pass an ISO date string (YYYY-MM-DD)
    to override — used by backfill so historical entries carry the snapshot date.

    Each stat block carries its own `seasonId` field — we use that rather than
    the league-level seasonId so that prior-season game blocks ESPN sometimes
    includes in the response are stored under their correct season and are
    naturally excluded when callers pass a `season_id` filter to load_game_log.
    """
    if captured_at is None:
        captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    log_path = history_root / GAME_LOG_FILE

    # Load seen keys so we never write a duplicate entry.
    seen: set[tuple[int, int, int]] = _load_seen_keys(log_path)

    new_entries: list[dict[str, Any]] = []
    for player in _iter_all_players(free_agents_raw, league_raw):
        pid = player.get("id")
        name = str(player.get("fullName") or "")
        if pid is None:
            continue
        pid = int(pid)

        for s in player.get("stats") or []:
            if s.get("statSourceId") != _ACTUAL_SOURCE:
                continue
            if s.get("statSplitTypeId") != _PER_GAME_SPLIT:
                continue
            period = int(s.get("scoringPeriodId") or 0)
            total = float(s.get("appliedTotal") or 0.0)
            # Use the stat block's own seasonId — ESPN sometimes includes
            # prior-season blocks in the same response.
            block_season = int(s.get("seasonId") or 0)
            if total <= 0.0 or period <= 0 or block_season <= 0:
                continue

            key = (pid, period, block_season)
            if key in seen:
                continue
            seen.add(key)

            new_entries.append({
                "player_id": pid,
                "player_name": name,
                "scoring_period_id": period,
                "season_id": block_season,
                "fantasy_points": total,
                "captured_at": captured_at,
            })

    if not new_entries:
        return 0

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as fp:
        for entry in new_entries:
            fp.write(json.dumps(entry, ensure_ascii=False) + "\n")

    log.info("game_log: appended %d new game entries to %s", len(new_entries), log_path)
    return len(new_entries)


def backfill_from_snapshots(raw_root: Path, history_root: Path) -> int:
    """Process all existing raw snapshots and append any game log entries not
    already present. Idempotent — safe to run multiple times.

    Returns total entries appended across all snapshots.
    """
    total = 0
    dirs = sorted(d for d in raw_root.iterdir() if d.is_dir())
    if not dirs:
        log.warning("game_log: no snapshot directories found under %s", raw_root)
        return 0

    log.info("game_log: backfilling from %d snapshot(s) in %s", len(dirs), raw_root)
    for date_dir in dirs:
        fa_path = date_dir / "free_agents.json"
        league_path = date_dir / "league.json"
        if not fa_path.exists() or not league_path.exists():
            log.debug("game_log: skipping %s (missing free_agents or league)", date_dir.name)
            continue

        try:
            fa_raw = json.loads(fa_path.read_text())
            league_raw = json.loads(league_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("game_log: could not load %s: %s", date_dir.name, exc)
            continue

        appended = append_game_logs(
            fa_raw, league_raw, history_root,
            captured_at=date_dir.name,  # use snapshot date
        )
        log.info("game_log: backfill %s → %d new entries", date_dir.name, appended)
        total += appended

    log.info("game_log: backfill complete — %d total new entries", total)
    return total


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

def load_game_log(
    history_root: Path,
    *,
    season_id: int | None = None,
) -> dict[int, list[dict[str, Any]]]:
    """Return {player_id: [{scoring_period_id, fantasy_points}, ...]} sorted by period asc.

    Pass `season_id` to filter to the current season only (recommended —
    keeps memory use bounded and avoids prior-season scores polluting
    rolling averages).
    """
    log_path = history_root / GAME_LOG_FILE
    if not log_path.exists():
        return {}

    result: dict[int, list[dict[str, Any]]] = {}
    seen: set[tuple[int, int]] = set()  # (player_id, period) within season

    for raw_line in log_path.read_text().splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        if season_id is not None and int(entry.get("season_id") or 0) != season_id:
            continue

        pid = entry.get("player_id")
        period = entry.get("scoring_period_id")
        fpts = entry.get("fantasy_points")
        if pid is None or period is None or fpts is None:
            continue

        pid, period = int(pid), int(period)
        dedup = (pid, period)
        if dedup in seen:
            continue
        seen.add(dedup)

        result.setdefault(pid, []).append({
            "scoring_period_id": period,
            "fantasy_points": float(fpts),
        })

    # Sort each player's list ascending so callers can slice [-N:] for recency.
    for games in result.values():
        games.sort(key=lambda g: g["scoring_period_id"])

    log.debug(
        "game_log: loaded %d players (%d total game entries)",
        len(result),
        sum(len(v) for v in result.values()),
    )
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_seen_keys(log_path: Path) -> set[tuple[int, int, int]]:
    """Read existing log and return the set of (player_id, period, season_id) keys."""
    seen: set[tuple[int, int, int]] = set()
    if not log_path.exists():
        return seen
    for raw_line in log_path.read_text().splitlines():
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
            seen.add((
                int(entry["player_id"]),
                int(entry["scoring_period_id"]),
                int(entry["season_id"]),
            ))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return seen


def _iter_all_players(
    free_agents_raw: dict[str, Any],
    league_raw: dict[str, Any],
) -> list[dict[str, Any]]:
    """Yield player dicts from both the FA pool and rostered players."""
    players: list[dict[str, Any]] = []

    # Free agents / waivers
    for entry in free_agents_raw.get("players") or []:
        p = entry.get("player") or {}
        if p:
            players.append(p)

    # Rostered players (nested deeper)
    for team in league_raw.get("teams") or []:
        for entry in (team.get("roster") or {}).get("entries") or []:
            pool = entry.get("playerPoolEntry") or {}
            p = pool.get("player") or {}
            if p:
                players.append(p)

    return players
