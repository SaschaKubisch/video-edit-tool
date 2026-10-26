"""
Assembly: build the timeline file from the matched clips and the analysis (SPECS 7.5).

Owner decisions that shape it:
  D6  one clap per clip, at its start. The film starts CLAP_GUARD_SEC (0.5 s) after
      it. A clip with no clap starts at its first steady frame and is listed.
  D7  every steady scene of STABLE_MIN_RUN_SEC (2 s) or longer is a scene of its own;
      the movement between scenes is dropped; shorter scenes are dropped. A clip
      with no steady scene is left out and listed ("Leave it out, list it"); the
      owner can add it back by hand.
  D8  a hard cut for the picture; the sound crossfades over 0.5 s at every join; the
      film fades in over 1 s and out over 2 s.
  Order: recording order (the natural order of the file names), or the project's
  selection when there is one.

`build` is a pure function of the clips and their analysis results. `assemble_project`
reads the files of a project and writes the timeline and the report.
"""

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import clap
import config
import fsutil
import safety
import timeline


class AssemblyError(Exception):
    """The timeline cannot be assembled."""


@dataclass
class AssembleSettings:
    frame: tuple = config.FILM_FRAME
    fit: str = config.FILM_FIT
    fade_in_s: float = config.FILM_FADE_IN_SEC
    fade_out_s: float = config.FILM_FADE_OUT_SEC
    join_s: float = config.FILM_JOIN_SEC
    join_curve: str = config.FILM_JOIN_CURVE
    min_run_sec: float = config.STABLE_MIN_RUN_SEC
    clap_guard_sec: float = config.CLAP_GUARD_SEC
    sample_rate: int = config.AUDIO_SAMPLE_RATE
    title: str = ""

    def as_dict(self) -> dict:
        return {"frame": list(self.frame), "fit": self.fit, "fade_in_s": self.fade_in_s,
                "fade_out_s": self.fade_out_s, "join_s": self.join_s,
                "join_curve": self.join_curve, "min_run_sec": self.min_run_sec,
                "clap_guard_sec": self.clap_guard_sec, "sample_rate": self.sample_rate}


@dataclass
class ClipInput:
    """One clip with everything assembly needs to know about it."""
    stem: str
    original_video: str
    audio: str
    offset_s: float
    preview_copy: str = None
    frames: int = None
    fps: dict = None
    size: dict = None
    lut_sha256: str = None
    copy_ok: bool = False           # a finished preview copy exists (status done)
    clap: dict = None               # clap.json, or None when not analysed
    scenes: dict = None             # scenes.json, or None when not analysed
    no_copy_reason: str = ""


@dataclass
class Assembly:
    data: dict
    report: dict


def _source_id(stem: str, taken: set) -> str:
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", stem)
    base, n = sid, 2
    while sid in taken:
        sid, n = f"{base}_{n}", n + 1
    return sid


def _floor_ms(x: float) -> float:
    return max(0.0, math.floor(x * 1000 + 1e-6) / 1000)


def fit_fades(lengths_s, join_s: float, fade_in: float, fade_out: float):
    """Shorten the film fades where the first or last scene is too short for them
    (rule V11). Returns (fade_in, fade_out, warnings)."""
    warnings = []
    fi, fo = fade_in, fade_out
    if len(lengths_s) == 1:
        length = lengths_s[0]
        if fi + fo > length:
            factor = length / (fi + fo)
            fi, fo = _floor_ms(fi * factor), _floor_ms(fo * factor)
            warnings.append(
                f"the only scene is {length:.2f} s long: fade-in shortened from {fade_in:g} s "
                f"to {fi:g} s and fade-out from {fade_out:g} s to {fo:g} s")
        return fi, fo, warnings
    room_in = lengths_s[0] - join_s / 2
    if fi > room_in:
        fi = _floor_ms(room_in)
        warnings.append(f"the first scene is {lengths_s[0]:.2f} s long: fade-in shortened "
                        f"from {fade_in:g} s to {fi:g} s")
    room_out = lengths_s[-1] - join_s / 2
    if fo > room_out:
        fo = _floor_ms(room_out)
        warnings.append(f"the last scene is {lengths_s[-1]:.2f} s long: fade-out shortened "
                        f"from {fade_out:g} s to {fo:g} s")
    return fi, fo, warnings


def _fps_tuple(fps):
    return (fps["num"], fps["den"])


