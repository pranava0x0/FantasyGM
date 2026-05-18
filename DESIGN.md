# DESIGN.md — Universal Visual & UX Principles

> Base file for every project in this folder with a UI. Project-specific `design.md` files extend this with palette, motif, and content rules. When project conflicts with base, project wins.
>
> Companion files: [CLAUDE.md](CLAUDE.md) is the engineering principles; [AGENTS.md](AGENTS.md) is the agent workflow.

---

## 0. Project palette + motif (Fantasy GM)

**Visual posture:** sports-desk dashboard. The page should read like a Bloomberg terminal for a fantasy GM, not a consumer fan-engagement product. Density over decoration. Tabular numerals everywhere there's a number — projected points, ownership %, gap-vs-league.

**Voice:** proactive and opportunity-framed. Avoid loaded words like *weakness*, *poor*, *struggling*. Use *top need*, *biggest upgrade*, *room to add*, *opportunity*. The user is a GM looking forward, not auditing past failure.

**Layout primitives:**

1. **Meta strip** at top — league name, scoring period, matchup period, captured timestamp. Tabular numerals.
2. **Waiver Targets** as the primary surface (the main product loop). Ranked list cards. Each card: name + WNBA team + position bucket + projected next-period points + ownership %. A small "fits N need" pill ("Fits FC", "Need") when adjusted score > base score and the pick targets the team's top need.
3. **Team Needs grid** — one card per team. Two mini-bars (G / F-C combined) with gap-vs-league overlay. The bar for the team's *top need bucket* renders in `--gap-weak` to draw the eye to the biggest upgrade area. Tap to expand → per-team waiver target list reorders.
4. **Transaction log** as a chronological list at the bottom. Latest first. Each row: team abbrev (no owner names ever), action type, players moved, timestamp.
5. **Player detail modal** — any player name (in waivers, rosters, team-target lists, transaction items, news tags) is a button that opens a centered modal with photo, position + pro team, stat blocks, status (rostered-by vs free agent), tagged news, recent transactions involving the player, and a deep-link to their ESPN page. Foundation for the "scouting" loop.

**Brand-adjacent color encoding (semantic tokens, not brand):**

```css
:root {
  --bucket-guard:    #4a90e2;   /* G — cool blue */
  --bucket-forward:  #f5a623;   /* F — amber */
  --bucket-center:   #b94aa3;   /* C — magenta */
  --gap-strong:      #2e7d32;   /* team is above league avg */
  --gap-weak:        #c62828;   /* team is below league avg */
  --gap-neutral:     var(--text-muted);
  --txn-add:         var(--gap-strong);
  --txn-drop:        var(--gap-weak);
  --txn-lineup:      var(--text-muted);
  --txn-trade:       #6f42c1;
}
```

Bucket colors are intentionally desaturated relative to ESPN's brand palette — we're not affiliated with ESPN. WNBA team abbreviations render in `--font-mono` to keep G/F/C colors as the load-bearing visual signal.

**Pills used in this project:**

| Pill | Meaning | Style |
| --- | --- | --- |
| `G` / `F` / `C` | Position bucket | Outline pill, color from `--bucket-*` |
| `Fits FC` / `Need` | Pick targets this team's top need | Solid pill, `--accent` (Fits) or `--gap-weak` (Need) |
| `+2.3 vs LG` | Gap to league avg, when positive | Inline span, `--gap-strong` |
| `-4.1 vs LG` | Gap when negative | Inline span, `--gap-weak` |
| `ADD` / `DROP` / `TRADE` / `LINEUP` | Transaction type | Outline pill, color from `--txn-*` |

**Content rules:**

- **No owner names, ever.** UI shows team `name` + `abbrev` + `team.id`. Hover-tooltip on the team chip shows ID and division — never a person.
- **"Captured as of <date>"** is visible in the meta strip. The data is a snapshot, not live.
- **Empty states** ("no transactions yet this period") render as italic muted — never a blank section.
- **Projected points always show 1 decimal.** Whole-number projections look fake; one-decimal precision signals "this is a model output."

---

---

## 1. Posture

Two sentences set every call below:

