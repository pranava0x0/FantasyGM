"""Check the boundaries that keep scouting evidence attached to the right player and date."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from pipeline import player_reports as reports

END = reports.instant("2026-11-10T03:00:00Z")


def test_only_recent_attributed_news_and_completed_game_lines() -> None:
    def story(pid: int, date: str) -> dict:
        return {"headline": "Tagged article", "published": date, "categories": [{"athleteId": pid}],
                "links": {"web": {"href": "https://www.espn.com/story"}}}

    raw = {"rotowire": {"headline": "Status updated", "published": "Mon Nov 09 13:49:56 PST 2026"},
           "news": [story(1, "2026-11-04T23:00:00Z"), story(2, "2026-11-04T23:00:00Z"),
                    story(1, "2026-10-31T23:00:00Z"), story(1, "2026-11-10T18:00:00Z")],
           "gameLog": {"events": {"a": {"gameResult": "W", "gameDate": "2026-11-05T01:00:00Z"},
                                   "b": {"gameDate": "2026-11-09T01:00:00Z"}},
                       "statistics": [{"names": ["targets"], "events": [
                           {"eventId": "a", "stats": ["5"]}, {"eventId": "b", "stats": ["0"]}]},
                           {"names": ["carries"], "events": [{"eventId": "a", "stats": ["3"]}]}]}}
    result = reports.details(raw, "1", "nfl", END)
    assert [a["headline"] for a in result["news"]] == ["Status updated", "Tagged article"]
    assert result["news"][0]["at"] == "2026-11-09T21:49:56+00:00"
    assert [g["stats"] for g in result["games"]] == [{"targets": "5", "carries": "3"}]


def test_refuse_ambiguous_team_roster_match(tmp_path: Path) -> None:
    athletes = [{"id": "1", "fullName": "A Player"}, {"id": "2", "fullName": "A. Player"}]
    def reply(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/teams/phi/roster")
        return httpx.Response(200, json={"athletes": [{"items": athletes}]})

    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        source = reports.PublicSource(client, tmp_path)
        with pytest.raises(ValueError, match="unique"):
            reports.athlete_id({"espn_id": None, "name": "A Player", "team": "PHI"}, source)


def test_syndicated_headlines_match_subject_and_stay_in_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reports.time, "sleep", lambda _: None)
    def post(text: str, at: str, key: str) -> dict:
        return {"post": {"author": {"handle": "rotowirenba.bsky.social"}, "uri": "at://did/example/" + key,
                         "record": {"text": text, "createdAt": at}}}

    def reply(request: httpx.Request) -> httpx.Response:
        assert "cookie" not in request.headers
        if request.url.params["actor"] == "rotowirenfl.bsky.social":
            return httpx.Response(200, json={"feed": []})
        if "cursor" not in request.url.params:
            return httpx.Response(200, json={"feed": [post("Nikola Jokić: Returning", "2026-11-08T14:00:00Z", "1"),
                                                      post("Other Player: Replaces Nikola Jokic", "2026-11-08T10:00:00Z", "2")],
                                            "cursor": "next"})
        return httpx.Response(200, json={"feed": [post("Nikola Jokic: Old news", "2026-10-30T14:00:00Z", "3")]})

    player = {"name": "Nikola Jokic", "sport": "nba"}
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        coverage = reports.beat_updates([player], reports.PublicSource(client, tmp_path), END)
    assert {v for v in coverage.values()} == {"seven-day window checked"}
    assert [a["headline"] for a in player["beat"]] == ["Nikola Jokić: Returning"]
