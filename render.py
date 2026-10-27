"""
Rendering from the timeline file (SPECS section 5).

This module has three layers:

1. The render plan (5.1): a pure function of the timeline file. It lists, for every
   enabled segment, the film start frame and length, the picture placement (5.2), the
   sound's place in the WAV, the joins and the fades. It reads no media.
2. The sound (5.4): `build_mix` places each segment's sound from the WAV at the stored
   offset, crossfades at every join, applies the film fades, and returns one float
   array. It works on numpy arrays; a loader function supplies the WAV arrays.
3. The film preview (5.5): small H.264 files per segment, cut from the preview copies,
   joined without a new encode, with the mix as AAC. Written to the work directory.

Owner decisions: D8, a hard cut for the picture, a sound crossfade at every join, the
film fades in and out. The final film (section 5.3) is step 3 and is not built here.
"""

import math
import os
from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np

import config
import loudness


# ── Picture placement (SPECS 5.2) ───────────────────────────────

def round_even(x) -> int:
    """The nearest even integer; a tie goes to the larger one."""
    return int(2 * math.floor(x / 2.0 + 0.5))


def preview_frame(film_frame: dict, width: int) -> tuple:
    """The size of the film preview: `width` wide, the height of the film's shape."""
    w = round_even(width)
    return w, round_even(width * film_frame["height"] / film_frame["width"])


def placement(src, frame, mode: str, crop: dict = None, allow_enlarge: bool = False) -> dict:
    """Where a picture of size `src` (width, height) goes in a frame of size `frame`.

    fit  The whole picture, scaled down only if it does not fit (never enlarged unless
         `allow_enlarge`), centred; black bars fill the rest.
    fill The largest window of the frame's shape inside the picture, placed by
         `crop` (x between 0 and 1, default 0.5, or x_from and x_to for a window that
         moves over the segment), then scaled to the frame.
    """
    sw, sh = src
    fw, fh = frame
    if mode == "fit":
        scale = min(fw / sw, fh / sh)
        if not allow_enlarge:
            scale = min(scale, 1.0)
        w, h = min(round_even(sw * scale), fw), min(round_even(sh * scale), fh)
        return {"mode": "fit", "scale": scale, "w": w, "h": h,
                "x": round_even((fw - w) / 2), "y": round_even((fh - h) / 2)}
    if mode != "fill":
        raise ValueError(f"unknown placement mode {mode!r}")
    if sw / sh >= fw / fh:
        ww, wh = round_even(sh * fw / fh), sh
    else:
        ww, wh = sw, round_even(sw * fh / fw)
    crop = crop or {}
    x_from = crop.get("x_from", crop.get("x", 0.5))
    x_to = crop.get("x_to", x_from)
    wx = lambda x: round_even((sw - ww) * x)
    return {"mode": "fill", "ww": ww, "wh": wh, "wx": wx(x_from), "wy": round_even((sh - wh) / 2),
            "wx_from": wx(x_from), "wx_to": wx(x_to), "scale": fw / ww, "out": (fw, fh)}


# ── The plan (SPECS 5.1) ────────────────────────────────────────

@dataclass
class SegmentPlan:
    id: str
    source: str
    in_frame: int
    out_frame: int
    film_start_frame: int
    length_frames: int
    film_start_sample: int
    sample_length: int
    source_audio_start: int             # may be negative: the WAV has no sound before 0
    join_in_samples: int                # crossfade into this segment, full length; 0 for the first
    curve_in: str
    join_out_samples: int               # crossfade into the next segment; 0 for the last
    curve_out: str
    fade_in_samples: int                # film fade-in (first segment only)
    fade_out_samples: int               # film fade-out (last segment only)
    picture_fade_in_frames: int
    picture_fade_out_frames: int
    gain_db: float
    placement: dict
    crop: dict = None
    index: int = 0


@dataclass
class RenderPlan:
    segments: list
    film_frames: int
    film_samples: int
    sample_rate: int
    fps: Fraction
    frame: tuple                        # the picture size the placement is made for
    warnings: list = field(default_factory=list)


def _segment_placement(size: dict, film_frame: dict, frame: tuple, fit: str, crop) -> dict:
    """The placement of a segment's picture in the film preview. For `fit` it is the
    placement the final film gets in its own frame, scaled to the preview, so a picture
    that the final film does not enlarge fills the same part of the preview. For `fill`
    the window is cut from the picture and scaled to the preview frame."""
    src = (size["width"], size["height"])
    if fit == "fill":
        return placement(src, frame, "fill", crop)
    final = placement(src, (film_frame["width"], film_frame["height"]), "fit")
    f = frame[0] / film_frame["width"]
    w, h = min(round_even(final["w"] * f), frame[0]), min(round_even(final["h"] * f), frame[1])
    return {"mode": "fit", "scale": final["scale"] * f, "w": w, "h": h,
            "x": round_even((frame[0] - w) / 2), "y": round_even((frame[1] - h) / 2)}


