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


class RecentGame(_Strict):
    """One actual game entry from the last ~2 weeks (statSplitTypeId=5)."""
    scoring_period_id: int
    fantasy_points: float


class PlayerRef(_Strict):
    """Minimal player reference, embedded in roster slots and waiver targets."""
    player_id: int
    name: str
    team: str | None = Field(None, description="WNBA team abbreviation, e.g. NY")
    position: str = Field(..., description="G / F / C / ? — derived from defaultPositionId")
    bucket: Literal["G", "F", "C"] = Field(..., description="G / F / C bucket used for team-needs math")
    eligible_slots: list[int] = Field(default_factory=list)
    injury_status: str | None = None


class RosterEntry(_Strict):
    """A single roster slot — player + how they're being deployed this period."""
    player: PlayerRef
    lineup_slot_id: int
    lineup_slot_label: str
    is_active: bool = Field(..., description="True if slot counts toward weekly production")
    projected_points: float | None = Field(None, description="Single-period (1-day) projection")
    projected_per_game: float | None = None
    projected_points_this_week: float | None = None
    games_this_week: int = 0
    actual_points: float | None = None


class TeamRecord(_Strict):
    wins: int
    losses: int
    ties: int
    pct: float


class MatchupResult(_Strict):
    """One completed H2H matchup for a team."""
    matchup_period_id: int
    opponent_team_id: int
    team_points: float
    opponent_points: float
    result: Literal["W", "L", "T"]


class TeamNeeds(_Strict):
    """Per-team production by bucket, with league-average comparison.

    Framed proactively as *needs* — the bucket with the largest negative
    gap-vs-league is the team's biggest upgrade opportunity, surfaced as
    `top_need_bucket`.

    The load-bearing comparison is **G vs FC** (frontcourt = F + C combined),
    because WNBA fantasy uses shared F/C lineup slots — a team with zero
    `defaultPositionId=3` players doesn't have a structural C need if they
    fill those slots with Forwards. The granular F and C breakdowns are
    kept for diagnostics, but `top_need_bucket` and waiver-target
    adjustment use the combined frontcourt number.
    """
    guard_proj: float
    forward_proj: float
    center_proj: float
    frontcourt_proj: float = Field(..., description="forward_proj + center_proj")
    guard_gap_vs_league: float = Field(..., description="team - league avg, negative = need")
    forward_gap_vs_league: float
    center_gap_vs_league: float
    frontcourt_gap_vs_league: float
    top_need_bucket: Literal["G", "FC"]


class TeamState(_Strict):
    team_id: int
    abbrev: str
    name: str
    logo_url: str | None = None
    division_id: int | None = None
    record: TeamRecord
    matchup_history: list[MatchupResult] = Field(
        default_factory=list,
        description="Completed H2H matchup results, sorted by matchup_period_id ascending.",
    )
    waiver_position: int | None = None
    faab_remaining: int | None = None
    roster: list[RosterEntry]
    needs: TeamNeeds
    summary: list[str] = Field(default_factory=list, description="Auto-generated bullet summary")
    recent_transaction_ids: list[str] = Field(
        default_factory=list,
        description="IDs of transactions attributable to this team (most recent first); UI joins to transactions_recent.",
    )


class WaiverTarget(_Strict):
    """A free-agent ranked for pickup, optionally with per-team adjustment."""
    player: PlayerRef
    projected_points_next_period: float = Field(..., description="Single-period (often 1-day) projection")
    projected_per_game: float = Field(..., description="Per-game projection (season basis)")
    projected_points_this_week: float = Field(..., description="proj_per_game × games_this_week")
    games_this_week: int = Field(..., description="Pro-team games in the upcoming window")
    projected_points_next_week: float = Field(0.0, description="proj_per_game × games_next_week")
    games_next_week: int = Field(0, description="Pro-team games in the following week's window")
    season_avg_points: float | None = None
    percent_owned: float | None = None
    percent_change: float | None = None
    base_score: float = Field(..., description="Sorting key = projected_points_this_week")
    injury_signal: str | None = Field(
        None,
        description=(
            "Detected availability signal derived from recent game-absence vs team schedule. "
            "'returning' = player missed ≥50% of their team's games in the rolling window "
            "but has a meaningful season history — likely returning from injury/rest. "
            "Base score boosted +15% to surface them. "
            "None = no anomaly detected."
        ),
    )
    recent_games: list[RecentGame] = Field(
        default_factory=list,
        description="Per-game fantasy point totals for the last ~14 scoring periods, newest first.",
    )
    ai_summary: str | None = Field(
        None,
        description="AI-authored 'why pick them up' GM take, keyed by player_id from data/ai_summaries.json. None when no summary exists for this player.",
    )


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


class RedditPost(_Strict):
    title: str
    url: str
    published_at: datetime | None = None
    subreddit: str = "wnba"


class BlueskyPost(_Strict):
    title: str
    url: str
    published_at: datetime | None = None
    handle: str = ""


class TwitterPost(_Strict):
    title: str
    url: str
    published_at: datetime | None = None
    screen_name: str = ""


class LeagueState(_Strict):
    """Top-level object written to `docs/data/state.json`."""
    meta: LeagueMeta
    teams: list[TeamState]
    transactions_recent: list[Transaction]
    waiver_targets_overall: list[WaiverTarget]
    waiver_targets_by_team: list[WaiverTargetsByTeam]
    news_recent: list[NewsItem] = Field(default_factory=list)
    news_by_player: dict[int, list[NewsItem]] = Field(default_factory=dict)
    reddit_posts_by_player: dict[int, list[RedditPost]] = Field(default_factory=dict)
    twitter_posts_by_player: dict[int, list[TwitterPost]] = Field(default_factory=dict)
    bluesky_posts_by_player: dict[int, list[BlueskyPost]] = Field(default_factory=dict)
