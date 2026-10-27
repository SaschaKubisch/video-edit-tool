"""
Reading the cuts back from a Shotcut project (SPECS 8.6; requirement E7; decision D11,
"Tool reads cuts back").

After the hand work in Shotcut, the cut points (`in`, `out`) and the order of the video
entries are read from the saved project and written into the timeline file. Nothing else
comes back: filters, transitions, volume changes, titles and audio edits made in Shotcut are
read only so that they can be listed as ignored. The 4K film is then rendered from the `.braw`
files with the new cut points (the preview copy and the `.braw` file have the same frame
numbers).

How an entry of the video track is matched:

- The writer puts the segment id into the producer of each video entry (`vet:segment`) and
  the clip id (`vet:source`). A producer that Shotcut kept under another name still has them.
- An entry whose producer has a segment id that no earlier entry used changes that segment's
  `in`, `out` and place in the order. (MLT counts `out` as the last frame used, so the
  segment's `out` is `out + 1`.)
- Any other entry (a clip split in Shotcut: its halves share one producer, so the second half
  has an id that is already used; or a preview copy added from the bin) becomes a new segment
  with `origin: "manual"`. The clip is found by the preview copy's path.
- A segment that no entry names any more gets `enabled: false` and stays in the file.
- The result goes through the rules of SPECS 4.1: validation, a snapshot before the write and
  the hash check. Cut points that break a rule (V9 and V11 among them) are refused with the
  field path, and nothing is written.
"""

import copy
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import timeline
import words

SEGMENT_ID = re.compile(r"^s([0-9]+)$")
OWN = "vet:own"


class ShotcutImportError(Exception):
    """The project cannot be read, or its cut points break a rule. Nothing was written."""


@dataclass
class Entry:
    producer: str
    resource: str
    source: object                # the clip id found from the resource, or None
    segment: object               # the segment id the writer put in the producer, or None
    first: int                    # the first frame used
    last: int                     # the last frame used (MLT's out)
    position: int                 # the place in the playlist, counted from 0 over the entries


@dataclass
class ImportReport:
    changed: list = field(default_factory=list)       # {id, in: [old, new], out: [old, new]}
    moved: list = field(default_factory=list)         # ids whose place in the order changed
    added: list = field(default_factory=list)         # ids of new segments
    removed: list = field(default_factory=list)       # ids that are now disabled
    ignored: list = field(default_factory=list)       # what was seen in the project and not read
    subtitles: list = field(default_factory=list)     # what the subtitle tracks of the project hold (never read back)
    findings: list = field(default_factory=list)      # warnings of the validator

    @property
    def nothing_changed(self) -> bool:
        return not (self.changed or self.moved or self.added or self.removed)

    def as_dict(self) -> dict:
        return {"changed": self.changed, "moved": self.moved, "added": self.added,
                "removed": self.removed, "ignored": self.ignored, "subtitles": self.subtitles}


# ── Reading the XML ─────────────────────────────────────────────

def parse_time(value, fps: float, default=None):
    """A time of an MLT file as a frame number: an integer, a clock `HH:MM:SS.mmm`, or a
    timecode `HH:MM:SS:FF`."""
    if value is None or value == "":
        return default
    text = str(value).strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    m = re.fullmatch(r"(\d+):(\d\d):(\d\d)[.,](\d+)", text)
    if m:
        h, mi, s, frac = m.groups()
        seconds = int(h) * 3600 + int(mi) * 60 + int(s) + int(frac) / 10 ** len(frac)
        return round(seconds * fps)
    m = re.fullmatch(r"(\d+):(\d\d):(\d\d):(\d\d)", text)
    if m:
        h, mi, s, f = (int(x) for x in m.groups())
        return round((h * 3600 + mi * 60 + s) * fps) + f
    m = re.fullmatch(r"(\d+)\.(\d+)", text)
    if m:
        return round(float(text) * fps)
    raise ShotcutImportError(f"cannot read the time {value!r}")


def _props(element) -> dict:
    out = {}
    for p in element.findall("property"):
        out[p.get("name")] = (p.text or "").strip()
    return out


