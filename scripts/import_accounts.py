"""
Bulk import Reddit accounts from CSV.

Writes to:
  1. config/account_profiles.json  (persona, attributes, creator mapping)
  2. config/queue_config.json      (assigns accounts to proxy_groups)

CSV format:
  adspower_id,username,creator,proxy_group,display_name,age,gender,location

Usage:
  python scripts/import_accounts.py accounts.csv
  python scripts/import_accounts.py accounts.csv --dry-run
"""

import argparse
import csv
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from processors.account_profile import (
    AccountProfile, AccountAttributes, PersonaInterests,
    RedditAccount, ProfileManager,
)

# ── Persona generation pools ──────────────────────────────────────────

HOBBY_POOL = [
    "cooking", "yoga", "hiking", "photography", "reading", "painting",
    "running", "swimming", "dancing", "gaming", "crafts", "baking",
    "gardening", "rock climbing", "cycling", "skateboarding", "karaoke",
    "thrifting", "journaling", "pottery", "knitting", "surfing",
]

INTEREST_POOL = [
    "coffee", "cats", "dogs", "fashion", "music", "movies", "anime",
    "horror", "true crime", "astrology", "tattoos", "travel", "memes",
    "sustainability", "vintage", "sneakers", "plants", "wine",
    "board games", "K-pop", "vinyl", "skincare", "tarot",
]

TRAIT_POOL = [
    "creative", "flirty", "chill", "witty", "moody", "spontaneous",
    "introverted", "bubbly", "sarcastic", "warm", "dry humor",
    "adventurous", "laid-back", "curious", "playful",
]

# Interest → subreddit mappings for favorite_subs generation
INTEREST_SUBS = {
    "coffee": ["Coffee", "espresso"],
    "cats": ["cats", "CatsAreAssholes"],
    "dogs": ["dogs", "rarepuppers"],
    "fashion": ["fashion", "streetwear"],
    "music": ["Music", "indieheads"],
    "movies": ["movies", "MovieSuggestions"],
    "anime": ["anime", "animemes"],
    "horror": ["horror", "creepy"],
    "true crime": ["TrueCrime", "UnresolvedMysteries"],
    "astrology": ["astrology", "Astronomy"],
    "tattoos": ["tattoos", "TattooDesigns"],
    "travel": ["travel", "solotravel"],
    "memes": ["memes", "dankmemes"],
    "plants": ["houseplants", "gardening"],
    "gaming": ["gaming", "pcgaming"],
    "cooking": ["cooking", "FoodPorn"],
    "yoga": ["yoga", "flexibility"],
    "photography": ["itookapicture", "photography"],
    "vintage": ["vintage", "ThriftStoreHauls"],
    "skincare": ["SkincareAddiction", "MakeupAddiction"],
}

# General subs everyone might follow
GENERAL_SUBS = [
    "AskReddit", "TrueOffMyChest", "TwoXChromosomes",
    "CozyPlaces", "oddlysatisfying", "mildlyinteresting",
]


def generate_persona(location, interests_subset=None):
    """Generate a random persona from the pools."""
    hobbies = random.sample(HOBBY_POOL, random.randint(3, 5))
    interests = random.sample(INTEREST_POOL, random.randint(4, 6))
    traits = random.sample(TRAIT_POOL, random.randint(2, 4))

    # Build favorite_subs from interests
    fav_subs = []
    for interest in interests:
        if interest in INTEREST_SUBS:
            fav_subs.extend(INTEREST_SUBS[interest])
    # Add 2-3 general subs
    fav_subs.extend(random.sample(GENERAL_SUBS, min(3, len(GENERAL_SUBS))))
    # Add location sub if it looks like a city
    if location:
        city = location.split(",")[0].strip().replace(" ", "")
        fav_subs.insert(0, city)

    # Deduplicate while preserving order
    seen = set()
    unique_subs = []
    for s in fav_subs:
        if s not in seen:
            seen.add(s)
            unique_subs.append(s)

    return PersonaInterests(
        location=location,
        hobbies=hobbies,
        interests=interests,
        personality_traits=traits,
        favorite_subs=unique_subs,
        comment_style="casual",
    )


def import_csv(csv_path, dry_run=False):
    """Import accounts from CSV into project config files."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        print(f"ERROR: CSV file not found: {csv_path}")
        return

    # Load existing configs
    profiles_path = PROJECT_ROOT / "config" / "account_profiles.json"
    queue_path = PROJECT_ROOT / "config" / "queue_config.json"

    pm = ProfileManager(str(profiles_path))

    if queue_path.exists():
        with open(queue_path, "r", encoding="utf-8") as f:
            queue_config = json.load(f)
    else:
        print(f"WARNING: {queue_path} not found, creating default")
        queue_config = {"proxy_groups": {}}

    # Read CSV
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("CSV is empty")
        return

    print(f"Found {len(rows)} accounts in CSV")
    print()

    created = 0
    skipped = 0

    for row in rows:
        adspower_id = row.get("adspower_id", "").strip()
        username = row.get("username", "").strip()
        creator = row.get("creator", "").strip()
        proxy_group = row.get("proxy_group", "").strip()
        display_name = row.get("display_name", "").strip() or username
        age = int(row.get("age", 22) or 22)
        gender = row.get("gender", "F").strip() or "F"
        location = row.get("location", "").strip()

        if not adspower_id or not username:
            print(f"  SKIP: missing adspower_id or username: {row}")
            skipped += 1
            continue

        # Use username as profile_id (lowercased, no spaces)
        profile_id = username.lower().replace(" ", "_")

        # Check if already exists
        if pm.get_profile(profile_id):
            print(f"  EXISTS: {profile_id} (skipping)")
            skipped += 1
            continue

        # Generate persona
        persona = generate_persona(location)

        # Build profile
        profile = AccountProfile(
            profile_id=profile_id,
            adspower_id=adspower_id,
            display_name=display_name,
            attributes=AccountAttributes(age=age, gender=gender),
            persona=persona,
            title_templates={"default": "{title}"},
            content_tags=[],
            reddit_account=RedditAccount(username=username),
            created_at=datetime.now().isoformat(),
            creator=creator,
        )

        print(f"  + {profile_id} (ads:{adspower_id}, creator:{creator}, "
              f"proxy:{proxy_group}, {len(persona.favorite_subs)} fav subs)")

        if not dry_run:
            pm.profiles[profile_id] = profile

        # Add to proxy group in queue config
        if proxy_group and proxy_group in queue_config.get("proxy_groups", {}):
            accts = queue_config["proxy_groups"][proxy_group].get("accounts", [])
            if profile_id not in accts:
                if not dry_run:
                    accts.append(profile_id)
                    queue_config["proxy_groups"][proxy_group]["accounts"] = accts
        elif proxy_group:
            print(f"    WARNING: proxy_group '{proxy_group}' not in queue_config")

        created += 1

    print()
    print(f"Summary: {created} created, {skipped} skipped")

    if dry_run:
        print("(dry run - no files written)")
        return

    # Save profiles
    pm.save()

    # Save queue config
    with open(queue_path, "w", encoding="utf-8") as f:
        json.dump(queue_config, f, indent=2)
    print(f"Saved queue config to {queue_path}")

    # Print proxy group summary
    print()
    for pg_name, pg in queue_config.get("proxy_groups", {}).items():
        accts = pg.get("accounts", [])
        print(f"  {pg_name}: {len(accts)} accounts")


def main():
    parser = argparse.ArgumentParser(description="Bulk import accounts from CSV")
    parser.add_argument("csv_file", help="Path to CSV file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be imported without writing")
    args = parser.parse_args()
    import_csv(args.csv_file, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
