"""
Matching for dual-system recording (BMPCC 4K + Zoom H2N).
Primary matching: duration similarity (files recorded together have ~same length).
Secondary: onset-envelope cross-correlation to disambiguate near-equal durations
and to find the precise sync offset.

OFFSET CONTRACT: offset = t_audio - t_video.  offset > 0 means the same event
occurs `offset` seconds LATER in the audio file than in the video's scratch
track (the recorder was started earlier than the camera).
"""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.optimize import linear_sum_assignment
from tqdm import tqdm

import config
from project import SceneMatch

_FFMPEG_HINT = ("ffmpeg/ffprobe not found. Install it with: brew install ffmpeg")

# Envelope resolution for the coarse search / similarity score.
_ENV_WINDOW_SEC = 0.02
# Half width of the raw-signal refinement search around the coarse lag.
_REFINE_HALF_WINDOW_SEC = 0.1
# Duration difference threshold (seconds) for considering two audio files
# "ambiguous" candidates for the same video.
_AMBIGUITY_THRESHOLD_SEC = 5.0
# Scale (seconds) of the duration margin that maps to confidence 1.0.
_DURATION_MARGIN_SCALE_SEC = 5.0
# Scale of the correlation margin that maps to confidence 1.0.
_CORR_MARGIN_SCALE = 0.2
# Weight of (1 - correlation) in the adjusted cost, in units of the
# ambiguity threshold.
_CORR_WEIGHT_FACTOR = 20.0


def _run(cmd, **kwargs):
    """subprocess.run with a clear error if the binary is missing."""
    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"{cmd[0]}: {_FFMPEG_HINT}") from e


