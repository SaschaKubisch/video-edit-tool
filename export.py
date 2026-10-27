"""
The final film (SPECS 5.3, 5.6, 5.7, 5.8, 8.5; requirements E2, E3, E6).

The picture is decoded from the original `.braw` files at full size and never from a preview
copy. For each enabled segment, in film order, three kinds of process work together:

1. the decoder (in this process, through the Decoder interface) writes the frames `in` to
   `out - 1` as raw 16-bit RGB to the standard input of
2. a segment filter (`ffmpeg`), which applies the base LUT, the creative LUT if there is
   one, the placement of SPECS 5.2 (the black bars of a wide clip are added in RGB, so they
   come out as video-level black) and the conversion to BT.709 video levels, and writes raw
   frames of the export size to the standard input of
3. the encoder (`ffmpeg`), one process for the whole film. It applies the film fades, encodes,
   and muxes the sound that was made first (SPECS 5.4), with the timecode, the colour tags
   and `faststart`.

The sound comes only from the WAV files (render.build_mix). It is levelled (decision D9),
encoded once, and read back to check the true peak after the encoder. Then the picture runs.

The encoder writes `<name>.<ext>.partial`. When it ends, the file is checked (SPECS 5.7 and
more) and renamed. A file that fails a check stays as `.partial` and the render report names
the check. A run that is interrupted (SIGTERM, Ctrl+C) or that fails removes its files.
"""

import datetime
import gc
import json
import os
import platform
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import config
import convert
import fsutil
import look
import loudness
import render
import safety
import textlayer
import textlayout
import timeline
import tools
import words

# ── Presets (SPECS 5.6, 8.5) ────────────────────────────────────

PRESETS = ("film-4k", "film-4k-x264", "film-master", "short")

PRORES_MB_PER_FRAME_DCI4K = 3.9      # SPECS 5.6: about 3.9 MB per frame at 4096 x 2160
SPACE_MARGIN = 1.1                   # E6: refuse when the free space is below 1.1 times the estimate


class ExportError(Exception):
    """An export cannot start, or its output failed."""


class ExportDisabled(ExportError):
    """The export is switched off (`enabled: false`)."""


class NotEnoughSpace(ExportError):
    """The folder has less free space than the estimate."""


class AlreadyRunning(ExportError):
    """Another render holds the lock of this work folder."""


def preset_entry(preset: str, output_dir: str, film_frame: dict, name: str = None,
                 width: int = None, height: int = None) -> dict:
    """An entry of `exports[]` for a preset. `width` and `height` set a custom resolution
    (it must have the shape of the film frame; the timeline rules check that). The name
    defaults to the preset's, or to `film-<height>p` for a custom size."""
    if preset not in PRESETS:
        raise ExportError(f"unknown preset {preset!r}; known: {', '.join(PRESETS)}")
    fw, fh = film_frame["width"], film_frame["height"]
    if (width is None) != (height is None):
        raise ExportError("give both width and height, or neither")
    w, h = (width, height) if width else (fw, fh)
    if name is None:
        name = preset if (w, h) == (fw, fh) else f"{preset.replace('-4k', '')}-{h}p"
    if preset == "film-master":
        return {"name": name, "output": os.path.join(output_dir, f"{name}.mov"), "enabled": False,
                "width": w, "height": h,
                "video": {"codec": "prores_videotoolbox", "profile": "hq"},
                "audio": {"codec": "pcm_s24le", "sample_rate": config.AUDIO_SAMPLE_RATE}}
    if preset == "short":
        # A vertical short (SPECS 11.7): MP4, H.264, 8 Mbit/s (YouTube's figure for 1080p at 24 to 30 frames per
        # second), AAC at 192 kbit/s and 48 kHz, the subtitles drawn into the picture (D13: the starting value for a short).
        return {"name": name, "output": os.path.join(output_dir, f"{name}.mp4"), "width": w, "height": h,
                "video": {"codec": "h264_videotoolbox",
                          "rate_control": {"mode": "bitrate", "mbps": config.SHORT_VIDEO_MBPS}},
                "audio": {"codec": "aac", "bitrate_kbps": config.SHORT_AUDIO_KBPS,
                          "sample_rate": config.AUDIO_SAMPLE_RATE},
                "subtitles": "burned"}
    video = {"codec": "h264_videotoolbox", "rate_control": {"mode": "bitrate", "mbps": 45}}
    if preset == "film-4k-x264":
        video = {"codec": "libx264", "rate_control": {"mode": "bitrate", "mbps": 45},
                 "preset": "slow"}
    return {"name": name, "output": os.path.join(output_dir, f"{name}.mp4"),
            "width": w, "height": h, "video": video,
            "audio": {"codec": "aac", "bitrate_kbps": 384, "sample_rate": config.AUDIO_SAMPLE_RATE}}


def find_export(data: dict, name: str) -> dict:
    for e in data.get("exports") or []:
        if e.get("name") == name:
            return e
    known = ", ".join(e.get("name", "?") for e in data.get("exports") or []) or "none"
    raise ExportError(f"the timeline file has no export {name!r} (exports: {known})")


def subtitle_form(entry: dict, kind: str = "film") -> str:
    """What this export does with the subtitles (D13): `srt`, `burned`, `both` or `none`. An entry that
    names none gets the starting value for its kind (config.SUBTITLE_FORM_DEFAULT)."""
    return entry.get("subtitles") or config.SUBTITLE_FORM_DEFAULT.get(kind, "srt")


def srt_path(output: str, lang: str = "en") -> str:
    """The SRT file next to the film: <film>.en.srt (language codes as YouTube writes them)."""
    return os.path.splitext(output)[0] + f".{lang}.srt"


