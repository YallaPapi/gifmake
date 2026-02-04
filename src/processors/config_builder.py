"""
Unified Subreddit Config Builder

Combines all processed data (tiers, categories, flairs, raw data) into a single
unified config that the posting workflow can use.

Input files:
- subreddit_tiers.json (tier classification)
- subreddit_categories.json (content categories)
- subreddit_flairs.json (flair/title requirements)
- subreddit_data_v3.json (raw scraped data for subscriber counts)

Output file:
- subreddit_config.json
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any


def load_json(path: str) -> Dict:
    """Load JSON file with error handling."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Warning: File not found: {path}")
        return {}
    except json.JSONDecodeError as e:
        print(f"Warning: Invalid JSON in {path}: {e}")
        return {}


def build_unified_config(
    tiers_file: str = "subreddit_tiers.json",
    categories_file: str = "subreddit_categories.json",
    flairs_file: str = "subreddit_flairs.json",
    raw_data_file: str = "subreddit_data_v3.json",
    output_file: str = "subreddit_config.json"
) -> Dict:
    """
    Combine all processed data into unified config.

    Args:
        tiers_file: Path to tier classification JSON
        categories_file: Path to content categories JSON
        flairs_file: Path to flair/title requirements JSON
        raw_data_file: Path to raw scraped data JSON
        output_file: Path to output unified config JSON

    Returns:
        The unified config dictionary
    """
    # Load all input files
    print("Loading input files...")
    tiers = load_json(tiers_file)
    categories = load_json(categories_file)
    flairs = load_json(flairs_file)
    raw_data = load_json(raw_data_file)

    # Initialize result structure
    result = {
        "subreddits": {},
        "by_tier": {"1": [], "2": [], "3": []},
        "by_category": {},
        "stats": {}
    }

    # Get all subreddit names from tier lists
    all_subs = set()
    tier_1_list = tiers.get("tier_1", [])
    tier_2_list = tiers.get("tier_2", [])
    tier_3_list = tiers.get("tier_3", [])

    all_subs.update(tier_1_list)
    all_subs.update(tier_2_list)
    all_subs.update(tier_3_list)

    print(f"Found {len(all_subs)} subreddits across all tiers")

    # Get category data structures
    by_subreddit = categories.get("by_subreddit", {})
    by_category = categories.get("by_category", {})

    # Get flair details
    flair_details = flairs.get("details", {})
    flair_required_list = set(flairs.get("flair_required", []))
    title_format_required_list = set(flairs.get("title_format_required", []))

    # Build subreddits dict
    print("Building subreddit entries...")
    for sub_name in all_subs:
        # Determine tier
        if sub_name in tier_3_list:
            tier = 3
        elif sub_name in tier_2_list:
            tier = 2
        else:
            tier = 1

        # Get categories for this subreddit
        sub_categories = by_subreddit.get(sub_name, ["general_nsfw"])

        # Get flair info
        flair_info = flair_details.get(sub_name, {})
        flair_required = sub_name in flair_required_list or flair_info.get("flair_required", False)
        title_formats = flair_info.get("title_formats", [])
        title_format = title_formats[0] if title_formats else None

        # Get raw data (subscribers, etc.)
        raw = raw_data.get(sub_name, {})
        subscribers = raw.get("subscribers", 0)
        over18 = raw.get("over18", True)

        # Get tier details for rules summary
        tier_details = tiers.get("details", {}).get(sub_name, {})
        reasons = tier_details.get("reasons", [])
        rules_summary = reasons[0] if reasons else None

        result["subreddits"][sub_name] = {
            "tier": tier,
            "categories": sub_categories,
            "subscribers": subscribers,
            "flair_required": flair_required,
            "title_format": title_format,
            "title_formats": title_formats,
            "over18": over18,
            "rules_summary": rules_summary
        }

        # Add to by_tier
        result["by_tier"][str(tier)].append(sub_name)

    # Sort by_tier lists by subscriber count (descending)
    print("Sorting tier lists by subscriber count...")
    for tier in ["1", "2", "3"]:
        result["by_tier"][tier].sort(
            key=lambda x: result["subreddits"].get(x, {}).get("subscribers", 0),
            reverse=True
        )

    # Build by_category with tier breakdown
    print("Building category breakdown...")
    all_categories = set()
    for cats in by_subreddit.values():
        all_categories.update(cats)

    for cat in sorted(all_categories):
        result["by_category"][cat] = {"tier_1": [], "tier_2": [], "tier_3": []}
        cat_subs = by_category.get(cat, [])

        for sub_name in cat_subs:
            if sub_name not in result["subreddits"]:
                continue
            tier = result["subreddits"][sub_name]["tier"]
            tier_key = f"tier_{tier}"
            result["by_category"][cat][tier_key].append(sub_name)

        # Sort each tier list by subscribers
        for tier_key in ["tier_1", "tier_2", "tier_3"]:
            result["by_category"][cat][tier_key].sort(
                key=lambda x: result["subreddits"].get(x, {}).get("subscribers", 0),
                reverse=True
            )

    # Calculate stats
    print("Calculating stats...")
    result["stats"] = {
        "total": len(all_subs),
        "tier_1_count": len(result["by_tier"]["1"]),
        "tier_2_count": len(result["by_tier"]["2"]),
        "tier_3_count": len(result["by_tier"]["3"]),
        "categories_count": len(all_categories),
        "flair_required_count": len([s for s in result["subreddits"].values() if s["flair_required"]]),
        "title_format_count": len([s for s in result["subreddits"].values() if s["title_format"]]),
    }

    # Save output
    print(f"Saving unified config to {output_file}...")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2)

    return result


