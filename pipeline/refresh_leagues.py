"""Refresh NFL/NBA rosters, then search public social sources for each owned player."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import re
import time
from typing import Any
import xml.etree.ElementTree as ET

import httpx

from pipeline import football, multileague
from pipeline.player_reports import ALIASES, build, instant, normalize
from pipeline.reddit import USER_AGENT

log = logging.getLogger(__name__)
ROOT = football.ROOT
OUTPUT = ROOT / "scratch/lineups"
SOURCES = ("Bluesky", "Reddit")
ATOM = "{http://www.w3.org/2005/Atom}"


def searchable_names(report: dict[str, Any], catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Take names only from rostered pro players, never from league or owner labels."""
    players = {}
    for league in report["leagues"]:
        sport = league["sport"]
        for player in league["players"]:
            pid, name = player["id"], player["name"]
            shared = catalog.get(pid, {}) if sport == "nfl" else {}
            if "DEF" in shared.get("positions", []) and shared.get("name") != pid:
                name = shared["name"] + " defense"
            aliases = [name] if name and name != pid else []
            if name in ALIASES:
                aliases.append(ALIASES[name])
            players[sport, pid] = {"sport": sport, "player_id": pid, "name": name, "names": aliases}
    return [players[key] for key in sorted(players)]


def search(client: httpx.Client, source: str, sport: str, name: str,
           start: datetime, end: datetime) -> list[dict[str, str]]:
    params = {"q": f'"{name}"', "sort": "latest", "limit": "35"}
    subreddit = "fantasyfootball" if sport == "nfl" else "fantasybball"
    if source == "Bluesky":
        url = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
        params.update(since=start.isoformat(), until=end.isoformat())
    else:
        url = f"https://www.reddit.com/r/{subreddit}/search.rss"
        params.update(sort="new", t="week", restrict_sr="on")
    res = client.get(url, params=params)
    res.raise_for_status()
    rows = []
    if source == "Bluesky":
        for post in res.json()["posts"]:
            uri = re.fullmatch(r"at://(did:plc:[a-z2-7]+|did:web:[A-Za-z0-9.-]+)/app\.bsky\.feed\.post/([A-Za-z0-9._~-]+)", post["uri"])
            if uri:
                record = post["record"]
                rows.append((record["text"], record["createdAt"],
                             f"https://bsky.app/profile/{uri[1]}/post/{uri[2]}"))
    else:
        root = ET.fromstring(res.content)
        if root.tag != ATOM + "feed":
            raise ValueError("expected Atom feed")
        for entry in root.findall(ATOM + "entry"):
            link = entry.find(ATOM + "link")
            path = link.get("href", "") if link is not None else ""
            match = re.fullmatch(rf"https://(?:www\.)?reddit\.com/r/{subreddit}/comments/([a-z0-9]+)(?:/[^?#]*)?", path, re.I)
            if match:
                rows.append((entry.findtext(ATOM + "title", ""), entry.findtext(ATOM + "published", ""),
                             f"https://www.reddit.com/comments/{match[1]}/"))
    pattern = re.compile(r"\b" + re.escape(normalize(name)) + r"\b")
    posts = []
    for text, timestamp, link in rows:
        dt = instant(timestamp)
        if start <= dt <= end and pattern.search(normalize(text)):
            posts.append({"url": link, "published_at": dt.isoformat()})
    return posts


