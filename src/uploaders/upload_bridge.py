"""
Upload Bridge - Simplified upload API for GUI integration with RedGIFs uploader.

Provides a clean interface between the GifMake GUI and the RedGIFs uploader module.
"""

import asyncio
import sys
import os
from pathlib import Path
from typing import Dict, Any, List, Optional

# Add redgifs directory to path so redgifs_core package can be found
_redgifs_path = str(Path(__file__).parent / "redgifs")
if _redgifs_path not in sys.path:
    sys.path.insert(0, _redgifs_path)

# Import from the redgifs_core package (renamed from src to avoid conflicts)
from redgifs_core.account_manager import AccountManager, Account
from redgifs_core.api_client import RedGifsAPIClient
from redgifs_core.uploader import VideoUploader

import aiohttp


class UploadBridge:
    """Bridge between GUI and RedGIFs uploader"""

    def __init__(self, account_name: str, override_settings: Optional[Dict[str, Any]] = None):
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

        try:
            api_client = RedGifsAPIClient(self.account)
            uploader = VideoUploader(self.account, api_client)

            # Use ThreadedResolver to avoid Windows DNS issues
            resolver = aiohttp.resolver.ThreadedResolver()
            connector = aiohttp.TCPConnector(resolver=resolver)

            async with aiohttp.ClientSession(connector=connector) as session:
                result = await uploader.upload_video(session, file_path, index, total)
                returned_filename, status = result

                # Check if upload was successful (contains RedGIFs URL)
                if "redgifs.com/watch" in status:
                    return {
                        "success": True,
                        "url": status,
                        "error": None,
                        "filename": returned_filename
                    }
                else:
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
    def refresh_tokens() -> bool:
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
                refresh_main()
                return True
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