def with_export(data: dict, entry: dict, replace: bool = False) -> dict:
    """A copy of the timeline data with the export entry added (or, with `replace`, replacing
    the entry of the same name)."""
    import copy
    out = copy.deepcopy(data)
    exports = out.setdefault("exports", [])
    for i, e in enumerate(exports):
        if e.get("name") == entry["name"]:
            if not replace:
                raise ExportError(f"the timeline file already has an export named {entry['name']!r}")
            exports[i] = entry
            return out
    exports.append(entry)
    return out


def with_export_enabled(data: dict, name: str, enabled: bool) -> dict:
    import copy
    out = copy.deepcopy(data)
    find_export(out, name)["enabled"] = enabled
    return out


# ── Sizes and space (E6) ────────────────────────────────────────

def pixel_format(entry: dict) -> str:
    """The raw pixel format between the segment filter and the encoder."""
    return "yuv422p10le" if entry["video"]["codec"].startswith("prores") else "yuv420p"


def frame_bytes(entry: dict, width: int = None, height: int = None) -> int:
    w, h = width or entry["width"], height or entry["height"]
    return w * h * (4 if pixel_format(entry) == "yuv422p10le" else 3 // 2)


def estimate_bytes(entry: dict, frames: int, fps) -> int:
    """The size of the finished file, from the settings (not measured): the video data rate
    for H.264, about 3.9 MB per DCI 4K frame for ProRes (SPECS 5.6), and the sound."""
    seconds = float(frames / fps)
    rate = config.AUDIO_SAMPLE_RATE
    if entry["video"]["codec"].startswith("prores"):
        video = PRORES_MB_PER_FRAME_DCI4K * 1e6 * frames * (entry["width"] * entry["height"]) / (4096 * 2160)
        audio = seconds * rate * 2 * 3
    else:
        rc = entry["video"].get("rate_control") or {"mode": "bitrate", "mbps": 45}
        mbps = rc.get("mbps", 45) if rc.get("mode") == "bitrate" else 60   # a crf has no rate: assume a high one
        video = seconds * mbps * 1e6 / 8
        audio = seconds * (entry["audio"].get("bitrate_kbps", 384) * 1000 / 8)
    return int(video + audio)


def scratch_bytes(frames: int, fps, rate: int) -> int:
    """The raw sound file the encoder may read (32-bit float stereo) and the logs."""
    return int(float(frames / fps) * rate * 8) + (1 << 20)


# ── The ffmpeg command lines ────────────────────────────────────

def _lut_filter(path: str) -> str:
    return f"lut3d=file={convert.escape_filter_path(path)}:interp=tetrahedral"


def segment_filter(tl, seg, source_size: dict, pix_fmt: str, frame: tuple) -> str:
    """The filter chain of the segment process: the base LUT, the creative LUT, the placement
    (SPECS 5.2), the conversion to BT.709 video levels. All of it before the conversion works
    on the 16-bit RGB picture, so the bars of a wide clip are black before they become Y = 16."""
    look_ = tl.data["look"]
    chain = []
    base = look_["base"]
    if base.get("mode") == "lut":
        chain.append(_lut_filter(base["path"]))
    if look_.get("lut"):
        chain.append(_lut_filter(look_["lut"]["path"]))
    p = seg.placement
    W, H = frame
    sw, sh = source_size["width"], source_size["height"]
    if p["mode"] == "fit":
        if (p["w"], p["h"]) != (sw, sh):
            chain.append(f"scale={p['w']}:{p['h']}:flags=bicubic")
        if (p["w"], p["h"]) != (W, H):
            chain.append(f"pad={W}:{H}:{p['x']}:{p['y']}:color=black")
    else:
        if p["wx_from"] == p["wx_to"]:
            wx = str(p["wx_from"])
        else:
            span = max(1, seg.length_frames - 1)
            wx = f"{p['wx_from']}+({p['wx_to'] - p['wx_from']})*n/{span}"
        chain.append(f"crop={p['ww']}:{p['wh']}:'{wx}':{p['wy']}")
        chain.append(f"scale={W}:{H}:flags=bicubic")
    chain.append("scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int+bicubic")
    chain.append(f"format={pix_fmt}")
    return ",".join(chain)


def segment_command(ffmpeg, tl, seg, source_size: dict, entry: dict, frame: tuple) -> list:
    fps = tl.data["film"]["fps"]
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "rawvideo", "-pix_fmt", "gbrp16le",
            "-s", f"{source_size['width']}x{source_size['height']}",
            "-r", f"{fps['num']}/{fps['den']}", "-i", "pipe:0",
            "-vf", segment_filter(tl, seg, source_size, pixel_format(entry), frame),
            "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", pixel_format(entry), "pipe:1"]


def video_encoder_args(entry: dict) -> list:
    v = entry["video"]
    codec = v["codec"]
    if codec == "h264_videotoolbox":
        mbps = (v.get("rate_control") or {}).get("mbps", 45)
        return ["-c:v", "h264_videotoolbox", "-profile:v", "high", "-allow_sw", "0",
                "-g", str(config.EXPORT_GOP), "-tag:v", "avc1", "-b:v", f"{mbps:g}M"]
    if codec == "libx264":
        rc = v.get("rate_control") or {"mode": "bitrate", "mbps": 45}
        args = ["-c:v", "libx264", "-profile:v", "high", "-preset", v.get("preset", "slow"),
                "-g", str(config.EXPORT_GOP), "-tag:v", "avc1"]
        args += ["-crf", str(rc["crf"])] if rc.get("mode") == "crf" else ["-b:v", f"{rc.get('mbps', 45):g}M"]
        return args
    if codec == "prores_videotoolbox":
        return ["-c:v", "prores_videotoolbox", "-profile:v", v.get("profile", "hq"), "-allow_sw", "0"]
    raise ExportError(f"unknown video codec {codec!r}")


