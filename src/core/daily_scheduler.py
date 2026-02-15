"""
Daily scheduler daemon for automated warmup and posting.

Generates a daily plan for each account based on warmup day/phase,
then executes warmup sessions (and eventually posts) at scheduled times.

Architecture:
  1. On startup (or midnight), generate a daily plan
  2. Plan distributes warmup sessions across the active window
  3. Check loop runs every 60s, launching due tasks in threads
  4. Proxy group exclusivity prevents concurrent same-proxy usage
  5. All activity logged to data/scheduler.db

Usage:
    python run_scheduler.py                 # Run daemon
    python run_scheduler.py --plan-only     # Show today's plan
    python run_scheduler.py --dry-run       # Plan + simulate
"""

import json
import os
import time
import random
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, date
from dataclasses import dataclass, field, asdict

from core.account_warmer import _get_session_plan, _get_daily_caps
from core.post_history import init_warmup, get_warmup_day

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────

BASE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROFILES_PATH = os.path.join(BASE_DIR, "config", "account_profiles.json")
API_KEYS_PATH = os.path.join(BASE_DIR, "config", "api_keys.json")
SCHEDULE_CONFIG_PATH = os.path.join(BASE_DIR, "config", "schedule_config.json")
SCHEDULE_DB_PATH = os.path.join(BASE_DIR, "data", "scheduler.db")
DAILY_PLAN_PATH = os.path.join(BASE_DIR, "data", "daily_plan.json")
ADSPOWER_CONFIG_PATH = os.path.join(
    BASE_DIR, "src", "uploaders", "redgifs", "adspower_config.json")
QUEUE_CONFIG_PATH = os.path.join(BASE_DIR, "config", "queue_config.json")


# ── Data classes ─────────────────────────────────────────────────

@dataclass
class ScheduledTask:
    """A single scheduled task for an account."""
    account_id: str          # persona key
    ads_id: str              # AdsPower profile ID
    display_name: str
    task_type: str           # "warmup" or "post"
    scheduled_time: str      # HH:MM
    duration_minutes: int    # session duration (warmup only)
    max_comments: int        # comment cap for this session
    proxy_group: str = ""
    status: str = "pending"  # pending | running | done | skipped | failed
    started_at: str = ""
    completed_at: str = ""
    stats: dict = field(default_factory=dict)
    db_id: int = 0           # schedule_log row id


def _get_max_posts(day, min_nsfw_days=14):
    """Posts/day ramp — mirrors AccountWarmer.get_max_posts_today()."""
    if day < min_nsfw_days:
        return 0
    elif day <= min_nsfw_days + 3:
        return 1
    elif day <= min_nsfw_days + 10:
        return random.choice([2, 3])
    else:
        t = min((day - min_nsfw_days - 10) / 20.0, 1.0)
        return 3 + int(round(t * 2))


# ── Scheduler ────────────────────────────────────────────────────

