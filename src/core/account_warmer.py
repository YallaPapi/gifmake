"""
Account warmup system for new Reddit accounts.
Progressively builds credibility through browsing, joining, upvoting,
and commenting before ramping up posting volume.
"""
import random
import logging

from core.post_history import init_warmup, get_warmup_day, record_activity

logger = logging.getLogger(__name__)

# Day -> {max_posts, browse_minutes, join_subs, upvotes, comments}
# Each value is (min, max) and gets randomized per session.
WARMUP_SCHEDULE = {
    1:  {"posts": (0, 0),  "browse_min": (15, 25), "joins": (3, 5), "upvotes": (5, 10),  "comments": (0, 0)},
    2:  {"posts": (0, 0),  "browse_min": (15, 25), "joins": (3, 5), "upvotes": (8, 15),  "comments": (1, 2)},
    3:  {"posts": (1, 1),  "browse_min": (10, 20), "joins": (2, 4), "upvotes": (5, 10),  "comments": (2, 3)},
    4:  {"posts": (1, 2),  "browse_min": (10, 15), "joins": (2, 3), "upvotes": (5, 8),   "comments": (2, 3)},
    5:  {"posts": (2, 3),  "browse_min": (5, 10),  "joins": (1, 2), "upvotes": (3, 5),   "comments": (1, 2)},
    6:  {"posts": (2, 3),  "browse_min": (5, 10),  "joins": (1, 2), "upvotes": (3, 5),   "comments": (1, 2)},
    7:  {"posts": (3, 5),  "browse_min": (5, 10),  "joins": (0, 1), "upvotes": (2, 4),   "comments": (1, 2)},
    14: {"posts": (5, 7),  "browse_min": (3, 5),   "joins": (0, 1), "upvotes": (2, 4),   "comments": (1, 2)},
    21: {"posts": (6, 8),  "browse_min": (2, 5),   "joins": (0, 0), "upvotes": (1, 3),   "comments": (0, 1)},
    30: {"posts": (8, 10), "browse_min": (2, 5),   "joins": (0, 0), "upvotes": (1, 3),   "comments": (0, 1)},
}

# Generic comments for NSFW subs — sounds like a real person, short and natural
COMMENT_BANK = [
    "wow",
    "gorgeous",
    "absolutely stunning",
    "need more of this",
    "you look amazing",
    "so hot",
    "perfection",
    "love this",
    "omg",
    "beautiful",
    "this is incredible",
    "you're so sexy",
    "can't stop staring",
    "damn girl",
    "more please",
    "goddess",
    "body goals",
    "this made my day",
    "insane body",
    "that smile tho",
    "obsessed",
    "yes please",
    "unreal",
    "oh my god yes",
    "literally perfect",
]


def _get_schedule_for_day(day):
    """Get the warmup schedule for a specific day, interpolating between defined days."""
    # Find the closest defined day at or below the current day
    defined_days = sorted(WARMUP_SCHEDULE.keys())
    active_day = defined_days[0]
    for d in defined_days:
        if d <= day:
            active_day = d
        else:
            break
    return WARMUP_SCHEDULE[active_day]


