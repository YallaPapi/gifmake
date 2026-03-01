"""
Post history tracking with SQLite.
Tracks what content was posted where, prevents duplicates, tracks bans.
"""
import os
import random
import sqlite3
import json
from datetime import datetime, timedelta

DB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DB_PATH = os.path.join(DB_DIR, "post_history.db")

# Daily caps per warmup phase — max totals across all sessions in one day.
# Each value is a (min, max) range; a random value is picked once per day per account.
DAILY_CAP_RANGES = {
    "phase1": {"comments": (4, 6),   "joins": (1, 2)},   # days 1-3
    "phase2": {"comments": (6, 10),  "joins": (2, 3)},   # days 4-7
    "phase3": {"comments": (8, 12),  "joins": (2, 4)},   # days 8-14
    "phase4": {"comments": (10, 15), "joins": (3, 4)},   # days 15-21
    "phase5": {"comments": (12, 18), "joins": (3, 5)},   # days 22+
}


def _get_conn():
    """Get a SQLite connection, creating the database if needed."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_tables(conn)
    _migrate_tables(conn)
    return conn


def _init_tables(conn):
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            content_file TEXT,
            subreddit TEXT NOT NULL,
            title TEXT,
            post_url TEXT,
            status TEXT DEFAULT 'pending',
            error TEXT,
            posted_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_content_sub
            ON posts(content_hash, subreddit);

        CREATE INDEX IF NOT EXISTS idx_profile
            ON posts(profile_id);

        CREATE TABLE IF NOT EXISTS banned_subs (
            profile_id TEXT NOT NULL,
            subreddit TEXT NOT NULL,
            reason TEXT,
            banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(profile_id, subreddit)
        );

        CREATE TABLE IF NOT EXISTS profile_stats (
            profile_id TEXT PRIMARY KEY,
            posts_today INTEGER DEFAULT 0,
            last_post_at TIMESTAMP,
            last_reset_date TEXT,
            status TEXT DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS account_warmup (
            profile_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            last_activity_date TEXT,
            total_posts INTEGER DEFAULT 0,
            total_comments INTEGER DEFAULT 0,
            total_upvotes INTEGER DEFAULT 0,
            total_joins INTEGER DEFAULT 0,
            status TEXT DEFAULT 'warming'
        );

        CREATE TABLE IF NOT EXISTS cqs_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            cqs_value TEXT,
            raw_response TEXT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_cqs_profile
            ON cqs_checks(profile_id, checked_at);

        CREATE TABLE IF NOT EXISTS warmup_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            date TEXT NOT NULL,
            comments INTEGER DEFAULT 0,
            upvotes INTEGER DEFAULT 0,
            downvotes INTEGER DEFAULT 0,
            joins INTEGER DEFAULT 0,
            posts_clicked INTEGER DEFAULT 0,
            subs_browsed INTEGER DEFAULT 0,
            sessions INTEGER DEFAULT 0,
            scrolls INTEGER DEFAULT 0,
            duration_sec INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_ws_profile_date
            ON warmup_sessions(profile_id, date);

        CREATE TABLE IF NOT EXISTS warmup_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            adspower_id TEXT,
            username TEXT,
            proxy_group TEXT,
            source TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            detail TEXT,
            stats_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_attempts_started
            ON warmup_attempts(started_at);

        CREATE INDEX IF NOT EXISTS idx_attempts_profile
            ON warmup_attempts(profile_id, started_at);

        CREATE TABLE IF NOT EXISTS karma_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            username TEXT NOT NULL,
            comment_karma INTEGER DEFAULT 0,
            link_karma INTEGER DEFAULT 0,
            total_karma INTEGER DEFAULT 0,
            account_age_days INTEGER DEFAULT 0,
            created_utc INTEGER DEFAULT 0,
            checked_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_karma_profile
            ON karma_checks(profile_id, checked_at);
    """)
    conn.commit()


