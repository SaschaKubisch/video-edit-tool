"""Step 6: `scenes crop SCENE --still`, a picture for Claude Code of the 9:16 window on a frame of a scene (SPECS 11.5).
The clip is a horizontal gradient (black on the left to white on the right), so the mean grey of what the window
shows tells where the window is."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import shorts
import timeline
import tools
from tests.test_scenes_cli import run

W, H = 320, 180
FPS = (30000, 1001)


def write_gradient(path, frames=90):
    cmd = [tools.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
           f"color=c=black:s={W}x{H}:r=30000/1001", "-vf", "geq=lum='255*X/W':cb=128:cr=128,format=yuv420p",
           "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p",
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv", str(path)]
    subprocess.run(cmd, check=True)


class WindowStillCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.copy = root / "G.mp4"
        write_gradient(cls.copy)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.work = self.dir / "work"
        self.work.mkdir()
        self.path = self.dir / "timeline.json"
        self.tl = timeline.Timeline({
            "version": 1, "kind": "film",
            "film": {"fps": {"num": FPS[0], "den": FPS[1]}, "frame": {"width": W * 2, "height": H * 2}, "fit": "fit"},
            "sources": {"G": {"original_video": "/o/G.braw", "preview_copy": str(self.copy), "audio": "/o/G.wav",
                              "audio_offset_s": 0.1, "frames": 90, "fps": {"num": FPS[0], "den": FPS[1]},
                              "size": {"width": W * 2, "height": H * 2}, "copy_lut_sha256": "0" * 64}},
            "segments": [{"id": "s001", "source": "G", "in": 0, "out": 90, "label": "gradient"},
                         {"id": "s002", "source": "G", "in": 0, "out": 60, "enabled": False}],
            "look": {"base": {"mode": "none"}}, "audio": {"sample_rate": 48000, "loudness": {"mode": "off"}}})
        self.tl.save(str(self.path))

    def still(self, *argv):
        code, out, err = run(["scenes", "crop", "s001", *argv, "--still", "--file", str(self.path),
                              "--work", str(self.work)])
        path = next((l.split("Still: ", 1)[1] for l in out.splitlines() if l.startswith("Still: ")), None)
        return code, out, err, path

    def cut_out_mean(self, path):
        """The mean grey of the right-hand panel (what the window shows)."""
        img = np.asarray(Image.open(path).convert("L"), dtype=float)
        h = img.shape[0] - 64                                  # the label strip is 64 px
        panel_w = int(1500 * 0.68)
        return img[:h, panel_w + 16:].mean()


class StillTests(WindowStillCase):
    def test_the_window_at_the_left_shows_dark_and_at_the_right_light(self):
        means = {}
        for x in ("0", "0.5", "1"):
            code, out, err, path = self.still("--x", x, "--dry-run")
            self.assertEqual(code, 0, err)
            means[x] = self.cut_out_mean(path)
        self.assertLess(means["0"], 80)
        self.assertGreater(means["1"], 170)
        self.assertTrue(means["0"] + 30 < means["0.5"] < means["1"] - 30, means)

    def test_the_file_is_a_picture_that_is_not_too_wide(self):
        code, out, err, path = self.still("--x", "0.2", "--dry-run")
        self.assertTrue(path.endswith(".jpg"))
        self.assertTrue(path.startswith(str(self.work / "stills")))
        img = Image.open(path)
        self.assertLessEqual(img.size[0], 2000)
        self.assertLessEqual(img.size[1], 2000)

    def test_the_outside_of_the_window_is_dimmed_and_the_window_is_not(self):
        code, out, err, path = self.still("--x", "1", "--dry-run")
        img = np.asarray(Image.open(path).convert("L"), dtype=float)
        panel_w = int(1500 * 0.68)
        mid = round(panel_w * H / W) // 2
        inside = img[mid, round(panel_w * 0.75)]           # the window covers the right 31.6 percent: gradient 190
        outside = img[mid, round(panel_w * 0.55)]          # gradient 140, dimmed to 30 percent
        self.assertGreater(inside, 150)
        self.assertLess(outside, 80)

    def test_dry_run_with_still_writes_no_change_but_the_picture(self):
        before = self.path.read_bytes()
        code, out, err, path = self.still("--x", "0.7", "--dry-run")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(Path(path).is_file())

    def test_without_dry_run_the_crop_is_saved_and_the_still_shows_it(self):
        code, out, err, path = self.still("--x", "1")
        self.assertEqual(code, 0, err)
        data = timeline.Timeline.load(str(self.path)).data
        self.assertEqual(data["segments"][0]["crop"], {"x": 1.0})
        self.assertGreater(self.cut_out_mean(path), 170)

    def test_still_alone_shows_the_window_as_it_is_now(self):
        run(["scenes", "crop", "s001", "--x", "0", "--file", str(self.path)])
        code, out, err, path = self.still()
        self.assertEqual(code, 0, err)
        self.assertLess(self.cut_out_mean(path), 80)
        self.assertEqual(timeline.Timeline.load(str(self.path)).data["segments"][0]["crop"], {"x": 0.0})

    def test_a_pan_still_shows_the_window_at_the_chosen_second(self):
        run(["scenes", "crop", "s001", "--from", "0", "--to", "1", "--file", str(self.path)])
        early = self.still("--at", "0.1")[3]
        late = self.still("--at", "2.9")[3]
        self.assertLess(self.cut_out_mean(early), self.cut_out_mean(late) - 100)

    def test_a_second_outside_the_scene_is_refused(self):
        code, out, err, path = self.still("--x", "0.5", "--at", "9", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("not inside scene s001", err)

    def test_a_removed_scene_has_no_place_for_a_still(self):
        code, out, err = run(["scenes", "crop", "s002", "--x", "0.5", "--still", "--dry-run", "--file", str(self.path),
                              "--work", str(self.work)])
        self.assertEqual(code, 1)
        self.assertIn("removed", err)

    def test_the_still_needs_a_work_folder(self):
        code, out, err = run(["scenes", "crop", "s001", "--x", "0.5", "--still", "--dry-run", "--file", str(self.path)])
        self.assertEqual(code, 2)
        self.assertIn("work", err.lower())


class WindowInfoTests(unittest.TestCase):
    def test_the_numbers_for_the_two_kinds_of_clips(self):
        a = shorts.window_info({"width": 4096, "height": 1720}, (1080, 1920), {"x": 0.3})
        self.assertEqual((a["window"], a["x"], a["free"]), ((968, 1720), 938, 3128))
        self.assertAlmostEqual(a["share_pct"], 23.6, places=1)
        self.assertAlmostEqual(a["scale"], 1.1157, places=3)
        b = shorts.window_info({"width": 4096, "height": 2160}, (1080, 1920), None)
        self.assertEqual((b["window"], b["x"], b["free"]), ((1216, 2160), 1440, 2880))
        self.assertAlmostEqual(b["scale"], 0.888, places=3)

    def test_a_pan_moves_the_window_with_the_time(self):
        crop = {"x_from": 0.0, "x_to": 1.0}
        self.assertEqual(shorts.window_info({"width": 4096, "height": 2160}, (1080, 1920), crop, 0.0)["x"], 0)
        self.assertEqual(shorts.window_info({"width": 4096, "height": 2160}, (1080, 1920), crop, 1.0)["x"], 2880)
        mid = shorts.window_info({"width": 4096, "height": 2160}, (1080, 1920), crop, 0.5)["x"]
        self.assertAlmostEqual(mid, 1440, delta=2)

    def test_the_text_names_the_numbers(self):
        text = shorts.describe_window(shorts.window_info({"width": 4096, "height": 1720}, (1080, 1920), {"x": 0.3}))
        for part in ("968 x 1720", "4096 x 1720", "23.6 percent", "938", "enlarged by 1.116", "1080 x 1920"):
            self.assertIn(part, text)


if __name__ == "__main__":
    unittest.main()
