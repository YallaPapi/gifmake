"""Run all account warmups — 1 thread per proxy group, 1 account at a time per proxy.

Safety architecture:
  - 4 proxy groups (P, G, F, 4u), each with a shared IP
  - 1 thread per group → only 1 account per proxy is ever active
  - Proxy IP rotated between accounts within the same group
  - Ban check before each warmup; permabans logged and auto-skipped

Pulls all reddit bot profiles from AdsPower API automatically.
"""
import sys
import json
import logging
import time
import os
import random
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Ensure working directory is the script's directory (critical for scheduled tasks)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "src")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)

ADSPOWER_API = "http://localhost:50325"
API_KEY = "caeba572837f3da2adc39f45f0751da9"

# Load Grok key from config/api_keys.json (not hardcoded — GitHub push protection)
def _load_grok_key():
    try:
        with open(os.path.join("config", "api_keys.json")) as f:
            return json.load(f).get("grok_api_key", "")
    except Exception:
        return os.environ.get("XAI_API_KEY", "")

GROK_KEY = _load_grok_key()

# Proxy group prefixes that identify reddit bot profiles in AdsPower
PROXY_PREFIXES = ("P ", "G ", "F ", "4u ")

BAN_LOG_PATH = os.path.join("data", "warmup_bans.json")
SCHEDULE_CONFIG_PATH = os.path.join("config", "schedule_config.json")


def close_all_browsers():
    """Close ALL open AdsPower browsers. Called at start and end of script."""
    log = logging.getLogger("cleanup")
    try:
        resp = requests.get(
            f"{ADSPOWER_API}/api/v1/browser/local-active",
            params={"api_key": API_KEY}, timeout=15,
        )
        data = resp.json()
        active = data.get("data", {}).get("list", [])
        if not active:
            log.info("No open browsers")
            return
        log.info(f"Closing {len(active)} open browsers...")
        for b in active:
            uid = b.get("user_id", "")
            try:
                requests.get(
                    f"{ADSPOWER_API}/api/v1/browser/stop",
                    params={"user_id": uid, "api_key": API_KEY}, timeout=15,
                )
            except Exception:
                pass
        time.sleep(3)
        log.info(f"Closed {len(active)} browsers")
    except Exception as e:
        log.warning(f"Browser cleanup failed: {e}")


# -- Config loaders --

def load_proxy_rotation_urls():
    """Load proxy rotation URLs from schedule_config.json.

    Returns dict: {"P": "https://...", "G": "https://...", ...}
    """
    try:
        with open(SCHEDULE_CONFIG_PATH) as f:
            cfg = json.load(f)
        groups = cfg.get("proxy_groups", {})
        return {k: v.get("rotation_url", "") for k, v in groups.items()}
    except Exception:
        return {}


def load_ban_log():
    """Load the ban log. Returns dict {adspower_id: {status, detected, ...}}."""
    if os.path.exists(BAN_LOG_PATH):
        with open(BAN_LOG_PATH, "r") as f:
            return json.load(f)
    return {}


def save_ban_log(ban_log):
    """Save ban log to disk (thread-safe via atomic write)."""
    os.makedirs(os.path.dirname(BAN_LOG_PATH), exist_ok=True)
    tmp = BAN_LOG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(ban_log, f, indent=2)
    os.replace(tmp, BAN_LOG_PATH)


# -- AdsPower account discovery --

def discover_accounts():
    """Query AdsPower API for all reddit bot profiles.

    Returns dict: {"P": [acc, ...], "G": [...], "F": [...], "4u": [...]}
    where each acc = {name, username, adspower_id, proxy_group}
    """
    by_group = {}
    page_num = 1
    total = 0
    while True:
        # Retry loop for AdsPower rate limiting
        data = None
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"{ADSPOWER_API}/api/v1/user/list",
                    params={"api_key": API_KEY, "page": page_num, "page_size": 100},
                    timeout=30,
                )
                data = resp.json()
            except Exception as e:
                logging.getLogger("discover").warning(f"API request failed: {e}")
                time.sleep(2)
                continue
            if data.get("code") == 0:
                break
            logging.getLogger("discover").warning(f"API rate limit (attempt {attempt+1}): {data}")
            time.sleep(2)
        if not data or data.get("code") != 0:
            logging.getLogger("discover").error(f"API error after retries: {data}")
            break

        items = data.get("data", {}).get("list", [])
        if not items:
            break

        for item in items:
            name = (item.get("name") or "").strip()
            for prefix in PROXY_PREFIXES:
                if name.startswith(prefix):
                    grp = prefix.strip()
                    username = name[len(prefix):].strip()
                    by_group.setdefault(grp, []).append({
                        "name": name,
                        "username": username.lower(),
                        "adspower_id": item["user_id"],
                        "proxy_group": grp,
                    })
                    total += 1
                    break

        page_num += 1
        time.sleep(1)  # Avoid AdsPower API rate limiting between pages

    logging.getLogger("discover").info(f"Found {total} accounts across {len(by_group)} groups")
    return by_group


