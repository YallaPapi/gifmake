"""
Karma tracking and daily report system for Reddit warmup accounts.

After warmup runs finish, this module:
1. Scrapes karma for Reddit accounts via public JSON API
2. Stores karma snapshots + comment logs in SQLite
3. Computes analytics (best subreddit+style combos, top performers)
4. Generates human-readable text report + JSON report

Uses the same SQLite DB as post_history.py (data/post_history.db).
"""
import os
import json
import time
import sqlite3
import logging
import requests
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)

# Paths relative to this module's location
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(_MODULE_DIR, "..", "..", "data")
DB_PATH = os.path.join(DB_DIR, "post_history.db")
REPORT_DIR = os.path.join(_MODULE_DIR, "..", "..", "data", "warmup_reports")
CONFIG_PATH = os.path.join(_MODULE_DIR, "..", "..", "config", "schedule_config.json")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
SCRAPE_DELAY = 6  # seconds between requests
RATE_LIMIT_SLEEP = 60  # seconds to sleep on 429
REQUEST_TIMEOUT = 15


# ---- Database setup ----

def _get_conn():
    """Get a SQLite connection, creating the database and tables if needed."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_karma_tables(conn)
    return conn


def _init_karma_tables(conn):
    """Create karma tracking tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS karma_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            date TEXT NOT NULL,
            comment_karma INTEGER DEFAULT 0,
            link_karma INTEGER DEFAULT 0,
            total_karma INTEGER DEFAULT 0,
            comments_today INTEGER DEFAULT 0,
            status TEXT DEFAULT 'healthy',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_karma_user_date
            ON karma_snapshots(username, date);

        CREATE TABLE IF NOT EXISTS comment_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            date TEXT NOT NULL,
            subreddit TEXT,
            comment_style TEXT,
            sentiment TEXT,
            comment_text TEXT,
            post_url TEXT,
            status TEXT DEFAULT 'verified',
            logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_comment_user_date
            ON comment_log(username, date);
    """)
    conn.commit()


# ---- Proxy loading ----

def load_proxy():
    """Load proxy from schedule_config.json.

    Reads the first available proxy group's http field and parses it
    into a requests-compatible proxy dict.

    Returns:
        dict like {"http": "http://user:pass@host:port", "https": "..."} or None
    """
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        groups = cfg.get("proxy_groups", {})
        for group_name, group_data in groups.items():
            http_str = group_data.get("http", "")
            if not http_str:
                continue
            raw = http_str.replace("http://", "").replace("https://", "")
            parts = raw.split(":")
            if len(parts) < 4:
                continue
            host, port, user, passwd = parts[0], parts[1], parts[2], parts[3]
            proxy_url = f"http://{user}:{passwd}@{host}:{port}"
            return {"http": proxy_url, "https": proxy_url}
    except Exception as e:
        logger.warning(f"Failed to load proxy from config: {e}")
    return None


# ---- Karma scraping ----

def scrape_karma(usernames, proxy=None):
    """Scrape karma for a list of Reddit usernames via public JSON API.

    Args:
        usernames: list of Reddit usernames
        proxy: optional requests proxy dict

    Returns:
        dict of {username: {comment_karma, link_karma, total_karma, status}}
    """
    results = {}
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    if proxy:
        session.proxies.update(proxy)

    for i, username in enumerate(usernames):
        if i > 0:
            time.sleep(SCRAPE_DELAY)

        # Log progress every 10 accounts
        if i > 0 and i % 10 == 0:
            logger.info(f"Karma scrape progress: {i}/{len(usernames)}")

        url = f"https://www.reddit.com/user/{username}/about.json"
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)

            if resp.status_code == 200:
                data = resp.json().get("data", {})
                comment_karma = data.get("comment_karma", 0)
                link_karma = data.get("link_karma", 0)
                is_suspended = data.get("is_suspended", False)

                if is_suspended:
                    results[username] = {
                        "comment_karma": comment_karma,
                        "link_karma": link_karma,
                        "total_karma": comment_karma + link_karma,
                        "status": "suspended",
                    }
                else:
                    results[username] = {
                        "comment_karma": comment_karma,
                        "link_karma": link_karma,
                        "total_karma": comment_karma + link_karma,
                        "status": "healthy",
                    }

            elif resp.status_code == 404:
                results[username] = {
                    "comment_karma": 0, "link_karma": 0,
                    "total_karma": 0, "status": "shadowbanned",
                }

            elif resp.status_code == 403:
                results[username] = {
                    "comment_karma": 0, "link_karma": 0,
                    "total_karma": 0, "status": "suspended",
                }

            elif resp.status_code == 429:
                logger.warning(f"Rate limited on {username}, sleeping {RATE_LIMIT_SLEEP}s...")
                time.sleep(RATE_LIMIT_SLEEP)
                # Retry once
                try:
                    resp2 = session.get(url, timeout=REQUEST_TIMEOUT)
                    if resp2.status_code == 200:
                        data = resp2.json().get("data", {})
                        comment_karma = data.get("comment_karma", 0)
                        link_karma = data.get("link_karma", 0)
                        results[username] = {
                            "comment_karma": comment_karma,
                            "link_karma": link_karma,
                            "total_karma": comment_karma + link_karma,
                            "status": "healthy",
                        }
                    else:
                        results[username] = {
                            "comment_karma": 0, "link_karma": 0,
                            "total_karma": 0, "status": "error",
                        }
                except Exception:
                    results[username] = {
                        "comment_karma": 0, "link_karma": 0,
                        "total_karma": 0, "status": "error",
                    }
            else:
                logger.warning(f"Unexpected status {resp.status_code} for {username}")
                results[username] = {
                    "comment_karma": 0, "link_karma": 0,
                    "total_karma": 0, "status": "error",
                }

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout scraping {username}")
            results[username] = {
                "comment_karma": 0, "link_karma": 0,
                "total_karma": 0, "status": "error",
            }
        except Exception as e:
            logger.warning(f"Error scraping {username}: {e}")
            results[username] = {
                "comment_karma": 0, "link_karma": 0,
                "total_karma": 0, "status": "error",
            }

    logger.info(f"Karma scrape complete: {len(results)}/{len(usernames)} accounts")
    return results


# ---- Database operations ----

def save_karma_snapshots(karma_data, comment_counts, date_str):
    """Save karma snapshots to the database.

    Args:
        karma_data: dict from scrape_karma() {username: {comment_karma, ...}}
        comment_counts: dict {username: int} of comments logged today
        date_str: date string like "2026-02-18"
    """
    conn = _get_conn()
    try:
        for username, kd in karma_data.items():
            comments_today = comment_counts.get(username, 0)
            conn.execute(
                """INSERT OR REPLACE INTO karma_snapshots
                   (username, date, comment_karma, link_karma, total_karma,
                    comments_today, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (username, date_str, kd["comment_karma"], kd["link_karma"],
                 kd["total_karma"], comments_today, kd["status"])
            )
        conn.commit()
        logger.info(f"Saved {len(karma_data)} karma snapshots for {date_str}")
    except Exception as e:
        logger.error(f"Failed to save karma snapshots: {e}")
    finally:
        conn.close()


