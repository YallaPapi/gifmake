"""Scan video sources (local folders, Google Drive later)."""

from pathlib import Path
from typing import Generator, Optional

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".flv", ".gif"}


def scan_local_folder(
    folder_path: str,
    recursive: bool = False
) -> Generator[Path, None, None]:
    """
    Yield video files from a local folder.

    Args:
        folder_path: Path to the folder to scan
        recursive: If True, scan subdirectories recursively

    Yields:
        Path objects for each video file found
    """
    folder = Path(folder_path)
    if not folder.exists():
        return

    if not folder.is_dir():
        # Single file passed
        if folder.suffix.lower() in VIDEO_EXTENSIONS:
            yield folder
        return

    if recursive:
        # Recursive glob pattern
        for ext in VIDEO_EXTENSIONS:
            for file in folder.rglob(f"*{ext}"):
                if file.is_file():
                    yield file
    else:
        # Non-recursive, single directory
        for file in sorted(folder.iterdir()):
            if file.is_file() and file.suffix.lower() in VIDEO_EXTENSIONS:
                yield file


def scan_all_sources(sources: list) -> Generator[tuple[str, Path], None, None]:
    """
    Scan all configured sources.

    Args:
        sources: List of Source objects from config

    Yields:
        Tuples of (account_name, file_path) for each video found
    """
    for source in sources:
        if source.type == "local":
            # Check if source has recursive attribute (optional)
            recursive = getattr(source, "recursive", False)
            for file_path in scan_local_folder(source.path, recursive=recursive):
                yield (source.account, file_path)
        elif source.type == "gdrive":
            # TODO: Google Drive support later
            pass


def get_video_count(folder_path: str, recursive: bool = False) -> int:
    """Count video files in a folder without yielding them."""
    return sum(1 for _ in scan_local_folder(folder_path, recursive))
