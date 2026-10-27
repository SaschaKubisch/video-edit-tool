"""
Configuration for Dual-System Audio Video Automation.
Designed for BMPCC 4K + Zoom H2N workflow. Constants only.
"""

VERSION = "1.1.0"

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

# ── Preview copies (the `convert` command, SPECS section 6) ─────
# Values are the ones measured in step 1 (SPECS 6.1 C).
PREVIEW_SCALE = 2              # decode at 1/2 size: 4096 x 2160 -> 2048 x 1080
PREVIEW_ENCODER = "h264_videotoolbox"   # or "libx264" (software; slow)
PREVIEW_Q = 73                 # h264_videotoolbox quality, -q:v (libx264: see convert.py)
PREVIEW_GOP = 30               # a key frame every 30 frames (about 1 s)
PREVIEW_AUDIO_KBPS = 192       # camera audio as AAC
CONVERSION_FILE = "conversion.json"   # in the work directory
# Resolve's LUT folder. The tool reads LUTs from here and never copies them.
RESOLVE_LUT_DIR = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT"
BASE_LUT_FILE = "Blackmagic Gen 5 Film to Video.cube"   # decision D2
EXPECTED_COLOUR_SCIENCE_GEN = "5"   # the default LUT is made for generation 5
# Space estimate (C6): size per minute of a copy of a 4096 x 2160 clip, from
# step 1 (70.7 MB). Used until a clip of that kind was converted and measured.
PREVIEW_PRIOR_MB_PER_MIN_DCI4K = 71.0
PREVIEW_SPACE_MARGIN = 1.25    # the size depends on the picture

# ── Sync clap (`analyse --clap`, SPECS 7.2, decision D6: only at the start) ──
# All values are starting values; the owner corrects them from the report.
CLAP_SEARCH_SEC = 20          # the clap is searched in the first N seconds
CLAP_HIGHPASS_HZ = 2000       # a clap is broadband; wind and room tone sit lower
CLAP_WINDOW_SEC = 0.002       # power is measured in windows of 2 ms
CLAP_MIN_ONSET = 2.0          # rise of log power between two windows (about 7 times)
CLAP_LOCAL_MAX_SEC = 0.05     # a candidate is the strongest onset within +-50 ms
CLAP_DECAY_SEC = 0.1          # a clap decays within 100 ms ...
CLAP_DECAY_FRACTION = 0.2     # ... to 20 percent of its peak power
CLAP_REFINE_FRACTION = 0.25   # to the first sample at a quarter of the local peak
CLAP_AGREE_SEC = 0.02         # camera and WAV claps agree within 20 ms of the offset
CLAP_PAIR_BAND_DB = 10.0      # the clap is the first shared event within 10 dB of the loudest
CLAP_WEAK_CONFIDENCE = 0.5    # a clap with a lower confidence is marked for review
CLAP_GUARD_SEC = 0.5          # the film starts 0.5 s after the clap (D6)
CLAP_LATE_SEC = 10.0          # a clap later than this is flagged for review
# 96 of 105 offsets of the first shoot lie in this range (PRD section 5 says 0.09 to
# 0.37 s; the lowest of the 96 is 0.0851 s, so the lower end is 0.085). An offset
# outside it is flagged for review.
USUAL_OFFSET_RANGE_SEC = (0.085, 0.37)

# ── Drift (`analyse --drift`, SPECS 7.3) ──
# The matcher measures the offset on the first 60 s. The drift measures it again on the
# last 60 s of every clip of at least 120 s (shorter clips would have overlapping windows).
DRIFT_WINDOW_SEC = 60         # the length of the head window (the matcher's) and of the tail window
DRIFT_MIN_CLIP_SEC = 120      # shorter clips are skipped
DRIFT_LIMIT_FRAMES = 0.5      # a clip whose drift exceeds half a frame is marked (SPECS 7.3)
DRIFT_PLAUSIBLE_SEC = 0.25    # a change of the offset beyond this is no clock drift: no common sound was found
DRIFT_DEFAULT_FPS = (30000, 1001)   # when conversion.json has no frame rate for the clip (first shoot: 29.97)

