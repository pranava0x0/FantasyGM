"""Shared pytest fixtures."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def league_raw() -> dict:
    """Slim, owner-redacted league response — 3 teams from 50-40-90 Club."""
    return json.loads((FIXTURE_DIR / "league_sample.json").read_text())


@pytest.fixture(scope="session")
def free_agents_raw() -> dict:
    """Synthetic FA fixture — 5 players across G/F/C with varying projections."""
    return json.loads((FIXTURE_DIR / "free_agents_sample.json").read_text())


@pytest.fixture(scope="session")
def captured_at() -> datetime:
    return datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
