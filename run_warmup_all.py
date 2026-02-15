"""Launch warmup for all 4 accounts concurrently.

Usage: python run_warmup_all.py [--minutes 30] [--max-comments 5]
"""
import sys
import json
import logging
import time
import threading
import argparse
from datetime import datetime

sys.path.insert(0, "src")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("warmup_all")

import requests as _requests

ADSPOWER_API = "http://localhost:50325"
API_KEY = "caeba572837f3da2adc39f45f0751da9"

with open("config/account_profiles.json", encoding="utf-8") as f:
    ALL_PROFILES = json.load(f)["profiles"]

with open("config/api_keys.json", encoding="utf-8") as f:
    API_KEYS = json.load(f)

GROK_KEY = API_KEYS.get("grok_api_key", "")


def resolve_age(profile_data):
    """Calculate account age in days from profile data."""
    account_created_at = profile_data.get("created_at")
    raw_age = (profile_data.get("reddit_account", {}) or {}).get("age_days")
    age_days = None
    try:
        if raw_age is not None:
            age_days = max(0, int(raw_age))
    except Exception:
        age_days = None

    if age_days is None and account_created_at:
        try:
            dt = datetime.fromisoformat(str(account_created_at).replace("Z", "+00:00"))
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            age_days = max(0, (now - dt).days)
        except Exception:
            age_days = None
    return age_days, account_created_at


def run_warmup_account(persona_key, profile_data, session_minutes, max_comments):
    """Run warmup for a single account (designed to run in a thread)."""
    ads_id = profile_data.get("adspower_id", "")
    display_name = profile_data.get("display_name", persona_key)
    persona = profile_data.get("persona", {})
    attributes = profile_data.get("attributes", {})
    age_days, created_at = resolve_age(profile_data)

    tag = f"[{display_name}/{ads_id}]"
    logger.info(f"{tag} Starting AdsPower browser...")

    try:
        resp = _requests.get(
            f"{ADSPOWER_API}/api/v1/browser/start"
            f"?user_id={ads_id}&api_key={API_KEY}",
            timeout=60,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.error(f"{tag} AdsPower error: {data}")
            return
        ws_endpoint = data.get("data", {}).get("ws", {}).get("puppeteer")
        if not ws_endpoint:
            logger.error(f"{tag} No CDP endpoint returned")
            return
        logger.info(f"{tag} Browser started, connecting Playwright...")
    except Exception as e:
        logger.error(f"{tag} Failed to start browser: {e}")
        return

    from playwright.sync_api import sync_playwright
    from core.account_warmer import AccountWarmer

    try:
        with sync_playwright() as p:
            browser = None
            for attempt in range(5):
                try:
                    browser = p.chromium.connect_over_cdp(ws_endpoint)
                    break
                except Exception as cdp_err:
                    if attempt < 4:
                        wait = 2 * (attempt + 1)
                        logger.info(f"{tag} CDP not ready, retry in {wait}s...")
                        time.sleep(wait)
                    else:
                        raise cdp_err

            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            all_pages = list(ctx.pages)

            # Pick Reddit tab or first tab
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

            # Close stale tabs
            closed = 0
            for pg in all_pages:
                if pg != page:
                    try:
                        pg.close()
                        closed += 1
                    except Exception:
                        pass
            tab_msg = f"{tag} Playwright connected"
            if closed:
                tab_msg += f" (closed {closed} stale tabs)"
            logger.info(tab_msg)

            warmer = AccountWarmer(
                ads_id, page,
                persona=persona,
                attributes=attributes,
                grok_api_key=GROK_KEY,
                account_age_days=age_days,
                account_created_at=created_at,
            )

            day = warmer.get_day()
            logger.info(
                f"{tag} Day {day}, session={session_minutes}min, "
                f"max_comments={max_comments}, {len(warmer.general_subs)} subs"
            )

            stats = warmer.run_daily_warmup(
                session_minutes=session_minutes,
                max_comments=max_comments,
            )

            if stats:
                logger.info(
                    f"{tag} COMPLETE — "
                    f"Sessions: {stats['sessions']}, "
                    f"Scrolls: {stats['scrolls']}, "
                    f"Upvotes: {stats['upvotes']}, "
                    f"Downvotes: {stats['downvotes']}, "
                    f"Comments: {stats['comments']}, "
                    f"Joins: {stats['joins']}, "
                    f"Posts clicked: {stats['posts_clicked']}"
                )
            else:
                logger.warning(f"{tag} Warmup returned no stats")

    except Exception as e:
        logger.error(f"{tag} Warmup failed: {e}", exc_info=True)


def main():
    parser = argparse.ArgumentParser(description="Run warmup for all accounts")
    parser.add_argument("--minutes", type=int, default=30, help="Session length in minutes")
    parser.add_argument("--max-comments", type=int, default=5, help="Max comments per session")
    args = parser.parse_args()

    accounts = list(ALL_PROFILES.items())
    logger.info(f"Launching warmup for {len(accounts)} accounts "
                f"({args.minutes}min, max {args.max_comments} comments each)")

    threads = []
    for persona_key, profile_data in accounts:
        t = threading.Thread(
            target=run_warmup_account,
            args=(persona_key, profile_data, args.minutes, args.max_comments),
            name=persona_key,
            daemon=True,
        )
        threads.append(t)
        t.start()
        time.sleep(3)  # Stagger browser launches by 3s

    # Wait for all to finish
    for t in threads:
        t.join()

    logger.info("=== ALL WARMUPS COMPLETE ===")


if __name__ == "__main__":
    main()
