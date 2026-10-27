"""
The scene index (SPECS 10.2; requirement P1): what Claude Code reads to find a scene.

The owner asks for a change in words ("cut out the scene where I am frying a sausage"). Claude
Code cannot see the film, so the tool prepares what it can read:

- `index.json` and `index.md`: one entry per scene (a scene is a segment of the timeline file)
  with its film times, its clip and frames, the words Whisper heard in it, the text of the
  scene's text box, its subtitle cues and the path of its still sheet;
- one still sheet per scene and overview pages (the drawing is in this module too, from the
  preview copies), and finer looks inside one scene (`scenes frames`).

Everything goes to `<work>/index/<timeline name>/`. The module reads the timeline file, the
transcripts that `subtitles auto` kept (`analysis/<clip>/transcript.json`) and the preview
copies. It writes only into that folder.

Times: a *film time* counts from the first frame of the film; a *source time* counts from the
first frame of the clip. A scene that is removed from the film has no film time. A phrase of the
transcript is in the time of the clip's WAV; the scene's sound starts at
`Timeline.source_audio_start`, so the phrase is at `wav time - that start` seconds into the scene.
"""

import datetime
import functools
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from fractions import Fraction

import config
import edits
import fsutil
import textlayout
import timeline
import tools
import transcribe
import words

INDEX_VERSION = 1


# ── Where things are ────────────────────────────────────────────

def timeline_name(timeline_path: str) -> str:
    """The timeline file's name without the extension: the index of `timeline-full.json` lives in
    a folder `timeline-full`, so two timeline files in one work folder do not share sheets."""
    base = os.path.basename(timeline_path)
    return base[:-5] if base.lower().endswith(".json") else base


def index_dir(work_dir: str, timeline_path: str) -> str:
    return os.path.join(work_dir, config.SCENE_INDEX_DIR, timeline_name(timeline_path))


def sheet_path(folder: str, scene_id: str) -> str:
    return os.path.join(folder, "sheets", f"{scene_id}.jpg")


def overview_name(page: int) -> str:
    return f"overview-{page:02d}.jpg"


def overview_place(position: int) -> tuple:
    """(page number, place on the page), both from 1, of the scene at `position` (from 0) in the file."""
    per = config.OVERVIEW_COLUMNS * config.OVERVIEW_ROWS
    return position // per + 1, position % per + 1


# ── Sheet stills of a scene ─────────────────────────────────────

def sheet_frame_count(length_s: float) -> int:
    """How many stills a scene's sheet holds: 3 up to 6 s, 6 up to 30 s, else 9 (config.SHEET_FRAMES)."""
    for limit, n in config.SHEET_FRAMES:
        if limit is None or length_s <= limit:
            return n
    return config.SHEET_FRAMES[-1][1]


def sample_offsets(length_frames: int, n: int) -> list:
    """Frame offsets (from the scene's first frame) of `n` stills at the centres of `n` equal parts
    of the scene. Fewer when the scene has fewer frames."""
    n = max(1, min(n, length_frames))
    return [min(length_frames - 1, int((k + 0.5) * length_frames / n)) for k in range(n)]


# ── Rows ────────────────────────────────────────────────────────

def _round(x, places=3):
    return None if x is None else round(float(x), places)


def _load_transcript(work_dir: str, source_id: str):
    """The kept transcript of a clip, or None when there is none or it cannot be read."""
    try:
        with open(transcribe.transcript_path(work_dir, source_id), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("phrases"), list) else None


def speech_in_scene(tl, seg: dict, transcript: dict, film_start_s) -> tuple:
    """The phrases of the clip's transcript that fall in the scene: (kept, dropped), each a list of
    {text, source_start_s, source_end_s, film_start_s, film_end_s, partial[, dropped, prob]} in
    time order. A phrase belongs to the scene when at least config.SPEECH_MIN_OVERLAP_SEC of it is
    inside; `partial` says the scene cuts it. Film times are None for a scene that is removed."""
    rate = tl.sample_rate
    fps = tl.fps
    s0 = tl.source_audio_start(seg) / rate                    # WAV time at which the scene's sound starts
    length = float((seg["out"] - seg["in"]) / fps)
    video0 = float(Fraction(seg["in"]) / fps)
    kept, dropped = [], []
    for p in transcript["phrases"]:
        a, b = p["start_s"] - s0, p["end_s"] - s0
        inside = min(b, length) - max(a, 0.0)
        if inside < config.SPEECH_MIN_OVERLAP_SEC - 1e-9:
            continue
        a_in, b_in = max(a, 0.0), min(b, length)
        row = {"text": p["text"].strip(),
               "source_start_s": _round(video0 + a_in), "source_end_s": _round(video0 + b_in),
               "film_start_s": None if film_start_s is None else _round(film_start_s + a_in),
               "film_end_s": None if film_start_s is None else _round(film_start_s + b_in),
               "partial": a < -1e-6 or b > length + 1e-6}
        if p.get("dropped"):
            row["dropped"] = p["dropped"]
            row["prob"] = _round(p.get("prob"), 2)
            dropped.append(row)
        else:
            kept.append(row)
    return kept, dropped


