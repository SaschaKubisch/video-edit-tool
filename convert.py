"""
The `convert` command: one small preview copy of every .braw clip (SPECS 6, PRD C1-C7).

A preview copy is an H.264 MP4, decoded at half size, with the base LUT baked in
and the camera's audio. Analysis, the film preview and Shotcut read the copies;
the final render reads the .braw files.

The picture comes from a Decoder (decoder.py) as raw 16-bit RGB frames, goes
through a pipe into ffmpeg, which applies the LUT (tetrahedral), converts to
BT.709 video levels and encodes. This is the chain measured in step 1.
"""

import datetime
import hashlib
import json
import os
import resource
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
import safety
import tools


# ── LUT ──────────────────────────────────────────────────────────

def escape_filter_path(path: str) -> str:
    """Quote a file path for use as an ffmpeg filter option value.

    ffmpeg unquotes a filter description twice: once for the filter graph and
    once for the options of the filter. So the path is first escaped for the
    option level (backslash, quote and colon get a backslash) and then wrapped
    in single quotes for the graph level (a quote inside is closed, escaped and
    reopened). Spaces need nothing. Tested with ffmpeg on a path with a space,
    a colon, a comma and a quote.
    """
    option_level = (str(path).replace("\\", "\\\\").replace("'", "\\'")
                    .replace(":", "\\:"))
    return "'" + option_level.replace("'", "'\\''") + "'"


def find_base_lut(explicit=None, environ=None, lut_dir=None) -> str:
    """Find the base LUT file at run time. The tool reads the LUT from Resolve's
    folder and never copies it into the repository.

    Order: the path given (--lut), the environment variable VET_BASE_LUT, then
    the default file name searched below Resolve's LUT folder.
    """
    environ = os.environ if environ is None else environ
    lut_dir = config.RESOLVE_LUT_DIR if lut_dir is None else lut_dir
    if explicit:
        if not Path(explicit).is_file():
            raise FileNotFoundError(f"LUT file not found: {explicit}")
        return str(explicit)
    from_env = environ.get("VET_BASE_LUT")
    if from_env:
        if not Path(from_env).is_file():
            raise FileNotFoundError(f"VET_BASE_LUT points to a missing file: {from_env}")
        return from_env
    root = Path(lut_dir)
    if root.is_dir():
        for candidate in sorted(root.rglob(config.BASE_LUT_FILE)):
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError(
        f"Base LUT '{config.BASE_LUT_FILE}' not found below {lut_dir}. "
        f"Install DaVinci Resolve (its LUT folder holds the Blackmagic LUTs), "
        f"or pass the file with --lut or the environment variable VET_BASE_LUT.")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ── The ffmpeg command ──────────────────────────────────────────

@dataclass
class PreviewSettings:
    scale: int = config.PREVIEW_SCALE
    quality: int = config.PREVIEW_Q
    gop: int = config.PREVIEW_GOP
    audio_kbps: int = config.PREVIEW_AUDIO_KBPS
    encoder: str = config.PREVIEW_ENCODER

    def as_dict(self) -> dict:
        return {"scale": self.scale, "quality": self.quality, "gop": self.gop,
                "audio_kbps": self.audio_kbps, "encoder": self.encoder}


def preview_size(info, scale: int) -> tuple:
    return info.width // scale, info.height // scale


def build_filter(lut_path) -> str:
    """The LUT on the full-range RGB picture, then the conversion to BT.709
    limited range, then 8-bit 4:2:0 (SPECS 6.3)."""
    chain = []
    if lut_path:
        chain.append(f"lut3d=file={escape_filter_path(lut_path)}:interp=tetrahedral")
    chain.append("scale=out_color_matrix=bt709:out_range=tv:"
                 "flags=accurate_rnd+full_chroma_int+bicubic")
    chain.append("format=yuv420p")
    return ",".join(chain)


def _encoder_args(settings: PreviewSettings) -> list:
    if settings.encoder == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-profile:v", "high", "-allow_sw", "0",
                "-g", str(settings.gop), "-tag:v", "avc1", "-q:v", str(settings.quality)]
    if settings.encoder == "libx264":
        # Software alternative; -crf 23 is a plain default, not a measured value.
        return ["-c:v", "libx264", "-profile:v", "high", "-preset", "veryfast",
                "-g", str(settings.gop), "-tag:v", "avc1", "-crf", "23"]
    raise ValueError(f"Unknown preview encoder: {settings.encoder}")


