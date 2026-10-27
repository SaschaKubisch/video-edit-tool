"""
Vertical shorts (step 6, SPECS section 11; requirements S1 to S5; decisions D15 and D16).

A short is a 9:16 video cut from the film. This module turns a film (the timeline data) and a length into a
second timeline file of kind `short`. It reads no media and writes no file: `plan_short` is a function of its
arguments, so the same film and settings give the same plan every time. The caller (`main.cmd_short`) saves
the plan next to the output file and renders it with `export.render_export`, the same renderer as the film.

What `plan_short` does, in order (all lengths in frames of the film; the film's frame rate is kept):

1. **The scenes it may use.** The enabled scenes of the film, without `priority: drop`, without `exclude`, and
   with `only` if that is given.
2. **How many.** About one scene per 4 s of the short (`config.SHORT_AIM_SCENE_SEC`), at most as many as fit at
   their floors.
3. **Which.** The scenes are cut, in film order, into that many parts of equal film length (a scene belongs to the
   part that holds its middle). One scene is kept from each part, the most important one: pinned
   (`priority: must`) or named (`prefer`), then a scene with scene text or subtitles, then the rest; among equals the
   longer, then the earlier. A pinned or named scene is always kept, even if a part holds two.
4. **How much time.** Each kept scene gets time in proportion to its length in the film, at least 2.0 s (the last
   scene at least what its fade-out and its half of the join need, 2.25 s), at most 8 s. The shares add up to the
   length of the short to the frame (largest remainder).
5. **Where in the scene.** The window of that many frames with the best score: it holds whole subtitles, does not
   cut through one, and keeps a quarter second away from the speech it does not hold; without subtitles it is the
   middle of the scene.
6. **The rest.** The crop of the scene (centred unless the scene sets it; a pan is cut to the window and keeps its
   speed), the subtitles and titles that stay (a cue that is cut stays only when at least max(1.0 s, 0.8 of its
   length) of it stays), the subtitles laid out again for the 9:16 frame, the export entry.

The film is not changed.
"""

import copy
import difflib
import hashlib
import math
import os
from dataclasses import dataclass, field
from fractions import Fraction

import config
import timeline
import words


class ShortError(Exception):
    """A short cannot be planned. The message says why and what to do."""


# ── Small helpers ───────────────────────────────────────────────

def _fps(film: dict) -> Fraction:
    f = film["film"]["fps"]
    return Fraction(f["num"], f["den"])


def frames_of(seconds: float, fps) -> int:
    """`seconds` as a whole number of frames (the nearest)."""
    return int(math.floor(Fraction(seconds).limit_denominator(10 ** 6) * Fraction(fps) + Fraction(1, 2)))


def _seconds(n: int, fps) -> float:
    return float(Fraction(n) / Fraction(fps))


def _snap(seconds: float, fps) -> float:
    """Seconds moved to the nearest frame, four decimals (the form the timeline file keeps)."""
    return round(float(Fraction(frames_of(seconds, fps)) / Fraction(fps)), 4)


def _clock(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60)
    return f"{int(m)}:{s:04.1f}"


# ── Floors: what V11 asks of a scene at its place ───────────────

def position_floors(fade_in_s: float, fade_out_s: float, join_s: float, fps) -> dict:
    """The shortest a kept scene may be, in frames, by its place in the short. Rule V11: a scene must last at
    least half the join before it plus half the join after it, and the film fades count in full. So an inner scene
    needs one join, the first scene the fade-in and half a join, the last scene half a join and the fade-out, a
    scene alone the two fades. `config.SHORT_MIN_SCENE_SEC` is the floor of every scene."""
    fps = Fraction(fps)

    def fr(sec):
        return max(1, int(math.ceil(float(sec) * fps - 1e-9)))

    base = fr(config.SHORT_MIN_SCENE_SEC)
    half = join_s / 2.0
    return {"inner_min": fr(join_s), "inner": base,
            "first": max(base, fr(fade_in_s + half)),
            "last": max(base, fr(half + fade_out_s)),
            "only": max(base, fr(fade_in_s + fade_out_s))}


# ── Time for each kept scene ────────────────────────────────────

def allocate(lengths, floors, caps, total) -> list:
    """Frames for each kept scene. Scene i gets `min(caps[i], max(floors[i], r * lengths[i]))` for the one number r
    for which the shares add up to `total`, found by bisection, then rounded to whole frames by the rule of the
    largest remainder, so the sum is exactly `total` and no share leaves its floor or cap. Raises ShortError when the
    floors together are longer than `total` or the caps together are shorter."""
    n = len(lengths)
    if sum(floors) > total:
        raise ShortError(f"the {n} scenes need at least {sum(floors)} frames at their shortest and the short has "
                         f"{total} frames")
    if sum(caps) < total:
        raise ShortError(f"the {n} scenes can give at most {sum(caps)} frames at their longest and the short has "
                         f"{total} frames")

    def shares(r):
        return [min(c, max(f, r * length)) for length, f, c in zip(lengths, floors, caps)]

    lo, hi = 0.0, max(c / length for length, c in zip(lengths, caps)) + 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if sum(shares(mid)) < total:
            lo = mid
        else:
            hi = mid
    real = shares(lo)
    base = [min(c, max(f, int(math.floor(a + 1e-7)))) for a, f, c in zip(real, floors, caps)]
    left = total - sum(base)
    order = sorted(range(n), key=lambda i: (-(real[i] - base[i]), i))
    while left > 0:
        moved = False
        for i in order:
            if left > 0 and base[i] < caps[i]:
                base[i] += 1
                left -= 1
                moved = True
        if not moved:
            break
    while left < 0:                                   # cannot happen (the real shares add up to at most total)
        i = max(range(n), key=lambda k: (base[k] - floors[k], k))
        base[i] -= 1
        left += 1
    return base


