"""Модуль загрузки и валидации конфигурации из .env файла"""

import os
from pathlib import Path
from urllib.parse import quote
from typing import Optional


class Config:
    """Конфигурация приложения"""

    def __init__(self):
        self._load_env()
        self._validate()

    def _load_env(self):
        """Загрузка переменных из .env файла"""
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key.strip()] = value.strip()

    def _validate(self):
        """Валидация обязательных параметров"""
        if not self.bearer_token or self.bearer_token == "your_token_here":
            raise ValueError(
                "REDGIFS_TOKEN не настроен!\n"
                "Создай .env файл с REDGIFS_TOKEN"
            )

    @property
    def bearer_token(self) -> str:
        """Bearer токен для API RedGifs"""
        return os.environ.get("REDGIFS_TOKEN", "").strip()

    @property
    def user_agent(self) -> str:
        """User-Agent для HTTP запросов"""
        default = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
        return os.environ.get("USER_AGENT", default).strip()

    @property
    def tags(self) -> list[str]:
        """Теги для загружаемых видео"""
        tags_string = os.environ.get("TAGS", "Amateur,Ass,Big Tits").strip()
        return [tag.strip() for tag in tags_string.split(',') if tag.strip()]

    @property
    def threads(self) -> int:
        """Количество параллельных потоков"""
        try:
            return int(os.environ.get("THREADS", "3").strip())
        except ValueError:
            return 3

    @property
    def proxy_url(self) -> Optional[str]:
        """URL прокси в формате http://user:pass@ip:port"""
        proxy_string = os.environ.get("PROXY", "").strip()
        if not proxy_string:
            return None

        try:
            parts = proxy_string.split(':')
            if len(parts) == 4:
                ip, port, user, password = parts
                return f"http://{quote(user)}:{quote(password)}@{ip}:{port}"
        except Exception:
            pass

        return None

    @property
    def keep_audio(self) -> bool:
        """Сохранять ли аудио в видео"""
        value = os.environ.get("KEEP_AUDIO", "false").strip().lower()
        return value in ("true", "1", "yes")

    @property
    def description(self) -> str:
        """Description/caption for uploaded videos"""
        return os.environ.get("DESCRIPTION", "").strip()

    @property
    def content_type(self) -> str:
        """Content type (Solo Female, Solo Male, Couple, etc.)"""
        return os.environ.get("CONTENT_TYPE", "Solo Female").strip()

    @property
    def sexuality(self) -> str:
        """Sexuality/orientation (straight, gay, lesbian, etc.)"""
        return os.environ.get("SEXUALITY", "straight").strip()

    @property
    def niches(self) -> list[str]:
        """Niches/categories for uploaded videos"""
        niches_string = os.environ.get("NICHES", "").strip()
        if not niches_string:
            return []
        return [niche.strip() for niche in niches_string.split(',') if niche.strip()]

    @property
    def api_base(self) -> str:
        """Базовый URL API"""
        return "https://api.redgifs.com"

    def get_headers(self) -> dict:
        """Получить заголовки для API запросов"""
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
            "Origin": "https://www.redgifs.com",
            "Referer": "https://www.redgifs.com/",
            "User-Agent": self.user_agent,
        }
