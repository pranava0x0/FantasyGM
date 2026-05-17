"""The ingest module must strip owner-identifying fields before writing snapshots."""

from __future__ import annotations

import json

from pipeline.ingest import _redact_owners


def test_strips_top_level_members() -> None:
    raw = {
        "id": 1,
        "members": [
            {"id": "M1", "displayName": "ALICE", "firstName": "Alice", "lastName": "X"},
            {"id": "M2", "displayName": "BOB", "userId": "u-bob"},
        ],
    }
    out = _redact_owners(raw)
    assert out["members"] == [{"id": "M1"}, {"id": "M2"}]
    # No display name survived anywhere.
    assert "displayName" not in json.dumps(out)


def test_strips_team_owners() -> None:
    raw = {
        "teams": [
            {"id": 1, "owners": ["{ABCD-1234}"], "primaryOwner": "{ABCD-1234}"},
        ],
    }
    out = _redact_owners(raw)
    assert "owners" not in out["teams"][0]
    assert "primaryOwner" not in out["teams"][0]


def test_strips_transaction_member_id() -> None:
    raw = {
        "transactions": [
            {"id": "t1", "memberId": "{ABCD-1234}", "isLeagueManager": False,
             "isActingAsTeamOwner": False, "items": []},
        ],
    }
    out = _redact_owners(raw)
    tx = out["transactions"][0]
    assert "memberId" not in tx
    assert "isLeagueManager" not in tx
    assert "isActingAsTeamOwner" not in tx


def test_idempotent() -> None:
    raw = {
        "members": [{"id": "M1", "displayName": "X"}],
        "teams": [{"id": 1, "owners": ["o"]}],
    }
    once = _redact_owners(raw)
    twice = _redact_owners(once)
    assert once == twice


def test_preserves_non_owner_fields() -> None:
    raw = {
        "id": 100,
        "seasonId": 2026,
        "settings": {"name": "League"},
        "teams": [{"id": 1, "abbrev": "AAA", "record": {"overall": {"wins": 3}}}],
    }
    out = _redact_owners(raw)
    assert out["id"] == 100
    assert out["settings"]["name"] == "League"
    assert out["teams"][0]["abbrev"] == "AAA"
    assert out["teams"][0]["record"]["overall"]["wins"] == 3
