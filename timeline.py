"""
The timeline file, version 1 (SPECS section 3).

One JSON file describes one film or one short. This module loads it, checks it
against the rules V1 to V22 (plus V0, structure, which the plan did not number),
saves it atomically with the keys in the order of the specification, and computes
the derived values of SPECS 3.4 (film order, film start frames, sample positions).

The file is kept as plain JSON data (dicts and lists), not as classes. People and
Claude Code edit the file directly, and the validator must name the exact field of
every problem, so it works on the data as it is. A key that starts with `x_` is a
free key: the tool keeps it and ignores it.
"""

import difflib
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import config
import safety
import tools

KNOWN_VERSION = 1

ERROR = "error"
WARNING = "warning"

# ── The schema: the keys of every object, in the order of SPECS 3.2 ──

TOP_KEYS = ["version", "kind", "film", "sources", "segments", "look", "audio",
            "titles", "subtitles", "exports", "derived_from"]
FILM_KEYS = ["title", "fps", "frame", "fit", "fade_in_s", "fade_out_s", "audio_join"]
FPS_KEYS = ["num", "den"]
FRAME_KEYS = ["width", "height"]
AUDIO_JOIN_KEYS = ["duration_s", "curve"]
SOURCE_KEYS = ["original_video", "preview_copy", "audio", "audio_offset_s",
               "audio_offset_hand_s", "audio_drift_ppm", "frames", "fps", "size",
               "copy_lut_sha256"]
SEGMENT_KEYS = ["id", "source", "in", "out", "enabled", "label", "origin", "note",
                "crop", "audio_join", "gain_db_adjust", "priority"]
CROP_KEYS = ["x", "x_from", "x_to"]
LOOK_KEYS = ["base", "lut"]
BASE_KEYS = ["mode", "path", "sha256"]
LUT_KEYS = ["path", "sha256", "interp", "name", "credit"]
AUDIO_KEYS = ["sample_rate", "loudness"]
LOUDNESS_KEYS = ["mode", "target_lufs", "true_peak_dbtp", "max_gain_db"]
TITLE_KEYS = ["id", "text", "anchor", "start_s", "end_s", "template", "fade_s"]
SUBTITLES_KEYS = ["language", "mode", "style", "cues"]
STYLE_KEYS = ["font", "size_pct", "outline_px", "max_chars_per_line"]
CUE_KEYS = ["id", "anchor", "start_s", "end_s", "text"]
EXPORT_KEYS = ["name", "output", "enabled", "width", "height", "video", "audio"]
EXPORT_VIDEO_KEYS = ["codec", "rate_control", "preset", "profile"]
RATE_CONTROL_KEYS = ["mode", "mbps", "crf"]
EXPORT_AUDIO_KEYS = ["codec", "bitrate_kbps", "sample_rate"]
DERIVED_KEYS = ["film", "target_length_s", "settings"]

ID_SOURCE = re.compile(r"^[A-Za-z0-9_-]+$")
ID_SEGMENT = re.compile(r"^s[0-9]+$")
ID_TITLE = re.compile(r"^t[0-9]+$")
ID_CUE = re.compile(r"^c[0-9]+$")
SHORT_MAX_SECONDS = 180


class TimelineError(Exception):
    """The file cannot be read as a timeline file."""


class UnknownVersion(TimelineError):
    """The file has a version this tool does not know (T2)."""


@dataclass
class Finding:
    level: str
    rule: str
    path: str
    message: str
    hint: str = ""

    def as_dict(self) -> dict:
        return {"level": self.level, "rule": self.rule, "path": self.path,
                "message": self.message, "hint": self.hint}


def has_errors(findings) -> bool:
    return any(f.level == ERROR for f in findings)


# ── Small helpers ───────────────────────────────────────────────

def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _join(base: str, key: str) -> str:
    return f"{base}.{key}" if base else key


def file_hash(path: str) -> str:
    """sha256 of the file's bytes. The page sends it back with an edit (SPECS 4.1)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def effective_offset(source: dict) -> float:
    """The offset the sound uses for a source: the one the owner typed
    (`audio_offset_hand_s`) when there is one, else the one from matching."""
    hand = source.get("audio_offset_hand_s")
    return source["audio_offset_s"] if hand is None else hand


_WAV_SECONDS = {}


def wav_length_seconds(path: str) -> float:
    """The length of an audio file in seconds, from ffprobe. Remembered per file and
    modification time, so a page that asks again does not start ffprobe again."""
    key = (path, os.stat(path).st_mtime_ns)
    if key not in _WAV_SECONDS:
        r = subprocess.run([tools.ffprobe_path(), "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError(r.stderr.strip() or "ffprobe found no duration")
        _WAV_SECONDS[key] = float(r.stdout.strip().splitlines()[0])
    return _WAV_SECONDS[key]


def count_video_frames(path: str) -> int:
    """Frames of the first video stream, counted as packets (one per frame in an MP4)."""
    cmd = [tools.ffprobe_path(), "-v", "error", "-count_packets", "-select_streams", "v:0",
           "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(r.stderr.strip() or "ffprobe found no video stream")
    return int(r.stdout.strip().split(",")[0])


# ── Key order and saving ────────────────────────────────────────

def _ordered(obj, keys, children=None):
    """A copy of dict `obj` with the known `keys` first, in order, then the free
    keys in their original order. `children` maps a key to a function that orders
    the value."""
    if not isinstance(obj, dict):
        return obj
    children = children or {}
    out = {}
    for k in keys:
        if k in obj:
            out[k] = children[k](obj[k]) if k in children else obj[k]
    for k, v in obj.items():
        if k not in out:
            out[k] = v
    return out


def _list_of(fn):
    return lambda v: [fn(i) for i in v] if isinstance(v, list) else v


def _canonical(data):
    """The data with every object's keys in the order of SPECS 3.2."""
    obj = lambda keys, **children: (lambda v: _ordered(v, keys, children))
    fps = obj(FPS_KEYS)
    join = obj(AUDIO_JOIN_KEYS)
    source = obj(SOURCE_KEYS, fps=fps, size=obj(FRAME_KEYS))
    segment = obj(SEGMENT_KEYS, crop=obj(CROP_KEYS), audio_join=join)
    export = obj(EXPORT_KEYS,
                 video=obj(EXPORT_VIDEO_KEYS, rate_control=obj(RATE_CONTROL_KEYS)),
                 audio=obj(EXPORT_AUDIO_KEYS))
    return _ordered(data, TOP_KEYS, {
        "film": obj(FILM_KEYS, fps=fps, frame=obj(FRAME_KEYS), audio_join=join),
        "sources": lambda v: {k: source(s) for k, s in v.items()} if isinstance(v, dict) else v,
        "segments": _list_of(segment),
        "look": obj(LOOK_KEYS, base=obj(BASE_KEYS), lut=obj(LUT_KEYS)),
        "audio": obj(AUDIO_KEYS, loudness=obj(LOUDNESS_KEYS)),
        "titles": _list_of(obj(TITLE_KEYS)),
        "subtitles": obj(SUBTITLES_KEYS, style=obj(STYLE_KEYS), cues=_list_of(obj(CUE_KEYS))),
        "exports": _list_of(export),
        "derived_from": obj(DERIVED_KEYS),
    })