def make_plan(tl, preview_width=config.FILM_PREVIEW_WIDTH) -> RenderPlan:
    """The render plan of a valid timeline. With `preview_width` the placement is made
    for the film preview (that many pixels wide); with None, for the film's own frame."""
    data = tl.data
    film = data["film"]
    rate = tl.sample_rate
    fps = tl.fps
    frame = preview_frame(film["frame"], preview_width) if preview_width else \
        (film["frame"]["width"], film["frame"]["height"])
    default_join = film.get("audio_join") or {"duration_s": 0, "curve": "equal_power"}
    fit = film.get("fit", "fit")
    order = tl.film_order()
    starts = tl.segment_film_starts()
    plans = []
    for k, seg in enumerate(order):
        src = data["sources"][seg["source"]]
        join = seg.get("audio_join") or default_join
        length = tl.segment_length_frames(seg)
        n = tl.segment_sample_length(seg)
        plans.append(SegmentPlan(
            id=seg["id"], source=seg["source"], in_frame=seg["in"], out_frame=seg["out"],
            film_start_frame=starts[seg["id"]], length_frames=length,
            film_start_sample=tl.film_sample(starts[seg["id"]]), sample_length=n,
            source_audio_start=tl.source_audio_start(seg),
            join_in_samples=round(join["duration_s"] * rate) if k > 0 else 0,
            curve_in=join.get("curve", "equal_power"),
            join_out_samples=0, curve_out="equal_power",
            fade_in_samples=min(n, round(film.get("fade_in_s", 0) * rate)) if k == 0 else 0,
            fade_out_samples=min(n, round(film.get("fade_out_s", 0) * rate))
            if k == len(order) - 1 else 0,
            picture_fade_in_frames=min(length, round(film.get("fade_in_s", 0) * fps))
            if k == 0 else 0,
            picture_fade_out_frames=min(length, round(film.get("fade_out_s", 0) * fps))
            if k == len(order) - 1 else 0,
            gain_db=float(seg.get("gain_db_adjust", 0.0)),
            placement=_segment_placement(src["size"], film["frame"], frame, fit, seg.get("crop")),
            crop=seg.get("crop"), index=k))
    for k in range(len(plans) - 1):
        plans[k].join_out_samples = plans[k + 1].join_in_samples
        plans[k].curve_out = plans[k + 1].curve_in
    return RenderPlan(plans, tl.film_length_frames(), tl.film_sample(tl.film_length_frames()),
                      rate, fps, frame)


# ── The sound (SPECS 5.4) ───────────────────────────────────────

@dataclass
class MixResult:
    samples: np.ndarray                 # (film samples, 2) float32
    warnings: list = field(default_factory=list)
    joins: list = field(default_factory=list)       # {"before", "after", "wanted_s", "used_s"}
    levels: dict = None                 # the loudness report (SPECS 5.8); None when the mode is off


def loudness_settings(tl) -> dict:
    """The loudness block of the timeline file with its defaults: `mode`, `target_lufs`,
    `true_peak_dbtp` (-1.0) and `max_gain_db` (12)."""
    block = dict((tl.data.get("audio") or {}).get("loudness") or {})
    block.setdefault("mode", "off")
    block.setdefault("true_peak_dbtp", -1.0)
    block.setdefault("max_gain_db", 12.0)
    return block


def _measure_key(wav_path, wav_len, s0, n, rate) -> str:
    """The name under which the loudness of a segment's own sound is kept: the WAV (path,
    size, modification time, decoded length), where the sound starts, its length, the rate."""
    try:
        st = os.stat(wav_path)
        ident = f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        ident = "-"
    return f"{wav_path}|{ident}|{wav_len}|{s0}|{n}|{rate}"


def _slice_with_silence(wav: np.ndarray, lo: int, hi: int) -> np.ndarray:
    out = np.zeros((hi - lo, 2), np.float32)
    a, b = max(lo, 0), min(hi, len(wav))
    if b > a:
        out[a - lo:b - lo] = wav[a:b]
    return out


def _real(lo: int, hi: int, length: int) -> int:
    """How many samples of [lo, hi) exist in a file of `length` samples."""
    return max(0, min(hi, length) - max(lo, 0))


def _rise(x: np.ndarray, curve: str) -> np.ndarray:
    return np.sin(0.5 * np.pi * x) if curve == "equal_power" else x


