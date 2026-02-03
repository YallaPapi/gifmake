# Changelog

All notable changes to the GifMake project are documented in this file.

## [v0.3.0-beta] - 2026-02-03

This release adds Reddit posting automation with CSV-driven batch workflows, completing the full upload-to-social pipeline.

---

## New Features

### Reddit Posting Integration
- **Automated Reddit posting** via browser automation (Selenium + Playwright implementations)
- **CSV-driven batch posting** - reads RedGIFs upload results and posts to subreddits
- **Flair selection support** - automatically selects post flairs when available
- **AdsPower integration** - connects to existing browser profiles for authenticated sessions
- **Two implementations:**
  - `reddit_poster.py` (Selenium) - 415 lines
  - `reddit_poster_playwright.py` (Playwright) - 663 lines with enhanced features

### CSV Export for RedGIFs Uploads
- **Automatic CSV generation** after each RedGIFs upload batch
- **Daily aggregation** - all uploads append to same CSV file per day
- **Format:** `{account_name}_uploads_{date}.csv`
- **Columns:** `title`, `redgifs_url`, `account_name`, `timestamp`
- **Purpose:** Enables seamless handoff to Reddit posting workflow

### Documentation
- **SYSTEM_BIBLE.md** (702 lines) - Complete technical reference with:
  - Full project architecture
  - API endpoint documentation
  - Database schema reference
  - Troubleshooting guide
  - Configuration examples
- **IMPLEMENTATION_SUMMARY.md** (220 lines) - Module-by-module implementation status
- **QUICK_START.md** (156 lines) - Fast-track setup guide

---

## Modified Files

### `src/uploaders/redgifs/main.py` (+14 lines)
**Changes:**
- Added CSV export after upload completion
- Calls `ResultsSaver.save_results_to_csv()` with account name and output directory
- CSV saved in same folder as source videos for easy access
- Error handling for CSV export failures

**Code added:**
```python
# Save successful uploads to CSV (for Reddit posting workflow)
try:
    csv_file = ResultsSaver.save_results_to_csv(
        results=results,
        account_name=account.name,
        output_dir=videos_dir
    )
    if csv_file:
        logger.info(f"[{account.name}] CSV exported: {csv_file}")
except Exception as e:
    logger.error(f"[{account.name}] Failed to export CSV: {e}")
```

### `src/uploaders/redgifs/redgifs_core/results_saver.py` (+99 lines)
**New methods:**

1. **`append_to_csv(title, redgifs_url, account_name, output_dir)`**
   - Appends single upload result to daily CSV file
   - Creates file with headers if doesn't exist
   - Adds timestamp to each row
   - Returns: Path to CSV file

2. **`save_results_to_csv(results, account_name, output_dir)`**
   - Batch method that processes multiple upload results
   - Filters successful uploads (those containing RedGIFs URLs)
   - Calls `append_to_csv()` for each success
   - Returns: Path to CSV file or None if no successes

---

## New Files Created

### Documentation (1,078 lines total)
- `docs/SYSTEM_BIBLE.md` - 702 lines
- `IMPLEMENTATION_SUMMARY.md` - 220 lines
- `QUICK_START.md` - 156 lines

### Reddit Posting Module (1,082 lines total)
**Location:** `src/uploaders/reddit/`

- `__init__.py` - 4 lines (module marker)
- `reddit_poster.py` - 415 lines (Selenium implementation)
  - `post_link_to_subreddit()` - Single post function
  - `batch_post_from_csv()` - CSV-driven batch posting
  - AdsPower browser connection
  - Reddit selector constants for new UI

- `reddit_poster_playwright.py` - 663 lines (Playwright implementation)
  - All Selenium features plus:
  - `AdsPowerClient` class - Structured API client
  - `select_flair()` - Flair picker automation
  - Enhanced error handling
  - Screenshot debugging support
  - Better retry logic

### Upload Results
- `src/uploaders/redgifs/tasteofmarina_results_2026-01-28_09-51-12.txt`
- `src/uploaders/redgifs/tasteofmarina_results_2026-01-28_10-00-18.txt`
- `src/uploaders/redgifs/tasteofmarina_results_2026-01-28_10-15-04.txt`

### Other
- `tags.txt` - Tag repository for video categorization
- `batch/` - Test batch processing folder with sample files
- `debug_screenshot.png` - Browser automation debug output

---

## Recent Commits (Last 7 Days)