def dumps(data) -> str:
    """The text of the file: UTF-8, two-space indent, known keys in the order of the
    specification, a newline at the end."""
    return json.dumps(_canonical(data), indent=2, ensure_ascii=False) + "\n"


# ── The validator ───────────────────────────────────────────────

class _Validator:
    def __init__(self, data, file_checks, frame_counter, hasher, duplicates, wav_seconds=None):
        self.d = data
        self.file_checks = file_checks
        self.frame_counter = frame_counter or count_video_frames
        self.hasher = hasher or file_hash
        self.duplicates = duplicates or []
        # V23 reads the length of the WAV of a clip with a hand offset: with the file
        # checks on by ffprobe, else only when the caller passes a function.
        self.wav_seconds = wav_seconds or (wav_length_seconds if file_checks else None)
        self.findings = []

    # ── reporting ──
    def add(self, level, rule, path, message, hint=""):
        self.findings.append(Finding(level, rule, path, message, hint))

    def err(self, rule, path, message, hint=""):
        self.add(ERROR, rule, path, message, hint)

    def warn(self, rule, path, message, hint=""):
        self.add(WARNING, rule, path, message, hint)

    # ── typed access (V0) ──
    def obj(self, parent, key, path, required=True):
        """The dict at parent[key], or None (with a V0 finding if required or wrong)."""
        p = _join(path, key)
        if key not in parent:
            if required:
                self.err("V0", p, f"missing required field '{key}'")
            return None
        if not isinstance(parent[key], dict):
            self.err("V0", p, f"'{key}' must be an object")
            return None
        return parent[key]

    def lst(self, parent, key, path, required=True):
        p = _join(path, key)
        if key not in parent:
            if required:
                self.err("V0", p, f"missing required field '{key}'")
            return None
        if not isinstance(parent[key], list):
            self.err("V0", p, f"'{key}' must be a list")
            return None
        return parent[key]

    def typed(self, parent, key, path, kind, required=True):
        """parent[key] if it has the type `kind` ('int', 'num', 'str', 'bool'), else None."""
        p = _join(path, key)
        if key not in parent:
            if required:
                self.err("V0", p, f"missing required field '{key}'")
            return None
        v = parent[key]
        ok = {"int": is_int, "num": is_num, "bool": lambda x: isinstance(x, bool),
              "str": lambda x: isinstance(x, str)}[kind](v)
        if not ok:
            names = {"int": "an integer", "num": "a number", "str": "a string",
                     "bool": "true or false"}
            self.err("V0", p, f"'{key}' must be {names[kind]}")
            return None
        return v

    def check_keys(self, obj, known, path):
        """V2: no unknown key unless it starts with x_."""
        for k in obj:
            if k in known or str(k).startswith("x_"):
                continue
            close = difflib.get_close_matches(k, known, n=1, cutoff=0.6)
            hint = f"did you mean '{close[0]}'?" if close else \
                f"known keys: {', '.join(known)}. Keys that start with x_ are free."
            self.err("V2", _join(path, k), f"unknown key '{k}'", hint)

    # ── the whole file ──
    def run(self):
        d = self.d
        if not isinstance(d, dict):
            self.err("V0", "", "the file must contain one JSON object")
            return self.findings
        for key, where in self.duplicates:
            if where == "sources":
                self.err("V6", f"sources.{key}", f"duplicate source id '{key}'")
            else:
                self.err("V0", key, f"duplicate key '{key}' in the file; the last one wins")
        self.check_keys(d, TOP_KEYS, "")

        self.version()
        kind = self.kind()
        film = self.film()
        sources = self.sources(film)
        segments = self.segments(sources, film)
        self.hand_offset_coverage(sources, segments, film)
        self.look(sources)
        self.audio()
        self.titles_and_subtitles(segments, film, sources)
        self.exports(film, sources)
        self.derived(kind)
        self.short_length(kind, segments, film)
        return self.findings

    # ── V1 ──
    def version(self):
        v = self.d.get("version")
        if "version" not in self.d:
            self.err("V1", "version", "the file has no version",
                     f"add \"version\": {KNOWN_VERSION}")
        elif not is_int(v):
            self.err("V1", "version", f"version must be an integer, found {v!r}")
        elif v != KNOWN_VERSION:
            self.err("V1", "version", f"version {v} is not known to this tool "
                     f"(it reads version {KNOWN_VERSION})",
                     "update the tool to a newer version" if v > KNOWN_VERSION
                     else f"use version {KNOWN_VERSION}")

    # ── V3 ──
    def kind(self):
        kind = self.typed(self.d, "kind", "", "str")
        if kind is not None and kind not in ("film", "short"):
            self.err("V3", "kind", f"kind must be 'film' or 'short', found {kind!r}")
            kind = None
        has = "derived_from" in self.d
        if kind == "short" and not has:
            self.err("V3", "derived_from", "a short needs 'derived_from'")
        elif kind == "film" and has:
            self.err("V3", "derived_from", "'derived_from' belongs to a short, not a film",
                     "remove it, or set kind to 'short'")
        return kind

    # ── film (V4, V5, V12) ──
    def fps(self, parent, path):
        """Validate {num, den}; returns (num, den) or None."""
        fps = self.obj(parent, "fps", path)
        if fps is None:
            return None
        p = _join(path, "fps")
        self.check_keys(fps, FPS_KEYS, p)
        out = []
        for k in FPS_KEYS:
            if k not in fps:
                self.err("V0", _join(p, k), f"missing required field '{k}'")
                out.append(None)
            elif not is_int(fps[k]) or fps[k] <= 0:
                self.err("V4", _join(p, k), f"{k} must be a positive integer, found {fps[k]!r}")
                out.append(None)
            else:
                out.append(fps[k])
        return tuple(out) if None not in out else None

    def size(self, parent, key, path):
        """Validate {width, height} as even positive integers (V5)."""
        size = self.obj(parent, key, path)
        if size is None:
            return None
        p = _join(path, key)
        self.check_keys(size, FRAME_KEYS, p)
        out = []
        for k in FRAME_KEYS:
            if k not in size:
                self.err("V0", _join(p, k), f"missing required field '{k}'")
                out.append(None)
            elif not is_int(size[k]) or size[k] <= 0 or size[k] % 2:
                self.err("V5", _join(p, k),
                         f"{k} must be an even positive integer, found {size[k]!r}")
                out.append(None)
            else:
                out.append(size[k])
        return tuple(out) if None not in out else None

    def join(self, join, path):
        """Validate {duration_s, curve} (V12). Returns the duration in seconds or None."""
        self.check_keys(join, AUDIO_JOIN_KEYS, path)
        duration = None
        if "duration_s" not in join:
            self.err("V0", _join(path, "duration_s"), "missing required field 'duration_s'")
        elif not is_num(join["duration_s"]):
            self.err("V0", _join(path, "duration_s"), "'duration_s' must be a number")
        elif not 0 <= join["duration_s"] <= 5:
            self.err("V12", _join(path, "duration_s"),
                     f"join duration {join['duration_s']} s is outside 0 to 5 s")
        else:
            duration = float(join["duration_s"])
        if "curve" in join and join["curve"] not in ("equal_power", "linear"):
            self.err("V12", _join(path, "curve"),
                     f"curve must be 'equal_power' or 'linear', found {join['curve']!r}")
        return duration

    def film(self):
        film = self.obj(self.d, "film", "")
        info = {"fps": None, "frame": None, "fit": "fit", "fade_in": 0.0, "fade_out": 0.0,
                "join": 0.0}
        if film is None:
            return info
        self.check_keys(film, FILM_KEYS, "film")
        self.typed(film, "title", "film", "str", required=False)
        info["fps"] = self.fps(film, "film")
        info["frame"] = self.size(film, "frame", "film")
        if "fit" in film:
            if film["fit"] not in ("fit", "fill"):
                self.err("V0", "film.fit", f"fit must be 'fit' or 'fill', found {film['fit']!r}")
            else:
                info["fit"] = film["fit"]
        for key, name in (("fade_in_s", "fade_in"), ("fade_out_s", "fade_out")):
            v = self.typed(film, key, "film", "num", required=False)
            if v is not None:
                if v < 0:
                    self.err("V0", f"film.{key}", f"{key} must not be negative")
                else:
                    info[name] = float(v)
        if "audio_join" in film:
            j = self.obj(film, "audio_join", "film")
            if j is not None:
                dur = self.join(j, "film.audio_join")
                if dur is not None:
                    info["join"] = dur
        return info

    # ── sources (V4, V5, V6, V7) ──
    def sources(self, film):
        src = self.obj(self.d, "sources", "")
        result = {}
        if src is None:
            return result
        for sid, s in src.items():
            path = f"sources.{sid}"
            if not ID_SOURCE.match(str(sid)):
                self.err("V6", path, f"source id '{sid}' may contain only letters, digits, "
                         f"'_' and '-'")
            if not isinstance(s, dict):
                self.err("V0", path, "a source must be an object")
                continue
            self.check_keys(s, SOURCE_KEYS, path)
            info = {"frames": None, "offset": 0.0, "drift": 0.0, "fps": None, "hand": None}
            for key in ("original_video", "preview_copy", "audio"):
                self.typed(s, key, path, "str")
            off = self.typed(s, "audio_offset_s", path, "num")
            if off is not None:
                info["offset"] = float(off)
            hand = self.typed(s, "audio_offset_hand_s", path, "num", required=False)
            if hand is not None:
                self.hand_offset(hand, f"{path}.audio_offset_hand_s", info)
            drift = self.typed(s, "audio_drift_ppm", path, "num", required=False)
            if drift is not None:
                info["drift"] = float(drift)
            frames = self.typed(s, "frames", path, "int")
            if frames is not None:
                if frames <= 0:
                    self.err("V0", f"{path}.frames", "frames must be a positive integer")
                else:
                    info["frames"] = frames
            fps = self.fps(s, path)
            info["fps"] = fps
            if fps and film["fps"] and fps[0] * film["fps"][1] != film["fps"][0] * fps[1]:
                self.err("V4", f"{path}.fps",
                         f"fps {fps[0]}/{fps[1]} differs from the film's "
                         f"{film['fps'][0]}/{film['fps'][1]}",
                         "all sources must have the film's frame rate")
            self.size(s, "size", path)
            self.typed(s, "copy_lut_sha256", path, "str")
            result[sid] = info
            self.source_files(sid, s, info)
        return result

    # V23
    def hand_offset(self, value, path, info):
        limit = config.OFFSET_HAND_MAX_SEC
        if not math.isfinite(value):
            self.err("V23", path, f"the offset {value} is not a finite number")
        elif abs(value) > limit:
            self.err("V23", path, f"the offset {value:g} s is outside -{limit:g} to {limit:g} s",
                     "the offset is in seconds; the recorder and the camera start a few seconds "
                     "apart at most")
        else:
            info["hand"] = float(value)
            info["offset"] = float(value)

    def hand_offset_coverage(self, sources, segments, film):
        """V23, warning: with the offset the owner typed, the WAV may hold no sound, or
        only part of the sound, for a segment of that clip. Only clips with a hand
        offset are checked; the render reports the others."""
        if self.wav_seconds is None or not film["fps"]:
            return
        num, den = film["fps"]
        one_frame = den / num
        for sid, info in sources.items():
            if info["hand"] is None:
                continue
            raw = self.d["sources"][sid]
            path = f"sources.{sid}.audio_offset_hand_s"
            wav = raw.get("audio")
            if not isinstance(wav, str):
                continue
            if self.file_checks and not os.path.isfile(wav):
                continue                    # V7 reports the missing file
            try:
                length = float(self.wav_seconds(wav))
            except Exception as e:          # ffprobe may fail in many ways
                self.warn("V23", path, f"cannot read the length of {wav}, so the offset "
                          f"could not be checked against it: {e}")
                continue
            for seg in segments:
                if seg["source"] != sid or not seg["enabled"]:
                    continue
                if seg["in"] is None or seg["out"] is None:
                    continue
                t0 = seg["in"] * den / num + info["hand"]
                t1 = seg["out"] * den / num + info["hand"]
                covered = max(0.0, min(t1, length) - max(t0, 0.0))
                if covered <= 1e-9:
                    self.warn("V23", path,
                              f"with the offset {info['hand']:+g} s the WAV ({length:.1f} s) holds "
                              f"no sound for segment {seg['id']} (it needs {t0:.1f} s to {t1:.1f} s); "
                              "the segment is silent")
                elif (t1 - t0) - covered >= one_frame:
                    self.warn("V23", path,
                              f"with the offset {info['hand']:+g} s the WAV ({length:.1f} s) holds "
                              f"sound for only {covered:.1f} of the {t1 - t0:.1f} s of segment "
                              f"{seg['id']}; the rest is silent")

    # V7
    def source_files(self, sid, s, info):
        if not self.file_checks:
            return
        path = f"sources.{sid}"
        for key in ("original_video", "preview_copy", "audio"):
            p = s.get(key)
            if isinstance(p, str) and not os.path.isfile(p):
                self.err("V7", f"{path}.{key}", f"file not found: {p}",
                         "run 'convert' to make the preview copy" if key == "preview_copy"
                         else "check the path or connect the drive")
        copy = s.get("preview_copy")
        if isinstance(copy, str) and os.path.isfile(copy) and info["frames"]:
            try:
                n = self.frame_counter(copy)
            except Exception as e:      # ffprobe may fail in many ways
                self.err("V7", f"{path}.preview_copy", f"cannot count the frames of {copy}: {e}")
                return
            if n != info["frames"]:
                self.err("V7", f"{path}.frames",
                         f"frames is {info['frames']} but the preview copy has {n}",
                         f"use {n}, or make the preview copy again with 'convert'")

    # ── segments (V6, V8..V15) ──
    def segments(self, sources, film):
        segs = self.lst(self.d, "segments", "")
        if segs is None:
            return []
        out = []
        seen = {}
        for i, s in enumerate(segs):
            path = f"segments[{i}]"
            if not isinstance(s, dict):
                self.err("V0", path, "a segment must be an object")
                continue
            self.check_keys(s, SEGMENT_KEYS, path)
            seg = {"i": i, "path": path, "raw": s, "id": None, "enabled": True,
                   "in": None, "out": None, "source": None, "join": None}
            sid = self.typed(s, "id", path, "str")
            if sid is not None:
                if not ID_SEGMENT.match(sid):
                    self.err("V6", f"{path}.id", f"segment id '{sid}' must be 's' and digits")
                elif sid in seen:
                    self.err("V6", f"{path}.id",
                             f"segment id '{sid}' is used twice (also segments[{seen[sid]}])")
                else:
                    seen[sid] = i
                seg["id"] = sid
            src = self.typed(s, "source", path, "str")
            if src is not None:
                if src not in sources:
                    self.err("V8", f"{path}.source", f"source '{src}' does not exist",
                             "use a key of 'sources'")
                else:
                    seg["source"] = src
            a = self.typed(s, "in", path, "int")
            b = self.typed(s, "out", path, "int")
            seg["in"], seg["out"] = a, b
            frames = sources[src]["frames"] if src in sources else None
            if a is not None and a < 0:
                self.err("V9", f"{path}.in", f"in {a} is negative", "use 0 or more")
                seg["in"] = None
            if a is not None and b is not None and seg["in"] is not None:
                if b <= a:
                    self.err("V9", f"{path}.out", f"out {b} is not greater than in {a}",
                             f"use a value greater than {a}")
                    seg["out"] = None
                elif frames is not None and b > frames:
                    self.err("V9", f"{path}.out",
                             f"out {b} is greater than the {frames} frames of source {src}",
                             f"use a value of {frames} or less")
                    seg["out"] = None
            if a is not None and frames is not None and a >= frames and seg["in"] is not None:
                self.err("V9", f"{path}.in", f"in {a} is not before the {frames} frames of "
                         f"source {src}", f"use a value below {frames}")
                seg["in"] = None
            enabled = self.typed(s, "enabled", path, "bool", required=False)
            seg["enabled"] = True if enabled is None else enabled
            for key in ("label", "origin", "note", "priority"):
                v = self.typed(s, key, path, "str", required=False)
                if key == "origin" and v is not None and v not in (
                        "assembly", "manual", "prompt", "short"):
                    self.err("V0", f"{path}.origin", f"origin must be 'assembly', 'manual', "
                             f"'prompt' or 'short', found {v!r}")
                if key == "priority" and v is not None and v not in ("normal", "must", "drop"):
                    self.err("V0", f"{path}.priority", f"priority must be 'normal', 'must' "
                             f"or 'drop', found {v!r}")
            gain = self.typed(s, "gain_db_adjust", path, "num", required=False)
            if gain is not None and not -30 <= gain <= 30:
                self.err("V14", f"{path}.gain_db_adjust",
                         f"gain_db_adjust {gain} is outside -30 to 30 dB")
            if "crop" in s:
                self.crop(s, path, film)
            if "audio_join" in s:
                j = self.obj(s, "audio_join", path)
                if j is not None:
                    seg["join"] = self.join(j, f"{path}.audio_join")
            out.append(seg)

        enabled = [s for s in out if s["enabled"]]
        if not enabled:
            self.err("V10", "segments", "the film has no enabled segment",
                     "enable at least one segment")
        else:
            first = enabled[0]
            if "audio_join" in first["raw"] and isinstance(first["raw"]["audio_join"], dict):
                self.warn("V12", f"{first['path']}.audio_join",
                          "an audio_join on the first enabled segment is ignored",
                          "the film's fade-in takes its place")
        self.fade_lengths(enabled, sources, film)
        return out

    # V13
    def crop(self, s, path, film):
        crop = self.obj(s, "crop", path)
        if crop is None:
            return
        p = f"{path}.crop"
        self.check_keys(crop, CROP_KEYS, p)
        has_x = "x" in crop
        has_from, has_to = "x_from" in crop, "x_to" in crop
        if has_x and (has_from or has_to):
            self.err("V13", p, "use 'x' alone or 'x_from' with 'x_to', not both")
        elif has_from != has_to:
            self.err("V13", p, "'x_from' and 'x_to' go together")
        elif not has_x and not has_from:
            self.err("V13", p, "crop needs 'x', or 'x_from' with 'x_to'")
        for k in CROP_KEYS:
            if k in crop:
                if not is_num(crop[k]):
                    self.err("V13", f"{p}.{k}", f"{k} must be a number")
                elif not 0 <= crop[k] <= 1:
                    self.err("V13", f"{p}.{k}", f"{k} {crop[k]} is outside 0 to 1")
        if film["fit"] == "fit":
            self.warn("V13", p, "crop has no effect when film.fit is 'fit'",
                      "set film.fit to 'fill', or remove the crop")

    # V11
    def fade_lengths(self, enabled, sources, film):
        fps = film["fps"]
        if not fps:
            return
        for k, seg in enumerate(enabled):
            if seg["in"] is None or seg["out"] is None:
                continue
            length = (seg["out"] - seg["in"]) * fps[1] / fps[0]
            nxt = enabled[k + 1] if k + 1 < len(enabled) else None
            before = film["fade_in"] if k == 0 else \
                self.join_of(seg, film) / 2
            after = film["fade_out"] if nxt is None else \
                self.join_of(nxt, film) / 2
            need = before + after
            if length + 1e-9 < need:
                self.err("V11", f"{seg['path']}.out",
                         f"the segment is {length:.2f} s long but its fades and joins need "
                         f"{need:.2f} s",
                         f"make it at least {need:.2f} s long, or shorten the fades and joins")

    @staticmethod
    def join_of(seg, film):
        """Join duration into this segment: its own, else the film's."""
        return seg["join"] if seg["join"] is not None else film["join"]

    # ── look (V16) ──
    def look(self, sources):
        look = self.obj(self.d, "look", "")
        if look is None:
            return
        self.check_keys(look, LOOK_KEYS, "look")
        base_hash = None
        mode = None
        base = self.obj(look, "base", "look")
        if base is not None:
            self.check_keys(base, BASE_KEYS, "look.base")
            mode = self.typed(base, "mode", "look.base", "str")
            if mode is not None and mode not in ("lut", "none"):
                self.err("V0", "look.base.mode", f"mode must be 'lut' or 'none', found {mode!r}")
                mode = None
            if mode == "lut":
                self.typed(base, "path", "look.base", "str")
                base_hash = self.typed(base, "sha256", "look.base", "str")
                self.lut_file(base, "look.base")
        if "lut" in look:
            lut = self.obj(look, "lut", "look")
            if lut is not None:
                self.check_keys(lut, LUT_KEYS, "look.lut")
                self.typed(lut, "path", "look.lut", "str")
                self.typed(lut, "sha256", "look.lut", "str")
                self.typed(lut, "name", "look.lut", "str", required=False)
                self.typed(lut, "credit", "look.lut", "str", required=False)
                interp = self.typed(lut, "interp", "look.lut", "str")
                if interp is not None and interp != "tetrahedral":
                    self.err("V16", "look.lut.interp",
                             f"interp must be 'tetrahedral', found {interp!r}")
                self.lut_file(lut, "look.lut")
        if mode == "lut" and base_hash:
            srcs = self.d.get("sources")
            for sid, s in (srcs.items() if isinstance(srcs, dict) else []):
                h = s.get("copy_lut_sha256") if isinstance(s, dict) else None
                if isinstance(h, str) and h != base_hash:
                    self.warn("V16", f"sources.{sid}.copy_lut_sha256",
                              "the preview copy carries another base LUT than look.base",
                              "the film preview shows it wrongly; the final render is not "
                              "affected. Run 'convert' to make the copies again")

    def lut_file(self, obj, path):
        if not self.file_checks:
            return
        p, want = obj.get("path"), obj.get("sha256")
        if not isinstance(p, str) or not isinstance(want, str):
            return
        if not os.path.isfile(p):
            self.err("V16", f"{path}.path", f"LUT file not found: {p}")
        elif self.hasher(p) != want:
            self.err("V16", f"{path}.sha256", f"the hash of {p} differs from sha256",
                     "the LUT file changed; run 'look set' to write the new hash")

    # ── audio (V17) ──
    def audio(self):
        audio = self.obj(self.d, "audio", "")
        if audio is None:
            return
        self.check_keys(audio, AUDIO_KEYS, "audio")
        rate = self.typed(audio, "sample_rate", "audio", "int")
        if rate is not None and rate <= 0:
            self.err("V0", "audio.sample_rate", "sample_rate must be positive")
        loud = self.obj(audio, "loudness", "audio")
        if loud is None:
            return
        p = "audio.loudness"
        self.check_keys(loud, LOUDNESS_KEYS, p)
        mode = self.typed(loud, "mode", p, "str")
        if mode is not None and mode not in ("off", "segment_then_film", "film_only"):
            self.err("V17", f"{p}.mode",
                     f"mode must be 'off', 'segment_then_film' or 'film_only', found {mode!r}")
            mode = None
        if mode is not None and mode != "off":
            if "target_lufs" not in loud:
                self.err("V17", f"{p}.target_lufs",
                         f"mode '{mode}' needs target_lufs", "set a value between -40 and -5")
        if "target_lufs" in loud:
            t = self.typed(loud, "target_lufs", p, "num")
            if t is not None and not -40 <= t <= -5:
                self.err("V17", f"{p}.target_lufs", f"target_lufs {t} is outside -40 to -5")
        if "true_peak_dbtp" in loud:
            t = self.typed(loud, "true_peak_dbtp", p, "num")
            if t is not None and t > 0:
                self.err("V17", f"{p}.true_peak_dbtp", f"true_peak_dbtp {t} is above 0")
        if "max_gain_db" in loud:
            t = self.typed(loud, "max_gain_db", p, "num")
            if t is not None and not 0 <= t <= 30:
                self.err("V17", f"{p}.max_gain_db", f"max_gain_db {t} is outside 0 to 30")

    # ── titles and subtitles (V6, V15, V19) ──
    def titles_and_subtitles(self, segments, film, sources):
        by_id = {s["id"]: s for s in segments if s["id"]}
        fps = film["fps"]

        def seg_seconds(seg):
            if not fps or seg["in"] is None or seg["out"] is None:
                return None
            return (seg["out"] - seg["in"]) * fps[1] / fps[0]

        film_seconds = None
        if fps:
            film_seconds = sum((s["out"] - s["in"]) for s in segments
                               if s["enabled"] and s["in"] is not None
                               and s["out"] is not None) * fps[1] / fps[0]

        def interval(item, path, anchor_len, anchor_name):
            s = self.typed(item, "start_s", path, "num")
            e = self.typed(item, "end_s", path, "num")
            if s is None or e is None:
                return
            if s >= e:
                self.err("V15", f"{path}.end_s", f"end_s {e} is not after start_s {s}")
            elif anchor_len is not None and (s < 0 or e > anchor_len + 1e-9):
                self.warn("V15", f"{path}.end_s",
                          f"the interval {s} to {e} s reaches outside {anchor_name} "
                          f"({anchor_len:.1f} s); the part outside is not shown")

        titles = self.lst(self.d, "titles", "", required=False)
        seen = {}
        for i, t in enumerate(titles or []):
            path = f"titles[{i}]"
            if not isinstance(t, dict):
                self.err("V0", path, "a title must be an object")
                continue
            self.check_keys(t, TITLE_KEYS, path)
            tid = self.typed(t, "id", path, "str")
            if tid is not None:
                if not ID_TITLE.match(tid):
                    self.err("V6", f"{path}.id", f"title id '{tid}' must be 't' and digits")
                elif tid in seen:
                    self.err("V6", f"{path}.id", f"title id '{tid}' is used twice")
                seen[tid] = i
            self.typed(t, "text", path, "str")
            self.typed(t, "template", path, "str", required=False)
            self.typed(t, "fade_s", path, "num", required=False)
            anchor = self.typed(t, "anchor", path, "str")
            length, name = None, ""
            if anchor is not None:
                if anchor == "film":
                    length, name = film_seconds, "the film"
                elif anchor in by_id:
                    length, name = seg_seconds(by_id[anchor]), f"segment {anchor}"
                else:
                    self.err("V15", f"{path}.anchor",
                             f"anchor '{anchor}' is neither 'film' nor a segment id")
            interval(t, path, length, name)

        subs = self.obj(self.d, "subtitles", "", required=False) if "subtitles" in self.d else None
        if subs is None:
            return
        self.check_keys(subs, SUBTITLES_KEYS, "subtitles")
        lang = self.typed(subs, "language", "subtitles", "str")
        if lang is not None and lang != "en":
            self.err("V19", "subtitles.language", f"language must be 'en', found {lang!r}",
                     "version 1 supports English only")
        mode = self.typed(subs, "mode", "subtitles", "str")
        if mode is not None and mode not in ("burn", "sidecar", "both"):
            self.err("V19", "subtitles.mode",
                     f"mode must be 'burn', 'sidecar' or 'both', found {mode!r}")
        max_chars = None
        style = self.obj(subs, "style", "subtitles", required=False) if "style" in subs else None
        if style is not None:
            self.check_keys(style, STYLE_KEYS, "subtitles.style")
            self.typed(style, "font", "subtitles.style", "str", required=False)
            self.typed(style, "size_pct", "subtitles.style", "num", required=False)
            self.typed(style, "outline_px", "subtitles.style", "num", required=False)
            max_chars = self.typed(style, "max_chars_per_line", "subtitles.style", "int",
                                   required=False)
        cues = self.lst(subs, "cues", "subtitles")
        seen = {}
        for i, c in enumerate(cues or []):
            path = f"subtitles.cues[{i}]"
            if not isinstance(c, dict):
                self.err("V0", path, "a cue must be an object")
                continue
            self.check_keys(c, CUE_KEYS, path)
            cid = self.typed(c, "id", path, "str")
            if cid is not None:
                if not ID_CUE.match(cid):
                    self.err("V6", f"{path}.id", f"cue id '{cid}' must be 'c' and digits")
                elif cid in seen:
                    self.err("V6", f"{path}.id", f"cue id '{cid}' is used twice")
                seen[cid] = i
            anchor = self.typed(c, "anchor", path, "str")
            length, name = None, ""
            if anchor is not None:
                if anchor in by_id:
                    length, name = seg_seconds(by_id[anchor]), f"segment {anchor}"
                else:
                    self.err("V15", f"{path}.anchor",
                             f"anchor '{anchor}' is not a segment id")
            interval(c, path, length, name)
            text = self.typed(c, "text", path, "str")
            if text is not None:
                if not text.strip():
                    self.err("V19", f"{path}.text", "the cue text is empty")
                else:
                    lines = text.split("\n")
                    longest = max(len(line) for line in lines)
                    if len(lines) > 2:
                        self.warn("V19", f"{path}.text", f"the cue has {len(lines)} lines; "
                                  f"two is the most that reads well")
                    elif max_chars and longest > max_chars:
                        self.warn("V19", f"{path}.text", f"a line has {longest} characters, "
                                  f"more than max_chars_per_line ({max_chars})")

    # ── exports (V5, V18, V21) ──
    def exports(self, film, sources):
        exports = self.lst(self.d, "exports", "", required=False)
        if exports is None:
            return
        folders = []
        for s in (self.d.get("sources") or {}).values():
            if isinstance(s, dict):
                for key in ("original_video", "audio"):
                    if isinstance(s.get(key), str):
                        folders.append(str(Path(s[key]).parent))
        seen = {}
        for i, e in enumerate(exports):
            path = f"exports[{i}]"
            if not isinstance(e, dict):
                self.err("V0", path, "an export must be an object")
                continue
            self.check_keys(e, EXPORT_KEYS, path)
            name = self.typed(e, "name", path, "str")
            if name is not None:
                if name in seen:
                    self.err("V18", f"{path}.name",
                             f"export name '{name}' is used twice (also exports[{seen[name]}])")
                seen[name] = i
            self.typed(e, "enabled", path, "bool", required=False)
            out = self.typed(e, "output", path, "str")
            if out is not None:
                try:
                    safety.assert_not_inside(out, folders)
                except safety.OriginalsProtectedError:
                    self.err("V18", f"{path}.output",
                             "the output lies inside a folder that holds an original",
                             "write the film to a folder of its own, for example on the SSD")
            w = self.typed(e, "width", path, "int")
            h = self.typed(e, "height", path, "int")
            for k, v in (("width", w), ("height", h)):
                if v is not None and (v <= 0 or v % 2):
                    self.err("V5", f"{path}.{k}", f"{k} must be an even positive integer, "
                             f"found {v!r}")
                    if k == "width":
                        w = None
                    else:
                        h = None
            fw, fh = film["frame"] if film["frame"] else (None, None)
            if w and h and fw and fh:
                if abs(w - h * fw / fh) > 1 and abs(h - w * fh / fw) > 1:
                    self.err("V5", f"{path}.height",
                             f"{w}x{h} does not have the shape of the film frame {fw}x{fh}",
                             "an export keeps the shape of the film; the tool adds no bars")
            self.export_codecs(e, path, out)

    # V21
    def export_codecs(self, e, path, out):
        video = self.obj(e, "video", path)
        audio = self.obj(e, "audio", path)
        vcodec = acodec = profile = None
        if video is not None:
            self.check_keys(video, EXPORT_VIDEO_KEYS, f"{path}.video")
            vcodec = self.typed(video, "codec", f"{path}.video", "str")
            self.typed(video, "preset", f"{path}.video", "str", required=False)
            profile = self.typed(video, "profile", f"{path}.video", "str", required=False)
            if "rate_control" in video:
                self.rate_control(video, f"{path}.video", vcodec)
        if audio is not None:
            self.check_keys(audio, EXPORT_AUDIO_KEYS, f"{path}.audio")
            acodec = self.typed(audio, "codec", f"{path}.audio", "str")
            self.typed(audio, "bitrate_kbps", f"{path}.audio", "num", required=False)
            self.typed(audio, "sample_rate", f"{path}.audio", "int", required=False)
        if out is None:
            return
        ext = Path(out).suffix.lower()
        if ext == ".mp4":
            if vcodec is not None and vcodec not in ("h264_videotoolbox", "libx264"):
                self.err("V21", f"{path}.video.codec",
                         f"an .mp4 export needs 'h264_videotoolbox' or 'libx264', found {vcodec!r}")
            if acodec is not None and acodec != "aac":
                self.err("V21", f"{path}.audio.codec",
                         f"an .mp4 export needs audio codec 'aac', found {acodec!r}")
        elif ext == ".mov":
            if vcodec is not None and vcodec != "prores_videotoolbox":
                self.err("V21", f"{path}.video.codec",
                         f"a .mov export needs 'prores_videotoolbox', found {vcodec!r}")
            if video is not None and profile != "hq":
                self.err("V21", f"{path}.video.profile",
                         f"a .mov export needs profile 'hq', found {profile!r}")
            if acodec is not None and acodec != "pcm_s24le":
                self.err("V21", f"{path}.audio.codec",
                         f"a .mov export needs audio codec 'pcm_s24le', found {acodec!r}")
        else:
            self.err("V21", f"{path}.output",
                     f"the output must end in .mp4 or .mov, found '{ext}'")

    def rate_control(self, video, path, codec):
        rc = self.obj(video, "rate_control", path)
        if rc is None:
            return
        p = f"{path}.rate_control"
        self.check_keys(rc, RATE_CONTROL_KEYS, p)
        mode = self.typed(rc, "mode", p, "str")
        if mode == "bitrate":
            v = self.typed(rc, "mbps", p, "num")
            if v is not None and v <= 0:
                self.err("V21", f"{p}.mbps", f"mbps must be positive, found {v}")
        elif mode == "crf":
            if codec is not None and codec != "libx264":
                self.err("V21", f"{p}.mode", "rate control 'crf' is only for 'libx264'")
            v = self.typed(rc, "crf", p, "int")
            if v is not None and not 0 <= v <= 51:
                self.err("V21", f"{p}.crf", f"crf {v} is outside 0 to 51")
        elif mode is not None:
            self.err("V21", f"{p}.mode", f"mode must be 'bitrate' or 'crf', found {mode!r}")

    # ── derived_from (V20) and short length (V22) ──
    def derived(self, kind):
        if "derived_from" not in self.d:
            return
        df = self.obj(self.d, "derived_from", "")
        if df is None:
            return
        self.check_keys(df, DERIVED_KEYS, "derived_from")
        film = self.typed(df, "film", "derived_from", "str")
        self.typed(df, "target_length_s", "derived_from", "num")
        self.obj(df, "settings", "derived_from")
        if kind == "short" and self.file_checks and film is not None \
                and not os.path.isfile(film):
            self.err("V20", "derived_from.film", f"the film file does not exist: {film}")

    def short_length(self, kind, segments, film):
        if kind != "short" or not film["fps"]:
            return
        frames = sum(s["out"] - s["in"] for s in segments
                     if s["enabled"] and s["in"] is not None and s["out"] is not None)
        seconds = frames * film["fps"][1] / film["fps"][0]
        if seconds > SHORT_MAX_SECONDS:
            self.warn("V22", "segments", f"the short is {seconds:.0f} s long; "
                      f"a short may be {SHORT_MAX_SECONDS} s at most")


