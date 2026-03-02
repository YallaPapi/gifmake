"""
GifMake - Video to GIF Converter
Main application entry point
"""

import sys
import os
import traceback
from datetime import datetime

# Add src directory to path for relative imports
src_dir = os.path.dirname(os.path.abspath(__file__))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from gui.main_window import GifMakeApp


def main():
    """Launch the GifMake application."""
    try:
        app = GifMakeApp()
        app.mainloop()
    except Exception:
        log_dir = os.path.join(os.path.dirname(src_dir), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "gui_startup_error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().isoformat()}] GUI startup failure\n")
            f.write(traceback.format_exc())
            f.write("\n")
        raise


if __name__ == "__main__":
    main()