def _fall(x: np.ndarray, curve: str) -> np.ndarray:
    return np.cos(0.5 * np.pi * x) if curve == "equal_power" else 1.0 - x


def build_mix(tl, plan: RenderPlan, loader, measure=None, cache=None) -> MixResult:
    """The sound of the film: float32 stereo at the mix rate, `plan.film_samples` long.

    `loader(path)` returns a WAV as a float32 array (samples, 2) at the mix rate. Each
    WAV is loaded once, whatever the number of segments that use it. The sound of a
    segment is the WAV at `source_audio_start`, `sample_length` samples long, plus a
    handle on each side that exists only to build the crossfade of a join. At a join of
    duration d the picture cuts at film sample F; the earlier sound falls and the later
    sound rises over [F - d/2, F + d/2). A handle the WAV cannot give shortens the
    crossfade (down to a hard cut) and is reported.

    Loudness (decision D9): with `audio.loudness.mode` `segment_then_film` every segment is
    measured over its own span and gets the gain that brings it to the target, at most
    `max_gain_db`; with `film_only` this step is skipped. In both modes the finished mix then
    gets one gain so that its integrated loudness equals the target, and a limiter if the
    true peak is over the limit (loudness.level_film). `measure(samples, rate)` measures
    (ffmpeg's ebur128 by default); `cache` is a dict that keeps the loudness of a segment
    between runs. The report is `result.levels`.
    """
    rate = plan.sample_rate
    segs = plan.segments
    N = plan.film_samples
    result = MixResult(np.zeros((N, 2), np.float32))
    warn = result.warnings.append
    settings = loudness_settings(tl)
    mode = settings["mode"]
    measure = measure or (lambda x, r: loudness.measure(x, r))
    cores, keys = {}, {}

    # Pass 1: load each WAV once and cut out every segment's nominal region.
    by_path = {}
    for s in segs:
        by_path.setdefault(tl.data["sources"][s.source]["audio"], []).append(s)
    regions, avail_before, avail_after = {}, {}, {}
    for path, group in by_path.items():
        wav = loader(path)
        length = len(wav)
        for s in group:
            h_in, h_out = s.join_in_samples // 2, s.join_out_samples // 2
            s0, n = s.source_audio_start, s.sample_length
            regions[s.id] = _slice_with_silence(wav, s0 - h_in, s0 + n + h_out)
            if mode == "segment_then_film":
                cores[s.id] = regions[s.id][h_in:h_in + n]
                keys[s.id] = _measure_key(path, length, s0, n, rate)
            avail_before[s.id] = _real(s0 - h_in, s0, length)
            avail_after[s.id] = _real(s0 + n, s0 + n + h_out, length)
            lead = max(0, min(-s0, n))
            if lead:
                warn(f"segment {s.id}: the sound starts {lead / rate:.3f} s after the picture "
                     "(silence before the start of the WAV)")
            tail = max(0, min(n, s0 + n - length))
            if tail and s0 < length:
                warn(f"segment {s.id}: the sound ends {tail / rate:.3f} s before the picture "
                     "ends (silence after the end of the WAV)")
            if s0 >= length:
                warn(f"segment {s.id}: the WAV holds no sound for this segment (silence)")

    # Loudness of each segment (decision D9): one gain per segment, from its own span.
    seg_levels, seg_gain = {}, {}
    if mode != "off":
        result.levels = {"mode": mode, "target_lufs": settings["target_lufs"],
                         "true_peak_dbtp": settings["true_peak_dbtp"],
                         "max_gain_db": settings["max_gain_db"], "segments": seg_levels}
    if mode == "segment_then_film":
        lufs = _measure_segments(cores, keys, rate, measure, cache)
        for s in segs:
            level = loudness.Level(lufs[s.id], 0.0)
            g = loudness.segment_gain(level, settings["target_lufs"], settings["max_gain_db"])
            seg_gain[s.id] = g.gain_db
            row = g.as_dict()
            row["adjust_db"] = s.gain_db
            seg_levels[s.id] = row
            if g.silent:
                warn(f"segment {s.id}: no sound above the gate (-70 LUFS) or shorter than "
                     f"0.4 s, so it is not levelled (gain stays 0)")
            elif g.capped:
                warn(f"segment {s.id}: the gain is capped at {settings['max_gain_db']:g} dB "
                     f"(it would need {g.wanted_db:.1f} dB to reach {settings['target_lufs']:g} LUFS)")
        cores.clear()

    # Joins: the crossfade is as long as both sides can give.
    used_half = {}
    for k in range(1, len(segs)):
        prev, nxt = segs[k - 1], segs[k]
        wanted = nxt.join_in_samples // 2
        used = min(wanted, avail_after[prev.id], avail_before[nxt.id])
        used_half[nxt.id] = used
        result.joins.append({"before": prev.id, "after": nxt.id,
                             "wanted_s": 2 * wanted / rate, "used_s": 2 * used / rate})
        if used < wanted:
            warn(f"join before {nxt.id}: crossfade shortened from {2 * wanted / rate:.3f} s to "
                 f"{2 * used / rate:.3f} s (the sound does not reach further)")

    # Pass 2: window each region and add it to the mix.
    for k, s in enumerate(segs):
        h_in, h_out = s.join_in_samples // 2, s.join_out_samples // 2
        n = s.sample_length
        region = regions[s.id]
        g = np.ones(len(region), np.float64)
        if k == 0:
            fi = s.fade_in_samples
            if fi:
                g[:fi] = np.sin(0.5 * np.pi * (np.arange(fi) / fi))
        else:
            h = used_half[s.id]
            g[:h_in - h] = 0.0
            if h:
                g[h_in - h:h_in + h] = _rise(np.arange(2 * h) / (2 * h), s.curve_in)
        if k == len(segs) - 1:
            fo = s.fade_out_samples
            if fo:
                fall = np.cos(0.5 * np.pi * ((np.arange(fo) + 1) / fo))
                fall[-1] = 0.0          # the film ends in exact silence (cos does not give 0)
                g[h_in + n - fo:h_in + n] *= fall
        else:
            h = used_half[segs[k + 1].id]
            g[h_in + n + h:] = 0.0
            if h:
                g[h_in + n - h:h_in + n + h] *= _fall(np.arange(2 * h) / (2 * h), s.curve_out)
        total_db = s.gain_db + seg_gain.get(s.id, 0.0)
        if total_db:
            g *= 10.0 ** (total_db / 20.0)
        pos = s.film_start_sample - h_in
        lo, hi = max(0, pos), min(N, pos + len(region))
        if hi > lo:
            result.samples[lo:hi] += (region[lo - pos:hi - pos] * g[lo - pos:hi - pos, None]
                                      ).astype(np.float32)

    # The film: one gain to the target, a limiter if the true peak is over the limit.
    if mode != "off":
        out, film = loudness.level_film(result.samples, settings["target_lufs"],
                                        settings["true_peak_dbtp"], rate,
                                        measure_fn=lambda x: measure(x, rate))
        result.samples = out
        result.levels["film"] = film.as_dict()
        for w in film.warnings:
            warn(w)
        if film.gain_db > settings["max_gain_db"]:
            warn(f"the film needed a gain of {film.gain_db:.1f} dB to reach the target "
                 f"(more than max_gain_db, {settings['max_gain_db']:g} dB)")
    return result


