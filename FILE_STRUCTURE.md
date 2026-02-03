# GifMake Project File Structure

**Generated:** 2026-02-03
**Total Python Lines:** 6,256

## Directory Tree

```
gifmake/
├── docs/
│   ├── prd.md                                 - Product Requirements Document
│   └── SYSTEM_BIBLE.md                        - System architecture documentation
│
├── src/
│   ├── __init__.py                    (2 lines) - Package initializer
│   ├── main.py                       (24 lines) - GUI entry point, launches GifMakeApp
│   │
│   ├── core/                                   - Core video processing functionality
│   │   ├── __init__.py                (5 lines) - Package initializer
│   │   ├── gif_generator.py         (430 lines) - Video-to-GIF/clip conversion using FFmpeg
│   │   ├── settings.py               (16 lines) - Default settings and configuration constants
│   │   └── README.md                           - Core module documentation
│   │
│   ├── gui/                                    - User interface components
│   │   ├── __init__.py                (3 lines) - Package initializer
│   │   └── main_window.py         (1,350 lines) - Main CustomTkinter GUI application
│   │
│   ├── scheduler/                              - Automated upload scheduling system
│   │   ├── __init__.py               (22 lines) - Package exports
│   │   ├── __main__.py                (6 lines) - Module entry point for CLI
│   │   ├── cli.py                   (170 lines) - Command-line interface with argparse
│   │   ├── config.py                (196 lines) - Configuration loader and validator
│   │   ├── database.py              (261 lines) - SQLite database for queue and history
│   │   ├── scheduler.py             (410 lines) - Main scheduler loop and logic
│   │   └── sources.py               (156 lines) - Source folder scanner for videos
│   │
│   └── uploaders/                              - Upload integrations
│       ├── __init__.py                (5 lines) - Package initializer
│       ├── upload_bridge.py         (270 lines) - Simplified API for GUI integration
│       │
│       ├── reddit/                             - Reddit posting (experimental)
│       │   ├── __init__.py            (4 lines) - Package initializer
│       │   ├── reddit_poster.py     (415 lines) - PRAW-based Reddit poster
│       │   └── reddit_poster_playwright.py (663 lines) - Playwright-based Reddit automation
│       │
│       └── redgifs/                            - RedGIFs uploader module
│           ├── main.py              (305 lines) - Multi-account upload orchestrator
│           ├── cleanup.py           (200 lines) - Utility to clean uploaded files
│           ├── refresh_tokens.py    (302 lines) - AdsPower browser token extractor
│           ├── accounts.json                   - Account credentials and settings
│           ├── adspower_config.json            - AdsPower browser profile mapping
│           ├── README.md                       - RedGIFs uploader documentation
│           ├── MULTI_ACCOUNT_SETUP.md          - Multi-account setup guide
│           ├── TOKEN_REFRESH.md                - Token refresh documentation
│           │
│           └── redgifs_core/                   - Core RedGIFs API client
│               ├── __init__.py        (3 lines) - Version and package info
│               ├── account_manager.py (166 lines) - Multi-account configuration manager
│               ├── api_client.py     (216 lines) - Async HTTP client for RedGIFs API
│               ├── config.py         (119 lines) - Upload configuration dataclass
│               ├── logger.py         (156 lines) - Colored logging setup
│               ├── results_saver.py  (170 lines) - TXT and CSV result exporter
│               ├── uploader.py       (236 lines) - Video upload workflow (5-step process)
│               └── utils.py          (152 lines) - MD5, MIME, duration utilities
│
├── tests/                                      - Test suite
│   ├── test_gif_generator.py                  - Unit tests for core module
│   ├── test_integration.py                    - Integration tests
│   └── test_upload_bridge.py                  - Upload bridge tests
│
├── examples/
│   └── simple_example.py                      - Example usage script
│
├── batch/                                      - Batch processing scripts
│
├── dist/                                       - PyInstaller build output
│   └── GifMake/                               - Standalone executable distribution
│       ├── GifMake.exe                        - Main executable
│       └── _internal/                         - Bundled dependencies
│
├── build/                                      - Build artifacts
│
├── scheduler_config.json                      - Scheduler configuration file
├── requirements.txt                           - Python dependencies
├── tags.txt                                   - Tag presets
├── IMPLEMENTATION_SUMMARY.md                  - Implementation overview
└── QUICK_START.md                             - Quick start guide
```

