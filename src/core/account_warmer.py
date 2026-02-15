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

from core.post_history import init_warmup, record_activity

logger = logging.getLogger(__name__)

GROK_MODEL = "grok-4-1-fast-reasoning"
GROK_URL = "https://api.x.ai/v1/chat/completions"


# -- Probabilities that scale with account age (PHASE-BASED) --
# Lesson learned: midnight_mae banned after 43 comments + 91 votes in 4 days
# on a brand-new account. New accounts need to lurk first.
#
# Phase 1 (days 1-7):   Lurker -- mostly read, rare votes, ~1-2 comments/day
# Phase 2 (days 8-14):  Light -- some votes, 2-4 comments/day
# Phase 3 (days 15-21): Regular -- normal activity, 4-7 comments/day
# Phase 4 (days 22+):   Active -- full engagement

def _get_probs(day):
    """Action probabilities for current warmup day (phase-based).

    Each phase has fixed probabilities -- no smooth curve that lets
    day-1 accounts blast 20+ actions in a single session.

    Voting: 50/50 coin flip per day -- either 0 or 1 vote total.
    Voting hurts CQS, commenting builds it.
    Vote probs are small but nonzero so the 1 allowed vote can fire;
    the daily cap (0 or 1) does the real gating via _vote_allowed().
    """
    if day <= 7:
        # Phase 1: Lurker -- read a lot, barely interact
        return {
            "vote_on_title": 0.0,                  # never vote from feed
            "click_post": 0.15,                    # browse, but mostly read
            "vote_on_post": 0.05,                  # cap gates to 0 or 1/day
            "vote_on_comment": 0.0,                # don't vote on comments
            "reply_to_voted_comment": 0.15,
            "top_level_comment": 0.08,
            "check_sub": 0.05,
            "join_after_browse": 0.10,
        }
    elif day <= 14:
        # Phase 2: Light participant -- starting to engage
        return {
            "vote_on_title": 0.0,
            "click_post": 0.18,
            "vote_on_post": 0.05,                  # cap gates to 0 or 1/day
            "vote_on_comment": 0.0,
            "reply_to_voted_comment": 0.30,
            "top_level_comment": 0.15,
            "check_sub": 0.07,
            "join_after_browse": 0.20,
        }
    elif day <= 21:
        # Phase 3: Regular user
        return {
            "vote_on_title": 0.0,
            "click_post": 0.20,
            "vote_on_post": 0.05,                  # cap gates to 0 or 1/day
            "vote_on_comment": 0.0,
            "reply_to_voted_comment": 0.45,
            "top_level_comment": 0.25,
            "check_sub": 0.08,
            "join_after_browse": 0.30,
        }
    else:
        # Phase 4: Active user (day 22+)
        return {
            "vote_on_title": 0.0,
            "click_post": 0.22,
            "vote_on_post": 0.05,                  # cap gates to 0 or 1/day
            "vote_on_comment": 0.0,
            "reply_to_voted_comment": 0.55,
            "top_level_comment": 0.35,
            "check_sub": 0.10,
            "join_after_browse": 0.35,
        }


# Hard daily caps per phase
# Votes: 50/50 coin flip (0 or 1) -- randomized fresh each day in _get_daily_caps
DAILY_CAPS = {
    "phase1": {"comments": 2, "joins": 2},      # days 1-7
    "phase2": {"comments": 5, "joins": 3},      # days 8-14
    "phase3": {"comments": 8, "joins": 3},      # days 15-21
    "phase4": {"comments": 15, "joins": 4},     # days 22+
}


def _get_daily_caps(day):
    """Return hard daily caps for the current phase.

    Votes: 50/50 coin flip -- 0 or 1 total votes per day.
    """
    if day <= 7:
        caps = dict(DAILY_CAPS["phase1"])
    elif day <= 14:
        caps = dict(DAILY_CAPS["phase2"])
    elif day <= 21:
        caps = dict(DAILY_CAPS["phase3"])
    else:
        caps = dict(DAILY_CAPS["phase4"])
    caps["votes"] = random.choice([0, 1])
    return caps


def _day_progress(day):
    """Normalize day 1..30 to 0..1 progress."""
    day = max(1, int(day or 1))
    return min((day - 1) / 29.0, 1.0)