class Project:
    """What the reader needs from the XML of a Shotcut project."""

    def __init__(self, path: str):
        try:
            tree = ET.parse(path)
        except (ET.ParseError, OSError) as e:
            raise ShotcutImportError(f"{path} is not an MLT project: {e}")
        self.path = path
        self.root = tree.getroot()
        if self.root.tag != "mlt":
            raise ShotcutImportError(f"{path} is not an MLT project (the root element is {self.root.tag!r})")
        profile = self.root.find("profile")
        if profile is None:
            raise ShotcutImportError(f"{path} has no profile, so the frame rate is unknown")
        self.fps = int(profile.get("frame_rate_num")) / int(profile.get("frame_rate_den"))
        self.base = self.root.get("root") or os.path.dirname(os.path.abspath(path))
        self.producers = {}
        for e in list(self.root.findall("producer")) + list(self.root.findall("chain")):
            props = _props(e)
            self.producers[e.get("id")] = {
                "element": e, "props": props,
                "in": parse_time(e.get("in"), self.fps, 0),
                "out": parse_time(e.get("out"), self.fps, None),
                "length": parse_time(props.get("length"), self.fps, None)}
        self.playlists = {p.get("id"): p for p in self.root.findall("playlist")}
        self.tractors = {t.get("id"): t for t in self.root.findall("tractor")}

    def timeline_tractor(self):
        """The tractor of the timeline: the one with the property `shotcut`, else the last."""
        marked = [t for t in self.root.findall("tractor") if _props(t).get("shotcut") == "1"]
        if marked:
            return marked[-1]
        tractors = self.root.findall("tractor")
        if not tractors:
            raise ShotcutImportError(f"{self.path} has no tractor, so it has no timeline")
        return tractors[-1]

    def resolve(self, resource: str) -> str:
        p = resource
        if not os.path.isabs(p):
            p = os.path.join(self.base, p)
        return os.path.realpath(p)


# ── The video entries ───────────────────────────────────────────

def _source_of(resource: str, project: Project, sources: dict):
    real = project.resolve(resource)
    for sid, src in sources.items():
        if os.path.realpath(src["preview_copy"]) == real:
            return sid
    names = [sid for sid, src in sources.items()
             if os.path.basename(src["preview_copy"]) == os.path.basename(resource)]
    return names[0] if len(names) == 1 else None


def video_entries(project: Project, sources: dict, report: ImportReport) -> list:
    """The entries of the video track, in playlist order, and the ignored things on the way."""
    tractor = project.timeline_tractor()
    tracks = tractor.findall("track")
    candidates = []
    for index, t in enumerate(tracks):
        pl = project.playlists.get(t.get("producer"))
        if pl is None:
            continue
        pp = _props(pl)
        entry_producers = [project.producers.get(e.get("producer")) for e in pl.findall("entry")]
        is_video = pp.get("shotcut:video") == "1" or any(
            p and p["props"].get("vet:segment") for p in entry_producers)
        candidates.append((index, t, pl, is_video, pp))
    videos = [c for c in candidates if c[3]]
    if not videos:
        raise ShotcutImportError(f"{project.path} has no video track with preview copies of this film")
    index, track, playlist, _, pp = videos[0]
    for other in videos[1:]:
        report.ignored.append(f"video track {_props(other[2]).get('shotcut:name', other[2].get('id'))}: "
                              "only the first video track is read")
    for c in candidates:
        if c[3] or c[0] == 0 or _is_own_sound_track(c[2], project):
            continue          # the video track, the background, and the writer's sound tracks
        report.ignored.append(f"track {c[4].get('shotcut:name', c[2].get('id'))}: not a video track, "
                              "its audio and its edits are not read")
    entries, position = [], 0
    for child in playlist:
        if child.tag == "blank":
            report.ignored.append(f"a gap of {child.get('length')} in the video track")
            continue
        if child.tag != "entry":
            continue
        prod = project.producers.get(child.get("producer"))
        if prod is None:
            if child.get("producer") in project.tractors:
                report.ignored.append(f"entry {position + 1}: a transition between clips "
                                      f"({child.get('producer')}); its clips are not read")
                position += 1
                continue
            raise ShotcutImportError(f"entry {position + 1} names the producer {child.get('producer')!r}, "
                               "which the project does not define")
        resource = prod["props"].get("resource", "")
        source = _source_of(resource, project, sources)
        if source is None:
            raise ShotcutImportError(
                f"entry {position + 1} uses {resource!r}, which is not a preview copy of a clip of this "
                "film. Only clips of the timeline file can be rendered from the .braw files.")
        first = parse_time(child.get("in"), project.fps, prod["in"])
        last = parse_time(child.get("out"), project.fps, prod["out"])
        if last is None:
            last = (prod["length"] or 0) - 1
        entries.append(Entry(child.get("producer"), resource, source,
                             prod["props"].get("vet:segment") or None, first, last, position))
        position += 1
        for f in child.findall("filter"):
            _note_filter(f, report, f"entry {position}")
    return entries


