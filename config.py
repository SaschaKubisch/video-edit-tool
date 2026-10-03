"""
Configuration for Dual-System Audio Video Automation.
Designed for BMPCC 4K + Zoom H2N workflow.
"""

# ── File discovery ───────────────────────────────────────────────
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".mxf"}
AUDIO_EXTENSIONS = {".wav", ".flac", ".aiff", ".aif"}

# ── Audio matching ───────────────────────────────────────────────
# Sample rate to downsample to for cross-correlation (lower = faster matching)
MATCH_SAMPLE_RATE = 16000

# Minimum cross-correlation score to consider a match valid (0.0 - 1.0)
MATCH_MIN_CONFIDENCE = 0.02

# Maximum duration (seconds) of audio to use for matching.
# Using the first N seconds is enough and keeps matching fast.
MATCH_MAX_DURATION_SEC = 60

# ── Thumbnail extraction ────────────────────────────────────────
# Time offset (seconds) into each clip to grab the thumbnail
THUMBNAIL_TIME_SEC = 3.0
THUMBNAIL_WIDTH = 480
THUMBNAIL_FORMAT = "jpg"
THUMBNAIL_QUALITY = 85  # JPEG quality (1-100)

# ── Sync output ──────────────────────────────────────────────────
# Output audio codec and quality (PCM 24-bit matches Zoom H2N native)
OUTPUT_AUDIO_CODEC = "pcm_s24le"

# Video codec for output. "copy" = no re-encoding (fast, preserves 4K quality).
# Only falls back to re-encoding when trimming requires it.
OUTPUT_VIDEO_CODEC = "copy"

# Re-encoding settings (used only when stream copy isn't possible)
REENCODE_CODEC = "libx264"
REENCODE_CRF = 18
REENCODE_PRESET = "medium"
REENCODE_PIX_FMT = "yuv420p"

# ── Naming ───────────────────────────────────────────────────────
# Pattern for renamed files. {index} = scene number, {label} = optional label.
RENAME_PATTERN = "scene_{index:02d}"

# ── Project file ─────────────────────────────────────────────────
# All match results, selections, and offsets are stored here
PROJECT_FILE = "project.json"

# ── Visual selector ─────────────────────────────────────────────
SELECTOR_PORT = 8765
SELECTOR_HTML = "scene_selector.html"
