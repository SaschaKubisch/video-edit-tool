"""Check 4.14: cues and titles laid over a picture by ffmpeg's overlay filter (SPECS 9.6, 9.7): a cue is
on the frames it is for and on no other, a title fades, the text stays in its safe area on a
letterboxed scene, the lanes are few, and the plan's signature moves with the text. Pillow's built-in
font draws the text, so no font file is needed."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import config
import textlayer
import textlayout
import tools
from tests.fonts import builtin_font
from tests.timeline_fixture import example

W, H = 960, 540
FRAME = (W, H)
FPS = 30000 / 1001


def run_overlay(plan, folder, frames=210, pix_fmt="yuv420p"):
    """The luma of every frame of a grey picture with the plan's text over it."""
    plan.write(folder)
    graph, label = plan.graph("0:v", 1, pix_fmt)
    cmd = [tools.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-f", "lavfi", "-i", f"color=c=0x808080:s={W}x{H}:r=30000/1001,format={pix_fmt}",
           *plan.inputs(), "-filter_complex", graph, "-map", f"[{label}]", "-frames:v", str(frames),
           "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-800:]
    return np.frombuffer(r.stdout, np.uint8).reshape(frames, H, W)


def ink(frame_luma, background=128, delta=40):
    return np.argwhere(np.abs(frame_luma.astype(int) - background) > delta)


def data_with(cues=(), titles=()):
    d = example()
    d["subtitles"]["cues"] = [dict(c) for c in cues]
    d["titles"] = [dict(t) for t in titles]
    return d


CUE = {"id": "c001", "anchor": "s001", "start_s": 3.2, "end_s": 6.0, "text": "Now the pan is hot enough.",
       "origin": "auto"}
TITLE = {"id": "t001", "text": "Sunday kitchen", "anchor": "film", "start_s": 1.0, "end_s": 5.0,
         "template": "center", "fade_s": 0.5}
FULL = {"s001": (0, 0, W, H), "s002": (0, 0, W, H), "s003": (0, 0, W, H)}


class PlanTests(unittest.TestCase):
    def setUp(self):
        builtin_font(self)

    def test_a_cue_has_the_frames_it_is_for(self):
        plan = textlayer.build(data_with([CUE]), FRAME, 0, 400, FULL)
        self.assertEqual(plan.cue_count, 1)
        item = plan.lanes[0].items[0]
        self.assertEqual((item.first, item.end), (96, 180))            # 3.2 s and 6.0 s at 29.97 fps
        self.assertEqual(plan.lanes[0].width, W)

    def test_frames_count_from_the_first_frame_of_the_range(self):
        plan = textlayer.build(data_with([CUE]), FRAME, 90, 400, FULL)
        self.assertEqual((plan.lanes[0].items[0].first, plan.lanes[0].items[0].end), (6, 90))

    def test_a_range_cuts_a_cue(self):
        plan = textlayer.build(data_with([CUE]), FRAME, 0, 120, FULL)
        self.assertEqual((plan.lanes[0].items[0].first, plan.lanes[0].items[0].end), (96, 120))
        self.assertTrue(textlayer.build(data_with([CUE]), FRAME, 200, 300, FULL).empty)

    def test_cues_on_scenes_of_different_pictures_get_lanes_of_their_own(self):
        d = data_with([CUE, {"id": "c002", "anchor": "s002", "start_s": 1.0, "end_s": 3.0, "text": "Wide.",
                             "origin": "manual"}])
        rects = dict(FULL, s002=(0, 55, W, 485))
        plan = textlayer.build(d, FRAME, 0, 2000, rects)
        self.assertEqual(len(plan.lanes), 2)
        bottoms = sorted(l.y + l.height for l in plan.lanes)
        self.assertLess(bottoms[0], bottoms[1])

    def test_forms_without_cues_draw_titles_only(self):
        d = data_with([CUE], [TITLE])
        plan = textlayer.build(d, FRAME, 0, 400, FULL, burn_cues=False)
        self.assertEqual((plan.cue_count, plan.title_count), (0, 1))
        none = textlayer.build(d, FRAME, 0, 400, FULL, burn_cues=False, burn_titles=False)
        self.assertTrue(none.empty)

    def test_the_signature_moves_with_the_text_and_only_then(self):
        a = textlayer.build(data_with([CUE]), FRAME, 0, 400, FULL).signature
        same = textlayer.build(data_with([CUE]), FRAME, 0, 400, FULL).signature
        text = textlayer.build(data_with([dict(CUE, text="Another text.")]), FRAME, 0, 400, FULL).signature
        time = textlayer.build(data_with([dict(CUE, start_s=3.3)]), FRAME, 0, 400, FULL).signature
        size = textlayer.build(data_with([CUE]), (1280, 720), 0, 400, {k: (0, 0, 1280, 720) for k in FULL}).signature
        self.assertEqual(a, same)
        self.assertEqual(len({a, text, time, size}), 4)

    def test_a_cue_that_needs_three_lines_is_drawn_and_reported(self):
        long = "This cue is far too long to read: " + "word " * 40
        plan = textlayer.build(data_with([dict(CUE, text=long)]), FRAME, 0, 400, FULL)
        self.assertTrue(any("does not fit" in w for w in plan.warnings), plan.warnings)
        self.assertGreater(plan.lanes[0].height, 2 * textlayout.geometry(FRAME).line_height)

    def test_has_text(self):
        self.assertFalse(textlayer.has_text(data_with()))
        self.assertTrue(textlayer.has_text(data_with([CUE])))
        self.assertTrue(textlayer.has_text(data_with(titles=[TITLE])))
        self.assertFalse(textlayer.has_text(data_with([CUE]), burn_cues=False))


class OverlayTests(unittest.TestCase):
    def setUp(self):
        builtin_font(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_cue_is_on_its_frames_and_on_no_other(self):
        plan = textlayer.build(data_with([CUE]), FRAME, 0, 400, FULL)
        video = run_overlay(plan, self.tmp.name)
        shown = [n for n in range(len(video)) if len(ink(video[n]))]
        self.assertEqual((shown[0], shown[-1], len(shown)), (96, 179, 84))

    def test_the_cue_is_in_the_safe_area_at_the_bottom(self):
        plan = textlayer.build(data_with([CUE]), FRAME, 0, 400, FULL)
        video = run_overlay(plan, self.tmp.name)
        pts = ink(video[120])
        g = textlayout.geometry(FRAME)
        self.assertGreaterEqual(pts[:, 1].min(), g.safe[0])
        self.assertLessEqual(pts[:, 1].max(), g.safe[2])
        self.assertLessEqual(pts[:, 0].max(), g.bottom + g.pad)
        self.assertGreater(pts[:, 0].min(), H * 0.7)
        self.assertAlmostEqual((pts[:, 1].min() + pts[:, 1].max()) / 2, W / 2, delta=3)

    def test_a_scene_with_bars_puts_the_text_above_the_bar(self):
        d = data_with([CUE])
        rects = dict(FULL, s001=(0, 55, W, 485))                       # 960 x 430 picture in 960 x 540
        plan = textlayer.build(d, FRAME, 0, 400, rects)
        video = run_overlay(plan, self.tmp.name)
        pts = ink(video[120])
        self.assertLess(pts[:, 0].max(), 485)
        self.assertGreater(pts[:, 0].min(), 55)

    def test_a_title_fades_in_holds_and_fades_out(self):
        plan = textlayer.build(data_with(titles=[TITLE]), FRAME, 0, 400, FULL)
        video = run_overlay(plan, self.tmp.name)
        amount = [int(np.abs(video[n].astype(int) - 128).sum()) for n in range(len(video))]
        self.assertEqual(amount[29], 0)                               # 1.0 s is frame 30
        self.assertGreater(amount[30], 0)
        self.assertLess(amount[30], amount[40])
        self.assertLess(amount[40], amount[60])
        self.assertEqual(amount[60], amount[100])                     # held
        self.assertGreater(amount[149], 0)
        self.assertLess(amount[149], amount[120])
        self.assertEqual(amount[150], 0)                              # 5.0 s is frame 150

    def test_a_title_is_in_the_middle_for_the_center_template(self):
        plan = textlayer.build(data_with(titles=[TITLE]), FRAME, 0, 400, FULL)
        video = run_overlay(plan, self.tmp.name)
        pts = ink(video[80])
        self.assertAlmostEqual((pts[:, 1].min() + pts[:, 1].max()) / 2, W / 2, delta=4)
        self.assertAlmostEqual((pts[:, 0].min() + pts[:, 0].max()) / 2, H / 2, delta=8)

    def test_the_lower_third_is_left_and_above_the_subtitles(self):
        t = dict(TITLE, template="lower_third")
        plan = textlayer.build(data_with([CUE], [t]), FRAME, 0, 400, FULL)
        video = run_overlay(plan, self.tmp.name)
        both = ink(video[120])
        title_only = ink(video[60])
        g = textlayout.geometry(FRAME)
        self.assertLess(title_only[:, 1].max(), W / 2)
        self.assertGreaterEqual(title_only[:, 1].min(), g.safe[0] - 2)
        self.assertLess(title_only[:, 0].max(), both[:, 0].max())

    def test_two_titles_at_once_and_a_cue(self):
        t2 = dict(TITLE, id="t002", text="Second", template="lower_third", start_s=2.0, end_s=4.0)
        plan = textlayer.build(data_with([CUE], [TITLE, t2]), FRAME, 0, 400, FULL)
        self.assertEqual(len(plan.lanes), 3)
        video = run_overlay(plan, self.tmp.name)
        self.assertTrue(len(ink(video[100])))

    def test_prores_format_overlay(self):
        plan = textlayer.build(data_with([CUE]), FRAME, 0, 400, FULL)
        video = run_overlay(plan, self.tmp.name, pix_fmt="yuv422p10le")
        shown = [n for n in range(len(video)) if len(ink(video[n]))]
        self.assertEqual((shown[0], shown[-1]), (96, 179))

    def test_nothing_changes_on_a_frame_without_text(self):
        plan = textlayer.build(data_with([CUE]), FRAME, 0, 400, FULL)
        video = run_overlay(plan, self.tmp.name)
        self.assertEqual(int(video[10].min()), 128)
        self.assertEqual(int(video[10].max()), 128)

    def test_the_graph_names_the_frames(self):
        plan = textlayer.build(data_with([CUE]), FRAME, 0, 400, FULL)
        plan.write(self.tmp.name)
        graph, label = plan.graph("0:v", 1, "yuv420p")
        self.assertIn("between(n,96,179)", graph)
        self.assertIn("[1:v]", graph)
        self.assertEqual(label, "txt0")
        self.assertEqual(plan.inputs()[:4], ["-f", "concat", "-safe", "0"])

    def test_the_list_changes_a_quarter_of_a_frame_early(self):
        plan = textlayer.build(data_with([CUE]), FRAME, 0, 400, FULL)
        plan.write(self.tmp.name)
        lines = Path(plan.lanes[0].list_file).read_text().splitlines()
        durations = [float(l.split()[1]) for l in lines if l.startswith("duration")]
        self.assertAlmostEqual(durations[0], (96 - 0.25) * 1001 / 30000, places=5)
        self.assertAlmostEqual(durations[1], 84 * 1001 / 30000, places=5)


if __name__ == "__main__":
    unittest.main()
