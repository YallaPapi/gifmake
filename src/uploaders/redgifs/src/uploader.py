"""Модуль загрузки видео на RedGifs"""

import asyncio
from pathlib import Path
from typing import Tuple, Optional

import aiohttp

from .config import Config
from .api_client import RedGifsAPIClient
from .utils import calculate_md5, get_mime_type, get_video_duration, format_time
from .logger import get_logger

logger = get_logger()


class RateLimitState:
    """Состояние для отслеживания rate limit"""

    def __init__(self):
        self.reached = False
        self.delay = 0


class VideoUploader:
    """Загрузчик видео на RedGifs"""

    def __init__(self, config: Config, api_client: RedGifsAPIClient):
        self.config = config
        self.api_client = api_client
        self.rate_limit = RateLimitState()

    async def upload_video(
        self,
        session: aiohttp.ClientSession,
        filepath: str,
        index: int,
        total: int
    ) -> Tuple[str, str]:
        """
        Асинхронная загрузка одного видео

        Args:
            session: aiohttp сессия
            filepath: Путь к видео файлу
            index: Номер файла
            total: Всего файлов

        Returns:
            Кортеж (имя_файла, статус)
        """
        filename = Path(filepath).name

        try:
            logger.info(f"[Thread {index}/{total}] {filename}")

            # Шаг 1: Инициализация загрузки
            md5 = calculate_md5(filepath)
            logger.info(f"[Thread {index}] [1/5] MD5: {md5}")

            init_data = {"md5": md5, "type": "gif", "timeline": True}
            upload_info = await self.api_client.api_post(session, "/v2/upload", init_data)

            # Проверка на rate limit
            if upload_info.get("status") == 429:
                return self._handle_rate_limit(upload_info, filename, index)

            if "id" not in upload_info:
                logger.error(f"[Thread {index}] Invalid API response: {upload_info}")
                return filename, "✗ Invalid API response"

            upload_id = upload_info["id"]
            status = upload_info.get("status")
            logger.info(f"[Thread {index}] [1/5] Upload ID: {upload_id} (status: {status})")

            # Шаг 2: Загрузка на S3
            if status == "ready":
                logger.info(f"[Thread {index}] [2/5] Duplicate - skipping upload")
            else:
                await self._upload_to_s3(session, filepath, upload_info, index)
                await self._wait_for_processing(session, init_data, index)

            # Шаг 3: Submit
            gif_id = await self._submit_video(session, filepath, upload_id, index)
            if gif_id is None:
                return filename, "✗ Submit error"

            # Шаг 4: Ожидание encoding
            await self._wait_for_encoding(session, gif_id, index)

            # Шаг 5: Публикация с тегами
            await self._publish_video(session, gif_id, index)

            url = f"https://www.redgifs.com/watch/{gif_id}"
            logger.info(f"[Thread {index}] [5/5] ✓ {url}")

            return filename, f"✓ {url}"

        except Exception as e:
            logger.error(f"[Thread {index}] Error: {type(e).__name__}: {str(e)}")
            return filename, f"✗ {type(e).__name__}: {str(e)[:40]}"

    def _handle_rate_limit(
        self,
        upload_info: dict,
        filename: str,
        index: int
    ) -> Tuple[str, str]:
        """Обработка rate limit"""
        error_data = upload_info.get("error", {})
        error_msg = error_data.get("message", "Rate limit")
        delay = error_data.get("delay", 0)

        self.rate_limit.reached = True
        self.rate_limit.delay = delay

        logger.error(f"[Thread {index}] LIMIT REACHED! {error_msg}")
        if delay > 0:
            time_str = format_time(delay)
            logger.error(f"[Thread {index}] Can upload more in: {time_str}")

        return filename, f"✗ LIMIT (in {format_time(delay) if delay > 0 else '?'})"

    async def _upload_to_s3(
        self,
        session: aiohttp.ClientSession,
        filepath: str,
        upload_info: dict,
        index: int
    ) -> None:
        """Загрузка файла на S3"""
        upload_url = upload_info.get("url")
        if not upload_url:
            raise ValueError("No upload URL provided")

        with open(filepath, "rb") as f:
            file_data = f.read()

        file_size = len(file_data) / (1024 * 1024)
        mime_type = get_mime_type(filepath)
        logger.info(f"[Thread {index}] [2/5] Uploading to S3 ({file_size:.1f} MB)")

        await self.api_client.s3_put(session, upload_url, file_data, mime_type)
        logger.info(f"[Thread {index}] [2/5] ✓ S3 uploaded")

    async def _wait_for_processing(
        self,
        session: aiohttp.ClientSession,
        init_data: dict,
        index: int
    ) -> None:
        """Ожидание готовности после загрузки на S3"""
        logger.info(f"[Thread {index}] [2/5] Waiting for processing...")
        for attempt in range(15):
            await asyncio.sleep(2)
            hb_data = await self.api_client.api_post(session, "/v2/upload", init_data)
            if hb_data.get("status") == "ready":
                logger.info(f"[Thread {index}] [2/5] ✓ Ready")
                break

    async def _submit_video(
        self,
        session: aiohttp.ClientSession,
        filepath: str,
        upload_id: str,
        index: int
    ) -> Optional[str]:
        """Submit видео для финализации"""
        duration = get_video_duration(filepath)
        submit_payload = {
            "ticket": upload_id,
            "tags": self.config.tags,
            "private": False,
            "keepAudio": self.config.keep_audio,
            "description": self.config.description or None,
            "niches": self.config.niches,
            "sexuality": self.config.sexuality,
            "contentType": self.config.content_type,
            "draft": False,
            "cut": {"start": 0, "duration": duration}
        }

        logger.info(f"[Thread {index}] [3/5] Finalizing...")
        submit_result = await self.api_client.api_post(session, "/v2/gifs/submit", submit_payload)

        # Проверка на rate limit
        if submit_result.get("status") == 429:
            self._handle_rate_limit(submit_result, Path(filepath).name, index)
            return None

        if "id" not in submit_result:
            logger.error(f"[Thread {index}] Invalid submit response: {submit_result}")
            return None

        gif_id = submit_result["id"]
        logger.info(f"[Thread {index}] [3/5] ✓ GIF ID: {gif_id}")
        return gif_id

    async def _wait_for_encoding(
        self,
        session: aiohttp.ClientSession,
        gif_id: str,
        index: int
    ) -> None:
        """Ожидание encoding"""
        logger.info(f"[Thread {index}] [4/5] Pause 2 sec...")
        await asyncio.sleep(2)

        try:
            enc_data = await self.api_client.api_get(session, f"/v1/gifs/fetch/status/{gif_id}")
            enc_status = enc_data.get("status", "unknown")
            logger.info(f"[Thread {index}] [4/5] Status: {enc_status}")
        except Exception as e:
            logger.warning(f"[Thread {index}] [4/5] Failed to check status: {e}")

        logger.info(f"[Thread {index}] [4/5] Pause 1 sec...")
        await asyncio.sleep(1)

    async def _publish_video(
        self,
        session: aiohttp.ClientSession,
        gif_id: str,
        index: int
    ) -> None:
        """Публикация видео с тегами"""
        logger.info(f"[Thread {index}] [5/5] Publishing...")
        metadata = {
            "tags": self.config.tags,
            "niches": self.config.niches,
            "sexuality": self.config.sexuality,
            "contentType": self.config.content_type,
            "description": self.config.description,
            "published": True
        }

        await self.api_client.api_patch(session, f"/v2/gifs/{gif_id}", metadata)
