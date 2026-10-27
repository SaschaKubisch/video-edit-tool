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

The style is defined once in config.TEXT_STYLE and config.TEXT_LAYOUTS, never per film. The one exception is the
font and the line height of the languages that Inter cannot draw (Japanese, Simplified Chinese, Thai): a cue in one
of them is drawn with a Noto font from VET_NOTO_FONT_DIR and its own line height (config.TEXT_LANG_STYLE, SPECS 9.6).
"""

import functools
import math
import os
from dataclasses import dataclass

import config


class FontMissing(Exception):
    """A font file is not where its folder variable says (VET_FONT_DIR for Inter, VET_NOTO_FONT_DIR for Noto)."""


class LayoutEngineMissing(FontMissing):
    """Thai needs Pillow's raqm layout engine, and this Pillow has none."""


def font_dir(env: str = None) -> str:
    return os.environ.get(env or config.TEXT_FONT_DIR_ENV, "")


def font_file(name: str, env: str = None, family: str = "Inter") -> str:
    """The path of the font file `name` in the folder that the variable `env` names (default VET_FONT_DIR, Inter)."""
    env = env or config.TEXT_FONT_DIR_ENV
    folder = font_dir(env)
    if not folder:
        raise FontMissing(f"No font folder. Set {env} to the folder with the "
                          f"{family} files (for example {name}).")
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        raise FontMissing(f"The font file {path} does not exist. {env} must "
                          f"name the folder with the {family} files.")
    return path


def own_font(lang) -> bool:
    """Whether the subtitles of `lang` need a font other than Inter (Japanese, Simplified Chinese, Thai)."""
    return lang in config.TEXT_LANG_STYLE


def lang_font_name(lang):
    """The file name of the font of `lang`, or None for a language that Inter draws."""
    return config.TEXT_LANG_STYLE[lang]["font"] if own_font(lang) else None


def lang_font_file(lang: str) -> str:
    """The path of the Noto font of `lang` (VET_NOTO_FONT_DIR). Raises FontMissing."""
    style = config.TEXT_LANG_STYLE[lang]
    return font_file(style["font"], config.TEXT_NOTO_DIR_ENV, style["family"])


def lang_fonts_available(lang) -> bool:
    """Whether the files that drawing `lang` needs are there (Inter for the other languages)."""
    try:
        if own_font(lang):
            lang_font_file(lang)
        else:
            font_file(config.TEXT_STYLE["subtitle_font"])
        return True
    except FontMissing:
        return False


def line_height_em(lang=None) -> float:
    """The distance between two lines of a subtitle, in em: the language's own where it has one, else Inter's."""
    if own_font(lang):
        return config.TEXT_LANG_STYLE[lang]["line_height_em"]
    return config.TEXT_STYLE["line_height_em"]


def _raqm_available() -> bool:
    from PIL import features
    return bool(features.check("raqm"))


def fonts_available() -> bool:
    try:
        font_file(config.TEXT_STYLE["subtitle_font"])
        font_file(config.TEXT_STYLE["title_font"])
        return True
    except FontMissing:
        return False


@functools.lru_cache(maxsize=64)
def _open(path: str, size: int, raqm: bool = False):
    from PIL import ImageFont
    if raqm:
        return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
    return ImageFont.truetype(path, size)


def get_font(kind: str, size: int, lang=None):
    """The font for "subtitle" or "title" at `size` pixels. A subtitle in Japanese, Simplified Chinese or Thai
    (`lang`) is drawn with the Noto font of that language; every other subtitle, and every title, is Inter. Thai is
    laid out with raqm and fails clearly when this Pillow has none. The tests replace this function with
    one that returns Pillow's built-in font, so they need no font file."""
    if kind == "subtitle" and own_font(lang):
        style = config.TEXT_LANG_STYLE[lang]
        path = lang_font_file(lang)
        raqm = bool(style.get("needs_raqm"))
        if raqm and not _raqm_available():
            raise LayoutEngineMissing(
                f"{style['family']} ({lang}) needs Pillow's raqm layout engine to stack the tone marks of Thai, and "
                f"this Pillow has none (PIL.features.check('raqm') is False). Install a Pillow build with raqm "
                f"(libraqm, harfbuzz and fribidi); the pip wheels of Pillow 8.2 and later include it.")
        return _open(path, int(size), raqm)
    name = config.TEXT_STYLE["subtitle_font" if kind == "subtitle" else "title_font"]
    return _open(font_file(name), int(size))


def even(x) -> int:
    return int(2 * round(x / 2.0))


# ── Measuring ───────────────────────────────────────────────────

class Measurer:
    """The width of a string in pixels, with the font at one size."""
    exact = True

    def __init__(self, kind: str, size: int, lang=None):
        self.kind, self.size = kind, int(size)
        self.font = get_font(kind, size, lang)

    def width(self, text: str) -> float:
        return float(self.font.getlength(text))


class EstimateMeasurer:
    """A rough width for when the font is missing: 0.48 em per character, the mean advance of
    Inter SemiBold in English text with spaces [measured with the real font, tests/test_textlayout.py]."""
    exact = False
    EM = 0.48

    def __init__(self, size: int):
        self.size = int(size)

    def width(self, text: str) -> float:
        return len(text) * self.size * self.EM


def measurer(kind: str, size: int, lang=None):
    try:
        return Measurer(kind, size, lang)
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
    lang: object = None          # the language of the text, for its font and line height (None: Inter, 1.2 em)


