"""Offline NFL refresh and large-league time, allocation and payload budgets."""
from __future__ import annotations

import gzip
import json
import logging
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.football import League, Player, Team, analyze, refresh

log = logging.getLogger(__name__)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    now = datetime.now(timezone.utc)
    players = {str(i): Player(id=str(i), name="Player " + str(i), positions=["WR"], team="BUF", adds=i) for i in range(10000)}
    league = League(id="stress-1",provider="sleeper",name="Stress",season=str(now.year),captured_at=now.isoformat(),source_url="https://sleeper.com",slots=["WR","WR","FLEX","BN"],availability_complete=True,teams=[Team(id=str(i),players=[str(i)]) for i in range(32)])
    tracemalloc.start()
    start = time.perf_counter()
    result = analyze(league, players, now)
    stress_seconds = time.perf_counter() - start
    _, stress_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert sum(len(t["waivers"]) for t in result["teams"].values()) <= 32 * 30
    assert stress_peak < 64 * 1024 * 1024
    assert stress_seconds < 30
    tracemalloc.start()
    start = time.perf_counter()
    index = refresh(root, offline=True)
    offline_seconds = time.perf_counter() - start
    _, offline_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    files = list((root / "docs/data/football").glob("*.json"))
    payloads = {p.name: {"bytes": p.stat().st_size, "gzip_bytes": len(gzip.compress(p.read_bytes()))} for p in files}
    assert all(p["gzip_bytes"] < 200000 for p in payloads.values())
    # First paint loads registry, shared data and exactly one league.
    report = {"stress_players": len(players), "stress_teams": len(league.teams), "stress_seconds": round(stress_seconds,3), "stress_peak_mib": round(stress_peak / 1048576,2), "offline_seconds": round(offline_seconds,3), "offline_peak_mib": round(offline_peak / 1048576,2), "offline_network_requests": index["requests"], "payloads": payloads}
    log.info("%s", json.dumps(report, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
