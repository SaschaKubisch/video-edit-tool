"""
Editing the timeline file from the page (SPECS 4.4) and printing it (SPECS 4.3).

The page does not send whole files. It sends a list of operations with the hash of the
version it started from. `apply_operations` applies them, in order, to a copy of the
data; the caller validates and saves the result (store.save).

    {"op": "trim_in",     "segment": "s001", "frames": 30}     positive: the start moves later
    {"op": "trim_out",    "segment": "s001", "frames": -30}    positive: the end moves later
    {"op": "set_enabled", "segment": "s001", "enabled": false}
    {"op": "move",        "segment": "s001", "to_index": 0}
    {"op": "set_label",   "segment": "s001", "label": "text"}
    {"op": "set_offset",  "source": "A002_..._C047", "offset_s": 0.02}   the owner's offset, seconds
    {"op": "reset_offset", "source": "A002_..._C047"}                    back to the one from matching

`summary` describes the film for the page and for `timeline summary`.
"""

import copy

import timeline


class OperationError(Exception):
    """An operation is unknown or malformed. The page gets HTTP 400."""


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _segment(data: dict, op: dict) -> dict:
    sid = op.get("segment")
    if not isinstance(sid, str):
        raise OperationError(f"operation {op.get('op')!r} needs the field 'segment' (a segment id)")
    for s in data["segments"]:
        if s.get("id") == sid:
            return s
    raise OperationError(f"there is no segment {sid!r}")


def _trim(data, op, key):
    if not _is_int(op.get("frames")):
        raise OperationError(f"operation {op['op']!r} needs 'frames', a whole number")
    seg = _segment(data, op)
    seg[key] += op["frames"]


def _set_enabled(data, op):
    if not isinstance(op.get("enabled"), bool):
        raise OperationError("operation 'set_enabled' needs 'enabled', true or false")
    _segment(data, op)["enabled"] = op["enabled"]


def _move(data, op):
    seg = _segment(data, op)
    to = op.get("to_index")
    if not _is_int(to) or not 0 <= to < len(data["segments"]):
        raise OperationError(f"operation 'move' needs 'to_index', a whole number from 0 to "
                             f"{len(data['segments']) - 1}")
    data["segments"].remove(seg)
    data["segments"].insert(to, seg)


def _set_label(data, op):
    if not isinstance(op.get("label"), str):
        raise OperationError("operation 'set_label' needs 'label', text")
    _segment(data, op)["label"] = op["label"]


def _clip(data: dict, op: dict) -> dict:
    sid = op.get("source")
    if not isinstance(sid, str):
        raise OperationError(f"operation {op.get('op')!r} needs the field 'source' (a clip id)")
    sources = data.get("sources")
    if not isinstance(sources, dict) or not isinstance(sources.get(sid), dict):
        raise OperationError(f"there is no clip {sid!r}")
    return sources[sid]


def _set_offset(data, op):
    value = op.get("offset_s")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise OperationError("operation 'set_offset' needs 'offset_s', a number of seconds")
    _clip(data, op)["audio_offset_hand_s"] = value


def _reset_offset(data, op):
    _clip(data, op).pop("audio_offset_hand_s", None)


OPERATIONS = {
    "trim_in": lambda d, o: _trim(d, o, "in"),
    "trim_out": lambda d, o: _trim(d, o, "out"),
    "set_enabled": _set_enabled,
    "move": _move,
    "set_label": _set_label,
    "set_offset": _set_offset,
    "reset_offset": _reset_offset,
}


def apply_operations(data: dict, operations) -> dict:
    """A copy of `data` with the operations applied in order. Raises OperationError
    (and changes nothing) if any operation is unknown or malformed."""
    if not isinstance(operations, list) or not operations:
        raise OperationError("'operations' must be a list with at least one operation")
    out = copy.deepcopy(data)
    for op in operations:
        if not isinstance(op, dict):
            raise OperationError("each operation must be an object")
        name = op.get("op")
        if not isinstance(name, str):
            raise OperationError("each operation needs the field 'op' (its name)")
        if name not in OPERATIONS:
            raise OperationError(f"unknown operation {name!r}; known: {', '.join(OPERATIONS)}")
        OPERATIONS[name](out, op)
    return out


# ── Settings written by commands (not page operations) ──────────

LOUDNESS_MODES = ("off", "segment_then_film", "film_only")


