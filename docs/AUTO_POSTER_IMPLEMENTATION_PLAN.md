# Auto Poster — Implementation Plan

## Architecture

### Folder = Profile = Creator

```
content/
├── marina/              → AdsPower Profile A (proxy A)
│   ├── clip1.mp4
│   ├── clip2.mp4
│   └── img1.jpg
├── jessica/             → AdsPower Profile B (proxy B)
│   ├── clip1.mp4
│   └── clip2.mp4
└── sophie/              → AdsPower Profile C (proxy C)
    └── clip1.mp4
```

Each folder is a **campaign**: one creator, one AdsPower profile, one proxy. Campaigns run in parallel (separate browser instances). Posts within a campaign are strictly sequential with humanized delays.

### Pipeline Per Campaign

```
1. LOAD      → Scan folder for videos/images
2. ANALYZE   → Claude Vision analyzes each file → tags, body_type, ethnicity, action, etc.
3. MATCH     → Score all 7,428 profiled GREEN subs against vision output
4. SELECT    → Weighted random pick from score tiers (different subs every time)
5. TITLE     → Grok generates a title per content-sub pairing
6. REVIEW    → (Manual mode) Worker reviews/edits plan. (Auto mode) Skip.
7. SPOOF     → Spoofer creates unique file copy per post
8. POST      → Playwright uploads to Reddit via AdsPower browser
9. TRACK     → Log result, detect bans, update history
```

Steps 2-5 happen before any posting begins. The full plan is generated first, then executed.

---

## Step 1: Vision Analyzer

### What
Claude Vision API analyzes each image/video and returns structured content tags.

### Implementation
- **File**: `src/core/vision_matcher.py` (exists, needs refinement)
- Model: `claude-sonnet-4-5-20250929` for vision
- For videos: extract thumbnail frame via FFmpeg, send to Vision API
- For images: send directly

### Vision Output Schema
```json
{
  "tags": ["tag1", "tag2", "tag3"],
  "body_type": "petite",
  "ethnicity": "latina",
  "hair_color": "brunette",
  "setting": "bedroom",
  "clothing": "lingerie",
  "action": "posing",
  "content_type": "photo|video|gif",
  "explicit_level": "nude|semi_nude|clothed"
}
```

### Notes
- Vision runs once per file, results cached
- Batch all files in a folder before moving to matching

---

## Step 2: Subreddit Matching + Random Selection

### What
Score vision output against all profiled GREEN subs. Weighted random selection.

### Implementation
- **File**: `src/core/vision_matcher.py` (extend existing `match_content()`)
- Score factors: tag overlap, body_type match, ethnicity match, setting match, clothing match, action match, format_preference match, subscriber bonus
- Scoring uses all profile fields (tags, body_type, ethnicity, setting, clothing, action, theme, format_preference)

### Random Selection Algorithm
```
1. Score ALL profiled subs against vision output
2. Exclude subs where this content was already posted (from history)
3. Exclude subs where this account is banned
4. Split into tiers:
   - Tier A: score > 70 (best matches)
   - Tier B: score 40-70 (good matches)
   - Tier C: score 20-40 (decent matches)
5. Random select N subs total:
   - 60% from Tier A
   - 30% from Tier B
   - 10% from Tier C
6. Shuffle final list
```

### Config
- `posts_per_content`: How many subs to post each piece to (default: 5-10, configurable)

---

## Step 3: Title Generation

### What
Grok generates unique, catchy titles for each content-to-subreddit pairing.

### Implementation
- **File**: `src/core/title_generator.py` (new)
- Model: `grok-4-1-fast-reasoning`
- Batch request: send all pairings at once (content tags + target sub name/theme)
- Grok returns a unique title per pairing

