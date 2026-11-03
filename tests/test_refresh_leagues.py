from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import httpx
import pytest

from pipeline import refresh_leagues as job


def test_roster_scope_defenses_and_deduplication() -> None:
    league = {"sport": "nfl", "owner": "Private Label", "players": [
        {"id": "MIN", "name": "MIN"}, {"id": "123", "name": "Rashee Rice"}]}
    roster = {"leagues": [league, league, {"sport": "nba", "players": [{"id": "123", "name": "Stephen Curry"}]}]}
    players = job.searchable_names(roster, {"MIN": {"positions": ["DEF"], "name": "Minnesota Vikings"}})
    assert [p["names"] for p in players] == [["Stephen Curry", "Steph Curry"], ["Rashee Rice"], ["Minnesota Vikings defense"]]
    assert "Private Label" not in json.dumps(players)


def test_public_query_date_matching_and_privacy() -> None:
    end = job.instant("2026-11-03T12:00:00Z")
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == '"Nikola Jokic"'
        assert "cookie" not in request.headers and "authorization" not in request.headers
        posts = [{"uri": f"at://did:plc:abc/app.bsky.feed.post/{i}", "author": {"handle": "private"},
                  "record": {"text": text, "createdAt": dt}} for i, (text, dt) in enumerate([
                      ("Nikola Jokić private comment", "2026-11-02T12:00:00.123456789Z"),
                      ("Nikola Jokicson", "2026-11-02T12:00:00Z"), ("Nikola Jokic", "2026-10-15T00:00:00Z")])]
        return httpx.Response(200, json={"posts": posts})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert job.search(client, "Bluesky", "nba", "Nikola Jokic", end-timedelta(days=7), end) == [
            {"url": "https://bsky.app/profile/did:plc:abc/post/0", "published_at": "2026-11-02T12:00:00.123456+00:00"}]


def test_rate_limit_stops_host_without_reporting_false_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    def blocked(*args: object) -> None:
        calls.append(1)
        response = httpx.Response(429, request=httpx.Request("GET", "https://www.reddit.com/"))
        response.raise_for_status()
    monkeypatch.setattr(job, "search", blocked)
    monkeypatch.setattr(job.time, "sleep", lambda _: None)
    results = job.collect("Reddit", [{"names": [name], "sport": "nba"} for name in ("First Player", "Next Player")],
                          job.instant("2026-11-03T12:00:00Z"))
    assert len(calls) == 2
    assert [r["queries"][0]["outcome"] for r in results] == ["unavailable: HTTP 429", "not searched: source rate-limited"]


def test_replay_without_provider_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Replay must not refresh league data")
    monkeypatch.setattr(job.football, "refresh", unexpected)
    capture = {"searched_at": "2026-11-03T12:00:00+00:00", "incomplete_rosters": True, "players": []}
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(capture))
    assert job.main(["--replay", str(path)]) == 0
    assert path.with_suffix(".md").read_text() == job.render(capture)


def test_reddit_keeps_only_scoped_post_ids() -> None:
    xml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <title>Joel Embiid update</title><published>2026-11-02T14:00:00Z</published>
    <link href="https://www.reddit.com/r/fantasybball/comments/abc123/private_title/"/>
    <author><name>private-author</name></author></entry></feed>"""
    def reply(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/r/fantasybball/search.rss"
        return httpx.Response(200, content=xml)
    end = job.instant("2026-11-03T12:00:00Z")
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        posts = job.search(client, "Reddit", "nba", "Joel Embiid", end-timedelta(days=7), end)
    assert posts == [{"published_at": "2026-11-02T14:00:00+00:00", "url": "https://www.reddit.com/comments/abc123/"}]
