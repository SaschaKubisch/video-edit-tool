"""
Prompt edits on the scenes of the film (SPECS 10.3, 10.4; requirements P2 and P4).

Claude Code decides which scenes or which moment the owner means. These functions do the
arithmetic, on a copy of the timeline data; the caller (`main.cmd_scenes`) validates the copy and saves
it with a snapshot, so one prompt is one version that `timeline undo` takes back.

- `remove`: the scenes are switched off (`enabled: false`). They stay in the file with their cues, so
  `restore` brings them back. This is the reading of "cut out" that D14 gives until the owner answers.
- `keep`: every scene that is not listed is switched off. A listed scene that is removed stays removed.
- `restore`: the scenes are switched on again.
- `cut`: a moment inside one scene (film seconds) is taken out by splitting the scene. The first part keeps
  the scene's id, the second part is a new scene (`origin` `prompt`) right after it. At the new join the
  film's own sound crossfade applies (D8): the second part has no `audio_join` of its own.

Subtitles follow the rules of step 4 (SPECS 9.4): cues made by hand or by Whisper go with their picture; a cue
that is cut keeps its time at the cut edge and is dropped when less than config.SUBTITLE_CLIP_KEEP_SEC is
left; cues made from the scene's text box are made again across the first part, which keeps the text.
A cut that starts at the first frame of a scene or ends at its last frame is a trim and is done as the page
does a trim (`words.retime_segment`).

`report` compares two versions and `format_report` writes it for Claude Code and the owner.
"""

import copy
import difflib
import math
import re
from fractions import Fraction

import config
import edits
import words
from words import OperationError


class SceneEditError(OperationError):
    """A scene command that cannot be done. The message says why and what to do instead."""


# ── Reading the arguments ───────────────────────────────────────

def parse_ids(items) -> list:
    """Scene ids from words such as ["s001,s002", "s005"]: split at commas and spaces, repeated ids once, in order."""
    out = []
    for item in items if isinstance(items, (list, tuple)) else [items]:
        for part in re.split(r"[,\s]+", str(item)):
            if part and part not in out:
                out.append(part)
    if not out:
        raise SceneEditError("name at least one scene, for example s017 or s017,s018")
    return out


def _by_id(data) -> dict:
    return {s["id"]: s for s in data["segments"]}


def _check_ids(data, ids) -> None:
    known = _by_id(data)
    unknown = [i for i in ids if i not in known]
    if not unknown:
        return
    ordered = list(known)
    hint = f"The scenes are {ordered[0]} to {ordered[-1]}." if ordered else "The file has no scenes."
    close = [difflib.get_close_matches(u, ordered, n=1, cutoff=0.7) for u in unknown]
    if len(unknown) == 1 and close[0]:
        hint += f" Did you mean {close[0][0]}?"
    raise SceneEditError(f"there is no scene {', '.join(unknown)}. {hint}")


def _enabled(seg) -> bool:
    return seg.get("enabled", True)


# ── remove, keep, restore ───────────────────────────────────────

def remove(data: dict, ids, notes=None) -> dict:
    """A copy of `data` with the scenes switched off."""
    ids = parse_ids(ids)
    _check_ids(data, ids)
    out = copy.deepcopy(data)
    segs = _by_id(out)
    for sid in ids:
        if not _enabled(segs[sid]):
            if notes is not None:
                notes.append(f"{sid} was already removed")
            continue
        segs[sid]["enabled"] = False
    if not any(_enabled(s) for s in out["segments"]):
        raise SceneEditError("this would remove every scene, and the film needs at least one. "
                             "To keep some scenes and remove the rest, use: scenes keep ID[,ID...]")
    return out


def keep(data: dict, ids, notes=None) -> dict:
    """A copy of `data` in which every scene that is not listed is switched off."""
    ids = parse_ids(ids)
    _check_ids(data, ids)
    out = copy.deepcopy(data)
    for seg in out["segments"]:
        if seg["id"] in ids:
            if not _enabled(seg) and notes is not None:
                notes.append(f"{seg['id']} is removed and stays removed: keep does not restore. "
                             f"To put it back: scenes restore {seg['id']}")
        elif _enabled(seg):
            seg["enabled"] = False
    if not any(_enabled(s) for s in out["segments"]):
        raise SceneEditError("none of the listed scenes is in the film, so keep would leave an empty film. "
                             "Restore the scenes you want first: scenes restore ID[,ID...]")
    return out


