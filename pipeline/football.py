"""Read-only NFL collection; shared sport data and isolated league analysis."""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Literal
import urllib.request

from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
API = "https://api.sleeper.app/v1"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
LEAGUES = ("1389723841625849857", "1389752644595089408")
OUT = {"Out", "IR", "Suspended", "PUP", "Doubtful", "Commissioner exempt", "IR-R"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Player(Strict):
    id: str
    name: str
    positions: list[str]
    team: str | None = None
    status: str | None = None
    espn_id: str | None = None
    adds: int = 0
    stats: dict[str, float] = Field(default_factory=dict)
    stats_source: str | None = None
    stats_at: str | None = None

    @field_validator("stats_at")
    @classmethod
    def stats_time(cls, value: str | None) -> str | None:
        if value:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise ValueError("Stats capture needs timezone")
            return dt.isoformat()
        return value

    @field_validator("stats")
    @classmethod
    def finite_stats(cls, value: dict[str, float]) -> dict[str, float]:
        if not all(math.isfinite(v) for v in value.values()):
            raise ValueError("Stats must be finite")
        return value


class Team(Strict):
    id: str
    label: str | None = None
    players: list[str]
    starters: list[str] = Field(default_factory=list)
    reserve: list[str] = Field(default_factory=list)
    budget_used: int | None = None


class Deadline(Strict):
    title: str
    at: str
    source_url: str

    @field_validator("at")
    @classmethod
    def aware_time(cls, value: str) -> str:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Deadline needs timezone")
        return dt.isoformat()


class League(Strict):
    id: str
    provider: Literal["sleeper", "yahoo", "espn"]
    name: str
    season: str
    captured_at: str
    source_url: str
    slots: list[str]
    settings: dict[str, int] = Field(default_factory=dict)
    scoring: dict[str, float] = Field(default_factory=dict)
    teams: list[Team]
    deadlines: list[Deadline] = Field(default_factory=list)
    my_team: str | None = None
    availability_complete: bool = False
    available_players: list[str] = Field(default_factory=list)
    projections: dict[str, float] = Field(default_factory=dict)
    projection_at: str | None = None
    projection_source: str | None = None
    projection_label: str | None = None
    player_status: dict[str, str] = Field(default_factory=dict)
    bye_players: list[str] = Field(default_factory=list)
    collection: str = "public API"
    game_times: dict[str, str] = Field(default_factory=dict)
    reminders: list[str] = Field(default_factory=list)

    @field_validator("projection_at")
    @classmethod
    def projection_time(cls, value: str | None) -> str | None:
        return Deadline.aware_time(value) if value else value

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        import re
        if not re.fullmatch(r"[a-z0-9-]+", value):
            raise ValueError("League ID must use lowercase letters, digits and hyphens")
        return value

    @field_validator("projections")
    @classmethod
    def finite_projections(cls, value: dict[str, float]) -> dict[str, float]:
        return cls.finite_scoring(value)

    @field_validator("captured_at")
    @classmethod
    def capture_time(cls, value: str) -> str:
        return Deadline.aware_time(value)

    @field_validator("scoring")
    @classmethod
    def finite_scoring(cls, value: dict[str, float]) -> dict[str, float]:
        if not all(math.isfinite(v) for v in value.values()):
            raise ValueError("Scoring must be finite")
        return value


class Import(Strict):
    league: League
    players: list[Player] = Field(default_factory=list)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    temporary.replace(path)


def sanitize_rosters(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"roster_id": r["roster_id"], "players": r.get("players") or [],
             "starters": r.get("starters") or [], "reserve": r.get("reserve") or [],
             "settings": {"waiver_budget_used": (r.get("settings") or {}).get("waiver_budget_used")}}
            for r in raw]


