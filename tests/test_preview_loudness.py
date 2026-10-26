"""The film preview has the final loudness (SPECS 5.5, decision D9): the mix of a film whose
loudness mode is on is levelled, the preview file's sound measures near the target, and the
record next to it (previews/<name>.json) carries the loudness report for the page."""

import json
import unittest
from pathlib import Path

import loudness
from tests.test_preview import PreviewFixture


class PreviewLoudnessTests(PreviewFixture):
    def levelled(self):
        tl = self.make_tl()
        tl.data["audio"]["loudness"] = {"mode": "segment_then_film", "target_lufs": -14.0,
                                        "true_peak_dbtp": -1.0, "max_gain_db": 20.0}
        return tl

    def test_the_sound_of_the_preview_is_at_the_target(self):
        result = self.render(self.levelled(), name="levelled")
        got = loudness.measure_file(result.path)
        # The preview's sound passes through AAC at 160 kbit/s after the levelling.
        self.assertAlmostEqual(got.lufs, -14.0, delta=0.7)
        self.assertLessEqual(got.peak_dbtp, -0.3)

    def test_the_record_carries_the_loudness_report(self):
        result = self.render(self.levelled(), name="levelled-record")
        record = json.loads((Path(result.path).with_suffix(".json")).read_text())
        report = record["loudness"]
        self.assertEqual(report["mode"], "segment_then_film")
        self.assertEqual(sorted(report["segments"]), ["s001", "s002", "s003"])
        self.assertAlmostEqual(report["film"]["after"]["lufs"], -14.0, delta=0.5)
        self.assertLessEqual(report["film"]["after"]["true_peak_dbtp"], -1.0)

    def test_the_loudness_of_a_segment_is_kept_between_renders(self):
        self.render(self.levelled(), name="levelled-cache")
        cache = json.loads((self.work / "previews" / "loudness-cache.json").read_text())
        self.assertEqual(len(cache), 3)

    def test_a_film_with_the_mode_off_has_no_report(self):
        result = self.render(self.make_tl(), name="plain")
        record = json.loads((Path(result.path).with_suffix(".json")).read_text())
        self.assertIsNone(record["loudness"])


if __name__ == "__main__":
    unittest.main()
