"""Reddit r/wnba signal layer.

Fetches the subreddit's RSS feed (no auth required — Atom feed still public)
and matches posts to players by name substring. Returns a list of normalized
post dicts ready for schema wrapping.

JSON API requires OAuth since 2023; RSS is the no-auth path.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

REDDIT_RSS_URL = "https://www.reddit.com/r/wnba/new/.rss?limit=50"
USER_AGENT = "FantasyGM/0.1 reddit-ingest (+github.com/pranava0x0/FantasyGM)"

_ATOM_NS = "http://www.w3.org/2005/Atom"


def fetch_reddit(limit: int = 50) -> list[dict[str, Any]]:
    """Fetch r/wnba newest posts via RSS. Returns raw normalized post dicts.

    Returns [] on any fetch/parse failure so the pipeline degrades gracefully.
    """
    url = f"https://www.reddit.com/r/wnba/new/.rss?limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
    except urllib.error.URLError as e:
        log.warning("reddit: RSS fetch failed (%s) — skipping", e)
        return []
    return _parse_rss(body)


def _parse_rss(body: bytes) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        log.warning("reddit: RSS parse failed (%s)", e)
        return []
    ns = {"a": _ATOM_NS}
    out = []
    for entry in root.findall("a:entry", ns):
        title_el = entry.find("a:title", ns)
        link_el = entry.find("a:link", ns)
        pub_el = entry.find("a:published", ns)
        if title_el is None or link_el is None:
            continue
        title = (title_el.text or "").strip()
        url = link_el.get("href", "")
        published_at = _parse_dt(pub_el.text if pub_el is not None else None)
        out.append({"title": title, "url": url, "published_at": published_at})
    return out


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).lower().strip()


def match_to_players(
    posts: list[dict[str, Any]],
    player_name_map: dict[int, str],
) -> dict[int, list[dict[str, Any]]]:
    """Return {player_id: [post, ...]} for posts whose title mentions the player.

    Matching strategy: normalize both title and player name, then check if the
    full name (or last name when it is 6+ chars and unique-ish) appears as a
    word-boundary substring. Last-name-only matching uses a word-boundary regex
    to avoid "Bell" matching "Isabelle".

    `player_name_map` is {player_id: full_name}.
    """
    if not posts or not player_name_map:
        return {}

    # Build normalized lookup: player_id -> (full_norm, last_norm)
    entries: list[tuple[int, str, str]] = []
    for pid, name in player_name_map.items():
        full = _normalize(name)
        parts = full.split()
        last = parts[-1] if parts else ""
        entries.append((pid, full, last))

    # Deduplicate last names that belong to more than one player —
    # ambiguous last names only match via full name.
    from collections import Counter
    last_counts = Counter(last for _, _, last in entries)

    by_player: dict[int, list[dict[str, Any]]] = {}
    for post in posts:
        title_norm = _normalize(post["title"])
        for pid, full_name, last_name in entries:
            matched = False
            if full_name in title_norm:
                matched = True
            elif len(last_name) >= 5 and last_counts[last_name] == 1:
                # Word-boundary match on unambiguous last names.
                if re.search(r"\b" + re.escape(last_name) + r"\b", title_norm):
                    matched = True
            if matched:
                by_player.setdefault(pid, []).append(post)

    return by_player
