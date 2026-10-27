# NFL and NBA lineup planning

Extend FantasyGM to the user's two Sleeper NFL leagues and ESPN NBA league while preserving the existing WNBA dashboard. Run refreshes and injury checks manually. Use only players already rostered; acquisitions and trades are separate decisions.

## Verified league rules

Read from the league APIs on 2026-10-26 Eastern time. Refresh these settings on every run rather than treating this table as configuration.

| League | Scoring | Starting slots | Important differences |
| --- | --- | --- | --- |
| Sleeper 1389723841625849857 | Half PPR; 4 points per passing TD; minus 2 per interception | QB, 2 RB, 2 WR, TE, 2 FLEX, K, DEF | Fractional kicker distance above 30 yards; defense yardage scoring; two IR slots; bench lock enabled |
| Sleeper 1389752644595089408 | Half PPR; 4 points per passing TD; minus 1 per interception | QB, 2 RB, 2 WR, TE, FLEX, K, DEF | Different kicker distance bands and defense scoring; long-play bonuses; one IR slot; bench lock disabled |
| ESPN 221146430, team 2, season 2027 | H2H category: FT%, REB, PTS, BLK, 3PM, STL, TO (lower wins), AST, FG% | PG, SG, SF, PF, C, G, F, 3 UTIL | Individual-game lineup locks; first-game roster transaction lock; 5 bench and 2 IR slots; one acquisition per scoring period |