def _rect_in(rect, margins):
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    l, t, r, b = margins
    return (x0 + round(l * w), y0 + round(t * h), x1 - round(r * w), y1 - round(b * h))


def geometry(frame, picture=None, size_pct=None, max_lines=None, lang=None) -> Geometry:
    """The numbers for subtitles in a frame of (width, height) pixels. `picture` is the rectangle
    (x0, y0, x1, y1) that the clip's picture fills: a letterboxed clip fills less than the frame,
    and the text stays inside the safe area of the picture it is on (SPECS 9.5). `lang` gives the line height of
    its script (Thai 1.45 em, Japanese and Chinese 1.3 em, else Inter's 1.2 em); the size and the safe area do
    not depend on it."""
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
    line_height = round(size * line_height_em(lang))
    lift = round(layout["bottom_pct"] / 100.0 * (pic[3] - pic[1]))
    bottom = safe[3] - lift
    band_h = max_lines * line_height + 2 * pad
    band = (0, bottom + pad - band_h, W, band_h)
    return Geometry(frame=(W, H), key=key, size=size, outline=outline, shadow=shadow, pad=pad,
                    line_height=line_height, safe=safe, max_width=(safe[2] - safe[0]) - 2 * outline,
                    max_chars=layout["max_chars"], bottom=bottom, band=band, lang=lang)


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


def lang_lines(text: str, lang: str, geo: Geometry):
    """The lines of a cue in Japanese, Simplified Chinese or Thai, cut at phrase boundaries (linebreak.py) so that
    every line is within both limits: the characters per line of its language (translate: 13 full-width characters
    for Japanese, 16 for Chinese, 35 for Thai without tone and vowel marks) and the safe width of the frame, measured
    in the real font. The fewest lines come first. Returns (lines, split_phrase): `split_phrase` says that a
    phrase wider than a line had to be cut inside."""
    import linebreak
    import translate
    text = " ".join(str(text).split())
    font = get_font("subtitle", geo.size, lang)
    chars = float(config.TRANSLATE_LANGUAGE_RULES[lang]["max_chars"])
    room = float(geo.max_width)

    def measure(s):                                  # 1.0 is exactly full in the tighter of the two limits
        return max(translate.visual_len(s, lang) / chars, font.getlength(s) / room)

    found = linebreak.break_lines(text, lang, 1.0, measure)
    return list(found.lines), bool(found.split_phrase)


# ── Drawing ─────────────────────────────────────────────────────

def _text_box(draw_font, text):
    left, top, right, bottom = draw_font.getbbox(text)
    return left, top, right, bottom


def render_lines(lines, geo: Geometry, kind="subtitle", canvas=None, align="center", anchor_x=None,
                 size=None, colour=None, center_x=None, lang=None):
    """Draw `lines` (a list of strings) white with a black outline and a soft shadow.

    With `canvas` = (w, h) the image has that size and the text sits at the bottom, with the
    last line at the same place whatever the number of lines (subtitles use this, so that a cue
    of one line and a cue of two lines share one lane). The text is centred on `center_x`, by
    default the middle of the safe area (which is not the middle of a 9:16 frame). Without a
    canvas the image is cut to the text.
    `lang` (default: the language of `geo`) chooses the font of a subtitle in Japanese, Simplified Chinese or Thai.
    The distance between lines is the geometry's (`geo.line_height`) unless a `size` is given. Lines of those
    languages are placed by the baseline: the font's ascent and descent are centred in the line, so that the stacked
    marks of Thai and the tails below the line have the same room in both lines.
    Returns (image, (x, y)): the RGBA image and its place in the frame, in pixels.
    """
    from PIL import Image, ImageDraw, ImageFilter
    style = config.TEXT_STYLE
    lang = geo.lang if lang is None else lang
    own = kind == "subtitle" and own_font(lang)
    lh_given = geo.line_height if size is None else None
    size = size or geo.size
    font = get_font(kind, size, lang if kind == "subtitle" else None)
    outline = max(1, round(size * style["outline_em"]))
    shadow = max(1, round(size * style["shadow_offset_em"]))
    blur = max(1, round(size * style["shadow_blur_em"]))
    pad = outline + shadow + 2 * blur
    lh = lh_given if lh_given is not None else round(size * style["line_height_em"])
    widths = [font.getlength(s) for s in lines]
    w = int(math.ceil(max(widths))) + 2 * pad if lines else 2 * pad
    h = lh * len(lines) + 2 * pad
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ascent, descent = font.getmetrics()
    layers = []
    for y_offset in (0, 1):
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for i, s in enumerate(lines):
            if align == "center":
                x = pad + (max(widths) - widths[i]) / 2.0
            else:
                x = pad
            if own:
                y, where = pad + i * lh + (lh - (ascent + descent)) / 2.0 + ascent, {"anchor": "ls"}   # the baseline
            else:
                y, where = pad + i * lh + (lh - size) // 2, {}
            if y_offset == 0:                       # the shadow: black, offset, blurred below
                d.text((x + shadow, y + shadow), s, font=font, fill=(0, 0, 0, 255), **where)
            else:
                d.text((x, y), s, font=font, fill=tuple(colour or style["colour"]) + (255,),
                       stroke_width=outline, stroke_fill=tuple(style["outline_colour"]) + (255,), **where)
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
    if center_x is None:
        center_x = (geo.safe[0] + geo.safe[2]) / 2.0
    x = int(round(center_x - w / 2.0)) if align == "center" else int(anchor_x or 0)
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
