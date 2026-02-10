"""
Title generator for Reddit posts using Grok API.
Generates unique, varied titles for each content-to-subreddit pairing.
"""
import json
import os
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

Respond ONLY in valid JSON:
{"titles": ["title1", "title2", ...]}"""


def generate_titles(pairings, api_key):
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
                    "temperature": 0.8,
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
            return titles[:len(pairings)]
        except json.JSONDecodeError:
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)

    return [None] * len(pairings)


def generate_titles_batch(all_pairings, api_key, batch_size=20):
    """Generate titles for many pairings, batched to avoid token limits.

    Args:
        all_pairings: Full list of pairing dicts
        api_key: Grok API key
        batch_size: Pairings per API call

    Returns:
        List of title strings, one per pairing
    """
    all_titles = []
    for i in range(0, len(all_pairings), batch_size):
        batch = all_pairings[i:i + batch_size]
        titles = generate_titles(batch, api_key)
        all_titles.extend(titles)
        if i + batch_size < len(all_pairings):
            time.sleep(1)
    return all_titles
