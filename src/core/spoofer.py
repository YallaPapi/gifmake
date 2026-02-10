"""
File spoofer — creates unique copies of videos/images to avoid platform duplicate detection.

Synchronous port of the i2v project's spoof_service.py reeld mode.

Two modes:
1. VIDEO (reeld-style): Single-pass FFmpeg with crop, scale, bitrate randomization,
   metadata injection, and optional duration modification. Tries NVENC first, falls
   back to libx264.

2. IMAGE (Pillow-based): Noise, color shift, crop, metadata strip, re-save with
   randomized quality.

Usage:
    from core.spoofer import spoof_file
    spoofed_path = spoof_file("input.mp4")  # Returns path to spoofed copy
    # ... upload spoofed_path ...
    os.remove(spoofed_path)  # Clean up after upload
"""
import json
import logging
import os
import random
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

# Image extensions
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".heic", ".heif"}

# Video extensions
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}

# Crop ranges (percentage-based, keep this much of the frame)
CROP_W_MIN, CROP_W_MAX = 0.93, 0.97   # keep 93-97% width (3-7% crop)
CROP_H_MIN, CROP_H_MAX = 0.95, 0.98   # keep 95-98% height (2-5% crop)

# Duration modification (trim or extend by 3-8%)
DURATION_MOD_MIN, DURATION_MOD_MAX = 0.03, 0.08

# Bitrate ranges
VBIT_MIN, VBIT_MAX = 3000, 17000      # kbps video
ABIT_MIN, ABIT_MAX = 128, 264         # kbps audio

# Scale factors (1.0x to 2.0x in 0.1 steps)
SCALE_FACTORS = [round(1.0 + 0.1 * i, 1) for i in range(0, 11)]

# NVENC max dimension (H.264 hardware encoder limit)
NVENC_MAX_DIM = 4096

# Encoder tags for metadata injection
ENCODER_TAGS = ["Lavf58.76.100", "Lavf59.27.100", "Lavf60.3.100"]

# Camera models for metadata injection
CAMERA_MODELS = ["iPhone 14 Pro", "iPhone 15", "Samsung Galaxy S23", "Google Pixel 8", "iPhone 13"]

# Camera makes mapped from models
CAMERA_MAKES = {
    "iPhone 14 Pro": "Apple",
    "iPhone 15": "Apple",
    "iPhone 13": "Apple",
    "Samsung Galaxy S23": "Samsung",
    "Google Pixel 8": "Google",
}

# Output directory for spoofed files
SPOOF_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "spoof_temp")


# =============================================================================
# HELPERS
# =============================================================================


def _run_ffmpeg(args, description="ffmpeg"):
    """Run an FFmpeg command synchronously. Returns (returncode, stdout, stderr)."""
    logger.debug(f"Running FFmpeg: {description}")
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=300,
        )
        if result.returncode != 0:
            logger.error(f"FFmpeg failed ({description}): {result.stderr[:500]}")
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        logger.error(f"FFmpeg timed out: {description}")
        return -1, "", "Timeout after 300 seconds"
    except FileNotFoundError:
        logger.error("FFmpeg not found. Make sure ffmpeg is on your PATH.")
        return -1, "", "ffmpeg not found"


def _run_ffprobe(video_path):
    """Get video info using ffprobe. Returns dict with width, height, fps, duration, has_audio."""
    args = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path,
    ]
    try:
        result = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr[:300]}")

        data = json.loads(result.stdout)
        stream = data.get("streams", [{}])[0] if data.get("streams") else {}
        fmt = data.get("format", {})

        # Parse frame rate
        fps_str = stream.get("r_frame_rate", "30/1")
        if "/" in fps_str:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 30.0
        else:
            fps = float(fps_str)

        duration = float(stream.get("duration", 0) or fmt.get("duration", 0))

        # Check for audio
        audio_args = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            video_path,
        ]
        audio_result = subprocess.run(audio_args, capture_output=True, text=True, errors="replace", timeout=10)
        has_audio = audio_result.returncode == 0 and "audio" in audio_result.stdout.strip()

        return {
            "width": int(stream.get("width", 0)),
            "height": int(stream.get("height", 0)),
            "fps": fps,
            "duration": duration,
            "has_audio": has_audio,
        }
    except FileNotFoundError:
        raise RuntimeError("ffprobe not found. Make sure ffprobe is on your PATH.")