def _measure_segments(cores: dict, keys: dict, rate: int, measure, cache) -> dict:
    """The integrated loudness of every segment's own span (None when silent): from the
    cache where the key is known, else measured, four at a time."""
    from concurrent.futures import ThreadPoolExecutor
    found, todo = {}, []
    for sid, core in cores.items():
        key = keys[sid]
        if cache is not None and key in cache:
            found[sid] = cache[key]
        else:
            todo.append(sid)
    if todo:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for sid, level in zip(todo, pool.map(lambda i: measure(cores[i], rate), todo)):
                found[sid] = level.lufs
                if cache is not None:
                    cache[keys[sid]] = level.lufs
    return found


# ── The film preview (SPECS 5.5) ────────────────────────────────

class RenderError(Exception):
    """A render cannot start or its output failed a check."""


@dataclass
class PreviewSettings:
    width: int = config.FILM_PREVIEW_WIDTH
    encoder: str = config.FILM_PREVIEW_ENCODER
    quality: int = config.FILM_PREVIEW_Q
    gop: int = config.FILM_PREVIEW_GOP
    audio_kbps: int = config.FILM_PREVIEW_AUDIO_KBPS

    def as_dict(self) -> dict:
        return {"width": self.width, "encoder": self.encoder, "quality": self.quality,
                "gop": self.gop, "audio_kbps": self.audio_kbps}


@dataclass
class PreviewResult:
    path: str
    frames: int
    seconds: float
    segments: list                      # ids of the segments in the file
    rendered: list = field(default_factory=list)
    cached: list = field(default_factory=list)
    sound_changed: list = field(default_factory=list)   # segments whose sound differs from the last preview
    warnings: list = field(default_factory=list)
    joins: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    timeline_hash: str = None
    elapsed_s: float = 0.0
    problems_fatal: bool = False
    loudness: dict = None               # the loudness report of the mix (None when the mode is off)

    def as_dict(self) -> dict:
        return {"file": os.path.basename(self.path), "frames": self.frames,
                "seconds": round(self.seconds, 3), "segments": self.segments,
                "rendered": self.rendered, "cached": self.cached,
                "sound_changed": self.sound_changed, "warnings": self.warnings,
                "joins": self.joins, "problems": self.problems, "loudness": self.loudness,
                "timeline_hash": self.timeline_hash, "elapsed_s": round(self.elapsed_s, 2)}