# ── The window inside a scene ───────────────────────────────────

def keep_cue(c0: float, c1: float, w0: float, w1: float):
    """What stays of a cue (times in seconds, in the time of its scene) inside the window [w0, w1): None when it
    is dropped, else (start, end). A cue inside the window stays whole. A cue that the window cuts stays, cut to the
    window, only when at least max(1.0 s, 0.8 of its length) of it is left; a cue shorter than 1.0 s stays whole or
    not at all."""
    a, b = max(c0, w0), min(c1, w1)
    inside = b - a
    if inside <= 1e-9:
        return None
    length = c1 - c0
    if inside >= length - 1e-6:
        return (c0, c1)
    need = max(config.SHORT_CUE_KEEP_MIN_SEC, config.SHORT_CUE_KEEP_FRACTION * length)
    if need > length + 1e-9 or inside < need - 1e-6:
        return None
    return (a, b)


def _window_score(start: int, window: int, cues, fps) -> float:
    w0, w1 = float(Fraction(start) / fps), float(Fraction(start + window) / fps)
    score = 0.0
    for c0, c1 in cues:
        inside = min(c1, w1) - max(c0, w0)
        length = c1 - c0
        if inside <= 1e-9:
            if 0 <= c0 - w1 < config.SHORT_EDGE_SEC or 0 <= w0 - c1 < config.SHORT_EDGE_SEC:
                score -= config.SHORT_EDGE_PENALTY_SEC           # a join would take in the start or end of speech
            continue
        if inside >= length - 1e-6:
            score += length
        elif keep_cue(c0, c1, w0, w1) is not None:
            score += inside - config.SHORT_CUT_PENALTY_SEC
        else:
            score -= config.SHORT_CUT_PENALTY_SEC
    return round(score, 9)


def shown_seconds(length: int, window: int, cues, fps) -> float:
    """How many seconds of subtitles the best window of `window` frames in a scene of `length` frames shows: whole
    cues, and the part of a cut cue that stays. A scene whose cues are all longer than the window shows none."""
    if not cues:
        return 0.0
    fps = Fraction(fps)
    window = min(window, length)
    s = best_window(length, window, cues, fps)
    w0, w1 = float(Fraction(s) / fps), float(Fraction(s + window) / fps)
    total = 0.0
    for c0, c1 in cues:
        kept = keep_cue(c0, c1, w0, w1)
        if kept is not None:
            total += kept[1] - kept[0]
    return total


def best_window(length: int, window: int, cues, fps) -> int:
    """The first frame of the window of `window` frames inside a scene of `length` frames with the best score.
    `cues` are (start, end) in seconds from the start of the scene. The score is the speech the window holds
    whole, less a penalty for each cue it cuts and for each cue it comes within a quarter second of; ties go to
    the window nearest the middle of the scene, then the earlier one. Without cues that is the middle."""
    room = length - window
    if room <= 0:
        return 0
    fps = Fraction(fps)
    mid = room / 2.0
    best_key, best_start = None, 0
    for s in range(room + 1):
        key = (_window_score(s, window, cues, fps), -abs(s - mid), -s)
        if best_key is None or key > best_key:
            best_key, best_start = key, s
    return best_start


# ── Subtitles laid out again for 9:16 ───────────────────────────

def lay_cue(text: str, duration: float, rules, fps):
    """The text of one cue as cues that fit `rules` (two lines of the width of the 9:16 frame). Returns
    (pieces, report): pieces are (start, end, text) in seconds from the start of the cue; a text that fits is one
    piece over the whole time. A text that does not fit is split at the ends of sentences, after commas and before
    conjunctions (words.split_cues), and the time is shared by characters (words.spread); the report of the
    spreading says `overflow` when the pieces need more time than the cue has."""
    flat = " ".join(str(text).split())
    wrapped = words.wrap(flat, rules)
    if wrapped is not None:
        return [(0.0, duration, "\n".join(wrapped))], {"overflow": False}
    pieces, _ = words.split_cues(flat, rules)
    spans, report = words.spread(pieces, duration, fps)
    return [(a, b, p) for p, (a, b) in zip(pieces, spans)], report


# ── The plan ────────────────────────────────────────────────────

@dataclass
class Cand:
    """A scene of the film that the short may use."""
    row: dict                  # words.film_segments row
    pos: int                   # position in the pool (film order)
    frames: int
    tier: int                  # 3 pinned or named, 2 scene text or subtitles, 1 the rest
    cue_s: float               # seconds of subtitles in the scene
    cues: list                 # (start, end) in seconds from the start of the scene
    shown_s: float = 0.0       # seconds of subtitles that a window of about the aim length shows

    @property
    def id(self) -> str:
        return self.row["seg"]["id"]

    @property
    def key(self) -> tuple:
        """Sort key, the highest wins: importance, then the subtitles a window can show, then the seconds of subtitles
        in the scene, then the longer scene, then the earlier one."""
        return (self.tier, round(self.shown_s, 6), round(self.cue_s, 6), self.frames, -self.pos)


@dataclass
class ShortPlan:
    data: dict                                   # the timeline data of the short
    plan_path: str = ""                          # where the plan is saved: next to the output, same name, .json
    target_frames: int = 0
    frames: int = 0
    fps: Fraction = Fraction(30000, 1001)
    scenes: list = field(default_factory=list)   # one dict per kept scene: ids, film and short times, crop, why, cues
    cues_kept: list = field(default_factory=list)
    cues_dropped: list = field(default_factory=list)       # {"id", "scene", "text", "why"}
    titles_kept: list = field(default_factory=list)
    titles_dropped: list = field(default_factory=list)     # ids of the film's titles
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    film_frames: int = 0
    pool_scenes: int = 0
    whole_film: bool = False


