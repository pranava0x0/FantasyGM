"""Cross-league isolation, source freshness, privacy and bounded NFL analysis."""
from __future__ import annotations

import json
import time
import tracemalloc
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
import pytest
from pydantic import ValidationError
from pipeline.football import Cache, Deadline, League, Player, Team, analyze, points, sanitize_rosters, refresh

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


def league(**kwargs: Any) -> League:
    values = dict(id="sleeper-1", provider="sleeper", name="League", season="2026", captured_at=NOW.isoformat(), source_url="https://sleeper.com/leagues/1", slots=["QB", "WR", "TE", "FLEX", "BN"], settings={"reserve_slots":1,"reserve_allow_out":1}, scoring={"rec":.5}, teams=[Team(id="9",players=["owned"],starters=["owned"])], availability_complete=True)
    values.update(kwargs)
    return League(**values)


def pool() -> dict[str, Player]:
    return {"owned":Player(id="owned",name="Owned",positions=["WR"],team="MIN"),"free":Player(id="free",name="Free",positions=["WR"],team="BUF",adds=100)}


def test_league_ownership_isolated() -> None:
    a=analyze(league(),pool(),NOW)["teams"]["9"]["waivers"]
    b=analyze(league(teams=[Team(id="9",players=["free"])]),pool(),NOW)["teams"]["9"]["waivers"]
    assert [p["player_id"] for p in a] == ["free"]
    assert not b


def test_partial_availability_never_infers_complement() -> None:
    assert not analyze(league(availability_complete=False),pool(),NOW)["teams"]["9"]["waivers"]
    assert analyze(league(availability_complete=False,available_players=["free"]),pool(),NOW)["teams"]["9"]["waivers"]


def test_raw_stats_recomputed_for_each_scoring_rules() -> None:
    p=Player(id="p",name="Player",positions=["WR"],stats={"rec":8},stats_at=NOW.isoformat(),stats_source="https://example.com/stats")
    assert points(p,{"rec":.5},NOW)==4
    assert points(p,{"rec":1},NOW)==8
    assert points(p,{"rec":1,"rec_yd":.1},NOW) is None
    assert points(p,{"rec":1},NOW+timedelta(days=8)) is None


def test_stale_roster_suppresses_advice() -> None:
    result=analyze(league(captured_at=(NOW-timedelta(days=2)).isoformat()),pool(),NOW)
    assert result["stale"]
    assert not result["teams"]["9"]["waivers"]
    assert not result["teams"]["9"]["lineup_moves"]


def test_ir_guidance_is_league_specific() -> None:
    players=pool();players["owned"].status="Out"
    a=analyze(league(),players,NOW)["teams"]["9"]["alerts"][0]
    b=analyze(league(settings={"reserve_slots":0}),players,NOW)["teams"]["9"]["alerts"][0]
    assert a["advice"].startswith("Consider IR")
    assert b["advice"].startswith("Hold")


def test_projection_window_and_lineup_isolation() -> None:
    players=pool()
    l=league(teams=[Team(id="9",players=["owned","free"],starters=["0","owned"])],player_status={"owned":"Out"},projections={"owned":12,"free":14},projection_at=NOW.isoformat(),projection_source="https://example.com",projection_label="Week 5")
    moves=analyze(l,players,NOW)["teams"]["9"]["lineup_moves"]
    assert moves == [{"in":"free","out":"owned","slot":"WR","gain":14.0,"reason":"Bye / unavailable starter"}]
    assert players["owned"].status is None  # Provider overrides cannot contaminate shared status.
    l.projection_at=(NOW-timedelta(days=2)).isoformat()
    stale_projection_moves=analyze(l,players,NOW)["teams"]["9"]["lineup_moves"]
    assert stale_projection_moves and stale_projection_moves[0]["gain"] is None


def test_redaction_uses_whitelist() -> None:
    safe=sanitize_rosters([{"roster_id":9,"owner_id":"private-id","metadata":{"name":"Owner PII"},"players":["p"],"settings":{"waiver_budget_used":10,"name":"Private"}}])
    value=json.dumps(safe)
    assert "private" not in value.lower() and "Owner" not in value


def test_timezone_and_finite_validation() -> None:
    with pytest.raises(ValidationError): Deadline(title="Waiver",at="2026-10-07T03:00",source_url="https://example.com")
    assert Deadline(title="Waiver",at="2026-10-07T03:00-04:00",source_url="https://example.com").at.endswith("-04:00")
    with pytest.raises(ValidationError): league(id="../../escape")
    with pytest.raises(ValidationError): league(projections={"p":float("nan")})
    with pytest.raises(ValidationError): league(owner_name="PII")


def test_faab_zero_preserved() -> None:
    result=analyze(league(settings={"waiver_type":2,"waiver_budget":100},teams=[Team(id="9",players=[],budget_used=100)]),pool(),NOW)
    assert result["teams"]["9"]["faab_remaining"]==0


def test_catalog_cache_shared_and_daily_limit(tmp_path: Path) -> None:
    path=tmp_path/"catalog.json";path.write_text(json.dumps({"captured_at":NOW.isoformat(),"data":{"one":1}}))
    cache=Cache(tmp_path,deep=True)
    assert cache.get("catalog","https://invalid.example.com",86400)[0]=={"one":1}
    assert cache.requests==0


def test_large_league_analysis_bounded() -> None:
    players={str(i):Player(id=str(i),name="Player "+str(i),positions=["WR"],team="BUF",adds=i) for i in range(3000)}
    l=league(teams=[Team(id=str(i),players=[str(i)]) for i in range(20)])
    tracemalloc.start();start=time.perf_counter()
    result=analyze(l,players,NOW)
    duration=time.perf_counter()-start;_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert all(len(t["waivers"])<=30 for t in result["teams"].values())
    assert duration<15  # Generous CI bound; catches algorithmic blowups.
    assert peak<40*1024*1024


def test_bye_alert_one_per_player() -> None:
    players=pool();players["owned"].status="Questionable"
    alerts=analyze(league(bye_players=["owned"]),players,NOW)["teams"]["9"]["alerts"]
    assert len(alerts)==1 and alerts[0]["status"]=="Bye"
    assert "Questionable" in alerts[0]["reason"]


def test_ir_player_has_no_active_bench_cost() -> None:
    players=pool();players["owned"].status="IR-R"
    alerts=analyze(league(teams=[Team(id="9",players=["owned"],reserve=["owned"])]),players,NOW)["teams"]["9"]["alerts"]
    assert "no active bench slot" in alerts[0]["advice"]
    assert "drop" not in alerts[0]["advice"].lower()


def test_unknown_projection_starter_replacement_has_no_fake_gain() -> None:
    players=pool();players["owned"].status="Out"
    moves=analyze(league(teams=[Team(id="9",players=["owned","free"],starters=["0","owned"])]),players,NOW)["teams"]["9"]["lineup_moves"]
    assert moves[0]["in"]=="free" and moves[0]["gain"] is None


def test_one_healthy_starter_does_not_create_backup_position_need() -> None:
    assert "WR" not in analyze(league(),pool(),NOW)["teams"]["9"]["needs"]
