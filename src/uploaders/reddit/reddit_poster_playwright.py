#!/usr/bin/env python3
"""
Reddit Link Poster using Playwright - Posts RedGIFs URLs to subreddits
Connects to existing AdsPower browser profiles
"""

import csv
import json
import os
import re
import time
from pathlib import Path
from typing import Optional
from datetime import datetime
import requests
import logging

from playwright.sync_api import sync_playwright, Page, Browser

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

DEBUG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "debug")


def dismiss_over18(page: Page):
    """Dismiss the 'Mature Content — Are you over 18?' popup if present."""
    try:
        btn = page.locator('button:has-text("Yes, I\'m Over 18")')
        if btn.count() > 0 and btn.first.is_visible():
            btn.first.click()
            page.wait_for_timeout(1000)
            logger.info("Dismissed 18+ popup")
            return True
    except Exception:
        pass
    return False


def _dump_failure(page: Page, subreddit: str, step: str):
    """Save screenshot + HTML dump when something fails. Saved to data/debug/."""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"{ts}_{subreddit}_{step}"

    try:
        screenshot_path = os.path.join(DEBUG_DIR, f"{prefix}.png")
        page.screenshot(path=screenshot_path, full_page=False)
        logger.info(f"Debug screenshot: {screenshot_path}")
    except Exception as e:
        logger.warning(f"Screenshot failed: {e}")

    try:
        html_path = os.path.join(DEBUG_DIR, f"{prefix}.html")
        html = page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f"Debug HTML dump: {html_path}")
    except Exception as e:
        logger.warning(f"HTML dump failed: {e}")


def _extract_post_url(page_url: str, subreddit: str) -> Optional[str]:
    """Extract a clean permalink from Reddit's redirect URL.

    Reddit redirects to different URL patterns after posting:
      Old: https://www.reddit.com/r/Sub/comments/abc123/post_title/
      New: https://www.reddit.com/r/Sub/?created=t3_abc123&createdPostType=IMAGE
    Both contain the post ID which we can build a clean URL from.
    """
    # New Shreddit UI: ?created=t3_XXXXX
    match = re.search(r'created=t3_([a-z0-9]+)', page_url)
    if match:
        post_id = match.group(1)
        return f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"

    # Old UI: /comments/XXXXX/
    match = re.search(r'/comments/([a-z0-9]+)/', page_url)
    if match:
        return page_url.split("?")[0]  # Strip query params

    # Already a /comments/ URL without trailing slash
    if "/comments/" in page_url:
        return page_url.split("?")[0]

    return None


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
    page.wait_for_timeout(3000)
    dismiss_over18(page)
    page.wait_for_timeout(2000)

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


def _try_select_flair(page: Page):
    """Try to open the flair picker and select the first available flair.

    Reddit's new UI uses <r-post-flairs-modal> which opens a flair list.
    We click the "Add flair" button, wait for the list, pick the first one.
    """
    try:
        # Click the flair button to open the picker
        flair_openers = [
            'button:has-text("Add flair")',
            '#post-flair-modal',
            'r-post-flairs-modal',
            '[class*="flair"]',
        ]
        opened = False
        for sel in flair_openers:
            try:
                btn = page.locator(sel)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    opened = True
                    break
            except:
                continue

        if not opened:
            logger.warning("Could not open flair picker")
            return

        page.wait_for_timeout(2000)

        # Look for flair options in the opened modal/dropdown
        flair_selectors = [
            'faceplate-tracker[noun="flair"]',  # New Shreddit flair items
            '[data-testid*="flair"]',
            'li[class*="flair"]',
            'div[class*="flair"] span',
            'label:has(input[name="flair"])',    # Radio button style
        ]

        for sel in flair_selectors:
            try:
                items = page.locator(sel)
                if items.count() > 0:
                    items.first.click()
                    logger.info(f"Selected first flair via: {sel}")
                    page.wait_for_timeout(500)

                    # Look for an "Apply" / "Save" / confirm button
                    for confirm_sel in ['button:has-text("Apply")', 'button:has-text("Save")',
                                        'button:has-text("Done")', 'button[type="submit"]']:
                        try:
                            confirm = page.locator(confirm_sel)
                            if confirm.count() > 0 and confirm.first.is_visible():
                                confirm.first.click()
                                break
                        except:
                            continue
                    return
            except:
                continue

        logger.warning("Could not find any flair options")
    except Exception as e:
        logger.warning(f"Flair selection failed: {e}")