def _generate_random_metadata():
    """Generate randomized metadata for video spoofing."""
    days_ago = random.randint(30, 365)
    random_date = datetime.now() - timedelta(days=days_ago)

    model = random.choice(CAMERA_MODELS)
    make = CAMERA_MAKES.get(model, "Apple")
    encoder = random.choice(ENCODER_TAGS)

    return {
        "creation_time": random_date.strftime("%Y-%m-%dT%H:%M:%S"),
        "title": f"IMG_{random.randint(1000, 9999)}",
        "comment": f"Processed_{random.randint(10000, 99999)}",
        "make": make,
        "model": model,
        "encoder": encoder,
    }


def _is_image(file_path):
    """Check if a file is an image based on extension."""
    return Path(file_path).suffix.lower() in IMAGE_EXTENSIONS


def _is_video(file_path):
    """Check if a file is a video based on extension."""
    return Path(file_path).suffix.lower() in VIDEO_EXTENSIONS


# =============================================================================
# VIDEO SPOOFING (reeld mode)
# =============================================================================


def spoof_video(input_path, output_path, options=None):
    """
    Apply reeld-style spoofing to a video file.

    Single-pass FFmpeg pipeline with:
    - Metadata randomization
    - Center-based crop (3-7% width, 2-5% height)
    - Scale (1.0x to 2.0x with Lanczos)
    - Bitrate encoding (3000-17000 kbps video, 128-264 kbps audio)
    - Optional duration modification (50/50 trim/extend by 3-8%)

    Args:
        input_path: Path to input video
        output_path: Path for output video
        options: Dict with toggles: metadata, crop, scale, bitrate, audio_bitrate, duration_mod

    Returns:
        Dict with success, applied, params, error
    """
    default_options = {
        "metadata": True,
        "crop": True,
        "scale": True,
        "bitrate": True,
        "audio_bitrate": True,
        "duration_mod": True,
    }
    opts = {**default_options, **(options or {})}

    applied = []
    params = {}

    try:
        # Get video info
        video_info = _run_ffprobe(input_path)
        duration = video_info["duration"]
        has_audio = video_info.get("has_audio", True)

        # Build filter chain
        vf_parts = []

        # 1. Crop (3-7% width, 2-5% height, center-based)
        if opts.get("crop"):
            w_keep = random.uniform(CROP_W_MIN, CROP_W_MAX)
            h_keep = random.uniform(CROP_H_MIN, CROP_H_MAX)
            crop_filter = (
                f"crop=iw*{w_keep:.4f}:ih*{h_keep:.4f}:"
                f"(iw-iw*{w_keep:.4f})/2:(ih-ih*{h_keep:.4f})/2"
            )
            vf_parts.append(crop_filter)
            applied.append("crop")
            params["crop_w_pct"] = round(100 * (1 - w_keep), 2)
            params["crop_h_pct"] = round(100 * (1 - h_keep), 2)

        # 2. Scale (1.0x to 2.0x with Lanczos, capped for NVENC compatibility)
        if opts.get("scale"):
            # Cap scale so output dimensions don't exceed NVENC limit
            max_dim = max(video_info["width"], video_info["height"])
            if max_dim > 0:
                max_scale = NVENC_MAX_DIM / max_dim
                valid_scales = [s for s in SCALE_FACTORS if s <= max_scale]
                if not valid_scales:
                    valid_scales = [1.0]
            else:
                valid_scales = SCALE_FACTORS

            scale_factor = random.choice(valid_scales)
            scale_filter = (
                f"scale=trunc(iw*{scale_factor:.1f}/2)*2:"
                f"trunc(ih*{scale_factor:.1f}/2)*2:flags=lanczos"
            )
            vf_parts.append(scale_filter)
            applied.append("scale")
            params["scale_factor"] = scale_factor

        # 3. Duration modification (50/50 trim or extend by 3-8%)
        new_duration = duration
        extend_seconds = 0.0
        if opts.get("duration_mod") and duration > 1.0:
            duration_action = random.choice(["trim", "extend"])
            change_pct = random.uniform(DURATION_MOD_MIN, DURATION_MOD_MAX)
            change_seconds = duration * change_pct

            if duration_action == "trim":
                new_duration = max(duration - change_seconds, 0.5)
                applied.append("duration_trim")
            else:
                extend_seconds = change_seconds
                new_duration = duration + extend_seconds
                applied.append("duration_extend")

            params["duration_action"] = duration_action
            params["duration_change_pct"] = round(change_pct * 100, 2)
            params["duration_change_seconds"] = round(change_seconds, 3)
            params["new_duration"] = round(new_duration, 3)

        # 4. Bitrate settings
        if opts.get("bitrate"):
            v_bitrate = random.randint(VBIT_MIN, VBIT_MAX)
            applied.append("bitrate")
            params["v_bitrate_k"] = v_bitrate
        else:
            v_bitrate = 8000

        if opts.get("audio_bitrate") and has_audio:
            a_bitrate = random.randint(ABIT_MIN, ABIT_MAX)
            applied.append("audio_bitrate")
            params["a_bitrate_k"] = a_bitrate
        else:
            a_bitrate = 192

        # 5. Metadata
        if opts.get("metadata"):
            metadata = _generate_random_metadata()
            applied.append("metadata")
            params["metadata"] = metadata
        else:
            metadata = None

        # Add tpad filter for extend (must be last in the filter chain)
        if extend_seconds > 0:
            tpad_filter = f"tpad=stop_mode=clone:stop_duration={extend_seconds:.3f}"
            vf_parts.append(tpad_filter)

        # Build FFmpeg command — try NVENC first, fallback to libx264
        vf_chain = ",".join(vf_parts) if vf_parts else None

        for encoder, is_nvenc in [("h264_nvenc", True), ("libx264", False)]:
            cmd = ["ffmpeg", "-y", "-i", input_path]

            # Duration limit (only for trim)
            if new_duration < duration:
                cmd.extend(["-t", f"{new_duration:.3f}"])

            # Video filters
            if vf_chain:
                cmd.extend(["-vf", vf_chain])

            # Video encoding
            cmd.extend(["-c:v", encoder])

            if is_nvenc:
                cmd.extend([
                    "-preset", "p5",
                    "-bf", "0",
                    "-g", "250",
                    "-tune", "hq",
                    "-b:v", f"{v_bitrate}k",
                    "-maxrate", f"{v_bitrate}k",
                    "-bufsize", f"{v_bitrate * 2}k",
                ])
            else:
                cmd.extend([
                    "-preset", "medium",
                    "-b:v", f"{v_bitrate}k",
                    "-maxrate", f"{v_bitrate}k",
                    "-bufsize", f"{v_bitrate * 2}k",
                ])

            cmd.extend(["-pix_fmt", "yuv420p"])

            # Audio encoding
            if has_audio:
                cmd.extend(["-c:a", "aac", "-b:a", f"{a_bitrate}k"])
            else:
                cmd.extend(["-an"])

            # Metadata injection
            if metadata:
                cmd.extend([
                    "-map_metadata", "-1",
                    "-metadata", f"creation_time={metadata['creation_time']}",
                    "-metadata", f"title={metadata['title']}",
                    "-metadata", f"comment={metadata['comment']}",
                    "-metadata", f"make={metadata['make']}",
                    "-metadata", f"model={metadata['model']}",
                    "-metadata", f"encoder={metadata['encoder']}",
                ])

            cmd.extend(["-movflags", "+faststart", output_path])

            rc, _, stderr = _run_ffmpeg(cmd, description=f"reeld spoof ({encoder})")

            if rc == 0:
                params["encoder_used"] = encoder
                if is_nvenc:
                    applied.append("nvenc")
                break
            elif is_nvenc:
                logger.warning(f"NVENC failed, falling back to libx264: {stderr[:200]}")
                continue
            else:
                raise RuntimeError(f"Encoding failed with both NVENC and libx264: {stderr[:300]}")

        logger.info(f"Video spoof complete: {applied}")
        return {"success": True, "applied": applied, "params": params}

    except Exception as e:
        logger.error(f"Video spoof failed: {e}")
        return {"success": False, "applied": applied, "params": params, "error": str(e)}


