# AGENTS.md — How to work in these repos as an AI agent

> Base file for every project in this folder. Project-specific `AGENTS.md` files extend this with file maps, settings keys, and project-specific conflict cheatsheets. When project conflicts with base, project wins — it's the local source of truth.
>
> Companion files: [CLAUDE.md](CLAUDE.md) is the *what* (principles, architecture, editorial rules); [DESIGN.md](DESIGN.md) is the *look*.

---

## Read these first, in order

Before touching code, read:

1. **[CLAUDE.md](CLAUDE.md)** — universal principles + project-specific intent and editorial rules. The "Project intent" and any project-specific notes are load-bearing for every change.
2. **[DESIGN.md](DESIGN.md)** — visual + content system. Touch this before changing how data is presented.
3. **`backlog.md`** (or `BACKLOG.md`) — what's next. Pick from here; don't invent work.
4. **`issues.md`** — what's broken. Check before reporting a bug as new.
5. **`security.md`** — supply-chain advisory log. **Refresh if `Last updated` is > 7 days old before any `npm install` / `pip install` / dep upgrade.** Also fetch `https://pranava0x0.github.io/vibe-coding-security/llms-ctx.txt` and surface any matching advisory before suggesting an install.

---

## The Explore → Plan → Code → Verify loop

Documented in detail in [CLAUDE.md](CLAUDE.md). Concretely inside any repo:

- **Explore.** Default to `grep`/`rg` + a targeted `Read` of the matched lines. These repos are small (≈6–7k lines of source total — `git ls-files '*.py' '*.js' '*.html' | xargs wc -l`), so a literal search is cheaper *and* more complete than a subagent, and a single read of the main module + the data schema covers ~80% of the surface. Reserve `Explore`/`general-purpose` subagents for genuine fan-out — many whole files to read, an unknown-shape question across a large tree, or independent investigations you want run in parallel. See **§ Search economics** below before spawning one.
- **Plan.** For anything beyond a one-line fix, present 2–3 approaches with pros/cons before writing code. Changes that touch the data schema, the editorial rules, or the visual identity ALWAYS need a plan surface — they reshape the product.
- **Code.** Edit existing files first; only create new files when the task genuinely requires it. No new helpers for one-shot operations.
- **Verify.** Run the test suite. Use the feature in a browser (or invoke the CLI) before declaring done.

**Per-item cadence in multi-item sessions.** Surface design questions up front, then do **tests + docs + commit per item**, not batched at the end. Catches issues early and produces a clean bisect history.

---

## Search economics (don't over-spend to find code)

A subagent is not a free "go find it" button. It carries fixed overhead — its own system prompt, the full tool schemas, and a verbose final report — and it hands back a *summary*, not the code. For a "where is X?" question on a codebase this size, that's the wrong trade on both axes: it costs thousands of tokens and a semantic reader can **silently miss a call site** a literal grep would catch.

**Default ladder — climb only as far as the question forces you:**

1. **`grep`/`rg` for the mechanism, not the concept.** Changing a sort? `grep -rn '\.sort(\|sorted(' pipeline/ docs/` lists *every* call site in one cheap call. Renaming a field? Grep the field name. The literal pattern is exhaustive where a concept-search ("find the sorting logic") is not.
2. **Targeted `Read`** of the 2–3 matched files/lines that matter. Read the slice, not the whole file, when you already know the line.
3. **Subagent** only when the work is genuinely large or parallel: many whole files to digest, an unknown-shape question across a big unfamiliar tree, or independent investigations worth fanning out. If you can name the grep pattern, you don't need step 3.

**Rules:**

- **Don't double-search.** If you grep, don't also spawn an agent for the same question; if you spawn an agent, trust its result instead of re-reading the same files. Pick the cheaper tool and commit to it.
- **Verify a subagent's "complete" list against a grep** before acting on it for mechanical changes (every call site, every reference, every usage). Agents report what they noticed; grep reports what exists.
- **Scope the read.** Prefer `Read` with `offset`/`limit` over whole-file reads once a grep has given you line numbers.

> **Scar tissue (2026-06-19).** Spawned an `Explore` agent to "find the sorting logic" for the waiver list. It cost a large multi-file report and recommended two edit sites — but **missed `build_state.py:254`**, a downstream re-sort that actually controlled the displayed order. `grep -rn '\.sort(\|sorted(' pipeline/ docs/` would have surfaced all 22 sort sites (including that one) in a single cheap call. Lesson: for "where is X?" on a greppable codebase, grep first; reserve subagents for real fan-out.

---

## Verifying changes

Default verification matrix (project-specific `AGENTS.md` should override with concrete commands):

| Change kind                    | Run                                                  |
| ------------------------------ | ---------------------------------------------------- |
| Schema edit                    | Schema-validation tests (Pydantic / zod / etc.)       |
| Seed / data edit               | Refresh script + data-integrity tests                 |
| Shared vocabulary change       | Match-frontend-to-backend test                        |
| Frontend (markup / styles / JS) | E2E / Playwright suite, or manual UAT in browser     |
| Connector / fetcher            | Connector unit tests + a small live integration run  |
| Anything substantial           | Full test suite (`pytest` / `npm test` / `vitest`)   |