def post_file_to_subreddit(
    page: Page,
    subreddit: str,
    title: str,
    file_path: str,
    mark_nsfw: bool = True,
    flair: Optional[str] = None,
    humanizer=None,
) -> bool:
    """
    Post a file (image/video) directly to a subreddit via upload.

    Args:
        page: Playwright page object
        subreddit: Subreddit name (without r/)
        title: Post title
        file_path: Path to the image/video file to upload
        mark_nsfw: Whether to mark as NSFW
        flair: Optional flair text to select
        humanizer: Optional Humanizer instance for human-like interactions

    Returns:
        True if successful
    """
    from pathlib import Path as P
    if not P(file_path).exists():
        logger.error(f"File not found: {file_path}")
        return False

    # Navigate to submit page (image/video type)
    submit_url = f"https://www.reddit.com/r/{subreddit}/submit?type=image"
    logger.info(f"Navigating to {submit_url}")
    page.goto(submit_url, timeout=60000)
    page.wait_for_timeout(3000)
    dismiss_over18(page)
    page.wait_for_timeout(2000)

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
                if humanizer:
                    humanizer.type_text(selector, title)
                else:
                    page.fill(selector, title)
                title_filled = True
                break
        except:
            continue

    if not title_filled:
        logger.error("Could not find title input")
        _dump_failure(page, subreddit, "no_title_input")
        return False

    # Upload file via file_chooser interception.
    # Reddit's Shreddit UI renders the upload zone inside shadow DOM of
    # <r-post-media-input>, containing a hidden <input type="file"> and
    # a <button id="device-upload-button">. Clicking the button triggers
    # the native file dialog, which we intercept with expect_file_chooser().
    page.wait_for_timeout(2000)
    logger.info(f"Uploading file: {P(file_path).name}")

    file_uploaded = False

    # Approach 1: Click the shadow DOM upload button via JS + intercept file chooser
    try:
        with page.expect_file_chooser(timeout=5000) as fc_info:
            # Click the upload button inside shadow DOM via JS
            page.evaluate("""() => {
                const el = document.querySelector('r-post-media-input');
                if (el && el.shadowRoot) {
                    const btn = el.shadowRoot.querySelector('#device-upload-button');
                    if (btn) { btn.click(); return; }
                }
                // Fallback: click the wrapper div (also inside shadow root)
                if (el && el.shadowRoot) {
                    const wrapper = el.shadowRoot.querySelector('#fileInputInnerWrapper');
                    if (wrapper) { wrapper.click(); return; }
                }
            }""")
        file_chooser = fc_info.value
        file_chooser.set_files(file_path)
        file_uploaded = True
        logger.info("File uploaded via shadow DOM button + file_chooser")
    except Exception as e:
        logger.debug(f"Shadow DOM file chooser approach failed: {e}")

    # Approach 2: Try Playwright's shadow-piercing locator for the upload button
    if not file_uploaded:
        try:
            with page.expect_file_chooser(timeout=5000) as fc_info:
                page.locator('#device-upload-button').click()
            file_chooser = fc_info.value
            file_chooser.set_files(file_path)
            file_uploaded = True
            logger.info("File uploaded via locator #device-upload-button + file_chooser")
        except Exception as e:
            logger.debug(f"Locator file chooser approach failed: {e}")

    # Approach 3: Direct set_input_files via JS on shadow DOM file input
    if not file_uploaded:
        try:
            # Playwright's set_input_files can work if we get the right element handle
            file_input = page.locator('r-post-media-input input[type="file"]')
            if file_input.count() > 0:
                file_input.first.set_input_files(file_path)
                file_uploaded = True
                logger.info("File uploaded via shadow DOM input set_input_files")
        except Exception as e:
            logger.debug(f"Shadow DOM direct input failed: {e}")

    # Approach 4: Fallback for older Reddit UI
    if not file_uploaded:
        for selector in ['input[type="file"]', 'input[accept*="image"]']:
            try:
                fi = page.locator(selector)
                if fi.count() > 0:
                    fi.first.set_input_files(file_path)
                    file_uploaded = True
                    logger.info(f"File uploaded via fallback: {selector}")
                    break
            except:
                continue

    if not file_uploaded:
        logger.error("Could not upload file — all approaches failed")
        _dump_failure(page, subreddit, "no_file_input")
        return False

    # Wait for upload to process
    logger.info("Waiting for upload to process...")
    page.wait_for_timeout(8000)

    # Verify the image actually uploaded by checking if the upload zone is still visible
    try:
        still_empty = page.evaluate("""() => {
            const el = document.querySelector('r-post-media-input');
            if (el && el.shadowRoot) {
                const info = el.shadowRoot.querySelector('#inputInfo');
                return info ? info.offsetParent !== null : false;
            }
            return false;
        }""")
        if still_empty:
            logger.warning("Upload zone still visible — image may not have uploaded")
            _dump_failure(page, subreddit, "upload_not_visible")
    except:
        pass

    # Wait for upload thumbnail/processing to finish
    page.wait_for_timeout(3000)

    # Check for upload progress/completion indicators
    try:
        loading_selectors = [
            '[class*="loading"]',
            '[class*="progress"]',
            '[class*="uploading"]',
        ]
        for sel in loading_selectors:
            try:
                loading = page.locator(sel)
                if loading.count() > 0 and loading.is_visible():
                    loading.wait_for(state="hidden", timeout=30000)
            except:
                pass
    except:
        pass

    # Mark NSFW
    if mark_nsfw:
        logger.info("Marking as NSFW...")
        page.wait_for_timeout(1000)
        nsfw_selectors = [
            'button[aria-label*="NSFW" i]',
            '[data-testid="nsfw-btn"]',
            'button:has-text("NSFW")',
        ]
        for selector in nsfw_selectors:
            try:
                btn = page.locator(selector)
                if btn.count() > 0:
                    aria = btn.get_attribute("aria-pressed")
                    if aria != "true":
                        if humanizer:
                            humanizer.human_click(selector)
                        else:
                            btn.click()
                    break
            except:
                continue

    # Select flair if specified
    if flair:
        page.wait_for_timeout(1000)
        select_flair(page, flair)

    # Check for required flair before submitting
    try:
        flair_error = page.locator('text="Your post must contain post flair"')
        flair_btn = page.locator('#post-flair-modal, r-post-flairs-modal, button:has-text("Add flair")')
        if flair_error.count() > 0 or (flair_btn.count() > 0 and flair_btn.first.get_attribute("flairs-required") is not None):
            logger.warning(f"Flair required for r/{subreddit} — attempting to select first available flair")
            _try_select_flair(page)
            page.wait_for_timeout(1000)
    except Exception as e:
        logger.debug(f"Flair check: {e}")

    # Submit
    logger.info("Submitting post...")
    page.wait_for_timeout(1000)

    # Reddit's new Shreddit UI uses custom web components for the submit button:
    #   <r-post-form-submit-button id="submit-post-button">
    # The actual <button> is rendered inside the shadow DOM.
    # We try multiple approaches: ID selector, shadow DOM pierce, classic selectors.
    submit_selectors = [
        '#submit-post-button',                     # New Shreddit: custom element by ID
        'r-post-form-submit-button[post-action-type="submit"]',  # New Shreddit: by tag+attr
        'button[type="submit"]:has-text("Post")',  # Old Reddit
        '[data-testid="submit-post-btn"]',         # Old Reddit test ID
        'button:has-text("Post")',                  # Generic fallback
    ]

    submitted = False
    for selector in submit_selectors:
        try:
            btn = page.locator(selector)
            if btn.count() > 0:
                # For custom elements, click triggers the shadow DOM button
                btn.first.click()
                submitted = True
                logger.info(f"Clicked submit via: {selector}")
                break
        except Exception as e:
            logger.debug(f"Submit selector {selector} failed: {e}")
            continue

    # Last resort: execute JS to find and click the button
    if not submitted:
        try:
            clicked = page.evaluate("""() => {
                // Try the custom element's click
                let el = document.getElementById('submit-post-button');
                if (el) { el.click(); return 'submit-post-button'; }
                // Try shadow DOM
                let buttons = document.querySelectorAll('button');
                for (let b of buttons) {
                    if (b.textContent.trim() === 'Post' && !b.disabled) {
                        b.click(); return 'button-post-text';
                    }
                }
                return null;
            }""")
            if clicked:
                submitted = True
                logger.info(f"Clicked submit via JS: {clicked}")
        except Exception as e:
            logger.debug(f"JS submit fallback failed: {e}")

    if not submitted:
        logger.error("Could not find submit button")
        _dump_failure(page, subreddit, "no_submit_btn")
        return False

    # Wait for redirect (file posts can take longer to process)
    page.wait_for_timeout(10000)
    current_url = page.url

    post_url = _extract_post_url(current_url, subreddit)
    if post_url:
        logger.info(f"SUCCESS! Post URL: {post_url}")
        return True
    else:
        logger.warning(f"Uncertain result. URL after submit: {current_url}")
        _dump_failure(page, subreddit, "uncertain_result")
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