def get_postable_subreddits(
    config: Dict,
    content_tags: List[str],
    account_tier: int = 1
) -> List[str]:
    """
    Get list of subreddits an account can post to based on content tags and tier.

    Args:
        config: The unified config dict
        content_tags: List of content categories (e.g. ["boobs", "petite"])
        account_tier: Max tier the account qualifies for (1, 2, or 3)

    Returns:
        List of subreddit names sorted by subscriber count (descending)
    """
    results = set()

    for tag in content_tags:
        if tag not in config["by_category"]:
            continue

        cat_data = config["by_category"][tag]

        # Add tier 1 always (easiest to post to)
        results.update(cat_data.get("tier_1", []))

        # Add tier 2 if account qualifies
        if account_tier >= 2:
            results.update(cat_data.get("tier_2", []))

        # Add tier 3 if account qualifies
        if account_tier >= 3:
            results.update(cat_data.get("tier_3", []))

    # Sort by subscribers (descending)
    return sorted(
        list(results),
        key=lambda x: config["subreddits"].get(x, {}).get("subscribers", 0),
        reverse=True
    )


def get_posting_info(config: Dict, subreddit: str, profile: Dict) -> Dict:
    """
    Get all info needed to post to a subreddit.

    Args:
        config: The unified config dict
        subreddit: Name of the subreddit
        profile: Account profile dict containing:
            - account_tier: int (1, 2, or 3)
            - age: int (for age_in_title format)
            - title: str (base title text)
            - flair_mappings: dict (subreddit -> flair name)

    Returns:
        Dict with:
            - can_post: True/False
            - subreddit: str
            - title: str (generated title)
            - flair: str or None
            - flair_required: bool
            - reason: str (why can/cannot post)
    """
    sub_config = config["subreddits"].get(subreddit)
    if not sub_config:
        return {
            "can_post": False,
            "subreddit": subreddit,
            "title": None,
            "flair": None,
            "flair_required": False,
            "reason": "Subreddit not in config"
        }

    # Check tier eligibility
    account_tier = profile.get("account_tier", 1)
    if sub_config["tier"] > account_tier:
        return {
            "can_post": False,
            "subreddit": subreddit,
            "title": None,
            "flair": None,
            "flair_required": sub_config.get("flair_required", False),
            "reason": f"Requires Tier {sub_config['tier']}, account is Tier {account_tier}"
        }

    # Generate title based on format requirements
    base_title = profile.get("title", "")
    title_format = sub_config.get("title_format")
    title_formats = sub_config.get("title_formats", [])

    title = base_title

    # Check for age requirement
    if any(fmt in title_formats for fmt in ["age_in_title", "age_required"]):
        age = profile.get("age", 25)
        # Prepend age at the beginning
        if not title.startswith(f"{age}"):
            title = f"{age} - {title}" if title else str(age)

    # Check for gender tag requirement
    if any(fmt in title_formats for fmt in ["gender_tag", "gender_in_title", "gender_required"]):
        gender = profile.get("gender", "F")
        # Add gender tag
        if f"[{gender}]" not in title:
            title = f"[{gender}] {title}"

    # Check for OC tag requirement
    if any(fmt in title_formats for fmt in ["oc_in_title", "oc_tag_in_title"]):
        if "[OC]" not in title.upper():
            title = f"[OC] {title}"

    # Get flair from mappings
    flair_mappings = profile.get("flair_mappings", {})
    flair = flair_mappings.get(subreddit)

    # Check if flair is required but not provided
    flair_required = sub_config.get("flair_required", False)
    if flair_required and not flair:
        return {
            "can_post": False,
            "subreddit": subreddit,
            "title": title,
            "flair": None,
            "flair_required": True,
            "reason": "Flair required but not configured for this subreddit"
        }

    return {
        "can_post": True,
        "subreddit": subreddit,
        "title": title.strip(),
        "flair": flair,
        "flair_required": flair_required,
        "reason": "OK"
    }


