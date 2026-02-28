"""
Account warmup system for new Reddit accounts.

Architecture: A single browse loop where all actions emerge probabilistically
from scrolling â€” just like how real people use Reddit.

The core loop: scroll â†’ see post â†’ maybe vote â†’ maybe click â†’ maybe comment
â†’ maybe discover sub â†’ keep scrolling.

Research-backed:
- 77% upvotes / 23% downvotes (Glenski et al. 2017)
- 73% of votes cast on titles WITHOUT clicking through
- Average session 10-18 min, 2-3 sessions/day
- New users ~20 min/day, established ~30 min/day
- Sub discovery from browsing, not searching
- Natural progression: lurk â†’ vote â†’ comment â†’ post
"""
import time
import random
import logging
import json
import os
import base64
import requests
from datetime import datetime

from core.post_history import init_warmup, record_activity, record_session

logger = logging.getLogger(__name__)


def warmup_stats_ok(stats):
    """True when a warmup run actually started and produced a usable session result."""
    if not isinstance(stats, dict):
        return False
    if stats.get("ok") is False:
        return False
    # Require actual browsing activity. `sessions` / `total_sec` alone can be
    # nonzero for startup-only runs (feed load, immediate stop/proxy death).
    for key in ("scrolls", "posts_clicked", "comments", "joins",
                "upvotes", "downvotes", "subs_browsed"):
        try:
            if int(stats.get(key, 0) or 0) > 0:
                return True
        except Exception:
            continue
    return False

GROK_MODEL = "grok-4-1-fast-reasoning"
GROK_URL = "https://api.x.ai/v1/chat/completions"


# -- Probabilities that scale with account age (PHASE-BASED) --
# Lesson learned: midnight_mae banned after 43 comments + 91 votes in 4 days
# on a brand-new account. New accounts need to lurk first.
#
# Phase 1 (days 1-3):   New -- browse, start commenting
# Phase 2 (days 4-7):   Getting comfortable
# Phase 3 (days 8-14):  Regular user
# Phase 4 (days 15-21): Active user
# Phase 5 (days 22+):   Established

def _get_probs(day):
    """Action probabilities for current warmup day (phase-based).

    Each phase returns per-session randomized probabilities within a range
    centered on the phase's target value. This prevents fingerprinting
    from identical probability signatures across sessions.

    Votes are disabled (0.0) -- comments build karma, votes don't.
    """
    if day <= 3:
        # Phase 1: New but present -- browse, start commenting
        return {
            "vote_on_title": 0.0,
            "click_post": random.uniform(0.14, 0.22),
            "vote_on_post": 0.0,
            "vote_on_comment": 0.0,
            "reply_to_voted_comment": random.uniform(0.20, 0.30),
            "top_level_comment": random.uniform(0.10, 0.20),
            "check_sub": random.uniform(0.04, 0.08),
            "join_after_browse": random.uniform(0.10, 0.20),
        }
    elif day <= 7:
        # Phase 2: Getting comfortable
        return {
            "vote_on_title": 0.0,
            "click_post": random.uniform(0.16, 0.24),
            "vote_on_post": 0.0,
            "vote_on_comment": 0.0,
            "reply_to_voted_comment": random.uniform(0.35, 0.45),
            "top_level_comment": random.uniform(0.17, 0.27),
            "check_sub": random.uniform(0.05, 0.09),
            "join_after_browse": random.uniform(0.15, 0.25),
        }
    elif day <= 14:
        # Phase 3: Regular user
        return {
            "vote_on_title": 0.0,
            "click_post": random.uniform(0.18, 0.26),
            "vote_on_post": 0.0,
            "vote_on_comment": 0.0,
            "reply_to_voted_comment": random.uniform(0.45, 0.55),
            "top_level_comment": random.uniform(0.25, 0.35),
            "check_sub": random.uniform(0.06, 0.10),
            "join_after_browse": random.uniform(0.20, 0.30),
        }
    elif day <= 21:
        # Phase 4: Active user
        return {
            "vote_on_title": 0.0,
            "click_post": random.uniform(0.20, 0.28),
            "vote_on_post": 0.0,
            "vote_on_comment": 0.0,
            "reply_to_voted_comment": random.uniform(0.50, 0.60),
            "top_level_comment": random.uniform(0.30, 0.40),
            "check_sub": random.uniform(0.08, 0.12),
            "join_after_browse": random.uniform(0.30, 0.40),
        }
    else:
        # Phase 5: Established user (day 22+)
        return {
            "vote_on_title": 0.0,
            "click_post": random.uniform(0.22, 0.30),
            "vote_on_post": 0.0,
            "vote_on_comment": 0.0,
            "reply_to_voted_comment": random.uniform(0.55, 0.65),
            "top_level_comment": random.uniform(0.33, 0.43),
            "check_sub": random.uniform(0.09, 0.13),
            "join_after_browse": random.uniform(0.33, 0.43),
        }


# Per-run caps per phase (the continuous loop handles multiple runs/day)
# Target: ~1,200+ karma by week 4 at ~3 karma/comment avg.
PER_RUN_CAP_RANGES = {
    "phase1": {"comments": (2, 3),  "joins": (0, 1)},     # days 1-3
    "phase2": {"comments": (2, 4),  "joins": (1, 2)},     # days 4-7
    "phase3": {"comments": (2, 5),  "joins": (1, 2)},     # days 8-14
    "phase4": {"comments": (3, 5),  "joins": (1, 2)},     # days 15-21
    "phase5": {"comments": (3, 5),  "joins": (1, 2)},     # days 22+
}


def _get_run_caps(day):
    """Return randomized per-run caps for the current phase.

    No votes -- comments build karma, votes don't.
    Each run gets a fresh random value within the phase range.
    """
    if day <= 3:
        ranges = PER_RUN_CAP_RANGES["phase1"]
    elif day <= 7:
        ranges = PER_RUN_CAP_RANGES["phase2"]
    elif day <= 14:
        ranges = PER_RUN_CAP_RANGES["phase3"]
    elif day <= 21:
        ranges = PER_RUN_CAP_RANGES["phase4"]
    else:
        ranges = PER_RUN_CAP_RANGES["phase5"]
    return {
        "comments": random.randint(*ranges["comments"]),
        "joins": random.randint(*ranges["joins"]),
        "votes": 0,
    }


def _day_progress(day):
    """Normalize day 1..30 to 0..1 progress."""
    day = max(1, int(day or 1))
    return min((day - 1) / 29.0, 1.0)


def _get_session_plan(day):
    """Scale session length by phase. Always exactly 1 session per run.

    The continuous loop handles multiple runs per day.

    Phase 1 (days 1-3):   1 session, 10-20 min
    Phase 2 (days 4-7):   1 session, 12-22 min
    Phase 3 (days 8-14):  1 session, 15-25 min
    Phase 4 (days 15-21): 1 session, 18-28 min
    Phase 5 (days 22+):   1 session, 20-30 min
    """
    if day <= 3:
        min_session_sec = 10 * 60   # 10 min
        max_session_sec = 20 * 60   # 20 min
    elif day <= 7:
        min_session_sec = 12 * 60   # 12 min
        max_session_sec = 22 * 60   # 22 min
    elif day <= 14:
        min_session_sec = 15 * 60   # 15 min
        max_session_sec = 25 * 60   # 25 min
    elif day <= 21:
        min_session_sec = 18 * 60   # 18 min
        max_session_sec = 28 * 60   # 28 min
    else:
        # Phase 5: day 22+
        min_session_sec = 20 * 60   # 20 min
        max_session_sec = 30 * 60   # 30 min

    return {
        "min_sessions": 1,
        "max_sessions": 1,
        "min_session_sec": min_session_sec,
        "max_session_sec": max_session_sec,
    }


def _derive_age_days_from_created_at(created_at):
    """Best-effort parse of created_at and return age in days."""
    if not created_at:
        return None
    try:
        text = str(created_at).strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        return max(0, (now - dt).days)
    except Exception:
        return None


# â”€â”€ Location â†’ subreddit mapping â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

LOCATION_SUBS = {
    "new york": ["nyc", "newyorkcity", "AskNYC"],
    "los angeles": ["LosAngeles", "AskLosAngeles"],
    "chicago": ["chicago", "ChicagoSuburbs"],
    "houston": ["houston"],
    "phoenix": ["phoenix", "arizona"],
    "philadelphia": ["philadelphia"],
    "san antonio": ["sanantonio"],
    "san diego": ["sandiego"],
    "dallas": ["Dallas"],
    "austin": ["Austin", "austinfood"],
    "san francisco": ["sanfrancisco", "bayarea"],
    "seattle": ["Seattle"],
    "denver": ["Denver"],
    "nashville": ["nashville"],
    "portland": ["Portland"],
    "las vegas": ["vegas", "LasVegas"],
    "atlanta": ["Atlanta"],
    "miami": ["Miami"],
    "tampa": ["tampa"],
    "charlotte": ["Charlotte"],
    "raleigh": ["raleigh", "triangle"],
    "orlando": ["orlando"],
    "minneapolis": ["Minneapolis", "TwinCities"],
    "pittsburgh": ["pittsburgh"],
    "cleveland": ["Cleveland"],
    "columbus": ["Columbus"],
    "indianapolis": ["indianapolis"],
    "detroit": ["Detroit"],
    "boston": ["boston"],
    "dc": ["washingtondc", "nova"],
    "washington": ["washingtondc", "nova"],
    "north carolina": ["NorthCarolina"],
    "florida": ["florida"],
    "texas": ["texas"],
    "california": ["California"],
    "ohio": ["Ohio"],
    "georgia": ["Georgia"],
    "virginia": ["Virginia"],
    "tennessee": ["Tennessee"],
    "arizona": ["arizona"],
    "colorado": ["Colorado"],
}


# â”€â”€ Hobby/Interest â†’ subreddit mapping â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

INTEREST_SUBS = {
    "cats": ["cats", "IllegallySmolCats", "CatsAreAssholes", "catpics", "Catswithjobs"],
    "dogs": ["dogs", "rarepuppers", "WhatsWrongWithYourDog", "DOG", "aww"],
    "pets": ["aww", "Eyebleach", "AnimalsBeingDerps", "AnimalsBeingBros", "Zoomies"],
    "cooking": ["Cooking", "food", "FoodPorn", "MealPrepSunday", "recipes", "EatCheapAndHealthy"],
    "baking": ["Baking", "cakedecorating", "Breadit", "dessert"],
    "fitness": ["fitness", "xxfitness", "gymsnark", "progresspics", "yoga"],
    "yoga": ["yoga", "flexibility", "Meditation"],
    "skincare": ["SkincareAddiction", "30PlusSkinCare", "beauty"],
    "makeup": ["MakeupAddiction", "drugstoreMUA", "beauty"],
    "fashion": ["femalefashionadvice", "FashionReps", "OUTFITS", "thriftstorehauls"],
    "nails": ["Nails", "NailArt", "RedditLaqueristas"],
    "hair": ["Hair", "curlyhair", "FancyFollicles"],
    "travel": ["travel", "TravelPorn", "solotravel", "backpacking"],
    "hiking": ["hiking", "CampingandHiking", "EarthPorn", "NationalPark"],
    "nature": ["NatureIsFuckingLit", "EarthPorn", "interestingasfuck", "natureismetal"],
    "photography": ["itookapicture", "photocritique", "pics"],
    "music": ["Music", "spotify", "indieheads", "popheads", "hiphopheads"],
    "movies": ["movies", "MovieSuggestions", "horror", "NetflixBestOf"],
    "tv": ["television", "NetflixBestOf", "BravoRealHousewives", "LoveIsBlindOnNetflix"],
    "reality_tv": ["BravoRealHousewives", "thebachelor", "LoveIsBlindOnNetflix", "90DayFiance"],
    "true_crime": ["TrueCrime", "UnresolvedMysteries", "TrueCrimePodcasts"],
    "reading": ["books", "BookRecommendations", "suggestmeabook", "romancebooks"],
    "gaming": ["gaming", "GirlGamers", "CozyGamers", "StardewValley", "AnimalCrossing"],
    "crafts": ["crafts", "crochet", "knitting", "CrossStitch", "DIY"],
    "plants": ["houseplants", "gardening", "plantclinic", "succulents"],
    "astrology": ["astrology", "AskAstrologers", "zodiacsigns"],
    "tattoos": ["tattoos", "tattoo", "TattooDesigns"],
    "memes": ["memes", "me_irl", "meirl", "wholesomememes", "dankmemes"],
    "humor": ["funny", "ContagiousLaughter", "TikTokCringe", "Unexpected"],
    "relationships": ["relationship_advice", "TwoXChromosomes", "AskWomen", "dating_advice"],
    "self_improvement": ["selfimprovement", "DecidingToBeBetter", "getdisciplined"],
    "mental_health": ["anxiety", "depression", "mentalhealth"],
    "parenting": ["Parenting", "Mommit", "beyondthebump"],
    "wine": ["wine", "cocktails", "drunk"],
    "coffee": ["Coffee", "cafe", "espresso"],
    "thrifting": ["ThriftStoreHauls", "Frugal", "BuyItForLife"],
}


