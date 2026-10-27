"""
The Shotcut project (SPECS 8.6; requirement E4).

Shotcut is for rare hand work, done last (decision D11, "Tool reads cuts back"). It cannot
decode BRAW, so the project uses the preview copies. The tool writes the timeline file as an MLT
XML project (the project format of Shotcut), and `import_shotcut.py` reads the cut points and the
order back after the hand work.

The tracks (the order is the order of the tractor):

    0 background  a black producer as long as the film
    1 V1          the video: one entry per enabled segment, from its preview copy
    2 A1, 3 A2    the sound: one entry per segment from `audio-synced/<clip>.wav`, on A1 and A2
                  in turn, so that the sound of two neighbours can overlap at a join

The sound of a clip is its WAV, shifted by the offset in whole samples so that its time 0 is the
picture's time 0 (`sync_audio`). A segment's picture and sound then have the same frame numbers
in Shotcut. The crossfade at a join is made with two `volume` filters (one falls, one rises, in
dB, a key per frame) on the two tracks over the overlap; the film fades are `brightness` and
`volume` filters over the first and the last frames. The creative LUT is an `avfilter.lut3d`
filter on V1. The base LUT is not in the project: the preview copies carry it.

D11's audio form (separate clips already shifted, or one premixed track) is still open; this is
the plan's default.
"""

import json
import math
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import config
import fsutil
import render
import safety
import timeline
import tools

SHOTCUT_VERSION = "26.9.28"          # the version the writer was tested against
MELT = "/Applications/Shotcut.app/Contents/MacOS/melt"
VIDEO_TRACK, AUDIO_TRACKS = 1, (2, 3)
FLOOR_DB = -90.0                       # the level a fade starts or ends at
OWN = "vet:own"                        # on every filter and transition the writer makes; the reader lists the others


class ShotcutError(Exception):
    """The project cannot be written or does not pass melt."""


def melt_path() -> str:
    return os.environ.get("VET_MELT") or MELT


def melt_env() -> dict:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"      # no window opens
    return env


def installed_version() -> str:
    """The Shotcut version in its application bundle, or "" when Shotcut is not installed."""
    try:
        import plistlib
        app = Path(melt_path()).parents[1] / "Info.plist"
        with open(app, "rb") as f:
            return str(plistlib.load(f).get("CFBundleShortVersionString", ""))
    except (OSError, ValueError, IndexError):
        return ""


# ── The sound of each clip, shifted (SPECS 8.6, "Synced audio") ──

def _probe_wav(path: str) -> dict:
    r = subprocess.run([tools.ffprobe_path(), "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "stream=codec_name,sample_rate,channels", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not json.loads(r.stdout).get("streams"):
        raise ShotcutError(f"ffprobe cannot read the sound file {path}: {r.stderr.strip()}")
    return json.loads(r.stdout)["streams"][0]


def sync_audio(tl, source_id: str, out_dir: str, ffmpeg: str = None) -> dict:
    """Write `<out_dir>/<source_id>.wav`: the clip's WAV at its own sample rate and sample
    format, shifted by the offset in whole samples so that its time 0 is the picture's time 0,
    and ending where the picture ends (silence is added at either end where the WAV has no
    sound). A positive offset drops that many samples at the start; a negative one adds
    silence. A file that is already right is kept. Returns {"path", "samples", "rate",
    "shift_samples", "kept"}."""
    ffmpeg = ffmpeg or tools.ffmpeg_path()
    src = tl.data["sources"][source_id]
    info = _probe_wav(src["audio"])
    rate = int(info["sample_rate"])
    shift = round(Fraction(timeline.effective_offset(src)) * rate)
    fps = tl.fps
    samples = round(Fraction(src["frames"]) * rate / fps)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{source_id}.wav")
    st = os.stat(src["audio"])
    key = {"wav": src["audio"], "size": st.st_size, "mtime_ns": st.st_mtime_ns, "rate": rate,
           "shift": shift, "samples": samples, "codec": info["codec_name"]}
    stamp = path + ".json"
    try:
        with open(stamp, encoding="utf-8") as f:
            if json.load(f) == key and os.path.getsize(path) > 0:
                return {"path": path, "samples": samples, "rate": rate, "shift_samples": shift, "kept": True}
    except (OSError, ValueError):
        pass
    chain = f"atrim=start_sample={shift},asetpts=N/SR/TB" if shift >= 0 else f"adelay={-shift}S:all=1"
    chain += f",apad,atrim=end_sample={samples},asetpts=N/SR/TB"
    codec = info["codec_name"] if info["codec_name"].startswith("pcm_") else "pcm_s24le"
    partial = path + ".partial.wav"
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src["audio"],
                        "-vn", "-af", chain, "-c:a", codec, partial], capture_output=True, text=True)
    if r.returncode != 0:
        _remove(partial)
        raise ShotcutError(f"ffmpeg could not write {path}: {r.stderr.strip()[-400:]}")
    os.replace(partial, path)
    fsutil.write_json_atomic(stamp, key)
    return {"path": path, "samples": samples, "rate": rate, "shift_samples": shift, "kept": False}