def _encoder_args(settings: PreviewSettings) -> list:
    if settings.encoder == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-profile:v", "high", "-g", str(settings.gop),
                "-bf", "0", "-q:v", str(settings.quality), "-tag:v", "avc1"]
    if settings.encoder == "libx264":
        return ["-c:v", "libx264", "-profile:v", "high", "-preset", "veryfast",
                "-crf", str(max(0, min(51, round((100 - settings.quality) * 0.45 - 6)))),
                "-g", str(settings.gop), "-bf", "0", "-tag:v", "avc1"]
    raise ValueError(f"Unknown preview encoder: {settings.encoder}")


def picture_rect(placement: dict, frame) -> tuple:
    """The rectangle (x0, y0, x1, y1) that a segment's picture fills in the frame: a letterboxed
    picture fills less than the frame (text stays inside it, SPECS 9.5)."""
    if placement["mode"] == "fit":
        return (placement["x"], placement["y"], placement["x"] + placement["w"], placement["y"] + placement["h"])
    return (0, 0, frame[0], frame[1])


def segment_rects(plan: RenderPlan) -> dict:
    return {s.id: picture_rect(s.placement, plan.frame) for s in plan.segments}


def segment_text(tl, plan: RenderPlan, seg: SegmentPlan, burn_text: bool = True):
    """The text that falls in the segment, as a textlayer plan with frames counted from the segment's
    first frame; None when the film has no text or `burn_text` is off. Nothing is drawn yet."""
    import textlayer
    if not burn_text or not textlayer.has_text(tl.data):
        return None
    text = textlayer.build(tl.data, plan.frame, seg.film_start_frame, seg.film_start_frame + seg.length_frames,
                           segment_rects(plan))
    return None if text.empty else text


def segment_filter(tl, plan: RenderPlan, seg: SegmentPlan, copy_size=None, text=None) -> str:
    """The ffmpeg filter chain that turns frames of the preview copy into the segment's
    picture in the film preview: new time stamps, the creative LUT if there is one, the
    placement (SPECS 5.2), the text (cues and titles, SPECS 9.6) if there is any, the film
    fades, 4:2:0. With `text` (a textlayer plan that has been written) the chain is a filter
    graph that ends in the label [vout]; without it a plain chain."""
    import convert
    fps = tl.data["film"]["fps"]
    chain = [f"setpts=N/({fps['num']}/{fps['den']})/TB"]
    lut = tl.data["look"].get("lut")
    if lut:
        chain.append(f"lut3d=file={convert.escape_filter_path(lut['path'])}:interp=tetrahedral")
    p = seg.placement
    W, H = plan.frame
    if p["mode"] == "fit":
        if copy_size != (p["w"], p["h"]):
            chain.append(f"scale={p['w']}:{p['h']}:flags=bicubic")
        if (p["w"], p["h"]) != (W, H):
            chain.append(f"pad={W}:{H}:{p['x']}:{p['y']}:color=black")
    else:
        src = tl.data["sources"][seg.source]["size"]
        k = (copy_size[0] / src["width"]) if copy_size else 1.0
        ww, wh = round_even(p["ww"] * k), round_even(p["wh"] * k)
        wy = round_even(p["wy"] * k)
        if p["wx_from"] == p["wx_to"]:
            wx = str(round_even(p["wx_from"] * k))
        else:
            span = max(1, seg.length_frames - 1)
            wx = (f"{round_even(p['wx_from'] * k)}+({round_even(p['wx_to'] * k) - round_even(p['wx_from'] * k)})"
                  f"*n/{span}")
        chain.append(f"crop={ww}:{wh}:'{wx}':{wy}")
        chain.append(f"scale={W}:{H}:flags=bicubic")
    tail = []
    if seg.picture_fade_in_frames:
        tail.append(f"fade=t=in:s=0:n={seg.picture_fade_in_frames}")
    if seg.picture_fade_out_frames:
        tail.append(f"fade=t=out:s={seg.length_frames - seg.picture_fade_out_frames}:"
                    f"n={seg.picture_fade_out_frames}")
    tail.append("format=yuv420p")
    if text is None:
        return ",".join(chain + tail)
    graph, label = text.graph("pic", 1, "yuv420p")
    return f"[0:v]{','.join(chain)}[pic];{graph};[{label}]{','.join(tail)}[vout]"