def _migrate_tables(conn):
    """Add new columns to existing tables. Safe to call repeatedly."""
    migrations = [
        "ALTER TABLE posts ADD COLUMN score INTEGER",
        "ALTER TABLE posts ADD COLUMN upvote_ratio REAL",
        "ALTER TABLE posts ADD COLUMN num_comments INTEGER",
        "ALTER TABLE posts ADD COLUMN is_removed INTEGER DEFAULT 0",
        "ALTER TABLE posts ADD COLUMN removed_reason TEXT",
        "ALTER TABLE posts ADD COLUMN last_checked_at TIMESTAMP",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()


def add_post(profile_id, content_hash, subreddit, title, content_file=None,
             status="success", post_url=None, error=None):
    """Record a post attempt."""
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO posts
               (profile_id, content_hash, content_file, subreddit, title,
                post_url, status, error, posted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (profile_id, content_hash, content_file, subreddit, title,
             post_url, status, error, datetime.now().isoformat())
        )
        conn.commit()
    finally:
        conn.close()


def is_posted(content_hash, subreddit):
    """Check if content was already posted to a subreddit."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM posts WHERE content_hash=? AND subreddit=? AND status='success'",
            (content_hash, subreddit)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_posted_subs(content_hash):
    """Get all subs where this content was successfully posted."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT subreddit FROM posts WHERE content_hash=? AND status='success'",
            (content_hash,)
        ).fetchall()
        return {row["subreddit"] for row in rows}
    finally:
        conn.close()


def add_ban(profile_id, subreddit, reason="detected"):
    """Record that a profile is banned from a subreddit."""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO banned_subs (profile_id, subreddit, reason) VALUES (?, ?, ?)",
            (profile_id, subreddit, reason)
        )
        conn.commit()
    finally:
        conn.close()


