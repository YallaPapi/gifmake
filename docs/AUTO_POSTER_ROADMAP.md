# Auto Poster Pipeline - Roadmap

## Overview

End-to-end pipeline: content folder → AI analysis → subreddit matching → automated Reddit posting via AdsPower browser profiles. Built into the GifMake GUI as the "Auto Poster" tab.

## Data Foundation (COMPLETE)

| Asset | Count | File |
|-------|-------|------|
| Subreddits scraped | 11,650 | `all_subs_merged.json` |
| Tier evaluated (Grok) | 11,648 | `subreddit_tiers_grok.json` |
| GREEN (postable) | 7,437 | filtered from tiers |
| Profiled (rich tags) | 7,428 | `subreddit_profiles.json` |
| Video-preferred subs | 744 | filtered from profiles |
| Photo-preferred subs | 4,741 | filtered from profiles |
| Local/hookup (skip) | ~2,072 | tagged in profiles |
| Content-postable subs | ~5,356 | GREEN minus local/hookup |

### Profile Quality

Each profiled sub has: `tags[]`, `body_type`, `ethnicity`, `setting`, `clothing`, `action`, `theme`, `format_preference`. Grok `grok-4-1-fast-reasoning` produced specific, accurate profiles with 0 failures.

**Known issue**: ~2,072 local/hookup/personals subs are GREEN (no posting restrictions) but useless for content posting. These need to be filtered out during matching — the profiles already identify them via tags like `local`, `hookup`, `meetup`.

---

## Phase 1: Content Analysis & Matching Engine (CURRENT)

**Status**: Built, needs refinement

### What exists
- `src/core/vision_matcher.py` — Claude Vision analyzes images/videos, returns content tags
- `src/gui/auto_poster_tab.py` — GUI tab for browsing folders, running analysis, viewing matches
- Scoring engine matches vision output to sub profiles (tag overlap, body type, ethnicity, theme)

### What needs work

1. **Filter out local/hookup subs** — Add a blacklist filter during matching to exclude subs tagged with `local`, `hookup`, `meetup`, `personals`, `bate`, `scat`, `piss` and other non-content niches
2. **Random subreddit selection** — When 100 subs match a "boobs" video, don't always pick the top 100 by score. Instead:
   - Score all matching subs
   - Group by score tier (high/medium/low match)
   - Randomly select N subs from each tier, weighted toward higher tiers
   - Never post the same content to the same sub twice (track history)
3. **Manual vs Automatic mode**:
   - **Manual**: Worker reviews matched subs, can add/remove subs, adjust titles, then confirms
   - **Automatic**: System picks random subs from matches, generates titles, posts without review
4. **Title generation** — Use Grok to generate catchy, varied titles based on content tags and target sub. Avoid repetitive titles.

### Deliverables
- [ ] Filter local/hookup/niche subs from matching
- [ ] Random sub selection with weighted tiers
- [ ] Manual review UI (checkboxes, edit titles)
- [ ] Automatic mode toggle
- [ ] Title generation via Grok
- [ ] Post history tracking (what was posted where)

---

## Phase 2: Posting Engine Integration

**Status**: Existing code needs to be wired into GUI

### What exists
- `src/uploaders/reddit/reddit_poster_playwright.py` — Full Playwright-based poster
  - `AdsPowerClient` — starts/stops browser profiles via API
  - `post_link_to_subreddit()` — navigates to submit page, fills fields, posts
  - `batch_post_from_csv()` — batch posting with delay between posts
  - `main_batch()` — multi-profile batch posting
- AdsPower config at `src/uploaders/redgifs/adspower_config.json`

### AdsPower Capabilities (CONFIRMED)

| Capability | Supported | Notes |
|------------|-----------|-------|
| Start profile via API | YES | `GET /api/v1/browser/start?user_id={id}` |
| Stop profile via API | YES | `GET /api/v1/browser/stop?user_id={id}` |
| Multiple profiles at once | YES | Limited by system resources |
| Sequential profile switching | YES | Close A, open B — already in code |
| Check if profile running | YES | `GET /api/v1/browser/active?user_id={id}` |
| Rate limits | 2 req/sec | For <200 profiles |

