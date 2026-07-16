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
    projected_points_next_week: float | None = Field(None, description="proj_per_game × games_next_week")
    games_next_week: int = Field(0, description="Pro-team games in the following week's window")
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


class CurrentMatchup(_Strict):
    """The in-progress H2H matchup — who you're playing and the live score.

    Distinct from `matchup_history`, which is completed weeks only. Points are
    ESPN's running totals as of `captured_at`, so they trail the live site by
    however stale the snapshot is.
    """
    matchup_period_id: int
    opponent_team_id: int
    team_points: float
    opponent_points: float


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


class LineupMove(_Strict):
    """One lineup action: start `player_in`, gaining `gain_pts`.

    `action="swap"` benches `player_out` to make room. `action="start"` fills
    an empty active slot (roster shorter than the nine slots) and carries no
    outgoing player.
    """
    action: Literal["swap", "start"]
    player_in_id: int
    player_in_name: str
    player_out_id: int | None = Field(None, description="None when action is 'start' — no one is benched.")
    player_out_name: str | None = None
    slot_label: str = Field(..., description="Active slot the incoming player fills (G / F / F/C / UTIL)")
    gain_pts: float = Field(..., description="Projected points gained by this move; always >= lineups.MIN_SWAP_GAIN_PTS")
    player_in_game_time: datetime | None = Field(
        None,
        description="Tip-off for the incoming player's game. Set on the 'tonight' horizon only; None over a week (several games, no single time) or when ESPN lists the start as TBD.",
    )
    player_out_reason: str = Field(
        ...,
        description="Why the slot is available: an ESPN injury status (OUT / INJURY_RESERVE / …), 'no game', 'lower projection', or 'empty slot' when action is 'start'.",
    )


class LineupCheck(_Strict):
    """Current-vs-optimal diff for one team over one horizon."""
    horizon: Literal["tonight", "week"]
    status: Literal["set", "moves_available"]
    points_left_on_bench: float = Field(
        ...,
        description="Points the recommended moves would capture — the sum of their gains, not optimal-minus-current, so the headline never promises points the moves list doesn't deliver.",
    )
    moves: list[LineupMove] = Field(default_factory=list, description="Minimal swap set, highest gain first.")
    computed_for_period: int = Field(..., description="Scoring period the check was computed against.")
    current_points: float = Field(..., description="Projected points from the lineup as actually set.")
    optimal_points: float = Field(..., description="Projected points from the optimal lineup, locks respected.")
    locked_player_ids: list[int] = Field(
        default_factory=list,
        description="Players whose game already tipped off — immovable, pinned to their current slot. Tonight horizon only.",
    )


class TeamLineupCheck(_Strict):
    """Both horizons for one team."""
    tonight: LineupCheck | None = Field(
        None,
        description="None when nobody on the roster plays in the current period — an off day has no lineup decision.",
    )
    week: LineupCheck
    week_start_period: int
    week_end_period: int


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
    current_matchup: CurrentMatchup | None = Field(
        None,
        description="The in-progress matchup. None during a bye or when the season's schedule doesn't cover the current period.",
    )
    waiver_position: int | None = None
    faab_remaining: int | None = None
    roster: list[RosterEntry]
    needs: TeamNeeds
    lineup_check: TeamLineupCheck | None = Field(
        None,
        description="Current-vs-optimal lineup diff. None when the pipeline ran without a per-day schedule (legacy snapshots / tests).",
    )
    summary: list[str] = Field(default_factory=list, description="Auto-generated bullet summary")
    recent_transaction_ids: list[str] = Field(
        default_factory=list,
        description="IDs of transactions attributable to this team (most recent first); UI joins to transactions_recent.",
    )


class BidGuidance(_Strict):
    """What to bid, priced off this league's own executed claims.

    Deliberately carries `sample_n` and `free_claims` so the UI can show the
    band's provenance. A band with n=26 quoted without its n reads like a
    market rate; quoted with it, it reads like what it is — this league's
    revealed prices. See `pipeline/faab.py` for why the spec's value-tier
    bucketing was dropped.
    """
    suggested_lo: int
    suggested_hi: int
    league_median: int = Field(..., description="Median winning bid across executed claims (zero-bid wins included).")
    league_max: int = Field(..., description="Largest winning bid on record — the tail the '$1-8 market' assumption missed.")
    sample_n: int = Field(..., description="Executed claims behind the band.")
    free_claims: int = Field(..., description="How many of those cost $0 — an uncontested claim is genuinely free.")
    faab_remaining: int | None = Field(None, description="The team's budget at capture; the band is clamped to it.")


