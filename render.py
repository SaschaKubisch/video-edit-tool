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


def build_mix(tl, plan: RenderPlan, loader) -> MixResult:
    """The sound of the film: float32 stereo at the mix rate, `plan.film_samples` long.

    `loader(path)` returns a WAV as a float32 array (samples, 2) at the mix rate. Each
    WAV is loaded once, whatever the number of segments that use it. The sound of a
    segment is the WAV at `source_audio_start`, `sample_length` samples long, plus a
    handle on each side that exists only to build the crossfade of a join. At a join of
    duration d the picture cuts at film sample F; the earlier sound falls and the later
    sound rises over [F - d/2, F + d/2). A handle the WAV cannot give shortens the
    crossfade (down to a hard cut) and is reported.
    """
    rate = plan.sample_rate
    segs = plan.segments
    N = plan.film_samples
    result = MixResult(np.zeros((N, 2), np.float32))
    warn = result.warnings.append

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
        if s.gain_db:
            g *= 10.0 ** (s.gain_db / 20.0)
        pos = s.film_start_sample - h_in
        lo, hi = max(0, pos), min(N, pos + len(region))
        if hi > lo:
            result.samples[lo:hi] += (region[lo - pos:hi - pos] * g[lo - pos:hi - pos, None]
                                      ).astype(np.float32)
    return result
