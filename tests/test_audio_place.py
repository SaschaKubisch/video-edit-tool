"""Check 2.8: the sound path (SPECS 5.4, 3.4). Where each segment's sound comes from
in the WAV, the crossfade at a join, the film fades. The mix is built in numpy, so the
tests hand it arrays instead of files. The cases of PlanTests in
tests/test_resolve_integration.py (back-to-back, trims, positive offset, negative
offset, clamped length) are restated for the audio span."""

import copy
import unittest

import numpy as np

import render
import timeline

RATE = 48000
FPS = 24            # 24/1: one frame is 2000 samples at 48 kHz


def noise(n, seed, std=0.1):
    return (np.random.default_rng(seed).standard_normal((n, 2)) * std).astype(np.float32)


def make_tl(segments, sources=("X",), offsets=None, join=0.5, curve="equal_power",
            fade_in=0.0, fade_out=0.0, frames=600):
    offsets = offsets or {}
    d = {
        "version": 1, "kind": "film",
        "film": {"fps": {"num": FPS, "den": 1}, "frame": {"width": 320, "height": 180},
                 "fit": "fit", "fade_in_s": fade_in, "fade_out_s": fade_out,
                 "audio_join": {"duration_s": join, "curve": curve}},
        "sources": {s: {"original_video": f"/o/{s}.braw", "preview_copy": f"/p/{s}.mp4",
                        "audio": f"/a/{s}.wav", "audio_offset_s": offsets.get(s, 0.0),
                        "frames": frames, "fps": {"num": FPS, "den": 1},
                        "size": {"width": 640, "height": 360}, "copy_lut_sha256": "0" * 64}
                    for s in sources},
        "segments": [{"id": f"s{i + 1:03d}", "source": src, "in": a, "out": b}
                     for i, (src, a, b) in enumerate(segments)],
        "look": {"base": {"mode": "none"}},
        "audio": {"sample_rate": RATE, "loudness": {"mode": "off"}},
    }
    return timeline.Timeline(d)


def mix(tl, wavs):
    calls = []

    def loader(path):
        calls.append(path)
        return wavs[path.split("/")[-1].split(".")[0]]

    plan = render.make_plan(tl)
    result = render.build_mix(tl, plan, loader)
    result.calls = calls
    return result


class PlacementOfSoundTests(unittest.TestCase):
    def test_back_to_back_segments_take_their_sound_from_the_start_of_each_clip(self):
        x, y = noise(20 * RATE, 1), noise(20 * RATE, 2)
        tl = make_tl([("X", 0, 48), ("Y", 0, 24)], sources=("X", "Y"), join=0)
        m = mix(tl, {"X": x, "Y": y})
        self.assertEqual(m.samples.shape, (72 * 2000, 2))
        np.testing.assert_array_equal(m.samples[:96000], x[:96000])
        np.testing.assert_array_equal(m.samples[96000:], y[:48000])
        self.assertEqual(m.warnings, [])

    def test_the_in_point_moves_the_start_of_the_sound(self):
        x = noise(20 * RATE, 3)
        tl = make_tl([("X", 24, 72)], join=0)             # starts 1 s into the clip
        m = mix(tl, {"X": x})
        np.testing.assert_array_equal(m.samples, x[48000:48000 + 96000])

    def test_a_positive_offset_advances_the_sound(self):
        x = noise(20 * RATE, 4)
        tl = make_tl([("X", 0, 48)], offsets={"X": 1.0}, join=0)
        m = mix(tl, {"X": x})
        np.testing.assert_array_equal(m.samples, x[48000:48000 + 96000])

    def test_a_negative_offset_delays_the_sound_and_says_so(self):
        x = noise(20 * RATE, 5)
        tl = make_tl([("X", 0, 48)], offsets={"X": -0.5}, join=0)
        m = mix(tl, {"X": x})
        self.assertTrue(np.all(m.samples[:24000] == 0))
        np.testing.assert_array_equal(m.samples[24000:], x[:96000 - 24000])
        self.assertTrue(any("0.500" in w and "silence" in w for w in m.warnings), m.warnings)

    def test_a_clip_whose_sound_is_shorter_than_the_picture_ends_in_silence_and_says_so(self):
        x = noise(8 * RATE, 6)
        tl = make_tl([("X", 0, 240)], join=0)             # 10 s of picture, 8 s of sound
        m = mix(tl, {"X": x})
        np.testing.assert_array_equal(m.samples[:8 * RATE], x)
        self.assertTrue(np.all(m.samples[8 * RATE:] == 0))
        self.assertTrue(any("silence" in w for w in m.warnings), m.warnings)

    def test_each_wav_is_loaded_once(self):
        x = noise(20 * RATE, 7)
        tl = make_tl([("X", 0, 48), ("X", 100, 148), ("X", 200, 248)], join=0)
        m = mix(tl, {"X": x})
        self.assertEqual(m.calls, ["/a/X.wav"])

    def test_gain_adjust_is_applied(self):
        x = noise(20 * RATE, 8)
        tl = make_tl([("X", 0, 48)], join=0)
        tl.data["segments"][0]["gain_db_adjust"] = -6.0206
        m = mix(tl, {"X": x})
        np.testing.assert_allclose(m.samples, x[:96000] * 0.5, atol=1e-5)

    def test_drift_moves_the_start_by_in_over_fps_times_ppm(self):
        x = noise(60 * RATE, 9)
        tl = make_tl([("X", 480, 528)], join=0)          # in = 20 s
        tl.data["sources"]["X"]["audio_drift_ppm"] = 50
        m = mix(tl, {"X": x})
        start = round((20 + 20 * 50e-6) * RATE)
        np.testing.assert_array_equal(m.samples, x[start:start + 96000])


