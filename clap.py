"""
Finding the sync clap (SPECS 7.2).

Decision D6, "Only at the start": each clip has one clap, at its beginning. The
tool searches the first CLAP_SEARCH_SEC seconds of the camera's scratch audio and
of the recorder's WAV, finds the first clap in each, and compares the two.

A clap is a very short broadband sound that dies away fast. The method for one
channel (`find_clap`):

1. high-pass the signal at 2 kHz (wind and room tone sit lower);
2. take the mean power in windows of 2 ms;
3. onset strength per window = the rise of the log power from the window before;
   a log ratio does not depend on the gain;
4. a candidate is a window whose onset is at least CLAP_MIN_ONSET and is the
   strongest within 50 ms; it counts as a clap only if the power falls under 20
   percent of its peak within CLAP_DECAY_SEC (a steady loud sound or a step in
   level does not decay);
5. the time is refined to the first sample, near the window, at a quarter of the
   local peak and at least five times the RMS of the 50 ms before (on the
   high-passed signal);
6. the passing candidates are listed in time order; `find_clap` returns the first,
   and names the strongest.

Then `analyse_clip` combines the two channels. The plan took the first clap of
each and compared them. On the first shoot that disagreed for 47 of 105 clips,
because one channel often had an earlier sound that the other did not hear. So
the clap is now a sound that both microphones heard at the stored offset, the
first of those that is within CLAP_PAIR_BAND_DB of the loudest (see analyse_clip).

offset = t_audio - t_video (the contract of matcher.py): the same event is
`offset` seconds later in the WAV than in the camera audio.
"""

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import signal

import config
import safety


@dataclass
class Candidate:
    time_s: float
    onset: float            # rise of the log power, natural log
    decay_s: float          # time from the onset until the power fell under 20 percent of its peak
    peak_db: float = 0.0    # peak power against the median power of the searched span, in dB

    @property
    def confidence(self) -> float:
        """0 to 1: the onset against three times the minimum onset."""
        return float(min(1.0, self.onset / (3 * config.CLAP_MIN_ONSET)))

    def as_dict(self) -> dict:
        return {"time_s": round(self.time_s, 6), "onset": round(self.onset, 3),
                "peak_db": round(self.peak_db, 1), "decay_s": round(self.decay_s, 4),
                "confidence": round(self.confidence, 3)}


@dataclass
class ClapResult:
    """The first clap in a signal."""
    time_s: float
    onset: float
    snr_db: float           # peak power of the clap against the median power of the searched span
    decay_s: float
    confidence: float       # 0 to 1: onset against three times the minimum onset
    candidates: list = field(default_factory=list)      # all claps found, in time order
    strongest: Candidate = None                         # the candidate with the largest onset

    def as_dict(self) -> dict:
        return {"time_s": round(self.time_s, 6), "onset": round(self.onset, 3),
                "snr_db": round(self.snr_db, 1), "decay_s": round(self.decay_s, 4),
                "confidence": round(self.confidence, 3),
                "candidates": [{"time_s": round(c.time_s, 6), "onset": round(c.onset, 3)}
                               for c in self.candidates],
                "strongest": None if self.strongest is None else
                {"time_s": round(self.strongest.time_s, 6),
                 "onset": round(self.strongest.onset, 3)}}


def _highpass(x: np.ndarray, rate: int, cutoff_hz: float) -> np.ndarray:
    if cutoff_hz >= rate / 2:
        return x
    sos = signal.butter(4, cutoff_hz, btype="highpass", fs=rate, output="sos")
    return signal.sosfilt(sos, x)


