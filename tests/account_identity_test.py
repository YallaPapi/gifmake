"""Unit tests for account identity normalization helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.account_identity import extract_username_from_profile_name, normalize_reddit_username


def test_normalize_reddit_username_basic():
    assert normalize_reddit_username("LunaMonroe") == "lunamonroe"
    assert normalize_reddit_username("u/LunaMonroe") == "lunamonroe"
    assert normalize_reddit_username(" luna_monroe-1 ") == "luna_monroe-1"


def test_extract_username_from_adspower_names():
    assert (
        extract_username_from_profile_name("4u - reddit bot - LunaMonroe", "4u ")
        == "lunamonroe"
    )
    assert (
        extract_username_from_profile_name("P reddit bot midnightMae", "P ")
        == "midnightmae"
    )
    assert (
        extract_username_from_profile_name("G   SoftBellaUnfiltered", "G ")
        == "softbellaunfiltered"
    )
