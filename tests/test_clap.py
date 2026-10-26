"""Check 2.4: finding the sync clap (SPECS 7.2, decision D6 "only at the start").

Tracks are made with `make_track` of tests/test_matcher.py: claps at known
times with a noise floor, with and without loud ambience, and with a wind-like
low-frequency step."""

import math
import unittest

import numpy as np
from scipy import signal

import clap
import config
from tests.test_matcher import make_track

SR = config.MATCH_SAMPLE_RATE      # 16000, as the matcher extracts audio
TOL = 0.005                        # the clap time is found within 5 ms


def rumble(total_sec, start_s, level, seed=7):
    """Wind-like low-frequency noise that steps up to `level` at start_s and stays."""
    rng = np.random.default_rng(seed)
    n = int(total_sec * SR)
    sos = signal.butter(2, 150, btype="lowpass", fs=SR, output="sos")
    x = signal.sosfilt(sos, rng.standard_normal(n))
    x = x / np.abs(x).max()
    env = np.where(np.arange(n) >= int(start_s * SR), level, 0.0)
    return (x * env).astype(np.float32)


def broadband_step(total_sec, start_s, level, seed=8):
    """White noise that becomes louder at start_s and stays loud: a step, not a clap."""
    rng = np.random.default_rng(seed)
    n = int(total_sec * SR)
    env = np.where(np.arange(n) >= int(start_s * SR), level, 0.0)
    return (rng.standard_normal(n) * env).astype(np.float32)


