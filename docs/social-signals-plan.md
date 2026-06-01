# Social Signals Expansion Plan

Covers the next three signal sources after Twitter/X. Each section: what data
we get, how to fetch it, what the implementation looks like, and what to watch
out for. All sources store canonical links in full — no truncation.

---

## 1. Bluesky (AT Protocol)

**Why:** Public search API, no auth, no paid keys. Bluesky is growing fast in
WNBA-adjacent sports discourse. Best ROI of the three sources.

**Endpoint:**
```
GET https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts
  ?q=<query>
  &limit=25
  &sort=latest
```
No authentication. ~100 req/min is safe per unofficial reports.

**Response shape:**
```json
{
  "posts": [
    {
      "uri": "at://did:plc:xxx/app.bsky.feed.post/yyy",
      "author": { "handle": "user.bsky.social", "displayName": "Name" },
      "record": { "text": "...", "createdAt": "2026-06-01T19:00:00.000Z" },
      "likeCount": 3, "replyCount": 1, "repostCount": 0
    }
  ]
}
```

**Canonical URL construction:**
```python
# AT URI: at://did:plc:xxx/app.bsky.feed.post/RKEY
rkey = uri.split("/")[-1]
handle = post["author"]["handle"]
url = f"https://bsky.app/profile/{handle}/post/{rkey}"
```

**Implementation sketch:**

`pipeline/bluesky.py` — mirrors `twitter.py` structure:
- `fetch_bluesky(query, limit=25)` — one HTTP GET, no auth, returns normalized dicts
- `match_to_players(posts, player_name_map)` — same logic as Reddit/Twitter
- Per-player queries: call once per top-15 target (`"{name}" wnba`)

`pipeline/schema.py`:
```python
class BlueskyPost(_Strict):
    title: str
    url: str
    published_at: datetime | None = None
    handle: str = ""
    like_count: int = 0
```

`LeagueState` gains `bluesky_posts_by_player: dict[int, list[BlueskyPost]]`.

**`pipeline/refresh.py`** — add after Reddit/Twitter:
```python
from pipeline import bluesky as bluesky_mod
bluesky_posts = bluesky_mod.fetch_all_players(player_names, limit=25)
```

**Frontend:** new `player-modal-bluesky` section in modal, same CSS pattern.

**No secrets needed.** Bluesky's public API returns public posts only.

**Edge cases:**
- API rate limit → log + return `[]`, don't crash
- `uri` malformed → skip that post
- `handle` contains `.` characters — that's fine in a URL path

---

## 2. Threads (Meta)

**Why:** Large WNBA community, especially beat writers and player accounts.

**Reality check:** Threads has no public search endpoint. Options ranked by effort:

| Option | Auth needed | Effort | Notes |
|---|---|---|---|
| Chrome scraping via skill | Instagram login in browser | Low — same pattern as X | Best fit |
| Meta Threads Basic Display API | Instagram app + review | Medium | Approved apps only |
| threads.net RSS | N/A | Does not exist | — |

**Recommended approach: Chrome scraping (same as X).**

Add to SKILL.md step 4 after the X scraping block:

```
Navigate to https://www.threads.net/search?serp_type=default&q="PLAYER+NAME"+wnba
```

The Threads search page renders in the browser if the user is logged into Instagram.
Extract with a similar `article` / post-container querySelector approach.

**DOM selectors (as of 2026):**
- Post text: `div[data-pressable-container] span` (fragile — verify on first run)
- Timestamp: `time` element (same as X)
- URL: post links follow `https://www.threads.net/@{handle}/post/{id}`

**Fallback:** If not logged in, write `[]` to `threads_raw.json` and skip.

`pipeline/threads.py` — same structure as `twitter.py`:
- `load_threads(raw_dir)` — reads `threads_raw.json`, falls back to `[]`
- `match_to_players(posts, player_name_map)`

`LeagueState` gains `threads_posts_by_player: dict[int, list[ThreadsPost]]`.

**Schema:**
```python
class ThreadsPost(_Strict):
    title: str
    url: str
    published_at: datetime | None = None
    handle: str = ""
```

---

## 3. Reddit (expansion)

**Current state:** Fetches `r/wnba` RSS (all posts, no auth), matches by player name.
Works. The gap: misses `r/fantasywbasketball`, which has the most waiver-wire signal.

**Plan A — add r/fantasywbasketball RSS (easy, same code pattern):**

The public Atom RSS still works without auth:
```
https://www.reddit.com/r/fantasywbasketball/new/.rss?limit=50
```

Update `pipeline/reddit.py`:
```python
SUBREDDITS = [
    "https://www.reddit.com/r/wnba/new/.rss?limit=50",
    "https://www.reddit.com/r/fantasywbasketball/new/.rss?limit=50",
]

def fetch_reddit(limit=50):
    posts = []
    for url in SUBREDDITS:
        posts.extend(_fetch_one(url, limit))
    return posts
```

Each post in the normalized dict gets a `subreddit` field (`"wnba"` or
`"fantasywbasketball"`) so the frontend can show the source.

**Schema update:**
```python
class RedditPost(_Strict):
    title: str
    url: str
    published_at: datetime | None = None
    subreddit: str = "wnba"   # new field, defaults for backward compat
```

**Plan B — per-player Reddit search (harder, needs OAuth):**
Reddit's JSON search endpoint (`/r/wnba/search.json?q={name}`) now requires OAuth
since 2023. Not worth implementing until we know the signal quality is worth the
credential management overhead. Defer to backlog.

**Frontend change:** Show subreddit badge on each post (`r/wnba` vs `r/fantasywbasketball`).

---

## Roll-out order

1. **Bluesky** — implement first. Zero auth, cleanest API, best ROI. Can go in the
   same PR as the Twitter per-player changes.
2. **Reddit r/fantasywbasketball** — one-line URL addition, ship alongside Bluesky.
3. **Threads** — add to SKILL.md Chrome step after Bluesky is live and stable.
   Depends on user having Instagram session in Chrome.

---

## Shared principles for all sources

- **Preserve canonical URLs in full.** Store `url` as returned by the source.
  For Chrome-scraped sources, construct from path components (screen_name + post ID)
  rather than `element.href` to avoid tracking query params — but the result is still
  the real permanent link.
- **Per-player queries for top 15 targets.** Generic source queries miss too much.
  Do a targeted `"{player name}" wnba` search per player; merge + deduplicate.
- **Graceful degradation.** Every source returns `[]` on failure. One bad source
  never blocks the pipeline.
- **Cap at 5 posts per player per source in state.json.** Raw files keep everything;
  state.json is the UI read model and shouldn't grow unbounded.
- **`published_at` always UTC ISO-8601.** Sources use different formats; normalize
  in the source module before returning.
