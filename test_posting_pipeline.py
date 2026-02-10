"""
End-to-end test of the auto-posting pipeline.
Tests: vision analysis → sub matching → title gen → spoof → post

Uses:
- AdsPower profile k19m2kqc (already active)
- Content from testvids/ folder
- Posts 1-2 files to 1-2 low-traffic subs to verify the pipeline
"""
import os
import sys
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Setup paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from core.vision_matcher import (
    analyze_image, load_profiles, match_content, random_select_subs,
    scan_content_folder, content_file_hash
)
from core.title_generator import generate_titles_batch
from core.spoofer import spoof_file
from core.humanizer import Humanizer
from core.ban_detector import check_post_result, check_account_health, BanStatus
from core.post_history import add_post, get_posted_subs, get_banned_subs

# ===== CONFIG =====
ADSPOWER_API_BASE = "http://127.0.0.1:50325"
ADSPOWER_API_KEY = "caeba572837f3da2adc39f45f0751da9"
PROFILE_ID = "k19m2kqc"
CONTENT_FOLDER = os.path.join(os.path.dirname(__file__), "testvids")
SUBS_PER_FILE = 2       # Only post to 2 subs per file for testing
MAX_FILES = 1            # Only test 1 file
SPOOF_ENABLED = True

# API keys from env
CLAUDE_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GROK_KEY = os.environ.get("GROK_API_KEY", "")


