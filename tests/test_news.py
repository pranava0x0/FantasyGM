"""Tests for the news ingest / match layer."""

from __future__ import annotations

from datetime import datetime, timezone

from pipeline.news import match_to_players, normalize_articles


_DEFAULT_WNBA_TEAM = 14  # Seattle Storm — a valid WNBA team ID


def _article(article_id: int, *, headline: str = "h", athletes: list[int] = None,
             teams: list[int] = None, premium: bool = False,
             published: str = "2026-05-18T02:00:00Z") -> dict:
    # Default to a WNBA team tag so articles pass the WNBA filter unless
    # the caller explicitly passes teams=[].
    if teams is None and not athletes:
        teams = [_DEFAULT_WNBA_TEAM]
    categories = []
    for a in athletes or []:
        categories.append({"type": "athlete", "athleteId": a, "description": f"Player {a}"})
    for t in teams or []:
        categories.append({"type": "team", "teamId": t, "description": f"Team {t}"})
    return {
        "id": article_id,
        "headline": headline,
        "description": "stub",
        "published": published,
        "premium": premium,
        "categories": categories,
        "links": {"web": {"href": f"https://espn.com/wnba/{article_id}"}},
    }


class TestNormalizeArticles:
    def test_returns_flat_dicts(self) -> None:
        raw = {"articles": [_article(1, athletes=[100], teams=[5])]}
        out = normalize_articles(raw)
        assert len(out) == 1
        a = out[0]
        assert a["id"] == 1
        assert a["athlete_ids"] == [100]
        assert a["pro_team_ids"] == [5]
        assert a["url"] == "https://espn.com/wnba/1"

    def test_premium_filtered(self) -> None:
        raw = {"articles": [
            _article(1, premium=True),
            _article(2),
        ]}
        out = normalize_articles(raw)
        assert [a["id"] for a in out] == [2]

    def test_sorted_newest_first(self) -> None:
        raw = {"articles": [
            _article(1, published="2026-05-10T00:00:00Z"),
            _article(2, published="2026-05-18T00:00:00Z"),
            _article(3, published="2026-05-12T00:00:00Z"),
        ]}
        out = normalize_articles(raw)
        assert [a["id"] for a in out] == [2, 3, 1]

    def test_published_at_parsed(self) -> None:
        raw = {"articles": [_article(1, published="2026-05-18T02:17:43Z")]}
        out = normalize_articles(raw)
        assert out[0]["published_at"] == datetime(2026, 5, 18, 2, 17, 43, tzinfo=timezone.utc)

    def test_empty_input(self) -> None:
        assert normalize_articles({}) == []
        assert normalize_articles({"articles": []}) == []

    def test_dedups_repeated_ids_within_article(self) -> None:
        raw = {"articles": [{
            "id": 1, "headline": "h", "description": "", "premium": False,
            "categories": [
                {"type": "athlete", "athleteId": 100},
                {"type": "athlete", "athleteId": 100},
            ],
            "links": {}, "published": "2026-05-18T02:00:00Z",
        }]}
        out = normalize_articles(raw)
        assert out[0]["athlete_ids"] == [100]


class TestMatchToPlayers:
    def test_direct_athlete_tag(self) -> None:
        arts = normalize_articles({"articles": [_article(1, athletes=[100])]})
        out = match_to_players(arts, player_ids={100, 200})
        assert list(out.keys()) == [100]
        assert out[100][0]["id"] == 1

    def test_team_tag_with_reverse_lookup(self) -> None:
        # Article mentions team 9, no athletes. Players 200/201 are on team 9.
        arts = normalize_articles({"articles": [_article(1, teams=[9])]})
        out = match_to_players(arts, player_ids={200, 201, 999}, pro_team_id_to_player_ids={9: {200, 201}})
        assert set(out.keys()) == {200, 201}

    def test_athlete_tag_skips_team_redundancy(self) -> None:
        # Article tags athlete 100 AND team 9. Player 100 is on team 9.
        # Should NOT add the article twice to player 100's list.
        arts = normalize_articles({"articles": [_article(1, athletes=[100], teams=[9])]})
        out = match_to_players(arts, player_ids={100}, pro_team_id_to_player_ids={9: {100}})
        assert len(out[100]) == 1

    def test_no_match_returns_empty_dict(self) -> None:
        arts = normalize_articles({"articles": [_article(1, athletes=[100])]})
        out = match_to_players(arts, player_ids={200, 300})
        assert out == {}


class TestWNBAFilter:
    def test_non_wnba_team_rejected(self) -> None:
        # Article with a non-WNBA team ID (e.g. NFL team 12) must be filtered.
        raw = {"articles": [_article(1, teams=[12])]}
        out = normalize_articles(raw)
        assert out == []

    def test_mixed_teams_rejected(self) -> None:
        # One WNBA team + one non-WNBA team → rejected (cross-sport article).
        raw = {"articles": [_article(1, teams=[14, 99])]}
        out = normalize_articles(raw)
        assert out == []

    def test_all_wnba_teams_kept(self) -> None:
        raw = {"articles": [_article(1, teams=[14, 17])]}  # SEA + LV
        out = normalize_articles(raw)
        assert len(out) == 1

    def test_athlete_only_no_team_kept(self) -> None:
        # No team tag but has athlete tag → kept (player-specific story).
        raw = {"articles": [_article(1, athletes=[999], teams=[])]}
        out = normalize_articles(raw)
        assert len(out) == 1

    def test_no_teams_no_athletes_rejected(self) -> None:
        raw = {"articles": [_article(1, athletes=[], teams=[])]}
        out = normalize_articles(raw)
        assert out == []
