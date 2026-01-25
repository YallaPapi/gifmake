# GIF Generator Module

High-quality GIF generation from video files using FFmpeg's two-pass approach.

## Features

- **High Quality**: Uses FFmpeg's palettegen + paletteuse for optimal 256-color GIFs
- **Batch Processing**: Automatically splits long videos into multiple GIF segments
- **Flexible Settings**: Adjustable duration, FPS, and resolution
- **Progress Tracking**: Optional callback for real-time progress updates
- **Error Handling**: Graceful failure handling, never crashes
- **Memory Efficient**: Processes segments one at a time

## Usage

### Basic Example

```python
from core.gif_generator import generate_gifs, get_video_duration

# Get video info
duration = get_video_duration("my_video.mp4")
print(f"Video is {duration:.1f} seconds long")

# Generate GIFs
gifs = generate_gifs(
    input_video="my_video.mp4",
    output_dir="output",
    duration_sec=4,      # 4 seconds per GIF
    fps=15,              # 15 frames per second
    resolution="480"     # 480p height
)

print(f"Created {len(gifs)} GIFs")
for gif_path in gifs:
    print(f"  - {gif_path}")
```

### With Progress Callback

```python
def on_progress(current, total):
    percent = (current / total) * 100
    print(f"Progress: {current}/{total} ({percent:.1f}%)")

gifs = generate_gifs(
    "my_video.mp4",
    "output",
    duration_sec=5,
    fps=20,
    resolution="720",
    progress_callback=on_progress
)
```

### Resolution Options

```python
# Original video resolution (maintains aspect ratio)
generate_gifs("video.mp4", "out", resolution="original")

# Fixed height, maintains aspect ratio
generate_gifs("video.mp4", "out", resolution="720")   # 720p
generate_gifs("video.mp4", "out", resolution="480")   # 480p
generate_gifs("video.mp4", "out", resolution="360")   # 360p

# With "p" suffix also works
generate_gifs("video.mp4", "out", resolution="720p")
```

## API Reference

### `get_video_duration(video_path: str) -> float`

Get the duration of a video file in seconds.

**Parameters:**
- `video_path` (str): Path to the video file

**Returns:**
- `float`: Duration in seconds

**Raises:**
- `RuntimeError`: If ffprobe fails or video cannot be read
- `FileNotFoundError`: If ffprobe is not found in PATH

---

### `generate_gifs(input_video, output_dir, duration_sec=4, fps=15, resolution="480", progress_callback=None) -> List[str]`

Split video into segments and convert each to a high-quality GIF.

**Parameters:**
- `input_video` (str): Path to input video file
- `output_dir` (str): Directory where GIFs will be saved
- `duration_sec` (int): Duration of each GIF in seconds (default: 4)
- `fps` (int): Frame rate for GIFs (default: 15)
- `resolution` (str): Resolution setting - "original", "720", "480", "360" (default: "480")
- `progress_callback` (callable, optional): Callback function(current_gif_num, total_gifs)

**Returns:**
- `List[str]`: List of paths to generated GIF files (empty list on failure)

**Output Naming:**
GIFs are named: `{video_name}_gif_001.gif`, `{video_name}_gif_002.gif`, etc.

**Example:**
```python
gifs = generate_gifs(
    "vacation.mp4",      # 60 second video
    "gifs",
    duration_sec=4,
    fps=15,
    resolution="480"
)
# Creates: vacation_gif_001.gif through vacation_gif_015.gif
```

## Technical Details

### Two-Pass Process

Each GIF is created using FFmpeg's optimal two-pass approach:

1. **Pass 1 - Palette Generation:**
   ```bash
   ffmpeg -ss {start} -t {duration} -i input.mp4 \
     -vf "fps=15,scale=W:H:flags=lanczos,palettegen=stats_mode=diff" \
     palette.png
   ```

2. **Pass 2 - GIF Creation:**
   ```bash
   ffmpeg -ss {start} -t {duration} -i input.mp4 -i palette.png \
     -lavfi "fps=15,scale=W:H:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5" \
     output.gif
   ```

### Why Two Passes?

- **Better Quality**: Palette is optimized for each specific segment
- **Smaller Files**: Better color selection reduces file size
- **Consistent Colors**: Avoids color banding and posterization

### Dimensions

- All output dimensions are guaranteed to be even numbers (FFmpeg requirement)
- Aspect ratio is always maintained
- Lanczos scaling provides high-quality resizing

## Requirements

- **Python**: 3.10+
- **FFmpeg**: Must be installed and available in PATH
- **FFprobe**: Usually bundled with FFmpeg

### Checking FFmpeg Installation

```bash
# Check if installed
ffmpeg -version
ffprobe -version

# Windows: Install via Chocolatey
choco install ffmpeg

# Or download from: https://ffmpeg.org/download.html
```

## Performance

Typical processing times (on mid-range hardware):

| Video Length | Settings | Processing Time |
|--------------|----------|-----------------|
| 1 minute | 15 FPS, 480p | ~15 seconds |
| 5 minutes | 15 FPS, 480p | ~60 seconds |
| 10 minutes | 20 FPS, 720p | ~3-4 minutes |

**Factors affecting speed:**
- Input video resolution and codec
- Output FPS and resolution settings
- CPU performance (FFmpeg is CPU-bound)
- Disk I/O speed

## Error Handling

The module handles errors gracefully:

```python
gifs = generate_gifs("missing.mp4", "out")
# Returns: []
# Prints: "Error: Input video not found: missing.mp4"
```

Common error scenarios:
- Missing input file → Returns empty list
- FFmpeg not found → Returns empty list, prints error
- Corrupt video → Skips bad segments, processes others
- Permission errors → Returns empty list

## Command-Line Usage

You can also run the module directly:

```bash
# Basic usage
python gif_generator.py input.mp4

# Specify output directory
python gif_generator.py input.mp4 my_gifs
```

This creates GIFs in the specified directory with default settings (4s duration, 15 FPS, 480p).
