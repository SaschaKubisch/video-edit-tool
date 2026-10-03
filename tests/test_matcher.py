"""Tests for matcher.py (stdlib unittest + numpy/scipy only)."""

import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

import config
import matcher

SR = config.MATCH_SAMPLE_RATE
HALF_FRAME = 1.0 / 48.0


def _clap(rng, dur=0.04):
    n = int(dur * SR)
    return rng.standard_normal(n) * np.exp(-np.arange(n) / (0.008 * SR))


def make_track(events, total_sec, gain=1.0, noise=0.01, ambience=0.0,
               seed=0, clap_seed=123):
    """Noise floor + ambience + claps at the given times (seconds).

    Clap waveform depends only on clap_seed, so the same event looks the same
    in both tracks; noise/ambience are independent per seed.
    """
    rng = np.random.default_rng(seed)
    n = int(total_sec * SR)
    x = rng.standard_normal(n) * noise
    if ambience:
        x += rng.standard_normal(n) * ambience
    crng = np.random.default_rng(clap_seed)
    clap = _clap(crng)
    for t in events:
        i = int(round(t * SR))
        if 0 <= i and i + len(clap) <= n:
            x[i:i + len(clap)] += clap * gain
    return x.astype(np.float32)


EVENTS = [2.0, 3.7, 6.1, 9.4, 12.2, 15.0]


class OffsetTests(unittest.TestCase):
    def _check(self, shift, ambience=0.0, gain=0.4):
        dur = 20.0
        video = make_track(EVENTS, dur, gain=1.0, noise=0.01,
                           ambience=ambience, seed=1)
        # audio: same events at t_v + shift, different gain/noise/ambience
        ev_a = [t + shift for t in EVENTS]
        audio = make_track(ev_a, dur, gain=gain, noise=0.02,
                           ambience=ambience * 0.8, seed=2)
        off = matcher._find_offset(video, audio)
        self.assertAlmostEqual(off, shift, delta=HALF_FRAME,
                               msg=f"expected {shift}, got {off}")

    def test_positive_shift(self):
        self._check(1.237)

    def test_negative_shift(self):
        self._check(-0.8)

    def test_positive_shift_loud_ambience(self):
        self._check(1.237, ambience=0.08)

    def test_negative_shift_loud_ambience(self):
        self._check(-0.8, ambience=0.08)

    def test_empty(self):
        self.assertEqual(matcher._find_offset(np.zeros(0), np.zeros(100)), 0.0)


class CorrelationScoreTests(unittest.TestCase):
    def setUp(self):
        self.video = make_track(EVENTS, 20.0, gain=1.0, noise=0.01,
                                ambience=0.05, seed=1)

    def test_correct_beats_wrong(self):
        good = make_track([t + 0.9 for t in EVENTS], 20.0, gain=0.5,
                          noise=0.02, ambience=0.05, seed=2)
        wrong_events = [1.1, 4.9, 7.3, 8.2, 13.7, 17.5]
        wrong = make_track(wrong_events, 20.0, gain=1.0, noise=0.01,
                           ambience=0.05, seed=3)
        s_good = matcher._correlation_score(self.video, good)
        s_wrong = matcher._correlation_score(self.video, wrong)
        self.assertGreater(s_good, s_wrong + 0.15)
        self.assertLessEqual(s_good, 1.0)
        self.assertGreaterEqual(s_wrong, 0.0)

    def test_louder_constant_background_does_not_win(self):
        good = make_track([t + 0.9 for t in EVENTS], 20.0, gain=0.5,
                          noise=0.02, ambience=0.05, seed=2)
        wrong = make_track([1.1, 4.9, 7.3, 8.2, 13.7, 17.5], 20.0, gain=0.2,
                           noise=0.01, ambience=0.5, seed=3)
        s_good = matcher._correlation_score(self.video, good)
        s_wrong = matcher._correlation_score(self.video, wrong)
        self.assertGreater(s_good, s_wrong + 0.15)

    def test_pure_noise_vs_noise_low(self):
        a = make_track([], 20.0, noise=0.3, seed=5)
        b = make_track([], 20.0, noise=0.3, seed=6)
        self.assertLess(matcher._correlation_score(a, b), 0.2)

    def test_empty(self):
        self.assertEqual(matcher._correlation_score(np.zeros(0), self.video), 0.0)


