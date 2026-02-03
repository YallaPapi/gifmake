# GifMake System Bible

**Last Updated:** 2026-02-03
**Purpose:** Complete technical reference for the GifMake project, including RedGIFs uploader and Reddit poster.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [File Structure](#2-file-structure)
3. [RedGIFs Uploader](#3-redgifs-uploader)
4. [Reddit Poster](#4-reddit-poster)
5. [AdsPower Integration](#5-adspower-integration)
6. [Scheduler System](#6-scheduler-system)
7. [Configuration Files](#7-configuration-files)
8. [Database Schema](#8-database-schema)
9. [API Endpoints](#9-api-endpoints)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Project Overview

**GifMake** is a desktop application that:
1. Converts videos into GIFs/clips using FFmpeg
2. Uploads videos to RedGIFs via their API
3. Posts RedGIFs links to Reddit subreddits via browser automation
4. Schedules automated uploads throughout the day

### Tech Stack
- **Python 3.10+**
- **CustomTkinter** - GUI framework
- **FFmpeg** - Video processing
- **aiohttp** - Async HTTP for RedGIFs API
- **Selenium/Playwright** - Browser automation for Reddit
- **AdsPower** - Anti-detect browser profile management
- **SQLite** - Scheduler queue persistence

---

## 2. File Structure

```
C:\Users\asus\Desktop\projects\gifmake\
├── src/
│   ├── main.py                          # GUI entry point
│   ├── core/
│   │   ├── gif_generator.py             # FFmpeg video-to-GIF conversion
│   │   └── settings.py                  # Default settings constants
│   ├── gui/
│   │   └── main_window.py               # CustomTkinter GUI (1350 lines)
│   ├── scheduler/
│   │   ├── cli.py                       # Typer CLI commands
│   │   ├── config.py                    # JSON config loader
│   │   ├── database.py                  # SQLite operations
│   │   ├── scheduler.py                 # Main scheduling loop
│   │   └── sources.py                   # Video file discovery
│   └── uploaders/
│       ├── upload_bridge.py             # GUI-to-RedGIFs facade
│       ├── redgifs/
│       │   ├── main.py                  # Multi-account upload orchestrator
│       │   ├── refresh_tokens.py        # AdsPower token extraction
│       │   ├── cleanup.py               # Post-upload file cleanup
│       │   ├── accounts.json            # Account credentials/settings
│       │   ├── adspower_config.json     # AdsPower API config
│       │   └── redgifs_core/
│       │       ├── account_manager.py   # Account dataclass + manager
│       │       ├── api_client.py        # Async HTTP client
│       │       ├── uploader.py          # 5-step upload workflow
│       │       ├── config.py            # .env loader
│       │       ├── utils.py             # MD5, MIME, duration helpers
│       │       ├── logger.py            # Colored logging
│       │       └── results_saver.py     # Results file writer
│       └── reddit/
│           ├── reddit_poster.py         # Selenium implementation
│           ├── reddit_poster_playwright.py  # Playwright implementation
│           └── __init__.py              # Module exports
├── docs/
│   └── prd.md                           # Product requirements
├── scheduler_config.json                # Scheduler configuration
├── run.bat                              # Windows launch script
└── requirements.txt                     # Python dependencies
```

---

## 3. RedGIFs Uploader

### 3.1 Upload Flow (5 Steps)

```
STEP 1: Initialize Upload
    POST /v2/upload
    Body: {"md5": file_hash, "type": "gif", "timeline": true}
    Response: {"id": upload_id, "status": "pending|ready", "url": s3_presigned_url}

STEP 2: Upload to S3 (if status != "ready")
    PUT {s3_presigned_url}
    Body: file bytes
    Headers: {"Content-Type": "video/mp4"}

STEP 3: Submit for Processing
    POST /v2/gifs/submit
    Body: {
        "ticket": upload_id,
        "tags": [...],
        "private": false,
        "keepAudio": bool,
        "description": str,
        "niches": [...],
        "sexuality": str,
        "contentType": str,
        "draft": false,
        "cut": {"start": 0, "duration": duration}
    }
    Response: {"id": gif_id}

STEP 4: Wait for Encoding
    GET /v1/gifs/fetch/status/{gif_id}
    Poll until status == "complete"

STEP 5: Publish
    PATCH /v2/gifs/{gif_id}
    Body: {"tags": [...], "published": true, ...}
    Result: Video live at https://www.redgifs.com/watch/{gif_id}
```

### 3.2 Account Dataclass

```python
@dataclass
class Account:
    name: str                    # Account identifier
    token: str                   # Bearer token (JWT)
    enabled: bool = True         # Include in upload run
    proxy: str = ""              # ip:port:user:pass
    proxy_rotation_url: str = "" # URL to rotate IP
    video_folder: str = "videos" # Folder with videos
    tags: list[str] = None       # Default: ["Amateur", "Ass", "Big Tits"]
    description: str = ""        # Video caption
    content_type: str = "Solo Female"
    sexuality: str = "straight"
    niches: list[str] = None
    threads: int = 3             # Concurrent uploads
    keep_audio: bool = False
```

### 3.3 Rate Limit Handling

- HTTP 429 response triggers rate limit mode
- `RateLimitState.reached = True` set
- All subsequent uploads skip immediately
- Delay from response body: `{"error": {"delay": 3600}}`

### 3.4 Proxy URL Format

**Input formats accepted:**
- `ip:port:user:pass`
- `http://ip:port:user:pass`

**Converted to aiohttp format:**
- `http://user:pass@ip:port`

---

## 4. Reddit Poster

### 4.1 Posting Flow

```
1. Load AdsPower config (adspower_config.json)
2. Start browser profile via AdsPower API
3. Connect Selenium/Playwright to browser
4. Navigate to: https://www.reddit.com/r/{subreddit}/submit?type=link
5. Fill title (textarea with Shadow DOM)
6. Fill URL (Shadow DOM: faceplate-textarea-input)
7. Mark as NSFW (check aria-pressed before clicking)
8. Click Post button
9. Verify success by checking for /comments/ in URL
```

### 4.2 CSS Selectors for Reddit UI

**Title field:**
```css
textarea[name="title"]
[data-testid="post-title-input"]
textarea[placeholder*="title" i]
```

**URL field (Shadow DOM):**
```python
# Playwright - pierces Shadow DOM
page.locator('faceplate-textarea-input[name="link"]').locator('textarea')
```

**NSFW toggle:**
```css
button[aria-label*="NSFW" i]
[data-testid="nsfw-btn"]
```

**Submit button:**
```css
button[type="submit"]
[data-testid="submit-post-btn"]
button:has-text("Post")
```

### 4.3 Shadow DOM Handling

Reddit's new UI uses Shreddit web components with Shadow DOM. Playwright can pierce Shadow DOM:

```python
# This pierces through the shadow boundary
url_input = page.locator('faceplate-textarea-input[name="link"]').locator('textarea')
url_input.fill(url)
```

### 4.4 Function Signature

```python
def post_link_to_subreddit(
    page: Page,           # Playwright page object
    subreddit: str,       # Without "r/" prefix
    title: str,           # Post title
    url: str,             # RedGIFs URL
    mark_nsfw: bool = True
) -> bool:                # True if successful
```

---

## 5. AdsPower Integration

### 5.1 What is AdsPower

AdsPower is an anti-detect browser that:
- Maintains isolated browser profiles (cookies, fingerprints)
- Allows separate login sessions per account
- Provides API for automated browser control

### 5.2 API Endpoints

**Start Browser:**
```
GET http://127.0.0.1:50325/api/v1/browser/start?user_id={profile_id}&api_key={api_key}

Response:
{
    "code": 0,
    "data": {
        "ws": {
            "selenium": "127.0.0.1:PORT",
            "puppeteer": "ws://127.0.0.1:PORT/devtools/browser/UUID"
        },
        "version": "140.0.7339.81"
    }
}
```

**Stop Browser:**
```
GET http://127.0.0.1:50325/api/v1/browser/stop?user_id={profile_id}&api_key={api_key}
```

### 5.3 Selenium Connection

```python
options = Options()
options.add_experimental_option("debuggerAddress", "127.0.0.1:PORT")
service = Service(executable_path=chromedriver_path)
driver = webdriver.Chrome(service=service, options=options)
```

### 5.4 Playwright Connection

```python
ws_endpoint = browser_data['ws']['puppeteer']  # Full ws:// URL
browser = playwright.chromium.connect_over_cdp(ws_endpoint)
page = browser.contexts[0].pages[0]
```

### 5.5 ChromeDriver Auto-Download

ChromeDriver is automatically downloaded to match browser version:

```
src/uploaders/redgifs/chromedriver_cache/
    chromedriver_140.exe
    chromedriver_139.exe
    ...
```

Source: `https://googlechromelabs.github.io/chrome-for-testing/`

---

## 6. Scheduler System

### 6.1 Scheduling Modes

**Spread Mode** (`schedule_mode: "spread"`):
- Distributes uploads evenly throughout active hours
- Example: 20 posts over 15 hours = 45 min intervals

**Batch Mode** (`schedule_mode: "batch"`):
- Uploads at specific times (e.g., 09:00, 15:00, 21:00)

### 6.2 CLI Commands

```bash
scheduler start              # Run in foreground
scheduler start --daemon     # Run in background
scheduler stop               # Stop daemon
scheduler status             # Show status and quotas
scheduler add /path -a acct  # Add video to queue
scheduler scan               # Scan all sources
scheduler errors             # Show recent errors
scheduler history            # Show upload history
```

### 6.3 Retry Logic

```json
{
    "retry_max": 3,
    "retry_backoff_minutes": [5, 30, 120]
}
```

- Retry 1: Wait 5 minutes
- Retry 2: Wait 30 minutes
- Retry 3: Wait 120 minutes
- After 3 failures: Mark as `failed`

---

## 7. Configuration Files

### 7.1 accounts.json

**Location:** `src/uploaders/redgifs/accounts.json`

```json
{
    "accounts": [
        {
            "name": "accountname",
            "enabled": true,
            "token": "eyJ...",
            "proxy": "ip:port:user:pass",
            "proxy_rotation_url": "https://...",
            "video_folder": "videos",
            "tags": ["Amateur", "Ass"],
            "description": "caption",
            "content_type": "Solo Female",
            "sexuality": "straight",
            "niches": [],
            "threads": 1,
            "keep_audio": false
        }
    ]
}
```

### 7.2 adspower_config.json

**Location:** `src/uploaders/redgifs/adspower_config.json`

```json
{
    "adspower_api_base": "http://127.0.0.1:50325",
    "api_key": "your_api_key",
    "profiles": [
        {
            "profile_id": "k17q5m3h",
            "account_name": "accountname"
        }
    ]
}
```

### 7.3 scheduler_config.json

**Location:** Project root

```json
{
    "posts_per_day": 20,
    "schedule_mode": "spread",
    "active_hours": {"start": "08:00", "end": "23:00"},
    "batch_times": ["09:00", "15:00", "21:00"],
    "sources": [
        {
            "type": "local",
            "path": "C:/path/to/videos",
            "account": "accountname"
        }
    ],
    "retry_max": 3,
    "retry_backoff_minutes": [5, 30, 120],
    "database_path": "scheduler.db"
}
```

---

## 8. Database Schema

### 8.1 Queue Table

```sql
CREATE TABLE queue (
    id INTEGER PRIMARY KEY,
    account_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    retry_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',  -- pending, processing, done, failed
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### 8.2 History Table

```sql
CREATE TABLE history (
    id INTEGER PRIMARY KEY,
    account_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    redgifs_url TEXT,
    status TEXT NOT NULL,  -- success, failed
    error_message TEXT,
    completed_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### 8.3 Errors Table

```sql
CREATE TABLE errors (
    id INTEGER PRIMARY KEY,
    queue_id INTEGER,
    account_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    error_type TEXT NOT NULL,  -- rate_limit, token, network, file, unknown
    error_message TEXT NOT NULL,
    occurred_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

---

## 9. API Endpoints

### 9.1 RedGIFs API

**Base URL:** `https://api.redgifs.com`

**Headers:**
```
Authorization: Bearer {token}
Content-Type: application/json
Origin: https://www.redgifs.com
Referer: https://www.redgifs.com/
```

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/v2/upload` | Initialize upload, get S3 URL |
| POST | `/v2/gifs/submit` | Submit for processing |
| GET | `/v1/gifs/fetch/status/{id}` | Check encoding status |
| PATCH | `/v2/gifs/{id}` | Publish with metadata |
| GET | `/v1/me` | Get current user (token validation) |

### 9.2 AdsPower Local API

**Base URL:** `http://127.0.0.1:50325`

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/v1/browser/start` | Start browser profile |
| GET | `/api/v1/browser/stop` | Stop browser profile |

---

## 10. Troubleshooting

### 10.1 Common Errors

**"Could not find URL input"**
- Reddit UI changed or Shadow DOM not pierced
- Update selectors in `reddit_poster_playwright.py`

**"Token not configured"**
- Run `refresh_tokens.py` to extract new tokens
- Verify AdsPower profile is logged into RedGIFs

**"Rate limit exceeded"**
- Wait for delay period (usually 1 hour)
- Reduce posts_per_day in config

**"ChromeDriver version mismatch"**
- Delete `chromedriver_cache/` folder
- Script will auto-download correct version

### 10.2 Token Refresh

Tokens expire after ~1 hour. To refresh:

```bash
cd src/uploaders/redgifs
python refresh_tokens.py
```

Requires:
- AdsPower running
- Browser profile logged into RedGIFs

### 10.3 Testing Reddit Poster

```bash
cd C:\Users\asus\Desktop\projects\gifmake
python src/uploaders/reddit/reddit_poster_playwright.py
```

Edit the `main()` function to set:
- `subreddit`
- `title`
- `url`

---

## Quick Reference

### Run GUI
```bash
python src/main.py
# or
run.bat
```

### Run RedGIFs Upload
```bash
cd src/uploaders/redgifs
python main.py
```

### Run Reddit Post
```bash
python src/uploaders/reddit/reddit_poster_playwright.py
```

### Run Scheduler
```bash
python -m src.scheduler start
```

### Refresh Tokens
```bash
cd src/uploaders/redgifs
python refresh_tokens.py
```

---

## 11. CSV Workflow (RedGIFs → Reddit)

### 11.1 Complete Workflow

```
RedGIFs Uploader posts videos
    ↓
Exports to CSV: title, redgifs_url, account_name, timestamp
    ↓
User prepares posts.csv with subreddit assignments
    ↓
Reddit Poster reads CSV
    ↓
For each row: post to ONE subreddit with that unique link
    ↓
Rate limit delays between posts
    ↓
Results saved to output CSV
```

### 11.2 CSV Export (RedGIFs → CSV)

After each upload run, CSV is automatically exported:

**File:** `{account_name}_uploads_{date}.csv`
**Location:** Same folder as videos

**Format:**
```csv
title,redgifs_url,account_name,timestamp
video_001,https://www.redgifs.com/watch/abc123,accountname,2026-02-03 10:15:30
video_002,https://www.redgifs.com/watch/def456,accountname,2026-02-03 10:16:45
```

### 11.3 CSV Input (Reddit Poster)

Prepare a CSV for batch posting:

**Format:**
```csv
subreddit,title,url,flair
mysubreddit,Post title here,https://redgifs.com/watch/abc123,OC
othersubreddit,Another post,https://redgifs.com/watch/def456,Video
```

**Columns:**
- `subreddit`: Target subreddit (without r/)
- `title`: Post title
- `url`: RedGIFs URL (must be unique per row)
- `flair`: Optional flair text to select

### 11.4 Batch Posting Function

```python
from src.uploaders.reddit.reddit_poster_playwright import batch_post_from_csv

results = batch_post_from_csv(
    page=page,              # Playwright page connected to AdsPower
    csv_path="posts.csv",   # Input CSV file
    output_path=None,       # Auto-generates: posts_results_{timestamp}.csv
    delay_seconds=60,       # Wait between posts (rate limiting)
    mark_nsfw=True          # Mark all posts as NSFW
)

print(f"Success: {results['success']}/{results['total']}")
```

**Return value:**
```python
{
    'total': 10,
    'success': 8,
    'failed': 1,
    'skipped': 1,  # Duplicate URLs or missing fields
    'results': [...],
    'output_file': 'posts_results_2026-02-03_14-30-00.csv'
}
```

### 11.5 Output CSV

**Format:**
```csv
subreddit,title,url,flair,status,error,posted_at
mysubreddit,Post title,https://redgifs.com/watch/abc,,success,,2026-02-03T14:30:00
other,Another,https://redgifs.com/watch/def,,failed,Post submission failed,
```

### 11.6 Flair Selection

Flairs are specified per-row in the CSV. The poster will:
1. Click the flair picker button
2. Search for matching flair text
3. Select it if found
4. Continue posting even if flair not found (logs warning)

```python
# Single post with flair
post_link_to_subreddit(
    page=page,
    subreddit="mysubreddit",
    title="My post",
    url="https://redgifs.com/watch/xyz",
    mark_nsfw=True,
    flair="OC"  # Optional flair text
)
```

### 11.7 Command Line Usage

```bash
# Run batch posting from CSV
python src/uploaders/reddit/reddit_poster_playwright.py --batch

# Input: posts.csv in current directory
# Output: posts_results_{timestamp}.csv
```

---

## Key Rules

**One unique RedGIFs link = One subreddit post. Never reuse links.**

The batch poster enforces this: duplicate URLs in the input CSV are automatically skipped.

---

## Future Development Notes

### Remaining TODO
1. Hook RedGIFs uploader directly to Reddit poster (end-to-end automation)
2. GUI integration for CSV workflow
3. Subreddit-specific rate limits (some subreddits have stricter limits)