def build_ffmpeg_command(ffmpeg, info, audio, audio_raw, out_path, lut_path,
                         settings: PreviewSettings) -> list:
    """The ffmpeg command line of SPECS 6.3. The picture arrives on standard input."""
    w, h = preview_size(info, settings.scale)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "gbrp16le", "-s", f"{w}x{h}",
           "-r", f"{info.fps_num}/{info.fps_den}", "-i", "-"]
    if audio is not None:
        cmd += ["-f", "s32le", "-ar", str(audio.sample_rate),
                "-ac", str(audio.channels), "-i", str(audio_raw)]
    cmd += ["-map", "0:v:0"]
    if audio is not None:
        cmd += ["-map", "1:a:0"]
    cmd += ["-vf", build_filter(lut_path)]
    cmd += _encoder_args(settings)
    cmd += ["-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", "bt709", "-color_range", "tv"]
    if audio is not None:
        cmd += ["-c:a", "aac", "-b:a", f"{settings.audio_kbps}k"]
    if info.timecode_start:
        cmd += ["-timecode", info.timecode_start]
    # The VideoToolbox encoder writes only the matrix into the stream header;
    # this filter sets primaries, transfer and range there too (SPECS 5.6).
    cmd += ["-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
                      "matrix_coefficients=1:video_full_range_flag=0"]
    cmd += ["-map_metadata", "-1", "-movflags", "+faststart",
            "-f", "mp4", str(out_path)]
    return cmd


# ── The check on a finished copy (C5, SPECS 6.6) ────────────────

@dataclass
class CopyExpectation:
    """What a finished copy must look like. The values come from the source."""
    width: int
    height: int
    fps_num: int
    fps_den: int
    frames: int
    has_audio: bool = True
    audio_rate: int = 48000
    timecode: str = ""          # "" : the source has none, none is required
    colour_tags: bool = True    # BT.709 primaries, transfer, matrix; TV range
    # Length of the source's own audio in seconds. A .braw file can hold less audio
    # than picture (found on a real clip: 4.000 s against 4.87 s). When given, the
    # copy's audio is compared with this; when None, with the picture.
    audio_seconds: float = None


@dataclass
class CheckResult:
    ok: bool
    problems: list = field(default_factory=list)
    details: dict = field(default_factory=dict)


def mp4_box_order(path: str) -> list:
    """The types of the top-level boxes of an MP4 file, in file order."""
    boxes = []
    size_total = os.path.getsize(path)
    with open(path, "rb") as f:
        pos = 0
        while pos + 8 <= size_total:
            f.seek(pos)
            head = f.read(8)
            if len(head) < 8:
                break
            size, kind = struct.unpack(">I4s", head)
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
            elif size == 0:
                size = size_total - pos
            if size < 8:
                break
            boxes.append(kind.decode("latin-1"))
            pos += size
    return boxes


def _frac(text):
    try:
        num, den = str(text).split("/")
        return int(num), int(den)
    except ValueError:
        return None


