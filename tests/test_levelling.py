"""Check 3.4: the levelling of every segment to the same loudness (SPECS 5.4 step 5, 8.4;
decision D9, "Each scene, then film"). Segments are noise at known levels. The mix is built
from arrays, so no media is needed; loudness is measured with ffmpeg's ebur128."""

import unittest

import numpy as np

import loudness
import render
import timeline
from tests import test_audio_place as base

RATE = base.RATE
FPS = base.FPS                  # 24 frames per second: one frame is 2000 samples


def noise(seconds, std, seed):
    return (np.random.default_rng(seed).standard_normal((int(seconds * RATE), 2)) * std
            ).astype(np.float32)


def tl_with(mode="segment_then_film", segments=None, sources=("X", "Y", "Z"), target=-14.0,
            peak=-1.0, max_gain=20.0, join=0, **kw):
    segments = segments or [("X", 0, 240), ("Y", 0, 240), ("Z", 0, 240)]      # 10 s each
    tl = base.make_tl(segments, sources=sources, join=join, **kw)
    tl.data["audio"]["loudness"] = {"mode": mode, "target_lufs": target,
                                    "true_peak_dbtp": peak, "max_gain_db": max_gain}
    return tl


def mix(tl, wavs, **kw):
    plan = render.make_plan(tl)
    loader = lambda path: wavs[path.split("/")[-1].split(".")[0]]
    return plan, render.build_mix(tl, plan, loader, **kw)


def span(plan, result, index):
    s = plan.segments[index]
    return result.samples[s.film_start_sample:s.film_start_sample + s.sample_length]


class LevellingTests(unittest.TestCase):
    wavs = None

    @classmethod
    def setUpClass(cls):
        # Three segments 6 dB apart: about -27, -21 and -15 LUFS before levelling.
        cls.wavs = {"X": noise(12, 0.04, 1), "Y": noise(12, 0.08, 2), "Z": noise(12, 0.16, 3)}
        cls.raw = [loudness.measure(w[:10 * RATE]).lufs for w in cls.wavs.values()]

    def test_the_test_material_differs_by_about_six_lu(self):
        self.assertAlmostEqual(self.raw[1] - self.raw[0], 6.0, delta=0.5)
        self.assertAlmostEqual(self.raw[2] - self.raw[1], 6.0, delta=0.5)

    def test_every_segment_ends_within_half_an_lu_of_the_target(self):
        plan, m = mix(tl_with(), self.wavs)
        for i in range(3):
            got = loudness.measure(span(plan, m, i)).lufs
            self.assertAlmostEqual(got, -14.0, delta=0.5, msg=f"segment {i}: {got}")

    def test_the_report_lists_the_measured_loudness_and_the_gain_of_each_segment(self):
        plan, m = mix(tl_with(), self.wavs)
        rows = m.levels["segments"]
        self.assertEqual(list(rows), ["s001", "s002", "s003"])
        for row, raw in zip(rows.values(), self.raw):
            self.assertAlmostEqual(row["lufs"], raw, delta=0.1)
            self.assertAlmostEqual(row["gain_db"], -14.0 - raw, delta=0.1)
            self.assertFalse(row["capped"])
            self.assertFalse(row["silent"])
        self.assertEqual(m.levels["mode"], "segment_then_film")
        self.assertEqual(m.levels["target_lufs"], -14.0)

    def test_a_segment_too_quiet_for_max_gain_is_capped_and_reported(self):
        wavs = dict(self.wavs, X=noise(12, 0.002, 4))           # about -55 LUFS
        plan, m = mix(tl_with(max_gain=12.0), wavs)
        row = m.levels["segments"]["s001"]
        self.assertTrue(row["capped"])
        self.assertAlmostEqual(row["gain_db"], 12.0, places=3)
        self.assertGreater(row["wanted_db"], 12.0)
        self.assertTrue(any("s001" in w and "12" in w and "cap" in w for w in m.warnings), m.warnings)

    def test_a_silent_segment_gets_gain_zero_and_a_warning(self):
        wavs = dict(self.wavs, Y=np.zeros((12 * RATE, 2), np.float32))
        plan, m = mix(tl_with(), wavs)
        row = m.levels["segments"]["s002"]
        self.assertTrue(row["silent"])
        self.assertEqual(row["gain_db"], 0.0)
        self.assertTrue(any("s002" in w and "no sound" in w for w in m.warnings), m.warnings)
        self.assertTrue(np.all(span(plan, m, 1) == 0))

    def test_film_only_keeps_the_differences_between_segments(self):
        plan, m = mix(tl_with(mode="film_only"), self.wavs)
        got = [loudness.measure(span(plan, m, i)).lufs for i in range(3)]
        self.assertAlmostEqual(got[1] - got[0], 6.0, delta=0.5)
        self.assertAlmostEqual(got[2] - got[1], 6.0, delta=0.5)
        self.assertEqual(m.levels["segments"], {})              # no segment was measured

    def test_gain_db_adjust_comes_on_top_of_the_levelling(self):
        tl = tl_with()
        tl.data["segments"][1]["gain_db_adjust"] = -6.0
        plan, m = mix(tl, self.wavs)
        got = [loudness.measure(span(plan, m, i)).lufs for i in range(3)]
        # The film gain moves all three together; the middle one stays 6 LU below the others.
        self.assertAlmostEqual(got[0] - got[1], 6.0, delta=0.5)
        self.assertAlmostEqual(got[2] - got[1], 6.0, delta=0.5)
        self.assertAlmostEqual(m.levels["segments"]["s002"]["adjust_db"], -6.0, places=3)

    def test_mode_off_changes_nothing(self):
        tl = tl_with(mode="off")
        del tl.data["audio"]["loudness"]["target_lufs"]
        plan, m = mix(tl, self.wavs)
        self.assertIsNone(m.levels)
        np.testing.assert_array_equal(span(plan, m, 0), self.wavs["X"][:10 * RATE])

    def test_a_cache_spares_the_measurement_of_a_segment_that_did_not_change(self):
        calls = []

        def counting(samples, rate):
            calls.append(len(samples))
            return loudness.measure(samples, rate)

        cache = {}
        tl = tl_with()
        mix(tl, self.wavs, measure=counting, cache=cache)
        per_segment = [c for c in calls if c == 10 * RATE]
        self.assertEqual(len(per_segment), 3)
        calls.clear()
        mix(tl, self.wavs, measure=counting, cache=cache)
        self.assertNotIn(10 * RATE, calls)             # only the film itself is measured again

    def test_a_short_segment_below_the_gate_block_is_left_alone_with_a_warning(self):
        tl = tl_with(segments=[("X", 0, 240), ("Y", 0, 4)])      # 4 frames = 0.17 s
        plan, m = mix(tl, self.wavs)
        row = m.levels["segments"]["s002"]
        self.assertTrue(row["silent"])
        self.assertTrue(any("s002" in w for w in m.warnings), m.warnings)


if __name__ == "__main__":
    unittest.main()
