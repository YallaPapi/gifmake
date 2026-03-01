"""
Helpers for normalizing account identifiers from AdsPower profile names.
"""

import re


def normalize_reddit_username(value):
    """Normalize a Reddit username-like value to lowercase plain text."""
    raw = str(value or "").strip()
    if not raw:
        return ""

    # Handle common wrappers/prefixes from UI labels.
    lowered = raw.lower()
    if lowered.startswith("u/"):
        raw = raw[2:].strip()

    # Strip common AdsPower descriptors.
    raw = re.sub(r"^(?:-\s*)?reddit\s*bot\s*-*\s*", "", raw, flags=re.IGNORECASE)

    # AdsPower names often include display prefixes like:
    # "4u - reddit bot - LunaMonroe".
    if " - " in raw:
        raw = raw.rsplit(" - ", 1)[-1].strip()

    # Keep Reddit-legal characters only (letters, digits, underscore, hyphen).
    raw = re.sub(r"[^A-Za-z0-9_-]+", "", raw)
    return raw.lower().strip()


def extract_username_from_profile_name(profile_name, prefix):
    """Extract normalized username from AdsPower profile name + prefix."""
    name = str(profile_name or "").strip()
    pref = str(prefix or "").strip()
    if not name:
        return ""

    if pref and name.startswith(pref):
        name = name[len(pref):].strip()

    return normalize_reddit_username(name)