def build(clips, base_lut: dict, settings: AssembleSettings = None) -> Assembly:
    """Assemble the timeline of `clips`, in the order given."""
    st = settings or AssembleSettings()
    entries, segments, sources = [], [], {}
    film_fps = None
    taken = set()
    n_seg = 0

    for c in clips:
        entry = {"clip": c.stem, "status": "left_out", "reason": "", "seconds": None,
                 "kept_seconds": 0.0, "segments": [], "clap": None, "earliest_frame": None,
                 "scenes_found": 0, "scenes_dropped": [], "review": False,
                 "review_reasons": [], "original_video": c.original_video,
                 "preview_copy": c.preview_copy, "frames": c.frames}
        entries.append(entry)
        if not c.copy_ok or not c.preview_copy:
            entry["reason"] = c.no_copy_reason or "no finished preview copy (run convert)"
            continue
        fps = _fps_tuple(c.fps)
        entry["seconds"] = round(c.frames * fps[1] / fps[0], 3)
        if film_fps is None:
            film_fps = fps
        if fps[0] * film_fps[1] != film_fps[0] * fps[1]:
            entry["reason"] = (f"frame rate {fps[0]}/{fps[1]} differs from the film's "
                               f"{film_fps[0]}/{film_fps[1]}")
            continue

        # The clap (D6). With no clap the clip is kept from its first steady frame.
        reasons = []
        earliest = 0
        if c.clap is None:
            reasons.append("the clap was not analysed (run analyse --clap); no clap cut")
            entry["clap"] = {"result": None, "time_s": None}
        else:
            t = c.clap.get("video_time_s")
            entry["clap"] = {"result": c.clap.get("result"), "time_s": t}
            if t is None:
                reasons.append("no clap found: the clip starts at its first steady frame")
            else:
                earliest = clap.first_frame_after(t, fps[0], fps[1], st.clap_guard_sec)
            if c.clap.get("review"):
                reasons.extend(c.clap.get("reasons") or ["the clap result is marked for review"])
        entry["earliest_frame"] = earliest

        # The steady scenes (D7).
        min_frames = st.min_run_sec * fps[0] / fps[1]
        picks = []          # (run number, in, out, score, low_confidence)
        if c.scenes is None:
            reasons.append("the steady scenes were not analysed (run analyse --scenes): "
                           "the clip after the clap is one scene, marked unanalysed")
            if c.frames - earliest >= min_frames - 1e-9:
                picks.append((0, earliest, c.frames, None, False))
            else:
                entry["scenes_dropped"].append(
                    {"scene": 0, "in": earliest, "out": c.frames,
                     "reason": f"shorter than {st.min_run_sec:g} s after the clap cut"})
        else:
            runs = c.scenes.get("runs", [])
            entry["scenes_found"] = len(runs)
            for k, r in enumerate(runs, start=1):
                a, b = max(r["in"], earliest), min(r["out"], c.frames)
                if b - a >= min_frames - 1e-9:
                    picks.append((k, a, b, r.get("score"), bool(r.get("low_confidence"))))
                elif r["out"] <= earliest:
                    entry["scenes_dropped"].append(
                        {"scene": k, "in": r["in"], "out": r["out"],
                         "reason": "ends before the clap cut"})
                else:
                    entry["scenes_dropped"].append(
                        {"scene": k, "in": r["in"], "out": r["out"],
                         "reason": f"shorter than {st.min_run_sec:g} s after the clap cut"})
        if any(p[4] for p in picks):
            reasons.append("some steps of a scene could not be measured (low confidence)")

        if not picks:
            entry["reason"] = (f"no steady scene of {st.min_run_sec:g} s or longer"
                               + (" after the clap cut" if entry["scenes_dropped"] else ""))
            entry["review"], entry["review_reasons"] = True, reasons
            continue

        sid = _source_id(c.stem, taken)
        taken.add(sid)
        sources[sid] = {
            "original_video": c.original_video, "preview_copy": c.preview_copy,
            "audio": c.audio, "audio_offset_s": c.offset_s, "frames": c.frames,
            "fps": dict(c.fps), "size": dict(c.size), "copy_lut_sha256": c.lut_sha256}
        kept = 0
        for k, a, b, score, low in picks:
            n_seg += 1
            seg_id = f"s{n_seg:03d}"
            if k == 0:
                label, note = f"{c.stem} (not analysed)", "unanalysed: the clip after the clap"
            else:
                label = f"{c.stem} scene {k}"
                parts = []
                if earliest and a == earliest:
                    parts.append(f"starts {st.clap_guard_sec:g} s after the clap at "
                                 f"{entry['clap']['time_s']:.2f} s")
                elif a != 0 and earliest == 0 and entry["clap"]["time_s"] is None:
                    parts.append("no clap: starts at the first steady frame")
                parts.append(f"steady scene {k} of {entry['scenes_found']}"
                             + (f", score {score:.2f}" if score is not None else ""))
                note = "; ".join(parts)
            segments.append({"id": seg_id, "source": sid, "in": a, "out": b,
                             "label": label, "origin": "assembly", "note": note})
            entry["segments"].append(seg_id)
            kept += b - a
        entry["status"] = "used"
        entry["kept_seconds"] = round(kept * fps[1] / fps[0], 3)
        entry["review"] = bool(reasons)
        entry["review_reasons"] = reasons

    if not segments:
        raise AssemblyError("Nothing to assemble: no clip has a steady scene of "
                            f"{st.min_run_sec:g} s or longer with a finished preview copy.")

    fps_obj = {"num": film_fps[0], "den": film_fps[1]}
    lengths = [(s["out"] - s["in"]) * film_fps[1] / film_fps[0] for s in segments]
    fade_in, fade_out, warnings = fit_fades(lengths, st.join_s, st.fade_in_s, st.fade_out_s)
    film = {"fps": fps_obj, "frame": {"width": st.frame[0], "height": st.frame[1]},
            "fit": st.fit, "fade_in_s": fade_in, "fade_out_s": fade_out,
            "audio_join": {"duration_s": st.join_s, "curve": st.join_curve}}
    if st.title:
        film = {"title": st.title, **film}
    base = {"mode": "lut", "path": base_lut["path"], "sha256": base_lut["sha256"]} \
        if base_lut else {"mode": "none"}
    data = {"version": 1, "kind": "film", "film": film, "sources": sources,
            "segments": segments, "look": {"base": base},
            "audio": {"sample_rate": st.sample_rate, "loudness": {"mode": "off"}}}

    findings = timeline.validate(data, file_checks=False)
    errors = [f for f in findings if f.level == timeline.ERROR]
    if errors:
        raise AssemblyError("The assembled timeline breaks a rule: " +
                            "; ".join(f"{f.rule} at {f.path}: {f.message}" for f in errors))

    total_frames = sum(s["out"] - s["in"] for s in segments)
    report = {
        "settings": st.as_dict(),
        "film": {"clips_used": sum(1 for e in entries if e["status"] == "used"),
                 "segments": len(segments), "frames": total_frames,
                 "seconds": round(total_frames * film_fps[1] / film_fps[0], 3),
                 "fade_in_s": fade_in, "fade_out_s": fade_out,
                 "fades_shortened": bool(warnings), "warnings": warnings,
                 "left_out": [e["clip"] for e in entries if e["status"] == "left_out"]},
        "clips": entries}
    return Assembly(data, report)