# =============================================================================
# IMAGE SPOOFING (Pillow-based)
# =============================================================================


def spoof_image(input_path, output_path, options=None):
    """
    Apply spoof modifications to an image using Pillow.

    Applies: metadata strip, noise, color shift, crop, re-save with randomized quality.

    Args:
        input_path: Path to input image
        output_path: Path for output image
        options: Dict with toggles: strip_metadata, add_noise, crop, color_shift

    Returns:
        Dict with success, applied, error
    """
    default_options = {
        "strip_metadata": True,
        "add_noise": True,
        "crop": True,
        "color_shift": True,
    }
    opts = {**default_options, **(options or {})}
    applied = []

    try:
        from PIL import Image
        import numpy as np
        # Register HEIC/HEIF support if available
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass
    except ImportError:
        return {
            "success": False,
            "applied": [],
            "error": "Pillow and numpy required. Install: pip install Pillow numpy",
        }

    try:
        img = Image.open(input_path)

        # Convert to RGB
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Strip metadata by creating a new image without EXIF
        if opts.get("strip_metadata"):
            data = list(img.getdata())
            clean_img = Image.new(img.mode, img.size)
            clean_img.putdata(data)
            img = clean_img
            applied.append("metadata_stripped")

        # Convert to numpy for pixel operations
        arr = np.array(img, dtype=np.int16)

        # Add imperceptible noise
        if opts.get("add_noise"):
            noise = np.random.randint(-2, 3, arr.shape, dtype=np.int16)
            arr = np.clip(arr + noise, 0, 255)
            applied.append("pixel_noise")

        # Color shift
        if opts.get("color_shift"):
            r_shift = random.randint(-2, 2)
            g_shift = random.randint(-1, 1)
            b_shift = random.randint(-2, 2)
            arr[:, :, 0] = np.clip(arr[:, :, 0] + r_shift, 0, 255)
            arr[:, :, 1] = np.clip(arr[:, :, 1] + g_shift, 0, 255)
            arr[:, :, 2] = np.clip(arr[:, :, 2] + b_shift, 0, 255)
            applied.append("color_shifted")

        # Convert back to image
        img = Image.fromarray(arr.astype(np.uint8))

        # Crop (1-3 pixels from random edges)
        if opts.get("crop"):
            w, h = img.size
            left = random.randint(0, 3)
            right = random.randint(0, 3)
            top = random.randint(0, 3)
            bottom = random.randint(0, 3)
            if left + right + top + bottom == 0:
                left = 1
            img = img.crop((left, top, w - right, h - bottom))
            applied.append("cropped")

        # Save with randomized quality
        ext = Path(output_path).suffix.lower()
        if ext in {".jpg", ".jpeg"}:
            quality = random.randint(92, 97)
            img.save(output_path, "JPEG", quality=quality, optimize=True)
        elif ext == ".png":
            img.save(output_path, "PNG", optimize=True)
        elif ext == ".webp":
            quality = random.randint(90, 97)
            img.save(output_path, "WEBP", quality=quality)
        else:
            img.save(output_path)

        applied.append("re_saved")
        logger.info(f"Image spoof complete: {applied}")
        return {"success": True, "applied": applied}

    except Exception as e:
        logger.error(f"Image spoof failed: {e}")
        return {"success": False, "applied": applied, "error": str(e)}


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================


