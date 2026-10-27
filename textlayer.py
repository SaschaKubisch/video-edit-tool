"""
Burned-in subtitles and titles: the lanes that ffmpeg lays over the picture (SPECS 9.6, 9.7).

The installed ffmpeg has no libass, freetype or drawtext [measured]. The text is drawn by
textlayout.py onto transparent PNG files, and ffmpeg's `overlay` filter lays them over the picture
after the LUTs and the letterbox. To keep the number of ffmpeg inputs small, the text of a whole film
goes through a few *lanes*. A lane is one ffmpeg input: a list of PNG files with the time each one
is shown (the concat demuxer), a transparent picture in the gaps. All subtitles of one height share a
lane; every title has a lane of its own (a title fades, so it is a run of pictures with a rising and
falling opacity).

    plan = textlayer.build(data, frame, first, last, rects, burn_cues=True)
    plan.signature          # changes when anything that shapes the text changes (the preview cache)
    plan.write(folder)      # draws the PNGs and the lists
    plan.inputs(start)      # the ffmpeg arguments for the lanes
    plan.graph(...)         # the overlay filters

Frames are counted in the stream that the overlay is applied to: a cue that starts at the stream's
frame 30 is shown from its frame 30. The lists put a change half a frame before the frame it is for,
so that no rounding of a time can move it by a frame.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from fractions import Fraction

import config
import textlayout
import words


@dataclass
class Item:
    kind: str                    # "cue" or "title"
    first: int                   # first frame shown, in the stream
    end: int                     # first frame not shown
    lines: tuple
    geometry: tuple              # what the picture looks like: (bottom, band height) for a cue
    opacity: float = 1.0         # a title ramps; a run of frames with one opacity is one item
    ident: str = ""              # the cue or title it came from


@dataclass
class Lane:
    x: int
    y: int
    width: int
    height: int
    items: list = field(default_factory=list)
    files: dict = field(default_factory=dict)       # item index -> PNG path (after write)
    gap_file: str = ""
    list_file: str = ""
    name: str = ""


@dataclass
class TextPlan:
    frame: tuple
    fps: Fraction
    lanes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    signature: str = ""
    cue_count: int = 0
    title_count: int = 0
    lang: str = "en"             # the language of the cues (`subtitles.language`): its font and line height

    @property
    def empty(self) -> bool:
        return not self.lanes

    # ── files ──
    def write(self, folder: str) -> None:
        """Draw the pictures and write the lists. Everything goes into `folder`."""
        os.makedirs(folder, exist_ok=True)
        from PIL import Image
        cache = {}
        for n, lane in enumerate(self.lanes):
            lane.name = f"lane{n}"
            gap = os.path.join(folder, f"{lane.name}-gap.png")
            Image.new("RGBA", (lane.width, lane.height), (0, 0, 0, 0)).save(gap, compress_level=1)
            lane.gap_file = gap
            for i, item in enumerate(lane.items):
                key = (item.kind, item.lines, item.geometry, round(item.opacity, 4), lane.width, lane.height)
                if key not in cache:
                    path = os.path.join(folder, f"{lane.name}-{len(cache):05d}.png")
                    self._draw(item, lane).save(path, compress_level=1)
                    cache[key] = path
                lane.files[i] = cache[key]
            lane.list_file = os.path.join(folder, f"{lane.name}.txt")
            with open(lane.list_file, "w", encoding="utf-8") as f:
                f.write(self._concat(lane))

    def _draw(self, item: Item, lane: Lane):
        W, H = self.frame
        if item.kind == "cue":
            g = textlayout.geometry(self.frame, picture=None, lang=self.lang)
            g.bottom = item.geometry[0]
            g.safe = (g.safe[0], g.safe[1], g.safe[2], g.safe[3])
            image, _ = textlayout.render_lines(list(item.lines), g, "subtitle", canvas=(lane.width, lane.height),
                                               center_x=item.geometry[2])
            return image
        image, _ = item_title_image(self.frame, item)
        return textlayout.with_opacity(image, item.opacity) if item.opacity < 1.0 else image

    def _concat(self, lane: Lane) -> str:
        """The concat list of a lane: a picture and for how long it is shown. A change is put a
        quarter of a frame before the frame it is for."""
        fps = self.fps
        step = float(1 / fps)

        def at(frame):
            return max(0.0, (frame - 0.25) * step)

        lines, cursor = [], 0
        for i, item in enumerate(lane.items):
            if item.first > cursor:
                lines.append((lane.gap_file, at(item.first) - at(cursor)))
            lines.append((lane.files[i], at(item.end) - at(item.first)))
            cursor = item.end
        lines.append((lane.gap_file, 3600.0))
        out = []
        for path, dur in lines:
            out.append("file '" + path.replace("'", "'\\''") + "'")
            out.append(f"duration {dur:.6f}")
        out.append("file '" + lane.gap_file.replace("'", "'\\''") + "'")
        return "\n".join(out) + "\n"

    # ── ffmpeg ──
    def inputs(self) -> list:
        """The ffmpeg input arguments of the lanes, in order."""
        args = []
        for lane in self.lanes:
            args += ["-f", "concat", "-safe", "0", "-i", lane.list_file]
        return args

    def graph(self, base: str, first_input: int, pix_fmt: str, out: str = "txt") -> tuple:
        """The overlay filters that lay every lane over the stream `[base]` (a label). Lane k is
        input `first_input + k`. Returns (the filter text, the label of the result)."""
        fmt = {"yuv420p": "yuv420", "yuv422p10le": "yuv422p10", "yuv422p": "yuv422"}.get(pix_fmt, "auto")
        parts, prev = [], base
        for k, lane in enumerate(self.lanes):
            label = f"{out}{k}"
            on = "+".join(f"between(n,{a},{b - 1})" for a, b in _runs(lane.items))
            parts.append(f"[{prev}][{first_input + k}:v]overlay=x={lane.x}:y={lane.y}:format={fmt}:"
                         f"alpha=straight:eof_action=repeat:shortest=1:enable='{on}'[{label}]")
            prev = label
        return ";".join(parts), prev


def _runs(items) -> list:
    """The frame ranges [a, b) where a lane shows something, joined where they touch."""
    runs = []
    for it in sorted(items, key=lambda i: i.first):
        if runs and it.first <= runs[-1][1]:
            runs[-1][1] = max(runs[-1][1], it.end)
        else:
            runs.append([it.first, it.end])
    return [(a, b) for a, b in runs]


# ── Titles ──────────────────────────────────────────────────────

def title_geometry(frame, template: str, size_pct=None):
    """(size in pixels, longest line in pixels, left edge x0, centre x, safe rectangle) of a title."""
    W, H = frame
    t = config.TITLE_TEMPLATES[template]
    g = textlayout.geometry(frame)
    size = textlayout.even(H * (size_pct or t["size_pct"]) / 100.0)
    safe_w = g.safe[2] - g.safe[0]
    max_width = min(safe_w, round(W * t["max_width_pct"] / 100.0)) - 2 * max(1, round(size * config.TEXT_STYLE["outline_em"]))
    return size, max_width, g.safe[0], (g.safe[0] + g.safe[2]) / 2.0, g.safe


def title_lines(frame, title: dict) -> tuple:
    """The lines of a title: its own line breaks, each wrapped by the measured width. Returns (lines, size,
    template, warnings)."""
    template = title.get("template", "center")
    size, max_width, x0, cx, safe = title_geometry(frame, template, title.get("size_pct"))
    measure = textlayout.measurer("title", size)
    rules = textlayout.LineRules(max_chars=10 ** 6, max_width=max_width, measure=measure.width,
                                 exact=getattr(measure, "exact", False), max_lines=config.TITLE_MAX_LINES)
    lines, warnings = [], []
    for part in title["text"].strip().split("\n"):
        part = " ".join(part.split())
        if not part:
            continue
        wrapped = words.wrap(part, rules, max_lines=config.TITLE_MAX_LINES)
        lines += wrapped if wrapped else [part]
        if wrapped is None:
            warnings.append(f"title {title['id']} does not fit its width")
    if len(lines) > config.TITLE_MAX_LINES:
        warnings.append(f"title {title['id']} has {len(lines)} lines")
    return tuple(lines), size, template, warnings


def item_title_image(frame, item: Item):
    """The picture of a title and its place: (image, (x, y)). `item.geometry` is
    (template, size, id) and `item.lines` the lines."""
    template, size, _ = item.geometry
    t = config.TITLE_TEMPLATES[template]
    g = textlayout.geometry(frame)
    image, _ = textlayout.render_lines(list(item.lines), g, "title", size=size, align=t["align"])
    w, h = image.size
    return image, title_place(frame, template, size, w, h)


def title_box(frame, lines, size: int) -> tuple:
    """(width, height) of the picture of a title, as render_lines makes it, found by measuring (no drawing, so no
    font file is needed; without one the width is an estimate)."""
    st = config.TEXT_STYLE
    outline = max(1, round(size * st["outline_em"]))
    shadow = max(1, round(size * st["shadow_offset_em"]))
    blur = max(1, round(size * st["shadow_blur_em"]))
    pad = outline + shadow + 2 * blur
    m = textlayout.measurer("title", size)
    width = int(math.ceil(max((m.width(s) for s in lines), default=0))) + 2 * pad
    return width, round(size * st["line_height_em"]) * len(lines) + 2 * pad


def title_place(frame, template: str, size: int, w: int, h: int) -> tuple:
    t = config.TITLE_TEMPLATES[template]
    W, H = frame
    _, _, x0, cx, safe = title_geometry(frame, template, None)
    pad = max(1, round(size * config.TEXT_STYLE["outline_em"])) + max(1, round(size * config.TEXT_STYLE["shadow_offset_em"])) \
        + 2 * max(1, round(size * config.TEXT_STYLE["shadow_blur_em"]))
    x = int(round(cx - w / 2.0)) if t["align"] == "center" else x0 - pad
    y_ref = t["y_pct"] / 100.0 * H
    y = int(round(y_ref - h / 2.0)) if t["y_ref"] == "center" else int(round(y_ref - h + pad))
    # The text stays inside the safe area of the frame (SPECS 11.6). In a 16:9 frame the templates lie inside it
    # already; in a 9:16 frame the bottom 35 percent are free, which the lower third (72 percent) would take.
    # A block taller than the safe area starts at its top.
    ink_h = h - 2 * pad
    y = textlayout.even(y)
    if y + pad + ink_h > safe[3]:                       # too low: up to an even row that is inside
        y = 2 * math.floor((safe[3] - ink_h - pad) / 2.0)
    if y + pad < safe[1]:                               # too high, or taller than the area: the top wins
        y = 2 * math.ceil((safe[1] - pad) / 2.0)
    return textlayout.even(x), y


# ── Building the plan ───────────────────────────────────────────

def _frames(seconds, fps) -> int:
    return int(round(seconds * fps))


def build(data: dict, frame, first: int, last: int, rects: dict, burn_cues: bool = True,
          burn_titles: bool = True) -> TextPlan:
    """The text that falls in the film frames [first, last), laid out for a picture of `frame` pixels.
    `rects` maps a segment id to the rectangle (x0, y0, x1, y1) that its picture fills in the frame (a
    letterboxed picture fills less than the frame). Frames in the plan count from `first`."""
    fps = words._fps(data)
    lang = (data.get("subtitles") or {}).get("language") or "en"
    own = textlayout.own_font(lang)                       # Japanese, Simplified Chinese, Thai: a Noto font (9.6)
    plan = TextPlan(frame=tuple(frame), fps=fps, lang=lang)
    W, H = frame
    rows = {r["seg"]["id"]: r for r in words.film_segments(data)}
    rules_cache, geo_cache = {}, {}
    cue_lanes = {}

    if burn_cues:
        for c in (data.get("subtitles") or {}).get("cues") or []:
            row = rows.get(c.get("anchor"))
            if row is None:
                continue
            a = row["start_frame"] + max(0, _frames(c["start_s"], fps))
            b = row["start_frame"] + min(row["frames"], _frames(c["end_s"], fps))
            a, b = max(a, first), min(b, last)
            if b <= a:
                continue
            rect = tuple(rects.get(row["seg"]["id"]) or (0, 0, W, H))
            if rect not in geo_cache:
                geo_cache[rect] = textlayout.geometry(frame, rect, lang=lang if own else None)
                if not own:
                    rules_cache[rect] = textlayout.rules(frame, rect)
            g = geo_cache[rect]
            key = (g.bottom, (g.safe[0] + g.safe[2]) / 2.0)
            if own:
                pieces = _lang_pieces(c, lang, g, a, b, fps, plan)
            else:
                rules = rules_cache[rect]
                lines = c["text"].strip().split("\n")
                if len(lines) > rules.max_lines or not all(words._fits(rules, s) for s in lines):
                    wrapped = words.wrap(" ".join(c["text"].split()), rules, max_lines=6)
                    if wrapped is None or len(wrapped) > config.SUBTITLE_MAX_LINES:
                        plan.warnings.append(f"cue {c['id']} does not fit two lines of the safe width; it is drawn "
                                             f"in {len(wrapped) if wrapped else '?'} lines")
                    lines = wrapped or lines
                pieces = [(a, b, tuple(lines))]
            for pa, pb, piece_lines in pieces:
                cue_lanes.setdefault(key, []).append(
                    Item("cue", pa - first, pb - first, tuple(piece_lines),
                         (g.bottom, 0, (g.safe[0] + g.safe[2]) / 2.0), 1.0, c["id"]))
            plan.cue_count += 1

    for (bottom, cx), items in sorted(cue_lanes.items()):
        g = next(g for g in geo_cache.values() if g.bottom == bottom)
        most = max(max(len(i.lines) for i in items), config.SUBTITLE_MAX_LINES)
        height = most * g.line_height + 2 * g.pad
        items.sort(key=lambda i: i.first)
        for k in range(len(items) - 1):                 # never two cues at once in a lane
            if items[k].end > items[k + 1].first:
                items[k].end = items[k + 1].first
        plan.lanes.append(Lane(x=0, y=bottom + g.pad - height, width=W, height=height, items=items))

    if burn_titles:
        for t in data.get("titles") or []:
            anchor = t.get("anchor", "film")
            if anchor == "film":
                base, limit = 0, None
            else:
                row = rows.get(anchor)
                if row is None:
                    continue
                base, limit = row["start_frame"], row["start_frame"] + row["frames"]
            a = base + _frames(t["start_s"], fps)
            b = base + _frames(t["end_s"], fps)
            if limit is not None:
                a, b = max(a, base), min(b, limit)
            ta, tb = max(a, first), min(b, last)
            if tb <= ta:
                continue
            lines, size, template, warns = title_lines(frame, t)
            plan.warnings += warns
            fade = _frames(t.get("fade_s", config.TITLE_FADE_SEC), fps)
            items = []
            for f in range(ta, tb):
                alpha = min(1.0, (f - a + 1) / (fade + 1.0), (b - f) / (fade + 1.0)) if fade else 1.0
                alpha = round(max(0.0, alpha), 3)
                if items and items[-1].opacity == alpha and items[-1].end == f - first:
                    items[-1].end = f - first + 1
                else:
                    items.append(Item("title", f - first, f - first + 1, lines, (template, size, t["id"]),
                                      alpha, t["id"]))
            if not items:
                continue
            bw, bh = title_box(frame, lines, size)
            pos = title_place(frame, template, size, bw, bh)
            lane = Lane(x=pos[0], y=pos[1], width=bw, height=bh, items=items)
            plan.lanes.append(lane)
            plan.title_count += 1
            if pos[0] < 0 or pos[1] < 0 or pos[0] + bw > W or pos[1] + bh > H:
                plan.warnings.append(f"title {t['id']} reaches outside the frame")

    plan.signature = _signature(plan)
    return plan


def font_credit(plan: TextPlan):
    """The line for the credit file of a film that has this text drawn into it: the fonts that were used (Inter for titles
    and for the subtitles of the languages it draws, the Noto font of a translated language) with their source and the
    licence (SPECS 9.6). None when nothing is drawn."""
    kinds = {i.kind for lane in plan.lanes for i in lane.items}
    names = []
    if "cue" in kinds:
        if textlayout.own_font(plan.lang):
            style = config.TEXT_LANG_STYLE[plan.lang]
            names.append(f"{style['family']} ({style['source']})")
        else:
            names.append(f"Inter ({config.TEXT_INTER_SOURCE})")
    if "title" in kinds and f"Inter ({config.TEXT_INTER_SOURCE})" not in names:
        names.append(f"Inter ({config.TEXT_INTER_SOURCE})")
    if not names:
        return None
    return f"Fonts: {', '.join(names)}; {config.TEXT_FONT_LICENCE}"


def _lang_pieces(cue: dict, lang: str, g, a: int, b: int, fps, plan: TextPlan) -> list:
    """The cue `cue` (frames [a, b)) in Japanese, Simplified Chinese or Thai as [(first, end, lines)]: lines cut at
    phrase boundaries in the real font (`textlayout.lang_lines`). A text that needs more than two lines is cut into
    cues of two lines each (the last may have one) that share the time of the cue in proportion to their length, as the
    English cues of a 9:16 frame do (`words.spread`); nothing is drawn smaller."""
    lines, split = textlayout.lang_lines(cue["text"], lang, g)
    if split:
        plan.warnings.append(f"cue {cue['id']}: a phrase is wider than a line and was cut inside")
    if len(lines) <= config.SUBTITLE_MAX_LINES:
        return [(a, b, tuple(lines))]
    groups = [lines[i:i + config.SUBTITLE_MAX_LINES] for i in range(0, len(lines), config.SUBTITLE_MAX_LINES)]
    spans, report = words.spread(["".join(g_) for g_ in groups], float((b - a) / fps), fps,
                                 max_cps=config.TRANSLATE_LANGUAGE_RULES[lang]["cps"])
    if report["overflow"]:
        plan.warnings.append(f"cue {cue['id']}: its {len(groups)} pieces need more time than the cue has "
                             f"({report['needed_s']} s of {report['available_s']:.2f} s); they are shorter than the "
                             f"reading speed of {lang} allows")
    out = []
    for (s0, s1), group in zip(spans, groups):
        pa, pb = a + int(round(s0 * fps)), a + int(round(s1 * fps))
        if pb > pa:
            out.append((pa, min(pb, b), tuple(group)))
    return out


def _signature_material(plan: TextPlan) -> dict:
    material = {
        "v": 1, "frame": list(plan.frame), "fps": [plan.fps.numerator, plan.fps.denominator],
        "style": config.TEXT_STYLE, "layouts": config.TEXT_LAYOUTS, "titles": config.TITLE_TEMPLATES,
        "fonts": _font_identity(),
        "lanes": [{"x": l.x, "y": l.y, "w": l.width, "h": l.height,
                   "items": [[i.kind, i.first, i.end, list(i.lines), list(map(str, i.geometry)), i.opacity]
                             for i in l.items]} for l in plan.lanes]}
    if textlayout.own_font(plan.lang):
        # a film in English has the signature it always had; another script adds its language, its font file and its line height
        material["lang"] = [plan.lang, config.TEXT_LANG_STYLE[plan.lang], _file_identity(
            lambda: textlayout.lang_font_file(plan.lang), config.TEXT_LANG_STYLE[plan.lang]["font"])]
    return material


def _signature(plan: TextPlan) -> str:
    return hashlib.sha256(json.dumps(_signature_material(plan), sort_keys=True, default=str).encode()).hexdigest()[:20]


def _file_identity(find, fallback_name: str) -> list:
    try:
        path = find()
        st = os.stat(path)
        return [os.path.basename(path), st.st_size, st.st_mtime_ns]
    except (textlayout.FontMissing, OSError):
        return [fallback_name, 0, 0]


def _font_identity():
    return [_file_identity(lambda key=key: textlayout.font_file(config.TEXT_STYLE[key]), config.TEXT_STYLE[key])
            for key in ("subtitle_font", "title_font")]


def has_text(data: dict, burn_cues: bool = True, burn_titles: bool = True) -> bool:
    """Whether the film has anything to draw, without laying it out."""
    if burn_titles and data.get("titles"):
        return True
    return bool(burn_cues and (data.get("subtitles") or {}).get("cues"))
