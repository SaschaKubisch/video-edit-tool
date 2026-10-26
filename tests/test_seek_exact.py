"""Check 2.11: the film preview reads exactly the frames `in` to `out - 1` of a
preview copy (SPECS 5.5, "frame accuracy"). The source is a synthetic H.264 file made
like a preview copy (a key frame every 30 frames, B-frames on or off) in which every
frame has its own grey value, so a cut that is one frame off shows."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

import render
import timeline
from tests import media

W, H = 320, 180
FRAMES = 100
CUTS = [(0, 30), (1, 31), (29, 59), (30, 60), (31, 61), (47, 77), (70, 100), (58, 62)]


def tl_for(copy, a, b):
    return timeline.Timeline({
        "version": 1, "kind": "film",
        "film": {"fps": {"num": 30000, "den": 1001}, "frame": {"width": W, "height": H},
                 "fit": "fit", "fade_in_s": 0, "fade_out_s": 0,
                 "audio_join": {"duration_s": 0, "curve": "equal_power"}},
        "sources": {"S": {"original_video": "/o/S.braw", "preview_copy": str(copy),
                          "audio": "/a/S.wav", "audio_offset_s": 0.0, "frames": FRAMES,
                          "fps": {"num": 30000, "den": 1001},
                          "size": {"width": W * 2, "height": H * 2}, "copy_lut_sha256": "0" * 64}},
        "segments": [{"id": "s001", "source": "S", "in": a, "out": b}],
        "look": {"base": {"mode": "none"}},
        "audio": {"sample_rate": 48000, "loudness": {"mode": "off"}}})


class SeekExactTests(unittest.TestCase):
    def check(self, bf):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "copy.mp4"
            media.write_ramp_mp4(copy, FRAMES, W, H, bf=bf)
            settings = render.PreviewSettings(width=W, encoder="libx264")
            for a, b in CUTS:
                with self.subTest(bf=bf, cut=(a, b)):
                    tl = tl_for(copy, a, b)
                    plan = render.make_plan(tl, preview_width=W)
                    out = Path(tmp) / f"seg_{a}_{b}.mp4"
                    render.render_segment(tl, plan, plan.segments[0], str(out), settings)
                    self.assertEqual(media.video_frames(out), b - a)
                    luma = media.read_luma(out, W, H)
                    self.assertEqual(len(luma), b - a)
                    centre = luma[:, H // 2, W // 2].astype(int)
                    want = np.array([media.frame_value(a + j) for j in range(b - a)])
                    np.testing.assert_allclose(centre, want, atol=3)

    def test_with_b_frames(self):
        self.check(bf=True)

    def test_without_b_frames(self):
        self.check(bf=False)

    def test_the_segment_file_has_the_film_frame_rate_and_starts_at_time_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "copy.mp4"
            media.write_ramp_mp4(copy, FRAMES, W, H)
            tl = tl_for(copy, 47, 77)
            plan = render.make_plan(tl, preview_width=W)
            out = Path(tmp) / "seg.mp4"
            render.render_segment(tl, plan, plan.segments[0], str(out),
                                  render.PreviewSettings(width=W, encoder="libx264"))
            v = [s for s in media.probe(out)["streams"] if s["codec_type"] == "video"][0]
            self.assertEqual(v["r_frame_rate"], "30000/1001")
            self.assertAlmostEqual(float(v["start_time"]), 0.0, delta=0.001)
            self.assertEqual(v["codec_name"], "h264")
            self.assertEqual((v["width"], v["height"]), (W, H))


if __name__ == "__main__":
    unittest.main()