def _check_ids(film: dict, names, what: str, enabled_only: bool = False) -> list:
    known = {s["id"]: s for s in film["segments"]}
    out = []
    for n in names or []:
        if n not in known:
            close = difflib.get_close_matches(n, list(known), n=1, cutoff=0.7)
            hint = f" Did you mean {close[0]}?" if close else ""
            raise ShortError(f"{what}: there is no scene {n}.{hint}")
        if enabled_only and not known[n].get("enabled", True):
            raise ShortError(f"{what}: scene {n} is removed from the film. Put it back first: "
                             f"python main.py scenes restore {n}")
        if n not in out:
            out.append(n)
    return out


def _group_of(pool, total_frames, parts):
    """The part (0 to parts - 1) of each scene of the pool: the film cut into equal lengths, a scene in the part
    that holds its middle."""
    out, at = [], 0
    for c in pool:
        mid = at + c.frames / 2.0
        out.append(min(parts - 1, int(mid * parts / total_frames)))
        at += c.frames
    return out


def _order(kept) -> list:
    return sorted(kept, key=lambda c: c.pos)


def _place(n: int, i: int) -> str:
    return "only" if n == 1 else "first" if i == 0 else "last" if i == n - 1 else "inner"


def _floors_for(kept, floors: dict) -> list:
    """The floor of each kept scene (in film order) by its place. A scene in the middle that is shorter than the
    usual floor keeps its whole length."""
    n = len(kept)
    out = []
    for i, c in enumerate(kept):
        place = _place(n, i)
        out.append(min(floors["inner"], c.frames) if place == "inner" else floors[place])
    return out


def _select(pool, parts, floors, named_ids):
    """One scene from each of `parts` equal parts of the film, and every pinned or named scene. Returns
    (kept scenes in film order, reason by scene id)."""
    total = sum(c.frames for c in pool)
    groups = {}
    for c, g in zip(pool, _group_of(pool, total, parts)):
        groups.setdefault(g, []).append(c)
    order = sorted(groups)
    kept, why = {}, {}
    for c in pool:
        if c.tier == 3:
            kept[c.id] = c
            why[c.id] = "named in the request" if c.id in named_ids else "pinned"
    for rank, g in enumerate(order):
        members = groups[g]
        if any(c.tier == 3 for c in members):
            continue
        place = _place(len(order), rank)
        need = floors[place] if place != "inner" else floors["inner_min"]
        options = [c for c in members if c.frames >= need]
        if not options:
            continue
        best = max(options, key=lambda c: c.key)
        kept[best.id] = best
        label = f"part {rank + 1} of {len(order)}"
        why[best.id] = (f"{label}: has scene text or subtitles" if best.tier == 2 and len(options) > 1
                        else f"{label}: the longest" if len(options) > 1 else f"{label}: the only scene")
        if best.tier == 2 and best.shown_s <= 0 and best.cues:
            why[best.id] += " (a window cannot show them)"
    return _order(list(kept.values())), why


def _fit_floors(kept, floors, total, notes, fps):
    """Drop the least important scenes until the floors of the rest fit `total` frames and every scene is as long as
    the floor of its place asks. A pinned or named scene is never dropped: if they alone do not fit, ShortError."""
    kept = list(kept)
    while True:
        fl = _floors_for(kept, floors)
        too_short = [(c, f) for c, f in zip(kept, fl) if c.frames < f]
        over = sum(fl) > total
        if not too_short and not over:
            return kept
        if too_short:
            movable = [(c, f) for c, f in too_short if c.tier < 3]
            if movable:
                c = movable[0][0]
                notes.append(f"{c.id} is too short for its place in the short and was left out")
                kept.remove(c)
                continue
            c, f = too_short[0]
            raise ShortError(f"the pinned or named scene {c.id} is {_seconds(c.frames, fps):.2f} s long; at its place in "
                             f"the short it needs {_seconds(f, fps):.2f} s for the fades and joins")
        movable = [c for c in kept if c.tier < 3]
        if movable:
            drop = min(movable, key=lambda c: c.key)
            notes.append(f"{drop.id} was left out: the floors of the kept scenes did not fit the length")
            kept.remove(drop)
            continue
        names = ", ".join(c.id for c in kept)
        raise ShortError(f"the pinned or named scenes ({names}) need at least {_seconds(sum(fl), fps):.1f} s at their "
                         f"shortest and the short is {_seconds(total, fps):.1f} s long. Pin or name fewer scenes, or "
                         f"ask for a longer short.")


def _scene_cues(film: dict, seg_id: str, length_s: float) -> list:
    out = []
    for c in (film.get("subtitles") or {}).get("cues") or []:
        if c.get("anchor") == seg_id:
            a, b = max(0.0, c["start_s"]), min(length_s, c["end_s"])
            if b > a:
                out.append((a, b))
    return sorted(out)


def _crop_for(crop, a: int, window: int, length: int):
    """The crop of the scene for the window that starts `a` frames into it and lasts `window` frames. A pan is cut to
    the window; the render moves the window by n / (length - 1) of the way, so the window keeps the position it had
    at each frame and the speed of the pan."""
    if not crop:
        return None
    crop = dict(crop)
    if "x_from" not in crop:
        return crop
    d = crop["x_to"] - crop["x_from"]
    span = max(1, length - 1)
    first = crop["x_from"] + d * a / span
    last = crop["x_from"] + d * (a + window - 1) / span
    return {"x_from": round(first, 6), "x_to": round(last, 6)}