def restore(data: dict, ids, notes=None) -> dict:
    """A copy of `data` with the scenes switched on."""
    ids = parse_ids(ids)
    _check_ids(data, ids)
    out = copy.deepcopy(data)
    segs = _by_id(out)
    for sid in ids:
        if _enabled(segs[sid]):
            if notes is not None:
                notes.append(f"{sid} is already in the film")
            continue
        segs[sid]["enabled"] = True
    return out


# ── cut ─────────────────────────────────────────────────────────

def _nearest_frame(seconds: float, fps: Fraction) -> int:
    """The frame nearest to a time; a time typed as 12.345 is read as that exact decimal."""
    return math.floor(Fraction(seconds).limit_denominator(10 ** 6) * fps + Fraction(1, 2))


def _clip_cue(cue, lo: float, hi: float, shift: float, fps_frame) -> bool:
    """Cut `cue` to the range [lo, hi] of its scene and move it by `shift`. Returns False when the
    cue should be dropped: nothing left, or less than config.SUBTITLE_CLIP_KEEP_SEC of a cue that was cut."""
    a, b = cue["start_s"], cue["end_s"]
    ca, cb = max(a, lo), min(b, hi)
    if cb - ca <= 1e-9:
        return False
    if (ca > a or cb < b) and cb - ca < config.SUBTITLE_CLIP_KEEP_SEC - 1e-9:
        return False
    cue["start_s"], cue["end_s"] = fps_frame(ca - shift), fps_frame(cb - shift)
    return True


def cut(data: dict, scene_id: str, from_s: float, to_s: float, notes=None) -> dict:
    """A copy of `data` with the film seconds `from_s` to `to_s` taken out of the scene `scene_id`."""
    _check_ids(data, [scene_id])
    for v, name in ((from_s, "--from"), (to_s, "--to")):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise SceneEditError(f"{name} must be a number of film seconds")
    out = copy.deepcopy(data)
    seg = _by_id(out)[scene_id]
    if not _enabled(seg):
        raise SceneEditError(f"scene {scene_id} is removed, so it has no film time to cut at. "
                             f"Put it back first: scenes restore {scene_id}")
    fps = words._fps(out)
    row = next(r for r in words.film_segments(out) if r["seg"]["id"] == scene_id)
    old_in, old_out = seg["in"], seg["out"]
    f0, f1 = _nearest_frame(from_s, fps), _nearest_frame(to_s, fps)
    a = old_in + (f0 - row["start_frame"])
    b = old_in + (f1 - row["start_frame"])
    span = (f"scene {scene_id} is in the film from {row['start_s']:.2f} s to "
            f"{row['start_s'] + row['length_s']:.2f} s")
    if f1 <= f0:
        raise SceneEditError(f"--to ({to_s:g}) must come after --from ({from_s:g}); {span}")
    if a < old_in or b > old_out:
        raise SceneEditError(f"the moment {from_s:g} to {to_s:g} s is not inside scene {scene_id}: {span}. "
                             f"Film seconds count from the start of the film.")
    if a == old_in and b == old_out:
        raise SceneEditError(f"that is the whole of scene {scene_id}. To take it out of the film use: "
                             f"scenes remove {scene_id}")
    n_frames = b - a
    if notes is not None:
        notes.append(f"removed moment: film {edits.clock(float(Fraction(f0) / fps))} - "
                     f"{edits.clock(float(Fraction(f1) / fps))} ({float(Fraction(n_frames) / fps):.2f} s, "
                     f"{n_frames} frames), frames {a}-{b} of clip {seg['source']}")

    if a == old_in or b == old_out:                                   # at an end of the scene: a trim
        if a == old_in:
            seg["in"] = b
        else:
            seg["out"] = a
        words.retime_segment(out, scene_id, old_in, old_out, notes)
        if notes is not None:
            notes.append("the cut touches an end of the scene, so the scene is shortened and no new scene is made")
        return out

    # a moment inside the scene: the scene is split
    new_id = words.next_id(out["segments"], "s")
    second = copy.deepcopy(seg)
    second.pop("text", None)
    second.pop("audio_join", None)                   # the join into the second part is the film's own (D8)
    second.update({"id": new_id, "in": b, "out": old_out, "origin": "prompt"})
    if seg.get("label"):
        second["label"] = f"{seg['label']} (after the cut)"
    second["note"] = f"the part of {scene_id} after a cut of film seconds {from_s:g} to {to_s:g}"
    seg["out"] = a
    out["segments"].insert(out["segments"].index(seg) + 1, second)

    # the cues of the scene
    subs = out.get("subtitles")
    cues = [c for c in (subs or {}).get("cues", []) if c.get("anchor") == scene_id]
    if cues:
        subs["cues"] = [c for c in subs["cues"] if c.get("anchor") != scene_id]
        text_cues = [c for c in cues if c.get("origin") == "text"]
        regenerate = bool(seg.get("text")) and bool(text_cues) and not any(words._adjusted(c) for c in text_cues)
        mine = [c for c in cues if not (regenerate and c in text_cues)]
        ra, rb = float(Fraction(a - old_in) / fps), float(Fraction(b - old_in) / fps)
        length = float(Fraction(old_out - old_in) / fps)
        frame = lambda x: words._frame_s(out, max(0.0, x))
        placed = []
        for c in mine:
            in_a = max(0.0, min(c["end_s"], ra) - c["start_s"])
            in_b = max(0.0, c["end_s"] - max(c["start_s"], rb))
            if in_a <= 0 and in_b <= 0:
                continue
            if in_a >= in_b:
                keep_it = _clip_cue(c, 0.0, ra, 0.0, frame)
            else:
                keep_it = _clip_cue(c, rb, length, rb, frame)
                c["anchor"] = new_id
            if keep_it:
                placed.append(c)
        if regenerate:
            words.regenerate_scene(out, scene_id, notes=notes)
        subs["cues"].extend(placed)
        words.sort_cues(out)
    return out


