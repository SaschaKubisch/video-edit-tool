"""Check 4.16: the final film with subtitles and titles (SPECS 5.6, 9.5, 9.6; decision D13). The form of the
subtitles is a setting of each export: `srt` writes the file next to the film, `burned` draws the
cues into the picture, `both` does both, `none` neither; titles are drawn whatever the form. The
film is the synthetic one of test_export; Pillow's built-in font draws the text."""

import json
import os
import unittest
from pathlib import Path

import numpy as np

import export
import textlayout
import words
from tests import media
from tests.fonts import builtin_font
from tests.test_export import ExportFixture, W, H

CUES = [
    {"id": "c001", "anchor": "s001", "start_s": 0.5, "end_s": 1.5, "text": "First scene.", "origin": "manual"},
    {"id": "c002", "anchor": "s002", "start_s": 0.5, "end_s": 1.5, "text": "Second scene with bars.", "origin": "manual"},
    {"id": "c003", "anchor": "s003", "start_s": 0.5, "end_s": 1.5, "text": "Third scene.", "origin": "manual"},
]
TITLE = {"id": "t001", "text": "Sunday kitchen", "anchor": "film", "start_s": 2.5, "end_s": 3.5,
         "template": "center", "fade_s": 0.2}


class ExportTextFixture(ExportFixture):
    def setUp(self):
        self.real_get_font = builtin_font(self)

    def tl(self, form, cues=CUES, titles=(), name=None):
        name = name or f"film-{form or 'default'}"
        entry = self.entry(name=name)
        if form:
            entry["subtitles"] = form
        tl = self.make_tl(exports=[entry], loud=False)
        tl.data["subtitles"] = {"language": "en", "cues": [dict(c) for c in cues]}
        if titles:
            tl.data["titles"] = [dict(t) for t in titles]
        return tl, entry["name"]

    def luma(self, result):
        return np.array([f[0] for f in self.frames_of(result.path)]).astype(int)

    def run_form(self, form, **kw):
        tl, name = self.tl(form, **kw)
        return self.run_export(tl, name)

    def diff(self, result, base):
        a, b = self.luma(result), self.luma(base)
        return np.abs(a - b)

    def shown(self, d, frame, threshold=40, minimum=20):
        return int((d[frame] > threshold).sum()) >= minimum


class SubtitleFormTests(ExportTextFixture):
    def test_burned_draws_the_cues_on_their_frames_and_writes_no_srt(self):
        base = self.run_form("none")
        burned = self.run_form("burned")
        self.assertTrue(burned.ok, burned.problems)
        d = self.diff(burned, base)
        self.assertFalse(self.shown(d, 14))
        self.assertTrue(self.shown(d, 15))
        self.assertTrue(self.shown(d, 44))
        self.assertFalse(self.shown(d, 45))
        self.assertTrue(self.shown(d, 60 + 20))
        self.assertTrue(self.shown(d, 120 + 20))
        self.assertIsNone(burned.srt_file)
        self.assertFalse(os.path.exists(export.srt_path(burned.path)))

    def test_srt_writes_the_file_and_draws_nothing(self):
        base = self.run_form("none")
        srt = self.run_form("srt")
        self.assertTrue(srt.ok)
        d = self.diff(srt, base)
        self.assertFalse(any(self.shown(d, n, minimum=1) for n in range(len(d))))
        self.assertEqual(srt.srt_file, export.srt_path(srt.path))
        self.assertTrue(srt.srt_file.endswith(".en.srt"))
        parsed = words.parse_srt(Path(srt.srt_file).read_text())
        self.assertEqual([c["text"] for c in parsed], ["First scene.", "Second scene with bars.", "Third scene."])
        # the SRT times are film times: s002 starts at frame 60, s003 at frame 120
        self.assertAlmostEqual(parsed[0]["start_s"], 0.5, delta=0.04)
        self.assertAlmostEqual(parsed[1]["start_s"], 60 * 1001 / 30000 + 0.5, delta=0.04)
        self.assertAlmostEqual(parsed[2]["end_s"], 120 * 1001 / 30000 + 1.5, delta=0.04)

    def test_both_draws_and_writes(self):
        base = self.run_form("none")
        both = self.run_form("both")
        self.assertTrue(self.shown(self.diff(both, base), 20))
        self.assertTrue(os.path.isfile(both.srt_file))

    def test_none_does_neither(self):
        base = self.run_form("none")
        self.assertIsNone(base.srt_file)
        self.assertFalse(os.path.exists(export.srt_path(base.path)))
        self.assertEqual(base.subtitles, "none")

    def test_an_export_without_the_setting_gets_the_starting_value_of_its_kind(self):
        result = self.run_form(None)
        self.assertEqual(result.subtitles, "srt")
        self.assertTrue(os.path.isfile(result.srt_file))
        self.assertEqual(export.subtitle_form({}, "film"), "srt")
        self.assertEqual(export.subtitle_form({}, "short"), "burned")
        self.assertEqual(export.subtitle_form({"subtitles": "both"}, "short"), "both")

    def test_the_form_can_be_replaced_for_one_render(self):
        base = self.run_form("none")
        tl, name = self.tl("srt", name="film-override")
        result = self.run_export(tl, name, subtitles="burned")
        self.assertEqual(result.subtitles, "burned")
        self.assertTrue(self.shown(self.diff(result, base), 20))
        self.assertIsNone(result.srt_file)
        self.assertEqual(tl.data["exports"][0]["subtitles"], "srt")           # the file is not changed
        with self.assertRaises(export.ExportError):
            self.run_export(tl, name, subtitles="hard")

    def test_the_report_names_the_form_and_the_file(self):
        result = self.run_form("both")
        row = json.loads((self.work / "reports" / "render-report.json").read_text())["renders"][-1]
        self.assertEqual((row["subtitles"], row["srt_file"]), ("both", result.srt_file))

    def test_titles_are_drawn_whatever_the_form(self):
        base = self.run_form("none", cues=[])
        tl, name = self.tl("srt", cues=[], titles=[TITLE], name="film-title-srt")
        titled = self.run_export(tl, name)
        d = self.diff(titled, base)
        self.assertFalse(self.shown(d, 70, minimum=1))
        self.assertTrue(self.shown(d, 90))                       # 2.5 s is frame 75, 3.5 s is frame 105
        self.assertFalse(self.shown(d, 110, minimum=1))


