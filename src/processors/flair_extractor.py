"""
Flair Extractor - Identifies subreddits requiring flair and title format requirements.

This module analyzes subreddit rules to detect:
1. Flair requirements (post must have flair)
2. Title format requirements ([F], [24F], OC in title, etc.)
3. Raw flair-related rules for manual review

Usage:
    python flair_extractor.py

    Or import and use:
    from flair_extractor import extract_flair_info, process_all
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Patterns indicating flair is required
FLAIR_INDICATORS = [
    r'flair.*required',
    r'must.*flair',
    r'use.*flair',
    r'select.*flair',
    r'add.*flair',
    r'post.*flair',
    r'choose.*flair',
    r'flair.*your.*post',
    r'posts.*must.*be.*flair',
    r'flair.*prior.*to.*post',
    r'flair.*all.*submission',
    r'flair.*appropriately',
    r'use.*the.*right.*flair',
    r'wrong.*flair',
    r'correct.*flair',
    r'proper.*flair',
]

# Patterns for title format requirements with their type labels
TITLE_FORMAT_PATTERNS: List[Tuple[str, str]] = [
    # Gender tags
    (r'\[f\]|\[m\]|\(f\)|\(m\)', "gender_tag"),  # [F] or [M] or (f) or (m)
    (r'\[\d+\s*f\]|\[\d+\s*m\]', "age_gender_tag"),  # [24F] or [24 F]
    (r'\(\d+\s*f\)|\(\d+\s*m\)', "age_gender_tag_parens"),  # (24F)

    # OC requirements
    (r'\boc\b.*in.*title', "oc_in_title"),
    (r'title.*\boc\b', "oc_in_title"),
    (r'\[oc\].*title', "oc_tag_in_title"),

    # Age requirements
    (r'age.*in.*title', "age_in_title"),
    (r'include.*age', "age_required"),
    (r'title.*must.*include.*age', "age_in_title"),

    # Gender requirements
    (r'include.*gender', "gender_required"),
    (r'gender.*in.*title', "gender_in_title"),

    # Custom format requirements
    (r'title.*must.*contain', "custom_title_format"),
    (r'title.*must.*include', "custom_title_format"),
    (r'title.*format', "custom_title_format"),
    (r'format.*title', "custom_title_format"),

    # Source requirements
    (r'source.*in.*title', "source_in_title"),
    (r'provide.*source.*title', "source_in_title"),

    # Clothing/description requirements
    (r'title.*must.*describe', "description_required"),
    (r'clothing.*description.*title', "clothing_in_title"),
]

# Patterns that indicate flair is NOT required (to avoid false positives)
FLAIR_EXCLUSIONS = [
    r'not.*required',
    r'optional.*flair',
    r'flair.*optional',
    r'without.*verification',
    r'do not use.*flair',
    r'don\'t use.*flair',
]


def extract_flair_info(rules: List[Dict]) -> Dict:
    """
    Extracts flair and title format requirements from subreddit rules.

    Args:
        rules: List of rule dictionaries with 'title' and 'description' keys

    Returns:
        Dictionary containing:
        - flair_required: bool
        - flair_indicators: list of matched patterns
        - title_formats: list of required title format types
        - raw_flair_rules: list of relevant rule excerpts for manual review
    """
    # Combine all rule text for searching
    all_text = ""
    for rule in rules:
        title = rule.get('title') or ''
        desc = rule.get('description') or ''
        all_text += f" {title} {desc}"
    all_text_lower = all_text.lower()

    result = {
        "flair_required": False,
        "flair_indicators": [],
        "title_formats": [],
        "raw_flair_rules": []
    }

    # Check for flair exclusions first
    has_exclusion = False
    for pattern in FLAIR_EXCLUSIONS:
        if re.search(pattern, all_text_lower):
            has_exclusion = True
            break

    # Check for flair requirements
    for pattern in FLAIR_INDICATORS:
        match = re.search(pattern, all_text_lower)
        if match:
            # Only mark as required if no exclusion found in same context
            # Get surrounding context
            start = max(0, match.start() - 50)
            end = min(len(all_text_lower), match.end() + 50)
            context = all_text_lower[start:end]

            # Check if exclusion is in this context
            context_has_exclusion = any(
                re.search(exc, context) for exc in FLAIR_EXCLUSIONS
            )

            if not context_has_exclusion:
                result["flair_required"] = True
                if pattern not in result["flair_indicators"]:
                    result["flair_indicators"].append(pattern)

    # Check for title format requirements
    seen_formats = set()
    for pattern, format_type in TITLE_FORMAT_PATTERNS:
        if re.search(pattern, all_text_lower):
            if format_type not in seen_formats:
                result["title_formats"].append(format_type)
                seen_formats.add(format_type)

    # Extract raw flair-related rules for manual review
    flair_keywords = ['flair', 'title format', 'title must', 'tag', '[f]', '[m]',
                      '[oc]', 'age in', 'gender in', 'include age', 'include gender']

    for rule in rules:
        title = (rule.get('title') or '').lower()
        desc = (rule.get('description') or '').lower()

        # Check if rule is flair/title related
        is_relevant = any(kw in title or kw in desc for kw in flair_keywords)

        if is_relevant:
            # Truncate description if too long
            desc_text = rule.get('description') or ''
            if len(desc_text) > 300:
                desc_text = desc_text[:300] + "..."

            result["raw_flair_rules"].append({
                "title": rule.get('title') or '',
                "description": desc_text
            })

    return result


def process_all(input_file: str, output_file: str) -> Dict:
    """
    Process all subreddits and extract flair information.

    Args:
        input_file: Path to subreddit_data_v3.json
        output_file: Path to output JSON file

    Returns:
        Results dictionary
    """
    input_path = Path(input_file)
    output_path = Path(output_file)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    print(f"Loading data from {input_file}...")
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    results = {
        "flair_required": [],
        "title_format_required": [],
        "both_required": [],
        "details": {},
        "summary": {
            "total_processed": 0,
            "total_scraped": 0,
            "flair_required_count": 0,
            "title_format_count": 0,
            "both_count": 0,
        }
    }

    print(f"Processing {len(data)} subreddits...")

    for name, sub in data.items():
        results["summary"]["total_processed"] += 1

        # Skip unscraped or errored subreddits
        if not sub.get('scraped') or sub.get('error'):
            continue

        results["summary"]["total_scraped"] += 1

        rules = sub.get('rules', [])
        if not rules:
            continue

        flair_info = extract_flair_info(rules)

        # Categorize subreddit
        has_flair = flair_info["flair_required"]
        has_title_format = bool(flair_info["title_formats"])

        if has_flair:
            results["flair_required"].append(name)
            results["summary"]["flair_required_count"] += 1

        if has_title_format:
            results["title_format_required"].append(name)
            results["summary"]["title_format_count"] += 1

        if has_flair and has_title_format:
            results["both_required"].append(name)
            results["summary"]["both_count"] += 1

        # Store details only if there's something interesting
        if has_flair or has_title_format or flair_info["raw_flair_rules"]:
            results["details"][name] = {
                "flair_required": flair_info["flair_required"],
                "flair_indicators": flair_info["flair_indicators"],
                "title_formats": flair_info["title_formats"],
                "raw_flair_rules": flair_info["raw_flair_rules"],
                "subscribers": sub.get("subscribers", 0)
            }

    # Sort lists by name
    results["flair_required"].sort()
    results["title_format_required"].sort()
    results["both_required"].sort()

    # Write output
    print(f"Writing results to {output_file}...")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Print summary
    print("\n" + "="*60)
    print("FLAIR EXTRACTION SUMMARY")
    print("="*60)
    print(f"Total subreddits processed: {results['summary']['total_processed']}")
    print(f"Successfully scraped: {results['summary']['total_scraped']}")
    print(f"Flair required: {results['summary']['flair_required_count']} subreddits")
    print(f"Title format required: {results['summary']['title_format_count']} subreddits")
    print(f"Both flair AND title format: {results['summary']['both_count']} subreddits")
    print(f"Total with flair/title rules: {len(results['details'])} subreddits")
    print("="*60)

    # Show some examples
    if results["flair_required"]:
        print("\nExample subreddits requiring flair:")
        for sub in results["flair_required"][:10]:
            print(f"  - r/{sub}")

    if results["title_format_required"]:
        print("\nExample subreddits with title format requirements:")
        for sub in results["title_format_required"][:10]:
            detail = results["details"].get(sub, {})
            formats = detail.get("title_formats", [])
            print(f"  - r/{sub}: {', '.join(formats)}")

    return results


def get_flair_requirements(subreddit_name: str, data_file: str = None) -> Optional[Dict]:
    """
    Get flair requirements for a specific subreddit.

    Args:
        subreddit_name: Name of the subreddit
        data_file: Path to subreddit data file (defaults to subreddit_data_v3.json)

    Returns:
        Flair info dictionary or None if not found
    """
    if data_file is None:
        # Try to find the data file relative to this script
        script_dir = Path(__file__).parent.parent.parent
        data_file = script_dir / "subreddit_data_v3.json"

    data_path = Path(data_file)
    if not data_path.exists():
        return None

    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    sub = data.get(subreddit_name.lower())
    if not sub or not sub.get('scraped') or sub.get('error'):
        return None

    rules = sub.get('rules', [])
    return extract_flair_info(rules)


if __name__ == "__main__":
    import sys
    import os

    # Determine paths - try multiple locations
    script_dir = Path(__file__).resolve().parent.parent.parent
    cwd = Path(os.getcwd())

    # Check for input file in order of preference
    possible_inputs = [
        script_dir / "subreddit_data_v3.json",
        cwd / "subreddit_data_v3.json",
    ]

    input_file = None
    for path in possible_inputs:
        if path.exists():
            input_file = path
            break

    # Allow command line override
    if len(sys.argv) > 1:
        input_file = Path(sys.argv[1])

    # Output file - prefer same directory as input
    if input_file:
        output_file = input_file.parent / "subreddit_flairs.json"
    else:
        output_file = script_dir / "subreddit_flairs.json"

    if len(sys.argv) > 2:
        output_file = Path(sys.argv[2])

    if input_file is None:
        print("Error: Could not find subreddit_data_v3.json")
        print(f"Searched in: {script_dir}, {cwd}")
        sys.exit(1)

    try:
        process_all(str(input_file), str(output_file))
        print(f"\nResults saved to: {output_file}")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}")
        sys.exit(1)
