"""Check 2.6, the real pipeline: synthetic H.264 files (tests/synth.py) are read through
ffmpeg at every sixth frame, 480 pixels wide, and the steady scenes come out in
frame numbers of the file. Also `analyse --scenes` over a project with preview copies
and a conversion.json (only copies whose status is `done` are read)."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import config
import main
import safety
import stable
from convert import ConversionStore
from project import Project, SceneMatch
from tests import synth

FPS_NUM, FPS_DEN = 30000, 1001


def pan_between(t):
    return (0.0 if t < 4 else (80 * (t - 4) if t < 6 else 160.0), 0.0, 1.0)


class VideoFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.static = root / "static.mp4"
        cls.pan = root / "pan.mp4"
        cls.person = root / "person.mp4"
        cls.n_static = synth.write_mp4(cls.static, synth.Camera(640, 360, seed=31, margin=300), 8, synth.still())
        cls.n_pan = synth.write_mp4(cls.pan, synth.Camera(640, 360, seed=32, margin=300), 10, pan_between)
        cam = synth.Camera(640, 360, seed=33, margin=300)
        cam.add_person(170, 300, lambda t: -80 + 55 * t, lambda t: 30)
        cls.n_person = synth.write_mp4(cls.person, cam, 8, synth.still())

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class ReadSamplesTests(unittest.TestCase):
    def test_every_sixth_frame_scaled_to_480_wide(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ramp.mp4"
            import subprocess
            import tools
            raw = b"".join(np.full((360, 640), 4 * i, np.uint8).tobytes() for i in range(60))
            subprocess.run([tools.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "rawvideo", "-pix_fmt", "gray", "-s", "640x360",
                            "-r", "30000/1001", "-i", "-", "-c:v", "libx264", "-crf", "10",
                            "-pix_fmt", "yuv420p", "-g", "30", "-bf", "0", str(path)],
                           input=raw, check=True)
            got = list(stable.read_samples(str(path), (640, 360), 6, 480))
        self.assertEqual([f for f, _ in got], list(range(0, 60, 6)))
        for f, pic in got:
            self.assertEqual(pic.shape, (270, 480))
            self.assertAlmostEqual(float(pic.mean()), 4 * f, delta=6)


class AnalyseVideoTests(VideoFixture):
    def run_video(self, path, frames):
        return stable.analyse_video(str(path), FPS_NUM, FPS_DEN, frames)

    def test_a_still_video_is_one_scene_in_frame_numbers_of_the_file(self):
        r = self.run_video(self.static, self.n_static)
        self.assertEqual(len(r.runs), 1)
        run = r.runs[0]
        self.assertLessEqual(run.in_frame, 12)
        self.assertGreaterEqual(run.out_frame, self.n_static - 18)
        self.assertLessEqual(run.out_frame, self.n_static)

    def test_still_pan_still_video(self):
        r = self.run_video(self.pan, self.n_pan)
        self.assertEqual(len(r.runs), 2)
        first, second = r.runs
        self.assertLess(first.out_frame, 4 * FPS_NUM / FPS_DEN)
        self.assertGreater(second.in_frame, 6 * FPS_NUM / FPS_DEN)
        self.assertTrue(any(a < 5 * FPS_NUM / FPS_DEN < b for a, b in r.moving))

    def test_a_person_walking_through_a_still_shot_is_not_camera_movement(self):
        r = self.run_video(self.person, self.n_person)
        self.assertEqual(r.moving, [])
        self.assertEqual(len(r.runs), 1)

    def test_an_unreadable_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.mp4"
            bad.write_bytes(b"not a video")
            with self.assertRaises(RuntimeError):
                stable.analyse_video(str(bad), FPS_NUM, FPS_DEN, 100, size=(640, 360))


class ProjectFixture(VideoFixture):
    def setUp(self):
        self._work_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._work_tmp.cleanup)
        root = Path(self._work_tmp.name).resolve()
        self.src = root / "Recordings"
        self.src.mkdir()
        self.work = root / "work"
        (self.work / "copies").mkdir(parents=True)
        names = ["A_C001", "A_C002", "A_C003"]
        videos = {"A_C001": (self.static, self.n_static), "A_C002": (self.pan, self.n_pan),
                  "A_C003": (self.person, self.n_person)}
        store = ConversionStore(str(self.work / config.CONVERSION_FILE))
        matches = []
        for i, name in enumerate(names):
            braw = self.src / f"{name}.braw"
            braw.write_bytes(b"fake braw %d " % i * 50)
            wav = self.src / f"ZOOM{i:04d}.WAV"
            wav.write_bytes(b"fake wav")
            copy = self.work / "copies" / f"{name}.mp4"
            copy.write_bytes(videos[name][0].read_bytes())
            store.clips[name] = {
                "original": str(braw), "preview_copy": str(copy), "status": "done",
                "frames": videos[name][1], "fps": {"num": FPS_NUM, "den": FPS_DEN},
                "size": {"width": 1280, "height": 720},
                "copy_size": {"width": 640, "height": 360}}
            matches.append(SceneMatch(index=i, video_path=str(braw), audio_path=str(wav),
                                      video_duration=8.0, audio_duration=8.0, offset=0.2,
                                      confidence=1.0))
        store.save()
        self.store = store
        self.project = Project(video_dir=str(self.src), audio_dir=str(self.src),
                               matches=matches, work_dir=str(self.work))
        self.project_file = root / "project.json"
        self.project.save(str(self.project_file))

    def listing(self):
        return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in self.src.iterdir())

    def run_cli(self, argv):
        args = main.build_parser().parse_args(argv)
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out):
            try:
                args.func(args)
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue()


class AnalyseProjectTests(ProjectFixture):
    def test_scenes_json_and_reports_are_written_for_every_clip(self):
        rows = stable.analyse_project(self.project, str(self.work), log=lambda s: None)
        self.assertEqual([r["clip"] for r in rows], ["A_C001", "A_C002", "A_C003"])
        self.assertEqual([r["runs"] for r in rows], [1, 2, 1])
        one = json.loads((self.work / "analysis" / "A_C002" / "scenes.json").read_text())
        self.assertEqual(one["clip"], "A_C002")
        self.assertEqual(len(one["runs"]), 2)
        self.assertEqual(one["fps"], {"num": FPS_NUM, "den": FPS_DEN})
        self.assertEqual(one["frames"], self.store.clips["A_C002"]["frames"])
        self.assertIn("settings", one)
        report = json.loads((self.work / "reports" / "scenes-report.json").read_text())
        self.assertEqual(len(report["clips"]), 3)
        text = (self.work / "reports" / "scenes-report.txt").read_text()
        self.assertIn("A_C002", text)
        self.assertIn("3 clips: 3 with steady scenes", text)

    def test_only_copies_that_are_done_are_read(self):
        self.store.clips["A_C002"]["status"] = "partial"
        self.store.save()
        rows = stable.analyse_project(self.project, str(self.work), log=lambda s: None)
        by = {r["clip"]: r for r in rows}
        self.assertEqual(by["A_C002"]["result"], "no_copy")
        self.assertFalse((self.work / "analysis" / "A_C002" / "scenes.json").exists())
        self.assertEqual(by["A_C001"]["result"], "scenes")

    def test_limit_and_only_and_report_name(self):
        rows = stable.analyse_project(self.project, str(self.work), limit=1,
                                      report_name="first1", log=lambda s: None)
        self.assertEqual(len(rows), 1)
        self.assertTrue((self.work / "reports" / "first1.txt").exists())
        rows = stable.analyse_project(self.project, str(self.work), only=["A_C003"],
                                      log=lambda s: None)
        self.assertEqual([r["clip"] for r in rows], ["A_C003"])

    def test_a_clip_without_a_steady_scene_is_listed_in_the_report(self):
        def reader(path, num, den, frames, st, size):
            return stable.SceneResult(num, den, frames, st)

        rows = stable.analyse_project(self.project, str(self.work), log=lambda s: None,
                                      reader=reader)
        self.assertTrue(all(r["result"] == "no_scene" for r in rows))
        text = (self.work / "reports" / "scenes-report.txt").read_text()
        self.assertIn("no steady scene", text)
        self.assertIn("0 with steady scenes, 3 without", text)

    def test_the_originals_are_not_changed(self):
        before = self.listing()
        stable.analyse_project(self.project, str(self.work), log=lambda s: None)
        self.assertEqual(self.listing(), before)

    def test_a_work_directory_inside_the_originals_is_refused(self):
        with self.assertRaises(safety.OriginalsProtectedError):
            stable.analyse_project(self.project, str(self.src / "work"), log=lambda s: None)
        self.assertFalse((self.src / "work").exists())

    def test_conversion_json_is_not_written(self):
        path = self.work / config.CONVERSION_FILE
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        stable.analyse_project(self.project, str(self.work), log=lambda s: None)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)


class AnalyseScenesCommandTests(ProjectFixture):
    def test_command_line(self):
        code, text = self.run_cli(["--project", str(self.project_file), "analyse", "--scenes",
                                   "--work", str(self.work), "--limit", "2"])
        self.assertEqual(code, 0, text)
        self.assertIn("A_C002", text)
        self.assertTrue((self.work / "analysis" / "A_C001" / "scenes.json").exists())
        self.assertFalse((self.work / "analysis" / "A_C003").exists())
        self.assertTrue((self.work / "reports" / "scenes-report.txt").exists())

    def test_only_and_report_name(self):
        code, text = self.run_cli(["--project", str(self.project_file), "analyse", "--scenes",
                                   "--work", str(self.work), "--only", "A_C001",
                                   "--report-name", "mine"])
        self.assertEqual(code, 0, text)
        self.assertTrue((self.work / "reports" / "mine.txt").exists())

    def test_asking_for_nothing_names_both_choices(self):
        code, text = self.run_cli(["--project", str(self.project_file), "analyse",
                                   "--work", str(self.work)])
        self.assertEqual(code, 2)
        self.assertIn("--clap", text)
        self.assertIn("--scenes", text)


if __name__ == "__main__":
    unittest.main()
