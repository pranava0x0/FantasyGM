"""Pydantic models for the *derived* state the UI reads.

The raw ESPN responses are dumped to `data/raw/<date>/*.json` without going
through a schema (we don't want to break when ESPN adds fields). The schema
here describes only the *output* surface: `docs/data/state.json` and the
append-only transaction log.

A single source of truth — the frontend `app.js` mirrors these shapes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    """Strict base — unknown fields raise."""
    model_config = ConfigDict(extra="forbid")


class PlayerRef(_Strict):
    """Minimal player reference, embedded in roster slots and waiver targets."""
    player_id: int
    name: str
    team: str | None = Field(None, description="WNBA team abbreviation, e.g. NY")
    position: str = Field(..., description="G / F / C / ? — derived from defaultPositionId")
    bucket: Literal["G", "F", "C"] = Field(..., description="G / F / C bucket used for weakness math")
    eligible_slots: list[int] = Field(default_factory=list)
    injury_status: str | None = None


class RosterEntry(_Strict):
    """A single roster slot — player + how they're being deployed this period."""
    player: PlayerRef
    lineup_slot_id: int
    lineup_slot_label: str
    is_active: bool = Field(..., description="True if slot counts toward weekly production")
    projected_points: float | None = None
    actual_points: float | None = None


class TeamRecord(_Strict):
    wins: int
    losses: int
    ties: int
    pct: float


class TeamWeakness(_Strict):
    """Per-team production by bucket, with league-average comparison.

    The load-bearing comparison is **G vs FC** (frontcourt = F + C combined),
    because WNBA fantasy uses shared F/C lineup slots — a team with zero
    `defaultPositionId=3` players isn't structurally weak at "C" if they
    fill those slots with Forwards. The granular F and C breakdowns are
    kept for diagnostics, but `weakest_bucket` and waiver-target adjustment
    use the combined frontcourt number.
    """
    guard_proj: float
    forward_proj: float
    center_proj: float
    frontcourt_proj: float = Field(..., description="forward_proj + center_proj")
    guard_gap_vs_league: float = Field(..., description="team - league avg, negative = weakness")
    forward_gap_vs_league: float
    center_gap_vs_league: float
    frontcourt_gap_vs_league: float
    weakest_bucket: Literal["G", "FC"]


class TeamState(_Strict):
    team_id: int
    abbrev: str
    name: str
    logo_url: str | None = None
    division_id: int | None = None
    record: TeamRecord
    waiver_position: int | None = None
    faab_remaining: int | None = None
    roster: list[RosterEntry]
    weakness: TeamWeakness


class WaiverTarget(_Strict):
    """A free-agent ranked for pickup, optionally with per-team adjustment."""
    player: PlayerRef
    projected_points_next_period: float = Field(..., description="Single-period (often 1-day) projection")
    projected_per_game: float = Field(..., description="Per-game projection (season basis)")
    projected_points_this_week: float = Field(..., description="proj_per_game × games_this_week")
    games_this_week: int = Field(..., description="Pro-team games in the upcoming window")
    season_avg_points: float | None = None
    percent_owned: float | None = None
    percent_change: float | None = None
    base_score: float = Field(..., description="Sorting key = projected_points_this_week")


class TransactionItem(_Strict):
    player_id: int
    player_name: str | None = None
    from_team_id: int | None = None
    to_team_id: int | None = None
    from_slot_id: int | None = None
    to_slot_id: int | None = None
    type: str = Field(..., description="LINEUP | ADD | DROP | TRADE_ACCEPT etc.")


class Transaction(_Strict):
    transaction_id: str
    occurred_at: datetime
    scoring_period_id: int
    team_id: int | None
    type: str
    bid_amount: int = 0
    status: str
    items: list[TransactionItem]


class LeagueMeta(_Strict):
    league_id: int
    league_name: str
    season_id: int
    scoring_period_id: int
    matchup_period_id: int
    scoring_type: str
    team_count: int
    captured_at: datetime
    source: str = "espn-private-api"
    week_start_period: int = Field(..., description="Inclusive start of the upcoming-week window")
    week_end_period: int = Field(..., description="Inclusive end of the upcoming-week window")


class WaiverTargetsByTeam(_Strict):
    team_id: int
    targets: list[WaiverTarget]


class NewsItem(_Strict):
    id: int
    headline: str
    description: str = ""
    url: str | None = None
    published_at: datetime | None = None
    athlete_ids: list[int] = Field(default_factory=list)
    pro_team_ids: list[int] = Field(default_factory=list)


class LeagueState(_Strict):
    """Top-level object written to `docs/data/state.json`."""
    meta: LeagueMeta
    teams: list[TeamState]
    transactions_recent: list[Transaction]
    waiver_targets_overall: list[WaiverTarget]
    waiver_targets_by_team: list[WaiverTargetsByTeam]
    news_recent: list[NewsItem] = Field(default_factory=list)
    news_by_player: dict[int, list[NewsItem]] = Field(default_factory=dict)
