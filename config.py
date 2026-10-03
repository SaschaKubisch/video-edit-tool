"""
Configuration for Dual-System Audio Video Automation.
Designed for BMPCC 4K + Zoom H2N workflow. Constants only.
"""

# ── File discovery ───────────────────────────────────────────────
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".mxf"}
AUDIO_EXTENSIONS = {".wav", ".flac", ".aiff", ".aif"}

# ── Audio matching ───────────────────────────────────────────────
# Sample rate to downsample to for cross-correlation (lower = faster matching)
MATCH_SAMPLE_RATE = 16000

# Maximum duration (seconds) of audio to use for matching.
# Using the first N seconds is enough and keeps matching fast.
MATCH_MAX_DURATION_SEC = 60

# ── Thumbnail extraction ────────────────────────────────────────
# Time offset (seconds) into each clip to grab the thumbnail
THUMBNAIL_TIME_SEC = 3.0
THUMBNAIL_WIDTH = 480
THUMBNAIL_FORMAT = "jpg"
THUMBNAIL_QUALITY = 85  # JPEG quality (1-100)

# ── Naming ───────────────────────────────────────────────────────
# Pattern for the legacy `rename` command. {index} = scene number, {label} = optional label.
RENAME_PATTERN = "scene_{index:02d}"

# ── Project file ─────────────────────────────────────────────────
# All match results, selections, and offsets are stored here (relative to CWD)
PROJECT_FILE = "project.json"

# ── Web app ─────────────────────────────────────────────────────
SELECTOR_PORT = 8765  # HTTP port (127.0.0.1 only)
