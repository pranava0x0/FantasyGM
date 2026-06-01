# UAT Baseline — FantasyGM

_Created: 2026-05-17_
_Last run: 2026-06-01 (second pass)_

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
| Topbar / Brand / Theme toggle | 2026-06-01 | Stable. Light↔dark toggle persists via localStorage. |
| Meta strip | 2026-06-01 | Wraps to 3 lines at 375px. "As of" aligns right. All 8 teams reflected. |
| Waivers tab | 2026-06-01 | 15 healthy targets render. OUT players correctly excluded. Bucket pills (G/F/C), per-game, ownership %, trend all present. Player modal opens and closes. |
| Team Needs tab | 2026-06-01 | 4-up desktop, 2-up tablet, 1-up mobile. Expand/collapse works. Summary bullets, tailored picks, full roster, transactions shown. |
| News tab | 2026-06-01 | Stable. WNBA-only articles. Player tags visible on highlighted items. |
| Transactions tab | 2026-06-01 | Stable. All 5 today's transactions render. Slot labels correct. FAAB bids shown. |
| Player modal — Desktop | 2026-06-01 | Stable. Opens from waivers, roster, transactions, team picks. Escape/backdrop close. Stats grid, news with TEAM NEWS badges, Reddit section, ESPN link. |
| Player modal — Mobile | 2026-06-01 | Stable. Fits 375px. 3-stat and 5-stat layouts correct. TEAM NEWS badges readable. Reddit section present. |
| Mobile 375px | 2026-06-01 | All 4 tab labels fully visible (padding-inline:8px at ≤420px). Layout correct at 1-up. |
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