def scene_rows(tl, timeline_path: str, work_dir: str, speech: bool = True) -> list:
    """One dict per scene (segment) in the order of the file. `speech` False leaves the transcripts
    unread (the drawing does not need them)."""
    fps = tl.fps
    starts = tl.segment_film_starts()
    folder = index_dir(work_dir, timeline_path)
    spans = {s["id"]: s for s in words.cue_spans(tl.data)}
    cues_by_scene, titles_by_scene = {}, {}
    for c in (tl.data.get("subtitles") or {}).get("cues") or []:
        cues_by_scene.setdefault(c.get("anchor"), []).append(c)
    for t in tl.data.get("titles") or []:
        titles_by_scene.setdefault(t.get("anchor"), []).append(t)
    transcripts = {cid: _load_transcript(work_dir, cid) if speech else None for cid in tl.data["sources"]}
    rows = []
    for position, seg in enumerate(tl.data["segments"]):
        sid = seg["id"]
        enabled = seg.get("enabled", True)
        length = seg["out"] - seg["in"]
        film_start = float(starts[sid] / fps) if enabled else None
        cues = []
        for c in cues_by_scene.get(sid, []):
            span = spans.get(c["id"])
            cues.append({"id": c["id"], "text": c["text"], "origin": c.get("origin", "manual"),
                         "locked": bool(c.get("locked")),
                         "film_start_s": _round(span["start_s"]) if span else None,
                         "film_end_s": _round(span["end_s"]) if span else None})
        transcript = transcripts.get(seg["source"])
        kept, dropped = speech_in_scene(tl, seg, transcript, film_start) if transcript else ([], [])
        path = sheet_path(folder, sid)
        page, place = overview_place(position)
        rows.append({
            "id": sid, "enabled": enabled, "label": seg.get("label", ""), "origin": seg.get("origin", ""),
            "note": seg.get("note", ""),
            "source": seg["source"], "source_in": seg["in"], "source_out": seg["out"],
            "source_start_s": _round(Fraction(seg["in"]) / fps), "source_end_s": _round(Fraction(seg["out"]) / fps),
            "length_frames": length, "length_s": _round(Fraction(length) / fps),
            "film_start_frame": starts[sid] if enabled else None,
            "film_start_s": _round(film_start),
            "film_end_s": _round(film_start + float(Fraction(length) / fps)) if enabled else None,
            "text": seg.get("text", ""),
            "cues": cues,
            "titles": [{"id": t["id"], "text": t["text"]} for t in titles_by_scene.get(sid, [])],
            "has_transcript": transcript is not None,
            "speech": kept, "speech_dropped": dropped,
            "sheet": {"path": path, "exists": os.path.isfile(path)},
            "overview": {"page": os.path.join(folder, overview_name(page)), "place": place},
        })
    return rows


# ── The index ───────────────────────────────────────────────────

def build_index(tl, timeline_path: str, work_dir: str) -> dict:
    """The index as data (nothing is written)."""
    rows = scene_rows(tl, timeline_path, work_dir)
    fps = tl.fps
    frames = tl.film_length_frames()
    folder = index_dir(work_dir, timeline_path)
    return {
        "version": INDEX_VERSION,
        "written": datetime.datetime.now().isoformat(timespec="seconds"),
        "timeline": os.path.abspath(timeline_path),
        "timeline_hash": timeline.file_hash(timeline_path),
        "work_dir": work_dir,
        "fps": tl.data["film"]["fps"],
        "film_frames": frames, "film_seconds": _round(Fraction(frames) / fps, 3),
        "scenes_in_film": sum(1 for r in rows if r["enabled"]),
        "scenes_removed": sum(1 for r in rows if not r["enabled"]),
        "clips_without_transcript": [cid for cid in tl.data["sources"]
                                     if _load_transcript(work_dir, cid) is None],
        "paths": {"folder": folder, "json": os.path.join(folder, "index.json"),
                  "markdown": os.path.join(folder, "index.md")},
        "scenes": rows,
    }


def write_index(tl, timeline_path: str, work_dir: str) -> dict:
    """Build the index and write `index.json` and `index.md`. Returns the index."""
    index = build_index(tl, timeline_path, work_dir)
    fsutil.write_json_atomic(index["paths"]["json"], index)
    fsutil.write_text_atomic(index["paths"]["markdown"], markdown(index))
    return index


