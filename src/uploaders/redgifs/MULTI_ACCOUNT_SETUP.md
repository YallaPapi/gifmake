# Multi-Account Setup Guide

## Quick Start

1. **Copy the example config**:
   ```bash
   cp accounts.json.example accounts.json
   ```

2. **Configure AdsPower integration** (optional but recommended):
   ```bash
   cp adspower_config.json.example adspower_config.json
   ```
   Edit with your AdsPower API credentials and profile IDs

3. **Edit `accounts.json`** with your account details

4. **Create video folders** for each account:
   ```bash
   mkdir videos
   mkdir videos2
   ```

5. **Run the uploader**:
   ```bash
   python main.py
   ```

   Tokens will be automatically refreshed from AdsPower if configured!

---

## Account Configuration

Each account in `accounts.json` has these fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Account identifier (used in logs) |
| `enabled` | boolean | Yes | `true` to enable, `false` to skip |
| `token` | string | Yes | Bearer token from RedGIFs (get from browser DevTools) |
| `proxy` | string | No | Format: `ip:port:user:password` |
| `proxy_rotation_url` | string | No | Mobile proxy rotation URL (will be called before uploads) |
| `video_folder` | string | Yes | Folder with videos (relative to main.py) |
| `tags` | array | Yes | Tags for uploads, e.g. `["Amateur", "Ass"]` |
| `description` | string | No | Caption for videos |
| `content_type` | string | Yes | `"Solo Female"`, `"Solo Male"`, `"Couple"`, etc. |
| `sexuality` | string | Yes | `"straight"`, `"gay"`, `"lesbian"`, etc. |
| `niches` | array | No | Additional niches, e.g. `["OnlyFans"]` |
| `threads` | number | Yes | Parallel upload threads (1-5 recommended) |
| `keep_audio` | boolean | Yes | Keep audio in videos |

---

## Mobile Proxy Rotation

If you use **mobile proxies with rotation URLs**:

1. Set `proxy_rotation_url` to your rotation endpoint
2. The uploader will call this URL before starting uploads
3. Example: `"https://your-proxy.com/rotate?key=xxx"`

---

## Folder Structure

```
src/uploaders/redgifs/
├── accounts.json           # Your account config (gitignored)
├── videos/                 # Account 1 videos
├── videos2/                # Account 2 videos
├── account1_results_*.txt  # Results for account1
└── account2_results_*.txt  # Results for account2
```

---

## How It Works

1. **Auto-refreshes tokens** from AdsPower (if configured)
   - Opens each browser profile
   - Captures bearer token from network requests
   - Updates `accounts.json` with fresh tokens
2. Loads all accounts from `accounts.json`
3. Filters to enabled accounts only
4. For each account:
   - Calls proxy rotation URL (if set)
   - Scans video folder
   - Uploads videos with account's token/proxy/settings
   - Saves results to `{account_name}_results_*.txt`
5. Shows final summary across all accounts

---

## Example Run

```
============================================================
RedGifs Multi-Account Uploader v0.7.0
============================================================
Enabled accounts: 2
  - account1 (videos)
  - account2 (videos2)
============================================================

============================================================
ACCOUNT: account1
============================================================
[account1] Files: 5
[account1] Threads: 3
[account1] Tags: Amateur, Ass, Big Tits
...

============================================================
ACCOUNT: account2
============================================================
[account2] Files: 3
[account2] Threads: 2
...

============================================================
FINAL SUMMARY (ALL ACCOUNTS)
============================================================
account1: 5 success, 0 failed, 0 skipped - OK
account2: 3 success, 0 failed, 0 skipped - OK
------------------------------------------------------------
TOTAL: 8 success, 0 failed, 0 skipped
============================================================
```