### Prompt Design
- Input: content tags + target sub name + sub theme
- Output: short Reddit-style title (not clickbait, fits the sub's culture)
- Vary style: some questioning, some descriptive, some playful
- Never repeat the same title pattern

---

## Step 4: AdsPower Multi-Profile Integration

### What
GUI lets worker assign AdsPower profiles to content folders. Multiple profiles run concurrently.

### Implementation
- **File**: `src/gui/auto_poster_tab.py` (extend)
- Load profiles from `src/uploaders/redgifs/adspower_config.json`
- Per-folder profile assignment (dropdown per folder row)
- "Connect All" button starts all selected profiles
- Per-profile session verification: navigate to `reddit.com/user/me`, confirm logged in

### Multi-Profile Execution
- Each profile runs in its own thread
- Each thread has its own Playwright browser connection
- Each thread has its own proxy (from AdsPower profile)
- Threads are independent: own delays, own browsing, own ban tracking
- Main thread collects results from all campaign threads

### AdsPower API Usage
```python
# Start profile
GET /api/v1/browser/start?user_id={profile_id}&api_key={key}
# Returns WebSocket endpoint for Playwright

# Check if active
GET /api/v1/browser/active?user_id={profile_id}&api_key={key}

# Stop profile
GET /api/v1/browser/stop?user_id={profile_id}&api_key={key}
```

---

## Step 5: Direct Upload Posting

### What
Default posting mode. Playwright uploads spoofed file directly to Reddit.

### Implementation
- **File**: `src/uploaders/reddit/reddit_poster_playwright.py` (extend)
- New function: `post_file_to_subreddit(page, subreddit, title, file_path, mark_nsfw, flair)`
- Navigate to `reddit.com/r/{sub}/submit`
- Select "Image & Video" tab (not "Link")
- Upload file via file input element
- Fill title, mark NSFW, select flair if needed
- Submit and verify

### Spoofer Integration
- Before each upload, spoofer creates a unique copy of the file
- Each copy has different metadata/fingerprint
- Unique copy uploaded, then deleted after successful post
- Existing spoofer code handles the transformation

### Video Splitter Connection
- Video Converter tab outputs clips to a folder
- That folder IS the campaign folder for the Auto Poster tab
- Worker splits videos → clips appear in folder → Auto Poster picks them up

---

## Step 6: Manual Review Mode

### What
Before posting begins, worker can review and edit the full plan.

### Implementation
- **File**: `src/gui/auto_poster_tab.py` (extend)
- After analysis + matching + title gen, display a table:
  | Content | Subreddit | Title | Action |
  |---------|-----------|-------|--------|
  | clip1.mp4 | r/boobs | "title here" | [x] Post / [ ] Skip |
- Worker can: check/uncheck subs, edit titles, remove content pieces
- "Start Posting" button only activates after review
- "Auto" toggle skips review entirely

---

## Step 7: Humanization

### What
Full humanization to make automated posting indistinguishable from manual.

### Implementation
- **File**: `src/core/humanizer.py` (new)

### Techniques (all implemented)

1. **Random delays**: `random.uniform(45, 120)` seconds between posts
2. **Long breaks**: Every 5-8 posts, take a 3-5 minute break
3. **Session warming**: On first connect per profile:
   - Visit reddit.com home feed
   - Scroll down 2-4 times over 5-15 seconds
   - Click one random post, view for 3-8 seconds
   - Navigate away
4. **Pre-post browsing**: Before each submission:
   - Visit target subreddit
   - Scroll feed for 3-8 seconds
   - Then navigate to submit page
5. **Typing simulation**: Use `page.type()` with `delay=random.uniform(50, 150)` ms per character
6. **Mouse jitter**: Random mouse movements (10-50px) before clicking submit, NSFW, flair buttons
7. **Daily post limits**: Configurable per account (default 8 for new accounts). Auto-stop when hit.
8. **Cadence variation**: Randomly switch between:
   - Bursty: 3-5 posts with 30-60s gaps
   - Normal: 1 post every 60-120s
   - Slow: 1 post every 5-10 minutes
   Pattern changes every 3-6 posts.

### Humanizer API
```python
humanizer = Humanizer(page, config)
humanizer.warm_session()           # Called once on connect
humanizer.pre_post_browse(sub)     # Before each post
humanizer.type_text(selector, text) # Instead of page.fill()
humanizer.human_click(selector)     # Instead of page.click()
humanizer.wait_between_posts()      # Between posts
humanizer.should_take_break()       # Check if long break needed
humanizer.should_stop_for_day()     # Check daily limit
```

---

## Step 8: Ban Detection + Results

### What
Detect bans and log all results.

### Implementation
- **File**: `src/core/ban_detector.py` (new)

### Detection Methods

1. **Sub ban**: After posting, check page for:
   - "you've been banned from participating"
   - "you aren't allowed to post here"
   - "this community has restricted posting"
   - Redirect to a ban page
2. **Account suspension**: Before each session, navigate to `reddit.com/user/me`:
   - Check for "Your account has been suspended"
   - Check for redirect to suspension page
   - If suspended, mark profile as dead, skip entirely
3. **Rate limiting**: Check for:
   - "you are doing that too much"
   - HTTP 429 responses
   - If hit, increase delays for this profile

### Results Logging
- Auto-save CSV after every post (crash-safe)
- Fields: profile_id, content_file, subreddit, title, status, error, posted_at, post_url
- Per-profile ban list (JSON): subs this account is banned from

---

## Step 9: Post History

### What
Track all posting history across sessions.

### Implementation
- **File**: `src/core/post_history.py` (new)
- SQLite database: `data/post_history.db`

### Schema
```sql
CREATE TABLE posts (
    id INTEGER PRIMARY KEY,
    profile_id TEXT,
    content_hash TEXT,      -- hash of original file (pre-spoof)
    subreddit TEXT,
    title TEXT,
    post_url TEXT,
    status TEXT,            -- success/failed/banned
    posted_at TIMESTAMP,
    UNIQUE(content_hash, subreddit)  -- prevent reposting same content to same sub
);

CREATE TABLE banned_subs (
    profile_id TEXT,
    subreddit TEXT,
    banned_at TIMESTAMP,
    PRIMARY KEY(profile_id, subreddit)
);
```

---

## Step 10: Scheduling + Rotation (Later)

Deferred. Build after core pipeline is working and tested.

---

## File Structure (New/Modified)

```
src/
├── core/
│   ├── vision_matcher.py      # MODIFY - refine vision + matching
│   ├── title_generator.py     # NEW - Grok title generation
│   ├── humanizer.py           # NEW - full humanization engine
│   ├── ban_detector.py        # NEW - ban detection
│   ├── post_history.py        # NEW - SQLite history tracking
│   └── gif_generator.py       # EXISTS - video splitter (unchanged)
├── gui/
│   ├── main_window.py         # MODIFY - connect tabs
│   └── auto_poster_tab.py     # MODIFY - full campaign UI
├── uploaders/
│   └── reddit/
│       └── reddit_poster_playwright.py  # MODIFY - add direct upload
data/
└── post_history.db            # NEW - SQLite history
```

## External Dependencies
- `subreddit_profiles.json` — 7,428 sub profiles (DONE)
- `subreddit_tiers_grok.json` — 11,648 tier evaluations (DONE)
- Existing video spoofer (already built, integrate don't rebuild)
- AdsPower running locally with profiles configured
- Claude API key (vision)
- Grok API key (titles)
