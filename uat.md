# UAT Baseline — FantasyGM

_Created: 2026-05-17_
_Last run: 2026-10-06 (PR #74 mobile interaction costs and NFL release checks)_

## Legacy WNBA baseline

The following original flows apply to `docs/wnba.html`. Current NFL release and repeatable mobile-cost checks appear below.

## Project Info

- **Stack**: Vanilla HTML/CSS/JS (no framework, no build). Python pipeline writes `docs/data/state.json`.
- **Dev server**: `node scripts/serve.mjs` (also wired into `.claude/launch.json` as `site`)
- **Entry point**: `docs/index.html`
- **Data source**: `./data/state.json` fetched by `docs/assets/app.js` on load
- **Surfaces**: single-page scroll layout — Meta strip → Top Waiver Targets → Team Needs grid → Recent Transactions

## Critical flows (run every time)

1. **Initial load** — fetch `./data/state.json`, render all three sections. No console errors. Meta strip shows period / matchup / season / team count / captured-at.
2. **Theme toggle** — clicking `#theme-toggle` flips `data-theme` between `light` ↔ `dark`. The choice persists in `localStorage` (key `fgm-theme`) across reloads.
3. **Team card expand/collapse** — clicking a `.team-card` toggles `aria-expanded` and reveals the per-team waiver targets list. Clicking again hides them. Only one team open at a time is *not* enforced — multiple can be expanded.
4. **Skip link** — `Tab` from a fresh load focuses the `.skip-link` and pressing Enter jumps to `#main`.

## Sections & last tested

| Section | Last Tested | Notes |
| --- | --- | --- |
| Topbar / Brand / Theme toggle | 2026-06-01 | Stable. Light↔dark toggle persists via localStorage. |
| Meta strip | 2026-06-05 | Period 29 / P32–38 / Matchup 4 / Teams 8 / 2026-06-05 13:40Z. Wraps to 3 lines at 375px. |
| Waivers tab | 2026-06-05 | 30 targets render. AI "GM Take" summaries show on all cards with correct fresh text. RETURNING badges on Hiedeman, Allemand, Melbourne. Bucket pills, per-game, ownership %, trend all present. |
| Team Needs tab | 2026-06-05 | All 8 teams present. G and FC bars rendering. FC-need teams: KAH, PEZ, C9 clearly FC-negative. G-need teams: KylB (-37.9), EET (-51.2). Expand/collapse and SUMMARY · AUTO-GENERATED work. |
| News tab | 2026-06-01 | Stable. WNBA-only articles. Player tags visible on highlighted items. |
| Transactions tab | 2026-06-05 | 1 FUTURE ROSTER transaction (Spda lineup swap: Kiki Rice F/C↔UTIL, Arike Ogunbowale UTIL↔F/C). Slot labels correct. Player names resolving. |
| Player modal — Desktop | 2026-06-05 | Stable. Fresh GM Take panel with today's AI text. Last-2-weeks game log. Social (X) feed: @BallersNewz, @DanielleHobeika, @EJayArrow, @WNBAStormChaser all rendering. |
| Player modal — Mobile | 2026-06-01 | Stable. Fits 375px. 3-stat and 5-stat layouts correct. TEAM NEWS badges readable. Reddit section present. |
| Mobile 375px | 2026-06-05 | All 4 tab labels fully visible. Player names unclipped. AI summary line rendering. No horizontal overflow. |
| Tablet 768px | 2026-06-01 | 2-up team grid confirmed. Tabs fit without clipping. |
| Dark mode (mobile) | 2026-06-01 | Stable. All surfaces, TEAM NEWS badges, Reddit section themed correctly. |
| Wide viewport 1920px | 2026-06-01 | Content centres at max-width. No horizontal scroll. Footer visible. |

## Known stable areas

- Theme toggle + persistence
- Mobile waiver-card layout at 375px (no horizontal scroll, no truncation)
- Desktop team-grid at 1280px (4 columns)
- Pill colors stable across themes
- All 45 pytest tests pass

## Known flaky / unstable areas

- **None known.** All previously open issues resolved.

## Exploration notes

Things worth trying on future runs:

- **Run after pipeline against full 8-team data.** Seed state has only 3 teams and 5 synthetic free agents — many "what if the real shape breaks something" risks unexplored. Specifically check: (a) team-grid behavior at 5–8 teams, (b) waiver list with 15+ entries, (c) transactions where `from_team_id` and `to_team_id` are both real teams (TRADE), (d) per-team detail panel with 6–10 ranked picks.
- **Player ID fallback (#playerId).** With real data, almost all transaction items should resolve to names. If many still render as `#NNNNNN`, that's the backlog name-resolution gap re-surfacing.
- **Slot label parity.** `docs/assets/app.js:SLOT_LABEL` is a hand-copy of `pipeline/positions.py:LINEUP_SLOT_LABEL`. If the Python map ever changes, the JS one must follow. Quick check: open transactions section, confirm no `S<n>` strings leak through.
- **Keyboard navigation.** Try `Tab → Enter` on a team card to expand it. Verify focus ring is visible (`:focus-visible` outline).
- **Reduced motion.** Set `prefers-reduced-motion: reduce` and confirm no transitions fire on hover/expand.
- **Tablet width (768px).** Specifically check that the team grid lays out 2-up.
- **Very wide viewports (≥1280).** Content max-width is 1280; outside that the page centers. Confirm no stretching at 1920.

## Issues found this run

See `issues.md` for the audit table.

**2026-05-17 (first run):**
- UAT-001: Slot ID `-1` rendered as `S-1`. Fixed.
- UAT-002: Team ID `0` rendered as `T0`. Fixed.
- UAT-003: Raw `S1`/`S6` instead of slot labels. Fixed.

**2026-06-01 (first pass — real 8-team data):**
- UAT-004: Non-WNBA articles in "Recent WNBA News" (e.g. OBJ story). Fixed.
- UAT-005: False player tag — DeWanna Bonner on OBJ article. Fixed.
- UAT-006: "Transactions" tab truncates at 375px. Fixed.

**2026-06-01 (second pass — desktop + mobile UAT):**
- UAT-007: Team-tagged news in player modal had no label. Fixed — "TEAM NEWS" badge added.
- UAT-008: 5-stat free-agent modal orphan row (Season + Ownership stretched to 50%). Fixed — `repeat(3, 1fr)` grid.

**2026-06-01 (third pass — desktop + mobile UAT, AI summaries):**
- UAT-009: Waiver-card player names clipped at 375px ("Jade M", "Elizabe"). Fixed — responsive grid-areas drop the schedule to its own row below 480px so the name gets full width.
- UAT-010: Team-detail "Top picks" rows clipped names and the bucket pill overlapped the team abbrev at 375px. Fixed — same grid-area reflow on `.team-target-row`.
- UAT-011: Ownership change rendered `-0.0%` for near-zero values. Fixed — `fmtPctChange` collapses `|v| < 0.05` to `0`.
- UAT-012 (data integrity): committed `state.json` was newer than the committed raw snapshot — state derived from data not in the audit trail. Fixed — `scripts/rebuild_state.py` regenerates state deterministically from the on-disk raw snapshot.
- Feature: AI "GM take" summaries for the top 30 free agents — clamped line on each waiver card + accented panel in the player modal (light + dark verified, mobile + desktop).

**2026-06-05 (post-refresh pass):**
- No new issues found.
- Verified: scoring period 29, 2026-06-05 capture date, 30/30 AI summaries with today's fresh text, X/Twitter social feed in player modal, all 8 teams in Team Needs with FC/G bars, FUTURE ROSTER transaction with correct slot labels, mobile 375px clean.
- `pytest`: 138 passed. Secret scan: clean.

## NFL release — 2026-10-06

- Initial My team selector visible and enabled after selected league loads; persisted IDs are scoped per league.
- Mobile 375px and desktop 1440px: no horizontal page overflow; all controls at least 44px.
- Switch both Sleeper leagues; own rosters 9 and 11. Yahoo browser-imported own Team 9; partial availability explicit. ESPN setup remains honest.
- Yahoo: Nico Collins replacement for OUT Justin Jefferson; Kelce bye and TE coverage need; IR-held Tyson preserve value. Imported Week 5 projection has a source and capture time.
- Waivers: sequentially type Dulcich; stable search input, one matching candidate. Position filter composes with search; no data reload.
- Injury alert news links require exact athlete IDs; shared news rows without URL excluded.
- WNBA: Copper Futures default, August snapshot visibly stale, legacy console intact; unload iframe when returning NFL.
- Rules: confirm timezone-aware cutoff, Today shows it; calendar export should import as UTC event with two-hour alarm. Exact processing hour not guessed.
- Performance script stresses 10,000 players / 32 teams and offline full rebuild; checks <64MiB stress allocations and <200KB gzipped per JSON.

## Mobile interaction baseline — 2026-10-06

`scripts/mobile_uat.cjs` measures 10 tasks across three locally connected NFL leagues at 375×812 and 390×812. PR #74 baseline: 60 journeys, median 4 interactions, p95/max 18, total 258 swipes. Saved report: `docs/mobile-uat-before.json`.

Serve `docs/`, then run:

```bash
NODE_PATH=/path/to/node_modules node scripts/mobile_uat.cjs tmp/mobile-current.json
NODE_PATH=/path/to/node_modules node scripts/mobile_uat.cjs tmp/mobile-after.json docs/mobile-uat-before.json
```

Requires existing Playwright and Chrome (`CHROME_PATH` optional). `UAT_URL` defaults to `http://127.0.0.1:8000`. NFL snapshots must exist locally. Fresh saved-team page per task; one tap or text entry counts one, swipe travels at most 609px. Content must expose its first 300px without nav overlap. Browser auto-scroll excluded. Native selector interactions excluded; costs start after league/team selection. Same task list and snapshot required for comparison. These are equal-weight scripted task costs, not real-user telemetry. Screenshots saved under ignored `tmp/uat-screenshots/`.

After fixes: median 3, p95/max 8, total 32 swipes across the same 60 journeys. Details and per-task ranges: `docs/mobile-uat.md`; raw result: `docs/mobile-uat-after.json`. `UAT_BUDGET=8` enforces the maximum independently of old snapshot identity. Five-result paging must keep all IDs reachable, reset on filters, preserve sequential-typing focus, and switch from a deep list to another view without scrolling back. Sources remain accessible via native disclosure.

Additional smoke: 279 assertions, 375px/1440px, light/dark, all three connected NFL leagues, complete paging without missing/duplicate IDs, zero matches, search clearing and sequential focus, sources disclosure, local cutoff/calendar download, all views/44px controls, ESPN setup, WNBA embed load/unload, stale snapshot advice suppression. Zero page errors. Full Python suite: 275 passed.

Bot fixes: 275 Python tests pass. Isolated cached rebuild: three leagues, 266 players, zero network requests; roster capture timestamps unchanged. Synthetic 10,000-player/32-team analysis: 3.629 seconds, 5.12 MiB traced allocations; isolated offline rebuild: 0.153 seconds, 14.43 MiB. Four Codex review threads resolved after fixes were pushed.

WNBA automation note: expected desktop tabs/meta and all 30 waiver targets to be visible on mobile; desktop chrome is intentionally hidden and the list initially pages ten targets. The iframe also needs one outer-page swipe to expose its bottom navigation. Test loaded meta text with `state: attached`, use `.bottom-nav-btn`, scope cards to the active section, and verify paging rather than assuming all cards render at once.

## Seasonal header — 2026-10-06

Collapsed header: 61px including border at 375px and 1440px; one NFL/WNBA disclosure, 44px target. Browser checks: three imported leagues, all five NFL views, default teams, selection persistence after reload, WNBA loading, Escape close, zero overflow/errors. 74 assertions passed locally. Full suite: 275 passed. When testing league controls, open the header disclosure; hidden enabled controls should be checked as attached, not visible.

## Pages payload recovery — 2026-10-06

Recovered previously captured local imports: two Sleeper leagues and Yahoo 544768 Team 9. Rebuilt through `pipeline.football --offline`: three leagues, 266 players, zero requests; original roster capture dates retained. Strict schema and deployment-path checks cover all connected league references, selected team IDs, player references, ignore rules and prohibited owner/credential fields. Raw imports/cache stay ignored; only the requested output files are allowlisted. Full Python suite: 276 passed.

## One-tap league switching — 2026-10-06

Three connected league buttons stay visible above NFL content; switching takes one click/tap, compared with opening the seasonal menu then opening/selecting the league dropdown. Selected button uses aria-pressed; full accessible names/title text survive mobile truncation. The row stays 44px at 375px and 1440px, with no page overflow. Browser verified each league, active state, selected roster, reload persistence, all NFL views and WNBA switching. No page errors. Python suite: 276 passed.
