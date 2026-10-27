"""Manually refresh NFL/NBA rules and prepare local computer-use tasks.

    .venv/bin/python -m pipeline.multileague --week 8 --sleeper-user USERNAME

Runs once; never schedules work or changes a lineup. Outputs stay local in
scratch/lineups. Sleeper's projection feed is undocumented and its scoring
coverage is reported explicitly rather than silently assuming generic PPR.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from pipeline.espn_client import ESPNClient, ESPNCredentials
from pipeline.ingest import SLEEPER_RULE_KEYS, _redact_owners
from pipeline.refresh import _load_dotenv

log = logging.getLogger(__name__)
SLEEPER_LEAGUES = ("1389723841625849857", "1389752644595089408")
NBA_LEAGUE = 221146430
NBA_TEAM = 2
NBA_SEASON = 2027
NBA_SLOTS = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F", 11: "UTIL"}
NFL_FLEX = {"FLEX": {"RB", "WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
            "REC_FLEX": {"WR", "TE"}, "WRRB_FLEX": {"WR", "RB"}}
UNAVAILABLE = {"OUT", "IR", "INJURED_RESERVE", "SUSPENDED", "PUP", "DNR"}
NBA_POINTS = {"0": 1, "6": 1, "3": 2, "2": 4, "1": 4, "11": -2,
              "13": 2, "14": -1, "15": 1, "16": -1, "17": 1}


def exact_assignment(players: list[dict[str, Any]], slots: list[str]) -> dict[int, str]:
    """Maximize additive projected value across slot instances in O(N S 2^S).

    Each player has id, eligible slots, current slot index, locked, available,
    and value. Locked bench players stay benched, and locked starters consume
    their precise slot instance. Ties favor keeping the current assignments.
    Missing/zero projections never manufacture a recommendation.
    """
    if len(slots) > 16:
        raise ValueError("Too many starting slots for the exact solver")
    if len({p["id"] for p in players}) != len(players):
        raise ValueError("Duplicate player IDs")
    pinned: dict[int, str] = {}
    mask = 0
    for p in players:
        index = p.get("current_slot")
        if p.get("locked") and index is not None:
            if not 0 <= index < len(slots) or mask & (1 << index):
                raise ValueError("Invalid or duplicate locked starting slot")
            pinned[index] = str(p["id"])
            mask |= 1 << index
    # Score tuple: projected value first; minimum slot churn second.
    states: dict[int, tuple[float, int, dict[int, str]]] = {mask: (0.0, 0, pinned)}
    for p in players:
        if p.get("locked") or not p.get("available", True) or p.get("value") is None:
            continue
        value = float(p["value"])
        if value <= 0:
            continue
        updated = dict(states)
        for used, (total, kept, assignment) in states.items():
            for index, slot in enumerate(slots):
                if used & (1 << index) or slot not in p["eligible"]:
                    continue
                candidate = (total + value, kept + int(p.get("current_slot") == index),
                             {**assignment, index: str(p["id"])})
                new_mask = used | (1 << index)
                if new_mask not in updated or candidate[:2] > updated[new_mask][:2]:
                    updated[new_mask] = candidate
        states = updated
    return max(states.values(), key=lambda item: item[:2])[2]


def score_projection(stats: dict[str, float], scoring: dict[str, float]) -> float:
    """Apply league weights to available projected stat components."""
    return sum(float(stats.get(key) or 0) * float(weight) for key, weight in scoring.items())


def injury_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare injury tags by league and player, without mixing platform IDs."""
    old = {(league["key"], p["id"]): p.get("injury_status")
           for league in previous.get("leagues", []) for p in league.get("players", [])}
    return [{"league": league["key"], "player": p["name"],
             "before": old[(league["key"], p["id"])], "after": p.get("injury_status")}
            for league in current.get("leagues", []) for p in league.get("players", [])
            if (league["key"], p["id"]) in old
            and old[(league["key"], p["id"])] != p.get("injury_status")]


def _get(client: httpx.Client, path: str) -> Any:
    response = client.get("https://api.sleeper.app/v1/" + path)
    response.raise_for_status()
    return response.json()