def find_clap(samples, rate: int, search_s: float = None):
    """The first clap in the first `search_s` seconds of a mono signal, or None."""
    search_s = config.CLAP_SEARCH_SEC if search_s is None else search_s
    x = np.asarray(samples, dtype=np.float64)[: int(search_s * rate)]
    win = max(1, int(round(config.CLAP_WINDOW_SEC * rate)))
    win_s = win / rate
    n = len(x) // win
    if n < 8:
        return None
    hp = _highpass(x, rate, config.CLAP_HIGHPASS_HZ)
    power = (hp[: n * win] ** 2).reshape(n, win).mean(axis=1)
    peak = float(power.max())
    if peak <= 0:
        return None
    eps = 1e-9 * peak       # a floor relative to the loudest window: gain does not matter
    logp = np.log(power + eps)
    onset = np.zeros(n)
    onset[1:] = np.maximum(0.0, np.diff(logp))

    half = max(1, int(round(config.CLAP_LOCAL_MAX_SEC / win_s)))
    decay_windows = max(1, int(math.ceil(config.CLAP_DECAY_SEC / win_s)))
    pk_windows = max(1, int(round(0.01 / win_s)))       # the peak power lies within 10 ms
    median_power = float(np.median(power)) or eps

    found = []
    for k in np.nonzero(onset >= config.CLAP_MIN_ONSET)[0]:
        lo, hi = max(0, k - half), min(n, k + half + 1)
        if int(lo + np.argmax(onset[lo:hi])) != k:      # not the strongest (first of equals) nearby
            continue
        seg = power[k:k + pk_windows]
        kpk = k + int(np.argmax(seg))
        ppk = float(power[kpk])
        end = min(n, k + decay_windows + 1)
        if end - k <= decay_windows:                    # cannot see far enough to judge
            continue
        below = np.nonzero(power[kpk + 1:end] <= config.CLAP_DECAY_FRACTION * ppk)[0]
        if len(below) == 0:
            continue
        decay = (kpk + 1 + int(below[0]) - k) * win_s
        # Refine the time on the high-passed signal. The rise starts in this window
        # or at the end of the one before; the peak is read over three windows. The
        # threshold is a quarter of that peak but at least five times the RMS of
        # the 50 ms before, so that loud ambience cannot pull the time earlier.
        a, b = max(0, (k - 1) * win), min(len(hp), (k + 2) * win)
        span = np.abs(hp[a:b])
        pre = hp[max(0, a - int(0.05 * rate)):a]
        noise_rms = float(np.sqrt(np.mean(pre ** 2))) if len(pre) else 0.0
        threshold = max(config.CLAP_REFINE_FRACTION * span.max(), 5 * noise_rms)
        hit = np.nonzero(span >= threshold)[0]
        first = int(hit[0]) if len(hit) else int(np.argmax(span))
        found.append((float((a + first) / rate), float(onset[k]), float(decay), ppk))

    if not found:
        return None
    db = lambda p: float(10 * np.log10(p / median_power)) if median_power > 0 else 99.0
    candidates = [Candidate(t, o, d, db(p)) for t, o, d, p in found]
    strongest = max(candidates, key=lambda c: c.onset)
    first = candidates[0]
    return ClapResult(
        time_s=first.time_s, onset=first.onset, decay_s=first.decay_s,
        snr_db=first.peak_db, confidence=first.confidence,
        candidates=candidates, strongest=strongest)


def first_frame_after(clap_s: float, fps_num: int, fps_den: int, guard_s: float = None) -> int:
    """The first frame of a segment that starts after the clap: ceil((t + guard) * fps).
    The guard keeps the tail of the clap out of the film (D6: 0.5 s)."""
    guard_s = config.CLAP_GUARD_SEC if guard_s is None else guard_s
    frames = (clap_s + guard_s) * fps_num / fps_den
    return max(0, math.ceil(frames - 1e-9))


@dataclass
class ClipClap:
    """The clap of one clip, from the camera audio and the WAV together."""
    result: str                     # agree, single or none
    video_time_s: float             # the clap in video time, or None
    camera: Candidate               # the event taken as the clap in the camera audio, or None
    wav: Candidate                  # the same event in the WAV, or None
    offset_s: float
    delta_s: float                  # (t_wav - t_camera) - offset, or None
    pairs: int                      # events heard by both at the stored offset
    confidence: float
    review: bool
    reasons: list

    def as_dict(self) -> dict:
        return {
            "result": self.result,
            "video_time_s": None if self.video_time_s is None else round(self.video_time_s, 6),
            "camera": None if self.camera is None else self.camera.as_dict(),
            "wav": None if self.wav is None else self.wav.as_dict(),
            "offset_s": round(self.offset_s, 6),
            "delta_s": None if self.delta_s is None else round(self.delta_s, 6),
            "pairs": self.pairs,
            "confidence": round(self.confidence, 3),
            "review": self.review,
            "reasons": self.reasons,
            "settings": {
                "search_s": config.CLAP_SEARCH_SEC, "highpass_hz": config.CLAP_HIGHPASS_HZ,
                "min_onset": config.CLAP_MIN_ONSET, "decay_s": config.CLAP_DECAY_SEC,
                "agree_s": config.CLAP_AGREE_SEC, "pair_band_db": config.CLAP_PAIR_BAND_DB,
                "guard_s": config.CLAP_GUARD_SEC, "late_s": config.CLAP_LATE_SEC,
                "weak_confidence": config.CLAP_WEAK_CONFIDENCE,
                "usual_offset_range_s": list(config.USUAL_OFFSET_RANGE_SEC)},
        }


