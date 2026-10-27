"""Multi-league correctness and privacy boundaries, without real accounts."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from pipeline.espn_client import ESPNClient, ESPNCredentials
from pipeline.multileague import (
    NBA_POINTS, browser_task, exact_assignment, injury_changes,
    refresh_nba, refresh_sleeper, score_projection,
)


def test_exact_assignment_preserves_specialist_slot() -> None:
    players = [{"id": "flexible", "eligible": ["PG", "SG"], "value": 30},
               {"id": "specialist", "eligible": ["PG"], "value": 29},
               {"id": "backup", "eligible": ["SG"], "value": 10}]
    assert exact_assignment(players, ["PG", "SG"]) == {0: "specialist", 1: "flexible"}


def test_locked_starter_stays_and_locked_bench_cannot_start() -> None:
    players = [{"id": "started", "eligible": ["RB", "FLEX"], "value": 1,
                "locked": True, "current_slot": 0},
               {"id": "bench_locked", "eligible": ["RB", "FLEX"], "value": 100,
                "locked": True},
               {"id": "available", "eligible": ["FLEX"], "value": 20}]
    assert exact_assignment(players, ["RB", "FLEX"]) == {0: "started", 1: "available"}


def test_solver_handles_duplicate_slots_and_preserves_ties() -> None:
    players = [{"id": "a", "eligible": ["WR"], "value": 20, "current_slot": 1},
               {"id": "b", "eligible": ["WR"], "value": 20, "current_slot": 0},
               {"id": "c", "eligible": ["WR"], "value": 20}]
    assert exact_assignment(players, ["WR", "WR"]) == {0: "b", 1: "a"}


def test_solver_excludes_out_ir_and_unknown_projections() -> None:
    players = [{"id": "out", "eligible": ["C"], "value": 80, "available": False},
               {"id": "unknown", "eligible": ["C"], "value": None},
               {"id": "zero", "eligible": ["C"], "value": 0}]
    assert exact_assignment(players, ["C"]) == {}
    assert exact_assignment([], []) == {}


def test_solver_rejects_ambiguous_locked_slots() -> None:
    with pytest.raises(ValueError, match="locked"):
        exact_assignment([{"id": str(i), "locked": True, "current_slot": 0} for i in range(2)], ["QB"])


def test_scoring_uses_league_weights() -> None:
    stats = {"pass_yd": 250, "pass_td": 2, "pass_int": 2, "rec": 4}
    weights = {"pass_yd": .04, "pass_td": 4, "pass_int": -2, "rec": .5}
    assert score_projection(stats, weights) == 16
    assert score_projection(stats, {**weights, "pass_int": -1}) == 18
    assert score_projection({"0": 25, "13": 8, "14": 17, "15": 6, "16": 7,
                             "17": 3, "6": 5, "3": 8, "1": 2, "2": 1, "11": 3}, NBA_POINTS) == 53


def test_injury_changes_are_scoped_by_platform_and_league() -> None:
    old = {"leagues": [{"key": "nba", "players": [{"id": "1", "injury_status": "ACTIVE"}]},
                       {"key": "nfl", "players": [{"id": "1", "injury_status": "OUT"}]}]}
    new = {"leagues": [{"key": "nba", "players": [{"id": "1", "name": "NBA player", "injury_status": "OUT"}]},
                       {"key": "nfl", "players": [{"id": "1", "name": "NFL player", "injury_status": "OUT"}]}]}
    assert injury_changes(old, new) == [{"league": "nba", "player": "NBA player", "before": "ACTIVE", "after": "OUT"}]


def test_espn_game_code_keeps_default_and_selects_nba() -> None:
    urls = []

    def handle(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={})

    for code in ["wfba", "fba"]:
        with ESPNClient(12, 2027, ESPNCredentials("test", "test"), game_code=code,
                        client=httpx.Client(transport=httpx.MockTransport(handle))) as client:
            client.fetch_league(["mSettings"])
            client.fetch_pro_team_schedules()
    assert "/games/wfba/" in urls[0]
    assert "/games/fba/" in urls[2] and "/games/fba/" in urls[3]


def test_sleeper_does_not_guess_or_save_owners() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rosters"):
            return httpx.Response(200, json=[{"owner_id": "private", "roster_id": 3,
                                             "players": ["p"], "starters": ["p"]}])
        return httpx.Response(200, json={"name": "League", "sport": "nfl", "season": "2026",
                                         "roster_positions": ["QB", "BN"],
                                         "scoring_settings": {"pass_td": 4}, "settings": {"leg": 7}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = refresh_sleeper(client, "123", None, None)
    assert result["status"] == "needs_roster_identity"
    assert result["roster_id"] is None
    assert "owner_id" not in json.dumps(result) and "private" not in json.dumps(result)


def test_sleeper_partial_projections_remain_explicit() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/rosters"):
            data: Any = [{"owner_id": "private", "roster_id": 3, "players": ["p", "q"], "starters": ["p"], "reserve": ["q"]}]
        elif "/projections/" in path:
            data = [{"player_id": "p", "stats": {"gp": 1, "pass_td": 2}}]
        elif path.endswith("/players/nfl"):
            data = {"p": {"full_name": "QB player", "fantasy_positions": ["QB"]},
                    "q": {"full_name": "IR player", "fantasy_positions": ["QB"], "injury_status": "OUT"}}
        else:
            data = {"name": "PRIVATE_LEAGUE", "sport": "nfl", "season": "2026", "roster_positions": ["QB", "BN"],
                    "scoring_settings": {"pass_td": 4, "missing_bonus": 2},
                    "settings": {"leg": 7, "custom_owner_field": "PRIVATE_OWNER"}}
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = refresh_sleeper(client, "123", 8, "private")
    assert result["roster_id"] == 3 and result["week"] == 8
    assert result["unprojected_scoring_components"] == ["missing_bonus"]
    assert result["players"][0]["projected_value"] == 8
    assert not result["players"][1]["available"]
    assert result["players"][1]["projected_value"] is None
    assert "private" not in json.dumps(result)
    assert "PRIVATE_" not in json.dumps(result)
    assert result["label"] == "Sleeper league 123"
    assert result["rules"]["settings"] == {"leg": 7}


def test_nba_counts_games_per_eastern_date_and_excludes_ir(monkeypatch: pytest.MonkeyPatch) -> None:
    # 00:30 UTC on Oct 28 is still Oct 27 in the league's Eastern timezone.
    tip = 1793147400000
    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            assert kwargs["game_code"] == "fba"

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def fetch_league(self, views: list[str]) -> dict[str, Any]:
            entries = [{"lineupSlotId": slot, "playerPoolEntry": {"player": {
                "id": pid, "fullName": f"Player {pid}", "proTeamId": 1, "eligibleSlots": [0, 11],
                "stats": [{"statSourceId": 1, "seasonId": 2027, "statSplitTypeId": 0,
                           "stats": {"0": points, "42": 10}}]}}} for pid, slot, points in [(1, 12, 100), (2, 13, 1000)]]
            return {"teams": [{"id": 2, "roster": {"entries": entries}}], "settings": {
                "name": "NBA", "scoringSettings": {"scoringType": "H2H_CATEGORY"},
                "rosterSettings": {"lineupSlotCounts": {"0": 1, "12": 5, "13": 2}},
                "scheduleSettings": {}, "acquisitionSettings": {}}}

        def fetch_pro_team_schedules(self) -> dict[str, Any]:
            return {"settings": {"proTeams": [{"id": 1, "proGamesByScoringPeriod": {"8": [
                {"id": 1, "date": tip, "scoringPeriodId": 8}]}}]}}

    monkeypatch.setattr("pipeline.multileague.ESPNClient", FakeClient)
    result = refresh_nba(ESPNCredentials("test", "test"), "2026-10-27", 2)
    assert result["daily_plan"][0]["assignments"][0]["player_id"] == "1"
    assert result["daily_plan"][0]["projected_points"] == 10
    assert result["daily_plan"][1]["assignments"] == []
    assert "proxy" in result["objective"]


def test_browser_task_preserves_scope_and_requires_verification() -> None:
    task = browser_task({"captured_at": "test", "leagues": []})
    assert "do not schedule" in task
    assert "Do not add/drop/trade" in task
    assert "reload and verify persistence" in task


def test_browser_task_ignores_private_legacy_labels() -> None:
    task = browser_task({"captured_at": "test", "leagues": [
        {"key": "sleeper:123", "sport": "nfl", "roster_id": 9, "name": "PRIVATE_TEAM",
         "url": "https://sleeper.com/leagues/123/team", "status": "test"},
        {"key": "espn:fba:456", "sport": "nba", "team_id": 2, "name": "PRIVATE_LEAGUE",
         "url": "https://fantasy.espn.com/", "status": "test"},
    ]})
    assert "PRIVATE_" not in task
    assert "Sleeper league 123 / roster 9" in task
    assert "ESPN league 456 / team 2" in task
