---
name: refresh-fantasy-gm
description: Run the FantasyGM pipeline against the user's ESPN fantasy WNBA league, summarize what changed, and optionally commit + push so GitHub Pages updates. Use whenever the user asks to "refresh fantasy", "update the league", "pull latest WNBA data", "rerun the GM", or otherwise wants the static site refreshed with the latest ESPN snapshot.
---

# Refresh FantasyGM

Single-purpose skill: pull ESPN, rebuild the static site, show the user what changed, and (with consent) commit + push.

## When to invoke

Trigger when the user asks anything like:
- "refresh fantasy gm" / "refresh fantasy" / "update the league"
- "pull the latest WNBA data" / "rerun the pipeline" / "rebuild state"
- "anyone added/dropped/picked up?"
- "is the page up to date?"

If the user explicitly says they want a **dry run** or just wants to verify their cookies still work, run only step 3 below and stop there.

## What this project is

- Working dir: `/Users/pranava/Documents/Projects/FantasyGM`
- League: ESPN fantasy women's basketball, `leagueId 2043154241` ("50-40-90 Club")
- Pipeline source: `pipeline/` (Python 3.9 + pydantic v2 + httpx)
- Output:
  - `data/raw/<YYYY-MM-DD>/league.json` + `free_agents.json` — daily ESPN snapshots, PII-stripped, committed
  - `data/history/transactions.jsonl` — append-only transaction log, deduped on `transaction_id`
  - `docs/data/state.json` — single source of truth the static site reads
- Frontend: `docs/index.html` (vanilla, GitHub Pages)
- Tests: `tests/` (pytest, fixture-based, no network)
- Read [CLAUDE.md](../../../CLAUDE.md) "Project intent" + "ESPN scar tissue" before debugging anything.

## How to run it — checklist

Execute these steps in order. Use a single Bash call per step where possible.

### 1. Sanity-check working tree

```bash
cd /Users/pranava/Documents/Projects/FantasyGM && git status --short && git rev-parse --abbrev-ref HEAD
```

If there are uncommitted changes unrelated to the refresh, ask the user before running the pipeline. We don't want to mix data updates with in-flight edits.

### 2. Make sure deps are installed

```bash
test -d .venv || python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
```

Idempotent — if `.venv` already exists, this is fast.

### 3. Validate cookies (dry-run)

```bash
.venv/bin/python -m pipeline.refresh --dry-run
```

- Exit 0 → cookies fine, proceed.
- Exit 2 → `.env` is missing or `ESPN_SWID` / `ESPN_S2` are unset. Stop and instruct the user to copy `.env.example` → `.env` and follow the comments. **Never print or repeat the values back to the user.**
- Exit 3 → cookies expired. Tell the user to re-copy `SWID` and `espn_s2` from Chrome DevTools (Application → Cookies → fantasy.espn.com) and update `.env`.

### 4. Full refresh

```bash
.venv/bin/python -m pipeline.refresh
```

This writes `data/raw/<date>/*.json`, appends to `data/history/transactions.jsonl`, and rewrites `docs/data/state.json`. Print the CLI's summary verbatim — it includes scoring period, transactions appended, and the snapshot path.

### 5. Tests + secret scan

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m pipeline._secret_scan
```

If either fails, **stop**. Don't commit. Show the failure and ask the user how to proceed.

### 6. Summarize what changed

```bash
git status --short
git diff --stat docs/data/state.json data/history/transactions.jsonl
```

Then read the relevant slice of `docs/data/state.json` and summarize for the user in ~5 bullet points:
- League name + scoring period.
- Number of teams; any with a new weakness (gap > 8 worse than yesterday).
- Top 3 waiver targets (name · bucket · projected points · ownership %).
- Recent transactions (count + last-seen timestamp).
- New transactions appended today.

Keep the summary tight. The user is checking-in, not asking for a report.

### 7. Confirm before committing

Before running any `git add` / `git commit` / `git push`, ask the user explicitly:
> "Pipeline ran clean. Commit and push to GitHub? [y/N]"

If yes, run:

```bash
git add data/ docs/data/state.json
git commit -m "$(cat <<'EOF'
data: refresh league snapshot

