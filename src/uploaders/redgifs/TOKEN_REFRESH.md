# Automatic Token Refresh with AdsPower

This document explains how the automatic bearer token refresh system works with AdsPower browser profiles.

## Overview

RedGIFs bearer tokens expire after approximately 1 hour. Instead of manually extracting tokens from DevTools every time they expire, this system automatically:

1. Opens your AdsPower browser profiles (where you're already logged into RedGIFs)
2. Captures the bearer token from network requests
3. Saves fresh tokens to `accounts.json`
4. Proceeds with video uploads

## Prerequisites

### Required Software

- **Python 3.10+** with dependencies installed
- **AdsPower** desktop application running
- **ChromeDriver** (auto-downloaded for Chrome versions 115+)
- **Selenium** library (`pip install selenium`)

### Required Configuration

1. AdsPower profiles with RedGIFs accounts logged in
2. AdsPower Local API enabled (usually `http://127.0.0.1:50325`)
3. API key from AdsPower settings

## Configuration

### Step 1: Create AdsPower Config

Create or edit `adspower_config.json`:

```json
{
  "adspower_api_base": "http://127.0.0.1:50325",
  "api_key": "your_adspower_api_key_here",
  "profiles": [
    {
      "profile_id": "k17q5m3h",
      "account_name": "tasteofmarina"
    },
    {
      "profile_id": "k199f724",
      "account_name": "msdinokiss"
    }
  ]
}
```

**Fields:**
- `adspower_api_base`: AdsPower Local API URL (default: `http://127.0.0.1:50325`)
- `api_key`: Your API key from AdsPower settings
- `profiles`: Array mapping AdsPower profile IDs to account names in `accounts.json`

### Step 2: Get Your Profile IDs

1. Open AdsPower application
2. Right-click on a profile → Copy → Profile ID
3. Add to `adspower_config.json`

### Step 3: Verify Accounts Config

Ensure `accounts.json` has matching account names:

```json
{
  "accounts": [
    {
      "name": "tasteofmarina",
      "enabled": true,
      "token": "will_be_auto_refreshed",
      ...
    }
  ]
}
```

## How It Works

### Token Refresh Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Load adspower_config.json and accounts.json              │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. For each profile in adspower_config.json:                │
│    - Call AdsPower API to start browser                     │
│    - Get WebSocket URL for Selenium connection              │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Selenium connects to browser:                            │
│    - Detect Chrome version from browser                     │
│    - Auto-download matching ChromeDriver if needed          │
│    - Connect via debuggerAddress                            │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Navigate to RedGIFs tab:                                 │
│    - Iterate through all browser tabs                       │
│    - Find tab with redgifs.com URL                          │
│    - Switch to that tab                                     │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Capture network traffic:                                 │
│    - Enable Chrome Performance logging                      │
│    - Wait 5 seconds for API requests                        │
│    - Parse network logs for api.redgifs.com requests        │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. Extract bearer token:                                    │
│    - Look for requests to /v1/me endpoint                   │
│    - Extract Authorization header                           │
│    - Strip "Bearer " prefix                                 │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. Update accounts.json:                                    │
│    - Map profile to account name                            │
│    - Update token field                                     │
│    - Save file                                              │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ 8. Stop browser and continue to next profile                │
└─────────────────────────────────────────────────────────────┘
```

### Technical Details

#### ChromeDriver Version Matching

The script automatically handles different Chrome versions across profiles:

1. Attempts connection with reported Chrome version
2. If version mismatch detected, parses error message for actual version
3. Downloads correct ChromeDriver from `chrome-for-testing` repository
4. Retries connection with matching driver
5. Caches downloaded drivers in `chromedriver_cache/` directory

#### Token Extraction Logic

The script prioritizes user-authenticated tokens over client-only tokens:

1. **Primary**: Tokens from `/v1/me` endpoint (full user auth)
2. **Secondary**: Tokens from `/v2/users/`, `/v2/upload`, `/v2/gifs/` (user-specific)
3. **Fallback**: Any `api.redgifs.com` token (may be read-only)

Read-only tokens have `"scopes":"read"` in JWT payload and will cause 401 errors on upload.

#### Browser Tab Detection

AdsPower profiles often start with proxy check screens. The script:

1. Enumerates all browser tabs/windows
2. Checks URL of each tab
3. Switches to first tab containing `redgifs.com`
4. Falls back to navigating to RedGIFs if no tab found

## Usage

### Standalone Token Refresh

Run token refresh script independently:

```bash
cd src/uploaders/redgifs
python refresh_tokens.py
```

Output:
```
2026-01-28 11:27:28 - Getting token for tasteofmarina (k17q5m3h)
2026-01-28 11:27:31 - Chrome version: 140.0.7339.81
2026-01-28 11:27:31 - Using cached ChromeDriver 140
2026-01-28 11:27:32 - Navigating to upload page to trigger auth token...
2026-01-28 11:27:45 - Captured 1102 network events
2026-01-28 11:27:45 - Found 28 api.redgifs.com requests
2026-01-28 11:27:45 - ✅ Found token from user endpoint: https://api.redgifs.com/v1/me
2026-01-28 11:27:45 - ✅ Token: eyJhbGciOiJSUzI1NiIs...
2026-01-28 11:27:55 - ✅ Updated tasteofmarina
...
2026-01-28 11:28:27 - ✅ accounts.json updated
```

### Integrated with Upload Workflow

Tokens are automatically refreshed when running `main.py`:

```bash
python main.py
```

The uploader will:
1. Auto-refresh tokens from AdsPower (lines 224-231 in main.py)
2. Load refreshed tokens from `accounts.json`
3. Proceed with uploads

If token refresh fails, the uploader continues with existing tokens and logs a warning.

## Troubleshooting

### Error: "Failed to start browser profile"

**Cause**: AdsPower profile not found or API error

**Solutions**:
- Verify profile ID is correct
- Ensure AdsPower application is running
- Check AdsPower Local API is enabled (Settings → Local API)
- Verify API key is correct

### Error: "This version of ChromeDriver only supports Chrome version X"

**Cause**: Version mismatch between installed ChromeDriver and browser

**Solutions**:
- Script should auto-download correct version
- If auto-download fails, manually delete `chromedriver_cache/` folder
- Ensure internet connection is available for downloads
- Check Chrome version is 115+ (older versions not supported)

### Error: "No authorization header found in network traffic"

**Cause**: No API requests captured, or user not logged in

**Solutions**:
- Ensure you're logged into RedGIFs in the AdsPower profile
- Try manually opening RedGIFs upload page in the profile first
- Increase wait time in `refresh_tokens.py` (line 195: `time.sleep(5)`)
- Check browser console for JavaScript errors blocking API calls

### Error: "401 Unauthorized" on uploads

**Cause**: Extracted token is read-only or expired

**Solutions**:
- Check token JWT payload doesn't contain `"scopes":"read"`
- Manually verify token works by testing in Postman/curl
- Ensure RedGIFs account has upload permissions
- Try logging out and back into RedGIFs in AdsPower profile

### Token Not Updating in accounts.json

**Cause**: Account name mismatch between configs

**Solutions**:
- Verify `adspower_config.json` `account_name` exactly matches `accounts.json` `name`
- Check for typos, case sensitivity matters
- Ensure `accounts.json` is not read-only

## Files

| File | Purpose |
|------|---------|
| `refresh_tokens.py` | Main token refresh script |
| `adspower_config.json` | AdsPower API credentials and profile mappings |
| `accounts.json` | Account configurations (tokens updated by refresh script) |
| `chromedriver_cache/` | Auto-downloaded ChromeDriver binaries |
| `main.py` | Upload script with integrated token refresh (lines 224-231) |

## Security Notes

- `accounts.json` and `adspower_config.json` contain sensitive credentials
- Both files are in `.gitignore` to prevent accidental commits
- Never share these files or commit to version control
- Store API keys securely
- Tokens are bearer tokens - anyone with token can act as that account

## Advanced Configuration

### Disable Auto-Refresh

To disable automatic token refresh in `main.py`, comment out lines 224-231:

```python
# # AUTO REFRESH TOKENS FIRST
# logger.info("Refreshing bearer tokens from AdsPower...")
# try:
#     from refresh_tokens import main as refresh_tokens_main
#     refresh_tokens_main()
# except Exception as e:
#     logger.error(f"Token refresh failed: {e}")
#     logger.warning("Continuing with existing tokens...")
```

### Custom Wait Time

Adjust network capture wait time in `refresh_tokens.py`:

```python
# Line 195 (approximately)
time.sleep(5)  # Increase to 10 for slower connections
```

### Custom Token Endpoint Priority

Modify endpoint priority in `refresh_tokens.py` (lines 206-207):

```python
# Prioritize different endpoints
user_endpoints = ['/v1/me', '/v2/users/', '/v2/upload', '/v2/gifs/']
```

## Integration with Main Upload Flow

The token refresh is integrated into `main.py` at lines 224-231:

```python
# AUTO REFRESH TOKENS FIRST
logger.info("Refreshing bearer tokens from AdsPower...")
try:
    from refresh_tokens import main as refresh_tokens_main
    refresh_tokens_main()
except Exception as e:
    logger.error(f"Token refresh failed: {e}")
    logger.warning("Continuing with existing tokens...")
```

This ensures:
- Tokens are fresh before every upload session
- Upload failures due to expired tokens are minimized
- System gracefully falls back to existing tokens if refresh fails

## Benefits

- **No manual token extraction**: Fully automated
- **Multi-account support**: Handles multiple profiles in one run
- **Version-agnostic**: Auto-downloads correct ChromeDriver versions
- **Reliable**: Captures from user-authenticated endpoints
- **Error resilient**: Falls back gracefully if refresh fails
- **Time-saving**: Eliminates hourly manual token updates

## Limitations

- Requires AdsPower (paid software for browser profile management)
- Chrome version must be 115+ for auto-download feature
- Profiles must have RedGIFs already logged in
- Internet connection required for ChromeDriver downloads
- ~10 seconds per profile for token refresh

## Future Improvements

Potential enhancements:
- Support for other browser profile managers (MultiLogin, GoLogin)
- Parallel profile processing for faster multi-account refresh
- Token validity checking before refresh
- Scheduled token refresh independent of upload runs
- Support for Chrome versions < 115
