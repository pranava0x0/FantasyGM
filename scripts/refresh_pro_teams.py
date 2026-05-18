"""Regenerate the WNBA proTeamId -> abbreviation map.

ESPN sometimes adds expansion teams (Toronto Tempo, Golden State Valkyries,
and Portland Fire were 2026 adds) with new 6-digit `proTeamId` values that
don't follow the NBA-era 1..20 numbering. This script pulls the canonical
list from ESPN's public site API and prints a Python dict you can paste
into `pipeline/build_state.py:WNBA_TEAM_ABBR`.

    python scripts/refresh_pro_teams.py

No auth required — this endpoint is public.
"""

from __future__ import annotations

import json
import sys
import urllib.request

URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams"


def main() -> int:
    req = urllib.request.Request(URL, headers={"User-Agent": "FantasyGM/0.1 refresh_pro_teams"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    leagues = ((data.get("sports") or [{}])[0]).get("leagues") or []
    teams_wrapped = (leagues[0] if leagues else {}).get("teams") or []
    teams = [tw["team"] for tw in teams_wrapped if "team" in tw]
    if not teams:
        print("ERROR: ESPN returned no teams. Endpoint shape may have changed.", file=sys.stderr)
        return 1

    teams.sort(key=lambda t: int(t["id"]))

    print("WNBA_TEAM_ABBR: dict[int, str] = {")
    for t in teams:
        abbr = t.get("abbreviation") or "?"
        display = t.get("displayName") or ""
        print(f"    {int(t['id'])}: {abbr!r},  # {display}")
    print("}")
    print(f"\n# {len(teams)} teams, fetched from {URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