def _get_duration(file_path: str) -> float:
    """Get file duration in seconds."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    result = _run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {file_path}")
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(f"ffprobe returned no duration for {file_path}")


def _extract_audio_raw(file_path: str, max_duration: float = None) -> np.ndarray:
    """Extract audio as mono numpy array for offset detection."""
    max_dur = max_duration or config.MATCH_MAX_DURATION_SEC
    sr = config.MATCH_SAMPLE_RATE

    cmd = [
        "ffmpeg", "-y",
        "-i", str(file_path),
        "-t", str(max_dur),      # output option: limits decoded audio length
        "-vn",
        "-ac", "1",
        "-ar", str(sr),
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "pipe:1",
    ]

    result = _run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to extract audio from {file_path}")

    audio = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32)
    if len(audio) > 0 and audio.max() != audio.min():
        audio = audio / max(abs(audio.max()), abs(audio.min()))
    return audio


def _envelope(audio: np.ndarray, window_sec: float = _ENV_WINDOW_SEC) -> np.ndarray:
    """Mean absolute amplitude per non-overlapping window (vectorised)."""
    window = max(1, int(config.MATCH_SAMPLE_RATE * window_sec))
    n = len(audio) // window
    if n == 0:
        return np.zeros(0)
    return np.abs(audio[: n * window]).reshape(n, window).mean(axis=1)


def _onset_envelope(audio: np.ndarray, window_sec: float = _ENV_WINDOW_SEC) -> np.ndarray:
    """
    Onset strength per window: positive part of the first difference of
    log(envelope + eps), mean-subtracted.  Steady ambience yields ~0 here
    while claps / transients stand out regardless of overall gain.
    """
    env = _envelope(audio, window_sec)
    if len(env) < 2:
        return np.zeros(0)
    eps = 1e-3 * max(float(env.max()), 1e-9)
    log_env = np.log(env + eps)
    onset = np.maximum(np.diff(log_env), 0.0)
    return onset - onset.mean()


def _ncc(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, int]:
    """
    Normalised full cross-correlation of two zero-mean vectors.
    Returns (corr, zero_lag_index); corr[k] pairs a[n] with b[n + k - zero_lag_index],
    so a positive lag means the pattern occurs later in b.  Values lie in [-1, 1].
    """
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return np.zeros(1), 0
    corr = signal.fftconvolve(b / nb, (a / na)[::-1], mode="full")
    return corr, len(a) - 1


def _highpass(audio: np.ndarray, cutoff_hz: float = 500.0) -> np.ndarray:
    """Remove wind / rumble so transients dominate the fine correlation."""
    sos = signal.butter(4, cutoff_hz, btype="highpass", fs=config.MATCH_SAMPLE_RATE, output="sos")
    return signal.sosfilt(sos, audio)


def _find_offset(video_audio: np.ndarray, audio_audio: np.ndarray) -> float:
    """
    Sync offset between the camera scratch track and the recorder audio.

    Returns offset = t_audio - t_video in seconds: positive means the same
    event occurs LATER in the audio file than in the video (recorder started
    earlier than the camera).

    1. Coarse lag from the normalised cross-correlation of onset envelopes
       (robust to constant ambience and gain differences).
    2. Refinement: cross-correlation of high-passed raw signals within
       +-_REFINE_HALF_WINDOW_SEC of the coarse lag, with parabolic peak
       interpolation (sub-sample precision).
    """
    if len(video_audio) == 0 or len(audio_audio) == 0:
        return 0.0
    sr = config.MATCH_SAMPLE_RATE

    on_v = _onset_envelope(video_audio)
    on_a = _onset_envelope(audio_audio)
    if len(on_v) < 2 or len(on_a) < 2:
        return 0.0
    corr, zero = _ncc(on_v, on_a)
    if len(corr) < 2:
        return 0.0
    coarse_windows = int(np.argmax(corr)) - zero
    coarse_samples = int(round(coarse_windows * _ENV_WINDOW_SEC * sr))

    # Fine stage on band-limited raw signals.
    hv = _highpass(video_audio.astype(np.float64))
    ha = _highpass(audio_audio.astype(np.float64))
    hv = hv - hv.mean()
    ha = ha - ha.mean()
    nv, na = np.linalg.norm(hv), np.linalg.norm(ha)
    if nv == 0 or na == 0:
        return coarse_samples / sr
    full = signal.fftconvolve(ha / na, (hv / nv)[::-1], mode="full")
    zero_f = len(hv) - 1
    half = int(_REFINE_HALF_WINDOW_SEC * sr) + int(_ENV_WINDOW_SEC * sr)
    lo = max(1, zero_f + coarse_samples - half)
    hi = min(len(full) - 2, zero_f + coarse_samples + half)
    if hi <= lo:
        return coarse_samples / sr
    k = lo + int(np.argmax(full[lo:hi + 1]))
    # Parabolic interpolation around the peak
    y0, y1, y2 = full[k - 1], full[k], full[k + 1]
    denom = y0 - 2 * y1 + y2
    delta = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
    delta = float(np.clip(delta, -1.0, 1.0))
    return float((k - zero_f + delta) / sr)


def find_files(directory: str, extensions: set) -> list[Path]:
    """Find all files with given extensions in a directory."""
    d = Path(directory)
    if not d.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    files = []
    for ext in extensions:
        files.extend(d.glob(f"*{ext}"))
        files.extend(d.glob(f"*{ext.upper()}"))
    return sorted(set(files))


def _correlation_score(video_audio: np.ndarray, audio_audio: np.ndarray) -> float:
    """
    Similarity of two recordings in [0, 1]: peak of the normalised
    cross-correlation of their onset envelopes.  Steady background noise does
    not contribute because only positive level changes (transients) are used
    and the means are removed.
    """
    on_v = _onset_envelope(video_audio)
    on_a = _onset_envelope(audio_audio)
    if len(on_v) < 2 or len(on_a) < 2:
        return 0.0
    corr, _ = _ncc(on_v, on_a)
    return float(np.clip(np.max(corr), 0.0, 1.0))


def _ambiguous_candidates(cost: np.ndarray, assignment: dict, threshold: float) -> dict:
    """Map row -> list of columns whose cost is within `threshold` of the assigned
    column's cost.  Only rows with more than one candidate are returned."""
    out = {}
    for r, c in assignment.items():
        cands = [j for j in range(cost.shape[1]) if abs(cost[r, j] - cost[r, c]) <= threshold]
        if len(cands) > 1:
            out[r] = cands
    return out


