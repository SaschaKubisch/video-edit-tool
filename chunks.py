"""
Resume of a long render (SPECS 5.3, step 6).

The render of the whole first shoot took 2 h 36 min as one encoder process; a failure at 90 percent lost about
2.3 hours. A long film is therefore rendered in **chunks**: the scenes are gathered, whole, into chunks of about two
minutes (`config.EXPORT_CHUNK_SEC`), each chunk is rendered as an MP4 (or MOV) file with the picture only, and the
chunks are joined with ffmpeg's concat in copy mode together with the sound, which is mixed and encoded once. A chunk
that is finished and whose key matches is not rendered again, so a render that was stopped, or that failed, goes on
where it was when the same command is run again.

This module has what needs no program: the cut into chunks, the key of a chunk, the folder and its records, the free
space that a chunked render needs, the list that the join reads, and the parameter sets of a chunk. `export.py` runs
the processes.

The key of a chunk (`key_material`) names everything that shapes its pictures: for each scene the original file (path,
size, time of change), the frames, the placement; both LUTs (their hashes are in the `look` block); the video settings
and the size of the export; the text drawn in the chunk (the signature of its text plan, which holds the fonts); the
fade frames; the versions of ffmpeg and of the decoder; and the format of this module. It does not name the sound
(the sound has a key of its own) or the name and the place of the export.
"""

import hashlib
import json
import os
import subprocess

import config

CHUNK_FORMAT = 1                      # a change of what a chunk is changes every key
CHUNK_SPACE_FACTOR = 2.1              # the chunks hold the film once and the joined file holds it again, plus the margin
SPACE_MARGIN = 1.1                    # the margin of a render in one process (export.SPACE_MARGIN)
SMALL_REST = 0.25                     # a last chunk shorter than this part of a chunk joins the chunk before it


# ── The cut ─────────────────────────────────────────────────────

def plan_chunks(segments: list, chunk_frames: int) -> list:
    """The scenes in chunks of whole scenes: scenes are gathered until their frames reach `chunk_frames`; a scene
    longer than that is a chunk of its own with what was gathered before it; a last chunk shorter than a quarter of
    `chunk_frames` joins the chunk before it. A `chunk_frames` of 0 or less, or no scenes, gives one chunk or none."""
    segments = list(segments)
    if not segments:
        return []
    if chunk_frames <= 0:
        return [segments]
    out, cur, n = [], [], 0
    for s in segments:
        cur.append(s)
        n += s.length_frames
        if n >= chunk_frames:
            out.append(cur)
            cur, n = [], 0
    if cur:
        if out and n < chunk_frames * SMALL_REST:
            out[-1] += cur
        else:
            out.append(cur)
    return out


def frames_of(chunk: list) -> int:
    return sum(s.length_frames for s in chunk)


# ── The key ─────────────────────────────────────────────────────

def _file_state(path: str) -> list:
    try:
        st = os.stat(path)
        return [path, st.st_size, st.st_mtime_ns]
    except OSError:
        return [path, 0, 0]


def key_material(tl, plan, segs: list, entry: dict, text_signature, fade, ffmpeg_version: str = "",
                 decoder: str = "") -> dict:
    """What a chunk of `segs` (scenes of `plan`) is made from. `text_signature` is the signature of the text plan of
    the chunk ("" without text) and `fade` the picture fade frames (in, out) that fall in it."""
    data = tl.data
    return {
        "format": CHUNK_FORMAT,
        "segments": [{"id": s.id, "source": s.source, "file": _file_state(data["sources"][s.source]["original_video"]),
                      "size": data["sources"][s.source]["size"], "in": s.in_frame, "out": s.out_frame,
                      "start": s.film_start_frame, "placement": s.placement} for s in segs],
        "frame": [entry["width"], entry["height"]], "video": entry["video"],
        "fps": [plan.fps.numerator, plan.fps.denominator],
        "look": data["look"], "text": text_signature or "", "fade": list(fade),
        "ffmpeg": ffmpeg_version, "decoder": decoder}


def chunk_key(material: dict) -> str:
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]


# ── The folder ──────────────────────────────────────────────────

def folder(work_dir: str, name: str, lang=None) -> str:
    """<work>/chunks/<export name>[.<lang>]: the chunks of one render, kept until the joined file has passed its checks."""
    tag = name if not lang or lang == config.SUBTITLE_LANGUAGE else f"{name}.{lang}"
    return os.path.join(work_dir, "chunks", tag)