# ── Steady scenes (`analyse --scenes`, SPECS 7.4, decision D7: every steady scene) ──
# All values are starting values; the owner corrects them from the report.
STABLE_SAMPLE_FPS = 5         # the analysis looks at about 5 frames per second
STABLE_WIDTH_PX = 480         # ... scaled to 480 pixels wide, grey
STABLE_GRID = (4, 3)          # columns and rows of blocks; each block is measured on its own
STABLE_MOVE_PX = 0.5          # camera shift between two samples that counts as movement
STABLE_DRIFT_PX = 3.0         # shift against the first sample of the run that counts as movement
STABLE_MIN_PEAK = 0.15        # a block whose correlation peak is lower is not used
STABLE_MIN_INLIERS = 3        # blocks that must agree on one camera motion to measure a step
STABLE_INLIER_PX = 0.75       # blocks agree when they differ by less than this
STABLE_CUT_DIFF = 0.12        # mean picture change (0 to 1) that counts as a cut when no block can be measured
STABLE_MIN_RUN_SEC = 2.0      # D7: a scene shorter than this is dropped
STABLE_MERGE_GAP_SEC = 0.5    # a shake shorter than this that ends where it began does not split a scene
STABLE_GUARD_SEC = 0.3        # each scene loses this much at both ends, so the camera has settled

# ── Sync offset typed by hand (SPECS 3.2 `audio_offset_hand_s`, rule V23) ──
# A starting value. The offsets of the first shoot lie between -5.03 and +1.94 s; a typed
# offset beyond this is taken for a mistake (for example milliseconds typed as seconds).
OFFSET_HAND_MAX_SEC = 30.0

# ── Assembly (`assemble`, SPECS 7.5; decisions D5 to D8) ──
TIMELINE_FILE = "timeline.json"      # next to the project file
FILM_FRAME = (4096, 2160)            # D5: DCI 4K, wide clips get black bars
FILM_FIT = "fit"
FILM_FADE_IN_SEC = 1.0               # D8: the film fades in over 1 s ...
FILM_FADE_OUT_SEC = 2.0              # ... and out over 2 s (starting values)
FILM_JOIN_SEC = 0.5                  # D8: the sound crossfades over 0.5 s at every join
FILM_JOIN_CURVE = "equal_power"
AUDIO_SAMPLE_RATE = 48000

# ── Film preview (`preview`, SPECS 5.5) ──
FILM_PREVIEW_WIDTH = 960             # pixels wide; the height follows the film frame
FILM_PREVIEW_ENCODER = "h264_videotoolbox"
FILM_PREVIEW_Q = 60                  # h264_videotoolbox quality, -q:v
FILM_PREVIEW_GOP = 12                # short groups of pictures, so seeking is quick
FILM_PREVIEW_AUDIO_KBPS = 160

# ── Loudness (decision D9, "Each scene, then film"; SPECS 5.4, 8.4) ──
# The code has no built-in loudness mode: the timeline file carries it. `loudness set` writes
# these values (the numbers shown to the owner with the question) when the mode is turned on.
LOUDNESS_MODE = "segment_then_film"
LOUDNESS_TARGET_LUFS = -14.0       # integrated loudness of the film
LOUDNESS_TRUE_PEAK_DBTP = -1.0     # the true peak stays at or under this
LOUDNESS_MAX_GAIN_DB = 12.0        # most that levelling adds to a quiet segment (starting value)

# ── Final film (`render`, SPECS 5.3 and 5.6) ──
EXPORT_GOP = 30                    # a key frame every 30 frames (about 1 s), as in the tested chain
EXPORT_LOG_EVERY_SEC = 30          # the render prints its progress this often

# ── Words (step 4, SPECS section 9): subtitles, titles, Whisper, translation ──
# Decisions of 2026-10-27: D12 model large-v3-turbo (file named by VET_WHISPER_MODEL), D13 form
# is a setting of each export, font Inter (folder named by VET_FONT_DIR), one style for all films.
SUBTITLE_LANGUAGE = "en"
SUBTITLE_FORMS = ("srt", "burned", "both", "none")
# Starting values, not decisions (D13): the form an export gets when it names none.
SUBTITLE_FORM_DEFAULT = {"film": "srt", "short": "burned"}