def _export_entry(film: dict, output: str, name: str, size, subtitles: str) -> dict:
    import export
    entry = export.preset_entry("short", os.path.dirname(output), {"width": size[0], "height": size[1]},
                                name=name, width=size[0], height=size[1])
    entry["output"] = output
    for e in film.get("exports") or []:
        if e.get("enabled", True) and str(e.get("output", "")).lower().endswith(".mp4") \
                and e.get("video", {}).get("codec") in ("h264_videotoolbox", "libx264"):
            entry["video"]["codec"] = e["video"]["codec"]
            if e["video"]["codec"] == "libx264":
                entry["video"]["preset"] = e["video"].get("preset", "slow")
            break
    entry["subtitles"] = subtitles
    return entry


def plan_short(film: dict, film_path: str = "", length_s: float = None, size=None, prefer=(), only=(), exclude=(),
               output: str = None, name: str = None, rules=None, film_hash: str = None,
               subtitles: str = "burned") -> ShortPlan:
    """The plan of a short of `length_s` seconds (default 60, decision D15) from the timeline data `film`. See the
    module text for the rules. `size` is the frame (default 1080 x 1920), `prefer` the scenes the request names
    (they are kept), `only` the scenes the short may be made from, `exclude` the scenes it may not use. `output` is the
    MP4 the short will be rendered to; the plan file sits next to it. `rules` are the line rules of the 9:16 frame
    (default: the real font through textlayout). Raises ShortError."""
    import textlayout
    length_s = config.SHORT_DEFAULT_LENGTH_SEC if length_s is None else float(length_s)
    size = tuple(size or config.SHORT_FRAME)
    if length_s <= 0:
        raise ShortError("the length of the short must be more than 0 seconds")
    if len(size) != 2 or size[0] <= 0 or size[1] <= 0 or size[0] % 2 or size[1] % 2 or size[1] <= size[0]:
        raise ShortError(f"the size {size[0]}x{size[1]} is not a vertical size with even sides (for example "
                         f"{config.SHORT_FRAME[0]}x{config.SHORT_FRAME[1]})")
    fps = _fps(film)
    notes, warnings = [], []
    prefer = _check_ids(film, prefer, "prefer", enabled_only=True)
    only = _check_ids(film, only, "only", enabled_only=True)
    exclude = _check_ids(film, exclude, "exclude")
    clash = [i for i in prefer if i in exclude]
    if clash:
        raise ShortError(f"{', '.join(clash)} is both named and excluded")

    rows = words.film_segments(film)
    if not rows:
        raise ShortError("the film has no scene")
    pool = []
    for row in rows:
        seg = row["seg"]
        sid = seg["id"]
        if sid in exclude:
            continue
        if seg.get("priority") == "drop":
            if sid in prefer:
                raise ShortError(f"scene {sid} is named, but it is marked drop (priority) in the film")
            continue
        if only and sid not in only and sid not in prefer:
            continue
        cues = _scene_cues(film, sid, row["length_s"])
        has_text = bool((seg.get("text") or "").strip()) or bool(cues)
        tier = 3 if seg.get("priority") == "must" or sid in prefer else 2 if has_text else 1
        pool.append(Cand(row=row, pos=len(pool), frames=row["frames"], tier=tier,
                         cue_s=sum(b - a for a, b in cues), cues=cues,
                         shown_s=shown_seconds(row["frames"], frames_of(config.SHORT_AIM_SCENE_SEC, fps), cues, fps)))
    if not pool:
        raise ShortError("no scene is left to make the short from (every scene is removed, dropped or excluded)")

    join_s = float((film["film"].get("audio_join") or {}).get("duration_s", 0.0))
    fade_in, fade_out = float(film["film"].get("fade_in_s", 0.0)), float(film["film"].get("fade_out_s", 0.0))
    floors = position_floors(fade_in, fade_out, join_s, fps)
    target = frames_of(length_s, fps)
    if target < floors["only"]:
        raise ShortError(f"a short of {length_s:g} s is too short: the fade-in and the fade-out alone need "
                         f"{_seconds(floors['only'], fps):.1f} s ({fade_in:g} s and {fade_out:g} s)")

    pool_frames = sum(c.frames for c in pool)
    whole = pool_frames <= target
    if whole:
        kept, why = list(pool), {c.id: "the whole film" for c in pool}
        notes.append(f"the film ({_clock(_seconds(pool_frames, fps))}) is not longer than {length_s:g} s, so the short "
                     f"is the whole film and {_seconds(target - pool_frames, fps):.1f} s shorter than asked")
        kept = _fit_floors(kept, floors, pool_frames + 10 ** 9, notes, fps)
        shares = [c.frames for c in kept]
        total_frames = sum(shares)
    else:
        named = set(prefer)
        top = floors["inner"]
        k_max = (target - floors["first"] - floors["last"]) // top + 2 \
            if target >= floors["first"] + floors["last"] else 1
        aim = max(1, int(math.floor(_seconds(target, fps) / config.SHORT_AIM_SCENE_SEC + 0.5)))
        parts_max = min(k_max, len(pool))
        parts = min(aim, parts_max)
        cap = frames_of(config.SHORT_MAX_SCENE_SEC, fps)
        kept = why = None
        while True:
            trial_notes = []
            chosen, reasons = _select(pool, parts, floors, named)
            chosen = _fit_floors(chosen, floors, target, trial_notes, fps)
            capacity = sum(min(cap, c.frames) for c in chosen)
            if capacity >= target or parts >= parts_max:
                kept, why = chosen, reasons
                notes += trial_notes
                break
            parts += 1
        if capacity < target:
            if sum(c.frames for c in kept) < target:
                raise ShortError(f"the scenes chosen are {_seconds(sum(c.frames for c in kept), fps):.1f} s long "
                                 f"together and the short is {length_s:g} s long")
            lo, hi = cap, max(c.frames for c in kept)
            while lo < hi:
                mid = (lo + hi) // 2
                if sum(min(mid, c.frames) for c in kept) >= target:
                    hi = mid
                else:
                    lo = mid + 1
            notes.append(f"the cap of {config.SHORT_MAX_SCENE_SEC:g} s per scene was raised to "
                         f"{_seconds(lo, fps):.1f} s: {len(kept)} scenes at the cap are shorter than {length_s:g} s")
            cap = lo
        fl = _floors_for(kept, floors)
        shares = allocate([c.frames for c in kept], fl, [min(cap, c.frames) for c in kept], target)
        total_frames = target

    if not kept:
        raise ShortError("no scene is long enough for its place in the short: the first scene needs "
                         f"{_seconds(floors['first'], fps):.2f} s (the fade-in), the last {_seconds(floors['last'], fps):.2f} s "
                         f"(the fade-out), a single scene {_seconds(floors['only'], fps):.2f} s")

    # windows, crop, cues, titles
    seg_rows = []
    fi = (film.get("subtitles") or {})
    has_subs = "subtitles" in film
    rules = rules or textlayout.rules(size)
    cue_out, titles_out = [], []
    cues_kept, cues_dropped = [], []
    film_cues = {}
    for c in (fi.get("cues") or []):
        film_cues.setdefault(c.get("anchor"), []).append(c)
    film_titles = film.get("titles") or []
    kept_ids = {c.id: i for i, c in enumerate(kept)}
    scene_rows = []
    windows = []
    for c, n in zip(kept, shares):
        a = best_window(c.frames, n, c.cues, fps)
        windows.append(a)
    new_ids = [f"s{i + 1:03d}" for i in range(len(kept))]

    for i, (c, n, a) in enumerate(zip(kept, shares, windows)):
        seg = c.row["seg"]
        w0, w1 = _seconds(a, fps), _seconds(a + n, fps)
        new = {"id": new_ids[i], "source": seg["source"], "in": seg["in"] + a, "out": seg["in"] + a + n}
        if seg.get("label"):
            new["label"] = seg["label"]
        new["origin"] = "short"
        new["note"] = f"from scene {c.id} of the film, {why.get(c.id, '')}".rstrip(", ")
        crop = _crop_for(seg.get("crop"), a, n, c.frames)
        if crop:
            new["crop"] = crop
        if seg.get("gain_db_adjust"):
            new["gain_db_adjust"] = seg["gain_db_adjust"]
        film_in = c.row["start_s"] + w0
        new["x_film_segment"] = c.id
        new["x_film_in_s"] = round(film_in, 4)
        new["x_film_out_s"] = round(c.row["start_s"] + w1, 4)
        seg_rows.append(new)

        # subtitles of the scene
        scene_len = _seconds(n, fps)
        mine = {"kept": [], "dropped": [], "pieces": 0}
        for cue in sorted(film_cues.get(c.id, []), key=lambda q: (q["start_s"], q["end_s"])):
            c0, c1 = max(0.0, cue["start_s"]), min(c.row["length_s"], cue["end_s"])
            if c1 <= c0:
                continue
            kept_span = keep_cue(c0, c1, w0, w1)
            if kept_span is None:
                why_dropped = "outside the window" if min(c1, w1) - max(c0, w0) <= 1e-9 else \
                    f"less than max(1.0 s, 0.8 of its length) of it stays in the window"
                cues_dropped.append({"id": cue["id"], "scene": c.id, "text": cue["text"], "why": why_dropped})
                mine["dropped"].append(cue["id"])
                continue
            a0, b0 = kept_span
            start = _snap(a0 - w0, fps)
            end = min(_snap(b0 - w0, fps), _snap(scene_len, fps))
            if end - start < float(1 / fps) - 1e-9:
                cues_dropped.append({"id": cue["id"], "scene": c.id, "text": cue["text"], "why": "shorter than a frame"})
                mine["dropped"].append(cue["id"])
                continue
            pieces, rep = lay_cue(cue["text"], end - start, rules, fps)
            if rep.get("overflow"):
                warnings.append(f"cue {cue['id']} of scene {c.id} needs more time than it has in the 9:16 frame; the "
                                f"{len(pieces)} lines are shorter than a person can read")
            for ps, pe, text in pieces:
                cue_out.append({"anchor": new_ids[i], "start_s": _snap(start + ps, fps),
                                "end_s": min(_snap(start + pe, fps), _snap(scene_len, fps)), "text": text,
                                "origin": cue.get("origin", "manual")})
            mine["kept"].append(cue["id"])
            mine["pieces"] += len(pieces)
            cues_kept.append(cue["id"])
        scene_rows.append(mine)

    # numbering of cues and titles in film order
    for k, cue in enumerate(cue_out):
        cue["id"] = f"c{k + 1:03d}"
        cue_out[k] = {"id": cue["id"], "anchor": cue["anchor"], "start_s": cue["start_s"], "end_s": cue["end_s"],
                      "text": cue["text"], "origin": cue["origin"]}

    titles_dropped = []
    rows_by_id = {r["seg"]["id"]: r for r in rows}
    for t in film_titles:
        anchor = t.get("anchor", "film")
        if anchor == "film":
            holder = next((r for r in rows if r["start_s"] - 1e-6 <= t["start_s"] < r["start_s"] + r["length_s"] - 1e-6), None)
            if holder is None:
                titles_dropped.append(t["id"])
                continue
            sid, base = holder["seg"]["id"], holder["start_s"]
            t0, t1 = t["start_s"] - base, min(t["end_s"] - base, holder["length_s"])
        else:
            sid = anchor
            row = rows_by_id.get(sid)
            if row is None:
                titles_dropped.append(t["id"])
                continue
            t0, t1 = max(0.0, t["start_s"]), min(row["length_s"], t["end_s"])
        if sid not in kept_ids:
            titles_dropped.append(t["id"])
            continue
        i = kept_ids[sid]
        w0, w1 = _seconds(windows[i], fps), _seconds(windows[i] + shares[i], fps)
        span = keep_cue(t0, t1, w0, w1) if t1 > t0 else None
        if span is None:
            titles_dropped.append(t["id"])
            continue
        new_t = {"id": f"t{len(titles_out) + 1:03d}", "text": t["text"], "anchor": new_ids[i],
                 "start_s": _snap(span[0] - w0, fps), "end_s": min(_snap(span[1] - w0, fps), _snap(_seconds(shares[i], fps), fps))}
        for key in ("template", "size_pct", "fade_s"):
            if key in t:
                new_t[key] = t[key]
        titles_out.append(new_t)
    if film_titles and titles_dropped:
        notes.append(f"{len(titles_dropped)} title(s) of the film are not in the short: {', '.join(titles_dropped)}")

    # the file
    used = []
    for s in seg_rows:
        if s["source"] not in used:
            used.append(s["source"])
    f = film["film"]
    short_film = {"fps": copy.deepcopy(f["fps"]), "frame": {"width": size[0], "height": size[1]}, "fit": "fill",
                  "fade_in_s": f.get("fade_in_s", 0.0), "fade_out_s": f.get("fade_out_s", 0.0)}
    if f.get("title"):
        short_film = {"title": f"{f['title']} (short, {length_s:g} s)", **short_film}
    if f.get("audio_join"):
        short_film["audio_join"] = copy.deepcopy(f["audio_join"])
    if output is None:
        output = os.path.join(os.path.dirname(film_path) or ".", f"short-{length_s:g}.mp4")
    name = name or os.path.splitext(os.path.basename(output))[0]
    data = {"version": 1, "kind": "short", "film": short_film,
            "sources": {sid: copy.deepcopy(film["sources"][sid]) for sid in used},
            "segments": seg_rows, "look": copy.deepcopy(film["look"]), "audio": copy.deepcopy(film["audio"])}
    if titles_out:
        data["titles"] = titles_out
    if has_subs:
        data["subtitles"] = {"language": fi.get("language", config.SUBTITLE_LANGUAGE), "cues": cue_out}
    data["exports"] = [_export_entry(film, output, name, size, subtitles)]
    data["derived_from"] = {
        "film": film_path, "target_length_s": length_s,
        "settings": {"aim_scene_s": config.SHORT_AIM_SCENE_SEC, "min_scene_s": config.SHORT_MIN_SCENE_SEC,
                     "max_scene_s": config.SHORT_MAX_SCENE_SEC, "size": [size[0], size[1]],
                     "prefer": prefer, "only": only, "exclude": exclude,
                     "film_sha256": film_hash or hashlib.sha256(timeline.dumps(film).encode("utf-8")).hexdigest()}}

    findings = timeline.validate(data, file_checks=False)
    errors = [x for x in findings if x.level == "error"]
    if errors:
        raise ShortError("the short breaks a rule of the timeline file: " + "; ".join(
            f"{x.rule} at {x.path}: {x.message}" for x in errors[:4]))
    warnings += [f"{x.rule} {x.path}: {x.message}" for x in findings if x.level == "warning"]

    scenes = []
    for i, (c, n, a) in enumerate(zip(kept, shares, windows)):
        s = seg_rows[i]
        start_s = _seconds(sum(shares[:i]), fps)
        scenes.append({"id": s["id"], "film_id": c.id, "source": s["source"],
                       "size": film["sources"][s["source"]]["size"], "film_in_s": s["x_film_in_s"],
                       "film_out_s": s["x_film_out_s"], "short_in_s": round(start_s, 3),
                       "short_out_s": round(start_s + _seconds(n, fps), 3), "frames": n,
                       "scene_frames": c.frames, "crop": s.get("crop"), "reason": why.get(c.id, ""),
                       "tier": c.tier, "cues": scene_rows[i]})
    return ShortPlan(data=data, plan_path=os.path.splitext(output)[0] + ".json", target_frames=target,
                     frames=total_frames, fps=fps, scenes=scenes, cues_kept=cues_kept, cues_dropped=cues_dropped,
                     titles_kept=[t["id"] for t in titles_out], titles_dropped=titles_dropped, notes=notes,
                     warnings=warnings, film_frames=sum(r["frames"] for r in rows), pool_scenes=len(pool),
                     whole_film=whole)


