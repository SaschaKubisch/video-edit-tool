"""Check 1.3: the space estimate (C6, SPECS 6.7). Pure functions; the refusal
by the converter itself is tested in tests/test_convert_flow.py."""

import os
import unittest

import config
import convert

MB = 1_000_000


class EstimateTests(unittest.TestCase):
    def test_unmeasured_kind_uses_the_prior_of_step_one(self):
        items = [convert.EstimateItem(kind="4096x2160", width=4096, height=2160,
                                      minutes=10.0)]
        got = convert.estimate_bytes(items, measured={}, margin=1.0)
        self.assertAlmostEqual(got, 10 * config.PREVIEW_PRIOR_MB_PER_MIN_DCI4K * MB,
                               delta=1)

    def test_prior_scales_with_the_picture_size(self):
        full = convert.prior_bytes_per_minute(4096, 2160)
        wide = convert.prior_bytes_per_minute(4096, 1720)
        self.assertAlmostEqual(wide / full, 1720 / 2160, places=6)
        # The prior is not below what step 1 measured for the wide kind (36.7 MB/min).
        self.assertGreater(wide, 36.7 * MB)

    def test_measured_size_per_minute_replaces_the_prior(self):
        items = [convert.EstimateItem("4096x1720", 4096, 1720, 2.0)]
        got = convert.estimate_bytes(items, measured={"4096x1720": 37 * MB}, margin=1.0)
        self.assertAlmostEqual(got, 74 * MB, delta=1)

    def test_kinds_are_summed_separately(self):
        items = [convert.EstimateItem("4096x2160", 4096, 2160, 1.0),
                 convert.EstimateItem("4096x1720", 4096, 1720, 3.0),
                 convert.EstimateItem("4096x1720", 4096, 1720, 1.0)]
        measured = {"4096x2160": 70 * MB, "4096x1720": 40 * MB}
        got = convert.estimate_bytes(items, measured, margin=1.0)
        self.assertAlmostEqual(got, (70 + 160) * MB, delta=1)

    def test_margin_is_applied(self):
        items = [convert.EstimateItem("4096x2160", 4096, 2160, 1.0)]
        a = convert.estimate_bytes(items, {"4096x2160": 100 * MB}, margin=1.0)
        b = convert.estimate_bytes(items, {"4096x2160": 100 * MB}, margin=1.25)
        self.assertAlmostEqual(b / a, 1.25, places=6)

    def test_default_margin_comes_from_config(self):
        items = [convert.EstimateItem("4096x2160", 4096, 2160, 1.0)]
        self.assertAlmostEqual(
            convert.estimate_bytes(items, {"4096x2160": 100 * MB}),
            100 * MB * config.PREVIEW_SPACE_MARGIN, delta=1)

    def test_first_shoot_estimate_is_about_what_the_plan_says(self):
        # SPECS 6.7: 23.75 min of 4096x2160 and 52.8 min of 4096x1720 make about 3.6 GB
        # at the measured sizes (70.7 and 36.7 MB per minute), before the margin.
        items = [convert.EstimateItem("4096x2160", 4096, 2160, 23.75),
                 convert.EstimateItem("4096x1720", 4096, 1720, 52.8)]
        got = convert.estimate_bytes(items, {"4096x2160": 70.7 * MB,
                                             "4096x1720": 36.7 * MB}, margin=1.0)
        self.assertAlmostEqual(got / 1e9, 3.6, delta=0.1)


class CheckSpaceTests(unittest.TestCase):
    def test_enough_space_passes(self):
        convert.check_space(required=5 * MB, free=6 * MB)

    def test_exactly_enough_passes(self):
        convert.check_space(required=5 * MB, free=5 * MB)

    def test_too_little_space_raises_with_both_numbers(self):
        with self.assertRaises(convert.NotEnoughSpace) as ctx:
            convert.check_space(required=5_000 * MB, free=4_000 * MB)
        msg = str(ctx.exception)
        self.assertIn("5.0 GB", msg)
        self.assertIn("4.0 GB", msg)


if __name__ == "__main__":
    unittest.main()


class ConverterSpaceTests(unittest.TestCase):
    """The converter refuses to start without space, and stops when it runs out."""

    def setUp(self):
        from tests.fakes import Workspace
        self.ws = Workspace(self)

    def converter(self, free):
        return convert.Converter(
            work_dir=str(self.ws.work), decoder=self.ws.decoder,
            settings=convert.PreviewSettings(encoder="libx264"),
            lut_path=str(self.ws.lut), originals_folders=[str(self.ws.src)],
            log=lambda line: None, free_bytes=free)

    def refs(self):
        return [convert.ClipRef(stem=n, path=p) for n, p in self.ws.refs]

    def test_refuses_before_decoding_anything(self):
        with self.assertRaises(convert.NotEnoughSpace):
            self.converter(lambda path: 1000).run(self.refs())
        kinds = [c[0] for c in self.ws.decoder.calls]
        self.assertNotIn("frames", kinds)
        self.assertNotIn("audio", kinds)
        self.assertEqual(list((self.ws.work / "copies").glob("*.mp4")), [])

    def test_starts_when_the_space_is_there(self):
        summary = self.converter(lambda path: 10 ** 12).run(self.refs())
        self.assertEqual(summary.converted, 2)

    def test_stops_when_the_space_runs_out_during_the_run(self):
        answers = iter([10 ** 12, 1000])        # enough at the start, nothing after clip 1
        last = [10 ** 12]

        def free(path):
            last[0] = next(answers, 1000)
            return last[0]

        with self.assertRaises(convert.NotEnoughSpace):
            self.converter(free).run(self.refs())
        done = [p.name for p in (self.ws.work / "copies").glob("*.mp4")]
        self.assertEqual(done, [self.ws.refs[0][0] + ".mp4"])

    def test_missing_ssd_is_refused_and_nothing_is_created(self):
        c = convert.Converter(
            work_dir="/Volumes/__no_such_ssd__/work", decoder=self.ws.decoder,
            settings=convert.PreviewSettings(encoder="libx264"),
            lut_path=str(self.ws.lut), originals_folders=[str(self.ws.src)],
            log=lambda line: None, free_bytes=lambda p: 10 ** 12)
        with self.assertRaises(convert.WorkDirUnavailable):
            c.run(self.refs())
        self.assertFalse(os.path.exists("/Volumes/__no_such_ssd__"))