def _t(seconds) -> str:
    return "-" if seconds is None else edits.clock(seconds)


def _quote(text: str) -> str:
    return '"' + " ".join(text.split()) + '"'


def _scene_block(s: dict) -> list:
    out = []
    if s["enabled"]:
        out.append(f"### {s['id']}  {_t(s['film_start_s'])} - {_t(s['film_end_s'])}  ({s['length_s']:.1f} s)")
    else:
        out.append(f"### {s['id']}  removed from the film  ({s['length_s']:.1f} s)")
    out.append(f"- clip {s['source']}, source {_t(s['source_start_s'])} - {_t(s['source_end_s'])}, "
               f"frames {s['source_in']}-{s['source_out']}")
    if s["label"]:
        out.append(f"- label: {s['label']}" + (f" ({s['origin']})" if s["origin"] else ""))
    if s["note"]:
        out.append(f"- note: {s['note']}")
    if s["speech"]:
        out.append("- heard (Whisper):")
        for p in s["speech"]:
            at = _t(p["film_start_s"]) if p["film_start_s"] is not None else "source " + _t(p["source_start_s"])
            out.append(f"  - {at}  {_quote(p['text'])}" + ("  (cut by the scene)" if p["partial"] else ""))
    elif s["has_transcript"]:
        out.append("- heard (Whisper): nothing kept")
    else:
        out.append("- heard (Whisper): no transcript for this clip (run `subtitles auto` to make one)")
    if s["speech_dropped"]:
        why = sorted({p["dropped"].split(" (")[0] for p in s["speech_dropped"]})
        out.append(f"- {len(s['speech_dropped'])} phrase(s) dropped as not speech ({'; '.join(why)}); "
                   f"see index.json or `subtitles search WORDS --all`")
    if s["text"]:
        out.append(f"- scene text box: {_quote(s['text'])}")
    for c in s["cues"]:
        at = f"{_t(c['film_start_s'])} - {_t(c['film_end_s'])}" if c["film_start_s"] is not None else "not in the film"
        out.append(f"- subtitle {c['id']} ({c['origin']}) {at}  {_quote(c['text'])}")
    for t in s["titles"]:
        out.append(f"- title {t['id']}  {_quote(t['text'])}")
    sheet = s["sheet"]
    out.append(f"- sheet: {sheet['path']}" + ("" if sheet["exists"] else "  (not drawn yet: run `scenes sheets`)"))
    out.append(f"- overview: {os.path.basename(s['overview']['page'])}, place {s['overview']['place']}")
    return out


def markdown(index: dict) -> str:
    """The index as text for Claude Code to read and search."""
    name = os.path.basename(index["timeline"])
    scenes = index["scenes"]
    lines = [
        f"# Scene index of {name}", "",
        f"Timeline file: {index['timeline']}",
        f"Hash: {index['timeline_hash'][:8]} (the full hash is in index.json). If `timeline summary` shows another "
        f"hash, the film has changed since this was written: run `scenes index` again.",
        f"Written: {index['written']}",
        f"Film: {_t(index['film_seconds'])}, {index['scenes_in_film']} scene(s) in the film, "
        f"{index['scenes_removed']} removed.", "",
        "Film time is hh:mm:ss.cc from the first frame of the film. Source time is the same from the first frame of "
        "the clip. A scene is one segment of the timeline file; its id (s001, s002, ...) is what the commands take.",
        "`heard (Whisper)` is the speech the transcript has in the scene, with the film time of each phrase; it is "
        "what Whisper made of the recorder's sound and may be wrong or invented where nobody speaks.", ""]
    if index["clips_without_transcript"]:
        lines += [f"No transcript for {len(index['clips_without_transcript'])} clip(s): "
                  f"{', '.join(index['clips_without_transcript'])}.", ""]
    lines += ["## Scenes in the film", ""]
    for s in scenes:
        if s["enabled"]:
            lines += _scene_block(s) + [""]
    lines += ["## Removed scenes", ""]
    removed = [s for s in scenes if not s["enabled"]]
    if not removed:
        lines += ["None.", ""]
    for s in removed:
        lines += _scene_block(s) + [""]
    return "\n".join(lines)


# ═══ The still sheets ════════════════════════════════════════════

SHEET_VERSION = 1
BACKGROUND = (24, 24, 28)
HEADER_BACKGROUND = (40, 40, 48)
TEXT_COLOUR = (240, 240, 240)
DIM_COLOUR = (170, 170, 180)
WARN_COLOUR = (255, 190, 90)
LINE_HEIGHTS = {"big": 36, "mid": 28, "small": 26}