class Cache:
    def __init__(self, root: Path, deep: bool = False) -> None:
        self.root, self.deep = root, deep
        self.requests = 0

    def get(self, key: str, url: str, ttl: int, *, rosters: bool = False) -> tuple[Any, str]:
        path = self.root / (key + ".json")
        prior = json.loads(path.read_text()) if path.exists() else None
        force = self.deep and key != "catalog"
        if prior and not force and time.time() - path.stat().st_mtime < ttl:
            log.info("cache hit %s", key)
            return prior["data"], prior["captured_at"]
        log.info("fetch %s", key)
        request = urllib.request.Request(url, headers={"User-Agent": "FantasyGM/2 (+https://github.com/pranava0x0/FantasyGM)"})
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = json.load(response)
        self.requests += 1
        if rosters:
            raw = sanitize_rosters(raw)
        if key.startswith("league-"):
            raw = {k: raw[k] for k in ("league_id", "name", "season", "sport", "settings", "scoring_settings", "roster_positions")}
        if key == "catalog":
            raw = {pid: {k: p.get(k) for k in ("full_name", "first_name", "last_name", "fantasy_positions", "position", "team", "injury_status", "espn_id", "active")}
                   for pid, p in raw.items()}
        captured = now_iso()
        write_json(path, {"captured_at": captured, "source_url": url, "data": raw})
        return raw, captured


def eligible(player: Player, slot: str) -> bool:
    positions = set(player.positions)
    return bool(positions & {"RB", "WR", "TE"}) if slot == "FLEX" else (
        bool(positions & {"QB", "RB", "WR", "TE"}) if slot == "SUPER_FLEX" else slot in positions)


def points(player: Player, scoring: dict[str, float], now: datetime) -> float | None:
    if not player.stats or not player.stats_at or not player.stats_source:
        return None
    at = datetime.fromisoformat(player.stats_at.replace("Z", "+00:00"))
    if at.tzinfo is None or (now - at).total_seconds() > 7 * 86400:
        return None
    # Every enabled scoring category must exist; partial stat imports cannot silently count as zero.
    if any(k not in player.stats for k, v in scoring.items() if v):
        return None
    return round(sum(player.stats.get(k, 0) * v for k, v in scoring.items()), 2)