def segment_command(ffmpeg, tl, plan, seg, out_path, settings, copy_size=None, text=None) -> list:
    fps = tl.data["film"]["fps"]
    src = tl.data["sources"][seg.source]
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if seg.in_frame > 0:
        # Seek half a frame before the first frame wanted, so that the frame at `in` is
        # kept and the frame before it is dropped, whatever rounding the time gets.
        cmd += ["-ss", f"{(seg.in_frame - 0.5) * fps['den'] / fps['num']:.6f}"]
    cmd += ["-i", src["preview_copy"]]
    if text is not None:
        cmd += text.inputs()
    cmd += ["-an", "-sn", "-frames:v", str(seg.length_frames)]
    if text is None:
        cmd += ["-vf", segment_filter(tl, plan, seg, copy_size)]
    else:
        cmd += ["-filter_complex", segment_filter(tl, plan, seg, copy_size, text), "-map", "[vout]"]
    cmd += ["-fps_mode", "cfr", "-r", f"{fps['num']}/{fps['den']}"]
    cmd += _encoder_args(settings)
    cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-color_range", "tv", "-f", "mp4", str(out_path)]
    return cmd


def _copy_size(path):
    import stable
    return stable.probe_size(path)


def render_segment(tl, plan: RenderPlan, seg: SegmentPlan, out_path: str,
                   settings: PreviewSettings, ffmpeg: str = None, burn_text: bool = True) -> None:
    """Cut one segment from its preview copy and write it as a small H.264 file with
    exactly `seg.length_frames` frames, with the cues and titles that fall in it drawn over the
    picture. The file is written as .partial and renamed."""
    import shutil
    import subprocess
    import tempfile
    import textlayout
    import tools
    ffmpeg = ffmpeg or tools.ffmpeg_path()
    src = tl.data["sources"][seg.source]
    copy_size = _copy_size(src["preview_copy"])
    partial = out_path + ".partial"
    text = segment_text(tl, plan, seg, burn_text)
    scratch = tempfile.mkdtemp(prefix="text-", dir=os.path.dirname(out_path))
    try:
        if text is not None:
            try:
                text.write(scratch)
            except textlayout.FontMissing as e:
                raise RenderError(f"The film has text, and the font is missing. {e}")
        cmd = segment_command(ffmpeg, tl, plan, seg, partial, settings, copy_size, text)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            _remove(partial)
            raise RenderError(f"ffmpeg failed on segment {seg.id}: {r.stderr.strip()[-600:]}")
        os.replace(partial, out_path)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _remove(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def segment_key(tl, plan: RenderPlan, seg: SegmentPlan, settings: PreviewSettings,
                burn_text: bool = True) -> str:
    """A name for the segment file that changes when anything that shapes the picture
    changes: the preview copy (path, size, modification time), in and out, the placement,
    the preview frame, the creative LUT, the film fades that touch the segment, the text that falls
    in the segment, the settings."""
    import hashlib
    import json
    src = tl.data["sources"][seg.source]
    st = os.stat(src["preview_copy"])
    lut = tl.data["look"].get("lut")
    material = {
        "v": 1, "copy": src["preview_copy"], "size": st.st_size, "mtime": st.st_mtime_ns,
        "in": seg.in_frame, "out": seg.out_frame, "placement": seg.placement,
        "frame": list(plan.frame), "lut": lut["sha256"] if lut else None,
        "fade_in": seg.picture_fade_in_frames, "fade_out": seg.picture_fade_out_frames,
        "fps": tl.data["film"]["fps"], "settings": settings.as_dict()}
    text = segment_text(tl, plan, seg, burn_text)
    if text is not None:
        material["text"] = text.signature        # only when there is text: films without text keep their keys
    digest = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:20]
    return f"seg-{digest}.mp4"


def sound_keys(tl, plan: RenderPlan) -> dict:
    """A short key per segment that changes when anything that shapes the segment's own
    sound changes: the WAV, where in it the sound starts (so the clip's offset, the hand
    offset and the drift), its length, the handles and curves of both joins, the film
    fades and the gain. It does not depend on where the segment stands in the film: a
    move changes the keys of the neighbours' joins, not the sound of the segment."""
    import hashlib
    import json
    keys = {}
    for s in plan.segments:
        material = {
            "v": 1, "wav": tl.data["sources"][s.source]["audio"], "rate": plan.sample_rate,
            "start": s.source_audio_start, "samples": s.sample_length,
            "join_in": [s.join_in_samples, s.curve_in], "join_out": [s.join_out_samples, s.curve_out],
            "fade_in": s.fade_in_samples, "fade_out": s.fade_out_samples, "gain_db": s.gain_db,
            "loudness": loudness_settings(tl)}
        keys[s.id] = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:20]
    return keys


