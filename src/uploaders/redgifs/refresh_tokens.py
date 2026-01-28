#!/usr/bin/env python3
"""
SIMPLE RedGIFs token extractor - opens AdsPower profile, grabs bearer token from network, saves to accounts.json
"""

import json
import sys
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
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

class SimpleAdsPower:
    def __init__(self, api_base: str, api_key: str):
        self.api_base = api_base.rstrip('/')
        self.api_key = api_key
        self.timeout = 30

    def start_browser(self, profile_id: str) -> Optional[Dict[str, Any]]:
        url = f"{self.api_base}/api/v1/browser/start?user_id={profile_id}&api_key={self.api_key}"
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 0:
                return data["data"]
        return None

    def stop_browser(self, profile_id: str):
        url = f"{self.api_base}/api/v1/browser/stop?user_id={profile_id}&api_key={self.api_key}"
        requests.get(url, timeout=10)


def get_chromedriver_path(chrome_version: str) -> Optional[str]:
    """Download and return path to ChromeDriver matching the Chrome version"""
    major_version = chrome_version.split('.')[0]

    # ChromeDriver directory
    driver_dir = Path(__file__).parent / "chromedriver_cache"
    driver_dir.mkdir(exist_ok=True)

    driver_path = driver_dir / f"chromedriver_{major_version}.exe"

    # Return if already downloaded
    if driver_path.exists():
        logger.info(f"Using cached ChromeDriver {major_version}")
        return str(driver_path)

    logger.info(f"Downloading ChromeDriver {major_version}...")

    try:
        # For Chrome 115+, use chrome-for-testing endpoints
        if int(major_version) >= 115:
            # Get version info
            version_url = f"https://googlechromelabs.github.io/chrome-for-testing/latest-versions-per-milestone-with-downloads.json"
            resp = requests.get(version_url, timeout=30)
            data = resp.json()

            if major_version not in data.get("milestones", {}):
                logger.error(f"Chrome version {major_version} not found")
                return None

            milestone = data["milestones"][major_version]
            downloads = milestone.get("downloads", {}).get("chromedriver", [])

            # Find Windows download
            win_download = None
            for dl in downloads:
                if dl.get("platform") == "win64":
                    win_download = dl.get("url")
                    break

            if not win_download:
                logger.error(f"No Windows download found for Chrome {major_version}")
                return None

            # Download zip
            logger.info(f"Downloading from {win_download}")
            zip_resp = requests.get(win_download, timeout=120)
            zip_path = driver_dir / f"chromedriver_{major_version}.zip"
            zip_path.write_bytes(zip_resp.content)

            # Extract
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Find chromedriver.exe in zip
                for file_info in zip_ref.filelist:
                    if file_info.filename.endswith('chromedriver.exe'):
                        # Extract to driver_path
                        with zip_ref.open(file_info) as source:
                            driver_path.write_bytes(source.read())
                        break

            # Clean up zip
            zip_path.unlink()

            if driver_path.exists():
                logger.info(f"✅ ChromeDriver {major_version} downloaded successfully")
                return str(driver_path)
            else:
                logger.error("Failed to extract chromedriver.exe from zip")
                return None
        else:
            logger.error(f"Chrome version {major_version} too old (< 115)")
            return None

    except Exception as e:
        logger.error(f"Failed to download ChromeDriver: {e}")
        return None

