# UAT Baseline — FantasyGM

_Created: 2026-05-17_
_Last run: 2026-05-17_

## Project Info

- **Stack**: Vanilla HTML/CSS/JS (no framework, no build). Python pipeline writes `docs/data/state.json`.
- **Dev server**: `node scripts/serve.mjs` (also wired into `.claude/launch.json` as `site`)
- **Entry point**: `docs/index.html`
- **Data source**: `./data/state.json` fetched by `docs/assets/app.js` on load
- **Surfaces**: single-page scroll layout — Meta strip → Top Waiver Targets → Team Weakness grid → Recent Transactions

## Critical flows (run every time)

1. **Initial load** — fetch `./data/state.json`, render all three sections. No console errors. Meta strip shows period / matchup / season / team count / captured-at.
2. **Theme toggle** — clicking `#theme-toggle` flips `data-theme` between `light` ↔ `dark`. The choice persists in `localStorage` (key `fgm-theme`) across reloads.
3. **Team card expand/collapse** — clicking a `.team-card` toggles `aria-expanded` and reveals the per-team waiver targets list. Clicking again hides them. Only one team open at a time is *not* enforced — multiple can be expanded.
4. **Skip link** — `Tab` from a fresh load focuses the `.skip-link` and pressing Enter jumps to `#main`.

## Sections & last tested

| Section | Last Tested | Notes |
| --- | --- | --- |
| Topbar / Brand / Theme toggle | 2026-05-17 | Stable. Toggle is a 44×44 button (touch target ✓). |
| Meta strip | 2026-05-17 | Wraps on narrow widths; "As of" pushes to right edge on wide. |
| Top Waiver Targets | 2026-05-17 | Card grid: rank · name+pills · projection. Bucket colors (G blue / F amber / C magenta) render. |
| Team Needs | 2026-05-17 | 1-up at <640px, 2-up at 640–1023, 4-up at ≥1024. Bar fill goes red on the top-need bucket. |
| Recent Transactions | 2026-05-17 | Fixed in this UAT pass — see "Known stable" below. |
| Empty / error state | 2026-05-17 | Tested via DevTools: fetch failure shows "Couldn't load state.json" line per section. |

## Known stable areas

- Theme toggle + persistence
- Mobile waiver-card layout at 375px (no horizontal scroll, no truncation)
- Desktop team-grid at 1280px (4 columns)
- Pill colors stable across themes
- All 45 pytest tests pass

## Known flaky / unstable areas

- **None known.** First UAT pass uncovered three transaction-rendering bugs (UAT-001..003 in `ISSUES.md`); fixed and confirmed in this run.

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

See `ISSUES.md` for the audit table:

- **UAT-001** Slot ID `-1` rendered literally as `S-1` (sentinel value). Fixed.
- **UAT-002** Team ID `0` rendered as `T0` (no-team sentinel). Fixed.
- **UAT-003** Raw `S1` / `S6` instead of slot labels (G / UTIL / BE). Fixed.