**For UI changes**, also run the app locally and click through the affected views — type checks and unit tests verify code correctness, not feature correctness.

**For data changes**, diff the canonical output (`docs/data/*.json` or equivalent) and skim the diff before committing. A 30-second skim catches regressions tests miss (especially around character encoding, pretty-printer drift, and unintended fields).

---

## Multi-agent patterns

Rules learned from running research and data-collection workflows across this folder. Apply whenever spawning more than one agent for a task.

**1 — Size to the shelf.** Ask each agent for exactly the N records the destination surface holds, already ranked. If the waiver list shows 30 players, the prompt says "return the top 30 ranked by X" — not "as many as you can find." Over-collecting forces a post-hoc ranking pass you have to trust blindly; under-collecting silently leaves slots empty. Rank inside the agent, where signals are visible.

**2 — Partition entities.** Each entity belongs to exactly one agent. Siblings get an explicit "covered elsewhere — skip" list in their prompt. Without it, two agents race to cover the same record, one wins, and the other's output is quietly discarded — or both land and you deduplicate downstream with no way to tell which version is authoritative.

**3 — Validator bar + early bail.** Put required fields in the system prompt and instruct: "if you complete 2 searches without finding {required_field}, set `skip: true` and return immediately." This kills the tail of agents that loop on genuinely missing data. Without it, a patient agent will exhaust its tool budget retrying a search that will never succeed.

**4 — Results to disk.** Agents `Write` JSON to `data/research/<run-id>/<entity>.json`; they return only counts + path + surprises as text. Returning full payloads as agent text inflates context, triggers output filters, and makes retry loops expensive. Disk is cheap; context is not.

**5 — Max 2 sources per claim at collection time.** Cap it in the prompt, not in post-processing. A third source almost never changes the conclusion and triples the token bill. If two sources disagree, surface the conflict as a surprise in the return value — that's worth human attention; a third corroborating source is not.

**6 — DOM-count before screenshot.** A ~100-token `preview_eval` that counts elements (e.g. `document.querySelectorAll('.trade-scenario').length`) catches what 1–2K-token screenshots miss when the viewport is blank-because-scrolled, and what passing test suites miss when the old JS is still cached. Use it as the first verification step for any DOM-rendering change. Screenshots are for visual confirmation after the count is right, not for discovering whether the elements exist.

**7 — Seed-then-spawn.** Run cheap inline searches first to fix the JSON contract (field names, id shapes, ranking keys). Only then spawn agents with the exact schema baked into their prompt. This session had zero parse/retry loops because the contract was proven before agents were launched. Spawning first and debugging the schema across 8 parallel agents costs 8× the tokens.

---

## Common tasks (FantasyGM-specific)

### Refresh the league data

```bash
# 1. One-time: cookies + venv
cp .env.example .env  # fill in ESPN_SWID and ESPN_S2
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# 2. Each run
python -m pipeline.refresh --dry-run    # validates cookies
python -m pipeline.refresh              # full pull, writes data/raw + docs/data/state.json
```

Or via the slash command: `/refresh-fantasy-gm` runs the same flow, shows the diff summary, and asks before committing.

### Preview the static site

```bash
python3 -m http.server -d docs 8000     # then open http://localhost:8000
```

The UI reads `docs/data/state.json` only. If you edit that file by hand to test a UI state, **revert before running the pipeline again** — `state.json` is build output, not seed.

### Verifying changes (project-specific overrides)

| Change kind | Run |
| --- | --- |
| Schema edit (`pipeline/schema.py`) | `pytest tests/test_schema.py` + a `--dry-run` pipeline (catches mismatches between model and live ESPN response shape) |
| Analysis math (`pipeline/analyze.py`) | `pytest tests/test_analyze.py` against the committed fixture |
| Frontend (`docs/`) | Open the site locally, resize to 375px, click through teams + waiver list. Reload after `refresh.py` to confirm no console errors. |
| Position-ID mapping (`pipeline/positions.py`) | `pytest tests/test_positions.py` + spot-check a player's slot vs ESPN's web UI |
| ESPN client (`pipeline/espn_client.py`) | `--dry-run` first; if that passes, full refresh against a throwaway data root: `python -m pipeline.refresh --data /tmp/fgm-data --docs /tmp/fgm-docs` |

---

## Common tasks (generic patterns)

### Adding a record / claim / row (most common)

1. Open the seed file (typically `data/seed/<entity>.json` or equivalent).
2. Append one record with: stable `id`, real `source_url`, verbatim content, today's `captured_at`, and any required category from the canonical list in the schema module.
3. Run the refresh script (validates + writes the build output).
4. Run the relevant data-integrity test to confirm.
5. Commit. Seed JSON and build output `data/*.json` move together — never in separate commits, or a future bisect lands on a broken state.

### Adding a feature