# ── The dry run ─────────────────────────────────────────────────

def _crop_text(crop) -> str:
    if not crop:
        return "centre"
    if "x" in crop:
        return f"x {crop['x']:g}"
    return f"x {crop['x_from']:g} to {crop['x_to']:g}"


def format_plan(plan: ShortPlan, film_name: str = "the film", plan_path: str = None) -> str:
    """The plan as text: the kept scenes with their film and short times, and the subtitles that stay and go."""
    fps = plan.fps
    lines = []
    got = _seconds(plan.frames, fps)
    lines.append(f"Short from {film_name}: asked for {plan.data['derived_from']['target_length_s']:g} s "
                 f"({plan.target_frames} frames), the plan is {got:.3f} s ({plan.frames} frames) in "
                 f"{len(plan.scenes)} scenes, {plan.data['film']['frame']['width']}x{plan.data['film']['frame']['height']}.")
    lines.append(f"Scenes of the film that the short may use: {plan.pool_scenes}.")
    lines.append("  short   film   film in - out (s)        short in - out (s)      length   crop         cues kept/dropped   why")
    for s in plan.scenes:
        c = s["cues"]
        lines.append(f"  {s['id']}    {s['film_id']}   {s['film_in_s']:8.2f} - {s['film_out_s']:8.2f}   "
                     f"{s['short_in_s']:8.2f} - {s['short_out_s']:8.2f}   {_seconds(s['frames'], fps):5.2f} s   "
                     f"{_crop_text(s['crop']):12} {len(c['kept'])}/{len(c['dropped'])}                 {s['reason']}")
    if plan.cues_kept or plan.cues_dropped:
        lines.append(f"Subtitles: the kept scenes have {len(plan.cues_kept) + len(plan.cues_dropped)} cues in the film; "
                     f"{len(plan.cues_kept)} stay ({len(plan.data.get('subtitles', {}).get('cues', []))} cues after "
                     f"the layout for 9:16), {len(plan.cues_dropped)} are dropped.")
        for d in plan.cues_dropped:
            lines.append(f"  dropped {d['id']} of {d['scene']} \"{' '.join(str(d['text']).split())}\": {d['why']}")
    if plan.data.get("titles") or plan.titles_dropped:
        lines.append(f"Titles: {len(plan.titles_kept)} stay, {len(plan.titles_dropped)} do not"
                     + (f" ({', '.join(plan.titles_dropped)})" if plan.titles_dropped else "") + ".")
    for n in plan.notes:
        lines.append(f"Note: {n}")
    for w in plan.warnings:
        lines.append(f"Warning: {w}")
    lines.append(f"Plan file: {plan_path or plan.plan_path}")
    return "\n".join(lines) + "\n"


