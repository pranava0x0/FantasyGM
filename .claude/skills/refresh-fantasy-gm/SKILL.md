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