def main_batch(csv_file: str):
    """Batch post from CSV - groups posts by profile_id and processes each profile"""

    # Load config
    config_path = Path(__file__).parent.parent / "redgifs" / "adspower_config.json"
    if not config_path.exists():
        config_path = Path(__file__).parent / "adspower_config.json"

    config = json.loads(config_path.read_text())
    client = AdsPowerClient(config["adspower_api_base"], config["api_key"])

    # CSV path from argument
    csv_path = Path(csv_file)

    if not csv_path.exists():
        logger.error(f"CSV file not found: {csv_path}")
        return

    # Read CSV and group by profile_id
    rows_by_profile = {}
    with open(csv_path, 'r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            profile_id = row.get('profile_id', '').strip()
            if not profile_id:
                logger.warning(f"Row missing profile_id, skipping: {row}")
                continue
            if profile_id not in rows_by_profile:
                rows_by_profile[profile_id] = []
            rows_by_profile[profile_id].append(row)

    if not rows_by_profile:
        logger.error("No valid rows found in CSV (all missing profile_id?)")
        return

    logger.info(f"Found {len(rows_by_profile)} profile(s) with posts:")
    for pid, rows in rows_by_profile.items():
        logger.info(f"  {pid}: {len(rows)} posts")

    # Process each profile
    all_results = []
    total_success = 0
    total_failed = 0
    total_skipped = 0

    for profile_id, profile_rows in rows_by_profile.items():
        logger.info("=" * 50)
        logger.info(f"STARTING PROFILE: {profile_id}")
        logger.info(f"Posts to make: {len(profile_rows)}")
        logger.info("=" * 50)

        # Start browser for this profile
        browser_data = client.start_browser(profile_id)
        if not browser_data:
            logger.error(f"Failed to start browser for profile {profile_id}")
            # Mark all rows for this profile as failed
            for row in profile_rows:
                all_results.append({
                    'profile_id': profile_id,
                    'subreddit': row.get('subreddit', ''),
                    'title': row.get('title', ''),
                    'url': row.get('url', ''),
                    'flair': row.get('flair', ''),
                    'status': 'failed',
                    'error': 'Failed to start AdsPower browser',
                    'posted_at': ''
                })
                total_failed += 1
            continue

        ws_endpoint = browser_data['ws']['puppeteer']
        logger.info(f"Connecting to: {ws_endpoint}")

        try:
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

                # Post each row for this profile
                seen_urls = set()
                for i, row in enumerate(profile_rows):
                    subreddit = row.get('subreddit', '').strip()
                    title = row.get('title', '').strip()
                    url = row.get('url', '').strip()
                    flair = row.get('flair', '').strip()

                    result = {
                        'profile_id': profile_id,
                        'subreddit': subreddit,
                        'title': title,
                        'url': url,
                        'flair': flair,
                        'status': 'pending',
                        'error': '',
                        'posted_at': ''
                    }

                    # Validate
                    if not subreddit or not title or not url:
                        result['status'] = 'skipped'
                        result['error'] = 'Missing required field'
                        total_skipped += 1
                        all_results.append(result)
                        logger.warning(f"[{i+1}/{len(profile_rows)}] Skipped: missing field")
                        continue

                    if url in seen_urls:
                        result['status'] = 'skipped'
                        result['error'] = 'Duplicate URL'
                        total_skipped += 1
                        all_results.append(result)
                        logger.warning(f"[{i+1}/{len(profile_rows)}] Skipped: duplicate URL")
                        continue

                    seen_urls.add(url)

                    # Post
                    logger.info(f"[{i+1}/{len(profile_rows)}] Posting to r/{subreddit}: {title[:40]}...")
                    try:
                        success = post_link_to_subreddit(
                            page=page,
                            subreddit=subreddit,
                            title=title,
                            url=url,
                            mark_nsfw=True,
                            flair=flair if flair else None
                        )

                        if success:
                            result['status'] = 'success'
                            result['posted_at'] = datetime.now().isoformat()
                            total_success += 1
                            logger.info(f"[{i+1}/{len(profile_rows)}] SUCCESS")
                        else:
                            result['status'] = 'failed'
                            result['error'] = 'Post submission failed'
                            total_failed += 1
                            logger.error(f"[{i+1}/{len(profile_rows)}] FAILED")

                    except Exception as e:
                        result['status'] = 'failed'
                        result['error'] = str(e)
                        total_failed += 1
                        logger.error(f"[{i+1}/{len(profile_rows)}] ERROR: {e}")

                    all_results.append(result)

                    # Delay between posts (skip after last post of this profile)
                    if i < len(profile_rows) - 1:
                        logger.info("Waiting 60 seconds before next post...")
                        time.sleep(30)  # TODO: change back to 60 for production

        except Exception as e:
            logger.error(f"Error with profile {profile_id}: {e}")
            # Mark remaining rows as failed
            for row in profile_rows:
                if not any(r['url'] == row.get('url') for r in all_results):
                    all_results.append({
                        'profile_id': profile_id,
                        'subreddit': row.get('subreddit', ''),
                        'title': row.get('title', ''),
                        'url': row.get('url', ''),
                        'flair': row.get('flair', ''),
                        'status': 'failed',
                        'error': str(e),
                        'posted_at': ''
                    })
                    total_failed += 1

        logger.info(f"Finished profile {profile_id}")

    # Write final results
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = csv_path.parent / f"posts_results_{timestamp}.csv"
    fieldnames = ['profile_id', 'subreddit', 'title', 'url', 'flair', 'status', 'error', 'posted_at']
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    # Final summary
    logger.info("=" * 50)
    logger.info("BATCH COMPLETE - ALL PROFILES")
    logger.info(f"  Total:   {len(all_results)}")
    logger.info(f"  Success: {total_success}")
    logger.info(f"  Failed:  {total_failed}")
    logger.info(f"  Skipped: {total_skipped}")
    logger.info(f"  Results: {output_path}")
    logger.info("=" * 50)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--batch":
        # Expect: python script.py --batch path/to/file.csv
        if len(sys.argv) < 3:
            print("Usage: python reddit_poster_playwright.py --batch <csv_file>")
            print("Example: python reddit_poster_playwright.py --batch posts.csv")
            sys.exit(1)
        csv_file = sys.argv[2]
        main_batch(csv_file)
    else:
        main()
