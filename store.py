"""
Saving the timeline file (SPECS 4.1).

- Atomic: the new file is written to a temporary file in the same folder and renamed
  over the old one (Timeline.save). A crash never leaves a half-written file.
- Snapshots: before the file is replaced, the old file is copied to
  timeline.history/NNNN.json (numbers rising, the newest HISTORY_KEEP kept).
- Hash check: a writer that started from a version passes that version's hash; if the
  file on disk has another hash, the save is refused with StaleHash and nothing changes.

- A successful `timeline validate` also snapshots the file when it differs from the newest
  snapshot (`snapshot_if_changed`), so the last valid version is kept after a direct edit.
- `undo` restores the newest snapshot that differs from the file (a snapshot that equals the
  file is skipped). The version it replaces goes to timeline.history/undone/, so an undo
  never loses work. The snapshot it restored stays, so a later direct edit can be undone
  too; the snapshots newer than it were copies of the undone version and are removed.
  Two undos go two versions back.
- `history` lists the snapshots and the file with their time and a one-line change
  (`describe_change`).
"""

import datetime
import json
import os
import re
import shutil
import tempfile

import timeline

HISTORY_DIR = "timeline.history"
UNDONE_DIR = "undone"
HISTORY_KEEP = 200
_SNAPSHOT = re.compile(r"^(\d{4,})\.json$")


class NothingToUndo(Exception):
    """No snapshot differs from the file: there is no earlier version to go back to."""


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


def snapshot_if_changed(path: str, keep: int = HISTORY_KEEP):
    """Snapshot the file unless the newest snapshot has the same bytes (SPECS 4.1 rule 3:
    what `timeline validate` does after a good check). Returns the snapshot's path, or None
    when there is no file or nothing changed."""
    if not os.path.isfile(path):
        return None
    existing = list_snapshots(path)
    if existing and timeline.file_hash(existing[-1]) == timeline.file_hash(path):
        return None
    return snapshot(path, keep)


def _number(path: str) -> int:
    return int(_SNAPSHOT.match(os.path.basename(path)).group(1))


