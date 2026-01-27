"""Script to delete successfully uploaded videos"""

import os
from pathlib import Path
from datetime import datetime


def find_latest_results_file() -> Path | None:
    """
    Find the latest results file

    Returns:
        Path to the latest results file or None
    """
    current_dir = Path.cwd()
    results_files = list(current_dir.glob("results_*.txt"))

    if not results_files:
        return None

    # Сортируем по времени изменения (самый свежий последний)
    results_files.sort(key=lambda f: f.stat().st_mtime)
    return results_files[-1]


def parse_results_file(filepath: Path) -> dict:
    """
    Parse results file

    Args:
        filepath: Path to results file

    Returns:
        Dictionary with successful, failed and skipped files
    """
    results = {
        "success": [],
        "failed": [],
        "skipped": []
    }

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()

            # Пропускаем комментарии и пустые строки
            if not line or line.startswith('#'):
                continue

            # Парсим формат: STATUS|FILENAME|URL_OR_ERROR
            parts = line.split('|', 2)
            if len(parts) != 3:
                continue

            status, filename, info = parts

            if status == "SUCCESS":
                results["success"].append({"filename": filename, "url": info})
            elif status == "FAILED":
                results["failed"].append({"filename": filename, "error": info})
            elif status == "SKIPPED":
                results["skipped"].append({"filename": filename, "reason": info})

    return results


def delete_successful_videos(success_list: list, videos_dir: Path) -> dict:
    """
    Delete successfully uploaded videos

    Args:
        success_list: List of successful uploads
        videos_dir: Videos folder

    Returns:
        Deletion statistics
    """
    stats = {
        "deleted": [],
        "not_found": [],
        "errors": []
    }

    for item in success_list:
        filename = item["filename"]
        filepath = videos_dir / filename

        try:
            if filepath.exists():
                filepath.unlink()
                stats["deleted"].append(filename)
                print(f"✓ Deleted: {filename}")
            else:
                stats["not_found"].append(filename)
                print(f"⚠ Not found: {filename}")
        except Exception as e:
            stats["errors"].append({"filename": filename, "error": str(e)})
            print(f"✗ Error deleting {filename}: {e}")

    return stats


def main():
    """Main function"""
    print("\n" + "=" * 60)
    print("Cleanup - Delete successfully uploaded videos")
    print("=" * 60)

    # Find latest results file
    results_file = find_latest_results_file()

    if not results_file:
        print("✗ No results files found!")
        print("Run main.py at least once to create a results file.")
        input("\nPress Enter to exit...")
        return

    print(f"\nFound file: {results_file.name}")
    file_time = datetime.fromtimestamp(results_file.stat().st_mtime)
    print(f"Created: {file_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Parse results
    results = parse_results_file(results_file)

    total_success = len(results["success"])
    total_failed = len(results["failed"])
    total_skipped = len(results["skipped"])

    print("\n" + "=" * 60)
    print("Statistics from results file:")
    print("=" * 60)
    print(f"✓ Successfully uploaded: {total_success}")
    print(f"✗ Upload errors:         {total_failed}")
    print(f"⊘ Skipped:               {total_skipped}")
    print("=" * 60)

    if total_success == 0:
        print("\n✓ No successfully uploaded videos to delete.")
        input("\nPress Enter to exit...")
        return

    # Confirm deletion
    print(f"\n⚠ {total_success} files will be deleted from 'videos/' folder")
    confirm = input("Continue? (y/N): ").strip().lower()

    if confirm != 'y':
        print("Cancelled by user.")
        input("\nPress Enter to exit...")
        return

    # Delete files
    videos_dir = Path.cwd() / "videos"
    if not videos_dir.exists():
        print(f"\n✗ 'videos' folder not found!")
        input("\nPress Enter to exit...")
        return

    print("\n" + "=" * 60)
    print("Deleting files:")
    print("=" * 60)

    stats = delete_successful_videos(results["success"], videos_dir)

    # Final statistics
    print("\n" + "=" * 60)
    print("Deletion summary:")
    print("=" * 60)
    print(f"✓ Deleted:    {len(stats['deleted'])}")
    print(f"⚠ Not found:  {len(stats['not_found'])}")
    print(f"✗ Errors:     {len(stats['errors'])}")
    print("=" * 60)

    # Details
    if stats["deleted"]:
        print("\nDeleted files:")
        for filename in stats["deleted"]:
            print(f"  - {filename}")

    if stats["not_found"]:
        print("\nNot found files:")
        for filename in stats["not_found"]:
            print(f"  - {filename}")

    if stats["errors"]:
        print("\nErrors:")
        for item in stats["errors"]:
            print(f"  - {item['filename']}: {item['error']}")

    print("\n" + "=" * 60)
    input("\nPress Enter to exit...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\n✗ Critical error: {e}")
        input("\nPress Enter to exit...")
