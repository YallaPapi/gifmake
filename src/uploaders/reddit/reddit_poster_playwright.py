#!/usr/bin/env python3
"""
Reddit Link Poster using Playwright - Posts RedGIFs URLs to subreddits
Connects to existing AdsPower browser profiles
"""

import csv
import json
import time
from pathlib import Path
from typing import Optional
from datetime import datetime
import requests
import logging

from playwright.sync_api import sync_playwright, Page, Browser

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


class AdsPowerClient:
    """Simple AdsPower API client"""

    def __init__(self, api_base: str, api_key: str):
        self.api_base = api_base.rstrip('/')
        self.api_key = api_key

    def start_browser(self, profile_id: str) -> Optional[dict]:
        """Start browser profile, return connection info"""
        url = f"{self.api_base}/api/v1/browser/start?user_id={profile_id}&api_key={self.api_key}"
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 0:
                return data["data"]
        return None

    def stop_browser(self, profile_id: str):
        """Stop browser profile"""
        url = f"{self.api_base}/api/v1/browser/stop?user_id={profile_id}&api_key={self.api_key}"
        requests.get(url, timeout=10)


def select_flair(page: Page, flair_text: str) -> bool:
    """
    Select a flair from the flair picker dropdown.

    Args:
        page: Playwright page object
        flair_text: Display text of the flair to select (e.g., "OC", "Video")

    Returns:
        True if flair was selected successfully, False otherwise
    """
    logger.info(f"Attempting to select flair: {flair_text}")

    # Try to find and click the flair picker button
    flair_picker_selectors = [
        'button:has-text("Add flair")',
        '[data-testid="flair-picker"]',
        'button[aria-label*="flair" i]',
        'flair-picker button',
        'shreddit-post-flair-picker button',
    ]

    picker_clicked = False
    for selector in flair_picker_selectors:
        try:
            picker_btn = page.locator(selector)
            if picker_btn.count() > 0:
                picker_btn.first.click()
                picker_clicked = True
                logger.info(f"Clicked flair picker with selector: {selector}")
                break
        except Exception as e:
            logger.debug(f"Flair picker selector {selector} failed: {e}")
            continue

    if not picker_clicked:
        logger.warning("Could not find flair picker button - subreddit may not have flairs")
        return False

    # Wait for dropdown to appear
    page.wait_for_timeout(1500)

    # Try to find the flair option in the dropdown
    # Reddit flair dropdowns can use various structures
    flair_option_selectors = [
        # Direct text match in various containers
        f'[role="option"]:has-text("{flair_text}")',
        f'[role="menuitem"]:has-text("{flair_text}")',
        f'li:has-text("{flair_text}")',
        f'div[role="listbox"] >> text="{flair_text}"',
        # Shreddit flair items
        f'flair-picker-item:has-text("{flair_text}")',
        f'shreddit-flair-picker-item:has-text("{flair_text}")',
        # Generic clickable with flair text
        f'button:has-text("{flair_text}")',
        f'span:has-text("{flair_text}")',
    ]

    flair_selected = False
    for selector in flair_option_selectors:
        try:
            flair_option = page.locator(selector)
            if flair_option.count() > 0:
                flair_option.first.click()
                flair_selected = True
                logger.info(f"Selected flair '{flair_text}' with selector: {selector}")
                break
        except Exception as e:
            logger.debug(f"Flair option selector {selector} failed: {e}")
            continue

    # If direct selectors failed, try searching within the dropdown
    if not flair_selected:
        try:
            # Look for any visible dropdown/modal and search within it
            dropdown_containers = [
                '[role="listbox"]',
                '[role="menu"]',
                'flair-picker',
                'shreddit-flair-picker',
                '[data-testid="flair-dropdown"]',
            ]

            for container_selector in dropdown_containers:
                container = page.locator(container_selector)
                if container.count() > 0:
                    # Find all clickable items in the container
                    items = container.locator('button, [role="option"], [role="menuitem"], li')
                    count = items.count()
                    for i in range(count):
                        item = items.nth(i)
                        item_text = item.inner_text()
                        if flair_text.lower() in item_text.lower():
                            item.click()
                            flair_selected = True
                            logger.info(f"Selected flair '{flair_text}' by text search in container")
                            break
                    if flair_selected:
                        break
        except Exception as e:
            logger.debug(f"Container search for flair failed: {e}")

    if not flair_selected:
        # Try to close the dropdown by pressing Escape
        try:
            page.keyboard.press("Escape")
        except:
            pass
        logger.warning(f"Could not find flair '{flair_text}' in dropdown - continuing without flair")
        return False

    # Wait for flair selection to register
    page.wait_for_timeout(500)

    # Some Reddit UIs have a confirmation/apply button after selecting flair
    apply_selectors = [
        'button:has-text("Apply")',
        'button:has-text("Done")',
        'button:has-text("Save")',
        '[data-testid="flair-apply-btn"]',
    ]

    for selector in apply_selectors:
        try:
            apply_btn = page.locator(selector)
            if apply_btn.count() > 0 and apply_btn.is_visible():
                apply_btn.click()
                logger.info("Clicked flair apply/confirm button")
                break
        except:
            continue

    page.wait_for_timeout(500)
    return True