## Component Descriptions

### Core Components (src/core/)

**gif_generator.py** (430 lines)
- Main video processing module using FFmpeg
- Converts videos to GIFs or MP4 clips
- Functions:
  - `get_ffmpeg_path()` / `get_ffprobe_path()` - Locate bundled or system FFmpeg
  - `get_video_duration()` - Extract video duration using ffprobe
  - `generate_gifs()` - Split video into multiple GIFs/clips
  - `generate_gifs_bulk()` - Batch process multiple videos
  - `scan_video_folder()` - Discover videos in directories
- Supports custom resolution, FPS, duration settings
- Single-pass palette generation for high-quality GIFs

**settings.py** (16 lines)
- Default configuration constants
- Supported video formats: MP4, MOV, AVI, MKV, WebM
- Default FPS options: 10, 15, 20, 24, 30
- Resolution presets: Original, 720p, 480p, 360p

### GUI (src/gui/)

**main_window.py** (1,350 lines)
- Full-featured CustomTkinter application
- Features:
  - Single video and bulk folder modes
  - Drag-and-drop interface
  - Real-time progress tracking
  - Output format toggle (GIF / Video Clips)
  - RedGIFs upload integration (optional)
  - Scrollable video list for bulk mode
  - Account selection with custom settings
  - Dark theme with modern UI components
- Uses threading to prevent UI freezing
- Async upload support via UploadBridge

### Scheduler (src/scheduler/)

**scheduler.py** (410 lines)
- Main scheduling engine
- Two modes:
  - **Spread mode**: Evenly distribute uploads across active hours
  - **Batch mode**: Upload at specific times (e.g., 9am, 3pm, 9pm)
- Features:
  - Daily quota management (posts per day per account)
  - Retry logic with exponential backoff
  - Error classification (rate_limit, token, network, file)
  - Upload history tracking
- Database-backed queue system

**database.py** (261 lines)
- SQLite database wrapper
- Tables:
  - `queue` - Pending uploads with scheduled times
  - `history` - Upload results and timestamps
  - `errors` - Detailed error logs
- Methods for queue manipulation, status updates, retry tracking

**config.py** (196 lines)
- Configuration loader from `scheduler_config.json`
- Validates:
  - Active hours (start/end times)
  - Schedule mode (spread/batch)
  - Posts per day limits
  - Retry policies
  - Source folder definitions

**sources.py** (156 lines)
- Scans configured source folders for videos
- Maps folders to accounts
- Filters by supported extensions
- Returns account+filepath pairs

**cli.py** (170 lines)
- Command-line interface with argparse
- Commands:
  - `scan` - Show videos in sources
  - `queue` - Display queue status
  - `add` - Manually add videos
  - `clear` - Clear queue
  - `run` - Start scheduler loop

### Uploaders (src/uploaders/)

**upload_bridge.py** (270 lines)
- Simplified API for GUI integration
- Bridges GifMake GUI with RedGIFs uploader
- Features:
  - Single file upload with progress
  - Account loading from accounts.json
  - Proxy rotation support
  - Override settings (tags, description, content type)
  - Token refresh integration
  - Synchronous wrapper for async operations
- Uses ThreadedResolver to avoid Windows DNS issues

**redgifs/main.py** (305 lines)
- Multi-account upload orchestrator
- Auto-refreshes tokens via AdsPower
- Per-account configuration:
  - Video folder path
  - Thread count (parallel uploads)
  - Tags and metadata
  - Proxy settings