def undo(path: str, expected_hash=None, keep: int = HISTORY_KEEP) -> dict:
    """Restore the newest snapshot that differs from the file at `path`.

    Raises FileNotFoundError (no file), StaleHash (the file is not the version the caller
    started from) or NothingToUndo; nothing changes in those cases. Otherwise the file is
    replaced, atomically and byte for byte, by the snapshot; the replaced version is copied
    to timeline.history/undone/NNNN.json first; the snapshots newer than the one restored
    (copies of the replaced version) are removed. Returns {"restored": snapshot number,
    "hash": new hash, "undone": path of the copy of the replaced version, "replaced_hash"}.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    current = timeline.file_hash(path)
    if expected_hash is not None and expected_hash != current:
        raise StaleHash(expected_hash, current)
    snaps = list_snapshots(path)
    target = next((s for s in reversed(snaps) if timeline.file_hash(s) != current), None)
    if target is None:
        raise NothingToUndo("There is no earlier version to restore.")

    undone_dir = os.path.join(history_dir(path), UNDONE_DIR)
    os.makedirs(undone_dir, exist_ok=True)
    taken = sorted(n for n in os.listdir(undone_dir) if _SNAPSHOT.match(n))
    number = int(_SNAPSHOT.match(taken[-1]).group(1)) + 1 if taken else 1
    undone = os.path.join(undone_dir, f"{number:04d}.json")
    shutil.copy2(path, undone)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".timeline-",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out, open(target, "rb") as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    except BaseException:
        for leftover in (tmp, undone):
            try:
                os.unlink(leftover)
            except OSError:
                pass
        raise
    for newer in snaps[snaps.index(target) + 1:]:
        os.unlink(newer)
    if keep > 0:
        for old in sorted(n for n in os.listdir(undone_dir) if _SNAPSHOT.match(n))[:-keep]:
            os.unlink(os.path.join(undone_dir, old))
    return {"restored": _number(target), "hash": timeline.file_hash(path), "undone": undone,
            "replaced_hash": current}


# ── History ─────────────────────────────────────────────────────

_MAX_CHANGES = 3


def _segment_change(old: dict, new: dict) -> str:
    parts = []
    for key in ("in", "out"):
        if old.get(key) != new.get(key):
            parts.append(f"{key} {old.get(key)} -> {new.get(key)}")
    if old.get("enabled", True) != new.get("enabled", True):
        parts.insert(0, "removed from the film" if not new.get("enabled", True)
                     else "back in the film")
    for key in sorted(set(old) | set(new)):
        if key not in ("id", "in", "out", "enabled") and old.get(key) != new.get(key):
            parts.append(key)
    return f"{new['id']} " + ", ".join(parts) if parts else ""


def _hand(src: dict):
    return src.get("audio_offset_hand_s") if isinstance(src, dict) else None


def describe_change(old: dict, new: dict) -> str:
    """One line that says what differs between two versions of the timeline data: the
    segments that were added, taken out, trimmed, removed from or put back into the film,
    moved or renamed; the offsets set by hand; the other parts of the file by name. At most
    three changes are written out, the rest is counted."""
    changes = []
    old_segs = {s["id"]: s for s in old.get("segments", []) if isinstance(s, dict) and "id" in s}
    new_segs = {s["id"]: s for s in new.get("segments", []) if isinstance(s, dict) and "id" in s}
    for sid in new_segs:
        if sid not in old_segs:
            changes.append(f"{sid} added")
    for sid in old_segs:
        if sid not in new_segs:
            changes.append(f"{sid} taken out of the file")
    common_old = [s for s in old_segs if s in new_segs]
    common_new = [s for s in new_segs if s in old_segs]
    if common_old != common_new:
        changes.append("order changed")
    for sid in common_new:
        line = _segment_change(old_segs[sid], new_segs[sid])
        if line:
            changes.append(line)
    old_src, new_src = old.get("sources") or {}, new.get("sources") or {}
    for sid in new_src:
        if sid not in old_src:
            changes.append(f"{sid} clip added")
            continue
        a, b = _hand(old_src[sid]), _hand(new_src[sid])
        if a != b:
            if a is None:
                changes.append(f"{sid} offset set by hand to {b:.3f} s")
            elif b is None:
                changes.append(f"{sid} offset set by hand removed")
            else:
                changes.append(f"{sid} offset set by hand {a:.3f} -> {b:.3f} s")
        for key in sorted(set(old_src[sid]) | set(new_src[sid])):
            if key != "audio_offset_hand_s" and old_src[sid].get(key) != new_src[sid].get(key):
                changes.append(f"{sid} {key}")
    for sid in old_src:
        if sid not in new_src:
            changes.append(f"{sid} clip taken out")
    for key in ("film", "look", "audio", "titles", "subtitles", "exports", "derived_from",
                "kind", "version"):
        if old.get(key) != new.get(key):
            changes.append(f"{key} changed")
    if not changes:
        return "no change"
    shown = "; ".join(changes[:_MAX_CHANGES])
    return shown + (f" (+{len(changes) - _MAX_CHANGES} more)" if len(changes) > _MAX_CHANGES else "")


def _read_data(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def history(path: str) -> list:
    """The versions of the timeline file at `path`, oldest first, the file itself last:
    [{number (None for the file), name, current, time (seconds since 1970, from the file's
    modification time: when that version was written), hash, change}]. `change` says what
    differs from the version before (describe_change)."""
    files = [(_number(s), s, False) for s in list_snapshots(path)]
    if os.path.isfile(path):
        files.append((None, path, True))
    rows, previous = [], None
    for number, file, current in files:
        data = _read_data(file)
        if previous is None:
            change = "no earlier version" if current else "first version kept"
            if data is None:
                change = "not a timeline file"
        elif data is None:
            change = "not a timeline file"
        elif previous == "unreadable":
            change = "the version before is not a timeline file"
        else:
            change = describe_change(previous, data)
        rows.append({"number": number, "name": os.path.basename(file), "current": current,
                     "time": os.path.getmtime(file), "hash": timeline.file_hash(file),
                     "change": change})
        previous = data if data is not None else "unreadable"
    return rows


def format_time(seconds: float) -> str:
    return datetime.datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")


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