class FindClapTests(unittest.TestCase):
    def test_a_clap_at_a_known_time_is_found_within_5_ms(self):
        for t in (0.5, 2.0, 7.3):
            x = make_track([t], 20.0, noise=0.01, seed=3)
            r = clap.find_clap(x, SR, 20.0)
            with self.subTest(t=t):
                self.assertIsNotNone(r)
                self.assertAlmostEqual(r.time_s, t, delta=TOL)

    def test_a_clap_very_near_the_start_is_found(self):
        r = clap.find_clap(make_track([0.1], 10.0, seed=4), SR, 10.0)
        self.assertAlmostEqual(r.time_s, 0.1, delta=TOL)

    def test_found_in_loud_ambience(self):
        x = make_track([3.0], 20.0, noise=0.01, ambience=0.08, seed=5)
        r = clap.find_clap(x, SR, 20.0)
        self.assertAlmostEqual(r.time_s, 3.0, delta=TOL)

    def test_the_time_is_precise_over_many_noise_seeds_and_ambience_levels(self):
        worst = 0.0
        for ambience in (0.0, 0.08, 0.2):
            for seed in range(30, 50):
                x = make_track([3.5], 12.0, noise=0.01, ambience=ambience, seed=seed)
                r = clap.find_clap(x, SR, 12.0)
                with self.subTest(ambience=ambience, seed=seed):
                    self.assertIsNotNone(r)
                    self.assertAlmostEqual(r.time_s, 3.5, delta=TOL)
                    worst = max(worst, abs(r.time_s - 3.5))
        self.assertLess(worst, TOL)

    def test_found_when_the_clap_is_quiet_against_the_noise(self):
        x = make_track([4.0], 20.0, gain=0.3, noise=0.04, seed=6)
        r = clap.find_clap(x, SR, 20.0)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r.time_s, 4.0, delta=TOL)

    def test_gain_does_not_matter(self):
        x = make_track([2.0], 10.0, seed=9)
        a = clap.find_clap(x, SR, 10.0)
        b = clap.find_clap(x * 0.001, SR, 10.0)
        self.assertAlmostEqual(a.time_s, b.time_s, delta=1 / SR)

    def test_a_wind_like_low_frequency_step_is_not_a_clap(self):
        x = make_track([6.0], 20.0, noise=0.01, seed=10) + rumble(20.0, 3.0, 0.6)
        r = clap.find_clap(x, SR, 20.0)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r.time_s, 6.0, delta=TOL)

    def test_a_loud_broadband_step_is_not_a_clap(self):
        x = make_track([6.0], 20.0, noise=0.01, seed=11) + broadband_step(20.0, 3.0, 0.2)
        r = clap.find_clap(x, SR, 20.0)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r.time_s, 6.0, delta=TOL)

    def test_a_step_alone_gives_no_clap(self):
        x = make_track([], 20.0, noise=0.01, seed=12) + broadband_step(20.0, 3.0, 0.2)
        self.assertIsNone(clap.find_clap(x, SR, 20.0))

    def test_noise_alone_gives_no_clap(self):
        for amb in (0.0, 0.08):
            x = make_track([], 20.0, noise=0.01, ambience=amb, seed=13)
            with self.subTest(ambience=amb):
                self.assertIsNone(clap.find_clap(x, SR, 20.0))

    def test_silence_and_empty_input_give_no_clap(self):
        self.assertIsNone(clap.find_clap(np.zeros(SR * 5, dtype=np.float32), SR, 5.0))
        self.assertIsNone(clap.find_clap(np.zeros(0, dtype=np.float32), SR, 5.0))
        self.assertIsNone(clap.find_clap(np.zeros(10, dtype=np.float32), SR, 5.0))

    def test_the_first_clap_wins_over_later_louder_sounds(self):
        x = make_track([2.0], 20.0, gain=1.0, noise=0.01, seed=14) \
            + make_track([7.0, 12.0], 20.0, gain=2.5, noise=0.0, seed=15, clap_seed=77)
        r = clap.find_clap(x, SR, 20.0)
        self.assertAlmostEqual(r.time_s, 2.0, delta=TOL)
        # The report also lists the strongest candidate: one of the later, louder sounds.
        self.assertIsNotNone(r.strongest)
        self.assertGreater(r.strongest.time_s, 6.0)
        self.assertGreaterEqual(len(r.candidates), 3)

    def test_later_sounds_alone_are_taken_as_the_clap_but_flagged_by_position(self):
        # A clip with no clap at the start but a door slam at 9 s: the method cannot
        # know; it returns the slam. The result carries the time, so the report shows it.
        x = make_track([9.0], 20.0, noise=0.01, seed=16)
        r = clap.find_clap(x, SR, 20.0)
        self.assertAlmostEqual(r.time_s, 9.0, delta=TOL)

    def test_only_the_search_window_is_searched(self):
        x = make_track([25.0], 40.0, noise=0.01, seed=17)
        self.assertIsNone(clap.find_clap(x, SR, 20.0))
        self.assertAlmostEqual(clap.find_clap(x, SR, 30.0).time_s, 25.0, delta=TOL)

    def test_strength_values(self):
        r = clap.find_clap(make_track([2.0], 10.0, noise=0.01, seed=18), SR, 10.0)
        self.assertGreaterEqual(r.onset, config.CLAP_MIN_ONSET)
        self.assertGreater(r.snr_db, 10.0)
        self.assertLess(r.decay_s, config.CLAP_DECAY_SEC)
        self.assertGreater(r.confidence, 0.0)
        self.assertLessEqual(r.confidence, 1.0)

    def test_a_stronger_clap_has_a_higher_confidence(self):
        weak = clap.find_clap(make_track([2.0], 10.0, gain=0.12, noise=0.02, seed=19), SR, 10.0)
        strong = clap.find_clap(make_track([2.0], 10.0, gain=1.0, noise=0.005, seed=19), SR, 10.0)
        self.assertLess(weak.confidence, strong.confidence)

    def test_works_at_other_sample_rates(self):
        rate = 48000
        rng = np.random.default_rng(20)
        x = rng.standard_normal(rate * 10) * 0.01
        n = int(0.04 * rate)
        x[3 * rate:3 * rate + n] += rng.standard_normal(n) * np.exp(-np.arange(n) / (0.008 * rate))
        r = clap.find_clap(x.astype(np.float32), rate, 10.0)
        self.assertAlmostEqual(r.time_s, 3.0, delta=TOL)


class FirstFrameTests(unittest.TestCase):
    def test_first_frame_after_the_clap_and_the_guard(self):
        # (2.0 + 0.5) s at 30000/1001 fps = 74.925 frames: the first whole frame is 75
        self.assertEqual(clap.first_frame_after(2.0, 30000, 1001, 0.5), 75)

    def test_a_clap_at_the_start_with_no_guard(self):
        self.assertEqual(clap.first_frame_after(0.0, 30000, 1001, 0.0), 0)

    def test_an_exact_frame_boundary_is_not_pushed_to_the_next_frame(self):
        # 1 s at 25 fps is frame 25 exactly
        self.assertEqual(clap.first_frame_after(0.5, 25, 1, 0.5), 25)

    def test_default_guard_comes_from_the_config(self):
        self.assertEqual(clap.first_frame_after(2.0, 30000, 1001),
                         math.ceil((2.0 + config.CLAP_GUARD_SEC) * 30000 / 1001))


