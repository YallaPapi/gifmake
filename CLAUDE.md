# GifMake Project Instructions

## Project Overview

GifMake is a desktop application for:
1. Converting videos into GIFs/clips using FFmpeg
2. Uploading videos to RedGIFs via their API
3. Posting RedGIFs links to Reddit subreddits via browser automation
4. Scraping subreddit rules for automated posting decisions

## Key Documentation

- `docs/SYSTEM_BIBLE.md` - Complete technical reference
- `STATUS_REPORT.md` - Current project completion status
- `CHANGELOG.md` - Recent changes
- `FILE_STRUCTURE.md` - Codebase layout

## Tech Stack

- Python 3.10+
- CustomTkinter (GUI)
- FFmpeg (video processing)
- aiohttp (async HTTP for RedGIFs)
- Playwright (Reddit browser automation)
- AdsPower (anti-detect browser profiles)
- SQLite (scheduler queue)

## Proxy Configuration

Proxy is configured in `src/uploaders/redgifs/accounts.json`:
```
proxy: "host:port:user:pass"
proxy_rotation_url: "https://..." (hit this URL to rotate IP)
```

Format for requests library:
```python
proxy_str = "host:port:user:pass"
parts = proxy_str.split(":")
proxy_url = f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
proxies = {"http": proxy_url, "https": proxy_url}
```

## Reddit API Rate Limits

**ACTUAL LIMITS (not guesses):**
- 100 requests per 10-minute window
- 10 requests per minute maximum
- 1 request every 6 seconds to stay safe
- Use proxy rotation to bypass IP-based limits

Headers returned by Reddit:
- `x-ratelimit-used`: requests used in current window
- `x-ratelimit-remaining`: requests left
- `x-ratelimit-reset`: seconds until reset

## Subreddit Scraping

**Main Script:** `scrape_v3.py` (with proxy + heartbeat monitoring)

Endpoints:
- `https://www.reddit.com/r/{sub}/about.json` - subreddit info
- `https://www.reddit.com/r/{sub}/about/rules.json` - rules list

Data to collect:
- subscribers, over18, description, submission_type
- rules (title + description for each)

Files:
- `all_subreddits.txt` - full list of 5,345 subreddits
- `subreddit_data_v3.json` - scraped data output (dict keyed by subreddit name)
- `scrape_progress_v3.json` - progress tracking for resume
- `scrape_errors.log` - error log
- `scrape_heartbeat.txt` - liveness monitoring
- `subreddit_analysis.json` - categorization (easy/hard to post)
- `SUBREDDIT_GROUPS.md` - organized by content niche

Supporting modules:
- `scrape_error_handler.py` - error classification
- `scrape_progress_tracker.py` - progress tracking
- `scrape_watchdog.py` - monitoring tool
- `run_scraper.py` - orchestrator (runs v3 + watchdog)

Archived (do not use):
- `scripts/archived/scrape_all_subreddits.py` - no proxy support
- `scripts/archived/scrape_v2.py` - superseded by v3

## Key Rules

1. **One RedGIFs link = One subreddit post** - Never reuse links
2. **Use proxy for scraping** - Local IP gets rate limited fast
3. **Skip verification-required subreddits** - Can't automate those
4. **Respect rate limits** - 10 requests/min with proxy

## File Locations

| Purpose | Path |
|---------|------|
| Main GUI | `src/main.py` |
| RedGIFs uploader | `src/uploaders/redgifs/main.py` |
| Reddit poster | `src/uploaders/reddit/reddit_poster_playwright.py` |
| Accounts config | `src/uploaders/redgifs/accounts.json` |
| AdsPower config | `src/uploaders/redgifs/adspower_config.json` |
| Scheduler | `src/scheduler/scheduler.py` |

## Common Commands

```bash
# Run GUI
python src/main.py

# Run RedGIFs upload
cd src/uploaders/redgifs && python main.py

# Run Reddit poster
python src/uploaders/reddit/reddit_poster_playwright.py

# Run subreddit scraper (with proxy + heartbeat)
python scrape_v3.py

# Or use the orchestrator (includes watchdog)
python run_scraper.py --no-test
```

## When Working on This Project

1. Always read SYSTEM_BIBLE.md first for context
2. Use proxy for any Reddit API requests
3. Check already_scraped.txt before scraping to avoid duplicates
4. Save checkpoints frequently (every 50 items)
5. Use multiple subagents for parallel tasks
