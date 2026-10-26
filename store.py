"""
Saving the timeline file (SPECS 4.1).

- Atomic: the new file is written to a temporary file in the same folder and renamed
  over the old one (Timeline.save). A crash never leaves a half-written file.
- Snapshots: before the file is replaced, the old file is copied to
  timeline.history/NNNN.json (numbers rising, the newest HISTORY_KEEP kept).
- Hash check: a writer that started from a version passes that version's hash; if the
  file on disk has another hash, the save is refused with StaleHash and nothing changes.

Built so far: save with snapshot and hash check. `timeline undo` and `timeline history`
(SPECS 4.3) are not built yet.
"""

import os
import re
import shutil

import timeline

HISTORY_DIR = "timeline.history"
HISTORY_KEEP = 200
_SNAPSHOT = re.compile(r"^(\d{4,})\.json$")


class StaleHash(Exception):
    """The file on disk is not the version the writer started from."""

    def __init__(self, expected, actual):
        super().__init__("The timeline file changed since it was read "
                         f"(expected {str(expected)[:8]}, found {str(actual)[:8]}).")
        self.expected = expected
        self.actual = actual


def history_dir(path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(path)), HISTORY_DIR)


def list_snapshots(path: str) -> list:
    """The snapshot files of the timeline file at `path`, oldest first."""
    folder = history_dir(path)
    if not os.path.isdir(folder):
        return []
    names = sorted(n for n in os.listdir(folder) if _SNAPSHOT.match(n))
    return [os.path.join(folder, n) for n in names]


def current_hash(path: str):
    """The sha256 of the file, or None when it does not exist."""
    return timeline.file_hash(path) if os.path.isfile(path) else None


def snapshot(path: str, keep: int = HISTORY_KEEP):
    """Copy the file at `path` to the history folder. Returns the snapshot's path,
    or None when there is no file. Older snapshots beyond `keep` are removed."""
    if not os.path.isfile(path):
        return None
    folder = history_dir(path)
    os.makedirs(folder, exist_ok=True)
    existing = list_snapshots(path)
    number = 1
    if existing:
        number = int(_SNAPSHOT.match(os.path.basename(existing[-1])).group(1)) + 1
    target = os.path.join(folder, f"{number:04d}.json")
    shutil.copy2(path, target)
    for old in list_snapshots(path)[:-keep] if keep > 0 else []:
        os.unlink(old)
    return target


def save(tl: "timeline.Timeline", path: str, expected_hash=None, keep: int = HISTORY_KEEP) -> str:
    """Save `tl` to `path`: refuse a stale hash, snapshot the old file, write atomically.
    Returns the hash of the new file."""
    if expected_hash is not None:
        actual = current_hash(path)
        if actual != expected_hash:
            raise StaleHash(expected_hash, actual)
    snapshot(path, keep)
    tl.save(path)
    return timeline.file_hash(path)
