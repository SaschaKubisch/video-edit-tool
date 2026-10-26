"""
Loudness (SPECS 5.4 steps 5 to 7 and 8.4; decision D9, "Each scene, then film").

`measure` pipes float stereo into ffmpeg's `ebur128` filter and returns the integrated
loudness (EBU R128, gates of -70 LUFS and 10 LU) and the true peak. `segment_gain` is
the gain that brings one segment to the target. `level_film` applies one gain to the
whole film so that its integrated loudness equals the target and, if the true peak is
then over the limit, a look-ahead limiter (ffmpeg `alimiter`) until it holds.

The method is a measured fixed gain followed by a limiter. `loudnorm` in its dynamic
mode is not used: it can change the loudness over time (SPECS 8.4).

Everything here works on numpy arrays of shape (samples, 2), float32 in -1..1.
"""

import math
import os
import subprocess
import tempfile
from dataclasses import dataclass, field

import numpy as np

import tools

GATE_FLOOR_LUFS = -70.0      # ebur128 reports this value when nothing is above the absolute gate


@dataclass(frozen=True)
class Level:
    """What ebur128 measured. `lufs` is None when the sound is below the absolute gate
    (silence, or a stretch shorter than the 0.4 s block). `peak_dbtp` is -inf for
    digital silence."""
    lufs: object
    peak_dbtp: float

    @property
    def silent(self) -> bool:
        return self.lufs is None

    def as_dict(self) -> dict:
        return {"lufs": None if self.lufs is None else round(self.lufs, 2),
                "true_peak_dbtp": None if math.isinf(self.peak_dbtp) else round(self.peak_dbtp, 2)}


def _to_db(linear: float) -> float:
    return -math.inf if linear <= 0 else 20.0 * math.log10(linear)


def _parse_metadata(path: str) -> Level:
    """The last integrated loudness and the largest true peak in an `ametadata` file."""
    integrated, peak = None, 0.0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("lavfi.r128.I="):
                integrated = float(line[len("lavfi.r128.I="):])
            elif line.startswith("lavfi.r128.true_peak="):
                peak = max(peak, float(line[len("lavfi.r128.true_peak="):]))
    lufs = None if integrated is None or integrated <= GATE_FLOOR_LUFS + 1e-6 else integrated
    return Level(lufs, _to_db(peak))


def _as_bytes_view(samples: np.ndarray) -> memoryview:
    """The bytes of the samples without a copy (subprocess accepts a memoryview as input)."""
    return memoryview(np.ascontiguousarray(samples, dtype=np.float32).reshape(-1).view(np.uint8))


def measure(samples: np.ndarray, rate: int = 48000, ffmpeg: str = None) -> Level:
    """Integrated loudness and true peak of float stereo `samples`."""
    if len(samples) == 0:
        return Level(None, -math.inf)
    ffmpeg = ffmpeg or tools.ffmpeg_path()
    fd, meta = tempfile.mkstemp(prefix="ebur128-", suffix=".txt")
    os.close(fd)
    try:
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-f", "f32le", "-ar", str(rate),
               "-ac", "2", "-i", "-",
               "-af", f"ebur128=peak=true:metadata=1,ametadata=mode=print:file={meta}",
               "-f", "null", "-"]
        r = subprocess.run(cmd, input=_as_bytes_view(samples), capture_output=True)
        if r.returncode != 0:
            raise RuntimeError("ffmpeg could not measure the loudness: "
                               + r.stderr.decode("utf-8", "replace").strip()[-400:])
        return _parse_metadata(meta)
    finally:
        try:
            os.unlink(meta)
        except OSError:
            pass


def measure_file(path: str, ffmpeg: str = None) -> Level:
    """Loudness and true peak of the first audio stream of a media file, as ffmpeg
    decodes it (so for an AAC track: after the encoder)."""
    ffmpeg = ffmpeg or tools.ffmpeg_path()
    fd, meta = tempfile.mkstemp(prefix="ebur128-", suffix=".txt")
    os.close(fd)
    try:
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-vn",
               "-map", "0:a:0",
               "-af", f"ebur128=peak=true:metadata=1,ametadata=mode=print:file={meta}",
               "-f", "null", "-"]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg could not measure {path}: "
                               + r.stderr.decode("utf-8", "replace").strip()[-400:])
        return _parse_metadata(meta)
    finally:
        try:
            os.unlink(meta)
        except OSError:
            pass