def _resolve_persona_subs(persona):
    """Turn persona interests/hobbies into a list of general subreddits."""
    subs = set()

    if hasattr(persona, "favorite_subs"):
        subs.update(persona.favorite_subs)
        all_interests = persona.hobbies + persona.interests
        location = persona.location
    elif isinstance(persona, dict):
        subs.update(persona.get("favorite_subs", []))
        all_interests = persona.get("hobbies", []) + persona.get("interests", [])
        location = persona.get("location", "")
    else:
        return list(subs)

    for interest in all_interests:
        key = interest.lower().replace(" ", "_")
        if key in INTEREST_SUBS:
            subs.update(INTEREST_SUBS[key])

    if location:
        loc_lower = location.lower()
        for loc_key, loc_subs in LOCATION_SUBS.items():
            if loc_key in loc_lower:
                subs.update(loc_subs)

    return list(subs)


def _build_persona_description(persona, attributes=None):
    """Build a short description string for Grok prompts.

    Includes gender, age, location, interests — everything Grok needs
    to write comments that sound like this specific person.
    """
    if not persona:
        return "a casual reddit user"

    parts = []

    # Pull attributes (age, gender) from the profile if available
    if attributes:
        if isinstance(attributes, dict):
            age = attributes.get("age", "")
            gender = attributes.get("gender", "")
        else:
            age = getattr(attributes, "age", "")
            gender = getattr(attributes, "gender", "")
        gender_word = {"F": "woman", "M": "man"}.get(gender, "person")
        if age:
            parts.append(f"a {age}-year-old {gender_word}")
        elif gender:
            parts.append(f"a young {gender_word}")

    if hasattr(persona, "location"):
        loc = persona.location
        hobbies = persona.hobbies
        interests = persona.interests
        traits = persona.personality_traits
    elif isinstance(persona, dict):
        loc = persona.get("location", "")
        hobbies = persona.get("hobbies", [])
        interests = persona.get("interests", [])
        traits = persona.get("personality_traits", [])
    else:
        return " ".join(parts) if parts else "a casual reddit user"

    if loc:
        parts.append(f"from {loc}")
    if hobbies:
        parts.append(f"into {', '.join(hobbies[:4])}")
    if interests:
        parts.append(f"also likes {', '.join(interests[:4])}")
    if traits:
        parts.append(f"personality: {', '.join(traits[:3])}")

    return ". ".join(parts) if parts else "a casual reddit user"


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  AccountWarmer
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class AccountWarmer:
    """Browse-loop warmup system.

    Instead of running separate "upvote 10 posts", "comment on 3 posts" tasks,
    this runs a realistic browsing session where votes, comments, and sub
    discovery happen naturally as side effects of scrolling.
    """

    def __init__(self, profile_id, page, persona=None, attributes=None,
                 grok_api_key=None,
                 account_age_days=None, account_created_at=None,
                 username=None, poetry_warmup=False, karma_target=1500):
        """
        Args:
            profile_id: AdsPower profile ID
            page: Playwright Page object
            attributes: dict with age, gender, etc. from account profile
            persona: PersonaInterests object or dict
            grok_api_key: xAI API key for contextual comment generation
            account_age_days: Optional reddit account age (days since creation)
            account_created_at: Optional ISO datetime for reddit account creation
            username: Reddit username for this account
            poetry_warmup: If True, all comments are poems replying to top
                comments for fast karma farming. Auto-disables at karma_target.
            karma_target: Karma threshold to auto-disable poetry mode (default 1500)
        """
        self.profile_id = profile_id
        self.page = page
        self.username = username or "unknown"

        # Auto-hide CupidBotOFM.ai browser plugin on every page load
        try:
            page.add_init_script("""
                new MutationObserver((_, obs) => {
                    const el = document.getElementById('wingman-preview');
                    if (el) { el.style.display = 'none'; obs.disconnect(); }
                }).observe(document.documentElement,
                           {childList: true, subtree: true});
            """)
        except Exception:
            pass

        self.persona = persona
        self.grok_api_key = grok_api_key or os.environ.get("GROK_API_KEY", "")
        db_day = init_warmup(profile_id)
        self.day = self._resolve_day(db_day, account_age_days, account_created_at)
        self.probs = _get_probs(self.day)
        self.persona_desc = _build_persona_description(persona, attributes)

        # Resolve persona into general subs
        if persona:
            self.general_subs = _resolve_persona_subs(persona)
        else:
            self.general_subs = ["aww", "memes", "funny", "me_irl", "cats",
                                 "food", "pics", "wholesomememes"]

        self._feed_url = "https://www.reddit.com/r/popular"  # Default, updated per session
        self.stop_requested = False

        # Human pacing guardrails. This prevents rapid-fire action chains.
        self._wait_scale = random.uniform(1.35, 1.75)
        self._min_wait_ms = random.randint(1000, 1800)
        self._min_action_gap_sec = random.uniform(1.8, 2.8)
        self._max_action_gap_sec = random.uniform(3.2, 5.2)
        self._last_action_ts = 0.0

        # Track clicked post URLs to avoid re-clicking the same post
        self._clicked_urls = set()

        # Top-comment hijack ratio (0.0 = always top-level, 1.0 = always hijack)
        self.hijack_ratio = random.uniform(0.30, 0.50)  # randomized per session
        self._max_comments = 0   # 0 = unlimited (overridden by run_daily_warmup)
        self._comment_fail_streak = 0  # consecutive comment submit failures
        self._comment_cooldown_until = 0  # time.time() after which commenting resumes

        # Phase-based per-run caps
        self._run_caps = _get_run_caps(self.day)
        self.min_nsfw_days = 14  # GUI can override this

        # Sub rotation tracking (Change 8)
        self._last_comment_sub = None
        self._sub_comment_counts = {}

        # Session stats (reset per run_daily_warmup)
        self.stats = {
            "upvotes": 0, "downvotes": 0, "comments": 0,
            "joins": 0, "posts_clicked": 0, "subs_browsed": 0,
            "sessions": 0, "total_sec": 0, "scrolls": 0,
        }
        self._run_failure = None
        # Per-action log for UI display: list of dicts
        # {type, sub, url, text, status, ts}
        self.action_log = []

        # Poetry warmup mode: fast karma farming via poem replies to top comments
        self.poetry_warmup = poetry_warmup
        self.karma_target = karma_target
        self._poetry_subs = []  # Assigned SFW subs for this account
        if self.poetry_warmup:
            self._poetry_subs = self._load_poetry_subs()
            # Poetry mode: more aggressive commenting (5-8 poems per run)
            self._run_caps["comments"] = random.randint(5, 8)
            # Higher click-through rate to find more posts to comment on
            self.probs["click_post"] = random.uniform(0.25, 0.35)
            self.probs["top_level_comment"] = random.uniform(0.40, 0.55)
            logger.info(f"Poetry warmup ON: {len(self._poetry_subs)} assigned subs, "
                        f"karma target: {self.karma_target}, "
                        f"comment cap: {self._run_caps['comments']}")

        phase = (1 if self.day <= 3 else 2 if self.day <= 7
                 else 3 if self.day <= 14 else 4 if self.day <= 21 else 5)
        logger.info(f"Warmer init: day {self.day} (phase {phase}), "
                    f"caps: {self._run_caps}, "
                    f"{len(self.general_subs)} general subs, "
                    f"grok={'yes' if self.grok_api_key else 'no'}")
        logger.info(
            "Human pacing: wait_scale=%.2fx, min_wait=%dms, action_gap=%.1f-%.1fs",
            self._wait_scale, self._min_wait_ms,
            self._min_action_gap_sec, self._max_action_gap_sec
        )

    def _load_poetry_subs(self):
        """Load SFW sub pool and assign a random subset for this account.

        Each account gets 30-50 random subs from the pool of ~800 to ensure
        multiple accounts don't all browse the same feeds.
        """
        pool_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "top_sfw_subs.json"
        )
        try:
            with open(pool_path, "r") as f:
                all_subs = json.load(f)
            # Assign 30-50 random subs to this account
            count = min(random.randint(30, 50), len(all_subs))
            assigned = random.sample(all_subs, count)
            logger.info(f"Poetry subs: {count} assigned from pool of {len(all_subs)}")
            return assigned
        except Exception as e:
            logger.warning(f"Failed to load poetry sub pool: {e}")
            # Fallback to high-engagement defaults
            return [
                "AskReddit", "funny", "mildlyinteresting", "todayilearned",
                "Showerthoughts", "pics", "aww", "gaming", "movies", "music",
                "food", "cats", "dogs", "memes", "nottheonion", "tifu",
                "interestingasfuck", "nextfuckinglevel", "MadeMeSmile",
                "wholesomememes", "Damnthatsinteresting", "oddlysatisfying",
            ]

    def _vote_allowed(self):
        """Check if vote cap hasn't been reached."""
        total_votes = self.stats["upvotes"] + self.stats["downvotes"]
        return total_votes < self._run_caps["votes"]

    def _comment_allowed(self):
        """Check if comment cap hasn't been reached."""
        return self.stats["comments"] < self._run_caps["comments"]

    def _sub_comment_allowed(self, sub_name):
        """Check sub rotation rules: no same-sub twice in a row, max 30% per sub."""
        if not sub_name:
            return True
        # Rule 1: Never comment in the same sub as the last comment
        if sub_name == self._last_comment_sub:
            logger.info(f"  Sub rotation: skipping r/{sub_name} (same as last comment)")
            return False
        # Rule 2: No sub gets more than 30% of this run's comment cap
        max_per_sub = max(1, int(self._run_caps["comments"] * 0.3 + 0.99))  # ceil
        count = self._sub_comment_counts.get(sub_name, 0)
        if count >= max_per_sub:
            logger.info(f"  Sub rotation: skipping r/{sub_name} ({count}/{max_per_sub} cap)")
            return False
        return True

    def _record_comment_sub(self, sub_name):
        """Track which sub the last comment was in."""
        if sub_name:
            self._last_comment_sub = sub_name
            self._sub_comment_counts[sub_name] = self._sub_comment_counts.get(sub_name, 0) + 1

    def _join_allowed(self):
        """Check if join cap hasn't been reached."""
        return self.stats["joins"] < self._run_caps["joins"]

    def _all_caps_hit(self):
        """Check if all daily caps are exhausted (can end session early)."""
        return (not self._vote_allowed() and
                not self._comment_allowed() and
                not self._join_allowed())

    def _maybe_enter_cooldown(self, action_type="comment"):
        """After N consecutive comment/reply failures, enter a randomized cooldown."""
        cooldown_threshold = random.randint(2, 4)
        if self._comment_fail_streak >= cooldown_threshold:
            cooldown_sec = random.randint(180, 420)
            self._comment_cooldown_until = time.time() + cooldown_sec
            logger.info(f"  {self._comment_fail_streak} consecutive {action_type} failures "
                        f"— comment cooldown for {cooldown_sec // 60} min (likely rate-limited)")
            self._comment_fail_streak = 0

    def _resolve_day(self, db_day, account_age_days=None, account_created_at=None):
        """Use the best available account-age signal for warmup scaling."""
        candidates = [max(1, int(db_day or 1))]

        if account_age_days is not None:
            try:
                candidates.append(max(1, int(account_age_days) + 1))
            except Exception:
                pass

        created_age = _derive_age_days_from_created_at(account_created_at)
        if created_age is not None:
            candidates.append(max(1, int(created_age) + 1))

        return max(candidates)

    def get_max_posts_today(self):
        """Posts/day ramp: no NSFW posts until day 14, then slow ramp.

        Day 1-13:  0 posts (warmup only)
        Day 14-17: 1 post/day (testing the waters)
        Day 18-24: 2-3 posts/day
        Day 25+:   3-5 posts/day
        """
        d = self.min_nsfw_days
        if self.day < d:
            return 0
        elif self.day <= d + 3:
            return 1
        elif self.day <= d + 10:
            return random.choice([2, 3])
        else:
            t = min((self.day - d - 10) / 20.0, 1.0)
            return 3 + int(round(t * 2))  # 3 -> 5

    def should_post_today(self):
        """No NSFW posting until min_nsfw_days (default 14, GUI-overridable)."""
        return self.day >= self.min_nsfw_days

    def get_day(self):
        return self.day

    def _wait_for_timeout(self, ms):
        """Interruptible version of Playwright wait_for_timeout()."""
        if ms and ms > 0:
            scaled = int(ms * self._wait_scale * random.uniform(0.9, 1.15))
            remaining = max(self._min_wait_ms, scaled)
        else:
            remaining = max(0, int(ms))
        while remaining > 0:
            if self.stop_requested:
                return False
            chunk = min(500, remaining)
            self.page.wait_for_timeout(chunk)
            remaining -= chunk
        return not self.stop_requested

    def _pre_action_pause(self):
        """Enforce a human-like gap before high-impact actions."""
        now = time.time()
        target_gap = random.uniform(self._min_action_gap_sec, self._max_action_gap_sec)
        elapsed = now - self._last_action_ts
        if elapsed < target_gap:
            wait_ms = int((target_gap - elapsed) * 1000)
            if not self._wait_for_timeout(wait_ms):
                return False

        # Occasional hesitation similar to rereading/rechecking before acting.
        if random.random() < 0.35:
            if not self._wait_for_timeout(random.randint(600, 1800)):
                return False

        self._last_action_ts = time.time()
        return not self.stop_requested

    def _log_action(self, action_type, sub="", url="", text="", status="ok",
                    style="", sentiment=""):
        """Record an individual action for the activity popout."""
        import datetime
        self.action_log.append({
            "type": action_type,
            "sub": sub,
            "url": url,
            "text": text[:200] if text else "",
            "status": status,
            "style": style,
            "sentiment": sentiment,
            "ts": datetime.datetime.now().strftime("%H:%M:%S"),
        })

    def _screenshot_error(self, context, detail=""):
        """Save a screenshot on any failure for later analysis.

        Saves to data/error_screenshots/<date>/<username>_<context>_<time>.png
        Also logs the screenshot path + page URL for traceability.
        """
        import datetime
        try:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            date_dir = datetime.datetime.now().strftime("%Y-%m-%d")
            base = os.path.join("data", "error_screenshots", date_dir)
            os.makedirs(base, exist_ok=True)
            username = getattr(self, 'username', 'unknown')
            # Sanitize context for filename
            safe_ctx = "".join(c if c.isalnum() or c in "-_" else "_" for c in context)[:50]
            filename = f"{username}_{safe_ctx}_{ts}.png"
            filepath = os.path.join(base, filename)
            self.page.screenshot(path=filepath, timeout=5000)
            page_url = self.page.url
            logger.info(f"  ERROR SCREENSHOT: {filepath} (url={page_url})")
            # Log to action_log for the report
            self._log_action("error_screenshot", url=page_url,
                             text=f"{context}: {detail}"[:200], status="error")
        except Exception as e:
            logger.debug(f"  Screenshot capture failed: {e}")

    def _capture_profile_karma(self):
        """Navigate to the account's profile page, extract karma, and screenshot.

        Saves screenshot to data/profile_screenshots/<date>/<username>.png
        Returns dict with karma values or empty dict on failure.
        """
        import datetime
        result = {}
        try:
            self.page.goto(f"https://www.reddit.com/user/{self.username}",
                           timeout=15000, wait_until="domcontentloaded")
            self._wait_for_timeout(random.randint(2000, 4000))

            # Extract karma from the profile page DOM
            karma_data = self.page.evaluate("""() => {
                const result = {};
                // Try the profile sidebar karma display
                const karmaEls = document.querySelectorAll('[id*="karma"], [data-testid*="karma"]');
                for (const el of karmaEls) {
                    const text = el.textContent.trim();
                    if (text) result['karma_element'] = text;
                }
                // Try to find specific karma values from the about/profile section
                const allText = document.body.innerText;
                // Look for "X karma" pattern
                const karmaMatch = allText.match(/(\\d[\\d,]*)\\s*karma/i);
                if (karmaMatch) result['total_karma'] = karmaMatch[1].replace(',', '');
                // Comment karma
                const commentMatch = allText.match(/(\\d[\\d,]*)\\s*comment\\s*karma/i);
                if (commentMatch) result['comment_karma'] = commentMatch[1].replace(',', '');
                // Post karma
                const postMatch = allText.match(/(\\d[\\d,]*)\\s*post\\s*karma/i);
                if (postMatch) result['post_karma'] = postMatch[1].replace(',', '');
                // Cake day / account age
                const cakeMatch = allText.match(/cake\\s*day[:\\s]*(\\w+\\s+\\d+,?\\s*\\d*)/i);
                if (cakeMatch) result['cake_day'] = cakeMatch[1];
                return result;
            }""")
            result = karma_data or {}
            logger.info(f"Profile karma for {self.username}: {result}")

            # Screenshot the profile page
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            date_dir = datetime.datetime.now().strftime("%Y-%m-%d")
            base = os.path.join("data", "profile_screenshots", date_dir)
            os.makedirs(base, exist_ok=True)
            filepath = os.path.join(base, f"{self.username}_{ts}.png")
            self.page.screenshot(path=filepath, timeout=5000)
            logger.info(f"Profile screenshot: {filepath}")

        except Exception as e:
            logger.info(f"Profile karma capture failed for {self.username}: {e}")

        return result

    # â"€â"€ Main entry point â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

    def run_daily_warmup(self, target_subs=None, session_minutes=None,
                         max_comments=None, remaining_budget=None):
        """Run age-scaled browsing sessions, return activity stats.

        Args:
            target_subs: NSFW sub names (for later-day browsing). Not used
                         directly in sessions — NSFW content appears in the
                         Home feed naturally once the account joins those subs
                         through posting.
            session_minutes: Override total session length in minutes.
                             If set, runs ONE session of this duration instead
                             of the auto-calculated multi-session plan.
            max_comments: Stop the session after this many comments.
                          0 or None = unlimited (time-based only).
            remaining_budget: Optional dict {"comments": int, "joins": int}
                              with remaining daily budget. Per-run caps are
                              clamped to not exceed this.

        Returns:
            dict with activity counts
        """
        self._run_started_at = datetime.now().isoformat()
        self.stats = {
            "upvotes": 0, "downvotes": 0, "comments": 0,
            "joins": 0, "posts_clicked": 0, "subs_browsed": 0,
            "sessions": 0, "total_sec": 0, "scrolls": 0,
        }
        self.stats["ok"] = True
        self.stats["failure_reason"] = ""
        self.stats["failure_detail"] = ""
        self.action_log = []
        self._max_comments = max_comments or 0  # 0 = unlimited
        # Reset sub rotation tracking per run
        self._last_comment_sub = None
        self._sub_comment_counts = {}
        self._run_failure = None

        # Clamp per-run caps to remaining daily budget
        if remaining_budget:
            orig_c = self._run_caps["comments"]
            orig_j = self._run_caps["joins"]
            self._run_caps["comments"] = min(
                orig_c, remaining_budget.get("comments", 999))
            self._run_caps["joins"] = min(
                orig_j, remaining_budget.get("joins", 999))
            if self._run_caps["comments"] != orig_c or self._run_caps["joins"] != orig_j:
                logger.info(
                    f"Budget-clamped caps: comments {orig_c}->{self._run_caps['comments']}, "
                    f"joins {orig_j}->{self._run_caps['joins']}")

        if session_minutes:
            # Manual override: single session of specified length
            total_sec = int(session_minutes * 60)
            logger.info(
                f"Manual session: {session_minutes}min, "
                f"max comments: {max_comments or 'unlimited'}"
            )
            self._vote_ratio = random.uniform(0.70, 0.85)
            if self._run_browse_session(session_sec=total_sec):
                self.stats["sessions"] = 1
        else:
            # Auto mode: day-scaled sessions
            plan = _get_session_plan(self.day)
            num_sessions = random.randint(plan["min_sessions"], plan["max_sessions"])
            caps = self._run_caps
            logger.info(
                f"Day {self.day}: running {num_sessions} browse sessions "
                f"(session {plan['min_session_sec']//60}-{plan['max_session_sec']//60} min, "
                f"caps: {caps['comments']}cmt/{caps['votes']}vote/{caps['joins']}join)"
            )

            for i in range(num_sessions):
                if self.stop_requested:
                    logger.info("Stop requested, skipping remaining sessions")
                    break
                if self._max_comments and self.stats["comments"] >= self._max_comments:
                    logger.info(f"Hit comment limit ({self._max_comments}), stopping")
                    break
                # Each session gets a fresh vote ratio (simulates mood)
                self._vote_ratio = random.uniform(0.70, 0.85)
                session_sec = random.randint(plan["min_session_sec"], plan["max_session_sec"])
                if not self._run_browse_session(session_sec=session_sec):
                    if not self.stop_requested:
                        logger.warning("Session aborted before feed became usable")
                    break
                self.stats["sessions"] += 1

                # Pause between sessions (compressed — in reality would be hours)
                if i < num_sessions - 1:
                    pause_ms = random.randint(15000, 45000)
                    logger.info(f"Pausing {pause_ms//1000}s between sessions")
                    if not self._wait_for_timeout(pause_ms):
                        break

        self.stats["action_log"] = list(self.action_log)
        if not warmup_stats_ok(self.stats):
            self.stats["ok"] = False
            if self.stop_requested:
                self.stats["failure_reason"] = "stopped"
                self.stats["failure_detail"] = "stop requested before warmup activity began"
            elif self._run_failure:
                self.stats["failure_reason"] = self._run_failure.get("reason", "warmup_failed")
                self.stats["failure_detail"] = self._run_failure.get("detail", "")
            else:
                self.stats["failure_reason"] = "warmup_not_started"
                self.stats["failure_detail"] = "no feed loaded and no activity was recorded"
            logger.warning(
                "Warmup aborted before usable session start: %s (%s)",
                self.stats["failure_reason"], self.stats["failure_detail"])
            return self.stats

        # Record to DB only for real runs (prevents zero-scroll false-success rows)
        record_activity(self.profile_id, "upvotes", self.stats["upvotes"])
        record_activity(self.profile_id, "comments", self.stats["comments"])
        record_activity(self.profile_id, "joins", self.stats["joins"])

        # Record this session for per-run/daily/weekly stats
        record_session(
            self.profile_id,
            started_at=self._run_started_at,
            finished_at=datetime.now().isoformat(),
            stats=self.stats,
        )

        # Capture profile karma + screenshot before closing
        karma_data = self._capture_profile_karma()
        if karma_data:
            self.stats["karma"] = karma_data

        logger.info(f"Warmup done: {self.stats}")
        return self.stats

    # â”€â”€ Browse session â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _run_browse_session(self, session_sec=None):
        """One browsing session: scroll â†’ react â†’ scroll.

        Time scales with day if session_sec is not provided.
        Starts on Home or Popular feed, with occasional detours into subs.
        """
        from uploaders.reddit.reddit_poster_playwright import dismiss_over18

        if session_sec is None:
            session_sec = random.randint(900, 1800)  # fallback 15-30 min
        start = time.time()
        self._clicked_urls = set()  # Reset per session

        # Feed URL candidates â€" try in order until one has posts
        if self.poetry_warmup and self._poetry_subs:
            # Poetry mode: browse specific subs (rising/hot) for variety
            session_subs = random.sample(
                self._poetry_subs, min(random.randint(3, 5), len(self._poetry_subs))
            )
            # Mix of /rising and /hot — rising has fewer posts but higher ROI
            feed_candidates = []
            for sub in session_subs:
                sort = random.choice(["rising", "hot", "hot"])  # 2:1 hot vs rising
                feed_candidates.append(f"https://www.reddit.com/r/{sub}/{sort}")
            # Always include one general feed as fallback
            feed_candidates.append(random.choice([
                "https://www.reddit.com/r/popular",
                "https://www.reddit.com/r/all",
            ]))
            logger.info(f"Poetry session subs: {session_subs}")
        else:
            feed_candidates = [
                "https://www.reddit.com",
                "https://www.reddit.com/r/popular",
                "https://www.reddit.com/r/all",
            ]
        random.shuffle(feed_candidates)

        _PROXY_DEAD_TOKENS = ("tunnel", "socks", "proxy", "err_proxy",
                               "net::err_connection", "net::err_timed_out")
        _BROWSER_CLOSED_TOKENS = (
            "target page, context or browser has been closed",
            "page has been closed",
            "browser has been closed",
            "target closed",
            "context closed",
            "connection closed",
            "session closed",
        )

        feed_url = None
        proxy_dead = False
        for candidate in feed_candidates:
            if self.stop_requested:
                self._run_failure = {
                    "reason": "stopped",
                    "detail": "stop requested during feed load",
                }
                logger.info("Stop requested during feed load; session not started")
                return False
            try:
                self.page.goto(candidate, timeout=30000,
                               wait_until="domcontentloaded")
                if not self._wait_for_timeout(random.randint(2000, 5000)):
                    if self.stop_requested:
                        self._run_failure = {
                            "reason": "stopped",
                            "detail": "stop requested during feed load wait",
                        }
                        logger.info("Stop requested during feed load wait; session not started")
                        return False
                    break
                dismiss_over18(self.page)
                if self.stop_requested:
                    self._run_failure = {
                        "reason": "stopped",
                        "detail": "stop requested after feed load before first scroll",
                    }
                    logger.info("Stop requested after feed load; session not started")
                    return False
                post_count = self.page.locator('shreddit-post').count()
                if post_count >= 2:
                    feed_url = candidate
                    break
                logger.info(f"Feed {candidate}: only {post_count} posts, trying next")
            except Exception as e:
                err_lower = str(e).lower()
                logger.info(f"Feed {candidate} failed: {e}")
                self._screenshot_error("feed_load_failed", f"{candidate}: {e}"[:150])
                if any(tok in err_lower for tok in _BROWSER_CLOSED_TOKENS):
                    self._run_failure = {
                        "reason": "browser_closed",
                        "detail": "page/context/browser closed while loading Reddit feed",
                    }
                    logger.warning(f"BROWSER CLOSED during feed load — aborting: {e}")
                    return False
                # Abort immediately on proxy/tunnel death — retrying is pointless
                if any(tok in err_lower for tok in _PROXY_DEAD_TOKENS):
                    logger.warning(f"PROXY DEAD — aborting session immediately: {e}")
                    proxy_dead = True
                    break

        if not feed_url:
            if proxy_dead:
                self._run_failure = {
                    "reason": "proxy_dead",
                    "detail": "proxy/tunnel failure while loading Reddit feed",
                }
                logger.warning("Proxy tunnel is dead, session cannot start")
            else:
                self._run_failure = {
                    "reason": "no_feed_loaded",
                    "detail": "all feed candidates failed to produce posts",
                }
                logger.warning("No feed URL produced posts, aborting session")
                self._screenshot_error("no_feed_loaded", "all feed candidates failed")
            return False

        self._run_failure = None
        self._feed_url = feed_url  # Store for recovery in _explore_post
        logger.info(f"Session start: {feed_url} ({session_sec//60} min)")
        logger.info("Feed loaded, starting scroll loop")

        # â”€â”€ Core scroll loop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        scroll_count = 0
        empty_feed_count = 0  # Consecutive cycles with no posts on page
        while time.time() - start < session_sec:
            if self.stop_requested:
                if scroll_count == 0:
                    self._run_failure = {
                        "reason": "stopped",
                        "detail": "stop requested before first scroll cycle",
                    }
                    logger.info("Stop requested before first scroll; session not started")
                    return False
                logger.info("Stop requested, ending session early")
                break
            if self._max_comments and self.stats["comments"] >= self._max_comments:
                logger.info(f"Comment limit reached ({self._max_comments}), ending session")
                break
            try:
                # Scroll down
                self.page.mouse.wheel(0, random.randint(300, 700))
                if not self._wait_for_timeout(random.randint(1800, 4500)):
                    if self.stop_requested and scroll_count == 0:
                        self._run_failure = {
                            "reason": "stopped",
                            "detail": "stop requested during first scroll wait",
                        }
                        logger.info("Stop requested during first scroll wait; session not started")
                        return False
                    break
                scroll_count += 1

                elapsed_min = (time.time() - start) / 60
                # Log progress every 20 scrolls
                if scroll_count % 20 == 0:
                    logger.info(
                        f"[{elapsed_min:.0f}m] Scroll #{scroll_count} | "
                        f"votes={self.stats['upvotes']}up/{self.stats['downvotes']}down "
                        f"comments={self.stats['comments']} "
                        f"clicks={self.stats['posts_clicked']} "
                        f"joins={self.stats['joins']}")

                # Stale feed detection: if no posts visible for 5 cycles, try alt feed
                post_count = self.page.locator('shreddit-post').count()
                if post_count < 2:
                    empty_feed_count += 1
                    if empty_feed_count >= 5:
                        # Try alternative feed URLs, not just the same one
                        if self.poetry_warmup and self._poetry_subs:
                            # Poetry mode: try different subs from pool
                            alt_subs = random.sample(
                                self._poetry_subs,
                                min(3, len(self._poetry_subs))
                            )
                            alt_feeds = [
                                f"https://www.reddit.com/r/{s}/hot"
                                for s in alt_subs
                            ]
                        else:
                            alt_feeds = [
                                "https://www.reddit.com",
                                "https://www.reddit.com/r/popular",
                                "https://www.reddit.com/r/all",
                            ]
                        recovered = False
                        for alt in alt_feeds:
                            logger.info(f"  Stale feed, trying {alt}...")
                            try:
                                self.page.goto(alt, timeout=15000,
                                               wait_until="domcontentloaded")
                                self._wait_for_timeout(random.randint(3000, 5000))
                                dismiss_over18(self.page)
                                if self.page.locator('shreddit-post').count() >= 2:
                                    feed_url = alt
                                    self._feed_url = alt
                                    recovered = True
                                    logger.info(f"  Recovered on {alt}")
                                    break
                            except Exception:
                                continue
                        empty_feed_count = 0
                        if not recovered:
                            wait_ms = random.randint(20000, 40000)
                            logger.info(f"  All feeds empty, waiting {wait_ms // 1000}s...")
                            self._wait_for_timeout(wait_ms)
                    continue
                else:
                    empty_feed_count = 0

                # Mouse jitter (natural movement)
                if random.random() < random.uniform(0.10, 0.20):
                    self._jitter_mouse()

                # Poetry mode: hop to a new sub every 15-25 scrolls for variety
                if (self.poetry_warmup and self._poetry_subs
                        and scroll_count > 0 and scroll_count % random.randint(15, 25) == 0):
                    new_sub = random.choice(self._poetry_subs)
                    sort = random.choice(["hot", "hot", "rising"])
                    new_url = f"https://www.reddit.com/r/{new_sub}/{sort}"
                    logger.info(f"  Poetry sub-hop: navigating to r/{new_sub}/{sort}")
                    try:
                        self.page.goto(new_url, timeout=15000,
                                       wait_until="domcontentloaded")
                        self._wait_for_timeout(random.randint(2000, 4000))
                        dismiss_over18(self.page)
                        feed_url = new_url
                        self._feed_url = new_url
                        self._clicked_urls = set()  # Reset for new sub
                    except Exception as e:
                        logger.info(f"  Sub-hop failed: {e}")

                # â"€â"€ Vote on a post title (without clicking in) â"€â"€â"€â"€â"€â"€â"€â"€
                # Daily cap: end session early if all caps hit
                if self._all_caps_hit():
                    logger.info("All daily caps reached, ending session early")
                    break

                if self._vote_allowed() and random.random() < self.probs["vote_on_title"]:
                    self._vote_in_feed()

                # -- Click into a post --
                if random.random() < self.probs["click_post"]:
                    self._explore_post()

                    # After returning from a post, we're back on the feed.
                    # Sometimes the page state is funky, small pause.
                    self._wait_for_timeout(random.randint(1000, 2500))

            except Exception as e:
                err_lower = str(e).lower()
                logger.info(f"Scroll loop error: {e}")
                self._screenshot_error("scroll_loop_exception", str(e)[:150])
                if any(tok in err_lower for tok in _BROWSER_CLOSED_TOKENS):
                    logger.warning(f"BROWSER CLOSED mid-session — aborting: {e}")
                    if scroll_count == 0:
                        self._run_failure = {
                            "reason": "browser_closed",
                            "detail": "page/context/browser closed during first scroll cycle",
                        }
                        return False
                    break
                # Proxy/tunnel dead — abort immediately, no recovery possible
                if any(tok in err_lower for tok in _PROXY_DEAD_TOKENS):
                    logger.warning(f"PROXY DEAD mid-session — aborting: {e}")
                    if scroll_count == 0:
                        self._run_failure = {
                            "reason": "proxy_dead",
                            "detail": "proxy/tunnel failure during first scroll cycle",
                        }
                        return False
                    break
                # Try to recover by going back to feed
                try:
                    self.page.goto(feed_url, timeout=15000,
                                   wait_until="domcontentloaded")
                    self._wait_for_timeout(random.randint(2000, 4000))
                    dismiss_over18(self.page)
                    empty_feed_count = 0
                except Exception:
                    break

        logger.info(f"Session scrolls: {scroll_count} total cycles")
        self.stats["scrolls"] += scroll_count

        elapsed = int(time.time() - start)
        self.stats["total_sec"] += elapsed
        logger.info(f"Session done: {elapsed}s, "
                    f"votes={self.stats['upvotes']}+{self.stats['downvotes']}, "
                    f"comments={self.stats['comments']}, "
                    f"joins={self.stats['joins']}")
        return True

    # â”€â”€ Feed actions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _vote_in_feed(self):
        """Vote on a post in the feed by its title (without clicking in).

        73% of real Reddit votes happen this way — voting on headlines.
        Vote buttons are inside shreddit-post shadow DOM with text "Upvote"/"Downvote".
        The JS checks aria-pressed !== 'true' so already-voted posts are skipped.
        """
        try:
            if not self._pre_action_pause():
                return
            is_upvote = random.random() < self._vote_ratio
            btn_text = "Upvote" if is_upvote else "Downvote"

            posts = self.page.locator('shreddit-post')
            count = posts.count()
            if count < 2:
                logger.info(f"  Feed vote: only {count} shreddit-post elements")
                return

            # Pick from the bottom 12 posts (most recently scrolled to),
            # try up to 5 candidates to find one we haven't voted on yet.
            bottom_start = max(0, count - 12)
            candidates = list(range(bottom_start, count))
            random.shuffle(candidates)
            candidates = candidates[:5]

            clicked = False
            idx = candidates[0]
            for try_idx in candidates:
                idx = try_idx
                clicked = self.page.evaluate(
                    """([idx, btnText]) => {
                        const posts = document.querySelectorAll('shreddit-post');
                        if (idx >= posts.length) return false;
                        const sr = posts[idx].shadowRoot;
                        if (!sr) return false;
                        for (const btn of sr.querySelectorAll('button')) {
                            if (btn.textContent.trim() === btnText
                                && btn.getAttribute('aria-pressed') !== 'true') {
                                btn.click();
                                return true;
                            }
                        }
                        return false;
                    }""",
                    [try_idx, btn_text]
                )
                if clicked:
                    break

            if clicked:
                vote_type = "upvote" if is_upvote else "downvote"
                if is_upvote:
                    self.stats["upvotes"] += 1
                    logger.info(f"  Feed vote: upvote on post {idx} (total: {self.stats['upvotes']})")
                else:
                    self.stats["downvotes"] += 1
                    logger.info(f"  Feed vote: downvote on post {idx} (total: {self.stats['downvotes']})")
                self._log_action(vote_type, text=f"Feed post #{idx}")
                self._wait_for_timeout(random.randint(700, 1600))
            else:
                logger.info(f"  Feed vote: could not click {btn_text} on post {idx}")
                self._screenshot_error("feed_vote_no_btn", f"post #{idx} btn={btn_text}")

        except Exception as e:
            logger.info(f"  Feed vote error: {e}")
            self._screenshot_error("feed_vote_exception", str(e)[:150])

    # â"€â"€ Post exploration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _explore_post(self):
        """Click into a post, read it, maybe interact, go back.

        This is where most meaningful actions happen: voting on the post,
        voting on comments, replying, discovering the subreddit.
        """
        from uploaders.reddit.reddit_poster_playwright import dismiss_over18
        try:
            if not self._pre_action_pause():
                return
            posts = self.page.locator('a[slot="full-post-link"]')
            count = posts.count()
            if count < 2:
                logger.info(f"  Explore: only {count} post links found (selector: a[slot='full-post-link'])")
                return

            # Pick from the bottom half of loaded posts (recently scrolled-to),
            # and skip any we've already clicked this session.
            bottom_start = max(0, count - 12)
            candidate_indices = list(range(bottom_start, count))
            random.shuffle(candidate_indices)

            # Get hrefs for candidates, skip already-clicked
            chosen_idx = None
            for idx in candidate_indices:
                href = self.page.evaluate(
                    """(idx) => {
                        const links = document.querySelectorAll('a[slot="full-post-link"]');
                        return idx < links.length ? (links[idx].href || '') : '';
                    }""", idx
                )
                if href and href not in self._clicked_urls:
                    chosen_idx = idx
                    self._clicked_urls.add(href)
                    break

            if chosen_idx is None:
                logger.info(f"  Explore: all {len(candidate_indices)} candidate posts already clicked")
                return

            idx = chosen_idx

            # Use JS click — Playwright's .click() fails on image posts
            # because <img> inside <slot name="post-media-container">
            # intercepts pointer events, causing 30s timeouts.
            clicked = self.page.evaluate(
                """(idx) => {
                    const links = document.querySelectorAll('a[slot="full-post-link"]');
                    if (idx >= links.length) return false;
                    links[idx].click();
                    return true;
                }""",
                idx
            )
            if not clicked:
                logger.info(f"  Explore: JS click failed on post link {idx}")
                self._screenshot_error("explore_click_failed", f"post link #{idx}")
                return

            self.stats["posts_clicked"] += 1
            # Wait for SPA navigation to complete (URL changes to /comments/)
            try:
                self.page.wait_for_url("**/comments/**", timeout=8000)
            except Exception:
                pass  # Lightbox or slow load — proceed anyway
            self._wait_for_timeout(random.randint(500, 1500))
            dismiss_over18(self.page)

            # Get post context
            post_title = self._get_post_title()
            current_sub = self._get_current_sub()
            post_url = self.page.url
            logger.info(f"  Clicked post #{self.stats['posts_clicked']}: "
                       f"r/{current_sub} - {post_title[:80]}")
            self._log_action("click", sub=current_sub, url=post_url,
                             text=post_title[:120])

            # Read the post â€” scroll through it
            scroll_count = random.randint(2, 5)
            for _ in range(scroll_count):
                self.page.mouse.wheel(0, random.randint(200, 500))
                self._wait_for_timeout(random.randint(1500, 3500))

            # â”€â”€ Maybe vote on the post â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            # -- Maybe vote on the post (check cap) --
            if self._vote_allowed() and random.random() < self.probs["vote_on_post"]:
                self._vote_on_current_post()

            # Check if we're in comment cooldown
            in_cooldown = time.time() < self._comment_cooldown_until

            # Sub rotation: check if commenting is allowed in this sub
            sub_ok = self._sub_comment_allowed(current_sub)

            # -- Maybe vote on a comment (and maybe reply) --
            if self._vote_allowed() and random.random() < self.probs["vote_on_comment"]:
                skip_reply = in_cooldown or not self._comment_allowed() or not sub_ok
                self._interact_with_comment(post_title, current_sub,
                                            skip_reply=skip_reply)

            # -- Maybe leave a top-level comment (check cap + cooldown) --
            elif (not in_cooldown and self._comment_allowed() and sub_ok
                  and random.random() < self.probs["top_level_comment"]):
                self._leave_top_comment(post_title, current_sub)

            # -- Maybe check out the subreddit (check join cap) --
            if self._join_allowed() and random.random() < self.probs["check_sub"] and current_sub:
                self._browse_and_maybe_join_sub(current_sub)
            else:
                # Go back to feed — SPA-aware: domcontentloaded + element wait
                self.page.go_back(wait_until="domcontentloaded")
                try:
                    self.page.locator('shreddit-post').first.wait_for(timeout=8000)
                except Exception:
                    logger.info("  go_back didn't reach feed, using goto")
                    self.page.goto(self._feed_url, timeout=15000,
                                   wait_until="domcontentloaded")
                self._wait_for_timeout(random.randint(500, 1500))

        except Exception as e:
            logger.info(f"  Explore post error: {e}")
            self._screenshot_error("explore_exception", str(e)[:150])
            try:
                # Navigate to feed URL instead of go_back() â€” if the click
                # failed, go_back() would leave the feed, not return to it.
                self.page.goto(self._feed_url, timeout=15000,
                               wait_until="domcontentloaded")
                self._wait_for_timeout(random.randint(1500, 3000))
            except Exception:
                pass

    def _vote_on_current_post(self):
        """Vote on the post we're currently viewing.

        The main post's vote buttons are in shreddit-post shadow DOM.
        """
        try:
            if not self._pre_action_pause():
                return
            is_upvote = random.random() < self._vote_ratio
            btn_text = "Upvote" if is_upvote else "Downvote"

            clicked = self.page.evaluate(
                """(btnText) => {
                    const post = document.querySelector('shreddit-post');
                    if (!post || !post.shadowRoot) return false;
                    for (const btn of post.shadowRoot.querySelectorAll('button')) {
                        if (btn.textContent.trim() === btnText
                            && btn.getAttribute('aria-pressed') !== 'true') {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }""",
                btn_text
            )

            if clicked:
                sub = self._get_current_sub()
                vote_type = "upvote" if is_upvote else "downvote"
                if is_upvote:
                    self.stats["upvotes"] += 1
                    logger.info(f"  Post vote: upvote on r/{sub}")
                else:
                    self.stats["downvotes"] += 1
                    logger.info(f"  Post vote: downvote on r/{sub}")
                self._log_action(vote_type, sub=sub, url=self.page.url)
                self._wait_for_timeout(random.randint(700, 1700))
            else:
                logger.info(f"  Post vote: {btn_text} button not found in shadow DOM")
                self._screenshot_error("post_vote_no_btn", f"btn={btn_text}")
        except Exception as e:
            logger.info(f"  Post vote error: {e}")
            self._screenshot_error("post_vote_exception", str(e)[:150])

    # â"€â"€ Comment interaction â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _interact_with_comment(self, post_title, sub_name, skip_reply=False):
        """Vote on a visible comment, maybe reply to it.

        If upvoted â†' positive reply. If downvoted â†' disagreeing reply.
        Comment vote buttons live inside shreddit-comment-action-row's shadow DOM,
        which is a light DOM child of shreddit-comment.
        """
        try:
            if not self._pre_action_pause():
                return
            comments = self.page.locator('shreddit-comment')
            comment_count = comments.count()
            if comment_count < 1:
                logger.info("  Comment interact: no shreddit-comment elements")
                return

            idx = random.randint(0, min(comment_count - 1, 8))
            is_upvote = random.random() < self._vote_ratio
            btn_text = "Upvote" if is_upvote else "Downvote"

            # Vote buttons are in: shreddit-comment > shreddit-comment-action-row (shadow root) > button
            clicked = self.page.evaluate(
                """([idx, btnText]) => {
                    const comments = document.querySelectorAll('shreddit-comment');
                    if (idx >= comments.length) return false;
                    const actionRow = comments[idx].querySelector('shreddit-comment-action-row');
                    if (!actionRow || !actionRow.shadowRoot) return false;
                    for (const btn of actionRow.shadowRoot.querySelectorAll('button')) {
                        if (btn.textContent.trim() === btnText
                            && btn.getAttribute('aria-pressed') !== 'true') {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }""",
                [idx, btn_text]
            )

            if clicked:
                vote_type = "upvote" if is_upvote else "downvote"
                if is_upvote:
                    self.stats["upvotes"] += 1
                else:
                    self.stats["downvotes"] += 1
                logger.info(f"  Comment vote: {btn_text.lower()} on comment {idx}")
                self._log_action(vote_type, sub=self._get_current_sub(),
                                 url=self.page.url, text=f"Comment #{idx}")
                self._wait_for_timeout(random.randint(800, 1800))

            # Maybe reply to a comment (only if vote succeeded and not in cooldown)
            if (clicked and not skip_reply
                    and random.random() < self.probs["reply_to_voted_comment"]):
                # Find a replyable comment â€” try the voted one first, then nearby
                reply_idx = None
                if self._is_replyable_comment(idx):
                    reply_idx = idx
                else:
                    # Search nearby comments for a suitable one
                    for offset in [1, 2, -1, 3, 4]:
                        alt = idx + offset
                        if 0 <= alt < comment_count and self._is_replyable_comment(alt):
                            reply_idx = alt
                            break

                if reply_idx is not None and self.grok_api_key:
                    comment_text = self._get_comment_text(reply_idx)
                    if comment_text:
                        sentiment = "agree" if is_upvote else "disagree"
                        reply = self._generate_comment(
                            post_title, sub_name, sentiment,
                            reply_to=comment_text
                        )
                        if reply:
                            self._submit_reply_to_comment(reply_idx, reply)

        except Exception as e:
            logger.info(f"  Comment interaction error: {e}")
            self._screenshot_error("comment_interact_exception", str(e)[:150])

    # Topics where commenting is too risky (factual errors, insensitivity)
    _SKIP_TOPICS = [
        "rip ", "r.i.p", "passed away", "died", "death of", "lost the battle",
        "cancer", "diagnosed", "terminal", "passed on", "gone too soon",
        "rest in peace", "tribute to", "in memoriam", "lost her", "lost him",
        "lost his", "funeral", "obituary", "tragically", "suicide",
        "killed", "murder", "shooting", "massacre",
    ]

    def _should_skip_topic(self, post_title, top_comments=None):
        """Check if this post touches a sensitive topic we shouldn't comment on."""
        text = (post_title or "").lower()
        if top_comments:
            text += " " + " ".join(c.lower() for c in top_comments[:3])
        return any(kw in text for kw in self._SKIP_TOPICS)

    def _leave_top_comment(self, post_title, sub_name):
        """Leave a comment on the current post — either top-level or hijack top comment.

        For video posts, always hijack (can't see the video, so riff off
        what the top commenter said about it instead).
        """
        if not self.grok_api_key:
            return

        if not self._pre_action_pause():
            return

        # Skip sensitive topics — too risky for factual errors or insensitivity
        top_comments = self._get_top_comments(3)
        if self._should_skip_topic(post_title, top_comments):
            logger.info(f"  Skipping comment — sensitive topic detected")
            return

        sentiment = random.choice(["positive", "positive", "positive", "agree", "neutral"])
        media_type = self._get_post_media_type()

        # Poetry mode: always hijack top comment (that's the whole strategy)
        # Video posts: always hijack (can't see the video, so riff off comments)
        # Image/text posts: use hijack_ratio slider
        should_hijack = (
            self.poetry_warmup
            or media_type == "video"
            or random.random() < self.hijack_ratio
        )

        if should_hijack:
            # Try top 3 comments for a replyable one
            reply = None
            for try_idx in range(3):
                if not self._is_replyable_comment(try_idx):
                    logger.info(
                        f"  Skipping hijack target #{try_idx}: "
                        "not replyable (stickied/mod/bot/locked)"
                    )
                    continue
                comment_text = self._get_comment_text(try_idx)
                if not comment_text:
                    continue
                reply = self._generate_comment(
                    post_title, sub_name, sentiment, reply_to=comment_text
                )
                if not reply:
                    continue
                mode = "video-hijack" if media_type == "video" else "hijack"
                logger.info(f"  {mode} reply to comment #{try_idx}: '{reply[:60]}'")
                if self._submit_reply_to_comment(try_idx, reply):
                    return
                logger.info(f"  Hijack comment #{try_idx} failed, trying next")

            # Hijack failed on all — fall back to top-level with the last reply text
            if reply:
                logger.info(f"  Hijack failed, posting as top-level instead")
                self._type_and_submit_comment(reply)
                return

        # Normal: leave a top-level comment
        comment = self._generate_comment(post_title, sub_name, sentiment)
        if not comment:
            return

        self._type_and_submit_comment(comment)

    # â”€â”€ Sub discovery â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _browse_and_maybe_join_sub(self, sub_name):
        """Browse a subreddit after seeing a post from it.

        The natural flow: saw an interesting post â†’ check out the sub â†’
        scroll â†’ click some posts â†’ maybe join â†’ go back.
        """
        from uploaders.reddit.reddit_poster_playwright import dismiss_over18

        try:
            if not self._pre_action_pause():
                return
            logger.info(f"Checking out r/{sub_name}")
            self.page.goto(
                f"https://www.reddit.com/r/{sub_name}", timeout=30000,
                wait_until="domcontentloaded"
            )
            self._wait_for_timeout(random.randint(2000, 4000))
            dismiss_over18(self.page)
            self.stats["subs_browsed"] += 1

            # Browse the sub â€” scroll, click a post or two
            for _ in range(random.randint(3, 7)):
                self.page.mouse.wheel(0, random.randint(300, 600))
                self._wait_for_timeout(random.randint(1500, 3000))

            # Maybe click into a hot post (random from top 8)
            if random.random() < random.uniform(0.30, 0.50):
                posts = self.page.locator('a[slot="full-post-link"]')
                post_count = posts.count()
                if post_count > 2:
                    if not self._pre_action_pause():
                        return
                    idx = random.randint(0, min(post_count - 1, 7))
                    # JS click to avoid image overlay interception
                    self.page.evaluate(
                        """(idx) => {
                            const links = document.querySelectorAll('a[slot="full-post-link"]');
                            if (idx < links.length) links[idx].click();
                        }""",
                        idx
                    )
                    # Wait for SPA navigation instead of fixed timeout
                    try:
                        self.page.wait_for_url("**/comments/**", timeout=8000)
                    except Exception:
                        pass
                    self._wait_for_timeout(random.randint(500, 1500))
                    dismiss_over18(self.page)

                    # Scroll the post
                    for _ in range(random.randint(1, 3)):
                        self.page.mouse.wheel(0, random.randint(200, 400))
                        self._wait_for_timeout(random.randint(1500, 3000))

                    # Maybe vote on it
                    if random.random() < random.uniform(0.20, 0.40):
                        self._vote_on_current_post()

                    self.page.go_back(wait_until="domcontentloaded")
                    try:
                        self.page.locator('shreddit-post').first.wait_for(timeout=8000)
                    except Exception:
                        pass
                    self._wait_for_timeout(random.randint(500, 1500))

            # Maybe sort by Top All Time (very natural new-sub behavior)
            if random.random() < random.uniform(0.15, 0.35):
                try:
                    self.page.goto(
                        f"https://www.reddit.com/r/{sub_name}/top/?t=all",
                        wait_until="domcontentloaded",
                        timeout=15000
                    )
                    self._wait_for_timeout(random.randint(2000, 4000))
                    for _ in range(random.randint(2, 4)):
                        self.page.mouse.wheel(0, random.randint(300, 600))
                        self._wait_for_timeout(random.randint(1500, 3000))
                except Exception:
                    pass

            # Maybe join
            if random.random() < self.probs["join_after_browse"]:
                try:
                    join_btn = self.page.locator(
                        'button:has-text("Join"):not(:has-text("Joined"))'
                    )
                    if join_btn.count() > 0 and join_btn.first.is_visible():
                        if not self._pre_action_pause():
                            return
                        join_btn.first.click()
                        self.stats["joins"] += 1
                        logger.info(f"Joined r/{sub_name}")
                        self._log_action("join", sub=sub_name,
                                         url=f"https://www.reddit.com/r/{sub_name}")
                        self._wait_for_timeout(random.randint(1000, 2500))
                except Exception:
                    pass

            # Return to feed — must use goto here since history stack
            # may be deep (feed → post → sub → top/all)
            self.page.goto(self._feed_url, timeout=15000,
                           wait_until="domcontentloaded")
            self._wait_for_timeout(random.randint(500, 1500))

        except Exception as e:
            logger.info(f"  Sub browse error: {e}")
            self._screenshot_error("sub_browse_exception", str(e)[:150])
            try:
                self.page.goto(self._feed_url, timeout=15000,
                               wait_until="domcontentloaded")
                self._wait_for_timeout(random.randint(1000, 2500))
            except Exception:
                pass

    # â"€â"€ Grok comment generation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _generate_comment(self, post_title, sub_name, sentiment,
                          reply_to=None):
        """Generate a contextual comment using Grok.

        Scrapes top comments from the page to give Grok real context
        about what the post is about (titles are often clickbait).

        Args:
            post_title: Title of the post being commented on
            sub_name: Subreddit name
            sentiment: "positive", "agree", "disagree", "negative"
            reply_to: If replying to a comment, the comment text

        Returns:
            Comment string or None on failure
        """
        if not self.grok_api_key or not post_title:
            return None

        # Scrape full context from the page — text + visual
        top_comments = self._get_top_comments(5)
        post_flair = self._get_post_flair()
        post_body = self._get_post_body()
        visual_frames = self._get_visual_context()
        if visual_frames:
            logger.info(f"  Vision: captured {len(visual_frames)} image(s) for Grok")

        # Build context block
        context_parts = [f'Post title: "{post_title[:200]}"']
        if post_body:
            context_parts.append(f'Post content: "{post_body}"')
        if post_flair:
            context_parts.append(f'Post type/flair: {post_flair}')
        if top_comments:
            context_parts.append("Top comments from other users:")
            for i, c in enumerate(top_comments, 1):
                context_parts.append(f'  {i}. "{c}"')
        context = "\n".join(context_parts)

        if reply_to:
            user_prompt = (
                f'You\'re replying to this comment on r/{sub_name}:\n'
                f'Comment: "{reply_to[:200]}"\n\n'
                f'{context}\n\n'
                f'You {"agree with" if sentiment in ("agree", "positive") else "disagree with"} '
                f'this comment.'
            )
        else:
            user_prompt = (
                f'You just saw this post on r/{sub_name} and want to comment.\n\n'
                f'{context}\n\n'
                f'Your reaction: {sentiment}'
            )

        # Pick a comment style — poetry mode forces poetry, normal uses weights
        if self.poetry_warmup:
            styles = "poetry"
        else:
            styles = random.choices(
                ["pun", "sarcasm", "absurd", "anecdote", "helpful", "question", "react"],
                weights=[20, 20, 10, 15, 20, 10, 5],
                k=1,
            )[0]

        # Store for action_log attribution
        self._last_comment_style = styles
        self._last_comment_sentiment = sentiment

        style_instructions = {
            "pun": (
                "Write a PUN or CLEVER WORDPLAY comment based on the post.\n"
                "- Reddit LOVES puns. A good pun based on the actual post content is the #1 way to get upvotes\n"
                "- Read the title and content carefully. Find a word or concept you can twist\n"
                "- 1-2 sentences max\n"
                "- The pun MUST relate to something specific in the post — generic humor gets ignored\n"
                "- Examples of how puns work on reddit:\n"
                '  Post about a cat stuck in a tree: "looks like things really escalated"\n'
                '  Post about someone\'s cooking fail: "well that plan went up in smoke"\n'
                '  Post about a dog at the beach: "he\'s really making waves out there"\n'
                '  Post about a carpenter: "nailed it"\n'
                '  Post about a bad haircut: "that\'s a real shear disaster"\n'
                '  Post about someone sleeping at work: "they really rested their case"\n'
                '  Post about electricity bill: "that\'s shocking"\n'
                "- If you can't think of a good pun, a sharp sarcastic observation works too\n"
                "- NEVER force a pun that doesn't fit. A bad pun is worse than no pun\n"
            ),
            "sarcasm": (
                "Write a DRY, SARCASTIC comment about the post.\n"
                "- Deadpan delivery. State something obvious in a way that's clearly ironic\n"
                "- 1-2 sentences max\n"
                "- NOT mean-spirited, just dry wit\n"
                "- Examples:\n"
                '  Post about someone\'s genius life hack: "wow nobody has ever thought of this before"\n'
                '  Post about obvious advice: "groundbreaking research"\n'
                '  Post about a bad parking job: "and they say parallel parking is hard"\n'
                '  Post about a company doing something greedy: "shocked. truly shocked."\n'
                '  Post about someone stating the obvious: "big if true"\n'
            ),
            "absurd": (
                "Write a comment that takes the post to an ABSURD or UNEXPECTED place.\n"
                "- Exaggerate, escalate, or take the post's premise to a ridiculous conclusion\n"
                "- 1-2 sentences\n"
                "- The humor comes from the unexpected direction\n"
                "- Examples:\n"
                '  Post about a messy room: "the floor is just a big shelf if you think about it"\n'
                '  Post about a strict boss: "my boss would have charged admission for this"\n'
                '  Post about a tiny dog: "that\'s not a dog that\'s a slightly aggressive hamster"\n'
                '  Post about being tired: "i haven\'t felt rested since 2014"\n'
            ),
            "anecdote": (
                "Write a SHORT personal anecdote or relatable story.\n"
                "- Share a brief personal experience related to the post\n"
                "- 2-4 sentences, like you're telling a friend\n"
                "- Make it specific enough to feel real\n"
                "- Examples:\n"
                '  "my roommate did this exact thing last week and i still haven\'t recovered"\n'
                '  "i used to work at a place like this. lasted about 3 days before i noped out"\n'
                '  "this reminds me of when my cat knocked over my entire setup at 3am. never forgave him"\n'
                '  "went through something similar last year, honestly the best decision i ever made was just walking away"\n'
            ),
            "helpful": (
                "Write a HELPFUL or INFORMATIVE comment.\n"
                "- Add a useful detail, tip, or context the OP might not know\n"
                "- 1-3 sentences\n"
                "- Sound knowledgeable but casual, not like a textbook\n"
                "- Examples:\n"
                '  "fyi you can actually fix this by just resetting the breaker, had the same issue"\n'
                '  "the real trick is to soak it overnight, game changer"\n'
                '  "not sure if anyone mentioned this but the warranty should cover that"\n'
                '  "pro tip: don\'t do this on an empty stomach. trust me"\n'
            ),
            "question": (
                "Write a comment that ASKS A QUESTION to spark conversation.\n"
                "- Ask something specific and interesting about the post\n"
                "- 1-2 sentences\n"
                "- The kind of question that makes other people want to answer too\n"
                "- Examples:\n"
                '  "ok but how long did this actually take you"\n'
                '  "wait does this actually work or am i getting my hopes up for nothing"\n'
                '  "has anyone tried this with the newer version? curious if it still works"\n'
                '  "genuine question, how do people even find out about stuff like this"\n'
            ),
            "react": (
                "Write a short REACTION comment with personality.\n"
                "- Express a genuine emotional reaction\n"
                "- 1 sentence, punchy\n"
                "- Examples:\n"
                '  "this is the content i come to reddit for"\n'
                '  "absolutely unhinged and i am here for it"\n'
                '  "i was not prepared for that ending"\n'
                '  "the dedication here is honestly impressive"\n'
            ),
            "poetry": (
                "Write a SHORT POEM (4-8 lines) as your Reddit comment.\n"
                "Output ONLY the poem. No intro like 'Here's a poem:' or any preamble.\n\n"
                "STRUCTURE:\n"
                "- 4-8 lines, in 1-2 stanzas (separate stanzas with a blank line)\n"
                "- Ballad meter: alternate lines of ~8 syllables and ~6 syllables\n"
                "- Rhyme scheme: ABCB — lines 2 and 4 rhyme. Lines 1 and 3 don't have to\n"
                "- The LAST LINE is the most important — it must be a punchline, twist, or emotional gut-punch\n\n"
                "CONTENT:\n"
                "- Use at least ONE specific concrete detail from the post or comment you're replying to\n"
                "- Match the tone: funny post = funny poem, touching post = touching poem\n"
                "- Simple vocabulary. Conversational. Use real contractions (it's, won't, they're)\n"
                "- Lowercase lines are fine — don't over-capitalize\n\n"
                "BANNED (these are AI poetry tells):\n"
                "- Words: heart, soul, tears, dream, shine, light, journey, weave, tapestry, whisper, shimmer, cascade, embrace, cherish, beacon, unfold\n"
                "- Line starters: Oh, Ah, Alas, And so, In the\n"
                "- More than one em-dash (—) per poem\n"
                "- Ending with a moral or lesson. End with a PUNCH, not a lecture\n\n"
                "EXAMPLES (study the rhythm and punch):\n\n"
                "Animal got into something:\n"
                "  He'd watched it for weeks from the hallway,\n"
                "  The lid with its vulnerable seam.\n"
                "  At three in the morning he opened it.\n"
                "  Cold chicken. The ultimate dream.\n\n"
                "Relatable fail:\n"
                "  I told myself just one more minute,\n"
                "  I'd said that at quarter to nine.\n"
                "  The sun rose and found me still clicking.\n"
                "  The tab count had reached thirty-nine.\n\n"
                "Heartwarming:\n"
                "  She practiced the words every morning,\n"
                "  She'd say them three times in the hall.\n"
                "  And nobody knew she was nervous —\n"
                "  She walked in and nailed the whole call.\n\n"
                "Absurd:\n"
                "  He looked at the keyboard with interest,\n"
                "  One paw raised in purposeful thought.\n"
                "  He typed out his first resignation.\n"
                "  His humans were cheaper than bought.\n"
            ),
        }

        if sentiment in ("agree", "positive"):
            sentiment_note = "agreeable, positive vibe"
        elif sentiment == "neutral":
            sentiment_note = "neutral, just sharing your take — not strongly agreeing or disagreeing"
        else:
            sentiment_note = "mildly disagreeing or offering a different take, but not hostile"

        system_prompt = (
            f"You are {self.persona_desc}. You're browsing reddit on your phone.\n\n"
            f"{style_instructions[styles]}\n"
            f"Tone: {sentiment_note}\n\n"
            f"RULES (critical):\n"
            f"- Write like a real person, NOT an AI. Use contractions (don't, can't, it's)\n"
            f"- Vary your sentence length. Mix short and long\n"
            f"- Lowercase is fine but not required — match how normal redditors type\n"
            f"- No emojis unless it genuinely fits (max 1)\n"
            f"- NEVER use: 'I think', 'In my opinion', 'It's worth noting', 'Absolutely!'\n"
            f"- NEVER use these AI-sounding words: 'vibes', 'vibe', 'wholesome', 'energy', 'kudos', 'spot on', 'resonate'\n"
            f"- NEVER sound like ChatGPT — no bullet points, no 'Great question!', no formal structure\n"
            f"- Reference the ACTUAL content of the post, not just the title\n"
            f"- Occasional typos or informal spelling are fine (gonna, kinda, ngl, tbh, lowkey)\n"
            f"- Do NOT mention your city or location in every comment. Real people rarely say where they live. Only mention location if genuinely relevant (maybe 1 in 10 comments)\n"
            f"- NEVER compare anyone to Hitler, Nazis, Goebbels, Stalin, or dictators. This gets accounts banned instantly\n"
            f"- NEVER comment on politically charged content about specific politicians — just skip it\n"
            f"- NEVER make factual claims about real people — whether someone is alive, dead, sick, recovered, dating someone, etc. You don't have current info and WILL get it wrong\n"
            f"- Keep it SHORT. 1-2 sentences is ideal. 3 max. Walls of text get ignored on reddit\n\n"
            f"Reply with ONLY the comment text. Nothing else."
        )

        try:
            # Build user message — multimodal if we have visual frames
            if visual_frames:
                user_content = [{"type": "text", "text": user_prompt}]
                for i, frame_b64 in enumerate(visual_frames):
                    user_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{frame_b64}",
                        },
                    })
                if len(visual_frames) == 1:
                    user_content[0]["text"] += "\n\n(The image from this post is attached above)"
                else:
                    user_content[0]["text"] += (
                        f"\n\n({len(visual_frames)} frames from the video in this post are attached)"
                    )
            else:
                user_content = user_prompt

            resp = requests.post(
                GROK_URL,
                headers={
                    "Authorization": f"Bearer {self.grok_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROK_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 1.1 if styles == "poetry" else (1.0 if styles in ("pun", "sarcasm", "absurd") else 0.9),
                    "max_tokens": 300 if styles == "poetry" else 150,
                },
                timeout=45,
            )
            if resp.status_code != 200:
                logger.info(f"Grok API error {resp.status_code}: {resp.text[:200]}")
                return None

            comment = resp.json()["choices"][0]["message"]["content"].strip()
            comment = self._clean_comment(comment, is_poetry=(styles == "poetry"))
            if comment is None:
                return None

            log_label = "Grok poem" if styles == "poetry" else "Grok comment"
            logger.info(f"{log_label} ({sentiment}): '{comment}'")
            return comment

        except Exception as e:
            logger.info(f"Grok comment generation failed: {e}")
            return None

    # Banned words that Grok likes to use despite being told not to.
    # Post-processing filter since system-prompt bans aren't reliable.
    _BANNED_WORDS = [
        "vibes", "vibe", "wholesome", "energy", "kudos",
        "spot on", "resonate", "resonates", "resonating",
        "lowkey fire", "slaps", "hits different",
    ]
    # Extra banned words for poetry mode (AI poetry tells)
    _POETRY_BANNED_WORDS = [
        "weave", "tapestry", "whispers", "whisper", "shimmer",
        "cascade", "unfold", "embrace", "cherish", "beacon",
        "beckon", "ethereal", "celestial", "luminous",
    ]

    def _clean_comment(self, comment, is_poetry=False):
        """Post-process a Grok-generated comment.

        Strips prefixes, caps length, and rejects banned words.
        Returns cleaned comment or None if it should be discarded.
        """
        # Strip wrapping quotes
        comment = comment.strip('"\'')
        # Remove "Comment:" or similar prefix
        for prefix in ["Comment:", "Reply:", "comment:", "reply:",
                        "Poem:", "poem:", "Here's a poem:", "here's a poem:"]:
            if comment.startswith(prefix):
                comment = comment[len(prefix):].strip()
        # Cap length — poems get more room (500 chars), prose stays at 200
        max_len = 500 if is_poetry else 200
        if len(comment) > max_len:
            if is_poetry:
                # For poetry, cut at last newline before limit to preserve stanza structure
                cut = comment[:max_len].rfind('\n')
                if cut > 100:
                    comment = comment[:cut]
                else:
                    comment = comment[:max_len]
            else:
                cut = comment[:max_len].rfind('.')
                if cut > 80:
                    comment = comment[:cut + 1]
                else:
                    cut = comment[:max_len].rfind(',')
                    if cut > 80:
                        comment = comment[:cut]
                    else:
                        comment = comment[:max_len]
        # Reject if it contains banned words
        lower = comment.lower()
        for word in self._BANNED_WORDS:
            if word in lower:
                logger.info(f"  Rejected comment (banned word '{word}'): '{comment[:60]}'")
                return None
        # Poetry-specific banned words
        if is_poetry:
            for word in self._POETRY_BANNED_WORDS:
                if word in lower:
                    logger.info(f"  Rejected poem (AI tell '{word}'): '{comment[:60]}'")
                    return None
            # Reject poems starting with "Oh " or "Ah " (top AI poetry tell)
            first_line = comment.split('\n')[0].strip()
            if first_line.startswith(("Oh ", "Ah ", "Alas")):
                logger.info(f"  Rejected poem (starts with Oh/Ah/Alas): '{first_line[:40]}'")
                return None
        return comment

    # ── DOM interaction helpers ────────────────────────────────────────────────

    def _activate_comment_composer(self):
        """Activate the collapsed comment composer by clicking the trigger.

        Reddit's comment box starts collapsed as a faceplate-textarea-input
        with placeholder "Join the conversation". Clicking the textarea inside
        its shadow DOM expands it into the full rich text editor.

        Returns True if composer is now active (contenteditable visible).
        """
        activated = self.page.evaluate("""() => {
            // Check if already active (contenteditable visible)
            const eds = document.querySelectorAll(
                'div[contenteditable="true"][data-lexical-editor="true"]'
            );
            for (const ed of eds) {
                if (ed.offsetHeight > 0) return 'already_active';
            }
            // Click the collapsed trigger
            const triggers = document.querySelectorAll(
                'faceplate-textarea-input[data-testid="trigger-button"]'
            );
            for (const inp of triggers) {
                if (inp.offsetHeight > 0 && inp.shadowRoot) {
                    const ta = inp.shadowRoot.querySelector('textarea');
                    if (ta) { ta.focus(); ta.click(); return 'activated'; }
                }
            }
            return false;
        }""")
        if activated == 'activated':
            self._wait_for_timeout(random.randint(800, 1500))
        return bool(activated)

    def _type_and_submit_comment(self, comment):
        """Find the top-level comment box, type comment, submit."""
        try:
            if not self._pre_action_pause():
                return False
            # Step 1: Activate the composer (it starts collapsed)
            if not self._activate_comment_composer():
                logger.info("  Comment: no composer trigger found")
                self._screenshot_error("comment_no_composer", "trigger not found")
                return False

            # Step 2: Find the now-visible contenteditable
            comment_box = self.page.locator(
                'div[contenteditable="true"][data-lexical-editor="true"]:visible'
            )
            if comment_box.count() == 0:
                logger.info("  Comment: composer activated but no editable found")
                self._screenshot_error("comment_no_editable", "composer opened but no contenteditable")
                return False

            # Use JS click to avoid viewport-scroll timeout from plugins
            comment_box.first.evaluate("el => el.click()")
            self._wait_for_timeout(random.randint(400, 800))

            # Step 3: Type with human-like timing
            for char in comment:
                self.page.keyboard.type(char, delay=random.randint(55, 180))
                if random.random() < random.uniform(0.02, 0.07):
                    self._wait_for_timeout(random.randint(250, 750))

            self._wait_for_timeout(random.randint(500, 1200))

            # Step 4: Click the Comment button via JS (first visible, non-disabled)
            clicked = self.page.evaluate("""() => {
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    if (btn.textContent.trim() === 'Comment'
                        && btn.offsetHeight > 0
                        && !btn.disabled
                        && btn.type === 'submit') {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""")
            if not clicked:
                logger.info("  Comment: no Comment submit button found")
                self._screenshot_error("comment_no_submit_btn", "typed text but no submit button")
                return False

            # Verify: wait and check composer closed
            self._wait_for_timeout(random.randint(2000, 4500))
            still_open = self.page.evaluate("""() => {
                // Check if the top-level composer still has content
                const eds = document.querySelectorAll(
                    'div[contenteditable="true"][data-lexical-editor="true"]'
                );
                // If no visible editables remain, or first one is empty = success
                for (const ed of eds) {
                    if (ed.offsetHeight > 0 && ed.textContent.trim().length > 0)
                        return true;
                }
                return false;
            }""")

            if still_open:
                logger.info("  Comment: clicked but composer still has text — may have failed")
                self._screenshot_error("comment_submit_rejected", "submit clicked but composer still open")
                self._comment_fail_streak += 1
                self._maybe_enter_cooldown("comment")
                return False

            self._comment_fail_streak = 0
            self.stats["comments"] += 1
            comment_sub = self._get_current_sub()
            self._record_comment_sub(comment_sub)
            logger.info(f"  Comment VERIFIED (total: {self.stats['comments']}): "
                       f"'{comment[:60]}'")
            self._log_action("comment", sub=comment_sub,
                             url=self.page.url, text=comment, status="verified",
                             style=getattr(self, '_last_comment_style', ''),
                             sentiment=getattr(self, '_last_comment_sentiment', ''))
            self._wait_for_timeout(random.randint(1000, 2000))
            return True

        except Exception as e:
            logger.info(f"  Comment submit failed: {e}")
            self._screenshot_error("comment_exception", str(e)[:150])
            self._comment_fail_streak += 1
            self._maybe_enter_cooldown("comment")
            self._log_action("comment", sub=self._get_current_sub(),
                             url=self.page.url, text=comment, status="failed")
            return False

    def _submit_reply_to_comment(self, comment_idx, reply_text):
        """Click Reply on a specific comment, type reply, submit.

        Targets the Reply button directly on the shreddit-comment element
        at comment_idx, not a flat list of all Reply buttons on the page.
        """
        try:
            if not self._pre_action_pause():
                return False
            # Click the Reply button on THIS specific comment via JS
            reply_clicked = self.page.evaluate(
                """(idx) => {
                    const comments = document.querySelectorAll('shreddit-comment');
                    if (idx >= comments.length) return false;
                    // Find Reply button in this comment's direct children (light DOM)
                    const btns = comments[idx].querySelectorAll('button');
                    for (const btn of btns) {
                        if (btn.textContent.trim() === 'Reply'
                            && btn.offsetHeight > 0) {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }""",
                comment_idx
            )
            if not reply_clicked:
                logger.info(f"  Reply: no Reply button on comment {comment_idx}")
                self._screenshot_error("reply_no_btn", f"comment #{comment_idx}")
                return False

            self._wait_for_timeout(random.randint(1000, 2000))

            # The reply composer opens inline â€” activate it if collapsed
            # Look for trigger specifically NEAR this comment (not the top-level one)
            self.page.evaluate("""(idx) => {
                const comments = document.querySelectorAll('shreddit-comment');
                if (idx >= comments.length) return;
                // Find faceplate-textarea-input trigger inside or near this comment
                const triggers = comments[idx].querySelectorAll(
                    'faceplate-textarea-input[data-testid="trigger-button"]'
                );
                for (const inp of triggers) {
                    if (inp.offsetHeight > 0 && inp.shadowRoot) {
                        const ta = inp.shadowRoot.querySelector('textarea');
                        if (ta) { ta.focus(); ta.click(); return; }
                    }
                }
                // Fallback: any newly visible trigger on the page
                const allTriggers = document.querySelectorAll(
                    'faceplate-textarea-input[data-testid="trigger-button"]'
                );
                for (const inp of allTriggers) {
                    if (inp.offsetHeight > 0 && inp.shadowRoot) {
                        const ta = inp.shadowRoot.querySelector('textarea');
                        if (ta) { ta.focus(); ta.click(); return; }
                    }
                }
            }""", comment_idx)
            self._wait_for_timeout(random.randint(800, 1500))

            # Find the reply's contenteditable WITHIN this comment's subtree
            has_editable = self.page.evaluate("""(idx) => {
                const comments = document.querySelectorAll('shreddit-comment');
                if (idx >= comments.length) return false;
                const eds = comments[idx].querySelectorAll(
                    'div[contenteditable="true"][data-lexical-editor="true"]'
                );
                for (const ed of eds) {
                    if (ed.offsetHeight > 0) { ed.click(); return true; }
                }
                return false;
            }""", comment_idx)
            if not has_editable:
                logger.info("  Reply: no editable found in comment subtree")
                self._screenshot_error("reply_no_editable", f"comment #{comment_idx}")
                return False

            self._wait_for_timeout(random.randint(300, 600))

            for char in reply_text:
                self.page.keyboard.type(char, delay=random.randint(55, 180))
                if random.random() < random.uniform(0.02, 0.07):
                    self._wait_for_timeout(random.randint(250, 750))

            self._wait_for_timeout(random.randint(500, 1200))

            # Click the Comment button WITHIN this comment's subtree (not the top-level one)
            submitted = self.page.evaluate("""(idx) => {
                const comments = document.querySelectorAll('shreddit-comment');
                if (idx >= comments.length) return false;
                const btns = comments[idx].querySelectorAll('button');
                for (const btn of btns) {
                    if (btn.textContent.trim() === 'Comment'
                        && btn.offsetHeight > 0
                        && !btn.disabled) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""", comment_idx)
            if not submitted:
                logger.info("  Reply: no Comment button in comment subtree")
                self._screenshot_error("reply_no_submit_btn", f"comment #{comment_idx}")
                return False

            # Verify: wait for composer to close (editable disappears from subtree)
            self._wait_for_timeout(random.randint(2000, 4500))
            still_open = self.page.evaluate("""(idx) => {
                const comments = document.querySelectorAll('shreddit-comment');
                if (idx >= comments.length) return false;
                const eds = comments[idx].querySelectorAll(
                    'div[contenteditable="true"][data-lexical-editor="true"]'
                );
                for (const ed of eds) {
                    if (ed.offsetHeight > 0) return true;
                }
                return false;
            }""", comment_idx)

            if still_open:
                logger.info("  Reply: Comment clicked but composer still open — submit failed")
                self._screenshot_error("reply_submit_rejected", f"comment #{comment_idx}")
                self._comment_fail_streak += 1
                self._maybe_enter_cooldown("reply")
                return False

            self._comment_fail_streak = 0
            self.stats["comments"] += 1
            reply_sub = self._get_current_sub()
            self._record_comment_sub(reply_sub)
            logger.info(f"  Reply VERIFIED (total: {self.stats['comments']}): "
                       f"'{reply_text[:60]}'")
            self._log_action("reply", sub=reply_sub,
                             url=self.page.url, text=reply_text, status="verified",
                             style=getattr(self, '_last_comment_style', ''),
                             sentiment=getattr(self, '_last_comment_sentiment', ''))
            self._wait_for_timeout(random.randint(1000, 2000))
            return True

        except Exception as e:
            logger.info(f"  Reply submit failed: {e}")
            self._screenshot_error("reply_exception", str(e)[:150])
            self._comment_fail_streak += 1
            self._maybe_enter_cooldown("reply")
            self._log_action("reply", sub=self._get_current_sub(),
                             url=self.page.url, text=reply_text, status="failed")
            return False

    def _get_post_title(self):
        """Scrape the current post's title."""
        try:
            title_el = self.page.locator('h1').first
            if title_el.is_visible():
                return title_el.text_content().strip()[:300]
        except Exception:
            pass
        return ""

    def _get_current_sub(self):
        """Get subreddit name from current URL."""
        try:
            url = self.page.url
            if "/r/" in url:
                return url.split("/r/")[1].split("/")[0]
        except Exception:
            pass
        return ""

    def _is_replyable_comment(self, idx):
        """Check if comment at idx is suitable for replying to.

        Returns False for:
        - Mod/stickied comments (AutoModerator, distinguished mods)
        - Bot comments ("I am a bot")
        - Welcome/rules stickies ("Welcome to r/", "Please remember")
        - Comments too short to be meaningful
        """
        try:
            return self.page.evaluate("""(idx) => {
                const comments = document.querySelectorAll('shreddit-comment');
                if (idx >= comments.length) return false;
                const c = comments[idx];

                // Check DOM attributes for stickied/mod indicators
                const author = (c.getAttribute('author') || '').toLowerCase();
                if (author === 'automoderator' || author === 'automod') return false;

                // Check for stickied attribute
                const stickied = c.getAttribute('stickied');
                if (stickied === 'true' || stickied === '') return false;

                // Check for mod distinguished badge
                const distinguished = c.getAttribute('distinguished');
                if (distinguished === 'moderator' || distinguished === 'admin') return false;

                // Text-based fallback â€” get comment body
                const slot = c.querySelector('[slot="comment"]');
                const text = slot ? slot.textContent.trim() : '';
                if (text.length < 15) return false;

                const lower = text.toLowerCase();
                const skipPatterns = [
                    'i am a bot', 'welcome to r/', 'hi and welcome',
                    'please remember', 'thank you for posting',
                    'submission guidelines', 'this is a reminder',
                    'join our discord', 'discord server',
                    'flair your post', 'read the rules'
                ];
                for (const pat of skipPatterns) {
                    if (lower.includes(pat)) return false;
                }
                return true;
            }""", idx)
        except Exception:
            return False

    def _get_comment_text(self, idx):
        """Get the text content of a comment at the given index."""
        try:
            return self.page.evaluate("""(idx) => {
                const comments = document.querySelectorAll('shreddit-comment');
                if (idx >= comments.length) return '';
                const c = comments[idx];
                // Try slot="comment" first, then any <p> tags in the comment
                const slot = c.querySelector('[slot="comment"]');
                if (slot) {
                    const text = slot.textContent.trim();
                    if (text.length > 5) return text.substring(0, 300);
                }
                // Fallback: grab all <p> text
                const ps = c.querySelectorAll('p');
                let text = '';
                for (const p of ps) text += p.textContent.trim() + ' ';
                return text.trim().substring(0, 300);
            }""", idx)
        except Exception:
            return ""

    def _get_post_media_type(self):
        """Detect the media type of the current post.

        Returns: "image", "video", "text", or "link"
        """
        try:
            return self.page.evaluate("""() => {
                const post = document.querySelector('shreddit-post');
                if (!post) return 'text';
                if (post.querySelector('shreddit-player') || post.querySelector('video'))
                    return 'video';
                if (post.querySelector(
                    'img[src*="redd.it"], img[src*="imgur"], '
                    + 'img[src*="preview"], img[slot="post-media-content"]'))
                    return 'image';
                if (post.querySelector('a[href*="http"]'))
                    return 'link';
                return 'text';
            }""")
        except Exception:
            return "text"

    def _get_visual_context(self):
        """Screenshot the post element for vision analysis.

        Returns a list with one base64 PNG — a screenshot of the
        shreddit-post element which contains the image, title, etc.
        Videos are skipped (handled via text context + hijack).

        Uses a 5s timeout to avoid hanging when browser plugins
        interfere with element visibility. Falls back to page
        screenshot if the element screenshot fails.
        """
        frames = []
        try:
            post_el = self.page.query_selector('shreddit-post')
            if not post_el:
                return frames
            try:
                shot = post_el.screenshot(timeout=5000)
            except Exception:
                # Element not visible — fall back to viewport screenshot
                try:
                    shot = self.page.screenshot(timeout=5000)
                except Exception:
                    return frames
            if len(shot) > 1000:
                frames.append(base64.b64encode(shot).decode())
        except Exception as e:
            logger.debug(f"Visual context error: {e}")
        return frames

    def _get_top_comments(self, n=5):
        """Scrape the top N visible comment texts from the current post.

        Returns a list of strings (the comment bodies), used to give Grok
        context about what a post is actually about.
        """
        try:
            return self.page.evaluate("""(n) => {
                const comments = document.querySelectorAll('shreddit-comment');
                const texts = [];
                for (let i = 0; i < Math.min(comments.length, n + 5); i++) {
                    // Get paragraphs in the comment body slot
                    const ps = comments[i].querySelectorAll(
                        '[slot="comment"] p, [id] > p'
                    );
                    let text = '';
                    for (const p of ps) {
                        text += p.textContent.trim() + ' ';
                    }
                    text = text.trim();
                    // Skip empty, AutoMod, stickied, or very short
                    if (text.length < 10) continue;
                    const lower = text.toLowerCase();
                    const skip = ['i am a bot', 'welcome to r/', 'hi and welcome',
                        'please remember', 'thank you for posting',
                        'submission guidelines', 'this is a reminder',
                        'join our discord', 'flair your post', 'read the rules'];
                    const author = (comments[i].getAttribute('author') || '').toLowerCase();
                    if (author === 'automoderator') continue;
                    if (skip.some(p => lower.includes(p))) continue;
                    texts.push(text.substring(0, 150));
                    if (texts.length >= n) break;
                }
                return texts;
            }""", n)
        except Exception:
            return []

    def _get_post_body(self, max_chars=500):
        """Scrape the post's body text / self-text content.

        For text posts this returns the body. For link/image/video posts
        it returns whatever description or alt-text is available.
        """
        try:
            return self.page.evaluate("""(maxLen) => {
                const post = document.querySelector('shreddit-post');
                if (!post) return '';

                // Try self-text (text posts)
                const selfText = post.querySelector(
                    '[slot="text-body"] p, .md p, [data-click-id="text"] p'
                );
                if (selfText) {
                    // Collect all paragraph text
                    const allP = post.querySelectorAll(
                        '[slot="text-body"] p, .md p, [data-click-id="text"] p'
                    );
                    let text = '';
                    for (const p of allP) {
                        text += p.textContent.trim() + ' ';
                        if (text.length > maxLen) break;
                    }
                    return text.trim().substring(0, maxLen);
                }

                // Try image alt-text
                const img = post.querySelector('img[alt]');
                if (img && img.alt && img.alt.length > 5) {
                    return 'Image: ' + img.alt.substring(0, maxLen);
                }

                // Try video title
                const video = post.querySelector('shreddit-player, video');
                if (video) {
                    return 'Video post';
                }

                // Try link
                const link = post.querySelector('a[href*="http"]');
                if (link && link.textContent.trim().length > 5) {
                    return 'Link: ' + link.textContent.trim().substring(0, maxLen);
                }

                return '';
            }""", max_chars)
        except Exception:
            return ""

    def _get_post_flair(self):
        """Get the post's flair/tag text if present (e.g. 'Video', 'Image')."""
        try:
            return self.page.evaluate("""() => {
                const post = document.querySelector('shreddit-post');
                if (!post) return '';
                const flair = post.getAttribute('flair-text')
                    || post.getAttribute('post-flair-text') || '';
                const type = post.getAttribute('post-type') || '';
                return (flair + ' ' + type).trim();
            }""")
        except Exception:
            return ""

    def _jitter_mouse(self):
        """Move mouse to a random viewport position."""
        try:
            vp = self.page.viewport_size
            if vp:
                self.page.mouse.move(
                    random.randint(100, vp["width"] - 100),
                    random.randint(100, vp["height"] - 100)
                )
        except Exception:
            pass


