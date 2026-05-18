# Fantasy GM

AI-powered GM for an ESPN fantasy WNBA league. Identifies trending waiver-wire targets, surfaces each team's biggest positional need (proactive framing — "top need," not "weakness"), and tracks transactions over time.

The data pipeline runs locally on your machine. The output is a static page hosted on GitHub Pages. No backend, no secrets in this repo.

## Quick start

```bash
# 1. Install deps (Python 3.9+)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Set your ESPN cookies (one-time)
cp .env.example .env
# edit .env to add ESPN_SWID and ESPN_S2 (see comments in .env.example)

# 3. Install pre-commit hooks (one-time) — blocks accidental cookie commits
bash scripts/install_hooks.sh

# 4. Refresh
python -m pipeline.refresh

# 4. Open the static site
open docs/index.html
```

Or trigger the whole thing through Claude Code: `/refresh-fantasy-gm`.

## Layout

```
pipeline/        Python data pipeline (idempotent)
  espn_client.py    Auth + thin wrapper over ESPN private API
  ingest.py         Fetch + dump raw snapshots to data/raw/<date>/
  analyze.py        Team-needs gaps + waiver target ranking
  build_state.py    Write docs/data/state.json (single UI source of truth)
  schema.py         Pydantic models — single source of truth for shapes
  refresh.py        CLI entrypoint that runs the full pipeline
data/raw/        Daily ESPN API snapshots, append-only audit trail
data/history/    transactions.jsonl, append-only league activity log
docs/            Static site served by GitHub Pages
tests/           pytest suite (schema + analysis fixtures)
.claude/skills/  Slash commands (refresh-fantasy-gm)
```

## What it does

- **Teams & rosters.** Pulls every team's name, roster, record, FAAB / waiver position, and transaction history. Tracks teams by stable ESPN team ID across name changes.
- **Team-needs scan.** Compares each team's guard production vs forward/center production vs the league average. Surfaces the bucket with the biggest upgrade opportunity ("top need") for each team.
- **Waiver target ranking.** Ranks the free-agent pool by projected fantasy production, then re-weights for each team's positional gaps.
- **Transaction history.** Records every add, drop, trade, lineup change with timestamps. Persisted to `data/history/transactions.jsonl`.

## Security

See [security.md](security.md). Short version: cookies live in `.env` (gitignored). The pipeline never runs in CI. The static site contains only data anyone in your league can already see.

## Conventions

- Project-universal principles: [CLAUDE.md](CLAUDE.md)
- Agent workflow: [AGENTS.md](AGENTS.md)
- Visual design: [DESIGN.md](DESIGN.md)
- Open bugs: [ISSUES.md](ISSUES.md)
- What's next: [BACKLOG.md](BACKLOG.md)