def get_previous_snapshot(username, before_date):
    """Get the most recent karma snapshot for a user before the given date.

    Returns:
        dict with karma fields or None
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            """SELECT * FROM karma_snapshots
               WHERE username = ? AND date < ?
               ORDER BY date DESC LIMIT 1""",
            (username, before_date)
        ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Failed to get previous snapshot for {username}: {e}")
        return None
    finally:
        conn.close()


def ingest_comment_logs(all_results, date_str):
    """Extract verified comments from warmup results and insert into comment_log.

    Args:
        all_results: list of result dicts from run_all_warmup.py
        date_str: date string like "2026-02-18"

    Returns:
        dict of {username: comment_count}
    """
    conn = _get_conn()
    comment_counts = {}
    total_inserted = 0
    try:
        for result in all_results:
            username = result.get("profile", "")
            if not username:
                continue
            stats = result.get("stats")
            if not stats:
                continue
            action_log = stats.get("action_log", [])
            count = 0
            for action in action_log:
                action_type = action.get("type", "")
                if action_type not in ("comment", "reply"):
                    continue
                if action.get("status") != "verified":
                    continue
                conn.execute(
                    """INSERT INTO comment_log
                       (username, date, subreddit, comment_style, sentiment,
                        comment_text, post_url, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (username, date_str, action.get("sub", ""),
                     action.get("style", ""), action.get("sentiment", ""),
                     action.get("text", "")[:500], action.get("url", ""),
                     action.get("status", "verified"))
                )
                count += 1
                total_inserted += 1
            if count > 0:
                comment_counts[username] = count

        conn.commit()
        logger.info(f"Ingested {total_inserted} comments for {len(comment_counts)} accounts")
    except Exception as e:
        logger.error(f"Failed to ingest comment logs: {e}")
    finally:
        conn.close()

    return comment_counts