def get_banned_subs(profile_id):
    """Get all subs this profile is banned from."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT subreddit FROM banned_subs WHERE profile_id=?",
            (profile_id,)
        ).fetchall()
        return {row["subreddit"] for row in rows}
    finally:
        conn.close()


def get_posts_today(profile_id):
    """Get how many successful posts this profile made today."""
    conn = _get_conn()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        row = conn.execute(
            """SELECT COUNT(*) as cnt FROM posts
               WHERE profile_id=? AND status='success'
               AND date(posted_at)=?""",
            (profile_id, today)
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def get_post_results(profile_id=None, limit=100):
    """Get recent post results, optionally filtered by profile."""
    conn = _get_conn()
    try:
        if profile_id:
            rows = conn.execute(
                """SELECT * FROM posts WHERE profile_id=?
                   ORDER BY posted_at DESC LIMIT ?""",
                (profile_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM posts ORDER BY posted_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def export_results_csv(output_path, profile_id=None):
    """Export post results to CSV."""
    import csv
    results = get_post_results(profile_id, limit=10000)
    if not results:
        return 0

    fieldnames = ["profile_id", "content_file", "subreddit", "title",
                  "post_url", "status", "error", "posted_at"]
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    return len(results)


# ---- Post performance tracking ----

def update_post_metrics(post_url, score, upvote_ratio, num_comments,
                        is_removed=False, removed_reason=None):
    """Update score/metrics for a post after checking Reddit."""
    conn = _get_conn()
    try:
        conn.execute(
            """UPDATE posts SET score=?, upvote_ratio=?, num_comments=?,
               is_removed=?, removed_reason=?, last_checked_at=?
               WHERE post_url=?""",
            (score, upvote_ratio, num_comments, int(is_removed),
             removed_reason, datetime.now().isoformat(), post_url)
        )
        conn.commit()
    finally:
        conn.close()


def get_unchecked_posts(profile_id=None, hours=72):
    """Get successful posts that need score checking.

    Returns posts that either haven't been checked yet or were posted
    within the last `hours` hours (scores keep changing).
    """
    conn = _get_conn()
    try:
        cutoff = datetime.now().timestamp() - (hours * 3600)
        cutoff_iso = datetime.fromtimestamp(cutoff).isoformat()

        if profile_id:
            rows = conn.execute(
                """SELECT id, post_url, subreddit, content_file, posted_at
                   FROM posts
                   WHERE status='success' AND post_url IS NOT NULL
                   AND profile_id=?
                   AND (last_checked_at IS NULL OR posted_at >= ?)
                   ORDER BY posted_at DESC""",
                (profile_id, cutoff_iso)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, post_url, subreddit, content_file, posted_at
                   FROM posts
                   WHERE status='success' AND post_url IS NOT NULL
                   AND (last_checked_at IS NULL OR posted_at >= ?)
                   ORDER BY posted_at DESC""",
                (cutoff_iso,)
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_sub_performance(profile_id=None):
    """Get performance stats grouped by subreddit.

    Returns list of dicts: {subreddit, post_count, avg_score, max_score,
                            removed_count, last_posted}
    """
    conn = _get_conn()
    try:
        where = "WHERE status='success' AND score IS NOT NULL"
        params = ()
        if profile_id:
            where += " AND profile_id=?"
            params = (profile_id,)

        rows = conn.execute(
            f"""SELECT subreddit,
                       COUNT(*) as post_count,
                       ROUND(AVG(score), 1) as avg_score,
                       MAX(score) as max_score,
                       SUM(is_removed) as removed_count,
                       MAX(posted_at) as last_posted
                FROM posts {where}
                GROUP BY subreddit
                ORDER BY avg_score DESC""",
            params
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_content_performance(profile_id=None):
    """Get performance stats grouped by content file.

    Returns list of dicts: {content_file, sub_count, avg_score, best_sub,
                            best_score}
    """
    conn = _get_conn()
    try:
        where = "WHERE status='success' AND score IS NOT NULL"
        params = ()
        if profile_id:
            where += " AND profile_id=?"
            params = (profile_id,)

        rows = conn.execute(
            f"""SELECT content_file,
                       COUNT(*) as sub_count,
                       ROUND(AVG(score), 1) as avg_score,
                       MAX(score) as best_score
                FROM posts {where}
                GROUP BY content_file
                ORDER BY avg_score DESC""",
            params
        ).fetchall()
        results = [dict(row) for row in rows]

        # Find best sub for each content file
        for r in results:
            best = conn.execute(
                f"""SELECT subreddit FROM posts
                    {where} AND content_file=?
                    ORDER BY score DESC LIMIT 1""",
                params + (r["content_file"],)
            ).fetchone()
            r["best_sub"] = best["subreddit"] if best else ""

        return results
    finally:
        conn.close()


def get_hot_subs(profile_id=None, min_posts=2, min_avg_score=5):
    """Get subs that consistently perform well.

    Returns subreddit names where avg score >= min_avg_score
    and post count >= min_posts.
    """
    conn = _get_conn()
    try:
        where = "WHERE status='success' AND score IS NOT NULL"
        params = ()
        if profile_id:
            where += " AND profile_id=?"
            params = (profile_id,)

        rows = conn.execute(
            f"""SELECT subreddit, AVG(score) as avg_score, COUNT(*) as cnt
                FROM posts {where}
                GROUP BY subreddit
                HAVING cnt >= ? AND avg_score >= ?
                ORDER BY avg_score DESC""",
            params + (min_posts, min_avg_score)
        ).fetchall()
        return [row["subreddit"] for row in rows]
    finally:
        conn.close()


# ---- Account warmup tracking ----

def init_warmup(profile_id):
    """Create or get warmup record for a profile. Returns day number."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT started_at FROM account_warmup WHERE profile_id=?",
            (profile_id,)
        ).fetchone()
        if row:
            started = datetime.fromisoformat(row["started_at"])
            return (datetime.now() - started).days + 1
        conn.execute(
            "INSERT INTO account_warmup (profile_id, started_at) VALUES (?, ?)",
            (profile_id, datetime.now().isoformat())
        )
        conn.commit()
        return 1
    finally:
        conn.close()


def get_warmup_day(profile_id):
    """Get the warmup day number (1-based). Returns 0 if not initialized."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT started_at FROM account_warmup WHERE profile_id=?",
            (profile_id,)
        ).fetchone()
        if not row:
            return 0
        started = datetime.fromisoformat(row["started_at"])
        return (datetime.now() - started).days + 1
    finally:
        conn.close()