# -- Profile data loader --

def load_profile_data(username):
    """Load persona/attributes from account_profiles.json if available."""
    try:
        with open("config/account_profiles.json") as f:
            profiles = json.load(f).get("profiles", {})
        profile = profiles.get(username)
        if not profile:
            return {}, {}, None, None

        persona = profile.get("persona", {})
        attributes = profile.get("attributes", {})

        raw_age = (profile.get("reddit_account", {}) or {}).get("age_days")
        created_at = profile.get("created_at")
        age_days = None
        try:
            if raw_age is not None:
                age_days = max(0, int(raw_age))
        except Exception:
            pass
        if age_days is None and created_at:
            try:
                dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
                age_days = max(0, (now - dt).days)
            except Exception:
                pass

        return persona, attributes, age_days, created_at
    except Exception:
        return {}, {}, None, None


def rotate_proxy(group, rotation_urls):
    """Hit the proxy rotation URL to get a fresh IP for this group.

    Retries up to 3 times on failure. This is critical — without rotation,
    consecutive accounts share the same IP and get linked together.
    """
    url = rotation_urls.get(group, "")
    if not url:
        return
    log = logging.getLogger(f"proxy:{group}")
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                log.info(f"Rotated proxy IP (status {resp.status_code})")
                time.sleep(5)  # Wait for new IP to propagate
                return
            log.warning(f"Rotation returned {resp.status_code}, retry {attempt+1}/3")
        except Exception as e:
            log.warning(f"Proxy rotation attempt {attempt+1}/3 failed: {e}")
        time.sleep(3)
    log.error(f"PROXY ROTATION FAILED after 3 attempts for group {group}")


# -- Single account warmup --