# ── The 9:16 window on a frame (for the crop commands) ──────────

def window_info(source_size: dict, size, crop=None, t: float = 0.5) -> dict:
    """Where the 9:16 window sits in a picture of `source_size` ({width, height}) for a frame of `size`, as the
    renderer cuts it (render.placement, mode fill). `t` (0 to 1) is how far into the scene: only a pan moves the window."""
    import render
    sw, sh = source_size["width"], source_size["height"]
    p = render.placement((sw, sh), tuple(size), "fill", crop)
    wx = render.round_even(p["wx_from"] + (p["wx_to"] - p["wx_from"]) * t)
    return {"source": (sw, sh), "window": (p["ww"], p["wh"]), "x": wx, "y": p["wy"], "free": sw - p["ww"],
            "share_pct": 100.0 * p["ww"] / sw, "scale": p["scale"], "frame": tuple(size),
            "pan": p["wx_from"] != p["wx_to"], "from": p["wx_from"], "to": p["wx_to"]}


def describe_window(info: dict) -> str:
    sw, sh = info["source"]
    ww, wh = info["window"]
    scale = info["scale"]
    how = f"enlarged by {scale:.3f}" if scale > 1.0005 else f"reduced to {scale:.3f}" if scale < 0.9995 else "not scaled"
    where = f"left edge at pixel {info['x']} of {info['free']}"
    if info["pan"]:
        where = f"a pan from pixel {info['from']} to {info['to']} of {info['free']}"
    return (f"window {ww} x {wh} of {sw} x {sh} ({info['share_pct']:.1f} percent of the width), {where}; "
            f"{how} to {info['frame'][0]} x {info['frame'][1]}")


