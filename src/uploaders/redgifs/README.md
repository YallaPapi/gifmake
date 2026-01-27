# RedGifs Uploader

Async video uploader for RedGifs with concurrent processing and rate limit handling.

## Features

- Async multi-threaded uploads
- Automatic rate limit detection
- Duplicate detection (MD5)
- Proxy support
- Colorized logging with thread-specific colors
- Upload results export to TXT
- Cleanup utility for uploaded videos

## Requirements

- Python 3.10+
- FFmpeg (ffprobe)

## Installation

1. Install FFmpeg:
```bash
winget install FFmpeg
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment:
```bash
cp .env.example .env
```

Edit `.env` and set your `REDGIFS_TOKEN`.

## Usage

### Upload Videos

1. Place video files in `videos/` folder
2. Run the uploader:
```bash
python main.py
```

The uploader will:
- Process videos concurrently
- Show colored output for each thread
- Save results to `results_YYYY-MM-DD_HH-MM-SS.txt`

### Cleanup Uploaded Videos

After successful uploads, remove uploaded files:

```bash
python cleanup.py
```

This will:
- Find the latest results file
- Show statistics
- Ask for confirmation
- Delete successfully uploaded videos

## Configuration

Edit `.env` file:

```env
REDGIFS_TOKEN=your_bearer_token_here
THREADS=3
TAGS=Amateur,Ass,Big Tits
PROXY=IP:PORT:USER:PASS
```

### Getting RedGifs Token

1. Open https://www.redgifs.com/ and login
2. Open browser DevTools (F12) → Network tab
3. Make any API request on the site
4. Copy the `Authorization: Bearer` token from request headers

## Project Structure

```
/
├── main.py              # Entry point - upload videos
├── cleanup.py           # Cleanup utility - delete uploaded videos
├── src/
│   ├── config.py        # Configuration loader
│   ├── api_client.py    # HTTP client wrapper
│   ├── uploader.py      # Upload logic
│   ├── utils.py         # Helper functions
│   ├── logger.py        # Colored logging with thread colors
│   └── results_saver.py # Results export to TXT
├── videos/              # Place videos here
├── results_*.txt        # Upload results (auto-generated)
├── .env                 # Configuration
└── requirements.txt     # Dependencies
```

## Upload Process

1. Calculate MD5 hash
2. Initialize upload (get S3 presigned URL)
3. Upload to S3
4. Submit for processing
5. Wait for encoding
6. Publish with tags

## Results File Format

After each run, a `results_YYYY-MM-DD_HH-MM-SS.txt` file is created:

```
# RedGifs Upload Results - 2025-12-10_15-30-45
# Format: STATUS|FILENAME|URL_OR_ERROR

SUCCESS|video1.mp4|https://www.redgifs.com/watch/abc123
FAILED|video2.mp4|HTTPException: 403
SKIPPED|video3.mp4|ПРОПУЩЕНО (лимит достигнут)
```

Easy to parse programmatically:
```python
with open('results_2025-12-10_15-30-45.txt') as f:
    for line in f:
        if line.startswith('#') or not line.strip():
            continue
        status, filename, info = line.strip().split('|', 2)
```

## Rate Limits

The uploader automatically detects rate limits and stops processing remaining videos. Check the output for cooldown time.

## License

MIT