### What needs to be built

1. **AdsPower profile selector in GUI** — Dropdown populated from config, "Connect" button
2. **Session verification** — After connecting, navigate to `reddit.com/user/me` to confirm logged in and not banned
3. **Direct posting from GUI** — Instead of exporting CSV then running script separately, post directly from the Auto Poster tab
4. **Per-post status tracking** — Live UI showing each post: sub name, status (queuing/posting/success/failed/banned)
5. **Ban detection**:
   - **Sub ban**: After posting, check for "you've been banned" text on page
   - **Account suspension**: Check `reddit.com/user/me` before each session
   - **Shadow ban**: After posting, check if post appears on sub's /new in a logged-out context (stretch goal)
6. **Stop button** — Gracefully halt mid-run

### Post flow per item
```
1. Connect to AdsPower profile (if not already connected)
2. Verify Reddit session is active
3. For each content piece:
   a. Get matched subs (random selection from pool)
   b. Pick first available sub not yet used for this content
   c. Generate title (or use manual title)
   d. Navigate to r/{sub}/submit?type=link
   e. Fill title, paste RedGIFs URL, mark NSFW, select flair if needed
   f. Submit
   g. Verify success (/comments/ in URL)
   h. Log result
   i. Wait random delay before next post
```

### Deliverables
- [ ] AdsPower profile dropdown in GUI
- [ ] Connect/disconnect buttons
- [ ] Session health check (logged in? banned?)
- [ ] Direct posting integration (no CSV export needed)
- [ ] Live progress display
- [ ] Ban detection (sub ban + account suspension)
- [ ] Stop button
- [ ] Results CSV auto-saved

---

## Phase 3: Humanization

**Status**: Not started

### Goal
Make automated posting indistinguishable from a human user browsing Reddit and posting.

### Techniques

1. **Random delays** — `random.uniform(45, 120)` seconds between posts instead of fixed 60s. Occasionally take a longer break (3-5 min) after every 5-8 posts.

2. **Session warming** — When first connecting a profile:
   - Visit Reddit home feed
   - Scroll for 5-15 seconds
   - Maybe click one post, view it briefly
   - Then start posting

3. **Pre-post browsing** — Before each submission:
   - Browse the target subreddit briefly (3-8 seconds)
   - Scroll down the feed
   - Then navigate to submit page

4. **Typing simulation** — Replace `page.fill()` with `page.type()` using random char delays (50-150ms per keystroke). More human-like input events.

5. **Mouse movement** — Small random mouse movements before clicking buttons. Playwright `page.mouse.move()` with slight jitter.

6. **Post cadence variation** — Don't post at a constant rate. Vary between rapid sessions (3-5 posts in 15 min) and slow sessions (1 post every 10 min). Real humans are bursty.

7. **Daily limits** — Cap new accounts at 5-10 posts per day. Increase gradually as account ages.

### Deliverables
- [ ] Random delay engine with configurable ranges
- [ ] Session warming routine
- [ ] Pre-post browsing routine
- [ ] Typing simulation mode
- [ ] Mouse jitter
- [ ] Daily post limit per account
- [ ] Cadence variation (bursty vs slow modes)

---

## Phase 4: Scheduling & Account Rotation

**Status**: Not started

### Goal
Automated scheduling that rotates between multiple Reddit accounts/AdsPower profiles, distributes posts across time, and manages account health.

### Design

1. **Profile rotation** — Run profile A for N posts, close it, open profile B for N posts. Spread across the day.

2. **Queue system** — Content enters a posting queue. Scheduler pulls from queue, picks a profile, posts, moves to next.

3. **Account health tracking** — Per-account database:
   - Posts today / this week
   - Last post time
   - Banned subs list
   - Account status (active / rate-limited / suspended)
   - Karma estimate