def _is_own_sound_track(playlist, project: Project) -> bool:
    """A sound track the writer made: every entry is a segment's sound (`vet:audio_of`)."""
    entries = playlist.findall("entry")
    return bool(entries) and all(
        (project.producers.get(e.get("producer")) or {"props": {}})["props"].get("vet:audio_of")
        for e in entries)


def _note_filter(f, report: ImportReport, where: str) -> None:
    props = _props(f)
    if props.get(OWN) == "1":
        return
    report.ignored.append(f"filter {props.get('mlt_service', '?')} on {where}")


def note_other_edits(project: Project, report: ImportReport) -> None:
    """List what was added in Shotcut and is not read: filters and transitions that the
    writer did not make."""
    for e in list(project.root.findall("chain")) + list(project.root.findall("producer")):
        for f in e.findall("filter"):
            _note_filter(f, report, f"clip {e.get('id')}")
    for t in project.root.findall("tractor"):
        for f in t.findall("filter"):
            _note_filter(f, report, f"track {f.get('track', '?')}")
        for tr in t.findall("transition"):
            props = _props(tr)
            if props.get(OWN) != "1":
                report.ignored.append(f"transition {props.get('mlt_service', '?')}")


def _cues(n: int) -> str:
    return f"{n} cue" + ("" if n == 1 else "s")


def note_subtitles(project: Project, report: ImportReport, data: dict) -> None:
    """Say what the subtitle tracks of the project hold (SPECS 8.6). They are not read back: a cue of the film belongs
    to a scene and moves with it, and a track in Shotcut has film times only. The English track the writer made is
    compared with the film's cues, so that an edit made in Shotcut is named and not lost without a word; a translation
    is counted; a track made in Shotcut is reported as that."""
    import config
    spans = words.cue_spans(data)
    names = {name: lang for lang, (name, code) in config.SHOTCUT_SUBTITLE_TRACKS.items()}
    frame = 1.0 / project.fps
    for t in project.root.findall("tractor"):
        for f in t.findall("filter"):
            props = _props(f)
            if props.get("mlt_service") != "subtitle_feed" or "text" not in props:
                continue
            name, code = props.get("feed", "?"), props.get("lang", "?")
            head = f"subtitle track {name} ({code}): "
            try:
                cues = words.parse_srt(props["text"])
            except words.WordsError:
                report.subtitles.append(head + "the text is not an SRT document; not read back")
                continue
            lang = names.get(name)
            if props.get(OWN) != "1":
                report.subtitles.append(head + f"{_cues(len(cues))}, made in Shotcut; not read back")
            elif lang != config.SUBTITLE_LANGUAGE:
                report.subtitles.append(head + f"{_cues(len(cues))}, a translation; not compared and not read back")
            else:
                differ = sum(1 for a, b in zip(cues, spans)
                             if abs(a["start_s"] - b["start_s"]) > frame + 1e-3 or abs(a["end_s"] - b["end_s"]) > frame + 1e-3
                             or " ".join(a["text"].split()) != " ".join(b["text"].split()))
                if len(cues) != len(spans):
                    report.subtitles.append(
                        head + f"changed in Shotcut ({len(cues)} cues in Shotcut, {len(spans)} in the timeline file); "
                        "not read back. To keep the changes, save the track from Shotcut as an SRT file and run: "
                        "python main.py subtitles import FILE.srt --replace")
                elif differ:
                    report.subtitles.append(
                        head + f"changed in Shotcut ({differ} of {len(cues)} cues differ); not read back. To keep the "
                        "changes, save the track from Shotcut as an SRT file and run: "
                        "python main.py subtitles import FILE.srt --replace")
                else:
                    report.subtitles.append(head + f"{_cues(len(cues))}, as the tool wrote them")


