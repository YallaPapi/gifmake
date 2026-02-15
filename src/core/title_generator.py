"""
Title generator for Reddit posts using Grok API.
Generates unique, varied titles for each content-to-subreddit pairing.
"""
import json
import re
import time
import requests

GROK_MODEL = "grok-4-1-fast-reasoning"
GROK_URL = "https://api.x.ai/v1/chat/completions"
MAX_RETRIES = 3

SYSTEM_PROMPT = """You write Reddit post titles as if you ARE the girl in the photo/video posting it herself.

VOICE: First person. Casual. Flirty. Like texting a crush — not writing an article.

GOOD title examples:
- "Do you like what you see? 😏"
- "Would you come fuck me in Carolina?"
- "Just got these new panties, what do you think"
- "I never wear a bra to the gym"
- "Can I sit on your face?"
- "POV: your new neighbor forgot to close her blinds"
- "Happy titty tuesday 💕"
- "I hope older men appreciate my body"
- "Wanna play with a thick latina tonight?"
- "My ex said my ass was too big... his loss"
- "First time posting here, be nice 🙈"
- "I need someone to rip these off me"

BAD titles (do NOT write like this):
- "Stunning brunette in red lingerie posing" (third person, sounds like a description)
- "Curvy BBW stunner rocks garters and stockings" (SEO garbage)
- "Hot latina babe teases in bedroom" (sounds like a porn title)

RULES:
- Write from HER perspective, first person
- Match the subreddit's vibe (local subs = mention the area, body subs = mention that body part, etc.)
- Keep it 3-12 words. Short and punchy wins
- Lowercase is fine, don't capitalize every word
- Emojis: max 1, and only sometimes
- Vary between questions, statements, and playful challenges
- NO third-person descriptions. NO adjective stacking. NO porn-title energy
- Across a batch, avoid repeating the same opening word/phrase
- Do NOT mention OnlyFans, links, promos, DMs, or "subscribe"
- Avoid repeating key nouns (like "latina", "lingerie", "curves") every line

Respond ONLY in valid JSON:
{"titles": ["title1", "title2", ...]}"""


PROMO_TERMS = ("onlyfans", "fansly", "subscribe", "link in bio", "dm me", "cashapp")


def _normalize_title(value):
    """Normalize title text for duplicate checks."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _title_opener(value):
    """Use first two words as an opener fingerprint."""
    words = _normalize_title(value).split()
    return " ".join(words[:2]) if words else ""


def _fallback_title(pairing, variant):
    """Deterministic fallback title generator when model output is weak/repetitive."""
    action = (pairing.get("action") or "posing").replace("_", " ").strip()
    setting = (pairing.get("setting") or "here").replace("_", " ").strip()
    tags = [str(t).replace("_", " ").strip() for t in pairing.get("content_tags", []) if t]
    detail = tags[0] if tags else (pairing.get("body_type") or "this look").replace("_", " ")

    templates = [
        f"should i keep {action} in {setting}?",
        f"would you join me in {setting} tonight?",
        f"i'm in {setting} and feeling {detail}",
        f"be honest, can you handle this {detail}?",
        f"i can't stop {action} right now",
        f"your turn: {action} with me in {setting}?",
    ]
    return templates[variant % len(templates)]


def _post_process_titles(pairings, titles):
    """Enforce batch variety and safety on generated titles."""
    cleaned = []
    used_norm = set()
    opener_counts = {}

    for i, pairing in enumerate(pairings):
        raw = titles[i] if i < len(titles) else None
        title = raw.strip().strip('"') if isinstance(raw, str) else ""
        norm = _normalize_title(title)
        opener = _title_opener(title)

        invalid = False
        if not title:
            invalid = True
        else:
            word_count = len(title.split())
            if word_count < 3 or word_count > 14:
                invalid = True
            if any(term in norm for term in PROMO_TERMS):
                invalid = True
            if norm in used_norm:
                invalid = True
            if opener and opener_counts.get(opener, 0) >= 1:
                invalid = True

        if invalid:
            tries = 0
            while tries < 12:
                candidate = _fallback_title(pairing, i + tries)
                candidate_norm = _normalize_title(candidate)
                candidate_opener = _title_opener(candidate)
                if candidate_norm not in used_norm and opener_counts.get(candidate_opener, 0) < 1:
                    title = candidate
                    norm = candidate_norm
                    opener = candidate_opener
                    break
                tries += 1
            if tries >= 12:
                title = _fallback_title(pairing, i)
                norm = _normalize_title(title)
                opener = _title_opener(title)

        cleaned.append(title)
        used_norm.add(norm)
        if opener:
            opener_counts[opener] = opener_counts.get(opener, 0) + 1

    return cleaned


def generate_titles(pairings, api_key, campaign_context=None):
    """Generate titles for a batch of content-to-subreddit pairings.

    Args:
        pairings: List of dicts with keys:
            - sub_name: target subreddit
            - sub_theme: subreddit's theme description
            - content_tags: list of content tags from vision
            - body_type: from vision analysis
            - action: from vision analysis
            - setting: from vision analysis
        api_key: Grok API key
        campaign_context: Optional free text like "traveling to NYC this weekend"
            that gets woven into all titles for thematic coherence.

    Returns:
        List of title strings, one per pairing. None entries on failure.
    """
    if not pairings:
        return []

    # Build the prompt with all pairings
    parts = []
    for i, p in enumerate(pairings):
        tags_str = ", ".join(p.get("content_tags", []))
        parts.append(
            f"{i+1}. r/{p['sub_name']} (theme: {p.get('sub_theme', 'general')[:100]})\n"
            f"   Content: {tags_str} | {p.get('body_type', 'any')} | "
            f"{p.get('action', 'posing')} | {p.get('setting', 'any')}"
        )

    prompt = f"Generate one unique title for each of these {len(pairings)} pairings:\n\n"
    prompt += "\n".join(parts)
    if campaign_context:
        prompt += (
            f"\n\nCampaign context (weave naturally into some titles, don't force it on every one):\n"
            f'"{campaign_context}"'
        )
    prompt += (
        "\n\nImportant batch constraints:\n"
        f"- Return exactly {len(pairings)} titles in the same order\n"
        "- Every line should sound different from the others\n"
        "- Use mixed structures: question, statement, playful dare\n"
    )

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                GROK_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROK_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.9,
                },
                timeout=120,
            )
            if resp.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            result = json.loads(content.strip())
            titles = result.get("titles", [])
            # Pad with None if Grok returned fewer titles than pairings
            while len(titles) < len(pairings):
                titles.append(None)
            return _post_process_titles(pairings, titles[:len(pairings)])
        except json.JSONDecodeError:
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)

    return _post_process_titles(pairings, [None] * len(pairings))


def generate_titles_batch(all_pairings, api_key, batch_size=12, campaign_context=None):
    """Generate titles for many pairings, batched to avoid token limits.

    Args:
        all_pairings: Full list of pairing dicts
        api_key: Grok API key
        batch_size: Pairings per API call
        campaign_context: Optional context string passed to each batch

    Returns:
        List of title strings, one per pairing
    """
    all_titles = []
    for i in range(0, len(all_pairings), batch_size):
        batch = all_pairings[i:i + batch_size]
        titles = generate_titles(batch, api_key, campaign_context=campaign_context)
        all_titles.extend(titles)
        if i + batch_size < len(all_pairings):
            time.sleep(1)
    return all_titles


# ── R4R title generation ─────────────────────────────────────────────────────

R4R_SYSTEM_PROMPT = """You write Reddit R4R (redditor-for-redditor) post titles for a girl posting to hookup/personals subreddits.

