"""Auto-generate "GM take" waiver-target summaries using the Anthropic API.

Runs during `pipeline.refresh` for the top N free agents. Summaries are
cached in `data/ai_summaries.json` keyed by str(player_id) and reused on
subsequent runs so they don't re-generate every refresh — only new players
or those whose `base_score` has changed meaningfully get fresh copy.

Design constraints:
- Uses claude-haiku-4-5 (cheapest tier) for cost efficiency.
- Prompt is compact: ~200 tokens in, ~80 tokens out per player.
- Fails gracefully: network errors or missing API key leave existing
  summaries intact and skip new ones without crashing the pipeline.
- Never stores player owner names (CLAUDE.md privacy rule).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 120
_TOP_N = 25            # generate summaries for the top N ranked free agents
_RESCORE_THRESHOLD = 5.0  # regenerate if base_score changed by more than this


def generate_summaries(
    ranked_fas: list[dict[str, Any]],
    *,
    summaries_path: Path,
    league_name: str = "the league",
    dry_run: bool = False,
) -> dict[str, str]:
    """Generate or refresh AI summaries for the top N waiver targets.

    Returns the full updated summaries dict (player_id_str → summary_text).
    Writes the result back to `summaries_path`.
    """
    try:
        import anthropic
    except ImportError:
        log.warning("ai_summary: anthropic package not installed — skipping. Run: pip install anthropic")
        return _load_existing(summaries_path)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        log.warning("ai_summary: ANTHROPIC_API_KEY not set — skipping generation")
        return _load_existing(summaries_path)

    existing = _load_existing(summaries_path)
    existing_scores: dict[str, float] = {}
    try:
        meta = json.loads(summaries_path.read_text()) if summaries_path.exists() else {}
        existing_scores = meta.get("scores", {})
    except (json.JSONDecodeError, OSError):
        pass

    client = anthropic.Anthropic(api_key=api_key)
    updated: dict[str, str] = dict(existing)
    score_map: dict[str, float] = dict(existing_scores)
    generated = 0

    for fa in ranked_fas[:_TOP_N]:
        pid = str(fa.get("player_id") or "")
        if not pid:
            continue
        name = fa.get("name") or "Unknown"
        base_score = float(fa.get("base_score") or 0.0)
        old_score = float(existing_scores.get(pid, -999))
        already_has = pid in existing

        # Skip regeneration if the summary exists and the score hasn't moved much.
        if already_has and abs(base_score - old_score) < _RESCORE_THRESHOLD:
            log.debug("ai_summary: skipping %s (score unchanged)", name)
            continue

        if dry_run:
            log.info("ai_summary: dry-run — would generate for %s (score=%.1f)", name, base_score)
            continue

        prompt = _build_prompt(fa, league_name)
        try:
            resp = client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            text = (resp.content[0].text or "").strip()
            if text:
                updated[pid] = text
                score_map[pid] = base_score
                generated += 1
                log.info("ai_summary: generated for %s", name)
        except Exception as exc:
            log.warning("ai_summary: failed for %s: %s", name, exc)
            continue

    if generated:
        _write(summaries_path, updated, score_map)
        log.info("ai_summary: wrote %d new/updated summaries to %s", generated, summaries_path)
    else:
        log.info("ai_summary: all %d top-target summaries are current", min(_TOP_N, len(ranked_fas)))

    return updated


def _build_prompt(fa: dict[str, Any], league_name: str) -> str:
    name    = fa.get("name") or "Unknown"
    pos     = fa.get("position") or fa.get("bucket") or "?"
    team    = (fa.get("pro_team_id") and f"pro team ID {fa['pro_team_id']}") or "unknown team"
    ppg     = fa.get("projected_per_game") or 0.0
    avg     = fa.get("season_avg_points") or 0.0
    games   = fa.get("games_this_week") or 0
    owned   = fa.get("percent_owned") or 0.0
    change  = fa.get("percent_change") or 0.0
    signal  = fa.get("injury_signal") or ""

    recent_games = fa.get("recent_games") or []
    recent_str = ""
    if recent_games:
        pts = [f"{g['fantasy_points']:.0f}" for g in recent_games[:5]]
        recent_str = f"Recent games (newest first): {', '.join(pts)} fpts. "

    injury_note = ""
    if signal == "returning":
        injury_note = "NOTE: This player missed most of their team's recent games — potential returner from injury/rest. "

    return (
        f"You are a sharp fantasy WNBA GM writing a one-sentence pickup note for {league_name}.\n\n"
        f"Player: {name} ({pos})\n"
        f"Blended projection: {ppg:.1f} fpts/game · {games} games this week\n"
        f"Season average: {avg:.1f} fpts/game\n"
        f"{recent_str}"
        f"Ownership: {owned:.1f}% owned · {change:+.1f}% 7-day change\n"
        f"{injury_note}"
        f"\nWrite a single GM-voice sentence (≤ 25 words) explaining why to add or avoid this player. "
        f"Be specific about the numbers. No filler. No first-person. Do not start with the player's name."
    )


def _load_existing(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data.get("summaries") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write(path: Path, summaries: dict[str, str], scores: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "model": _MODEL,
        "note": "Auto-generated by pipeline.ai_summary. Do not hand-edit — regenerated on refresh.",
        "scores": scores,
        "summaries": summaries,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
