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
import json
import os
from fractions import Fraction

import config
import edits
import fsutil
import timeline
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


def scene_rows(tl, timeline_path: str, work_dir: str) -> list:
    """One dict per scene (segment) in the order of the file."""
    fps = tl.fps
    starts = tl.segment_film_starts()
    folder = index_dir(work_dir, timeline_path)
    spans = {s["id"]: s for s in words.cue_spans(tl.data)}
    cues_by_scene, titles_by_scene = {}, {}
    for c in (tl.data.get("subtitles") or {}).get("cues") or []:
        cues_by_scene.setdefault(c.get("anchor"), []).append(c)
    for t in tl.data.get("titles") or []:
        titles_by_scene.setdefault(t.get("anchor"), []).append(t)
    transcripts = {cid: _load_transcript(work_dir, cid) for cid in tl.data["sources"]}
    rows = []
    for seg in tl.data["segments"]:
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