def shared_events(cam_result, wav_result, offset_s: float) -> list:
    """Pairs (camera candidate, WAV candidate) that lie `offset_s` apart, within
    CLAP_AGREE_SEC: the same sound heard by both microphones. In time order."""
    if not cam_result or not wav_result:
        return []
    pairs = []
    for c in cam_result.candidates:
        for w in wav_result.candidates:
            if abs((w.time_s - c.time_s) - offset_s) <= config.CLAP_AGREE_SEC:
                pairs.append((c, w))
    return pairs


def analyse_clip(camera_samples, wav_samples, rate: int, offset_s: float,
                 search_s: float = None) -> ClipClap:
    """Find the clap in the camera audio and in the WAV and decide the video time.

    The plan took the first clap of each channel and compared the two. On the
    first shoot (105 clips) that agreed for 58 clips; in the others one channel
    had an earlier sound that the other did not hear (a click, a touch of the
    microphone). The stored offset is known to about a millisecond, so the
    method now looks for sounds that both microphones heard at that offset.

    agree   at least one sound was heard by both at the stored offset (within
            CLAP_AGREE_SEC). The clap is the first of them that is within
            CLAP_PAIR_BAND_DB of the loudest of them: a clap is among the loudest
            sounds, and the first of those is the one at the start. The video
            time is the camera's.
    single  no shared sound. The first clap of the camera audio is taken, else the
            WAV's minus the offset. Marked for review.
    none    neither channel holds a clap. No clap cut. Marked for review.
    """
    cam = find_clap(camera_samples, rate, search_s)
    wav = find_clap(wav_samples, rate, search_s)
    reasons = []
    delta = None
    pairs = shared_events(cam, wav, offset_s)
    chosen_cam = chosen_wav = None
    if pairs:
        loud = lambda p: min(p[0].peak_db, p[1].peak_db)
        top = max(loud(p) for p in pairs)
        chosen_cam, chosen_wav = next(p for p in pairs if loud(p) >= top - config.CLAP_PAIR_BAND_DB)
        delta = (chosen_wav.time_s - chosen_cam.time_s) - offset_s
        result, t = "agree", chosen_cam.time_s
        confidence = min(chosen_cam.confidence, chosen_wav.confidence)
    elif cam or wav:
        result = "single"
        if cam:
            chosen_cam, t = cam.candidates[0], cam.candidates[0].time_s
        if wav:
            chosen_wav = wav.candidates[0]
            t = t if cam else chosen_wav.time_s - offset_s
        if cam and wav:
            delta = (chosen_wav.time_s - chosen_cam.time_s) - offset_s
            reasons.append(f"camera and WAV share no sound at the stored offset; their first "
                           f"claps differ from it by {delta:+.3f} s")
        elif cam:
            reasons.append("only the camera audio holds a clap")
        else:
            reasons.append("only the WAV holds a clap")
        confidence = min(c.confidence for c in (chosen_cam, chosen_wav) if c)
    else:
        result, t, confidence = "none", None, 0.0
        reasons.append("no clap found in the first "
                       f"{config.CLAP_SEARCH_SEC if search_s is None else search_s:g} s")
    if t is not None:
        if t > config.CLAP_LATE_SEC:
            reasons.append(f"the clap is at {t:.1f} s; a clap at the start is expected")
        if result == "agree" and confidence < config.CLAP_WEAK_CONFIDENCE:
            reasons.append(f"the clap is weak (confidence {confidence:.2f})")
    lo, hi = config.USUAL_OFFSET_RANGE_SEC
    if not lo <= offset_s <= hi:
        reasons.append(f"the offset {offset_s:.3f} s is outside the usual range "
                       f"{lo} to {hi} s")
    return ClipClap(result, t, chosen_cam, chosen_wav, offset_s, delta, len(pairs),
                    confidence, bool(reasons), reasons)


