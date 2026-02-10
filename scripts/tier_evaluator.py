"""
Batch subreddit tier evaluator using Grok API.
Processes a chunk of subreddits and saves results to a JSON file.

Usage: python tier_evaluator.py <merged_json> <start_idx> <end_idx> <output_file> <api_key>
"""
import json
import sys
import time
import requests

BATCH_SIZE = 30  # subs per API call
GROK_MODEL = "grok-4-1-fast-reasoning"
GROK_URL = "https://api.x.ai/v1/chat/completions"
MAX_RETRIES = 3
RETRY_DELAY = 5
# API key passed as command line argument (sys.argv[5])

SYSTEM_PROMPT = """You evaluate NSFW subreddits for posting eligibility.

You will receive the subreddit NAME, DESCRIPTION, SUBSCRIBER COUNT, and RULES (if available).
USE ALL OF THIS INFORMATION to make your determination. The description is just as important as the rules.
Read the subreddit name itself - it tells you the niche. Read the description carefully - it often contains
posting restrictions, verification requirements, and content guidelines even when formal rules are missing.
Read the rules if present. Use subscriber count for context. USE EVERYTHING.

Do NOT say UNKNOWN just because formal rules are missing. If the description exists, READ IT and make a call.
Only use UNKNOWN if there is truly ZERO useful information (no description, no rules, nothing).

For each subreddit determine:

1. TIER: Can a BRAND NEW Reddit account (0 karma, 0 post history, 0 account age) post a RedGIFs video link there?
   - GREEN: Nothing in the name, description, or rules suggests a new account would be blocked.
   - YELLOW: Description or rules mention requirements (karma, account age, flair, specific formats) that might block new accounts.
   - RED: Description or rules require verification, approved submitters only, OC only, or other hard blocks.
   - UNKNOWN: Absolutely zero information available (no description AND no rules). This should be rare.

2. CATEGORY: Content niche based on the subreddit name and description. Use one of these or create a specific one:
   pussy, ass, boobs, teen, amateur, asian, latina, ebony, milf, redhead, blonde, brunette,
   petite, thick/curvy, bbw, feet, anal, blowjob, cumshot, lingerie, bikini, cosplay,
   lesbian, threesome, bdsm, public, voyeur, couple, onlyfans, general_nsfw, hentai, fitness,
   local/{city_or_state} (for geo-targeted subs like hookup/swingers/gonewild subs tied to a location)

Respond ONLY in valid JSON. No markdown, no backticks, no explanation outside the JSON.
Format:
{
  "subreddit_name": {"tier": "GREEN/YELLOW/RED/UNKNOWN", "category": "niche", "reason": "brief why"},
  ...
}"""


def build_batch_prompt(subs_dict):
    parts = []
    for name, data in subs_dict.items():
        rules_str = ""
        if data.get("rules"):
            for r in data["rules"]:
                title = r.get("title", "")
                desc = (r.get("description", "") or "")[:250]
                rules_str += f"  - {title}: {desc}\n"
        else:
            rules_str = "  (no rules data available)\n"

        desc = (data.get("description", "") or "")[:400]
        subs_count = data.get("subscribers") or 0
        parts.append(f"r/{name} | {subs_count:,} subscribers\nDescription: {desc}\nRules:\n{rules_str}")

    return "EVALUATE THESE SUBREDDITS:\n\n" + "\n---\n".join(parts)


def call_grok(prompt, api_key):
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
                    "temperature": 0.1,
                },
                timeout=120,
            )
            if resp.status_code == 429:
                wait = RETRY_DELAY * (attempt + 1) * 2
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            # Strip markdown code fences if present
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            return json.loads(content)
        except json.JSONDecodeError as e:
            print(f"  JSON parse error attempt {attempt+1}: {e}")
            print(f"  Raw content: {content[:500]}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"  API error attempt {attempt+1}: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    return None


def main():
    if len(sys.argv) != 6:
        print("Usage: python tier_evaluator.py <merged_json> <start_idx> <end_idx> <output_file> <api_key>")
        sys.exit(1)

    merged_path = sys.argv[1]
    start_idx = int(sys.argv[2])
    end_idx = int(sys.argv[3])
    output_path = sys.argv[4]
    api_key = sys.argv[5]

    with open(merged_path, encoding="utf-8") as f:
        all_subs = json.load(f)

    keys = list(all_subs.keys())[start_idx:end_idx]
    chunk = {k: all_subs[k] for k in keys}
    total = len(chunk)

    print(f"Processing {total} subs (index {start_idx}-{end_idx})")

    results = {}
    processed = 0
    failed_batches = 0

    # Process in batches
    batch_keys = list(chunk.keys())
    for i in range(0, len(batch_keys), BATCH_SIZE):
        batch_slice = batch_keys[i : i + BATCH_SIZE]
        batch_data = {k: chunk[k] for k in batch_slice}

        prompt = build_batch_prompt(batch_data)
        result = call_grok(prompt, api_key)

        if result:
            # Normalize keys - Grok sometimes returns with r/ prefix
            for k, v in result.items():
                clean_key = k.replace("r/", "").lower()
                results[clean_key] = v
            processed += len(batch_slice)
        else:
            failed_batches += 1
            for k in batch_slice:
                results[k] = {"tier": "ERROR", "category": "unknown", "reason": "API call failed"}
            processed += len(batch_slice)

        pct = (processed / total) * 100
        print(f"  Progress: {processed}/{total} ({pct:.0f}%) | Failed batches: {failed_batches}")

        # Small delay between batches to avoid rate limits
        time.sleep(1)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nDone! {len(results)} results saved to {output_path}")
    print(f"Failed batches: {failed_batches}")


if __name__ == "__main__":
    main()
