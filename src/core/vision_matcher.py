"""
Vision-based content analyzer and subreddit matcher.
Uses Claude Vision to analyze images/videos, then matches against sub profiles.
Includes weighted random sub selection for varied posting.
"""
import json
import os
import base64
import hashlib
import re
import random
import subprocess
import sys
import requests

PROFILES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "subreddit_profiles.json")
TIERS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "subreddit_tiers_grok.json")
SUB_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "subreddit_data_v3.json")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

# Heuristics to exclude creator/promo/fan-page style subs from generic matching.
CREATOR_NAME_KEYWORDS = {
    "onlyfans", "fansly", "ofans", "fanclub", "fan_page", "fanpage",
}
CREATOR_THEME_PHRASES = {
    "dedicated to",
    "content dedicated to",
    "fan content",
    "fan-run",
    "fan page",
    "fan subreddit",
    "tribute to",
    "content featuring",
    "photos and videos featuring",
    "worship content",
    "updates and worship",
    "onlyfans links",
    "onlyfans previews",
    "content from",
    "photos and videos of",
    "videos and photos dedicated to",
}
CREATOR_ROLE_WORDS = {
    "pornstar", "adult star", "adult performer", "model", "actress",
    "creator", "cam model", "influencer",
}
CELEB_PROMO_NAME_TERMS = {
    "deepfake", "deepfakes", "celeb", "celebrity", "goon", "gooning",
    "presenter", "politician",
}
CELEB_PROMO_THEME_TERMS = {
    "deepfake",
    "deepfakes",
    "celebrity",
    "celeb",
    "gooning",
    "tv presenter",
    "presenter",
    "actress",
    "actor",
    "politician",
    "official",
}
CELEB_PROMO_TAG_TERMS = {
    "celeb_nsfw",
    "celebrity_nsfw",
    "deepfake",
    "deepfakes",
    "gooning",
    "tv_presenter",
    "politician",
}
FANDOM_THEME_TERMS = {
    "apex legends",
    "video game",
    "fictional character",
    "fictional",
    "fanart",
    "rule34",
    "rule 34",
    "anime character",
    "cartoon character",
}
FANDOM_TAG_TERMS = {
    "apex_legends",
    "video_game",
    "fictional_character",
    "fanart",
    "rule34",
    "anime",
    "cartoon",
    "cosplay_character",
}
GENERIC_SUB_WORDS = {
    "gonewild", "gw", "nsfw", "milf", "teen", "boob", "boobs", "tits", "ass",
    "pussy", "sex", "nude", "porn", "hookup", "r4r", "amateur", "latina",
    "asian", "ebony", "petite", "curvy", "thick", "wife", "wives", "couple",
    "girls", "women", "lingerie", "bikini", "city", "state",
}
LOW_QUALITY_CORE_FRAGMENTS = {
    "ass", "tits", "boobs", "pussy", "slut", "whore", "cum", "creampie",
    "porn", "xxx", "milf", "teen", "bbw", "pawg", "gape", "fuck", "fucking",
}
LOW_QUALITY_EXTRA_FRAGMENTS = {
    "big", "huge", "tiny", "petite", "slim", "thick", "curvy", "fit", "hot",
    "sexy", "dirty", "naughty", "amateur", "real", "booty", "butt", "tit",
    "boob", "vagina", "cunt", "sluts", "whores", "teens", "latina", "asian",
    "ebony", "wife", "wives", "gf", "girl", "girls", "woman", "women", "nude",
    "nudes", "show", "showing", "out",
}
LOW_QUALITY_ALLOW_SUBSTRINGS = {
    "gonewild", "amateur", "realgirls", "normalnudes", "legalteens",
}
LOW_QUALITY_SUFFIXES = {
    "porn", "xxx", "sluts", "whores", "megasource",
}
STRICT_NEW_ACCOUNT_PHRASES = {
    "new account",
    "low karma",
    "minimum karma",
    "account age",
    "days old account",
    "weeks old account",
    "months old account",
    "must be verified",
    "verification required",
    "verified users only",
    "approved users only",
    "no unverified oc",
}
STRICT_NEW_ACCOUNT_PATTERNS = (
    (("account", "karma"), ("new", "low", "minimum", "must have")),
    (("verified",), ("required", "only", "must")),
    (("approval", "approved"), ("required", "only")),
)
HIGH_RISK_NICHE_TOKENS = {
    "bbc", "cuckold", "cuck", "bdsm", "dom", "domme", "submissive", "fetish",
    "petplay", "furry", "incest", "rape", "nonconsensual", "hentai", "loli",
    "femboy", "sissy", "gay", "cock", "dick", "blowjob", "deepfake", "celeb",
    "goon", "gooning", "foot", "feet", "findom", "humiliation", "shemale",
    "hookup", "hookups", "p2p", "sales", "selling",
}
HIGH_RISK_NICHE_PHRASES = {
    "male male",
    "male_male",
    "big black cock",
    "race play",
    "pet play",
    "girlfriend experience",
    "content sales",
    "trade content",
}
LOW_QUALITY_FRAGMENT_ORDER = sorted(
    LOW_QUALITY_CORE_FRAGMENTS | LOW_QUALITY_EXTRA_FRAGMENTS, key=len, reverse=True
)

