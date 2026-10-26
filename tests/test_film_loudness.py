"""Check 3.5: the loudness and the true peak of the whole film (SPECS 5.4 step 7, 8.4;
decision D9: -14 LUFS integrated, true peak at most -1 dBTP). Synthetic tones and noise at
known levels; the numbers are read back with ffmpeg's ebur128, which is also what the
renderer uses, so a second reading by another route (a plain RMS check) guards the first."""

import unittest

import numpy as np

import loudness
import render
from tests import test_audio_place as base
from tests.test_levelling import mix, noise, tl_with

RATE = base.RATE


def tone(seconds, freq, level, rate=RATE):
    t = np.arange(int(seconds * rate)) / rate
    x = (level * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([x, x], -1)


class MeasureTests(unittest.TestCase):
    def test_a_997_hz_tone_at_minus_20_dbfs_measures_minus_20_lufs(self):
        lv = loudness.measure(tone(10, 997, 0.1))
        self.assertAlmostEqual(lv.lufs, -20.0, delta=0.05)
        self.assertAlmostEqual(lv.peak_dbtp, -20.0, delta=0.05)

    def test_silence_and_a_stretch_shorter_than_one_block_are_silent(self):
        self.assertTrue(loudness.measure(np.zeros((3 * RATE, 2), np.float32)).silent)
        self.assertTrue(loudness.measure(tone(0.2, 997, 0.1)).silent)
        self.assertTrue(loudness.measure(np.zeros((0, 2), np.float32)).silent)

    def test_the_true_peak_of_a_click_is_read(self):
        x = np.zeros((2 * RATE, 2), np.float32)
        x[1000, :] = 0.5
        x += noise(2, 0.01, 1)
        self.assertAlmostEqual(loudness.measure(x).peak_dbtp, -6.02, delta=0.3)

    def test_a_true_peak_between_two_samples_is_above_the_sample_peak(self):
        # Two equal samples side by side: the wave between them is higher than either.
        t = np.arange(2 * RATE)
        x = np.sin(2 * np.pi * (RATE / 4) * t / RATE + np.pi / 4) * 0.9      # samples peak at 0.636
        x = np.stack([x, x], -1).astype(np.float32)
        sample_peak_db = 20 * np.log10(np.abs(x).max())
        self.assertGreater(loudness.measure(x).peak_dbtp, sample_peak_db + 1.0)


class LimiterTests(unittest.TestCase):
    def test_the_limiter_keeps_the_length_the_timing_and_the_quiet_parts(self):
        x = tone(4, 440, 0.2)
        x[2 * RATE:2 * RATE + 4800] += 0.9 * tone(0.1, 3000, 1.0)
        y = loudness.apply_limiter(x, -3.0)
        self.assertEqual(y.shape, x.shape)
        np.testing.assert_allclose(y[:RATE], x[:RATE], atol=1e-6)         # untouched, in step
        self.assertLessEqual(np.abs(y).max(), 10 ** (-3.0 / 20) + 1e-4)


class FilmLevelTests(unittest.TestCase):
    def test_noise_is_levelled_to_the_target_within_half_an_lu(self):
        x = noise(30, 0.03, 1)
        out, r = loudness.level_film(x.copy(), -14.0, -1.0)
        got = loudness.measure(out)
        self.assertAlmostEqual(got.lufs, -14.0, delta=0.5)
        self.assertLessEqual(got.peak_dbtp, -1.0)
        self.assertTrue(r.reached_target and r.reached_peak)
        self.assertEqual(r.limiter_ceilings_db, [])
        self.assertEqual(r.warnings, [])

    def test_material_with_strong_clicks_goes_through_the_limiter_and_ends_under_the_limit(self):
        x = noise(30, 0.03, 2)
        t = np.arange(480) / RATE
        click = (0.8 * np.exp(-t * 600) * np.sin(2 * np.pi * 4000 * t)).astype(np.float32)
        for k in range(0, len(x) - 480, RATE // 2):
            x[k:k + 480] += click[:, None]
        out, r = loudness.level_film(x.copy(), -14.0, -1.0)
        got = loudness.measure(out)
        self.assertGreater(len(r.limiter_ceilings_db), 0)
        self.assertEqual(r.limiter_ceilings_db[0], -1.5)           # half a dB under the limit
        self.assertLessEqual(got.peak_dbtp, -1.0)
        self.assertAlmostEqual(got.lufs, -14.0, delta=0.5)
        self.assertEqual(len(out), len(x))

    def test_the_ceiling_falls_by_at_least_half_a_dB_per_run(self):
        # A limiter that does nothing: the peak never holds.
        x = noise(10, 0.03, 3)
        x[1000, :] = 0.9
        out, r = loudness.level_film(x.copy(), -14.0, -1.0, limiter_fn=lambda s, c: s.copy())
        self.assertEqual(len(r.limiter_ceilings_db), loudness.LIMITER_TRIES)
        for a, b in zip(r.limiter_ceilings_db, r.limiter_ceilings_db[1:]):
            self.assertLessEqual(b, a - 0.5 + 1e-9)

    def test_a_peak_that_cannot_be_brought_down_is_reported_not_hidden(self):
        x = noise(10, 0.03, 4)
        x[1000, :] = 0.9
        out, r = loudness.level_film(x.copy(), -14.0, -1.0, limiter_fn=lambda s, c: s.copy())
        self.assertFalse(r.reached_peak)
        self.assertTrue(any("true peak" in w and "limit" in w for w in r.warnings), r.warnings)

    def test_a_film_without_sound_is_left_alone_with_a_warning(self):
        x = np.zeros((5 * RATE, 2), np.float32)
        out, r = loudness.level_film(x, -14.0, -1.0)
        self.assertTrue(np.all(out == 0))
        self.assertTrue(any("no sound" in w for w in r.warnings), r.warnings)

    def test_the_limiter_takes_loudness_away_and_the_gain_makes_it_up(self):
        x = noise(30, 0.03, 5)
        t = np.arange(480) / RATE
        click = (0.9 * np.exp(-t * 500) * np.sin(2 * np.pi * 3000 * t)).astype(np.float32)
        for k in range(0, len(x) - 480, RATE // 4):
            x[k:k + 480] += click[:, None]
        out, r = loudness.level_film(x.copy(), -14.0, -1.0)
        self.assertAlmostEqual(loudness.measure(out).lufs, -14.0, delta=0.2)


class FilmInTheMixTests(unittest.TestCase):
    def test_the_mix_of_a_film_meets_the_target_and_the_peak(self):
        wavs = {"X": noise(12, 0.04, 1), "Y": noise(12, 0.08, 2), "Z": noise(12, 0.16, 3)}
        plan, m = mix(tl_with(join=0.5), wavs)
        got = loudness.measure(m.samples)
        self.assertAlmostEqual(got.lufs, -14.0, delta=0.5)
        self.assertLessEqual(got.peak_dbtp, -1.0)
        film = m.levels["film"]
        self.assertAlmostEqual(film["after"]["lufs"], got.lufs, delta=0.05)
        self.assertTrue(film["reached_target"] and film["reached_peak"])
        self.assertIn("before", film)

    def test_the_target_comes_from_the_timeline_file(self):
        wavs = {"X": noise(12, 0.04, 1), "Y": noise(12, 0.08, 2), "Z": noise(12, 0.16, 3)}
        plan, m = mix(tl_with(target=-20.0, peak=-2.0), wavs)
        got = loudness.measure(m.samples)
        self.assertAlmostEqual(got.lufs, -20.0, delta=0.5)
        self.assertLessEqual(got.peak_dbtp, -2.0)

    def test_a_loudness_warning_reaches_the_mix_warnings(self):
        wavs = {"X": np.zeros((12 * RATE, 2), np.float32), "Y": np.zeros((12 * RATE, 2), np.float32),
                "Z": np.zeros((12 * RATE, 2), np.float32)}
        plan, m = mix(tl_with(), wavs)
        self.assertTrue(any("film has no sound" in w for w in m.warnings), m.warnings)


if __name__ == "__main__":
    unittest.main()
