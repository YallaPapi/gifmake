"""
GifMake - Video to GIF Converter
Main application entry point
"""

import sys
import os

# Add src directory to path for relative imports
src_dir = os.path.dirname(os.path.abspath(__file__))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from gui.main_window import GifMakeApp


def main():
    """Launch the GifMake application."""
    app = GifMakeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