1. **The content is the product. Chrome earns its pixels.** Headers, filters, KPIs — anything that isn't the primary surface (map, feed, list, form, canvas) justifies itself by helping the user understand or narrow what they're looking at.
2. **Performance is a design constraint, not a follow-up.** Every "nice touch" (web font, blur, full-page animation) competes with the first paint and the 60fps pan/zoom budget. Choose perf when they conflict.

Aesthetic should follow the product. An editorial dashboard reads like the FT (serif headlines, citation footer, tabular numerals). A consumer app reads like its category (warm palette for food, dark + accent for tooling). A government data tool reads like a public record. Don't paste one product's voice onto another. Project-level `design.md` carries the *specific* visual identity; this file carries the *universal* rules every identity has to respect.

---

## 2. Typography — system stacks only by default

No web fonts unless the project specifically justifies one. A Google Fonts link costs a render-blocking RTT and ~50KB; the system stack approximates Charter / Inter / SF Mono on every target browser.

```css
--font-sans:  -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui,
              "Helvetica Neue", Arial, sans-serif;
--font-serif: "Charter", "Source Serif 4", "Source Serif Pro",
              "Iowan Old Style", "Apple Garamond", "Palatino", "Georgia",
              "Times New Roman", serif;
--font-mono:  ui-monospace, "SF Mono", "JetBrains Mono", Menlo,
              Consolas, monospace;
```

- **Serif for editorial display** (H1, hero H2, KPI numerals, verbatim quotes). Signals "this is content, not chrome."
- **Sans for body and UI** (everything else).
- **Mono for code, IDs, paths, share codes.** Anything that has to round-trip a copy/paste.
- **Tabular numerals.** `font-feature-settings: "tnum"` on `:root`. Any column of numbers (KPIs, table cells, dates, counts) lines up.