# ── The text report ─────────────────────────────────────────────

def format_report(report: dict) -> str:
    film = report["film"]
    lines = [f"Assembly: {film['segments']} segments from {film['clips_used']} clips, "
             f"{film['seconds']:.1f} s, fade-in {film['fade_in_s']:g} s, "
             f"fade-out {film['fade_out_s']:g} s.", ""]
    head = f"{'clip':22} {'status':9} {'clip s':>7} {'kept s':>7} {'scenes':>6}  note"
    lines += [head, "-" * len(head)]
    for e in report["clips"]:
        note = e["reason"] if e["status"] == "left_out" else ""
        if e["review"]:
            note = "; ".join(x for x in [note] + e["review_reasons"] if x)
        sec = "-" if e["seconds"] is None else f"{e['seconds']:.1f}"
        lines.append(f"{e['clip']:22} {e['status'].replace('_', ' '):9} {sec:>7} "
                     f"{e['kept_seconds']:>7.1f} {len(e['segments']):>6}  {note}")
    lines.append("")
    if film["left_out"]:
        lines.append("Clips left out of the film (add one back by hand as a segment with "
                     "origin \"manual\"):")
        for stem in film["left_out"]:
            reason = next(e["reason"] for e in report["clips"] if e["clip"] == stem)
            lines.append(f"  {stem}: {reason}")
    else:
        lines.append("No clip was left out.")
    no_clap = [e["clip"] for e in report["clips"]
               if e["status"] == "used" and e["clap"] and e["clap"]["time_s"] is None]
    if no_clap:
        lines.append("Clips without a clap (they start at their first steady frame): "
                     + ", ".join(no_clap))
    for w in film["warnings"]:
        lines.append(f"Warning: {w}")
    return "\n".join(lines) + "\n"