def post_link_to_subreddit(
    page: Page,
    subreddit: str,
    title: str,
    url: str,
    mark_nsfw: bool = True,
    flair: Optional[str] = None
) -> bool:
    """
    Post a link to a subreddit using Playwright

    Args:
        page: Playwright page object
        subreddit: Subreddit name (without r/)
        title: Post title
        url: URL to post (e.g., RedGIFs link)
        mark_nsfw: Whether to mark as NSFW
        flair: Optional flair text to select (e.g., "OC", "Video")

    Returns:
        True if successful
    """

    # Navigate directly to link submit page
    submit_url = f"https://www.reddit.com/r/{subreddit}/submit?type=link"
    logger.info(f"Navigating to {submit_url}")
    page.goto(submit_url, timeout=60000)
    page.wait_for_timeout(5000)  # Just wait 5 seconds instead of networkidle

    # Fill title
    logger.info(f"Filling title: {title[:50]}...")
    title_selectors = [
        'textarea[name="title"]',
        '[data-testid="post-title-input"]',
        'textarea[placeholder*="title" i]',
    ]

    title_filled = False
    for selector in title_selectors:
        try:
            if page.locator(selector).count() > 0:
                page.fill(selector, title)
                title_filled = True
                break
        except:
            continue

    if not title_filled:
        logger.error("Could not find title input")
        return False

    # Fill URL - Reddit uses Shadow DOM, need to pierce it
    page.wait_for_timeout(2000)  # Wait for URL field to appear
    logger.info(f"Filling URL: {url[:50]}...")

    url_filled = False
    try:
        # Pierce Shadow DOM to find the textarea inside faceplate-textarea-input
        url_input = page.locator('faceplate-textarea-input[name="link"]').locator('textarea')
        if url_input.count() > 0:
            url_input.fill(url)
            url_filled = True
            logger.info("Filled URL via Shadow DOM pierce")
    except Exception as e:
        logger.warning(f"Shadow DOM approach failed: {e}")

    # Fallback to regular selectors
    if not url_filled:
        url_selectors = [
            'input[name="url"]',
            '[data-testid="post-url-input"]',
            'input[placeholder*="url" i]',
            'textarea[name="link"]',
        ]
        for selector in url_selectors:
            try:
                if page.locator(selector).count() > 0:
                    page.fill(selector, url)
                    url_filled = True
                    break
            except:
                continue

    if not url_filled:
        logger.error("Could not find URL input")
        return False

    # Mark NSFW
    if mark_nsfw:
        logger.info("Marking as NSFW...")
        nsfw_selectors = [
            'button[aria-label*="NSFW" i]',
            '[data-testid="nsfw-btn"]',
            'button:has-text("NSFW")',
        ]

        for selector in nsfw_selectors:
            try:
                btn = page.locator(selector)
                if btn.count() > 0:
                    # Check if already pressed
                    aria = btn.get_attribute("aria-pressed")
                    if aria != "true":
                        btn.click()
                    break
            except:
                continue

    # Select flair if specified
    if flair:
        page.wait_for_timeout(1000)  # Brief pause before flair selection
        flair_result = select_flair(page, flair)
        if not flair_result:
            # Flair selection failed but we continue with posting
            logger.info("Continuing without flair...")

    # Submit
    logger.info("Submitting post...")
    page.wait_for_timeout(1000)  # Brief pause

    submit_selectors = [
        'button[type="submit"]:has-text("Post")',
        '[data-testid="submit-post-btn"]',
        'button:has-text("Post")',
    ]

    submitted = False
    for selector in submit_selectors:
        try:
            btn = page.locator(selector)
            if btn.count() > 0:
                btn.click()
                submitted = True
                break
        except:
            continue

    if not submitted:
        logger.error("Could not find submit button")
        return False

    # Wait for redirect
    page.wait_for_timeout(5000)
    current_url = page.url

    if "/comments/" in current_url:
        logger.info(f"SUCCESS! Post created: {current_url}")
        return True
    else:
        logger.info(f"Current URL: {current_url}")
        return "/submit" not in current_url