class PlacementTests(ExportTextFixture):
    def test_text_on_a_letterboxed_scene_stays_inside_the_picture(self):
        base = self.run_form("none")
        burned = self.run_form("burned")
        d = self.diff(burned, base)
        rows = np.where((d[60 + 20] > 40).sum(axis=1) > 0)[0]
        self.assertTrue(len(rows))
        self.assertGreaterEqual(rows.min(), 22)
        self.assertLess(rows.max(), H - 22)

    def test_text_is_inside_the_safe_area_on_a_full_scene(self):
        base = self.run_form("none")
        burned = self.run_form("burned")
        d = self.diff(burned, base)
        g = textlayout.geometry((W, H))
        cols = np.where((d[20] > 40).sum(axis=0) > 0)[0]
        rows = np.where((d[20] > 40).sum(axis=1) > 0)[0]
        self.assertGreaterEqual(cols.min(), g.safe[0])
        self.assertLessEqual(cols.max(), g.safe[2])
        self.assertLessEqual(rows.max(), g.bottom + g.pad)


class RefusalTests(ExportTextFixture):
    def test_a_missing_font_stops_the_export_before_anything_is_written(self):
        textlayout.get_font = self.real_get_font
        old = os.environ.pop("VET_FONT_DIR", None)
        self.addCleanup(lambda: old and os.environ.__setitem__("VET_FONT_DIR", old))
        tl, name = self.tl("burned", name="film-nofont")
        with self.assertRaises(export.ExportError) as ctx:
            self.run_export(tl, name)
        self.assertIn("font", str(ctx.exception))
        self.assertFalse(os.path.exists(tl.data["exports"][0]["output"]))
        self.assertFalse(os.path.exists(tl.data["exports"][0]["output"] + ".partial"))

    def test_the_srt_form_needs_no_font(self):
        textlayout.get_font = self.real_get_font
        old = os.environ.pop("VET_FONT_DIR", None)
        self.addCleanup(lambda: old and os.environ.__setitem__("VET_FONT_DIR", old))
        tl, name = self.tl("srt", name="film-srt-nofont")
        result = self.run_export(tl, name)
        self.assertTrue(result.ok)
        self.assertTrue(os.path.isfile(result.srt_file))

    def test_a_cue_that_does_not_fit_is_reported_in_the_warnings(self):
        long = dict(CUES[0], text="This cue is far too long to read: " + "word " * 40)
        tl, name = self.tl("burned", cues=[long], name="film-long")
        result = self.run_export(tl, name)
        self.assertTrue(result.ok)
        self.assertTrue(any("does not fit" in w for w in result.warnings), result.warnings)


if __name__ == "__main__":
    unittest.main()
