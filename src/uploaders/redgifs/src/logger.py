"""Модуль цветного логирования с использованием colorlog"""

import logging
import random
import colorlog


# Цвета для потоков (яркие, хорошо видимые)
THREAD_COLORS = [
    'cyan', 'light_cyan', 'blue', 'light_blue',
    'purple', 'light_purple', 'magenta', 'light_magenta',
    'green', 'light_green', 'yellow', 'light_yellow',
    'red', 'light_red', 'white'
]

# Словарь для хранения цветов потоков
_thread_colors = {}


def get_thread_color(thread_id: int) -> str:
    """
    Получить цвет для конкретного потока

    Args:
        thread_id: Номер потока

    Returns:
        Название цвета для colorlog
    """
    if thread_id not in _thread_colors:
        # Перемешиваем цвета для разнообразия
        available_colors = THREAD_COLORS.copy()
        random.shuffle(available_colors)
        _thread_colors[thread_id] = available_colors[thread_id % len(available_colors)]

    return _thread_colors[thread_id]


def colorize_thread_id(message: str) -> str:
    """
    Раскрашивает номер потока в сообщении

    Args:
        message: Сообщение с [Thread X] или [Thread X/Y]

    Returns:
        Сообщение с ANSI цветами
    """
    import re

    # Search for pattern [Thread X] or [Thread X/Y]
    pattern = r'\[Thread (\d+)(?:/\d+)?\]'
    match = re.search(pattern, message)

    if match:
        thread_id = int(match.group(1))
        color = get_thread_color(thread_id)

        # ANSI коды цветов
        color_codes = {
            'cyan': '\033[36m',
            'light_cyan': '\033[96m',
            'blue': '\033[34m',
            'light_blue': '\033[94m',
            'purple': '\033[35m',
            'light_purple': '\033[95m',
            'magenta': '\033[35m',
            'light_magenta': '\033[95m',
            'green': '\033[32m',
            'light_green': '\033[92m',
            'yellow': '\033[33m',
            'light_yellow': '\033[93m',
            'red': '\033[31m',
            'light_red': '\033[91m',
            'white': '\033[97m'
        }

        color_code = color_codes.get(color, '\033[97m')
        reset_code = '\033[0m'

        # Replace [Thread X] with colored version
        colored_thread = f"{color_code}{match.group(0)}{reset_code}"
        message = message.replace(match.group(0), colored_thread)

    return message


class ColoredThreadFormatter(colorlog.ColoredFormatter):
    """Форматтер с поддержкой цветных номеров потоков"""

    def format(self, record):
        # Сначала применяем стандартное форматирование
        result = super().format(record)
        # Затем раскрашиваем номер потока
        return colorize_thread_id(result)


def setup_logger(name: str = "redgifs_uploader", level: int = logging.INFO) -> logging.Logger:
    """
    Настройка цветного логгера

    Args:
        name: Имя логгера
        level: Уровень логирования

    Returns:
        Настроенный логгер
    """
    logger = logging.getLogger(name)

    # Если логгер уже настроен - возвращаем его
    if logger.handlers:
        return logger

    logger.setLevel(level)

    # Создаем обработчик для консоли
    console_handler = colorlog.StreamHandler()
    console_handler.setLevel(level)

    # Формат с цветами
    formatter = ColoredThreadFormatter(
        fmt='%(log_color)s%(levelname)-8s%(reset)s %(white)s%(message)s',
        datefmt=None,
        reset=True,
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white',
        },
        secondary_log_colors={},
        style='%'
    )

    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str = "redgifs_uploader") -> logging.Logger:
    """
    Получить существующий логгер или создать новый

    Args:
        name: Имя логгера

    Returns:
        Логгер
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger
