"""
Subreddit Tier Classifier

Classifies subreddits into tiers based on posting requirements:
- Tier 1: No/minimal requirements (easy to post)
- Tier 2: Account age/karma requirements (medium barrier)
- Tier 3: Verification required (highest barrier)
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple


# Tier 3: Verification Required (HIGHEST barrier)
# These patterns indicate users must verify their identity before posting
TIER_3_PATTERNS = [
    # Direct verification requirements
    r'must.*verif',
    r'verif.*required',
    r'require.*verif',
    r'verification.*process',
    r'you.*need.*to.*verif',
    r'need.*to.*be.*verified',
    r'must.*be.*verified',
    r'only.*verified.*can.*post',
    r'verified.*users.*only',
    r'verification.*is.*required',
    r'won\'t.*be.*able.*to.*post.*until.*verified',
    r'won\'t.*be.*able.*to.*post.*until.*approved',

    # Verification process instructions
    r'selfie.*paper',
    r'paper.*with.*username',
    r'handwritten.*sign',
    r'photo.*username.*paper',
    r'pics?.*including.*paper',
    r'verify.*yourself',
    r'get.*verified',
    r'how.*to.*verify',
    r'verification.*wiki',
    r'submit.*verification',
    r'send.*verification',

    # OC verification
    r'oc.*only.*verif',
    r'original.*content.*verif',
    r'no.*unverified.*oc',
    r'must.*verify.*before.*post',
    r'verify.*before.*post',

    # Restricted to verified
    r'restricted.*to.*verified',
    r'this.*sub.*require.*verification',
    r'sub.*is.*for.*verified',
]

# Tier 2: Account Requirements (MEDIUM barrier)
# These patterns indicate account age or karma minimums
# NOTE: Patterns must be specific to avoid false positives from:
# - "social media accounts" (Instagram, Snapchat, etc.)
# - Random numbers in rules
# - Generic "account" mentions
TIER_2_PATTERNS = [
    # Account age requirements (specific phrases)
    r'account\s+age',
    r'account\s+must\s+be\s+\d+\s*day',
    r'account\s+at\s+least\s+\d+',
    r'accounts?\s+less\s+than\s+\d+\s*day',
    r'accounts?\s+younger\s+than\s+\d+',
    r'accounts?\s+newer\s+than\s+\d+',
    r'accounts?\s+older\s+than\s+\d+',
    r'\d+\s*day\s*old\s+account',
    r'minimum\s+account\s+age',
    r'reddit\s+account.*\d+\s*day',

    # Karma requirements (specific phrases)
    r'minimum\s+karma',
    r'karma\s+requirement',
    r'karma\s+minimum',
    r'require.*\d+\s*karma',
    r'\d+\s*karma\s+required',
    r'\d+\s*karma\s+minimum',
    r'at\s+least\s+\d+\s*karma',
    r'low\s+karma\s+removed',
    r'low\s+karma\s+not\s+allowed',
    r'low\s+karma\s+will\s+be',
    r'no\s+low\s+karma',
    r'meet\s+karma',
    r'karma\s+threshold',
    r'comment\s+karma',
    r'post\s+karma',
    r'enough\s+karma',

    # New account restrictions (specific about Reddit accounts, not social media)
    r'new\s+reddit\s+account',
    r'brand\s+new\s+account.*not\s+allowed',
    r'brand\s+new\s+account.*banned',
    r'brand\s+new\s+account.*removed',
    r'new\s+accounts?\s+are\s+not\s+allowed\s+to\s+post',
    r'new\s+accounts?\s+will\s+be\s+removed',
    r'new\s+accounts?\s+cannot\s+post',

    # Participate elsewhere first
    r'participate.*elsewhere.*first',
    r'participate\s+on\s+reddit\s+elsewhere',
    r'participate.*before\s+(posting|submitting)',
    r'engage\s+with.*community.*first',

    # Combined requirements
    r'account\s+age.*karma',
    r'karma.*account\s+age',
    r'new\s+account.*low\s+karma',

    # Rule titles that explicitly mention these requirements
    r'account\s+age.*karma.*requirement',
    r'new\s+account.*low\s+karma',
]


def classify_subreddit(rules: List[Dict]) -> Tuple[int, List[str]]:
    """
    Classify a subreddit based on its rules.

    Args:
        rules: List of rule dictionaries with 'title' and 'description' keys

    Returns:
        Tuple of (tier, reasons) where:
        - tier: 1 (easy), 2 (medium), or 3 (hard)
        - reasons: List of strings explaining the classification
    """
    # Combine all rule text for searching
    all_text = ""
    for rule in rules:
        title = rule.get('title', '') or ''
        description = rule.get('description', '') or ''
        all_text += f" {title} {description}"

    all_text = all_text.lower()
    reasons = []

    # Check Tier 3 first (most restrictive)
    for pattern in TIER_3_PATTERNS:
        match = re.search(pattern, all_text, re.IGNORECASE)
        if match:
            matched_text = match.group(0)[:50]  # Truncate for readability
            reasons.append(f"Tier 3 match: '{matched_text}' (pattern: {pattern})")
            return (3, reasons)

    # Check Tier 2 (account/karma requirements)
    for pattern in TIER_2_PATTERNS:
        match = re.search(pattern, all_text, re.IGNORECASE)
        if match:
            matched_text = match.group(0)[:50]
            reasons.append(f"Tier 2 match: '{matched_text}' (pattern: {pattern})")
            return (2, reasons)

    # Default to Tier 1 (no restrictive requirements found)
    return (1, ["No restrictive requirements found"])


def process_all(input_file: str, output_file: str) -> Dict:
    """
    Process all subreddits from input file and classify them.

    Args:
        input_file: Path to subreddit_data_v3.json
        output_file: Path to write subreddit_tiers.json

    Returns:
        Results dictionary with tier lists and details
    """
    input_path = Path(input_file)
    output_path = Path(output_file)

    print(f"Reading from: {input_path.absolute()}")

    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    results = {
        "tier_1": [],
        "tier_2": [],
        "tier_3": [],
        "stats": {
            "total_processed": 0,
            "skipped_not_scraped": 0,
            "skipped_error": 0,
        },
        "details": {}
    }

    for name, sub in data.items():
        # Skip if not successfully scraped
        if not sub.get('scraped'):
            results["stats"]["skipped_not_scraped"] += 1
            continue

        if sub.get('error'):
            results["stats"]["skipped_error"] += 1
            continue

        results["stats"]["total_processed"] += 1

        # Get rules (may be empty list or None)
        rules = sub.get('rules') or []

        # Classify
        tier, reasons = classify_subreddit(rules)

        # Add to appropriate tier list
        results[f"tier_{tier}"].append(name)

        # Store details
        results["details"][name] = {
            "tier": tier,
            "reasons": reasons,
            "subscribers": sub.get('subscribers', 0),
            "over18": sub.get('over18', False),
            "rule_count": len(rules)
        }

    # Sort tier lists by subscriber count (descending)
    for tier in [1, 2, 3]:
        tier_key = f"tier_{tier}"
        results[tier_key] = sorted(
            results[tier_key],
            key=lambda x: results["details"][x]["subscribers"],
            reverse=True
        )

    # Write output
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)

    print(f"\nWritten to: {output_path.absolute()}")

    return results


def print_summary(results: Dict):
    """Print a summary of the classification results."""
    print("\n" + "=" * 60)
    print("SUBREDDIT TIER CLASSIFICATION SUMMARY")
    print("=" * 60)

    stats = results["stats"]
    print(f"\nProcessed: {stats['total_processed']} subreddits")
    print(f"Skipped (not scraped): {stats['skipped_not_scraped']}")
    print(f"Skipped (errors): {stats['skipped_error']}")

    print(f"\n--- TIER BREAKDOWN ---")
    print(f"Tier 1 (Easy - No requirements):     {len(results['tier_1']):,} subreddits")
    print(f"Tier 2 (Medium - Account/Karma):     {len(results['tier_2']):,} subreddits")
    print(f"Tier 3 (Hard - Verification):        {len(results['tier_3']):,} subreddits")

    # Show top subreddits in each tier
    print(f"\n--- TOP 10 TIER 1 (by subscribers) ---")
    for name in results['tier_1'][:10]:
        subs = results['details'][name]['subscribers']
        print(f"  r/{name}: {subs:,} subscribers")

    print(f"\n--- TOP 10 TIER 2 (by subscribers) ---")
    for name in results['tier_2'][:10]:
        subs = results['details'][name]['subscribers']
        reason = results['details'][name]['reasons'][0] if results['details'][name]['reasons'] else "N/A"
        print(f"  r/{name}: {subs:,} subscribers")
        print(f"      Reason: {reason[:70]}...")

    print(f"\n--- TOP 10 TIER 3 (by subscribers) ---")
    for name in results['tier_3'][:10]:
        subs = results['details'][name]['subscribers']
        reason = results['details'][name]['reasons'][0] if results['details'][name]['reasons'] else "N/A"
        print(f"  r/{name}: {subs:,} subscribers")
        print(f"      Reason: {reason[:70]}...")


def main():
    """Main entry point."""
    # Use paths relative to project root
    project_root = Path(__file__).parent.parent.parent
    input_file = project_root / "subreddit_data_v3.json"
    output_file = project_root / "subreddit_tiers.json"

    # Check if input file exists
    if not input_file.exists():
        print(f"ERROR: Input file not found: {input_file}")
        print("Make sure subreddit_data_v3.json exists in the project root.")
        return

    # Process and classify
    results = process_all(str(input_file), str(output_file))

    # Print summary
    print_summary(results)


if __name__ == "__main__":
    main()