# ── The report ──────────────────────────────────────────────────

def _scene(row) -> dict:
    seg = row["seg"]
    return {"id": seg["id"], "label": seg.get("label", ""), "source": seg["source"],
            "film_start_s": row["start_s"], "film_end_s": row["start_s"] + row["length_s"],
            "length_s": row["length_s"], "in": seg["in"], "out": seg["out"]}


def _cues(data) -> dict:
    return {c["id"]: c for c in (data.get("subtitles") or {}).get("cues", [])}


def report(before, after, notes=(), warnings=()) -> dict:
    """What differs between two versions of the timeline (Timeline objects): the scenes that leave the
    film, enter it, are shortened or are new; the film length; the cues; the titles placed by film time."""
    fps = before.fps
    b = {r["seg"]["id"]: r for r in words.film_segments(before.data)}
    a = {r["seg"]["id"]: r for r in words.film_segments(after.data)}
    known_before = {s["id"] for s in before.data["segments"]}
    left = [_scene(b[i]) for i in b if i not in a]
    entered = [_scene(a[i]) for i in a if i not in b and i in known_before]
    new = [_scene(a[i]) for i in a if i not in known_before]
    changed = [{"id": i, "before": _scene(b[i]), "after": _scene(a[i])} for i in b if i in a
               and (b[i]["seg"]["in"], b[i]["seg"]["out"]) != (a[i]["seg"]["in"], a[i]["seg"]["out"])]
    cb, ca = _cues(before.data), _cues(after.data)
    spans_b = {s["id"]: s for s in words.cue_spans(before.data)}
    spans_a = {s["id"]: s for s in words.cue_spans(after.data)}
    cues = {
        "before": len(spans_b), "after": len(spans_a),
        "hidden": sorted(i for i in spans_b if i not in spans_a and i in ca),
        "removed": sorted(i for i in cb if i not in ca),
        "back": sorted(i for i in spans_a if i not in spans_b and i in cb),
        "changed": sorted(i for i in spans_b if i in spans_a and cb[i] != ca[i]),
        "new": sorted(i for i in ca if i not in cb),
        "text": {i: (cb.get(i) or ca.get(i))["text"] for i in
                 set(i for i in spans_b if i not in spans_a) | set(i for i in spans_a if i not in spans_b)
                 | {i for i in spans_b if i in spans_a and cb[i] != ca[i]}},
        "scene": {i: (cb.get(i) or ca.get(i)).get("anchor") for i in set(cb) | set(ca)},
    }
    # the film second from which things have moved: the start of a scene that leaves or enters, the start of a
    # scene whose start was trimmed, else the new end of a scene that was shortened at its end
    touched = [s["film_start_s"] for s in left] + [s["film_start_s"] for s in entered] \
        + [c["before"]["film_start_s"] if c["before"]["in"] != c["after"]["in"]
           else min(c["before"]["film_end_s"], c["after"]["film_end_s"]) for c in changed]
    titles = []
    if touched:
        first = min(touched)
        titles = [{"id": t["id"], "text": t["text"], "start_s": t["start_s"], "end_s": t["end_s"]}
                  for t in after.data.get("titles") or []
                  if t.get("anchor", "film") == "film" and t["end_s"] > first - 1e-9]
    fb, fa = before.film_length_frames(), after.film_length_frames()
    return {
        "film_before_s": float(Fraction(fb) / fps), "film_after_s": float(Fraction(fa) / fps),
        "frames_before": fb, "frames_after": fa,
        "scenes_before": len(b), "scenes_after": len(a),
        "left": left, "entered": entered, "new": new, "changed": changed,
        "cues": cues, "titles": titles, "notes": list(notes), "warnings": list(warnings),
        "no_change": before.dumps() == after.dumps(),
    }


