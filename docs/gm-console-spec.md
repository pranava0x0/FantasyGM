# Spec: GM Console — Waivers 2.0, Trades 2.0, Lineup Reset

> Status: **planned** (backlog placeholder under High). Authored 2026-07-16 from a codebase audit
> plus a competitive survey of ESPN, Yahoo Fantasy Plus, Sleeper, HashtagBasketball,
> Basketball Monster, RotoWire/RotoBaller, KeepTradeCut, and the AI-assistant field
> (Duello, League Loom, WalterPicks, FantasySP, Yahoo Assistant GM).
> Scope: the three product loops the user acts on weekly — **pick up players, make trades,
> reset lineups** — plus the cross-cutting UX overhaul that makes each answer reachable in
> ≤ 2 taps with no scrolling for the #1 recommendation.

---

## 1. Why this, why now

The competitive survey's core finding: **season-long WNBA fantasy tooling is a
near-zero-competitor space.** There is no "WNBA Monster," no WNBA trade analyzer, no
roster-aware WNBA waiver tool, no WNBA streaming planner. Everything that exists is DFS
optimizers (RotoWire, Daily Fantasy Fuel) or weekly editorial columns. Yahoo has no WNBA
season-long game at all. ESPN's own WFBA product lacks the "Trending"/"Recommended"
modules its NBA/NFL products got in the 2025 rebuild.

Meanwhile the patterns that make the best NBA/NFL tools win are all implementable on our
existing static-page + local-pipeline architecture:

| Pattern (who does it best) | Where we stand |
| --- | --- |
| Roster-aware recommendations, not vacuum rankings (ESPN "Recommended", Basketball Monster league sync) | ✅ Have it — per-team reorder by top need is our core loop |
| Every recommendation quantifies impact — "+Y pts", "improves win odds" (Yahoo Assistant GM, BBM standings delta) | ❌ We show raw projections, not *net gain over what you'd give up* |
| Schedule-awareness as the moat (Hashtag "playing today," BBM quality games) | 🟡 Have games-this-week/next; no daily view, no streaming planner |
| Trade tools as matchmakers, not judges (Yahoo Top 3 Trade Partners, Trade Monster suggestions) | 🟡 Calculator + one canned scenario ("trade your best player"); no partner matching |
| One-to-two-tap action paths (ESPN quick-add, Yahoo one-click lineup, Sleeper counter-in-place) | ❌ Recommendations dead-end; no action affordance, no ESPN deep links |
| A standing daily artifact, not a chatbot (gap — nobody does this) | 🟡 The page *is* the artifact but opens on a generic list, not "what should I do today" |
| Fairness bar + value trend + "similar real trades" (KeepTradeCut) | ❌ Composite score is a bare number; league's own history unused |

Full survey findings preserved in §9 (appendix) so this document is self-contained.