class SheetError(Exception):
    """A sheet cannot be drawn: a preview copy is missing, an ffmpeg run failed, or the request makes no sense."""


class Fonts:
    """The three sizes of Inter the sheets use, or Pillow's plain font when VET_FONT_DIR does not name Inter.
    `signature` goes into the key of every sheet, so a sheet is drawn again when the font changes."""

    def __init__(self):
        self.warning = None
        try:
            self.big = textlayout.get_font("title", 26)
            self.mid = textlayout.get_font("subtitle", 20)
            self.small = textlayout.get_font("subtitle", 18)
            parts = ["inter"]
            for name in (config.TEXT_STYLE["title_font"], config.TEXT_STYLE["subtitle_font"]):
                st = os.stat(textlayout.font_file(name))
                parts.append(f"{name}:{st.st_size}:{st.st_mtime_ns}")
            self.signature = "|".join(parts)
        except (textlayout.FontMissing, OSError, AttributeError) as e:
            from PIL import ImageFont
            self.big = ImageFont.load_default(26)
            self.mid = ImageFont.load_default(20)
            self.small = ImageFont.load_default(18)
            self.signature = "builtin"
            self.warning = (f"Inter is not available ({e}); the labels are drawn with Pillow's plain font. "
                            f"Set {config.TEXT_FONT_DIR_ENV} to the folder with Inter-SemiBold.ttf and Inter-Bold.ttf.")

    def get(self, style: str):
        return {"big": self.big, "mid": self.mid, "small": self.small}[style]


@functools.lru_cache(maxsize=1)
def _fonts() -> Fonts:
    return Fonts()


def tile_width(columns: int = None) -> int:
    columns = columns or config.SHEET_COLUMNS
    return (config.SHEET_WIDTH - (columns + 1) * config.SHEET_GAP) // columns


def tile_height() -> int:
    """Height of a still's area on a scene sheet: 16:9 at the tile width."""
    return textlayout.even(tile_width() * 9 / 16)


def overview_tile_height() -> int:
    return textlayout.even(tile_width(config.OVERVIEW_COLUMNS) * 9 / 16)


def tile_rects(count: int, columns: int, width: int, gap: int, tile_h: int, label_h: int, top: int) -> list:
    """The rectangles (x, y, w, h) of the stills of a sheet, row by row. Under each lies a strip of
    `label_h` pixels for its label. `top` is the height of the header."""
    tile_w = (width - (columns + 1) * gap) // columns
    rects = []
    for k in range(count):
        row, col = divmod(k, columns)
        rects.append((gap + col * (tile_w + gap), top + gap + row * (tile_h + label_h + gap), tile_w, tile_h))
    return rects


def _clock_or_dash(seconds) -> str:
    return "-" if seconds is None else edits.clock(seconds)


def sheet_header(row: dict, extra: str = "") -> list:
    """The header lines of a scene's sheet: [(style, text)]. `extra` is added to the first line."""
    if row["enabled"]:
        first = (f"{row['id']}   film {_clock_or_dash(row['film_start_s'])} - {_clock_or_dash(row['film_end_s'])}"
                 f"   {row['length_s']:.1f} s")
    else:
        first = f"{row['id']}   removed from the film   {row['length_s']:.1f} s"
    second = (f"clip {row['source']}   source {_clock_or_dash(row['source_start_s'])} - "
              f"{_clock_or_dash(row['source_end_s'])}   frames {row['source_in']}-{row['source_out']}")
    if row["label"]:
        second += f"   {row['label']}"
    lines = [("big", first + (f"   {extra}" if extra else "")), ("mid", second)]
    if not row["enabled"]:
        lines.append(("mid", f"restore it with: python main.py scenes restore {row['id']}"))
    return lines


def header_height(row: dict) -> int:
    return 10 + sum(LINE_HEIGHTS[style] for style, _ in sheet_header(row)) + 6


def overview_header_height() -> int:
    return 10 + LINE_HEIGHTS["big"] + LINE_HEIGHTS["mid"] + 6


def sheet_tiles(row: dict, fps) -> list:
    """The stills of a scene's sheet: [{frame, source_s, film_s, rel_s}] (film_s None for a removed scene)."""
    n = row["length_frames"]
    out = []
    for off in sample_offsets(n, sheet_frame_count(float(Fraction(n) / fps))):
        out.append(_tile(row, fps, off))
    return out


def _tile(row: dict, fps, offset: int) -> dict:
    frame = row["source_in"] + offset
    film = None if row["film_start_frame"] is None else _round(Fraction(row["film_start_frame"] + offset) / fps)
    return {"frame": frame, "source_s": _round(Fraction(frame) / fps), "film_s": film,
            "rel_s": _round(Fraction(offset) / fps, 3)}


