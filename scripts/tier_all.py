"""
Tier ALL subreddits using Grok API.
Concurrent requests via ThreadPoolExecutor. Resumable.
Usage: python tier_all.py
"""
import json
import time
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

MERGED_PATH = os.path.join(os.path.dirname(__file__), "..", "all_subs_merged.json")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "subreddit_tiers_grok.json")

BATCH_SIZE = 30
WORKERS = 10
GROK_MODEL = "grok-3-mini"
GROK_URL = "https://api.x.ai/v1/chat/completions"
API_KEY = os.environ.get("GROK_API_KEY", "")
MAX_RETRIES = 3

lock = threading.Lock()

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


def build_prompt(subs_dict):
    parts = []
    for name, data in subs_dict.items():
        rules_str = ""
        if data.get("rules"):
            for r in data["rules"]:
                title = r.get("title", "") or ""
                desc = (r.get("description", "") or "")[:250]
                rules_str += f"  - {title}: {desc}\n"
        else:
            rules_str = "  (no rules data available)\n"
        desc = (data.get("description", "") or "")[:400]
        subs_count = data.get("subscribers") or 0
        parts.append(f"r/{name} | {subs_count:,} subscribers\nDescription: {desc}\nRules:\n{rules_str}")
    return "EVALUATE THESE SUBREDDITS:\n\n" + "\n---\n".join(parts)


def call_grok(prompt):
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                GROK_URL,
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
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
                wait = 10 * (attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            return json.loads(content.strip())
        except json.JSONDecodeError:
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
    return None


def process_batch(batch_idx, batch_keys, all_subs):
    batch_data = {k: all_subs[k] for k in batch_keys}
    prompt = build_prompt(batch_data)
    result = call_grok(prompt)
    if result:
        cleaned = {}
        for k, v in result.items():
            cleaned[k.replace("r/", "").lower()] = v
        return batch_idx, cleaned, None
    else:
        error_results = {k: {"tier": "ERROR", "category": "unknown", "reason": "API call failed"} for k in batch_keys}
        return batch_idx, error_results, "failed"


def main():
    print("Loading merged subreddits...", flush=True)
    with open(MERGED_PATH, encoding="utf-8") as f:
        all_subs = json.load(f)
    all_keys = list(all_subs.keys())
    total = len(all_keys)
    print(f"Total: {total} subreddits", flush=True)

    # Load existing progress
    results = {}
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            results = json.load(f)
        print(f"Resuming: {len(results)} already done", flush=True)

    done_keys = set(results.keys())
    remaining_keys = [k for k in all_keys if k not in done_keys]
    print(f"Remaining: {len(remaining_keys)}", flush=True)

    if not remaining_keys:
        print("All done!", flush=True)
        return

    # Split into batches
    batches = []
    for i in range(0, len(remaining_keys), BATCH_SIZE):
        batches.append(remaining_keys[i : i + BATCH_SIZE])

    print(f"Batches: {len(batches)} (x{BATCH_SIZE} subs) with {WORKERS} concurrent workers", flush=True)

    processed = len(done_keys)
    failed = 0
    start_time = time.time()
    save_counter = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(process_batch, idx, batch, all_subs): idx
            for idx, batch in enumerate(batches)
        }

        for future in as_completed(futures):
            batch_idx, batch_results, error = future.result()

            with lock:
                results.update(batch_results)
                processed += len(batch_results)
                if error:
                    failed += 1
                save_counter += 1

                # Save every 5 completed batches
                if save_counter % 5 == 0:
                    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                        json.dump(results, f, indent=2, ensure_ascii=False)

                elapsed = time.time() - start_time
                rate = (processed - len(done_keys)) / elapsed if elapsed > 0 else 0
                remaining = total - processed
                eta = remaining / rate if rate > 0 else 0
                print(f"  [{processed}/{total}] ({processed*100/total:.1f}%) | Failed: {failed} | {rate:.1f} subs/s | ETA: {eta/60:.1f}min", flush=True)

    # Final save
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    tiers = {}
    for v in results.values():
        t = v.get("tier", "ERROR")
        tiers[t] = tiers.get(t, 0) + 1

    print(f"\n=== DONE ===", flush=True)
    print(f"Total processed: {len(results)}", flush=True)
    for t, c in sorted(tiers.items()):
        print(f"  {t}: {c}", flush=True)
    print(f"Saved to: {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
