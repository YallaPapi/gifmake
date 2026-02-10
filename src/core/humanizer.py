"""
Full humanization engine for Reddit browser automation.
Makes automated posting indistinguishable from manual human browsing.
"""
import random
import time
import logging

logger = logging.getLogger(__name__)


class Humanizer:
    """Humanization layer for Playwright page interactions."""

    def __init__(self, page, config=None):
        """
        Args:
            page: Playwright Page object
            config: Optional dict overriding defaults:
                - min_delay: Min seconds between posts (default 45)
                - max_delay: Max seconds between posts (default 120)
                - break_every: Take long break every N posts (default random 5-8)
                - break_duration: Long break seconds (default 180-300)
                - daily_limit: Max posts per day (default 8)
                - typing_min_ms: Min ms per keystroke (default 50)
                - typing_max_ms: Max ms per keystroke (default 150)
        """
        self.page = page
        cfg = config or {}
        self.min_delay = cfg.get("min_delay", 1)
        self.max_delay = cfg.get("max_delay", 4)
        self.break_every = cfg.get("break_every", random.randint(5, 8))
        self.break_min = cfg.get("break_min", 5)
        self.break_max = cfg.get("break_max", 10)
        self.daily_limit = cfg.get("daily_limit", 8)
        self.typing_min_ms = cfg.get("typing_min_ms", 30)
        self.typing_max_ms = cfg.get("typing_max_ms", 80)

        self._posts_this_session = 0
        self._posts_since_break = 0
        self._cadence_mode = random.choice(["bursty", "normal", "slow"])
        self._cadence_counter = 0
        self._cadence_switch_at = random.randint(3, 6)

    def warm_session(self):
        """Browse Reddit home feed to warm up the session. Call once on connect."""
        logger.info("Warming session: browsing home feed")
        try:
            self.page.goto("https://www.reddit.com", timeout=30000)
            self.page.wait_for_timeout(random.randint(2000, 4000))

            # Scroll down a few times
            for _ in range(random.randint(2, 4)):
                self.page.mouse.wheel(0, random.randint(300, 700))
                self.page.wait_for_timeout(random.randint(1000, 3000))

            # Maybe click a random post and view it
            if random.random() < 0.6:
                try:
                    posts = self.page.locator('a[data-click-id="body"]')
                    count = posts.count()
                    if count > 3:
                        idx = random.randint(0, min(count - 1, 9))
                        posts.nth(idx).click()
                        self.page.wait_for_timeout(random.randint(3000, 8000))
                        # Scroll the post a bit
                        self.page.mouse.wheel(0, random.randint(200, 500))
                        self.page.wait_for_timeout(random.randint(1000, 3000))
                except Exception:
                    pass

            logger.info("Session warm-up complete")
        except Exception as e:
            logger.warning(f"Session warm-up failed (non-fatal): {e}")

    def pre_post_browse(self, subreddit):
        """Browse the target subreddit before posting. Looks like natural discovery."""
        logger.info(f"Pre-post browsing: r/{subreddit}")
        try:
            self.page.goto(f"https://www.reddit.com/r/{subreddit}",
                          timeout=30000)
            self.page.wait_for_timeout(random.randint(2000, 4000))

            # Scroll the feed
            for _ in range(random.randint(1, 3)):
                self.page.mouse.wheel(0, random.randint(300, 600))
                self.page.wait_for_timeout(random.randint(1000, 2500))

            # Small random mouse movements
            self._jitter_mouse()

        except Exception as e:
            logger.warning(f"Pre-post browse failed (non-fatal): {e}")

    def type_text(self, selector, text):
        """Type text with human-like per-character delays."""
        try:
            element = self.page.locator(selector)
            element.click()
            self.page.wait_for_timeout(random.randint(200, 500))

            for char in text:
                delay = random.randint(self.typing_min_ms, self.typing_max_ms)
                # Occasional longer pause (thinking)
                if random.random() < 0.05:
                    delay += random.randint(200, 600)
                element.type(char, delay=0)
                self.page.wait_for_timeout(delay)
        except Exception:
            # Fallback to instant fill
            self.page.fill(selector, text)

    def human_click(self, selector):
        """Click with slight mouse movement toward target first."""
        try:
            element = self.page.locator(selector).first
            box = element.bounding_box()
            if box:
                # Move toward the element with slight randomness
                target_x = box["x"] + box["width"] / 2 + random.randint(-5, 5)
                target_y = box["y"] + box["height"] / 2 + random.randint(-3, 3)
                self.page.mouse.move(target_x, target_y)
                self.page.wait_for_timeout(random.randint(100, 300))
            element.click()
        except Exception:
            # Fallback to regular click
            self.page.click(selector)

    def wait_between_posts(self):
        """Wait between posts with cadence variation."""
        self._posts_this_session += 1
        self._posts_since_break += 1
        self._cadence_counter += 1

        # Check if we should switch cadence mode
        if self._cadence_counter >= self._cadence_switch_at:
            self._cadence_mode = random.choice(["bursty", "normal", "slow"])
            self._cadence_counter = 0
            self._cadence_switch_at = random.randint(3, 6)
            logger.info(f"Switching to {self._cadence_mode} cadence")

        # Check if long break is needed
        if self._posts_since_break >= self.break_every:
            duration = random.randint(self.break_min, self.break_max)
            logger.info(f"Taking a {duration}s break after {self._posts_since_break} posts")
            time.sleep(duration)
            self._posts_since_break = 0
            self.break_every = random.randint(5, 8)
            return

        # Cadence-based delay (scaled to configured range)
        if self._cadence_mode == "bursty":
            delay = random.uniform(self.min_delay * 0.5, self.min_delay)
        elif self._cadence_mode == "slow":
            delay = random.uniform(self.max_delay, self.max_delay * 2)
        else:  # normal
            delay = random.uniform(self.min_delay, self.max_delay)

        logger.info(f"Waiting {delay:.0f}s before next post ({self._cadence_mode} mode)")
        time.sleep(delay)

    def should_stop_for_day(self, posts_today):
        """Check if daily post limit is reached."""
        if posts_today >= self.daily_limit:
            logger.info(f"Daily limit reached: {posts_today}/{self.daily_limit}")
            return True
        return False

    def _jitter_mouse(self):
        """Small random mouse movements."""
        try:
            viewport = self.page.viewport_size
            if viewport:
                for _ in range(random.randint(1, 3)):
                    x = random.randint(100, viewport["width"] - 100)
                    y = random.randint(100, viewport["height"] - 100)
                    self.page.mouse.move(x, y)
                    self.page.wait_for_timeout(random.randint(100, 400))
        except Exception:
            pass