def analyze(league: League, players: dict[str, Player], now: datetime) -> dict[str, Any]:
    owned = {pid for team in league.teams for pid in team.players}
    if not league.availability_complete and not league.available_players:
        log.warning("%s has incomplete availability; pickups disabled", league.id)
    projected_fresh = bool(league.projection_at and league.projection_source and league.projection_label and
                           (now - datetime.fromisoformat(league.projection_at)).total_seconds() <= 86400)
    def score(p: Player) -> float | None:
        return league.projections.get(p.id) if projected_fresh else points(p, league.scoring, now)
    def status(p: Player) -> str | None:
        return league.player_status.get(p.id, p.status)
    stale = (now - datetime.fromisoformat(league.captured_at)).total_seconds() > 86400
    teams: dict[str, Any] = {}
    for team in league.teams:
        roster = [players[p] for p in team.players if p in players]
        issues = []
        slots = [s for s in league.slots if s not in {"BN", "IR", "TAXI"}]
        needs = {s for s in slots if sum(eligible(p, s) and status(p) not in OUT and p.id not in league.bye_players for p in roster) < slots.count(s)}
        for p in roster:
            state = status(p)
            if p.id in league.bye_players:
                issues.append({"player_id": p.id, "status": "Bye", "starter": p.id in team.starters,
                               "advice": "Bench for the bye; find an eligible replacement. A bye alone is not a drop reason.",
                               "reason": "Provider week schedule shows no game." + (" Reported status: " + state + "." if state else "")})
                continue
            if not state or state == "Healthy":
                continue
            ir_allowed = state == "IR" or bool(league.settings.get({"Out": "reserve_allow_out", "Suspended": "reserve_allow_sus", "Doubtful": "reserve_allow_doubtful", "PUP": "reserve_allow_pup"}.get(state, "")))
            has_room = len(team.reserve) < league.settings.get("reserve_slots", 0)
            advice = "Consider IR; verify provider eligibility before moving." if ir_allowed and has_room else "Hold pending recovery news and replacement value; no verified return date."
            if p.id in team.reserve:
                advice = "Hold in occupied IR slot pending recovery / eligibility news; no active bench slot is being used. Verify when provider requires activation."
            if state == "Questionable":
                advice = "Monitor practice and inactive reports; keep a same-position backup before kickoff."
            alternatives = [q for q in players.values() if q.id not in owned and (league.availability_complete or q.id in league.available_players) and status(q) not in OUT and set(q.positions) & set(p.positions) and score(q) is not None]
            replacement = max(alternatives, key=lambda q: score(q) or 0) if alternatives else None
            comparison = ""
            if replacement:
                comparison = f" Available same-position option: {replacement.name}, {score(replacement):.1f} points ({league.projection_label or 'imported per-game stats'})."
                if score(p) is not None and score(p) > (score(replacement) or 0):
                    advice += " Player's healthy projection exceeds the available replacement; preserve value while monitoring status."
                elif p.id not in team.reserve and not has_room and state in OUT:
                    advice += " Review a drop only if the confirmed absence outlasts your bench capacity and you need an active replacement."
            issues.append({"player_id": p.id, "status": state, "starter": p.id in team.starters, "advice": advice,
                           "reason": "Status from provider snapshot or shared Sleeper catalog. Recovery date and long-term value unknown; status alone does not justify a drop." + comparison})
        candidates = []
        if not stale:
            for p in players.values():
                if p.id in owned or status(p) in OUT or p.id in league.bye_players or (not league.availability_complete and p.id not in league.available_players) or not p.team or not any(eligible(p, s) for s in slots):
                    continue
                fit = any(eligible(p, s) for s in needs)
                scored = score(p)
                if not p.adds and scored is None:
                    continue
                candidates.append({"player_id": p.id, "need_fit": fit, "adds": p.adds, "points": scored,
                                   "reason": ("Adds depth at " + ", ".join(sorted(s for s in needs if eligible(p, s))) + ". " if fit else "Eligible roster depth. ") +
                                   (("Ranked by provider " + league.projection_label + " projection." if projected_fresh else "Ranked by imported per-game stats scored with this league's rules.") if scored is not None else "Global add activity is a popularity signal; point projection unavailable. Compare role before claiming." )})
        candidates.sort(key=lambda c: (c["need_fit"], c["points"] is not None, c["points"] or 0, c["adds"]), reverse=True)
        budget = league.settings.get("waiver_budget")
        teams[team.id] = {"alerts": issues, "waivers": candidates[:30], "needs": sorted(needs), "lineup_moves": [] if stale else lineup_moves(league, team, players, score, status),
                          "faab_remaining": max(0, budget - team.budget_used) if league.settings.get("waiver_type") == 2 and budget is not None and team.budget_used is not None else None}
    return {"stale": stale, "teams": teams}


def lineup_moves(league: League, team: Team, players: dict[str, Player],
                 score: Callable[[Player], float | None], status: Callable[[Player], str | None]) -> list[dict[str, Any]]:
    """Only compare eligible bench replacements with a verified common projection window."""
    moves: list[dict[str, Any]] = []
    slots = [s for s in league.slots if s not in {"BN", "IR", "TAXI"}]
    bench = [players[p] for p in team.players if p in players and p not in team.starters and p not in team.reserve]
    used: set[str] = set()
    for i, pid in enumerate(team.starters):
        if i >= len(slots):
            break
        starter = players.get(pid)
        unavailable = pid in league.bye_players or bool(starter and status(starter) in OUT) or pid == "0"
        base = 0 if unavailable else score(starter) if starter else None
        if base is None:
            continue
        eligible_bench = [p for p in bench if p.id not in used and p.id not in league.bye_players and status(p) not in OUT and eligible(p, slots[i])]
        options = [p for p in eligible_bench if score(p) is not None]
        if unavailable and not options and eligible_bench:
            best = sorted(eligible_bench, key=lambda p: p.name)[0]
            used.add(best.id)
            moves.append({"in": best.id, "out": pid, "slot": slots[i], "gain": None,
                          "reason": "Unavailable starter; eligible healthy bench option. No verified projection comparison; confirm role and game locks."})
            continue
        if not options:
            continue
        best = max(options, key=lambda p: score(p) or 0)
        gain = (score(best) or 0) - base
        if gain > 0:
            used.add(best.id)
            moves.append({"in": best.id, "out": pid, "slot": slots[i], "gain": round(gain, 2),
                          "reason": "Bye / unavailable starter" if unavailable else "Higher provider projection; verify both players are unlocked."})
    return moves


