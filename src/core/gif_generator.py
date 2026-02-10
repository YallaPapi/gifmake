"""
GifMake - GIF Generator Core Module
Handles video to GIF conversion using FFmpeg
"""

import subprocess
import sys
import os
from typing import List, Optional, Callable, Dict, Any


def get_ffmpeg_path() -> str:
    """Get the path to ffmpeg executable, checking local directory first."""
    # Check if running as PyInstaller bundle
    if getattr(sys, 'frozen', False):
        # Running as compiled exe
        app_dir = os.path.dirname(sys.executable)
    else:
        # Running as script - check project root
        app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    local_ffmpeg = os.path.join(app_dir, "ffmpeg.exe")
    if os.path.exists(local_ffmpeg):
        return local_ffmpeg
    return "ffmpeg"  # Fall back to PATH


def get_ffprobe_path() -> str:
    """Get the path to ffprobe executable, checking local directory first."""
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    local_ffprobe = os.path.join(app_dir, "ffprobe.exe")
    if os.path.exists(local_ffprobe):
        return local_ffprobe
    return "ffprobe"  # Fall back to PATH


def get_video_duration(video_path: str) -> float:
    """
    Get the duration of a video file in seconds.

    Args:
        video_path: Path to the video file

    Returns:
        Duration in seconds as a float

    Raises:
        RuntimeError: If ffprobe fails or duration cannot be determined
    """
    try:
        # Build ffprobe command
        cmd = [
            get_ffprobe_path(),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]

        # Run ffprobe
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=creationflags
        )

        if result.returncode != 0:
            raise RuntimeError(f"ffprobe error: {result.stderr}")

        duration = float(result.stdout.strip())
        return duration

    except ValueError as e:
        raise RuntimeError(f"Could not parse video duration: {e}")
    except FileNotFoundError:
        raise RuntimeError("FFmpeg/ffprobe not found. Please install FFmpeg and add it to PATH.")