If the project genuinely needs a custom font (rare — most don't), audit the alternative system stack on target browsers before introducing a fetch.

---

## 3. Color tokens

**All colors live as CSS custom properties on `:root`, with `[data-theme="dark"]` overrides.** JS reads via `getComputedStyle()` — never hardcode a hex outside `:root`.

```css
:root {
  --bg: …;          /* page background */
  --surface: …;     /* cards, panels */
  --surface-2: …;   /* inputs, secondary surfaces */
  --border: …;
  --text: …;
  --text-muted: …;
  --accent: …;      /* primary CTA, focus ring */
}

[data-theme="dark"] {
  --bg: …;
  /* ... */
}
```

### 3.1 Semantic separation

When a project uses color to encode meaning (status, stance, category), keep *meaning* and *brand* in separate token families:

- **Brand / surface tokens** — neutral chrome (`--bg`, `--surface`, `--text`).
- **Semantic tokens** — meaning (`--status-{success,warning,error}`, `--stance-{positive,mixed,negative}`).
- **Category tokens** — distinguish without ranking (`--category-{a,b,c}`).

Never conflate them. Coloring "category" with the same palette as "status" tells the user "category A is bad," which is rarely what you mean.

### 3.2 Brand-adjacent colors are not brand colors

When showing third-party brands (companies, products, services), use **brand-adjacent but neutral** tones — desaturated versions that distinguish without implying endorsement. Using a company's actual brand color implies affiliation and invites legal questions.

### 3.3 Theme swap is JS, not filter

When users toggle light/dark, re-paint via the CSS variable swap and a JS pass over any canvas / SVG layers that read the variables. Never use `filter: brightness/contrast` on a tile pane or content layer — it recomposites every pan/zoom frame and tanks mobile perf.

---

## 4. Spacing scale

A 4 / 8 px ladder covers ~99% of cases:

`4, 6, 8, 10, 12, 14, 16, 18, 22, 24, 28, 32`

If you find yourself typing `7px` or `13px`, round to the nearest step unless you have a documented reason. A project usually doesn't need an explicit `--space-2` variable — keep values inlined until a refactor would actually save more LOC than it churns.

---

## 5. Radii, shadows, motion

- **Radii:** `4` (chips), `6` (small inputs), `8` (buttons, cards), `12` (modals), `14` (mobile bottom sheets), `999` (pills).
- **Shadows:** one soft (`0 1px 2px rgba(0,0,0,0.06)`) for at-rest cards; one elevated (`0 4px 18px rgba(0,0,0,0.08)`) for panels and toasts. Dark theme uses heavier alpha (`0.4–0.5`) because contrast against a dark `--bg` needs more.
- **Motion:**
  - `90ms` — table row hover, color swaps
  - `120ms` — button / input hover
  - `200ms` — panel slide-in/out, modal open
  - `300ms max` — toast, fade
  - **No motion above 300ms.** No CSS animations on hot paths (pan / zoom / scroll).
- **Respect `prefers-reduced-motion`.** When set, kill panel transforms and any non-essential transition.

---

## 6. Layout — mobile-first, three breakpoints

Default to three viewport bands, matched 1:1 with Tailwind defaults:

| Width band     | Name    | Tailwind prefix | Layout shape                                     |
| -------------- | ------- | --------------- | ------------------------------------------------ |
| `< 640px`      | Mobile  | (none)          | Single column, sticky toolbar, FAB, bottom sheets |
| `640–1023px`   | Tablet  | `sm:`, `md:`    | 2-up grids, full CTA labels, hamburger nav        |
| `≥ 1024px`     | Desktop | `lg:`           | 3-up / 4-up grids, inline nav, side panels        |

A fourth tier is rarely justified — desktop scales fine above 1280 if you cap content width (`max-width: 1280px; margin: 0 auto`).

**Don't use container queries unless an independent embedded component needs them.** Viewport media queries are simpler, work everywhere, and match how the rest of the layout reasons.

**Don't duplicate DOM trees for mobile / desktop.** A `<section class="hero-copy">` that's `display: none` on mobile is fine; rendering a separate mobile-only block is not.

---

## 7. Mobile patterns

- **Bottom sheets, not full-page overlays**, for detail panels and filters. A full overlay covers the primary surface and breaks the "tap a result → read → keep browsing" loop.
- **Carousels (scroll-snap), not stacked grids**, for KPI strips. Stacking pushes the primary surface below the fold.
- **Hide hero copy on mobile**, keep KPI / summary chips. The user already knows what they opened.
- **Bump input font-size to 16px on iOS** to suppress auto-zoom on focus.
- **Respect safe-area-inset.** Bottom-edge FABs, sheets, and bars use `bottom: max(1rem, env(safe-area-inset-bottom))` so they don't sit under the home indicator.
- **Sticky toolbars** so users can switch views from any scroll position; keep them slim (~52px).
- **Touch targets ≥ 44 × 44px.** Non-negotiable. Even for "small" admin actions.
- **The `<details>` primitive is preferred over JS accordions.** Native, keyboard-accessible, screen-reader-friendly; `open` toggle doesn't re-render the inner content.

---

## 8. Components

### 8.1 Buttons

| Variant   | Use                                  | Spec                                                 |
| --------- | ------------------------------------ | ---------------------------------------------------- |
| Primary   | The one CTA per view                 | Filled `--accent`, white text, rounded 8/12          |
| Secondary | Adjacent actions                     | Bordered, transparent bg, accent text                |
| Ghost     | Tertiary / inline                    | No border, no bg, accent text, hover bg `--surface-2` |
| Icon      | Toolbar (filters, theme, share)      | 32×32 (44×44 touch target), rounded 8, hover bg     |

Focus state is a 2px `--accent` outline with 2px offset via `:focus-visible` (not `:focus`) so mouse users don't see it on click.

### 8.2 Pills

A single base `.pill { padding: 1px 8px; border-radius: 999px; font-size: 10.5px; font-weight: 600 }` with semantic variants. Outline pills (`.pill.outline`) for "candidate" / "eligible" / "ready" signals; solid pills for status. Stack left-to-right as a readability ladder (program → status → readiness).

### 8.3 Cards

`background: var(--surface); border: 1px solid var(--border); border-radius: 12; padding: 16` is the safe default. Use shadow `0 1px 3px rgba(0,0,0,0.05)` at rest, `0 4px 12px rgba(accent, 0.15)` on hover.

### 8.4 KV grids (detail panels)

```html
<dl class="kv">
  <dt>Label</dt>
  <dd>Value <span class="dd-note">optional sub-line</span></dd>
</dl>
```

`grid-template-columns: 130px 1fr` on desktop, `110px 1fr` on mobile. Null values render as italic muted (`<dd class="muted-cell">Not available</dd>`), never blank.

### 8.5 Toasts

One at a time. Don't grow into a queue — if you need stacked toasts, swap in a real library. Lazy-mount a single `#toast` div, fade in via `.visible`, auto-fade after 4s.

---

## 9. Accessibility (baseline)

| Concern                | Implementation                                                                |
| ---------------------- | ----------------------------------------------------------------------------- |
| Skip to content        | `<a class="skip-link">` is the first focusable element; visually hidden until focused |
| Landmarks              | `<header role="banner">` · `<nav>` · `<main role="main">` · `<aside>` · `<footer role="contentinfo">` |
| Focus indicators       | `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }` on every interactive element |
| Live region for counts | `<span aria-live="polite">` so result counts and dynamic state changes announce |
| Filters as fieldsets   | `<fieldset><legend>` for grouped controls                                     |
| Tabs                   | `role="tablist"` / `role="tab"` / `role="tabpanel"` / `aria-controls` / `aria-selected` |
| Color contrast         | All text/bg pairs ≥ 4.5:1 in both light and dark themes (verify with audit tools) |
| Reduced motion         | `@media (prefers-reduced-motion: reduce)` kills non-essential transforms      |
| Touch                  | `touch-action: manipulation` on interactive elements                          |

---

## 10. Performance constraints on design

Design decisions that look like aesthetic calls but are actually performance calls:

| Choice                                      | Reason                                                      |
| ------------------------------------------- | ----------------------------------------------------------- |
| System fonts only (default)                 | Save a render-blocking RTT + ~50KB                          |
| No `backdrop-filter: blur` on map / overlay | Recomposites on every pan/zoom frame                        |
| No `filter:` on hot panes                   | Same                                                        |
| CSS-var theme + JS re-paint                 | Theme swap doesn't trigger a full re-style cascade          |
| Canvas markers, not SVG                     | SVG nodes melt mobile at 10k+                               |
| Pagination + IntersectionObserver           | DOM stays small; sentinel auto-appends only when needed     |
| Lazy-load non-default data layers           | First paint stays small                                     |
| `<link rel="preload">` for critical JSON    | Races the JSON behind defer-loaded JS                       |
| `priority: "low"` on enrichment fetches     | Browser deprioritizes behind first-paint resources          |
| `contain: layout paint` on heavy panels     | Bounds invalidation cost when content re-renders            |

If a proposal trades any of these for visual polish, it either (a) proves it works on a mid-range Android over throttled connection, or (b) gets explicit sign-off that the perf cost is acceptable.

---

## 11. Editorial / content rules

These apply to any project that surfaces data, claims, or content from external sources.

- **Cite primary sources.** Every numeric claim links to a primary or authoritative source. If a claim has no source, it doesn't ship.
- **Surface "why".** Boolean badges ("Eligible", "Candidate", "Verified") carry their qualifying criteria inline as italic sub-lines or tooltips — the badge alone is opaque.
- **Title-case CAPS source data on the way in.** Many federal / government / scraped feeds ship ALL CAPS or placeholder sentinels (`-- Not Defined --`, `_NULL_`). Run a `prettyPlace()` / `prettyName()` at ingest; preserve raw on `*_raw` for debugging. Maintain an acronym whitelist for words that should stay uppercase (NASA, NPS, USA, …).
- **"Not available", not blank.** Optional fields render as italic muted placeholders — never an empty cell.
- **"Adjacent", not "0.0 mi".** Pre-rounding bin edges are signals, not nulls. Render `n < threshold` as a meaningful word, not a misleading number.
- **No emojis by default.** Outline pills do the badge work. If the project's voice calls for emoji (consumer apps, social features), use sparingly and document in the project `design.md`.
- **Lowercase for prose, uppercase for labels.** Eyebrows, KPI labels, table heads, and outline-pill text are uppercase with `0.04–0.14em` letter-spacing. Everything else is sentence case.
- **AI-generated content is visibly distinguished.** A 3px accent left-border on the card plus a meta line crediting the model. The reader should never wonder whether they're looking at primary data or generated narrative.

---

## 12. Common pitfalls (the "scar tissue" list)

These are encoded across this folder's projects. If you're tempted to undo them, read the rationale.

### 12.1 The `[hidden]` trap

`display: inline-flex | block | flex` on an element that uses the `hidden` HTML attribute silently overrides the implicit `display: none`. The element renders despite `hidden` being set.

**Always** ship a `[hidden] { display: none }` rule alongside any `display: ...` override. If the element animates out (e.g. slide), use `visibility: hidden` + `transform` on `[hidden]` instead.

### 12.2 `text-overflow: ellipsis` no-ops on `display: inline`

A `<span>` defaults to `display: inline`; `overflow: hidden` + `text-overflow: ellipsis` silently does nothing. Always set `display: block | inline-block | flex | grid` on the element you're ellipsizing. Pair with `min-width: 0` on the parent grid item.

### 12.3 IntersectionObserver callbacks need a scroll-position guard

`isIntersecting === true` is necessary but not sufficient for "user scrolled near the bottom." During tab swaps and in headless contexts, layout can settle in multiple paint passes, firing the observer several times — each firing prefetches another page. Add an explicit `scrollHeight - scrollTop - clientHeight > 400` check to bail when the user hasn't actually scrolled.

### 12.4 Anything that enumerates a fixed list MUST iterate the source-of-truth array

Reset buttons, dropdown populators, persona buttons — anything that touches "all programs / themes / tiers / categories" must iterate from the canonical constant array, never a hardcoded subset. When the list grows, the iterating code picks up the change for free; the hardcoded subset silently drops the new entries.

### 12.5 No web fonts without sign-off (see § 2)

### 12.6 No CSS `filter` on hot paths (see § 10)

### 12.7 Pills do NOT fall back across columns

When a row has a "Program" column and a "Status" column, the Status cell must render Status-specific content (or `—`), never the Program pill as a fallback. Two identical pills doubles visual noise without adding signal.

---

## 13. What's intentionally NOT in design

Decisions made by *omission*:

- **No icon libraries by default.** System emoji + outline pills cover most needs. Add Lucide / Heroicons only when a project genuinely needs ~30+ distinct glyphs.
- **No animation libraries.** CSS transitions and Tailwind's `animate-pulse` cover what we need without the bundle weight.
- **No marker clustering on maps** (when canvas markers + decimation handle the load). `leaflet.markercluster` is the right answer when interactive behavior demands grouping.
- **No multi-toast queue** (until a project actually needs it).
- **No infinite zoom / unconstrained pan.** Set `maxBounds`. Most projects have a meaningful viewport (US-only, city-only); enforce it.
- **No backend until profit / scale demands one.** Static-first is the default. JSON in `docs/`, GitHub Pages, no runtime. (levels.io: "you don't need a backend.")

---

## 14. When to revisit this document

- A new component pattern emerges across 2+ projects (promote from project `design.md` to here).
- A bullet in § 12 (pitfalls) repeats in a third project — that means it needs more emphasis or a different remedy.
- A new accessibility standard lands (WCAG update, platform-level mandate).
- A perf budget regresses across the portfolio (e.g. mid-range Android performance audit).

---

## Influences

- **FT, Bloomberg Businessweek, ProPublica, Greater Greater Washington** — editorial gravitas through typography and restraint, not dependencies.
- **Linear** — typography discipline, dark UI without losing readability.
- **Apple Human Interface Guidelines** — touch targets, safe areas, mobile-first ergonomics.
- **Pieter Levels (levels.io)** — "you don't need a backend, you don't need a CSS framework, you don't need a font, you don't need npm." When in doubt, ship the simpler thing.
- **Andrej Karpathy** — performance budgets are real constraints, not afterthoughts; measure before optimizing; the smallest version that works is the right starting point.