class CombineTests(unittest.TestCase):
    """The camera's scratch audio and the recorder's WAV both hold the clap. The
    WAV time minus the camera time is the sync offset (offset = t_audio - t_video)."""

    OFFSET = 0.276

    def tracks(self, cam_clap=2.0, wav_clap=None, offset=None):
        offset = self.OFFSET if offset is None else offset
        cam = make_track([cam_clap] if cam_clap is not None else [], 20.0, gain=1.0,
                         noise=0.01, seed=1)
        t_wav = wav_clap if wav_clap is not None else (cam_clap + offset if cam_clap is not None else None)
        wav = make_track([t_wav] if t_wav is not None else [], 20.0, gain=0.4,
                         noise=0.02, seed=2)
        return cam, wav

    def test_both_found_and_consistent_is_agree(self):
        cam, wav = self.tracks()
        r = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET)
        self.assertEqual(r.result, "agree")
        self.assertAlmostEqual(r.video_time_s, 2.0, delta=TOL)
        self.assertFalse(r.review)
        self.assertLessEqual(abs(r.delta_s), config.CLAP_AGREE_SEC)

    def test_agree_works_for_a_negative_offset(self):
        cam, wav = self.tracks(cam_clap=7.0, offset=-5.03)
        r = clap.analyse_clip(cam, wav, SR, offset_s=-5.03)
        self.assertEqual(r.result, "agree")
        self.assertAlmostEqual(r.video_time_s, 7.0, delta=TOL)

    def test_only_the_camera_found_is_single_and_marked_for_review(self):
        cam, wav = self.tracks()
        wav = make_track([], 20.0, noise=0.02, seed=2)       # no clap in the WAV
        r = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET)
        self.assertEqual(r.result, "single")
        self.assertAlmostEqual(r.video_time_s, 2.0, delta=TOL)
        self.assertTrue(r.review)

    def test_only_the_wav_found_gives_its_time_minus_the_offset(self):
        cam, wav = self.tracks()
        cam = make_track([], 20.0, noise=0.01, seed=1)       # no clap in the camera audio
        r = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET)
        self.assertEqual(r.result, "single")
        self.assertAlmostEqual(r.video_time_s, 2.0, delta=TOL)
        self.assertTrue(r.review)

    def test_disagreeing_times_are_single_with_the_camera_time_and_the_delta(self):
        cam, wav = self.tracks()
        r = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET + 0.5)   # stored offset is wrong
        self.assertEqual(r.result, "single")
        self.assertAlmostEqual(r.video_time_s, 2.0, delta=TOL)
        self.assertTrue(r.review)
        self.assertAlmostEqual(r.delta_s, -0.5, delta=TOL)

    def test_no_clap_anywhere_is_none_and_marked_for_review(self):
        cam = make_track([], 20.0, noise=0.01, seed=1)
        wav = make_track([], 20.0, noise=0.02, seed=2)
        r = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET)
        self.assertEqual(r.result, "none")
        self.assertIsNone(r.video_time_s)
        self.assertTrue(r.review)

    def test_an_offset_outside_the_usual_range_is_marked_for_review(self):
        cam, wav = self.tracks(offset=1.28)
        r = clap.analyse_clip(cam, wav, SR, offset_s=1.28)
        self.assertEqual(r.result, "agree")
        self.assertTrue(r.review)
        self.assertTrue(any("usual" in reason for reason in r.reasons), r.reasons)

    def test_an_offset_inside_the_usual_range_is_not(self):
        cam, wav = self.tracks()
        r = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET)
        self.assertEqual(r.reasons, [])

    # ── A clap is an event that both microphones hear at the stored offset ──

    def test_an_early_blip_in_one_channel_does_not_hide_the_real_clap(self):
        # The WAV has a click at 0.05 s that the camera does not hear (found on real clips:
        # the first candidate of each channel then disagree). The shared event is the clap.
        cam, wav = self.tracks(cam_clap=2.0)
        wav = wav + make_track([0.05], 20.0, gain=0.6, noise=0.0, seed=3, clap_seed=99)
        first = clap.find_clap(wav, SR, 20.0)
        self.assertAlmostEqual(first.time_s, 0.05, delta=TOL)          # the plain first candidate
        r = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET)
        self.assertEqual(r.result, "agree")
        self.assertAlmostEqual(r.video_time_s, 2.0, delta=TOL)
        self.assertFalse(r.review)

    def test_an_early_blip_in_the_camera_audio_does_not_hide_the_real_clap(self):
        cam, wav = self.tracks(cam_clap=2.0)
        cam = cam + make_track([0.4], 20.0, gain=0.6, noise=0.0, seed=3, clap_seed=98)
        r = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET)
        self.assertEqual(r.result, "agree")
        self.assertAlmostEqual(r.video_time_s, 2.0, delta=TOL)

    def test_the_first_shared_event_within_the_loud_band_wins(self):
        # A shared event at 1.0 s that is 6 dB quieter than a shared one at 5.0 s: the
        # first is within the band (10 dB) and wins.
        cam = make_track([1.0], 20.0, gain=0.5, noise=0.01, seed=1) \
            + make_track([5.0], 20.0, gain=1.0, noise=0.0, seed=1, clap_seed=77)
        wav = make_track([1.0 + self.OFFSET], 20.0, gain=0.2, noise=0.02, seed=2) \
            + make_track([5.0 + self.OFFSET], 20.0, gain=0.4, noise=0.0, seed=2, clap_seed=77)
        r = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET)
        self.assertAlmostEqual(r.video_time_s, 1.0, delta=TOL)

    def test_a_much_quieter_early_event_is_passed_over(self):
        # A shared event at 1.0 s that is 20 dB below a shared one at 5.0 s is not the clap.
        cam = make_track([1.0], 20.0, gain=0.1, noise=0.003, seed=1) \
            + make_track([5.0], 20.0, gain=1.0, noise=0.0, seed=1, clap_seed=77)
        wav = make_track([1.0 + self.OFFSET], 20.0, gain=0.04, noise=0.006, seed=2) \
            + make_track([5.0 + self.OFFSET], 20.0, gain=0.4, noise=0.0, seed=2, clap_seed=77)
        r = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET)
        self.assertAlmostEqual(r.video_time_s, 5.0, delta=TOL)

    def test_the_chosen_events_are_reported_with_their_strength(self):
        cam, wav = self.tracks()
        d = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET).as_dict()
        for side in ("camera", "wav"):
            for key in ("time_s", "onset", "peak_db", "decay_s", "confidence"):
                self.assertIn(key, d[side])
        self.assertEqual(d["pairs"], 1)
        self.assertGreater(d["confidence"], 0.5)

    def test_a_late_clap_is_marked_for_review(self):
        cam, wav = self.tracks(cam_clap=12.0)
        r = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET)
        self.assertEqual(r.result, "agree")
        self.assertTrue(r.review)
        self.assertTrue(any("expected" in reason for reason in r.reasons), r.reasons)

    def test_a_weak_clap_is_marked_for_review(self):
        cam = make_track([2.0], 20.0, gain=0.08, noise=0.02, seed=1)
        wav = make_track([2.0 + self.OFFSET], 20.0, gain=0.08, noise=0.02, seed=2)
        r = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET)
        self.assertEqual(r.result, "agree")
        self.assertTrue(r.review)
        self.assertTrue(any("weak" in reason for reason in r.reasons), r.reasons)

    def test_the_usual_offset_range_has_exactly_the_nine_offsets_of_the_first_shoot_outside(self):
        nine = [-5.027, -1.697, 0.632, 0.641, 0.657, 0.833, 0.894, 1.282, 1.94]
        lo, hi = config.USUAL_OFFSET_RANGE_SEC
        for off in nine:
            self.assertFalse(lo <= off <= hi, off)
        # the lowest and the highest of the other 96 offsets of the first shoot
        self.assertTrue(lo <= 0.0850680 <= hi)
        self.assertTrue(lo <= 0.3678369 <= hi)

    def test_the_result_serialises_for_clap_json(self):
        cam, wav = self.tracks()
        d = clap.analyse_clip(cam, wav, SR, offset_s=self.OFFSET).as_dict()
        for key in ("result", "video_time_s", "camera", "wav", "delta_s", "offset_s",
                    "review", "reasons", "settings"):
            self.assertIn(key, d)
        self.assertIn("time_s", d["camera"])
        self.assertIn("confidence", d["camera"])


if __name__ == "__main__":
    unittest.main()