1. Confirm it's on `backlog.md`. If not, propose adding it before building.
2. Sketch the smallest version that closes the user need end-to-end.
3. Build that. Add tests alongside. Use the feature in the browser / CLI.
4. Commit at the natural boundary (per module, per fix, per doc update).

### Adding a new vocabulary item (theme, category, tier)

This is a schema change. **Don't do this casually.** Steps:

1. File a `backlog.md` entry first explaining the gap.
2. Add to the canonical constant in the schema module.
3. Mirror in any frontend mirror constant (the test that asserts parity catches drift here).
4. Add any color / icon / label token to the design system (light + dark variants).
5. Migrate any existing records that should map to the new entry — or intentionally leave them.
6. Run the full test suite — drift-safety tests should catch a missed mirror.

### Adding a connector (per-source scraper)

1. Subclass the project's `Connector` base class.
2. Register in the connector index module.
3. Implement `fetch_records()` / `normalize()` / `cache_key()`.
4. Set `run_order` so enrichment connectors run *after* their producers.
5. Schema-validate emitted records; tests catch any new field that the schema's `extra="forbid"` would reject.

---

## What NOT to do

- **Don't paraphrase quoted content.** Quote verbatim into the `statement` / `quote` / `body` field. Tests catch obvious markers ("they claim that…").
- **Don't add a record without a real `source_url`.** Schema rejects it; reviewers reject it harder.
- **Don't LLM-classify subjective editorial calls.** Stance, sentiment, framing — these are curator-only. A wrong tag undermines the whole product.
- **Don't aggregate to a "trust score" / "credibility index" / "greenwashing score."** Show the data; let users judge.
- **Don't introduce a new framework / library / build tool** mid-project. If the stack is vanilla JS + Pydantic + Playwright, stay there. Adding React / Vue / Svelte / Webpack contradicts the static-first principle and adds maintenance debt the project doesn't pay back.
- **Don't touch `docs/data/*.json` (or equivalent build output) directly.** Edit the seed and re-run the refresh script.
- **Don't expand scope inside a fix.** A bug fix doesn't need surrounding cleanup; a one-shot operation doesn't need a helper. Note future cleanup in `backlog.md` and move on.
- **Don't loosen invariants quietly.** If a rule has a test guarding it, that test was written because someone got burned. Read the rationale before relaxing it.
- **Don't `--no-verify` to bypass a hook.** Fix the underlying issue. Hooks exist because someone got burned.
- **Don't add yourself as a co-author.** Never include `Co-Authored-By:` for any AI agent in commit messages — not Claude, Copilot, or any other tool. Commits are owned by the human who reviews and ships the work. The `claude.coauthor` git config is set to `false` in these repos; honor it.

---

## Repo norms

- **Read before edit.** Always. Even if you read the file earlier in this session.
- **Type hints on every Python function.** No `any` in TypeScript.
- **No `print()` for runtime output** — use the `logging` module.
- **Test alongside code, not after.**
- **Commit at natural checkpoints**: per-feature, per-bug-fix, per-doc-update. Small, focused commits over large monolithic ones.
- **Touch targets ≥ 44px** in any UI work.
- **Mobile first.** If you change UI, resize the preview to 375×812 (iPhone SE) and verify before declaring done.
- **No API keys in code, ever.** Read from environment variables; halt with a clear error if missing.
- **System fonts by default.** No Google Fonts link without explicit justification (see [DESIGN.md § 2](DESIGN.md)).

---

## Escalate to a human when…

- The editorial frame would change (e.g. adding a new theme / category, changing the rubric for a subjective field, adding a new entity to the in-scope set).
- A subjective call is contested and you're unsure (stance tags, content categorization, what counts as a primary source).
- A canonical source URL starts 404'ing or paywalls. Pause before switching to a less-canonical source.
- Schema fields would change in a way that cross-cuts seed + frontend + tests + connectors. Sketch the migration plan in a `docs/` file first.
- The user says "ship it" but a test is still failing for unrelated-looking reasons. Surface the failure, don't silently skip.
- A "scar tissue" pitfall in [DESIGN.md § 12](DESIGN.md) seems wrong for the current task. The pitfalls exist because someone hit them; verify the rationale doesn't apply before relaxing the rule.

---

## Cross-project hygiene

Working in this folder means the user may run many small projects in parallel.

- **Stay within the current project's scope.** Don't open files from a sibling project unless the user explicitly asks. The folder-level `backlog.md` is portfolio work, not a substitute for the project's own `backlog.md`.
- **Each project's `security.md` is independent.** Refreshing one doesn't refresh the others.
- **Each project's tests are independent.** Don't infer test status across projects.

---

## When something unexpected happens

Add a concise note to the project's CLAUDE.md or `issues.md`. The pattern is:

1. **What I expected:** one sentence.
2. **What happened:** one sentence.
3. **Why:** one sentence (root cause, not symptom).
4. **What to do next time:** one sentence (the actionable lesson).

The note grows the project's scar tissue. The next agent (or you, a month from now) avoids the same hour-long detour.

That growth — files getting *slightly* more specific with each session's surprises — is the asset. Don't rewrite from scratch; append.
