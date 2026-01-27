"""Главный файл - точка входа в приложение"""

import asyncio
import random
from pathlib import Path

import aiohttp

from src import __version__
from src.config import Config
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
    Загрузка с задержкой для избежания rate limit

    Args:
        uploader: Экземпляр VideoUploader
        session: aiohttp сессия
        filepath: Путь к файлу
        index: Номер файла
        total: Всего файлов
        delay: Задержка перед стартом в секундах
    """
    # Проверка rate limit перед стартом
    if uploader.rate_limit.reached:
        filename = Path(filepath).name
        logger.warning(f"[Thread {index}] {filename} - skipped (limit reached)")
        return filename, "✗ SKIPPED (limit reached)"

    if delay > 0:
        logger.info(f"[Thread {index}] Starting in {delay:.1f} sec...")
        await asyncio.sleep(delay)

        # Проверка rate limit после задержки
        if uploader.rate_limit.reached:
            filename = Path(filepath).name
            logger.warning(f"[Thread {index}] {filename} - skipped (limit reached)")
            return filename, "✗ SKIPPED (limit reached)"

    return await uploader.upload_video(session, filepath, index, total)


async def main():
    """Главная функция приложения"""
    try:
        # Загрузка конфигурации
        config = Config()
    except ValueError as e:
        logger.error(str(e))
        input("Press Enter to exit...")
        return

    # Проверка ffprobe
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

    # Поиск видео файлов в папке videos
    videos_dir = Path(__file__).parent / "videos"
    if not videos_dir.exists():
        logger.error("'videos' folder not found! Create a videos folder and put videos there.")
        input("Press Enter to exit...")
        return

    video_files = find_video_files(videos_dir)

    if not video_files:
        logger.warning("No video files found in 'videos' folder")
        input("Press Enter to exit...")
        return

    # Заголовок
    print("\n" + "=" * 60)
    print(f"RedGifs Uploader v{__version__}")
    print("=" * 60)
    logger.info(f"Files: {len(video_files)}")
    logger.info(f"Threads: {config.threads}")
    logger.info(f"Tags: {', '.join(config.tags)}")
    logger.info(f"Token: {config.bearer_token[:20]}...{config.bearer_token[-10:]}")
    if config.proxy_url:
        logger.info("Proxy: ✓")
    print("=" * 60)

    # Создание клиентов
    api_client = RedGifsAPIClient(config)
    uploader = VideoUploader(config, api_client)

    # Асинхронная загрузка
    # Use ThreadedResolver to fix DNS issues on Windows
    resolver = aiohttp.resolver.ThreadedResolver()
    connector = aiohttp.TCPConnector(limit=config.threads * 2, resolver=resolver)
    async with aiohttp.ClientSession(connector=connector) as session:

        # Создание задач с задержками
        tasks = []
        for i, video_file in enumerate(video_files, 1):
            delay = random.uniform(10, 20) if i > 1 else 0
            task = delayed_upload(uploader, session, str(video_file), i, len(video_files), delay)
            tasks.append(task)

        # Семафор для ограничения параллельных загрузок
        semaphore = asyncio.Semaphore(config.threads)

        async def limited_upload(task):
            async with semaphore:
                return await task

        limited_tasks = [limited_upload(task) for task in tasks]
        results = await asyncio.gather(*limited_tasks, return_exceptions=True)

    # Подсчет результатов
    success = sum(1 for r in results if isinstance(r, tuple) and r[1].startswith("✓"))
    skipped = sum(1 for r in results if isinstance(r, tuple) and "SKIPPED" in r[1])
    failed = sum(1 for r in results if isinstance(r, tuple) and r[1].startswith("✗") and "SKIPPED" not in r[1])

    # Итоговая статистика
    print("\n" + "=" * 60)
    print("SUMMARY:")
    print("=" * 60)
    logger.info(f"✓ Success: {success}")
    if failed > 0:
        logger.error(f"✗ Failed: {failed}")
    if skipped > 0:
        logger.warning(f"⊘ Skipped (limit): {skipped}")
    logger.info(f"Total: {len(video_files)}")

    # Сообщение о лимите
    if uploader.rate_limit.reached and uploader.rate_limit.delay > 0:
        print("=" * 60)
        logger.warning("⚠ GIF LIMIT REACHED")
        print("=" * 60)
        logger.warning(f"Can upload more in: {format_time(uploader.rate_limit.delay)}")
        print("=" * 60)

    # Детальная информация
    print("=" * 60)
    print("Details:")
    print("=" * 60)
    for result in results:
        if isinstance(result, tuple):
            filename, status = result
            if status.startswith("✓"):
                logger.info(f"{filename}: {status}")
            elif "SKIPPED" in status:
                logger.warning(f"{filename}: {status}")
            else:
                logger.error(f"{filename}: {status}")
        else:
            logger.error(f"Exception: {result}")
    print("=" * 60)

    # Сохранение результатов в файл
    try:
        results_file = ResultsSaver.save_results(results)
        logger.info(f"Results saved: {results_file}")
    except Exception as e:
        logger.error(f"Failed to save results: {e}")

    input("\nPress Enter to exit...")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("\nInterrupted by user")
    except Exception as e:
        logger.error(f"Critical error: {e}", exc_info=True)
        input("Press Enter to exit...")