# ---- Analytics ----

def compute_analytics(karma_data, comment_counts, date_str):
    """Compute all analytics from karma data and comment logs.

    Returns:
        dict with keys: top_performers, best_combos, avg_karma_per_comment,
                        nsfw_ready_count, projections
    """
    analytics = {
        "top_performers": [],
        "best_combos": [],
        "avg_karma_per_comment": 0.0,
        "nsfw_ready_count": 0,
        "projections": {},
    }

    # --- Top performers: accounts with highest karma change today ---
    performers = []
    for username, kd in karma_data.items():
        if kd["status"] not in ("healthy",):
            continue
        prev = get_previous_snapshot(username, date_str)
        if prev:
            change = kd["total_karma"] - prev["total_karma"]
        else:
            change = None  # new account, no previous data

        # Gather subreddits this user commented in today
        user_subs = _get_user_comment_subs(username, date_str)

        performers.append({
            "username": username,
            "total_karma": kd["total_karma"],
            "comment_karma": kd["comment_karma"],
            "link_karma": kd["link_karma"],
            "change": change,
            "comments_today": comment_counts.get(username, 0),
            "subreddits": user_subs,
        })

    # Sort by change descending (None values last)
    performers.sort(key=lambda x: (x["change"] is not None, x["change"] or 0), reverse=True)
    analytics["top_performers"] = performers[:10]

    # --- Best subreddit + style combos ---
    analytics["best_combos"] = _compute_best_combos(karma_data, comment_counts, date_str)

    # --- Average karma per comment (global) ---
    total_karma_change = 0
    total_comments = 0
    for username, kd in karma_data.items():
        if kd["status"] != "healthy":
            continue
        prev = get_previous_snapshot(username, date_str)
        if prev:
            change = kd["total_karma"] - prev["total_karma"]
            total_karma_change += change
        comments = comment_counts.get(username, 0)
        total_comments += comments

    if total_comments > 0:
        analytics["avg_karma_per_comment"] = round(total_karma_change / total_comments, 2)

    # --- Accounts ready for NSFW (>= 1000 total karma) ---
    nsfw_ready = sum(
        1 for kd in karma_data.values()
        if kd["status"] == "healthy" and kd["total_karma"] >= 1000
    )
    analytics["nsfw_ready_count"] = nsfw_ready

    # --- Days to 1000 projection ---
    projections = {}
    for username, kd in karma_data.items():
        if kd["status"] != "healthy":
            continue
        if kd["total_karma"] >= 1000:
            projections[username] = 0  # already there
            continue
        avg_daily = _get_avg_daily_growth(username, date_str)
        if avg_daily and avg_daily > 0:
            remaining = 1000 - kd["total_karma"]
            projections[username] = round(remaining / avg_daily, 1)
        else:
            projections[username] = None  # not enough data
    analytics["projections"] = projections

    return analytics


