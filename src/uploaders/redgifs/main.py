"""Main entry point for multi-account RedGifs uploader"""

import asyncio
import random
from pathlib import Path

import aiohttp

from src import __version__
from src.account_manager import AccountManager, Account
from src.api_client import RedGifsAPIClient
from src.uploader import VideoUploader
from src.utils import find_video_files, format_time, check_ffprobe_installed
from src.logger import setup_logger
from src.results_saver import ResultsSaver

logger = setup_logger()


async def delayed_upload(
    uploader: VideoUploader,
    session: aiohttp.ClientSession,
    filepath: str,
    index: int,
    total: int,
    delay: float
):
    """
    Upload with delay to avoid rate limit

    Args:
        uploader: VideoUploader instance
        session: aiohttp session
        filepath: Path to file
        index: File number
        total: Total files
        delay: Delay before start in seconds
    """
    # Check rate limit before start
    if uploader.rate_limit.reached:
        filename = Path(filepath).name
        logger.warning(f"[Thread {index}] {filename} - skipped (limit reached)")
        return filename, "X SKIPPED (limit reached)"

    if delay > 0:
        logger.info(f"[Thread {index}] Starting in {delay:.1f} sec...")
        await asyncio.sleep(delay)

        # Check rate limit after delay
        if uploader.rate_limit.reached:
            filename = Path(filepath).name
            logger.warning(f"[Thread {index}] {filename} - skipped (limit reached)")
            return filename, "X SKIPPED (limit reached)"

    return await uploader.upload_video(session, filepath, index, total)


async def rotate_proxy_ip(account: Account) -> bool:
    """
    Rotate proxy IP by calling the rotation URL

    Args:
        account: Account with proxy_rotation_url configured

    Returns:
        True if rotation succeeded, False otherwise
    """
    if not account.proxy_rotation_url:
        return True  # No rotation URL configured, nothing to do

    logger.info(f"[{account.name}] Rotating proxy IP...")

    try:
        resolver = aiohttp.resolver.ThreadedResolver()
        connector = aiohttp.TCPConnector(resolver=resolver)
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.get(account.proxy_rotation_url) as response:
                status = response.status
                if status == 200:
                    logger.info(f"[{account.name}] Proxy IP rotated successfully")
                    return True
                else:
                    logger.error(f"[{account.name}] Proxy rotation failed: HTTP {status} - uploads will not proceed")
                    return False
    except aiohttp.ClientError as e:
        logger.error(f"[{account.name}] Proxy rotation network error: {e} - uploads will not proceed")
        return False
    except Exception as e:
        logger.error(f"[{account.name}] Proxy rotation error: {e} - uploads will not proceed")
        return False


