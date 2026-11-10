"""Build local scouting reports for owned NFL/NBA players and each displayed waiver lead."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
from pathlib import Path
import re
import time
from typing import Any
import unicodedata
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from pipeline.football import ROOT, write_json

log = logging.getLogger(__name__)
SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/"
OVERVIEW = "https://site.web.api.espn.com/apis/common/v3/sports/"
SPORTS = {"nfl": "football/nfl", "nba": "basketball/nba"}
OUTPUT = ROOT / "scratch/lineups"
EASTERN = ZoneInfo("America/New_York")


ALIASES = {"Nikola Jokic": "Nikola Jokić", "Stephen Curry": "Steph Curry",
           "Kenny Gainwell": "Kenneth Gainwell", "Luther Burden III": "Luther Burden",
           "Wendell Carter Jr.": "Wendell Carter"}


def normalize(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9 -]", "", ascii_text).replace("-", " ").split())


def instant(value: str) -> datetime:
    # Python 3.9 accepts microseconds, while AT Protocol timestamps can have nanoseconds.
    value = re.sub(r"\.(\d+)", lambda m: "." + m[1][:6].ljust(6, "0"), value)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("missing timestamp offset")
    return dt.astimezone(timezone.utc)


class PublicSource:
    """Reuse successful responses within a capture hour; stop on sustained throttling."""

    def __init__(self, client: httpx.Client, cache: Path) -> None:
        self.client = client
        self.cache = cache
        self.blocked: set[str] = set()
        self.last: dict[str, float] = {}

    def get(self, url: str) -> dict[str, Any]:
        host = httpx.URL(url).host
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if path.exists():
            return json.loads(path.read_text())
        if host in self.blocked:
            raise ValueError("source rate-limited; request skipped")
        for attempt in range(2):
            time.sleep(max(0, 1.65 - (time.monotonic() - self.last.get(host, 0))))
            self.last[host] = time.monotonic()
            response = self.client.get(url)
            if response.status_code != 429:
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("unexpected provider response")
                write_json(path, data)
                return data
            retry = response.headers.get("retry-after", "10")
            if not attempt and retry.isdigit() and int(retry) <= 30:
                time.sleep(max(10, int(retry)))
                continue
            self.blocked.add(host)
            break
        raise ValueError("source rate-limited")


def subjects(rosters: dict, shared: dict, desks: list[dict]) -> list[dict]:
    players: dict[tuple[str, str], dict] = {}

    def entry(sport: str, pid: str, name: str) -> dict:
        catalog = shared["players"].get(pid, {}) if sport == "nfl" else {}
        return players.setdefault((sport, pid), {
            "sport": sport, "id": pid, "name": catalog.get("name", name), "team": catalog.get("team"),
            "defense": "DEF" in catalog.get("positions", []), "catalog_status": catalog.get("status"),
            "espn_id": pid if sport == "nba" else catalog.get("espn_id"), "rosters": [], "waivers": [],
        })

    for league in rosters["leagues"]:
        sport = league["sport"]
        for p in league["players"]:
            row = entry(sport, p["id"], p["name"])
            row["rosters"].append({"key": league["key"], "status": p["injury_status"], "ir": p["on_ir"],
                                   "projection": p.get("projected_value") if sport == "nfl" else p.get("projected_points_per_game"),
                                   "week": league.get("week"), "url": league["url"]})
    for desk in desks:
        league = desk["league"]
        team = desk["analysis"]["teams"].get(league["my_team"])
        if desk["analysis"]["stale"] or not team:
            continue
        for rank, pick in enumerate(team["waivers"], 1):
            pid = pick["player_id"]
            entry("nfl", pid, shared["players"][pid]["name"])["waivers"].append({
                "key": league["id"], "rank": rank, "adds": pick["adds"], "reason": pick["reason"]})
    return sorted(players.values(), key=lambda p: (not p["rosters"], p["sport"], p["name"], p["id"]))


def name_key(value: str) -> str:
    return normalize(value).replace(" ", "")


def athlete_id(row: dict, espn: PublicSource) -> str:
    if row["espn_id"]:
        return str(row["espn_id"])
    if not row["team"]:
        raise ValueError("no team or ESPN athlete ID")
    team = {"WAS": "WSH"}.get(row["team"], row["team"]).lower()
    roster = espn.get(SITE + team + "/roster")
    names = {name_key(row["name"]), name_key(ALIASES.get(row["name"], row["name"]))}
    matches = {str(p["id"]) for group in roster["athletes"] for p in group["items"]
               if any(name_key(p.get(k) or "") in names for k in ("fullName", "displayName"))}
    if len(matches) != 1:
        raise ValueError("no unique name match on ESPN team roster")
    return matches.pop()


def news_date(value: str) -> datetime:
    try:
        return instant(value)
    except ValueError:
        # RotoWire uses named Pacific offsets ahead of the year.
        for zone, offset in ((" PST ", " -0800 "), (" PDT ", " -0700 ")):
            value = value.replace(zone, offset)
        if re.fullmatch(r"\w{3} \w{3} +\d{1,2} [\d:]+ [+-]\d{4} \d{4}", value):
            return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc)
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            raise ValueError("ambiguous news date")
        return dt.astimezone(timezone.utc)


def details(raw: dict, aid: str, sport: str, end: datetime) -> dict:
    if not any(raw.get(k) for k in ("statistics", "news", "rotowire", "gameLog", "nextGame")):
        raise ValueError("empty athlete overview")
    url = f"https://www.espn.com/{sport}/player/_/id/{aid}"
    reports = []
    rotowire = raw.get("rotowire") or {}
    for article in [rotowire, *(raw.get("news") or [])]:
        if not article.get("headline") or not article.get("published") or article.get("premium"):
            continue
        if article is not rotowire and not any(str(c.get("athleteId")) == aid for c in article.get("categories", [])):
            continue
        try:
            dt = news_date(article["published"])
        except (TypeError, ValueError, OverflowError):
            continue
        href = url if article is rotowire else article.get("links", {}).get("web", {}).get("href", "")
        if end - timedelta(days=7) <= dt <= end and href.startswith("https://"):
            reports.append({"at": dt.isoformat(), "headline": article["headline"], "url": href,
                            "source": "RotoWire via ESPN" if article is rotowire else "ESPN"})
    gamelog = raw.get("gameLog") or {}
    lines: dict[str, dict] = {}
    for group in gamelog.get("statistics", []):
        for row in group.get("events", []):
            event = gamelog.get("events", {}).get(str(row["eventId"]), {})
            date = event.get("gameDate")
            if not date or not event.get("gameResult") or instant(date) > end or not row.get("stats"):
                continue
            game = lines.setdefault(str(row["eventId"]), {"at": date, "opponent": event.get("opponent", {}).get("abbreviation"),
                                    "result": event["gameResult"], "score": event.get("score"), "stats": {}})
            game["stats"].update(zip(group.get("names", []), row["stats"]))
    season = raw.get("statistics") or {}
    totals = next((dict(zip(season.get("names", []), s.get("stats", []))) for s in season.get("splits", [])
                   if s.get("displayName") == "Regular Season"), {})
    return {"url": url, "news": sorted(reports, key=lambda a: a["at"], reverse=True),
            "games": sorted(lines.values(), key=lambda g: g["at"], reverse=True)[:3], "season": totals}


def defense_details(row: dict, espn: PublicSource, end: datetime) -> dict:
    url = SITE + row["id"].lower() + "/schedule"
    data = espn.get(url)
    if not data.get("events"):
        raise ValueError("empty team schedule")
    games = []
    for event in data["events"]:
        c = event["competitions"][0]
        if not c["status"]["type"]["completed"] or instant(event["date"]) > end:
            continue
        sides = c["competitors"]
        games.append({"at": event["date"], "matchup": event["shortName"],
                      "score": ", ".join(f"{s['team']['abbreviation']} {s['score']['displayValue']}" for s in sides)})
    return {"url": url, "games": sorted(games, key=lambda g: g["at"], reverse=True)[:3]}


def beat_updates(players: list[dict], source: PublicSource, end: datetime) -> dict[str, str]:
    """Match the named subject of RotoWire headlines, never another player in the body."""
    start = end - timedelta(days=7)
    coverage = {}
    for sport in SPORTS:
        handle = f"rotowire{sport}.bsky.social"
        names: dict[str, list[dict]] = {}
        for p in players:
            if p["sport"] == sport:
                for key in {name_key(p["name"]), name_key(ALIASES.get(p["name"], p["name"]))}:
                    names.setdefault(key, []).append(p)
        cursor = ""
        seen: set[str] = set()
        coverage[handle] = "partial: page limit"
        try:
            for _ in range(12):
                args = {"actor": handle, "filter": "posts_no_replies", "limit": "100"}
                if cursor:
                    args["cursor"] = cursor
                feed = source.get("https://api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?" + urlencode(args))
                oldest = end
                for item in feed["feed"]:
                    post = item["post"]
                    if item.get("reason") or post["author"]["handle"] != handle:
                        continue
                    record = post["record"]
                    at = instant(record["createdAt"])
                    oldest = min(oldest, at)
                    title = record["text"].splitlines()[0].strip() if record["text"].strip() else ""
                    matches = names.get(name_key(title.partition(":")[0]), [])
                    post_id = post["uri"].rsplit("/", 1)[-1]
                    if (not start <= at <= end or len(matches) != 1 or post_id in seen
                            or not re.fullmatch(r"[a-zA-Z0-9._~-]+", post_id)):
                        continue
                    seen.add(post_id)
                    matches[0].setdefault("beat", []).append({"at": at.isoformat(), "headline": title,
                        "url": f"https://bsky.app/profile/{handle}/post/{post_id}"})
                cursor = feed.get("cursor", "")
                if not cursor or oldest < start:
                    coverage[handle] = "seven-day window checked"
                    break
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            coverage[handle] = f"incomplete: {exc}"
    for p in players:
        p.get("beat", []).sort(key=lambda r: (r["at"], r["url"]), reverse=True)
    return coverage


def stat_line(stats: dict) -> str:
    preferred = {
        "minutes": "MIN", "points": "PTS", "totalRebounds": "REB", "assists": "AST",
        "steals": "STL", "blocks": "BLK", "avgPoints": "PTS/G", "avgRebounds": "REB/G",
        "avgAssists": "AST/G", "gamesPlayed": "GP", "passingYards": "pass yds",
        "passingTouchdowns": "pass TD", "interceptions": "INT", "rushingAttempts": "carries",
        "rushingYards": "rush yds", "rushingTouchdowns": "rush TD", "receptions": "rec",
        "receivingTargets": "targets", "receivingYards": "rec yds", "receivingTouchdowns": "rec TD",
        "fieldGoalsMade": "FG made", "fieldGoalAttempts": "FG att", "extraPointsMade": "XP made",
    }
    selected = [(label, stats[key]) for key, label in preferred.items() if key in stats]
    return ", ".join(f"{value} {label}" for label, value in selected or stats.items())


def render(capture: dict) -> str:
    rows = ["# Player reports", "", f"Report compiled: {capture['checked_at']}. Roster capture: {capture['roster_at']}. Public provider responses are cached within the hour.",
            "NFL projections omit unsupported scoring components. NBA projections use ESPN standard points per game, a proxy for category scoring.",
            "Waiver ranks apply only to the specified team. Social links are mentions, not verified availability.", ""]
    if capture.get("roster_errors"):
        rows.extend(["Roster coverage incomplete: a league failed or its roster was unidentified.", ""])
    for feed, status in capture.get("feeds", {}).items():
        rows.append(f"- {feed}: {status}")
    rows.append("")
    for p in capture["players"]:
        rows.extend([f"## {p['name']} · {p['sport'].upper()}", ""])
        for r in p["rosters"]:
            unit = f"Week {r['week']} estimate" if p["sport"] == "nfl" else "standard points/game"
            value = "unavailable" if r["projection"] is None else f"{r['projection']:.1f}"
            rows.append(f"- [Roster {r['key']}]({r['url']}): {r['status']}" + (" · IR slot" if r["ir"] else "") + f" · {unit}: {value}.")
        if p["waivers"]:
            rows.append(f"- Team: {p['team']} · Sleeper injury tag: {p.get('catalog_status') or 'none reported'}.")
        for w in p["waivers"]:
            rows.append(f"- Waiver #{w['rank']} in {w['key']} · {w['adds']:,} global adds: {w['reason']}")
        if "error" in p:
            rows.append(f"- ESPN detail unavailable: {p['error']}.")
        else:
            info = p["details"]
            rows.append(f"- [ESPN {'team schedule' if p['defense'] else 'player profile'}]({info['url']})")
            for a in info.get("news", [])[:3]:
                rows.append(f"- {a['at'][:10]} · {a['source']}: [{a['headline']}]({a['url']})")
            if not p["defense"] and not info.get("news"):
                rows.append("- No dated athlete report in this source’s seven-day window.")
            if info.get("season"):
                rows.append("- Regular season: " + stat_line(info["season"]))
            if not info["games"]:
                rows.append("- No recent game line supplied.")
            for g in info["games"]:
                date = instant(g["at"]).astimezone(EASTERN).date()
                line = (f"{g['matchup']} · {g['score']}" if p["defense"] else
                        f"vs {g['opponent']} ({g['result']} {g['score']}): " +
                        stat_line(g["stats"]))
                rows.append(f"- {date} · {line}")
        for note in p.get("beat", [])[:3]:
            rows.append(f"- {note['at'][:10]} · RotoWire feed: [{note['headline']}]({note['url']})")
        if p["rosters"] and not p.get("social"):
            rows.append("- Individual name-search coverage unavailable for this roster capture.")
        if p.get("social"):
            for source, s in p["social"].items():
                coverage = ", ".join(sorted({q["outcome"] for q in s["queries"]})) or "not searched"
                links = " · ".join(f"[{v['published_at'][:10]}]({v['url']})" for v in s["posts"][:2])
                rows.append(f"- {source}: {coverage}" + (f" · {links}" if links else ""))
        rows.append("")
    return "\n".join(rows) + "\n"


def build(output: Path = OUTPUT, root: Path = ROOT) -> int:
    roster = json.loads((output / "report.json").read_text())
    social = json.loads((output / "player-name-searches.json").read_text())
    desk = root / "docs/data/football"
    shared = json.loads((desk / "nfl.json").read_text())
    desks = [json.loads((desk / (league["key"].replace(":", "-") + ".json")).read_text())
             for league in roster["leagues"] if league["key"].startswith("sleeper:")]
    players = subjects(roster, shared, desks)
    end = datetime.now(timezone.utc)
    channels = {(p["sport"], p["player_id"]): p["sources"] for p in social["players"]
                if instant(social["searched_at"]) == instant(roster["captured_at"])}
    with httpx.Client(timeout=15, headers={"User-Agent": "FantasyGM/1.0 (+https://github.com/pranava0x0/FantasyGM)"}) as client:
        espn = PublicSource(client, output / "espn-cache" / end.strftime("%Y%m%dT%H"))
        for p in players:
            p["social"] = channels.get((p["sport"], p["id"]))
            try:
                if p["defense"]:
                    p["details"] = defense_details(p, espn, end)
                else:
                    aid = athlete_id(p, espn)
                    raw = espn.get(f"{OVERVIEW}{SPORTS[p['sport']]}/athletes/{aid}/overview")
                    p["details"] = details(raw, aid, p["sport"], end)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
                p["error"] = str(exc) or type(exc).__name__
                log.warning("%s: %s", p["name"], p["error"])
        feeds = beat_updates(players, espn, end)
    roster_errors = bool(roster["errors"] or any(not l["players"] for l in roster["leagues"]))
    capture = {"checked_at": end.isoformat(), "roster_at": roster["captured_at"], "players": players,
               "roster_errors": roster_errors, "feeds": feeds}
    archive = output / "player-history" / (end.strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    write_json(archive, capture)
    write_json(output / "player-reports.json", capture)
    (output / "player-reports.md").write_text(render(capture))
    ok = sum("details" in p for p in players)
    log.info("ESPN profiles or schedules: %d/%d. Report: %s", ok, len(players), output / "player-reports.md")
    return int(roster_errors or not players or ok != len(players))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, help="Render a saved scouting capture without network access")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.replay:
        args.replay.with_suffix(".md").write_text(render(json.loads(args.replay.read_text())))
        return 0
    return build()


if __name__ == "__main__":
    raise SystemExit(main())