# ── Over a project ───────────────────────────────────────────────

def _write_json_atomic(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def analyse_project(project, work_dir: str, limit: int = None, report_name: str = "clap-report",
                    extract=None, log=print) -> list:
    """Find the clap in every matched clip (or the first `limit`) and write
    work_dir/analysis/<stem>/clap.json for each, and work_dir/reports/<report_name>.json
    and .txt for all. Returns the report rows.

    `extract(path, seconds)` returns mono audio at config.MATCH_SAMPLE_RATE; the default
    is matcher._extract_audio_raw (ffmpeg, read-only on the originals).
    """
    if extract is None:
        import matcher
        extract = lambda path, seconds: matcher._extract_audio_raw(path, seconds)
    safety.assert_not_inside(work_dir, safety.originals_folders(project))
    rate = config.MATCH_SAMPLE_RATE
    seconds = config.CLAP_SEARCH_SEC + 10.0     # the clap in the WAV may lie |offset| later
    matches = list(project.matches)
    if limit:
        matches = matches[:limit]
    rows = []
    for i, m in enumerate(matches, start=1):
        d = m if isinstance(m, dict) else m.__dict__
        stem = Path(d["video_path"]).stem
        cam = extract(d["video_path"], seconds)
        wav = extract(d["audio_path"], seconds)
        res = analyse_clip(cam, wav, rate, d["offset"])
        _write_json_atomic(os.path.join(work_dir, "analysis", stem, "clap.json"),
                           {"clip": stem, "video": d["video_path"], "audio": d["audio_path"],
                            **res.as_dict()})
        row = {"index": d["index"], "clip": stem, "audio": Path(d["audio_path"]).name,
               "offset_s": round(d["offset"], 3), "result": res.result,
               "clap_s": None if res.video_time_s is None else round(res.video_time_s, 3),
               "confidence": round(res.confidence, 3), "pairs": res.pairs,
               "camera_clap_s": None if res.camera is None else round(res.camera.time_s, 3),
               "wav_clap_s": None if res.wav is None else round(res.wav.time_s, 3),
               "delta_s": None if res.delta_s is None else round(res.delta_s, 4),
               "review": res.review, "reasons": res.reasons}
        rows.append(row)
        log(f"[{i}/{len(matches)}] {stem}  {res.result:6}  clap "
            f"{'-' if res.video_time_s is None else format(res.video_time_s, '.3f')} s"
            f"{'  REVIEW: ' + '; '.join(res.reasons) if res.review else ''}")
    reports = os.path.join(work_dir, "reports")
    _write_json_atomic(os.path.join(reports, f"{report_name}.json"),
                       {"settings": rows and res.as_dict()["settings"], "clips": rows})
    with open(os.path.join(reports, f"{report_name}.txt"), "w", encoding="utf-8") as f:
        f.write(format_report(rows))
    return rows


def format_report(rows: list) -> str:
    head = f"{'clip':22} {'offset':>7} {'result':6} {'clap_s':>7} {'conf':>5} " \
           f"{'cam_s':>7} {'wav_s':>7} {'delta':>7}  review"
    lines = [head, "-" * len(head)]
    num = lambda v, w, p=3: f"{'-':>{w}}" if v is None else f"{v:>{w}.{p}f}"
    for r in rows:
        lines.append(f"{r['clip']:22} {r['offset_s']:>7.3f} {r['result']:6} "
                     f"{num(r['clap_s'], 7)} {r['confidence']:>5.2f} "
                     f"{num(r['camera_clap_s'], 7)} {num(r['wav_clap_s'], 7)} "
                     f"{num(r['delta_s'], 7)}  "
                     f"{'; '.join(r['reasons']) if r['review'] else ''}")
    counts = {k: sum(1 for r in rows if r["result"] == k) for k in ("agree", "single", "none")}
    lines.append("")
    lines.append(f"{len(rows)} clips: agree {counts['agree']}, single {counts['single']}, "
                 f"none {counts['none']}; for review {sum(1 for r in rows if r['review'])}.")
    return "\n".join(lines) + "\n"
