"""
Generate rich content profiles for GREEN subreddits using Grok API.
Concurrent requests via ThreadPoolExecutor. Resumable.
Usage: python profile_subs.py
"""
import json
import time
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

SCRIPT_DIR = os.path.dirname(__file__)
TIERS_PATH = os.path.join(SCRIPT_DIR, "..", "subreddit_tiers_grok.json")
MERGED_PATH = os.path.join(SCRIPT_DIR, "..", "all_subs_merged.json")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "..", "subreddit_profiles.json")

BATCH_SIZE = 25
WORKERS = 10
GROK_MODEL = "grok-4-1-fast-reasoning"
GROK_URL = "https://api.x.ai/v1/chat/completions"
API_KEY = os.environ.get("GROK_API_KEY", "")
MAX_RETRIES = 3

lock = threading.Lock()

SYSTEM_PROMPT = """You are building a content matching database for NSFW subreddits. For each subreddit, analyze its name, description, and rules to determine EXACTLY what content belongs there.

Be SPECIFIC. Don't just say "general nsfw" — look at the name and description and figure out the niche. r/yogapants wants yoga pants. r/naughty_nurses_nsfw wants nurse outfits. r/airplanebathroom wants content in airplane bathrooms. USE THE NAME.

For each subreddit provide:

1. tags: Array of 3-8 specific content tags that describe what this sub wants. Be granular.
   Examples: ["nurse_outfit", "medical", "uniform", "cosplay"] or ["yoga_pants", "leggings", "athleisure", "clothed"]

2. body_type: What body types fit. Use: "any", "petite", "slim", "athletic", "fit", "average", "curvy", "thick", "bbw", "muscular" — or combine like "petite,slim"

3. ethnicity: Preference or "any". Use: "any", "white", "asian", "latina", "ebony", "indian", "mixed" — or specific if the sub targets it

4. setting: What setting/context. Examples: "any", "bedroom", "outdoor", "bathroom", "gym", "office", "public", "pool", "beach"

5. clothing: What clothing/state. Examples: "any", "nude", "lingerie", "bikini", "yoga_pants", "uniform", "dressed_to_nude", "partially_clothed"

6. action: What's happening. Examples: "any", "posing", "solo", "sex", "blowjob", "anal", "masturbation", "stripping", "twerking", "flashing"

7. theme: One sentence describing the ideal post for this sub.

8. format_preference: What format works best. "video", "photo", "gif", or "any"

Respond ONLY in valid JSON. No markdown, no backticks.
Format:
{
  "subreddit_name": {
    "tags": ["tag1", "tag2", ...],
    "body_type": "...",
    "ethnicity": "...",
    "setting": "...",
    "clothing": "...",
    "action": "...",
    "theme": "...",
    "format_preference": "..."
  },
  ...
}"""


def build_prompt(subs_dict, merged_data):
    parts = []
    for name in subs_dict:
        data = merged_data.get(name, {})
        rules_str = ""
        if data.get("rules"):
            for r in data["rules"]:
                title = r.get("title", "") or ""
                desc = (r.get("description", "") or "")[:200]
                rules_str += f"  - {title}: {desc}\n"
        else:
            rules_str = "  (no rules data)\n"
        desc = (data.get("description", "") or "")[:400]
        subs_count = data.get("subscribers") or 0
        parts.append(f"r/{name} | {subs_count:,} subscribers\nDescription: {desc}\nRules:\n{rules_str}")
    return "PROFILE THESE SUBREDDITS:\n\n" + "\n---\n".join(parts)


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
                time.sleep(10 * (attempt + 1))
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
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
    return None


def process_batch(batch_keys, merged_data):
    prompt = build_prompt(batch_keys, merged_data)
    result = call_grok(prompt)
    if result:
        cleaned = {}
        for k, v in result.items():
            cleaned[k.replace("r/", "").lower()] = v
        return cleaned, None
    else:
        return {k: {"tags": [], "theme": "FAILED", "body_type": "any", "ethnicity": "any", "setting": "any", "clothing": "any", "action": "any", "format_preference": "any"} for k in batch_keys}, "failed"


def main():
    if not API_KEY:
        print("ERROR: Set GROK_API_KEY environment variable", flush=True)
        return

    print("Loading data...", flush=True)
    with open(TIERS_PATH, encoding="utf-8") as f:
        tiers = json.load(f)
    with open(MERGED_PATH, encoding="utf-8") as f:
        merged = json.load(f)

    green_subs = [k for k, v in tiers.items() if v.get("tier") == "GREEN"]
    print(f"GREEN subs: {len(green_subs)}", flush=True)

    # Load existing progress
    profiles = {}
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            profiles = json.load(f)
        print(f"Resuming: {len(profiles)} already profiled", flush=True)

    remaining = [k for k in green_subs if k not in profiles]
    print(f"Remaining: {len(remaining)}", flush=True)

    if not remaining:
        print("All done!", flush=True)
        return

    batches = [remaining[i:i + BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
    print(f"Batches: {len(batches)} (x{BATCH_SIZE}) with {WORKERS} workers", flush=True)

    done = len(profiles)
    total = len(green_subs)
    failed = 0
    start_time = time.time()
    save_counter = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(process_batch, batch, merged): batch
            for batch in batches
        }

        for future in as_completed(futures):
            batch_results, error = future.result()

            with lock:
                profiles.update(batch_results)
                done += len(batch_results)
                if error:
                    failed += 1
                save_counter += 1

                if save_counter % 5 == 0:
                    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                        json.dump(profiles, f, indent=2, ensure_ascii=False)

                elapsed = time.time() - start_time
                rate = (done - (total - len(remaining))) / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                print(f"  [{done}/{total}] ({done*100/total:.1f}%) | Failed: {failed} | {rate:.1f}/s | ETA: {eta/60:.1f}min", flush=True)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2, ensure_ascii=False)

    print(f"\n=== DONE ===", flush=True)
    print(f"Total profiled: {len(profiles)}", flush=True)
    print(f"Failed batches: {failed}", flush=True)
    print(f"Saved to: {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