VISION_PROMPT = """You are analyzing NSFW images of people to determine which Reddit subreddits they should be posted to. We have thousands of subreddits categorized by body type, ethnicity, body parts, sexual acts, clothing, etc. Your job is to describe the PERSON in the image so we can match them to the right subreddits.

Focus on describing the person — their body, what body parts are visible/featured, sizes, and what they are doing.

Describe:

1. body_type: petite, slim, athletic, fit, average, curvy, thick, bbw, muscular
2. ethnicity: white, asian, latina, ebony, indian, mixed, or other
3. hair_color: blonde, brunette, redhead, black, other
4. breast_size: flat, small, medium, large, huge (or "not_visible" if covered/not shown)
5. ass_size: small, average, round, big, huge (or "not_visible" if not shown)
6. body_parts_featured: which body parts are prominent/visible in the image
   (examples: breasts, ass, pussy, thighs, abs, back, feet, face, lips, full_body)
7. clothing: what they're wearing (nude, lingerie, bikini, dress, yoga_pants, uniform, topless, panties, partially_clothed, etc.)
8. action: what is happening (posing, solo, sex, blowjob, masturbation, bent_over, spreading, twerking, flashing, riding, doggy, etc.)
9. setting: where (bedroom, bathroom, outdoor, car, gym, pool, shower, office, etc.)
10. tags: 8-10 snake_case tags describing the person and content. Pick from real subreddit-style categories:
    (examples: big_tits, big_ass, pawg, busty, milf, amateur, solo_female, petite, curvy, thick, bbw, anal, blowjob, latina, asian, ebony, gonewild, nudes, lingerie, nude, interracial, feet, tattooed, pierced, tanlines, fit, natural_tits, fake_tits, hotwife, selfie, pov)
    Focus on what describes THIS person and what they are showing. Do not pad with generic filler tags.
11. vibe: amateur, professional, selfie, pov, artistic, candid

Respond ONLY in valid JSON:
{
  "body_type": "...",
  "ethnicity": "...",
  "hair_color": "...",
  "breast_size": "...",
  "ass_size": "...",
  "body_parts_featured": ["...", "..."],
  "clothing": "...",
  "action": "...",
  "setting": "...",
  "tags": ["tag1", "tag2", ...],
  "vibe": "..."
}"""


def get_ffmpeg_path():
    """Find ffmpeg executable."""
    bundled = os.path.join(os.path.dirname(__file__), "..", "..", "ffmpeg", "ffmpeg.exe")
    if os.path.exists(bundled):
        return bundled
    return "ffmpeg"


