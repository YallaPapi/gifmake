#!/usr/bin/env python3
"""
Simple Reddit Link Poster - Posts RedGIFs URLs to subreddits via AdsPower browser profiles
"""

import json
import time
import re
import os
import zipfile
from pathlib import Path
from typing import Dict, Any, Optional
import requests
import logging

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


# ==============================================================================
# REDDIT SELECTORS (New Reddit UI - 2024/2025)
# ==============================================================================
"""
Reddit's new UI uses Shreddit web components. Key selectors:

CREATE POST PAGE: https://www.reddit.com/r/{subreddit}/submit

1. POST TYPE TABS (at top of form):
   - Text:  [data-testid="post-type-btn-text"] or button containing "Text"
   - Link:  [data-testid="post-type-btn-link"] or button containing "Link"
   - Image: [data-testid="post-type-btn-image"]
   - Video: [data-testid="post-type-btn-video"]
   - Poll:  [data-testid="post-type-btn-poll"]

2. TITLE INPUT:
   - textarea[name="title"] or [data-testid="post-title-input"]
   - Also: [placeholder*="title" i]

3. URL/LINK INPUT (only visible when Link tab selected):
   - input[name="url"] or [data-testid="post-url-input"]
   - Also: [placeholder*="url" i]

4. NSFW TOGGLE:
   - button[aria-label*="NSFW" i] or [data-testid="nsfw-btn"]
   - The toggle: input[type="checkbox"] near NSFW label
   - Shreddit: <shreddit-nsfw-button>

5. SUBMIT BUTTON:
   - button[type="submit"] with text "Post"
   - [data-testid="submit-post-btn"]
   - button:has-text("Post")

6. FLAIR (if required):
   - [data-testid="flair-picker"]
   - button containing "Add flair"
"""

# Selectors to try in order (fallbacks)
SELECTORS = {
    # Link tab button
    "link_tab": [
        '[data-testid="post-type-btn-link"]',
        'button[role="tab"]:has-text("Link")',
        'a[href*="/submit?type=link"]',
        '//button[contains(text(), "Link")]',  # XPath fallback
    ],

    # Title input
    "title": [
        'textarea[name="title"]',
        '[data-testid="post-title-input"]',
        'textarea[placeholder*="title" i]',
        '#title-field textarea',
    ],

    # URL input
    "url": [
        'input[name="url"]',
        '[data-testid="post-url-input"]',
        'input[placeholder*="url" i]',
        'input[type="url"]',
    ],

    # NSFW button/toggle
    "nsfw": [
        'button[aria-label*="NSFW" i]',
        '[data-testid="nsfw-btn"]',
        'shreddit-nsfw-button button',
        '//button[contains(., "NSFW")]',  # XPath
    ],

    # Submit button
    "submit": [
        'button[type="submit"]',
        '[data-testid="submit-post-btn"]',
        'button:has-text("Post")',
        '//button[contains(text(), "Post")]',  # XPath
    ],
}


class AdsPowerClient:
    """Simple AdsPower API client"""

    def __init__(self, api_base: str, api_key: str):
        self.api_base = api_base.rstrip('/')
        self.api_key = api_key

    def start_browser(self, profile_id: str) -> Optional[Dict[str, Any]]:
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


def get_chromedriver_path(chrome_version: str) -> Optional[str]:
    """Download and return path to ChromeDriver matching Chrome version"""
    major_version = chrome_version.split('.')[0]

    driver_dir = Path(__file__).parent / "chromedriver_cache"
    driver_dir.mkdir(exist_ok=True)

    driver_path = driver_dir / f"chromedriver_{major_version}.exe"

    if driver_path.exists():
        logger.info(f"Using cached ChromeDriver {major_version}")
        return str(driver_path)

    logger.info(f"Downloading ChromeDriver {major_version}...")

    try:
        if int(major_version) >= 115:
            version_url = "https://googlechromelabs.github.io/chrome-for-testing/latest-versions-per-milestone-with-downloads.json"
            resp = requests.get(version_url, timeout=30)
            data = resp.json()

            if major_version not in data.get("milestones", {}):
                logger.error(f"Chrome version {major_version} not found")
                return None

            milestone = data["milestones"][major_version]
            downloads = milestone.get("downloads", {}).get("chromedriver", [])

            win_download = None
            for dl in downloads:
                if dl.get("platform") == "win64":
                    win_download = dl.get("url")
                    break

            if not win_download:
                return None

            zip_resp = requests.get(win_download, timeout=120)
            zip_path = driver_dir / f"chromedriver_{major_version}.zip"
            zip_path.write_bytes(zip_resp.content)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file_info in zip_ref.filelist:
                    if file_info.filename.endswith('chromedriver.exe'):
                        with zip_ref.open(file_info) as source:
                            driver_path.write_bytes(source.read())
                        break

            zip_path.unlink()

            if driver_path.exists():
                logger.info(f"ChromeDriver {major_version} downloaded")
                return str(driver_path)
    except Exception as e:
        logger.error(f"Failed to download ChromeDriver: {e}")

    return None


def connect_to_adspower(ws_url: str, chrome_version: str) -> Optional[webdriver.Chrome]:
    """Connect Selenium to existing AdsPower browser"""
    driver_path = get_chromedriver_path(chrome_version)
    if not driver_path:
        return None

    options = Options()
    options.add_experimental_option("debuggerAddress", ws_url.replace("ws://", ""))

    service = Service(executable_path=driver_path)

    try:
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except Exception as e:
        # Handle version mismatch
        if "Current browser version is" in str(e):
            match = re.search(r"Current browser version is (\d+\.\d+\.\d+\.\d+)", str(e))
            if match:
                actual_version = match.group(1)
                driver_path = get_chromedriver_path(actual_version)
                if driver_path:
                    service = Service(executable_path=driver_path)
                    return webdriver.Chrome(service=service, options=options)
        logger.error(f"Failed to connect: {e}")
        return None


