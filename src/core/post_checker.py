"""
Reddit post performance checker.
Fetches scores, upvote ratios, comment counts, and removal status
for previously posted content using Reddit's public JSON API.
"""
import logging
import random
import re
import time

import requests

from core.post_history import get_unchecked_posts, update_post_metrics

logger = logging.getLogger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
]

_POST_ID_RE = re.compile(r'/comments/([a-z0-9]+)')

BATCH_SIZE = 100  # Reddit's max IDs per /api/info request
REQUEST_DELAY = 6  # seconds between requests (~10/min Reddit limit)


def _build_proxy_dict(proxy_string):
    """Convert 'host:port:user:pass' to requests proxy dict."""
    if not proxy_string:
        return None
    parts = proxy_string.split(":")
    if len(parts) != 4:
        return None
    host, port, user, passwd = parts
    url = f"http://{user}:{passwd}@{host}:{port}"
    return {"http": url, "https": url}


def _extract_post_id(post_url):
    """Extract Reddit post ID from a permalink URL."""
    m = _POST_ID_RE.search(post_url or "")
    return m.group(1) if m else None


def check_posts_batch(post_urls, proxy=None):
    """Check scores for a batch of post URLs via Reddit's /api/info endpoint.

    Args:
        post_urls: List of Reddit post URLs
        proxy: Optional proxy string 'host:port:user:pass'

    Returns:
        Dict mapping post_url -> {score, upvote_ratio, num_comments,
                                   is_removed, removed_reason}
    """
    # Map post IDs back to their URLs
    id_to_url = {}
    for url in post_urls:
        pid = _extract_post_id(url)
        if pid:
            id_to_url[f"t3_{pid}"] = url

    if not id_to_url:
        return {}

    proxies = _build_proxy_dict(proxy)
    results = {}
    ids_list = list(id_to_url.keys())

    for i in range(0, len(ids_list), BATCH_SIZE):
        batch_ids = ids_list[i:i + BATCH_SIZE]
        id_param = ",".join(batch_ids)

        try:
            resp = requests.get(
                "https://www.reddit.com/api/info.json",
                params={"id": id_param},
                headers={"User-Agent": random.choice(_USER_AGENTS)},
                proxies=proxies,
                timeout=30,
            )
            if resp.status_code == 429:
                logger.warning("Rate limited by Reddit, waiting 60s...")
                time.sleep(60)
                continue
            resp.raise_for_status()
            data = resp.json().get("data", {})

            for child in data.get("children", []):
                post = child.get("data", {})
                fullname = f"t3_{post.get('id', '')}"
                url = id_to_url.get(fullname)
                if not url:
                    continue

                removed_reason = post.get("removed_by_category")
                is_removed = bool(removed_reason)

                # Additional removal signals
                if not is_removed and post.get("selftext") == "[removed]":
                    is_removed = True
                    removed_reason = "mod_removed"
                if not is_removed and post.get("score", 1) == 0 and post.get("upvote_ratio", 0.5) == 0:
                    is_removed = True
                    removed_reason = "shadow_removed"

                results[url] = {
                    "score": post.get("score", 0),
                    "upvote_ratio": post.get("upvote_ratio", 0),
                    "num_comments": post.get("num_comments", 0),
                    "is_removed": is_removed,
                    "removed_reason": removed_reason,
                }

            # Mark posts not found in response as deleted
            for fid in batch_ids:
                url = id_to_url[fid]
                if url not in results:
                    results[url] = {
                        "score": 0,
                        "upvote_ratio": 0,
                        "num_comments": 0,
                        "is_removed": True,
                        "removed_reason": "not_found",
                    }

        except requests.RequestException as e:
            logger.error(f"Reddit API error: {e}")

        # Rate limit delay between batches
        if i + BATCH_SIZE < len(ids_list):
            time.sleep(REQUEST_DELAY)

    return results


def run_check_cycle(profile_id=None, proxy=None, hours=72):
    """Run a full check cycle: fetch unchecked posts, get scores, update DB.

    Args:
        profile_id: Optional profile filter
        proxy: Optional proxy string 'host:port:user:pass'
        hours: How far back to check (default 72h)

    Returns:
        Dict: {checked, removed, avg_score} summary
    """
    posts = get_unchecked_posts(profile_id=profile_id, hours=hours)
    if not posts:
        logger.info("No posts to check")
        return {"checked": 0, "removed": 0, "avg_score": 0}

    urls = [p["post_url"] for p in posts if p.get("post_url")]
    if not urls:
        return {"checked": 0, "removed": 0, "avg_score": 0}

    logger.info(f"Checking {len(urls)} posts...")
    results = check_posts_batch(urls, proxy=proxy)

    # Update DB
    total_score = 0
    removed_count = 0
    checked = 0

    for url, metrics in results.items():
        update_post_metrics(
            post_url=url,
            score=metrics["score"],
            upvote_ratio=metrics["upvote_ratio"],
            num_comments=metrics["num_comments"],
            is_removed=metrics["is_removed"],
            removed_reason=metrics.get("removed_reason"),
        )
        total_score += metrics["score"]
        if metrics["is_removed"]:
            removed_count += 1
        checked += 1

    avg = round(total_score / checked, 1) if checked else 0
    summary = {"checked": checked, "removed": removed_count, "avg_score": avg}
    logger.info(
        f"Check complete: {checked} posts, avg score {avg}, "
        f"{removed_count} removed"
    )
    return summary