def _get_user_comment_subs(username, date_str):
    """Get list of subreddits a user commented in on a given date."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT subreddit FROM comment_log WHERE username = ? AND date = ?",
            (username, date_str)
        ).fetchall()
        return [row["subreddit"] for row in rows if row["subreddit"]]
    except Exception:
        return []
    finally:
        conn.close()


def _compute_best_combos(karma_data, comment_counts, date_str):
    """Compute best (subreddit, comment_style) combos ranked by avg karma/comment.

    Attribution: for each account, compute karma_change / comments_today as that
    account's avg karma per comment. Then attribute that avg equally to each
    (subreddit, style) combo the account used.
    """
    conn = _get_conn()
    try:
        # Build per-account avg karma per comment
        account_avg = {}
        for username, kd in karma_data.items():
            if kd["status"] != "healthy":
                continue
            comments = comment_counts.get(username, 0)
            if comments == 0:
                continue
            prev = get_previous_snapshot(username, date_str)
            if prev:
                change = kd["total_karma"] - prev["total_karma"]
            else:
                change = kd["total_karma"]  # assume all karma is new
            account_avg[username] = change / comments

        # Collect (sub, style) combos per account from comment_log
        combo_scores = defaultdict(lambda: {"total_avg": 0.0, "count": 0})
        for username, avg in account_avg.items():
            rows = conn.execute(
                """SELECT subreddit, comment_style, COUNT(*) as cnt
                   FROM comment_log
                   WHERE username = ? AND date = ?
                   GROUP BY subreddit, comment_style""",
                (username, date_str)
            ).fetchall()
            for row in rows:
                sub = row["subreddit"] or "unknown"
                style = row["comment_style"] or "unknown"
                key = (sub, style)
                combo_scores[key]["total_avg"] += avg
                combo_scores[key]["count"] += row["cnt"]

        # Rank by average karma per comment attributed to each combo
        ranked = []
        for (sub, style), data in combo_scores.items():
            if data["count"] == 0:
                continue
            # total_avg is sum of per-account averages that used this combo
            # Normalize by number of accounts that contributed
            # But simpler: total_avg / number of contributors
            # Actually per spec: "Attribute that avg equally to each combo"
            # So the combo's score = average of all account_avgs that used it
            contributors = 0
            for username, avg in account_avg.items():
                user_used = conn.execute(
                    """SELECT 1 FROM comment_log
                       WHERE username = ? AND date = ? AND subreddit = ?
                       AND comment_style = ? LIMIT 1""",
                    (username, date_str, sub, style)
                ).fetchone()
                if user_used:
                    contributors += 1
            if contributors > 0:
                avg_score = data["total_avg"] / contributors
            else:
                avg_score = 0.0
            ranked.append({
                "subreddit": sub,
                "style": style,
                "avg_karma_per_comment": round(avg_score, 1),
                "comment_count": data["count"],
            })

        ranked.sort(key=lambda x: x["avg_karma_per_comment"], reverse=True)
        return ranked[:10]

    except Exception as e:
        logger.error(f"Failed to compute best combos: {e}")
        return []
    finally:
        conn.close()


def _get_avg_daily_growth(username, current_date):
    """Compute average daily karma growth from historical snapshots.

    Returns average daily growth or None if insufficient data.
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT date, total_karma FROM karma_snapshots
               WHERE username = ? AND date <= ?
               ORDER BY date ASC""",
            (username, current_date)
        ).fetchall()
        if len(rows) < 2:
            return None

        first = rows[0]
        last = rows[-1]
        try:
            d1 = datetime.strptime(first["date"], "%Y-%m-%d")
            d2 = datetime.strptime(last["date"], "%Y-%m-%d")
            days = (d2 - d1).days
        except Exception:
            return None

        if days <= 0:
            return None

        karma_diff = last["total_karma"] - first["total_karma"]
        return karma_diff / days
    except Exception:
        return None
    finally:
        conn.close()


# ---- Report generation ----

def _classify_accounts(all_results, ban_log, karma_data):
    """Classify accounts into good, bad, and skipped categories.

    Returns:
        (good_list, bad_list, skipped_list) where each entry is a dict with
        username, total_karma, change, comments_today, status fields.
    """
    good = []
    bad = []
    skipped = []

    # Track all usernames we've processed
    seen = set()

    for result in all_results:
        username = result.get("profile", "")
        if not username or username in seen:
            continue
        seen.add(username)

        result_status = result.get("status", "")
        kd = karma_data.get(username)

        if result_status in ("success",) or (kd and kd.get("status") == "healthy"):
            # Good account
            entry = {
                "username": username,
                "total_karma": kd["total_karma"] if kd else 0,
                "comment_karma": kd["comment_karma"] if kd else 0,
                "link_karma": kd["link_karma"] if kd else 0,
                "change": None,
                "comments_today": 0,
                "status": "healthy",
            }
            good.append(entry)
        elif result_status in ("banned", "shadowbanned", "skip_banned"):
            entry = {
                "username": username,
                "total_karma": kd["total_karma"] if kd else 0,
                "status": _get_bad_status(result_status, kd),
            }
            bad.append(entry)
        elif result_status in ("not_logged_in", "browser_crashed", "error", "failed"):
            entry = {
                "username": username,
                "total_karma": kd["total_karma"] if kd else 0,
                "status": result_status,
            }
            skipped.append(entry)

    # Also add ban_log entries not already covered
    for adspower_id, ban_info in ban_log.items():
        username = ban_info.get("username", "")
        if not username or username in seen:
            continue
        seen.add(username)
        kd = karma_data.get(username)
        ban_status = ban_info.get("status", "unknown")
        if ban_status in ("permaban", "shadowban"):
            bad.append({
                "username": username,
                "total_karma": kd["total_karma"] if kd else 0,
                "status": "permabanned" if ban_status == "permaban" else "shadowbanned",
            })

    return good, bad, skipped


def _get_bad_status(result_status, kd):
    """Map result status to display status for bad accounts."""
    if result_status == "banned" or result_status == "skip_banned":
        return "permabanned"
    if result_status == "shadowbanned":
        return "shadowbanned"
    if kd:
        if kd.get("status") == "suspended":
            return "suspended"
        if kd.get("status") == "shadowbanned":
            return "shadowbanned"
    return "banned"


def generate_text_report(karma_data, comment_counts, analytics, all_results,
                         ban_log, date_str):
    """Generate human-readable text report.

    Returns:
        str: the full report text
    """
    good, bad, skipped = _classify_accounts(all_results, ban_log, karma_data)

    # Enrich good accounts with karma change and comment count
    for entry in good:
        username = entry["username"]
        prev = get_previous_snapshot(username, date_str)
        if prev:
            entry["change"] = karma_data[username]["total_karma"] - prev["total_karma"]
        else:
            entry["change"] = None  # new
        entry["comments_today"] = comment_counts.get(username, 0)

    # Sort good accounts by total karma descending
    good.sort(key=lambda x: x.get("total_karma", 0), reverse=True)

    total_accounts = len(good) + len(bad) + len(skipped)
    healthy_count = len(good)
    total_healthy = sum(1 for kd in karma_data.values() if kd.get("status") == "healthy")

    lines = []
    lines.append(f"=== WARMUP DAILY REPORT -- {date_str} ===")
    lines.append(
        f"Accounts: {total_accounts} total | {len(good)} good | "
        f"{len(bad)} bad | {len(skipped)} skipped"
    )

    avg_kpc = analytics.get("avg_karma_per_comment", 0.0)
    nsfw_ready = analytics.get("nsfw_ready_count", 0)
    lines.append(
        f"Avg karma/comment: {avg_kpc} | "
        f"Ready for NSFW (>=1000): {nsfw_ready}/{total_healthy}"
    )
    lines.append("")

    # --- Good accounts table ---
    lines.append(f"GOOD ACCOUNTS ({len(good)}):")
    lines.append(f"{'Username':<22}| {'Karma':>5} | {'Change':>6} | {'Comments':>8} | Status")
    lines.append(f"{'-'*22}|{'-'*7}|{'-'*8}|{'-'*10}|{'-'*10}")
    for entry in good:
        username = entry["username"]
        karma = entry.get("total_karma", 0)
        change = entry.get("change")
        comments = entry.get("comments_today", 0)
        status = entry.get("status", "healthy")

        if change is None:
            change_str = "new"
        elif change >= 0:
            change_str = f"+{change}"
        else:
            change_str = str(change)

        lines.append(
            f"{username:<22}| {karma:>5} | {change_str:>6} | {comments:>8} | {status}"
        )

    lines.append("")

    # --- Bad accounts table ---
    if bad:
        lines.append(f"BAD ACCOUNTS ({len(bad)}):")
        lines.append(f"{'Username':<22}| {'Karma':>5} | Status")
        lines.append(f"{'-'*22}|{'-'*7}|{'-'*15}")
        for entry in bad:
            username = entry["username"]
            karma = entry.get("total_karma", 0)
            status = entry.get("status", "banned")
            lines.append(f"{username:<22}| {karma:>5} | {status}")
        lines.append("")

    # --- Skipped accounts ---
    if skipped:
        lines.append(f"SKIPPED ACCOUNTS ({len(skipped)}):")
        for entry in skipped:
            lines.append(f"  {entry['username']}: {entry.get('status', 'unknown')}")
        lines.append("")

    # --- Top performers ---
    top = analytics.get("top_performers", [])
    if top:
        lines.append(f"TOP PERFORMERS (top {len(top)}):")
        for i, p in enumerate(top, 1):
            change = p.get("change")
            if change is None:
                change_str = "new"
            elif change >= 0:
                change_str = f"+{change}"
            else:
                change_str = str(change)

            subs = p.get("subreddits", [])
            subs_str = ", ".join(f"r/{s}" for s in subs[:5])
            if len(subs) > 5:
                subs_str += f" +{len(subs)-5} more"
            comments = p.get("comments_today", 0)
            lines.append(
                f"{i}. {p['username']}: {change_str} karma "
                f"({comments} comments in {subs_str})"
            )
        lines.append("")

    # --- Best subreddit + style combos ---
    combos = analytics.get("best_combos", [])
    if combos:
        lines.append(f"BEST SUBREDDIT + STYLE COMBOS (top {len(combos)}):")
        for c in combos:
            lines.append(
                f"r/{c['subreddit']} + {c['style']}: "
                f"+{c['avg_karma_per_comment']} avg karma/comment "
                f"({c['comment_count']} comments)"
            )
        lines.append("")

    # --- Comment log ---
    comments_today = _get_all_comments_today(date_str)
    if comments_today:
        lines.append(f"COMMENT LOG ({len(comments_today)} comments today):")
        for c in comments_today:
            text_preview = c.get("comment_text", "")
            if len(text_preview) > 60:
                text_preview = text_preview[:57] + "..."
            lines.append(
                f"{c['username']} | r/{c.get('subreddit', '?')} | "
                f"{c.get('comment_style', '?')} | "
                f"\"{text_preview}\" | {c.get('post_url', '')}"
            )
        lines.append("")

    # --- Days to 1000 projections ---
    projections = analytics.get("projections", {})
    accounts_with_projections = [
        (u, d) for u, d in projections.items()
        if d is not None and d > 0
    ]
    if accounts_with_projections:
        accounts_with_projections.sort(key=lambda x: x[1])
        lines.append("DAYS TO 1000 KARMA PROJECTIONS:")
        for username, days in accounts_with_projections[:15]:
            kd = karma_data.get(username, {})
            current = kd.get("total_karma", 0)
            lines.append(f"  {username}: ~{days:.0f} days (currently {current})")
        lines.append("")

    return "\n".join(lines)


def _get_all_comments_today(date_str):
    """Get all comment_log entries for today."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT username, subreddit, comment_style, sentiment,
                      comment_text, post_url
               FROM comment_log WHERE date = ?
               ORDER BY logged_at ASC""",
            (date_str,)
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []
    finally:
        conn.close()


def generate_json_report(karma_data, comment_counts, analytics, all_results,
                         ban_log, date_str):
    """Generate structured JSON report.

    Returns:
        dict: the full report data
    """
    good, bad, skipped = _classify_accounts(all_results, ban_log, karma_data)

    # Enrich good accounts
    for entry in good:
        username = entry["username"]
        prev = get_previous_snapshot(username, date_str)
        if prev:
            entry["change"] = karma_data[username]["total_karma"] - prev["total_karma"]
        else:
            entry["change"] = None
        entry["comments_today"] = comment_counts.get(username, 0)

    good.sort(key=lambda x: x.get("total_karma", 0), reverse=True)

    comments_today = _get_all_comments_today(date_str)

    report = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_accounts": len(good) + len(bad) + len(skipped),
            "good_count": len(good),
            "bad_count": len(bad),
            "skipped_count": len(skipped),
            "avg_karma_per_comment": analytics.get("avg_karma_per_comment", 0.0),
            "nsfw_ready_count": analytics.get("nsfw_ready_count", 0),
        },
        "good_accounts": good,
        "bad_accounts": bad,
        "skipped_accounts": skipped,
        "top_performers": analytics.get("top_performers", []),
        "best_combos": analytics.get("best_combos", []),
        "projections": analytics.get("projections", {}),
        "comment_log": comments_today,
        "karma_data": {
            username: {
                "comment_karma": kd["comment_karma"],
                "link_karma": kd["link_karma"],
                "total_karma": kd["total_karma"],
                "status": kd["status"],
            }
            for username, kd in karma_data.items()
        },
    }

    return report


def save_reports(text_report, json_report, date_str):
    """Save text and JSON reports to disk.

    Returns:
        tuple: (text_report_path, json_report_path)
    """
    os.makedirs(REPORT_DIR, exist_ok=True)

    text_path = os.path.join(REPORT_DIR, f"karma_report_{date_str.replace('-', '')}.txt")
    json_path = os.path.join(REPORT_DIR, f"karma_report_{date_str.replace('-', '')}.json")

    with open(text_path, "w", encoding="utf-8") as f:
        f.write(text_report)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_report, f, indent=2, default=str)

    logger.info(f"Text report saved: {text_path}")
    logger.info(f"JSON report saved: {json_path}")

    return text_path, json_path


# ---- Main entry point ----

def run_karma_report(all_results, ban_log):
    """Run full karma tracking pipeline. Returns path to text report.

    Called from run_all_warmup.py after all warmups complete.

    Args:
        all_results: list of result dicts from run_all_warmup.py
        ban_log: dict of {adspower_id: {status, username, ...}} from ban tracking

    Returns:
        str: path to the generated text report file
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Starting karma report for {date_str}")

    # Step 1: Collect all usernames
    usernames = set()
    for result in all_results:
        username = result.get("profile", "")
        if username:
            usernames.add(username)
    for adspower_id, ban_info in ban_log.items():
        username = ban_info.get("username", "")
        if username:
            usernames.add(username)

    usernames = sorted(usernames)
    logger.info(f"Collected {len(usernames)} unique usernames")

    # Step 2: Load proxy and scrape karma
    proxy = load_proxy()
    if proxy:
        logger.info("Using proxy for karma scraping")
    else:
        logger.warning("No proxy configured, scraping without proxy")

    karma_data = scrape_karma(usernames, proxy=proxy)

    # Override status for accounts known to be banned from ban_log
    for adspower_id, ban_info in ban_log.items():
        username = ban_info.get("username", "")
        ban_status = ban_info.get("status", "")
        if username and username in karma_data:
            if ban_status == "permaban":
                karma_data[username]["status"] = "suspended"
            elif ban_status == "shadowban":
                karma_data[username]["status"] = "shadowbanned"

    # Step 3: Ingest comment logs from action_logs
    comment_counts = ingest_comment_logs(all_results, date_str)
    logger.info(f"Comment counts: {comment_counts}")

    # Step 4: Save karma snapshots
    save_karma_snapshots(karma_data, comment_counts, date_str)

    # Step 5: Compute analytics
    analytics = compute_analytics(karma_data, comment_counts, date_str)
    logger.info(
        f"Analytics: avg_kpc={analytics['avg_karma_per_comment']}, "
        f"nsfw_ready={analytics['nsfw_ready_count']}, "
        f"top_performers={len(analytics['top_performers'])}"
    )

    # Step 6: Generate reports
    text_report = generate_text_report(
        karma_data, comment_counts, analytics, all_results, ban_log, date_str
    )
    json_report = generate_json_report(
        karma_data, comment_counts, analytics, all_results, ban_log, date_str
    )

    # Step 7: Save reports
    text_path, json_path = save_reports(text_report, json_report, date_str)

    # Step 8: Log the text report to console
    logger.info("")
    for line in text_report.split("\n"):
        logger.info(line)
    logger.info("")

    return text_path