### 2026-02-02: Scheduler System
**Commit:** `0846a52` - Add automated upload scheduler with proxy rotation support

**Changes:** 1,119 insertions
- Added `src/scheduler/` module (9 files)
- CLI interface for scheduler management
- SQLite database for queue persistence
- Proxy rotation support
- Configuration file: `scheduler_config.json`
- Upload bridge for RedGIFs integration

**Files:**
```
scheduler_config.json          |  24 +++
src/scheduler/__init__.py      |  22 +++
src/scheduler/__main__.py      |   6 +
src/scheduler/cli.py           | 306 +++++++++++++++
src/scheduler/config.py        |  64 ++++
src/scheduler/database.py      | 168 +++++++++
src/scheduler/scheduler.py     | 410 ++++++++++++++++++++
src/scheduler/sources.py       |  69 ++++
src/uploaders/upload_bridge.py |  50 +++
```

### 2026-01-28: RedGIFs Integration
**Commit:** `b347960` - Add RedGIFs upload integration and video clip output modes

**Changes:** 1,031 insertions, 83 deletions
- Integrated RedGIFs upload into GUI
- Added video clip output mode (no GIF conversion)
- Refactored RedGIFs uploader structure (`src/` → `redgifs_core/`)
- Added `upload_bridge.py` for GUI integration
- Created comprehensive test suite

**Files:**
```
src/core/gif_generator.py      | 103 modifications
src/gui/main_window.py         | 362 additions
src/uploaders/__init__.py      |   5 +
src/uploaders/upload_bridge.py | 220 +++
tests/test_gif_generator.py    | 100 +++
tests/test_integration.py      | 166 +++
tests/test_upload_bridge.py    | 144 +++
```

### 2026-01-28: Token Management
**Commit:** `2b92219` - Add automatic bearer token refresh with AdsPower integration

**Changes:** 1,248 insertions, 113 deletions
- Auto-refresh bearer tokens using AdsPower browser profiles
- Multi-account management system
- Account configuration file: `accounts.json`
- Token refresh script: `refresh_tokens.py`
- Documentation: `TOKEN_REFRESH.md`, `MULTI_ACCOUNT_SETUP.md`

**Files:**
```
src/uploaders/redgifs/main.py            | 301 modifications
src/uploaders/redgifs/refresh_tokens.py  | 302 +++
src/uploaders/redgifs/src/account_manager.py | 166 +++
MULTI_ACCOUNT_SETUP.md                   | 129 +++
TOKEN_REFRESH.md                         | 362 +++
accounts.json.example                    |  34 +
adspower_config.json.example             |  14 +
```

---

## Usage Examples

### CSV Export (Automatic)
The CSV export happens automatically after each RedGIFs upload batch:

```bash
cd src/uploaders/redgifs
python main.py
```

**Output:**
```
[tasteofmarina] Results saved: tasteofmarina_results_2026-02-03_14-30-15.txt
[tasteofmarina] CSV exported: C:\Videos\tasteofmarina_uploads_2026-02-03.csv
```

**CSV Format:**
```csv
title,redgifs_url,account_name,timestamp
video001,https://redgifs.com/watch/sparklingbluewhale,tasteofmarina,2026-02-03 14:30:15
video002,https://redgifs.com/watch/adorableyellowfish,tasteofmarina,2026-02-03 14:30:45
```

### Reddit Posting (Playwright)

**Single Post:**
```python
from src.uploaders.reddit.reddit_poster_playwright import post_link_to_subreddit

success = post_link_to_subreddit(
    profile_id="abc123",           # AdsPower profile ID
    subreddit="test",
    title="My Awesome Video",
    url="https://redgifs.com/watch/sparklingbluewhale",
    flair="Video"                  # Optional
)
```

**Batch Post from CSV:**
```python
from src.uploaders.reddit.reddit_poster_playwright import batch_post_from_csv

batch_post_from_csv(
    profile_id="abc123",
    csv_path="C:/Videos/tasteofmarina_uploads_2026-02-03.csv",
    subreddit="test",
    flair="Video",
    delay_seconds=300              # 5 minutes between posts
)
```

**Command-line:**
```bash
cd src/uploaders/reddit
python reddit_poster_playwright.py --profile abc123 --csv results.csv --subreddit test --flair Video
```

### Reddit Posting (Selenium)

**Similar API, simpler implementation:**
```python
from src.uploaders.reddit.reddit_poster import post_link_to_subreddit, batch_post_from_csv

# Same function signatures as Playwright version
```