class ResolveAssignmentTests(unittest.TestCase):
    THR = 5.0

    def test_no_scores_is_plain_hungarian(self):
        cost = np.array([[0.0, 10.0], [10.0, 1.0]])
        self.assertEqual(matcher._resolve_assignment(cost, {}, self.THR),
                         {0: 0, 1: 1})

    def test_correlation_decides_near_identical_durations(self):
        cost = np.array([[0.10, 0.12, 0.11],
                         [0.12, 0.10, 0.13],
                         [0.11, 0.13, 0.10]])
        # truth: v0->a2, v1->a0, v2->a1 (a cyclic permutation)
        truth = {0: 2, 1: 0, 2: 1}
        scores = {}
        for i in range(3):
            for j in range(3):
                scores[(i, j)] = 0.6 if truth[i] == j else 0.05
        self.assertEqual(matcher._resolve_assignment(cost, scores, self.THR),
                         truth)

    def test_chained_reassignment(self):
        # Duration-only Hungarian gives identity.  Correlation says
        # v0 -> a1, v1 -> a2, v2 -> a0.  A greedy pairwise swap handles
        # v0 first (swap with v1: v1 gets a0), then v1 wants a2 (swap with v2:
        # v2 gets a0), but v2 wants a0 and ... the result depends on order and
        # can undo earlier decisions.  The global re-solve must get it right.
        cost = np.array([[0.0, 0.5, 1.0],
                         [0.5, 0.0, 0.5],
                         [1.0, 0.5, 0.0]])
        truth = {0: 1, 1: 2, 2: 0}
        scores = {(i, j): (0.7 if truth[i] == j else 0.1)
                  for i in range(3) for j in range(3)}
        self.assertEqual(matcher._resolve_assignment(cost, scores, self.THR),
                         truth)

    def test_partial_scores_and_unequal_counts(self):
        # 2 videos, 3 audio; only row 0 is ambiguous
        cost = np.array([[0.1, 0.2, 40.0],
                         [30.0, 30.0, 0.5]])
        scores = {(0, 0): 0.05, (0, 1): 0.5}
        self.assertEqual(matcher._resolve_assignment(cost, scores, self.THR),
                         {0: 1, 1: 2})

    def test_candidates(self):
        cost = np.array([[0.0, 3.0, 30.0], [20.0, 21.0, 0.0]])
        cands = matcher._ambiguous_candidates(cost, {0: 0, 1: 2}, 5.0)
        self.assertEqual(cands, {0: [0, 1]})


class ConfidenceTests(unittest.TestCase):
    def test_clear_duration_match_high(self):
        cost = np.array([[0.2, 40.0]])
        c = matcher._pair_confidence(cost, {}, {0: 0}, 0, 5.0)
        self.assertGreater(c, 0.8)

    def test_duration_coin_flip_low(self):
        cost = np.array([[1.0, 1.1]])
        c = matcher._pair_confidence(cost, {}, {0: 0}, 0, 5.0)
        self.assertLess(c, 0.15)

    def test_correlation_margin(self):
        cost = np.array([[1.0, 1.1]])
        clear = matcher._pair_confidence(
            cost, {(0, 0): 0.6, (0, 1): 0.05}, {0: 0}, 0, 5.0)
        flip = matcher._pair_confidence(
            cost, {(0, 0): 0.31, (0, 1): 0.30}, {0: 0}, 0, 5.0)
        self.assertGreater(clear, 0.5)
        self.assertLess(flip, 0.15)

    def test_big_duration_diff_capped(self):
        cost = np.array([[45.0, 90.0]])
        c = matcher._pair_confidence(cost, {}, {0: 0}, 0, 5.0)
        self.assertLess(c, 0.15)

    def test_in_unit_range(self):
        cost = np.array([[0.0]])
        c = matcher._pair_confidence(cost, {}, {0: 0}, 0, 5.0)
        self.assertTrue(0.0 <= c <= 1.0)


def _write_wav(path, x):
    pcm = (np.clip(x, -1, 1) * 32000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "ffmpeg not installed")
class IntegrationTests(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            vdir, adir = tmp / "video", tmp / "audio"
            vdir.mkdir()
            adir.mkdir()
            ev_a = [1.5, 3.0, 5.2, 7.0]
            ev_b = [1.0, 2.2, 4.4, 6.6]
            shift = 1.0
            dur_v = 9.0
            # video A scratch: events ev_a; matching WAV started `shift` earlier
            scratch = make_track(ev_a, dur_v, gain=1.0, noise=0.01, ambience=0.03, seed=1)
            _write_wav(tmp / "scratch.wav", scratch)
            good = make_track([t + shift for t in ev_a], dur_v + shift + 0.2,
                              gain=0.5, noise=0.02, ambience=0.03, seed=2)
            decoy = make_track(ev_b, dur_v + shift + 0.1, gain=0.5, noise=0.02,
                               ambience=0.03, seed=3)
            _write_wav(adir / "ZOOM0001.WAV", decoy)
            _write_wav(adir / "ZOOM0002.WAV", good)
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-f", "lavfi", "-i", f"color=c=black:s=64x36:d={dur_v}:r=24",
                 "-i", str(tmp / "scratch.wav"), "-shortest",
                 "-c:v", "libx264", "-c:a", "aac", str(vdir / "A001.MOV")],
                capture_output=True, text=True)
            if r.returncode != 0:
                self.skipTest(f"ffmpeg could not build test video: {r.stderr}")
            matches = matcher.match_files(str(vdir), str(adir))
            self.assertEqual(len(matches), 1)
            m = matches[0]
            self.assertEqual(Path(m.audio_path).name, "ZOOM0002.WAV")
            self.assertAlmostEqual(m.offset, shift, delta=0.03)
            self.assertGreater(m.offset, 0)
            self.assertTrue(0.0 <= m.confidence <= 1.0)


if __name__ == "__main__":
    unittest.main()
