"""End-to-end tests for build_state: fixtures → LeagueState → state.json round-trip."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline import build_state as bs
from pipeline.schema import LeagueState


def test_build_state_returns_league_state(
    league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    state = bs.build_state(
        league_raw=league_raw,
        free_agents_raw=free_agents_raw,
        captured_at=captured_at,
    )
    assert isinstance(state, LeagueState)


def test_meta_populated(
    league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    state = bs.build_state(
        league_raw=league_raw, free_agents_raw=free_agents_raw, captured_at=captured_at,
    )
    assert state.meta.league_name == "50-40-90 Club"
    assert state.meta.season_id == 2026
    assert state.meta.scoring_period_id == 10
    assert state.meta.team_count == len(state.teams)
    assert state.meta.captured_at == captured_at


def test_teams_have_needs_payload(
    league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    state = bs.build_state(
        league_raw=league_raw, free_agents_raw=free_agents_raw, captured_at=captured_at,
    )
    for t in state.teams:
        # WNBA fantasy uses shared F/C slots — top need is G vs combined FC.
        assert t.needs.top_need_bucket in ("G", "FC")


def test_waiver_targets_present(
    league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    state = bs.build_state(
        league_raw=league_raw, free_agents_raw=free_agents_raw, captured_at=captured_at,
    )
    assert state.waiver_targets_overall, "expected at least one overall waiver target"
    assert state.waiver_targets_by_team, "expected per-team targets"
    assert len(state.waiver_targets_by_team) == len(state.teams)


def test_state_round_trip_through_json(
    league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    state = bs.build_state(
        league_raw=league_raw, free_agents_raw=free_agents_raw, captured_at=captured_at,
    )
    blob = state.model_dump_json()
    parsed = json.loads(blob)
    # Re-construct from the serialized form — exercises every field.
    re_state = LeagueState.model_validate(parsed)
    assert re_state == state


def test_write_state_creates_file(
    tmp_path: Path, league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    state = bs.build_state(
        league_raw=league_raw, free_agents_raw=free_agents_raw, captured_at=captured_at,
    )
    out_path = bs.write_state(state, tmp_path)
    assert out_path.exists()
    on_disk = json.loads(out_path.read_text())
    assert on_disk["meta"]["league_name"] == "50-40-90 Club"


def test_append_transactions_history_dedupes(
    tmp_path: Path, league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    state = bs.build_state(
        league_raw=league_raw, free_agents_raw=free_agents_raw, captured_at=captured_at,
    )
    n1 = bs.append_transactions_history(state, tmp_path)
    n2 = bs.append_transactions_history(state, tmp_path)
    # First call writes everything; second writes nothing.
    assert n1 == len(state.transactions_recent)
    assert n2 == 0


def test_matchup_history_populated(
    league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    state = bs.build_state(
        league_raw=league_raw, free_agents_raw=free_agents_raw, captured_at=captured_at,
    )
    # Fixture has currentMatchupPeriod=3 with 2 completed periods (1 and 2).
    team_by_id = {t.team_id: t for t in state.teams}

    # team1: lost period 1 to team5, lost period 2 to team2
    t1 = team_by_id[1]
    assert len(t1.matchup_history) == 2
    p1 = next(m for m in t1.matchup_history if m.matchup_period_id == 1)
    assert p1.opponent_team_id == 5
    assert p1.result == "L"
    assert p1.team_points == 350.0
    assert p1.opponent_points == 430.0
    p2 = next(m for m in t1.matchup_history if m.matchup_period_id == 2)
    assert p2.opponent_team_id == 2
    assert p2.result == "L"

    # team5: won period 1 vs team1; no period 2 matchup in fixture
    t5 = team_by_id[5]
    assert len(t5.matchup_history) == 1
    assert t5.matchup_history[0].result == "W"
    assert t5.matchup_history[0].opponent_team_id == 1

    # period 3 is in progress (both 0.0) — must not appear
    for t in state.teams:
        assert all(m.matchup_period_id < 3 for m in t.matchup_history)


def test_no_owner_fields_in_state(
    league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    state = bs.build_state(
        league_raw=league_raw, free_agents_raw=free_agents_raw, captured_at=captured_at,
    )
    blob = state.model_dump_json()
    # Sanity: none of the canonical owner-identifying field names made it in.
    for forbidden in ("displayName", "primaryOwner", "memberId", "firstName", "lastName"):
        assert forbidden not in blob, f"state.json contains '{forbidden}'"
