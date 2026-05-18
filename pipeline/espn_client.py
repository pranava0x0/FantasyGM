"""Thin HTTP client over the ESPN private fantasy API.

Endpoints live at lm-api-reads.fantasy.espn.com. The game code for WNBA is
`wfba` (not `wbasketball` as the public URL suggests).

The league is private, so every call carries the user's `SWID` + `espn_s2`
session cookies. Cookies come from the environment — never logged, never
written to disk.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE_URL_TMPL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/wfba"
    "/seasons/{season}/segments/0/leagues/{league_id}"
)
SEASON_URL_TMPL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/wfba/seasons/{season}"
)

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 4
BACKOFF_BASE_S = 1.5


class ESPNAuthError(RuntimeError):
    """Raised when ESPN returns 401/403 — usually expired cookies."""


class ESPNAPIError(RuntimeError):
    """Raised on non-retryable error responses."""


@dataclass(frozen=True)
class ESPNCredentials:
    """Session cookies needed to authenticate against the private API."""
    swid: str
    espn_s2: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ESPNCredentials":
        env = env if env is not None else os.environ
        swid = env.get("ESPN_SWID", "").strip()
        s2 = env.get("ESPN_S2", "").strip()
        if not swid or not s2:
            raise ESPNAuthError(
                "Missing ESPN_SWID and/or ESPN_S2 env vars. See .env.example."
            )
        if not swid.startswith("{") or not swid.endswith("}"):
            raise ESPNAuthError(
                "ESPN_SWID must be wrapped in curly braces, e.g. {ABCD-...-XYZ}. "
                "Re-copy it from Chrome DevTools."
            )
        return cls(swid=swid, espn_s2=s2)


class ESPNClient:
    """Small, retrying HTTP client for one league + season."""

    def __init__(
        self,
        league_id: int,
        season: int,
        creds: ESPNCredentials,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.league_id = league_id
        self.season = season
        self._creds = creds
        self._base_url = BASE_URL_TMPL.format(season=season, league_id=league_id)
        self._season_url = SEASON_URL_TMPL.format(season=season)
        self._client = client or httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            headers={
                "Accept": "application/json",
                "User-Agent": "FantasyGM/0.1 (local; +github.com/pranava0x0/FantasyGM)",
            },
            cookies={"SWID": creds.swid, "espn_s2": creds.espn_s2},
        )

    # --- public surface --------------------------------------------------

    def fetch_league(self, views: list[str]) -> dict[str, Any]:
        """Fetch the league with one or more `view` filters."""
        params = [("view", v) for v in views]
        return self._request("GET", self._base_url, params=params)

    def fetch_pro_team_schedules(self) -> dict[str, Any]:
        """Fetch the season-level pro-team schedule view.

        This view (`proTeamSchedules_wl`) is *only* served at the season
        endpoint — the league endpoint silently returns `settings: {name}`
        and zero proTeams. Discovered 2026-05-17 while wiring game-count
        weighting; see CLAUDE.md scar tissue.
        """
        return self._request(
            "GET", self._season_url, params=[("view", "proTeamSchedules_wl")]
        )

    def fetch_free_agents(
        self,
        scoring_period_id: int,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Fetch the free-agent / waiver pool ranked by % ownership.

        Uses ESPN's `kona_player_info` view + the `X-Fantasy-Filter` JSON
        header which is how the fantasy site itself paginates and sorts.
        """
        x_fantasy_filter = {
            "players": {
                "filterStatus": {
                    "value": ["FREEAGENT", "WAIVERS"],
                },
                "limit": limit,
                "offset": 0,
                "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
                "filterStatsForTopScoringPeriodIds": {
                    "value": 5,
                    "additionalValue": [
                        f"00{self.season}",
                        f"10{self.season}",
                        f"02{self.season}",
                        f"01{self.season}",
                    ],
                },
            }
        }
        headers = {"X-Fantasy-Filter": json.dumps(x_fantasy_filter, separators=(",", ":"))}
        params = [
            ("view", "kona_player_info"),
            ("scoringPeriodId", str(scoring_period_id)),
        ]
        return self._request("GET", self._base_url, params=params, headers=headers)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ESPNClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # --- internals -------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: list[tuple[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._client.request(method, url, params=params, headers=headers)
            except httpx.HTTPError as e:
                last_exc = e
                wait = BACKOFF_BASE_S ** attempt
                log.warning("HTTP error on %s %s (attempt %d/%d): %s — retrying in %.1fs",
                            method, url, attempt, MAX_RETRIES, e, wait)
                time.sleep(wait)
                continue

            if resp.status_code in (401, 403):
                # No retry — cookies are bad.
                raise ESPNAuthError(
                    f"ESPN returned {resp.status_code}. Your SWID/espn_s2 cookies "
                    "are likely expired. Re-copy them from Chrome DevTools and "
                    "update .env."
                )

            if resp.status_code in RETRYABLE_STATUS:
                wait = BACKOFF_BASE_S ** attempt
                log.warning("ESPN returned %d on %s (attempt %d/%d) — retrying in %.1fs",
                            resp.status_code, url, attempt, MAX_RETRIES, wait)
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                raise ESPNAPIError(
                    f"ESPN returned {resp.status_code} for {url}: {resp.text[:200]}"
                )

            return resp.json()

        # Exhausted retries.
        raise ESPNAPIError(
            f"ESPN request failed after {MAX_RETRIES} attempts: {last_exc!r}"
        )
