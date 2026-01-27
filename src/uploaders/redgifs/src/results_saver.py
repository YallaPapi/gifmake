"""Модуль для сохранения результатов загрузки в файл"""

from datetime import datetime
from pathlib import Path
from typing import List, Tuple


class ResultsSaver:
    """Класс для сохранения результатов загрузки"""

    @staticmethod
    def save_results(results: List[Tuple], output_dir: Path = None) -> str:
        """
        Сохранение результатов в TXT файл (формат для парсинга)

        Формат файла:
        STATUS|FILENAME|URL_OR_ERROR

        STATUS: SUCCESS, FAILED, SKIPPED
        FILENAME: имя файла
        URL_OR_ERROR: URL для успешных, текст ошибки для остальных

        Args:
            results: Список результатов загрузки
            output_dir: Директория для сохранения

        Returns:
            Путь к созданному файлу
        """
        if output_dir is None:
            output_dir = Path.cwd()

        # Генерация имени файла с датой и временем
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"results_{timestamp}.txt"
        filepath = output_dir / filename

        lines = []

        # Заголовок с датой
        lines.append(f"# RedGifs Upload Results - {timestamp}")
        lines.append(f"# Format: STATUS|FILENAME|URL_OR_ERROR")
        lines.append("")

        # Обработка результатов
        for result in results:
            if not isinstance(result, tuple) or len(result) != 2:
                lines.append(f"FAILED|unknown|Invalid result format: {result}")
                continue

            filename, status = result

            # Успешная загрузка
            if status.startswith("✓"):
                url = status.replace("✓ ", "").strip()
                lines.append(f"SUCCESS|{filename}|{url}")

            # Пропущено из-за лимита
            elif "SKIPPED" in status:
                error = status.replace("✗ ", "").strip()
                lines.append(f"SKIPPED|{filename}|{error}")

            # Ошибка
            else:
                error = status.replace("✗ ", "").strip()
                lines.append(f"FAILED|{filename}|{error}")

        # Сохранение
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        return str(filepath)
