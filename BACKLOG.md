# Backlog

Living list of ideas, features, and enhancements. Each item: brief description + priority.

## High

- *(empty for now — populated as the product grows)*

## Medium

- **Projection sources beyond ESPN.** Pull projections from wnba.com, Yahoo Sports, and basketball-reference.com to triangulate ESPN's numbers. Reduces single-source bias on waiver-target ranking.
- **News + social signal layer.** Surface injury reports and credible Twitter/X signal earlier than ESPN's own status updates — backup PG promotions, minute redistributions on injuries, etc. Affects waiver target ranking.
- **My team mode.** Lets the user designate "their" team (via team ID) and gets a daily personalized brief: weak slots this week, who to drop, who to bid, projected matchup margin.
- **Trade analyzer.** Given two rosters, project the weekly H2H point swing if they traded specific players.
- **Matchup preview cards.** For the upcoming week, show each opposing team's projected output vs. ours by position.

## Low

- **Resolve transaction player names by ID.** Right now `transaction_items.player_name` is filled only for players currently on a roster or in the FA top-N. When a player was dropped + already picked up by a third team, their name might miss the index and the UI renders `#playerId`. Fix: pull `kona_playercard` for any unresolved IDs at build_state time, or maintain a cumulative `data/history/players.json` index.
- **Historical drift charts.** Plot each team's win probability / power ranking over time using the raw daily snapshots in `data/raw/`.
- **Player ownership trends.** Track free-agent percentage-owned changes day-over-day; flag fastest risers.
- **Discord/Slack/email digest.** Push the daily brief to a channel instead of waiting for the user to load the page.
- **PWA install + offline cache.** Make the static page installable; show last successful snapshot when offline.
- **Light/dark theme parity audit.** Run a contrast check across both themes once frontend has more components.
- **Move the site to its own domain** if/when GitHub Pages URL becomes a friction point.