def _resolve_assignment(cost: np.ndarray, corr_scores: dict, ambiguity_threshold: float) -> dict:
    """
    Re-solve the global assignment with correlation evidence.

    corr_scores: {(row, col): score in [0, 1]} for pairs where correlation
    was computed.  For every row that has any score, each column gets an extra
    cost W * (best_score_in_row - score), where a missing score counts as 0
    (relative to the row's best so rows with uniformly weak correlation fall
    back to durations).  W = _CORR_WEIGHT_FACTOR * ambiguity_threshold, so a
    0.1 correlation difference is worth 2 * threshold seconds of duration
    error.  Solved once with linear_sum_assignment; returns {row: col}.
    """
    adjusted = np.array(cost, dtype=float)
    weight = _CORR_WEIGHT_FACTOR * ambiguity_threshold
    rows = {r for (r, _c) in corr_scores}
    for r in rows:
        row_scores = np.array([corr_scores.get((r, j), 0.0) for j in range(adjusted.shape[1])])
        adjusted[r] += weight * (row_scores.max() - row_scores)
    ri, ci = linear_sum_assignment(adjusted)
    return {int(r): int(c) for r, c in zip(ri, ci)}


def _pair_confidence(cost: np.ndarray, corr_scores: dict, assignment: dict,
                     row: int, ambiguity_threshold: float) -> float:
    """
    Margin-based confidence in [0, 1] for the chosen pair of `row`.

      * Pair decided by correlation (the row has computed scores for >= 2
        columns): conf = (score_chosen - best_other_score) / 0.2, clipped.
        A margin of 0.06 gives 0.3 (green), under 0.03 gives < 0.15 (red).
      * Otherwise duration margin: conf = (cost_runner_up - cost_chosen) / 5 s,
        clipped; clear matches (runner-up >= 1.5 s worse) land >= 0.3, a
        coin-flip (< 0.75 s apart) lands < 0.15.
      * Duration penalty: |dur diff| >= 30 s caps confidence at 0.1;
        10-30 s caps at 0.25.
    """
    c = assignment[row]
    chosen_cost = float(cost[row, c])
    others = [j for j in range(cost.shape[1]) if j != c]

    row_scores = {j: s for (r, j), s in corr_scores.items() if r == row}
    if c in row_scores and len(row_scores) >= 2:
        other_best = max(s for j, s in row_scores.items() if j != c)
        conf = (row_scores[c] - other_best) / _CORR_MARGIN_SCALE
    elif others:
        runner_up = min(float(cost[row, j]) for j in others)
        conf = (runner_up - chosen_cost) / _DURATION_MARGIN_SCALE_SEC
    else:
        conf = 1.0
    conf = float(np.clip(conf, 0.0, 1.0))

    if chosen_cost >= 30.0:
        conf = min(conf, 0.1)
    elif chosen_cost >= 10.0:
        conf = min(conf, 0.25)
    return conf


class _AudioCache:
    """Lazy per-file extraction cache with optional threaded prefetch."""

    def __init__(self, max_duration):
        self.max_duration = max_duration
        self._data = {}

    def _load(self, path):
        try:
            return _extract_audio_raw(str(path), self.max_duration)
        except FileNotFoundError:
            raise
        except Exception as e:  # noqa: BLE001 - cached and re-raised on get()
            return e

    def prefetch(self, paths, workers=4):
        todo = [p for p in dict.fromkeys(paths) if p not in self._data]
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for p, res in zip(todo, pool.map(self._load, todo)):
                self._data[p] = res

    def get(self, path):
        if path not in self._data:
            self._data[path] = self._load(path)
        res = self._data[path]
        if isinstance(res, Exception):
            raise res
        return res