def _get_session_plan(day):
    """Scale sessions and session length by phase.

    Phase 1 (days 1-7):  1 session, 8-12 min (short lurk)
    Phase 2 (days 8-14): 1-2 sessions, 10-18 min
    Phase 3 (days 15-21): 1-2 sessions, 12-22 min
    Phase 4 (days 22+): 2-3 sessions, 15-30 min
    """
    if day <= 7:
        min_sessions, max_sessions = 1, 1
        min_session_sec = 8 * 60    # 8 min
        max_session_sec = 12 * 60   # 12 min
    elif day <= 14:
        min_sessions, max_sessions = 1, 2
        min_session_sec = 10 * 60   # 10 min
        max_session_sec = 18 * 60   # 18 min
    elif day <= 21:
        min_sessions, max_sessions = 1, 2
        min_session_sec = 12 * 60   # 12 min
        max_session_sec = 22 * 60   # 22 min
    else:
        min_sessions, max_sessions = 2, 3
        min_session_sec = 15 * 60   # 15 min
        max_session_sec = 30 * 60   # 30 min

    return {
        "min_sessions": min_sessions,
        "max_sessions": max_sessions,
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
                 account_age_days=None, account_created_at=None):
        """
        Args:
            profile_id: AdsPower profile ID
            page: Playwright Page object
            attributes: dict with age, gender, etc. from account profile
            persona: PersonaInterests object or dict
            grok_api_key: xAI API key for contextual comment generation
            account_age_days: Optional reddit account age (days since creation)
            account_created_at: Optional ISO datetime for reddit account creation
        """
        self.profile_id = profile_id
        self.page = page
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

        # Track clicked post URLs to avoid re-clicking the same post
        self._clicked_urls = set()

        # Top-comment hijack ratio (0.0 = always top-level, 1.0 = always hijack)
        self.hijack_ratio = 0.4  # default: 40% hijack, 60% top-level
        self._max_comments = 0   # 0 = unlimited (overridden by run_daily_warmup)
        self._comment_fail_streak = 0  # consecutive comment submit failures
        self._comment_cooldown_until = 0  # time.time() after which commenting resumes

        # Phase-based daily caps
        self._daily_caps = _get_daily_caps(self.day)
        self.min_nsfw_days = 14  # GUI can override this

        # Session stats (reset per run_daily_warmup)
        self.stats = {
            "upvotes": 0, "downvotes": 0, "comments": 0,
            "joins": 0, "posts_clicked": 0, "subs_browsed": 0,
            "sessions": 0, "total_sec": 0, "scrolls": 0,
        }
        # Per-action log for UI display: list of dicts
        # {type, sub, url, text, status, ts}
        self.action_log = []

        phase = 1 if self.day <= 7 else 2 if self.day <= 14 else 3 if self.day <= 21 else 4
        logger.info(f"Warmer init: day {self.day} (phase {phase}), "
                    f"caps: {self._daily_caps}, "
                    f"{len(self.general_subs)} general subs, "
                    f"grok={'yes' if self.grok_api_key else 'no'}")

    def _vote_allowed(self):
        """Check if vote cap hasn't been reached."""
        total_votes = self.stats["upvotes"] + self.stats["downvotes"]
        return total_votes < self._daily_caps["votes"]

    def _comment_allowed(self):
        """Check if comment cap hasn't been reached."""
        return self.stats["comments"] < self._daily_caps["comments"]

    def _join_allowed(self):
        """Check if join cap hasn't been reached."""
        return self.stats["joins"] < self._daily_caps["joins"]

    def _all_caps_hit(self):
        """Check if all daily caps are exhausted (can end session early)."""
        return (not self._vote_allowed() and
                not self._comment_allowed() and
                not self._join_allowed())

    def _maybe_enter_cooldown(self, action_type="comment"):
        """After 3 consecutive comment/reply failures, enter a 5-minute cooldown."""
        if self._comment_fail_streak >= 3:
            cooldown_sec = 300  # 5 minutes
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
        remaining = max(0, int(ms))
        while remaining > 0:
            if self.stop_requested:
                return False
            chunk = min(500, remaining)
            self.page.wait_for_timeout(chunk)
            remaining -= chunk
        return not self.stop_requested

    def _log_action(self, action_type, sub="", url="", text="", status="ok"):
        """Record an individual action for the activity popout."""
        import datetime
        self.action_log.append({
            "type": action_type,
            "sub": sub,
            "url": url,
            "text": text[:200] if text else "",
            "status": status,
            "ts": datetime.datetime.now().strftime("%H:%M:%S"),
        })

    # â"€â"€ Main entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def run_daily_warmup(self, target_subs=None, session_minutes=None,
                         max_comments=None):
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

        Returns:
            dict with activity counts
        """
        self.stats = {
            "upvotes": 0, "downvotes": 0, "comments": 0,
            "joins": 0, "posts_clicked": 0, "subs_browsed": 0,
            "sessions": 0, "total_sec": 0, "scrolls": 0,
        }
        self.action_log = []
        self._max_comments = max_comments or 0  # 0 = unlimited

        if session_minutes:
            # Manual override: single session of specified length
            total_sec = int(session_minutes * 60)
            logger.info(
                f"Manual session: {session_minutes}min, "
                f"max comments: {max_comments or 'unlimited'}"
            )
            self._vote_ratio = random.uniform(0.70, 0.85)
            self._run_browse_session(session_sec=total_sec)
            self.stats["sessions"] = 1
        else:
            # Auto mode: day-scaled sessions
            plan = _get_session_plan(self.day)
            num_sessions = random.randint(plan["min_sessions"], plan["max_sessions"])
            caps = self._daily_caps
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
                self._run_browse_session(session_sec=session_sec)
                self.stats["sessions"] += 1

                # Pause between sessions (compressed — in reality would be hours)
                if i < num_sessions - 1:
                    pause_ms = random.randint(15000, 45000)
                    logger.info(f"Pausing {pause_ms//1000}s between sessions")
                    if not self._wait_for_timeout(pause_ms):
                        break

        # Record to DB
        record_activity(self.profile_id, "upvotes", self.stats["upvotes"])
        record_activity(self.profile_id, "comments", self.stats["comments"])
        record_activity(self.profile_id, "joins", self.stats["joins"])

        logger.info(f"Warmup done: {self.stats}")
        self.stats["action_log"] = list(self.action_log)
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

        # Feed URL candidates â€” try in order until one has posts
        feed_candidates = [
            "https://www.reddit.com",
            "https://www.reddit.com/r/popular",
            "https://www.reddit.com/r/all",
        ]
        random.shuffle(feed_candidates)

        feed_url = None
        for candidate in feed_candidates:
            try:
                self.page.goto(candidate, timeout=30000,
                               wait_until="domcontentloaded")
                self._wait_for_timeout(random.randint(2000, 5000))
                dismiss_over18(self.page)
                post_count = self.page.locator('shreddit-post').count()
                if post_count >= 2:
                    feed_url = candidate
                    break
                logger.info(f"Feed {candidate}: only {post_count} posts, trying next")
            except Exception as e:
                logger.info(f"Feed {candidate} failed: {e}")

        if not feed_url:
            logger.warning("No feed URL produced posts, aborting session")
            return

        self._feed_url = feed_url  # Store for recovery in _explore_post
        logger.info(f"Session start: {feed_url} ({session_sec//60} min)")
        logger.info("Feed loaded, starting scroll loop")

        # â”€â”€ Core scroll loop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        scroll_count = 0
        empty_feed_count = 0  # Consecutive cycles with no posts on page
        while time.time() - start < session_sec:
            if self.stop_requested:
                logger.info("Stop requested, ending session early")
                break
            if self._max_comments and self.stats["comments"] >= self._max_comments:
                logger.info(f"Comment limit reached ({self._max_comments}), ending session")
                break
            try:
                # Scroll down
                self.page.mouse.wheel(0, random.randint(300, 700))
                self._wait_for_timeout(random.randint(1500, 4000))
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
                            logger.info("  All feeds empty, waiting 30s...")
                            self._wait_for_timeout(30000)
                    continue
                else:
                    empty_feed_count = 0

                # Mouse jitter (natural movement)
                if random.random() < 0.15:
                    self._jitter_mouse()

                # â”€â”€ Vote on a post title (without clicking in) â”€â”€â”€â”€â”€â”€â”€â”€
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
                logger.info(f"Scroll loop error: {e}")
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

    # â”€â”€ Feed actions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _vote_in_feed(self):
        """Vote on a post in the feed by its title (without clicking in).

        73% of real Reddit votes happen this way — voting on headlines.
        Vote buttons are inside shreddit-post shadow DOM with text "Upvote"/"Downvote".
        The JS checks aria-pressed !== 'true' so already-voted posts are skipped.
        """
        try:
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
                self._wait_for_timeout(random.randint(200, 600))
            else:
                logger.info(f"  Feed vote: could not click {btn_text} on post {idx}")

        except Exception as e:
            logger.info(f"  Feed vote error: {e}")

    # â”€â”€ Post exploration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _explore_post(self):
        """Click into a post, read it, maybe interact, go back.

        This is where most meaningful actions happen: voting on the post,
        voting on comments, replying, discovering the subreddit.
        """
        from uploaders.reddit.reddit_poster_playwright import dismiss_over18
        try:
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
                return

            self.stats["posts_clicked"] += 1
            self._wait_for_timeout(random.randint(2000, 5000))
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

            # -- Maybe vote on a comment (and maybe reply) --
            if self._vote_allowed() and random.random() < self.probs["vote_on_comment"]:
                skip_reply = in_cooldown or not self._comment_allowed()
                self._interact_with_comment(post_title, current_sub,
                                            skip_reply=skip_reply)

            # -- Maybe leave a top-level comment (check cap + cooldown) --
            elif (not in_cooldown and self._comment_allowed()
                  and random.random() < self.probs["top_level_comment"]):
                self._leave_top_comment(post_title, current_sub)

            # -- Maybe check out the subreddit (check join cap) --
            if self._join_allowed() and random.random() < self.probs["check_sub"] and current_sub:
                self._browse_and_maybe_join_sub(current_sub)
            else:
                # Go back to feed â€” try go_back first (faster, keeps DOM),
                # fall back to explicit goto if we end up off-feed
                self.page.go_back()
                self._wait_for_timeout(random.randint(1500, 3000))
                # Verify we're on the feed (has post links)
                if self.page.locator('shreddit-post').count() < 2:
                    logger.info("  go_back didn't reach feed, using goto")
                    self.page.goto(self._feed_url, timeout=15000,
                                   wait_until="domcontentloaded")
                    self._wait_for_timeout(random.randint(1500, 3000))

        except Exception as e:
            logger.info(f"  Explore post error: {e}")
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
                self._wait_for_timeout(random.randint(200, 800))
            else:
                logger.info(f"  Post vote: {btn_text} button not found in shadow DOM")
        except Exception as e:
            logger.info(f"  Post vote error: {e}")

    # â”€â”€ Comment interaction â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _interact_with_comment(self, post_title, sub_name, skip_reply=False):
        """Vote on a visible comment, maybe reply to it.

        If upvoted â†' positive reply. If downvoted â†' disagreeing reply.
        Comment vote buttons live inside shreddit-comment-action-row's shadow DOM,
        which is a light DOM child of shreddit-comment.
        """
        try:
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
                self._wait_for_timeout(random.randint(300, 800))

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

        # Skip sensitive topics — too risky for factual errors or insensitivity
        top_comments = self._get_top_comments(3)
        if self._should_skip_topic(post_title, top_comments):
            logger.info(f"  Skipping comment — sensitive topic detected")
            return

        sentiment = random.choice(["positive", "positive", "positive", "agree", "neutral"])
        media_type = self._get_post_media_type()

        # Video posts: always hijack (we read comments to understand the video)
        # Image/text posts: use hijack_ratio slider
        should_hijack = (
            media_type == "video"
            or random.random() < self.hijack_ratio
        )

        if should_hijack:
            # Try top 3 comments for a replyable one
            for try_idx in range(3):
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
            if random.random() < 0.4:
                posts = self.page.locator('a[slot="full-post-link"]')
                post_count = posts.count()
                if post_count > 2:
                    idx = random.randint(0, min(post_count - 1, 7))
                    # JS click to avoid image overlay interception
                    self.page.evaluate(
                        """(idx) => {
                            const links = document.querySelectorAll('a[slot="full-post-link"]');
                            if (idx < links.length) links[idx].click();
                        }""",
                        idx
                    )
                    self._wait_for_timeout(random.randint(3000, 6000))
                    dismiss_over18(self.page)

                    # Scroll the post
                    for _ in range(random.randint(1, 3)):
                        self.page.mouse.wheel(0, random.randint(200, 400))
                        self._wait_for_timeout(random.randint(1500, 3000))

                    # Maybe vote on it
                    if random.random() < 0.3:
                        self._vote_on_current_post()

                    self.page.go_back()
                    self._wait_for_timeout(random.randint(1500, 3000))

            # Maybe sort by Top All Time (very natural new-sub behavior)
            if random.random() < 0.25:
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
                        join_btn.first.click()
                        self.stats["joins"] += 1
                        logger.info(f"Joined r/{sub_name}")
                        self._log_action("join", sub=sub_name,
                                         url=f"https://www.reddit.com/r/{sub_name}")
                        self._wait_for_timeout(random.randint(1000, 2500))
                except Exception:
                    pass

            # Return to feed (explicit navigation, not go_back)
            self.page.goto(self._feed_url, timeout=15000,
                           wait_until="domcontentloaded")
            self._wait_for_timeout(random.randint(1500, 3000))

        except Exception as e:
            logger.info(f"  Sub browse error: {e}")
            try:
                self.page.goto(self._feed_url, timeout=15000,
                               wait_until="domcontentloaded")
                self._wait_for_timeout(1500)
            except Exception:
                pass

    # â”€â”€ Grok comment generation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

        # Pick a comment style weighted toward high-karma types
        styles = random.choices(
            ["pun", "sarcasm", "absurd", "anecdote", "helpful", "question", "react"],
            weights=[20, 20, 10, 15, 20, 10, 5],
            k=1,
        )[0]

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
                    "temperature": 1.0 if styles in ("pun", "sarcasm", "absurd") else 0.9,
                    "max_tokens": 150,
                },
                timeout=45,
            )
            if resp.status_code != 200:
                logger.info(f"Grok API error {resp.status_code}: {resp.text[:200]}")
                return None

            comment = resp.json()["choices"][0]["message"]["content"].strip()
            comment = self._clean_comment(comment)
            if comment is None:
                return None

            logger.info(f"Grok comment ({sentiment}): '{comment}'")
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

    def _clean_comment(self, comment):
        """Post-process a Grok-generated comment.

        Strips prefixes, caps length, and rejects banned words.
        Returns cleaned comment or None if it should be discarded.
        """
        # Strip wrapping quotes
        comment = comment.strip('"\'')
        # Remove "Comment:" or similar prefix
        for prefix in ["Comment:", "Reply:", "comment:", "reply:"]:
            if comment.startswith(prefix):
                comment = comment[len(prefix):].strip()
        # Cap length — short punchy comments outperform walls of text
        if len(comment) > 200:
            cut = comment[:200].rfind('.')
            if cut > 80:
                comment = comment[:cut + 1]
            else:
                # Try cutting at last comma or space
                cut = comment[:200].rfind(',')
                if cut > 80:
                    comment = comment[:cut]
                else:
                    comment = comment[:200]
        # Reject if it contains banned words
        lower = comment.lower()
        for word in self._BANNED_WORDS:
            if word in lower:
                logger.info(f"  Rejected comment (banned word '{word}'): '{comment[:60]}'")
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
            # Step 1: Activate the composer (it starts collapsed)
            if not self._activate_comment_composer():
                logger.info("  Comment: no composer trigger found")
                return False

            # Step 2: Find the now-visible contenteditable
            comment_box = self.page.locator(
                'div[contenteditable="true"][data-lexical-editor="true"]:visible'
            )
            if comment_box.count() == 0:
                logger.info("  Comment: composer activated but no editable found")
                return False

            comment_box.first.click()
            self._wait_for_timeout(random.randint(400, 800))

            # Step 3: Type with human-like timing
            for char in comment:
                self.page.keyboard.type(char, delay=random.randint(30, 120))
                if random.random() < 0.04:
                    self._wait_for_timeout(random.randint(150, 500))

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
                return False

            # Verify: wait and check composer closed
            self._wait_for_timeout(3000)
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
                self._comment_fail_streak += 1
                self._maybe_enter_cooldown("comment")
                return False

            self._comment_fail_streak = 0
            self.stats["comments"] += 1
            logger.info(f"  Comment VERIFIED (total: {self.stats['comments']}): "
                       f"'{comment[:60]}'")
            self._log_action("comment", sub=self._get_current_sub(),
                             url=self.page.url, text=comment, status="verified")
            self._wait_for_timeout(random.randint(1000, 2000))
            return True

        except Exception as e:
            logger.info(f"  Comment submit failed: {e}")
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
                return False

            self._wait_for_timeout(random.randint(300, 600))

            for char in reply_text:
                self.page.keyboard.type(char, delay=random.randint(30, 120))
                if random.random() < 0.04:
                    self._wait_for_timeout(random.randint(150, 500))

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
                return False

            # Verify: wait for composer to close (editable disappears from subtree)
            self._wait_for_timeout(3000)
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
                self._comment_fail_streak += 1
                self._maybe_enter_cooldown("reply")
                return False

            self._comment_fail_streak = 0
            self.stats["comments"] += 1
            logger.info(f"  Reply VERIFIED (total: {self.stats['comments']}): "
                       f"'{reply_text[:60]}'")
            self._log_action("reply", sub=self._get_current_sub(),
                             url=self.page.url, text=reply_text, status="verified")
            self._wait_for_timeout(random.randint(1000, 2000))
            return True

        except Exception as e:
            logger.info(f"  Reply submit failed: {e}")
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
        """
        frames = []
        try:
            post_el = self.page.query_selector('shreddit-post')
            if not post_el:
                return frames
            shot = post_el.screenshot()
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

    # -- CQS Checker ----------------------------------------------------------

    def check_cqs(self):
        """Post to r/whatismycqs, read automod reply, delete post, return CQS.

        Flow:
          1. Navigate to r/whatismycqs/submit
          2. Create text post with title "what is my cqs"
          3. Wait for automod reply (up to 30s)
          4. Parse CQS value from reply
          5. Delete the post
          6. Save to DB and return the value
        """
        import re
        from uploaders.reddit.reddit_poster_playwright import dismiss_over18
        from core.post_history import record_cqs

        logger.info("CQS check: starting...")
        cqs_value = None
        raw_response = ""
        post_url = None

        try:
            # 1. Navigate to submit page (text post)
            submit_url = "https://www.reddit.com/r/whatismycqs/submit?type=text"
            self.page.goto(submit_url, timeout=30000,
                           wait_until="domcontentloaded")
            self._wait_for_timeout(random.randint(2000, 4000))
            dismiss_over18(self.page)

            # 2. Fill title
            title_filled = False
            for selector in ['textarea[name="title"]',
                             '[data-testid="post-title-input"]',
                             'textarea[placeholder*="title" i]']:
                try:
                    if self.page.locator(selector).count() > 0:
                        self.page.fill(selector, "what is my cqs")
                        title_filled = True
                        break
                except Exception:
                    continue

            if not title_filled:
                logger.warning("CQS check: could not find title input")
                return None

            self._wait_for_timeout(1500)

            # 3. Submit
            submitted = False
            for selector in ['#submit-post-button',
                             'r-post-form-submit-button[post-action-type="submit"]',
                             'button[type="submit"]:has-text("Post")',
                             'button:has-text("Post")']:
                try:
                    btn = self.page.locator(selector)
                    if btn.count() > 0:
                        btn.first.click()
                        submitted = True
                        break
                except Exception:
                    continue

            if not submitted:
                # JS fallback
                try:
                    self.page.evaluate("""() => {
                        let el = document.getElementById('submit-post-button');
                        if (el) { el.click(); return true; }
                        return false;
                    }""")
                    submitted = True
                except Exception:
                    pass

            if not submitted:
                logger.warning("CQS check: could not click submit")
                return None

            # 4. Wait for post creation + automod reply
            self._wait_for_timeout(8000)
            current_url = self.page.url
            if "/comments/" not in current_url:
                logger.warning(f"CQS check: post may not have been created. URL: {current_url}")
                return None

            post_url = current_url
            logger.info(f"CQS check: post created at {post_url}")

            # Poll for automod reply (every 3s, up to 30s)
            automod_text = None
            for _ in range(10):
                self._wait_for_timeout(3000)
                try:
                    automod_text = self.page.evaluate("""() => {
                        const comments = document.querySelectorAll('shreddit-comment');
                        for (const c of comments) {
                            const author = c.getAttribute('author') || '';
                            if (author.toLowerCase() === 'automoderator') {
                                const sr = c.shadowRoot;
                                if (sr) {
                                    const content = sr.querySelector('[slot="comment"]')
                                                  || sr.querySelector('.md')
                                                  || sr;
                                    return content.textContent || '';
                                }
                                return c.textContent || '';
                            }
                        }
                        // Fallback: scan page text
                        const m = document.body.innerText.match(
                            /(?:cqs|comment quality score|contributor quality)[^\\d]*(\\d+)/i
                        );
                        return m ? m[0] : null;
                    }""")
                except Exception:
                    automod_text = None
                if automod_text:
                    break

            if automod_text:
                raw_response = automod_text.strip()
                logger.info(f"CQS check: automod replied: {raw_response[:200]}")

                # Parse CQS value
                match = re.search(r'(?:cqs|score)[^\d]*(\d+)', raw_response, re.IGNORECASE)
                if match:
                    cqs_value = match.group(1)
                else:
                    match = re.search(r'(\d+)', raw_response)
                    if match:
                        cqs_value = match.group(1)
                    else:
                        cqs_value = raw_response[:100]

                logger.info(f"CQS check: parsed value = {cqs_value}")
            else:
                logger.warning("CQS check: no automod reply after 30s")
                raw_response = "(no reply)"

            # 5. Delete the post
            if post_url:
                self._delete_cqs_post()

            # 6. Save to DB
            record_cqs(self.profile_id, cqs_value, raw_response)
            logger.info(f"CQS check complete: {cqs_value}")
            return cqs_value

        except Exception as e:
            logger.warning(f"CQS check failed: {e}")
            if post_url:
                try:
                    self._delete_cqs_post()
                except Exception:
                    pass
            return None

    def _delete_cqs_post(self):
        """Delete the current post via the overflow menu."""
        try:
            # Open overflow menu on the post
            menu_result = self.page.evaluate("""() => {
                const post = document.querySelector('shreddit-post');
                if (!post) return 'no-post';
                const sr = post.shadowRoot;
                if (!sr) return 'no-sr';
                // Overflow menu button (three dots)
                const btn = sr.querySelector('shreddit-post-overflow-menu')
                         || sr.querySelector('button[aria-label*="more" i]');
                if (btn) { btn.click(); return 'opened'; }
                return 'no-btn';
            }""")
            logger.info(f"  CQS delete: menu={menu_result}")
            if menu_result != 'opened':
                return False

            self._wait_for_timeout(1500)

            # Click Delete in dropdown
            del_result = self.page.evaluate("""() => {
                const items = document.querySelectorAll(
                    '[role="menuitem"], li, button, shreddit-overflow-menu-item'
                );
                for (const el of items) {
                    if ((el.textContent || '').toLowerCase().includes('delete')) {
                        el.click();
                        return 'clicked';
                    }
                }
                return 'not-found';
            }""")
            logger.info(f"  CQS delete: delete={del_result}")
            if del_result != 'clicked':
                return False

            self._wait_for_timeout(1500)

            # Confirm dialog
            confirm = self.page.evaluate("""() => {
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    const t = (b.textContent || '').trim().toLowerCase();
                    if (t === 'delete' || t === 'yes' || t === 'confirm') {
                        b.click();
                        return 'confirmed';
                    }
                }
                return 'no-confirm';
            }""")
            logger.info(f"  CQS delete: confirm={confirm}")
            self._wait_for_timeout(2000)
            return confirm == 'confirmed'

        except Exception as e:
            logger.warning(f"  CQS delete error: {e}")
            return False

