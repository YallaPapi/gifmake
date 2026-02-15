"""
Headless queue runner for Reddit automation.
Runs parallel workers, each with its own mobile proxy.
Processes accounts sequentially per worker: warmup → post → close → rotate → next.

Usage:
    python scripts/queue_runner.py                # Run all accounts
    python scripts/queue_runner.py --workers 2    # Only 2 proxies
    python scripts/queue_runner.py --dry-run      # Print plan only
    python scripts/queue_runner.py --accounts midnight_mae,honeytonedhaze
    python scripts/queue_runner.py --warmup-only  # Skip posting for all accounts
"""

import argparse
import json
import logging
import os
import random
import sys
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from core.vision_matcher import (
    analyze_image, load_profiles, match_content, random_select_subs,
    content_file_hash,
)
from core.title_generator import generate_titles_batch
from core.post_history import (
    add_post, get_posted_subs, get_banned_subs, get_posts_today,
    add_ban,
)
from core.spoofer import spoof_file
from core.account_warmer import AccountWarmer
from core.content_bank import ContentBank
from core.ban_detector import BanStatus, check_post_result
from core.humanizer import Humanizer
from processors.account_profile import ProfileManager

logger = logging.getLogger("queue_runner")

# ── Global shared data (loaded once, read-only) ──────────────────────

SUB_PROFILES = None
SUB_TIERS = None
SUB_DATA = None
API_KEYS = {}
QUEUE_CONFIG = {}


def load_global_data():
    """Load sub profiles, tiers, and API keys at startup."""
    global SUB_PROFILES, SUB_TIERS, SUB_DATA, API_KEYS, QUEUE_CONFIG

    logger.info("Loading subreddit profiles and tiers...")
    SUB_PROFILES, SUB_TIERS, SUB_DATA = load_profiles()
    logger.info(
        f"Loaded {len(SUB_PROFILES)} profiles, "
        f"{len(SUB_TIERS)} tiers, "
        f"{len(SUB_DATA)} sub data entries"
    )

    keys_path = PROJECT_ROOT / "config" / "api_keys.json"
    if keys_path.exists():
        with open(keys_path, encoding="utf-8-sig") as f:
            API_KEYS = json.load(f)

    config_path = PROJECT_ROOT / "config" / "queue_config.json"
    with open(config_path, encoding="utf-8") as f:
        QUEUE_CONFIG = json.load(f)


# ── Proxy rotation ───────────────────────────────────────────────────

def rotate_proxy(proxy_config):
    """Hit the proxy rotation URL and wait for new IP."""
    url = proxy_config.get("rotation_url", "")
    wait = proxy_config.get("wait_after_rotate_sec", 10)
    if not url:
        logger.debug("No rotation URL configured, skipping")
        return
    try:
        resp = requests.get(url, timeout=15)
        logger.info(f"Proxy rotated (status {resp.status_code}), waiting {wait}s")
        time.sleep(wait)
    except Exception as e:
        logger.warning(f"Proxy rotation failed: {e}, continuing anyway")
        time.sleep(wait)


# ── AdsPower browser management ──────────────────────────────────────

def start_adspower(adspower_id, config):
    """Start an AdsPower browser profile. Returns ws endpoint or None."""
    api_base = config.get("adspower_api_base", "http://127.0.0.1:50325")
    api_key = config.get("adspower_api_key", "")
    url = f"{api_base}/api/v1/browser/start?user_id={adspower_id}&api_key={api_key}"
    try:
        resp = requests.get(url, timeout=60)
        data = resp.json()
        if data.get("code") != 0:
            logger.error(f"Browser start failed: {data.get('msg', data)}")
            return None
        ws = data["data"]["ws"]["puppeteer"]
        logger.info(f"AdsPower started: {adspower_id}")
        return ws
    except Exception as e:
        logger.error(f"AdsPower start error: {e}")
        return None


def stop_adspower(adspower_id, config):
    """Stop an AdsPower browser profile."""
    api_base = config.get("adspower_api_base", "http://127.0.0.1:50325")
    api_key = config.get("adspower_api_key", "")
    url = f"{api_base}/api/v1/browser/stop?user_id={adspower_id}&api_key={api_key}"
    try:
        requests.get(url, timeout=10)
        logger.info(f"AdsPower stopped: {adspower_id}")
    except Exception as e:
        logger.warning(f"AdsPower stop failed: {e}")