def draw_window_still(tl, scene_id: str, work_dir: str, size=None, at_s: float = None, out_path: str = None) -> dict:
    """A picture for Claude Code to look at: a frame of the scene with the 9:16 window outlined (the rest dimmed) and
    next to it what the window shows. The frame is cut from the clip's preview copy (so it carries the base LUT) at
    film second `at_s` (default: the middle of the scene), with the creative LUT if the film has one. Returns
    {"path", "info", "film_s"}. Raises ShortError for a scene that is removed or an `at_s` outside it."""
    import subprocess

    import convert
    import frames
    import render
    from PIL import Image, ImageDraw, ImageFont

    size = tuple(size or config.SHORT_FRAME)
    data = tl.data
    rows = {r["seg"]["id"]: r for r in words.film_segments(data)}
    seg = next((s for s in data["segments"] if s["id"] == scene_id), None)
    if seg is None:
        raise ShortError(f"there is no scene {scene_id}")
    row = rows.get(scene_id)
    if row is None:
        raise ShortError(f"scene {scene_id} is removed from the film; the still needs a place in it. "
                         f"Put it back first: python main.py scenes restore {scene_id}")
    fps = tl.fps
    if at_s is None:
        at_s = row["start_s"] + row["length_s"] / 2.0
    if not row["start_s"] - 1e-6 <= at_s < row["start_s"] + row["length_s"] - 1e-6:
        raise ShortError(f"{at_s:g} s is not inside scene {scene_id} "
                         f"({row['start_s']:.2f} to {row['start_s'] + row['length_s']:.2f} s of the film)")
    k = at_s - row["start_s"]
    t = k / max(1e-9, row["length_s"])
    src = data["sources"][seg["source"]]
    info = window_info(src["size"], size, seg.get("crop"), t)
    frame_no = seg["in"] + min(seg["out"] - seg["in"] - 1, frames_of(k, fps))
    folder = os.path.join(work_dir, "stills")
    os.makedirs(folder, exist_ok=True)
    chain = "setpts=PTS"
    if data["look"].get("lut"):
        chain += f",lut3d=file={convert.escape_filter_path(data['look']['lut']['path'])}:interp=tetrahedral"
    raw = os.path.join(folder, f".crop-{scene_id}.tmp.png")
    frames._extract(tl, src["preview_copy"], frame_no, chain, raw)
    try:
        img = Image.open(raw).convert("RGB")
        img.load()
    finally:
        try:
            os.unlink(raw)
        except OSError:
            pass
    cw, ch = img.size
    kx = cw / src["size"]["width"]
    ww, wh = info["window"]
    box = (round(info["x"] * kx), round(info["y"] * kx), round((info["x"] + ww) * kx), round((info["y"] + wh) * kx))
    panel_w = int(config.SHORT_STILL_WIDTH * 0.68)
    panel_h = round(panel_w * ch / cw)
    left = img.resize((panel_w, panel_h), Image.LANCZOS)
    f = panel_w / cw
    sbox = tuple(round(v * f) for v in box)
    dim = Image.eval(left, lambda v: int(v * 0.3))
    dim.paste(left.crop(sbox), sbox[:2])
    d = ImageDraw.Draw(dim)
    d.rectangle(sbox, outline=(255, 214, 10), width=3)
    if info["pan"]:                                      # the start and the end of a pan
        for px, colour in ((info["from"], (255, 255, 255)), (info["to"], (255, 120, 120))):
            x0 = round(px * kx * f)
            d.rectangle((x0, sbox[1], x0 + (sbox[2] - sbox[0]), sbox[3]), outline=colour, width=1)
    cut = img.crop(box)
    cut_h = panel_h
    cut_w = round(cut_h * size[0] / size[1])
    cut = cut.resize((cut_w, cut_h), Image.LANCZOS)
    gap, strip = 16, 64
    out = Image.new("RGB", (panel_w + gap + cut_w, panel_h + strip), (20, 20, 28))
    out.paste(dim, (0, 0))
    out.paste(cut, (panel_w + gap, 0))
    d = ImageDraw.Draw(out)
    font = ImageFont.load_default(18)
    crop_text = "centre (no crop set)" if not seg.get("crop") else \
        f"x {seg['crop']['x']:g}" if "x" in seg["crop"] else f"pan x {seg['crop']['x_from']:g} to {seg['crop']['x_to']:g}"
    d.text((8, panel_h + 8), f"{scene_id}   film {at_s:.2f} s   clip {seg['source']}   crop: {crop_text}", fill=(235, 235, 235), font=font)
    d.text((8, panel_h + 34), describe_window(info), fill=(200, 200, 200), font=font)
    if out_path is None:
        label = "centre" if not seg.get("crop") else f"x{seg['crop']['x']:g}" if "x" in seg["crop"] \
            else f"pan{seg['crop']['x_from']:g}-{seg['crop']['x_to']:g}"
        out_path = os.path.join(folder, f"crop-{scene_id}-{label}-at{at_s:.1f}s.jpg")
    out.save(out_path, quality=config.SHEET_JPEG_QUALITY)
    return {"path": out_path, "info": info, "film_s": at_s}


