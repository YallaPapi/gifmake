"""Main scheduler loop - processes queue and uploads videos."""

import sys
import time
import traceback
import logging
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

# Add parent paths for imports
_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from scheduler.database import Database
from scheduler.config import load_config, Config
from scheduler.sources import scan_all_sources

# Set up logging
logger = logging.getLogger(__name__)


class Scheduler:
    """
    Main scheduler class that manages video upload scheduling.

    The scheduler:
    1. Scans configured sources for video files
    2. Queues them with scheduled upload times
    3. Processes uploads at scheduled times using UploadBridge
    4. Handles errors with retry logic
    """

    def __init__(self, config: Config = None, config_path: str = None):
        """
        Initialize the scheduler.

        Args:
            config: Config object (if None, loads from file)
            config_path: Path to config file (used if config is None)
        """
        self.config = config or load_config(config_path)
        self.db = Database(self.config.database_path)
        self.running = False
        self._upload_bridge_cache = {}  # Cache UploadBridge instances

    def calculate_schedule_times(self, count: int) -> list[datetime]:
        """
        Calculate evenly spread upload times within active hours.

        For example, with 20 posts over 15 hours (8am-11pm):
        - Interval = 15 hours / 20 posts = 45 minutes between posts

        Args:
            count: Number of upload slots to calculate

        Returns:
            List of datetime objects for scheduled upload times.
            Only includes future times.
        """
        now = datetime.now()
        today = now.date()

        start_time = datetime.strptime(self.config.active_hours_start, "%H:%M").time()
        end_time = datetime.strptime(self.config.active_hours_end, "%H:%M").time()

        start_dt = datetime.combine(today, start_time)
        end_dt = datetime.combine(today, end_time)

        # If we're past end time, schedule for tomorrow
        if now > end_dt:
            start_dt += timedelta(days=1)
            end_dt += timedelta(days=1)

        # If we're before start time today, use today's window
        # If we're within the window, use remaining time
        if now < start_dt:
            effective_start = start_dt
        else:
            effective_start = now

        total_minutes = (end_dt - start_dt).total_seconds() / 60

        if count <= 1:
            # Single post - schedule at start of window or now
            return [max(start_dt, now)]

        # Calculate interval based on full window, not remaining time
        # This ensures consistent spacing
        interval = total_minutes / count

        times = []
        for i in range(count):
            upload_time = start_dt + timedelta(minutes=interval * i)
            if upload_time > now:  # Only future times
                times.append(upload_time)

        return times

    def calculate_batch_times(self) -> list[datetime]:
        """
        Get batch upload times for today.

        Batch mode uploads all queued videos at specific times
        (e.g., 09:00, 15:00, 21:00).

        Returns:
            List of datetime objects for remaining batch times today.
        """
        now = datetime.now()
        today = now.date()
        times = []

        for time_str in self.config.batch_times:
            t = datetime.strptime(time_str, "%H:%M").time()
            dt = datetime.combine(today, t)
            if dt > now:
                times.append(dt)

        # If no times left today, return first batch time tomorrow
        if not times and self.config.batch_times:
            first_time_str = self.config.batch_times[0]
            t = datetime.strptime(first_time_str, "%H:%M").time()
            dt = datetime.combine(today + timedelta(days=1), t)
            times.append(dt)

        return times

    def scan_and_queue(self):
        """Scan all sources and add new videos to queue."""
        added = 0
        for account_name, file_path in scan_all_sources(self.config.sources):
            # Skip if already in queue
            if self.db.file_in_queue(account_name, file_path):
                continue

            # Calculate scheduled time
            pending = self.db.get_pending_count(account_name)
            uploaded_today = self.db.get_uploads_today(account_name)
            remaining_quota = self.config.posts_per_day - uploaded_today

            if pending >= remaining_quota:
                continue  # Queue is full for today's quota

            if self.config.schedule_mode == "spread":
                times = self.calculate_schedule_times(remaining_quota)
                scheduled_at = times[pending] if pending < len(times) else datetime.now()
            else:
                batch_times = self.calculate_batch_times()
                scheduled_at = batch_times[0] if batch_times else datetime.now()

            self.db.add_to_queue(account_name, str(file_path), scheduled_at)
            added += 1
            print(f"Queued: {file_path.name} for {account_name} at {scheduled_at.strftime('%H:%M')}")

        return added

    def get_upload_bridge(self, account_name: str):
        """
        Get or create UploadBridge for an account.

        Caches bridge instances to avoid repeated initialization.

        Args:
            account_name: Name of the account to get bridge for

        Returns:
            UploadBridge instance configured for the account
        """
        if account_name in self._upload_bridge_cache:
            return self._upload_bridge_cache[account_name]

        try:
            from uploaders.upload_bridge import UploadBridge
        except ImportError:
            # Fallback for different working directories
            try:
                from upload_bridge import UploadBridge
            except ImportError:
                raise ImportError(
                    "Could not import UploadBridge. Ensure uploaders package is available."
                )

        bridge = UploadBridge(account_name)
        self._upload_bridge_cache[account_name] = bridge
        return bridge

    def process_upload(self, item: dict) -> bool:
        """Process a single upload. Returns True on success."""
        account_name = item["account_name"]
        file_path = item["file_path"]
        queue_id = item["id"]

        print(f"Uploading: {Path(file_path).name} for {account_name}...")

        self.db.update_status(queue_id, "processing")

        try:
            bridge = self.get_upload_bridge(account_name)
            result = bridge.upload_single_file_sync(file_path, 1, 1)

            if result.get("success"):
                url = result.get("url", "")
                print(f"  Success: {url}")
                self.db.update_status(queue_id, "done")
                self.db.add_to_history(account_name, file_path, "success", redgifs_url=url)
                return True
            else:
                error_msg = result.get("error", "Unknown error")
                print(f"  Failed: {error_msg}")
                self._handle_failure(item, error_msg)
                return False

        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            print(f"  Exception: {error_msg}")
            traceback.print_exc()
            self._handle_failure(item, error_msg)
            return False

    def _classify_error(self, error_msg: str) -> str:
        """
        Classify an error message into a category.

        Categories:
        - rate_limit: API rate limiting (429, too many requests)
        - token: Authentication issues (401, invalid token)
        - network: Connection issues (timeout, DNS, etc.)
        - file: File-related issues (not found, too large)
        - unknown: Unclassified errors

        Args:
            error_msg: The error message to classify

        Returns:
            Error category string
        """
        error_lower = error_msg.lower()

        # Rate limiting
        if any(x in error_lower for x in ["rate", "limit", "429", "too many"]):
            return "rate_limit"

        # Authentication
        if any(x in error_lower for x in ["token", "auth", "401", "403", "forbidden", "unauthorized"]):
            return "token"

        # Network issues
        if any(x in error_lower for x in ["network", "connection", "timeout", "dns", "refused", "reset"]):
            return "network"

        # File issues
        if any(x in error_lower for x in ["file not found", "no such file", "too large", "size"]):
            return "file"

        return "unknown"

    def _handle_failure(self, item: dict, error_msg: str):
        """
        Handle upload failure - retry with backoff or mark as failed.

        Retry backoff is configured in config.retry_backoff_minutes.
        After retry_max failures, the item is marked as failed.

        Args:
            item: Queue item dict with id, account_name, file_path
            error_msg: Error message from the failed upload
        """
        queue_id = item["id"]
        account_name = item["account_name"]
        file_path = item["file_path"]
        retry_count = self.db.get_retry_count(queue_id)

        # Classify error
        error_type = self._classify_error(error_msg)

        # Log error
        self.db.log_error(queue_id, account_name, file_path, error_type, error_msg)
        logger.warning(f"Upload failed for {Path(file_path).name}: {error_type} - {error_msg}")

        # Retry or fail
        if retry_count < self.config.retry_max:
            backoff_index = min(retry_count, len(self.config.retry_backoff_minutes) - 1)
            backoff = self.config.retry_backoff_minutes[backoff_index]
            next_retry = datetime.now() + timedelta(minutes=backoff)
            self.db.increment_retry(queue_id, next_retry)
            print(f"  Scheduled retry #{retry_count + 1} in {backoff} minutes")
            logger.info(f"Retry scheduled for {Path(file_path).name} at {next_retry}")
        else:
            self.db.update_status(queue_id, "failed")
            self.db.add_to_history(account_name, file_path, "failed", error_message=error_msg)
            print(f"  Max retries ({self.config.retry_max}) exceeded, marked as failed")
            logger.error(f"Max retries exceeded for {Path(file_path).name}")

    def run_once(self):
        """Run one iteration of the scheduler."""
        # Get enabled accounts from sources
        accounts = set(s.account for s in self.config.sources)

        for account_name in accounts:
            # Check daily limit
            uploaded_today = self.db.get_uploads_today(account_name)
            if uploaded_today >= self.config.posts_per_day:
                continue

            # Get next due item
            item = self.db.get_next_pending(account_name)
            if item:
                self.process_upload(item)
                # Small delay between uploads
                time.sleep(5)

    def run(self, check_interval: int = 60):
        """
        Main scheduler loop.

        Continuously checks for due uploads and processes them.
        Runs until stop() is called or KeyboardInterrupt.

        Args:
            check_interval: Seconds between queue checks (default: 60)
        """
        print(f"Scheduler started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Mode: {self.config.schedule_mode}, Posts/day: {self.config.posts_per_day}")
        print(f"Active hours: {self.config.active_hours_start} - {self.config.active_hours_end}")
        print(f"Check interval: {check_interval}s")
        print()

        logger.info(f"Scheduler started: mode={self.config.schedule_mode}, posts_per_day={self.config.posts_per_day}")
        self.running = True

        while self.running:
            try:
                self.run_once()
            except KeyboardInterrupt:
                print("\nShutting down...")
                logger.info("Scheduler interrupted by user")
                break
            except Exception as e:
                print(f"Scheduler error: {e}")
                logger.exception("Scheduler error in main loop")
                traceback.print_exc()

            # Sleep for check_interval seconds, checking running flag periodically
            for _ in range(check_interval):
                if not self.running:
                    break
                time.sleep(1)

        self.db.close()
        print("Scheduler stopped")
        logger.info("Scheduler stopped")

    def stop(self):
        """Stop the scheduler."""
        self.running = False

    def get_status(self) -> dict:
        """
        Get current scheduler status.

        Returns:
            Dict containing:
            - running: bool - whether scheduler loop is running
            - timestamp: str - current timestamp
            - config: dict - posts_per_day, schedule_mode, active_hours
            - accounts: dict - per-account status with uploads, remaining, pending
        """
        accounts = set(s.account for s in self.config.sources)
        status = {
            "running": self.running,
            "timestamp": datetime.now().isoformat(),
            "config": {
                "posts_per_day": self.config.posts_per_day,
                "schedule_mode": self.config.schedule_mode,
                "active_hours_start": self.config.active_hours_start,
                "active_hours_end": self.config.active_hours_end,
                "retry_max": self.config.retry_max,
            },
            "accounts": {},
        }

        for account in sorted(accounts):
            uploaded = self.db.get_uploads_today(account)
            pending = self.db.get_pending_count(account)
            remaining = self.config.posts_per_day - uploaded

            status["accounts"][account] = {
                "uploaded_today": uploaded,
                "remaining": remaining,
                "pending_in_queue": pending,
                "quota_reached": remaining <= 0,
            }

        return status

    def clear_cache(self):
        """Clear cached UploadBridge instances (useful after token refresh)."""
        self._upload_bridge_cache.clear()


def main():
    """Entry point for scheduler."""
    scheduler = Scheduler()
    scheduler.run()


if __name__ == "__main__":
    main()