class AccountWarmer:
    """Manages account warmup activities for a Reddit profile."""

    def __init__(self, profile_id, page):
        """
        Args:
            profile_id: AdsPower profile ID
            page: Playwright Page object
        """
        self.profile_id = profile_id
        self.page = page
        self.day = init_warmup(profile_id)
        self.schedule = _get_schedule_for_day(self.day)
        self._comments_used = set()

    def get_max_posts_today(self):
        """How many posts this account is allowed today."""
        lo, hi = self.schedule["posts"]
        return random.randint(lo, hi)

    def should_post_today(self):
        """Whether posting is allowed at all today."""
        return self.schedule["posts"][1] > 0

    def get_day(self):
        return self.day

    def run_daily_warmup(self, target_subs=None):
        """Execute today's warmup activities (browse, join, upvote, comment).

        Args:
            target_subs: List of subreddit names to use for joining/browsing.
                         If None, just browses the home feed.

        Returns:
            dict with counts of activities performed
        """
        logger.info(f"Warmup day {self.day} for {self.profile_id}")
        logger.info(f"Schedule: posts={self.schedule['posts']}, "
                     f"browse={self.schedule['browse_min']}min, "
                     f"joins={self.schedule['joins']}, "
                     f"upvotes={self.schedule['upvotes']}, "
                     f"comments={self.schedule['comments']}")

        results = {"browsed": 0, "joined": 0, "upvoted": 0, "commented": 0}

        # 1. Browse home feed
        browse_lo, browse_hi = self.schedule["browse_min"]
        browse_minutes = random.randint(browse_lo, browse_hi)
        self._browse_feed(browse_minutes)
        results["browsed"] = browse_minutes

        # 2. Join subreddits
        if target_subs:
            join_lo, join_hi = self.schedule["joins"]
            join_count = random.randint(join_lo, join_hi)
            if join_count > 0:
                subs_to_join = random.sample(target_subs, min(join_count, len(target_subs)))
                joined = self._join_subreddits(subs_to_join)
                results["joined"] = joined
                record_activity(self.profile_id, "joins", joined)

        # 3. Upvote posts (while browsing target subs)
        upvote_lo, upvote_hi = self.schedule["upvotes"]
        upvote_count = random.randint(upvote_lo, upvote_hi)
        if upvote_count > 0:
            browse_subs = random.sample(target_subs, min(3, len(target_subs))) if target_subs else []
            upvoted = self._upvote_posts(upvote_count, browse_subs)
            results["upvoted"] = upvoted
            record_activity(self.profile_id, "upvotes", upvoted)

        # 4. Leave comments
        comment_lo, comment_hi = self.schedule["comments"]
        comment_count = random.randint(comment_lo, comment_hi)
        if comment_count > 0:
            comment_subs = random.sample(target_subs, min(2, len(target_subs))) if target_subs else []
            commented = self._leave_comments(comment_count, comment_subs)
            results["commented"] = commented
            record_activity(self.profile_id, "comments", commented)

        logger.info(f"Warmup complete: {results}")
        return results

    def _browse_feed(self, minutes):
        """Browse Reddit home feed for a given number of minutes."""
        logger.info(f"Browsing home feed for ~{minutes} minutes")
        try:
            from uploaders.reddit.reddit_poster_playwright import dismiss_over18
            self.page.goto("https://www.reddit.com", timeout=30000)
            self.page.wait_for_timeout(random.randint(3000, 6000))
            dismiss_over18(self.page)

            # Each scroll+pause cycle takes roughly 5-15 seconds
            cycles = (minutes * 60) // random.randint(8, 12)
            for i in range(int(cycles)):
                # Scroll
                self.page.mouse.wheel(0, random.randint(300, 800))
                self.page.wait_for_timeout(random.randint(2000, 5000))

                # Occasionally click a post and read it
                if random.random() < 0.2:
                    try:
                        posts = self.page.locator('a[slot="full-post-link"]')
                        count = posts.count()
                        if count > 2:
                            idx = random.randint(0, min(count - 1, 15))
                            posts.nth(idx).click()
                            self.page.wait_for_timeout(random.randint(3000, 10000))
                            dismiss_over18(self.page)
                            # Scroll the post
                            self.page.mouse.wheel(0, random.randint(200, 500))
                            self.page.wait_for_timeout(random.randint(2000, 5000))
                            # Go back
                            self.page.go_back()
                            self.page.wait_for_timeout(random.randint(2000, 4000))
                    except Exception:
                        pass

                # Random mouse jitter
                if random.random() < 0.3:
                    try:
                        vp = self.page.viewport_size
                        if vp:
                            self.page.mouse.move(
                                random.randint(100, vp["width"] - 100),
                                random.randint(100, vp["height"] - 100)
                            )
                    except Exception:
                        pass

        except Exception as e:
            logger.warning(f"Browse feed error (non-fatal): {e}")

    def _join_subreddits(self, subs):
        """Join (subscribe to) subreddits by clicking the Join button."""
        joined = 0
        from uploaders.reddit.reddit_poster_playwright import dismiss_over18
        for sub in subs:
            try:
                logger.info(f"Joining r/{sub}")
                self.page.goto(f"https://www.reddit.com/r/{sub}", timeout=30000)
                self.page.wait_for_timeout(random.randint(2000, 4000))
                dismiss_over18(self.page)

                # Look for Join button (not already joined)
                join_btn = self.page.locator('button:has-text("Join"):not(:has-text("Joined"))')
                if join_btn.count() > 0 and join_btn.first.is_visible():
                    join_btn.first.click()
                    joined += 1
                    logger.info(f"Joined r/{sub}")
                    self.page.wait_for_timeout(random.randint(1000, 3000))
                else:
                    logger.info(f"Already joined r/{sub} or no button found")

                # Scroll the sub a bit to look natural
                for _ in range(random.randint(1, 3)):
                    self.page.mouse.wheel(0, random.randint(300, 600))
                    self.page.wait_for_timeout(random.randint(1500, 3000))

            except Exception as e:
                logger.warning(f"Join r/{sub} failed: {e}")

            # Random pause between subs
            self.page.wait_for_timeout(random.randint(2000, 5000))

        return joined

    def _upvote_posts(self, count, subs):
        """Upvote random posts while browsing subreddits."""
        upvoted = 0
        from uploaders.reddit.reddit_poster_playwright import dismiss_over18

        # Mix of home feed and specific subs
        urls = ["https://www.reddit.com"]
        for sub in subs:
            urls.append(f"https://www.reddit.com/r/{sub}")

        for url in urls:
            if upvoted >= count:
                break
            try:
                self.page.goto(url, timeout=30000)
                self.page.wait_for_timeout(random.randint(2000, 4000))
                dismiss_over18(self.page)

                # Scroll a bit first
                for _ in range(random.randint(2, 4)):
                    self.page.mouse.wheel(0, random.randint(300, 600))
                    self.page.wait_for_timeout(random.randint(1500, 3000))

                # Find upvote buttons
                upvote_btns = self.page.locator('button[aria-label="upvote"]')
                btn_count = upvote_btns.count()

                if btn_count > 0:
                    # Pick random posts to upvote
                    indices = random.sample(range(btn_count), min(count - upvoted, btn_count, 5))
                    for idx in indices:
                        try:
                            btn = upvote_btns.nth(idx)
                            if btn.is_visible() and btn.get_attribute("aria-pressed") != "true":
                                btn.click()
                                upvoted += 1
                                self.page.wait_for_timeout(random.randint(500, 2000))
                        except Exception:
                            continue
            except Exception as e:
                logger.warning(f"Upvote at {url} failed: {e}")

        logger.info(f"Upvoted {upvoted} posts")
        return upvoted

    def _leave_comments(self, count, subs):
        """Leave generic comments on posts in target subreddits."""
        commented = 0
        from uploaders.reddit.reddit_poster_playwright import dismiss_over18

        for sub in subs:
            if commented >= count:
                break
            try:
                self.page.goto(f"https://www.reddit.com/r/{sub}/hot", timeout=30000)
                self.page.wait_for_timeout(random.randint(2000, 4000))
                dismiss_over18(self.page)

                # Scroll and find a post to click into
                self.page.mouse.wheel(0, random.randint(200, 500))
                self.page.wait_for_timeout(random.randint(1500, 3000))

                # Click a post
                post_links = self.page.locator('a[slot="full-post-link"]')
                if post_links.count() < 2:
                    continue

                idx = random.randint(0, min(post_links.count() - 1, 8))
                post_links.nth(idx).click()
                self.page.wait_for_timeout(random.randint(3000, 6000))
                dismiss_over18(self.page)

                # Scroll down to comment area
                self.page.mouse.wheel(0, random.randint(300, 600))
                self.page.wait_for_timeout(random.randint(1000, 2000))

                # Find comment box
                comment_box = self.page.locator(
                    'div[contenteditable="true"][data-lexical-editor="true"],'
                    'shreddit-composer div[contenteditable="true"],'
                    'div[role="textbox"][contenteditable="true"]'
                )

                if comment_box.count() > 0:
                    # Pick a comment
                    comment = self._pick_comment()
                    comment_box.first.click()
                    self.page.wait_for_timeout(random.randint(500, 1000))

                    # Type character by character
                    for char in comment:
                        self.page.keyboard.type(char, delay=random.randint(30, 100))
                        if random.random() < 0.05:
                            self.page.wait_for_timeout(random.randint(200, 500))

                    self.page.wait_for_timeout(random.randint(500, 1500))

                    # Click the comment submit button
                    submit_btn = self.page.locator(
                        'button:has-text("Comment"),'
                        'button[type="submit"]:has-text("Comment")'
                    )
                    if submit_btn.count() > 0 and submit_btn.first.is_visible():
                        submit_btn.first.click()
                        commented += 1
                        logger.info(f"Commented on r/{sub}: '{comment}'")
                        self.page.wait_for_timeout(random.randint(2000, 4000))

            except Exception as e:
                logger.warning(f"Comment on r/{sub} failed: {e}")

            self.page.wait_for_timeout(random.randint(3000, 8000))

        logger.info(f"Left {commented} comments")
        return commented

    def _pick_comment(self):
        """Pick a random comment that hasn't been used this session."""
        available = [c for c in COMMENT_BANK if c not in self._comments_used]
        if not available:
            self._comments_used.clear()
            available = COMMENT_BANK
        comment = random.choice(available)
        self._comments_used.add(comment)
        return comment