**Product thesis:** FantasyGM becomes the *daily standing brief* for a 5-minute-a-day GM —
the page is already right when it opens: today's lineup moves, today's claim, this week's
trade play. Advice-only (ESPN's write API is a wall — see §5.4), but every recommendation
ends in a one-tap deep link into ESPN with the action pre-staged.

---

## 2. Current state (audit, 2026-07-16)

**Pipeline** (`pipeline/`): ESPN private API (wfba) → raw snapshots → `docs/data/state.json`.
Per-game projection blends actual season avg, rolling 2-week avg, CBS + Yahoo scrapes
(`analyze.py`). Waiver ranking = per-game × games-next-week, injury-return boost, per-team
need adjustment (`rank_free_agents`, `waiver_targets_for_team`). Trades: one auto scenario
per team — "trade your best player, here's a fair 1–3-player package from each rival,"
composite = 0.6 fairness + 0.4 need fit (`trades.py`). Schedule: games per pro team this
week / next week (`schedule.py`). History: `transactions.jsonl` (338 rows: 122 WAIVER —
26 executed FAAB bids with amounts, mostly $1–8 — 85 ROSTER, 3 TRADE_PROPOSAL,
1 TRADE_DECLINE), `game_logs.jsonl`, news/social feeds, AI GM takes for top-30 FAs.

**UI** (`docs/`, vanilla JS, 1.7k-line app.js): 5 top tabs (Waivers / Team Needs / Trades /
News / Transactions). Client-side lineup logic already exists: `optimalLineupSlots()` fills
the 9 active slots (2 G, 3 F, 1 F/C, 3 UTIL) greedily; `recommendedStartersNextWeek()`
renders Start/Sit-next-week pills in roster tables; the trade calculator re-optimizes both
lineups post-trade and shows Δthis-week/Δnext-week/Δppg.

**What's structurally missing:**

1. **No "me."** Every view is league-symmetric; the user's own team isn't privileged.
   (Backlog already has "My team mode" — this spec promotes it to the keystone.)
2. **No current-vs-optimal lineup diff.** We compute the optimal lineup but never compare
   it to the *actual* `lineup_slot_id` assignments — the literal "reset lineup" feature.
3. **No daily granularity.** WNBA locks per game time; matchups are weekly but the game is
   daily streaming (2–4 games/team/week, unlimited bench). We only model week windows.
4. **No net-impact framing.** A waiver card says "31.4 proj next wk" — not "+9.2 over your
   worst starter if you drop X."
5. **No action affordances.** No ESPN deep links, no bid guidance, no add/drop pairing.
6. **Trades tab answers a question nobody asked** ("what if I traded my best player?")
   instead of "who should I trade *with*, for *what*, and *why them*."

---

## 3. Feature spec A — Waivers 2.0: from ranked list to claim sheet

**Job to be done:** "Tell me who to claim, what to bid, who to drop, and let me act in one tap."

### A1. Net-impact framing (the headline number changes)

Every waiver card for the selected team (see §D My Team) leads with **net gain**, not raw
projection:

```
+9.2 pts this week  ·  over your current worst starter (K. Martin, 12.1)
```

- Computation (client-side, data already present): simulate `optimalLineupSlots(roster + candidate)`
  minus `optimalLineupSlots(roster)` on projected-points-this-week. That inherently handles
  "she's good but you're saturated at G" — net gain collapses toward 0.
- League-wide (no team selected): keep current ranking, but card sub-line shows
  "best fit: NUT (+11.0) · C9 (+8.4)" — the two teams who gain most.
- Rationale: Yahoo quantifies every suggestion's impact on projected score/win odds; BBM
  shows standings movement. "Do X" without "worth +Y" doesn't convert (survey §11.2).

### A2. Add/drop pairing (drop candidate built into the card)

A claim is always a pair in a full-roster league. Each card (team-scoped view) shows the
recommended drop: lowest net-loss roster player (bench-first, injured-first, fewest games
remaining), with the combined line:

```
ADD R. Howard (FA) · DROP K. Martin  →  net +7.8 this wk, +6.1 next wk
```

Tapping the drop name opens the existing player modal (nothing new to build there).
Never auto-suggest dropping a player whose season ppg ranks in the roster's top 6 without
an explicit "core player" warning pill.

### A3. FAAB bid guidance

We hold both sides of the market: each team's `faab_remaining` (already in state) and the
league's own executed bid history (26 winning bids in `transactions.jsonl`). Show on each
card:

```
Suggested bid: $3–7   ·  league range for similar adds: $1–8  ·  you have $69
```

- Pipeline: new `pipeline/faab.py` aggregates executed WAIVER bids → percentile bands
  overall and bucketed by the added player's trailing per-game value (e.g. <10 / 10–20 / 20+
  ppg tiers). Small-N is fine — show the band with `n=` so it's honest
  ("league range $1–8 · 26 claims"). Append-only source, idempotent aggregation.
