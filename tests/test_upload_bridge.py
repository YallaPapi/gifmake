"""
Unit tests for upload_bridge module
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_upload_bridge_import():
    """Test that UploadBridge can be imported."""
    try:
        from uploaders.upload_bridge import UploadBridge
        print("[PASS] test_upload_bridge_import passed")
    except ImportError as e:
        print(f"[FAIL] test_upload_bridge_import - Import failed: {e}")
        raise


def test_upload_bridge_invalid_account():
    """Test UploadBridge raises error for invalid account."""
    from uploaders.upload_bridge import UploadBridge

    try:
        bridge = UploadBridge("nonexistent_account_name_12345")
        print("[FAIL] test_upload_bridge_invalid_account - Should have raised ValueError")
        assert False
    except ValueError as e:
        assert "not found" in str(e).lower()
        print("[PASS] test_upload_bridge_invalid_account passed")
    except FileNotFoundError:
        # accounts.json not found is also acceptable
        print("[PASS] test_upload_bridge_invalid_account passed (accounts.json not found)")


def test_get_available_accounts():
    """Test get_available_accounts returns a list."""
    from uploaders.upload_bridge import UploadBridge

    accounts = UploadBridge.get_available_accounts()
    assert isinstance(accounts, list)
    print(f"[PASS] test_get_available_accounts passed - found {len(accounts)} accounts")


def test_get_enabled_accounts():
    """Test get_enabled_accounts returns a list."""
    from uploaders.upload_bridge import UploadBridge

    accounts = UploadBridge.get_enabled_accounts()
    assert isinstance(accounts, list)
    print(f"[PASS] test_get_enabled_accounts passed - found {len(accounts)} enabled accounts")


def test_upload_single_file_nonexistent():
    """Test upload_single_file with nonexistent file."""
    from uploaders.upload_bridge import UploadBridge

    # Skip if no accounts configured
    accounts = UploadBridge.get_available_accounts()
    if not accounts:
        print("[SKIP] test_upload_single_file_nonexistent - no accounts configured")
        return

    bridge = UploadBridge(accounts[0])
    result = bridge.upload_single_file_sync("nonexistent_file_12345.mp4")

    assert result["success"] == False
    assert result["error"] is not None
    assert result["url"] is None
    assert result["filename"] == "nonexistent_file_12345.mp4"
    print("[PASS] test_upload_single_file_nonexistent passed")


def test_override_settings():
    """Test that override_settings are applied correctly."""
    from uploaders.upload_bridge import UploadBridge

    # Skip if no accounts configured
    accounts = UploadBridge.get_available_accounts()
    if not accounts:
        print("[SKIP] test_override_settings - no accounts configured")
        return

    override = {
        "tags": ["TestTag1", "TestTag2"],
        "description": "Test description",
        "content_type": "Test Content",
        "sexuality": "test",
        "niches": ["test_niche"],
        "keep_audio": True
    }

    bridge = UploadBridge(accounts[0], override_settings=override)

    assert bridge.account.tags == ["TestTag1", "TestTag2"]
    assert bridge.account.description == "Test description"
    assert bridge.account.content_type == "Test Content"
    assert bridge.account.sexuality == "test"
    assert bridge.account.niches == ["test_niche"]
    assert bridge.account.keep_audio == True
    print("[PASS] test_override_settings passed")


def test_result_dict_structure():
    """Test that result dict has correct structure."""
    from uploaders.upload_bridge import UploadBridge

    # Skip if no accounts configured
    accounts = UploadBridge.get_available_accounts()
    if not accounts:
        print("[SKIP] test_result_dict_structure - no accounts configured")
        return

    bridge = UploadBridge(accounts[0])
    result = bridge.upload_single_file_sync("nonexistent.mp4")

    # Check all required keys exist
    required_keys = ["success", "url", "error", "filename"]
    for key in required_keys:
        assert key in result, f"Missing key: {key}"

    # Check types
    assert isinstance(result["success"], bool)
    assert result["filename"] is not None
    print("[PASS] test_result_dict_structure passed")


if __name__ == "__main__":
    print("Running upload_bridge tests...\n")

    test_upload_bridge_import()
    test_upload_bridge_invalid_account()
    test_get_available_accounts()
    test_get_enabled_accounts()
    test_upload_single_file_nonexistent()
    test_override_settings()
    test_result_dict_structure()

    print("\nAll tests passed!")
