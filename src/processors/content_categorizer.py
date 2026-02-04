"""
Content Categorizer for Subreddits

Tags each subreddit with broad content categories based on name and description.
A subreddit can belong to multiple categories.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Set

# Keyword mapping for each category
CATEGORY_KEYWORDS = {
    "general_nsfw": ["nsfw", "porn", "nude", "naked", "gonewild", "sexy", "hot", "xxx", "adult"],
    "boobs": ["boob", "tit", "breast", "busty", "bust", "nipple", "cleavage", "chest", "rack", "titty", "boobies"],
    "ass": ["ass", "butt", "booty", "rear", "pawg", "asstastic", "behind", "bum", "cheeks"],
    "pussy": ["pussy", "vagina", "labia", "clit", "vulva", "innie", "mound", "kitty"],
    "asian": ["asian", "chinese", "japanese", "korean", "thai", "vietnamese", "filipin", "kpop", "jpop"],
    "latina": ["latina", "latin", "hispanic", "mexican", "spanish", "puerto"],
    "ebony": ["ebony", "black", "darksin"],
    "petite": ["petite", "tiny", "small", "skinny", "slim", "thin", "little", "spinner"],
    "thick": ["thick", "curvy", "bbw", "chubby", "plus", "fat", "plump", "ssbbw", "voluptuous", "thicc"],
    "milf": ["milf", "mom", "mature", "cougar", "over30", "over40", "wife", "30plus", "40plus", "gilf", "granny"],
    "teen": ["teen", "18", "19", "college", "young", "1819", "18_19", "barely", "legal"],
    "feet": ["feet", "foot", "toes", "sole", "feetish", "soles", "footjob"],
    "redhead": ["redhead", "ginger", "red hair", "redhair"],
    "blonde": ["blonde", "blond", "blondes"],
    "brunette": ["brunette", "brown hair", "dark hair", "darkhair", "brownhair"],
    "amateur": ["amateur", "homemade", "real", "oc", "girlfriend", "wife", "selfie", "selfshot", "realg"],
    "fitness": ["fit", "gym", "athletic", "muscle", "abs", "toned", "workout", "crossfit", "sporty"],
    "lingerie": ["lingerie", "bra", "panties", "thong", "stocking", "garter", "underwear", "lace", "corset"],
    "hairy": ["hairy", "bush", "natural", "unshaved", "furry", "pubes"],
    "anal": ["anal", "gape", "asshole", "butthole", "buttplug", "buttplugs"]
}


def categorize_subreddit(name: str, description: str) -> List[str]:
    """
    Returns list of categories this subreddit belongs to.
    A subreddit can have multiple categories.

    Args:
        name: Subreddit name
        description: Subreddit description

    Returns:
        List of category strings
    """
    # Combine name and description, convert to lowercase
    text = f"{name} {description or ''}".lower()
    categories = []

    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            # Use word boundary-like matching for short keywords to avoid false positives
            if len(keyword) <= 3:
                # For very short keywords, require word boundaries
                pattern = r'\b' + re.escape(keyword) + r'\b'
                if re.search(pattern, text):
                    categories.append(category)
                    break
            else:
                # For longer keywords, simple substring match is fine
                if keyword in text:
                    categories.append(category)
                    break

    # If no categories found, mark as general_nsfw
    if not categories:
        categories = ["general_nsfw"]

    return categories


def process_all(input_file: str, output_file: str) -> Dict:
    """
    Process all subreddits and output categorized data.

    Args:
        input_file: Path to subreddit_data_v3.json
        output_file: Path to output subreddit_categories.json

    Returns:
        Results dictionary with by_subreddit and by_category mappings
    """
    # Read input data
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Build both directions: sub->categories and category->subs
    results = {
        "by_subreddit": {},
        "by_category": {cat: [] for cat in CATEGORY_KEYWORDS.keys()}
    }

    # Track stats
    total_processed = 0
    skipped_errors = 0
    skipped_not_scraped = 0

    for name, sub in data.items():
        # Skip entries that weren't scraped or had errors
        if not sub.get('scraped'):
            skipped_not_scraped += 1
            continue
        if sub.get('error'):
            skipped_errors += 1
            continue

        description = sub.get('description', '') or ''
        categories = categorize_subreddit(name, description)

        results["by_subreddit"][name] = categories

        for cat in categories:
            if cat not in results["by_category"]:
                results["by_category"][cat] = []
            results["by_category"][cat].append(name)

        total_processed += 1

    # Write output
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)

    # Print stats
    print(f"\n{'='*60}")
    print("SUBREDDIT CONTENT CATEGORIZATION RESULTS")
    print(f"{'='*60}")
    print(f"\nProcessed: {total_processed} subreddits")
    print(f"Skipped (not scraped): {skipped_not_scraped}")
    print(f"Skipped (errors): {skipped_errors}")
    print(f"\n{'-'*40}")
    print("Category counts (sorted by size):")
    print(f"{'-'*40}")

    for cat, subs in sorted(results["by_category"].items(), key=lambda x: -len(x[1])):
        print(f"  {cat:20s}: {len(subs):5d} subreddits")

    # Calculate overlap stats
    multi_category = sum(1 for cats in results["by_subreddit"].values() if len(cats) > 1)
    single_category = sum(1 for cats in results["by_subreddit"].values() if len(cats) == 1)

    print(f"\n{'-'*40}")
    print("Category distribution:")
    print(f"{'-'*40}")
    print(f"  Single category:   {single_category:5d} subreddits")
    print(f"  Multiple categories: {multi_category:5d} subreddits")

    print(f"\nOutput saved to: {output_file}")
    print(f"{'='*60}\n")

    return results


def get_subreddits_by_category(results: Dict, category: str) -> List[str]:
    """Get all subreddits in a given category."""
    return results.get("by_category", {}).get(category, [])


def get_categories_for_subreddit(results: Dict, subreddit: str) -> List[str]:
    """Get all categories for a given subreddit."""
    return results.get("by_subreddit", {}).get(subreddit, [])


if __name__ == "__main__":
    import sys

    # Default paths
    base_dir = Path(__file__).parent.parent.parent  # Go up to project root
    default_input = base_dir / "subreddit_data_v3.json"
    default_output = base_dir / "subreddit_categories.json"

    # Allow command line overrides
    input_file = sys.argv[1] if len(sys.argv) > 1 else str(default_input)
    output_file = sys.argv[2] if len(sys.argv) > 2 else str(default_output)

    process_all(input_file, output_file)