def extract_bearer_token(ws_url: str, chrome_version: str) -> Optional[str]:
    """Connect to browser, capture first api.redgifs.com request Authorization header"""

    # Get matching ChromeDriver
    driver_path = get_chromedriver_path(chrome_version)
    if not driver_path:
        logger.error("Failed to get matching ChromeDriver")
        return None

    # Connect to existing browser using experimental option
    options = Options()
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    options.add_experimental_option("debuggerAddress", ws_url.replace("ws://", ""))

    service = Service(executable_path=driver_path)

    try:
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as e:
        error_msg = str(e)

        # Check if it's a version mismatch error
        if "Current browser version is" in error_msg:
            # Parse the actual browser version from error message
            match = re.search(r"Current browser version is (\d+\.\d+\.\d+\.\d+)", error_msg)
            if match:
                actual_version = match.group(1)
                logger.warning(f"Version mismatch detected. Retrying with Chrome {actual_version}")

                # Download correct ChromeDriver
                driver_path = get_chromedriver_path(actual_version)
                if not driver_path:
                    logger.error("Failed to get matching ChromeDriver for actual version")
                    return None

                # Retry with correct driver
                service = Service(executable_path=driver_path)
                try:
                    driver = webdriver.Chrome(service=service, options=options)
                except Exception as retry_e:
                    logger.error(f"Failed to connect after retry: {retry_e}")
                    return None
            else:
                logger.error(f"Failed to connect to browser: {e}")
                logger.error("Make sure ChromeDriver is installed and matches your Chrome version")
                return None
        else:
            logger.error(f"Failed to connect to browser: {e}")
            logger.error("Make sure ChromeDriver is installed and matches your Chrome version")
            return None

    try:
        # Check all window handles to find RedGIFs tab
        logger.info(f"Found {len(driver.window_handles)} browser tabs/windows")

        redgifs_found = False
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            time.sleep(0.5)
            current_url = driver.current_url
            logger.info(f"Tab URL: {current_url}")
            if 'redgifs.com' in current_url:
                logger.info("Found RedGIFs tab, using it")
                redgifs_found = True
                break

        if not redgifs_found:
            # Close all other tabs and navigate to RedGIFs
            logger.info("No RedGIFs tab found, navigating to upload page...")
            main_handle = driver.window_handles[0]
            for handle in driver.window_handles:
                if handle != main_handle:
                    driver.switch_to.window(handle)
                    driver.close()
            driver.switch_to.window(main_handle)
            driver.get("https://www.redgifs.com/upload")

        time.sleep(5)  # Give it time to load and make API calls

        # Get network logs
        logs = driver.get_log('performance')
        logger.info(f"Captured {len(logs)} network events")

        # Collect all api.redgifs.com URLs for debugging
        api_urls = []
        all_tokens = {}

        for log in logs:
            try:
                msg = json.loads(log['message'])['message']
                if msg['method'] == 'Network.requestWillBeSent':
                    url = msg['params']['request']['url']
                    if 'api.redgifs.com' in url:
                        api_urls.append(url)
                        headers = msg['params']['request']['headers']
                        auth = headers.get('authorization') or headers.get('Authorization')
                        if auth and auth.startswith('Bearer '):
                            token = auth[7:]
                            all_tokens[url] = token
            except (KeyError, json.JSONDecodeError):
                continue

        logger.info(f"Found {len(api_urls)} api.redgifs.com requests:")
        for url in api_urls[:10]:  # Log first 10
            logger.info(f"  - {url}")

        # Look for user-authenticated endpoints (not client-only tokens)
        # Prioritize /v1/me and upload endpoints as they have full user auth
        user_endpoints = ['/v1/me', '/v2/users/', '/v2/upload', '/v2/gifs/']

        for url, token in all_tokens.items():
            if any(endpoint in url for endpoint in user_endpoints):
                logger.info(f"✅ Found token from user endpoint: {url}")
                logger.info(f"✅ Token: {token[:20]}...")
                return token

        # Fallback: grab any api.redgifs.com token
        if all_tokens:
            url, token = list(all_tokens.items())[0]
            logger.warning(f"No user endpoint token found, using token from: {url}")
            logger.info(f"✅ Token: {token[:20]}...")
            return token

        logger.warning("No authorization header found in network traffic")
        return None

    except Exception as e:
        logger.error(f"Error during token extraction: {e}")
        return None
    finally:
        try:
            driver.quit()
        except:
            pass

def main():
    # Load config
    config = json.loads(Path("adspower_config.json").read_text())
    api_base = config["adspower_api_base"]
    api_key = config["api_key"]
    profiles = config["profiles"]

    # Load accounts
    accounts = json.loads(Path("accounts.json").read_text())
    acc_map = {acc["name"]: acc for acc in accounts["accounts"]}

    client = SimpleAdsPower(api_base, api_key)

    for profile in profiles:
        profile_id = profile["profile_id"]
        account_name = profile["account_name"]

        logger.info(f"Getting token for {account_name} ({profile_id})")

        browser_data = client.start_browser(profile_id)
        if not browser_data:
            logger.error(f"Failed to start {profile_id}")
            continue

        ws_url = browser_data['ws']['selenium']

        # Get Chrome version from browser_data
        chrome_version = browser_data.get('version', '140.0.7339.81')  # Default to version seen in error
        logger.info(f"Chrome version: {chrome_version}")

        token = extract_bearer_token(ws_url, chrome_version)

        client.stop_browser(profile_id)

        if token:
            # Update accounts.json
            acc_map[account_name]["token"] = token
            logger.info(f"✅ Updated {account_name}")
        else:
            logger.error(f"❌ No token for {account_name}")

    # Save
    Path("accounts.json").write_text(json.dumps(accounts, indent=2))
    logger.info("✅ accounts.json updated")

if __name__ == "__main__":
    main()
