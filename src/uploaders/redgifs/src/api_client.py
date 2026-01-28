"""Клиент для работы с API RedGifs"""

import asyncio
import json
from typing import Optional, Dict, Any, Union

import aiohttp

from .config import Config
from .account_manager import Account
from .logger import get_logger

logger = get_logger()


class RedGifsAPIClient:
    """Клиент для асинхронной работы с API RedGifs"""

    def __init__(self, config: Union[Config, Account]):
        self.config = config
        self.timeout_short = aiohttp.ClientTimeout(total=10)
        self.timeout_long = aiohttp.ClientTimeout(total=600)  # 10 минут для больших файлов

    async def _request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[aiohttp.ClientTimeout] = None,
        retries: int = 3
    ) -> Dict[str, Any]:
        """
        Универсальный метод для HTTP запросов

        Args:
            session: aiohttp сессия
            method: HTTP метод (GET, POST, PATCH)
            url: URL для запроса
            json_data: JSON данные для отправки
            headers: Дополнительные заголовки
            timeout: Таймаут запроса
            retries: Количество повторных попыток

        Returns:
            Распарсенный JSON ответ
        """
        if timeout is None:
            timeout = self.timeout_short

        request_headers = self.config.get_headers()
        if headers:
            request_headers.update(headers)

        for attempt in range(retries):
            try:
                async with session.request(
                    method,
                    url,
                    json=json_data,
                    headers=request_headers,
                    timeout=timeout,
                    proxy=self.config.proxy_url
                ) as resp:
                    text = await resp.text()

                    # Rate limit обработка
                    if resp.status == 429:
                        try:
                            data = json.loads(text)
                            return {
                                "error": data.get("error", {}),
                                "status": 429,
                                "_raw": text
                            }
                        except json.JSONDecodeError:
                            return {
                                "error": {"message": "Rate limit"},
                                "status": 429,
                                "_raw": text
                            }

                    # Логирование ошибок
                    if resp.status != 200:
                        logger.debug(f"HTTP {resp.status}: {text[:300]}")

                    resp.raise_for_status()

                    # Парсинг JSON
                    if text:
                        try:
                            return json.loads(text)
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON parsing error: {e}")
                            return {}
                    return {}

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < retries - 1:
                    logger.debug(f"Retry {attempt + 1}/{retries}: {str(e)[:100]}")
                    await asyncio.sleep(3)
                else:
                    logger.error(f"Request failed after {retries} attempts: {e}")
                    raise

    async def api_post(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        data: Dict[str, Any],
        retries: int = 3
    ) -> Dict[str, Any]:
        """
        POST запрос к API

        Args:
            session: aiohttp сессия
            endpoint: API endpoint (например /v2/upload)
            data: Данные для отправки
            retries: Количество попыток

        Returns:
            Ответ API
        """
        url = f"{self.config.api_base}{endpoint}"
        return await self._request(session, "POST", url, json_data=data, retries=retries)

    async def api_get(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        retries: int = 3
    ) -> Dict[str, Any]:
        """
        GET запрос к API

        Args:
            session: aiohttp сессия
            endpoint: API endpoint
            retries: Количество попыток

        Returns:
            Ответ API
        """
        url = f"{self.config.api_base}{endpoint}"
        return await self._request(session, "GET", url, retries=retries)

    async def api_patch(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        data: Dict[str, Any],
        retries: int = 3
    ) -> Dict[str, Any]:
        """
        PATCH запрос к API

        Args:
            session: aiohttp сессия
            endpoint: API endpoint
            data: Данные для обновления
            retries: Количество попыток

        Returns:
            Ответ API
        """
        url = f"{self.config.api_base}{endpoint}"
        return await self._request(session, "PATCH", url, json_data=data, retries=retries)

    async def s3_put(
        self,
        session: aiohttp.ClientSession,
        url: str,
        data: bytes,
        content_type: str,
        retries: int = 10
    ) -> None:
        """
        PUT запрос на S3 для загрузки файла

        Args:
            session: aiohttp сессия
            url: Presigned S3 URL
            data: Байты файла
            content_type: MIME тип
            retries: Количество попыток
        """
        # Экспоненциальная задержка: 30с, 60с, 120с, 240с, 240с...
        delay_schedule = [30, 60, 120, 240]

        for attempt in range(retries):
            try:
                async with session.put(
                    url,
                    data=data,
                    headers={"Content-Type": content_type},
                    timeout=self.timeout_long,
                    proxy=self.config.proxy_url
                ) as resp:
                    resp.raise_for_status()
                    return
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < retries - 1:
                    # Вычисляем задержку
                    if attempt < len(delay_schedule):
                        delay = delay_schedule[attempt]
                    else:
                        delay = delay_schedule[-1]  # 240 секунд для всех последующих

                    logger.warning(f"S3 PUT retry {attempt + 1}/{retries}: {e}")
                    logger.info(f"Retry in {delay} sec...")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"S3 upload failed after {retries} attempts: {e}")
                    raise
