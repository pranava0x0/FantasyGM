"""Compose `docs/data/state.json` and append to `data/history/transactions.jsonl`.

`state.json` is the single source of truth the frontend reads. Each refresh
rebuilds it from raw snapshots — never mutated in place.

Transactions are also appended (deduped on `transaction_id`) to
`data/history/transactions.jsonl` so we keep a complete history even if
ESPN's transaction window rolls off old entries.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import re

from pipeline import analyze, bluesky as bluesky_mod, instagram as instagram_mod, lineups, news, reddit as reddit_mod, schedule, schema, scoring_formula as sf_mod, summary, twitter as twitter_mod
from pipeline.projections_ext import resolve_external_projections

# Per-player cap on news/social items surfaced in state.json. High enough to
# cover a full season of history (see data/history/{news,social}.jsonl) while
# bounding state.json's page weight for a handful of extremely prolific
# sources. The UI paginates ("show more") rather than rendering all at once.
MAX_HISTORY_PER_PLAYER = 50

# News articles only mention a player directly sometimes — the rest of the
# time they're tagged via the player's pro team (a team recap that happens
# to roster them). Direct mentions are the player's own story and get the
# full cap above; team-only mentions are ambient context and capped much
# lower so a busy team doesn't drown a player's history in generic recaps.
MAX_TEAM_ONLY_NEWS_PER_PLAYER = 10

# ---------------------------------------------------------------------------
# Social-media return-from-injury signal detection
# ---------------------------------------------------------------------------

# Strong multi-word phrases that nearly always mean a player is returning.
# Checked against lowercased post text with simple substring search.
_RETURN_STRONG_PHRASES: frozenset[str] = frozenset({
    "return from injur",
    "returns from injur",
    "back from injur",
    "cleared to play",
    "cleared to return",
    "cleared for play",
    "off the injury report",
    "off ir",
    "off the ir",
    "activated from",
    "coming back from",
    "expected to return",
    "targeting a return",
    "return to action",
    "return to play",
    "back in the lineup",
    "back on the court",
    "return to the court",
    "set to return",
    "making her return",
    "could return",
})

# Injury-context words. A post must contain at least one of these alongside
# a return-action word to qualify as a Tier-2 signal.
_INJURY_CONTEXT_WORDS: frozenset[str] = frozenset({
    "injur", "ir", "sideline", "surgery", "knee", "ankle",
    "achilles", "hamstring", "shoulder", "wrist", "recovery", "rehab",
})

# Return-action words. Used with an injury-context word for Tier-2 detection.
_RETURN_ACTION_WORDS: frozenset[str] = frozenset({
    "return", "returning", "back", "cleared", "activat", "recover",
    "healthy", "practice",
})

# Minimum characters a player name must have before we try to match it
# in social text (guards against matching one-letter tokens).
_MIN_NAME_LEN = 5


def _text_has_return_signal(text: str) -> bool:
    """Return True if lowercase `text` signals a player returning from injury.

    Two-tier:
    1. Strong phrase — single substring match is enough.
    2. Injury-context word AND a return-action word — both must appear.
    """
    for phrase in _RETURN_STRONG_PHRASES:
        if phrase in text:
            return True
    has_injury = any(w in text for w in _INJURY_CONTEXT_WORDS)
    has_return = any(w in text for w in _RETURN_ACTION_WORDS)
    return has_injury and has_return


def _collect_social_texts(
    reddit_posts: list[dict[str, Any]] | None,
    twitter_posts: list[dict[str, Any]] | None,
    bluesky_posts: list[dict[str, Any]] | None,
    news_raw: dict[str, Any] | None,
    instagram_posts: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Flatten all post/headline texts into a single lowercased list."""
    texts: list[str] = []
    for source in (reddit_posts or [], twitter_posts or [], bluesky_posts or [], instagram_posts or []):
        for post in source:
            t = str(post.get("title") or "").lower().strip()
            if t:
                texts.append(t)
    if news_raw:
        articles = news_raw.get("articles") or []
        for a in articles:
            for field in ("headline", "description"):
                t = str(a.get(field) or "").lower().strip()
                if t:
                    texts.append(t)
    return texts


def _detect_social_return_signals(
    player_id_to_name: dict[int, str],
    fa_player_ids: set[int],
    social_texts: list[str],
) -> set[int]:
    """Return IDs of FA players whose name appears in a return-signal text.

    For each free-agent player, check whether any social/news text both
    mentions their name and contains a return-from-injury signal. Name
    matching is done on last name (≥5 chars) plus first-initial to reduce
    false positives from common last names.
    """
    if not social_texts or not fa_player_ids:
        return set()

    result: set[int] = set()
    for pid in fa_player_ids:
        full_name = (player_id_to_name.get(pid) or "").strip()
        if not full_name:
            continue

        parts = full_name.lower().split()
        # Build match tokens: full name, last name (if ≥5 chars), and
        # first-initial + last ("a. wilson").
        tokens: list[str] = [full_name.lower()]
        if len(parts) >= 2 and len(parts[-1]) >= _MIN_NAME_LEN:
            tokens.append(parts[-1])
            tokens.append(f"{parts[0][0]}. {parts[-1]}")

        for text in social_texts:
            if any(tok in text for tok in tokens):
                if _text_has_return_signal(text):
                    result.add(pid)
                    log.debug(
                        "build_state: social return signal → %s (pid=%d)", full_name, pid
                    )
                    break  # one match per player is enough

    return result