# Readability limits (SPECS 9.3). Sources: the Netflix English Timed Text Style Guide (42
# characters per line, two lines at most, a bottom-heavy shape, 17 characters per second for
# children's programmes and 20 for adults', 5/6 s to 7 s per subtitle, a gap of two frames);
# the owner asked for about 17 characters per second and 1 to 7 s.
SUBTITLE_MAX_LINES = 2
SUBTITLE_MAX_CPS = 17.0            # characters per second a viewer can read (spaces count)
SUBTITLE_MIN_SEC = 1.0             # shortest cue the spreading makes (where the scene allows)
SUBTITLE_MAX_SEC = 7.0             # longest cue
SUBTITLE_GAP_SEC = 0.1             # the gap the spreading leaves between two cues (starting value)
SUBTITLE_MIN_GAP_FRAMES = 2        # a gap below this is a warning (Netflix: two frames)
SUBTITLE_CLIP_KEEP_SEC = 0.5       # a trim that leaves less than this of a cue drops the cue
SUBTITLE_FILLERS = ("um", "umm", "uh", "uhh", "uhm", "er", "erm", "errm", "ehm")

# The one style (SPECS 9.5). Sizes are fractions of the frame height, so the preview and
# every export size look the same. Fonts come from VET_FONT_DIR.
TEXT_FONT_DIR_ENV = "VET_FONT_DIR"
TEXT_STYLE = {
    "subtitle_font": "Inter-SemiBold.ttf",
    "title_font": "Inter-Bold.ttf",
    "colour": (255, 255, 255),
    "outline_colour": (0, 0, 0),
    "outline_em": 0.075,            # outline width, in em
    "shadow_offset_em": 0.045,
    "shadow_blur_em": 0.05,
    "shadow_alpha": 0.6,
    "line_height_em": 1.2,
}
# Layouts by the shape of the frame. `safe` is the margin of the safe area as a fraction of the
# frame: (left, top, right, bottom). 16:9: the graphics safe area of EBU R 95 (5 percent on every
# side). 9:16: the margins of SPECS 11.6 (secondary source). `size_pct` is the font size as a
# percent of the frame height; `bottom_pct` lifts the last line this far above the bottom of the
# safe area, as a percent of the picture height; `max_chars` is the line length in characters.
TEXT_LAYOUTS = {
    "16:9": {"safe": (0.05, 0.05, 0.05, 0.05), "size_pct": 4.5, "bottom_pct": 2.5, "max_chars": 42},
    "9:16": {"safe": (0.04, 0.15, 0.18, 0.35), "size_pct": 3.3, "bottom_pct": 0.0, "max_chars": 24},
}
# Titles (SPECS 9.7): font size in percent of the frame height, the box and where it sits.
# `y_ref` says what `y_pct` means: the middle of the text block ("center") or its bottom edge
# ("bottom"). `align` is the alignment of the lines; a left-aligned title starts at the left
# edge of the safe area.
TITLE_TEMPLATES = {
    "center": {"size_pct": 7.0, "align": "center", "y_pct": 50.0, "y_ref": "center", "max_width_pct": 80.0},
    "lower_third": {"size_pct": 4.8, "align": "left", "y_pct": 72.0, "y_ref": "bottom", "max_width_pct": 60.0},
    "opening": {"size_pct": 9.0, "align": "center", "y_pct": 45.0, "y_ref": "center", "max_width_pct": 80.0},
}
TITLE_MAX_LINES = 3
TITLE_FADE_SEC = 0.5

# Whisper (D12, SPECS 9.2). The names of the files are settings; nothing is downloaded at run time.
WHISPER_THREADS = 4
WHISPER_MIN_MEAN_PROB = 0.55       # a phrase whose tokens average a lower probability is dropped
WHISPER_MIN_ACTIVITY = 0.10        # ... or whose sound is mostly at the noise floor
WHISPER_REPEAT_RUN = 3             # the same phrase this many times in a row is a loop and is dropped

# Translation through OpenRouter (SPECS 9.9). The key is read from OPENROUTER_API_KEY only.
TRANSLATE_LANGUAGES = ("pt-BR", "zh-Hans", "th", "de", "ja", "ru")
TRANSLATE_BATCH = 30               # cues per request
TRANSLATE_CONTEXT = 3              # cues of context before and after a batch
TRANSLATE_TRIES = 3                # requests per batch before the run stops


def layout_key(width: int, height: int) -> str:
    """The key of TEXT_LAYOUTS for a frame: "9:16" for a frame that is higher than wide, else "16:9"."""
    return "9:16" if height > width else "16:9"