FORMAT: [age + gender letter] location - casual flirty text about wanting to meet someone

GOOD examples:
- "[25F] Austin - alt girl looking for late night fun"
- "[25F] visiting NYC this weekend, anyone wanna grab drinks and see where it goes?"
- "[25F] ATX - tatted girl who can't sleep, keep me company?"
- "[25F] Houston area - looking for someone adventurous tonight"
- "[25F] Austin - bored and looking for trouble 😏"
- "[25F] new to Dallas, show me around?"

BAD examples:
- "[25F] Looking for hookup" (too generic, no personality)
- "Hot girl seeks man for fun times" (no age tag, sounds like spam)
- "[25F] Austin, TX - I am a 25 year old female seeking..." (way too formal)

RULES:
- ALWAYS start with [{age}{gender}] tag
- ALWAYS include the city/area name
- Keep it casual, flirty, like a real girl posting — NOT a bot or escort ad
- 5-15 words after the tag
- Vary between: lonely/bored tonight, looking for fun, can't sleep, new in town, spontaneous
- NO mention of money, rates, OF, links, or anything transactional
- NO overly sexual language (Reddit removes explicit r4r titles)
- Emojis: max 1, optional

Respond ONLY in valid JSON:
{"titles": ["title1", "title2", ...]}"""


def generate_r4r_titles(pairings, api_key, persona_info):
    """Generate r4r-style titles with [age+gender] location format.

    Args:
        pairings: List of dicts with at least sub_name, sub_theme
        api_key: Grok API key
        persona_info: Dict with age, gender, location, and optional context
            e.g. {"age": 25, "gender": "F", "location": "Austin, TX",
                   "context": "traveling to NYC this weekend"}

    Returns:
        List of title strings with [25F] prefix baked in
    """
    if not pairings:
        return []

    age = persona_info.get("age", 25)
    gender = persona_info.get("gender", "F")
    location = persona_info.get("location", "")
    context = persona_info.get("context", "")

    # Parse city from location for the prompt
    city = location.split(",")[0].strip() if location else "here"

    parts = []
    for i, p in enumerate(pairings):
        parts.append(
            f"{i+1}. r/{p['sub_name']} (theme: {p.get('sub_theme', 'hookup personals')[:100]})"
        )

    prompt = (
        f"Generate one unique R4R title for each of these {len(pairings)} subreddits.\n"
        f"The girl is {age}{gender} from {city}.\n"
    )
    if context:
        prompt += f'Current situation: "{context}"\n'
    prompt += "\n" + "\n".join(parts)
    prompt += (
        f"\n\nConstraints:\n"
        f"- Return exactly {len(pairings)} titles\n"
        f"- Every title MUST start with [{age}{gender}]\n"
        f"- Vary the vibe across titles\n"
    )

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                GROK_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROK_MODEL,
                    "messages": [
                        {"role": "system", "content": R4R_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.9,
                },
                timeout=120,
            )
            if resp.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            result = json.loads(content.strip())
            titles = result.get("titles", [])

            # Ensure all titles have the age/gender prefix
            tag = f"[{age}{gender}]"
            cleaned = []
            for t in titles:
                t = t.strip().strip('"') if isinstance(t, str) else ""
                if t and not t.startswith("["):
                    t = f"{tag} {t}"
                cleaned.append(t if t else f"{tag} {city} - looking for fun tonight")

            while len(cleaned) < len(pairings):
                cleaned.append(f"{tag} {city} - anyone up?")
            return cleaned[:len(pairings)]

        except json.JSONDecodeError:
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)

    # Total failure fallback
    tag = f"[{age}{gender}]"
    return [f"{tag} {city} - looking for fun tonight" for _ in pairings]