def filter_by_subscribers(
    config: Dict,
    subreddits: List[str],
    min_subscribers: int = 0,
    max_subscribers: int = None
) -> List[str]:
    """
    Filter subreddit list by subscriber count.

    Args:
        config: The unified config dict
        subreddits: List of subreddit names to filter
        min_subscribers: Minimum subscriber count (inclusive)
        max_subscribers: Maximum subscriber count (inclusive, None = no limit)

    Returns:
        Filtered list of subreddit names
    """
    results = []
    for sub in subreddits:
        sub_info = config["subreddits"].get(sub, {})
        subs_count = sub_info.get("subscribers", 0)

        if subs_count < min_subscribers:
            continue
        if max_subscribers is not None and subs_count > max_subscribers:
            continue

        results.append(sub)

    return results


def get_subreddits_needing_flair(config: Dict) -> List[str]:
    """
    Get list of subreddits that require flair.

    Args:
        config: The unified config dict

    Returns:
        List of subreddit names sorted by subscriber count
    """
    results = [
        name for name, info in config["subreddits"].items()
        if info.get("flair_required", False)
    ]

    return sorted(
        results,
        key=lambda x: config["subreddits"].get(x, {}).get("subscribers", 0),
        reverse=True
    )


def get_subreddits_by_title_format(config: Dict, title_format: str) -> List[str]:
    """
    Get list of subreddits that require a specific title format.

    Args:
        config: The unified config dict
        title_format: The title format to search for (e.g. "age_in_title", "oc_in_title")

    Returns:
        List of subreddit names sorted by subscriber count
    """
    results = []
    for name, info in config["subreddits"].items():
        formats = info.get("title_formats", [])
        if title_format in formats:
            results.append(name)

    return sorted(
        results,
        key=lambda x: config["subreddits"].get(x, {}).get("subscribers", 0),
        reverse=True
    )


def load_config(config_path: str = "subreddit_config.json") -> Dict:
    """
    Load the unified config from file.

    Args:
        config_path: Path to the config JSON file

    Returns:
        The unified config dictionary
    """
    return load_json(config_path)


if __name__ == "__main__":
    import os

    # Change to project root directory
    project_root = Path(__file__).parent.parent.parent
    os.chdir(project_root)

    print("=" * 60)
    print("Unified Subreddit Config Builder")
    print("=" * 60)
    print()

    # Build the unified config
    result = build_unified_config()

    print()
    print("=" * 60)
    print("STATS:")
    print("=" * 60)
    for key, value in result["stats"].items():
        print(f"  {key}: {value}")

    print()
    print("=" * 60)
    print("TOP 10 TIER 1 SUBREDDITS (by subscribers):")
    print("=" * 60)
    for sub in result["by_tier"]["1"][:10]:
        info = result["subreddits"][sub]
        print(f"  {sub}: {info['subscribers']:,} subscribers")

    print()
    print("=" * 60)
    print("EXAMPLE: Get postable subreddits for 'boobs' content, Tier 1 account")
    print("=" * 60)
    postable = get_postable_subreddits(result, ["boobs"], account_tier=1)
    print(f"Found {len(postable)} subreddits:")
    for sub in postable[:10]:
        info = result["subreddits"][sub]
        print(f"  {sub}: {info['subscribers']:,} subscribers, categories: {info['categories']}")

    print()
    print("=" * 60)
    print("EXAMPLE: Get postable subreddits for 'asian' + 'petite' content, Tier 3 account")
    print("=" * 60)
    postable = get_postable_subreddits(result, ["asian", "petite"], account_tier=3)
    print(f"Found {len(postable)} subreddits:")
    for sub in postable[:10]:
        info = result["subreddits"][sub]
        print(f"  {sub}: {info['subscribers']:,} subscribers, tier: {info['tier']}")

    print()
    print("=" * 60)
    print("EXAMPLE: Get posting info for 'teenbeauties'")
    print("=" * 60)
    profile = {
        "account_tier": 1,
        "age": 22,
        "title": "Do you like my body?",
        "gender": "F",
        "flair_mappings": {}
    }
    posting_info = get_posting_info(result, "teenbeauties", profile)
    print(f"  can_post: {posting_info['can_post']}")
    print(f"  title: {posting_info['title']}")
    print(f"  flair: {posting_info['flair']}")
    print(f"  flair_required: {posting_info['flair_required']}")
    print(f"  reason: {posting_info['reason']}")

    print()
    print("=" * 60)
    print("SUBREDDITS REQUIRING FLAIR:")
    print("=" * 60)
    flair_subs = get_subreddits_needing_flair(result)
    print(f"Found {len(flair_subs)} subreddits requiring flair:")
    for sub in flair_subs[:15]:
        info = result["subreddits"][sub]
        print(f"  {sub}: {info['subscribers']:,} subscribers")

    print()
    print("=" * 60)
    print("SUBREDDITS REQUIRING AGE IN TITLE:")
    print("=" * 60)
    age_subs = get_subreddits_by_title_format(result, "age_in_title")
    print(f"Found {len(age_subs)} subreddits requiring age in title:")
    for sub in age_subs[:15]:
        info = result["subreddits"][sub]
        print(f"  {sub}: {info['subscribers']:,} subscribers")

    print()
    print("=" * 60)
    print(f"Config saved to: subreddit_config.json")
    print("=" * 60)
