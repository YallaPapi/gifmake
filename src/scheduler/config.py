"""Load scheduler configuration from JSON."""

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


@dataclass
class Source:
    type: str  # 'local' or 'gdrive'
    path: str  # folder path or drive folder id
    account: str


@dataclass
class Config:
    posts_per_day: int
    schedule_mode: str  # 'spread' or 'batch'
    batch_times: list[str]
    active_hours_start: str
    active_hours_end: str
    sources: list[Source]
    retry_max: int
    retry_backoff_minutes: list[int]
    database_path: str


def load_config(config_path: str = None) -> Config:
    """Load config from JSON file."""
    if config_path is None:
        # Look in common locations
        locations = [
            Path("scheduler_config.json"),
            Path(__file__).parent / "scheduler_config.json",
            Path(__file__).parent.parent / "scheduler_config.json",
            Path(__file__).parent.parent.parent / "scheduler_config.json",
        ]
        for loc in locations:
            if loc.exists():
                config_path = loc
                break
        else:
            raise FileNotFoundError("scheduler_config.json not found")

    with open(config_path, "r") as f:
        data = json.load(f)

    sources = [
        Source(type=s["type"], path=s["path"], account=s["account"])
        for s in data.get("sources", [])
    ]

    return Config(
        posts_per_day=data.get("posts_per_day", 20),
        schedule_mode=data.get("schedule_mode", "spread"),
        batch_times=data.get("batch_times", ["09:00", "15:00", "21:00"]),
        active_hours_start=data.get("active_hours", {}).get("start", "08:00"),
        active_hours_end=data.get("active_hours", {}).get("end", "23:00"),
        sources=sources,
        retry_max=data.get("retry_max", 3),
        retry_backoff_minutes=data.get("retry_backoff_minutes", [5, 30, 120]),
        database_path=data.get("database_path", "scheduler.db"),
    )