4. **Time distribution** — Don't post everything at 3am. Spread posts across active Reddit hours (roughly 8am-11pm EST). Use randomized scheduling windows.

5. **Content deduplication** — Never post the same RedGIFs URL to more than one subreddit. Track all posted URLs globally.

6. **Existing scheduler** — `src/scheduler/scheduler.py` already has SQLite-based queue infrastructure. Extend rather than rebuild.

### Deliverables
- [ ] Profile rotation engine
- [ ] Posting queue (SQLite-based)
- [ ] Account health dashboard
- [ ] Time distribution algorithm
- [ ] Global URL deduplication
- [ ] Scheduler integration in GUI

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     AUTO POSTER TAB (GUI)                    │
├─────────────┬─────────────┬────────────────┬────────────────┤
│ Content     │ Analysis    │ Sub Matching   │ Posting        │
│ Browser     │ Engine      │ Engine         │ Engine         │
│             │             │                │                │
│ Drop folder │ Claude      │ Score & rank   │ AdsPower       │
│ of videos/  │ Vision API  │ all GREEN subs │ Playwright     │
│ images      │ analyzes    │ vs content     │ posts to       │
│             │ each piece  │ tags           │ Reddit         │
│             │             │                │                │
│             │ Returns:    │ Random select  │ Humanized      │
│             │ tags, body, │ from top       │ delays, browse │
│             │ ethnicity,  │ matches        │ ban detection  │
│             │ action etc  │                │                │
└─────────────┴──────┬──────┴───────┬────────┴───────┬────────┘
                     │              │                │
              ┌──────▼──────┐ ┌────▼─────┐  ┌──────▼───────┐
              │ Claude API  │ │ Profiles │  │ AdsPower API │
              │ (Vision)    │ │ 7,428    │  │ Start/Stop   │
              │ sonnet-4-5  │ │ subs     │  │ Profiles     │
              └─────────────┘ └──────────┘  └──────────────┘
```

## Random Sub Selection Algorithm

```
Input: content_tags from Claude Vision
Output: list of N random subreddits to post to

1. Score ALL 5,356 content-postable GREEN subs against content_tags
2. Filter out:
   - Local/hookup/personals subs
   - Subs where this content was already posted
   - Subs where this account is banned
3. Split remaining into tiers:
   - Tier A (score > 70): Best matches
   - Tier B (score 40-70): Good matches
   - Tier C (score 20-40): Decent matches
4. Randomly select:
   - 60% of picks from Tier A
   - 30% from Tier B
   - 10% from Tier C
5. Shuffle final list (no predictable order)

Result: Every posting session uses different subs, even for similar content.
         No sub gets hit repeatedly. Fresh and random.
```

## Key Files

| File | Purpose |
|------|---------|
| `src/core/vision_matcher.py` | Claude Vision analysis + sub matching |
| `src/gui/auto_poster_tab.py` | Auto Poster GUI tab |
| `src/gui/main_window.py` | Main window with tabview |
| `src/uploaders/reddit/reddit_poster_playwright.py` | Playwright posting engine |
| `src/uploaders/redgifs/adspower_config.json` | AdsPower profile config |
| `src/scheduler/scheduler.py` | SQLite queue (for Phase 4) |
| `subreddit_profiles.json` | 7,428 rich sub profiles |
| `subreddit_tiers_grok.json` | 11,648 tier evaluations |
| `all_subs_merged.json` | 11,650 merged sub data |
| `scripts/tier_all.py` | Tier evaluation script |
| `scripts/profile_subs.py` | Sub profiling script |

## Timeline

| Phase | Focus | Status |
|-------|-------|--------|
| Data Foundation | Scrape, tier, profile all subs | COMPLETE |
| Phase 1 | Content analysis + matching + random selection | IN PROGRESS |
| Phase 2 | Posting engine integration in GUI | NOT STARTED |
| Phase 3 | Humanization | NOT STARTED |
| Phase 4 | Scheduling + account rotation | NOT STARTED |