# ── Stills of a rendered short ──────────────────────────────────

def scene_stills(short_data: dict, mp4_path: str, folder: str = None) -> dict:
    """One still per scene of a rendered short, for Claude Code and the owner to look at: the middle frame of each scene,
    cut from the finished file into `<output without .mp4>.stills/<scene id>.jpg`, and a contact sheet
    `<output without .mp4>.contact.jpg` that shows them side by side with the scene id, the scene of the film it
    came from, its length and its crop. Returns {"folder", "stills": [paths], "contact": path}."""
    import subprocess

    import tools
    from PIL import Image, ImageDraw, ImageFont

    base = os.path.splitext(mp4_path)[0]
    folder = folder or base + ".stills"
    os.makedirs(folder, exist_ok=True)
    f = short_data["film"]["fps"]
    fps = Fraction(f["num"], f["den"])
    stills, start = [], 0
    for seg in short_data["segments"]:
        if not seg.get("enabled", True):
            continue
        n = seg["out"] - seg["in"]
        mid = start + n // 2
        out = os.path.join(folder, f"{seg['id']}.jpg")
        cmd = [tools.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-ss", f"{float((mid + Fraction(1, 2)) / fps):.6f}", "-i", mp4_path, "-frames:v", "1",
               "-q:v", "2", out]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(out):
            raise ShortError(f"ffmpeg could not cut the still of {seg['id']} from {mp4_path}: {r.stderr.strip()[-300:]}")
        stills.append((seg, n, out))
        start += n
    cols = config.SHORT_CONTACT_COLUMNS
    tw = 270
    th = round(tw * short_data["film"]["frame"]["height"] / short_data["film"]["frame"]["width"])
    label_h, gap = 34, 8
    rows = (len(stills) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + gap) + gap, rows * (th + label_h + gap) + gap), (20, 20, 28))
    d = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(16)
    for k, (seg, n, path) in enumerate(stills):
        im = Image.open(path).convert("RGB").resize((tw, th), Image.LANCZOS)
        x, y = gap + (k % cols) * (tw + gap), gap + (k // cols) * (th + label_h + gap)
        sheet.paste(im, (x, y))
        crop = seg.get("crop")
        crop_text = "centre" if not crop else f"x {crop['x']:g}" if "x" in crop else f"pan {crop['x_from']:g}-{crop['x_to']:g}"
        d.text((x + 2, y + th + 2), f"{seg['id']}  from {seg.get('x_film_segment', '?')}  {float(Fraction(n) / fps):.1f} s", fill=(235, 235, 235), font=font)
        d.text((x + 2, y + th + 18), f"crop {crop_text}", fill=(180, 180, 180), font=font)
    contact = base + ".contact.jpg"
    sheet.save(contact, quality=config.SHEET_JPEG_QUALITY)
    return {"folder": folder, "stills": [p for _, _, p in stills], "contact": contact}
