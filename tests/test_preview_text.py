"""Check 4.15: the film preview with the cues and titles drawn into the picture (SPECS 5.5, 9.6): the text is
on the frames it is for, a title goes across the join of two scenes, the text of a letterboxed
scene stays inside its picture, and moving one cue renders again only the scene it is in. Sources
are synthetic; Pillow's built-in font draws the text."""

import copy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import render
import textlayout
import tools
from tests.fonts import builtin_font
from tests.test_preview import PreviewFixture, W, H

CUE1 = {"id": "c001", "anchor": "s001", "start_s": 0.5, "end_s": 1.5, "text": "First scene.", "origin": "manual"}
CUE2 = {"id": "c002", "anchor": "s002", "start_s": 0.5, "end_s": 1.5, "text": "Second scene with bars.", "origin": "manual"}
CUE3 = {"id": "c003", "anchor": "s003", "start_s": 0.5, "end_s": 1.5, "text": "Third scene.", "origin": "manual"}
TITLE = {"id": "t001", "text": "Across the join", "anchor": "film", "start_s": 1.5, "end_s": 3.0,
         "template": "center", "fade_s": 0.2}


def luma(path):
    r = subprocess.run([tools.ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-pix_fmt", "gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    assert r.returncode == 0
    a = np.frombuffer(r.stdout, np.uint8)
    return a.reshape(-1, H, W)


class PreviewTextTests(PreviewFixture):
    def setUp(self):
        self.real_get_font = builtin_font(self)
        self.render_dir = Path(tempfile.mkdtemp(dir=self.work))
        self.addCleanup(__import__("shutil").rmtree, self.render_dir, True)

    def tl_with(self, cues=(), titles=()):
        tl = self.make_tl()
        tl.data["subtitles"] = {"language": "en", "cues": [dict(c) for c in cues]}
        if titles:
            tl.data["titles"] = [dict(t) for t in titles]
        return tl

    def diff(self, tl):
        """Frames of the preview with the text minus the same preview without it."""
        with_text = luma(self.render(tl, name="with").path)
        without = luma(self.render(tl, name="without", burn_text=False).path)
        return np.abs(with_text.astype(int) - without.astype(int))

    def shown(self, d, frame, threshold=40, minimum=20):
        return int((d[frame] > threshold).sum()) >= minimum

    def test_a_cue_is_on_its_frames(self):
        d = self.diff(self.tl_with([CUE1, CUE3]))
        # s001 has 60 frames and starts at film frame 0; 0.5 s is frame 15 and 1.5 s is frame 45
        self.assertFalse(self.shown(d, 14))
        self.assertTrue(self.shown(d, 15))
        self.assertTrue(self.shown(d, 44))
        self.assertFalse(self.shown(d, 45))
        # s003 starts at film frame 120
        self.assertFalse(self.shown(d, 120 + 14))
        self.assertTrue(self.shown(d, 120 + 15))
        self.assertFalse(self.shown(d, 120 + 45))
        self.assertFalse(self.shown(d, 90))

    def test_a_title_goes_across_the_join(self):
        d = self.diff(self.tl_with(titles=[TITLE]))
        # 1.5 s is frame 45 and 3.0 s is frame 90; the join of s001 and s002 is at frame 60
        self.assertFalse(self.shown(d, 44, minimum=1))
        for n in (50, 59, 60, 61, 75):
            self.assertTrue(self.shown(d, n, minimum=10), n)
        amounts = [int(d[n].sum()) for n in range(45, 90)]
        self.assertLess(amounts[0], amounts[8])                 # it fades in
        self.assertLess(amounts[-1], amounts[-12])              # and out
        self.assertFalse(self.shown(d, 91, minimum=1))

    def test_text_on_a_letterboxed_scene_stays_inside_its_picture(self):
        # s002 is clip B: a 320 x 136 picture with bars of 22 rows above and below
        d = self.diff(self.tl_with([CUE2]))
        frame = 60 + 20
        rows = np.where((d[frame] > 40).sum(axis=1) > 0)[0]
        self.assertTrue(len(rows))
        self.assertGreaterEqual(rows.min(), 22)
        self.assertLess(rows.max(), H - 22)

    def test_only_the_scene_that_changed_is_rendered_again(self):
        tl = self.tl_with([CUE1, CUE2, CUE3])
        first = self.render(tl, name="cache")
        self.assertEqual(sorted(first.rendered), ["s001", "s002", "s003"])
        again = self.render(tl, name="cache")
        self.assertEqual(again.rendered, [])
        moved = copy.deepcopy(tl)
        moved.data["subtitles"]["cues"][2]["start_s"] = 0.8
        third = self.render(moved, name="cache")
        self.assertEqual(third.rendered, ["s003"])
        self.assertEqual(sorted(third.cached), ["s001", "s002"])

    def test_a_title_renders_again_only_the_scenes_it_touches(self):
        tl = self.tl_with([CUE1, CUE2, CUE3], [TITLE])
        self.render(tl, name="cache")
        longer = copy.deepcopy(tl)
        longer.data["titles"][0]["text"] = "Another title"
        again = self.render(longer, name="cache")
        self.assertEqual(sorted(again.rendered), ["s001", "s002"])

    def test_pending_work_sees_a_changed_cue(self):
        tl = self.tl_with([CUE1, CUE2, CUE3])
        self.render(tl, name="preview")
        self.assertEqual(render.pending_work(tl, str(self.render_dir), self.settings)["picture"], [])
        moved = copy.deepcopy(tl)
        moved.data["subtitles"]["cues"][0]["end_s"] = 1.2
        self.assertEqual(render.pending_work(moved, str(self.render_dir), self.settings)["picture"], ["s001"])

    def test_without_text_the_keys_are_those_of_a_film_without_text(self):
        plain = self.make_tl()
        with_empty = self.tl_with()
        plan_a, plan_b = render.make_plan(plain, W), render.make_plan(with_empty, W)
        self.assertEqual(render.segment_key(plain, plan_a, plan_a.segments[0], self.settings),
                         render.segment_key(with_empty, plan_b, plan_b.segments[0], self.settings))

    def test_a_missing_font_stops_the_preview_and_says_so(self):
        textlayout.get_font = self.real_get_font          # the real lookup, which needs VET_FONT_DIR
        old = os.environ.pop("VET_FONT_DIR", None)
        self.addCleanup(lambda: old and os.environ.__setitem__("VET_FONT_DIR", old))
        with self.assertRaises(render.RenderError) as ctx:
            self.render(self.tl_with([CUE1]), name="nofont")
        self.assertIn("font", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