def get_warmup_status(profile_id):
    """Get full warmup state for a profile."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM account_warmup WHERE profile_id=?",
            (profile_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_warmed_today_ids():
    """Return set of profile_ids that already completed warmup today."""
    conn = _get_conn()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT profile_id FROM account_warmup WHERE last_activity_date=?",
            (today,)
        ).fetchall()
        return {row["profile_id"] for row in rows}
    finally:
        conn.close()


def reset_today_warmup():
    """Clear today's warmup flags so all accounts can re-run.

    Sets last_activity_date to yesterday for any profile marked today.
    Preserves all counters and history.
    """
    conn = _get_conn()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        cur = conn.execute(
            "UPDATE account_warmup SET last_activity_date=? WHERE last_activity_date=?",
            (yesterday, today))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_daily_cap(day):
    """Return randomized daily cap dict for the given warmup day number.

    Returns {"comments": int, "joins": int}.
    """
    if day <= 3:
        ranges = DAILY_CAP_RANGES["phase1"]
    elif day <= 7:
        ranges = DAILY_CAP_RANGES["phase2"]
    elif day <= 14:
        ranges = DAILY_CAP_RANGES["phase3"]
    elif day <= 21:
        ranges = DAILY_CAP_RANGES["phase4"]
    else:
        ranges = DAILY_CAP_RANGES["phase5"]
    return {
        "comments": random.randint(*ranges["comments"]),
        "joins": random.randint(*ranges["joins"]),
    }


def get_daily_totals(profile_id):
    """Get today's cumulative comments and joins for one profile.

    Queries warmup_sessions table (append-only, one row per run).
    Returns {"comments": int, "joins": int, "run_count": int}.
    """
    conn = _get_conn()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        row = conn.execute(
            """SELECT COALESCE(SUM(comments), 0) as comments,
                      COALESCE(SUM(joins), 0) as joins,
                      COUNT(*) as run_count
               FROM warmup_sessions
               WHERE date=? AND profile_id=?""",
            (today, profile_id)
        ).fetchone()
        return dict(row) if row else {"comments": 0, "joins": 0, "run_count": 0}
    finally:
        conn.close()


def get_all_warmup_stats():
    """Get warmup stats for ALL profiles. Returns list of dicts."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT profile_id, started_at, last_activity_date,
                      total_posts, total_comments, total_upvotes, total_joins, status
               FROM account_warmup
               ORDER BY last_activity_date DESC, total_comments DESC"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def record_activity(profile_id, activity_type, count=1):
    """Increment activity counters. activity_type: posts, comments, upvotes, joins."""
    col_map = {
        "posts": "total_posts",
        "comments": "total_comments",
        "upvotes": "total_upvotes",
        "joins": "total_joins",
    }
    col = col_map.get(activity_type)
    if not col:
        return
    conn = _get_conn()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        conn.execute(
            f"UPDATE account_warmup SET {col} = {col} + ?, last_activity_date = ? WHERE profile_id = ?",
            (count, today, profile_id)
        )
        conn.commit()
    finally:
        conn.close()


