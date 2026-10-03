"""
Matching for dual-system recording (BMPCC 4K + Zoom H2N).
Primary matching: duration similarity (files recorded together have ~same length).
Secondary: audio cross-correlation to find the precise sync offset.
"""

import subprocess
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.optimize import linear_sum_assignment
from tqdm import tqdm

import config
from project import SceneMatch


def _get_duration(file_path: str) -> float:
    """Get file duration in seconds."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {file_path}")
    return float(result.stdout.strip())


def _extract_audio_raw(file_path: str, max_duration: float = None) -> np.ndarray:
    """Extract audio as mono numpy array for offset detection."""
    max_dur = max_duration or config.MATCH_MAX_DURATION_SEC
    sr = config.MATCH_SAMPLE_RATE

    cmd = [
        "ffmpeg", "-y",
        "-i", str(file_path),
        "-t", str(max_dur),
        "-ac", "1",
        "-ar", str(sr),
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "pipe:1",
    ]

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to extract audio from {file_path}")

    audio = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32)
    if len(audio) > 0 and audio.max() != audio.min():
        audio = audio / max(abs(audio.max()), abs(audio.min()))
    return audio


def _find_offset(audio_a: np.ndarray, audio_b: np.ndarray) -> float:
    """
    Find the time offset between two audio signals using cross-correlation.
    Returns offset in seconds (positive = audio_b starts later).
    """
    if len(audio_a) == 0 or len(audio_b) == 0:
        return 0.0

    # Use amplitude envelope for more robust matching across different mics
    window = int(config.MATCH_SAMPLE_RATE * 0.05)  # 50ms window
    env_a = np.array([np.mean(np.abs(audio_a[i:i+window]))
                      for i in range(0, len(audio_a) - window, window)])
    env_b = np.array([np.mean(np.abs(audio_b[i:i+window]))
                      for i in range(0, len(audio_b) - window, window)])

    if len(env_a) < 2 or len(env_b) < 2:
        return 0.0

    corr = signal.fftconvolve(env_b, env_a[::-1], mode="full")
    peak_idx = np.argmax(np.abs(corr))
    offset_windows = peak_idx - (len(env_a) - 1)
    offset_sec = offset_windows * window / config.MATCH_SAMPLE_RATE

    return float(offset_sec)


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


def _correlation_score(audio_a: np.ndarray, audio_b: np.ndarray) -> float:
    """
    Compute normalised cross-correlation peak between two audio signals.
    Returns a score in [0, 1] where higher = more similar.
    Used to verify/disambiguate duration-based matches.
    """
    if len(audio_a) == 0 or len(audio_b) == 0:
        return 0.0

    # Use amplitude envelope (50ms windows) — robust across different mics
    window = int(config.MATCH_SAMPLE_RATE * 0.05)
    env_a = np.array([np.mean(np.abs(audio_a[i:i+window]))
                      for i in range(0, len(audio_a) - window, window)])
    env_b = np.array([np.mean(np.abs(audio_b[i:i+window]))
                      for i in range(0, len(audio_b) - window, window)])

    if len(env_a) < 2 or len(env_b) < 2:
        return 0.0

    # Normalise envelopes
    norm_a = np.linalg.norm(env_a)
    norm_b = np.linalg.norm(env_b)
    if norm_a == 0 or norm_b == 0:
        return 0.0

    corr = signal.fftconvolve(env_b / norm_b, (env_a / norm_a)[::-1], mode="full")
    return float(np.max(np.abs(corr)))


# Duration difference threshold (seconds) for considering two audio files
# "ambiguous" candidates for the same video.  When the best and second-best
# audio candidates are within this margin, cross-correlation is used to pick.
_AMBIGUITY_THRESHOLD_SEC = 5.0


def match_files(
    video_dir: str,
    audio_dir: str,
    max_duration: float = None,
    min_confidence: float = None,
) -> list[SceneMatch]:
    """
    Match video files to audio files.

    Two-stage strategy:
      1. **Duration matching** — build a cost matrix of |video_dur − audio_dur|
         and solve with the Hungarian algorithm for the optimal 1-to-1 assignment.
      2. **Cross-correlation tiebreaker** — for any video whose top-2 audio
         candidates have similar durations (within AMBIGUITY_THRESHOLD), extract
         audio from both the camera scratch track and the candidate H2N files and
         pick the one with the highest waveform correlation (claps, transients).
      3. Find the precise sync offset for each final pair.

    Args:
        video_dir: Directory containing BMPCC video files.
        audio_dir: Directory containing Zoom H2N audio files.
        max_duration: Max audio duration for offset detection.
        min_confidence: Not used (kept for API compatibility).

    Returns:
        List of SceneMatch objects (one per matched pair).
    """
    video_files = find_files(video_dir, config.VIDEO_EXTENSIONS)
    audio_files = find_files(audio_dir, config.AUDIO_EXTENSIONS)

    if not video_files:
        raise FileNotFoundError(f"No video files found in {video_dir}")
    if not audio_files:
        raise FileNotFoundError(f"No audio files found in {audio_dir}")

    print(f"Found {len(video_files)} video files and {len(audio_files)} audio files.")

    # ── Step 1: Get all durations ────────────────────────────────────
    print("\nGetting file durations...")
    video_durations = {}
    for vf in tqdm(video_files, desc="Video durations"):
        try:
            video_durations[vf] = _get_duration(str(vf))
        except RuntimeError as e:
            print(f"  Warning: {vf.name}: {e}")

    audio_durations = {}
    for af in tqdm(audio_files, desc="Audio durations"):
        try:
            audio_durations[af] = _get_duration(str(af))
        except RuntimeError as e:
            print(f"  Warning: {af.name}: {e}")

    v_files = list(video_durations.keys())
    a_files = list(audio_durations.keys())
    v_durs = [video_durations[f] for f in v_files]
    a_durs = [audio_durations[f] for f in a_files]

    n_vid = len(v_files)
    n_aud = len(a_files)

    # ── Step 2: Duration cost matrix + Hungarian ─────────────────────
    print(f"\nBuilding duration cost matrix ({n_vid} videos x {n_aud} audio)...")
    cost = np.zeros((n_vid, n_aud))
    for i in range(n_vid):
        for j in range(n_aud):
            cost[i, j] = abs(v_durs[i] - a_durs[j])

    row_idx, col_idx = linear_sum_assignment(cost)

    # ── Step 3: Detect ambiguous matches & resolve with correlation ──
    # For each matched video, check if another audio file had a very
    # similar duration.  If so, extract audio and let cross-correlation
    # pick the winner (claps / transients give strong peaks).
    print("\nChecking for ambiguous duration matches...")

    # Pre-extract video scratch audio only for ambiguous cases (lazy cache)
    _video_audio_cache: dict[Path, np.ndarray] = {}
    _audio_audio_cache: dict[Path, np.ndarray] = {}

    def _get_video_audio(vf: Path) -> np.ndarray:
        if vf not in _video_audio_cache:
            _video_audio_cache[vf] = _extract_audio_raw(str(vf), max_duration)
        return _video_audio_cache[vf]

    def _get_audio_audio(af: Path) -> np.ndarray:
        if af not in _audio_audio_cache:
            _audio_audio_cache[af] = _extract_audio_raw(str(af), max_duration)
        return _audio_audio_cache[af]

    # Build initial assignment as a mutable dict: video_idx -> audio_idx
    assignment = {r: c for r, c in zip(row_idx, col_idx)}
    assigned_audio = set(assignment.values())

    reassign_count = 0
    for r in row_idx:
        c_best = assignment[r]
        best_cost = cost[r, c_best]

        # Find all audio files with duration within the ambiguity threshold
        candidates = []
        for j in range(n_aud):
            if abs(cost[r, j] - best_cost) <= _AMBIGUITY_THRESHOLD_SEC:
                candidates.append(j)

        if len(candidates) <= 1:
            continue  # Unambiguous — only one plausible candidate

        # Multiple audio files have similar durations → use cross-correlation
        print(f"  Ambiguous: {v_files[r].name} ({v_durs[r]:.1f}s) has "
              f"{len(candidates)} candidates within {_AMBIGUITY_THRESHOLD_SEC}s")

        try:
            v_audio = _get_video_audio(v_files[r])
        except Exception as e:
            print(f"    Could not extract video audio: {e} — keeping duration match")
            continue

        best_corr = -1.0
        best_j = c_best
        for j in candidates:
            try:
                a_audio = _get_audio_audio(a_files[j])
                score = _correlation_score(v_audio, a_audio)
                status_tag = " (current)" if j == c_best else ""
                print(f"    vs {a_files[j].name}: correlation = {score:.4f}{status_tag}")
                if score > best_corr:
                    best_corr = score
                    best_j = j
            except Exception as e:
                print(f"    vs {a_files[j].name}: extraction failed ({e})")

        if best_j != c_best:
            # Check if best_j is already assigned to another video
            other_vid = None
            for rv, cv in assignment.items():
                if cv == best_j:
                    other_vid = rv
                    break

            if other_vid is not None:
                # Swap: give the other video our old audio
                print(f"    -> Swapping: {v_files[r].name} gets {a_files[best_j].name}, "
                      f"{v_files[other_vid].name} gets {a_files[c_best].name}")
                assignment[other_vid] = c_best
            assignment[r] = best_j
            reassign_count += 1

    if reassign_count:
        print(f"\n  Cross-correlation resolved {reassign_count} ambiguous match(es).")
    else:
        print("  No ambiguous matches found — all durations are distinct.")

    # ── Step 4: Build match pairs ────────────────────────────────────
    print("\nFinal matches:")
    matched_pairs = []
    for r, c in assignment.items():
        vf = v_files[r]
        af = a_files[c]
        dur_diff = cost[r, c]
        v_dur = v_durs[r]
        a_dur = a_durs[c]

        if v_dur > 0:
            confidence = max(0, 1.0 - (dur_diff / v_dur))
        else:
            confidence = 0.0

        status = "OK" if dur_diff < 10 else "WARN" if dur_diff < 30 else "BAD"
        print(f"  [{status}] {vf.name} ({v_dur:.1f}s) <-> {af.name} ({a_dur:.1f}s)  "
              f"diff: {dur_diff:.1f}s  conf: {confidence:.2f}")

        matched_pairs.append((vf, af, v_dur, a_dur, dur_diff, confidence))

    # ── Step 5: Find precise sync offset for each pair ───────────────
    print("\nFinding sync offsets...")
    matches = []

    for i, (vf, af, v_dur, a_dur, dur_diff, confidence) in enumerate(
        tqdm(matched_pairs, desc="Sync offsets")
    ):
        offset = 0.0
        try:
            v_audio = _get_video_audio(vf) if vf in _video_audio_cache else _extract_audio_raw(str(vf), max_duration)
            a_audio = _get_audio_audio(af) if af in _audio_audio_cache else _extract_audio_raw(str(af), max_duration)
            offset = _find_offset(v_audio, a_audio)
        except Exception as e:
            print(f"  Warning: offset detection failed for {vf.name}: {e}")

        match = SceneMatch(
            index=i,
            video_path=str(vf),
            audio_path=str(af),
            video_duration=float(v_dur),
            audio_duration=float(a_dur),
            offset=float(offset),
            confidence=float(confidence),
        )
        matches.append(match)

    # Sort by video filename
    matches.sort(key=lambda m: Path(m.video_path).name)
    for i, m in enumerate(matches):
        m.index = i

    # Summary
    print(f"\nMatched {len(matches)} pairs:")
    good = sum(1 for _, _, _, _, d, _ in matched_pairs if d < 10)
    warn = sum(1 for _, _, _, _, d, _ in matched_pairs if 10 <= d < 30)
    bad = sum(1 for _, _, _, _, d, _ in matched_pairs if d >= 30)
    print(f"  Good (<10s diff): {good}")
    if warn:
        print(f"  Uncertain (10-30s diff): {warn} — check these manually")
    if bad:
        print(f"  Likely wrong (>30s diff): {bad} — these may be mismatched")
    if reassign_count:
        print(f"  Cross-correlation resolved {reassign_count} ambiguous case(s)")

    return matches
