"""
Text in the picture: the one style, the safe areas, measuring and drawing (SPECS 9.5, 9.7).

The installed ffmpeg has no libass, freetype or drawtext [measured], so the tool draws the
text itself with Pillow, in Inter, onto transparent PNG files that ffmpeg lays over the picture
(overlays.py). This module has no ffmpeg in it.

- `geometry` turns a frame (and the picture inside it) into pixel numbers: the font size, the
  safe rectangle, the longest line, the place of the last line. Every size is a fraction of the
  frame height, so the preview and an export of any size look the same.
- `Measurer` measures a string with the real font. `rules` builds the line rules (longest line
  in characters and in pixels) that words.py wraps and splits by. Without the font a rough
  estimate is used and says so (`exact` is False).
- `render_lines` draws lines of text (white, black outline, soft shadow) and returns the image.

The style is defined once in config.TEXT_STYLE and config.TEXT_LAYOUTS, never per film.
"""

import functools
import math
import os
from dataclasses import dataclass

import config


class FontMissing(Exception):
    """The Inter font files are not where VET_FONT_DIR says."""


def font_dir() -> str:
    return os.environ.get(config.TEXT_FONT_DIR_ENV, "")


def font_file(name: str) -> str:
    folder = font_dir()
    if not folder:
        raise FontMissing(f"No font folder. Set {config.TEXT_FONT_DIR_ENV} to the folder with the "
                          f"Inter files (for example {name}).")
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        raise FontMissing(f"The font file {path} does not exist. {config.TEXT_FONT_DIR_ENV} must "
                          f"name the folder with the Inter files.")
    return path


def fonts_available() -> bool:
    try:
        font_file(config.TEXT_STYLE["subtitle_font"])
        font_file(config.TEXT_STYLE["title_font"])
        return True
    except FontMissing:
        return False


