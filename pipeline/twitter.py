"""Twitter/X signal layer.

Primary path: reads `twitter_raw.json` written by the refresh skill's Chrome
scraping step (using the user's already-logged-in browser — no credentials
needed in this file).

Fallback path: if the raw file is absent, falls back to the internal search
API using session cookies (TWITTER_AUTH_TOKEN + TWITTER_CT0 in .env).

The raw file format is a JSON array of objects:
    [{"title": "<tweet text>", "url": "https://x.com/…/status/…",
      "screen_name": "@handle", "published_at": "<ISO-8601 or null>"}, …]

The Chrome scraping step in the skill writes this file automatically before
`pipeline.refresh` is invoked.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SEARCH_URL = "https://twitter.com/i/api/1.1/search/tweets.json"
_RAW_FILENAME = "twitter_raw.json"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_tweets(raw_dir: Path | None = None, query: str = "wnba", limit: int = 50) -> list[dict[str, Any]]:
    """Return normalized tweet dicts, trying sources in order:

    1. `raw_dir/twitter_raw.json` — written by the skill's Chrome step.
    2. Internal search API with session cookies (TWITTER_AUTH_TOKEN + TWITTER_CT0).
    3. [] if both fail / are unconfigured.
    """
    if raw_dir is not None:
        tweets = _load_from_file(raw_dir / _RAW_FILENAME)
        if tweets:
            log.info("twitter: loaded %d tweets from %s", len(tweets), raw_dir / _RAW_FILENAME)
            return tweets
        log.debug("twitter: %s not found or empty — trying HTTP fallback", raw_dir / _RAW_FILENAME)

    return _fetch_via_http(query=query, limit=limit)


# ---------------------------------------------------------------------------
# Source 1: pre-scraped file (Chrome step writes this)
# ---------------------------------------------------------------------------

def _load_from_file(path: Path) -> list[dict[str, Any]]:
    """Load tweets from the pre-scraped JSON file.

    URLs are preserved exactly as written by the Chrome step — the skill builds
    canonical `https://x.com/<handle>/status/<id>` paths from DOM pathname
    attributes, which are the real permanent links (no tracking query params).
    This function never strips or rewrites URLs.
    """
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("twitter: failed to read %s (%s)", path, e)
        return []
    if not isinstance(raw, list):
        log.warning("twitter: %s is not a JSON array — skipping", path)
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or item.get("text") or "").strip()
        if not title:
            continue
        published_at = _coerce_dt(item.get("published_at"))
        out.append({
            "title": title,
            "url": item.get("url") or "",   # preserved as-is from twitter_raw.json
            "published_at": published_at,
            "screen_name": (item.get("screen_name") or "").lstrip("@"),
        })
    return out


# ---------------------------------------------------------------------------
# Source 2: internal search API (cookie fallback)
# ---------------------------------------------------------------------------

def _credentials() -> tuple[str, str, str] | None:
    """Return (bearer, auth_token, ct0) from env, or None if any are unset."""
    bearer = os.environ.get("TWITTER_BEARER", "").strip()
    auth_token = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
    ct0 = os.environ.get("TWITTER_CT0", "").strip()
    if bearer and auth_token and ct0:
        return bearer, auth_token, ct0
    return None


def _fetch_via_http(query: str = "wnba", limit: int = 50) -> list[dict[str, Any]]:
    """Hit twitter.com/i/api/1.1/search/tweets.json with session cookies."""
    creds = _credentials()
    if creds is None:
        log.info(
            "twitter: HTTP fallback skipped — "
            "TWITTER_BEARER / TWITTER_AUTH_TOKEN / TWITTER_CT0 not all set in .env"
        )
        return []

    bearer, auth_token, ct0 = creds
    params = urllib.parse.urlencode({
        "q": query,
        "result_type": "recent",
        "count": min(limit, 100),
        "tweet_mode": "extended",
        "lang": "en",
    })
    url = f"{_SEARCH_URL}?{params}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {bearer}")
    req.add_header("x-csrf-token", ct0)
    req.add_header("Cookie", f"auth_token={auth_token}; ct0={ct0}")
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    req.add_header("Referer", "https://twitter.com/search")
    req.add_header("x-twitter-active-user", "yes")
    req.add_header("x-twitter-auth-type", "OAuth2Session")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            log.warning(
                "twitter: 401 — session cookies are expired. "
                "Re-run skill so the Chrome step refreshes twitter_raw.json, "
                "or update TWITTER_AUTH_TOKEN/TWITTER_CT0 in .env."
            )
        elif e.code == 429:
            log.warning("twitter: 429 rate-limited — skipping this run")
        else:
            log.warning("twitter: HTTP %d — skipping", e.code)
        return []
    except urllib.error.URLError as e:
        log.warning("twitter: fetch failed (%s) — skipping", e)
        return []

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        log.warning("twitter: JSON parse failed (%s)", e)
        return []

    out: list[dict[str, Any]] = []
    for tw in data.get("statuses") or []:
        text = (tw.get("full_text") or tw.get("text") or "").strip()
        if not text:
            continue
        tweet_id = tw.get("id_str") or str(tw.get("id") or "")
        user = tw.get("user") or {}
        screen_name = user.get("screen_name") or ""
        url = (
            f"https://twitter.com/{screen_name}/status/{tweet_id}"
            if screen_name and tweet_id else ""
        )
        out.append({
            "title": text,
            "url": url,
            "published_at": _parse_twitter_date(tw.get("created_at")),
            "screen_name": screen_name,
        })
    return out


# ---------------------------------------------------------------------------
# Player matching (mirrors reddit.match_to_players)
# ---------------------------------------------------------------------------

def match_to_players(
    tweets: list[dict[str, Any]],
    player_name_map: dict[int, str],
) -> dict[int, list[dict[str, Any]]]:
    """Return {player_id: [tweet, ...]} for tweets whose text mentions the player."""
    if not tweets or not player_name_map:
        return {}

    entries: list[tuple[int, str, str]] = []
    for pid, name in player_name_map.items():
        full = _normalize(name)
        parts = full.split()
        last = parts[-1] if parts else ""
        entries.append((pid, full, last))

    last_counts = Counter(last for _, _, last in entries)

    by_player: dict[int, list[dict[str, Any]]] = {}
    for tweet in tweets:
        text_norm = _normalize(tweet.get("title", ""))
        for pid, full_name, last_name in entries:
            matched = False
            if full_name in text_norm:
                matched = True
            elif len(last_name) >= 5 and last_counts[last_name] == 1:
                if re.search(r"\b" + re.escape(last_name) + r"\b", text_norm):
                    matched = True
            if matched:
                by_player.setdefault(pid, []).append(tweet)

    return by_player


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).lower().strip()


def _parse_twitter_date(s: str | None) -> datetime | None:
    """Parse Twitter's `created_at` format: 'Thu Jun 01 12:00:00 +0000 2026'."""
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except Exception:
        return None


def _coerce_dt(value: Any) -> datetime | None:
    """Accept ISO-8601 string, Twitter date string, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    return _parse_twitter_date(s)
