# Backlog

Living list of ideas, features, and enhancements. Each item: brief description + priority.

## High

- *(empty for now — populated as the product grows)*

## Medium

- **Short-term vs. long-term need lens.** Today's "Top need" is a one-week snapshot — a team with two Guards on byes could read as a structural Guard need when it's actually a one-week dip. Add a toggle on the Team Needs tab: **This week** (current behavior, schedule-weighted) vs **Rest of season** (per-game projection × remaining games, no game-count weighting). Same math, different denominator. The two will disagree often enough to be informative — a team showing a *persistent* Frontcourt need across both is a real hole; a team that's only weak this week is just streaming.
- **Streamer vs. anchor pickup distinction.** Tag every waiver-target card with one of two intents: `streamer` (high projected this week but low season per-game — useful for a one-week plug, drop after) vs `anchor` (high per-game baseline regardless of schedule — keep). The Waiver Targets list could split into two sub-sections; the per-player modal could show "Hold value: high / medium / low" derived from per-game baseline and ownership trend. Prevents the "I picked them up for the games and now my bench is full" loop.
- **Need persistence indicator on team cards.** Show a small streak chip on each team card: "G-need: 3 wks" or "FC-need: 1 wk." Pulls from the daily snapshots in `data/raw/` to detect which top needs are recurring vs transient. A 3+ week need signals it's a real structural hole worth trading for, not a passing slump.
- **Lookback sparkline on team cards.** Tiny inline sparkline (8 weekly points) under each bucket bar showing gap-vs-league over the last 8 weeks. Cheap visual: lets the user see a need *trending up* (getting worse) vs *resolving* (improving). Drives the "drop / hold / acquire" decision better than a single snapshot.
- **Matchup-aware top need.** "Top need" today ignores who the team is playing this week. A team strong at G facing an opponent stacked at G should see Guard-quality picks promoted *for that week* even if the bucket math says they're fine season-wide. Surfaces in a per-matchup card on the Team Needs tab — H2H positional gap vs the assigned opponent, refreshed each matchup period.
- **Full per-player game log inside the player modal.** Modal currently shows projections + this week. Add a 10-game rolling table (date, opponent, pts, min, key stats) once we have a source. ESPN's player splits endpoint is the obvious target; basketball-reference.com is the public fallback.
- **Cumulative cross-team transaction history per player.** Player modal currently only shows the recent (last 50) transaction window. Ship `data/history/transactions.jsonl` to `docs/data/` (or a slimmed version) so the modal can show every move involving the player across the whole season.
- **Player ownership trend in the modal.** Sparkline of `percent_owned` over the last 14 days, sourced from the daily raw snapshots. Fast-rising % owned is a leading indicator of "claim them now."
- **Projection sources beyond ESPN.** Pull projections from wnba.com, Yahoo Sports, and basketball-reference.com to triangulate ESPN's numbers. Reduces single-source bias on waiver-target ranking.
- **Twitter / X signal layer.** Surface beat-writer + player tweets earlier than ESPN's own status updates — backup-PG promotions, minute redistributions on injuries, late scratches. Requires X API keys (no scraping). Should feed the waiver-ranker as a bonus tier.
- **Reddit r/wnba signal.** Public JSON endpoint (`reddit.com/r/wnba/new.json`) is no-auth. Surface posts flaired "Injury" / "News" / "Game Thread" and match to players by name. Lower precedence than ESPN's official feed.
- **My team mode.** Lets the user designate "their" team (via team ID) and gets a daily personalized brief: top need this week, who to drop, who to bid, projected matchup margin.
- **Trade analyzer.** Given two rosters, project the weekly H2H point swing if they traded specific players.
- **Matchup preview cards.** For the upcoming week, show each opposing team's projected output vs. ours by position.

## Done

- ~~**Player-specific pop-outs / pages.**~~ Shipped: every player name in waivers, rosters, team targets, transactions, and news tags opens a centered modal with photo, position + pro team, projection per game + total this week, rostered-by status, news tagged to their `athleteId`, recent transactions involving the player, and a deep-link to their ESPN page. Foundation for the per-player game-log + cross-team history items in **Medium**.

## Low

- **Resolve transaction player names by ID.** Right now `transaction_items.player_name` is filled only for players currently on a roster or in the FA top-N. When a player was dropped + already picked up by a third team, their name might miss the index and the UI renders `#playerId`. Fix: pull `kona_playercard` for any unresolved IDs at build_state time, or maintain a cumulative `data/history/players.json` index.
- **Historical drift charts.** Plot each team's win probability / power ranking over time using the raw daily snapshots in `data/raw/`.
- **Player ownership trends.** Track free-agent percentage-owned changes day-over-day; flag fastest risers.
- **Discord/Slack/email digest.** Push the daily brief to a channel instead of waiting for the user to load the page.
- **PWA install + offline cache.** Make the static page installable; show last successful snapshot when offline.
- **Light/dark theme parity audit.** Run a contrast check across both themes once frontend has more components.
- **Move the site to its own domain** if/when GitHub Pages URL becomes a friction point.