def warmup_one(account, ban_log, rotation_urls):
    """Run warmup for a single account. Returns result dict."""
    profile_key = account["username"]
    adspower_id = account["adspower_id"]
    grp = account["proxy_group"]
    log = logging.getLogger(f"{grp}:{profile_key}")

    # Skip if permanently banned
    prev = ban_log.get(adspower_id, {})
    if prev.get("status") == "permaban":
        log.info(f"SKIP — permabanned ({prev.get('detected', '?')})")
        return {"profile": profile_key, "status": "skip_banned",
                "adspower_id": adspower_id, "proxy_group": grp}

    # Load optional profile data
    persona_data, attributes, age_days, created_at = load_profile_data(profile_key)
    log.info(f"profile={'yes' if persona_data else 'no'}, age_days={age_days}")

    # Start AdsPower browser
    log.info(f"Starting browser {adspower_id}...")
    try:
        resp = requests.get(
            f"{ADSPOWER_API}/api/v1/browser/start?user_id={adspower_id}&api_key={API_KEY}",
            timeout=60,
        )
        data = resp.json()
    except Exception as e:
        log.error(f"Failed to start browser: {e}")
        return {"profile": profile_key, "status": "failed",
                "adspower_id": adspower_id, "error": str(e)}

    if data.get("code") != 0:
        log.error(f"Browser start failed: {data}")
        return {"profile": profile_key, "status": "failed",
                "adspower_id": adspower_id, "error": str(data)}
    ws_endpoint = data["data"]["ws"]["puppeteer"]

    from playwright.sync_api import sync_playwright
    from core.account_warmer import AccountWarmer
    from core.ban_detector import check_account_health, BanStatus

    try:
        with sync_playwright() as p:
            # CDP connection with retries
            browser = None
            for attempt in range(5):
                try:
                    browser = p.chromium.connect_over_cdp(ws_endpoint)
                    break
                except Exception as e:
                    if attempt < 4:
                        wait = 2 * (attempt + 1)
                        log.warning(f"CDP retry in {wait}s... ({e})")
                        time.sleep(wait)
                    else:
                        raise

            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            all_pages = list(ctx.pages) if ctx else []

            # Pick existing Reddit tab or first tab
            page = None
            for pg in all_pages:
                try:
                    url = pg.url or ""
                except Exception:
                    url = ""
                if "reddit.com" in url:
                    page = pg
                    break
            if not page:
                page = all_pages[0] if all_pages else ctx.new_page()

            # Close stale extra tabs
            for pg in all_pages:
                if pg != page:
                    try:
                        pg.close()
                    except Exception:
                        pass

            # === BAN CHECK ===
            log.info("Checking account health...")
            status, detail = check_account_health(page)

            if status == BanStatus.ACCOUNT_SUSPENDED:
                log.warning(f"BANNED: {detail}")
                ban_log[adspower_id] = {
                    "status": "permaban", "username": profile_key,
                    "proxy_group": grp, "detail": detail,
                    "detected": datetime.now().isoformat(),
                }
                save_ban_log(ban_log)
                return {"profile": profile_key, "status": "banned",
                        "adspower_id": adspower_id, "detail": detail}

            if status == BanStatus.SHADOW_BANNED:
                log.warning(f"SHADOW BANNED: {detail}")
                ban_log[adspower_id] = {
                    "status": "shadowban", "username": profile_key,
                    "proxy_group": grp, "detail": detail,
                    "detected": datetime.now().isoformat(),
                }
                save_ban_log(ban_log)
                return {"profile": profile_key, "status": "shadowbanned",
                        "adspower_id": adspower_id, "detail": detail}

            if status == BanStatus.UNKNOWN_ERROR:
                if "not logged in" in detail:
                    log.warning("NOT LOGGED IN — skipping")
                    return {"profile": profile_key, "status": "not_logged_in",
                            "adspower_id": adspower_id, "proxy_group": grp}
                # Detect browser/page crash — AdsPower profile is broken
                crash_phrases = [
                    "browser has been closed",
                    "target page",
                    "connection closed",
                    "target closed",
                    "session closed",
                    "page has been closed",
                ]
                if any(phrase in detail.lower() for phrase in crash_phrases):
                    log.warning(f"BROWSER CRASHED: {detail}")
                    return {"profile": profile_key, "status": "browser_crashed",
                            "adspower_id": adspower_id, "proxy_group": grp,
                            "error": detail}
                log.warning(f"Health check issue: {detail} — proceeding anyway")

            log.info(f"Account healthy: {detail}")

            # === WARMUP ===
            warmer = AccountWarmer(
                adspower_id, page,
                persona=persona_data or None,
                attributes=attributes or None,
                grok_api_key=GROK_KEY,
                account_age_days=age_days,
                account_created_at=created_at,
                username=profile_key,
            )

            day = warmer.get_day()
            log.info(f"Day {day}, {len(warmer.general_subs)} subs")

            stats = warmer.run_daily_warmup()

            log.info(f"DONE — {stats['comments']}cmt, {stats['upvotes']}up, "
                     f"{stats['joins']}join, {stats['total_sec']//60}m")

            # Clear non-permaban entries on success
            if adspower_id in ban_log and ban_log[adspower_id].get("status") != "permaban":
                del ban_log[adspower_id]
                save_ban_log(ban_log)

            return {"profile": profile_key, "status": "success",
                    "adspower_id": adspower_id, "proxy_group": grp,
                    "stats": stats}

    except Exception as e:
        err_str = str(e).lower()
        crash_phrases = ["browser has been closed", "target page", "connection closed",
                         "target closed", "session closed", "page has been closed"]
        if any(phrase in err_str for phrase in crash_phrases):
            log.warning(f"BROWSER CRASHED during warmup: {e}")
            return {"profile": profile_key, "status": "browser_crashed",
                    "adspower_id": adspower_id, "proxy_group": grp, "error": str(e)}
        log.error(f"Warmup error: {e}", exc_info=True)
        return {"profile": profile_key, "status": "error",
                "adspower_id": adspower_id, "proxy_group": grp, "error": str(e)}
    finally:
        # 1. Close the browser FIRST
        try:
            requests.get(
                f"{ADSPOWER_API}/api/v1/browser/stop?user_id={adspower_id}&api_key={API_KEY}",
                timeout=15,
            )
            log.info("Browser stopped")
        except Exception as e:
            log.warning(f"Failed to stop browser: {e}")

        # 2. Rotate proxy IP AFTER browser is closed — guaranteed every time
        time.sleep(2)  # Let browser fully die
        rotate_proxy(grp, rotation_urls)


