"""
GifMake - Video to GIF Converter
Main application window using CustomTkinter
"""

import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import os
import subprocess
import sys
import asyncio

# Add src directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Add uploaders directory to path for import
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploaders'))
try:
    from upload_bridge import UploadBridge
    UPLOAD_AVAILABLE = True
except ImportError:
    UPLOAD_AVAILABLE = False

try:
    from gui.auto_poster_tab import AutoPosterTab
    AUTO_POSTER_AVAILABLE = True
except ImportError:
    AUTO_POSTER_AVAILABLE = False


class GifMakeApp(ctk.CTk):
    """Main application window for GifMake video to GIF converter."""

    # Supported video formats
    VIDEO_FORMATS = [
        ("Video files", "*.mp4 *.mov *.avi *.mkv *.webm *.MP4 *.MOV *.AVI *.MKV *.WEBM"),
        ("MP4 files", "*.mp4 *.MP4"),
        ("All files", "*.*")
    ]

    # Video file extensions for bulk mode
    VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}

    # Resolution options mapping
    RESOLUTION_OPTIONS = {
        "Original": None,
        "720p": 720,
        "480p": 480,
        "360p": 360
    }

    # Frame rate options
    FPS_OPTIONS = ["10", "15", "20", "24", "30"]

    def __init__(self):
        super().__init__()

        # Window configuration
        self.title("GifMake")
        self.geometry("650x850")
        # Set minimum window size to ensure all content is visible
        self.minsize(550, 750)

        # Set dark theme
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # State variables
        self.video_path = None  # Single video path (for single mode)
        self.video_paths = []   # List of video paths (for bulk mode)
        self.video_durations = {}  # Map of video_path -> duration
        self.video_duration = 0
        self.total_duration = 0  # Total duration for bulk mode
        self.is_processing = False
        self.bulk_mode = False  # False = Single Video, True = Bulk Folder

        # Upload state variables
        self.upload_enabled = False
        self.selected_account = None
        self.account_manager = None
        self.upload_settings = {}

        # Build the UI
        self._create_widgets()

    def _create_widgets(self):
        """Create all UI widgets."""

        # Configure root window grid
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Create tabview for main navigation
        self.tabview = ctk.CTkTabview(self, fg_color="transparent")
        self.tabview.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)

        # Tab 1: Video Converter (existing functionality)
        converter_tab = self.tabview.add("Video Converter")
        converter_tab.grid_columnconfigure(0, weight=1)
        converter_tab.grid_rowconfigure(0, weight=1)

        # Tab 2: Auto Poster
        poster_tab = self.tabview.add("Auto Poster")
        poster_tab.grid_columnconfigure(0, weight=1)
        poster_tab.grid_rowconfigure(0, weight=1)

        # Create scrollable container inside the converter tab
        self.scroll_container = ctk.CTkScrollableFrame(
            converter_tab,
            fg_color="transparent",
            corner_radius=0
        )
        self.scroll_container.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.scroll_container.grid_columnconfigure(0, weight=1)

        # Main container frame with padding inside the scrollable area
        self.main_frame = ctk.CTkFrame(self.scroll_container, fg_color="transparent")
        self.main_frame.grid(row=0, column=0, sticky="nsew", padx=30, pady=20)
        self.main_frame.grid_columnconfigure(0, weight=1)

        # Initialize Auto Poster tab
        if AUTO_POSTER_AVAILABLE:
            poster_scroll = ctk.CTkScrollableFrame(
                poster_tab, fg_color="transparent", corner_radius=0
            )
            poster_scroll.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
            poster_scroll.grid_columnconfigure(0, weight=1)
            poster_frame = ctk.CTkFrame(poster_scroll, fg_color="transparent")
            poster_frame.grid(row=0, column=0, sticky="nsew", padx=30, pady=20)
            poster_frame.grid_columnconfigure(0, weight=1)
            self.auto_poster = AutoPosterTab(poster_frame, self)

        # Configure row weights for proper expansion
        self.main_frame.grid_rowconfigure(0, weight=0)  # Mode toggle
        self.main_frame.grid_rowconfigure(1, weight=0)  # Drop zone
        self.main_frame.grid_rowconfigure(2, weight=0)  # File info
        self.main_frame.grid_rowconfigure(3, weight=0)  # Video list (bulk mode)
        self.main_frame.grid_rowconfigure(4, weight=0)  # Settings header
        self.main_frame.grid_rowconfigure(5, weight=0)  # Settings frame
        self.main_frame.grid_rowconfigure(6, weight=0)  # Generate button
        self.main_frame.grid_rowconfigure(7, weight=0)  # Progress

        # ===== MODE TOGGLE =====
        self._create_mode_toggle()

        # ===== DROP ZONE =====
        self._create_drop_zone()

        # ===== FILE INFO SECTION =====
        self._create_file_info_section()

        # ===== VIDEO LIST (BULK MODE) =====
        self._create_video_list_section()

        # ===== SETTINGS SECTION =====
        self._create_settings_section()

        # ===== GENERATE BUTTON =====
        self._create_generate_button()

        # ===== PROGRESS SECTION =====
        self._create_progress_section()

    def _create_mode_toggle(self):
        """Create the mode toggle switch between Single Video and Bulk Folder."""

        self.mode_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.mode_frame.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        self.mode_frame.grid_columnconfigure(0, weight=1)

        # Centered container for the segmented button
        self.mode_center_frame = ctk.CTkFrame(self.mode_frame, fg_color="transparent")
        self.mode_center_frame.grid(row=0, column=0)

        self.mode_label = ctk.CTkLabel(
            self.mode_center_frame,
            text="Mode:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#333333", "#FFFFFF")
        )
        self.mode_label.grid(row=0, column=0, padx=(0, 10))

        self.mode_segmented = ctk.CTkSegmentedButton(
            self.mode_center_frame,
            values=["Single Video", "Bulk Folder"],
            command=self.on_mode_change,
            font=ctk.CTkFont(size=12),
            selected_color=("#1E88E5", "#1565C0"),
            selected_hover_color=("#1976D2", "#0D47A1")
        )
        self.mode_segmented.set("Single Video")
        self.mode_segmented.grid(row=0, column=1)

    def _create_drop_zone(self):
        """Create the drag and drop zone for video selection."""

        self.drop_frame = ctk.CTkFrame(
            self.main_frame,
            height=150,
            corner_radius=15,
            border_width=2,
            border_color=("#3B8ED0", "#1F6AA5"),
            fg_color=("#E8F4FD", "#1A1A2E")
        )
        self.drop_frame.grid(row=1, column=0, sticky="ew", pady=(0, 20))
        self.drop_frame.grid_columnconfigure(0, weight=1)
        self.drop_frame.grid_rowconfigure(0, weight=1)
        self.drop_frame.grid_propagate(False)

        # Drop zone content
        self.drop_content_frame = ctk.CTkFrame(self.drop_frame, fg_color="transparent")
        self.drop_content_frame.grid(row=0, column=0, sticky="nsew")
        self.drop_content_frame.grid_columnconfigure(0, weight=1)

        # Icon label (using text as placeholder)
        self.drop_icon = ctk.CTkLabel(
            self.drop_content_frame,
            text="[VIDEO]",
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=("#3B8ED0", "#5DADE2")
        )
        self.drop_icon.grid(row=0, column=0, pady=(25, 5))

        # Main drop text
        self.drop_label = ctk.CTkLabel(
            self.drop_content_frame,
            text="Drag & Drop Video Here",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=("#333333", "#FFFFFF")
        )
        self.drop_label.grid(row=1, column=0, pady=(0, 5))

        # Secondary text
        self.drop_sublabel = ctk.CTkLabel(
            self.drop_content_frame,
            text="or Click to Browse",
            font=ctk.CTkFont(size=12),
            text_color=("#666666", "#AAAAAA")
        )
        self.drop_sublabel.grid(row=2, column=0, pady=(0, 5))

        # Supported formats
        self.format_label = ctk.CTkLabel(
            self.drop_content_frame,
            text="Supports: MP4, MOV, AVI, MKV, WebM",
            font=ctk.CTkFont(size=10),
            text_color=("#888888", "#777777")
        )
        self.format_label.grid(row=3, column=0, pady=(0, 15))

        # Make the entire drop zone clickable
        self.drop_frame.bind("<Button-1>", lambda e: self.select_video_or_folder())
        self.drop_content_frame.bind("<Button-1>", lambda e: self.select_video_or_folder())
        self.drop_icon.bind("<Button-1>", lambda e: self.select_video_or_folder())
        self.drop_label.bind("<Button-1>", lambda e: self.select_video_or_folder())
        self.drop_sublabel.bind("<Button-1>", lambda e: self.select_video_or_folder())
        self.format_label.bind("<Button-1>", lambda e: self.select_video_or_folder())

        # Cursor change on hover
        for widget in [self.drop_frame, self.drop_content_frame, self.drop_icon,
                       self.drop_label, self.drop_sublabel, self.format_label]:
            widget.configure(cursor="hand2")

    def _create_file_info_section(self):
        """Create the file information display section."""

        self.info_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.info_frame.grid(row=2, column=0, sticky="ew", pady=(0, 15))
        self.info_frame.grid_columnconfigure(0, weight=1)

        # Selected file display
        self.selected_label = ctk.CTkLabel(
            self.info_frame,
            text="Selected: No video selected",
            font=ctk.CTkFont(size=13),
            text_color=("#555555", "#CCCCCC"),
            anchor="w"
        )
        self.selected_label.grid(row=0, column=0, sticky="w")

        # Duration and estimated GIFs
        self.duration_label = ctk.CTkLabel(
            self.info_frame,
            text="Duration: --:-- | Estimated GIFs: --",
            font=ctk.CTkFont(size=12),
            text_color=("#777777", "#999999"),
            anchor="w"
        )
        self.duration_label.grid(row=1, column=0, sticky="w", pady=(5, 0))

    def _create_video_list_section(self):
        """Create the scrollable video list section for bulk mode."""

        # Container frame for the video list (hidden by default)
        self.video_list_container = ctk.CTkFrame(
            self.main_frame,
            corner_radius=10,
            fg_color=("#F5F5F5", "#2B2B3D")
        )
        # Initially hidden - will be shown in bulk mode
        self.video_list_container.grid(row=3, column=0, sticky="ew", pady=(0, 15))
        self.video_list_container.grid_columnconfigure(0, weight=1)
        self.video_list_container.grid_remove()  # Hide initially

        # Header with collapse toggle
        self.video_list_header = ctk.CTkFrame(self.video_list_container, fg_color="transparent")
        self.video_list_header.grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))
        self.video_list_header.grid_columnconfigure(1, weight=1)

        self.video_list_title = ctk.CTkLabel(
            self.video_list_header,
            text="Videos in Folder",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#333333", "#FFFFFF")
        )
        self.video_list_title.grid(row=0, column=0, sticky="w")

        self.video_count_label = ctk.CTkLabel(
            self.video_list_header,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=("#666666", "#AAAAAA")
        )
        self.video_count_label.grid(row=0, column=1, sticky="w", padx=(10, 0))

        # Scrollable frame for video list
        self.video_list_scroll = ctk.CTkScrollableFrame(
            self.video_list_container,
            height=150,
            fg_color="transparent"
        )
        self.video_list_scroll.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.video_list_scroll.grid_columnconfigure(0, weight=1)

        # Store video item widgets
        self.video_item_widgets = []

    def _create_settings_section(self):
        """Create the settings panel with all controls."""

        # Settings header
        self.settings_header = ctk.CTkLabel(
            self.main_frame,
            text="Settings",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=("#333333", "#FFFFFF")
        )
        self.settings_header.grid(row=4, column=0, sticky="w", pady=(10, 15))

        # Settings container
        self.settings_frame = ctk.CTkFrame(
            self.main_frame,
            corner_radius=10,
            fg_color=("#F5F5F5", "#2B2B3D")
        )
        self.settings_frame.grid(row=5, column=0, sticky="ew", pady=(0, 20))
        self.settings_frame.grid_columnconfigure(1, weight=1)

        row = 0
        padding_y = 15
        padding_x = 20

        # ----- GIF Duration -----
        self.duration_setting_label = ctk.CTkLabel(
            self.settings_frame,
            text="GIF Duration:",
            font=ctk.CTkFont(size=13),
            anchor="w"
        )
        self.duration_setting_label.grid(row=row, column=0, sticky="w", padx=padding_x, pady=(padding_y, 5))

        # Duration slider frame
        self.slider_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        self.slider_frame.grid(row=row, column=1, sticky="ew", padx=padding_x, pady=(padding_y, 5))
        self.slider_frame.grid_columnconfigure(0, weight=1)

        self.duration_slider = ctk.CTkSlider(
            self.slider_frame,
            from_=1,
            to=10,
            number_of_steps=9,
            command=self.on_duration_change,
            width=200
        )
        self.duration_slider.set(4)  # Default 4 seconds
        self.duration_slider.grid(row=0, column=0, sticky="w")

        self.duration_value_label = ctk.CTkLabel(
            self.slider_frame,
            text="4 seconds",
            font=ctk.CTkFont(size=12),
            width=80,
            anchor="w"
        )
        self.duration_value_label.grid(row=0, column=1, padx=(15, 0))

        row += 1

        # ----- Frame Rate -----
        self.fps_label = ctk.CTkLabel(
            self.settings_frame,
            text="Frame Rate:",
            font=ctk.CTkFont(size=13),
            anchor="w"
        )
        self.fps_label.grid(row=row, column=0, sticky="w", padx=padding_x, pady=padding_y)

        self.fps_dropdown = ctk.CTkComboBox(
            self.settings_frame,
            values=self.FPS_OPTIONS,
            width=150,
            state="readonly",
            font=ctk.CTkFont(size=12)
        )
        self.fps_dropdown.set("15")  # Default 15 FPS
        self.fps_dropdown.grid(row=row, column=1, sticky="w", padx=padding_x, pady=padding_y)

        self.fps_unit_label = ctk.CTkLabel(
            self.settings_frame,
            text="FPS",
            font=ctk.CTkFont(size=12),
            text_color=("#666666", "#AAAAAA")
        )
        self.fps_unit_label.grid(row=row, column=1, sticky="w", padx=(175, 0), pady=padding_y)

        row += 1

        # ----- Resolution -----
        self.resolution_label = ctk.CTkLabel(
            self.settings_frame,
            text="Resolution:",
            font=ctk.CTkFont(size=13),
            anchor="w"
        )
        self.resolution_label.grid(row=row, column=0, sticky="w", padx=padding_x, pady=padding_y)

        self.resolution_dropdown = ctk.CTkComboBox(
            self.settings_frame,
            values=list(self.RESOLUTION_OPTIONS.keys()),
            width=150,
            state="readonly",
            font=ctk.CTkFont(size=12)
        )
        self.resolution_dropdown.set("480p")  # Default 480p
        self.resolution_dropdown.grid(row=row, column=1, sticky="w", padx=padding_x, pady=padding_y)

        row += 1

        # ----- Output Format -----
        self.output_format_label = ctk.CTkLabel(
            self.settings_frame,
            text="Output Format:",
            font=ctk.CTkFont(size=13),
            anchor="w"
        )
        self.output_format_label.grid(row=row, column=0, sticky="w", padx=padding_x, pady=padding_y)

        self.format_segmented = ctk.CTkSegmentedButton(
            self.settings_frame,
            values=["GIF", "Video Clips"],
            command=self.on_format_change,
            font=ctk.CTkFont(size=12),
            selected_color=("#1E88E5", "#1565C0"),
            selected_hover_color=("#1976D2", "#0D47A1")
        )
        self.format_segmented.set("GIF")
        self.format_segmented.grid(row=row, column=1, sticky="w", padx=padding_x, pady=padding_y)

        row += 1

        # ----- Preserve Quality (only shown for Video Clips) -----
        self.preserve_quality_checkbox = ctk.CTkCheckBox(
            self.settings_frame,
            text="Preserve Quality (no FPS/resolution reduction)",
            font=ctk.CTkFont(size=13),
        )
        self.preserve_quality_checkbox.grid(row=row, column=0, columnspan=2, sticky="w", padx=padding_x, pady=(0, padding_y))
        self.preserve_quality_checkbox.grid_remove()  # Hidden by default (only for Video Clips)

        row += 1

        # ----- Upload to RedGIFs -----
        self.upload_checkbox = ctk.CTkCheckBox(
            self.settings_frame,
            text="Upload to RedGIFs after generation",
            font=ctk.CTkFont(size=13),
            command=self.on_upload_toggle
        )
        self.upload_checkbox.grid(row=row, column=0, columnspan=2, sticky="w", padx=padding_x, pady=padding_y)

        if not UPLOAD_AVAILABLE:
            self.upload_checkbox.configure(state="disabled")

        row += 1

        # Upload settings frame (expandable)
        self.upload_settings_frame = ctk.CTkFrame(self.settings_frame, fg_color=("#E8E8E8", "#2A2A3E"))
        self.upload_settings_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=padding_x, pady=(0, padding_y))
        self.upload_settings_frame.grid_columnconfigure(1, weight=1)
        self.upload_settings_frame.grid_remove()  # Hidden by default

        # Account dropdown
        upload_row = 0
        self.account_label = ctk.CTkLabel(self.upload_settings_frame, text="Account:", font=ctk.CTkFont(size=12))
        self.account_label.grid(row=upload_row, column=0, sticky="w", padx=10, pady=(10, 5))

        self.account_dropdown = ctk.CTkComboBox(
            self.upload_settings_frame,
            values=["Loading..."],
            width=150,
            state="readonly",
            command=self.on_account_change
        )
        self.account_dropdown.grid(row=upload_row, column=1, sticky="w", padx=10, pady=(10, 5))

        upload_row += 1

        # Tags
        self.tags_label = ctk.CTkLabel(self.upload_settings_frame, text="Tags:", font=ctk.CTkFont(size=12))
        self.tags_label.grid(row=upload_row, column=0, sticky="w", padx=10, pady=5)

        self.tags_entry = ctk.CTkEntry(self.upload_settings_frame, placeholder_text="Comma-separated tags")
        self.tags_entry.grid(row=upload_row, column=1, sticky="ew", padx=10, pady=5)

        upload_row += 1

        # Description
        self.desc_label = ctk.CTkLabel(self.upload_settings_frame, text="Description:", font=ctk.CTkFont(size=12))
        self.desc_label.grid(row=upload_row, column=0, sticky="nw", padx=10, pady=5)

        self.desc_textbox = ctk.CTkTextbox(self.upload_settings_frame, height=60)
        self.desc_textbox.grid(row=upload_row, column=1, sticky="ew", padx=10, pady=5)

        upload_row += 1

        # Content Type
        self.content_label = ctk.CTkLabel(self.upload_settings_frame, text="Content Type:", font=ctk.CTkFont(size=12))
        self.content_label.grid(row=upload_row, column=0, sticky="w", padx=10, pady=5)

        self.content_dropdown = ctk.CTkComboBox(
            self.upload_settings_frame,
            values=["Solo Female", "Solo Male", "Couple", "Group"],
            width=150,
            state="readonly"
        )
        self.content_dropdown.grid(row=upload_row, column=1, sticky="w", padx=10, pady=5)

        upload_row += 1

        # Keep Audio
        self.audio_checkbox = ctk.CTkCheckBox(self.upload_settings_frame, text="Keep Audio")
        self.audio_checkbox.grid(row=upload_row, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 10))

        row += 1

        # ----- Output Folder -----
        self.output_label = ctk.CTkLabel(
            self.settings_frame,
            text="Output Folder:",
            font=ctk.CTkFont(size=13),
            anchor="w"
        )
        self.output_label.grid(row=row, column=0, sticky="w", padx=padding_x, pady=(padding_y, padding_y))

        # Output folder frame
        self.output_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        self.output_frame.grid(row=row, column=1, sticky="ew", padx=padding_x, pady=(padding_y, padding_y))
        self.output_frame.grid_columnconfigure(0, weight=1)

        self.output_entry = ctk.CTkEntry(
            self.output_frame,
            placeholder_text="Same as input video",
            font=ctk.CTkFont(size=12),
            width=250
        )
        self.output_entry.grid(row=0, column=0, sticky="ew")

        self.output_browse_btn = ctk.CTkButton(
            self.output_frame,
            text="Browse",
            width=80,
            font=ctk.CTkFont(size=12),
            command=self.select_output_folder
        )
        self.output_browse_btn.grid(row=0, column=1, padx=(10, 0))

    def _create_generate_button(self):
        """Create the main generate button."""

        self.generate_btn = ctk.CTkButton(
            self.main_frame,
            text="Generate GIFs",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50,
            corner_radius=10,
            command=self.generate_gifs,
            fg_color=("#1E88E5", "#1565C0"),
            hover_color=("#1976D2", "#0D47A1")
        )
        self.generate_btn.grid(row=6, column=0, sticky="ew", pady=(10, 20))

    def _create_progress_section(self):
        """Create the progress bar and status display."""

        self.progress_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.progress_frame.grid(row=7, column=0, sticky="ew", pady=(0, 10))
        self.progress_frame.grid_columnconfigure(0, weight=1)

        # Bulk progress label (hidden by default, shown in bulk mode during processing)
        self.bulk_progress_label = ctk.CTkLabel(
            self.progress_frame,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#333333", "#FFFFFF"),
            anchor="w"
        )
        self.bulk_progress_label.grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.bulk_progress_label.grid_remove()  # Hidden by default

        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(
            self.progress_frame,
            height=12,
            corner_radius=6
        )
        self.progress_bar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.progress_bar.set(0)

        # Status label
        self.status_label = ctk.CTkLabel(
            self.progress_frame,
            text="Status: Ready",
            font=ctk.CTkFont(size=12),
            text_color=("#666666", "#AAAAAA"),
            anchor="w"
        )
        self.status_label.grid(row=2, column=0, sticky="w")

    # ===== EVENT HANDLERS =====

    def on_mode_change(self, value):
        """Handle mode toggle change."""
        self.bulk_mode = (value == "Bulk Folder")

        # Reset state
        self.video_path = None
        self.video_paths = []
        self.video_durations = {}
        self.video_duration = 0
        self.total_duration = 0

        # Update drop zone text based on mode
        if self.bulk_mode:
            self.drop_icon.configure(text="[FOLDER]", text_color=("#3B8ED0", "#5DADE2"))
            self.drop_label.configure(text="Click to Select Video Folder")
            self.drop_sublabel.configure(text="or Drop Folder Here")
            self.selected_label.configure(text="Selected: No folder selected")
            self.video_list_container.grid()  # Show video list
        else:
            self.drop_icon.configure(text="[VIDEO]", text_color=("#3B8ED0", "#5DADE2"))
            self.drop_label.configure(text="Drag & Drop Video Here")
            self.drop_sublabel.configure(text="or Click to Browse")
            self.selected_label.configure(text="Selected: No video selected")
            self.video_list_container.grid_remove()  # Hide video list

        # Reset other displays
        self.duration_label.configure(text="Duration: --:-- | Estimated GIFs: --")
        self._clear_video_list()

        # Clear output folder if it was auto-set
        self.output_entry.delete(0, "end")

    def select_video_or_folder(self):
        """Route to appropriate selection method based on mode."""
        if self.is_processing:
            return

        if self.bulk_mode:
            self.select_folder()
        else:
            self.select_video()

    def select_video(self):
        """Open file dialog to select a video file."""
        if self.is_processing:
            return

        filepath = filedialog.askopenfilename(
            title="Select a Video File",
            filetypes=self.VIDEO_FORMATS
        )

        if filepath:
            self.video_path = filepath
            filename = os.path.basename(filepath)

            # Truncate filename if too long
            display_name = filename if len(filename) <= 40 else filename[:37] + "..."
            self.selected_label.configure(text=f"Selected: {display_name}")

            # Update drop zone appearance
            self.drop_label.configure(text=filename if len(filename) <= 30 else filename[:27] + "...")
            self.drop_sublabel.configure(text="Click to change")
            self.drop_icon.configure(text="[OK]", text_color=("#28A745", "#5CB85C"))

            # Get video duration
            self._get_video_duration()

            # Set default output folder to video's directory
            if not self.output_entry.get():
                video_dir = os.path.dirname(filepath)
                self.output_entry.delete(0, "end")
                self.output_entry.insert(0, video_dir)

    def select_folder(self):
        """Open folder dialog to select a folder containing videos."""
        if self.is_processing:
            return

        folder_path = filedialog.askdirectory(
            title="Select Folder with Videos"
        )

        if folder_path:
            # Scan folder for video files
            self._scan_folder_for_videos(folder_path)

    def _scan_folder_for_videos(self, folder_path):
        """Scan a folder for video files and update the UI."""
        self.video_paths = []
        self.video_durations = {}
        self.total_duration = 0

        # Find all video files in the folder
        try:
            for filename in sorted(os.listdir(folder_path)):
                ext = os.path.splitext(filename)[1].lower()
                if ext in self.VIDEO_EXTENSIONS:
                    full_path = os.path.join(folder_path, filename)
                    if os.path.isfile(full_path):
                        self.video_paths.append(full_path)
        except Exception as e:
            messagebox.showerror("Error", f"Could not scan folder:\n{e}")
            return

        if not self.video_paths:
            messagebox.showwarning(
                "No Videos Found",
                f"No video files found in the selected folder.\n\nSupported formats: {', '.join(self.VIDEO_EXTENSIONS)}"
            )
            return

        # Update drop zone
        folder_name = os.path.basename(folder_path)
        self.drop_icon.configure(text="[OK]", text_color=("#28A745", "#5CB85C"))
        self.drop_label.configure(text=folder_name if len(folder_name) <= 30 else folder_name[:27] + "...")
        self.drop_sublabel.configure(text="Click to change folder")

        # Update selected label
        self.selected_label.configure(text=f"Selected: {folder_path}")

        # Set default output folder
        if not self.output_entry.get():
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, folder_path)

        # Get durations for all videos (in background thread to avoid UI freeze)
        self.status_label.configure(text="Status: Scanning videos...")
        thread = threading.Thread(target=self._scan_video_durations_worker, daemon=True)
        thread.start()

    def _scan_video_durations_worker(self):
        """Worker thread to get durations of all videos."""
        try:
            from core.gif_generator import get_video_duration

            for video_path in self.video_paths:
                try:
                    duration = get_video_duration(video_path)
                    self.video_durations[video_path] = duration
                except Exception:
                    self.video_durations[video_path] = 0

            # Calculate total duration
            self.total_duration = sum(self.video_durations.values())

            # Update UI on main thread
            self.after(0, self._update_bulk_ui)

        except ImportError:
            # Fallback using ffprobe directly
            for video_path in self.video_paths:
                try:
                    result = subprocess.run(
                        [
                            "ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of",
                            "default=noprint_wrappers=1:nokey=1",
                            video_path
                        ],
                        capture_output=True,
                        text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                    )
                    self.video_durations[video_path] = float(result.stdout.strip())
                except Exception:
                    self.video_durations[video_path] = 0

            self.total_duration = sum(self.video_durations.values())
            self.after(0, self._update_bulk_ui)

    def _update_bulk_ui(self):
        """Update UI after scanning videos (called on main thread)."""
        # Update video count label
        self.video_count_label.configure(text=f"({len(self.video_paths)} videos)")

        # Format total duration
        total_minutes = int(self.total_duration // 60)
        total_seconds = int(self.total_duration % 60)
        duration_str = f"{total_minutes}:{total_seconds:02d}"

        # Calculate total estimated GIFs
        gif_duration = int(self.duration_slider.get())
        total_estimated_gifs = sum(
            max(1, int(d // gif_duration)) for d in self.video_durations.values()
        )

        self.duration_label.configure(
            text=f"Found: {len(self.video_paths)} videos | Total Duration: {duration_str} | Estimated GIFs: {total_estimated_gifs}"
        )

        # Populate video list
        self._populate_video_list()

        self.status_label.configure(text="Status: Ready")

    def _clear_video_list(self):
        """Clear all items from the video list."""
        for widget in self.video_item_widgets:
            widget.destroy()
        self.video_item_widgets = []
        self.video_count_label.configure(text="")

    def _populate_video_list(self):
        """Populate the scrollable video list with video items."""
        self._clear_video_list()

        gif_duration = int(self.duration_slider.get())

        for i, video_path in enumerate(self.video_paths):
            filename = os.path.basename(video_path)
            duration = self.video_durations.get(video_path, 0)

            # Format duration
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            duration_str = f"({minutes}:{seconds:02d})"

            # Estimate GIFs for this video
            estimated_gifs = max(1, int(duration // gif_duration)) if duration > 0 else "?"

            # Create item frame
            item_frame = ctk.CTkFrame(
                self.video_list_scroll,
                fg_color=("#FFFFFF", "#363654") if i % 2 == 0 else ("#F0F0F0", "#2D2D45"),
                corner_radius=5,
                height=35
            )
            item_frame.grid(row=i, column=0, sticky="ew", pady=2, padx=5)
            item_frame.grid_columnconfigure(1, weight=1)
            item_frame.grid_propagate(False)

            # Filename label
            filename_label = ctk.CTkLabel(
                item_frame,
                text=filename if len(filename) <= 35 else filename[:32] + "...",
                font=ctk.CTkFont(size=11),
                text_color=("#333333", "#FFFFFF"),
                anchor="w"
            )
            filename_label.grid(row=0, column=0, sticky="w", padx=(10, 5), pady=5)

            # Duration label
            dur_label = ctk.CTkLabel(
                item_frame,
                text=duration_str,
                font=ctk.CTkFont(size=10),
                text_color=("#666666", "#AAAAAA"),
                width=50
            )
            dur_label.grid(row=0, column=1, sticky="e", padx=5, pady=5)

            # Estimated GIFs label
            gifs_label = ctk.CTkLabel(
                item_frame,
                text=f"~{estimated_gifs} GIFs",
                font=ctk.CTkFont(size=10),
                text_color=("#1E88E5", "#5DADE2"),
                width=60
            )
            gifs_label.grid(row=0, column=2, sticky="e", padx=(5, 10), pady=5)

            self.video_item_widgets.append(item_frame)

    def _get_video_duration(self):
        """Get the duration of the selected video using ffprobe."""
        if not self.video_path:
            return

        try:
            from core.gif_generator import get_video_duration
            self.video_duration = get_video_duration(self.video_path)
            self._update_duration_display()
        except ImportError:
            # Fallback: try using ffprobe directly
            try:
                result = subprocess.run(
                    [
                        "ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        self.video_path
                    ],
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
                self.video_duration = float(result.stdout.strip())
                self._update_duration_display()
            except Exception as e:
                self.duration_label.configure(text=f"Duration: Unable to read | Estimated GIFs: --")
                self.video_duration = 0
        except Exception as e:
            self.duration_label.configure(text=f"Duration: Unable to read | Estimated GIFs: --")
            self.video_duration = 0

    def _update_duration_display(self):
        """Update the duration and estimated GIFs display."""
        if self.bulk_mode:
            # For bulk mode, recalculate totals
            if self.video_durations:
                gif_duration = int(self.duration_slider.get())
                total_estimated_gifs = sum(
                    max(1, int(d // gif_duration)) for d in self.video_durations.values()
                )
                total_minutes = int(self.total_duration // 60)
                total_seconds = int(self.total_duration % 60)
                duration_str = f"{total_minutes}:{total_seconds:02d}"

                self.duration_label.configure(
                    text=f"Found: {len(self.video_paths)} videos | Total Duration: {duration_str} | Estimated GIFs: {total_estimated_gifs}"
                )

                # Also update video list with new estimates
                self._populate_video_list()
        else:
            # Single video mode
            if self.video_duration > 0:
                minutes = int(self.video_duration // 60)
                seconds = int(self.video_duration % 60)
                duration_str = f"{minutes}:{seconds:02d}"

                gif_duration = int(self.duration_slider.get())
                estimated_gifs = int(self.video_duration // gif_duration)

                self.duration_label.configure(
                    text=f"Duration: {duration_str} | Estimated GIFs: {estimated_gifs}"
                )

    def select_output_folder(self):
        """Open folder dialog to select output directory."""
        if self.is_processing:
            return

        initial_dir = self.output_entry.get() or (
            os.path.dirname(self.video_path) if self.video_path else None
        )

        folder = filedialog.askdirectory(
            title="Select Output Folder",
            initialdir=initial_dir
        )

        if folder:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, folder)

    def on_duration_change(self, value):
        """Handle duration slider change."""
        duration = int(value)
        self.duration_value_label.configure(text=f"{duration} seconds")
        self._update_duration_display()

    def on_format_change(self, value):
        """Handle output format change between GIF and Video Clips."""
        if value == "Video Clips":
            self.generate_btn.configure(text="Generate Clips")
            self.duration_setting_label.configure(text="Clip Duration:")
            self.preserve_quality_checkbox.grid()  # Show preserve quality option
        else:
            self.generate_btn.configure(text="Generate GIFs")
            self.duration_setting_label.configure(text="GIF Duration:")
            self.preserve_quality_checkbox.grid_remove()  # Hide preserve quality option

    def on_upload_toggle(self):
        """Handle upload checkbox toggle."""
        self.upload_enabled = self.upload_checkbox.get()
        if self.upload_enabled:
            self.upload_settings_frame.grid()
            self._load_accounts()
        else:
            self.upload_settings_frame.grid_remove()

    def _load_accounts(self):
        """Load accounts from the account manager."""
        if not UPLOAD_AVAILABLE:
            return
        try:
            # Add redgifs directory to path for imports
            import sys
            from pathlib import Path
            redgifs_path = str(Path(__file__).parent.parent / "uploaders" / "redgifs")
            if redgifs_path not in sys.path:
                sys.path.insert(0, redgifs_path)
            from redgifs_core.account_manager import AccountManager
            accounts_file = Path(__file__).parent.parent / "uploaders" / "redgifs" / "accounts.json"
            self.account_manager = AccountManager(accounts_file)
            accounts = self.account_manager.get_enabled_accounts()
            if accounts:
                names = [acc.name for acc in accounts]
                self.account_dropdown.configure(values=names)
                self.account_dropdown.set(names[0])
                self.on_account_change(names[0])
            else:
                self.account_dropdown.configure(values=["No accounts found"])
                self.account_dropdown.set("No accounts found")
        except Exception as e:
            print(f"Failed to load accounts: {e}")
            self.account_dropdown.configure(values=["Error loading accounts"])
            self.account_dropdown.set("Error loading accounts")

    def on_account_change(self, account_name):
        """Handle account dropdown change."""
        if not self.account_manager:
            return
        account = self.account_manager.get_account_by_name(account_name)
        if account:
            self.tags_entry.delete(0, "end")
            self.tags_entry.insert(0, ", ".join(account.tags))
            self.desc_textbox.delete("1.0", "end")
            self.desc_textbox.insert("1.0", account.description)
            self.content_dropdown.set(account.content_type)
            if account.keep_audio:
                self.audio_checkbox.select()
            else:
                self.audio_checkbox.deselect()
            self.selected_account = account

    def generate_gifs(self):
        """Start the GIF generation process."""

        # Validate input based on mode
        if self.bulk_mode:
            if not self.video_paths:
                messagebox.showwarning("No Videos Selected", "Please select a folder with videos first.")
                return
        else:
            if not self.video_path:
                messagebox.showwarning("No Video Selected", "Please select a video file first.")
                return

            if not os.path.exists(self.video_path):
                messagebox.showerror("File Not Found", "The selected video file no longer exists.")
                return

        # Get output folder
        output_folder = self.output_entry.get()
        if not output_folder:
            if self.bulk_mode and self.video_paths:
                output_folder = os.path.dirname(self.video_paths[0])
            elif self.video_path:
                output_folder = os.path.dirname(self.video_path)
            else:
                messagebox.showwarning("No Output Folder", "Please select an output folder.")
                return

        if not os.path.exists(output_folder):
            try:
                os.makedirs(output_folder)
            except Exception as e:
                messagebox.showerror("Error", f"Cannot create output folder:\n{e}")
                return

        # Disable UI during processing
        self.is_processing = True
        self.generate_btn.configure(state="disabled", text="Processing...")
        self.progress_bar.set(0)
        self.status_label.configure(text="Status: Starting...")

        # Get settings
        gif_duration = int(self.duration_slider.get())
        fps = int(self.fps_dropdown.get())
        resolution_key = self.resolution_dropdown.get()
        resolution = self.RESOLUTION_OPTIONS.get(resolution_key)
        output_format = "mp4" if self.format_segmented.get() == "Video Clips" else "gif"
        preserve_quality = self.preserve_quality_checkbox.get() if output_format == "mp4" else False

        # Start worker thread
        if self.bulk_mode:
            self.bulk_progress_label.grid()  # Show bulk progress label
            thread = threading.Thread(
                target=self._generate_bulk_worker,
                args=(self.video_paths.copy(), output_folder, gif_duration, fps, resolution, output_format, preserve_quality),
                daemon=True
            )
        else:
            self.bulk_progress_label.grid_remove()  # Hide bulk progress label
            thread = threading.Thread(
                target=self._generate_worker,
                args=(self.video_path, output_folder, gif_duration, fps, resolution, output_format, preserve_quality),
                daemon=True
            )
        thread.start()

    def _generate_worker(self, video_path, output_folder, gif_duration, fps, resolution, output_format, preserve_quality=False):
        """Worker function that runs in a separate thread (single video mode)."""
        try:
            from core.gif_generator import generate_gifs

            # Define progress callback
            def progress_callback(current, total):
                self.after(0, lambda: self._update_progress(current, total, output_format))

            # Generate GIFs or clips
            gif_paths = generate_gifs(
                video_path=video_path,
                output_folder=output_folder,
                gif_duration=gif_duration,
                fps=fps,
                resolution=resolution,
                progress_callback=progress_callback,
                output_format=output_format,
                preserve_quality=preserve_quality
            )

            # Signal completion on main thread
            self.after(0, lambda: self._on_complete(gif_paths, output_folder, output_format))

        except ImportError as e:
            error_msg = f"Missing module: {e}\n\nPlease ensure the core.gif_generator module is implemented."
            self.after(0, lambda: self._on_error(error_msg))
        except Exception as e:
            self.after(0, lambda: self._on_error(str(e)))

    def _generate_bulk_worker(self, video_paths, output_folder, gif_duration, fps, resolution, output_format, preserve_quality=False):
        """Worker function for bulk video processing."""
        try:
            from core.gif_generator import generate_gifs

            all_gif_paths = []
            total_videos = len(video_paths)

            for video_index, video_path in enumerate(video_paths):
                # Check if video still exists
                if not os.path.exists(video_path):
                    continue

                video_filename = os.path.splitext(os.path.basename(video_path))[0]

                # Update bulk progress label on main thread
                self.after(0, lambda vi=video_index, tv=total_videos, vf=video_filename:
                    self._update_bulk_progress(vi + 1, tv, vf))

                # Create subfolder for this video's output files
                video_output_folder = os.path.join(output_folder, video_filename)
                os.makedirs(video_output_folder, exist_ok=True)

                # Define progress callback for current video
                def progress_callback(current, total, vi=video_index, tv=total_videos, fmt=output_format):
                    self.after(0, lambda c=current, t=total: self._update_progress(c, t, fmt))

                try:
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
                    all_gif_paths.extend(gif_paths)
                except Exception as e:
                    # Log error but continue with next video
                    print(f"Error processing {video_path}: {e}")
                    continue

            # Signal completion on main thread
            self.after(0, lambda: self._on_bulk_complete(all_gif_paths, output_folder, total_videos, output_format))

        except ImportError as e:
            error_msg = f"Missing module: {e}\n\nPlease ensure the core.gif_generator module is implemented."
            self.after(0, lambda: self._on_error(error_msg))
        except Exception as e:
            self.after(0, lambda: self._on_error(str(e)))

    def _update_bulk_progress(self, current_video, total_videos, video_name):
        """Update the bulk progress label (called on main thread)."""
        self.bulk_progress_label.configure(
            text=f"Processing: {video_name} ({current_video} of {total_videos})"
        )

    def _update_progress(self, current, total, output_format="gif"):
        """Update progress bar and status label (called on main thread)."""
        if total > 0:
            progress = current / total
            self.progress_bar.set(progress)
            percentage = int(progress * 100)
            format_name = "clip" if output_format == "mp4" else "GIF"
            self.status_label.configure(
                text=f"Status: Creating {format_name} {current} of {total} ({percentage}%)"
            )

    def _on_complete(self, gif_paths, output_folder, output_format="gif"):
        """Handle completion of GIF/clip generation (called on main thread)."""
        self.progress_bar.set(1)
        self.bulk_progress_label.grid_remove()

        count = len(gif_paths) if gif_paths else 0
        format_name = "clips" if output_format == "mp4" else "GIFs"
        format_singular = "Clip" if output_format == "mp4" else "GIF"
        btn_text = "Generate Clips" if output_format == "mp4" else "Generate GIFs"

        # Check if upload is enabled
        if self.upload_enabled and UPLOAD_AVAILABLE and self.selected_account and gif_paths:
            self.status_label.configure(text=f"Status: Generated {count} {format_name}. Starting upload...")
            thread = threading.Thread(
                target=self._upload_files_worker,
                args=(gif_paths, self.selected_account.name, output_folder),
                daemon=True
            )
            thread.start()
        else:
            self.is_processing = False
            self.generate_btn.configure(state="normal", text=btn_text)
            self.status_label.configure(text=f"Status: Complete! Generated {count} {format_name}")

            # Show completion dialog
            result = messagebox.askquestion(
                "Generation Complete",
                f"Successfully generated {count} {format_name}!\n\nWould you like to open the output folder?",
                icon="info"
            )

            if result == "yes":
                self._open_folder(output_folder)

    def _on_bulk_complete(self, gif_paths, output_folder, total_videos, output_format="gif"):
        """Handle completion of bulk GIF/clip generation (called on main thread)."""
        self.progress_bar.set(1)
        self.bulk_progress_label.grid_remove()

        count = len(gif_paths) if gif_paths else 0
        format_name = "clips" if output_format == "mp4" else "GIFs"
        btn_text = "Generate Clips" if output_format == "mp4" else "Generate GIFs"

        # Check if upload is enabled
        if self.upload_enabled and UPLOAD_AVAILABLE and self.selected_account and gif_paths:
            self.status_label.configure(text=f"Status: Generated {count} {format_name} from {total_videos} videos. Starting upload...")
            thread = threading.Thread(
                target=self._upload_files_worker,
                args=(gif_paths, self.selected_account.name, output_folder),
                daemon=True
            )
            thread.start()
        else:
            self.is_processing = False
            self.generate_btn.configure(state="normal", text=btn_text)
            self.status_label.configure(text=f"Status: Complete! Generated {count} {format_name} from {total_videos} videos")

            # Show completion dialog
            result = messagebox.askquestion(
                "Bulk Generation Complete",
                f"Successfully generated {count} {format_name} from {total_videos} videos!\n\nEach video's {format_name} are in their own subfolder.\n\nWould you like to open the output folder?",
                icon="info"
            )

            if result == "yes":
                self._open_folder(output_folder)

    def _on_error(self, error_msg):
        """Handle errors during GIF generation (called on main thread)."""
        self.is_processing = False
        self.generate_btn.configure(state="normal", text="Generate GIFs")
        self.progress_bar.set(0)
        self.status_label.configure(text="Status: Error occurred")
        self.bulk_progress_label.grid_remove()

        messagebox.showerror("Error", f"An error occurred during processing:\n\n{error_msg}")

    def _upload_files_worker(self, file_paths, account_name, output_folder):
        """Upload files after generation (runs in background thread)."""
        try:
            # Refresh tokens
            UploadBridge.refresh_tokens()

            # Get override settings from UI
            tags_text = self.tags_entry.get()
            tags = [t.strip() for t in tags_text.split(",")] if tags_text else []

            override_settings = {
                "tags": tags,
                "description": self.desc_textbox.get("1.0", "end").strip(),
                "content_type": self.content_dropdown.get(),
                "keep_audio": self.audio_checkbox.get()
            }

            bridge = UploadBridge(account_name, override_settings)

            # Upload each file
            results = []
            for i, file_path in enumerate(file_paths, 1):
                self.after(0, lambda c=i, t=len(file_paths):
                    self.status_label.configure(text=f"Status: Uploading {c}/{t}..."))

                result = asyncio.run(bridge.upload_single_file(file_path, i, len(file_paths)))
                results.append(result)

            self.after(0, lambda: self._on_upload_complete(results, output_folder))
        except Exception as e:
            self.after(0, lambda: self._on_upload_error(str(e), output_folder))

    def _on_upload_complete(self, results, output_folder):
        """Handle upload completion (called on main thread)."""
        self.is_processing = False
        self.generate_btn.configure(state="normal", text="Generate GIFs")

        success_count = sum(1 for r in results if r["success"])
        failed_count = len(results) - success_count
        self.status_label.configure(text=f"Status: Uploaded {success_count} success, {failed_count} failed")

        # Show completion dialog
        if failed_count > 0:
            result = messagebox.askquestion(
                "Upload Complete",
                f"Upload complete!\n\nSuccessful: {success_count}\nFailed: {failed_count}\n\nWould you like to open the output folder?",
                icon="warning"
            )
        else:
            result = messagebox.askquestion(
                "Upload Complete",
                f"Successfully uploaded {success_count} files to RedGIFs!\n\nWould you like to open the output folder?",
                icon="info"
            )

        if result == "yes":
            self._open_folder(output_folder)

    def _on_upload_error(self, error_msg, output_folder):
        """Handle upload errors (called on main thread)."""
        self.is_processing = False
        self.generate_btn.configure(state="normal", text="Generate GIFs")
        self.status_label.configure(text="Status: Upload error")

        result = messagebox.askquestion(
            "Upload Error",
            f"Upload failed: {error_msg[:100]}\n\nGIFs were generated successfully.\n\nWould you like to open the output folder?",
            icon="error"
        )

        if result == "yes":
            self._open_folder(output_folder)

    def _open_folder(self, folder_path):
        """Open the specified folder in the system file explorer."""
        try:
            if sys.platform == "win32":
                os.startfile(folder_path)
            elif sys.platform == "darwin":
                subprocess.run(["open", folder_path])
            else:
                subprocess.run(["xdg-open", folder_path])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder:\n{e}")


def main():
    """Application entry point."""
    app = GifMakeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