# ── Writing the cuts into the timeline data ─────────────────────

def _next_number(segments) -> int:
    n = 0
    for s in segments:
        m = SEGMENT_ID.match(s.get("id", ""))
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def apply_entries(data: dict, entries: list) -> tuple:
    """A copy of the timeline data with the cut points and the order of `entries`, and the
    ImportReport. Raises ShotcutImportError for an entry that cannot belong to the film."""
    out = copy.deepcopy(data)
    report = ImportReport()
    old = out["segments"]
    by_id = {s["id"]: s for s in old}
    old_enabled_order = [s["id"] for s in old if s.get("enabled", True)]
    claimed, ordered = set(), []
    next_no = _next_number(old)
    for e in entries:
        seg = by_id.get(e.segment) if e.segment else None
        if seg is not None and seg["id"] not in claimed and seg["source"] == e.source \
                and seg.get("enabled", True):
            claimed.add(seg["id"])
            new_in, new_out = e.first, e.last + 1
            if (seg["in"], seg["out"]) != (new_in, new_out):
                report.changed.append({"id": seg["id"], "in": [seg["in"], new_in], "out": [seg["out"], new_out]})
                was_in, was_out = seg["in"], seg["out"]
                seg["in"], seg["out"] = new_in, new_out
                if 0 <= new_in < new_out:           # the cues stay with their picture
                    words.retime_segment(out, seg["id"], was_in, was_out)
            ordered.append(seg)
            continue
        parent = by_id.get(e.segment) if e.segment else None
        new = {"id": f"s{next_no:03d}", "source": e.source, "in": e.first, "out": e.last + 1,
               "origin": "manual"}
        next_no += 1
        if parent is not None and parent["source"] == e.source:
            if parent.get("label"):
                new["label"] = f"{parent['label']} (split)"
            new["note"] = f"cut in Shotcut from {parent['id']}"
        else:
            new["note"] = "added in Shotcut"
        by_id[new["id"]] = new
        ordered.append(new)
        report.added.append(new["id"])

    # The order: the entries first, then every segment that no entry names, next to the
    # segment that stood before it in the old file.
    result = list(ordered)
    last_placed = -1
    for s in old:
        if s["id"] in claimed:
            last_placed = result.index(s)
            continue
        if s.get("enabled", True):
            s["enabled"] = False
            report.removed.append(s["id"])
        result.insert(last_placed + 1, s)
        last_placed += 1
    out["segments"] = result
    now = [s["id"] for s in result if s["id"] in claimed]
    was = [i for i in old_enabled_order if i in claimed]
    report.moved = [i for i, j in zip(now, was) if i != j]
    return out, report


# ── The whole read-back ─────────────────────────────────────────

def read_back(project_path: str, timeline_path: str, dry_run: bool = False, file_checks: bool = False) -> ImportReport:
    """Read the project and write the cut points into the timeline file. Returns the report.
    Raises ShotcutImportError (nothing written) for a project that cannot be read or cut points that
    break a rule of the timeline file."""
    import store
    tl = timeline.Timeline.load(timeline_path)
    before_hash = timeline.file_hash(timeline_path)
    project = Project(project_path)
    report = ImportReport()
    entries = video_entries(project, tl.data["sources"], report)
    note_other_edits(project, report)
    note_subtitles(project, report, tl.data)
    new_data, applied = apply_entries(tl.data, entries)
    applied.ignored = report.ignored + [i for i in applied.ignored if i not in report.ignored]
    applied.subtitles = report.subtitles
    new = timeline.Timeline(new_data, tl.duplicates)
    findings = new.validate(file_checks=file_checks)
    errors = [f for f in findings if f.level == "error"]
    if errors:
        raise ShotcutImportError("The cut points break the rules of the timeline file; nothing was written:\n" +
                           "\n".join(f"  {f.rule} {f.path}: {f.message}" + (f" ({f.hint})" if f.hint else "")
                                     for f in errors))
    applied.findings = [f.as_dict() for f in findings]
    if not dry_run and not applied.nothing_changed:
        try:
            store.save(new, timeline_path, expected_hash=before_hash)
        except store.StaleHash as e:
            raise ShotcutImportError(str(e))
    return applied