def _probe_json(path, ffprobe):
    cmd = [ffprobe, "-v", "error", "-count_packets", "-show_entries",
           "stream=index,codec_type,codec_name,profile,pix_fmt,width,height,"
           "r_frame_rate,avg_frame_rate,nb_read_packets,sample_rate,channels,"
           "duration,color_range,color_space,color_transfer,color_primaries"
           ":stream_tags=timecode", "-of", "json", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ffprobe failed")
    return json.loads(r.stdout)


def _gray_frame_is_constant(path, seconds, ffmpeg):
    """Decode one frame at `seconds`. True when every pixel has one value.
    None when no frame could be decoded."""
    cmd = [ffmpeg, "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(path),
           "-frames:v", "1", "-vf", "scale=64:-2", "-pix_fmt", "gray",
           "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    return min(r.stdout) == max(r.stdout)


def verify_copy(path: str, expected: CopyExpectation, ffprobe=None, ffmpeg=None) -> CheckResult:
    """Check a preview copy the way SPECS 6.6 lists: codec, profile, pixel
    format, size, frame rate, frame count, audio, timecode, colour tags, the
    moov box before the mdat box, and a first and a middle frame that are not
    one constant value.

    The frame count is the number of packets in the video stream. An MP4 holds
    one packet per frame, and reading the packets is much faster than decoding
    the whole file.
    """
    ffprobe = ffprobe or tools.ffprobe_path()
    ffmpeg = ffmpeg or tools.ffmpeg_path()
    problems = []
    details = {}
    try:
        info = _probe_json(path, ffprobe)
    except (RuntimeError, ValueError) as e:
        return CheckResult(False, [f"ffprobe cannot read the file: {e}"], {})

    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if video is None:
        return CheckResult(False, ["no video stream"], {})

    details["codec"] = video.get("codec_name", "")
    details["pix_fmt"] = video.get("pix_fmt", "")
    if video.get("codec_name") != "h264":
        problems.append(f"codec is {video.get('codec_name')}, expected h264")
    if video.get("profile") != "High":
        problems.append(f"profile is {video.get('profile')}, expected High")
    if video.get("pix_fmt") != "yuv420p":
        problems.append(f"pixel format is {video.get('pix_fmt')}, expected yuv420p")
    if (video.get("width"), video.get("height")) != (expected.width, expected.height):
        problems.append(f"size is {video.get('width')}x{video.get('height')}, "
                        f"expected {expected.width}x{expected.height}")
    want_rate = (expected.fps_num, expected.fps_den)
    rate = _frac(video.get("avg_frame_rate"))
    if rate is None or rate[0] * want_rate[1] != want_rate[0] * rate[1]:
        problems.append(f"frame rate is {video.get('avg_frame_rate')}, "
                        f"expected {want_rate[0]}/{want_rate[1]}")
    frames = int(video.get("nb_read_packets", 0) or 0)
    details["frames"] = frames
    if frames != expected.frames:
        problems.append(f"{frames} frames, expected {expected.frames}")

    # Audio
    if expected.has_audio:
        if len(audio) != 1 or audio[0].get("codec_name") != "aac":
            problems.append("expected exactly one AAC audio stream, found "
                            f"{[a.get('codec_name') for a in audio]}")
            details["audio"] = ""
        else:
            details["audio"] = "aac"
            if int(audio[0].get("sample_rate", 0)) != expected.audio_rate:
                problems.append(f"audio sample rate is {audio[0].get('sample_rate')}, "
                                f"expected {expected.audio_rate}")
            try:
                frame_s = expected.fps_den / expected.fps_num
                audio_s = float(audio[0]["duration"])
                if expected.audio_seconds is not None:
                    gap = abs(audio_s - expected.audio_seconds)
                    if gap >= frame_s:
                        problems.append(
                            f"the audio is {audio_s:.3f} s but the source audio is "
                            f"{expected.audio_seconds:.3f} s (more than one frame apart)")
                else:
                    gap = abs(audio_s - float(video["duration"]))
                    if gap >= frame_s:
                        problems.append(f"audio and video differ by {gap:.3f} s, "
                                        f"more than one frame ({frame_s:.3f} s)")
            except (KeyError, ValueError):
                problems.append("audio or video duration is unknown")
    else:
        details["audio"] = ""
        if audio:
            problems.append("the source has no audio but the copy has an audio stream")

    # Timecode
    tc = (video.get("tags") or {}).get("timecode", "")
    details["timecode"] = tc
    if expected.timecode and tc != expected.timecode:
        problems.append(f"timecode is '{tc}', expected '{expected.timecode}'")

    # Colour tags
    if expected.colour_tags:
        want = {"color_range": "tv", "color_space": "bt709",
                "color_transfer": "bt709", "color_primaries": "bt709"}
        bad = {k: video.get(k) for k, v in want.items() if video.get(k) != v}
        if bad:
            problems.append(f"colour tags are not BT.709 / TV range: {bad}")

    # moov before mdat
    try:
        order = mp4_box_order(path)
        if "moov" not in order or "mdat" not in order:
            problems.append(f"missing moov or mdat box (boxes: {order})")
        elif order.index("moov") > order.index("mdat"):
            problems.append("moov box is after the mdat box (not faststart)")
    except OSError as e:
        problems.append(f"cannot read the file: {e}")

    # First and middle frame must not be one constant value
    try:
        duration = float(video.get("duration") or 0)
    except ValueError:
        duration = 0.0
    for label, t in (("first", 0.0), ("middle", duration / 2)):
        constant = _gray_frame_is_constant(path, t, ffmpeg)
        if constant is None:
            problems.append(f"the {label} frame cannot be decoded")
        elif constant:
            problems.append(f"the {label} frame is one constant value")

    return CheckResult(not problems, problems, details)


# ── Space estimate (C6, SPECS 6.7) ──────────────────────────────

class NotEnoughSpace(Exception):
    """The work directory has less free space than the estimate."""


@dataclass
class EstimateItem:
    kind: str       # "<width>x<height>" of the decoded picture
    width: int
    height: int
    minutes: float


def kind_of(info) -> str:
    return f"{info.width}x{info.height}"


def prior_bytes_per_minute(width: int, height: int) -> float:
    """Size per minute of a copy before any clip of that kind was converted.
    The 4096 x 2160 figure of step 1, scaled with the number of pixels. For the
    wide kind (4096 x 1720) this is 56 MB per minute against 37 measured: it
    errs on the large side."""
    return config.PREVIEW_PRIOR_MB_PER_MIN_DCI4K * 1_000_000 * (width * height) / (4096 * 2160)


def estimate_bytes(items, measured: dict, margin: float = None) -> int:
    """Bytes the copies of `items` will take, with the safety margin.
    `measured` maps a kind to bytes per minute measured on a converted clip."""
    margin = config.PREVIEW_SPACE_MARGIN if margin is None else margin
    total = 0.0
    for it in items:
        rate = measured.get(it.kind) or prior_bytes_per_minute(it.width, it.height)
        total += rate * it.minutes
    return int(total * margin)


def _gb(n) -> str:
    return f"{n / 1e9:.1f} GB"


def check_space(required: int, free: int) -> None:
    if free < required:
        raise NotEnoughSpace(
            f"Not enough free space in the work directory: the preview copies need "
            f"about {_gb(required)} (with margin) and {_gb(free)} is free.")


# ── The conversion file (SPECS 6.4) ─────────────────────────────

class ConversionStore:
    """conversion.json: one entry per clip. Written atomically after every clip,
    so an interrupted run loses at most the clip it was working on."""
    VERSION = 1

    def __init__(self, path: str, data: dict = None):
        self.path = str(path)
        self.data = data or {"version": self.VERSION, "clips": {}}

    @classmethod
    def load(cls, path: str) -> "ConversionStore":
        if not os.path.exists(path):
            return cls(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != cls.VERSION:
            raise ValueError(f"{path} has version {data.get('version')}; this tool "
                             f"reads version {cls.VERSION}. Update the tool.")
        data.setdefault("clips", {})
        return cls(path, data)

    @property
    def clips(self) -> dict:
        return self.data["clips"]

    def save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".conversion-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ── The converter ───────────────────────────────────────────────

class AlreadyRunning(Exception):
    """Another convert run holds the lock of this work directory."""


class WorkDirUnavailable(Exception):
    """The work directory cannot be used: the SSD is not mounted or not writable."""


@dataclass
class ClipRef:
    stem: str       # file name without extension; names the copy
    path: str       # the original (.braw)


@dataclass
class Summary:
    converted: int = 0
    skipped: int = 0
    failed: int = 0
    would_convert: int = 0
    total_frames: int = 0
    total_seconds: float = 0.0      # sum of the time spent on the converted clips
    total_bytes: int = 0
    media_seconds: float = 0.0
    wall_seconds: float = 0.0
    load_start: tuple = ()
    load_end: tuple = ()

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0

    @property
    def fps(self) -> float:
        return self.total_frames / self.total_seconds if self.total_seconds else 0.0


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hms(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _clip_minutes(info) -> float:
    return info.frames * info.fps_den / info.fps_num / 60.0


def _nearest_existing(path: str) -> str:
    p = Path(path).absolute()
    while not p.exists() and p != p.parent:
        p = p.parent
    return str(p)


def _default_free_bytes(path: str) -> int:
    return shutil.disk_usage(_nearest_existing(path)).free


def _peak_rss_gb(who) -> float:
    rss = resource.getrusage(who).ru_maxrss
    return rss / (1e9 if sys.platform == "darwin" else 1e6)


def check_work_dir_available(work_dir: str) -> None:
    """Refuse an SSD that is not mounted (a path below /Volumes/<name> whose
    volume is missing) and a folder that cannot be written."""
    parts = Path(work_dir).absolute().parts
    if len(parts) >= 3 and parts[1] == "Volumes" and not Path(*parts[:3]).exists():
        raise WorkDirUnavailable(
            f"The volume {Path(*parts[:3])} is not mounted. Connect the SSD and try again.")
    anchor = _nearest_existing(work_dir)
    if not os.access(anchor, os.W_OK):
        raise WorkDirUnavailable(f"Cannot write to {anchor}.")


class Converter:
    """Makes the preview copies of a list of clips in `work_dir`.

    work_dir/copies/<stem>.mp4          the preview copies
    work_dir/conversion.json            what was done, per clip
    work_dir/tmp/                       scratch files, removed after each clip
    work_dir/.convert.lock              held while a run is active

    Nothing is written anywhere else. In particular, nothing is written into a
    folder in `originals_folders` (M9): the work directory is checked against
    them before anything is created.
    """

    def __init__(self, work_dir, decoder, settings, lut_path, originals_folders,
                 log=print, free_bytes=_default_free_bytes, ffmpeg=None, ffprobe=None):
        self.work_dir = str(work_dir)
        self.decoder = decoder
        self.settings = settings
        self.lut_path = lut_path
        self.originals_folders = list(originals_folders)
        self.log = log
        self.free_bytes = free_bytes
        self.ffmpeg = ffmpeg or tools.ffmpeg_path()
        self.ffprobe = ffprobe or tools.ffprobe_path()
        self.copies_dir = os.path.join(self.work_dir, "copies")
        self.tmp_dir = os.path.join(self.work_dir, "tmp")
        self.conversion_path = os.path.join(self.work_dir, config.CONVERSION_FILE)
        self.lock_path = os.path.join(self.work_dir, ".convert.lock")

    # ── lock ──
    def _acquire_lock(self):
        for _ in range(2):
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    pid = int(Path(self.lock_path).read_text().strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                alive = False
                if pid > 0:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except ProcessLookupError:
                        alive = False
                    except PermissionError:
                        alive = True
                if alive:
                    raise AlreadyRunning(
                        f"Another convert run (process {pid}) is using {self.work_dir}.")
                try:
                    os.unlink(self.lock_path)
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            return
        raise AlreadyRunning(f"Cannot take the lock {self.lock_path}.")

    def _release_lock(self):
        try:
            os.unlink(self.lock_path)
        except FileNotFoundError:
            pass

    # ── helpers ──
    def _lut_sha(self) -> str:
        return sha256_file(self.lut_path) if self.lut_path else ""

    def _is_current(self, entry, ref, lut_sha) -> bool:
        """True when the clip has a finished copy that is still valid."""
        if not entry or entry.get("status") != "done":
            return False
        try:
            st = os.stat(ref.path)
            copy_size = os.path.getsize(entry["preview_copy"])
        except (OSError, KeyError):
            return False
        return (entry.get("original") == ref.path
                and entry.get("original_bytes") == st.st_size
                and entry.get("original_mtime_ns") == st.st_mtime_ns
                and entry.get("bytes") == copy_size
                and entry.get("lut_sha256") == lut_sha
                and entry.get("settings") == self.settings.as_dict())

    def _measured(self, store) -> dict:
        """Bytes per minute by kind, from the finished copies in the store that
        were made with the current settings."""
        sums = {}
        for e in store.clips.values():
            if e.get("status") != "done" or e.get("settings") != self.settings.as_dict():
                continue
            size = e.get("size") or {}
            fps = e.get("fps") or {}
            if not size or not fps or not e.get("frames"):
                continue
            kind = f"{size['width']}x{size['height']}"
            minutes = e["frames"] * fps["den"] / fps["num"] / 60.0
            b, m = sums.get(kind, (0, 0.0))
            sums[kind] = (b + e["bytes"], m + minutes)
        return {k: b / m for k, (b, m) in sums.items() if m > 0}

    def _required(self, todo, infos, measured) -> int:
        items = [EstimateItem(kind_of(infos[r.stem]), infos[r.stem].width,
                              infos[r.stem].height, _clip_minutes(infos[r.stem]))
                 for r in todo if r.stem in infos]
        scratch = 0
        for r in todo:
            info = infos.get(r.stem)
            if info:    # raw camera audio: 48 kHz, 2 channels, 4 bytes, assumed
                scratch = max(scratch, int(_clip_minutes(info) * 60 * 48000 * 2 * 4))
        return estimate_bytes(items, measured) + scratch

    def _cleanup_stale(self):
        for d in (self.copies_dir, self.tmp_dir):
            if os.path.isdir(d):
                for name in os.listdir(d):
                    if d == self.tmp_dir or name.endswith(".partial"):
                        try:
                            os.unlink(os.path.join(d, name))
                        except OSError:
                            pass

    # ── the run ──
    def run(self, clips, dry_run=False, max_frames=None) -> Summary:
        stems = [c.stem for c in clips]
        if len(set(stems)) != len(stems):
            dup = sorted({s for s in stems if stems.count(s) > 1})
            raise ValueError(f"Two clips have the same name: {', '.join(dup)}")
        safety.assert_not_inside(self.work_dir, self.originals_folders)
        check_work_dir_available(self.work_dir)

        summary = Summary(load_start=os.getloadavg())
        t_run = time.monotonic()
        if dry_run:
            self._run_locked(clips, summary, dry_run, max_frames)
        else:
            os.makedirs(self.copies_dir, exist_ok=True)
            os.makedirs(self.tmp_dir, exist_ok=True)
            self._acquire_lock()
            try:
                self._run_locked(clips, summary, dry_run, max_frames)
            finally:
                self._release_lock()
        summary.wall_seconds = time.monotonic() - t_run
        summary.load_end = os.getloadavg()
        self._print_summary(summary)
        return summary

    def _run_locked(self, clips, summary, dry_run, max_frames):
        store = ConversionStore.load(self.conversion_path)
        lut_sha = self._lut_sha()
        if not dry_run:
            self._cleanup_stale()
            store.data.update({
                "decoder": getattr(self.decoder, "name", "decoder"),
                "encoder": self.settings.encoder,
                "settings": self.settings.as_dict(),
                "base_lut": {"path": self.lut_path or "", "sha256": lut_sha},
            })

        todo, infos = [], {}
        for ref in clips:
            if not ref.path.lower().endswith(".braw"):
                summary.skipped += 1
                store.clips[ref.stem] = {"original": ref.path, "status": "skipped",
                                         "error": "not a .braw file"}
                continue
            if self._is_current(store.clips.get(ref.stem), ref, lut_sha):
                summary.skipped += 1
                continue
            todo.append(ref)
        already = summary.skipped
        if already:
            self.log(f"{already} clip(s) already have a current preview copy and are skipped.")

        # Probe what has to be converted (opening a clip decodes no picture).
        failed_refs = []
        for ref in todo:
            try:
                infos[ref.stem] = self.decoder.probe(ref.path)
            except Exception as e:      # the decoder may raise anything
                failed_refs.append(ref)
                summary.failed += 1
                self.log(f"FAILED {ref.stem}: cannot open the clip: {e}")
                store.clips[ref.stem] = {"original": ref.path, "status": "failed",
                                         "error": f"cannot open the clip: {e}"}
        todo = [r for r in todo if r.stem in infos]

        measured = self._measured(store)
        required = self._required(todo, infos, measured)
        free = self.free_bytes(self.work_dir)
        if dry_run:
            summary.would_convert = len(todo)
            minutes = sum(_clip_minutes(infos[r.stem]) for r in todo)
            self.log(f"Would convert {len(todo)} clip(s), {minutes:.1f} minutes of footage. "
                     f"Estimated size with margin: {required / 1e9:.1f} GB; "
                     f"free: {free / 1e9:.1f} GB.")
            check_space(required, free)
            return
        try:
            check_space(required, free)
        except NotEnoughSpace:
            store.save()
            raise
        store.save()

        done_media = 0.0
        media_total = sum(
            (infos[r.stem].frames if max_frames is None else min(max_frames, infos[r.stem].frames))
            * infos[r.stem].fps_den / infos[r.stem].fps_num for r in todo)
        t_start = time.monotonic()
        for i, ref in enumerate(todo, start=1):
            info = infos[ref.stem]
            frames = info.frames if max_frames is None else min(max_frames, info.frames)
            media = frames * info.fps_den / info.fps_num
            self.log(f"[{i}/{len(todo)}] {ref.stem}  {kind_of(info)}  {frames} frames  "
                     f"{media:.1f} s  converting ...")
            ok = self._convert_one(ref, info, store, lut_sha, max_frames, summary, i, len(todo),
                                   t_start, done_media, media_total)
            done_media += media
            if ok:
                measured = self._measured(store)
                remaining = todo[i:]
                if remaining:
                    try:
                        check_space(self._required(remaining, infos, measured),
                                    self.free_bytes(self.work_dir))
                    except NotEnoughSpace:
                        store.save()
                        raise
        summary.media_seconds = done_media

    def _convert_one(self, ref, info, store, lut_sha, max_frames, summary, i, n,
                     t_start, done_media, media_total) -> bool:
        st = os.stat(ref.path)
        copy_path = os.path.join(self.copies_dir, f"{ref.stem}.mp4")
        partial = copy_path + ".partial"
        audio_raw = os.path.join(self.tmp_dir, f"{ref.stem}.s32le")
        ffmpeg_log = os.path.join(self.tmp_dir, f"{ref.stem}.ffmpeg.log")
        w, h = preview_size(info, self.settings.scale)
        frames = info.frames if max_frames is None else min(max_frames, info.frames)

        warnings = []
        if info.colour_science_gen != config.EXPECTED_COLOUR_SCIENCE_GEN:
            warnings.append(
                f"colour science generation {info.colour_science_gen or 'unknown'}: the "
                f"default LUT is made for generation {config.EXPECTED_COLOUR_SCIENCE_GEN}")
        if (info.gamma and info.gamma != "Blackmagic Design Film") or \
                (info.gamut and info.gamut != "Blackmagic Design"):
            warnings.append(f"gamma/gamut '{info.gamma}' / '{info.gamut}' is not "
                            f"Blackmagic Design Film / Blackmagic Design")
        for wmsg in warnings:
            self.log(f"WARNING {ref.stem}: {wmsg}")

        entry = {
            "original": ref.path,
            "original_bytes": st.st_size,
            "original_mtime_ns": st.st_mtime_ns,
            "preview_copy": copy_path,
            "status": "partial",
            "frames": frames,
            "fps": {"num": info.fps_num, "den": info.fps_den},
            "size": {"width": info.width, "height": info.height},
            "copy_size": {"width": w, "height": h},
            "timecode_start": info.timecode_start,
            "colour_science_gen": info.colour_science_gen,
            "gamma": info.gamma,
            "gamut": info.gamut,
            "iso": info.iso,
            "white_balance_k": info.white_balance_k,
            "has_sidecar": info.has_sidecar,
            "audio": None,
            "lut_sha256": lut_sha,
            "settings": self.settings.as_dict(),
            "warnings": warnings,
        }
        store.clips[ref.stem] = entry
        store.save()

        t0 = time.monotonic()
        error = None
        proc = None
        try:
            ainfo = self.decoder.write_audio(ref.path, audio_raw, max_frames)
            entry["audio"] = None if ainfo is None else {
                "sample_rate": ainfo.sample_rate, "channels": ainfo.channels,
                "samples": ainfo.samples}
            audio_s = None
            if ainfo is not None:
                audio_s = ainfo.samples / ainfo.sample_rate
                picture_s = frames * info.fps_den / info.fps_num
                if abs(audio_s - picture_s) >= info.fps_den / info.fps_num:
                    word = "shorter" if audio_s < picture_s else "longer"
                    msg = (f"the camera audio ({audio_s:.2f} s) is {word} than the picture "
                           f"({picture_s:.2f} s) in the source; the copy carries what the "
                           f"source has")
                    warnings.append(msg)
                    self.log(f"WARNING {ref.stem}: {msg}")
            cmd = build_ffmpeg_command(self.ffmpeg, info, ainfo, audio_raw, partial,
                                       self.lut_path, self.settings)
            with open(ffmpeg_log, "wb") as errfile:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errfile)
                broken_pipe = False
                try:
                    self.decoder.write_frames(ref.path, self.settings.scale,
                                              proc.stdin, max_frames)
                except BrokenPipeError:
                    broken_pipe = True      # ffmpeg stopped early; its log says why
                except BaseException:
                    proc.kill()
                    proc.wait()
                    raise
                finally:
                    try:
                        proc.stdin.close()
                    except BrokenPipeError:
                        pass
                rc = proc.wait()
            if rc != 0 or broken_pipe:
                msg = Path(ffmpeg_log).read_text(errors="replace").strip()
                raise RuntimeError(f"ffmpeg failed (exit {rc}): {msg[-600:]}")
            result = verify_copy(
                partial,
                CopyExpectation(width=w, height=h, fps_num=info.fps_num,
                                fps_den=info.fps_den, frames=frames,
                                has_audio=ainfo is not None,
                                audio_rate=ainfo.sample_rate if ainfo else 48000,
                                timecode=info.timecode_start, colour_tags=True,
                                audio_seconds=audio_s),
                ffprobe=self.ffprobe, ffmpeg=self.ffmpeg)
            if not result.ok:
                raise RuntimeError("check of the copy failed: " + "; ".join(result.problems))
            os.replace(partial, copy_path)
            seconds = time.monotonic() - t0
            size = os.path.getsize(copy_path)
            media = frames * info.fps_den / info.fps_num
            entry.update({
                "status": "done",
                "convert_seconds": round(seconds, 2),
                "convert_fps": round(frames / seconds, 2),
                "realtime_factor": round(media / seconds, 2),
                "bytes": size,
                "check": result.details,
                "converted_at": _now_iso(),
            })
        except Exception as e:
            error = f"{type(e).__name__}: {e}" if not isinstance(e, RuntimeError) else str(e)
        finally:
            for leftover in (partial, audio_raw, ffmpeg_log):
                try:
                    os.unlink(leftover)
                except OSError:
                    pass
        if error:
            entry.update({"status": "failed", "error": error})
            store.save()
            summary.failed += 1
            self.log(f"[{i}/{n}] FAILED {ref.stem}: {error}")
            return False
        store.save()
        summary.converted += 1
        summary.total_frames += frames
        summary.total_seconds += seconds
        summary.total_bytes += size
        media_done = done_media + media
        elapsed = time.monotonic() - t_start
        eta = (elapsed / media_done) * (media_total - media_done) if media_done else 0
        self.log(f"[{i}/{n}] {ref.stem}  done  {frames / seconds:.1f} fps  "
                 f"{media / seconds:.2f}x real time  {size / 1e6:.1f} MB  "
                 f"elapsed {_hms(elapsed)}  eta {_hms(eta)}")
        return True

    def _print_summary(self, s: Summary):
        self.log(f"Converted {s.converted}, skipped {s.skipped}, failed {s.failed}.")
        if s.converted:
            self.log(f"Encoded {s.total_frames} frames in {s.total_seconds:.1f} s: "
                     f"{s.fps:.1f} fps. Copies: {s.total_bytes / 1e6:.1f} MB. "
                     f"Wall time {_hms(s.wall_seconds)}.")
        if s.load_start:
            self.log(f"Load average {s.load_start[0]:.1f} at start, {s.load_end[0]:.1f} at end. "
                     f"Peak memory: python {_peak_rss_gb(resource.RUSAGE_SELF):.2f} GB, "
                     f"ffmpeg {_peak_rss_gb(resource.RUSAGE_CHILDREN):.2f} GB.")
