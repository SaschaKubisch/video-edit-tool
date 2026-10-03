"""
Configuration for Dual-System Audio Video Automation.
Designed for BMPCC 4K + Zoom H2N workflow. Constants only.
"""

# ── File discovery ───────────────────────────────────────────────
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".mxf", ".braw"}
AUDIO_EXTENSIONS = {".wav", ".flac", ".aiff", ".aif"}

# ── Audio matching ───────────────────────────────────────────────
# Videos and audio files are both recorded and named in sequential order, so
# pairs must not cross: if video i < video j are matched to audio a and b,
# then a < b.  Either side may have unmatched files anywhere (not recorded).
# Set to False to use a plain global duration assignment (Hungarian) that
# ignores file order, e.g. when mixing cards whose numbering resets.
MATCH_PRESERVE_ORDER = True

# Order-preserving matching only: pairs whose duration difference (seconds)
# exceeds this are never matched; both files are left unmatched instead.
# Real pairs differ by 0.1-0.3 s (a few seconds at worst).
MATCH_MAX_DURATION_DIFF_SEC = 10.0

# Order-preserving matching only: cost (seconds of duration difference) of
# leaving ONE file unmatched.  Skipping a video and an audio file costs twice
# this, so a pair is matched whenever its duration difference is below
# 2 * MATCH_SKIP_PENALTY (and within the tolerance above).
MATCH_SKIP_PENALTY = 6.0

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