# ---- Standalone usage ----

if __name__ == "__main__":
    """Standalone mode: scrape karma for all known accounts and generate report.

    Useful for running independently of the warmup pipeline.
    Reads previous results from the most recent run JSON if available.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

    # Try to load the most recent run results
    all_results = []
    ban_log = {}

    # Load ban log
    ban_log_path = os.path.join(_MODULE_DIR, "..", "..", "data", "warmup_bans.json")
    if os.path.exists(ban_log_path):
        try:
            with open(ban_log_path, "r") as f:
                ban_log = json.load(f)
            logger.info(f"Loaded ban log with {len(ban_log)} entries")
        except Exception as e:
            logger.warning(f"Failed to load ban log: {e}")

    # Try to load most recent run report for all_results
    if os.path.exists(REPORT_DIR):
        run_files = sorted(
            [f for f in os.listdir(REPORT_DIR) if f.startswith("run_") and f.endswith(".json")],
            reverse=True
        )
        if run_files:
            latest_run = os.path.join(REPORT_DIR, run_files[0])
            try:
                with open(latest_run, "r") as f:
                    run_data = json.load(f)
                all_results = run_data.get("results", [])
                logger.info(f"Loaded {len(all_results)} results from {latest_run}")
            except Exception as e:
                logger.warning(f"Failed to load run results: {e}")

    report_path = run_karma_report(all_results, ban_log)
    print(f"\nReport saved to: {report_path}")