def tile_label(row: dict, tile: dict, places: int = 1) -> str:
    """The label under a still: the scene, the film time and the seconds into the scene; for a removed scene
    the source time."""
    if tile["film_s"] is None:
        return f"{row['id']}  removed  source {edits.clock(tile['source_s'])}  +{tile['rel_s']:.{places}f} s"
    return f"{row['id']}  {edits.clock(tile['film_s'])}  +{tile['rel_s']:.{places}f} s"


# ── Frames from the preview copies, cached ──────────────────────

def _identity(path: str) -> list:
    st = os.stat(path)
    return [os.path.abspath(path), st.st_size, st.st_mtime_ns]


def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def extract_frames(fps, copy: str, frames: list, width: int, out_paths: list) -> None:
    """Write the given frames (numbers in the clip) of the preview copy as PNG files `width` pixels wide.
    One ffmpeg run reads the frames: each input is opened half a frame before its frame, as `timeline frames`
    does (SPECS 4.3 step 5), and the picture is turned from TV-range BT.709 to full-range RGB."""
    cmd = [tools.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    num, den = fps.numerator, fps.denominator
    for f in frames:
        if f > 0:
            cmd += ["-ss", f"{(f - 0.5) * den / num:.6f}"]
        cmd += ["-i", copy]
    vf = (f"scale={width}:-2:in_range=tv:out_range=pc:in_color_matrix=bt709:"
          f"flags=accurate_rnd+full_chroma_int,format=rgb24")
    for i, out in enumerate(out_paths):
        cmd += ["-map", f"{i}:v:0", "-an", "-sn", "-frames:v", "1", "-vf", vf, out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    missing = [o for o in out_paths if not os.path.isfile(o)]
    if r.returncode != 0 or missing:
        raise SheetError(f"ffmpeg could not cut stills from {copy}: {r.stderr.strip()[-400:] or 'no picture'}")


def thumbnails(work_dir: str, fps, copy: str, frames: list, width: int) -> dict:
    """{frame: PIL image} for the frames of a preview copy at `width` pixels wide. A still is made once and kept
    in <work>/index/thumbs/, so a sheet that only needs a new label does not read the video again."""
    from PIL import Image
    ident = hashlib.sha1(json.dumps(_identity(copy)).encode()).hexdigest()[:12]
    folder = os.path.join(work_dir, config.SCENE_INDEX_DIR, "thumbs", ident)
    os.makedirs(folder, exist_ok=True)
    path_of = lambda f: os.path.join(folder, f"{f}-{width}.jpg")
    todo = sorted({f for f in frames if not os.path.isfile(path_of(f))})
    if todo:
        scratch = tempfile.mkdtemp(prefix="stills-", dir=folder)
        try:
            pngs = [os.path.join(scratch, f"{f}.png") for f in todo]
            extract_frames(fps, copy, todo, width, pngs)
            for f, png in zip(todo, pngs):
                with Image.open(png) as im:
                    tmp = path_of(f) + ".tmp"
                    im.convert("RGB").save(tmp, format="JPEG", quality=92)
                os.replace(tmp, path_of(f))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    out = {}
    for f in set(frames):
        with Image.open(path_of(f)) as im:
            out[f] = im.convert("RGB")
    return out


# ── Drawing ─────────────────────────────────────────────────────

def _fit_text(draw, font, text: str, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "...", font=font) > max_w:
        text = text[:-1]
    return text + "..."


def compose(header: list, items: list, columns: int, top: int, tile_h: int, fonts: Fonts):
    """A sheet as a PIL image. `header` is [(style, text)], `items` is [(picture or None, label, dim)]."""
    from PIL import Image, ImageDraw
    rects = tile_rects(len(items), columns, config.SHEET_WIDTH, config.SHEET_GAP, tile_h,
                       config.SHEET_LABEL_HEIGHT, top)
    rows = max(1, math.ceil(len(items) / columns))
    height = top + config.SHEET_GAP + rows * (tile_h + config.SHEET_LABEL_HEIGHT + config.SHEET_GAP)
    img = Image.new("RGB", (config.SHEET_WIDTH, height), BACKGROUND)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, config.SHEET_WIDTH, top], fill=HEADER_BACKGROUND)
    y = 10
    for style, text in header:
        colour = WARN_COLOUR if text.startswith("restore it") or "removed" in text[:40] else TEXT_COLOUR
        if style == "mid":
            colour = WARN_COLOUR if text.startswith("restore it") else DIM_COLOUR
        d.text((config.SHEET_GAP + 2, y), _fit_text(d, fonts.get(style), text, config.SHEET_WIDTH - 20),
               font=fonts.get(style), fill=colour)
        y += LINE_HEIGHTS[style]
    for (x, y0, w, h), (pic, label, dim) in zip(rects, items):
        d.rectangle([x, y0, x + w - 1, y0 + h - 1], fill=(12, 12, 14))
        if pic is not None:
            if pic.height > h:
                pic = pic.resize((max(1, round(pic.width * h / pic.height)), h))
            if dim:
                from PIL import ImageEnhance
                pic = ImageEnhance.Brightness(pic).enhance(0.55)
            img.paste(pic, (x + (w - pic.width) // 2, y0 + (h - pic.height) // 2))
        d.text((x + 2, y0 + h + 4), _fit_text(d, fonts.get("small"), label, w - 4), font=fonts.get("small"),
               fill=DIM_COLOUR if dim else TEXT_COLOUR)
    return img


def _save_jpeg(img, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + ".tmp")
    img.save(tmp, format="JPEG", quality=config.SHEET_JPEG_QUALITY, optimize=True)
    os.replace(tmp, path)


def _load_manifest(folder: str) -> dict:
    try:
        with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == SHEET_VERSION:
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {"version": SHEET_VERSION, "sheets": {}, "overview": {}, "frames": {}}


def _save_manifest(folder: str, manifest: dict) -> None:
    fsutil.write_json_atomic(os.path.join(folder, "manifest.json"), manifest)


def _copy_of(tl, row: dict) -> str:
    return tl.data["sources"][row["source"]]["preview_copy"]


def _check_copies(tl, rows: list, only=None) -> None:
    missing = {}
    for r in rows:
        if only is not None and r["id"] not in only:
            continue
        copy = _copy_of(tl, r)
        if not os.path.isfile(copy):
            missing[r["source"]] = copy
    if missing:
        raise SheetError("The preview copy of " + "; ".join(f"clip {c} is missing ({p})" for c, p in missing.items())
                         + ". Make the preview copies first: python main.py convert --work DIR")


def _scene_key(tl, row: dict, fonts: Fonts) -> str:
    return _key(SHEET_VERSION, fonts.signature, config.SHEET_WIDTH, config.SHEET_COLUMNS, config.SHEET_GAP,
                config.SHEET_LABEL_HEIGHT, config.SHEET_FRAMES, config.SHEET_JPEG_QUALITY,
                row["id"], row["enabled"], row["film_start_frame"], str(tl.fps), row["source"],
                row["source_in"], row["source_out"], row["label"], _identity(_copy_of(tl, row)))


def draw_sheet(tl, work_dir: str, row: dict, fonts: Fonts):
    """One scene's sheet as an image."""
    tiles = sheet_tiles(row, tl.fps)
    pics = thumbnails(work_dir, tl.fps, _copy_of(tl, row), [t["frame"] for t in tiles], tile_width())
    items = [(pics[t["frame"]], tile_label(row, t), not row["enabled"]) for t in tiles]
    return compose(sheet_header(row), items, config.SHEET_COLUMNS, header_height(row), tile_height(), fonts)


def _overview_pages(rows: list) -> list:
    per = config.OVERVIEW_COLUMNS * config.OVERVIEW_ROWS
    return [rows[i:i + per] for i in range(0, len(rows), per)]


def _overview_tile(row: dict, fps) -> dict:
    return _tile(row, fps, row["length_frames"] // 2)


def _overview_label(row: dict) -> str:
    if row["enabled"]:
        return f"{row['id']}  {edits.clock(row['film_start_s'])}  {row['length_s']:.1f} s"
    return f"{row['id']}  removed  {row['length_s']:.1f} s"


def draw_overview(tl, work_dir: str, page_rows: list, number: int, pages: int, total: int, fonts: Fonts):
    tw = tile_width(config.OVERVIEW_COLUMNS)
    items = []
    by_copy = {}
    for r in page_rows:
        by_copy.setdefault(_copy_of(tl, r), []).append(r)
    pics = {}
    for copy, group in by_copy.items():
        frames = [_overview_tile(r, tl.fps)["frame"] for r in group]
        got = thumbnails(work_dir, tl.fps, copy, frames, tw)
        for r, f in zip(group, frames):
            pics[r["id"]] = got[f]
    for r in page_rows:
        items.append((pics[r["id"]], _overview_label(r), not r["enabled"]))
    header = [("big", f"Scenes {page_rows[0]['id']} to {page_rows[-1]['id']} of {total}   page {number} of {pages}"),
              ("mid", "One still from the middle of each scene, with its film time and length. "
                      "The sheet of a scene shows more.")]
    return compose(header, items, config.OVERVIEW_COLUMNS, overview_header_height(), overview_tile_height(), fonts)


def _overview_key(tl, page_rows: list, number: int, pages: int, total: int, fonts: Fonts) -> str:
    return _key(SHEET_VERSION, fonts.signature, config.SHEET_WIDTH, config.OVERVIEW_COLUMNS, config.OVERVIEW_ROWS,
                config.SHEET_GAP, config.SHEET_LABEL_HEIGHT, config.SHEET_JPEG_QUALITY, number, pages, total,
                str(tl.fps),
                [[r["id"], r["enabled"], r["film_start_frame"], r["source_in"], r["source_out"],
                  _identity(_copy_of(tl, r))] for r in page_rows])


def draw_sheets(tl, timeline_path: str, work_dir: str, log=None) -> dict:
    """Draw the sheet of every scene (removed scenes too) and the overview pages into
    <work>/index/<timeline name>/. A sheet whose key (the scene, its film place, its frames, its label, its
    clip's preview copy, the font) has not changed is kept. Returns the report: the ids drawn and reused, the
    overview pages drawn and reused, the page and place of each scene, the files deleted, warnings, seconds."""
    started = time.time()
    folder = index_dir(work_dir, timeline_path)
    rows = scene_rows(tl, timeline_path, work_dir, speech=False)
    _check_copies(tl, rows)
    fonts = _fonts()
    manifest = _load_manifest(folder)
    report = {"folder": folder, "drawn": [], "reused": [], "overview": {"drawn": [], "reused": []},
              "overview_of": {}, "deleted": [], "warnings": [fonts.warning] if fonts.warning else [], "seconds": 0.0}
    sheets = {}
    for n, row in enumerate(rows, start=1):
        key = _scene_key(tl, row, fonts)
        path = sheet_path(folder, row["id"])
        sheets[row["id"]] = key
        if manifest["sheets"].get(row["id"]) == key and os.path.isfile(path):
            report["reused"].append(row["id"])
            continue
        _save_jpeg(draw_sheet(tl, work_dir, row, fonts), path)
        manifest["sheets"][row["id"]] = key
        _save_manifest(folder, manifest)
        report["drawn"].append(row["id"])
        if log:
            log(f"[{n}/{len(rows)}] {row['id']} drawn")
    pages = _overview_pages(rows)
    wanted = set()
    for number, page_rows in enumerate(pages, start=1):
        name = overview_name(number)
        wanted.add(name)
        for position, r in enumerate(page_rows, start=1):
            report["overview_of"][r["id"]] = (name, position)
        key = _overview_key(tl, page_rows, number, len(pages), len(rows), fonts)
        path = os.path.join(folder, name)
        if manifest["overview"].get(name) == key and os.path.isfile(path):
            report["overview"]["reused"].append(name)
            continue
        _save_jpeg(draw_overview(tl, work_dir, page_rows, number, len(pages), len(rows), fonts), path)
        manifest["overview"][name] = key
        _save_manifest(folder, manifest)
        report["overview"]["drawn"].append(name)
    # files of scenes and pages that are no longer in the file
    for sid in [s for s in list(manifest["sheets"]) if s not in sheets]:
        path = sheet_path(folder, sid)
        if os.path.isfile(path):
            os.unlink(path)
            report["deleted"].append(f"sheets/{sid}.jpg")
        manifest["sheets"].pop(sid)
    for name in [n for n in list(manifest["overview"]) if n not in wanted]:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            os.unlink(path)
            report["deleted"].append(name)
        manifest["overview"].pop(name)
    _save_manifest(folder, manifest)
    report["seconds"] = round(time.time() - started, 2)
    return report


# ── A finer look inside one scene ───────────────────────────────

def parse_step(text: str) -> float:
    """'1s' -> 1.0, '0.5s' -> 0.5, '500ms' -> 0.5, '2' -> 2.0. Raises ValueError for anything else."""
    t = str(text).strip().lower()
    try:
        value = float(t[:-2]) / 1000.0 if t.endswith("ms") else float(t[:-1] if t.endswith("s") else t)
    except ValueError:
        raise ValueError(f"cannot read {text!r} as a step: write it like 1s, 0.5s or 500ms") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"the step must be more than zero, not {text!r}")
    return value


def frame_tiles(row: dict, fps, every_s: float, from_s=None, to_s=None) -> tuple:
    """The stills of a finer look: (tiles, lo, hi). From `from_s` to `to_s` (film seconds; the whole scene by
    default) a still every `every_s` seconds: the frame nearest to each time, as long as it belongs to the scene.
    A removed scene has no film time; its range is the whole scene, counted from its first frame."""
    fps = Fraction(fps)
    n = row["length_frames"]
    one_frame = float(1 / fps)
    if every_s < one_frame - 1e-9:
        raise SheetError(f"a step of {every_s:g} s is less than one frame ({one_frame:.4f} s)")
    if row["enabled"]:
        start = Fraction(row["film_start_frame"]) / fps
        end = start + Fraction(n) / fps
        lo = float(start) if from_s is None else float(from_s)
        hi = float(end) if to_s is None else float(to_s)
        if lo < float(start) - one_frame or hi > float(end) + one_frame or hi <= lo:
            raise SheetError(f"the range {lo:g} to {hi:g} s does not lie inside scene {row['id']}, which is in the film "
                             f"from {float(start):.2f} s to {float(end):.2f} s")
    else:
        if from_s is not None or to_s is not None:
            raise SheetError(f"scene {row['id']} is removed, so it has no film time; leave out --from and --to")
        start, lo, hi = Fraction(0), 0.0, float(Fraction(n) / fps)
    tiles, k = [], 0
    while True:
        t = lo + k * every_s
        if t >= hi - 1e-9:
            break
        offset = max(0, math.floor(Fraction(t - float(start)).limit_denominator(10 ** 6) * fps + Fraction(1, 2)))
        if offset >= n:
            break
        tiles.append(_tile(row, fps, offset))
        k += 1
        if k > config.FRAMES_MAX:
            break
    return tiles, lo, hi


def draw_frames(tl, timeline_path: str, work_dir: str, scene_id: str, every_s: float, from_s=None, to_s=None,
                log=None) -> dict:
    """Stills of one scene every `every_s` seconds, on pages of config.FRAMES_PER_PAGE. Returns {paths, tiles,
    count, drawn, reused, folder}."""
    if not isinstance(every_s, (int, float)) or not math.isfinite(every_s) or every_s <= 0:
        raise SheetError("the step must be more than zero seconds")
    rows = scene_rows(tl, timeline_path, work_dir, speech=False)
    row = next((r for r in rows if r["id"] == scene_id), None)
    if row is None:
        raise SheetError(f"there is no scene {scene_id!r}; the scenes are {rows[0]['id']} to {rows[-1]['id']}")
    tiles, lo, hi = frame_tiles(row, tl.fps, every_s, from_s, to_s)
    if len(tiles) > config.FRAMES_MAX:
        raise SheetError(f"every {every_s:g} s over {hi - lo:.1f} s would be more than {config.FRAMES_MAX} stills "
                         f"(the most is {config.FRAMES_MAX}); use a larger step, or --from and --to for a shorter stretch")
    if not tiles:
        raise SheetError(f"no still falls in scene {scene_id} between {lo:g} and {hi:g} s")
    _check_copies(tl, [row])
    fonts = _fonts()
    folder = index_dir(work_dir, timeline_path)
    manifest = _load_manifest(folder)
    per = config.FRAMES_PER_PAGE
    pages = [tiles[i:i + per] for i in range(0, len(tiles), per)]
    out = {"folder": folder, "paths": [], "tiles": tiles, "count": len(tiles), "drawn": [], "reused": [],
           "warnings": [fonts.warning] if fonts.warning else []}
    tag = "src" if not row["enabled"] else ""
    for number, page in enumerate(pages, start=1):
        name = f"{scene_id}-every{every_s:g}s-{tag}{lo:.2f}-{hi:.2f}-p{number}.jpg"
        path = os.path.join(folder, "frames", name)
        extra = f"every {every_s:g} s, page {number} of {len(pages)}"
        key = _key(SHEET_VERSION, fonts.signature, config.SHEET_WIDTH, config.SHEET_COLUMNS, config.SHEET_GAP,
                   config.SHEET_LABEL_HEIGHT, config.SHEET_JPEG_QUALITY, row["id"], row["enabled"],
                   row["film_start_frame"], str(tl.fps), row["source"], row["source_in"], row["source_out"],
                   row["label"], _identity(_copy_of(tl, row)), extra, [t["frame"] for t in page])
        out["paths"].append(path)
        if manifest["frames"].get(name) == key and os.path.isfile(path):
            out["reused"].append(name)
            continue
        pics = thumbnails(work_dir, tl.fps, _copy_of(tl, row), [t["frame"] for t in page], tile_width())
        items = [(pics[t["frame"]], tile_label(row, t, places=2), not row["enabled"]) for t in page]
        _save_jpeg(compose(sheet_header(row, extra), items, config.SHEET_COLUMNS, header_height(row),
                           tile_height(), fonts), path)
        manifest["frames"][name] = key
        _save_manifest(folder, manifest)
        out["drawn"].append(name)
    return out
