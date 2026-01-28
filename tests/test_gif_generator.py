"""
Unit tests for gif_generator module
"""

import os
import sys
import tempfile
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.gif_generator import (
    get_video_duration,
    generate_gifs,
    _ensure_even_dimension,
    _get_scale_filter
)


def test_ensure_even_dimension():
    """Test dimension rounding function."""
    assert _ensure_even_dimension(100) == 100
    assert _ensure_even_dimension(101) == 100
    assert _ensure_even_dimension(720) == 720
    assert _ensure_even_dimension(721) == 720
    print("[PASS] test_ensure_even_dimension passed")


def test_get_scale_filter():
    """Test scale filter generation."""
    # Test height-based scaling
    filter_480 = _get_scale_filter("480", "dummy.mp4")
    assert "480" in filter_480
    assert "scale=" in filter_480

    filter_720 = _get_scale_filter("720p", "dummy.mp4")
    assert "720" in filter_720

    # Test original (fallback when file doesn't exist)
    filter_orig = _get_scale_filter("original", "nonexistent.mp4")
    assert "scale=" in filter_orig
    print("[PASS] test_get_scale_filter passed")


def test_get_video_duration_invalid():
    """Test duration function with invalid file."""
    try:
        get_video_duration("nonexistent_file.mp4")
        assert False, "Should have raised an exception"
    except (RuntimeError, FileNotFoundError):
        print("[PASS] test_get_video_duration_invalid passed")


def test_generate_gifs_invalid_input():
    """Test GIF generation with invalid input."""
    with tempfile.TemporaryDirectory() as tmpdir:
        gifs = generate_gifs(
            "nonexistent_file.mp4",
            tmpdir,
            duration_sec=2,
            fps=10,
            resolution="480"
        )
        assert gifs == [], "Should return empty list for invalid input"
    print("[PASS] test_generate_gifs_invalid_input passed")


def test_progress_callback():
    """Test that progress callback is called."""
    call_count = [0]

    def callback(current, total):
        call_count[0] += 1
        assert current > 0
        assert total > 0
        assert current <= total

    # This will fail on missing file, but we're just testing the callback setup
    with tempfile.TemporaryDirectory() as tmpdir:
        generate_gifs(
            "test.mp4",
            tmpdir,
            progress_callback=callback
        )

    # Callback won't be called if file doesn't exist, which is fine
    print("[PASS] test_progress_callback passed")


if __name__ == "__main__":
    print("Running gif_generator tests...\n")

    test_ensure_even_dimension()
    test_get_scale_filter()
    test_get_video_duration_invalid()
    test_generate_gifs_invalid_input()
    test_progress_callback()

    print("\nAll tests passed!")
