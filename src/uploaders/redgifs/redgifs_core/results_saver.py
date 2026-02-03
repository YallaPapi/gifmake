"""Модуль для сохранения результатов загрузки в файл"""

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional


class ResultsSaver:
    """Класс для сохранения результатов загрузки"""

    @staticmethod
    def save_results(results: List[Tuple], output_dir: Path = None, prefix: str = "") -> str:
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
            prefix: Префикс для имени файла (например account_name_)

        Returns:
            Путь к созданному файлу
        """
        if output_dir is None:
            output_dir = Path.cwd()

        # Генерация имени файла с датой и временем
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{prefix}results_{timestamp}.txt"
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

    @staticmethod
    def append_to_csv(
        title: str,
        redgifs_url: str,
        account_name: str,
        output_dir: Optional[Path] = None
    ) -> str:
        """
        Append a single upload result to a CSV file.

        Creates the file with headers if it doesn't exist, otherwise appends.
        File naming: {account_name}_uploads_{date}.csv

        Args:
            title: Video title (usually the filename without extension)
            redgifs_url: The RedGIFs URL for the uploaded video
            account_name: Name of the account that uploaded the video
            output_dir: Directory to save CSV (default: current working directory)

        Returns:
            Path to the CSV file
        """
        if output_dir is None:
            output_dir = Path.cwd()

        # Generate filename with date only (not time) so we append to same file all day
        date_str = datetime.now().strftime("%Y-%m-%d")
        csv_filename = f"{account_name}_uploads_{date_str}.csv"
        csv_path = output_dir / csv_filename

        # Check if file exists to determine if we need headers
        file_exists = csv_path.exists()

        # Get current timestamp for the row
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Open in append mode
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)

            # Write header if new file
            if not file_exists:
                writer.writerow(['title', 'redgifs_url', 'account_name', 'timestamp'])

            # Write the data row
            writer.writerow([title, redgifs_url, account_name, timestamp])

        return str(csv_path)

    @staticmethod
    def save_results_to_csv(
        results: List[Tuple],
        account_name: str,
        output_dir: Optional[Path] = None
    ) -> Optional[str]:
        """
        Save all successful upload results to a CSV file.

        This is a batch method that processes multiple results at once.
        Only successful uploads (with valid RedGIFs URLs) are saved.

        Args:
            results: List of (filename, status) tuples from upload
            account_name: Name of the account
            output_dir: Directory to save CSV (default: current working directory)

        Returns:
            Path to CSV file if any successful uploads, None otherwise
        """
        if output_dir is None:
            output_dir = Path.cwd()

        csv_path = None

        for result in results:
            if not isinstance(result, tuple) or len(result) != 2:
                continue

            filename, status = result

            # Only process successful uploads
            if status.startswith("✓") and "redgifs.com/watch" in status:
                url = status.replace("✓ ", "").strip()
                # Use filename without extension as title
                title = Path(filename).stem

                csv_path = ResultsSaver.append_to_csv(
                    title=title,
                    redgifs_url=url,
                    account_name=account_name,
                    output_dir=output_dir
                )

        return csv_path