def batch_post_from_csv(
    page: Page,
    csv_path: str,
    output_path: str = None,
    delay_seconds: int = 60,
    mark_nsfw: bool = True
) -> dict:
    """
    Batch post links to Reddit from a CSV file.

    IMPORTANT: Each unique RedGIFs URL should only be posted to ONE subreddit.
    The CSV should contain unique URL-to-subreddit mappings (no URL reuse).

    Args:
        page: Playwright page object (connected to AdsPower browser)
        csv_path: Path to input CSV with columns: subreddit, title, url, flair (optional)
        output_path: Path to save results CSV (default: input_path with _results suffix)
        delay_seconds: Seconds to wait between posts (default: 60 for rate limiting)
        mark_nsfw: Whether to mark posts as NSFW (default: True)

    Returns:
        dict with keys:
            - total: int - total rows processed
            - success: int - successful posts
            - failed: int - failed posts
            - skipped: int - skipped rows (e.g., duplicates)
            - results: list[dict] - detailed results per row
            - output_file: str - path to results CSV

    CSV Input Format:
        subreddit,title,url,flair
        subredditname,My Title,https://redgifs.com/watch/xyz,

    CSV Output Format:
        subreddit,title,url,flair,status,error,posted_at
    """

    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    # Generate output path if not provided
    if output_path is None:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_path = csv_path.parent / f"{csv_path.stem}_results_{timestamp}.csv"
    else:
        output_path = Path(output_path)

    # Read input CSV
    rows = []
    with open(csv_path, 'r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        logger.warning(f"CSV file is empty: {csv_path}")
        return {
            'total': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'results': [],
            'output_file': str(output_path)
        }

    # Track seen URLs to enforce one-URL-per-subreddit rule
    seen_urls = set()

    # Process results
    results = []
    success_count = 0
    failed_count = 0
    skipped_count = 0

    logger.info(f"Starting batch post: {len(rows)} rows from {csv_path}")
    logger.info(f"Delay between posts: {delay_seconds} seconds")

    for i, row in enumerate(rows):
        subreddit = row.get('subreddit', '').strip()
        title = row.get('title', '').strip()
        url = row.get('url', '').strip()
        flair = row.get('flair', '').strip()  # Optional, not yet implemented

        result = {
            'subreddit': subreddit,
            'title': title,
            'url': url,
            'flair': flair,
            'status': 'pending',
            'error': '',
            'posted_at': ''
        }

        # Validate required fields
        if not subreddit or not title or not url:
            result['status'] = 'skipped'
            result['error'] = 'Missing required field (subreddit, title, or url)'
            skipped_count += 1
            results.append(result)
            logger.warning(f"[{i+1}/{len(rows)}] Skipped: missing required field")
            continue

        # Check for duplicate URL (enforce one-URL-per-subreddit rule)
        if url in seen_urls:
            result['status'] = 'skipped'
            result['error'] = 'Duplicate URL - each RedGIFs URL should only be posted once'
            skipped_count += 1
            results.append(result)
            logger.warning(f"[{i+1}/{len(rows)}] Skipped duplicate URL: {url[:50]}...")
            continue

        # Mark URL as seen
        seen_urls.add(url)

        # Post to Reddit
        logger.info(f"[{i+1}/{len(rows)}] Posting to r/{subreddit}: {title[:40]}...")

        try:
            success = post_link_to_subreddit(
                page=page,
                subreddit=subreddit,
                title=title,
                url=url,
                mark_nsfw=mark_nsfw,
                flair=flair if flair else None
            )

            if success:
                result['status'] = 'success'
                result['posted_at'] = datetime.now().isoformat()
                success_count += 1
                logger.info(f"[{i+1}/{len(rows)}] SUCCESS: r/{subreddit}")
            else:
                result['status'] = 'failed'
                result['error'] = 'Post submission failed (check logs for details)'
                failed_count += 1
                logger.error(f"[{i+1}/{len(rows)}] FAILED: r/{subreddit}")

        except Exception as e:
            result['status'] = 'failed'
            result['error'] = str(e)
            failed_count += 1
            logger.error(f"[{i+1}/{len(rows)}] ERROR: {e}")

        results.append(result)

        # Write results after each post (in case of crash)
        _write_results_csv(output_path, results)

        # Delay before next post (skip delay after last post)
        if i < len(rows) - 1:
            logger.info(f"Waiting {delay_seconds} seconds before next post...")
            time.sleep(delay_seconds)

    # Final summary
    summary = {
        'total': len(rows),
        'success': success_count,
        'failed': failed_count,
        'skipped': skipped_count,
        'results': results,
        'output_file': str(output_path)
    }

    logger.info("=" * 50)
    logger.info("BATCH POST COMPLETE")
    logger.info(f"  Total:   {summary['total']}")
    logger.info(f"  Success: {summary['success']}")
    logger.info(f"  Failed:  {summary['failed']}")
    logger.info(f"  Skipped: {summary['skipped']}")
    logger.info(f"  Results: {output_path}")
    logger.info("=" * 50)

    return summary


def _write_results_csv(output_path: Path, results: list) -> None:
    """Write results to CSV file (internal helper)"""
    fieldnames = ['subreddit', 'title', 'url', 'flair', 'status', 'error', 'posted_at']
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)


