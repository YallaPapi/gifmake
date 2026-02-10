"""
Reddit ban and rate limit detection.
Checks for sub bans, account suspensions, shadow bans, and rate limiting.
"""
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

DEBUG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "debug")


def _dump_page(page, label):
    """Save screenshot + HTML when something unexpected happens."""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"{ts}_{label}"
    try:
        page.screenshot(path=os.path.join(DEBUG_DIR, f"{prefix}.png"), full_page=False)
    except Exception:
        pass
    try:
        with open(os.path.join(DEBUG_DIR, f"{prefix}.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception:
        pass

# Text patterns that indicate various ban states
SUB_BAN_PATTERNS = [
    "you've been banned",
    "you have been banned",
    "you are banned from",
    "you aren't allowed to post",
    "you are not allowed to post",
    "this community has restricted posting",
    "you can't post here",
    "your account has been temporarily banned",
    "banned from participating",
]

ACCOUNT_SUSPENDED_PATTERNS = [
    "your account has been suspended",
    "this account has been suspended",
    "account suspended",
    "your account has been permanently suspended",
]

RATE_LIMIT_PATTERNS = [
    "you are doing that too much",
    "try again in",
    "take a break",
    "you're doing that too much",
]

SHADOW_BAN_INDICATORS = [
    "page not found",
    "this page is no longer available",
]


class BanStatus:
    """Result of a ban check."""
    OK = "ok"
    SUB_BANNED = "sub_banned"
    ACCOUNT_SUSPENDED = "account_suspended"
    RATE_LIMITED = "rate_limited"
    SHADOW_BANNED = "shadow_banned"
    UNKNOWN_ERROR = "unknown_error"


def check_post_result(page):
    """Check if a post submission succeeded, failed, or hit a ban.

    Call this AFTER clicking submit and waiting for the page to load.

    Args:
        page: Playwright Page object

    Returns:
        (status, detail) tuple:
            - ("ok", post_url) if post succeeded
            - ("sub_banned", reason) if banned from subreddit
            - ("rate_limited", reason) if rate limited
            - ("unknown_error", reason) if something else went wrong
    """
    current_url = page.url

    # Success: redirected to the new post
    # New Shreddit UI: ?created=t3_XXXXX
    created_match = re.search(r'created=t3_([a-z0-9]+)', current_url)
    if created_match:
        post_id = created_match.group(1)
        # Extract subreddit from URL to build clean permalink
        sub_match = re.search(r'/r/([^/?]+)', current_url)
        sub_name = sub_match.group(1) if sub_match else "unknown"
        post_url = f"https://www.reddit.com/r/{sub_name}/comments/{post_id}/"
        return BanStatus.OK, post_url

    # Old Reddit UI: /comments/XXXXX/
    if "/comments/" in current_url:
        post_url = current_url.split("?")[0]
        return BanStatus.OK, post_url

    # Check page content for ban/error messages
    try:
        body_text = page.inner_text("body").lower()
    except Exception:
        body_text = ""

    # Check for sub ban
    for pattern in SUB_BAN_PATTERNS:
        if pattern in body_text:
            logger.warning(f"Sub ban detected: {pattern}")
            _dump_page(page, "sub_banned")
            return BanStatus.SUB_BANNED, pattern

    # Check for rate limiting
    for pattern in RATE_LIMIT_PATTERNS:
        if pattern in body_text:
            wait_match = re.search(r"try again in (\d+) (minute|second|hour)", body_text)
            detail = wait_match.group(0) if wait_match else pattern
            logger.warning(f"Rate limited: {detail}")
            _dump_page(page, "rate_limited")
            return BanStatus.RATE_LIMITED, detail

    # If still on submit page, something failed
    if "/submit" in current_url:
        try:
            errors = page.locator('[class*="error"], [class*="Error"], [role="alert"]')
            if errors.count() > 0:
                error_text = errors.first.inner_text()
                _dump_page(page, "submit_error")
                return BanStatus.UNKNOWN_ERROR, error_text[:200]
        except Exception:
            pass
        _dump_page(page, "still_on_submit")
        return BanStatus.UNKNOWN_ERROR, "still on submit page"

    _dump_page(page, "unexpected_url")
    return BanStatus.UNKNOWN_ERROR, f"unexpected url: {current_url}"


def check_account_health(page):
    """Check if the Reddit account is healthy (not suspended/banned).

    Call this before starting a posting session.

    Args:
        page: Playwright Page object

    Returns:
        (status, detail) tuple:
            - ("ok", username) if account is healthy
            - ("account_suspended", reason) if suspended
            - ("shadow_banned", reason) if likely shadow banned
            - ("unknown_error", reason) if can't determine
    """
    try:
        page.goto("https://www.reddit.com/user/me", timeout=30000)
        page.wait_for_timeout(3000)

        current_url = page.url

        # If redirected to login, not logged in
        if "/login" in current_url or "/register" in current_url:
            return BanStatus.UNKNOWN_ERROR, "not logged in"

        # Check for suspension page
        try:
            body_text = page.inner_text("body").lower()
        except Exception:
            body_text = ""

        for pattern in ACCOUNT_SUSPENDED_PATTERNS:
            if pattern in body_text:
                return BanStatus.ACCOUNT_SUSPENDED, pattern

        # Try to extract username from the page
        username = None
        if "/user/" in current_url:
            parts = current_url.split("/user/")
            if len(parts) > 1:
                username = parts[1].strip("/").split("/")[0].split("?")[0]

        if username:
            return BanStatus.OK, username

        return BanStatus.OK, "logged in"

    except Exception as e:
        return BanStatus.UNKNOWN_ERROR, str(e)


def check_shadow_ban(page, username):
    """Check if an account might be shadow banned.

    Opens the user profile in a way that simulates a logged-out view.

    Args:
        page: Playwright Page object
        username: Reddit username to check

    Returns:
        (is_shadow_banned: bool, detail: str)
    """
    try:
        # Try accessing the profile via old reddit (more reliable for this check)
        page.goto(f"https://old.reddit.com/user/{username}", timeout=15000)
        page.wait_for_timeout(2000)

        body_text = page.inner_text("body").lower()

        for pattern in SHADOW_BAN_INDICATORS:
            if pattern in body_text:
                return True, f"profile shows: {pattern}"

        return False, "profile accessible"

    except Exception as e:
        return False, f"check failed: {e}"