def apply_limiter(samples: np.ndarray, ceiling_db: float, rate: int = 48000,
                  ffmpeg: str = None) -> np.ndarray:
    """A look-ahead limiter (ffmpeg `alimiter`) with the ceiling in dBFS. The delay of
    the look-ahead is compensated, so the result is as long as the input and in step
    with it. Auto level is off: the filter only lowers peaks."""
    ffmpeg = ffmpeg or tools.ffmpeg_path()
    limit = 10.0 ** (ceiling_db / 20.0)
    limit = min(1.0, max(0.0625, limit))
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-f", "f32le", "-ar", str(rate),
           "-ac", "2", "-i", "-",
           "-af", f"alimiter=limit={limit:.6f}:attack=5:release=50:level=disabled:latency=1",
           "-f", "f32le", "-"]
    r = subprocess.run(cmd, input=_as_bytes_view(samples), capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg could not limit the sound: "
                           + r.stderr.decode("utf-8", "replace").strip()[-400:])
    out = np.frombuffer(r.stdout, np.float32).reshape(-1, 2)
    if len(out) != len(samples):
        raise RuntimeError(f"the limiter changed the length: {len(samples)} -> {len(out)} samples")
    return out


# ── The gain of one segment (SPECS 5.4 step 5) ─────────────────

@dataclass
class SegmentGain:
    gain_db: float                  # the levelling gain, without gain_db_adjust
    lufs: object                    # the measured loudness of the segment, None when silent
    capped: bool = False            # the wanted gain was larger than max_gain_db
    wanted_db: object = None        # the gain that would reach the target (None when silent)
    silent: bool = False

    def as_dict(self) -> dict:
        return {"lufs": None if self.lufs is None else round(self.lufs, 2),
                "gain_db": round(self.gain_db, 2), "capped": self.capped,
                "wanted_db": None if self.wanted_db is None else round(self.wanted_db, 2),
                "silent": self.silent}


def segment_gain(level: Level, target_lufs: float, max_gain_db: float) -> SegmentGain:
    """`min(target - L, max_gain_db)`: a quiet segment gets at most `max_gain_db`; a loud
    one gets as much negative gain as it needs. A segment with no sound above the
    absolute gate gets gain 0."""
    if level.silent:
        return SegmentGain(0.0, None, silent=True)
    wanted = target_lufs - level.lufs
    capped = wanted > max_gain_db
    return SegmentGain(min(wanted, max_gain_db), level.lufs, capped=capped, wanted_db=wanted)


# ── The gain of the film (SPECS 5.4 step 7) ────────────────────

@dataclass
class FilmLevel:
    before: Level                   # the mix as it came in
    after: Level                    # the mix as it is returned
    gain_db: float = 0.0            # the one gain applied to the film (before the limiter)
    peak_before_limiter_dbtp: object = None   # the true peak after that gain, before any limiter
    limiter_ceilings_db: list = field(default_factory=list)   # one per limiter run
    reached_target: bool = False    # within LOUDNESS_TOLERANCE_LU of the target
    reached_peak: bool = False      # at or under the limit
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"before": self.before.as_dict(), "after": self.after.as_dict(),
                "gain_db": round(self.gain_db, 3),
                "peak_before_limiter_dbtp": None if self.peak_before_limiter_dbtp is None
                or math.isinf(self.peak_before_limiter_dbtp)
                else round(self.peak_before_limiter_dbtp, 2),
                "limiter_ceilings_db": [round(c, 2) for c in self.limiter_ceilings_db],
                "reached_target": self.reached_target, "reached_peak": self.reached_peak,
                "warnings": self.warnings}


