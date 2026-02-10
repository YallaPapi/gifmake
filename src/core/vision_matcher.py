"""
Vision-based content analyzer and subreddit matcher.
Uses Claude Vision to analyze images/videos, then matches against sub profiles.
Includes weighted random sub selection for varied posting.
"""
import json
import math
import os
import base64
import hashlib
import random
import subprocess
import sys
import requests

PROFILES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "subreddit_profiles.json")
TIERS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "subreddit_tiers_grok.json")
SUB_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "subreddit_data_v3.json")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

VISION_PROMPT = """Analyze this image for subreddit content matching. Describe:

1. body_type: petite, slim, athletic, fit, average, curvy, thick, bbw, muscular
2. ethnicity: white, asian, latina, ebony, indian, mixed, or other
3. hair_color: blonde, brunette, redhead, black, other
4. clothing: what they're wearing (nude, lingerie, bikini, dress, yoga_pants, uniform, etc.)
5. action: what's happening (posing, solo, sex, blowjob, masturbation, stripping, flashing, etc.)
6. setting: where it is (bedroom, bathroom, outdoor, gym, office, pool, beach, car, public, etc.)
7. tags: 5-10 specific descriptive tags for matching content to subreddits
8. vibe: amateur, professional, selfie, pov, artistic, candid

Respond ONLY in valid JSON:
{
  "body_type": "...",
  "ethnicity": "...",
  "hair_color": "...",
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
                   excluded_subs=None, sub_data=None, max_subscribers=None):
    """Match analyzed content against sub profiles. Returns ranked list.

    Args:
        vision_result: Dict from Claude Vision analysis
        profiles: Sub profiles dict
        tiers: Sub tiers dict
        max_results: Max subs to return (high default so random selection has a big pool)
        excluded_subs: Set of sub names to skip (already posted, banned, etc.)
        sub_data: Dict from subreddit_data_v3.json with subscriber counts
        max_subscribers: Max subscriber count (filters out bigger subs — use for new accounts)
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

    all_content_words = content_tags | {
        content_body, content_ethnicity, content_clothing,
        content_action, content_setting, content_hair, content_vibe
    }

    scored = []

    for sub_name, profile in profiles.items():
        if sub_name in excluded:
            continue

        tier_info = tiers.get(sub_name, {})
        if tier_info.get("tier") != "GREEN":
            continue

        # Filter by subscriber count (for new account safety)
        sd = sub_data.get(sub_name, {})
        subs_count = sd.get("subscribers") or 0
        if max_subscribers and isinstance(subs_count, (int, float)) and subs_count > max_subscribers:
            continue

        score = 0
        sub_tags = set(t.lower() for t in profile.get("tags", []))
        sub_theme = profile.get("theme", "").lower()
        sub_body = profile.get("body_type", "").lower()
        sub_ethnicity = profile.get("ethnicity", "").lower()
        sub_clothing = profile.get("clothing", "").lower()
        sub_action = profile.get("action", "").lower()
        sub_setting = profile.get("setting", "").lower()

        # Tag overlap (strongest signal)
        tag_overlap = content_tags & sub_tags
        score += len(tag_overlap) * 10

        # Check if any content words appear in the sub's theme description
        for word in all_content_words:
            if word and len(word) > 3 and word in sub_theme:
                score += 5

        # Body type match
        if sub_body == "any" or not sub_body:
            score += 2
        elif content_body and content_body in sub_body:
            score += 15
        elif content_body and sub_body != "any" and content_body not in sub_body:
            score -= 20

        # Ethnicity match
        if sub_ethnicity == "any" or not sub_ethnicity:
            score += 1
        elif content_ethnicity and content_ethnicity in sub_ethnicity:
            score += 15
        elif content_ethnicity and sub_ethnicity != "any" and content_ethnicity not in sub_ethnicity:
            score -= 20

        # Clothing match
        if sub_clothing != "any" and content_clothing:
            if any(w in sub_clothing for w in content_clothing.split("_") if len(w) > 3):
                score += 8

        # Action match
        if sub_action != "any" and content_action:
            if any(w in sub_action for w in content_action.split("_") if len(w) > 3):
                score += 8

        # Setting match
        if sub_setting != "any" and content_setting:
            if any(w in sub_setting for w in content_setting.split("_") if len(w) > 3):
                score += 8

        # Subscriber count factor — prefer smaller subs (easier for new accounts)
        if isinstance(subs_count, (int, float)) and subs_count > 0:
            if subs_count < 10000:
                score += 10  # Strong bonus for tiny subs
            elif subs_count < 50000:
                score += 5   # Moderate bonus for small subs
            elif subs_count < 200000:
                score += 0   # Neutral for medium subs
            else:
                score -= 5   # Penalty for large subs

        if score > 0:
            scored.append((sub_name, score, profile.get("theme", ""), tag_overlap))

    scored.sort(key=lambda x: -x[1])
    return scored[:max_results]


def random_select_subs(scored_matches, count=8, tier_a_pct=0.6, tier_b_pct=0.3,
                       tier_a_threshold=70, tier_b_threshold=40):
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

    Returns:
        Shuffled list of (sub_name, score, theme, tag_overlap)
    """
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

    # Calculate how many from each tier
    n_a = max(1, round(count * tier_a_pct)) if tier_a else 0
    n_b = max(1, round(count * tier_b_pct)) if tier_b else 0
    n_c = count - n_a - n_b

    # Clamp to available
    n_a = min(n_a, len(tier_a))
    n_b = min(n_b, len(tier_b))
    n_c = min(n_c, len(tier_c))

    # If we're short, fill from whichever tier has surplus
    total = n_a + n_b + n_c
    if total < count:
        deficit = count - total
        for tier, current, cap in [(tier_a, n_a, len(tier_a)),
                                    (tier_b, n_b, len(tier_b)),
                                    (tier_c, n_c, len(tier_c))]:
            can_add = cap - current
            add = min(can_add, deficit)
            if tier is tier_a:
                n_a += add
            elif tier is tier_b:
                n_b += add
            else:
                n_c += add
            deficit -= add
            if deficit <= 0:
                break

    # Random sample from each tier
    selected = []
    if n_a > 0:
        selected.extend(random.sample(tier_a, n_a))
    if n_b > 0:
        selected.extend(random.sample(tier_b, n_b))
    if n_c > 0:
        selected.extend(random.sample(tier_c, n_c))

    random.shuffle(selected)
    return selected


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
