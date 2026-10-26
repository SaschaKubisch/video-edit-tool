"""Stills of the film at given times (SPECS 4.3 step 5, 10.4: `timeline frames`).

Claude Code looks at a still to see what the film shows at a film time, or what a clip shows
at a source time. A film time is turned into the segment that plays then and the frame of the
clip (the frame nearest to the time); the still is cut from the clip's preview copy (so it
carries the base LUT) and, for a film time, is placed in the frame of the film preview with
the creative LUT, if there is one, and the letterbox bars. The film fades are left out, so a still at the first second is not
black. PNG files go to <work_dir>/stills/.
"""

import dataclasses
import math
import os
import subprocess
from fractions import Fraction

import render
import tools


class FrameError(Exception):
    """A time cannot be turned into a frame. The message says why."""


def parse_times(text: str) -> list:
    """'12.0,48.5' -> [12.0, 48.5]. Raises ValueError for anything that is not numbers."""
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if not parts:
        raise ValueError("no time given")
    return [float(p) for p in parts]


def _nearest_frame(t: float, fps: Fraction) -> int:
    """The frame nearest to time `t`. A time typed with a few decimals ("1.6683" for frame 50
    at 29.97 fps) must give that frame, not the one before it, so the time is read as an
    exact decimal and rounded to the nearest frame."""
    return math.floor(Fraction(t).limit_denominator(10 ** 6) * fps + Fraction(1, 2))


def locate_film_time(tl, t: float):
    """The segment that plays at film time `t` and the frames: (segment, film frame,
    frame of the clip). Removed segments have no film time."""
    if t < 0:
        raise FrameError(f"the film time {t:g} s is negative")
    fps = tl.fps
    frame = _nearest_frame(t, fps)
    total = tl.film_length_frames()
    if frame >= total:
        raise FrameError(f"the film time {t:g} s is beyond the end of the film "
                         f"({float(total / fps):.2f} s)")
    starts = tl.segment_film_starts()
    for seg in tl.film_order():
        start = starts[seg["id"]]
        if start <= frame < start + tl.segment_length_frames(seg):
            return seg, frame, seg["in"] + (frame - start)
    raise FrameError(f"no segment plays at the film time {t:g} s")    # cannot happen in a valid file


def locate_source_time(tl, source: str, t: float) -> int:
    """The frame of clip `source` at source time `t`."""
    sources = tl.data["sources"]
    if source not in sources:
        raise FrameError(f"there is no clip {source!r} in the timeline file "
                         f"(clips: {', '.join(sources)})")
    if t < 0:
        raise FrameError(f"the source time {t:g} s is negative")
    frame = _nearest_frame(t, tl.fps)
    frames = sources[source]["frames"]
    if frame >= frames:
        raise FrameError(f"the source time {t:g} s is beyond the end of the clip "
                         f"({float(frames / tl.fps):.2f} s, {frames} frames)")
    return frame


_TO_RGB = "scale=in_range=tv:out_range=pc:in_color_matrix=bt709:flags=accurate_rnd+full_chroma_int"


def _extract(tl, copy: str, frame: int, chain: str, out_path: str) -> None:
    fps = tl.data["film"]["fps"]
    cmd = [tools.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if frame > 0:
        # half a frame before the frame wanted, so that frame is the first one read
        cmd += ["-ss", f"{(frame - 0.5) * fps['den'] / fps['num']:.6f}"]
    tmp = os.path.join(os.path.dirname(out_path), "." + os.path.basename(out_path) + ".tmp.png")
    cmd += ["-i", copy, "-an", "-sn", "-frames:v", "1", "-vf", f"{chain},{_TO_RGB},format=rgb24", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(tmp):
        if os.path.isfile(tmp):
            os.unlink(tmp)
        raise FrameError(f"ffmpeg could not cut a still from {copy}: {r.stderr.strip()[-400:]}")
    os.replace(tmp, out_path)


def film_stills(tl, times, work_dir: str, settings=None) -> list:
    """One PNG per film time, in the order given. Returns the paths."""
    settings = settings or render.PreviewSettings()
    plan = render.make_plan(tl, preview_width=settings.width)
    by_id = {s.id: s for s in plan.segments}
    folder = os.path.join(work_dir, "stills")
    os.makedirs(folder, exist_ok=True)
    located = [(t, *locate_film_time(tl, t)) for t in times]       # every time is checked first
    paths = []
    for t, seg, film_frame, source_frame in located:
        sp = dataclasses.replace(by_id[seg["id"]], picture_fade_in_frames=0,
                                 picture_fade_out_frames=0)
        copy = tl.data["sources"][seg["source"]]["preview_copy"]
        chain = render.segment_filter(tl, plan, sp, render._copy_size(copy))
        out = os.path.join(folder, f"film-{t:.3f}s.png")
        _extract(tl, copy, source_frame, chain, out)
        paths.append(out)
    return paths


def source_stills(tl, source: str, times, work_dir: str) -> list:
    """One PNG per source time: the frame of the clip's preview copy as it is."""
    frames = [locate_source_time(tl, source, t) for t in times]
    folder = os.path.join(work_dir, "stills")
    os.makedirs(folder, exist_ok=True)
    copy = tl.data["sources"][source]["preview_copy"]
    paths = []
    for t, frame in zip(times, frames):
        out = os.path.join(folder, f"{source}-{t:.3f}s.png")
        _extract(tl, copy, frame, "setpts=PTS", out)
        paths.append(out)
    return paths