def main():
    if not CLAUDE_KEY:
        logger.error("Set ANTHROPIC_API_KEY env var")
        return
    if not GROK_KEY:
        logger.error("Set GROK_API_KEY env var")
        return

    # 1. Scan content folder
    logger.info(f"Scanning: {CONTENT_FOLDER}")
    files = scan_content_folder(CONTENT_FOLDER)
    if not files:
        logger.error("No content files found")
        return
    logger.info(f"Found {len(files)} files, using first {MAX_FILES}")
    files = files[:MAX_FILES]

    # 2. Load sub profiles and tiers
    logger.info("Loading sub profiles and tiers...")
    sub_profiles, sub_tiers, sub_data = load_profiles()
    green_count = sum(1 for v in sub_tiers.values() if v.get("tier") == "GREEN")
    logger.info(f"Loaded {len(sub_profiles)} profiles, {green_count} GREEN subs")

    # 3. Analyze each file with Claude Vision
    posting_plan = []
    for file_path in files:
        fname = os.path.basename(file_path)
        logger.info(f"\nAnalyzing: {fname}")

        vision = analyze_image(file_path, CLAUDE_KEY)
        if not vision:
            logger.error(f"  Vision analysis failed for {fname}")
            continue

        tags = vision.get("tags", [])
        logger.info(f"  Tags: {', '.join(tags[:8])}")
        logger.info(f"  Body: {vision.get('body_type', '?')}, Action: {vision.get('action', '?')}")

        # 4. Match to subs
        file_hash = content_file_hash(file_path)
        posted = get_posted_subs(file_hash)
        banned = get_banned_subs(PROFILE_ID)
        excluded = posted | banned

        all_matches = match_content(vision, sub_profiles, sub_tiers, excluded_subs=excluded,
                                     sub_data=sub_data, max_subscribers=50000)
        selected = random_select_subs(all_matches, count=SUBS_PER_FILE)

        if not selected:
            logger.warning(f"  No matching subs found for {fname}")
            continue

        for sub_name, score, theme, sub_tags in selected:
            logger.info(f"  Matched: r/{sub_name} (score: {score:.0f}, theme: {theme})")
            posting_plan.append({
                "file_path": file_path,
                "file_hash": file_hash,
                "file_name": fname,
                "sub_name": sub_name,
                "score": score,
                "sub_theme": theme,
                "content_tags": tags,
                "body_type": vision.get("body_type", ""),
                "action": vision.get("action", ""),
                "setting": vision.get("setting", ""),
            })

    if not posting_plan:
        logger.error("No posts planned. Exiting.")
        return

    # 5. Generate titles
    logger.info(f"\nGenerating titles for {len(posting_plan)} posts...")
    pairings = [{
        "sub_name": p["sub_name"],
        "sub_theme": p["sub_theme"],
        "content_tags": p["content_tags"],
        "body_type": p["body_type"],
        "action": p["action"],
        "setting": p["setting"],
    } for p in posting_plan]

    titles = generate_titles_batch(pairings, GROK_KEY)
    for i, title in enumerate(titles):
        if title and i < len(posting_plan):
            posting_plan[i]["title"] = title
            logger.info(f"  r/{posting_plan[i]['sub_name']}: \"{title}\"")

    # Fill blanks
    for item in posting_plan:
        if not item.get("title"):
            item["title"] = "Testing posting pipeline"

    # 6. Show plan and confirm
    print("\n" + "=" * 60)
    print("POSTING PLAN")
    print("=" * 60)
    for item in posting_plan:
        print(f"  {item['file_name']} → r/{item['sub_name']}")
        print(f"    Title: \"{item['title']}\"")
        print(f"    Score: {item['score']:.0f}")
    print("=" * 60)

    confirm = input("\nProceed with posting? (y/n): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    # 7. Connect to AdsPower browser
    import requests as req
    logger.info(f"\nConnecting to AdsPower profile {PROFILE_ID}...")

    # Check if already active
    check_url = f"{ADSPOWER_API_BASE}/api/v1/browser/active?user_id={PROFILE_ID}&api_key={ADSPOWER_API_KEY}"
    resp = req.get(check_url, timeout=10).json()

    if resp.get("code") == 0 and resp["data"].get("status") == "Active":
        ws_endpoint = resp["data"]["ws"]["puppeteer"]
        logger.info(f"Profile already active, connecting...")
    else:
        start_url = f"{ADSPOWER_API_BASE}/api/v1/browser/start?user_id={PROFILE_ID}&api_key={ADSPOWER_API_KEY}"
        resp = req.get(start_url, timeout=60).json()
        if resp.get("code") != 0:
            logger.error(f"Failed to start browser: {resp}")
            return
        ws_endpoint = resp["data"]["ws"]["puppeteer"]
        logger.info("Browser started")

    from playwright.sync_api import sync_playwright
    from uploaders.reddit.reddit_poster_playwright import post_file_to_subreddit

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws_endpoint)
        contexts = browser.contexts
        if contexts:
            context = contexts[0]
            pages = context.pages
            page = pages[0] if pages else context.new_page()
        else:
            context = browser.new_context()
            page = context.new_page()

        # 8. Check account health
        logger.info("Checking account health...")
        status, detail = check_account_health(page)
        logger.info(f"Account status: {status} — {detail}")
        if status == BanStatus.ACCOUNT_SUSPENDED:
            logger.error("Account is suspended!")
            return

        # 9. Setup humanizer (testing delays)
        humanizer = Humanizer(page, {"daily_limit": 20})

        # 10. Warm session
        logger.info("Warming session...")
        humanizer.warm_session()

        # 11. Post each item
        success = 0
        failed = 0

        for i, item in enumerate(posting_plan):
            sub = item["sub_name"]
            title = item["title"]
            file_path = item["file_path"]

            logger.info(f"\n--- Post {i+1}/{len(posting_plan)} ---")
            logger.info(f"Sub: r/{sub}")
            logger.info(f"Title: {title}")
            logger.info(f"File: {item['file_name']}")

            # Spoof
            spoofed_path = None
            upload_path = file_path
            if SPOOF_ENABLED:
                logger.info("Spoofing file...")
                spoofed_path, spoof_result = spoof_file(file_path)
                if spoofed_path:
                    upload_path = spoofed_path
                    logger.info(f"Spoofed: {', '.join(spoof_result.get('applied', []))}")
                else:
                    logger.warning(f"Spoof failed: {spoof_result.get('error')} — using original")

            # Pre-post browse
            logger.info("Pre-post browsing...")
            humanizer.pre_post_browse(sub)

            # Post
            try:
                logger.info("Posting...")
                ok = post_file_to_subreddit(
                    page=page,
                    subreddit=sub,
                    title=title,
                    file_path=upload_path,
                    mark_nsfw=True,
                    humanizer=humanizer,
                )

                # Check result
                time.sleep(3)  # Let page settle
                post_status, detail = check_post_result(page)

                if post_status == BanStatus.OK:
                    success += 1
                    add_post(PROFILE_ID, item["file_hash"], sub, title, file_path, "success", detail)
                    logger.info(f"SUCCESS: {detail}")
                elif post_status == BanStatus.SUB_BANNED:
                    failed += 1
                    add_post(PROFILE_ID, item["file_hash"], sub, title, file_path, "banned", error=detail)
                    logger.warning(f"BANNED from r/{sub}: {detail}")
                elif post_status == BanStatus.RATE_LIMITED:
                    failed += 1
                    add_post(PROFILE_ID, item["file_hash"], sub, title, file_path, "rate_limited", error=detail)
                    logger.warning(f"RATE LIMITED: {detail}")
                else:
                    failed += 1
                    add_post(PROFILE_ID, item["file_hash"], sub, title, file_path, "failed", error=detail)
                    logger.warning(f"FAILED: {detail}")

            except Exception as e:
                failed += 1
                logger.error(f"ERROR: {e}")
            finally:
                if spoofed_path and os.path.exists(spoofed_path):
                    os.remove(spoofed_path)

            # Wait between posts
            if i < len(posting_plan) - 1:
                humanizer.wait_between_posts()

    # 12. Summary
    print("\n" + "=" * 60)
    print(f"DONE: {success} success, {failed} failed out of {len(posting_plan)} planned")
    print("=" * 60)


if __name__ == "__main__":
    main()