---

## Technical Details

### CSV Schema
```python
{
    "columns": [
        "title",        # Video title (filename without extension)
        "redgifs_url",  # Full RedGIFs watch URL
        "account_name", # RedGIFs account that uploaded it
        "timestamp"     # ISO format: YYYY-MM-DD HH:MM:SS
    ],
    "filename_pattern": "{account_name}_uploads_{YYYY-MM-DD}.csv",
    "location": "Same directory as source videos"
}
```

### Reddit Selectors (New Reddit UI - 2025)
```python
SELECTORS = {
    "post_type_link": 'button[data-testid="post-type-btn-link"]',
    "title_input": 'textarea[name="title"]',
    "url_input": 'input[name="url"]',
    "submit_button": 'button[type="submit"]',
    "flair_picker": 'button:has-text("Select flair")'
}
```

### AdsPower Integration
Both Reddit implementations connect to AdsPower browser profiles:
```python
# Start browser
url = f"http://local.adspower.net:50325/api/v1/browser/start?user_id={profile_id}"
response = requests.get(url)
ws_endpoint = response.json()["data"]["ws"]["puppeteer"]

# Connect with Playwright
browser = playwright.chromium.connect_over_cdp(ws_endpoint)
```

---

## Code Statistics

### Total Changes (Uncommitted)
- **Modified tracked files:** 2 files, +113 lines
- **New untracked files:** 12 files, 2,160+ lines
- **Total additions:** 2,273+ lines

### Module Breakdown
| Module | Files | Lines | Purpose |
|--------|-------|-------|---------|
| Reddit Posting | 3 | 1,082 | Browser automation for Reddit |
| Documentation | 3 | 1,078 | Technical reference & guides |
| CSV Export | 2 | +113 | RedGIFs-to-Reddit workflow |

### Recent Commits (7 Days)
- **Commits:** 3
- **Total insertions:** 3,398 lines
- **Total deletions:** 196 lines
- **Net change:** +3,202 lines

---

## Architecture Overview

### Complete Upload-to-Social Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                     GIFMAKE PIPELINE                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. VIDEO CONVERSION (src/core/gif_generator.py)                │
│     └── FFmpeg: Video → GIF/Clip                               │
│                                                                 │
│  2. REDGIFS UPLOAD (src/uploaders/redgifs/)                     │
│     ├── API client: Upload videos                              │
│     ├── Account manager: Multi-account support                 │
│     ├── Token refresh: AdsPower automation                     │
│     └── CSV export: Save results → {account}_uploads_{date}.csv│
│                                                                 │
│  3. REDDIT POSTING (src/uploaders/reddit/)                      │
│     ├── Read CSV: Load RedGIFs URLs                            │
│     ├── AdsPower: Connect to browser profile                   │
│     ├── Navigate: r/{subreddit}/submit                         │
│     ├── Fill form: Title, URL, Flair                           │
│     └── Submit: Create post with rate limiting                 │
│                                                                 │
│  4. SCHEDULER (src/scheduler/)                                  │
│     └── Queue: Automate uploads throughout day                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Next Steps

### Immediate Priorities

1. **Test Reddit posting workflow end-to-end**
   - Upload videos to RedGIFs
   - Verify CSV generation
   - Run batch Reddit posting
   - Validate rate limiting

2. **Integrate scheduler with Reddit posting**
   - Add Reddit posting to scheduler queue
   - Implement multi-account Reddit support
   - Add scheduling rules (time windows, frequency)

3. **Error handling improvements**
   - Retry logic for failed Reddit posts
   - Better captcha detection
   - Rate limit monitoring

### Feature Requests

- [ ] GUI integration for Reddit posting
- [ ] Subreddit rotation support
- [ ] Custom title templates
- [ ] Duplicate post detection
- [ ] Analytics dashboard (views, upvotes)
- [ ] Cross-post to multiple subreddits
- [ ] Reddit comment automation

### Technical Debt

- [ ] Unit tests for Reddit poster modules
- [ ] Integration tests for CSV workflow
- [ ] API documentation for Reddit functions
- [ ] Refactor selector constants to config file
- [ ] Add type hints to Reddit modules
- [ ] Create Reddit posting documentation

---

## Breaking Changes

None in this release. All changes are additive.

---

## Contributors

- **YallaPapi** - All development

---

## License

[Add license information]

---

**Generated:** 2026-02-03
**Branch:** main
**Commit:** 0846a52 (HEAD)