- Saves results as TXT and CSV
- Rate limit detection and handling

**redgifs/refresh_tokens.py** (302 lines)
- Extracts fresh bearer tokens from AdsPower browsers
- Uses Playwright to capture network traffic
- Updates accounts.json automatically
- Configurable via adspower_config.json

**redgifs/cleanup.py** (200 lines)
- Moves uploaded videos to completed folder
- Reads CSV from uploader results
- Prevents accidental re-uploads

### RedGIFs Core (src/uploaders/redgifs/redgifs_core/)

**uploader.py** (236 lines)
- 5-step upload workflow:
  1. Initialize upload (POST /v2/upload, get upload ID)
  2. Upload to S3 (PUT with presigned URL)
  3. Submit video (POST /v2/gifs/submit)
  4. Wait for encoding
  5. Publish with metadata (PATCH /v2/gifs/{id})
- Rate limit detection
- Retry logic with exponential backoff

**api_client.py** (216 lines)
- Async HTTP client for RedGIFs API
- Methods: `api_post()`, `api_get()`, `api_patch()`, `s3_put()`
- Handles:
  - Bearer token authentication
  - Proxy support
  - Timeout configuration
  - JSON parsing
  - Error responses (429 rate limits)

**account_manager.py** (166 lines)
- Loads accounts from accounts.json
- Account dataclass with:
  - Name, token, enabled flag
  - Proxy configuration (IP:PORT:USER:PASS format)
  - Proxy rotation URL
  - Video folder path
  - Tags, description, content type
  - Thread count, audio settings
- Proxy URL conversion for aiohttp

**results_saver.py** (170 lines)
- Saves upload results to:
  - TXT files (timestamped, checkmark/X status)
  - CSV files (for Reddit posting workflow)
- CSV columns: filename, redgifs_url, status, timestamp

**utils.py** (152 lines)
- Utility functions:
  - `calculate_md5()` - File hash for duplicate detection
  - `get_mime_type()` - MIME type detection
  - `get_video_duration()` - ffprobe wrapper
  - `format_time()` - Human-readable time formatting
  - `find_video_files()` - Recursive video scanner

**logger.py** (156 lines)
- Colored console logging
- Log levels: DEBUG, INFO, WARNING, ERROR
- File and console handlers
- Timestamped log files

**config.py** (119 lines)
- Upload configuration dataclass
- API endpoints and headers
- User-Agent management
- Metadata defaults

### Tests (tests/)

- `test_gif_generator.py` - Unit tests for video processing
- `test_integration.py` - End-to-end tests
- `test_upload_bridge.py` - Upload integration tests

## Configuration Files

### scheduler_config.json
```json
{
  "sources": [
    {"account": "account1", "folder": "path/to/videos"}
  ],
  "posts_per_day": 20,
  "schedule_mode": "spread",
  "active_hours_start": "08:00",
  "active_hours_end": "23:00",
  "retry_max": 3,
  "retry_backoff_minutes": [5, 15, 60]
}
```

### accounts.json (RedGIFs)
```json
{
  "accounts": [
    {
      "name": "account1",
      "enabled": true,
      "token": "your_bearer_token_here",
      "proxy": "IP:PORT:USER:PASS",
      "proxy_rotation_url": "http://proxy.provider/rotate",
      "video_folder": "videos",
      "tags": ["Amateur", "Ass", "Big Tits"],
      "description": "Default description",
      "content_type": "Solo Female",
      "sexuality": "straight",
      "niches": [],
      "threads": 3,
      "keep_audio": false
    }
  ]
}
```

### adspower_config.json
```json
{
  "accounts": [
    {
      "account_name": "account1",
      "profile_id": "adspower_profile_id",
      "open_tabs": 1
    }
  ]
}
```

## Key Dependencies

