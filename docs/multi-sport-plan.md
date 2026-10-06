# Multi-sport migration

## Mobile interaction plan — 2026-10-06

Measure PR #74 before changing presentation: same saved-team tasks, two phone widths, three connected leagues. Count explicit taps, text entries and swipes of at most 75% screen height; no locator auto-scroll. Equal-weight task p95 measures this task set, not observed user behavior. Keep before/after JSON and reject changed snapshot comparisons.

Options: denser cards alone preserve long tails; jumps/paging alone add taps; combine compact cards with Today jumps, mobile bottom tabs and five-result pages. Select the combined approach. Keep explanations, source links and snapshot dates visible or in labeled native disclosures. Search covers the entire shortlist, stays focused while typing, and resets paging. View changes return to content. Desktop keeps top tabs.

Bot repairs use existing schemas: match each healthy non-reserve player to only one lineup slot, neutralize stale status advice, report unavailable scoring when rules are absent, retain prior league snapshots with visible refresh errors.

## Original migration

User requested 2026-10-05. Selected approach: unified mobile shell, sport-scoped shared data, league-scoped rosters and advice. Separate apps duplicate collection; full rewrite risks the proven WNBA optimizer. Existing WNBA console remains available at wnba.html, loaded only when selected.

NFL uses documented read-only Sleeper league, roster, state and trending APIs. Owner fields never persist. Shared player catalog fetched at most once daily; news and schedules cached once per sport. Each league stores only IDs, settings and team-specific analysis. NFL ranks available pickups by roster coverage and global add activity; without verified stat inputs, no point projections or definitive drop instructions are fabricated. Optional shared per-game raw-stat imports are scored independently by each league's exact scoring settings.

Yahoo and ESPN NFL use a validated portable snapshot import until authorized credentials and actual league links exist. Setup stays visible. Each import must use stable Sleeper NFL IDs for players; shared stats never contain league-specific fantasy totals. No writes to provider rosters.

Deadline schema stores explicit UTC timestamps with source links. Public Sleeper settings contain undocumented numeric day codes: display raw setting and ask for an explicit verified waiver deadline instead of inventing an exact cutoff. Lineup decisions use published game kickoff times per roster; past kickoff shown locked. Injury guidance distinguishes confirmed status, IR eligibility from league rules, and unknown recovery/value. News alerts match exact ESPN athlete IDs only. Stale snapshots suppress actionable advice.

Daily refresh: league rosters + shared status/news/schedule/trends with TTLs. Deep run once or twice weekly: force enrichment and recompute; catalog still honors 24h limit. WNBA existing refresh unchanged. Imported leagues retain capture dates; refresh never makes them appear current.