def refresh_sleeper(client: httpx.Client, league_id: str, week: int | None,
                    user_id: str | None, *, roster_id: int | None = None) -> dict[str, Any]:
    """Read settings/rosters; retain roster IDs, never owner IDs or names."""
    league = _get(client, f"league/{league_id}")
    if league.get("sport") != "nfl":
        raise ValueError("Expected a Sleeper NFL league")
    target_week = week or int((league.get("settings") or {})["leg"])
    rosters = _get(client, f"league/{league_id}/rosters")
    mine = next((r for r in rosters if (r.get("owner_id") == user_id if user_id
                                      else roster_id is not None and r["roster_id"] == roster_id)), None)
    slots = [s for s in league["roster_positions"] if s not in {"BN", "IR"}]
    scoring = {k: v for k, v in league["scoring_settings"].items() if v}
    result: dict[str, Any] = {
        "key": f"sleeper:{league_id}", "label": f"Sleeper league {league_id}", "sport": "nfl",
        "season": league["season"], "week": target_week,
        "url": f"https://sleeper.com/leagues/{league_id}/team",
        "rules": {"slots": slots, "scoring": scoring,
                  "settings": {k: v for k, v in league["settings"].items() if k in SLEEPER_RULE_KEYS}},
        "roster_id": mine["roster_id"] if mine else None,
        "rosters": [{"roster_id": r["roster_id"], "players": r.get("players") or [],
                     "starters": r.get("starters") or [], "reserve": r.get("reserve") or [],
                     "taxi": r.get("taxi") or []} for r in rosters],
        "players": [], "status": "needs_roster_identity", "warnings": [],
    }
    if not mine:
        result["warnings"].append("Supply --sleeper-user to identify your roster; no team was guessed.")
        return result
    response = client.get(f"https://api.sleeper.com/projections/nfl/{league['season']}/{target_week}",
                          params={"season_type": "regular"})
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("Unexpected Sleeper projection response")
    projected = {str(r["player_id"]): r for r in rows if r.get("stats", {}).get("gp")}
    covered = {k for r in rows for k in r.get("stats", {})}
    result["projection_source"] = str(response.url)
    result["unprojected_scoring_components"] = sorted(set(scoring) - covered)
    # Player directory supplies injuries even for players absent from projections.
    directory = _get(client, "players/nfl")
    for pid in mine.get("players") or []:
        p = directory.get(pid) or {}
        row = projected.get(pid)
        positions = set(p.get("fantasy_positions") or [p.get("position")])
        current = next((i for i, starter in enumerate(mine.get("starters") or []) if starter == pid), None)
        status = p.get("injury_status") or "ACTIVE"
        result["players"].append({
            "id": pid, "name": p.get("full_name") or pid, "team": p.get("team"),
            "injury_status": status, "current_slot": current,
            "eligible": [slot for slot in set(slots) if positions & NFL_FLEX.get(slot, {slot})],
            "on_ir": pid in (mine.get("reserve") or []),
            "available": status.upper() not in UNAVAILABLE and pid not in (mine.get("reserve") or [])
                         and pid not in (mine.get("taxi") or []),
            "projected_value": score_projection(row["stats"], scoring) if row else None,
            "projection_stats": row["stats"] if row else None,
        })
    projected_dates = [r.get("date") for r in rows if r.get("stats", {}).get("gp")]
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    # Before the week's first game all players are movable, apart from any
    # platform-specific AutoSub state. Midweek, unknown locks freeze the plan.
    future_week = bool(projected_dates) and all(d and d > today for d in projected_dates)
    candidates = [{**p, "value": p["projected_value"], "locked": not future_week}
                  for p in result["players"]]
    assignment = exact_assignment(candidates, slots)
    by_id = {p["id"]: p for p in result["players"]}
    result["tentative_plan"] = {
        "assignments": [{"slot_index": i, "slot": slots[i], "player_id": pid,
                         "name": by_id[pid]["name"]} for i, pid in sorted(assignment.items())],
        "unfilled_slots": [slots[i] for i in range(len(slots)) if i not in assignment],
        "moves": [{"slot_index": i, "slot": slots[i], "player_in": pid,
                   "player_out": next((p["id"] for p in result["players"] if p["current_slot"] == i), None)}
                  for i, pid in sorted(assignment.items()) if by_id[pid]["current_slot"] != i],
        "all_projected_games_in_future": future_week,
    }
    if not future_week:
        result["warnings"].append("Game times are unverified: current assignments are frozen until live locks are read.")
    if result["tentative_plan"]["unfilled_slots"]:
        result["warnings"].append("Available owned players cannot fill every starting slot. Review injuries and roster constraints.")
    result["status"] = "needs_live_lock_verification"
    result["warnings"].append("Projection components are incomplete; values are estimates, not full league-scored totals.")
    result["warnings"].append("Verify kickoff and AutoSub locks in the signed-in UI before solving or applying moves.")
    return result