def match_files(
    video_dir: str,
    audio_dir: str,
    max_duration: float = None,
    min_confidence: float = None,
) -> list[SceneMatch]:
    """
    Match video files to audio files.

    Strategy:
      1. Duration cost matrix |video_dur - audio_dur| + Hungarian assignment.
      2. For videos with several audio candidates within the ambiguity
         threshold, compute onset-envelope correlation for all candidate pairs,
         add W * (best - score) to the cost and re-solve once (global, so
         chained reassignments cannot undo each other).
      3. Margin-based confidence (see _pair_confidence).
      4. Sync offset per final pair (offset = t_audio - t_video, see _find_offset).

    Args:
        video_dir: Directory containing BMPCC video files.
        audio_dir: Directory containing Zoom H2N audio files.
        max_duration: Max audio duration for correlation / offset detection.
        min_confidence: Not used (kept for API compatibility).

    Returns:
        List of SceneMatch objects (one per matched pair), sorted by video name.
    """
    video_files = find_files(video_dir, config.VIDEO_EXTENSIONS)
    audio_files = find_files(audio_dir, config.AUDIO_EXTENSIONS)

    if not video_files:
        raise FileNotFoundError(f"No video files found in {video_dir}")
    if not audio_files:
        raise FileNotFoundError(f"No audio files found in {audio_dir}")

    print(f"Found {len(video_files)} video files and {len(audio_files)} audio files.")

    # -- Step 1: durations ------------------------------------------------
    print("\nGetting file durations...")
    video_durations = {}
    skipped = []
    for vf in tqdm(video_files, desc="Video durations"):
        try:
            video_durations[vf] = _get_duration(str(vf))
        except RuntimeError as e:
            print(f"  Warning: {vf.name}: {e}")
            skipped.append(vf)

    audio_durations = {}
    for af in tqdm(audio_files, desc="Audio durations"):
        try:
            audio_durations[af] = _get_duration(str(af))
        except RuntimeError as e:
            print(f"  Warning: {af.name}: {e}")
            skipped.append(af)

    v_files = list(video_durations.keys())
    a_files = list(audio_durations.keys())
    v_durs = [video_durations[f] for f in v_files]
    a_durs = [audio_durations[f] for f in a_files]
    n_vid, n_aud = len(v_files), len(a_files)
    if n_vid == 0 or n_aud == 0:
        raise RuntimeError("No readable video or audio files (ffprobe failed for all of one kind).")

    # -- Step 2: duration cost + Hungarian --------------------------------
    print(f"\nBuilding duration cost matrix ({n_vid} videos x {n_aud} audio)...")
    cost = np.abs(np.array(v_durs)[:, None] - np.array(a_durs)[None, :])
    row_idx, col_idx = linear_sum_assignment(cost)
    assignment = {int(r): int(c) for r, c in zip(row_idx, col_idx)}

    # -- Step 3: ambiguity resolution via correlation ---------------------
    print("\nChecking for ambiguous duration matches...")
    cache = _AudioCache(max_duration)
    candidates = _ambiguous_candidates(cost, assignment, _AMBIGUITY_THRESHOLD_SEC)
    corr_scores = {}

    if candidates:
        need = set()
        for r, cols in candidates.items():
            need.add(v_files[r])
            need.update(a_files[j] for j in cols)
        cache.prefetch(sorted(need))

    for r, cols in candidates.items():
        print(f"  Ambiguous: {v_files[r].name} ({v_durs[r]:.1f}s) has "
              f"{len(cols)} candidates within {_AMBIGUITY_THRESHOLD_SEC}s")
        try:
            v_audio = cache.get(v_files[r])
        except FileNotFoundError:
            raise
        except Exception as e:
            print(f"    Could not extract video audio: {e} - keeping duration match")
            continue
        for j in cols:
            try:
                score = _correlation_score(v_audio, cache.get(a_files[j]))
            except FileNotFoundError:
                raise
            except Exception as e:
                print(f"    vs {a_files[j].name}: extraction failed ({e})")
                continue
            corr_scores[(r, j)] = score
            tag = " (duration pick)" if j == assignment[r] else ""
            print(f"    vs {a_files[j].name}: correlation = {score:.4f}{tag}")

    changed = 0
    if corr_scores:
        new_assignment = _resolve_assignment(cost, corr_scores, _AMBIGUITY_THRESHOLD_SEC)
        for r in sorted(new_assignment):
            if new_assignment[r] != assignment.get(r):
                changed += 1
                print(f"  -> Re-assigned {v_files[r].name}: "
                      f"{a_files[assignment[r]].name} -> {a_files[new_assignment[r]].name}")
        assignment = new_assignment
        print(f"\n  Cross-correlation changed {changed} assignment(s).")
    else:
        print("  No ambiguous matches found - all durations are distinct.")

    # -- Step 4: final pairs, confidence, logging -------------------------
    print("\nFinal matches:")
    matched_pairs = []
    for r, c in sorted(assignment.items()):
        dur_diff = float(cost[r, c])
        confidence = _pair_confidence(cost, corr_scores, assignment, r,
                                      _AMBIGUITY_THRESHOLD_SEC)
        status = "OK" if dur_diff < 10 else "WARN" if dur_diff < 30 else "BAD"
        print(f"  [{status}] {v_files[r].name} ({v_durs[r]:.1f}s) <-> {a_files[c].name} "
              f"({a_durs[c]:.1f}s)  diff: {dur_diff:.1f}s  conf: {confidence:.2f}")
        matched_pairs.append((v_files[r], a_files[c], v_durs[r], a_durs[c], dur_diff, confidence))

    # -- Step 5: unmatched files ------------------------------------------
    used_v = set(assignment.keys())
    used_a = set(assignment.values())
    un_v = [v_files[i] for i in range(n_vid) if i not in used_v]
    un_a = [a_files[j] for j in range(n_aud) if j not in used_a]
    if un_v:
        print(f"\nVideos with no audio ({len(un_v)}):")
        for f in un_v:
            print(f"  - {f.name}")
    if un_a:
        print(f"\nAudio files with no video ({len(un_a)}):")
        for f in un_a:
            print(f"  - {f.name}")
    if skipped:
        print(f"\nSkipped (unreadable by ffprobe): {', '.join(f.name for f in skipped)}")

    # -- Step 6: sync offsets ---------------------------------------------
    print("\nFinding sync offsets...")
    cache.prefetch([p for vf, af, *_ in matched_pairs for p in (vf, af)])
    matches = []
    for i, (vf, af, v_dur, a_dur, dur_diff, confidence) in enumerate(
        tqdm(matched_pairs, desc="Sync offsets")
    ):
        offset = 0.0
        try:
            offset = _find_offset(cache.get(vf), cache.get(af))
        except FileNotFoundError:
            raise
        except Exception as e:
            print(f"  Warning: offset detection failed for {vf.name}: {e}")
        matches.append(SceneMatch(
            index=i,
            video_path=str(vf),
            audio_path=str(af),
            video_duration=float(v_dur),
            audio_duration=float(a_dur),
            offset=float(offset),
            confidence=float(confidence),
        ))

    matches.sort(key=lambda m: Path(m.video_path).name)
    for i, m in enumerate(matches):
        m.index = i

    print(f"\nMatched {len(matches)} pairs:")
    good = sum(1 for p in matched_pairs if p[4] < 10)
    warn = sum(1 for p in matched_pairs if 10 <= p[4] < 30)
    bad = [p for p in matched_pairs if p[4] >= 30]
    print(f"  Good (<10s diff): {good}")
    if warn:
        print(f"  Uncertain (10-30s diff): {warn} - check these manually")
    if bad:
        print(f"  Likely wrong (>=30s diff): {len(bad)} - these may be mismatched:")
        for vf, af, _vd, _ad, d, _c in bad:
            print(f"    BAD: {vf.name} <-> {af.name} (diff {d:.1f}s)")
    if changed:
        print(f"  Cross-correlation resolved {changed} ambiguous case(s)")

    return matches
