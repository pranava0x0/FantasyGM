"""WNBA news ingestion + player/team matching.

ESPN publishes a public WNBA news feed at
`site.api.espn.com/apis/site/v2/sports/basketball/wnba/news`. No auth
required. Each article carries a `categories[]` array with
`{type: athlete, athleteId}` and `{type: team, teamId}` entries — those
map directly to the same IDs we use in `state.json`, so matching is
exact and one-shot (no name fuzzing).

Twitter and Reddit live on the backlog — Twitter requires API keys we
don't have, and the Reddit RSS feed is volatile. ESPN is the canonical
official source; this module is intentionally scoped to it for now.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

NEWS_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/news"
USER_AGENT = "FantasyGM/0.1 news-ingest (+github.com/pranava0x0/FantasyGM)"

# Canonical WNBA pro-team IDs (mirrors WNBA_TEAM_ABBR in build_state.py — keep in sync).
# Articles whose pro_team_ids contain ANY ID outside this set are rejected as non-WNBA.
WNBA_TEAM_IDS: frozenset[int] = frozenset({
    3, 5, 6, 8, 9, 11, 14, 16, 17, 18, 19, 20,
    129689, 131935, 132052,  # 2026 expansion: GS / TOR / POR
})


def fetch_news(limit: int = 50, *, _opener: Any = None) -> dict[str, Any]:
    """Hit ESPN's WNBA news endpoint. Raw response, no transformation.

    `_opener` is a urllib opener for tests; production passes None.
    """
    req = urllib.request.Request(
        f"{NEWS_URL}?limit={limit}", headers={"User-Agent": USER_AGENT}
    )
    try:
        with (_opener.open(req) if _opener else urllib.request.urlopen(req, timeout=15)) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        log.warning("news: ESPN fetch failed (%s) — returning empty feed", e)
        return {"articles": []}


def normalize_articles(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten ESPN's article shape into the bits our state needs.

    Returns a list of dicts (not pydantic-validated here — schema is in
    `pipeline.schema.NewsItem`; `build_state` does the wrapping):

      {
        id, headline, description, published_at (UTC datetime),
        url, athlete_ids[], pro_team_ids[]
      }

    Premium-locked articles are filtered out (can't link them).
    Non-WNBA articles are filtered: any article whose pro_team_ids contains an
    ID not in WNBA_TEAM_IDS is rejected. Articles with no team tags are kept
    only when they have at least one athlete_id (so pure cross-sport stories
    with no athlete tagging are also dropped).
    """
    out: list[dict[str, Any]] = []
    for a in raw.get("articles") or []:
        if a.get("premium"):
            continue
        athletes: list[int] = []
        teams: list[int] = []
        for c in a.get("categories") or []:
            t = c.get("type")
            if t == "athlete" and c.get("athleteId") is not None:
                athletes.append(int(c["athleteId"]))
            elif t == "team" and c.get("teamId") is not None:
                teams.append(int(c["teamId"]))
        # Reject non-WNBA articles: if any pro_team_id is outside the WNBA set
        # it's a cross-sport story (NFL/NBA sharing ESPN's ID space).
        if teams and not all(tid in WNBA_TEAM_IDS for tid in teams):
            continue
        # Articles with no team tag and no athlete tag are generic fluff — skip.
        if not teams and not athletes:
            continue
        url = _extract_web_url(a)
        published = _parse_published(a.get("published"))
        out.append({
            "id": int(a.get("id") or 0),
            "headline": str(a.get("headline") or "").strip(),
            "description": str(a.get("description") or "").strip(),
            "url": url,
            "published_at": published,
            "athlete_ids": sorted(set(athletes)),
            "pro_team_ids": sorted(set(teams)),
        })
    # Newest first.
    out.sort(key=lambda r: r["published_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out


def _extract_web_url(article: dict[str, Any]) -> str | None:
    """Pick the canonical web URL from ESPN's links structure."""
    links = article.get("links") or {}
    web = links.get("web") or {}
    href = web.get("href")
    if href:
        return str(href)
    return None


def _parse_published(s: str | None) -> datetime | None:
    """Parse ESPN's ISO8601 timestamps (always UTC-suffixed)."""
    if not s:
        return None
    try:
        # ESPN ships "2026-05-18T02:17:43Z" — Python 3.9 accepts "+00:00".
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def match_to_players(
    articles: list[dict[str, Any]],
    player_ids: set[int],
    pro_team_id_to_player_ids: dict[int, set[int]] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    """Group articles by player id they pertain to.

    A player is "mentioned" by an article if (a) the article's
    `athlete_ids` includes that player, or (b) the article's
    `pro_team_ids` overlaps with the player's pro team (passed in via
    the reverse-lookup map).

    Returns `{player_id: [article_dict, ...]}`, articles within each list
    in published-date order (newest first).
    """
    by_player: dict[int, list[dict[str, Any]]] = {pid: [] for pid in player_ids}
    if pro_team_id_to_player_ids is None:
        pro_team_id_to_player_ids = {}
    for art in articles:
        # Direct athlete tag.
        for aid in art["athlete_ids"]:
            if aid in by_player:
                by_player[aid].append(art)
        # Team tag — only if the player isn't already credited via athlete.
        direct = set(art["athlete_ids"]) & player_ids
        for tid in art["pro_team_ids"]:
            for pid in pro_team_id_to_player_ids.get(tid, set()):
                if pid in by_player and pid not in direct:
                    by_player[pid].append(art)
    return {pid: arts for pid, arts in by_player.items() if arts}