def refresh_nba(creds: ESPNCredentials, start: str, days: int) -> dict[str, Any]:
    """Read NBA rules and plan daily lineups using the user's points objective."""
    with ESPNClient(NBA_LEAGUE, NBA_SEASON, creds, game_code="fba") as client:
        league = client.fetch_league(["mSettings", "mTeam", "mRoster", "mMatchup", "mStatus"])
        schedule = client.fetch_pro_team_schedules()
    team = next(t for t in league["teams"] if t["id"] == NBA_TEAM)
    settings = _redact_owners(league)["settings"]
    unsupported = [slot for slot, count in settings["rosterSettings"]["lineupSlotCounts"].items()
                   if count and int(slot) not in {*NBA_SLOTS, 12, 13}]
    if unsupported:
        raise ValueError(f"Unsupported NBA starting slots: {unsupported}")
    if settings["rosterSettings"].get("lineupSlotStatLimits"):
        raise ValueError("NBA game/stat caps require optimization across dates")
    first = date.fromisoformat(start)
    end = first + timedelta(days=days)
    team_games: dict[int, list[dict[str, Any]]] = {}
    for pro in schedule.get("settings", {}).get("proTeams", []):
        games: dict[int, dict[str, Any]] = {}
        for period_games in (pro.get("proGamesByScoringPeriod") or {}).values():
            for game in period_games:
                tip = datetime.fromtimestamp(game["date"] / 1000, timezone.utc)
                if first <= tip.astimezone(ZoneInfo("America/New_York")).date() < end:
                    games[game["id"]] = {"id": game["id"], "start": tip.isoformat(),
                                          "time_tbd": game.get("startTimeTBD", False),
                                          "scoring_period": game["scoringPeriodId"]}
        team_games[pro["id"]] = sorted(games.values(), key=lambda g: g["start"])
    players = []
    for entry in team.get("roster", {}).get("entries", []):
        pool = entry["playerPoolEntry"]
        p = pool["player"]
        projection = next((s for s in p.get("stats", []) if s.get("statSourceId") == 1
                           and s.get("seasonId") == NBA_SEASON and s.get("statSplitTypeId") == 0), None)
        totals = (projection or {}).get("stats") or {}
        gp = totals.get("42") or 0
        # Percentage categories must be computed from makes/attempts when aggregating.
        rates = {k: float(v) / gp for k, v in totals.items() if k in {"0", "1", "2", "3", "6", "11", "13", "14", "15", "16", "17"}} if gp else {}
        players.append({"id": str(p["id"]), "name": p["fullName"], "team": p.get("proTeamId"),
                        "injury_status": p.get("injuryStatus") or "ACTIVE",
                        "current_slot": entry["lineupSlotId"], "on_ir": entry["lineupSlotId"] == 13,
                        "eligible": [NBA_SLOTS[s] for s in p.get("eligibleSlots", []) if s in NBA_SLOTS],
                        "lineup_locked_at_capture": pool.get("lineupLocked"),
                        "games": team_games.get(p.get("proTeamId"), []),
                        "projected_stats_per_game": rates,
                        "projected_points_per_game": score_projection(rates, NBA_POINTS) if rates else None})
    slots = [NBA_SLOTS[int(slot)] for slot, count in settings["rosterSettings"]["lineupSlotCounts"].items()
             if int(slot) in NBA_SLOTS for _ in range(count)]
    daily = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        candidates = []
        for p in players:
            games = [g for g in p["games"] if datetime.fromisoformat(g["start"]).astimezone(ZoneInfo("America/New_York")).date() == day]
            candidates.append({"id": p["id"], "eligible": p["eligible"],
                               "available": not p["on_ir"] and p["injury_status"] not in UNAVAILABLE,
                               "value": p["projected_points_per_game"] if games else None})
        assignment = exact_assignment(candidates, slots)
        # Tentative date-based plan: live locks must be merged before execution.
        by_id = {p["id"]: p for p in players}
        daily.append({"date": day.isoformat(), "tentative": True,
                      "assignments": [{"slot_index": i, "slot": slots[i], "player_id": pid,
                                       "name": by_id[pid]["name"]} for i, pid in sorted(assignment.items())],
                      "projected_points": round(sum(by_id[pid]["projected_points_per_game"] for pid in assignment.values()), 1)})
    return _redact_owners({
        "key": f"espn:fba:{NBA_LEAGUE}", "label": f"ESPN league {NBA_LEAGUE} / team {NBA_TEAM}", "sport": "nba",
        "season": NBA_SEASON, "team_id": NBA_TEAM, "start_date": start, "days": days,
        "url": f"https://fantasy.espn.com/basketball/team?leagueId={NBA_LEAGUE}&teamId={NBA_TEAM}&seasonId={NBA_SEASON}",
        "rules": {"scoring": settings["scoringSettings"], "roster": settings["rosterSettings"],
                  "schedule": settings["scheduleSettings"], "acquisition": settings["acquisitionSettings"]},
        "players": players, "status": "tentative_points_plan_needs_live_lock_verification",
        "objective": "ESPN standard fantasy points (user requested proxy for this category league)",
        "objective_weights": NBA_POINTS, "daily_plan": daily,
        "warnings": ["H2H_CATEGORY: user requested total fantasy points; this proxy does not optimize category wins.",
                     "Current lock flags apply to the captured scoring period, not future dates.",
                     "IR activation requires an explicit roster decision; keep IR players excluded."],
    })