def extract_thumbnail(video_path, timestamp=5):
    """Extract a single frame from a video as JPEG bytes."""
    ffmpeg = get_ffmpeg_path()
    cmd = [
        ffmpeg, "-y", "-ss", str(timestamp), "-i", video_path,
        "-vframes", "1", "-f", "image2", "-vcodec", "mjpeg", "pipe:1"
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except Exception:
        pass
    return None


def _convert_heic_to_jpeg(image_path):
    """Convert HEIC/HEIF to JPEG bytes using pillow-heif."""
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        from PIL import Image
        import io
        img = Image.open(image_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()
    except ImportError:
        # Fallback: try FFmpeg conversion
        ffmpeg = get_ffmpeg_path()
        try:
            result = subprocess.run(
                [ffmpeg, "-y", "-i", image_path, "-f", "image2", "-vcodec", "mjpeg", "pipe:1"],
                capture_output=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
        except Exception:
            pass
    except Exception:
        pass
    return None


def _tokenize_text(value):
    """Normalize a free-form string to lowercase alnum tokens."""
    if not value:
        return []
    return [t for t in re.split(r"[^a-z0-9]+", value.lower()) if t]


def _has_term_overlap(content_value, sub_value, min_len=4):
    """Return True when two text fields share meaningful tokens."""
    content_tokens = {t for t in _tokenize_text(content_value) if len(t) >= min_len}
    sub_tokens = {t for t in _tokenize_text(sub_value) if len(t) >= min_len}
    if not content_tokens or not sub_tokens:
        return False
    return bool(content_tokens & sub_tokens)


def _compact_alnum(value):
    """Return lowercase alnum-only text for substring heuristics."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _keyword_fragment_coverage(value):
    """Greedy fragment coverage over compact text, returns (ratio, fragments)."""
    compact = _compact_alnum(value)
    if not compact:
        return 0.0, []

    covered = 0
    fragments = []
    idx = 0
    while idx < len(compact):
        hit = None
        for fragment in LOW_QUALITY_FRAGMENT_ORDER:
            if compact.startswith(fragment, idx):
                hit = fragment
                break
        if hit:
            covered += len(hit)
            fragments.append(hit)
            idx += len(hit)
        else:
            idx += 1
    return covered / len(compact), fragments


def _looks_like_personal_handle(sub_name):
    """Best-effort detection of creator-handle style subreddit names."""
    n = (sub_name or "").lower().strip()
    if not n:
        return False

    if any(k in n for k in GENERIC_SUB_WORDS):
        return False

    # Two-word handle style (e.g. first_last, creator_name123).
    if "_" in n:
        parts = [p for p in n.split("_") if p]
        if len(parts) in (2, 3) and all(2 <= len(p) <= 20 for p in parts):
            return True

    # Long alpha/num single token with no generic category words.
    if n.replace("-", "").isalnum() and 10 <= len(n) <= 30:
        return True

    return False


def _is_creator_or_promo_sub(sub_name, profile):
    """Heuristic filter for creator-specific or promo-heavy subreddits."""
    name = (sub_name or "").lower()
    theme = (profile.get("theme") or "").lower()
    tags = [str(t).lower() for t in profile.get("tags", [])]

    if any(k in name for k in CREATOR_NAME_KEYWORDS):
        return True

    if any(k in name for k in CELEB_PROMO_NAME_TERMS):
        return True

    if any(phrase in theme for phrase in CREATOR_THEME_PHRASES):
        return True

    if any(term in theme for term in CELEB_PROMO_THEME_TERMS):
        return True

    if "onlyfans" in theme and any(k in theme for k in ("creator", "model", "links", "previews")):
        return True

    if any("onlyfans" in t or "fansly" in t for t in tags):
        return True

    tags_blob = " ".join(tags)
    if any(term in tags_blob for term in CELEB_PROMO_TAG_TERMS):
        return True

    if any(term in theme for term in FANDOM_THEME_TERMS):
        return True
    if any(term in tags_blob for term in FANDOM_TAG_TERMS):
        return True

    name_compact = re.sub(r"[^a-z0-9]", "", name)
    for tag in tags:
        tag_compact = re.sub(r"[^a-z0-9]", "", tag)
        if len(tag_compact) >= 6 and (name_compact in tag_compact or tag_compact in name_compact):
            if _looks_like_personal_handle(name):
                return True

    if _looks_like_personal_handle(name):
        if any(k in theme for k in ("my content", "my page", "my pics", "my videos")):
            return True
        if any(k in tags for k in ("pornstar", "creator", "model", "cam_model")):
            return True

    if _looks_like_personal_handle(name) and any(k in theme for k in CREATOR_ROLE_WORDS):
        return True

    return False


def _is_low_quality_keyword_soup_sub(sub_name, profile):
    """Filter out spammy keyword-soup communities that are high removal risk."""
    name = (sub_name or "").lower().strip()
    if not name:
        return False

    tokens = _tokenize_text(name)
    token_hits = [t for t in tokens if t in LOW_QUALITY_CORE_FRAGMENTS]
    if len(token_hits) >= 4:
        return True

    compact = _compact_alnum(name)
    if len(compact) < 10:
        return False

    if any(allowed in name for allowed in LOW_QUALITY_ALLOW_SUBSTRINGS):
        return False

    coverage, fragments = _keyword_fragment_coverage(name)
    unique_fragments = set(fragments)
    core_hits = unique_fragments & LOW_QUALITY_CORE_FRAGMENTS

    # Strong signal for no-separator keyword mashups (e.g. bigasstitslatinaporn).
    if "_" not in name and "-" not in name:
        if coverage >= 0.88 and len(unique_fragments) >= 3 and len(core_hits) >= 1:
            return True

        if len(compact) >= 12 and any(compact.endswith(sfx) for sfx in LOW_QUALITY_SUFFIXES):
            if len(core_hits) >= 1:
                return True

    theme = (profile.get("theme") or "").lower()
    if "keyword dump" in theme or "dumping ground" in theme:
        return True

    return False


def _is_strict_for_new_accounts(sub_data_entry):
    """Detect communities that explicitly gate new/low-karma accounts."""
    if not isinstance(sub_data_entry, dict):
        return False

    submission_type = str(sub_data_entry.get("submission_type") or "").lower().strip()
    if submission_type == "self":
        return True

    parts = []
    description = sub_data_entry.get("description")
    if isinstance(description, str):
        parts.append(description)

    rules = sub_data_entry.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict):
                title = rule.get("title")
                desc = rule.get("description")
                if isinstance(title, str):
                    parts.append(title)
                if isinstance(desc, str):
                    parts.append(desc)
            elif isinstance(rule, str):
                parts.append(rule)

    blob = " ".join(parts).lower()
    if not blob:
        return False

    if any(phrase in blob for phrase in STRICT_NEW_ACCOUNT_PHRASES):
        return True

    if re.search(r"\b\d+\s*(day|days|week|weeks|month|months)\b", blob):
        if "account" in blob and any(k in blob for k in ("old", "age", "minimum", "must be")):
            return True

    for required_terms, qualifier_terms in STRICT_NEW_ACCOUNT_PATTERNS:
        if any(rt in blob for rt in required_terms) and any(qt in blob for qt in qualifier_terms):
            return True

    return False


def _is_high_risk_niche_sub(sub_name, profile):
    """Exclude niche/fetish categories for early-account warmup safety."""
    name = (sub_name or "").lower()
    theme = (profile.get("theme") or "").lower()
    tags = [str(t).lower() for t in profile.get("tags", [])]
    blob = f"{name} {theme} {' '.join(tags)}"
    tokens = set(_tokenize_text(blob))

    if tokens & HIGH_RISK_NICHE_TOKENS:
        return True

    if any(phrase in blob for phrase in HIGH_RISK_NICHE_PHRASES):
        return True

    return False


def analyze_image(image_path, api_key):
    """Analyze a single image using Claude Vision API."""
    ext = os.path.splitext(image_path)[1].lower()

    if ext in VIDEO_EXTENSIONS:
        image_bytes = extract_thumbnail(image_path)
        if not image_bytes:
            return None
        media_type = "image/jpeg"
        b64_data = base64.b64encode(image_bytes).decode("utf-8")
    elif ext in IMAGE_EXTENSIONS:
        if ext in {".heic", ".heif"}:
            # Convert HEIC/HEIF to JPEG for Claude Vision API
            image_bytes = _convert_heic_to_jpeg(image_path)
            if not image_bytes:
                return None
            media_type = "image/jpeg"
        else:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            media_type = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"
            }.get(ext, "image/jpeg")
        b64_data = base64.b64encode(image_bytes).decode("utf-8")
    else:
        return None

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-5-20250929",
            "max_tokens": 500,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        }
                    },
                    {"type": "text", "text": VISION_PROMPT}
                ]
            }]
        },
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["content"][0]["text"].strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return json.loads(content.strip())


def load_profiles():
    """Load sub profiles, tier data, and subscriber counts.

    Returns:
        (profiles, tiers, sub_data) where sub_data maps sub_name -> {subscribers: int, ...}
    """
    profiles = {}
    tiers = {}
    sub_data = {}
    if os.path.exists(PROFILES_PATH):
        with open(PROFILES_PATH, encoding="utf-8") as f:
            profiles = json.load(f)
    if os.path.exists(TIERS_PATH):
        with open(TIERS_PATH, encoding="utf-8") as f:
            tiers = json.load(f)
    if os.path.exists(SUB_DATA_PATH):
        with open(SUB_DATA_PATH, encoding="utf-8") as f:
            sub_data = json.load(f)
    return profiles, tiers, sub_data


def match_content(vision_result, profiles, tiers, max_results=500,
                   excluded_subs=None, sub_data=None, max_subscribers=None,
                   min_subscribers=None, exclude_creator_subs=True,
                   exclude_low_quality_subs=True,
                   exclude_strict_new_account_subs=True,
                   exclude_high_risk_niche_subs=True):
    """Match analyzed content against sub profiles. Returns ranked list.

    Args:
        vision_result: Dict from Claude Vision analysis
        profiles: Sub profiles dict
        tiers: Sub tiers dict
        max_results: Max subs to return (high default so random selection has a big pool)
        excluded_subs: Set of sub names to skip (already posted, banned, etc.)
        sub_data: Dict from subreddit_data_v3.json with subscriber counts
        max_subscribers: Max subscriber count (filters out bigger subs - use for new accounts)
        min_subscribers: Minimum subscriber count (skips tiny/unknown communities)
        exclude_creator_subs: Exclude creator-specific/promo subs from generic matching
        exclude_low_quality_subs: Exclude keyword-soup subs that are usually low quality
        exclude_strict_new_account_subs: Exclude subs with explicit new-account restrictions
        exclude_high_risk_niche_subs: Exclude niche/fetish subs for warmup safety
    """
    if not vision_result:
        return []

    excluded = excluded_subs or set()
    sub_data = sub_data or {}

    content_tags = set(t.lower() for t in vision_result.get("tags", []))
    content_body = vision_result.get("body_type", "").lower()
    content_ethnicity = vision_result.get("ethnicity", "").lower()
    content_clothing = vision_result.get("clothing", "").lower()
    content_action = vision_result.get("action", "").lower()
    content_setting = vision_result.get("setting", "").lower()
    content_hair = vision_result.get("hair_color", "").lower()
    content_vibe = vision_result.get("vibe", "").lower()
    content_breast_size = vision_result.get("breast_size", "").lower()
    content_ass_size = vision_result.get("ass_size", "").lower()
    content_body_parts = set(
        p.lower() for p in vision_result.get("body_parts_featured", [])
    )

    # Map breast/ass sizes to sub tag patterns for matching
    breast_tag_map = {
        "huge": {"big_tits", "big_boobs", "busty", "huge_tits", "massive_tits"},
        "large": {"big_tits", "big_boobs", "busty", "natural_tits"},
        "small": {"small_tits", "tiny_tits", "itty_bitty_titties", "flat"},
        "flat": {"small_tits", "tiny_tits", "flat"},
    }
    ass_tag_map = {
        "huge": {"big_ass", "pawg", "thick_ass", "big_butt", "ass_worship"},
        "big": {"big_ass", "pawg", "thick_ass", "big_butt", "booty"},
        "round": {"big_ass", "pawg", "booty", "ass"},
    }
    body_part_tag_map = {
        "ass": {"ass", "big_ass", "booty", "ass_focus", "ass_worship", "pawg"},
        "breasts": {"big_tits", "big_boobs", "busty", "tits", "boobs", "natural_tits"},
        "pussy": {"pussy", "spread", "closeup"},
        "feet": {"feet", "foot_fetish", "toes"},
        "thighs": {"thick_thighs", "thighs", "thicc"},
        "abs": {"fit", "athletic", "abs"},
        "face": {"face", "pretty", "selfie"},
        "full_body": {"nudes", "gonewild", "nude", "full_body"},
    }

    theme_match_words = content_tags | {
        content_clothing, content_action, content_setting, content_vibe,
        content_body, content_ethnicity,
    }

    scored = []

    for sub_name, profile in profiles.items():
        if sub_name in excluded:
            continue

        tier_info = tiers.get(sub_name, {})
        if tier_info.get("tier") != "GREEN":
            continue

        if exclude_creator_subs and _is_creator_or_promo_sub(sub_name, profile):
            continue

        if exclude_low_quality_subs and _is_low_quality_keyword_soup_sub(sub_name, profile):
            continue
        if exclude_high_risk_niche_subs and _is_high_risk_niche_sub(sub_name, profile):
            continue

        # Filter by subscriber count (for new account safety)
        sd = sub_data.get(sub_name, {})
        if exclude_strict_new_account_subs and _is_strict_for_new_accounts(sd):
            continue

        subs_count = sd.get("subscribers") or 0
        if max_subscribers and isinstance(subs_count, (int, float)) and subs_count > max_subscribers:
            continue
        if min_subscribers and isinstance(subs_count, (int, float)) and subs_count < min_subscribers:
            continue

        score = 0
        sub_tags = set(t.lower() for t in profile.get("tags", []))
        sub_theme = profile.get("theme", "").lower()
        sub_body = profile.get("body_type", "").lower()
        sub_ethnicity = profile.get("ethnicity", "").lower()
        sub_clothing = profile.get("clothing", "").lower()
        sub_action = profile.get("action", "").lower()
        sub_setting = profile.get("setting", "").lower()

        relevance_hits = 0
        hard_relevance_hits = 0

        # Tag overlap (strongest signal)
        tag_overlap = content_tags & sub_tags
        if tag_overlap:
            relevance_hits += len(tag_overlap)
            hard_relevance_hits += len(tag_overlap)
            score += len(tag_overlap) * 12

        # Check if any content words appear in the sub's theme description
        theme_hits = 0
        for word in theme_match_words:
            if word and len(word) > 3 and word in sub_theme:
                theme_hits += 1
        if theme_hits:
            relevance_hits += 1
            hard_relevance_hits += 1
            score += min(theme_hits, 4) * 4

        # Body type match
        if sub_body and sub_body != "any":
            if content_body and content_body in sub_body:
                relevance_hits += 1
                hard_relevance_hits += 1
                score += 15
            elif content_body and content_body not in sub_body:
                score -= 15

        # Ethnicity match
        if sub_ethnicity and sub_ethnicity != "any":
            if content_ethnicity and content_ethnicity in sub_ethnicity:
                relevance_hits += 1
                hard_relevance_hits += 1
                score += 15
            elif content_ethnicity and content_ethnicity not in sub_ethnicity:
                score -= 15

        # Breast size → sub tag matching
        breast_tags = breast_tag_map.get(content_breast_size, set())
        if breast_tags and (breast_tags & sub_tags):
            relevance_hits += 1
            hard_relevance_hits += 1
            score += 10

        # Ass size → sub tag matching
        ass_tags = ass_tag_map.get(content_ass_size, set())
        if ass_tags and (ass_tags & sub_tags):
            relevance_hits += 1
            hard_relevance_hits += 1
            score += 10

        # Body parts featured → sub tag matching
        body_part_score = 0
        for part in content_body_parts:
            part_tags = body_part_tag_map.get(part, set())
            if part_tags & sub_tags:
                body_part_score += 8
            # Also check if the body part word appears in the sub's theme
            if part and len(part) > 3 and part in sub_theme:
                body_part_score += 4
        if body_part_score > 0:
            relevance_hits += 1
            hard_relevance_hits += 1
            score += min(body_part_score, 20)

        # Clothing match
        if sub_clothing != "any" and content_clothing:
            if _has_term_overlap(content_clothing, sub_clothing):
                relevance_hits += 1
                hard_relevance_hits += 1
                score += 6

        # Action match
        if sub_action != "any" and content_action:
            if _has_term_overlap(content_action, sub_action):
                relevance_hits += 1
                hard_relevance_hits += 1
                score += 12

        # Setting match
        if sub_setting != "any" and content_setting:
            if _has_term_overlap(content_setting, sub_setting):
                relevance_hits += 1
                hard_relevance_hits += 1
                score += 10

        # Need at least one relevant signal
        if relevance_hits <= 0:
            continue

        # Subscriber count factor — prefer smaller subs (easier for new accounts)
        if isinstance(subs_count, (int, float)) and subs_count > 0:
            if subs_count < 10000:
                score += 5   # Mild bonus for tiny subs
            elif subs_count < 50000:
                score += 3   # Small bonus for small subs
            elif subs_count < 100000:
                score += 1
            elif subs_count < 250000:
                score += 0   # Neutral for medium subs
            else:
                score -= 3   # Mild penalty for large subs

        if score > 0:
            scored.append((sub_name, score, profile.get("theme", ""), tag_overlap))

    scored.sort(key=lambda x: -x[1])
    return scored[:max_results]


def random_select_subs(scored_matches, count=8, tier_a_pct=0.6, tier_b_pct=0.3,
                       tier_a_threshold=35, tier_b_threshold=24, min_score=0):
    """Weighted random selection from scored matches.

    Splits matches into score tiers and randomly samples from each,
    so the same subs aren't picked every time.

    Args:
        scored_matches: List of (sub_name, score, theme, tag_overlap) from match_content()
        count: How many subs to select
        tier_a_pct: Fraction of picks from tier A (best matches)
        tier_b_pct: Fraction of picks from tier B (good matches)
        tier_a_threshold: Minimum score for tier A
        tier_b_threshold: Minimum score for tier B
        min_score: Drop matches below this score before tiering

    Returns:
        Shuffled list of (sub_name, score, theme, tag_overlap)
    """
    if not scored_matches or count <= 0:
        return []

    if min_score > 0:
        scored_matches = [m for m in scored_matches if m[1] >= min_score]
        if not scored_matches:
            return []

    # If fewer matches than requested, return all shuffled
    if len(scored_matches) <= count:
        result = list(scored_matches)
        random.shuffle(result)
        return result

    # Split into tiers by score
    tier_a = [m for m in scored_matches if m[1] >= tier_a_threshold]
    tier_b = [m for m in scored_matches if tier_b_threshold <= m[1] < tier_a_threshold]
    tier_c = [m for m in scored_matches if 0 < m[1] < tier_b_threshold]

    pools = {"a": tier_a, "b": tier_b, "c": tier_c}

    # Calculate target shares without forcing minimums that can overshoot count.
    target_a = round(count * tier_a_pct)
    target_b = round(count * tier_b_pct)
    target_a = max(0, min(target_a, count))
    target_b = max(0, min(target_b, count - target_a))
    target_c = count - target_a - target_b

    picks = {
        "a": min(target_a, len(pools["a"])),
        "b": min(target_b, len(pools["b"])),
        "c": min(target_c, len(pools["c"])),
    }

    # Fill deficit from highest-scoring tiers first.
    remaining = count - (picks["a"] + picks["b"] + picks["c"])
    if remaining > 0:
        for key in ("a", "b", "c"):
            spare = len(pools[key]) - picks[key]
            if spare <= 0:
                continue
            add = min(spare, remaining)
            picks[key] += add
            remaining -= add
            if remaining <= 0:
                break

    # Random sample from each tier
    selected = []
    if picks["a"] > 0:
        selected.extend(random.sample(tier_a, picks["a"]))
    if picks["b"] > 0:
        selected.extend(random.sample(tier_b, picks["b"]))
    if picks["c"] > 0:
        selected.extend(random.sample(tier_c, picks["c"]))

    random.shuffle(selected)
    return selected[:count]


# ── R4R subreddit selection ─────────────────────────────────────────────────

# Common US city → alias mappings for flexible location matching
_CITY_ALIASES = {
    "new york": ["nyc", "new_york", "ny", "manhattan", "brooklyn", "queens"],
    "los angeles": ["la", "los_angeles", "socal", "hollywood"],
    "san francisco": ["sf", "san_francisco", "bay_area", "bayarea"],
    "san antonio": ["san_antonio", "sanantonio", "sa"],
    "san diego": ["san_diego", "sandiego", "sd"],
    "austin": ["austin", "atx"],
    "dallas": ["dallas", "dfw"],
    "houston": ["houston", "htx"],
    "chicago": ["chicago", "chi"],
    "seattle": ["seattle", "sea"],
    "portland": ["portland", "pdx"],
    "denver": ["denver"],
    "phoenix": ["phoenix", "phx"],
    "atlanta": ["atlanta", "atl"],
    "miami": ["miami", "mia", "sofla"],
    "boston": ["boston"],
    "philadelphia": ["philadelphia", "philly"],
    "washington": ["dc", "dmv", "washington_dc"],
    "detroit": ["detroit"],
    "minneapolis": ["minneapolis", "twin_cities", "mn"],
    "orlando": ["orlando"],
    "tampa": ["tampa", "tampa_bay"],
    "nashville": ["nashville"],
    "charlotte": ["charlotte", "clt"],
    "las vegas": ["las_vegas", "vegas"],
    "raleigh": ["raleigh", "triangle"],
    "columbus": ["columbus"],
    "indianapolis": ["indianapolis", "indy"],
    "jacksonville": ["jacksonville", "jax"],
    "pittsburgh": ["pittsburgh", "pgh"],
}

_STATE_ALIASES = {
    "TX": ["texas", "tx"], "CA": ["california", "ca", "cali"],
    "NY": ["new_york", "ny"], "FL": ["florida", "fl"],
    "OH": ["ohio", "oh"], "PA": ["pennsylvania", "pa"],
    "IL": ["illinois", "il"], "GA": ["georgia", "ga"],
    "NC": ["north_carolina", "nc"], "MI": ["michigan", "mi"],
    "WA": ["washington", "wa"], "OR": ["oregon", "or"],
    "CO": ["colorado", "co"], "AZ": ["arizona", "az"],
    "TN": ["tennessee", "tn"], "MO": ["missouri", "mo"],
    "IN": ["indiana", "in"], "MN": ["minnesota", "mn"],
    "VA": ["virginia", "va"], "SC": ["south_carolina", "sc"],
    "AL": ["alabama", "al"], "LA": ["louisiana", "la"],
    "NV": ["nevada", "nv"], "MD": ["maryland", "md"],
    "MA": ["massachusetts", "ma"], "NJ": ["new_jersey", "nj"],
}


def _parse_location(location_str):
    """Parse 'Austin, TX' into city tokens + state tokens for matching."""
    if not location_str:
        return [], []

    parts = [p.strip() for p in location_str.split(",")]
    city_raw = parts[0].lower().strip() if parts else ""
    state_raw = parts[1].strip().upper() if len(parts) > 1 else ""

    # City tokens: direct + aliases
    city_tokens = [city_raw.replace(" ", "_"), city_raw.replace(" ", "")]
    for canonical, aliases in _CITY_ALIASES.items():
        if city_raw == canonical or city_raw in aliases:
            city_tokens.extend(aliases)
            break

    # State tokens
    state_tokens = []
    if state_raw in _STATE_ALIASES:
        state_tokens = _STATE_ALIASES[state_raw]
    elif state_raw:
        state_tokens = [state_raw.lower()]

    return list(set(city_tokens)), list(set(state_tokens))


def select_r4r_subs(location, profiles, tiers, excluded_subs=None,
                     sub_data=None, count=3, mode="trickle"):
    """Select r4r subreddits matching a location.

    Args:
        location: Location string like "Austin, TX" or "New York"
        profiles: Dict from subreddit_profiles.json
        tiers: Dict from subreddit_tiers_grok.json
        excluded_subs: Set of sub names to skip (banned, already posted)
        sub_data: Dict from subreddit_data_v3.json (optional, for subscriber counts)
        count: How many r4r subs to return
        mode: "trickle" or "area_focus" (location-only) or "blast" (all GREEN r4r)

    Returns:
        List of (sub_name, score, theme, tag_overlap) tuples, same as random_select_subs
    """
    excluded_subs = excluded_subs or set()
    city_tokens, state_tokens = _parse_location(location)
    all_tokens = set(city_tokens + state_tokens)

    scored = []
    for sub_name, profile in profiles.items():
        # Must be an r4r sub
        tags = profile.get("tags", [])
        is_r4r = "r4r" in sub_name.lower() or "r4r" in tags
        if not is_r4r:
            continue

        # GREEN tier only
        tier_info = tiers.get(sub_name, {})
        if tier_info.get("tier", "UNKNOWN") != "GREEN":
            continue

        # Not excluded
        if sub_name.lower() in {s.lower() for s in excluded_subs}:
            continue

        # Score by location match — check tags, sub name words, and theme words
        score = 0
        tag_set = {t.lower() for t in tags}
        sub_words = set(re.split(r'[_\-]', sub_name.lower()))
        sub_lower = sub_name.lower()
        # Split theme into individual words for matching (avoids substring false positives)
        theme_words = set(re.findall(r'[a-z]{3,}', profile.get("theme", "").lower()))
        matchable = tag_set | sub_words | theme_words

        city_match = any(t in matchable or t in sub_lower for t in city_tokens) if city_tokens else False
        state_match = any(t in matchable for t in state_tokens) if state_tokens else False

        if city_match:
            score += 20
        if state_match:
            score += 10
        if not city_match and not state_match:
            # Generic r4r (dirtyr4r, breedingr4r, etc.)
            score += 3

        # In trickle/area_focus: only keep location matches
        if mode in ("trickle", "area_focus") and score < 10:
            continue

        # Tag overlap for the return tuple
        tag_overlap = matchable & all_tokens

        scored.append((sub_name, score, profile.get("theme", ""), tag_overlap))

    scored.sort(key=lambda x: -x[1])

    if not scored:
        return []

    # Random sample with preference for higher scores
    if len(scored) <= count:
        result = list(scored)
        random.shuffle(result)
        return result

    # Top half gets 70% of picks, bottom half gets 30%
    mid = max(1, len(scored) // 2)
    top = scored[:mid]
    bottom = scored[mid:]
    top_picks = min(round(count * 0.7), len(top))
    bottom_picks = min(count - top_picks, len(bottom))

    selected = random.sample(top, top_picks)
    if bottom_picks > 0:
        selected.extend(random.sample(bottom, bottom_picks))

    # Fill remainder from whichever pool has extras
    remaining = count - len(selected)
    if remaining > 0:
        pool = [s for s in scored if s not in selected]
        selected.extend(random.sample(pool, min(remaining, len(pool))))

    random.shuffle(selected)
    return selected[:count]


def content_file_hash(file_path):
    """Generate a stable hash for a content file (for dedup tracking)."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def scan_content_folder(folder_path):
    """Scan a folder for images and videos."""
    files = []
    all_extensions = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
    for fname in os.listdir(folder_path):
        ext = os.path.splitext(fname)[1].lower()
        if ext in all_extensions:
            files.append(os.path.join(folder_path, fname))
    files.sort()
    return files
