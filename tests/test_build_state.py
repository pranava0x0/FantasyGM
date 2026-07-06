"""End-to-end tests for build_state: fixtures → LeagueState → state.json round-trip."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline import build_state as bs
from pipeline import news as news_mod
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


def _news_raw(*articles: dict) -> dict:
    return {"articles": list(articles)}


def _article(article_id: int, *, headline: str = "h", athletes: list[int] | None = None,
             teams: list[int] | None = None, published: str = "2026-05-18T02:00:00Z") -> dict:
    categories = [{"type": "athlete", "athleteId": a} for a in (athletes or [])]
    categories += [{"type": "team", "teamId": t} for t in (teams or [])]
    return {
        "id": article_id,
        "headline": headline,
        "description": "stub",
        "published": published,
        "premium": False,
        "categories": categories,
        "links": {"web": {"href": f"https://espn.com/wnba/{article_id}"}},
    }


def test_append_news_history_dedupes(tmp_path: Path) -> None:
    raw = _news_raw(_article(1, athletes=[4433403]), _article(2, athletes=[4433403]))
    n1 = bs.append_news_history(raw, tmp_path)
    n2 = bs.append_news_history(raw, tmp_path)
    assert n1 == 2
    assert n2 == 0


def test_load_news_history_round_trips(tmp_path: Path) -> None:
    raw = _news_raw(_article(1, headline="Clark drops 30", athletes=[4433403]))
    bs.append_news_history(raw, tmp_path)
    loaded = bs.load_news_history(tmp_path)
    assert len(loaded) == 1
    assert loaded[0]["id"] == 1
    assert loaded[0]["headline"] == "Clark drops 30"


def test_append_social_history_dedupes_by_source_and_url(tmp_path: Path) -> None:
    posts = {
        "reddit": [{"title": "t1", "url": "https://reddit.com/1", "published_at": None, "subreddit": "wnba"}],
        "twitter": [{"title": "t2", "url": "https://x.com/1", "published_at": None, "screen_name": "a"}],
    }
    n1 = bs.append_social_history(posts, tmp_path)
    n2 = bs.append_social_history(posts, tmp_path)
    assert n1 == 2
    assert n2 == 0
    loaded = bs.load_social_history(tmp_path)
    assert len(loaded["reddit"]) == 1
    assert len(loaded["twitter"]) == 1
    assert loaded["bluesky"] == []


def test_build_state_merges_extra_news_into_player_history(
    league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    # Caitlin Clark (4433403) has no news in today's feed, but an older
    # archived article — already normalized, as load_news_history() returns —
    # should still surface in her history.
    archived = news_mod.normalize_articles(_news_raw(
        _article(99, headline="old Clark story", athletes=[4433403], published="2026-04-01T00:00:00Z"),
    ))
    state = bs.build_state(
        league_raw=league_raw, free_agents_raw=free_agents_raw, captured_at=captured_at,
        extra_news=archived,
    )
    assert 4433403 in state.news_by_player
    assert any(n.id == 99 for n in state.news_by_player[4433403])


def test_build_state_caps_team_only_news_lower_than_direct(
    league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    # 15 team-tagged articles (proTeamId 5 = Clark's team) that never name
    # her directly — these are "ambient" and should cap at
    # MAX_TEAM_ONLY_NEWS_PER_PLAYER, not the full per-player cap.
    archived = news_mod.normalize_articles(_news_raw(*[
        _article(100 + i, headline=f"team recap {i}", teams=[5], athletes=[9999990 + i])
        for i in range(15)
    ]))
    state = bs.build_state(
        league_raw=league_raw, free_agents_raw=free_agents_raw, captured_at=captured_at,
        extra_news=archived,
    )
    clark_news = state.news_by_player.get(4433403, [])
    assert len(clark_news) == bs.MAX_TEAM_ONLY_NEWS_PER_PLAYER
    assert all(4433403 not in n.athlete_ids for n in clark_news)


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


def test_ai_summary_attached_to_waiver_targets(
    league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    """ai_summaries (keyed by str player_id) flow onto WaiverTarget.ai_summary."""
    summaries = {"9000001": "Pick them up — full slate this week."}
    state = bs.build_state(
        league_raw=league_raw,
        free_agents_raw=free_agents_raw,
        captured_at=captured_at,
        ai_summaries=summaries,
    )
    matched = [t for t in state.waiver_targets_overall if t.player.player_id == 9000001]
    assert matched, "expected player 9000001 among overall targets"
    assert matched[0].ai_summary == "Pick them up — full slate this week."
    # Players without an authored summary stay None, not "".
    others = [t for t in state.waiver_targets_overall if t.player.player_id != 9000001]
    assert all(t.ai_summary is None for t in others)


def test_ai_summary_absent_by_default(
    league_raw: dict, free_agents_raw: dict, captured_at: datetime
) -> None:
    """No ai_summaries arg → every waiver target has ai_summary None."""
    state = bs.build_state(
        league_raw=league_raw, free_agents_raw=free_agents_raw, captured_at=captured_at,
    )
    assert all(t.ai_summary is None for t in state.waiver_targets_overall)