def main():
    """Example: Connect to AdsPower and post"""

    # Load config
    config_path = Path(__file__).parent.parent / "redgifs" / "adspower_config.json"
    if not config_path.exists():
        config_path = Path(__file__).parent / "adspower_config.json"

    config = json.loads(config_path.read_text())
    client = AdsPowerClient(config["adspower_api_base"], config["api_key"])

    # Start browser - use first profile from config (or override below)
    profile = config["profiles"][0]
    profile_id = profile["profile_id"]
    account_name = profile["account_name"]

    # Override for testing specific profile:
    # profile_id = "k199f724"
    # account_name = "msdinokiss"

    logger.info(f"Starting browser for {account_name}...")
    browser_data = client.start_browser(profile_id)

    if not browser_data:
        logger.error("Failed to start browser")
        return

    # Get CDP endpoint for Playwright
    # AdsPower returns ws://127.0.0.1:PORT/devtools/browser/UUID
    ws_endpoint = browser_data['ws']['puppeteer']
    logger.info(f"Connecting to: {ws_endpoint}")

    with sync_playwright() as p:
        # Connect to existing browser via CDP
        browser = p.chromium.connect_over_cdp(ws_endpoint)

        # Get existing context and page
        contexts = browser.contexts
        if contexts:
            context = contexts[0]
            pages = context.pages
            if pages:
                page = pages[0]
            else:
                page = context.new_page()
        else:
            context = browser.new_context()
            page = context.new_page()

        # Post - CONFIGURE THESE VALUES
        success = post_link_to_subreddit(
            page=page,
            subreddit="test",  # Change to target subreddit
            title="Test post",  # Change to your title
            url="https://redgifs.com/watch/yourvideohere",  # Change to RedGIFs URL
            mark_nsfw=True,
            flair=None  # Optional: set to flair text like "OC" or "Video"
        )

        if success:
            logger.info("Post successful!")
        else:
            logger.error("Post failed")

        # Don't close - browser belongs to AdsPower
        # browser.close()

    # Optionally stop browser
    # client.stop_browser(profile_id)


def main_batch():
    """Example: Connect to AdsPower and batch post from CSV"""

    # Load config
    config_path = Path(__file__).parent.parent / "redgifs" / "adspower_config.json"
    if not config_path.exists():
        config_path = Path(__file__).parent / "adspower_config.json"

    config = json.loads(config_path.read_text())
    client = AdsPowerClient(config["adspower_api_base"], config["api_key"])

    # Start browser - use first profile from config
    profile = config["profiles"][0]
    profile_id = profile["profile_id"]
    account_name = profile["account_name"]

    logger.info(f"Starting browser for {account_name}...")
    browser_data = client.start_browser(profile_id)

    if not browser_data:
        logger.error("Failed to start browser")
        return

    ws_endpoint = browser_data['ws']['puppeteer']
    logger.info(f"Connecting to: {ws_endpoint}")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws_endpoint)

        contexts = browser.contexts
        if contexts:
            context = contexts[0]
            pages = context.pages
            if pages:
                page = pages[0]
            else:
                page = context.new_page()
        else:
            context = browser.new_context()
            page = context.new_page()

        # BATCH POST FROM CSV - Configure path below
        csv_path = Path(__file__).parent / "posts.csv"  # Change to your CSV path

        if not csv_path.exists():
            # Create example CSV if it doesn't exist
            logger.info(f"Creating example CSV at {csv_path}")
            example_content = """subreddit,title,url,flair
test,My first post title,https://redgifs.com/watch/example1,
test,My second post title,https://redgifs.com/watch/example2,
"""
            csv_path.write_text(example_content)
            logger.info("Please edit the CSV file with your actual posts, then run again.")
            return

        # Run batch post
        results = batch_post_from_csv(
            page=page,
            csv_path=str(csv_path),
            delay_seconds=60,  # 60 seconds between posts
            mark_nsfw=True
        )

        logger.info(f"Batch complete: {results['success']}/{results['total']} succeeded")
        logger.info(f"Results saved to: {results['output_file']}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--batch":
        main_batch()
    else:
        main()