def _read_record(work_dir: str, name: str) -> dict:
    import json
    try:
        with open(os.path.join(work_dir, "previews", f"{name}.json"), encoding="utf-8") as f:
            record = json.load(f)
        return record if isinstance(record, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_cache(path: str) -> dict:
    import json
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(path: str, cache: dict) -> None:
    import fsutil
    fsutil.write_json_atomic(path, cache)


def pending_work(tl, work_dir: str, settings: PreviewSettings = None, name: str = "preview",
                 burn_text: bool = True) -> dict:
    """What the next film preview has to do for the film as it is now: the segments whose
    picture file does not exist yet (`picture`) and the segments whose sound differs from
    the sound of the last preview (`sound`). A changed offset changes the sound only. Reads
    no media and starts no program."""
    settings = settings or PreviewSettings()
    plan = make_plan(tl, preview_width=settings.width)
    old = _read_record(work_dir, name).get("sound_keys") or {}
    new = sound_keys(tl, plan)
    folder = os.path.join(work_dir, "previews")
    picture = []
    for s in plan.segments:
        try:
            target = os.path.join(folder, segment_key(tl, plan, s, settings, burn_text))
        except OSError:                     # the preview copy is missing: nothing can be reused
            picture.append(s.id)
            continue
        if not (os.path.isfile(target) and os.path.getsize(target) > 0):
            picture.append(s.id)
    return {"picture": picture, "sound": [s.id for s in plan.segments if new[s.id] != old.get(s.id)]}


def load_wav(path: str, rate: int) -> np.ndarray:
    """Decode a WAV with ffmpeg to float32 stereo at `rate`: (samples, 2)."""
    import subprocess
    import tools
    cmd = [tools.ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-vn",
           "-f", "f32le", "-ac", "2", "-ar", str(rate), "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RenderError(f"ffmpeg could not read {path}: {r.stderr.decode('utf-8', 'replace').strip()}")
    return np.frombuffer(r.stdout, np.float32).reshape(-1, 2)


def _frame_count(path: str) -> int:
    import subprocess
    import tools
    r = subprocess.run([tools.ffprobe_path(), "-v", "error", "-count_packets", "-select_streams",
                        "v:0", "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise RenderError(f"ffprobe cannot read {path}: {r.stderr.strip()}")
    return int(r.stdout.strip().split(",")[0])


def _stream_durations(path: str) -> dict:
    import json
    import subprocess
    import tools
    r = subprocess.run([tools.ffprobe_path(), "-v", "error", "-show_entries",
                        "stream=codec_type,duration:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RenderError(f"ffprobe cannot read {path}: {r.stderr.strip()}")
    info = json.loads(r.stdout)
    out = {}
    for s in info.get("streams", []):
        d = s.get("duration")
        out.setdefault(s["codec_type"], float(d) if d not in (None, "N/A") else
                       float(info["format"]["duration"]))
    return out


def verify_preview(path: str, frames: int, fps: Fraction) -> list:
    """The checks of SPECS 5.7 on a finished file. Returns a list of problems (empty
    when all pass): as many video frames as the film has, a sound track, the sound as
    long as the picture within one frame, a file that is not empty."""
    problems = []
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return [f"{path} is empty or missing"]
    n = _frame_count(path)
    if n != frames:
        problems.append(f"the file has {n} video frames, the film has {frames}")
    d = _stream_durations(path)
    one_frame = float(1 / fps)
    if "audio" not in d:
        problems.append("the file has no sound")
    elif abs(d["audio"] - d.get("video", d["audio"])) >= one_frame:
        problems.append(f"the sound is {d['audio']:.3f} s long and the picture {d['video']:.3f} s: "
                        "they differ by one frame or more")
    return problems


def render_preview(tl, work_dir: str, name: str = "preview", settings: PreviewSettings = None,
                   film_range=None, loader=None, timeline_hash: str = None, protected=None,
                   log=print, progress=None, burn_text: bool = True) -> PreviewResult:
    """Render the film preview of `tl` to <work_dir>/previews/<name>.mp4.

    Picture: one small file per segment in previews/seg-<key>.mp4, cut from the preview
    copy and kept between runs (an unchanged segment is not rendered again), joined
    without a new encode. Sound: the whole mix of build_mix, encoded as AAC. A second file,
    previews/<name>.json, records the timeline hash and the segments, so the page can tell
    whether the preview is up to date. `film_range` (from, to) in film seconds renders
    only the segments that touch it. The cues and titles that fall in a segment are drawn into its
    picture (SPECS 9.6) unless `burn_text` is off; moving one cue renders again only the scenes it touches. `protected` lists folders that hold originals; the
    work directory must not lie inside one.
    """
    import datetime
    import subprocess
    import time
    import fsutil
    import safety
    import tools

    started = time.time()
    settings = settings or PreviewSettings()
    if protected:
        safety.assert_not_inside(work_dir, protected)
    findings = tl.validate(file_checks=False)
    errors = [f for f in findings if f.level == "error"]
    if errors:
        raise RenderError("The timeline is not valid: " + "; ".join(
            f"{f.rule} at {f.path}: {f.message}" for f in errors))
    for sid, src in tl.data["sources"].items():
        if not os.path.isfile(src["preview_copy"]):
            raise RenderError(f"The preview copy of clip {sid} is missing "
                              f"({src['preview_copy']}). Run convert.")
    plan = make_plan(tl, preview_width=settings.width)
    fps = plan.fps
    segs = plan.segments
    if film_range is not None:
        a, b = film_range
        segs = [s for s in segs
                if float(s.film_start_frame / fps) < b
                and float((s.film_start_frame + s.length_frames) / fps) > a]
        if not segs:
            raise RenderError(f"No segment lies within {a:g} to {b:g} s of the film.")
    ffmpeg = tools.ffmpeg_path()
    folder = os.path.join(work_dir, "previews")
    os.makedirs(folder, exist_ok=True)
    result = PreviewResult(path=os.path.join(folder, f"{name}.mp4"), frames=0, seconds=0.0,
                           segments=[s.id for s in segs], timeline_hash=timeline_hash)

    # Picture: one file per segment, from the cache where it exists.
    files = []
    for i, s in enumerate(segs):
        target = os.path.join(folder, segment_key(tl, plan, s, settings, burn_text))
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            result.cached.append(s.id)
        else:
            log(f"[{i + 1}/{len(segs)}] rendering segment {s.id} ({s.length_frames} frames)")
            render_segment(tl, plan, s, target, settings, ffmpeg, burn_text)
            result.rendered.append(s.id)
        files.append(target)
        if progress:
            progress(i + 1, len(segs))

    # Sound: the whole mix, then the part that belongs to the segments in the file.
    all_keys = sound_keys(tl, plan)
    keys = {s.id: all_keys[s.id] for s in segs}
    before = _read_record(work_dir, name).get("sound_keys") or {}
    result.sound_changed = [s.id for s in segs if keys[s.id] != before.get(s.id)]
    loader = loader or (lambda path: load_wav(path, plan.sample_rate))
    cache_path = os.path.join(folder, "loudness-cache.json")
    cache = _read_cache(cache_path)
    mix = build_mix(tl, plan, loader, cache=cache)
    _write_cache(cache_path, cache)
    result.warnings, result.joins, result.loudness = mix.warnings, mix.joins, mix.levels
    first, last = segs[0], segs[-1]
    lo = first.film_start_sample
    hi = last.film_start_sample + last.sample_length
    audio = np.ascontiguousarray(mix.samples[lo:hi])
    result.frames = sum(s.length_frames for s in segs)
    result.seconds = float(result.frames / fps)

    list_file = os.path.join(folder, f"{name}.concat.txt")
    mix_file = os.path.join(folder, f"{name}.mix.f32le")
    partial = result.path + ".partial"
    try:
        with open(list_file, "w", encoding="utf-8") as f:
            for p in files:
                f.write("file '" + p.replace("'", "'\\''") + "'\n")
        audio.tofile(mix_file)
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-f", "concat", "-safe", "0", "-i", list_file,
               "-f", "f32le", "-ar", str(plan.sample_rate), "-ac", "2", "-i", mix_file,
               "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
               "-c:a", "aac", "-b:a", f"{settings.audio_kbps}k",
               "-movflags", "+faststart", "-f", "mp4", partial]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RenderError(f"ffmpeg failed to join the preview: {r.stderr.strip()[-600:]}")
        result.problems = verify_preview(partial, result.frames, fps)
        if result.problems:
            raise RenderError("The preview failed its checks: " + "; ".join(result.problems))
        os.replace(partial, result.path)
    finally:
        for p in (list_file, mix_file, partial):
            _remove(p)

    result.elapsed_s = time.time() - started
    fsutil.write_json_atomic(os.path.join(folder, f"{name}.json"), {
        **result.as_dict(), "range": list(film_range) if film_range else None,
        "settings": settings.as_dict(), "sound_keys": keys,
        "rendered_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return result