def chunk_path(folder_: str, index: int, ext: str, partial: bool = False) -> str:
    return os.path.join(folder_, f"chunk-{index:04d}.{ext}" + (".partial" if partial else ""))


def record_path(folder_: str, index: int) -> str:
    return os.path.join(folder_, f"chunk-{index:04d}.json")


def write_record(folder_: str, index: int, record: dict) -> None:
    import fsutil
    fsutil.write_json_atomic(record_path(folder_, index), record)


def read_record(folder_: str, index: int):
    try:
        with open(record_path(folder_, index), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def reusable(folder_: str, index: int, key: str, frames: int, ext: str) -> bool:
    """Whether chunk `index` is finished and is the chunk that `key` describes: the record has the key and the number
    of frames, and the file is as large as the record says it was when it was finished."""
    record = read_record(folder_, index)
    if not record or record.get("key") != key or record.get("frames") != frames:
        return False
    try:
        return os.path.getsize(chunk_path(folder_, index, ext)) == record.get("bytes") and record.get("bytes", 0) > 0
    except OSError:
        return False


def prune(folder_: str, wanted: dict, ext: str) -> list:
    """Remove what cannot be used again: files that were being written (`.partial`), chunks whose key is not the key
    wanted for their number, chunks beyond the last one wanted, and records without a file. `wanted` maps a chunk
    number to its key. The sound file and its record stay. Returns the paths that were removed."""
    removed = []
    try:
        names = sorted(os.listdir(folder_))
    except OSError:
        return removed
    for name in names:
        path = os.path.join(folder_, name)
        if name.endswith(".partial"):
            keep = False
        elif name.startswith("chunk-") and (name.endswith("." + ext) or name.endswith(".json")):
            index = int(name[len("chunk-"):len("chunk-") + 4])
            record = read_record(folder_, index)
            keep = index in wanted and bool(record) and record.get("key") == wanted[index] and \
                os.path.isfile(chunk_path(folder_, index, ext))
        else:
            keep = True
        if not keep:
            try:
                os.unlink(path)
                removed.append(path)
            except OSError:
                pass
    return removed


def existing_bytes(folder_: str, wanted: dict, ext: str) -> int:
    """The bytes of the finished chunks that match `wanted` (they are already on the disk)."""
    total = 0
    for index, key in wanted.items():
        record = read_record(folder_, index)
        if record and record.get("key") == key:
            try:
                total += os.path.getsize(chunk_path(folder_, index, ext))
            except OSError:
                pass
    return total


def space_needed(estimate: int, existing: int, scratch: int, chunked: bool) -> int:
    """The free space to start with: one process needs the file and its margin; a chunked render needs the chunks and
    the joined file, about 2.1 times the file, less what the finished chunks already take, and never less than the
    margin of one process."""
    if not chunked:
        return int(estimate * SPACE_MARGIN) + scratch
    return max(int(estimate * CHUNK_SPACE_FACTOR) - existing, int(estimate * SPACE_MARGIN)) + scratch


def write_concat_list(paths: list, list_path: str) -> None:
    """The list that ffmpeg's concat demuxer reads: one `file '<path>'` per chunk, in order."""
    with open(list_path, "w", encoding="utf-8") as f:
        for p in paths:
            f.write("file '" + str(p).replace("'", "'\\''") + "'\n")


# ── The parameter sets (H.264) ──────────────────────────────────

def parameter_sets(path: str, ffmpeg: str):
    """A short fingerprint of the sequence and picture parameter sets (SPS and PPS) at the start of an H.264 chunk, or
    None when the file holds no H.264 (ProRes is intra-only and joins without a question). Joining chunks in copy
    mode needs the same parameter sets in all of them; two runs of `h264_videotoolbox` with the same settings wrote
    the same bytes [measured, SPECS 5.3], and the render checks it before it joins."""
    r = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", path, "-map", "0:v:0", "-c:v", "copy",
                        "-bsf:v", "h264_mp4toannexb", "-frames:v", "1", "-f", "h264", "-"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    data, found, j, starts = r.stdout, [], 0, []
    while True:
        k = data.find(b"\x00\x00\x01", j)
        if k < 0:
            break
        starts.append(k + 3)
        j = k + 3
    for a, b in zip(starts, starts[1:] + [len(data)]):
        if data[a] & 0x1F in (7, 8):
            found.append(data[a:b].rstrip(b"\x00"))
    if not found:
        return None
    return hashlib.sha1(b"\x00".join(found)).hexdigest()[:16]
