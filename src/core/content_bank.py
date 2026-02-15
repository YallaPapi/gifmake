"""
Content bank management.
Picks random unexhausted files from a creator's content folder.

A file is "exhausted" when it has been posted to at least `max_posts_per_file`
subreddits. Uses post_history.db to track what's already been posted.

Usage:
    bank = ContentBank("D:\\content_bank")
    files = bank.pick_files("creator_mae", count=3)
"""

import os
import random
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Supported media extensions
MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif",
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
}


class ContentBank:
    """Manages content folders and picks random unexhausted files for posting."""

    def __init__(self, root_path, max_posts_per_file=8):
        """
        Args:
            root_path: Parent folder containing creator subfolders.
            max_posts_per_file: A file is exhausted after this many sub posts.
        """
        self.root = Path(root_path)
        self.max_posts = max_posts_per_file
        self._hash_cache = {}  # file_path -> hash (avoid re-hashing)

    def list_creators(self):
        """Return list of creator folder names."""
        if not self.root.exists():
            return []
        return [d.name for d in self.root.iterdir() if d.is_dir()]

    def pick_files(self, creator, count=3):
        """Pick `count` random unexhausted files from a creator's folder.

        Returns list of absolute file paths. May return fewer than `count`
        if not enough unexhausted files exist.
        """
        # Import here to avoid circular imports at module level
        from core.vision_matcher import content_file_hash
        from core.post_history import get_posted_subs

        folder = self.root / creator
        if not folder.exists():
            logger.warning(f"Content folder not found: {folder}")
            return []

        # Scan for media files
        all_files = []
        for f in folder.rglob("*"):
            if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS:
                all_files.append(str(f))

        if not all_files:
            logger.warning(f"No media files in {folder}")
            return []

        # Filter out exhausted files
        available = []
        for fpath in all_files:
            h = self.get_file_hash(fpath, content_file_hash)
            posted_count = len(get_posted_subs(h))
            if posted_count < self.max_posts:
                available.append(fpath)

        if not available:
            logger.info(f"All {len(all_files)} files exhausted for '{creator}'")
            return []

        picked = random.sample(available, min(count, len(available)))
        logger.info(
            f"Picked {len(picked)}/{len(available)} available files "
            f"for '{creator}' ({len(all_files)} total)"
        )
        return picked

    def get_file_hash(self, file_path, hash_fn=None):
        """Return cached content hash for a file."""
        if file_path not in self._hash_cache:
            if hash_fn is None:
                from core.vision_matcher import content_file_hash
                hash_fn = content_file_hash
            self._hash_cache[file_path] = hash_fn(file_path)
        return self._hash_cache[file_path]

    def get_stats(self, creator):
        """Return stats for a creator's content bank.

        Returns dict: {total, exhausted, available}
        """
        from core.vision_matcher import content_file_hash
        from core.post_history import get_posted_subs

        folder = self.root / creator
        if not folder.exists():
            return {"total": 0, "exhausted": 0, "available": 0}

        total = 0
        exhausted = 0
        for f in folder.rglob("*"):
            if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS:
                total += 1
                h = self.get_file_hash(str(f), content_file_hash)
                if len(get_posted_subs(h)) >= self.max_posts:
                    exhausted += 1

        return {
            "total": total,
            "exhausted": exhausted,
            "available": total - exhausted,
        }