def record_session(profile_id, started_at, finished_at, stats):
    """Write one warmup session row. Called once per run_daily_warmup()."""
    conn = _get_conn()
    try:
        date_str = finished_at[:10]  # YYYY-MM-DD from ISO string
        conn.execute(
            """INSERT INTO warmup_sessions
               (profile_id, started_at, finished_at, date,
                comments, upvotes, downvotes, joins,
                posts_clicked, subs_browsed, sessions, scrolls, duration_sec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (profile_id, started_at, finished_at, date_str,
             stats.get("comments", 0), stats.get("upvotes", 0),
             stats.get("downvotes", 0), stats.get("joins", 0),
             stats.get("posts_clicked", 0), stats.get("subs_browsed", 0),
             stats.get("sessions", 0), stats.get("scrolls", 0),
             stats.get("total_sec", 0))
        )
        conn.commit()
    finally:
        conn.close()


def record_warmup_attempt_start(profile_id, adspower_id=None, username=None,
                                proxy_group=None, source="run_all"):
    """Record the start of a warmup attempt.

    This is written before warmup execution so interrupted runs still leave
    traceable attempt rows.
    """
    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO warmup_attempts
               (profile_id, adspower_id, username, proxy_group, source,
                started_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (profile_id, adspower_id, username, proxy_group, source,
             datetime.now().isoformat(), "running")
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def record_warmup_attempt_finish(attempt_id, status, detail="", stats=None):
    """Finalize a warmup attempt row with terminal status and optional stats."""
    if attempt_id is None:
        return
    conn = _get_conn()
    try:
        stats_json = ""
        if stats is not None:
            try:
                stats_json = json.dumps(stats, ensure_ascii=False)
            except Exception:
                stats_json = json.dumps({"error": "stats_not_serializable"})
        conn.execute(
            """UPDATE warmup_attempts
               SET finished_at=?, status=?, detail=?, stats_json=?
               WHERE id=?""",
            (datetime.now().isoformat(), str(status or "unknown"),
             str(detail or "")[:1000], stats_json, int(attempt_id))
        )
        conn.commit()
    finally:
        conn.close()


def get_today_sessions(profile_id=None):
    """Get today's session totals, optionally filtered by profile_id.

    Returns list of dicts with profile_id and summed stats for today.
    """
    conn = _get_conn()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        if profile_id:
            rows = conn.execute(
                """SELECT profile_id,
                          SUM(comments) as comments, SUM(upvotes) as upvotes,
                          SUM(downvotes) as downvotes, SUM(joins) as joins,
                          SUM(posts_clicked) as posts_clicked,
                          SUM(duration_sec) as duration_sec,
                          COUNT(*) as run_count
                   FROM warmup_sessions
                   WHERE date=? AND profile_id=?
                   GROUP BY profile_id""",
                (today, profile_id)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT profile_id,
                          SUM(comments) as comments, SUM(upvotes) as upvotes,
                          SUM(downvotes) as downvotes, SUM(joins) as joins,
                          SUM(posts_clicked) as posts_clicked,
                          SUM(duration_sec) as duration_sec,
                          COUNT(*) as run_count
                   FROM warmup_sessions
                   WHERE date=?
                   GROUP BY profile_id""",
                (today,)
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def record_karma(profile_id, username, comment_karma, link_karma,
                  total_karma, account_age_days, created_utc):
    """Save a karma check result."""
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO karma_checks
               (profile_id, username, comment_karma, link_karma,
                total_karma, account_age_days, created_utc, checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (profile_id, username, comment_karma, link_karma,
             total_karma, account_age_days, created_utc,
             datetime.now().isoformat()))
        conn.commit()
    finally:
        conn.close()


def get_latest_karma(profile_id=None):
    """Get the most recent karma check for each profile (or one specific profile).

    Returns list of dicts with username, comment_karma, link_karma,
    total_karma, account_age_days, checked_at.
    """
    conn = _get_conn()
    try:
        if profile_id:
            rows = conn.execute(
                """SELECT profile_id, username, comment_karma, link_karma,
                          total_karma, account_age_days, checked_at
                   FROM karma_checks
                   WHERE profile_id = ?
                   ORDER BY checked_at DESC LIMIT 1""",
                (profile_id,)).fetchall()
        else:
            rows = conn.execute(
                """SELECT k.profile_id, k.username, k.comment_karma, k.link_karma,
                          k.total_karma, k.account_age_days, k.checked_at
                   FROM karma_checks k
                   INNER JOIN (
                       SELECT profile_id, MAX(checked_at) as max_checked
                       FROM karma_checks GROUP BY profile_id
                   ) latest ON k.profile_id = latest.profile_id
                              AND k.checked_at = latest.max_checked
                   ORDER BY k.total_karma DESC""").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def record_cqs(profile_id, cqs_value, raw_response=""):
    """Save a CQS check result."""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO cqs_checks (profile_id, cqs_value, raw_response) VALUES (?, ?, ?)",
            (profile_id, str(cqs_value), raw_response[:2000])
        )
        conn.commit()
    finally:
        conn.close()


def get_cqs_history(profile_id, limit=30):
    """Get recent CQS check results for a profile."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT cqs_value, checked_at FROM cqs_checks WHERE profile_id = ? ORDER BY checked_at DESC LIMIT ?",
            (profile_id, limit)
        ).fetchall()
        return [{"cqs": row[0], "checked_at": row[1]} for row in rows]
    finally:
        conn.close()


def get_latest_cqs(profile_id):
    """Get the most recent CQS value for a profile, or None."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT cqs_value, checked_at FROM cqs_checks WHERE profile_id = ? ORDER BY checked_at DESC LIMIT 1",
            (profile_id,)
        ).fetchone()
        return {"cqs": row[0], "checked_at": row[1]} if row else None
    finally:
        conn.close()
