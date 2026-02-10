"""
Post history tracking with SQLite.
Tracks what content was posted where, prevents duplicates, tracks bans.
"""
import os
import sqlite3
from datetime import datetime

DB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DB_PATH = os.path.join(DB_DIR, "post_history.db")


def _get_conn():
    """Get a SQLite connection, creating the database if needed."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_tables(conn)
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
    """)
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