def _remove(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ── The XML ─────────────────────────────────────────────────────

def _prop(parent, name, value):
    e = ET.SubElement(parent, "property", name=name)
    e.text = str(value)
    return e


def _db(linear: float) -> float:
    return FLOOR_DB if linear <= 10 ** (FLOOR_DB / 20) else 20 * math.log10(linear)


def _fade_keys(frames: int, curve: str, rising: bool) -> str:
    """An animation string for the `level` of a `volume` filter: one key per frame over
    `frames` frames, in dB, following the amplitude curve of the plan (SPECS 5.4)."""
    keys = []
    for i in range(frames):
        x = i / max(1, frames - 1)
        if curve == "equal_power":
            g = math.sin(0.5 * math.pi * x) if rising else math.cos(0.5 * math.pi * x)
        else:
            g = x if rising else 1.0 - x
        keys.append(f"{i}={_db(g):.3f}")
    return ";".join(keys)


def _dar(w: int, h: int) -> tuple:
    g = math.gcd(w, h)
    return w // g, h // g


@dataclass
class ProjectResult:
    path: str
    segments: int
    audio_files: dict = field(default_factory=dict)
    gains_db: dict = field(default_factory=dict)
    profile: tuple = ()
    shotcut_version: str = ""
    warnings: list = field(default_factory=list)


def segment_gains(tl, plan, loader=None, cache=None) -> tuple:
    """The gain in dB of every segment's sound in the project: the levelling gain of 5.4, the
    segment's `gain_db_adjust`, and the film's one gain. With the loudness mode off only the
    adjust. Returns ({segment id: dB}, the loudness report or None, warnings)."""
    settings = render.loudness_settings(tl)
    if settings["mode"] == "off":
        return {s.id: s.gain_db for s in plan.segments}, None, []
    loader = loader or (lambda p: render.load_wav(p, plan.sample_rate))
    mix = render.build_mix(tl, plan, loader, cache=cache)
    film_gain = (mix.levels.get("film") or {}).get("gain_db", 0.0)
    gains = {s.id: mix.levels["segments"].get(s.id, {}).get("gain_db", 0.0) + s.gain_db + film_gain
             for s in plan.segments}
    return gains, mix.levels, mix.warnings


def build_project(tl, work_dir: str, audio_files: dict, gains_db: dict, profile=None) -> ET.Element:
    """The MLT XML tree of the timeline `tl` (SPECS 8.6). `audio_files` maps a clip id to the
    path of its shifted WAV, `gains_db` a segment id to its gain in dB."""
    data = tl.data
    plan = render.make_plan(tl, preview_width=None)
    fps = data["film"]["fps"]
    rate = plan.sample_rate
    if profile is None:
        w, h = _profile_size(tl)
    else:
        w, h = profile
    total = plan.film_frames
    dar_n, dar_d = _dar(w, h)
    root = ET.Element("mlt", {"LC_NUMERIC": "C", "version": "7.41.0",
                              "title": f"Shotcut version {SHOTCUT_VERSION}", "producer": "main_bin"})
    ET.SubElement(root, "profile", {
        "description": f"{w}x{h} {fps['num'] / fps['den']:.2f} fps", "width": str(w), "height": str(h),
        "progressive": "1", "sample_aspect_num": "1", "sample_aspect_den": "1",
        "display_aspect_num": str(dar_n), "display_aspect_den": str(dar_d),
        "frame_rate_num": str(fps["num"]), "frame_rate_den": str(fps["den"]), "colorspace": "709"})
    bin_ = ET.SubElement(root, "playlist", id="main_bin")
    _prop(bin_, "xml_retain", 1)

    black = ET.SubElement(root, "producer", {"id": "black", "in": "0", "out": str(total - 1)})
    for k, v in (("length", total), ("eof", "pause"), ("resource", 0), ("aspect_ratio", 1),
                 ("mlt_service", "color"), ("mlt_image_format", "rgba"), ("set.test_audio", 0)):
        _prop(black, k, v)
    bg = ET.SubElement(root, "playlist", id="background")
    ET.SubElement(bg, "entry", {"producer": "black", "in": "0", "out": str(total - 1)})

    n_chain = 0
    video_entries, audio_entries = [], []       # (segment plan, chain id, in, out)
    segs = plan.segments

    def add_chain(resource, frames, props, filters=()):
        nonlocal n_chain
        cid = f"chain{n_chain}"
        n_chain += 1
        c = ET.SubElement(root, "chain", {"id": cid, "out": str(frames - 1)})
        for k, v in (("length", frames), ("eof", "pause"), ("resource", resource),
                     ("mlt_service", "avformat-novalidate"), ("seekable", 1)):
            _prop(c, k, v)
        for k, v in props.items():
            _prop(c, k, v)
        for service, fprops in filters:
            f = ET.SubElement(c, "filter")
            for k, v in fprops.items():
                _prop(f, k, v)
            _prop(f, "mlt_service", service)
            _prop(f, OWN, 1)
        return cid

    for s in segs:
        src = data["sources"][s.source]
        seg = tl.segment(s.id)
        cid = add_chain(src["preview_copy"], src["frames"],
                        {"vet:segment": s.id, "vet:source": s.source,
                         "shotcut:caption": seg.get("label") or s.id})
        video_entries.append((s, cid, s.in_frame, s.out_frame - 1))

    # The sound: handles at the joins, in frames, as much as both neighbours can give.
    join_frames = [0] * len(segs)               # the half-length in frames of the join before segment k
    for k in range(1, len(segs)):
        a, b = segs[k - 1], segs[k]
        wanted = round(Fraction(b.join_in_samples, 2) * fps["num"] / (fps["den"] * rate))
        src_a = data["sources"][a.source]["frames"]
        join_frames[k] = max(0, min(wanted, src_a - a.out_frame, b.in_frame))
    for k, s in enumerate(segs):
        src = data["sources"][s.source]
        before = join_frames[k]
        after = join_frames[k + 1] if k + 1 < len(segs) else 0
        cid = add_chain(audio_files[s.source], src["frames"],
                        {"vet:audio_of": s.id, "vet:source": s.source, "shotcut:caption": f"{s.id} sound"},
                        filters=[("volume", {"level": f"{gains_db.get(s.id, 0.0):.3f}"})])
        audio_entries.append((s, cid, s.in_frame - before, s.out_frame - 1 + after, before))

    # Playlists.
    v1 = ET.SubElement(root, "playlist", id="playlist0")
    _prop(v1, "shotcut:video", 1)
    _prop(v1, "shotcut:name", "V1")
    for s, cid, a, b in video_entries:
        ET.SubElement(v1, "entry", {"producer": cid, "in": str(a), "out": str(b)})
    track_of = {}
    playlists = []
    for i, name in enumerate(("A1", "A2")):
        p = ET.SubElement(root, "playlist", id=f"playlist{i + 1}")
        _prop(p, "shotcut:audio", 1)
        _prop(p, "shotcut:name", name)
        playlists.append(p)
    at = [0, 0]                                # the film frame at which each audio track ends so far
    for k, (s, cid, a, b, before) in enumerate(audio_entries):
        t = k % 2
        track_of[s.id] = AUDIO_TRACKS[t]
        start = s.film_start_frame - before
        if start > at[t]:
            ET.SubElement(playlists[t], "blank", {"length": str(start - at[t])})
        ET.SubElement(playlists[t], "entry", {"producer": cid, "in": str(a), "out": str(b)})
        at[t] = start + (b - a + 1)

    # The tractor: tracks, the mixing of the tracks, the filters.
    tr = ET.SubElement(root, "tractor", {"id": "tractor0", "in": "0", "out": str(total - 1)})
    _prop(tr, "shotcut", 1)
    _prop(tr, "shotcut:projectAudioChannels", 2)
    _prop(tr, "shotcut:projectFolder", 0)
    ET.SubElement(tr, "track", producer="background")
    ET.SubElement(tr, "track", {"producer": "playlist0", "hide": "audio"})
    for i in range(2):
        ET.SubElement(tr, "track", {"producer": f"playlist{i + 1}", "hide": "video"})
    n_tr = 0

    def transition(**props):
        nonlocal n_tr
        t = ET.SubElement(tr, "transition", id=f"transition{n_tr}")
        n_tr += 1
        for k, v in props.items():
            _prop(t, k, v)
        _prop(t, OWN, 1)
        return t

    for b in AUDIO_TRACKS:
        transition(a_track=0, b_track=b, mlt_service="mix", always_active=1, sum=1)
    transition(a_track=0, b_track=VIDEO_TRACK, mlt_service="qtblend", compositing=0, distort=0,
               rotate_center=0)
    n_f = 0

    def track_filter(track, service, a=None, b=None, **props):
        nonlocal n_f
        attrs = {"id": f"filter{n_f}", "track": str(track)}
        n_f += 1
        if a is not None:
            attrs["in"], attrs["out"] = str(a), str(b)
        f = ET.SubElement(tr, "filter", attrs)
        for k, v in props.items():
            _prop(f, k, v)
        _prop(f, "mlt_service", service)
        _prop(f, OWN, 1)

    lut = data["look"].get("lut")
    if lut:
        track_filter(VIDEO_TRACK, "avfilter.lut3d", **{"av.file": lut["path"], "av.interp": "tetrahedral"})
    first, last = segs[0], segs[-1]
    fi, fo = first.picture_fade_in_frames, last.picture_fade_out_frames
    if fi:
        track_filter(VIDEO_TRACK, "brightness", 0, fi - 1, level=f"0=0;{fi - 1}=1")
    if fo:
        track_filter(VIDEO_TRACK, "brightness", total - fo, total - 1, level=f"0=1;{fo - 1}=0")
    afi = round(Fraction(first.fade_in_samples) * fps["num"] / (fps["den"] * rate))
    afo = round(Fraction(last.fade_out_samples) * fps["num"] / (fps["den"] * rate))
    if afi:
        track_filter(track_of[first.id], "volume", 0, afi - 1, level=_fade_keys(afi, "equal_power", True))
    if afo:
        track_filter(track_of[last.id], "volume", total - afo, total - 1,
                     level=_fade_keys(afo, "equal_power", False))
    for k in range(1, len(segs)):
        h = join_frames[k]
        if not h:
            continue
        f0 = segs[k].film_start_frame
        curve = segs[k].curve_in
        track_filter(track_of[segs[k - 1].id], "volume", f0 - h, f0 + h - 1, level=_fade_keys(2 * h, curve, False))
        track_filter(track_of[segs[k].id], "volume", f0 - h, f0 + h - 1, level=_fade_keys(2 * h, curve, True))
    return root


def _profile_size(tl) -> tuple:
    """The size of the Shotcut profile: the film frame at the scale of the preview copies."""
    import stable
    plan_sources = [s for s in tl.film_order()]
    frame = tl.data["film"]["frame"]
    for seg in plan_sources:
        src = tl.data["sources"][seg["source"]]
        try:
            cw, ch = stable.probe_size(src["preview_copy"])
        except Exception:
            continue
        k = cw / src["size"]["width"]
        return render.round_even(frame["width"] * k), render.round_even(frame["height"] * k)
    k = 1 / config.PREVIEW_SCALE
    return render.round_even(frame["width"] * k), render.round_even(frame["height"] * k)


def write_project(tl, work_dir: str, out_path: str = None, protected=None, loader=None, cache=None,
                  ffmpeg: str = None, log=print, check: bool = True) -> ProjectResult:
    """Write the Shotcut project of the timeline `tl` (SPECS 8.6): the shifted WAV files in
    `<work_dir>/audio-synced/`, then `film.mlt` (or `out_path`), written atomically; with
    `check` it is read by melt before this returns. A Shotcut version other than the tested
    one is a warning."""
    out_path = out_path or os.path.join(work_dir, "film.mlt")
    if tl.data.get("kind") == "short" or tl.data["film"].get("fit") == "fill":
        raise ShotcutError("A short has no Shotcut project: the project has no 9:16 window (fill and crop), so Shotcut "
                           "would show the wide pictures letterboxed in a vertical frame. Cut the short in the tool: "
                           "python main.py short, and change its scenes with the scenes command.")
    if protected:
        safety.assert_not_inside(out_path, protected)
        safety.assert_not_inside(work_dir, protected)
    findings = tl.validate(file_checks=False)
    errors = [f for f in findings if f.level == "error"]
    if errors:
        raise ShotcutError("The timeline is not valid: " + "; ".join(
            f"{f.rule} at {f.path}: {f.message}" for f in errors))
    result = ProjectResult(path=out_path, segments=len(tl.film_order()))
    for sid, src in tl.data["sources"].items():
        if not os.path.isfile(src["preview_copy"]) and any(s["source"] == sid for s in tl.film_order()):
            raise ShotcutError(f"The preview copy of clip {sid} is missing ({src['preview_copy']}). Run convert.")
    plan = render.make_plan(tl, preview_width=None)
    used = [sid for sid in tl.data["sources"] if any(s.source == sid for s in plan.segments)]
    audio_dir = os.path.join(work_dir, "audio-synced")
    for sid in used:
        info = sync_audio(tl, sid, audio_dir, ffmpeg)
        result.audio_files[sid] = info["path"]
        if tl.data["sources"][sid].get("audio_drift_ppm"):
            result.warnings.append(f"clip {sid}: the drift of {tl.data['sources'][sid]['audio_drift_ppm']} ppm is "
                                   "not applied to the shifted sound file")
    log(f"shifted sound written for {len(used)} clip(s) in {audio_dir}")
    gains, levels, warnings = segment_gains(tl, plan, loader, cache)
    result.gains_db = gains
    result.warnings += warnings
    root = build_project(tl, work_dir, result.audio_files, gains)
    result.profile = (int(root.find("profile").get("width")), int(root.find("profile").get("height")))
    ET.indent(root, space="  ")
    text = '<?xml version="1.0" standalone="no"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
    version = installed_version()
    result.shotcut_version = version
    if version and version != SHOTCUT_VERSION:
        result.warnings.append(f"Shotcut {version} is installed; the writer was tested with {SHOTCUT_VERSION}")
    elif not version:
        result.warnings.append("Shotcut is not installed at the usual place, so the project was not checked "
                               "against its version")
    fsutil.write_text_atomic(out_path, text)
    if check:
        check_with_melt(out_path)
    return result


# ── The check with melt ─────────────────────────────────────────

def check_with_melt(path: str, frames: int = 60, melt: str = None) -> dict:
    """Read the project with Shotcut's `melt` and render its first `frames` frames to nothing:
    the file must load, every producer must open and the frames must be made. The check runs
    on a copy without the root `producer` attribute, which names Shotcut's bin (with it `melt`
    would play the empty bin), and with the validating `avformat` service in place of
    `avformat-novalidate`, so that a missing file is reported. No window opens
    (`QT_QPA_PLATFORM=offscreen`)."""
    melt = melt or melt_path()
    if not os.path.isfile(melt):
        raise ShotcutError(f"melt not found at {melt}")
    text = Path(path).read_text(encoding="utf-8")
    text = re.sub(r'(<mlt [^>]*?) producer="main_bin"', r"\1", text, count=1)
    # Shotcut opens clips with avformat-novalidate, which does not complain about a missing file;
    # avformat does ("failed to load chain").
    text = text.replace("avformat-novalidate", "avformat")
    probe = os.path.join(os.path.dirname(os.path.abspath(path)), f".check-{os.path.basename(path)}")
    Path(probe).write_text(text, encoding="utf-8")
    try:
        r = subprocess.run([melt, probe, "in=0", f"out={frames - 1}", "-consumer", "null", "real_time=-1"],
                           capture_output=True, text=True, env=melt_env(), timeout=600)
    finally:
        _remove(probe)
    err = r.stderr.replace("\r", "\n")
    bad = [ln for ln in err.splitlines() if re.search(r"fail|error|cannot|unable|invalid", ln, re.I)]
    if r.returncode != 0 or bad:
        raise ShotcutError(f"melt did not accept {path} (exit {r.returncode}): " + " | ".join(bad[:5] or [err[-300:]]))
    done = re.findall(r"Current Frame:\s+(\d+)", err)
    return {"melt": melt, "frames_rendered": int(done[-1]) + 1 if done else 0}
