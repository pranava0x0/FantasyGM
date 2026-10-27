# Mobile task costs — PR #74

Measured 2026-10-06 on the same local NFL snapshot. Three connected leagues, saved own teams, 375×812 and 390×812. Each task starts fresh. Ten tasks × three leagues × two widths = 60 journeys.

| Metric | Before | After | Change |
| --- | ---: | ---: | ---: |
| Median interactions | 4 | 3 | −25% |
| p95 interactions | 18 | 8 | −56% |
| Worst interactions | 18 | 8 | −56% |
| Total swipes, all 60 journeys | 258 | 32 | −88% |

| Task | Before interactions | After interactions | After taps / entries / swipes |
| --- | ---: | ---: | --- |
| lineup | 1 | 1 | 1 / 0 / 0 |
| last-alert | 3–5 | 1–3 | 1 / 0 / 0–2 |
| deadline | 0 | 1 | 1 / 0 / 0 |
| first-pick | 2 | 1 | 1 / 0 / 0 |
| last-pick | 10–14 | 5–7 | 5–6 / 0 / 0–1 |
| find-pick | 4 | 3 | 2 / 1 / 0 |
| last-roster | 4–5 | 3 | 1 / 0 / 2 |
| last-news | 6 | 6 | 6 / 0 / 0 |
| confirm-cutoff | 2 | 2 | 1 / 0 / 1 |
| deep-list-to-roster | 14–18 | 6–8 | 6–7 / 0 / 0–1 |

Interactions = explicit tap + text-entry session + swipe of at most 609px (75% screen height). Shorter final swipes count one. Browser locator auto-scroll is excluded. The target must expose its first 300px (or whole element if smaller) above the bottom tabs. Lineup and deadline tasks target their section headings; the other tasks target cards, list rows or the cutoff form. Reading time, device keyboard occlusion, native league/team selectors and first-use setup are not measured. Equal task weighting; p95 is a property of this task set, not real-user telemetry.

Changes: two-row mobile selectors; bottom tabs stay reachable; view changes return to content; Today jumps reach lineup, alerts and deadlines; supporting explanations use native disclosures; pickups/news show five results per page with paging above results; search filters the entire shortlist without replacing the active input.

Tradeoffs: initial deadline heading now takes one tap instead of zero; sources/context take one disclosure tap; browsing all results takes explicit page taps. No result, recommendation input or source was removed.

Reproduce: serve docs, then run `NODE_PATH=/path/to/node_modules node scripts/mobile_uat.cjs tmp/mobile-current.json`. For a fresh-snapshot regression ceiling, set `UAT_BUDGET=8`. Compare the exact original snapshot with `node scripts/mobile_uat.cjs tmp/mobile-after.json docs/mobile-uat-before.json`; changed snapshots intentionally fail this comparison. Chrome path: `CHROME_PATH`; site URL: `UAT_URL`.

Raw per-journey costs: [before](mobile-uat-before.json), [after](mobile-uat-after.json). Runner: [scripts/mobile_uat.cjs](../scripts/mobile_uat.cjs).