def spoof_file(input_path, output_dir=None, options=None):
    """
    Spoof a file (video or image) to create a unique copy.

    Detects file type by extension and routes to the appropriate spoofer.

    Args:
        input_path: Path to the original file
        output_dir: Directory for spoofed output (default: data/spoof_temp/)
        options: Dict of spoof options (passed to spoof_video or spoof_image)

    Returns:
        (spoofed_path, result_dict) on success
        (None, result_dict) on failure

    Example:
        spoofed_path, result = spoof_file("video.mp4")
        if spoofed_path:
            upload(spoofed_path)
            os.remove(spoofed_path)
    """
    if not os.path.exists(input_path):
        return None, {"success": False, "error": f"File not found: {input_path}"}

    # Setup output directory
    if output_dir is None:
        output_dir = SPOOF_OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    # Generate unique output filename
    ext = Path(input_path).suffix.lower()
    base = Path(input_path).stem
    unique_id = random.randint(10000, 99999)
    output_name = f"{base}_spoof_{unique_id}{ext}"
    output_path = os.path.join(output_dir, output_name)

    # Force appropriate output extensions
    if _is_video(input_path):
        if ext != ".mp4":
            output_path = os.path.join(output_dir, f"{base}_spoof_{unique_id}.mp4")
        result = spoof_video(input_path, output_path, options)
    elif _is_image(input_path):
        # Force .jpg for HEIC (Reddit doesn't accept HEIC uploads)
        if ext in {".heic", ".heif"}:
            output_path = os.path.join(output_dir, f"{base}_spoof_{unique_id}.jpg")
        result = spoof_image(input_path, output_path, options)
    else:
        return None, {"success": False, "error": f"Unsupported file type: {ext}"}

    if result.get("success") and os.path.exists(output_path):
        original_size = os.path.getsize(input_path)
        spoofed_size = os.path.getsize(output_path)
        logger.info(
            f"Spoofed: {os.path.basename(input_path)} "
            f"({original_size:,} → {spoofed_size:,} bytes)"
        )
        return output_path, result
    else:
        # Clean up failed output
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        return None, result


def cleanup_spoof_dir():
    """Remove all files in the spoof temp directory."""
    if os.path.exists(SPOOF_OUTPUT_DIR):
        for f in Path(SPOOF_OUTPUT_DIR).iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        logger.info("Spoof temp directory cleaned")
