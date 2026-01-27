"""Вспомогательные утилиты для работы с файлами и форматирования"""

import hashlib
import subprocess
from pathlib import Path
from typing import Optional

from .logger import get_logger

logger = get_logger()


def check_ffprobe_installed() -> bool:
    """
    Проверка установлен ли ffprobe

    Returns:
        True если ffprobe доступен, False иначе
    """
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def calculate_md5(filepath: str) -> str:
    """
    Вычисление MD5 хеша файла

    Args:
        filepath: Путь к файлу

    Returns:
        MD5 хеш в виде строки
    """
    md5_hash = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()
    except (OSError, IOError) as e:
        logger.error(f"Error reading file {filepath}: {e}")
        raise


def get_mime_type(filepath: str) -> str:
    """
    Определение MIME типа по расширению файла

    Args:
        filepath: Путь к файлу

    Returns:
        MIME тип
    """
    ext = Path(filepath).suffix.lower()
    mime_map = {
        '.mp4': 'video/mp4',
        '.mov': 'video/quicktime',
        '.avi': 'video/x-msvideo',
        '.wmv': 'video/x-ms-wmv',
        '.mpg': 'video/mpeg',
        '.mpeg': 'video/mpeg',
        '.m4v': 'video/x-m4v',
        '.webm': 'video/webm',
        '.ogv': 'video/ogg',
        '.ogm': 'video/ogg'
    }
    return mime_map.get(ext, 'video/mp4')


def get_video_duration(filepath: str) -> float:
    """
    Получение длительности видео через ffprobe

    Args:
        filepath: Путь к видео файлу

    Returns:
        Длительность в секундах

    Raises:
        RuntimeError: Если ffprobe не установлен
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10
        )
        return float(result.stdout.strip())
    except FileNotFoundError:
        logger.error("ffprobe not found in system!")
        logger.error("Install FFmpeg:")
        logger.error("  1. winget install FFmpeg")
        logger.error("  2. Or download from https://ffmpeg.org/download.html")
        raise RuntimeError(
            "ffprobe is not installed. Install FFmpeg to get video duration."
        )
    except (subprocess.TimeoutExpired, ValueError, OSError) as e:
        logger.error(f"Error running ffprobe: {e}")
        raise


def format_time(seconds: int) -> str:
    """
    Конвертация секунд в читаемый формат

    Args:
        seconds: Количество секунд

    Returns:
        Отформатированная строка (например "2 ч 30 мин")
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)

    if hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


def find_video_files(directory: Path) -> list[Path]:
    """
    Поиск всех видео файлов в директории

    Args:
        directory: Директория для поиска

    Returns:
        Список путей к видео файлам
    """
    extensions = ['mp4', 'mov', 'avi', 'wmv', 'mpg', 'mpeg',
                  'm4v', 'webm', 'ogv', 'ogm']

    video_files = set()
    for ext in extensions:
        video_files.update(directory.glob(f"*.{ext}"))
        video_files.update(directory.glob(f"*.{ext.upper()}"))

    return sorted(video_files)
