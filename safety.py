"""
Protection of the originals (PRD M9).

The tool opens the .braw and WAV originals read-only and never writes into
a folder that holds them. Every writer of a new file calls
assert_not_inside() with the path it is about to write.
"""

import os
from pathlib import Path


class OriginalsProtectedError(Exception):
    """A write was refused because the target lies inside a folder that holds originals."""


def _real(path) -> str:
    # realpath resolves symbolic links and "..", also for a path whose last
    # parts do not exist yet.
    return os.path.realpath(os.fspath(path))


def assert_not_inside(path, folders) -> None:
    """Raise OriginalsProtectedError when `path` is one of `folders` or lies below one."""
    target = _real(path)
    for folder in folders:
        base = _real(folder)
        if target == base or target.startswith(base.rstrip(os.sep) + os.sep):
            raise OriginalsProtectedError(
                f"Refusing to write {target}: it lies inside {base}, "
                f"a folder that holds original recordings.")


def originals_folders(project) -> list:
    """The folders that hold the originals of a project: the parent folder of
    every matched video and audio file, and the two folders named in the project."""
    folders = []

    def add(p):
        if p and p not in folders:
            folders.append(p)

    add(project.video_dir)
    add(project.audio_dir)
    for m in project.matches:
        d = m if isinstance(m, dict) else m.__dict__
        for key in ("video_path", "audio_path"):
            if d.get(key):
                add(str(Path(d[key]).parent))
    return folders
