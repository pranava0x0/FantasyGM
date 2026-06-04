"""Fetch WNBA player projections from CBS Sports and Yahoo Sports.

Both scrapers return a dict mapping **lowercase player name → projected
fantasy points per game** so the caller can join on name to ESPN player IDs.

Design constraints:
- No new pip dependencies (httpx is already present; stdlib html.parser).
- Fail gracefully: network errors, missing tables, or changed page structure
  return an empty dict and log a warning — they never crash the pipeline.
- Rate-limit: 2 s between requests to each host (CLAUDE.md §network-ethics).
- Cache: callers should pass the raw_dir so we can persist and re-use within
  the same run.

Both scrapers return per-game *projected* stats that are then converted to
fantasy points using the league's ScoringFormula. When a source returns
season-average stats instead of projections (Yahoo), those serve as a
reasonable stand-in.
"""

from __future__ import annotations

import logging
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from pipeline.scoring_formula import ScoringFormula

log = logging.getLogger(__name__)

_REQUEST_DELAY = 2.0  # seconds between requests to the same host
_TIMEOUT = httpx.Timeout(15.0, connect=8.0)
_HEADERS = {
    "User-Agent": "FantasyGM/0.1 (local; +github.com/pranava0x0/FantasyGM)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# ----- public surface --------------------------------------------------------

def fetch_all_external(
    scoring_formula: ScoringFormula,
    *,
    cache_dir: Path | None = None,
) -> dict[str, dict[str, float]]:
    """Return {source_name: {player_name_lower: per_game_fpts}} for each live source.

    Sources that fail are omitted (not None). Callers iterate over whatever
    came back.  `cache_dir` writes each source's raw HTML to disk so a
    re-run on the same day avoids re-fetching.
    """
    results: dict[str, dict[str, float]] = {}

    cbs = _fetch_cbs(scoring_formula, cache_dir=cache_dir)
    if cbs:
        results["cbs"] = cbs
        log.info("projections_ext: CBS Sports returned %d player projections", len(cbs))

    yahoo = _fetch_yahoo(scoring_formula, cache_dir=cache_dir)
    if yahoo:
        results["yahoo"] = yahoo
        log.info("projections_ext: Yahoo Sports returned %d player stats", len(yahoo))

    return results


def resolve_external_projections(
    player_id_to_name: dict[int, str],
    external_by_source: dict[str, dict[str, float]],
) -> dict[int, dict[str, float]]:
    """Map ESPN player IDs to per-source per-game projection values.

    Returns {player_id: {source_name: per_game_fpts}}.
    Name matching is case-insensitive fuzzy: try exact, then
    first+last initial, then last-name-only as fallback.
    """
    out: dict[int, dict[str, float]] = {}
    for pid, full_name in player_id_to_name.items():
        name_lower = full_name.lower().strip()
        row: dict[str, float] = {}
        for source, proj_map in external_by_source.items():
            val = _name_lookup(name_lower, proj_map)
            if val is not None:
                row[source] = val
        if row:
            out[pid] = row
    return out


# ----- CBS Sports ------------------------------------------------------------

_CBS_URL = "https://www.cbssports.com/fantasy/basketball/stats/players/wnba/weekly/stats/FullSeason/"
_CBS_CACHE_FILE = "cbs_projections.html"

# Column names CBS Sports uses in their stats table (varies; we try multiple).
# We need FGM, 3PM, FTM, REB, AST, STL to apply the scoring formula.
_CBS_STAT_COLS = {
    "fg": "FGM",
    "fgm": "FGM",
    "3pt": "3PM",
    "3pm": "3PM",
    "ft": "FTM",
    "ftm": "FTM",
    "reb": "REB",
    "ast": "AST",
    "stl": "STL",
}


class _TableParser(HTMLParser):
    """Minimal HTML table parser that extracts the first <table> it finds."""

    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_row: list[str] = []
        self.current_cell: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table" and not self.in_table:
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.in_row = True
            self.current_row = []
        elif tag in ("td", "th") and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.in_table:
            self.in_table = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if self.current_row:
                self.rows.append(self.current_row)
        elif tag in ("td", "th") and self.in_cell:
            self.in_cell = False
            self.current_row.append(" ".join(self.current_cell).strip())

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            text = data.strip()
            if text:
                self.current_cell.append(text)


def _fetch_cbs(
    scoring_formula: ScoringFormula,
    *,
    cache_dir: Path | None = None,
) -> dict[str, float]:
    """Scrape CBS Sports WNBA weekly stats and convert to fantasy points per game.

    Returns {} on any failure — the pipeline proceeds with other sources.
    """
    html = _get_html(_CBS_URL, cache_dir=cache_dir, cache_file=_CBS_CACHE_FILE)
    if not html:
        return {}
    try:
        return _parse_cbs_html(html, scoring_formula)
    except Exception as exc:
        log.warning("projections_ext: CBS parse failed: %s", exc, exc_info=True)
        return {}


def _parse_cbs_html(html: str, formula: ScoringFormula) -> dict[str, float]:
    """Extract player stats from CBS Sports WNBA stats table.

    Returns {player_name_lower: per_game_fpts}.
    """
    parser = _TableParser()
    parser.feed(html)
    if not parser.rows:
        # No table found — CBS may have restructured or requires JS rendering.
        # Try JSON-LD or inline data as fallback.
        return _parse_cbs_json_fallback(html, formula)

    rows = parser.rows
    if len(rows) < 2:
        log.debug("projections_ext: CBS table has fewer than 2 rows, skipping")
        return {}

    # First row is header — normalize to lowercase.
    header = [h.lower().replace(" ", "").replace("/", "") for h in rows[0]]
    log.debug("projections_ext: CBS header: %s", header)

    # Locate the player-name column and stat columns.
    name_col = _find_col(header, ["player", "name"])
    if name_col is None:
        log.debug("projections_ext: CBS no player-name column found in header %s", header)
        return {}

    stat_col_map: dict[str, int] = {}  # stat abbrev → column index
    for i, h in enumerate(header):
        mapped = _CBS_STAT_COLS.get(h)
        if mapped and mapped not in stat_col_map:
            stat_col_map[mapped] = i

    if not stat_col_map:
        log.debug("projections_ext: CBS no recognizable stat columns in header")
        return {}

    results: dict[str, float] = {}
    for row in rows[1:]:
        if len(row) <= name_col:
            continue
        player_name = _clean_name(row[name_col])
        if not player_name:
            continue
        box: dict[str, float] = {}
        for stat, col_i in stat_col_map.items():
            if col_i < len(row):
                try:
                    box[stat] = float(row[col_i])
                except (ValueError, TypeError):
                    pass
        if box:
            fpts = formula.compute_from_box_stats(box)
            if fpts > 0:
                results[player_name] = fpts

    log.debug("projections_ext: CBS parsed %d player rows", len(results))
    return results


def _parse_cbs_json_fallback(html: str, formula: ScoringFormula) -> dict[str, float]:
    """Try to extract inline JSON player data when CBS uses a JS-rendered table."""
    # CBS sometimes embeds player data as a JSON blob in a <script> tag.
    match = re.search(r'"players"\s*:\s*(\[.*?\])', html, re.DOTALL)
    if not match:
        return {}
    try:
        import json
        players = json.loads(match.group(1))
    except Exception:
        return {}

    results: dict[str, float] = {}
    for p in players:
        name = _clean_name(str(p.get("fullName") or p.get("name") or ""))
        if not name:
            continue
        stats = p.get("stats") or p.get("averages") or {}
        box: dict[str, float] = {}
        for key, val in stats.items():
            mapped = _CBS_STAT_COLS.get(key.lower().replace(" ", ""))
            if mapped:
                try:
                    box[mapped] = float(val)
                except (TypeError, ValueError):
                    pass
        if box:
            fpts = formula.compute_from_box_stats(box)
            if fpts > 0:
                results[name] = fpts
    return results


# ----- Yahoo Sports ----------------------------------------------------------

_YAHOO_URL = "https://sports.yahoo.com/wnba/stats/weekly/?date=2026&gtype=reg&pos=players"
_YAHOO_CACHE_FILE = "yahoo_stats.html"

# Yahoo uses slightly different column names.
_YAHOO_STAT_COLS = {
    "fgm": "FGM",
    "fg": "FGM",
    "3ptm": "3PM",
    "3pm": "3PM",
    "3p": "3PM",
    "ftm": "FTM",
    "ft": "FTM",
    "reb": "REB",
    "r": "REB",
    "ast": "AST",
    "a": "AST",
    "stl": "STL",
    "st": "STL",
}


def _fetch_yahoo(
    scoring_formula: ScoringFormula,
    *,
    cache_dir: Path | None = None,
) -> dict[str, float]:
    """Scrape Yahoo Sports WNBA season stats and convert to fantasy points per game.

    Yahoo's public stats tables don't include FGM/3PM/FTM breakdowns on all
    pages, so results may be approximate. Returns {} on any failure.
    """
    html = _get_html(_YAHOO_URL, cache_dir=cache_dir, cache_file=_YAHOO_CACHE_FILE)
    if not html:
        return {}
    try:
        return _parse_yahoo_html(html, scoring_formula)
    except Exception as exc:
        log.warning("projections_ext: Yahoo parse failed: %s", exc, exc_info=True)
        return {}


def _parse_yahoo_html(html: str, formula: ScoringFormula) -> dict[str, float]:
    """Extract per-game averages from Yahoo Sports WNBA stats table."""
    parser = _TableParser()
    parser.feed(html)
    if not parser.rows:
        log.debug("projections_ext: Yahoo no table found (likely JS-rendered)")
        return {}

    rows = parser.rows
    if len(rows) < 2:
        return {}

    header = [h.lower().strip() for h in rows[0]]
    name_col = _find_col(header, ["player", "name"])
    if name_col is None:
        log.debug("projections_ext: Yahoo no player column in %s", header)
        return {}

    stat_col_map: dict[str, int] = {}
    for i, h in enumerate(header):
        mapped = _YAHOO_STAT_COLS.get(h)
        if mapped and mapped not in stat_col_map:
            stat_col_map[mapped] = i

    if not stat_col_map:
        log.debug("projections_ext: Yahoo no recognizable stat columns")
        return {}

    results: dict[str, float] = {}
    for row in rows[1:]:
        if len(row) <= name_col:
            continue
        player_name = _clean_name(row[name_col])
        if not player_name:
            continue
        box: dict[str, float] = {}
        for stat, col_i in stat_col_map.items():
            if col_i < len(row):
                try:
                    box[stat] = float(row[col_i])
                except (ValueError, TypeError):
                    pass
        if box:
            fpts = formula.compute_from_box_stats(box)
            if fpts > 0:
                results[player_name] = fpts

    log.debug("projections_ext: Yahoo parsed %d player rows", len(results))
    return results


# ----- shared utilities ------------------------------------------------------

def _get_html(url: str, *, cache_dir: Path | None, cache_file: str) -> str | None:
    """Fetch URL with caching and graceful error handling."""
    if cache_dir is not None:
        cache_path = cache_dir / cache_file
        if cache_path.exists():
            log.debug("projections_ext: cache hit %s", cache_path)
            return cache_path.read_text(encoding="utf-8", errors="replace")

    try:
        time.sleep(_REQUEST_DELAY)
        with httpx.Client(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            log.warning("projections_ext: %s returned HTTP %d", url, resp.status_code)
            return None
        html = resp.text
    except httpx.HTTPError as exc:
        log.warning("projections_ext: request to %s failed: %s", url, exc)
        return None

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / cache_file).write_text(html, encoding="utf-8")

    return html


def _find_col(header: list[str], candidates: list[str]) -> int | None:
    """Return index of first column whose normalized name is in candidates."""
    for i, h in enumerate(header):
        if h in candidates:
            return i
    # Partial match fallback.
    for i, h in enumerate(header):
        if any(c in h for c in candidates):
            return i
    return None


_SUFFIX_RE = re.compile(r"\b(jr\.?|sr\.?|ii|iii|iv)\s*$", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def _clean_name(raw: str) -> str:
    """Normalize a player name to lowercase, no extra whitespace, no suffixes."""
    name = raw.strip()
    # Strip team abbreviations that sometimes trail the name (e.g. "A. Wilson LV").
    name = re.sub(r"\s+[A-Z]{2,4}\s*$", "", name)
    # Drop position designators that may trail (e.g. "Player Name PG").
    name = re.sub(r"\s+(PG|SG|SF|PF|C|G|F)\s*$", "", name)
    name = _SUFFIX_RE.sub("", name)
    name = _WHITESPACE_RE.sub(" ", name).strip().lower()
    return name


def _name_lookup(name_lower: str, proj_map: dict[str, float]) -> float | None:
    """Look up a player projection with exact → last-name-only fallback."""
    if name_lower in proj_map:
        return proj_map[name_lower]
    # Try last-name + first-initial: "brittney griner" → "b. griner"
    parts = name_lower.split()
    if len(parts) >= 2:
        initial_key = f"{parts[0][0]}. {' '.join(parts[1:])}"
        if initial_key in proj_map:
            return proj_map[initial_key]
        # Last-name only (common when external source abbreviates first name).
        last_name = parts[-1]
        candidates = {k: v for k, v in proj_map.items() if k.split()[-1] == last_name}
        if len(candidates) == 1:
            return next(iter(candidates.values()))
    return None