# ── Over a project ──────────────────────────────────────────────

class TimelineExists(AssemblyError):
    """The timeline file exists and --replace was not given."""


def _load_json(path):
    import json
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def clip_inputs(project, work_dir: str, store_, limit=None, only=None) -> list:
    """The clips of the project in film order, with the results of the analysis.
    Film order is the project's selection when there is one, else recording order
    (the natural order of the file names)."""
    import matcher
    matches = [m if hasattr(m, "video_path") else project.get_match(m["index"])
               for m in project.matches]
    if project.selection:
        ordered = [project.get_match(i) for i in project.selection]
        matches = [m for m in ordered if m is not None]
    else:
        matches = sorted(matches, key=lambda m: matcher._natural_key(Path(m.video_path).name))
    if only:
        wanted = set(only)
        matches = [m for m in matches if Path(m.video_path).stem in wanted]
    if limit:
        matches = matches[:limit]
    inputs = []
    for m in matches:
        stem = Path(m.video_path).stem
        entry = store_.clips.get(stem)
        reason = ""
        ok = False
        if not entry:
            reason = "no entry in conversion.json (run convert)"
        elif entry.get("status") != "done":
            reason = f"the preview copy is not finished (status {entry.get('status')}); run convert"
        elif not os.path.isfile(entry.get("preview_copy", "")):
            reason = "the preview copy file is missing; run convert"
        else:
            ok = True
        e = entry or {}
        inputs.append(ClipInput(
            stem=stem, original_video=m.video_path, audio=m.audio_path, offset_s=m.offset,
            preview_copy=e.get("preview_copy") if ok else None, frames=e.get("frames"),
            fps=e.get("fps"), size=e.get("size"), lut_sha256=e.get("lut_sha256"),
            copy_ok=ok, no_copy_reason=reason,
            clap=_load_json(os.path.join(work_dir, "analysis", stem, "clap.json")),
            scenes=_load_json(os.path.join(work_dir, "analysis", stem, "scenes.json"))))
    return inputs


def assemble_project(project, work_dir: str, output: str, replace: bool = False,
                     limit: int = None, only=None, settings: AssembleSettings = None,
                     report_name: str = "assembly-report", file_checks: bool = True,
                     log=print) -> Assembly:
    """Build the timeline of the project's clips and write it to `output`, and the
    report to work_dir/reports/<report_name>.json and .txt. Reads conversion.json
    and the analysis files; writes only the timeline, its snapshot and the reports."""
    import store
    from convert import ConversionStore

    folders = safety.originals_folders(project)
    safety.assert_not_inside(work_dir, folders)
    safety.assert_not_inside(output, folders)
    if os.path.exists(output) and not replace:
        raise TimelineExists(f"{output} exists. Give --replace to assemble again; the old "
                             "file is kept as a snapshot.")
    conv = ConversionStore.load(os.path.join(work_dir, config.CONVERSION_FILE))
    base_lut = conv.data.get("base_lut")
    if not base_lut:
        raise AssemblyError("conversion.json has no base_lut: the preview copies were made "
                            "without a record of the LUT. Run convert.")
    clips = clip_inputs(project, work_dir, conv, limit=limit, only=only)
    result = build(clips, base_lut, settings)
    findings = timeline.validate(result.data, file_checks=file_checks)
    errors = [f for f in findings if f.level == timeline.ERROR]
    if errors:
        raise AssemblyError("The timeline is not valid, so it was not written: " +
                            "; ".join(f"{f.rule} at {f.path}: {f.message}" for f in errors))
    result.report["warnings"] = [f.as_dict() for f in findings if f.level == timeline.WARNING]
    store.save(timeline.Timeline(result.data), output)
    reports = os.path.join(work_dir, "reports")
    fsutil.write_json_atomic(os.path.join(reports, f"{report_name}.json"), result.report)
    fsutil.write_text_atomic(os.path.join(reports, f"{report_name}.txt"),
                             format_report(result.report))
    log(format_report(result.report))
    return result
