"""Find the external programs the tool starts."""

import os
import shutil
from pathlib import Path


def ffmpeg_path() -> str:
    """VET_FFMPEG if set, else ffmpeg on the PATH."""
    explicit = os.environ.get("VET_FFMPEG")
    if explicit:
        return explicit
    found = shutil.which("ffmpeg")
    if not found:
        raise FileNotFoundError("ffmpeg not found. Install it with: brew install ffmpeg")
    return found


def ffprobe_path() -> str:
    """The ffprobe that belongs to VET_FFMPEG if set, else ffprobe on the PATH."""
    explicit = os.environ.get("VET_FFMPEG")
    if explicit:
        sibling = Path(explicit).with_name("ffprobe")
        if sibling.exists():
            return str(sibling)
    found = shutil.which("ffprobe")
    if not found:
        raise FileNotFoundError("ffprobe not found. Install it with: brew install ffmpeg")
    return found