def find_element(driver, selector_list: list, timeout: int = 10):
    """Try multiple selectors, return first match"""
    wait = WebDriverWait(driver, timeout)

    for selector in selector_list:
        try:
            if selector.startswith('//'):
                # XPath
                element = wait.until(EC.element_to_be_clickable((By.XPATH, selector)))
            else:
                # CSS
                element = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
            return element
        except:
            continue

    return None


def post_link_to_subreddit(
    driver: webdriver.Chrome,
    subreddit: str,
    title: str,
    url: str,
    mark_nsfw: bool = True
) -> bool:
    """
    Post a link to a subreddit

    Args:
        driver: Connected Selenium driver
        subreddit: Subreddit name (without r/)
        title: Post title
        url: URL to post (e.g., RedGIFs link)
        mark_nsfw: Whether to mark as NSFW (default True)

    Returns:
        True if successful, False otherwise
    """

    # Step 1: Navigate to submit page
    submit_url = f"https://www.reddit.com/r/{subreddit}/submit"
    logger.info(f"Navigating to {submit_url}")
    driver.get(submit_url)
    time.sleep(3)  # Let page load

    # Step 2: Click "Link" tab (Reddit defaults to Text post)
    logger.info("Selecting Link post type...")

    # Try multiple approaches for Link tab
    link_clicked = False

    # Approach 1: Direct URL with type parameter
    link_url = f"https://www.reddit.com/r/{subreddit}/submit?type=link"
    driver.get(link_url)
    time.sleep(2)

    # Approach 2: Click Link tab if needed
    try:
        link_tab = find_element(driver, SELECTORS["link_tab"], timeout=5)
        if link_tab:
            link_tab.click()
            link_clicked = True
            time.sleep(1)
    except:
        pass

    # Step 3: Fill in title
    logger.info(f"Filling title: {title[:50]}...")
    title_input = find_element(driver, SELECTORS["title"], timeout=10)
    if not title_input:
        logger.error("Could not find title input")
        return False

    title_input.clear()
    title_input.send_keys(title)
    time.sleep(0.5)

    # Step 4: Fill in URL
    logger.info(f"Filling URL: {url[:50]}...")
    url_input = find_element(driver, SELECTORS["url"], timeout=10)
    if not url_input:
        logger.error("Could not find URL input - Link tab may not be selected")
        return False

    url_input.clear()
    url_input.send_keys(url)
    time.sleep(0.5)

    # Step 5: Mark as NSFW if needed
    if mark_nsfw:
        logger.info("Marking as NSFW...")
        try:
            nsfw_btn = find_element(driver, SELECTORS["nsfw"], timeout=5)
            if nsfw_btn:
                # Check if already marked
                aria_pressed = nsfw_btn.get_attribute("aria-pressed")
                if aria_pressed != "true":
                    nsfw_btn.click()
                    time.sleep(0.5)
                    logger.info("NSFW marked")
                else:
                    logger.info("Already marked NSFW")
        except Exception as e:
            logger.warning(f"Could not set NSFW: {e}")

    # Step 6: Submit
    logger.info("Submitting post...")
    submit_btn = find_element(driver, SELECTORS["submit"], timeout=10)
    if not submit_btn:
        logger.error("Could not find submit button")
        return False

    submit_btn.click()

    # Step 7: Wait for redirect (success indicator)
    time.sleep(5)
    current_url = driver.current_url

    # Check if we're on a post page (success) or still on submit (failure)
    if "/comments/" in current_url:
        logger.info(f"SUCCESS! Post created: {current_url}")
        return True
    elif "/submit" in current_url:
        logger.error("Still on submit page - post may have failed")
        # Try to find error message
        try:
            error = driver.find_element(By.CSS_SELECTOR, '[class*="error"], [class*="Error"]')
            logger.error(f"Error: {error.text}")
        except:
            pass
        return False
    else:
        logger.info(f"Redirected to: {current_url}")
        return True


def main():
    """Example usage"""

    # Load AdsPower config
    config_path = Path(__file__).parent.parent / "redgifs" / "adspower_config.json"
    if not config_path.exists():
        config_path = Path(__file__).parent / "adspower_config.json"

    config = json.loads(config_path.read_text())

    client = AdsPowerClient(config["adspower_api_base"], config["api_key"])

    # Use first profile
    profile = config["profiles"][0]
    profile_id = profile["profile_id"]
    account_name = profile["account_name"]

    logger.info(f"Starting browser for {account_name}...")

    browser_data = client.start_browser(profile_id)
    if not browser_data:
        logger.error("Failed to start browser")
        return

    ws_url = browser_data['ws']['selenium']
    chrome_version = browser_data.get('version', '140.0.0.0')

    driver = connect_to_adspower(ws_url, chrome_version)
    if not driver:
        logger.error("Failed to connect to browser")
        client.stop_browser(profile_id)
        return

    try:
        # Example post
        success = post_link_to_subreddit(
            driver=driver,
            subreddit="test",  # Change to your target subreddit
            title="Test post from automation",
            url="https://www.redgifs.com/watch/examplegif",
            mark_nsfw=True
        )

        if success:
            logger.info("Post successful!")
        else:
            logger.error("Post failed")

    finally:
        # Don't close browser - let user verify
        # driver.quit()
        pass

    # Optionally stop browser
    # client.stop_browser(profile_id)


if __name__ == "__main__":
    main()