class DailyScheduler:
    """Generates and executes a daily warmup/posting plan."""

    def __init__(self, config_path=None):
        self.config = self._load_config(config_path or SCHEDULE_CONFIG_PATH)
        self.profiles = self._load_profiles()
        self.api_keys = self._load_api_keys()
        self.adspower_config = self._load_adspower_config()
        self.queue_config = self._load_queue_config()
        self.daily_plan: list[ScheduledTask] = []
        self.running = False
        self._plan_date = ""
        self._active_proxy_groups: dict[str, str] = {}  # proxy_group -> ads_id
        self._active_tasks: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._db = self._init_db()

    # ── Config loading ───────────────────────────────────────────

    def _load_config(self, path):
        defaults = {
            "enabled": True,
            "active_hours": {"start": "09:00", "end": "23:00"},
            "warmup_enabled": True,
            "posting_enabled": False,
            "accounts": "all",
            "check_interval_seconds": 60,
            "time_jitter_minutes": 15,
            "proxy_stagger_minutes": 5,
        }
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    defaults.update(json.load(f))
        except Exception as e:
            logger.warning(f"Config load error ({path}): {e}")
        return defaults

    def _load_profiles(self):
        with open(PROFILES_PATH, encoding="utf-8") as f:
            return json.load(f).get("profiles", {})

    def _load_api_keys(self):
        try:
            with open(API_KEYS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _load_adspower_config(self):
        try:
            if os.path.exists(ADSPOWER_CONFIG_PATH):
                with open(ADSPOWER_CONFIG_PATH, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"adspower_api_base": "http://localhost:50325", "api_key": ""}

    def _load_queue_config(self):
        try:
            if os.path.exists(QUEUE_CONFIG_PATH):
                with open(QUEUE_CONFIG_PATH, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    # ── Database ─────────────────────────────────────────────────

    def _init_db(self):
        os.makedirs(os.path.dirname(SCHEDULE_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(SCHEDULE_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schedule_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                account_id TEXT NOT NULL,
                ads_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                scheduled_time TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                status TEXT DEFAULT 'pending',
                duration_minutes INTEGER,
                max_comments INTEGER,
                stats_json TEXT,
                error TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_schedule_date
            ON schedule_log(date, account_id, status)
        """)
        conn.commit()
        return conn

    # ── Plan generation ──────────────────────────────────────────

    def _get_enabled_accounts(self):
        accts = self.config.get("accounts", "all")
        if accts == "all":
            return list(self.profiles.items())
        return [(k, self.profiles[k]) for k in accts if k in self.profiles]

    def generate_daily_plan(self):
        """Generate today's schedule for all enabled accounts.

        If a plan already exists in the DB for today, reloads it instead
        of creating a duplicate.
        """
        today = date.today().isoformat()

        existing = self._db.execute(
            "SELECT COUNT(*) as cnt FROM schedule_log WHERE date = ?",
            (today,)
        ).fetchone()
        if existing and existing["cnt"] > 0:
            logger.info(
                f"Plan already exists for {today} ({existing['cnt']} tasks)")
            return self._load_existing_plan(today)

        accounts = self._get_enabled_accounts()
        start_min, end_min, window = self._parse_active_hours()
        jitter = self.config.get("time_jitter_minutes", 15)

        plan: list[ScheduledTask] = []

        for persona_key, prof in accounts:
            ads_id = prof.get("adspower_id", "")
            display_name = prof.get("display_name", persona_key)
            proxy_group = prof.get("proxy_group", "")
            if not ads_id:
                continue

            # Get warmup day — initialise if first ever run
            day = get_warmup_day(ads_id)
            if not day:
                day = init_warmup(ads_id)

            session_plan = _get_session_plan(day)
            daily_caps = _get_daily_caps(day)

            num_sessions = random.randint(
                session_plan["min_sessions"],
                session_plan["max_sessions"],
            )

            max_posts = 0
            if self.config.get("posting_enabled", False):
                max_posts = _get_max_posts(day)

            total_slots = num_sessions + max_posts
            if total_slots == 0:
                continue

            # Distribute across active window
            segment = window / (total_slots + 1)
            slot = 0

            for i in range(num_sessions):
                slot += 1
                t = self._pick_time(start_min, end_min, segment * slot, jitter)
                dur = random.randint(
                    session_plan["min_session_sec"] // 60,
                    session_plan["max_session_sec"] // 60,
                )
                plan.append(ScheduledTask(
                    account_id=persona_key,
                    ads_id=ads_id,
                    display_name=display_name,
                    task_type="warmup",
                    scheduled_time=self._minutes_to_hhmm(t),
                    duration_minutes=dur,
                    max_comments=daily_caps.get("comments", 5),
                    proxy_group=proxy_group,
                ))

            for i in range(max_posts):
                slot += 1
                t = self._pick_time(start_min, end_min, segment * slot, jitter)
                plan.append(ScheduledTask(
                    account_id=persona_key,
                    ads_id=ads_id,
                    display_name=display_name,
                    task_type="post",
                    scheduled_time=self._minutes_to_hhmm(t),
                    duration_minutes=0,
                    max_comments=0,
                    proxy_group=proxy_group,
                ))

        plan.sort(key=lambda t: t.scheduled_time)
        self._resolve_proxy_conflicts(plan)
        self._save_plan(today, plan)
        self.daily_plan = plan
        return plan

    def _parse_active_hours(self):
        s = self.config["active_hours"]["start"]
        e = self.config["active_hours"]["end"]
        sh, sm = map(int, s.split(":"))
        eh, em = map(int, e.split(":"))
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        return start_min, end_min, end_min - start_min

    def _pick_time(self, start_min, end_min, center, jitter):
        offset = random.randint(-jitter, jitter)
        t = int(center + start_min + offset)
        return max(start_min, min(end_min - 10, t))

    @staticmethod
    def _minutes_to_hhmm(m):
        return f"{int(m) // 60:02d}:{int(m) % 60:02d}"

    @staticmethod
    def _hhmm_to_minutes(s):
        h, m = map(int, s.split(":"))
        return h * 60 + m

    def _resolve_proxy_conflicts(self, plan):
        """Shift tasks sharing a proxy group so they don't overlap."""
        stagger = self.config.get("proxy_stagger_minutes", 5)
        by_proxy: dict[str, list[ScheduledTask]] = {}
        for task in plan:
            if task.proxy_group:
                by_proxy.setdefault(task.proxy_group, []).append(task)

        for group, tasks in by_proxy.items():
            tasks.sort(key=lambda t: t.scheduled_time)
            for i in range(1, len(tasks)):
                prev = tasks[i - 1]
                curr = tasks[i]
                prev_end = (self._hhmm_to_minutes(prev.scheduled_time)
                            + prev.duration_minutes + stagger)
                curr_start = self._hhmm_to_minutes(curr.scheduled_time)
                if curr_start < prev_end:
                    curr.scheduled_time = self._minutes_to_hhmm(prev_end)

    def _save_plan(self, today, plan):
        for task in plan:
            cur = self._db.execute(
                """INSERT INTO schedule_log
                   (date, account_id, ads_id, task_type, scheduled_time,
                    duration_minutes, max_comments, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (today, task.account_id, task.ads_id, task.task_type,
                 task.scheduled_time, task.duration_minutes, task.max_comments)
            )
            task.db_id = cur.lastrowid
        self._db.commit()

        # Write human-readable JSON
        os.makedirs(os.path.dirname(DAILY_PLAN_PATH), exist_ok=True)
        with open(DAILY_PLAN_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "date": today,
                "generated_at": datetime.now().isoformat(),
                "tasks": [asdict(t) for t in plan],
            }, f, indent=2)

        logger.info(f"Saved {len(plan)} tasks for {today}")

    def _load_existing_plan(self, today):
        rows = self._db.execute(
            """SELECT * FROM schedule_log
               WHERE date = ? AND status IN ('pending', 'running')
               ORDER BY scheduled_time""",
            (today,)
        ).fetchall()

        plan = []
        for row in rows:
            prof = self.profiles.get(row["account_id"], {})
            plan.append(ScheduledTask(
                account_id=row["account_id"],
                ads_id=row["ads_id"],
                display_name=prof.get("display_name", row["account_id"]),
                task_type=row["task_type"],
                scheduled_time=row["scheduled_time"],
                duration_minutes=row["duration_minutes"] or 0,
                max_comments=row["max_comments"] or 0,
                proxy_group=prof.get("proxy_group", ""),
                status=row["status"],
                db_id=row["id"],
            ))
        self.daily_plan = plan
        return plan

    # ── Display ──────────────────────────────────────────────────

    def print_plan(self, plan=None):
        """Pretty-print today's schedule to stdout."""
        plan = plan or self.daily_plan
        if not plan:
            print("  (no tasks scheduled)")
            return

        for task in sorted(plan, key=lambda t: t.scheduled_time):
            icon = {"pending": "○", "running": "●", "done": "✓",
                    "skipped": "─", "failed": "✗"}.get(task.status, "?")
            ttype = "WARMUP" if task.task_type == "warmup" else "POST  "
            parts = []
            if task.duration_minutes:
                parts.append(f"{task.duration_minutes}min")
            if task.max_comments:
                parts.append(f"max {task.max_comments} comments")
            details = ", ".join(parts)
            proxy = f" [{task.proxy_group}]" if task.proxy_group else ""
            day = get_warmup_day(task.ads_id) or "?"
            print(f"  {icon} {task.scheduled_time}  {ttype}  "
                  f"{task.display_name} ({task.ads_id}) day {day}  "
                  f"{details}{proxy}")

    # ── Main loop ────────────────────────────────────────────────

    def run(self):
        """Main scheduler loop — checks for due tasks every interval."""
        self.running = True
        interval = self.config.get("check_interval_seconds", 60)

        logger.info("=" * 60)
        logger.info("Daily Scheduler started")
        logger.info(f"Active hours: "
                     f"{self.config['active_hours']['start']} – "
                     f"{self.config['active_hours']['end']}")
        logger.info(f"Check interval: {interval}s")
        logger.info("=" * 60)

        plan = self.generate_daily_plan()
        pending = sum(1 for t in plan if t.status == "pending")
        logger.info(f"Today: {len(plan)} tasks, {pending} pending")
        print(f"\nToday's schedule ({date.today()}):")
        self.print_plan()
        print()

        while self.running:
            try:
                now = datetime.now()
                now_hhmm = now.strftime("%H:%M")
                today_str = date.today().isoformat()

                # Midnight rollover → new plan
                if self._plan_date and self._plan_date != today_str:
                    logger.info("New day — generating fresh plan")
                    self.daily_plan = []
                    plan = self.generate_daily_plan()
                    print(f"\nNew schedule ({date.today()}):")
                    self.print_plan()
                    print()
                self._plan_date = today_str

                # Launch due tasks
                for task in self.daily_plan:
                    if task.status != "pending":
                        continue
                    if task.scheduled_time <= now_hhmm:
                        self._execute_task(task)

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)

            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)

        # Wait for running tasks
        for ads_id, thread in list(self._active_tasks.items()):
            if thread.is_alive():
                logger.info(f"Waiting for {ads_id}...")
                thread.join(timeout=120)

        self._db.close()
        logger.info("Scheduler stopped")

    def stop(self):
        self.running = False

    # ── Task execution ───────────────────────────────────────────

    def _execute_task(self, task: ScheduledTask):
        """Launch a task in a background thread."""
        # Proxy group lock
        with self._lock:
            if task.proxy_group:
                holder = self._active_proxy_groups.get(task.proxy_group)
                if holder and holder != task.ads_id:
                    return  # Retry on next check

            # Don't double-launch
            if task.ads_id in self._active_tasks:
                return

            task.status = "running"
            task.started_at = datetime.now().isoformat()
            if task.proxy_group:
                self._active_proxy_groups[task.proxy_group] = task.ads_id

        self._db.execute(
            """UPDATE schedule_log SET status='running', started_at=?
               WHERE id=?""",
            (task.started_at, task.db_id)
        )
        self._db.commit()

        target = (self._run_warmup_task if task.task_type == "warmup"
                  else self._run_post_task)

        thread = threading.Thread(
            target=target, args=(task,),
            name=f"{task.task_type}-{task.account_id}",
            daemon=True,
        )
        self._active_tasks[task.ads_id] = thread
        thread.start()
        logger.info(
            f"[{task.display_name}] ▶ {task.task_type} "
            f"(due {task.scheduled_time}, {task.duration_minutes}min, "
            f"max {task.max_comments} comments)")

    def _run_warmup_task(self, task: ScheduledTask):
        """Execute a warmup session (background thread)."""
        import requests as _requests

        ads_id = task.ads_id
        api_base = (self.queue_config.get("adspower_api_base")
                    or self.adspower_config.get(
                        "adspower_api_base", "http://localhost:50325"))
        api_key = (self.queue_config.get("adspower_api_key")
                   or self.adspower_config.get("api_key", ""))
        grok_key = self.api_keys.get("grok_api_key", "")

        prof = self.profiles.get(task.account_id, {})
        persona = prof.get("persona", {})
        attributes = prof.get("attributes", {})
        account_age_days, account_created_at = self._resolve_age(prof)

        # Rotate proxy before browser launch
        if task.proxy_group:
            self._rotate_proxy(task.proxy_group)

        stats = None
        try:
            # 1. Start AdsPower browser
            resp = _requests.get(
                f"{api_base}/api/v1/browser/start"
                f"?user_id={ads_id}&api_key={api_key}",
                timeout=60,
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"AdsPower error: {data}")

            ws_endpoint = data.get("data", {}).get("ws", {}).get("puppeteer")
            if not ws_endpoint:
                raise RuntimeError("No CDP endpoint returned")

            logger.info(f"[{task.display_name}] Browser started")

            # 2. Connect Playwright
            from playwright.sync_api import sync_playwright
            from core.account_warmer import AccountWarmer

            with sync_playwright() as p:
                browser = None
                for attempt in range(5):
                    try:
                        browser = p.chromium.connect_over_cdp(ws_endpoint)
                        break
                    except Exception as e:
                        if attempt < 4:
                            time.sleep(2 * (attempt + 1))
                        else:
                            raise e

                ctx = (browser.contexts[0] if browser.contexts
                       else browser.new_context())
                all_pages = list(ctx.pages)

                # Pick Reddit tab or first tab
                page = None
                for pg in all_pages:
                    try:
                        if "reddit.com" in (pg.url or ""):
                            page = pg
                            break
                    except Exception:
                        pass
                if not page:
                    page = all_pages[0] if all_pages else ctx.new_page()

                # Close stale tabs
                for pg in all_pages:
                    if pg != page:
                        try:
                            pg.close()
                        except Exception:
                            pass

                # 3. Run warmup
                warmer = AccountWarmer(
                    ads_id, page,
                    persona=persona,
                    attributes=attributes,
                    grok_api_key=grok_key,
                    account_age_days=account_age_days,
                    account_created_at=account_created_at,
                )

                day = warmer.get_day()
                logger.info(
                    f"[{task.display_name}] Day {day}, "
                    f"{task.duration_minutes}min, "
                    f"max {task.max_comments} comments, "
                    f"{len(warmer.general_subs)} subs")

                stats = warmer.run_daily_warmup(
                    session_minutes=task.duration_minutes,
                    max_comments=task.max_comments,
                )

            # Success
            self._complete_task(task, "done", stats)
            if stats:
                logger.info(
                    f"[{task.display_name}] ✓ DONE — "
                    f"scrolls={stats['scrolls']}, "
                    f"votes={stats['upvotes']}↑/{stats['downvotes']}↓, "
                    f"comments={stats['comments']}, "
                    f"joins={stats['joins']}")

        except Exception as e:
            logger.error(
                f"[{task.display_name}] ✗ FAILED: {e}", exc_info=True)
            self._complete_task(task, "failed", error=str(e))

        finally:
            self._release_task(task)

    def _run_post_task(self, task: ScheduledTask):
        """Execute a posting task (Phase 2 — stub)."""
        logger.info(
            f"[{task.display_name}] Posting not yet implemented, skipping")
        self._complete_task(task, "skipped")
        self._release_task(task)

    # ── Task lifecycle ───────────────────────────────────────────

    def _complete_task(self, task, status, stats=None, error=None):
        task.status = status
        task.completed_at = datetime.now().isoformat()
        task.stats = stats or {}

        stats_json = json.dumps(stats) if stats else None
        self._db.execute(
            """UPDATE schedule_log
               SET status=?, completed_at=?, stats_json=?, error=?
               WHERE id=?""",
            (status, task.completed_at, stats_json, error, task.db_id)
        )
        self._db.commit()

    def _release_task(self, task):
        """Release proxy group lock and remove from active tasks."""
        with self._lock:
            if (task.proxy_group
                    and self._active_proxy_groups.get(task.proxy_group)
                    == task.ads_id):
                del self._active_proxy_groups[task.proxy_group]
            self._active_tasks.pop(task.ads_id, None)

    # ── Helpers ──────────────────────────────────────────────────

    def _resolve_age(self, prof):
        """Return (age_days, created_at) from a profile dict."""
        account_created_at = prof.get("created_at")
        age_days = None
        try:
            raw = (prof.get("reddit_account", {}) or {}).get("age_days")
            if raw is not None:
                age_days = max(0, int(raw))
        except Exception:
            pass

        if age_days is None and account_created_at:
            try:
                dt = datetime.fromisoformat(
                    str(account_created_at).replace("Z", "+00:00"))
                now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
                age_days = max(0, (now - dt).days)
            except Exception:
                pass
        return age_days, account_created_at

    def _rotate_proxy(self, proxy_group):
        pg = self.queue_config.get("proxy_groups", {}).get(proxy_group, {})
        url = pg.get("rotation_url", "")
        if not url:
            return
        try:
            import requests as _requests
            logger.info(f"Rotating proxy {proxy_group}...")
            _requests.get(url, timeout=15)
            wait = pg.get("wait_after_rotate_sec", 5)
            time.sleep(wait)
        except Exception as e:
            logger.warning(f"Proxy rotation failed ({proxy_group}): {e}")

    # ── Status ───────────────────────────────────────────────────

    def get_status(self):
        today = date.today().isoformat()
        rows = self._db.execute(
            """SELECT status, COUNT(*) as cnt
               FROM schedule_log WHERE date=? GROUP BY status""",
            (today,)
        ).fetchall()
        counts = {r["status"]: r["cnt"] for r in rows}
        return {
            "date": today,
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "done": counts.get("done", 0),
            "failed": counts.get("failed", 0),
            "skipped": counts.get("skipped", 0),
        }

    def get_history(self, days=7):
        """Get summary for the last N days."""
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = self._db.execute(
            """SELECT date, task_type, status, COUNT(*) as cnt
               FROM schedule_log WHERE date >= ?
               GROUP BY date, task_type, status
               ORDER BY date DESC""",
            (cutoff,)
        ).fetchall()
        return [dict(r) for r in rows]
