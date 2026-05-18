"""Per-team narrative summary — short bullet points the UI shows in the
team pop-out, marked clearly as auto-generated per DESIGN.md § 11.

This is deterministic, not LLM-driven. Inputs are the same `teams_view`
+ `transactions` already in `LeagueState`; outputs are 3–5 short
bullet strings per team. A real LLM-driven summary (running at refresh
time via the Anthropic SDK) is on BACKLOG.md.

The bullets follow a fixed precedence so the team's biggest signal
surfaces first:

  1. Win-loss record (only included once we're past matchup 1)
  2. Top need + the gap, framed proactively as "add more X"
  3. Top per-week projected scorer on the roster + their bucket
  4. Recent transaction activity (count + most-recent type)
  5. Active-roster shape ("guard-heavy", "frontcourt-balanced", etc.)

Empty bullets are dropped — a team in the first day of the season might
return only 2–3.
"""

from __future__ import annotations

from typing import Any


def build_team_summaries(
    teams_view: list[dict[str, Any]],
    needs_by_team: dict[int, dict[str, Any]],
    transactions: list[dict[str, Any]],
    *,
    matchup_period_id: int = 0,
) -> dict[int, list[str]]:
    """Generate bullet-list summaries for every team.

    `transactions` is the normalized transaction list (post-`analyze.
    normalize_transactions`) — ordered newest-first, with `team_id` set
    when the transaction is attributable to a single team.
    """
    # Group transactions by team for fast counting.
    by_team_txn: dict[int, list[dict[str, Any]]] = {}
    for tx in transactions:
        tid = tx.get("team_id")
        if tid is None:
            continue
        by_team_txn.setdefault(int(tid), []).append(tx)

    out: dict[int, list[str]] = {}
    for t in teams_view:
        tid = int(t["team_id"])
        bullets = list(_bullets_for_team(t, needs_by_team.get(tid, {}),
                                         by_team_txn.get(tid, []),
                                         matchup_period_id=matchup_period_id))
        if bullets:
            out[tid] = bullets
    return out


def _bullets_for_team(
    team_view: dict[str, Any],
    needs: dict[str, Any],
    team_txns: list[dict[str, Any]],
    *,
    matchup_period_id: int,
) -> list[str]:
    bullets: list[str] = []

    record = team_view.get("record") or {}
    wins, losses, ties = int(record.get("wins", 0)), int(record.get("losses", 0)), int(record.get("ties", 0))
    if matchup_period_id >= 2 and (wins + losses + ties) > 0:
        bullets.append(_record_bullet(wins, losses, ties))

    top_need = needs.get("top_need_bucket")
    if top_need == "G":
        gap = float(needs.get("guard_gap_vs_league") or 0.0)
        if gap <= -10.0:
            bullets.append(f"Top need: add Guard production — ~{abs(gap):.0f} pts below league average this week.")
        elif gap < 0:
            bullets.append(f"Room to add a Guard — ~{abs(gap):.0f} pts below league average this week.")
    elif top_need == "FC":
        gap = float(needs.get("frontcourt_gap_vs_league") or 0.0)
        if gap <= -10.0:
            bullets.append(f"Top need: add F/C production — ~{abs(gap):.0f} pts below league average this week.")
        elif gap < 0:
            bullets.append(f"Room to add a F/C — ~{abs(gap):.0f} pts below league average this week.")

    top_scorer = _top_active_scorer(team_view)
    if top_scorer:
        name, pos_label, week_proj, games = top_scorer
        if week_proj > 0:
            bullets.append(f"Top projected scorer this week: {name} ({pos_label}, {games} game{'s' if games != 1 else ''}, ~{week_proj:.0f} pts).")

    txn_bullet = _transaction_bullet(team_txns)
    if txn_bullet:
        bullets.append(txn_bullet)

    shape_bullet = _shape_bullet(team_view)
    if shape_bullet and len(bullets) < 5:
        bullets.append(shape_bullet)

    sched_bullet = _schedule_bullet(team_view)
    if sched_bullet and len(bullets) < 5:
        bullets.append(sched_bullet)

    return bullets[:5]


def _record_bullet(wins: int, losses: int, ties: int) -> str:
    if ties > 0:
        rec = f"{wins}–{losses}–{ties}"
    else:
        rec = f"{wins}–{losses}"
    if wins > losses:
        tone = "above .500 so far"
    elif wins < losses:
        tone = "needs to turn it around"
    else:
        tone = "even record"
    return f"Currently {rec} — {tone}."


def _top_active_scorer(team_view: dict[str, Any]) -> tuple[str, str, float, int] | None:
    best: tuple[str, str, float, int] | None = None
    for p in team_view.get("roster") or []:
        if not p.get("is_active"):
            continue
        week_proj = float(p.get("projected_points_this_week") or 0.0)
        if week_proj <= 0:
            continue
        if best is None or week_proj > best[2]:
            best = (
                str(p.get("name") or "Unknown"),
                _position_label(p),
                week_proj,
                int(p.get("games_this_week") or 0),
            )
    return best


def _position_label(p: dict[str, Any]) -> str:
    bucket = p.get("bucket") or "?"
    team = p.get("pro_team_id")
    if team is None:
        return bucket
    return bucket  # The team abbrev is shown alongside in the UI; bucket suffices in prose.


def _transaction_bullet(team_txns: list[dict[str, Any]]) -> str | None:
    if not team_txns:
        return None
    n = len(team_txns)
    latest_type = (team_txns[0].get("type") or "transaction").lower().replace("_", " ")
    if n == 1:
        return f"Made 1 recent {latest_type} move."
    return f"{n} recent transactions — latest a {latest_type} move."


def _shape_bullet(team_view: dict[str, Any]) -> str | None:
    """Only call out genuinely imbalanced rosters.

    5 G + 4 FC is the typical 50-40-90 Club lineup (2 G slots + 3 UTIL
    typically deployed as guards = 5 Gs), so flagging that as
    "guard-heavy" was noisy. Require 6+ in one bucket for the label.
    """
    counts = team_view.get("active_counts") or {}
    g = int(counts.get("G", 0))
    f = int(counts.get("F", 0))
    c = int(counts.get("C", 0))
    fc = f + c
    if g + fc == 0:
        return None
    if g >= 6:
        return f"Guard-heavy active roster ({g} G vs {fc} F/C)."
    if fc >= 7:
        return f"Frontcourt-heavy active roster ({fc} F/C vs {g} G)."
    return None


def _schedule_bullet(team_view: dict[str, Any]) -> str | None:
    """Emit a games-this-week distribution bullet when interesting.

    Surfaces a useful waiver signal: "5 of 9 starters have 4-game
    weeks" tells you the team is in a strong opportunity window;
    "3 starters on bye / 1-game weeks" signals streaming opportunities.
    """
    starters = [p for p in (team_view.get("roster") or []) if p.get("is_active")]
    if not starters:
        return None
    heavy = sum(1 for p in starters if int(p.get("games_this_week") or 0) >= 4)
    light = sum(1 for p in starters if 1 <= int(p.get("games_this_week") or 0) <= 2)
    byes = sum(1 for p in starters if int(p.get("games_this_week") or 0) == 0)
    if byes >= 2:
        return f"{byes} active starter{'s' if byes != 1 else ''} on bye this week — streaming opportunity."
    if heavy >= 5:
        return f"{heavy} of {len(starters)} active starters have 4-game weeks — strong opportunity slate."
    if light >= 5:
        return f"{light} of {len(starters)} active starters have only 1–2 games this week — light slate."
    return None