class DropCandidate(_Strict):
    """Who to drop to make room — a claim is a pair in a full-roster league."""
    player_id: int
    player_name: str
    net_loss: float = Field(..., description="Points the optimal lineup loses without her. 0 = she never cracked it.")
    is_core: bool = Field(..., description="True when she's top-6 on the roster by season rate — the UI warns rather than proposing it silently.")
    injury_status: str | None = None
    games_this_week: int = 0


class BestFit(_Strict):
    """Which teams gain most from an add — the league-wide view's sub-line."""
    team_id: int
    team_abbrev: str
    net_gain: float


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
    ai_summary_date: str | None = Field(
        None,
        description="ISO date (YYYY-MM-DD) when ai_summary was last authored. Used to surface staleness when a player drops off the top-30 and the summary is not refreshed.",
    )
    # --- Waivers 2.0 (spec §3). Team-scoped fields are None on the
    # league-wide list, which has no roster to measure a gain against.
    net_gain_this_week: float | None = Field(
        None,
        description="Points this add would add to the team's OPTIMAL lineup this week — not her projection. Collapses toward 0 when the team is saturated at her position, which raw projection can't express.",
    )
    net_gain_next_week: float | None = None
    drop_candidate: DropCandidate | None = Field(
        None,
        description="The paired drop. None on the league-wide list.",
    )
    bid_guidance: BidGuidance | None = Field(
        None,
        description="None when the league's claim history is too thin to quote (< faab.MIN_SAMPLE) or the team has no budget left.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Intent tags: 'streamer' (below-median rate, heavy slate — churn) or 'anchor' (at/above-median rate — hold). Empty when neither applies.",
    )
    plays_tonight: bool = Field(
        False,
        description="Her pro team plays in the current scoring period — Hashtag's core filter primitive, and the reason to claim now rather than tomorrow.",
    )
    best_fit: list[BestFit] = Field(
        default_factory=list,
        description="League-wide list only: the teams who gain most from this add, best first.",
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


class InstagramPost(_Strict):
    title: str
    url: str
    published_at: datetime | None = None
    username: str = ""
    post_type: str = "post"   # "post" | "reel" | "comment"


class PlayerSocials(_Strict):
    twitter: str = ""      # handle without @
    instagram: str = ""    # handle without @


class TradePlayer(_Strict):
    """A player referenced in a trade scenario."""
    player_id: int
    name: str
    fantasy_team_id: int
    fantasy_team_abbrev: str
    projected_per_game: float
    bucket: Literal["G", "F", "C"]


class TradePkg(_Strict):
    """A 1–3-player package from one fantasy team."""
    players: list[TradePlayer]
    total_ppg: float


class TradeOffer(_Strict):
    """One potential offer from an opposing team."""
    from_team_id: int
    from_team_abbrev: str
    pkg_received: TradePkg
    value_ratio: float = Field(..., description="pkg total ppg / target ppg; 1.0 = perfectly fair")
    need_fit_score: float = Field(..., description="0–1: how well received players fill team's top need")
    composite_score: float = Field(..., description="0.6×fairness + 0.4×need_fit; higher is better")


class TradeScenario(_Strict):
    """One team's 'trade your best player' analysis."""
    team_id: int
    team_abbrev: str
    team_name: str
    best_player: TradePlayer
    top_need_bucket: Literal["G", "FC"]
    offers: list[TradeOffer] = Field(
        default_factory=list,
        description="Best offer per opposing team, sorted by composite_score descending.",
    )


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
    instagram_posts_by_player: dict[int, list[InstagramPost]] = Field(default_factory=dict)
    player_socials: dict[int, PlayerSocials] = Field(default_factory=dict)
    trade_scenarios: list[TradeScenario] = Field(default_factory=list)