H264_COLOUR_BSF = ("h264_metadata=colour_primaries=1:transfer_characteristics=1:"
                   "matrix_coefficients=1:video_full_range_flag=0")


def encoder_command(ffmpeg, tl, plan, entry: dict, audio_input: list, out_path: str,
                    timecode: str = "00:00:00:00", text=None) -> list:
    """The encoder process: raw frames in on the standard input, the finished sound as a
    second input, fades, the colour tags, the timecode, `faststart`. With `text` (a textlayer plan
    that has been written) the cues and titles are laid over the picture before the fades, so that
    they fade with the picture; the lanes are inputs 1 to n and the sound is input n + 1."""
    fps = tl.data["film"]["fps"]
    first, last = plan.segments[0], plan.segments[-1]
    fades = []
    if first.picture_fade_in_frames:
        fades.append(f"fade=t=in:s=0:n={first.picture_fade_in_frames}")
    m = last.picture_fade_out_frames
    if m >= 2:
        # The last frame is black, as the last sample of the sound is silent: the fade takes
        # the m - 1 frames before it, and a frame past the end of a fade is black.
        fades.append(f"fade=t=out:s={plan.film_frames - m}:n={m - 1}")
    elif m == 1:
        fades.append(f"fade=t=out:s={plan.film_frames - 1}:n=1")
    if entry["video"]["codec"].startswith("prores"):
        # The VideoToolbox ProRes encoder writes only the matrix into the file, whatever the
        # -color_* options say; tagging the frames makes the colr atom carry all three [measured].
        fades.insert(0, "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-f", "rawvideo", "-pix_fmt", pixel_format(entry), "-s", f"{entry['width']}x{entry['height']}",
           "-r", f"{fps['num']}/{fps['den']}", "-i", "pipe:0"]
    if text is None:
        cmd += [*audio_input, "-map", "0:v:0", "-map", "1:a:0"]
        if fades:
            cmd += ["-vf", ",".join(fades)]
    else:
        graph, label = text.graph("0:v", 1, pixel_format(entry))
        if fades:
            graph += f";[{label}]{','.join(fades)}[vout]"
            label = "vout"
        cmd += [*text.inputs(), *audio_input, "-filter_complex", graph, "-map", f"[{label}]",
                "-map", f"{len(text.lanes) + 1}:a:0"]
    cmd += video_encoder_args(entry)
    cmd += ["-fps_mode", "cfr", "-r", f"{fps['num']}/{fps['den']}",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-color_range", "tv"]
    if entry["video"]["codec"] in ("h264_videotoolbox", "libx264"):
        # The VideoToolbox encoder writes only the matrix into the stream header; this filter
        # sets primaries, transfer and range there too (SPECS 5.6).
        cmd += ["-bsf:v", H264_COLOUR_BSF]
    cmd += ["-timecode", timecode, "-map_metadata", "-1", "-movflags", "+faststart",
            "-f", "mov" if str(out_path).endswith((".mov", ".mov.partial")) else "mp4", str(out_path)]
    return cmd


def audio_input_args(entry: dict, audio_path: str, rate: int) -> list:
    """The sound input of the encoder and what is done with it."""
    if entry["audio"]["codec"] == "aac":
        return ["-i", audio_path, "-c:a", "copy"]
    return ["-f", "f32le", "-ar", str(rate), "-ac", "2", "-i", audio_path,
            "-c:a", entry["audio"]["codec"], "-ar", str(entry["audio"].get("sample_rate", rate))]


# ── The sound (SPECS 5.4) ───────────────────────────────────────

def encode_aac(samples, path: str, rate: int, kbps: int, out_rate: int, ffmpeg: str) -> None:
    """Encode float stereo `samples` as AAC-LC in an MP4 audio file."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-f", "f32le", "-ar", str(rate), "-ac", "2", "-i", "pipe:0",
           "-c:a", "aac", "-b:a", f"{kbps}k", "-ar", str(out_rate), "-movflags", "+faststart",
           "-f", "mp4", str(path)]
    r = subprocess.run(cmd, input=loudness._as_bytes_view(samples), capture_output=True)
    if r.returncode != 0:
        raise ExportError("ffmpeg could not encode the sound: " + r.stderr.decode("utf-8", "replace").strip()[-400:])


AAC_TRIES = 3


def make_audio_file(samples, entry: dict, settings: dict, rate: int, scratch: str, ffmpeg: str,
                    warn=lambda m: None, encode=None, measure_file=None, limiter=None) -> tuple:
    """Write the film's sound in the form the encoder takes. For AAC the sound is encoded
    here and read back: the encoder moves the true peak a little, so when the decoded file is
    over the limit the sound gets a limiter and is encoded again (at most three times). The
    limiter works on sample values, and the true peak of a sound with sharp transients lies
    above its sample peak, so the ceiling is the sample peak minus the excess. Returns
    (path, Level of the file as a player decodes it, or None for PCM)."""
    encode = encode or (lambda x, path, kbps, out_rate: encode_aac(x, path, rate, kbps, out_rate, ffmpeg))
    measure_file = measure_file or (lambda path: loudness.measure_file(path, ffmpeg))
    limiter = limiter or (lambda x, ceiling: loudness.apply_limiter(x, ceiling, rate, ffmpeg))
    if entry["audio"]["codec"] != "aac":
        path = os.path.join(scratch, "mix.f32le")
        np.ascontiguousarray(samples, dtype="<f4").tofile(path)
        return path, None
    path = os.path.join(scratch, "mix.m4a")
    out_rate = entry["audio"].get("sample_rate", rate)
    kbps = entry["audio"].get("bitrate_kbps", 384)
    limit_db = settings.get("true_peak_dbtp", -1.0)
    limit = limit_db - loudness.PEAK_MARGIN_DB
    levelled = settings.get("mode", "off") != "off"
    level = None
    for attempt in range(AAC_TRIES + 1):
        encode(samples, path, kbps, out_rate)
        level = measure_file(path)
        if not levelled or level.peak_dbtp <= limit or attempt == AAC_TRIES:
            break
        sample_peak = float(np.abs(samples).max())
        sample_peak_db = 20.0 * np.log10(sample_peak) if sample_peak > 0 else -120.0
        ceiling = sample_peak_db - (level.peak_dbtp - limit) - 0.1
        warn(f"after AAC the true peak is {level.peak_dbtp:.2f} dBTP; the sound gets a limiter with "
             f"a ceiling of {ceiling:.2f} dB and is encoded again")
        samples = limiter(samples, ceiling)
    if levelled and level.peak_dbtp > limit:
        warn(f"the true peak of the encoded sound is {level.peak_dbtp:.2f} dBTP, above the limit of "
             f"{limit_db:g} dBTP")
    return path, level


# ── The checks on the finished file (SPECS 5.7) ─────────────────

@dataclass
class FileCheck:
    ok: bool
    problems: list = field(default_factory=list)
    details: dict = field(default_factory=dict)


def _probe(path: str, ffprobe: str) -> dict:
    cmd = [ffprobe, "-v", "error", "-count_packets", "-show_entries",
           "stream=index,codec_type,codec_name,profile,pix_fmt,width,height,avg_frame_rate,"
           "nb_read_packets,sample_rate,channels,duration,start_time,color_range,color_space,"
           "color_transfer,color_primaries:stream_tags=timecode:format=duration,size,bit_rate",
           "-of", "json", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ExportError(r.stderr.strip() or "ffprobe failed")
    return json.loads(r.stdout)


def verify_file(path: str, entry: dict, frames: int, fps, ffprobe: str = None) -> FileCheck:
    """The checks of SPECS 5.7 and the ones the export promises: the container and codec,
    the pixel format, the size, the frame rate, the colour tags, as many video frames as the
    film has, a sound track as long as the picture within one frame, the timecode, the moov
    box before the mdat box, and a file that is not empty."""
    ffprobe = ffprobe or tools.ffprobe_path()
    problems, details = [], {}
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return FileCheck(False, [f"{path} is empty or missing"], {})
    try:
        info = _probe(path, ffprobe)
    except (ExportError, ValueError) as e:
        return FileCheck(False, [f"ffprobe cannot read the file: {e}"], {})
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if video is None:
        return FileCheck(False, ["no video stream"], {})
    codec = entry["video"]["codec"]
    want_codec = "prores" if codec.startswith("prores") else "h264"
    details.update(codec=video.get("codec_name"), profile=video.get("profile"),
                   pix_fmt=video.get("pix_fmt"), width=video.get("width"), height=video.get("height"))
    if video.get("codec_name") != want_codec:
        problems.append(f"codec is {video.get('codec_name')}, expected {want_codec}")
    if want_codec == "h264":
        if video.get("profile") != "High":
            problems.append(f"profile is {video.get('profile')}, expected High")
        if video.get("pix_fmt") != "yuv420p":
            problems.append(f"pixel format is {video.get('pix_fmt')}, expected yuv420p")
    else:
        if video.get("profile") != "HQ":
            problems.append(f"ProRes profile is {video.get('profile')}, expected HQ")
        if video.get("pix_fmt") != "yuv422p10le":
            problems.append(f"pixel format is {video.get('pix_fmt')}, expected yuv422p10le")
    if (video.get("width"), video.get("height")) != (entry["width"], entry["height"]):
        problems.append(f"size is {video.get('width')}x{video.get('height')}, "
                        f"expected {entry['width']}x{entry['height']}")
    rate = convert._frac(video.get("avg_frame_rate"))
    num, den = fps.numerator, fps.denominator
    if rate is None or rate[0] * den != num * rate[1]:
        problems.append(f"frame rate is {video.get('avg_frame_rate')}, expected {num}/{den}")
    n = int(video.get("nb_read_packets", 0) or 0)
    details["frames"] = n
    if n != frames:
        problems.append(f"the file has {n} video frames, the film has {frames}")
    want = {"color_range": "tv", "color_space": "bt709", "color_transfer": "bt709",
            "color_primaries": "bt709"}
    bad = {k: video.get(k) for k, v in want.items() if video.get(k) != v}
    if bad:
        problems.append(f"colour tags are not BT.709 / TV range: {bad}")
    tc = (video.get("tags") or {}).get("timecode", "")
    details["timecode"] = tc
    if tc != "00:00:00:00":
        # The timecode may sit on a separate data stream in a .mov file.
        tcs = [(s.get("tags") or {}).get("timecode") for s in streams]
        if "00:00:00:00" not in tcs:
            problems.append(f"timecode is '{tc}', expected '00:00:00:00'")
    if len(audio) != 1:
        problems.append(f"expected one sound track, found {len(audio)}")
    else:
        a = audio[0]
        details["audio_codec"] = a.get("codec_name")
        want_audio = "aac" if entry["audio"]["codec"] == "aac" else entry["audio"]["codec"]
        if a.get("codec_name") != want_audio:
            problems.append(f"sound codec is {a.get('codec_name')}, expected {want_audio}")
        if int(a.get("sample_rate", 0) or 0) != entry["audio"].get("sample_rate", config.AUDIO_SAMPLE_RATE):
            problems.append(f"sound sample rate is {a.get('sample_rate')}")
        if int(a.get("channels", 0) or 0) != 2:
            problems.append(f"sound has {a.get('channels')} channels, expected 2")
        try:
            one_frame = float(1 / fps)
            dv, da = float(video["duration"]), float(a["duration"])
            details["video_seconds"], details["audio_seconds"] = dv, da
            if abs(dv - da) >= one_frame:
                problems.append(f"the sound is {da:.3f} s long and the picture {dv:.3f} s: "
                                "they differ by one frame or more")
            st = float(a.get("start_time") or 0)
            if abs(st - float(video.get("start_time") or 0)) >= one_frame:
                problems.append(f"the sound starts at {st:.3f} s, the picture at "
                                f"{video.get('start_time')} s")
        except (KeyError, ValueError):
            problems.append("the duration of the picture or the sound is unknown")
    try:
        order = convert.mp4_box_order(path)
        if "moov" not in order or "mdat" not in order:
            problems.append(f"missing moov or mdat box (boxes: {order})")
        elif order.index("moov") > order.index("mdat"):
            problems.append("the moov box is after the mdat box (not faststart)")
    except OSError as e:
        problems.append(f"cannot read the file: {e}")
    fmt = info.get("format") or {}
    details["bytes"] = int(fmt.get("size", 0) or 0)
    try:
        details["mbps"] = round(int(fmt.get("bit_rate")) / 1e6, 2)
    except (TypeError, ValueError):
        details["mbps"] = None
    return FileCheck(not problems, problems, details)


# ── The render ──────────────────────────────────────────────────

class _CountingSink:
    """A file object that counts what is written, so the render can show its progress while
    the decoder writes a segment."""

    def __init__(self, f, frame_bytes: int, on_frames):
        self.f, self.frame_bytes, self.on_frames = f, frame_bytes, on_frames
        self.written = 0
        self.frames = 0

    def write(self, data):
        self.f.write(data)
        self.written += len(data)
        frames = self.written // self.frame_bytes
        if frames != self.frames:
            self.frames = frames
            self.on_frames(frames)

    def flush(self):
        self.f.flush()

    def __getattr__(self, name):
        return getattr(self.f, name)


@dataclass
class ExportResult:
    name: str
    path: str = ""
    frames: int = 0
    seconds: float = 0.0
    elapsed_s: float = 0.0
    video_fps: float = 0.0
    realtime_factor: float = 0.0
    bytes: int = 0
    mbps: object = None
    stage_s: dict = field(default_factory=dict)
    levels: dict = None                 # the loudness report of the mix
    output_loudness: dict = None        # as measured on the finished file
    warnings: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    checks: dict = field(default_factory=dict)
    credit_file: str = None
    srt_file: str = None
    subtitles: str = ""
    report_file: str = None
    ok: bool = False

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "name", "path", "frames", "seconds", "elapsed_s", "video_fps", "realtime_factor",
            "bytes", "mbps", "stage_s", "levels", "output_loudness", "warnings", "problems",
            "checks", "credit_file", "srt_file", "subtitles", "ok")}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tail(path: str, n: int = 600) -> str:
    try:
        return Path(path).read_text(errors="replace").strip()[-n:]
    except OSError:
        return ""


def _kill(proc) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.kill()
        proc.wait()
    if proc.stdin is not None:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass


def _remove(path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


class _Lock:
    """work/.render.lock holds the pid of the run that renders in this work folder."""

    def __init__(self, work_dir: str):
        self.path = os.path.join(work_dir, ".render.lock")

    def acquire(self):
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    pid = int(Path(self.path).read_text().strip() or 0)
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
                    raise AlreadyRunning(f"Another render (process {pid}) is using this work folder.")
                _remove(self.path)
                continue
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            return
        raise AlreadyRunning(f"Cannot take the lock {self.path}.")

    def release(self):
        _remove(self.path)


def _check_sources(tl, plan, decoder, log) -> None:
    """Each clip the film uses: the original exists, and the decoder reports the size, the
    frame rate and the frame count that the timeline file says (a mismatch would put wrong
    pictures in the film)."""
    fps = tl.data["film"]["fps"]
    seen = set()
    for seg in plan.segments:
        if seg.source in seen:
            continue
        seen.add(seg.source)
        src = tl.data["sources"][seg.source]
        if not os.path.isfile(src["original_video"]):
            raise ExportError(f"the original of clip {seg.source} is missing: {src['original_video']}")
        if not os.path.isfile(src["audio"]):
            raise ExportError(f"the sound file of clip {seg.source} is missing: {src['audio']}")
        try:
            info = decoder.probe(src["original_video"])
        except Exception as e:
            raise ExportError(f"cannot open the clip {seg.source}: {e}")
        if (info.width, info.height) != (src["size"]["width"], src["size"]["height"]):
            raise ExportError(f"clip {seg.source} decodes at {info.width}x{info.height}, the timeline "
                              f"file says {src['size']['width']}x{src['size']['height']}")
        if info.fps_num * fps["den"] != info.fps_den * fps["num"]:
            raise ExportError(f"clip {seg.source} runs at {info.fps_num}/{info.fps_den} fps, the film "
                              f"at {fps['num']}/{fps['den']}")
        if info.frames != src["frames"]:
            raise ExportError(f"clip {seg.source} has {info.frames} frames, the timeline file says "
                              f"{src['frames']}")
    for key in ("base", "lut"):
        entry = tl.data["look"].get(key)
        if entry and (key == "lut" or entry.get("mode") == "lut"):
            if not os.path.isfile(entry["path"]):
                raise ExportError(f"the LUT file {entry['path']} is missing")
            if look.sha256_file(entry["path"]) != entry["sha256"]:
                raise ExportError(f"the LUT file {entry['path']} changed (its hash differs from the "
                                  "timeline file); run look set again")
    log(f"checked {len(seen)} clip(s): originals, sound files, sizes, frame counts, LUTs")


def render_export(tl, name: str, work_dir: str, decoder, timeline_hash: str = None, protected=None,
                  loader=None, free_bytes=None, ffmpeg: str = None, ffprobe: str = None,
                  log=print, progress=None, force: bool = False, report_name: str = "render-report",
                  mix_cache: bool = True, subtitles: str = None) -> ExportResult:
    """Render the export `name` of the timeline `tl` from the original `.braw` files.

    `decoder` is a Decoder (decoder.py). `loader(path)` returns a WAV as float32 stereo at
    the mix rate (default: ffmpeg). `progress(frames_done, frames_total, fps)` is called as
    frames are written. `force` renders an export that is switched off. `subtitles` (srt, burned, both,
    none) replaces the form of the subtitles for this render only; the timeline file is not changed. Raises ExportError
    (ExportDisabled, NotEnoughSpace, AlreadyRunning) before anything is written, and
    KeyboardInterrupt after it has removed what it made. On a failed check the file stays as
    `.partial`; the result has `ok` False and the problems."""
    entry = find_export(tl.data, name)
    if not entry.get("enabled", True) and not force:
        raise ExportDisabled(f"the export {name!r} is switched off (enabled: false). "
                             f"Switch it on with: python main.py export enable {name}")
    findings = tl.validate(file_checks=False)
    errors = [f for f in findings if f.level == "error"]
    if errors:
        raise ExportError("The timeline is not valid: " + "; ".join(
            f"{f.rule} at {f.path}: {f.message}" for f in errors))
    ffmpeg = ffmpeg or tools.ffmpeg_path()
    ffprobe = ffprobe or tools.ffprobe_path()
    protected = protected or []
    out_path = entry["output"]
    if protected:
        safety.assert_not_inside(out_path, protected)
        safety.assert_not_inside(work_dir, protected)
    try:
        convert.check_work_dir_available(os.path.dirname(os.path.abspath(out_path)))
    except convert.WorkDirUnavailable as e:
        raise ExportError(str(e))
    fps = tl.fps
    plan = render.make_plan(tl, preview_width=entry["width"])
    frames = plan.film_frames
    seconds = float(frames / fps)
    free_bytes = free_bytes or convert._default_free_bytes
    need = int((estimate_bytes(entry, frames, fps)) * SPACE_MARGIN) + scratch_bytes(
        frames, fps, plan.sample_rate)
    free = free_bytes(os.path.dirname(os.path.abspath(out_path)))
    if free < need:
        raise NotEnoughSpace(
            f"Not enough free space for {name!r}: the file needs about {need / 1e9:.1f} GB "
            f"(with a margin of {int((SPACE_MARGIN - 1) * 100)} percent and the sound scratch file) "
            f"and {free / 1e9:.1f} GB is free in {os.path.dirname(os.path.abspath(out_path))}.")

    if subtitles is not None and subtitles not in config.SUBTITLE_FORMS:
        raise ExportError(f"subtitles must be one of {', '.join(config.SUBTITLE_FORMS)}, not {subtitles!r}")
    form = subtitles or subtitle_form(entry, tl.data["kind"])
    text = None
    if textlayer.has_text(tl.data, burn_cues=form in ("burned", "both")):
        text = textlayer.build(tl.data, (entry["width"], entry["height"]), 0, frames,
                               render.segment_rects(plan), burn_cues=form in ("burned", "both"))
        if text.empty:
            text = None
        else:
            try:
                textlayout.get_font("subtitle", 12)
                textlayout.get_font("title", 12)
            except textlayout.FontMissing as e:
                raise ExportError(f"The film has text to draw, and the font is missing. {e}")

    result = ExportResult(name=name, path=out_path, frames=frames, seconds=seconds, subtitles=form)
    warn = result.warnings.append
    if text is not None:
        for w in text.warnings:
            warn(w)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    lock = _Lock(work_dir)
    lock.acquire()
    scratch = None
    partial = out_path + ".partial"
    started = time.time()
    started_iso = _now_iso()
    load_start = os.getloadavg()
    keep_partial = False
    enc = filt = None
    try:
        scratch = tempfile.mkdtemp(prefix=f"render-{name}-", dir=work_dir)
        log(f"Export {name}: {frames} frames, {seconds:.1f} s, {entry['width']}x{entry['height']}, "
            f"{entry['video']['codec']} -> {out_path}")
        _check_sources(tl, plan, decoder, log)

        # The sound first (SPECS 5.4): the mix, its loudness, one encode, read back.
        t0 = time.time()
        cache_path = os.path.join(work_dir, "reports", "loudness-cache.json")
        cache = render._read_cache(cache_path) if mix_cache else None
        loader = loader or (lambda path: render.load_wav(path, plan.sample_rate))
        mix = render.build_mix(tl, plan, loader, cache=cache)
        if mix_cache:
            render._write_cache(cache_path, cache)
        result.levels = mix.levels
        for w in mix.warnings:
            warn(w)
        result.stage_s["mix_s"] = round(time.time() - t0, 1)
        log(f"sound mixed in {result.stage_s['mix_s']} s" + (
            f"; film {mix.levels['film']['after']['lufs']} LUFS, true peak "
            f"{mix.levels['film']['after']['true_peak_dbtp']} dBTP" if mix.levels else ""))
        t0 = time.time()
        audio_path, aac_level = make_audio_file(
            mix.samples, entry, render.loudness_settings(tl), plan.sample_rate, scratch, ffmpeg, warn)
        result.stage_s["audio_s"] = round(time.time() - t0, 1)
        del mix
        gc.collect()

        # The picture.
        t0 = time.time()
        enc_log = os.path.join(scratch, "encoder.log")
        if text is not None:
            text.write(os.path.join(scratch, "text"))
        cmd = encoder_command(ffmpeg, tl, plan, entry,
                              audio_input_args(entry, audio_path, plan.sample_rate), partial, text=text)
        with open(enc_log, "wb") as enc_err:
            enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=enc_err)
            done_before = 0
            last_log = time.time()
            for i, seg in enumerate(plan.segments, start=1):
                src = tl.data["sources"][seg.source]
                size = src["size"]
                seg_log = os.path.join(scratch, "segment.log")
                with open(seg_log, "wb") as seg_err:
                    filt = subprocess.Popen(
                        segment_command(ffmpeg, tl, seg, size, entry, (entry["width"], entry["height"])),
                        stdin=subprocess.PIPE, stdout=enc.stdin, stderr=seg_err)
                    base = done_before
                    state = {"last": 0}

                    def on_frames(n, base=base, state=state):
                        state["last"] = n
                        if progress:
                            progress(base + n, frames, (base + n) / max(1e-9, time.time() - t0))

                    sink = _CountingSink(filt.stdin, size["width"] * size["height"] * 6, on_frames)
                    broken = False
                    try:
                        written = decoder.write_frame_range(src["original_video"], 1, sink,
                                                            seg.in_frame, seg.out_frame)
                    except BrokenPipeError:
                        broken = True
                        written = state["last"]
                    except BaseException:
                        _kill(filt)
                        raise
                    finally:
                        try:
                            filt.stdin.close()
                        except BrokenPipeError:
                            pass
                    rc = filt.wait()
                filt = None
                if rc != 0 or broken:
                    if enc.poll() is not None and enc.returncode != 0:
                        raise ExportError(f"the encoder stopped on segment {seg.id}: {_tail(enc_log)}")
                    raise ExportError(f"the segment filter failed on segment {seg.id} "
                                      f"({seg.source}, frames {seg.in_frame} to {seg.out_frame}): "
                                      f"{_tail(seg_log)}")
                if written != seg.length_frames:
                    warn(f"segment {seg.id}: the decoder gave {written} frames, the segment has "
                         f"{seg.length_frames}")
                done_before += seg.length_frames
                if time.time() - last_log >= config.EXPORT_LOG_EVERY_SEC or i == len(plan.segments):
                    last_log = time.time()
                    rate = done_before / max(1e-9, time.time() - t0)
                    eta = (frames - done_before) / rate if rate else 0
                    log(f"[{i}/{len(plan.segments)}] segment {seg.id}  {done_before}/{frames} frames "
                        f"({100 * done_before / frames:.1f} percent)  {rate:.1f} fps  "
                        f"{rate / float(fps):.2f}x real time  eta {convert._hms(eta)}")
            try:
                enc.stdin.close()
            except BrokenPipeError:
                pass
            rc = enc.wait()
            enc = None
        if rc != 0:
            raise ExportError(f"the encoder failed (exit {rc}): {_tail(enc_log)}")
        video_s = time.time() - t0
        result.stage_s["video_s"] = round(video_s, 1)
        result.video_fps = round(frames / video_s, 2)
        result.realtime_factor = round(seconds / video_s, 3)

        # The checks, then the name.
        t0 = time.time()
        check = verify_file(partial, entry, frames, fps, ffprobe)
        result.checks = {"file": check.ok, **{k: v for k, v in check.details.items()}}
        result.bytes = check.details.get("bytes", 0)
        result.mbps = check.details.get("mbps")
        try:
            out_level = loudness.measure_file(partial, ffmpeg)
            result.output_loudness = out_level.as_dict()
        except RuntimeError as e:
            warn(f"the loudness of the file could not be measured: {e}")
            out_level = None
        settings = render.loudness_settings(tl)
        if settings["mode"] != "off" and out_level is not None:
            if out_level.lufs is None or abs(out_level.lufs - settings["target_lufs"]) > loudness.LOUDNESS_TOLERANCE_LU:
                warn(f"the file's integrated loudness is {out_level.as_dict()['lufs']} LUFS, more than "
                     f"{loudness.LOUDNESS_TOLERANCE_LU:g} LU from the target of {settings['target_lufs']:g} LUFS")
            if out_level.peak_dbtp > settings["true_peak_dbtp"]:
                warn(f"the file's true peak is {out_level.as_dict()['true_peak_dbtp']} dBTP, above the limit of "
                     f"{settings['true_peak_dbtp']:g} dBTP")
        want = entry["video"].get("rate_control") or {}
        if want.get("mode") == "bitrate" and result.mbps and not 0.75 <= result.mbps / want["mbps"] <= 1.25:
            warn(f"the file's data rate is {result.mbps} Mbit/s, the export asks for {want['mbps']:g}")
        result.stage_s["check_s"] = round(time.time() - t0, 1)
        if not check.ok:
            result.problems = check.problems
            keep_partial = True
            log("FAILED the checks: " + "; ".join(check.problems))
        else:
            os.replace(partial, out_path)
            result.ok = True
            if form in ("srt", "both"):
                spans = words.cue_spans(tl.data)
                srt = srt_path(out_path)
                fsutil.write_text_atomic(srt, words.srt_text(spans, words.RULES_FACTORY(tl.data)))
                result.srt_file = srt
                if not spans:
                    warn("the film has no subtitles, so the SRT file is empty")
            credit = look.film_credit(tl.data)
            if credit:
                credit_path = os.path.splitext(out_path)[0] + ".credit.txt"
                fsutil.write_text_atomic(credit_path, credit + "\n")
                result.credit_file = credit_path
    finally:
        _kill(enc)
        _kill(filt)
        if not keep_partial:
            _remove(partial)
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        lock.release()

    result.elapsed_s = round(time.time() - started, 1)
    write_report(work_dir, report_name, tl, entry, result, started_iso, load_start,
                 os.getloadavg(), timeline_hash, decoder, ffmpeg)
    log(f"{'Done' if result.ok else 'FAILED'}: {out_path}  {result.frames} frames  "
        f"{result.video_fps:.1f} fps  {result.realtime_factor:.2f}x real time  "
        f"{result.bytes / 1e9:.2f} GB  elapsed {convert._hms(result.elapsed_s)}")
    return result


def _ffmpeg_version(ffmpeg: str) -> str:
    try:
        r = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True)
        return r.stdout.splitlines()[0] if r.stdout else ""
    except OSError:
        return ""


def write_report(work_dir, report_name, tl, entry, result, started_iso, load_start, load_end,
                 timeline_hash, decoder, ffmpeg) -> None:
    """Append this render to <work>/reports/<report_name>.json (the newest 30 are kept)
    (SPECS 5.8): what was shortened, capped or silent, the film's loudness and true peak before
    and after levelling and as measured on the file, the speed, the checks."""
    path = os.path.join(work_dir, "reports", f"{report_name}.json")
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        if not isinstance(report, dict) or not isinstance(report.get("renders"), list):
            report = {"version": 1, "renders": []}
    except (OSError, ValueError):
        report = {"version": 1, "renders": []}
    levels = result.levels or {}
    seg_levels = levels.get("segments") or {}
    row = {
        "export": entry["name"], "output": entry["output"], "started_at": started_iso,
        "finished_at": _now_iso(), "ok": result.ok, "timeline_hash": timeline_hash,
        "tools": {"ffmpeg": _ffmpeg_version(ffmpeg), "decoder": getattr(decoder, "name", "decoder"),
                  "python": platform.python_version()},
        "settings": entry, "frames": result.frames, "seconds": round(result.seconds, 3),
        "elapsed_s": result.elapsed_s, "stage_s": result.stage_s,
        "speed": {"video_fps": result.video_fps, "realtime_factor": result.realtime_factor},
        "load_average": {"start": [round(x, 2) for x in load_start], "end": [round(x, 2) for x in load_end]},
        "peak_memory_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1e9 if sys.platform == "darwin" else 1e6), 2),
        "bytes": result.bytes, "mbps": result.mbps,
        "loudness": {"mode": levels.get("mode"), "target_lufs": levels.get("target_lufs"),
                     "true_peak_dbtp": levels.get("true_peak_dbtp"), "film": levels.get("film"),
                     "output_file": result.output_loudness},
        "gain_capped": [k for k, v in seg_levels.items() if v.get("capped")],
        "silent_segments": [k for k, v in seg_levels.items() if v.get("silent")],
        "warnings": result.warnings, "problems": result.problems, "checks": result.checks,
        "credit_file": result.credit_file, "subtitles": result.subtitles, "srt_file": result.srt_file}
    report["renders"].append(row)
    report["renders"] = report["renders"][-30:]
    fsutil.write_json_atomic(path, report)
    result.report_file = path


def latest_render(work_dir: str, name: str, report_name: str = "render-report"):
    """The newest report row of the export `name`, or None."""
    try:
        with open(os.path.join(work_dir, "reports", f"{report_name}.json"), encoding="utf-8") as f:
            rows = json.load(f).get("renders", [])
    except (OSError, ValueError, AttributeError):
        return None
    for row in reversed(rows):
        if row.get("export") == name:
            return row
    return None


# ── Running a render in the background ──────────────────────────

def spawn_detached(argv: list, log_path: str, pid_path: str, cwd: str = None) -> int:
    """Start `argv` as a process of its own session, so that it keeps running when the shell
    or the session that started it ends. Its output goes to `log_path` (appended) and its
    process id to `pid_path`. Refuses when the pid file names a process that is still alive.
    Returns the pid."""
    try:
        old = int(Path(pid_path).read_text().strip() or 0)
    except (OSError, ValueError):
        old = 0
    if old > 0:
        try:
            os.kill(old, 0)
            raise AlreadyRunning(f"A render is already running as process {old} ({pid_path}).")
        except ProcessLookupError:
            pass
        except PermissionError:
            raise AlreadyRunning(f"A render is already running as process {old} ({pid_path}).")
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    log = open(log_path, "ab")
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True, cwd=cwd)
    finally:
        log.close()
    fsutil.write_text_atomic(pid_path, f"{proc.pid}\n")
    import threading
    threading.Thread(target=proc.wait, daemon=True).start()      # reaps it if this process lives on
    return proc.pid