def new_warnings(old_findings, new_findings) -> list:
    """The warnings of the new version that the old one did not have: [text]."""
    seen = {(f.rule, f.path, f.message) for f in old_findings if f.level == "warning"}
    return [f"{f.rule} {f.path}: {f.message}" for f in new_findings
            if f.level == "warning" and (f.rule, f.path, f.message) not in seen]


def _span(s) -> str:
    return f"{edits.clock(s['film_start_s'])} - {edits.clock(s['film_end_s'])}"


def _scene_line(s, was=False) -> str:
    label = f'  "{s["label"]}"' if s["label"] else ""
    return (f"  {s['id']}  {'was ' if was else ''}{_span(s)}  {s['length_s']:.1f} s  clip {s['source']}, "
            f"frames {s['in']}-{s['out']}{label}")


def _ids(ids, limit=12) -> str:
    shown = ", ".join(ids[:limit])
    return shown + (f" (+{len(ids) - limit} more)" if len(ids) > limit else "")


def format_report(rep: dict, command: str, dry_run: bool) -> str:
    """The report as text: what Claude Code shows the owner before and after a change."""
    lines = [f"{command}" + ("   [dry run: nothing was written]" if dry_run else "")]
    if rep["no_change"]:
        lines.append("No change: the film is as it was.")
        lines += [f"  note: {n}" for n in rep["notes"]]
        return "\n".join(lines) + "\n"
    delta = rep["film_after_s"] - rep["film_before_s"]
    lines.append(f"Film: {edits.clock(rep['film_before_s'])} -> {edits.clock(rep['film_after_s'])}  "
                 f"({delta:+.1f} s, {rep['frames_after'] - rep['frames_before']:+d} frames)   "
                 f"scenes in the film: {rep['scenes_before']} -> {rep['scenes_after']}")
    if rep["left"]:
        lines.append("Leaving the film (they stay in the file; `scenes restore ID` brings them back):")
        lines += [_scene_line(s, was=True) for s in rep["left"]]
    if rep["entered"]:
        lines.append("Back in the film:")
        lines += [_scene_line(s) for s in rep["entered"]]
    if rep["changed"]:
        lines.append("Shortened:")
        for c in rep["changed"]:
            b, a = c["before"], c["after"]
            lines.append(f"  {c['id']}  {_span(b)} ({b['length_s']:.1f} s) -> {_span(a)} ({a['length_s']:.1f} s)  "
                         f"frames {b['in']}-{b['out']} -> {a['in']}-{a['out']}")
    if rep["new"]:
        lines.append("New scene:")
        lines += [_scene_line(s) for s in rep["new"]]
    c = rep["cues"]
    if any(c[k] for k in ("hidden", "removed", "back", "changed", "new")) or c["before"] != c["after"]:
        lines.append(f"Subtitles in the film: {c['before']} -> {c['after']}.")
        if c["hidden"]:
            lines.append(f"  hidden with their scene (they stay in the file): {_ids(c['hidden'])}")
        if c["back"]:
            lines.append(f"  back with their scene: {_ids(c['back'])}")
        if c["changed"]:
            lines.append(f"  cut or moved with their picture: {_ids(c['changed'])}")
        if c["removed"]:
            lines.append("  removed from the file: " + _ids([f"{i} \"{c['text'].get(i, '')}\"" for i in c["removed"]]))
        if c["new"]:
            lines.append(f"  made again from the scene text: {_ids(c['new'])}")
    if rep["titles"]:
        lines.append("Titles placed by film time (they do not move with the scenes, so they may now be over "
                     "other scenes): " + "; ".join(f"{t['id']} {t['start_s']:g}-{t['end_s']:g} s \"{t['text']}\""
                                                   for t in rep["titles"]))
    if rep["notes"]:
        lines.append("Notes:")
        lines += [f"  {n}" for n in rep["notes"]]
    if rep["warnings"]:
        lines.append("New warnings from the validator:")
        lines += [f"  {w}" for w in rep["warnings"]]
    return "\n".join(lines) + "\n"