@functools.lru_cache(maxsize=64)
def _open(path: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(path, size)


def get_font(kind: str, size: int):
    """The font for "subtitle" or "title" at `size` pixels. The tests replace this function with
    one that returns Pillow's built-in font, so they need no font file."""
    name = config.TEXT_STYLE["subtitle_font" if kind == "subtitle" else "title_font"]
    return _open(font_file(name), int(size))


def even(x) -> int:
    return int(2 * round(x / 2.0))


# ── Measuring ───────────────────────────────────────────────────

class Measurer:
    """The width of a string in pixels, with the font at one size."""
    exact = True

    def __init__(self, kind: str, size: int):
        self.kind, self.size = kind, int(size)
        self.font = get_font(kind, size)

    def width(self, text: str) -> float:
        return float(self.font.getlength(text))


class EstimateMeasurer:
    """A rough width for when the font is missing: 0.57 em per character, the mean of the
    Inter SemiBold letters in English text [measured with the real font, tests/test_textlayout.py]."""
    exact = False
    EM = 0.57

    def __init__(self, size: int):
        self.size = int(size)

    def width(self, text: str) -> float:
        return len(text) * self.size * self.EM


def measurer(kind: str, size: int):
    try:
        return Measurer(kind, size)
    except FontMissing:
        return EstimateMeasurer(size)


# ── Geometry ────────────────────────────────────────────────────

@dataclass
class Geometry:
    frame: tuple                 # (width, height)
    key: str                     # "16:9" or "9:16"
    size: int                    # font size in pixels
    outline: int                 # outline width in pixels
    shadow: int                  # shadow offset in pixels
    pad: int                     # room around the text in its picture, for outline and shadow
    line_height: int
    safe: tuple                  # (x0, y0, x1, y1): where text may be
    max_width: int               # longest line in pixels
    max_chars: int               # longest line in characters
    bottom: int                  # y of the bottom of the last line's box
    band: tuple                  # (x, y, w, h) of the picture that holds up to two lines


def _rect_in(rect, margins):
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    l, t, r, b = margins
    return (x0 + round(l * w), y0 + round(t * h), x1 - round(r * w), y1 - round(b * h))


def geometry(frame, picture=None, size_pct=None, max_lines=None) -> Geometry:
    """The numbers for subtitles in a frame of (width, height) pixels. `picture` is the rectangle
    (x0, y0, x1, y1) that the clip's picture fills: a letterboxed clip fills less than the frame,
    and the text stays inside the safe area of the picture it is on (SPECS 9.5)."""
    W, H = frame
    key = config.layout_key(W, H)
    layout = config.TEXT_LAYOUTS[key]
    style = config.TEXT_STYLE
    max_lines = max_lines or config.SUBTITLE_MAX_LINES
    frame_safe = _rect_in((0, 0, W, H), layout["safe"])
    pic = tuple(picture) if picture else (0, 0, W, H)
    pic_safe = _rect_in(pic, layout["safe"])
    safe = (max(frame_safe[0], pic_safe[0]), max(frame_safe[1], pic_safe[1]),
            min(frame_safe[2], pic_safe[2]), min(frame_safe[3], pic_safe[3]))
    size = even(H * (size_pct or layout["size_pct"]) / 100.0)
    outline = max(1, round(size * style["outline_em"]))
    shadow = max(1, round(size * style["shadow_offset_em"]))
    blur = max(1, round(size * style["shadow_blur_em"]))
    pad = outline + shadow + 2 * blur
    line_height = round(size * style["line_height_em"])
    lift = round(layout["bottom_pct"] / 100.0 * (pic[3] - pic[1]))
    bottom = safe[3] - lift
    band_h = max_lines * line_height + 2 * pad
    band = (0, bottom + pad - band_h, W, band_h)
    return Geometry(frame=(W, H), key=key, size=size, outline=outline, shadow=shadow, pad=pad,
                    line_height=line_height, safe=safe, max_width=(safe[2] - safe[0]) - 2 * outline,
                    max_chars=layout["max_chars"], bottom=bottom, band=band)


@dataclass
class LineRules:
    """What words.py wraps and splits by."""
    max_chars: int
    max_width: float
    measure: object              # callable: string -> width in pixels
    exact: bool = True
    max_lines: int = config.SUBTITLE_MAX_LINES


def rules(frame, picture=None, kind="subtitle") -> LineRules:
    """The line rules for `frame` (the film's frame, or an export's), measured with the real
    font when it is available."""
    g = geometry(frame, picture)
    m = measurer(kind, g.size)
    return LineRules(max_chars=g.max_chars, max_width=g.max_width, measure=m.width,
                     exact=getattr(m, "exact", False))


def char_rules(max_chars: int, max_lines: int = config.SUBTITLE_MAX_LINES) -> LineRules:
    """Rules by characters only (a fake width of 10 per character): for tests and for the SRT
    writer when no frame is known."""
    return LineRules(max_chars=max_chars, max_width=float(max_chars * 10), measure=lambda s: 10.0 * len(s),
                     exact=False, max_lines=max_lines)


# ── Drawing ─────────────────────────────────────────────────────

def _text_box(draw_font, text):
    left, top, right, bottom = draw_font.getbbox(text)
    return left, top, right, bottom


def render_lines(lines, geo: Geometry, kind="subtitle", canvas=None, align="center", anchor_x=None,
                 size=None, colour=None):
    """Draw `lines` (a list of strings) white with a black outline and a soft shadow.

    With `canvas` = (w, h) the image has that size and the text sits at the bottom, with the
    last line at the same place whatever the number of lines (subtitles use this, so that a cue
    of one line and a cue of two lines share one lane). Without it the image is cut to the text.
    Returns (image, (x, y)): the RGBA image and its place in the frame, in pixels.
    """
    from PIL import Image, ImageDraw, ImageFilter
    style = config.TEXT_STYLE
    size = size or geo.size
    font = get_font(kind, size)
    outline = max(1, round(size * style["outline_em"]))
    shadow = max(1, round(size * style["shadow_offset_em"]))
    blur = max(1, round(size * style["shadow_blur_em"]))
    pad = outline + shadow + 2 * blur
    lh = round(size * style["line_height_em"])
    widths = [font.getlength(s) for s in lines]
    w = int(math.ceil(max(widths))) + 2 * pad if lines else 2 * pad
    h = lh * len(lines) + 2 * pad
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ascent, _ = font.getmetrics()
    layers = []
    for y_offset in (0, 1):
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for i, s in enumerate(lines):
            if align == "center":
                x = pad + (max(widths) - widths[i]) / 2.0
            else:
                x = pad
            y = pad + i * lh + (lh - size) // 2
            if y_offset == 0:                       # the shadow: black, offset, blurred below
                d.text((x + shadow, y + shadow), s, font=font, fill=(0, 0, 0, 255))
            else:
                d.text((x, y), s, font=font, fill=tuple(colour or style["colour"]) + (255,),
                       stroke_width=outline, stroke_fill=tuple(style["outline_colour"]) + (255,))
        layers.append(layer)
    shadow_layer = layers[0].filter(ImageFilter.GaussianBlur(blur))
    alpha = shadow_layer.getchannel("A").point(lambda v: int(v * style["shadow_alpha"]))
    shadow_layer.putalpha(alpha)
    base = Image.alpha_composite(base, shadow_layer)
    base = Image.alpha_composite(base, layers[1])
    if canvas is None:
        return base, (0, 0)
    cw, ch = canvas
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    x = int(round((cw - w) / 2.0)) if align == "center" else int(anchor_x or 0)
    y = ch - h
    out.alpha_composite(base, (max(0, x), max(0, y)))
    return out, (0, 0)


def ink_box(image):
    """The box (x0, y0, x1, y1) of the pixels that are not transparent, or None."""
    return image.getchannel("A").getbbox()


def with_opacity(image, factor: float):
    """A copy of `image` with its alpha multiplied by `factor` (0 to 1)."""
    out = image.copy()
    alpha = out.getchannel("A").point(lambda v: int(round(v * factor)))
    out.putalpha(alpha)
    return out
