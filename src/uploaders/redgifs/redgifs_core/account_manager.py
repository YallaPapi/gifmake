"""Account manager for multi-account support"""

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote


@dataclass
class Account:
    """Single account configuration"""
    name: str
    token: str
    enabled: bool = True
    proxy: str = ""
    proxy_rotation_url: str = ""
    video_folder: str = "videos"
    tags: list[str] = None
    description: str = ""
    content_type: str = "Solo Female"
    sexuality: str = "straight"
    niches: list[str] = None
    threads: int = 3
    keep_audio: bool = False

    def __post_init__(self):
        if self.tags is None:
            self.tags = ["Amateur", "Ass", "Big Tits"]
        if self.niches is None:
            self.niches = []

    @property
    def proxy_url(self) -> Optional[str]:
        """Convert proxy string to URL format.

        Accepts formats:
        - http://IP:PORT:USERNAME:PASSWORD
        - https://IP:PORT:USERNAME:PASSWORD
        - IP:PORT:USERNAME:PASSWORD

        Returns aiohttp format: http://username:password@ip:port
        """
        if not self.proxy:
            return None

        try:
            proxy_str = self.proxy.strip()

            # Strip http:// or https:// prefix if present
            if proxy_str.startswith("https://"):
                proxy_str = proxy_str[8:]
            elif proxy_str.startswith("http://"):
                proxy_str = proxy_str[7:]

            parts = proxy_str.split(':')
            if len(parts) == 4:
                ip, port, user, password = parts
                return f"http://{quote(user)}:{quote(password)}@{ip}:{port}"
        except Exception:
            pass

        return None

    @property
    def api_base(self) -> str:
        """Base API URL"""
        return "https://api.redgifs.com"

    @property
    def user_agent(self) -> str:
        """User-Agent for HTTP requests"""
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"

    @property
    def bearer_token(self) -> str:
        """Alias for token (compatibility with Config)"""
        return self.token

    def get_headers(self) -> dict:
        """Get headers for API requests"""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Origin": "https://www.redgifs.com",
            "Referer": "https://www.redgifs.com/",
            "User-Agent": self.user_agent,
        }


class AccountManager:
    """Manages multiple accounts from accounts.json"""

    def __init__(self, accounts_file: Optional[Path] = None):
        if accounts_file is None:
            accounts_file = Path(__file__).parent.parent / "accounts.json"
        self.accounts_file = accounts_file
        self.accounts: list[Account] = []
        self._load()

    def _load(self) -> None:
        """Load accounts from JSON file"""
        if not self.accounts_file.exists():
            raise FileNotFoundError(
                f"accounts.json not found at {self.accounts_file}\n"
                "Create accounts.json with your account settings."
            )

        with open(self.accounts_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.accounts = []
        for acc_data in data.get("accounts", []):
            account = Account(
                name=acc_data.get("name", "unnamed"),
                token=acc_data.get("token", ""),
                enabled=acc_data.get("enabled", True),
                proxy=acc_data.get("proxy", ""),
                proxy_rotation_url=acc_data.get("proxy_rotation_url", ""),
                video_folder=acc_data.get("video_folder", "videos"),
                tags=acc_data.get("tags", ["Amateur", "Ass", "Big Tits"]),
                description=acc_data.get("description", ""),
                content_type=acc_data.get("content_type", "Solo Female"),
                sexuality=acc_data.get("sexuality", "straight"),
                niches=acc_data.get("niches", []),
                threads=acc_data.get("threads", 3),
                keep_audio=acc_data.get("keep_audio", False),
            )
            self.accounts.append(account)

    def get_enabled_accounts(self) -> list[Account]:
        """Get list of enabled accounts"""
        return [acc for acc in self.accounts if acc.enabled]

    def get_account_by_name(self, name: str) -> Optional[Account]:
        """Get account by name"""
        for acc in self.accounts:
            if acc.name == name:
                return acc
        return None

    def save(self) -> None:
        """Save accounts back to JSON file"""
        data = {
            "accounts": [
                {
                    "name": acc.name,
                    "enabled": acc.enabled,
                    "token": acc.token,
                    "proxy": acc.proxy,
                    "proxy_rotation_url": acc.proxy_rotation_url,
                    "video_folder": acc.video_folder,
                    "tags": acc.tags,
                    "description": acc.description,
                    "content_type": acc.content_type,
                    "sexuality": acc.sexuality,
                    "niches": acc.niches,
                    "threads": acc.threads,
                    "keep_audio": acc.keep_audio,
                }
                for acc in self.accounts
            ]
        }

        with open(self.accounts_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