# -- Per-group sequential runner --

def run_group(group, accounts, ban_log, rotation_urls):
    """Process all accounts in one proxy group SEQUENTIALLY.

    This is the key safety guarantee: only 1 account per proxy is active at a time.
    Between accounts, the proxy IP is rotated.

    Rotation order (guaranteed in warmup_one's finally block):
      1. Close browser (AdsPower stop API)
      2. Rotate proxy IP (hit rotation URL)
      3. Next account opens browser on fresh IP
    """
    log = logging.getLogger(f"group:{group}")
    results = []

    # Shuffle order so accounts don't always run in the same sequence
    shuffled = list(accounts)
    random.shuffle(shuffled)

    active = [a for a in shuffled
              if ban_log.get(a["adspower_id"], {}).get("status") != "permaban"]
    log.info(f"Starting {group}: {len(active)} accounts ({len(accounts) - len(active)} permabanned)")

    for i, acc in enumerate(active):
        result = warmup_one(acc, ban_log, rotation_urls)
        results.append(result)

        status = result.get("status")
        if status == "banned":
            log.warning(f"{acc['username']}: BANNED — continuing with remaining accounts")

    log.info(f"Group {group} done: {len(results)} accounts processed")
    return results


# -- Main --

if __name__ == "__main__":
    logger = logging.getLogger("main")
    logger.info("=" * 60)
    logger.info("WARMUP ALL ACCOUNTS")
    logger.info("=" * 60)

    # Close any leftover browsers from previous runs
    close_all_browsers()

    # Discover accounts grouped by proxy
    accounts_by_group = discover_accounts()
    for grp, accs in sorted(accounts_by_group.items()):
        logger.info(f"  {grp}: {len(accs)} accounts")

    # Load configs
    ban_log = load_ban_log()
    rotation_urls = load_proxy_rotation_urls()

    permabanned = sum(1 for v in ban_log.values() if v.get("status") == "permaban")
    if permabanned:
        logger.info(f"Skipping {permabanned} previously permabanned accounts")

    logger.info(f"Rotation URLs loaded for: {', '.join(rotation_urls.keys())}")
    logger.info("Architecture: 1 thread per proxy group, sequential within group")
    logger.info("")

    # Launch 1 thread per proxy group — max 4 concurrent (one per group)
    all_results = []
    with ThreadPoolExecutor(max_workers=len(accounts_by_group)) as pool:
        futures = {}
        for grp, accs in accounts_by_group.items():
            future = pool.submit(run_group, grp, accs, ban_log, rotation_urls)
            futures[future] = grp

        for future in as_completed(futures):
            grp = futures[future]
            try:
                group_results = future.result()
                all_results.extend(group_results)
            except Exception as e:
                logger.error(f"Group {grp} crashed: {e}")

    # Final summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("ALL WARMUPS FINISHED")
    logger.info("=" * 60)

    by_status = {}
    for r in all_results:
        s = r.get("status", "unknown")
        by_status.setdefault(s, []).append(r["profile"])

    for status, names in sorted(by_status.items()):
        logger.info(f"  {status}: {len(names)}")
        if status in ("banned", "shadowbanned", "not_logged_in", "browser_crashed", "error"):
            for n in names:
                logger.info(f"    - {n}")

    # Final ban log save
    save_ban_log(ban_log)
    total_banned = sum(1 for v in ban_log.values() if v.get("status") == "permaban")
    total_accounts = sum(len(a) for a in accounts_by_group.values())
    logger.info(f"Total: {total_accounts} accounts, {total_banned} permabanned")

    # Save daily results JSON for reporting
    report_dir = os.path.join("data", "warmup_reports")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    report = {
        "timestamp": datetime.now().isoformat(),
        "total_accounts": total_accounts,
        "total_permabanned": total_banned,
        "by_status": {s: names for s, names in by_status.items()},
        "results": all_results,
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info(f"Report saved: {report_path}")

    # Run karma tracking report
    try:
        from core.karma_tracker import run_karma_report
        run_karma_report(all_results, ban_log)
    except Exception as e:
        logger.error(f"Karma report failed: {e}", exc_info=True)

    # Close ALL browsers at the end — leave nothing open
    close_all_browsers()