def normalize_sleeper(raw: dict[str, Any], rosters: list[dict[str, Any]], captured: str) -> League:
    if raw.get("sport") != "nfl":
        raise ValueError("Expected NFL league")
    return League(id="sleeper-" + raw["league_id"], provider="sleeper", name=raw["name"], season=str(raw["season"]),
                  captured_at=captured, source_url="https://sleeper.com/leagues/" + raw["league_id"],
                  availability_complete=True, slots=raw["roster_positions"], settings=raw.get("settings") or {}, scoring=raw.get("scoring_settings") or {},
                  teams=[Team(id=str(r["roster_id"]), players=r["players"], starters=r["starters"], reserve=r["reserve"], budget_used=r["settings"].get("waiver_budget_used")) for r in rosters])


def refresh(root: Path = ROOT, deep: bool = False, offline: bool = False) -> dict[str, Any]:
    cache = Cache(root / "data/nfl/cache", deep)
    identity_path = root / "data/nfl/teams.json"
    identities = json.loads(identity_path.read_text()) if identity_path.exists() else {}
    destination = root / "docs/data/football"
    errors: list[str] = []
    leagues: list[League] = []
    def get(key: str, url: str, ttl: int, **kwargs: Any) -> tuple[Any, str]:
        if offline:
            path = cache.root / (key + ".json")
            if key.startswith("schedule-") and not path.exists():
                path = cache.root / "schedule.json"
            saved = json.loads(path.read_text())
            return saved["data"], saved["captured_at"]
        return cache.get(key, url, ttl, **kwargs)
    catalog, catalog_at = get("catalog", API + "/players/nfl", 86400)
    trends, trends_at = get("trends", API + "/players/nfl/trending/add?lookback_hours=24&limit=100", 3600)
    news_raw, news_at = get("news", ESPN + "/news?limit=30", 3600)
    schedule_url = ESPN + "/scoreboard?limit=100"
    schedule_raw, schedule_at = get("schedule", schedule_url, 1800)
    schedule_events = schedule_raw.get("events", [])
    if schedule_events and not any(datetime.fromisoformat(e["date"].replace("Z", "+00:00")) > datetime.now(timezone.utc) for e in schedule_events):
        next_week = int((schedule_raw.get("week") or {}).get("number", 0)) + 1
        season = int((schedule_raw.get("season") or {}).get("year", datetime.now().year))
        schedule_url = ESPN + f"/scoreboard?year={season}&seasontype=2&week={next_week}&limit=100"
        schedule_raw, schedule_at = get(f"schedule-{season}-{next_week}", schedule_url, 1800)
    for lid in LEAGUES:
        try:
            raw, _ = get("league-" + lid, API + "/league/" + lid, 900)
            roster, captured = get("roster-" + lid, API + "/league/" + lid + "/rosters", 900, rosters=True)
            leagues.append(normalize_sleeper(raw, roster, captured))
        except (OSError, ValueError, KeyError) as exc:
            log.error("league %s failed: %s", lid, exc)
            errors.append("Sleeper " + lid + ": " + str(exc))
    imports = root / "data/nfl/imports"
    additions: dict[str, Player] = {}
    for path in sorted(imports.glob("*.json")) if imports.exists() else []:
        imported = Import.model_validate_json(path.read_text())
        if imported.league.id in {l.id for l in leagues}:
            raise ValueError("Duplicate league ID: " + imported.league.id)
        leagues.append(imported.league)
        additions.update({p.id: p for p in imported.players})
    required = {str(t["player_id"]) for t in trends} | {p for l in leagues for t in l.teams for p in t.players} | {p for l in leagues for p in l.available_players}
    counts = {str(t["player_id"]): int(t["count"]) for t in trends}
    players: dict[str, Player] = {}
    for pid in required:
        p = catalog.get(pid)
        if p:
            players[pid] = Player(id=pid, name=p.get("full_name") or " ".join(filter(None, [p.get("first_name"), p.get("last_name")])) or pid,
                                  positions=p.get("fantasy_positions") or [p.get("position") or "?"], team=p.get("team"),
                                  status=p.get("injury_status"), espn_id=str(p["espn_id"]) if p.get("espn_id") else None, adds=counts.get(pid, 0))
        else:
            players[pid] = Player(id=pid, name="Player " + pid, positions=["?"], adds=counts.get(pid, 0))
    players.update(additions)
    for league in leagues:
        if league.id not in identities and league.my_team:
            identities[league.id] = league.my_team
        league.my_team = identities.get(league.id)
    stats_path = root / "data/nfl/stats.json"
    if stats_path.exists():
        for record in json.loads(stats_path.read_text()):
            imported_player = Player.model_validate(record)
            if imported_player.id in players:
                p = players[imported_player.id]
                p.stats, p.stats_source, p.stats_at = imported_player.stats, imported_player.stats_source, imported_player.stats_at
    news = [{"headline": a.get("headline"), "url": (a.get("links") or {}).get("web", {}).get("href"), "published_at": a.get("published"),
             "athlete_ids": [str(c["athleteId"]) for c in a.get("categories", []) if c.get("type") == "athlete" and c.get("athleteId")]}
            for a in news_raw.get("articles", []) if not a.get("premium")]
    games = []
    for event in schedule_raw.get("events", []):
        for competition in event.get("competitions", []):
            for c in competition.get("competitors", []):
                games.append({"team": c["team"]["abbreviation"], "at": event["date"], "name": event["name"], "source_url": schedule_url})
    bye_teams = {t["abbreviation"] for t in (schedule_raw.get("week") or {}).get("teamsOnBye", [])}
    for league in leagues:
        league.bye_players = list(set(league.bye_players) | {p.id for p in players.values() if p.team in bye_teams})
    shared = {"schedule_week": (schedule_raw.get("week") or {}).get("number"), "schedule_source": schedule_url, "catalog_at": catalog_at, "news_at": news_at, "schedule_at": schedule_at, "trends_at": trends_at,
              "players": {k: v.model_dump() for k, v in players.items()}, "news": news, "games": games}
    write_json(destination / "nfl.json", shared)
    registry = []
    for league in leagues:
        result = analyze(league, players, datetime.now(timezone.utc))
        filename = league.id + ".json"
        write_json(destination / filename, {"league": league.model_dump(), "analysis": result})
        registry.append({"id": league.id, "name": league.name, "provider": league.provider, "path": "data/football/" + filename, "source_url": league.source_url})
    if not any(l.provider == "yahoo" for l in leagues):
        registry.append({"id": "yahoo-544768", "name": "Yahoo · League 544768 · Team 9", "provider": "yahoo", "path": None, "source_url": "https://football.fantasysports.yahoo.com/f1/544768/9"})
    if not any(l.provider == "espn" for l in leagues):
        registry.append({"id": "espn-setup", "name": "ESPN · Add NFL league", "provider": "espn", "path": None, "source_url": "https://fantasy.espn.com/football/"})
    index = {"version": 1, "built_at": now_iso(), "leagues": registry, "errors": errors, "requests": cache.requests}
    wnba_path = root / "docs/data/state.json"
    if wnba_path.exists():
        meta = json.loads(wnba_path.read_text())["meta"]
        index["wnba"] = {"name": meta["league_name"], "captured_at": meta["captured_at"], "path": "wnba.html", "my_team": "1"}
    write_json(destination / "index.json", index)
    log.info("NFL ready: %d leagues, %d players, %d requests", len(leagues), len(players), cache.requests)
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import", dest="snapshot_import", type=Path)
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.snapshot_import:
        imported = Import.model_validate_json(args.snapshot_import.read_text())
        write_json(args.root / "data/nfl/imports" / (imported.league.id + ".json"), imported.model_dump())
    refresh(args.root, args.deep, args.offline)


if __name__ == "__main__":
    main()