NFL starters generally lock at kickoff, so Thursday, Sunday, and Monday decisions remain separate. These Sleeper leagues also enable up to three AutoSubs with different eligibility rules. AutoSubs are mobile-only to edit according to [Sleeper support](https://support.sleeper.com/en/articles/9731991-how-does-player-autosubs-work); a browser executor must preserve them and stop if their locks affect a proposed move.

NBA counts several games per player. Daily eligibility and slot congestion determine how many can actually score. Weekly roster transaction locks must not be confused with daily lineup locks. The user explicitly chose total fantasy points for every sport, overriding an earlier choice of category wins. Because this NBA league has no points total, use [ESPN's standard fantasy points formula](https://africa.espn.com/fantasy/basketball/story/_/id/30296896/espn-fantasy-default-points-league-scoring-explained) as a labeled comparison metric: PTS + 3PM + 2 FGM − FGA + FTM − FTA + REB + 2 AST + 4 STL + 4 BLK − 2 TO. This objective does not measure category wins.

## Implementation choices

1. Browser-only collection and lineup changes: immediate access to the site's exact controls, but slow and dependent on browser layout.
2. Extend all WNBA models and presentation at once: one dashboard, but cross-cutting schema and position changes risk the existing workflow.
3. Separate league adapters and a shared exact slot assignment solver: preserves the WNBA output, supports a CLI first, and keeps league rules explicit. This is the recommended approach for the requested expansion.

## Data migration

### Privacy boundary

The user requires fantasy league/team names and participant identities to stay out of saved artifacts. UI-only masking would leave names in JSON; a write-time denylist alone could miss new provider fields. Use ID-based labels plus allowlisted multi-league rules, sanitize ESPN fantasy-team identity at ingest, and repeat sanitization when rebuilding legacy snapshots. Public professional athlete names and pro-team abbreviations remain usable for lineup decisions. Keep the current schema fields for compatibility, populating them with `League <id>`, `Team <id>` and `T<id>` rather than private names or logos.

Migrate local session reports and raw discovery files, existing league snapshots, and the current built dashboard. Rebuild the dashboard from sanitized inputs. Replace the unpublished session commits so their private names do not enter reachable branch history. Existing remote repository history is a separate publication surface; do not claim it was erased or rewrite it without a dedicated coordinated migration.

Keep `docs/data/state.json` and existing WNBA schema unchanged. Store new league refreshes and generated plans under gitignored `scratch/lineups/` until a private or intentionally published multi-league view is selected. Never store owner names or identifiers in snapshots. Sleeper roster identity resolves in memory from a supplied username; persist roster ID only. ESPN cookies remain in `.env` and are sent only to ESPN.

Integration with the separately published NFL dashboard preserves its existing requested league set. NFL cache writes use selected numeric rules and neutral league labels; portable imports normalize league/team labels before storage. WNBA dry-run logs use league IDs. Live refresh and replay both merge the persistent transaction archive, including when the current ESPN feed is empty. Historical Yahoo imports retain their original capture date until explicitly refreshed from a verified source.

Add game-code selection to the existing ESPN reader with `wfba` as the default, so existing callers retain their behavior. NBA uses `fba` and season 2027 for 2026–27. Read NBA eligible slots from player data and counts from settings; never reuse WNBA position IDs.

## Optimization and injury checks

NFL projections must be scored from the projected stat vector using that league's complete nonzero scoring weights, rather than using generic half-PPR totals. Missing projected stats must surface as coverage gaps. Preserve locked starters and exclude locked bench players. Use exact assignment across all slots so a flexible player cannot steal the only spot available to a specialist.

NBA refreshes expose per-player schedules and ESPN season projections converted to per-game rates. Solve each date independently for maximum projected standard fantasy points; summing those daily plans maximizes the chosen date window when there are no game caps or constraints across dates. Daily plans are tentative until the browser verifies live locks. Keep players on IR excluded until an explicit activation decision is made. Injury tags are a capture-time snapshot: refresh before each execution rather than assuming the whole week stays healthy.

Each manual run saves a timestamped report with current injuries, uncertain players, locks, projection coverage, eligible slots, rules, and changes since the previous capture. The daily entry point runs once and exits; no scheduler, launch agent, cron job, or recurring Codex automation is installed.

## Computer use execution

Generate a task from fresh data. In an authenticated browser, verify league ID, team or roster ID, scoring date, ownership, injuries, current slots, and locks before touching controls. Apply only roster-to-roster lineup moves. Re-read after each move because autosave can change the DOM and invalidate later steps. Replan if any precondition changed; stop on unknown controls, illegal roster states, AutoSub conflicts, or authentication prompts. Verify persisted assignments after reload and capture the result.

The executor needs a signed-in page to establish actual controls. A generated task is preparation, not proof that a lineup was changed. A future standalone computer-use runner would need its own model/runtime credentials; the existing Codex browser tools can execute the task without adding a new dependency or exporting session cookies.

### Verified ESPN browser workflow

Verified and executed on 2026-10-26: **Quick Lineup → Rest of Matchup → Confirm** fills each daily lineup through Sunday while respecting started games. The radio input is visually hidden; click the visible “Rest of Matchup” label, then verify that the radio with value `matchup` is checked before confirming. Wait for the “Setting Lineups” dialog to disappear.

ESPN uses player ratings for category leagues, so this is only a starting point for the user's points objective. Read the first roster table on every date and compare its playing starters with the generated daily plan. On Wednesday October 28, the helper started Ace Bailey and benched RJ Barrett. Corrected it with **Select RJ Barrett to move → Confirm move of Ace Bailey to Util**, then reloaded and verified Barrett at UTIL and Bailey on the bench. The standard-points projection difference was about 5.8 points. All six upcoming dates were checked; IR players remained on IR.

The calendar carousel renders offscreen dates in the accessibility tree. Clicking an offscreen date can leave the current date unchanged. Use its Next/Previous buttons to bring the target panel into view, then wait for the roster table's date heading before reading or modifying it. Date labels alone do not prove navigation succeeded.

## Verification

Test exact assignment against flexible-slot counterexamples, locked starters and bench players, missing projections, league-specific scoring, injury changes, owner redaction, and NBA category guards. Run the full suite, then run each adapter against the real league. Browser lineup execution is complete only after the persisted lineup is visibly verified.

## Manual commands

From the project root:

```bash
.venv/bin/python -m pipeline.multileague --sleeper-user USERNAME
.venv/bin/python -m pipeline.multileague --week 8 --start-date 2026-10-27 --days 6
```

The first command discovers the account's active NFL leagues, checks injuries and the current NFL week, and plans the next seven calendar dates for NBA. Once resolved, local captures retain roster IDs so subsequent runs can omit the username. The second command plans the upcoming NFL week and the remaining Tuesday–Sunday NBA window for this capture. Outputs are `scratch/lineups/report.json`, a timestamped capture, and `scratch/lineups/computer-use-task.md`. Invoke the generated task in Codex to perform browser verification and lineup execution. No standalone browser executor is claimed until signed-in controls have been verified.