# ── Core per-account processing ──────────────────────────────────────

def process_account(profile_id, profile, proxy_config, content_bank,
                    config, warmup_only=False):
    """Process a single account: warmup and/or posting.

    Args:
        profile_id: Profile identifier (e.g., "midnight_mae")
        profile: AccountProfile object
        proxy_config: Proxy group config dict
        content_bank: ContentBank instance
        config: Full queue_config dict
        warmup_only: If True, skip posting regardless of account age

    Returns:
        dict with status and details
    """
    adspower_id = profile.adspower_id
    tag = f"[{profile_id}]"
    result = {
        "profile_id": profile_id,
        "status": "ok",
        "warmup": {},
        "posts": {"success": 0, "failed": 0, "total": 0},
    }

    # 1. Rotate proxy
    rotate_proxy(proxy_config)

    # 2. Start browser
    ws_endpoint = start_adspower(adspower_id, config)
    if not ws_endpoint:
        result["status"] = "browser_failed"
        return result

    try:
        from playwright.sync_api import sync_playwright
        from uploaders.reddit.reddit_poster_playwright import post_file_to_subreddit

        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(ws_endpoint)
            contexts = browser.contexts
            context = contexts[0] if contexts else browser.new_context()
            pages = context.pages
            page = pages[0] if pages else context.new_page()

            # 3. Load persona
            persona = profile.persona
            attributes = profile.attributes
            grok_key = API_KEYS.get("grok_api_key", "")
            claude_key = API_KEYS.get("claude_api_key", "")

            # 4. Create warmer
            warmer = AccountWarmer(
                profile_id, page,
                persona=persona,
                attributes=attributes,
                grok_api_key=grok_key,
                account_created_at=profile.created_at,
            )
            warmer.min_nsfw_days = config.get("min_nsfw_days", 14)
            warmup_day = warmer.get_day()
            logger.info(
                f"{tag} Day {warmup_day}, "
                f"should_post={warmer.should_post_today()}"
            )

            # 5. Run warmup
            s_lo, s_hi = config.get("session_range_min", [15, 30])
            c_lo, c_hi = config.get("comments_range", [10, 20])
            session_min = random.randint(s_lo, s_hi)
            max_comments = random.randint(c_lo, c_hi)

            logger.info(
                f"{tag} Starting warmup: {session_min}min, "
                f"max {max_comments} comments"
            )
            warmup_stats = warmer.run_daily_warmup(
                session_minutes=session_min,
                max_comments=max_comments,
            )
            result["warmup"] = warmup_stats
            logger.info(
                f"{tag} Warmup done: "
                f"sessions={warmup_stats.get('sessions', 0)}, "
                f"comments={warmup_stats.get('comments', 0)}, "
                f"votes={warmup_stats.get('upvotes', 0)}"
            )

            # 6. CQS check
            try:
                cqs = warmer.check_cqs()
                if cqs is not None:
                    logger.info(f"{tag} CQS = {cqs}")
                    result["cqs"] = cqs
            except Exception as e:
                logger.warning(f"{tag} CQS check failed: {e}")

            # 7. Check if we should post
            if warmup_only or not warmer.should_post_today():
                reason = "warmup_only flag" if warmup_only else f"day {warmup_day} < {warmer.min_nsfw_days}"
                logger.info(f"{tag} No posting: {reason}")
                result["status"] = "warmup_only"
                return result

            # ── POSTING PIPELINE ──────────────────────────────────

            creator = profile.creator
            if not creator:
                logger.warning(f"{tag} No creator mapped, skipping posts")
                result["status"] = "no_creator"
                return result

            # 8. Pick content from bank
            max_posts = min(
                warmer.get_max_posts_today(),
                config.get("daily_post_limit", 8),
            )
            posts_today = get_posts_today(profile_id)
            remaining = max_posts - posts_today

            if remaining <= 0:
                logger.info(f"{tag} Daily limit reached ({posts_today}/{max_posts})")
                result["status"] = "daily_limit"
                return result

            # Pick enough files to fill remaining posts
            # Each file gets posted to subs_per_file subs, so we need
            # ceil(remaining / subs_per_file) files
            subs_per_file = config.get("subs_per_file", 8)
            files_needed = max(1, -(-remaining // subs_per_file))  # ceiling division
            files = content_bank.pick_files(creator, count=files_needed)

            if not files:
                logger.warning(f"{tag} No unexhausted files for '{creator}'")
                result["status"] = "no_content"
                return result

            logger.info(f"{tag} Picked {len(files)} files, up to {remaining} posts")

            # 9. Vision analysis + sub matching
            banned_subs = get_banned_subs(profile_id)
            posting_plan = []

            for fpath in files:
                fname = os.path.basename(fpath)
                vision = analyze_image(fpath, claude_key)
                if not vision:
                    logger.warning(f"{tag} Vision failed: {fname}")
                    continue

                file_hash = content_file_hash(fpath)
                posted = get_posted_subs(file_hash)
                excluded = banned_subs | posted

                matches = match_content(
                    vision, SUB_PROFILES, SUB_TIERS,
                    excluded_subs=excluded,
                    sub_data=SUB_DATA,
                    max_subscribers=config.get("max_subscribers", 50000),
                    min_subscribers=config.get("min_subscribers", 2500),
                    exclude_low_quality_subs=True,
                    exclude_strict_new_account_subs=True,
                    exclude_high_risk_niche_subs=True,
                )
                selected = random_select_subs(
                    matches,
                    count=subs_per_file,
                    min_score=config.get("min_score", 20),
                )

                logger.info(
                    f"{tag} {fname}: {len(selected)} subs matched "
                    f"(from {len(matches)} candidates)"
                )

                for sub_name, score, theme, tags in selected:
                    posting_plan.append({
                        "file_path": fpath,
                        "file_hash": file_hash,
                        "file_name": fname,
                        "sub_name": sub_name,
                        "score": score,
                        "sub_theme": theme,
                        "content_tags": vision.get("tags", []),
                        "body_type": vision.get("body_type", ""),
                        "action": vision.get("action", ""),
                        "setting": vision.get("setting", ""),
                    })

            # Cap to remaining daily limit
            if len(posting_plan) > remaining:
                posting_plan = posting_plan[:remaining]

            if not posting_plan:
                logger.info(f"{tag} No postable content after matching")
                result["status"] = "no_matches"
                return result

            # 10. Generate titles
            pairings = [{
                "sub_name": p["sub_name"],
                "sub_theme": p["sub_theme"],
                "content_tags": p["content_tags"],
                "body_type": p["body_type"],
                "action": p["action"],
                "setting": p["setting"],
            } for p in posting_plan]

            titles = generate_titles_batch(pairings, grok_key)
            for i, title in enumerate(titles):
                if i < len(posting_plan):
                    posting_plan[i]["title"] = title or "come say hi"

            logger.info(f"{tag} Generated {len(titles)} titles, posting {len(posting_plan)} items")

            # 11. Post each item
            humanizer = Humanizer(page, {"daily_limit": max_posts})
            success_count = 0
            fail_count = 0
            browse_lo, browse_hi = config.get("browse_range_sec", [80, 150])

            for i, item in enumerate(posting_plan):
                if warmer.stop_requested:
                    logger.info(f"{tag} Stop requested, ending posting")
                    break

                sub = item["sub_name"]
                title = item.get("title", "hey there")
                fpath = item["file_path"]

                # Interleaved browse before post
                browse_sec = random.randint(browse_lo, browse_hi)
                logger.info(f"{tag} [{i+1}/{len(posting_plan)}] Browsing {browse_sec}s before r/{sub}")
                try:
                    warmer._run_browse_session(session_sec=browse_sec)
                except Exception as e:
                    logger.warning(f"{tag} Browse error: {e}")

                # Spoof file
                upload_path = fpath
                spoofed_path = None
                if config.get("spoof_enabled", True):
                    try:
                        spoofed, _ = spoof_file(fpath)
                        if spoofed:
                            spoofed_path = spoofed
                            upload_path = spoofed
                    except Exception as e:
                        logger.warning(f"{tag} Spoof error: {e}")

                # Post
                try:
                    logger.info(f"{tag} [{i+1}/{len(posting_plan)}] Posting to r/{sub}: \"{title[:50]}\"")
                    ok = post_file_to_subreddit(
                        page=page,
                        subreddit=sub,
                        title=title,
                        file_path=upload_path,
                        mark_nsfw=True,
                        humanizer=humanizer,
                    )

                    post_status, detail = check_post_result(page)

                    if post_status == BanStatus.OK:
                        success_count += 1
                        add_post(profile_id, item["file_hash"], sub, title,
                                 fpath, "success", post_url=detail)
                        logger.info(f"{tag} SUCCESS r/{sub}: {detail}")

                    elif post_status == BanStatus.SUB_BANNED:
                        add_ban(profile_id, sub, detail)
                        add_post(profile_id, item["file_hash"], sub, title,
                                 fpath, "banned", error=detail)
                        logger.warning(f"{tag} BANNED r/{sub}: {detail}")

                    elif post_status == BanStatus.RATE_LIMITED:
                        add_post(profile_id, item["file_hash"], sub, title,
                                 fpath, "rate_limited", error=detail)
                        logger.warning(f"{tag} RATE LIMITED, waiting 5min")
                        time.sleep(300)

                    else:
                        fail_count += 1
                        add_post(profile_id, item["file_hash"], sub, title,
                                 fpath, "failed", error=detail)
                        logger.warning(f"{tag} FAILED r/{sub}: {detail}")

                except Exception as e:
                    fail_count += 1
                    add_post(profile_id, item["file_hash"], sub, title,
                             fpath, "failed", error=str(e))
                    logger.error(f"{tag} POST ERROR: {e}")

                finally:
                    # Cleanup spoofed file
                    if spoofed_path and os.path.exists(spoofed_path):
                        try:
                            os.remove(spoofed_path)
                        except OSError:
                            pass

                # Wait between posts
                if i < len(posting_plan) - 1:
                    humanizer.wait_between_posts()

            result["posts"] = {
                "success": success_count,
                "failed": fail_count,
                "total": len(posting_plan),
            }
            logger.info(
                f"{tag} Posting done: {success_count}/{len(posting_plan)} success, "
                f"{fail_count} failed"
            )

    except Exception as e:
        logger.error(f"{tag} FATAL: {e}", exc_info=True)
        result["status"] = "error"
        result["detail"] = str(e)

    finally:
        # Always stop browser
        stop_adspower(adspower_id, config)

    return result


# ── Worker loop ──────────────────────────────────────────────────────

def worker_loop(worker_id, proxy_group_name, proxy_config, config,
                warmup_only=False):
    """Process all accounts in a proxy group sequentially."""
    account_ids = proxy_config.get("accounts", [])
    logger.info(
        f"[Worker {worker_id}] Starting: {len(account_ids)} accounts "
        f"on {proxy_group_name}"
    )

    pm = ProfileManager(str(PROJECT_ROOT / "config" / "account_profiles.json"))
    content_bank = ContentBank(
        config.get("content_bank_root", ""),
        max_posts_per_file=config.get("max_posts_per_file", 8),
    )

    results = []
    for i, profile_id in enumerate(account_ids):
        profile = pm.get_profile(profile_id)
        if not profile:
            logger.warning(f"[Worker {worker_id}] Profile not found: {profile_id}")
            results.append({"profile_id": profile_id, "status": "not_found"})
            continue

        logger.info(
            f"[Worker {worker_id}] Account {i+1}/{len(account_ids)}: "
            f"{profile_id} (ads:{profile.adspower_id})"
        )

        try:
            result = process_account(
                profile_id, profile, proxy_config,
                content_bank, config, warmup_only=warmup_only,
            )
            results.append(result)
        except Exception as e:
            logger.error(
                f"[Worker {worker_id}] {profile_id} crashed: {e}",
                exc_info=True,
            )
            results.append({
                "profile_id": profile_id,
                "status": "crash",
                "detail": str(e),
            })

        # Inter-account delay
        if i < len(account_ids) - 1:
            lo, hi = config.get("inter_account_delay_sec", [30, 90])
            delay = random.randint(lo, hi)
            logger.info(f"[Worker {worker_id}] Waiting {delay}s before next account")
            time.sleep(delay)

    return results


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Headless queue runner for Reddit automation"
    )
    parser.add_argument(
        "--workers", type=int, default=None,
        help="Override number of parallel workers (default: from config)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print account assignments without executing"
    )
    parser.add_argument(
        "--accounts", type=str, default=None,
        help="Comma-separated profile_ids to run (overrides config)"
    )
    parser.add_argument(
        "--warmup-only", action="store_true",
        help="Run warmup for all accounts, skip posting"
    )
    args = parser.parse_args()

    # Setup logging
    log_dir = PROJECT_ROOT / "data"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                str(log_dir / "queue_runner.log"),
                encoding="utf-8",
            ),
        ],
    )

    logger.info("=" * 60)
    logger.info("Queue Runner starting")
    logger.info("=" * 60)

    load_global_data()

    proxy_groups = QUEUE_CONFIG.get("proxy_groups", {})
    num_workers = args.workers or QUEUE_CONFIG.get("workers", 4)

    # If --accounts specified, override the proxy group assignments
    if args.accounts:
        account_list = [a.strip() for a in args.accounts.split(",")]
        # Put all specified accounts into proxy_1 (single worker)
        proxy_groups = {
            "manual": {
                "rotation_url": "",
                "wait_after_rotate_sec": 0,
                "accounts": account_list,
            }
        }
        num_workers = 1
        logger.info(f"Manual mode: {len(account_list)} accounts on 1 worker")

    # Summary
    total_accounts = sum(
        len(pg.get("accounts", [])) for pg in proxy_groups.values()
    )
    logger.info(f"Workers: {num_workers}, Total accounts: {total_accounts}")

    if args.dry_run:
        print()
        print(f"DRY RUN — {total_accounts} accounts across {len(proxy_groups)} proxy groups:")
        print()
        pm = ProfileManager(str(PROJECT_ROOT / "config" / "account_profiles.json"))
        for pg_name, pg in proxy_groups.items():
            accts = pg.get("accounts", [])
            print(f"  {pg_name} ({len(accts)} accounts):")
            for aid in accts:
                p = pm.get_profile(aid)
                if p:
                    day = "?"  # Can't check without browser
                    print(f"    - {aid} (ads:{p.adspower_id}, creator:{p.creator})")
                else:
                    print(f"    - {aid} (NOT FOUND)")
            print()

        bank = ContentBank(
            QUEUE_CONFIG.get("content_bank_root", ""),
            max_posts_per_file=QUEUE_CONFIG.get("max_posts_per_file", 8),
        )
        creators = bank.list_creators()
        if creators:
            print(f"Content bank ({bank.root}):")
            for c in creators:
                stats = bank.get_stats(c)
                print(f"  {c}: {stats['available']} available, "
                      f"{stats['exhausted']} exhausted, "
                      f"{stats['total']} total")
        else:
            print(f"Content bank: {bank.root} (no creators found)")
        return

    # Launch workers
    start_time = time.time()
    all_results = []

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {}
        for i, (pg_name, pg_config) in enumerate(proxy_groups.items()):
            if i >= num_workers:
                break
            if not pg_config.get("accounts"):
                logger.info(f"Skipping {pg_name}: no accounts assigned")
                continue
            future = executor.submit(
                worker_loop, i + 1, pg_name, pg_config,
                QUEUE_CONFIG, warmup_only=args.warmup_only,
            )
            futures[future] = pg_name

        for future in as_completed(futures):
            pg_name = futures[future]
            try:
                results = future.result()
                all_results.extend(results)
                ok = sum(1 for r in results
                         if r.get("status") in ("ok", "warmup_only", "daily_limit"))
                err = sum(1 for r in results
                          if r.get("status") in ("error", "crash", "browser_failed"))
                logger.info(f"Worker {pg_name} done: {ok} ok, {err} errors")
            except Exception as e:
                logger.error(f"Worker {pg_name} crashed: {e}", exc_info=True)

    # Final summary
    elapsed = int(time.time() - start_time)
    total_posts = sum(r.get("posts", {}).get("success", 0) for r in all_results)
    total_failed = sum(r.get("posts", {}).get("failed", 0) for r in all_results)
    warmup_only_count = sum(1 for r in all_results if r.get("status") == "warmup_only")

    logger.info("=" * 60)
    logger.info(f"Queue Runner complete in {elapsed // 60}m {elapsed % 60}s")
    logger.info(
        f"Accounts: {len(all_results)} processed, "
        f"{warmup_only_count} warmup-only"
    )
    logger.info(f"Posts: {total_posts} success, {total_failed} failed")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