- Suggested bid = band midpoint scaled by (candidate's net gain ÷ median net gain of
  this week's top-10 targets), clamped to remaining budget, floor $1.
- Schema: `WaiverTarget.bid_guidance: {suggested_lo, suggested_hi, league_min, league_max, sample_n} | None`.

### A4. Urgency + schedule chips (fewer clicks to the "when")

- **`Plays tonight`** chip (needs per-day schedule — §5.2) — Hashtag's core filter primitive.
- **`3 gms this wk`** already exists; add **`+2 gms at your need`** variant when team-scoped:
  the pickup adds N games *at the team's top-need bucket* this week. Nobody in the survey
  models schedule-density streaming for WNBA; this is the moat feature.
- **`Trending +12%`** chip from `percent_change` (social proof à la ESPN Trending/Sleeper
  add counts — data already in state, currently buried in the modal).
- **`Streamer` / `Anchor`** tag (promoted from backlog): `streamer` = high this-week proj,
  low season per-game; `anchor` = high per-game floor. One glance answers "keep or churn."

### A5. Act-on-ESPN deep link

Card footer: **`Claim on ESPN ↗`** →
`https://fantasy.espn.com/wbasketball/players/add?leagueId=2043154241&playerId=<id>`
(exact path to be verified against the live app during implementation; fall back to the
player-card URL already used in the modal). This is the "recommendation → pre-staged
action → one tap" pattern the survey found nobody does cleanly (§12). Advice-only stays
true: the tap lands in ESPN's own confirm flow.

**Card anatomy (mobile, target ≤ 88px collapsed):**

```
┌──────────────────────────────────────────────────┐
│ 1  [photo] R. Howard   G · DAL     ▲ +9.2 net    │
│    Streamer · Plays tonight · 3 gms · Own 14% ↑  │
│    ADD her / DROP K. Martin · bid $3–7           │
│    ── tap to expand: GM take, last 10, claim ↗ ──│
└──────────────────────────────────────────────────┘
```

Rank, identity, and the one decision-number on line 1; everything else is chips; deep
detail stays behind the existing modal. No card requires horizontal scroll at 375px.

---

## 4. Feature spec B — Trades 2.0: from judge to matchmaker

**Job to be done:** "Find me the partner, the package, and the pitch."
League context: 3 proposals, 1 decline, 0 accepted trades all season — the barrier is
*starting* negotiations, so the tool optimizes for credible openers, not adjudication.

### B1. Trade Partner Finder (replaces "scenarios" as the tab's lead)

The Yahoo Trade Hub "Top 3 Trade Partners" pattern, computed from data we already have:

- For each team pair (A, B): **complementarity score** = A's surplus at B's top-need bucket
  × B's surplus at A's top-need bucket (surplus = positive gap_vs_league). Mutual-benefit
  deals only — one-sided "fleece finder" tools poison league trust and never clear.
- Lead card per partner: `NUT ↔ C9 · They need G, you're +45 G · You need FC, they're +38 FC`
  followed by 1–3 concrete packages (reuse `trades.py` package search, but seeded from
  *both* teams' need-fit rather than "best player" as the pivot).
- Each package shows a **before/after strip for both sides**: Δ this week, Δ next week,
  Δ ppg — the existing `evaluateTrade()` simulation, now applied to generated offers, plus
  the fairness bar (B2).

### B2. Fairness bar + verdict (KTC's instant-read presentation)

Replace the bare `composite_score` with a horizontal two-tone bar: each side's package
value (rest-of-season ppg × remaining games basis), a center notch at even, a verdict
label at most one word off-center: `Even` / `Slightly favors NUT` / `Favors C9`. The
0.6-fairness/0.4-need-fit composite stays as the *sort key*; the bar is the *display*.

### B3. Standings stakes (BBM's killer framing, points-league edition)

For H2H points, translate package Δppg into matchup terms: from `matchup_history`, compute
each team's average weekly margin; show
`This deal swings your typical matchup by +6.3 pts — you've lost 2 games by less this season.`
That last clause — margin-of-defeat lookup against actual completed matchups — is one
`Array.filter` on data already shipped, and it's the single most persuasive line in the
product (nothing in the survey does it).

### B4. Trade calculator keeps its job, gets the same clothes

The manual two-roster calculator stays (it's the "verify my own idea" path) but adopts the
fairness bar, standings-stakes line, and post-trade **lineup diff** (which players enter /
leave each side's optimal lineup — computed already, just not rendered as a diff).

### B5. AI trade pitch (reuses the GM-take machinery)

For the top 3 generated packages league-wide, an authored-at-refresh 2-sentence pitch
*written to be sent to the other GM*: "C9 is 45 pts over league average at G but has the
league's biggest FC gap; you're the mirror image. X-for-Y moves both of you up." Rendered
with the existing AI-attribution treatment (3px accent border + model credit). Storage:
same pattern as `ai_summaries.json` (`data/ai_trade_pitches.json`, keyed by a stable hash
of the two team-ids + player-id sets so unchanged packages aren't re-generated).

Schema: `TradeScenario` extended (or superseded) by
`trade_partners: list[TradePartner]` with
`{team_a_id, team_b_id, complementarity, packages: [{gives, gets, fairness_ratio, delta_week_a, delta_week_b, delta_ppg_a, delta_ppg_b, margin_context_a, margin_context_b, ai_pitch}]}`.

---

## 5. Feature spec C — Lineup Reset (the new surface)

**Job to be done:** "Is my lineup right for tonight and for the week — and if not, exactly
which swaps fix it?" This is the assessed "how to reset lineups" answer.

### C1. What "reset" can and cannot mean (the assessment)

Verified during research: ESPN's write API (`lm-api-writes.fantasy.espn.com`) is
undocumented and unimplemented in every public library — the standard `espn-api` client
(which now ships a WNBA `wbasketball` module) is GET-only; even automation-branded
projects are recommendation-only. Every credible third-party assistant (League Loom,
STACKED, Duello) is read-advise-deep-link. **Decision: FantasyGM computes the reset and
stages it; the user applies it on ESPN in one tap.** Three execution tiers:

1. **Ship now — moves list + deep link** (`fantasy.espn.com/wbasketball/team?leagueId=…&teamId=…`).
   Zero risk, works on the static page.
2. **Ship later, optional — local Chrome-assisted apply**: a `/set-lineup` step inside the
   existing refresh skill drives the user's own logged-in browser (same pattern as the X
   scraper) to perform the staged swaps, with per-swap confirmation. Local only, never CI,
   session cookies never leave the machine. Off the critical path.
3. **Never — direct `lm-api-writes` calls.** Unofficial write endpoints against the user's
   auth cookies risk the account and break silently. Documented as a non-goal.

### C2. Moves list (current vs optimal diff)

Pipeline gains `pipeline/lineups.py`:

- Compare actual slot assignments (`lineup_slot_id`, already in state) against
  `optimal_lineup(roster, horizon)` for two horizons: **tonight** (players with a game at
  the next scoring period, per-day schedule §5.2) and **this week**.
- Emit the **minimal swap set**, not two lineups to eyeball:
  `[{action: "swap", player_in, player_out, slot_label, gain_pts}, …]` ordered by gain.
- Output per team: `lineup_check: {status: "set" | "moves_available", points_left_on_bench,
  moves: […], computed_for_period}`. "Points left on bench" = optimal minus current
  projected total — the single scalar that says whether to bother.
- Edge rules: confirmed-OUT players never recommended in (mirror `_is_out`); a locked
  player (game started) is excluded from tonight's swaps; ties don't generate churn moves
  (require gain ≥ 0.5 pts).

UI: a **Lineup panel** at the top of each team card and (for My Team) on Today:

```
⚠ 2 moves available tonight — 14.6 pts on your bench
  1. Start A. Gray (G, plays 7:00) over K. Martin (no game)   +9.2
  2. Start T. Reid (F/C, plays 9:30) over D. Carter (OUT)     +5.4
  [ Apply on ESPN ↗ ]                       All set? This clears itself.
```

Empty state is a win state: `✓ Lineup set — optimal for tonight` (positive framing, per
vocabulary discipline — never "lazy lineup" / "mistakes").

### C3. Week planner grid (streaming view)

A roster × days grid for the matchup week: dot = game (filled = projected starter that
day, outline = benched that day, red ring = OUT/QUES). Column totals = starters-with-games
per day; a day whose total < 9 active slots gets a `stream day` chip — that's the day a
1-game pickup is pure profit, which links back into Waivers filtered to `plays that day`.
This is BBM's "quality games" idea adapted to unlimited-bench weekly-H2H WNBA, and per the
survey it exists nowhere for WNBA. Desktop: full grid; mobile: horizontally scroll-snapped
day columns inside the card (the page itself never scrolls horizontally).

### C4. Lineup efficiency (retro audit)

From `game_logs.jsonl` + snapshot history: last week's actual points captured vs the
best-possible lineup in hindsight → `Lineup efficiency: 91% (missed 12.3 pts)` on each
team card, sparkline over weeks later. Motivates the habit loop and quantifies what the
tool is worth. (Also a natural future notification hook: efficiency < 85% → nudge.)

### C5. Matchup header (win-odds context)

Each team's Lineup panel is topped by the live matchup frame: opponent, current score,
`proj 412 – 398`, and a simple win-probability estimate (normal approximation from
historical weekly-score variance in `matchup_history`; label it "rough odds," 0 decimal).
This gives every move/claim/trade a "why this matters now" anchor — Yahoo's win-odds
framing, computed from data we already ship.

---

## 6. Feature spec D — My Team mode (the keystone, promoted from backlog)

Everything above sharpens when the page knows whose GM it is:

- **Selection:** first visit → team picker (8 logos, one tap); persisted in
  `localStorage.fgm_my_team`; switchable from the header chip. No accounts, no cookies,
  static page unchanged. URL param `?team=NUT` overrides for shareability.
- **New default tab: "Today"** — the standing daily brief (the artifact no competitor
  builds, survey §12): ① Lineup panel (C2) with tonight's moves, ② today's #1 claim with
  bid + drop pairing (A1–A3), ③ this week's best trade opener (B1), ④ matchup frame (C5),
  ⑤ two-line AI daily note. Every block is one action with one number and one tap-through.
  Target: the five blocks fit one 375×812 viewport with zero scrolling.
- All other tabs auto-scope: Waivers reorder to my team (current per-team logic, now
  defaulted), Teams grid sorts my team first, Trades leads with my best partner.
- League-wide view remains one tap away (header chip → "All teams") — the league-symmetric
  product is a feature (it's also the commissioner's view), not a casualty.

---

## 7. Design & UX spec

### 7.1 Identity: ESPN-adjacent, deliberately not Claude

The page should read like a **broadcast-grade sports terminal**: the user should feel
ESPN-fluency (same idioms) without ESPN's brand (per DESIGN.md §3.2) — and *nothing* about
it should feel like an AI chat product.

**Adopt (ESPN fantasy fluency):**
- Player identity rows: headshot-left, name + position/team sub-line — the universal
  fantasy grammar (headshots already load in the modal; extend to top-3 waiver cards and
  lineup moves; lazy-load below the fold, `loading="lazy"`, single CDN origin).
- Red/green signed deltas with ▲/▼ glyphs for every net-impact number.
- Position-color chips (our existing desaturated G/F/C tokens — adjacent to ESPN's
  position colors, not copies).
- Dense stat tables with tabular numerals; uppercase micro-labels (`PROJ`, `OWN%`, `GMS`).
- Dark theme default flipped on (sports terminals are dark; light stays one tap away).

**Distinguish (so it isn't ESPN):**
- Editorial serif display numerals for the hero numbers (DESIGN.md §2) — ESPN is
  all-sans; this is our FT-meets-scoreboard signature.
- Opportunity-framed language everywhere ESPN/Yahoo say "weakness" (vocabulary discipline
  is a product differentiator per the survey, not just hygiene).
- The accent system stays ours (`--accent`, bucket tokens) — no ESPN red/navy.

**Anti-Claude rules (hard constraints):**
- No chat interface, no message bubbles, no prompt box, no "ask me anything."
- No purple/violet gradients, no sparkle ✨ iconography, no rounded-blob mascot energy.
- AI content is *ingredient, not persona*: GM takes and trade pitches keep the existing
  3px-border + tiny "AI" credit treatment, always subordinate to the numbers.
- System font stack + our serif display — never an "AI product" geometric sans.

### 7.2 Interaction budget (measurable, testable in UAT)

| User question | Budget |
| --- | --- |
| "Any lineup moves tonight?" | 0 taps, 0 scroll (Today, block ①) |
| "Who do I claim + what bid?" | 0 taps for #1; 1 tap for full list |
| "Whom should I trade with?" | 1 tap (Trades) or 0 (Today block ③) |
| "Details on player X" | ≤ 2 taps from anywhere (existing modal) |
| "Do the thing on ESPN" | +1 tap (deep link) from any recommendation |
| Another team's view | 2 taps (header chip → team) |

Every recommendation block renders: **one number, one sentence, one action.** Anything
more lives behind expand/modal. Add `uat.md` cases asserting the budgets.

### 7.3 Responsive layout (375 / 768 / 1024+, per DESIGN.md bands)

- **Mobile (<640):** bottom tab bar (5 items: Today · Waivers · Teams · Trades · More),
  44px+ targets, `env(safe-area-inset-bottom)`; the top tab row is retired on mobile —
  thumb-reach beats reachability-hostile top tabs for a check-in app. Detail panels become
  bottom sheets (radius 14); the player modal converts to a bottom sheet at this band.
  Week-planner grid scroll-snaps inside its card.
- **Tablet (640–1023):** 2-up grids (team cards, partner cards); Today becomes a 2-col
  masonry (lineup + matchup left, claim + trade right); top tabs return.
- **Desktop (≥1024, max-width 1280):** 3-col Today; Teams grid 3-up; Trades = partner list
  left / detail right (master-detail, no page navigation); optional sticky right rail on
  Waivers showing my-team lineup context while browsing claims.
- One DOM tree, media queries only (no duplicated mobile/desktop trees). `[hidden]{display:none}`
  guard rides along with every new `display:` override (DESIGN.md §12.1).

### 7.4 Performance & a11y budgets

Page stays first-paint-light: state.json grows (per-day schedule, lineup checks, partners)
— budget ≤ 200 KB gzipped; if exceeded, split `state.json` (core) + `extras.json`
(lazy-loaded on tab open, `priority: "low"`). Headshots lazy + `<img>` dimensions set (no
CLS). No new libraries, no web fonts, no icon set — chips and glyphs only. All new
interactive elements: `:focus-visible` rings, tab-panel ARIA kept, bottom bar is a
`<nav>` with `aria-current`. Contrast ≥ 4.5:1 in both themes including the new dark
default. Reduced-motion kills sheet transforms.

---

## 8. Delivery plan

Ship order optimizes user-visible value per unit of new data plumbing. Each phase is
end-to-end shippable (no half-features on main).

| Phase | Contents | New pipeline surface |
| --- | --- | --- |
| **P1 — Lineup Reset core** | `lineups.py` moves list + points-left-on-bench; Lineup panel on team cards; ESPN deep links (lineup + player claim); My Team selection + team-scoped defaults | Per-day pro schedule (§5.2 note below); `lineup_check` on `TeamState` |
| **P2 — Today tab + mobile nav** | Today brief (5 blocks); bottom tab bar; dark default; bottom-sheet modal on mobile | none (recomposition of P1 + existing data) |
| **P3 — Waivers 2.0** | Net-gain framing, add/drop pairing, FAAB guidance, urgency chips, streamer/anchor | `faab.py` bid bands; `bid_guidance`, `tags` on `WaiverTarget` |
| **P4 — Trades 2.0** | Partner finder, fairness bar, standings-stakes line, calculator reskin, AI pitches | `trade_partners` in state; `ai_trade_pitches.json` |
| **P5 — Planner & efficiency** | Week planner grid, stream-day chips, lineup-efficiency retro | Daily lineup/game-log joins from history |

Per-day schedule note: `schedule.py` already ingests the pro schedule to count games per
week; P1 extends it to expose `games_by_pro_team_by_period` (per scoring period = per day)
so "tonight" and the planner grid share one source. Tests: golden-fixture tests for
`lineups.py` swap minimality and lock/OUT edge rules; property test that moves never
recommend an OUT player in; `faab.py` percentile math on the fixture history; schema
round-trips with `extra="forbid"`.

**Non-goals (unchanged from CLAUDE.md + reaffirmed):** no lm-api-writes calls, no CI
pipeline runs, no accounts/backend, no owner names anywhere, no category-league support
(league is H2H points), no NBA generalization until the WNBA loop is airtight.

**Open questions for the user:**
1. Confirm ESPN deep-link URL shapes on the live league (add-player and team-roster pages) — 5-minute browser check during P1.
2. Dark-as-default: preference confirmed, or keep light default?
3. Chrome-assisted lineup apply (C1 tier 2): worth building after P2, or is deep-link enough in practice?

**Success metrics (checkable from history files + UAT):** lineup efficiency trend ≥ 95%
after P1 (from C4 retro math); time-to-first-action in UAT ≤ 10s cold load on mobile;
every UAT interaction-budget case green; at least one FAAB claim and one proposed trade
in `transactions.jsonl` informed by the tool (anecdotal but the point).

---

## 9. Appendix — competitive survey (2026-07-16)

Condensed per-product findings; kept here so the spec is self-contained.

- **ESPN Fantasy (native):** 2025 rebuild added Trending (global add-rate), Recommended
  (roster-need-aware pickups), home-feed quick-add. No trade fairness/analyzer at all —
  propose → review → accept/veto only. No lineup optimizer; "Auto Control (AI)" exists
  only as a commissioner toggle for abandoned teams. Redesign drew heavy backlash
  (buried navigation). WFBA (WNBA) product lacks the NBA product's new modules.
- **Yahoo Fantasy Plus (~$35–49/yr, NBA only — no WNBA game):** the premium benchmark.
  Waiver add/drop suggestions; Research Assistant comparisons; min/max (floor/ceiling)
  projections; **Trade Hub** with Most Traded, Team Analysis, League Rosters, and Top 3
  Trade Partners (matches your gaps to rivals' surpluses); **Assistant GM** one-click
  optimal lineup, multi-week lineups, and a morning push when a change improves win odds —
  every suggestion shows projected-score/win-probability impact. Criticized for paywalling
  and projection-chasing.
- **Sleeper:** trending adds/drops with add counts (social proof, no projections/needs
  awareness); no native trade values — its redesigned Trade Center is negotiation UX
  (trade block into league chat, interest signaling, exploding offers, counter-in-place)
  with valuation outsourced to an ecosystem (KTC, FantasyCalc) enabled by its open
  read-only API; no lineup optimizer. Basketball thinner than football.
- **HashtagBasketball (NBA only):** waiver page = playing-today × rostered-<50% × ranked;
  trade analyzer totals each side and shows per-category strengths via color-graded
  z-score tables (the community-standard visualization); no league sync so no roster
  context; dated UI.
- **Basketball Monster (NBA only, paid):** the quant benchmark — per-category z-scores,
  per-game vs total value toggle, league sync, games-played-cap "quality games" planning;
  Trade Analysis shows projected-standings impact; Trade Monster auto-balances and
  suggests mutually beneficial trades. 1990s UI, steep curve.
- **RotoWire / RotoBaller / Daily Fantasy Fuel:** DFS-optimizer-first; RotoWire is the one
  outlet with real WNBA coverage (draft kit, custom rankings, daily lineups). RotoBaller's
  free "Who Should I Start?" head-to-head comparison tiles with star ratings are a good
  pattern. Season-long lineup tooling thin everywhere.
- **KeepTradeCut (dynasty NFL):** crowdsourced values via forced-choice micro-judgments;
  calculator = per-asset value, summed sides, fairness bar, even-the-deal suggestions,
  6-month value sparklines, and "similar real trades" from its database. The
  value-presentation reference. No basketball equivalent at scale.
- **AI assistants:** Yahoo Assistant GM (only shipped platform-native AI GM); WalterPicks
  (paid, range-of-outcomes, NBA newer); FantasySP Fantasy Assistant (waiver recs, trade
  breakdowns, optimal lineups); Duello (ESPN import via Chrome extension, AI trade
  verdicts with win/loss scoring); League Loom + STACKED (free read-only MCP connectors —
  "it reads, you decide"). **All advice-only; execution stays inside platforms.**
- **ESPN API:** `lm-api-reads` is the documented-by-community GET surface (cookies for
  private leagues); `espn-api` Python lib now has a `wbasketball` module, GET-only;
  `lm-api-writes` exists but no public implementation — recommendation + deep-link is the
  industry-standard workaround.
- **WNBA format facts:** ESPN WFBA is the only mainstream season-long WNBA game; H2H
  points default; ~9 active slots in our league (2 G / 3 F / 1 F/C / 3 UTIL + unlimited
  bench); default lineup locks are per-player at game time → daily-streaming dynamics on
  weekly matchups; 2–4 games/team/week makes schedule density the dominant edge.