- **customtkinter** - Modern GUI framework
- **aiohttp** - Async HTTP client
- **asyncio** - Async/await support
- **Pillow** - Image processing
- **requests** - HTTP library
- **playwright** - Browser automation
- **praw** - Reddit API wrapper
- **ffmpeg/ffprobe** - Video processing (external binary)

## Build System

- **PyInstaller** - Creates standalone executable
- Bundles FFmpeg binaries
- Includes all Python dependencies
- Output: `dist/GifMake/GifMake.exe`

## Workflow Overview

### Single Video Workflow
1. User selects video in GUI
2. Configure settings (duration, FPS, resolution, format)
3. `gif_generator.generate_gifs()` splits video into clips
4. Optional: Upload to RedGIFs via `upload_bridge`
5. Results saved to output folder

### Bulk Folder Workflow
1. User selects folder with multiple videos
2. GUI scans and displays all videos
3. Each video processed into subfolder
4. Optional: Batch upload all generated files
5. Progress tracking per video and per clip

### Scheduled Upload Workflow
1. Configure sources in `scheduler_config.json`
2. Run scheduler: `python -m scheduler.cli run`
3. Scheduler scans sources, queues videos
4. Uploads at scheduled times
5. Retries on failure with backoff
6. Tracks history and errors in SQLite database

### Token Refresh Workflow
1. Run `refresh_tokens.py` (or auto-refresh in main.py)
2. Opens AdsPower browser profiles
3. Captures RedGIFs network traffic
4. Extracts bearer tokens
5. Updates `accounts.json`

## File Size Statistics

- **Total Python files:** 75
- **Total Python lines:** 6,256
- **Largest module:** `gui/main_window.py` (1,350 lines)
- **Core modules:** 446 lines
- **Scheduler system:** 1,215 lines
- **RedGIFs uploader:** 2,225 lines
- **Reddit integration:** 1,078 lines

## Documentation Files

- `docs/prd.md` - Product requirements
- `docs/SYSTEM_BIBLE.md` - System architecture
- `src/core/README.md` - Core module docs
- `src/uploaders/redgifs/README.md` - RedGIFs uploader guide
- `src/uploaders/redgifs/MULTI_ACCOUNT_SETUP.md` - Multi-account setup
- `src/uploaders/redgifs/TOKEN_REFRESH.md` - Token refresh guide
- `IMPLEMENTATION_SUMMARY.md` - Implementation overview
- `QUICK_START.md` - Quick start guide

## Entry Points

### GUI Application
```bash
python src/main.py
# OR
GifMake.exe  # (standalone build)
```

### Scheduler
```bash
python -m src.scheduler.cli run
# OR
python -m src.scheduler
```

### RedGIFs Uploader
```bash
cd src/uploaders/redgifs
python main.py
```

### Token Refresh
```bash
cd src/uploaders/redgifs
python refresh_tokens.py
```

## Architecture Patterns

### Separation of Concerns
- **Core**: Pure video processing logic
- **GUI**: User interface only, delegates to core
- **Uploaders**: Independent upload modules
- **Scheduler**: Orchestration layer

### Plugin-like Design
- Uploaders are modular (RedGIFs, Reddit)
- Easy to add new upload destinations
- Bridge pattern for GUI integration

### Async Architecture
- RedGIFs uploader uses asyncio + aiohttp
- Parallel uploads with semaphore limiting
- Non-blocking network operations

### Database-backed State
- SQLite for scheduler queue/history
- Persistent across restarts
- Supports retry logic and error tracking

## Future Expansion Points

1. **Additional upload platforms** (e.g., PornHub, Imgur)
2. **Video effects** (filters, watermarks)
3. **Web dashboard** for scheduler monitoring
4. **REST API** for programmatic access
5. **Cloud storage integration** (S3, GDrive)
6. **Advanced scheduling** (time-of-day optimization)

---

*This file structure document was generated automatically on 2026-02-03*