def collect(source: str, players: list[dict[str, Any]], end: datetime) -> list[dict[str, Any]]:
    start = end - timedelta(days=7)
    results = []
    blocked = False
    with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        for player in players:
            queries, posts = [], {}
            for name in player["names"]:
                if blocked:
                    queries.append({"query": name, "outcome": "not searched: source rate-limited"})
                    continue
                for attempt in range(2):
                    try:
                        time.sleep(1.65)
                        found = search(client, source, player["sport"], name, start, end)
                        posts.update((p["url"], p) for p in found)
                        outcome = "matches" if found else "no recent match"
                    except httpx.HTTPStatusError as exc:
                        status = exc.response.status_code
                        outcome = f"unavailable: HTTP {status}"
                        if status == 429:
                            wait = exc.response.headers.get("retry-after", "10")
                            if not attempt and wait.isdigit() and int(wait) <= 30:
                                time.sleep(max(10, int(wait)))
                                continue
                            blocked = True
                    except (httpx.RequestError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
                        outcome = "unavailable: " + type(exc).__name__
                    break
                queries.append({"query": name, "outcome": outcome})
            results.append({"queries": queries, "posts": sorted(posts.values(),
                            key=lambda p: (p["published_at"], p["url"]), reverse=True)})
    return results


def render(report: dict[str, Any]) -> str:
    lines = ["# Player name searches", "", f"Seven days ending {report['searched_at']} (UTC). "
             "Links are public mentions, not verified lineup news.", ""]
    if report["incomplete_rosters"]:
        lines += ["Roster coverage is incomplete; only successfully identified rosters are listed.", ""]
    for sport in ("nfl", "nba"):
        lines += [f"## {sport.upper()}", "", "| Player or defense | Bluesky | Reddit |", "|---|---|---|"]
        for player in report["players"]:
            if player["sport"] != sport:
                continue
            cells = [player["name"]]
            for source in SOURCES:
                result = player["sources"][source]
                posts = result["posts"]
                outcomes = sorted({q["outcome"] for q in result["queries"]})
                links = " · ".join(f"[{p['published_at'][:10]}]({p['url']})" for p in posts[:2])
                cells.append((f"{len(posts)}: {links}" if posts else "") +
                             ("; " if posts and len(outcomes) > 1 else "") +
                             ", ".join(o for o in outcomes if o != "matches")
                             if outcomes else "not searched: missing player name")
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, help="Render a saved search JSON locally without network access")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.replay:
        args.replay.with_suffix(".md").write_text(render(json.loads(args.replay.read_text())))
        return 0
    index = football.refresh(ROOT, deep=True)
    shared = json.loads((ROOT / "docs/data/football/nfl.json").read_text())
    week = shared["schedule_week"]
    # ESPN also reports postseason weeks; the Sleeper solver handles regular season only.
    if not isinstance(week, int) or not 1 <= week <= 18:
        raise ValueError("No supported NFL schedule week; check the league season.")
    code = multileague.main(["--week", str(week), "--output", str(OUTPUT)])
    roster = json.loads((OUTPUT / "report.json").read_text())
    end = instant(roster["captured_at"])
    players = searchable_names(roster, shared["players"])
    incomplete = bool(code or any(not league["players"] for league in roster["leagues"]))
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        futures = {s: pool.submit(collect, s, players, end) for s in SOURCES}
        channels = {s: f.result() for s, f in futures.items()}
    for i, player in enumerate(players):
        player["sources"] = {s: channels[s][i] for s in SOURCES}
    capture = {"searched_at": end.isoformat(), "since": (end - timedelta(days=7)).isoformat(),
               "incomplete_rosters": incomplete, "players": players}
    stem = "player-name-searches"
    archive = OUTPUT / "social-history"
    archive.mkdir(exist_ok=True)
    football.write_json(archive / (end.strftime("%Y%m%dT%H%M%S%fZ") + ".json"), capture)
    football.write_json(OUTPUT / (stem + ".json"), capture)
    (OUTPUT / (stem + ".md")).write_text(render(capture))
    covered = sum(any(q["outcome"] in ("matches", "no recent match") for s in p["sources"].values()
                      for q in s["queries"]) for p in players)
    for s in SOURCES:
        missed = sum(q["outcome"] not in ("matches", "no recent match") for p in players
                     for q in p["sources"][s]["queries"])
        log.info("%s: %d unavailable or skipped queries", s, missed)
    log.info("Completed a name search for %d/%d roster entries. Report: %s", covered, len(players), OUTPUT / (stem + ".md"))
    profiles_code = build(OUTPUT, ROOT)
    return int(bool(index["errors"] or incomplete or not players or covered != len(players) or profiles_code))


if __name__ == "__main__":
    raise SystemExit(main())
