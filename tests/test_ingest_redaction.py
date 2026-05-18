"""The ingest module must strip owner-identifying fields before writing snapshots."""

from __future__ import annotations

import json

from pipeline.ingest import _redact_owners


# Synthetic UUIDs only — never paste a real SWID into a committed test.
_FAKE_SWID_1 = "{AAAA1111-2222-3333-4444-555566667777}"
_FAKE_SWID_2 = "{BBBB1111-2222-3333-4444-555566667777}"


def test_drops_members_array_entirely() -> None:
    # members[] is dropped wholesale. Each entry's `id` is a UUID-in-braces
    # which is identical in shape to the user's SWID auth cookie; preserving
    # even id-only stubs leaks identifiable session material.
    raw = {
        "id": 1,
        "members": [
            {"id": _FAKE_SWID_1, "displayName": "ALICE"},
            {"id": _FAKE_SWID_2, "displayName": "BOB"},
        ],
    }
    out = _redact_owners(raw)
    assert "members" not in out, "members[] must be removed entirely"


def test_scrubs_uuid_in_braces_anywhere_in_tree() -> None:
    # Even after dropping fields by name, a member-ID-shaped UUID
    # appearing deep in the tree (transactions[*].source, etc.) must
    # be replaced with a stable placeholder.
    raw = {
        "transactions": [
            {"id": "txn-1", "source": _FAKE_SWID_1, "items": []},
        ],
        "settings": {
            "weirdNested": {"someId": _FAKE_SWID_2},
        },
    }
    out = _redact_owners(raw)
    blob = json.dumps(out)
    assert "AAAA1111" not in blob
    assert "BBBB1111" not in blob
    assert "REDACTED-MEMBER-ID" in blob


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