async def upload_for_account(account: Account, base_dir: Path) -> dict:
    """
    Run upload process for a single account

    Args:
        account: Account configuration
        base_dir: Base directory for video folders

    Returns:
        Dict with results summary
    """
    logger.info(f"\n{'=' * 60}")
    logger.info(f"ACCOUNT: {account.name}")
    logger.info(f"{'=' * 60}")

    # Validate token
    if not account.token or account.token == "your_bearer_token_here":
        logger.error(f"[{account.name}] Token not configured - skipping")
        return {"account": account.name, "success": 0, "failed": 0, "skipped": 0, "error": "Token not configured"}

    # Find video files in account's folder
    videos_dir = base_dir / account.video_folder
    if not videos_dir.exists():
        logger.warning(f"[{account.name}] Folder '{account.video_folder}' not found - skipping")
        return {"account": account.name, "success": 0, "failed": 0, "skipped": 0, "error": f"Folder not found: {account.video_folder}"}

    video_files = find_video_files(videos_dir)

    if not video_files:
        logger.warning(f"[{account.name}] No video files in '{account.video_folder}' - skipping")
        return {"account": account.name, "success": 0, "failed": 0, "skipped": 0, "error": "No video files"}

    # Log account info
    logger.info(f"[{account.name}] Files: {len(video_files)}")
    logger.info(f"[{account.name}] Threads: {account.threads}")
    logger.info(f"[{account.name}] Tags: {', '.join(account.tags)}")
    logger.info(f"[{account.name}] Token: {account.token[:20]}...{account.token[-10:]}")
    if account.proxy_url:
        logger.info(f"[{account.name}] Proxy: {account.proxy_url}")

    # Rotate proxy IP if rotation URL is configured
    if account.proxy_rotation_url:
        rotation_success = await rotate_proxy_ip(account)
        if not rotation_success:
            logger.error(f"[{account.name}] Proxy rotation failed - skipping all uploads for this account")
            return {
                "account": account.name,
                "success": 0,
                "failed": 0,
                "skipped": len(video_files),
                "error": "Proxy rotation failed"
            }

    # Create clients
    api_client = RedGifsAPIClient(account)
    uploader = VideoUploader(account, api_client)

    # Async upload
    resolver = aiohttp.resolver.ThreadedResolver()
    connector = aiohttp.TCPConnector(limit=account.threads * 2, resolver=resolver)

    async with aiohttp.ClientSession(connector=connector) as session:
        # Create tasks with delays
        tasks = []
        for i, video_file in enumerate(video_files, 1):
            delay = random.uniform(1, 2) if i > 1 else 0
            task = delayed_upload(uploader, session, str(video_file), i, len(video_files), delay)
            tasks.append(task)

        # Semaphore to limit parallel uploads
        semaphore = asyncio.Semaphore(account.threads)

        async def limited_upload(task):
            async with semaphore:
                return await task

        limited_tasks = [limited_upload(task) for task in tasks]
        results = await asyncio.gather(*limited_tasks, return_exceptions=True)

    # Count results (uploader returns checkmark for success, X for failure)
    success = sum(1 for r in results if isinstance(r, tuple) and "redgifs.com/watch" in r[1])
    skipped = sum(1 for r in results if isinstance(r, tuple) and "SKIPPED" in r[1])
    failed = len(results) - success - skipped

    # Account summary
    logger.info(f"\n[{account.name}] Summary:")
    logger.info(f"[{account.name}] Success: {success}")
    if failed > 0:
        logger.error(f"[{account.name}] Failed: {failed}")
    if skipped > 0:
        logger.warning(f"[{account.name}] Skipped (limit): {skipped}")

    # Rate limit message
    if uploader.rate_limit.reached and uploader.rate_limit.delay > 0:
        logger.warning(f"[{account.name}] GIF LIMIT REACHED - can upload more in: {format_time(uploader.rate_limit.delay)}")

    # Save results for this account
    try:
        results_file = ResultsSaver.save_results(results, prefix=f"{account.name}_")
        logger.info(f"[{account.name}] Results saved: {results_file}")
    except Exception as e:
        logger.error(f"[{account.name}] Failed to save results: {e}")

    return {
        "account": account.name,
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "results": results
    }


async def main():
    """Main application function"""

    # Check ffprobe
    if not check_ffprobe_installed():
        logger.error("=" * 60)
        logger.error("ffprobe NOT INSTALLED!")
        logger.error("=" * 60)
        logger.error("ffprobe is required to determine video duration.")
        logger.error("")
        logger.error("Install FFmpeg using one of these methods:")
        logger.error("  1. winget install FFmpeg")
        logger.error("  2. choco install ffmpeg")
        logger.error("  3. Download from https://ffmpeg.org/download.html")
        logger.error("=" * 60)
        input("Press Enter to exit...")
        return

    # AUTO REFRESH TOKENS FIRST
    logger.info("Refreshing bearer tokens from AdsPower...")
    try:
        from refresh_tokens import main as refresh_tokens_main
        refresh_tokens_main()
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        logger.warning("Continuing with existing tokens...")

    # Load accounts
    try:
        manager = AccountManager()
    except FileNotFoundError as e:
        logger.error(str(e))
        input("Press Enter to exit...")
        return

    enabled_accounts = manager.get_enabled_accounts()

    if not enabled_accounts:
        logger.error("No enabled accounts found in accounts.json")
        input("Press Enter to exit...")
        return

    # Header
    print("\n" + "=" * 60)
    print(f"RedGifs Multi-Account Uploader v{__version__}")
    print("=" * 60)
    logger.info(f"Enabled accounts: {len(enabled_accounts)}")
    for acc in enabled_accounts:
        logger.info(f"  - {acc.name} ({acc.video_folder})")
    print("=" * 60)

    # Base directory
    base_dir = Path(__file__).parent

    # Process each account
    all_results = []
    for account in enabled_accounts:
        result = await upload_for_account(account, base_dir)
        all_results.append(result)

    # Final summary
    print("\n" + "=" * 60)
    print("FINAL SUMMARY (ALL ACCOUNTS)")
    print("=" * 60)

    total_success = sum(r["success"] for r in all_results)
    total_failed = sum(r["failed"] for r in all_results)
    total_skipped = sum(r["skipped"] for r in all_results)

    for result in all_results:
        status = "OK" if result.get("error") is None else result.get("error", "")
        logger.info(f"{result['account']}: {result['success']} success, {result['failed']} failed, {result['skipped']} skipped - {status}")

    print("-" * 60)
    logger.info(f"TOTAL: {total_success} success, {total_failed} failed, {total_skipped} skipped")
    print("=" * 60)

    input("\nPress Enter to exit...")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("\nInterrupted by user")
    except Exception as e:
        logger.error(f"Critical error: {e}", exc_info=True)
        input("Press Enter to exit...")