- ESPN ingest: data/raw/<date>/{league,free_agents}.json
- Transactions appended to data/history/transactions.jsonl
- docs/data/state.json rebuilt
EOF
)"
git push origin main
```

If no, leave the working tree as-is. The user can preview locally before deciding.

### 8. Optional: preview

If the user wants to see the result locally before pushing, the static dev server is wired in `.claude/launch.json`. The skill caller can suggest it:

```bash
node scripts/serve.mjs   # then open http://127.0.0.1:9876
```

## Common pitfalls

- **Don't run with `--no-verify`.** The secret scan exists because someone got burned.
- **Don't `git add -A`.** Use the explicit paths in step 7 so a stray `.env` or scratch file can't sneak in.
- **Don't commit `data/raw/` outside today's folder.** If `git status` shows raw files from a past date are dirty, that's an editing bug — investigate before adding.
- **The skill never reads or echoes the cookie values.** Cookies live in `.env`, which is gitignored. If the user asks for help debugging an auth error, never paste the cookie string back — tell them the *shape* (e.g. "SWID should be wrapped in curly braces").
- **Owner privacy.** ESPN responses contain member names. The pipeline's `_redact_owners` strips them before writing raw snapshots. If you ever need to debug a transaction by name, do it in memory only — don't write the unredacted JSON to disk or paste it to the chat.

## Scar tissue cheat sheet (re-verify if anything looks wrong)

These are the gotchas that cost the most time to discover the first time. If the pipeline is misbehaving, check these first.

| Symptom | Cause | Fix |
| --- | --- | --- |
| API returns 404 with the right league ID | Wrong game code in the URL — `wbasketball` returns 404; only `wfba` works for the fantasy reads endpoint | `pipeline/espn_client.py:BASE_URL_TMPL` is the canonical version |
| API returns 401 even with cookies set | Cookies are expired or the SWID is missing curly braces | Re-copy from Chrome DevTools; `ESPNCredentials.from_env()` enforces the `{...}` shape |
| Roster shows fewer active players than expected | `lineupSlotCounts` shows count=1 for slot 7 but `isBenchUnlimited: true` overrides it | Slot 7 is bench. `pipeline/positions.py` labels it `BE`. Active set is {1, 4, 5, 6}. |
| A Forward shows up in slot 5 (which we labeled C) | Slot 5 is "F/C eligible" — forwards can fill a center slot | `LINEUP_SLOT_LABEL[5] = "F/C"`. Don't change this just because one player surprises you. |
| Player positions look wrong | NBA fantasy maps `defaultPositionId` 1=PG, 2=SG, …, 5=C. WNBA uses 1=G, 2=F, 3=C. | `pipeline/positions.py:DEFAULT_POSITION_LABEL` — verified empirically against the 50-40-90 Club roster. |
| Transactions show `#playerId` instead of names | Player wasn't in the team's current roster or the FA top-N (`_player_name_index` is built from those two sources) | Backlog item: pull `kona_playercard` for unresolved IDs. Cosmetic, not a correctness bug. |
| `python3 -m http.server` errors with `PermissionError: Operation not permitted` | Sandbox restricts `os.getcwd()` from the preview launcher's CWD | Use `node scripts/serve.mjs` (configured in `.claude/launch.json`) |

If you discover a new pitfall, **append a row to this table and add the same row to `CLAUDE.md` § "ESPN scar tissue."** The two should stay in sync — CLAUDE.md is read first by every agent; the skill table is read when this specific flow runs.

## Validating against ESPN.com (after step 4)

Once the pipeline writes a new `state.json`, do a quick cross-check against `https://fantasy.espn.com/womens-basketball/league/...?leagueId=2043154241`. Open three pages in Chrome (or via the Chrome MCP if available) and confirm:

1. **Standings page** — `league/standings?leagueId=2043154241`
   - Team count + names match `state.meta.team_count` and `state.teams[*].name`.
   - W–L–T per team matches `state.teams[*].record`.

2. **Rosters page** — `league/rosters?leagueId=2043154241`
   - For one team (pick at random), the roster size and player names match `state.teams[*].roster[*].player.name`.
   - The lineup slot assignments (G / F / C / UTIL / BE) match the column ESPN shows.

3. **Recent Activity page** — `recentactivity?leagueId=2043154241`
   - The latest transaction (top of the list) matches `state.transactions_recent[0]` — same player, same team, same timestamp.

If any of these disagree:

- **Team name mismatch:** a team got renamed since last refresh. The `team_id` is the load-bearing key; `name` is allowed to change. Confirm `state.teams[*].team_id` matches what ESPN's URL uses (`?teamId=N`).
- **Roster mismatch:** likely scoring period drift. ESPN's roster pages can show "this period" or "next period" lineups; the pipeline pulls the *current* scoring period from `league.scoringPeriodId`. If the user is looking at next period in the UI, that's the discrepancy.
- **Transaction mismatch:** ESPN sometimes lags the transaction log by a minute or two on the activity page. Re-run after a minute. If still wrong, the player ID didn't resolve to a name — see the cheat sheet row above.
- **Projected points off:** ESPN updates `appliedTotal` projections as games happen. If the difference is small (< 2 pts) and only on players who played today, that's expected drift. Big differences point to a stat-source-ID bug — check `pipeline/analyze.py:_player_projected_points` to ensure we're picking `statSourceId=1` for the current `scoringPeriodId`.

If you discover a *new* class of discrepancy, document it as:

1. A row in the "Scar tissue cheat sheet" above with symptom + cause + fix.
2. A matching row in `CLAUDE.md` § "ESPN scar tissue."
3. A regression test in `tests/` if it's testable without live API access.

That's the loop: every surprise becomes a piece of documented scar tissue, so the next pipeline run avoids the same trap.