def browser_task(report: dict[str, Any]) -> str:
    """Prepare a manually invoked Codex computer-use task with explicit guards."""
    lines = ["# Check and set fantasy lineups", "",
             f"Prepared from capture {report['captured_at']}. Run manually; do not schedule.", "",
             "Refresh with the same CLI arguments before acting. Read report.json in this directory.",
             "Use cua_repl for computer use. Verify the live league, my roster ID, scoring date,",
             "rules, injuries, eligible slots and locks. Use only currently owned players.",
             "Preserve AutoSubs; stop if they lock a player or cannot be verified on the web.",
             "Do not add/drop/trade players. Do not activate IR players without a roster plan.",
             "For NFL, obtain live league-scored projections and kickoff locks before calling",
             "pipeline.multileague.exact_assignment; incomplete component estimates do not prove optimality.",
             "For NBA, maximize ESPN standard fantasy points as the user requested, despite category scoring.",
             "The daily plan is tentative; re-solve around live locks before executing each date.",
             "Prepare the complete proposed assignments before changing the UI. Apply valid moves",
             "using observed controls, re-read after every change, then reload and verify persistence.",
             "Stop on unknown controls, stale inputs, authentication, illegal rosters, or ambiguous locks.",
             "Save only a roster-table screenshot, cropped to exclude private league/team labels,",
             "owner names, handles and chat. Never save full-page league screenshots or raw DOM dumps.",
             "Identify participants only by platform, league ID and team/roster ID in reports.",
             "Report exactly which lineups were changed.", ""]
    for league in report.get("leagues", []):
        identity = f"Sleeper league {league['key'].split(':')[-1]} / roster {league.get('roster_id', '?')}" if league["sport"] == "nfl" else f"ESPN league {league['key'].split(':')[-1]} / team {league['team_id']}"
        lines.extend([f"## {identity}", "", league["url"], "",
                      f"Status: {league['status']}", ""])
        lines.extend(f"- {warning}" for warning in league.get("warnings", []))
        lines.append("")
        if league.get("tentative_plan"):
            names = {p["id"]: p["name"] for p in league["players"]}
            lines.extend([f"NFL week {league['week']} tentative moves:", ""])
            for move in league["tentative_plan"]["moves"]:
                lines.append(f"- {move['slot']}: {names[move['player_in']]} over {names.get(move['player_out'], 'empty slot')}")
            if league["tentative_plan"]["unfilled_slots"]:
                lines.append("- Unfilled with available owned players: " + ", ".join(league["tentative_plan"]["unfilled_slots"]))
            lines.append("")
        for day in league.get("daily_plan", []):
            lines.extend([f"### {day['date']}", "",
                          f"Tentative projection: {day['projected_points']:.1f} standard fantasy points.", ""])
            lines.extend(f"- {a['slot']}: {a['name']}" for a in day["assignments"])
            lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sleeper-user", help="Username used in memory to resolve your roster IDs")
    parser.add_argument("--week", type=int, choices=range(1, 19), help="NFL week; defaults to each league's current leg")
    parser.add_argument("--start-date", type=date.fromisoformat,
                        default=datetime.now(ZoneInfo("America/New_York")).date())
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--output", type=Path, default=Path("scratch/lineups"))
    args = parser.parse_args(argv)
    if not 1 <= args.days <= 14:
        parser.error("--days must be between 1 and 14")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # HTTP request logging can expose usernames in API paths.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _load_dotenv(Path(__file__).resolve().parent.parent)
    args.output.mkdir(parents=True, exist_ok=True)
    previous_path = args.output / "report.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    report: dict[str, Any] = {"captured_at": datetime.now(timezone.utc).isoformat(), "leagues": [], "errors": []}
    user_id = None
    league_ids = list(SLEEPER_LEAGUES)
    previous_rosters = {**previous.get("roster_ids", {}),
                        **{l["key"]: l["roster_id"] for l in previous.get("leagues", [])
                           if l.get("roster_id") is not None}}
    # Identity survives a failed fetch without retaining stale league data.
    # Only platform/league keys and numeric roster IDs persist here.
    report["roster_ids"] = dict(previous_rosters)
    with httpx.Client(timeout=30) as client:
        if args.sleeper_user:
            user = _get(client, f"user/{quote(args.sleeper_user, safe='')}")
            if not user or not user.get("user_id"):
                parser.error("Sleeper username could not be resolved")
            user_id = user["user_id"]
            nfl_state = _get(client, "state/nfl")
            leagues = _get(client, f"user/{user_id}/leagues/nfl/{nfl_state['season']}")
            league_ids = [l["league_id"] for l in leagues if l.get("status") == "in_season"]
        for lid in league_ids:
            try:
                refreshed = refresh_sleeper(client, lid, args.week, user_id,
                                            roster_id=previous_rosters.get(f"sleeper:{lid}"))
                report["leagues"].append(refreshed)
                if refreshed.get("roster_id") is not None:
                    report["roster_ids"][refreshed["key"]] = refreshed["roster_id"]
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                log.error("Sleeper league %s refresh failed (%s)", lid, type(exc).__name__)
                report["errors"].append({"league": f"sleeper:{lid}", "error": type(exc).__name__})
    try:
        report["leagues"].append(refresh_nba(ESPNCredentials.from_env(), args.start_date.isoformat(), args.days))
    except (RuntimeError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.error("NBA refresh failed (%s)", type(exc).__name__)
        report["errors"].append({"league": "espn:fba", "error": type(exc).__name__})
    report["injury_changes"] = injury_changes(previous, report)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (args.output / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")).write_text(text)
    previous_path.write_text(text)
    (args.output / "computer-use-task.md").write_text(browser_task(report))
    log.info("Refreshed %d leagues; %d failures; %d injury changes. Report: %s",
             len(report["leagues"]), len(report["errors"]), len(report["injury_changes"]), previous_path.resolve())
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