log = logging.getLogger(__name__)


def build_state(
    *,
    league_raw: dict[str, Any],
    free_agents_raw: dict[str, Any],
    captured_at: datetime,
    news_raw: dict[str, Any] | None = None,
    reddit_posts: list[dict[str, Any]] | None = None,
    twitter_posts: list[dict[str, Any]] | None = None,
    bluesky_posts: list[dict[str, Any]] | None = None,
    instagram_posts: list[dict[str, Any]] | None = None,
    player_socials_raw: dict[str, dict[str, str]] | None = None,
    ai_summaries: dict[str, str] | None = None,
    summary_dates: dict[str, str] | None = None,
    ext_projections_by_source: dict[str, dict[str, float]] | None = None,
    player_game_log: dict[int, list[dict[str, Any]]] | None = None,
    extra_transactions: list[dict[str, Any]] | None = None,
    extra_news: list[dict[str, Any]] | None = None,
    extra_reddit: list[dict[str, Any]] | None = None,
    extra_twitter: list[dict[str, Any]] | None = None,
    extra_bluesky: list[dict[str, Any]] | None = None,
    extra_instagram: list[dict[str, Any]] | None = None,
) -> schema.LeagueState:
    """Compose the LeagueState from raw responses + analysis.

    `news_raw` is ESPN's public WNBA news feed. When None we skip news.
    `reddit_posts` is the normalized output of `reddit.fetch_reddit()`.
    `twitter_posts` is the normalized output of `twitter.fetch_twitter()`.
    `ext_projections_by_source` maps source_name → {player_name_lower: per_game_fpts}
    from CBS Sports / Yahoo Sports; used to blend multi-source projections.
    `player_game_log` is the accumulated per-game history from game_log.py —
    {player_id → [{scoring_period_id, fantasy_points}]}. Extends rolling
    averages and "Last 2 weeks" displays beyond ESPN's current snapshot window.
    `extra_news`/`extra_reddit`/`extra_twitter`/`extra_bluesky`/`extra_instagram`
    are the persistent archives from `data/history/{news,social}.jsonl` — merged
    in (deduped by id/url) so a player's history survives after today's items
    scroll out of the live feed. Same pattern as `extra_transactions`.
    All default to None so tests and legacy callers don't need to change.
    """
    # Compute the upcoming week's game-count signal first; team views and
    # waiver ranking both consume it. Without it, team production is
    # measured in 1-day projection sums which conflates roster
    # composition with player quality.
    week_start, week_end = schedule.upcoming_week_periods(league_raw)
    games_by_pro_team = schedule.games_per_team(league_raw, week_start, week_end)
    nw_start, nw_end = schedule.next_week_periods(league_raw)
    games_by_pro_team_next_week = schedule.games_per_team(league_raw, nw_start, nw_end)

    current_period = int(league_raw.get("scoringPeriodId") or 0)
    rolling_window_start = max(1, current_period - 14)
    games_in_rolling_window = schedule.games_per_team(league_raw, rolling_window_start, current_period)

    # Per-day schedule with tip-off times, spanning today's slate through the
    # end of the upcoming week. `lineups.check_team` needs the day granularity
    # for "tonight" and the tip-off times to know which players are locked.
    games_by_period = schedule.games_by_period(
        league_raw, min(current_period, week_start), week_end
    )

    # Build the player name index first so external projections can be
    # resolved by player_id before the main analysis runs.
    player_name_index = _player_name_index(league_raw, free_agents_raw)

    # Resolve external projections (CBS, Yahoo) from name → player_id.
    ext_projections_by_player: dict[int, dict[str, float]] = {}
    if ext_projections_by_source:
        ext_projections_by_player = resolve_external_projections(
            player_name_index, ext_projections_by_source
        )
        log.info(
            "build_state: external projections resolved for %d players from %d sources",
            len(ext_projections_by_player),
            len(ext_projections_by_source),
        )

    teams_view = analyze.build_team_views(
        league_raw,
        games_by_pro_team=games_by_pro_team,
        games_by_pro_team_next_week=games_by_pro_team_next_week,
        ext_projections_by_player=ext_projections_by_player,
        player_game_log=player_game_log,
    )
    needs = analyze.compute_team_needs(teams_view)

    # Current-vs-optimal lineup diff per team. `captured_at` is the honest
    # "now" for lock detection — it's the instant this snapshot describes.
    # One team's bad roster row must never sink the whole refresh, so each
    # check is isolated; a team that fails simply ships no lineup panel.
    lineup_checks: dict[int, schema.TeamLineupCheck] = {}
    for t in teams_view:
        team_id_for_check = int(t["team_id"])
        try:
            lineup_checks[team_id_for_check] = schema.TeamLineupCheck(
                **lineups.check_team(
                    t["roster"],
                    current_period=current_period,
                    week_start=week_start,
                    week_end=week_end,
                    games_by_period=games_by_period,
                    now=captured_at,
                )
            )
        except Exception:
            log.exception(
                "build_state: lineup check failed for team %s — shipping without a lineup panel",
                team_id_for_check,
            )
    moves_total = sum(
        len(c.tonight.moves) if c.tonight else 0 for c in lineup_checks.values()
    )
    log.info(
        "build_state: lineup checks for %d/%d teams — %d tonight move(s) available league-wide",
        len(lineup_checks), len(teams_view), moves_total,
    )

    ranked_fas_dicts = analyze.rank_free_agents(
        free_agents_raw,
        scoring_period_id=current_period,
        limit=40,
        games_by_pro_team=games_by_pro_team,
        games_by_pro_team_next_week=games_by_pro_team_next_week,
        games_in_rolling_window_by_team=games_in_rolling_window,
        ext_projections_by_player=ext_projections_by_player,
        player_game_log=player_game_log,
    )

    # Social-media return signal: scan news + social posts for players whose
    # name appears alongside return-from-injury language. If confirmed, set
    # injury_signal = "returning" and apply the same +15% boost used by the
    # game-absence detector (idempotent — already-flagged players aren't
    # double-boosted).
    social_texts = _collect_social_texts(reddit_posts, twitter_posts, bluesky_posts, news_raw)
    fa_ids = {int(d.get("player_id") or 0) for d in ranked_fas_dicts}
    social_returners = _detect_social_return_signals(player_name_index, fa_ids, social_texts)
    if social_returners:
        log.info(
            "build_state: social return signals for %d player(s): %s",
            len(social_returners),
            [player_name_index.get(pid, str(pid)) for pid in sorted(social_returners)],
        )
    for fa in ranked_fas_dicts:
        if int(fa.get("player_id") or 0) in social_returners:
            if fa.get("injury_signal") is None:
                fa["injury_signal"] = "returning"
                fa["base_score"] = round(float(fa["base_score"]) * analyze._RETURN_BOOST, 2)
    # Re-sort so social-boosted players surface at the correct rank in the
    # overall list (absence-detector boosts were applied before the first sort
    # in rank_free_agents; social boosts land after, so we need a second pass).
    if social_returners:
        ranked_fas_dicts.sort(
            key=lambda r: (r["projected_points_next_week"], r["base_score"]),
            reverse=True,
        )

    # AI "why pick them up" summaries, keyed by str(player_id). Authored
    # out-of-band (data/ai_summaries.json) and attached to waiver targets.
    ai_summaries = ai_summaries or {}
    summary_dates = summary_dates or {}

    # Normalize transactions once up front — we need them for both the
    # global feed and per-team grouping.
    transactions_raw = analyze.normalize_transactions(league_raw)

    # Merge in extra transactions (e.g. full season history from transactions.jsonl).
    # extra_transactions dicts have occurred_at as ISO string or datetime.
    if extra_transactions:
        seen_ids = {t["transaction_id"] for t in transactions_raw}
        for tx in extra_transactions:
            tid = tx.get("transaction_id")
            if not tid or tid in seen_ids:
                continue
            # Normalize occurred_at to datetime if it came as a string
            occ = tx.get("occurred_at")
            if isinstance(occ, str):
                try:
                    occ = datetime.fromisoformat(occ.replace("Z", "+00:00"))
                except ValueError:
                    occ = None
            transactions_raw.append({**tx, "occurred_at": occ})
            seen_ids.add(tid)
        transactions_raw.sort(
            key=lambda r: r["occurred_at"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        log.info("build_state: merged %d extra transactions (total=%d)", len(extra_transactions), len(transactions_raw))

    current_matchup_period = int((league_raw.get("status") or {}).get("currentMatchupPeriod") or 0)

    # Generate the per-team narrative bullets up front, using the
    # already-built teams_view + needs + transactions.
    summaries = summary.build_team_summaries(
        teams_view,
        needs,
        transactions_raw,
        matchup_period_id=current_matchup_period,
    )

    matchup_history_by_team = _build_matchup_history(league_raw, current_matchup_period)
    current_matchup_by_team = _build_current_matchups(league_raw, current_matchup_period)

    # Per-team transaction id grouping (newest first).
    team_txn_ids: dict[int, list[str]] = {}
    for tx in transactions_raw:
        tid = tx.get("team_id")
        if tid is None:
            continue
        team_txn_ids.setdefault(int(tid), []).append(str(tx.get("transaction_id") or ""))

    teams: list[schema.TeamState] = []
    by_team_targets: list[schema.WaiverTargetsByTeam] = []
    for t in teams_view:
        w = needs[t["team_id"]]
        roster_entries = [
            schema.RosterEntry(
                player=schema.PlayerRef(
                    player_id=int(p["player_id"]),
                    name=str(p["name"] or "Unknown"),
                    team=_pro_team_abbr(p["pro_team_id"]),
                    position=p["position"],
                    bucket=p["bucket"],
                    eligible_slots=p["eligible_slots"],
                    injury_status=p["injury_status"],
                ),
                lineup_slot_id=int(p["lineup_slot_id"]),
                lineup_slot_label=p["lineup_slot_label"],
                is_active=bool(p["is_active"]),
                projected_points=float(p.get("projected_points") or 0.0),
                projected_per_game=float(p.get("projected_per_game") or 0.0) or None,
                projected_points_this_week=float(p.get("projected_points_this_week") or 0.0) or None,
                games_this_week=int(p.get("games_this_week") or 0),
                projected_points_next_week=float(p.get("projected_points_next_week") or 0.0) or None,
                games_next_week=int(p.get("games_next_week") or 0),
                actual_points=p["actual_points"],
            )
            for p in t["roster"]
            if p["player_id"] is not None
        ]

        team_id_int = int(t["team_id"])
        team_state = schema.TeamState(
            team_id=team_id_int,
            abbrev=str(t["abbrev"] or f"T{t['team_id']}"),
            name=str(t["name"]),
            logo_url=t["logo"],
            division_id=t["division_id"],
            record=schema.TeamRecord(**t["record"]),
            matchup_history=matchup_history_by_team.get(team_id_int, []),
            current_matchup=current_matchup_by_team.get(team_id_int),
            waiver_position=t["waiver_position"],
            faab_remaining=t["faab_remaining"],
            roster=roster_entries,
            needs=schema.TeamNeeds(
                guard_proj=w["guard_proj"],
                forward_proj=w["forward_proj"],
                center_proj=w["center_proj"],
                frontcourt_proj=w["frontcourt_proj"],
                guard_gap_vs_league=w["guard_gap_vs_league"],
                forward_gap_vs_league=w["forward_gap_vs_league"],
                center_gap_vs_league=w["center_gap_vs_league"],
                frontcourt_gap_vs_league=w["frontcourt_gap_vs_league"],
                top_need_bucket=w["top_need_bucket"],
            ),
            lineup_check=lineup_checks.get(team_id_int),
            summary=summaries.get(team_id_int, []),
            recent_transaction_ids=team_txn_ids.get(team_id_int, []),
        )
        teams.append(team_state)

        team_targets = analyze.waiver_targets_for_team(
            w, ranked_fas_dicts,
            active_counts={k: int(v) for k, v in t["active_counts"].items()},
            limit=10,
        )
        by_team_targets.append(
            schema.WaiverTargetsByTeam(
                team_id=team_id_int,
                targets=[_to_waiver_target(d, ai_summaries, summary_dates) for d in team_targets],
            )
        )

    overall_targets = [_to_waiver_target(d, ai_summaries, summary_dates) for d in ranked_fas_dicts[:30]]

    # Trade scenario evaluator: per-team "trade your best player" analysis.
    from pipeline import trades as trades_mod
    trade_scenarios = trades_mod.build_trade_scenarios(teams)

    transactions_view: list[schema.Transaction] = []
    for tx in transactions_raw:
        items = [
            schema.TransactionItem(
                player_id=int(it["player_id"]),
                player_name=player_name_index.get(it["player_id"]),
                from_team_id=it["from_team_id"],
                to_team_id=it["to_team_id"],
                from_slot_id=it["from_slot_id"],
                to_slot_id=it["to_slot_id"],
                type=it["type"],
            )
            for it in tx["items"]
            if it["player_id"] is not None
        ]
        transactions_view.append(schema.Transaction(
            transaction_id=str(tx["transaction_id"]),
            occurred_at=tx["occurred_at"] or captured_at,
            scoring_period_id=tx["scoring_period_id"],
            team_id=tx["team_id"],
            type=tx["type"],
            bid_amount=tx["bid_amount"],
            status=tx["status"],
            items=items,
        ))

    meta = schema.LeagueMeta(
        league_id=int(league_raw.get("id") or 0),
        league_name=str((league_raw.get("settings") or {}).get("name") or "Unknown League"),
        season_id=int(league_raw.get("seasonId") or 0),
        scoring_period_id=int(league_raw.get("scoringPeriodId") or 0),
        matchup_period_id=current_matchup_period,
        scoring_type=str(((league_raw.get("settings") or {}).get("scoringSettings") or {}).get("scoringType") or "UNKNOWN"),
        team_count=len(teams),
        captured_at=captured_at,
        week_start_period=week_start,
        week_end_period=week_end,
    )

    # News: today's feed merged with the persistent archive (deduped by
    # article id) so a player's news survives after it scrolls out of
    # ESPN's own feed window. Falls back to just the archive when the
    # caller didn't pass news_raw (e.g. social-only refresh).
    news_items: list[schema.NewsItem] = []
    news_by_player: dict[int, list[schema.NewsItem]] = {}
    articles = news.normalize_articles(news_raw) if news_raw else []
    if extra_news:
        seen_article_ids = {a["id"] for a in articles}
        for a in extra_news:
            aid = a.get("id")
            if aid is None or aid in seen_article_ids:
                continue
            articles.append({**a, "published_at": _coerce_dt(a.get("published_at"))})
            seen_article_ids.add(aid)
        articles.sort(key=lambda r: r["published_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    if articles:
        news_items = [_to_news_item(a) for a in articles[:30]]

        # Build the player → news map (rostered players + waiver targets).
        all_player_ids: set[int] = set()
        pro_team_to_players: dict[int, set[int]] = {}
        for t in teams:
            for r in t.roster:
                all_player_ids.add(r.player.player_id)
                if r.player.team is not None:
                    pass  # We need proTeamId int, not abbrev — see below.
        # Rebuild proTeam → player_id map from raw league for proper int keys.
        for raw_team in league_raw.get("teams") or []:
            for e in (raw_team.get("roster") or {}).get("entries") or []:
                p = (e.get("playerPoolEntry") or {}).get("player") or {}
                pid = p.get("id")
                tid = p.get("proTeamId")
                if pid is not None and tid is not None:
                    pro_team_to_players.setdefault(int(tid), set()).add(int(pid))
        for fa_entry in free_agents_raw.get("players") or []:
            p = fa_entry.get("player") or {}
            pid = p.get("id")
            tid = p.get("proTeamId")
            if pid is not None:
                all_player_ids.add(int(pid))
            if pid is not None and tid is not None:
                pro_team_to_players.setdefault(int(tid), set()).add(int(pid))

        raw_player_to_articles = news.match_to_players(
            articles, all_player_ids, pro_team_to_players,
        )
        # Direct mentions (the player's own story) get the full cap; team-only
        # mentions (roster overlap, not named) fill in the rest at a much
        # lower cap so one prolific team doesn't drown a player's history.
        for pid, arts in raw_player_to_articles.items():
            direct = [a for a in arts if pid in a["athlete_ids"]][:MAX_HISTORY_PER_PLAYER]
            team_only = [a for a in arts if pid not in a["athlete_ids"]][:MAX_TEAM_ONLY_NEWS_PER_PLAYER]
            combined = sorted(
                direct + team_only,
                key=lambda a: a["published_at"] or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            news_by_player[pid] = [_to_news_item(a) for a in combined]

    # Reddit: today's fetch merged with the persistent archive (deduped by
    # url), matched to players by name.
    reddit_by_player: dict[int, list[schema.RedditPost]] = {}
    merged_reddit = _merge_by_url(reddit_posts, extra_reddit)
    if merged_reddit:
        raw_reddit_by_player = reddit_mod.match_to_players(merged_reddit, player_name_index)
        for pid, posts in raw_reddit_by_player.items():
            reddit_by_player[pid] = [
                schema.RedditPost(
                    title=p["title"],
                    url=p["url"],
                    published_at=p.get("published_at"),
                    subreddit=p.get("subreddit") or "wnba",
                )
                for p in posts[:MAX_HISTORY_PER_PLAYER]
            ]

    # Twitter/X: same merge-then-match pattern.
    twitter_by_player: dict[int, list[schema.TwitterPost]] = {}
    merged_twitter = _merge_by_url(twitter_posts, extra_twitter)
    if merged_twitter:
        raw_twitter_by_player = twitter_mod.match_to_players(merged_twitter, player_name_index)
        for pid, tweets in raw_twitter_by_player.items():
            twitter_by_player[pid] = [
                schema.TwitterPost(
                    title=t["title"],
                    url=t["url"],
                    published_at=t.get("published_at"),
                    screen_name=t.get("screen_name") or "",
                )
                for t in tweets[:MAX_HISTORY_PER_PLAYER]
            ]

    # Bluesky: same merge-then-match pattern.
    bluesky_by_player: dict[int, list[schema.BlueskyPost]] = {}
    merged_bluesky = _merge_by_url(bluesky_posts, extra_bluesky)
    if merged_bluesky:
        raw_bsky_by_player = bluesky_mod.match_to_players(merged_bluesky, player_name_index)
        for pid, posts in raw_bsky_by_player.items():
            bluesky_by_player[pid] = [
                schema.BlueskyPost(
                    title=p["title"],
                    url=p["url"],
                    published_at=p.get("published_at"),
                    handle=p.get("handle") or "",
                )
                for p in posts[:MAX_HISTORY_PER_PLAYER]
            ]

    # Instagram: same merge-then-match pattern; matches by profile handle or name mention.
    instagram_by_player: dict[int, list[schema.InstagramPost]] = {}
    merged_instagram = _merge_by_url(instagram_posts, extra_instagram)
    if merged_instagram:
        socials_for_match = {
            int(pid): v for pid, v in (player_socials_raw or {}).items() if pid.isdigit()
        }
        raw_ig_by_player = instagram_mod.match_to_players(
            merged_instagram, player_name_index, player_socials=socials_for_match
        )
        for pid, posts in raw_ig_by_player.items():
            instagram_by_player[pid] = [
                schema.InstagramPost(
                    title=p["title"],
                    url=p["url"],
                    published_at=p.get("published_at"),
                    username=p.get("username") or "",
                    post_type=p.get("post_type") or "post",
                )
                for p in posts[:MAX_HISTORY_PER_PLAYER]
            ]

    # Player social profiles (for direct links in the UI).
    player_socials_by_id: dict[int, schema.PlayerSocials] = {}
    for pid_str, handles in (player_socials_raw or {}).items():
        try:
            pid = int(pid_str)
        except (ValueError, TypeError):
            continue
        player_socials_by_id[pid] = schema.PlayerSocials(
            twitter=handles.get("twitter") or "",
            instagram=handles.get("instagram") or "",
        )

    return schema.LeagueState(
        meta=meta,
        teams=teams,
        transactions_recent=transactions_view,
        waiver_targets_overall=overall_targets,
        waiver_targets_by_team=by_team_targets,
        news_recent=news_items,
        news_by_player=news_by_player,
        reddit_posts_by_player=reddit_by_player,
        twitter_posts_by_player=twitter_by_player,
        bluesky_posts_by_player=bluesky_by_player,
        instagram_posts_by_player=instagram_by_player,
        player_socials=player_socials_by_id,
        trade_scenarios=trade_scenarios,
    )


def _to_news_item(d: dict[str, Any]) -> schema.NewsItem:
    return schema.NewsItem(
        id=int(d["id"]),
        headline=d["headline"],
        description=d.get("description") or "",
        url=d.get("url"),
        published_at=d.get("published_at"),
        athlete_ids=list(d.get("athlete_ids") or []),
        pro_team_ids=list(d.get("pro_team_ids") or []),
    )


def write_state(state: schema.LeagueState, docs_root: Path) -> Path:
    """Write `docs/data/state.json`. Idempotent."""
    out = docs_root / "data" / "state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    body = state.model_dump_json(indent=2)
    out.write_text(body + "\n")
    log.info("build_state: wrote %s (%d bytes)", out, len(body))
    return out


def append_transactions_history(
    state: schema.LeagueState,
    history_root: Path,
) -> int:
    """Append any new transactions to `data/history/transactions.jsonl`.

    Returns count of new entries appended.
    """
    history_root.mkdir(parents=True, exist_ok=True)
    log_path = history_root / "transactions.jsonl"
    seen: set[str] = set()
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                seen.add(json.loads(line)["transaction_id"])
            except (json.JSONDecodeError, KeyError):
                continue

    appended = 0
    with log_path.open("a") as fp:
        for tx in state.transactions_recent:
            if tx.transaction_id in seen:
                continue
            fp.write(tx.model_dump_json() + "\n")
            seen.add(tx.transaction_id)
            appended += 1
    log.info("build_state: appended %d new transactions to %s", appended, log_path)
    return appended


def append_news_history(news_raw: dict[str, Any] | None, history_root: Path) -> int:
    """Append today's normalized articles (deduped by `id`) to `data/history/news.jsonl`.

    Returns count of new entries appended.
    """
    if not news_raw:
        return 0
    history_root.mkdir(parents=True, exist_ok=True)
    log_path = history_root / "news.jsonl"
    seen: set[int] = set()
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                seen.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError):
                continue

    appended = 0
    with log_path.open("a") as fp:
        for a in news.normalize_articles(news_raw):
            if a["id"] in seen:
                continue
            fp.write(_to_news_item(a).model_dump_json() + "\n")
            seen.add(a["id"])
            appended += 1
    log.info("build_state: appended %d new articles to %s", appended, log_path)
    return appended


def load_news_history(history_root: Path) -> list[dict[str, Any]]:
    """Load the persistent news archive as plain dicts for `extra_news=`."""
    log_path = history_root / "news.jsonl"
    if not log_path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in log_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# Social sources keyed the same way build_state's extra_* params are named,
# so callers can round-trip a single dict through append/load.
_SOCIAL_SOURCES = ("reddit", "twitter", "bluesky", "instagram")


def append_social_history(posts_by_source: dict[str, list[dict[str, Any]]], history_root: Path) -> int:
    """Append today's social posts (deduped by `(source, url)`) to `data/history/social.jsonl`.

    `posts_by_source` maps source name ("reddit"/"twitter"/"bluesky"/"instagram")
    to that source's normalized post dicts. Returns count of new entries appended.
    """
    history_root.mkdir(parents=True, exist_ok=True)
    log_path = history_root / "social.jsonl"
    seen: set[tuple[str, str]] = set()
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                seen.add((row["source"], row["url"]))
            except (json.JSONDecodeError, KeyError):
                continue

    appended = 0
    with log_path.open("a") as fp:
        for source in _SOCIAL_SOURCES:
            for p in posts_by_source.get(source) or []:
                url = p.get("url")
                if not url or (source, url) in seen:
                    continue
                published = p.get("published_at")
                row = {
                    "source": source,
                    "title": p.get("title", ""),
                    "url": url,
                    "published_at": published.isoformat() if isinstance(published, datetime) else published,
                }
                # Carry the source-specific attribution field (subreddit/handle/etc).
                for extra_key in ("subreddit", "handle", "screen_name", "username", "post_type"):
                    if extra_key in p:
                        row[extra_key] = p[extra_key]
                fp.write(json.dumps(row) + "\n")
                seen.add((source, url))
                appended += 1
    log.info("build_state: appended %d new social posts to %s", appended, log_path)
    return appended


def load_social_history(history_root: Path) -> dict[str, list[dict[str, Any]]]:
    """Load the persistent social archive, split by source, for `extra_reddit=`/etc."""
    log_path = history_root / "social.jsonl"
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in _SOCIAL_SOURCES}
    if not log_path.exists():
        return out
    for line in log_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            source = row.pop("source")
        except (json.JSONDecodeError, KeyError):
            continue
        if source in out:
            out[source].append(row)
    return out


# --- helpers --------------------------------------------------------------

def _coerce_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 string (from a jsonl archive) into a datetime; pass datetimes through."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _merge_by_url(
    today: list[dict[str, Any]] | None,
    archive: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge today's fetch with the persistent archive, deduped by `url`.

    `archive` entries have `published_at` as an ISO string (loaded from
    jsonl); coerced to datetime so callers can sort/compare uniformly.
    """
    merged = list(today or [])
    if not archive:
        return merged
    seen_urls = {p["url"] for p in merged if p.get("url")}
    for p in archive:
        url = p.get("url")
        if not url or url in seen_urls:
            continue
        merged.append({**p, "published_at": _coerce_dt(p.get("published_at"))})
        seen_urls.add(url)
    return merged

def _build_matchup_history(
    league_raw: dict[str, Any],
    current_matchup_period: int,
) -> dict[int, list[schema.MatchupResult]]:
    """Return a per-team dict of completed H2H matchup results (sorted ascending).

    Only includes periods strictly before the current matchup period, where
    both sides have non-zero points (i.e., the week is complete).
    """
    by_team: dict[int, list[schema.MatchupResult]] = {}
    for m in league_raw.get("schedule") or []:
        period = int(m.get("matchupPeriodId") or 0)
        if period >= current_matchup_period:
            continue
        home = m.get("home") or {}
        away = m.get("away") or {}
        home_id = int(home.get("teamId") or 0)
        away_id = int(away.get("teamId") or 0)
        home_pts = float(home.get("totalPoints") or 0.0)
        away_pts = float(away.get("totalPoints") or 0.0)
        if home_pts == 0.0 and away_pts == 0.0:
            continue
        if home_pts > away_pts:
            home_result, away_result = "W", "L"
        elif away_pts > home_pts:
            home_result, away_result = "L", "W"
        else:
            home_result, away_result = "T", "T"
        if home_id:
            by_team.setdefault(home_id, []).append(schema.MatchupResult(
                matchup_period_id=period,
                opponent_team_id=away_id,
                team_points=home_pts,
                opponent_points=away_pts,
                result=home_result,
            ))
        if away_id:
            by_team.setdefault(away_id, []).append(schema.MatchupResult(
                matchup_period_id=period,
                opponent_team_id=home_id,
                team_points=away_pts,
                opponent_points=home_pts,
                result=away_result,
            ))
    for results in by_team.values():
        results.sort(key=lambda r: r.matchup_period_id)
    return by_team


def _build_current_matchups(
    league_raw: dict[str, Any],
    current_matchup_period: int,
) -> dict[int, schema.CurrentMatchup]:
    """Return {team_id: CurrentMatchup} for the in-progress matchup period.

    Unlike `_build_matchup_history` this keeps zero-zero pairings — a matchup
    the user hasn't started scoring yet is still the matchup they're in, and
    it's exactly when "who am I playing?" matters most.
    """
    by_team: dict[int, schema.CurrentMatchup] = {}
    for m in league_raw.get("schedule") or []:
        if int(m.get("matchupPeriodId") or 0) != current_matchup_period:
            continue
        home = m.get("home") or {}
        away = m.get("away") or {}
        home_id = int(home.get("teamId") or 0)
        away_id = int(away.get("teamId") or 0)
        home_pts = float(home.get("totalPoints") or 0.0)
        away_pts = float(away.get("totalPoints") or 0.0)
        if home_id and away_id:
            by_team[home_id] = schema.CurrentMatchup(
                matchup_period_id=current_matchup_period,
                opponent_team_id=away_id,
                team_points=home_pts,
                opponent_points=away_pts,
            )
            by_team[away_id] = schema.CurrentMatchup(
                matchup_period_id=current_matchup_period,
                opponent_team_id=home_id,
                team_points=away_pts,
                opponent_points=home_pts,
            )
    return by_team


def _player_name_index(
    league_raw: dict[str, Any],
    free_agents_raw: dict[str, Any],
) -> dict[int, str]:
    idx: dict[int, str] = {}
    for t in league_raw.get("teams") or []:
        for e in (t.get("roster") or {}).get("entries") or []:
            pool = e.get("playerPoolEntry") or {}
            p = pool.get("player") or {}
            if p.get("id") is not None and p.get("fullName"):
                idx[int(p["id"])] = str(p["fullName"])
    for entry in free_agents_raw.get("players") or []:
        p = entry.get("player") or {}
        if p.get("id") is not None and p.get("fullName") and int(p["id"]) not in idx:
            idx[int(p["id"])] = str(p["fullName"])
    return idx


def _to_waiver_target(
    d: dict[str, Any],
    ai_summaries: dict[str, str] | None = None,
    summary_dates: dict[str, str] | None = None,
) -> schema.WaiverTarget:
    pid = str(d["player_id"])
    return schema.WaiverTarget(
        player=schema.PlayerRef(
            player_id=int(d["player_id"]),
            name=str(d["name"] or "Unknown"),
            team=_pro_team_abbr(d["pro_team_id"]),
            position=d["position"],
            bucket=d["bucket"],
            eligible_slots=d["eligible_slots"],
            injury_status=d["injury_status"],
        ),
        projected_points_next_period=float(d["projected_points_next_period"]),
        projected_per_game=float(d.get("projected_per_game", 0.0)),
        projected_points_this_week=float(d.get("projected_points_this_week", d.get("base_score", 0.0))),
        games_this_week=int(d.get("games_this_week", 0)),
        projected_points_next_week=float(d.get("projected_points_next_week", 0.0)),
        games_next_week=int(d.get("games_next_week", 0)),
        season_avg_points=d["season_avg_points"],
        percent_owned=d["percent_owned"],
        percent_change=d["percent_change"],
        base_score=float(d["base_score"]),
        injury_signal=d.get("injury_signal"),
        recent_games=[
            schema.RecentGame(
                scoring_period_id=int(g["scoring_period_id"]),
                fantasy_points=float(g["fantasy_points"]),
            )
            for g in (d.get("recent_games") or [])
        ],
        ai_summary=(ai_summaries or {}).get(pid),
        ai_summary_date=(summary_dates or {}).get(pid),
    )


# WNBA pro team ID -> abbreviation. CANONICAL map, pulled from ESPN's
# public site API on 2026-05-17:
#
#   curl https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams
#
# To refresh: `python scripts/refresh_pro_teams.py`.
#
# Earlier versions of this file had a guess-based map adapted from NBA
# conventions; almost every ID was wrong (3 was DAL not ATL, 6 was LA not
# CONN, 19 was CHI not WSH, etc.). The lesson: do not infer WNBA team IDs
# from NBA habits; ESPN assigns them independently and the league's
# expansion teams (TOR / GS / POR) have 6-digit IDs. See CLAUDE.md scar
# tissue.
#
# ID 0 means "no team" (free-agent / unsigned) — intentionally absent.
WNBA_TEAM_ABBR: dict[int, str] = {
    3: "DAL",       # Dallas Wings
    5: "IND",       # Indiana Fever
    6: "LA",        # Los Angeles Sparks
    8: "MIN",       # Minnesota Lynx
    9: "NY",        # New York Liberty
    11: "PHX",      # Phoenix Mercury
    14: "SEA",      # Seattle Storm
    16: "WSH",      # Washington Mystics
    17: "LV",       # Las Vegas Aces
    18: "CON",      # Connecticut Sun
    19: "CHI",      # Chicago Sky
    20: "ATL",      # Atlanta Dream
    129689: "GS",   # Golden State Valkyries (2026 expansion)
    131935: "TOR",  # Toronto Tempo (2026 expansion)
    132052: "POR",  # Portland Fire (2026 expansion)
}


def _pro_team_abbr(pro_team_id: int | None) -> str | None:
    if pro_team_id is None:
        return None
    return WNBA_TEAM_ABBR.get(int(pro_team_id))