def set_loudness(data: dict, mode: str, target_lufs=None, true_peak_dbtp=None, max_gain_db=None) -> dict:
    """A copy of `data` with `audio.loudness` set. For the modes that level, the values the
    owner decided on (config.LOUDNESS_*) fill what is not given; for `off` only the mode is
    written. The caller validates and saves."""
    import config
    if mode not in LOUDNESS_MODES:
        raise OperationError(f"mode must be one of {', '.join(LOUDNESS_MODES)}, not {mode!r}")
    out = copy.deepcopy(data)
    old = out.setdefault("audio", {}).get("loudness") or {}
    block = {k: v for k, v in old.items() if k.startswith("x_")}
    block["mode"] = mode
    if mode != "off":
        block["target_lufs"] = target_lufs if target_lufs is not None else \
            old.get("target_lufs", config.LOUDNESS_TARGET_LUFS)
        block["true_peak_dbtp"] = true_peak_dbtp if true_peak_dbtp is not None else \
            old.get("true_peak_dbtp", config.LOUDNESS_TRUE_PEAK_DBTP)
        block["max_gain_db"] = max_gain_db if max_gain_db is not None else \
            old.get("max_gain_db", config.LOUDNESS_MAX_GAIN_DB)
    out["audio"]["loudness"] = block
    return out


# ── The summary ─────────────────────────────────────────────────

def clock(seconds: float) -> str:
    cs = round(seconds * 100)
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, c = divmod(rest, 100)
    return f"{h:02d}:{m:02d}:{s:02d}.{c:02d}"


def summary(tl, file_hash: str = None, review: dict = None) -> dict:
    """The film as data: totals, and one row per segment (in the order of the file) with
    its film time (None for a disabled segment), length and source frames. `review` maps
    a segment id to the reasons the assembly report gives for looking at it."""
    fps = tl.fps
    starts = tl.segment_film_starts()
    rows = []
    for seg in tl.data["segments"]:
        enabled = seg.get("enabled", True)
        length = seg["out"] - seg["in"]
        start = starts.get(seg["id"]) if enabled else None
        src = tl.data["sources"].get(seg["source"], {})
        row = {
            "id": seg["id"], "enabled": enabled, "source": seg["source"],
            "in": seg["in"], "out": seg["out"], "length_frames": length,
            "length_s": float(length / fps),
            "film_start_frame": start,
            "film_start_s": None if start is None else float(start / fps),
            "source_frames": src.get("frames"),
            "label": seg.get("label", ""), "origin": seg.get("origin", ""),
            "note": seg.get("note", "")}
        if review and review.get(seg["id"]):
            row["review"] = review[seg["id"]]
        rows.append(row)
    total = tl.film_length_frames()
    return {"hash": file_hash, "kind": tl.data["kind"], "fps": tl.data["film"]["fps"],
            "frame": tl.data["film"]["frame"], "segments": len(rows),
            "enabled": sum(1 for r in rows if r["enabled"]),
            "film_frames": total, "film_seconds": float(total / fps), "rows": rows,
            "clips": clip_rows(tl)}


def clip_rows(tl) -> list:
    """One row per clip (source) of the file: its segments, the offset from matching, the
    offset the owner typed (None when there is none) and the offset the sound uses."""
    out = []
    for sid, src in tl.data["sources"].items():
        hand = src.get("audio_offset_hand_s")
        out.append({
            "source": sid,
            "segments": [s["id"] for s in tl.data["segments"] if s["source"] == sid],
            "offset_original_s": src["audio_offset_s"],
            "offset_hand_s": hand,
            "offset_s": timeline.effective_offset(src)})
    return out


def format_summary(s: dict, name: str = "timeline.json") -> str:
    fps = s["fps"]["num"] / s["fps"]["den"]
    frame = s["frame"]
    head = (f"{name}  {s['kind']}  {fps:.2f} fps  {frame['width']}x{frame['height']}  "
            f"hash {(s['hash'] or '-')[:8]}  {s['segments']} segments, {s['enabled']} enabled, "
            f"{s['film_seconds']:.1f} s")
    lines = [head, f"{'id':5} {'film time':23} {'len':>7}  {'source':20} {'frames':13} label"]
    for r in s["rows"]:
        if r["enabled"]:
            a = r["film_start_s"]
            film = f"{clock(a)}-{clock(a + r['length_s'])}"
        else:
            film = "(removed)"
        lines.append(f"{r['id']:5} {film:23} {r['length_s']:>5.1f} s  {r['source']:20} "
                     f"{str(r['in']) + '-' + str(r['out']):13} {r['label']}")
    typed = [c for c in s.get("clips", []) if c["offset_hand_s"] is not None]
    if typed:
        lines.append("offsets set by hand: " + "; ".join(
            f"{c['source']} {c['offset_hand_s']:.3f} s (from matching "
            f"{c['offset_original_s']:.3f} s)" for c in typed))
    return "\n".join(lines) + "\n"
