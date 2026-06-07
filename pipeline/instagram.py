"""Instagram signal layer.

Primary path: reads `instagram_raw.json` written by the refresh skill's Chrome
scraping step (navigating to player profile pages + comment sections using the
user's already-logged-in browser — no credentials needed in this file).

The raw file format is a JSON array:
    [{"title": "<caption or comment text>",
      "url": "https://www.instagram.com/p/<shortcode>/",
      "username": "<handle without @>",
      "published_at": "<ISO-8601 or null>",
      "post_type": "post|reel|comment"}, ...]

The Chrome scraping step navigates to each player's Instagram profile (handles
from `data/player_socials.json`) plus a hashtag search (e.g. #WNBA), scrolls
to collect captions and top comments, and writes this file.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_RAW_FILENAME = "instagram_raw.json"


def load_instagram(raw_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load Instagram posts from the pre-scraped file. Returns [] if absent."""
    if raw_dir is None:
        return []
    path = raw_dir / _RAW_FILENAME
    if not path.exists():
        log.debug("instagram: %s not found — skipping", path)
        return []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("instagram: failed to read %s (%s)", path, e)
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or item.get("caption") or item.get("text") or "").strip()
        if not title:
            continue
        pub = _coerce_dt(item.get("published_at"))
        out.append({
            "title": title,
            "url": item.get("url") or "",
            "published_at": pub,
            "username": (item.get("username") or item.get("handle") or "").lstrip("@"),
            "post_type": item.get("post_type") or "post",
        })
    log.info("instagram: loaded %d posts from %s", len(out), path)
    return out


def match_to_players(
    posts: list[dict[str, Any]],
    player_name_map: dict[int, str],
    player_socials: dict[int, dict[str, str]] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    """Return {player_id: [post, ...]} for posts that mention or belong to the player.

    Matching strategy (in priority order):
    1. Post username matches the player's known Instagram handle (from player_socials).
    2. Player's full name appears in the post caption/comment.
    3. Player's last name (≥5 chars, unique across the player map) appears with word boundary.
    """
    if not posts or not player_name_map:
        return {}

    socials = player_socials or {}

    entries: list[tuple[int, str, str, str]] = []
    for pid, name in player_name_map.items():
        full = _normalize(name)
        parts = full.split()
        last = parts[-1] if parts else ""
        ig_handle = _normalize(socials.get(pid, {}).get("instagram") or "")
        entries.append((pid, full, last, ig_handle))

    last_counts = Counter(last for _, _, last, _ in entries)

    by_player: dict[int, list[dict[str, Any]]] = {}
    for post in posts:
        text_norm = _normalize(post.get("title", ""))
        post_username = _normalize(post.get("username", ""))
        for pid, full_name, last_name, ig_handle in entries:
            matched = False
            if ig_handle and post_username == ig_handle:
                matched = True
            elif full_name in text_norm:
                matched = True
            elif len(last_name) >= 5 and last_counts[last_name] == 1:
                if re.search(r"\b" + re.escape(last_name) + r"\b", text_norm):
                    matched = True
            if matched:
                by_player.setdefault(pid, []).append(post)

    return by_player


def _normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).lower().strip()


def _coerce_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
