# Product Requirements Document: GifMake

## Overview

**Product Name:** GifMake
**Type:** Desktop Application (Local)
**Purpose:** Convert long videos into multiple short, social-media-ready GIFs with adjustable length and quality settings.

---

## Problem Statement

Creating GIFs from videos for social media is tedious. Users must manually:
- Open video editing software
- Find interesting segments
- Export each segment individually
- Convert to GIF format
- Optimize file size

**GifMake** automates this entire workflow with a single click.

---

## Target Users

- Social media content creators
- Marketing professionals
- Anyone who wants to quickly create GIFs from video content

---

## Core Features

### 1. Video Input
- **Supported formats:** MP4, MOV, AVI, MKV, WebM
- **Input method:** Drag-and-drop or file browser
- **No file size limit** (processes locally)

### 2. GIF Generation Settings

#### Length Control
| Setting | Options |
|---------|---------|
| GIF Duration | 1-10 seconds (user-defined) |
| Default | 4 seconds |

#### Quality Control
| Setting | Options |
|---------|---------|
| Frame Rate | 10, 15, 20, 24, 30 FPS |
| Resolution | Original, 720p, 480p, 360p, Custom |
| Color Palette | 64, 128, 256 colors |
| Default | 15 FPS, 480p, 256 colors |

### 3. Batch Processing
- Input: Single video of any length (e.g., 5 minutes)
- Output: Multiple GIFs of specified duration
- Example: 5-minute video → 75 GIFs at 4 seconds each

### 4. Output Options
- **Output folder:** User-selectable (default: same as input)
- **Naming convention:** `{original_name}_gif_001.gif`, `_002.gif`, etc.
- **Preview:** Optional preview of first GIF before full batch

---

## User Interface

### Main Window
```
┌─────────────────────────────────────────────────────┐
│  GifMake                                       [—][×]│
├─────────────────────────────────────────────────────┤
│                                                     │
│   ┌─────────────────────────────────────────────┐   │
│   │                                             │   │
│   │     Drag & Drop Video Here                  │   │
│   │         or Click to Browse                  │   │
│   │                                             │   │
│   └─────────────────────────────────────────────┘   │
│                                                     │
│   Selected: my_video.mp4 (5:32)                     │
│                                                     │
│   ─────────── Settings ───────────                  │
│                                                     │
│   GIF Duration:  [ 4 ] seconds                      │
│                                                     │
│   Frame Rate:    ( ) 10  (•) 15  ( ) 20  ( ) 30 FPS │
│                                                     │
│   Resolution:    [▼ 480p                    ]       │
│                                                     │
│   Output Folder: [C:\Users\...\GIFs    ] [Browse]   │
│                                                     │
│   ─────────────────────────────────────────────     │
│                                                     │
│   Estimated output: 83 GIFs                         │
│                                                     │
│          ┌──────────────────────┐                   │
│          │   Generate GIFs      │                   │
│          └──────────────────────┘                   │
│                                                     │
│   [████████████████░░░░░░░░] 67% - Creating GIF 56  │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### Workflow
1. User drags video file onto window (or clicks to browse)
2. App displays video duration and estimated GIF count
3. User adjusts settings (duration, quality)
4. User clicks "Generate GIFs"
5. Progress bar shows conversion status
6. Completion notification with "Open Folder" button

---

## Technical Requirements

### Technology Stack
- **Language:** Python 3.10+
- **GUI Framework:** PyQt6 or Tkinter
- **Video Processing:** FFmpeg (via ffmpeg-python)
- **Packaging:** PyInstaller (standalone .exe)

### Performance
- Process videos locally (no internet required)
- Utilize multi-threading for batch processing
- Memory-efficient streaming (don't load entire video into RAM)

### System Requirements
- **OS:** Windows 10/11
- **RAM:** 4GB minimum
- **Disk:** Space for output GIFs (varies by settings)

---

## Non-Functional Requirements

1. **Simplicity:** Maximum 3 clicks from launch to GIF generation
2. **Speed:** Process 1 minute of video in under 30 seconds (typical hardware)
3. **Reliability:** Handle corrupt/incomplete videos gracefully
4. **Portability:** Single executable, no installation required

---

## Future Enhancements (Out of Scope for MVP)

- [ ] Manual segment selection (scrubber to pick specific moments)
- [ ] Text/caption overlay on GIFs
- [ ] Smart scene detection (auto-find interesting moments)
- [ ] Preset profiles (Twitter, Discord, Reddit optimal settings)
- [ ] GIF compression/optimization post-processing
- [ ] Batch input (multiple videos at once)

---

## Success Criteria

1. User can convert a 5-minute video into 4-second GIFs with one click
2. All GIFs are valid and playable in browsers/social media
3. Settings changes reflect in output quality as expected
4. Application runs without requiring separate FFmpeg installation

---

## File Structure

```
gifmake/
├── src/
│   ├── main.py           # Application entry point
│   ├── gui/
│   │   ├── main_window.py
│   │   └── widgets.py
│   ├── core/
│   │   ├── video_processor.py
│   │   ├── gif_generator.py
│   │   └── settings.py
│   └── utils/
│       └── ffmpeg_wrapper.py
├── assets/
│   └── icon.ico
├── tests/
├── requirements.txt
├── README.md
└── docs/
    └── prd.md
```

---

## Acceptance Criteria

- [ ] Application launches and displays main window
- [ ] User can select video file via drag-drop or file browser
- [ ] Duration slider works (1-10 seconds)
- [ ] Frame rate selection works
- [ ] Resolution dropdown works
- [ ] "Generate GIFs" button creates correct number of GIFs
- [ ] Progress bar updates during processing
- [ ] Output GIFs match specified duration and quality
- [ ] Error handling for invalid video files