def generate_gifs(
    video_path: str,
    output_folder: str,
    gif_duration: int = 4,
    fps: int = 15,
    resolution: Optional[int] = 480,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    output_format: str = "gif",
    preserve_quality: bool = False
) -> List[str]:
    """
    Generate multiple GIFs or video clips from a video file.

    Args:
        video_path: Path to the source video file
        output_folder: Directory to save generated GIFs/clips
        gif_duration: Duration of each GIF/clip in seconds (default: 4)
        fps: Frame rate for GIFs (ignored for mp4 when preserve_quality=True)
        resolution: Height in pixels (ignored for mp4 when preserve_quality=True)
        progress_callback: Optional callback function(current, total) for progress updates
        output_format: Output format - "gif" or "mp4" (default: "gif")
        preserve_quality: If True and output_format="mp4", preserves original quality
                         (no FPS/resolution reduction, uses high-quality encoding)

    Returns:
        List of paths to generated GIF or MP4 files

    Raises:
        RuntimeError: If video processing fails
        FileNotFoundError: If video file doesn't exist
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    # Ensure output folder exists
    os.makedirs(output_folder, exist_ok=True)

    # Get video duration
    total_duration = get_video_duration(video_path)

    # Calculate number of GIFs
    num_gifs = int(total_duration // gif_duration)
    if num_gifs == 0:
        num_gifs = 1  # At least one GIF if video is shorter than gif_duration

    # Get base filename for output
    video_filename = os.path.splitext(os.path.basename(video_path))[0]

    # Build scale filter
    if resolution:
        scale_filter = f"scale=-1:{resolution}:flags=lanczos"
    else:
        scale_filter = "scale=trunc(iw/2)*2:trunc(ih/2)*2"  # Ensure even dimensions

    # Generate each GIF
    gif_paths = []

    for i in range(num_gifs):
        start_time = i * gif_duration

        # Calculate actual duration for this segment (last segment might be shorter)
        actual_duration = min(gif_duration, total_duration - start_time)
        if actual_duration <= 0:
            break

        # Output filename based on format
        if output_format == "mp4":
            output_filename = f"{video_filename}_clip_{i + 1:03d}.mp4"
        else:
            output_filename = f"{video_filename}_gif_{i + 1:03d}.gif"
        output_path = os.path.join(output_folder, output_filename)

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            ffmpeg_path = get_ffmpeg_path()

            if output_format == "mp4":
                if preserve_quality:
                    # Preserve quality mode: stream copy, no re-encoding
                    cmd = [
                        ffmpeg_path,
                        "-y",
                        "-ss", str(start_time),
                        "-t", str(actual_duration),
                        "-i", video_path,
                        "-c", "copy",
                        "-avoid_negative_ts", "make_zero",
                        output_path
                    ]
                else:
                    # Standard mode: apply FPS and resolution settings
                    cmd = [
                        ffmpeg_path,
                        "-y",
                        "-ss", str(start_time),
                        "-t", str(actual_duration),
                        "-i", video_path,
                        "-vf", f"fps={fps},{scale_filter}",
                        "-c:v", "libx264",
                        "-preset", "fast",
                        "-crf", "23",
                        "-c:a", "aac",
                        "-b:a", "128k",
                        output_path
                    ]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    creationflags=creationflags
                )

                if result.returncode != 0:
                    raise RuntimeError(f"FFmpeg error: {result.stderr}")
            else:
                # GIF encoding: Single-pass approach with palette generation for good quality
                simple_cmd = [
                    ffmpeg_path,
                    "-y",
                    "-ss", str(start_time),
                    "-t", str(actual_duration),
                    "-i", video_path,
                    "-vf", f"fps={fps},{scale_filter},split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5",
                    output_path
                ]

                result = subprocess.run(
                    simple_cmd,
                    capture_output=True,
                    text=True,
                    creationflags=creationflags
                )

                if result.returncode != 0:
                    # Try simpler approach if complex filter fails
                    fallback_cmd = [
                        ffmpeg_path,
                        "-y",
                        "-ss", str(start_time),
                        "-t", str(actual_duration),
                        "-i", video_path,
                        "-vf", f"fps={fps},{scale_filter}",
                        output_path
                    ]
                    result = subprocess.run(
                        fallback_cmd,
                        capture_output=True,
                        text=True,
                        creationflags=creationflags
                    )

                    if result.returncode != 0:
                        raise RuntimeError(f"FFmpeg error: {result.stderr}")

            gif_paths.append(output_path)

        except FileNotFoundError:
            raise RuntimeError("FFmpeg not found. Please install FFmpeg and add it to PATH.")

        # Report progress
        if progress_callback:
            progress_callback(i + 1, num_gifs)

    return gif_paths


# Supported video file extensions for bulk processing
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def scan_video_folder(folder_path: str) -> List[Dict[str, Any]]:
    """
    Scan a folder for video files and return their metadata.

    Args:
        folder_path: Path to the folder to scan

    Returns:
        List of dicts with keys: path, filename, duration
        Videos that cannot be read are skipped with a warning printed to stderr.

    Raises:
        FileNotFoundError: If folder doesn't exist
        NotADirectoryError: If path is not a directory
    """
    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"Folder not found: {folder_path}")

    if not os.path.isdir(folder_path):
        raise NotADirectoryError(f"Path is not a directory: {folder_path}")

    video_infos = []

    for filename in os.listdir(folder_path):
        file_ext = os.path.splitext(filename)[1].lower()
        if file_ext not in SUPPORTED_VIDEO_EXTENSIONS:
            continue

        file_path = os.path.join(folder_path, filename)

        # Skip if not a file (e.g., directory with .mp4 name)
        if not os.path.isfile(file_path):
            continue

        try:
            duration = get_video_duration(file_path)
            video_infos.append({
                "path": file_path,
                "filename": filename,
                "duration": duration
            })
        except Exception as e:
            # Skip videos that can't be read, print warning
            print(f"Warning: Could not read video '{filename}': {e}", file=sys.stderr)
            continue

    return video_infos


def get_total_estimated_gifs(video_infos: List[Dict[str, Any]], gif_duration: int) -> int:
    """
    Calculate total estimated number of GIFs for a list of videos.

    Args:
        video_infos: List of video info dicts from scan_video_folder()
        gif_duration: Duration of each GIF in seconds

    Returns:
        Total estimated number of GIFs across all videos
    """
    total = 0
    for video_info in video_infos:
        duration = video_info.get("duration", 0)
        if duration > 0:
            num_gifs = int(duration // gif_duration)
            if num_gifs == 0:
                num_gifs = 1  # At least one GIF if video is shorter than gif_duration
            total += num_gifs
    return total


def generate_gifs_bulk(
    video_paths: List[str],
    output_folder: str,
    gif_duration: int = 4,
    fps: int = 15,
    resolution: Optional[int] = 480,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    video_callback: Optional[Callable[[int, int, str], None]] = None,
    output_format: str = "gif",
    preserve_quality: bool = False
) -> Dict[str, Any]:
    """
    Generate GIFs or video clips from multiple video files.

    For each video, creates a subfolder named after the video file
    and generates GIFs or clips into that subfolder.

    Args:
        video_paths: List of video file paths to process
        output_folder: Base output directory
        gif_duration: Duration of each GIF/clip in seconds (default: 4)
        fps: Frame rate for the output (default: 15)
        resolution: Height in pixels (None for original, default: 480)
        progress_callback: Called for each output completed within current video: callback(num, total)
        video_callback: Called when starting each video: callback(video_num, total_videos, video_filename)
        output_format: Output format - "gif" or "mp4" (default: "gif")
        preserve_quality: If True and output_format="mp4", preserves original quality

    Returns:
        Dict with keys:
            - success: List of {"video": path, "gifs": [gif_paths]}
            - failed: List of {"video": path, "error": error_message}
            - total_gifs: Total number of GIFs generated
    """
    results = {
        "success": [],
        "failed": [],
        "total_gifs": 0
    }

    total_videos = len(video_paths)

    for video_idx, video_path in enumerate(video_paths, start=1):
        video_filename = os.path.basename(video_path)

        # Notify about starting this video
        if video_callback:
            video_callback(video_idx, total_videos, video_filename)

        try:
            # Validate video file exists
            if not os.path.exists(video_path):
                raise FileNotFoundError(f"Video file not found: {video_path}")

            # Create subfolder for this video's GIFs
            video_name_no_ext = os.path.splitext(video_filename)[0]
            video_output_folder = os.path.join(output_folder, video_name_no_ext)
            os.makedirs(video_output_folder, exist_ok=True)

            # Generate GIFs or clips for this video
            gif_paths = generate_gifs(
                video_path=video_path,
                output_folder=video_output_folder,
                gif_duration=gif_duration,
                fps=fps,
                resolution=resolution,
                progress_callback=progress_callback,
                output_format=output_format,
                preserve_quality=preserve_quality
            )

            results["success"].append({
                "video": video_path,
                "gifs": gif_paths
            })
            results["total_gifs"] += len(gif_paths)

        except Exception as e:
            results["failed"].append({
                "video": video_path,
                "error": str(e)
            })
            # Continue to next video even if one fails

    return results


if __name__ == "__main__":
    # Test the module
    import sys

    if len(sys.argv) < 2:
        print("Usage: python gif_generator.py <video_path>")
        sys.exit(1)

    video_path = sys.argv[1]

    print(f"Video: {video_path}")

    try:
        duration = get_video_duration(video_path)
        print(f"Duration: {duration:.2f} seconds")

        def progress(current, total):
            print(f"Progress: {current}/{total}")

        output_folder = os.path.dirname(video_path) or "."
        gifs = generate_gifs(
            video_path,
            output_folder,
            gif_duration=4,
            fps=15,
            resolution=480,
            progress_callback=progress
        )

        print(f"Generated {len(gifs)} GIFs:")
        for gif in gifs:
            print(f"  - {gif}")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
