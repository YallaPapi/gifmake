"""Default settings for GifMake"""

DEFAULTS = {
    "gif_duration": 4,      # seconds
    "fps": 15,              # frames per second
    "resolution": "480",    # height in pixels
}

SUPPORTED_FORMATS = [
    ("Video files", "*.mp4 *.mov *.avi *.mkv *.webm *.MP4 *.MOV *.AVI *.MKV *.WEBM"),
    ("All files", "*.*")
]

FPS_OPTIONS = [10, 15, 20, 24, 30]
RESOLUTION_OPTIONS = ["Original", "720p", "480p", "360p"]
