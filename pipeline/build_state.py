"""Compose `docs/data/state.json` and append to `data/history/transactions.jsonl`.

`state.json` is the single source of truth the frontend reads. Each refresh
rebuilds it from raw snapshots — never mutated in place.

Transactions are also appended (deduped on `transaction_id`) to
`data/history/transactions.jsonl` so we keep a complete history even if
ESPN's transaction window rolls off old entries.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline import analyze, schema

log = logging.getLogger(__name__)


def build_state(
    *,
    league_raw: dict[str, Any],
    free_agents_raw: dict[str, Any],
    captured_at: datetime,
) -> schema.LeagueState:
    """Compose the LeagueState from raw responses + analysis."""
    teams_view = analyze.build_team_views(league_raw)
    weakness = analyze.compute_team_weakness(teams_view)
    ranked_fas_dicts = analyze.rank_free_agents(
        free_agents_raw,
        scoring_period_id=int(league_raw.get("scoringPeriodId") or 0),
        limit=25,
    )

    # Player id -> name, for transaction items.
    player_name_index = _player_name_index(league_raw, free_agents_raw)

    teams: list[schema.TeamState] = []
    by_team_targets: list[schema.WaiverTargetsByTeam] = []
    for t in teams_view:
        w = weakness[t["team_id"]]
        roster_entries = [
            schema.RosterEntry(
                player=schema.PlayerRef(
                    player_id=int(p["player_id"]),
                    name=str(p["name"] or "Unknown"),
                    team=_pro_team_abbr(p["pro_team_id"]),
                    position=p["position"],
                    bucket=p["bucket"],
                    eligible_slots=p["eligible_slots"],
                    injury_status=p["injury_status"],
                ),
                lineup_slot_id=int(p["lineup_slot_id"]),
                lineup_slot_label=p["lineup_slot_label"],
                is_active=bool(p["is_active"]),
                projected_points=float(p["projected_points"] or 0.0),
                actual_points=p["actual_points"],
            )
            for p in t["roster"]
            if p["player_id"] is not None
        ]

        team_state = schema.TeamState(
            team_id=int(t["team_id"]),
            abbrev=str(t["abbrev"] or f"T{t['team_id']}"),
            name=str(t["name"]),
            logo_url=t["logo"],
            division_id=t["division_id"],
            record=schema.TeamRecord(**t["record"]),
            waiver_position=t["waiver_position"],
            faab_remaining=t["faab_remaining"],
            roster=roster_entries,
            weakness=schema.TeamWeakness(
                guard_proj=w["guard_proj"],
                forward_proj=w["forward_proj"],
                center_proj=w["center_proj"],
                frontcourt_proj=w["frontcourt_proj"],
                guard_gap_vs_league=w["guard_gap_vs_league"],
                forward_gap_vs_league=w["forward_gap_vs_league"],
                center_gap_vs_league=w["center_gap_vs_league"],
                frontcourt_gap_vs_league=w["frontcourt_gap_vs_league"],
                weakest_bucket=w["weakest_bucket"],
            ),
        )
        teams.append(team_state)

        team_targets = analyze.waiver_targets_for_team(w, ranked_fas_dicts, limit=10)
        by_team_targets.append(
            schema.WaiverTargetsByTeam(
                team_id=int(t["team_id"]),
                targets=[_to_waiver_target(d) for d in team_targets],
            )
        )

    overall_targets = [_to_waiver_target(d) for d in ranked_fas_dicts[:15]]

    transactions_raw = analyze.normalize_transactions(league_raw)
    transactions_view: list[schema.Transaction] = []
    for tx in transactions_raw[:50]:
        items = [
            schema.TransactionItem(
                player_id=int(it["player_id"]),
                player_name=player_name_index.get(it["player_id"]),
                from_team_id=it["from_team_id"],
                to_team_id=it["to_team_id"],
                from_slot_id=it["from_slot_id"],
                to_slot_id=it["to_slot_id"],
                type=it["type"],
            )
            for it in tx["items"]
            if it["player_id"] is not None
        ]
        transactions_view.append(schema.Transaction(
            transaction_id=str(tx["transaction_id"]),
            occurred_at=tx["occurred_at"] or captured_at,
            scoring_period_id=tx["scoring_period_id"],
            team_id=tx["team_id"],
            type=tx["type"],
            bid_amount=tx["bid_amount"],
            status=tx["status"],
            items=items,
        ))

    meta = schema.LeagueMeta(
        league_id=int(league_raw.get("id") or 0),
        league_name=str((league_raw.get("settings") or {}).get("name") or "Unknown League"),
        season_id=int(league_raw.get("seasonId") or 0),
        scoring_period_id=int(league_raw.get("scoringPeriodId") or 0),
        matchup_period_id=int((league_raw.get("status") or {}).get("currentMatchupPeriod") or 0),
        scoring_type=str(((league_raw.get("settings") or {}).get("scoringSettings") or {}).get("scoringType") or "UNKNOWN"),
        team_count=len(teams),
        captured_at=captured_at,
    )

    return schema.LeagueState(
        meta=meta,
        teams=teams,
        transactions_recent=transactions_view,
        waiver_targets_overall=overall_targets,
        waiver_targets_by_team=by_team_targets,
    )


def write_state(state: schema.LeagueState, docs_root: Path) -> Path:
    """Write `docs/data/state.json`. Idempotent."""
    out = docs_root / "data" / "state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    body = state.model_dump_json(indent=2)
    out.write_text(body + "\n")
    log.info("build_state: wrote %s (%d bytes)", out, len(body))
    return out


def append_transactions_history(
    state: schema.LeagueState,
    history_root: Path,
) -> int:
    """Append any new transactions to `data/history/transactions.jsonl`.

    Returns count of new entries appended.
    """
    history_root.mkdir(parents=True, exist_ok=True)
    log_path = history_root / "transactions.jsonl"
    seen: set[str] = set()
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                seen.add(json.loads(line)["transaction_id"])
            except (json.JSONDecodeError, KeyError):
                continue

    appended = 0
    with log_path.open("a") as fp:
        for tx in state.transactions_recent:
            if tx.transaction_id in seen:
                continue
            fp.write(tx.model_dump_json() + "\n")
            seen.add(tx.transaction_id)
            appended += 1
    log.info("build_state: appended %d new transactions to %s", appended, log_path)
    return appended


# --- helpers --------------------------------------------------------------

def _player_name_index(
    league_raw: dict[str, Any],
    free_agents_raw: dict[str, Any],
) -> dict[int, str]:
    idx: dict[int, str] = {}
    for t in league_raw.get("teams") or []:
        for e in (t.get("roster") or {}).get("entries") or []:
            pool = e.get("playerPoolEntry") or {}
            p = pool.get("player") or {}
            if p.get("id") is not None and p.get("fullName"):
                idx[int(p["id"])] = str(p["fullName"])
    for entry in free_agents_raw.get("players") or []:
        p = entry.get("player") or {}
        if p.get("id") is not None and p.get("fullName") and int(p["id"]) not in idx:
            idx[int(p["id"])] = str(p["fullName"])
    return idx


def _to_waiver_target(d: dict[str, Any]) -> schema.WaiverTarget:
    return schema.WaiverTarget(
        player=schema.PlayerRef(
            player_id=int(d["player_id"]),
            name=str(d["name"] or "Unknown"),
            team=_pro_team_abbr(d["pro_team_id"]),
            position=d["position"],
            bucket=d["bucket"],
            eligible_slots=d["eligible_slots"],
            injury_status=d["injury_status"],
        ),
        projected_points_next_period=float(d["projected_points_next_period"]),
        season_avg_points=d["season_avg_points"],
        percent_owned=d["percent_owned"],
        percent_change=d["percent_change"],
        base_score=float(d["base_score"]),
    )


# WNBA pro team ID -> abbreviation. CANONICAL map, pulled from ESPN's
# public site API on 2026-05-17:
#
#   curl https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams
#
# To refresh: `python scripts/refresh_pro_teams.py`.
#
# Earlier versions of this file had a guess-based map adapted from NBA
# conventions; almost every ID was wrong (3 was DAL not ATL, 6 was LA not
# CONN, 19 was CHI not WSH, etc.). The lesson: do not infer WNBA team IDs
# from NBA habits; ESPN assigns them independently and the league's
# expansion teams (TOR / GS / POR) have 6-digit IDs. See CLAUDE.md scar
# tissue.
#
# ID 0 means "no team" (free-agent / unsigned) — intentionally absent.
WNBA_TEAM_ABBR: dict[int, str] = {
    3: "DAL",       # Dallas Wings
    5: "IND",       # Indiana Fever
    6: "LA",        # Los Angeles Sparks
    8: "MIN",       # Minnesota Lynx
    9: "NY",        # New York Liberty
    11: "PHX",      # Phoenix Mercury
    14: "SEA",      # Seattle Storm
    16: "WSH",      # Washington Mystics
    17: "LV",       # Las Vegas Aces
    18: "CON",      # Connecticut Sun
    19: "CHI",      # Chicago Sky
    20: "ATL",      # Atlanta Dream
    129689: "GS",   # Golden State Valkyries (2026 expansion)
    131935: "TOR",  # Toronto Tempo (2026 expansion)
    132052: "POR",  # Portland Fire (2026 expansion)
}


def _pro_team_abbr(pro_team_id: int | None) -> str | None:
    if pro_team_id is None:
        return None
    return WNBA_TEAM_ABBR.get(int(pro_team_id))