def power_db(a):
    return 10 * np.log10(float(np.mean(a.astype(np.float64) ** 2)) + 1e-20)


class CrossfadeTests(unittest.TestCase):
    JOIN = 0.5

    def join_window(self, f_frame):
        centre = f_frame * 2000
        half = int(self.JOIN * RATE / 2)
        return centre - half, centre + half

    def test_two_uncorrelated_sounds_keep_their_power_through_an_equal_power_join(self):
        x, y = noise(20 * RATE, 11), noise(20 * RATE, 12)
        tl = make_tl([("X", 100, 196), ("Y", 100, 196)], sources=("X", "Y"),
                     join=self.JOIN, curve="equal_power")
        m = mix(tl, {"X": x, "Y": y})
        lo, hi = self.join_window(96)
        reference = power_db(m.samples[lo - 60000:lo - 12000])
        for a in range(lo, hi, 2400):          # 50 ms windows across the join
            self.assertAlmostEqual(power_db(m.samples[a:a + 2400]), reference, delta=1.0)

    def test_two_parts_of_the_same_sound_keep_their_level_through_a_linear_join(self):
        x = noise(40 * RATE, 13)
        # the second part starts where the first ends, in the same clip
        tl = make_tl([("X", 100, 196), ("X", 196, 292)], join=self.JOIN, curve="linear")
        m = mix(tl, {"X": x})
        lo, hi = self.join_window(96)
        reference = power_db(m.samples[lo - 60000:lo - 12000])
        for a in range(lo, hi, 2400):
            self.assertAlmostEqual(power_db(m.samples[a:a + 2400]), reference, delta=1.0)
        # and the sound is the clip's own sound: the crossfade changes nothing
        start = 100 * 2000
        np.testing.assert_allclose(m.samples, x[start:start + 192 * 2000], atol=1e-5)

    def test_the_gains_of_the_two_sides_follow_the_curve(self):
        ones = np.ones((20 * RATE, 2), np.float32)
        tl = make_tl([("X", 100, 196), ("Y", 100, 196)], sources=("X", "Y"),
                     join=self.JOIN, curve="equal_power")
        m = mix(tl, {"X": ones, "Y": ones})
        lo, hi = self.join_window(96)
        mid = m.samples[(lo + hi) // 2, 0]
        # at the middle of the join both gains are cos/sin(pi/4): their sum is sqrt(2)
        self.assertAlmostEqual(float(mid), 2 ** 0.5, delta=0.01)
        self.assertAlmostEqual(float(m.samples[lo - 1, 0]), 1.0, delta=1e-6)
        self.assertAlmostEqual(float(m.samples[hi + 1, 0]), 1.0, delta=0.01)

    def test_the_picture_does_not_get_shorter_at_a_join(self):
        x, y = noise(20 * RATE, 14), noise(20 * RATE, 15)
        tl = make_tl([("X", 100, 196), ("Y", 100, 196)], sources=("X", "Y"), join=self.JOIN)
        plan = render.make_plan(tl)
        self.assertEqual(plan.film_samples, 192 * 2000)
        self.assertEqual(mix(tl, {"X": x, "Y": y}).samples.shape[0], 192 * 2000)

    def test_a_handle_that_the_clip_cannot_give_shortens_the_crossfade_and_says_so(self):
        x, y = noise(20 * RATE, 16), noise(20 * RATE, 17)
        # the second segment starts 3 frames (6000 samples) into its clip: only 6000
        # samples of handle exist before it, the join wants 12000 on each side
        tl = make_tl([("X", 100, 196), ("Y", 3, 99)], sources=("X", "Y"), join=self.JOIN)
        m = mix(tl, {"X": x, "Y": y})
        self.assertTrue(any("shortened" in w and "s002" in w for w in m.warnings), m.warnings)
        self.assertEqual(m.joins[0]["wanted_s"], 0.5)
        self.assertAlmostEqual(m.joins[0]["used_s"], 0.25, places=3)
        f = 96 * 2000
        end_x = 100 * 2000 + 96 * 2000
        # before the shortened crossfade the first sound plays alone ...
        np.testing.assert_allclose(m.samples[f - 12000:f - 6000], x[end_x - 12000:end_x - 6000],
                                   atol=1e-6)
        # ... and after it the second sound plays alone
        np.testing.assert_allclose(m.samples[f + 6000:f + 8000], y[12000:14000], atol=1e-6)

    def test_a_clip_that_starts_at_its_first_sample_gets_a_hard_cut(self):
        x, y = noise(20 * RATE, 18), noise(20 * RATE, 19)
        tl = make_tl([("X", 100, 196), ("Y", 0, 96)], sources=("X", "Y"), join=self.JOIN)
        m = mix(tl, {"X": x, "Y": y})
        f = 96 * 2000
        end_x = 100 * 2000 + 96 * 2000
        np.testing.assert_allclose(m.samples[f - 2000:f], x[end_x - 2000:end_x], atol=1e-6)
        np.testing.assert_allclose(m.samples[f:f + 2000], y[:2000], atol=1e-6)
        self.assertEqual(m.joins[0]["used_s"], 0.0)

    def test_a_join_of_zero_length_is_a_hard_cut(self):
        x, y = noise(20 * RATE, 20), noise(20 * RATE, 21)
        tl = make_tl([("X", 100, 196), ("Y", 100, 196)], sources=("X", "Y"), join=0)
        m = mix(tl, {"X": x, "Y": y})
        np.testing.assert_array_equal(m.samples[:192000], x[200000:392000])
        np.testing.assert_array_equal(m.samples[192000:], y[200000:392000])


class FilmFadeTests(unittest.TestCase):
    def test_the_first_and_last_10_ms_of_the_film_are_silent_when_the_fades_are_on(self):
        ones = np.ones((60 * RATE, 2), np.float32)
        tl = make_tl([("X", 0, 96), ("X", 200, 296)], join=0.5, fade_in=1.0, fade_out=2.0)
        m = mix(tl, {"X": ones})
        self.assertEqual(float(m.samples[0, 0]), 0.0)
        self.assertEqual(float(m.samples[-1, 0]), 0.0)
        self.assertLess(float(np.abs(m.samples[:480]).max()), 0.02)
        self.assertLess(float(np.abs(m.samples[-480:]).max()), 0.02)

    def test_the_fades_have_the_set_lengths(self):
        ones = np.ones((60 * RATE, 2), np.float32)
        tl = make_tl([("X", 0, 96), ("X", 200, 296)], join=0.5, fade_in=1.0, fade_out=2.0)
        m = mix(tl, {"X": ones})
        self.assertAlmostEqual(float(m.samples[RATE, 0]), 1.0, delta=1e-4)         # fade-in done at 1 s
        self.assertLess(float(m.samples[RATE // 2, 0]), 0.75)
        n = m.samples.shape[0]
        self.assertAlmostEqual(float(m.samples[n - 2 * RATE - 1, 0]), 1.0, delta=1e-3)
        self.assertLess(float(m.samples[n - RATE, 0]), 0.75)

    def test_without_fades_the_film_starts_and_ends_at_full_level(self):
        ones = np.ones((60 * RATE, 2), np.float32)
        tl = make_tl([("X", 0, 96)], join=0.5, fade_in=0.0, fade_out=0.0)
        m = mix(tl, {"X": ones})
        self.assertEqual(float(m.samples[0, 0]), 1.0)
        self.assertEqual(float(m.samples[-1, 0]), 1.0)


if __name__ == "__main__":
    unittest.main()
