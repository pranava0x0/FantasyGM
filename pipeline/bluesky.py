"""Bluesky signal layer.

Primary path: reads `bluesky_raw.json` written by the refresh skill's Chrome
scraping step (per-player bsky.app searches using the user's logged-in browser).

Bluesky's public AT Protocol API has returned 403 in testing; Chrome scraping
is more reliable and produces richer per-player results anyway.

The raw file format is a JSON array:
    [{"title": "<post text>", "url": "https://bsky.app/profile/handle/post/id",
      "handle": "<handle>", "published_at": "<ISO-8601 or null>"}, ...]
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

_RAW_FILENAME = "bluesky_raw.json"


def load_bluesky(raw_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load Bluesky posts from the pre-scraped file. Returns [] if absent."""
    if raw_dir is None:
        return []
    path = raw_dir / _RAW_FILENAME
    if not path.exists():
        log.debug("bluesky: %s not found — skipping", path)
        return []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("bluesky: failed to read %s (%s)", path, e)
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        if not title:
            continue
        pub = _coerce_dt(item.get("published_at"))
        out.append({
            "title": title,
            "url": item.get("url") or "",
            "published_at": pub,
            "handle": (item.get("handle") or "").lstrip("@"),
        })
    log.info("bluesky: loaded %d posts from %s", len(out), path)
    return out


def match_to_players(
    posts: list[dict[str, Any]],
    player_name_map: dict[int, str],
) -> dict[int, list[dict[str, Any]]]:
    """Return {player_id: [post, ...]} for posts whose text mentions the player."""
    if not posts or not player_name_map:
        return {}

    entries: list[tuple[int, str, str]] = []
    for pid, name in player_name_map.items():
        full = _normalize(name)
        parts = full.split()
        last = parts[-1] if parts else ""
        entries.append((pid, full, last))

    last_counts = Counter(last for _, _, last in entries)

    by_player: dict[int, list[dict[str, Any]]] = {}
    for post in posts:
        text_norm = _normalize(post.get("title", ""))
        for pid, full_name, last_name in entries:
            matched = False
            if full_name in text_norm:
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