LOUDNESS_TOLERANCE_LU = 0.5     # PRD section 11: within 0.5 LU of the target
LOUDNESS_AIM_LU = 0.1           # the limiter loop stops when the loudness is this close
LIMITER_STEP_DB = 0.5           # SPECS 5.4 step 7
LIMITER_TRIES = 6               # SPECS 5.4 says three; changed in the build (SPECS 5.4)
PEAK_MARGIN_DB = 0.01           # ebur128 reports the peak as a linear number with three decimals


def level_film(samples: np.ndarray, target_lufs: float, peak_limit_dbtp: float, rate: int = 48000,
               measure_fn=None, limiter_fn=None, tries: int = LIMITER_TRIES) -> tuple:
    """Bring the film to `target_lufs` with one gain; if the true peak is then above
    `peak_limit_dbtp`, limit it with a ceiling 0.5 dB under the limit and measure again,
    lowering the ceiling by 0.5 dB (or by the overshoot, if that is more) until the limit
    holds. A limiter takes loudness away: when the peak holds but the loudness is more than
    0.1 LU from the target, the gain before the limiter is changed by the difference (scaled
    by how much of a gain change the limiter let through, from the last two runs) and the
    limiter runs again. At most `tries` limiter runs are made.

    Returns (samples, FilmLevel). The input array is changed in place by the gain; the
    returned array is the input or, after a limiter run, a new array. A film with no sound
    above the gate is returned as it is, with a warning.
    """
    measure_fn = measure_fn or (lambda x: measure(x, rate))
    limiter_fn = limiter_fn or (lambda x, c: apply_limiter(x, c, rate))
    reported_limit = peak_limit_dbtp
    peak_limit_dbtp = reported_limit - PEAK_MARGIN_DB     # a reading is never above the limit by rounding
    before = measure_fn(samples)
    result = FilmLevel(before=before, after=before)
    if before.silent:
        result.warnings.append("the film has no sound above the gate (-70 LUFS): it is not levelled")
        return samples, result

    result.gain_db = target_lufs - before.lufs
    samples *= np.float32(10.0 ** (result.gain_db / 20.0))
    out, level = samples, measure_fn(samples)
    result.peak_before_limiter_dbtp = level.peak_dbtp
    if level.peak_dbtp > peak_limit_dbtp:
        ceiling = reported_limit - LIMITER_STEP_DB
        slope, last = 1.0, None                 # last: (gain, loudness) of the previous run that held
        for run in range(tries):
            result.limiter_ceilings_db.append(ceiling)
            out = limiter_fn(samples, ceiling)
            level = measure_fn(out)
            if level.peak_dbtp > peak_limit_dbtp:
                # Lower the ceiling by half a dB, or by the overshoot if that is more
                # (the limiter works on sample peaks; the true peak lies a little above).
                ceiling -= max(LIMITER_STEP_DB, level.peak_dbtp - peak_limit_dbtp + 0.05)
                last = None
                continue
            if level.lufs is None:
                break
            miss = target_lufs - level.lufs
            if abs(miss) <= LOUDNESS_AIM_LU or run == tries - 1:
                break
            if last is not None and abs(result.gain_db - last[0]) > 1e-9:
                slope = min(1.0, max(0.1, (level.lufs - last[1]) / (result.gain_db - last[0])))
            last = (result.gain_db, level.lufs)
            step = miss / slope
            samples *= np.float32(10.0 ** (step / 20.0))
            result.gain_db += step
    result.after = level
    result.reached_peak = level.peak_dbtp <= peak_limit_dbtp
    result.reached_target = level.lufs is not None and \
        abs(level.lufs - target_lufs) <= LOUDNESS_TOLERANCE_LU
    if not result.reached_peak:
        result.warnings.append(
            f"the true peak is {level.peak_dbtp:.2f} dBTP, above the limit of {reported_limit:g} dBTP "
            f"after {len(result.limiter_ceilings_db)} limiter run(s)")
    if not result.reached_target:
        shown = "none" if level.lufs is None else f"{level.lufs:.2f}"
        result.warnings.append(
            f"the film's integrated loudness is {shown} LUFS, more than "
            f"{LOUDNESS_TOLERANCE_LU:g} LU from the target of {target_lufs:g} LUFS")
    return out, result
