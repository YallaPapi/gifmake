"""
Upload Bridge - Simplified upload API for GUI integration with RedGIFs uploader.

Provides a clean interface between the GifMake GUI and the RedGIFs uploader module.
"""

import asyncio
import logging
import sys
import os
import requests
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Add redgifs directory to path so redgifs_core package can be found
_redgifs_path = str(Path(__file__).parent / "redgifs")
if _redgifs_path not in sys.path:
    sys.path.insert(0, _redgifs_path)

# Import from the redgifs_core package (renamed from src to avoid conflicts)
from redgifs_core.account_manager import AccountManager, Account
from redgifs_core.api_client import RedGifsAPIClient
from redgifs_core.uploader import VideoUploader
from browser_uploader import RedGifsBrowserUploader

import aiohttp


class UploadBridge:
    """Bridge between GUI and RedGIFs uploader"""

    def __init__(
        self,
        account_name: str,
        override_settings: Optional[Dict[str, Any]] = None,
        adspower_profile_id: Optional[str] = None
    ):
        """
        Initialize upload bridge with account settings.

        Args:
            account_name: Account name from accounts.json
            override_settings: Dict with keys: tags, description, content_type,
                             sexuality, niches, keep_audio

        Raises:
            ValueError: If account not found
            FileNotFoundError: If accounts.json not found
        """
        # Load account from accounts.json in redgifs directory
        accounts_file = Path(__file__).parent / "redgifs" / "accounts.json"
        manager = AccountManager(accounts_file)
        self.account = manager.get_account_by_name(account_name)

        if not self.account:
            available = [acc.name for acc in manager.accounts]
            raise ValueError(
                f"Account '{account_name}' not found. "
                f"Available accounts: {', '.join(available) if available else 'none'}"
            )

        # Apply overrides if provided
        if override_settings:
            if "tags" in override_settings:
                self.account.tags = override_settings["tags"]
            if "description" in override_settings:
                self.account.description = override_settings["description"]
            if "content_type" in override_settings:
                self.account.content_type = override_settings["content_type"]
            if "sexuality" in override_settings:
                self.account.sexuality = override_settings["sexuality"]
            if "niches" in override_settings:
                self.account.niches = override_settings["niches"]
            if "keep_audio" in override_settings:
                self.account.keep_audio = override_settings["keep_audio"]

        # Explicit uploader proxies are disabled by design.
        # AdsPower profile networking is used only for browser/token refresh.
        self.account.proxy = ""
        self.account.proxy_rotation_url = ""
        self.adspower_profile_id = (adspower_profile_id or "").strip()
        self._browser_uploader: Optional[RedGifsBrowserUploader] = None

    def _get_browser_uploader(self) -> RedGifsBrowserUploader:
        if not self.adspower_profile_id:
            raise RuntimeError("Browser uploader requested without AdsPower profile ID")
        if self._browser_uploader is None:
            self._browser_uploader = RedGifsBrowserUploader(
                profile_id=self.adspower_profile_id,
                account_name=self.account.name,
                config_path=Path(__file__).parent / "redgifs" / "adspower_config.json",
            )
        return self._browser_uploader

    def _apply_adspower_proxy(self, profile_id: str) -> None:
        """
        Load proxy settings from AdsPower profile and apply them to this upload account.
        This ensures GUI-selected browser profile proxy takes precedence over stale accounts.json proxy.
        """
        cfg_path = Path(__file__).parent / "redgifs" / "adspower_config.json"
        api_base = "http://127.0.0.1:50325"
        api_key = ""
        try:
            if cfg_path.exists():
                import json
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                api_base = (cfg.get("adspower_api_base") or api_base).rstrip("/")
                api_key = (cfg.get("api_key") or "").strip()
        except Exception as e:
            logger.warning(f"[{self.account.name}] Could not read adspower_config.json: {e}")

        try:
            proxy_str = self._fetch_adspower_profile_proxy(api_base, api_key, profile_id)
            if proxy_str:
                self.account.proxy = proxy_str
                # Rotation URL from accounts.json is unrelated to AdsPower profile proxies.
                self.account.proxy_rotation_url = ""
                logger.info(
                    f"[{self.account.name}] Using AdsPower proxy from profile {profile_id}: "
                    f"{proxy_str.split(':')[0]}:{proxy_str.split(':')[1]}"
                )
            else:
                self.account.proxy = ""
                self.account.proxy_rotation_url = ""
                logger.warning(
                    f"[{self.account.name}] No AdsPower proxy found for profile {profile_id}; "
                    "disabling proxy for this upload run"
                )
        except Exception as e:
            self.account.proxy = ""
            self.account.proxy_rotation_url = ""
            logger.warning(
                f"[{self.account.name}] Failed to fetch AdsPower proxy for {profile_id}: {e}; "
                "disabling proxy for this upload run"
            )

    @staticmethod
    def _fetch_adspower_profile_proxy(api_base: str, api_key: str, profile_id: str) -> Optional[str]:
        """Query AdsPower user list and return proxy string as host:port:user:pass for the profile."""
        def _proxy_from_item(item: Dict[str, Any]) -> Optional[str]:
            p = (item.get("user_proxy_config") or {})
            host = (p.get("proxy_host") or "").strip()
            port = str(p.get("proxy_port") or "").strip()
            user = (p.get("proxy_user") or "").strip()
            password = (p.get("proxy_password") or "").strip()
            proxy_type = (p.get("proxy_type") or "").strip().lower()
            scheme = "http"
            if proxy_type.startswith("socks5"):
                scheme = "socks5"
            elif proxy_type.startswith("socks4"):
                scheme = "socks4"
            elif proxy_type in {"http", "https"}:
                scheme = proxy_type
            if host and port and user and password:
                return f"{scheme}://{host}:{port}:{user}:{password}"
            return None

        # 1) Direct lookup by user_id (more reliable for profiles not returned in generic pagination).
        direct_params = {"user_id": profile_id}
        if api_key:
            direct_params["api_key"] = api_key
        resp = requests.get(f"{api_base}/api/v1/user/list", params=direct_params, timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(f"AdsPower API HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        direct_items = ((data.get("data") or {}).get("list") or [])
        if direct_items:
            proxy = _proxy_from_item(direct_items[0])
            if proxy:
                return proxy

        # 2) Fallback: paginate full list and scan by user_id.
        page = 1
        while page <= 20:
            params = {"page": page, "page_size": 100, "group_id": 0}
            if api_key:
                params["api_key"] = api_key
            resp = requests.get(f"{api_base}/api/v1/user/list", params=params, timeout=20)
            if resp.status_code != 200:
                raise RuntimeError(f"AdsPower API HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            items = ((data.get("data") or {}).get("list") or [])
            if not items:
                break
            for item in items:
                if str(item.get("user_id", "")).strip() != str(profile_id).strip():
                    continue
                return _proxy_from_item(item)
            page += 1
        return None

    async def _rotate_proxy(self) -> bool:
        """
        Rotate proxy IP by calling the rotation URL.

        Only rotates if account has proxy_rotation_url configured.
        Failures are logged as warnings but do not prevent upload attempts.

        Returns:
            True if rotation succeeded or not configured, False on failure
        """
        if not self.account.proxy_rotation_url:
            return True  # No rotation URL configured, nothing to do

        logger.info(f"[{self.account.name}] Rotating proxy IP...")

        try:
            resolver = aiohttp.resolver.ThreadedResolver()
            connector = aiohttp.TCPConnector(resolver=resolver)
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.get(self.account.proxy_rotation_url) as response:
                    status = response.status
                    if status == 200:
                        logger.info(f"[{self.account.name}] Proxy IP rotated successfully")
                        return True
                    else:
                        logger.warning(
                            f"[{self.account.name}] Proxy rotation failed: HTTP {status} - "
                            "will attempt upload anyway"
                        )
                        return False
        except aiohttp.ClientError as e:
            logger.warning(
                f"[{self.account.name}] Proxy rotation network error: {e} - "
                "will attempt upload anyway"
            )
            return False
        except Exception as e:
            logger.warning(
                f"[{self.account.name}] Proxy rotation error: {e} - "
                "will attempt upload anyway"
            )
            return False

    async def upload_single_file(
        self,
        file_path: str,
        index: int = 1,
        total: int = 1
    ) -> Dict[str, Any]:
        """
        Upload single file to RedGIFs.

        Args:
            file_path: Path to the video/GIF file to upload
            index: Current file index (for progress display)
            total: Total number of files (for progress display)

        Returns:
            Dict with keys:
                - success: bool - whether upload succeeded
                - url: str or None - RedGIFs watch URL if successful
                - error: str or None - error message if failed
                - filename: str - name of the uploaded file
        """
        filename = Path(file_path).name

        # Validate file exists
        if not Path(file_path).exists():
            return {
                "success": False,
                "url": None,
                "error": f"File not found: {file_path}",
                "filename": filename
            }

        # If a concrete AdsPower profile is selected, run full browser automation.
        if self.adspower_profile_id:
            # Playwright sync API cannot run inside an active asyncio loop thread.
            return await asyncio.to_thread(
                self.upload_single_file_browser_sync,
                file_path,
                index,
                total,
            )

        # Rotate proxy IP before upload (if configured)
        await self._rotate_proxy()

        first_result = await self._upload_once(file_path, index, total, filename)
        if first_result.get("success"):
            return first_result

        # If proxy is configured and first attempt failed with proxy-network errors,
        # retry once without proxy to avoid hard-failing on dead proxy endpoints.
        if self.account.proxy and self._is_proxy_error(first_result.get("error")):
            logger.warning(
                f"[{self.account.name}] Upload failed via proxy, retrying once without proxy"
            )
            original_proxy = self.account.proxy
            try:
                self.account.proxy = ""
                retry_result = await self._upload_once(file_path, index, total, filename)
                # Keep proxy disabled for this runtime if retry succeeded, otherwise restore.
                if retry_result.get("success"):
                    logger.warning(
                        f"[{self.account.name}] Upload succeeded without proxy; "
                        "keeping proxy disabled for remaining uploads in this run"
                    )
                    return retry_result
                self.account.proxy = original_proxy
                return retry_result
            except Exception:
                self.account.proxy = original_proxy
                raise

        return first_result

    async def _upload_once(
        self,
        file_path: str,
        index: int,
        total: int,
        filename: str
    ) -> Dict[str, Any]:
        """Run one upload attempt with current account settings."""
        try:
            api_client = RedGifsAPIClient(self.account)
            uploader = VideoUploader(self.account, api_client)

            resolver = aiohttp.resolver.ThreadedResolver()
            connector = aiohttp.TCPConnector(resolver=resolver)

            async with aiohttp.ClientSession(connector=connector) as session:
                returned_filename, status = await uploader.upload_video(session, file_path, index, total)

            if "redgifs.com/watch" in status:
                return {
                    "success": True,
                    "url": status,
                    "error": None,
                    "filename": returned_filename
                }
            return {
                "success": False,
                "url": None,
                "error": status,
                "filename": returned_filename
            }
        except Exception as e:
            return {
                "success": False,
                "url": None,
                "error": str(e),
                "filename": filename
            }

    @staticmethod
    def _is_proxy_error(error_text: Optional[str]) -> bool:
        """Best-effort detection for proxy connectivity failures."""
        if not error_text:
            return False
        text = str(error_text).lower()
        markers = [
            "proxy",
            "clientproxyconnectionerror",
            "cannot connect to host",
            "connection refused",
            "expected http/",
            "expected http/, rtsp/ or ice/",
            "socks5://",
            "socks4://",
        ]
        return any(m in text for m in markers)

    def upload_single_file_sync(
        self,
        file_path: str,
        index: int = 1,
        total: int = 1
    ) -> Dict[str, Any]:
        """
        Synchronous wrapper for upload_single_file.
        Convenience method for non-async callers.

        Args:
            file_path: Path to the video/GIF file to upload
            index: Current file index (for progress display)
            total: Total number of files (for progress display)

        Returns:
            Dict with keys: success, url, error, filename
        """
        return asyncio.run(self.upload_single_file(file_path, index, total))

    def upload_single_file_browser_sync(
        self,
        file_path: str,
        index: int = 1,
        total: int = 1,
    ) -> Dict[str, Any]:
        """
        Synchronous browser upload path.
        Intended for GUI worker threads when AdsPower profile is selected.
        """
        if not self.adspower_profile_id:
            raise RuntimeError("Browser sync upload requires adspower_profile_id")
        browser_uploader = self._get_browser_uploader()
        return browser_uploader.upload_file(
            file_path=file_path,
            tags=self.account.tags,
            description=self.account.description,
            content_type=self.account.content_type,
            keep_audio=self.account.keep_audio,
            index=index,
            total=total,
        )

    def close(self) -> None:
        """Release background resources (browser connections, etc.)."""
        if self._browser_uploader is not None:
            self._browser_uploader.close(stop_profile=False)
            self._browser_uploader = None

    @staticmethod
    def get_available_accounts() -> List[str]:
        """
        Get list of available account names from accounts.json.

        Returns:
            List of account names
        """
        try:
            accounts_file = Path(__file__).parent / "redgifs" / "accounts.json"
            manager = AccountManager(accounts_file)
            return [acc.name for acc in manager.accounts]
        except FileNotFoundError:
            return []

    @staticmethod
    def get_enabled_accounts() -> List[str]:
        """
        Get list of enabled account names from accounts.json.

        Returns:
            List of enabled account names
        """
        try:
            accounts_file = Path(__file__).parent / "redgifs" / "accounts.json"
            manager = AccountManager(accounts_file)
            return [acc.name for acc in manager.get_enabled_accounts()]
        except FileNotFoundError:
            return []

    @staticmethod
    def refresh_tokens(
        profile_id: Optional[str] = None,
        account_name: Optional[str] = None
    ) -> bool:
        """
        Refresh all account tokens using AdsPower.

        This opens browser profiles and extracts fresh bearer tokens
        from the RedGIFs network traffic.

        Returns:
            True if refresh succeeded, False otherwise
        """
        try:
            # Change to redgifs directory for relative imports in refresh_tokens
            original_cwd = os.getcwd()
            redgifs_dir = Path(__file__).parent / "redgifs"
            os.chdir(redgifs_dir)

            try:
                # Import and run the refresh script
                from refresh_tokens import main as refresh_main
                result = refresh_main(profile_id=profile_id, account_name=account_name)
                # refresh_main may return bool or None depending version.
                return bool(result) if result is not None else True
            finally:
                os.chdir(original_cwd)

        except FileNotFoundError as e:
            print(f"Token refresh failed - config file not found: {e}")
            return False
        except ImportError as e:
            print(f"Token refresh failed - missing dependencies: {e}")
            return False
        except Exception as e:
            print(f"Token refresh failed: {e}")
            return False