def validate(data, file_checks=True, frame_counter=None, hasher=None, duplicates=None,
             wav_seconds=None):
    """All findings for `data`, a parsed timeline file.

    file_checks=False skips every check that reads the disk (V7, V16 hashes, V20).
    frame_counter(path) -> int and hasher(path) -> sha256 can be replaced in tests.
    wav_seconds(path) -> seconds is the length of a WAV for rule V23; with the file
    checks on it defaults to ffprobe, with them off it is not checked unless given.
    """
    return _Validator(data, file_checks, frame_counter, hasher, duplicates, wav_seconds).run()


# ── The timeline object ─────────────────────────────────────────

def _find_duplicates(text):
    """Duplicate keys in the JSON text, as (key, where) pairs. `where` is
    'sources' for a duplicate source id, else ''. json keeps the last value, so
    this is the only place where the repetition shows."""
    dups = []

    def hook(pairs):
        seen = set()
        for k, _ in pairs:
            if k in seen:
                dups.append(k)
            seen.add(k)
        return dict(pairs)

    json.loads(text, object_pairs_hook=hook)
    return dups


class Timeline:
    def __init__(self, data, duplicates=None):
        self.data = data
        self.duplicates = duplicates or []

    # ── files ──
    @classmethod
    def loads(cls, text: str) -> "Timeline":
        try:
            data = json.loads(text)
            dups = _find_duplicates(text)
        except json.JSONDecodeError as e:
            raise TimelineError(f"not valid JSON: {e.msg} at line {e.lineno}, "
                                f"column {e.colno}") from e
        if not isinstance(data, dict):
            raise TimelineError("the file must contain one JSON object")
        v = data.get("version")
        if not is_int(v) or v != KNOWN_VERSION:
            raise UnknownVersion(
                f"the file has version {v!r}; this tool reads version {KNOWN_VERSION}. "
                f"Update the tool to read a newer file.")
        source_keys = set((data.get("sources") or {}).keys()) if isinstance(
            data.get("sources"), dict) else set()
        duplicates = [(k, "sources" if k in source_keys else "") for k in dups]
        return cls(data, duplicates)

    @classmethod
    def load(cls, path: str) -> "Timeline":
        with open(path, encoding="utf-8") as f:
            return cls.loads(f.read())

    def dumps(self) -> str:
        return dumps(self.data)

    def save(self, path: str) -> None:
        """Write the file atomically: a temporary file in the same folder, then a rename."""
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".timeline-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(_canonical(self.data), f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def validate(self, **kw):
        return validate(self.data, duplicates=self.duplicates, **kw)

    # ── derived values (SPECS 3.4) ──
    @property
    def fps(self) -> Fraction:
        f = self.data["film"]["fps"]
        return Fraction(f["num"], f["den"])

    @property
    def sample_rate(self) -> int:
        return self.data["audio"]["sample_rate"]

    def segment(self, segment_id: str) -> dict:
        for s in self.data["segments"]:
            if s["id"] == segment_id:
                return s
        raise KeyError(segment_id)

    def film_order(self) -> list:
        """The enabled segments, in the order of the file."""
        return [s for s in self.data["segments"] if s.get("enabled", True)]

    @staticmethod
    def segment_length_frames(seg) -> int:
        return seg["out"] - seg["in"]

    def segment_lengths(self) -> dict:
        return {s["id"]: self.segment_length_frames(s) for s in self.film_order()}

    def segment_film_starts(self) -> dict:
        """The film frame at which each enabled segment starts."""
        starts, at = {}, 0
        for s in self.film_order():
            starts[s["id"]] = at
            at += self.segment_length_frames(s)
        return starts

    def film_length_frames(self) -> int:
        return sum(self.segment_length_frames(s) for s in self.film_order())

    def film_sample(self, frame: int) -> int:
        """Sample position of a film frame: round(frame * rate * den / num)."""
        return round(Fraction(frame) * self.sample_rate / self.fps)

    def segment_sample_length(self, seg) -> int:
        """Samples of a segment: the difference of two film sample positions, so
        rounding never adds up over many segments."""
        start = self.segment_film_starts()[seg["id"]]
        return self.film_sample(start + self.segment_length_frames(seg)) - self.film_sample(start)

    def source_audio_start(self, seg) -> int:
        """Sample position in the source WAV at which the segment's sound starts:
        round((in * den / num + offset) * rate), where offset is the one the owner typed
        (`audio_offset_hand_s`) or else the one from matching (`audio_offset_s`), with
        the drift correction of SPECS 7.3: the start moves by in/fps * ppm * 1e-6 seconds."""
        src = self.data["sources"][seg["source"]]
        t = Fraction(seg["in"]) / self.fps + Fraction(effective_offset(src))
        ppm = src.get("audio_drift_ppm", 0)
        if ppm:
            t += Fraction(seg["in"]) / self.fps * Fraction(ppm) * Fraction(1, 10 ** 6)
        return round(t * self.sample_rate)
