"""Check 2.5: the drift between camera and recorder (SPECS 7.3).

`analyse --drift` measures the offset again on the last 60 seconds of every clip of at least
120 seconds, with the same method as the matcher, and compares it with the stored offset,
which the matcher measured on the first 60 seconds. The drift is `offset_tail - offset_head`
seconds over the `T - 60` seconds between the middles of the two windows, reported in parts
per million. The report marks every clip whose drift exceeds half a frame.

Camera and recorder tracks are made with the claps of `make_track` at known times. For a
recorder clock that runs `ppm` parts per million fast, the clap that the camera hears at t
lies at t * (1 + ppm * 1e-6) + offset in the WAV."""

import contextlib
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

import clap
import config
import main
import matcher
from project import Project, SceneMatch
from tests.test_matcher import make_track

SR = 16000


def write_wav(path, samples):
    x = np.asarray(samples, dtype=np.float64)
    x = x / max(1e-9, np.abs(x).max()) * 0.9
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def events(total_sec, seed=7, every=4.5):
    """Claps at irregular times, from 3 s to 3 s before the end."""
    rng = np.random.default_rng(seed)
    t, out = 3.0, []
    while t < total_sec - 3.0:
        out.append(round(float(t), 4))
        t += every * (0.5 + rng.random())
    return out


def tracks(total_sec, offset=0.25, ppm=0.0, seed=7):
    """(camera track, recorder track) of `total_sec` seconds. The recorder hears every event
    `offset` later and its clock is `ppm` parts per million fast."""
    ev = events(total_sec, seed)
    cam = make_track(ev, total_sec, gain=1.0, noise=0.01, ambience=0.02, seed=1)
    wav = make_track([t * (1 + ppm * 1e-6) + offset for t in ev], total_sec + 1.0, gain=0.5,
                     noise=0.02, ambience=0.02, seed=2)
    return cam, wav


def stored_offset(cam, wav):
    """What the matcher stores: the offset of the first 60 seconds."""
    n = 60 * SR
    return matcher._find_offset(cam[:n], wav[:n])


class OneClip(unittest.TestCase):
    """measure_drift on arrays, no files."""

    def measure(self, total_sec, ppm, offset=0.25, fps=(30000, 1001)):
        cam, wav = tracks(total_sec, offset, ppm)
        head = stored_offset(cam, wav)
        # the same extraction that the project analysis uses, from arrays in memory
        def extract(which, seconds, start):
            x = cam if which == "cam" else wav
            a = int(start * SR)
            return x[a:a + int(seconds * SR)]
        return clap.measure_drift(extract, total_sec, head, fps)

    def test_a_recorder_clock_50_ppm_fast_is_measured_within_15_ppm(self):
        r = self.measure(200.0, 50.0)
        self.assertEqual(r.status, "measured")
        self.assertAlmostEqual(r.ppm, 50.0, delta=15.0)
        self.assertAlmostEqual(r.drift_s, 50e-6 * 140, delta=0.002)       # 7 ms between the windows
        self.assertFalse(r.exceeds)

    def test_a_clock_that_runs_slow_gives_a_negative_drift(self):
        r = self.measure(200.0, -50.0)
        self.assertAlmostEqual(r.ppm, -50.0, delta=15.0)
        self.assertLess(r.drift_s, 0)

    def test_no_drift_is_measured_as_about_zero(self):
        r = self.measure(200.0, 0.0)
        self.assertAlmostEqual(r.ppm, 0.0, delta=15.0)
        self.assertLess(abs(r.drift_s), 0.002)

    def test_a_drift_over_half_a_frame_is_marked(self):
        r = self.measure(200.0, 200.0)                                   # 28 ms between the windows
        self.assertTrue(r.exceeds)
        self.assertAlmostEqual(r.limit_s, 0.5 * 1001 / 30000, places=6)  # half a frame at 29.97 fps
        self.assertGreater(abs(r.drift_s), r.limit_s)

    def test_the_limit_is_half_a_frame_of_the_frame_rate_of_the_clip(self):
        r = self.measure(200.0, 0.0, fps=(25, 1))
        self.assertAlmostEqual(r.limit_s, 0.02, places=6)                # 20 ms at 25 fps, as in SPECS 7.3

    def test_a_clip_shorter_than_120_seconds_is_skipped(self):
        r = self.measure(100.0, 50.0)
        self.assertEqual(r.status, "skipped")
        self.assertIsNone(r.drift_s)
        self.assertIn("120", r.reason)

    def test_a_clip_of_120_seconds_is_measured(self):
        r = self.measure(120.0, 50.0)
        self.assertEqual(r.status, "measured")

    def test_a_tail_with_no_sound_in_common_is_not_measurable(self):
        cam, wav = tracks(200.0, 0.25, 0.0)
        wav = wav.copy()
        wav[-60 * SR:] = make_track([], 60.0, noise=0.02, ambience=0.02, seed=9)[: 60 * SR]
        head = stored_offset(cam, wav)

        def extract(which, seconds, start):
            x = cam if which == "cam" else wav
            a = int(start * SR)
            return x[a:a + int(seconds * SR)]
        r = clap.measure_drift(extract, 200.0, head, (30000, 1001))
        self.assertEqual(r.status, "not_measurable")
        self.assertIsNone(r.drift_s)
        self.assertIn("no sound in common", r.reason)

    def test_an_extraction_that_fails_is_an_error_for_that_clip_not_a_crash(self):
        def broken(which, seconds, start):
            raise RuntimeError("ffmpeg failed")
        r = clap.measure_drift(broken, 200.0, 0.25, (30000, 1001))
        self.assertEqual(r.status, "error")
        self.assertIn("ffmpeg failed", r.reason)

    def test_the_windows_are_the_last_60_seconds_and_the_recorder_window_follows_the_offset(self):
        asked = []

        def extract(which, seconds, start):
            asked.append((which, seconds, round(start, 6)))
            return np.zeros(int(seconds * SR), np.float32)
        clap.measure_drift(extract, 200.0, 0.25, (30000, 1001))
        self.assertEqual(asked, [("cam", 60.0, 140.0), ("wav", 60.0, 140.25)])

    def test_a_negative_head_offset_does_not_start_the_recorder_window_before_the_file(self):
        asked = []

        def extract(which, seconds, start):
            asked.append((which, round(start, 6)))
            return np.zeros(int(seconds * SR), np.float32)
        clap.measure_drift(extract, 200.0, -300.0, (30000, 1001))       # absurd, but must not crash
        self.assertEqual(asked[1], ("wav", 0.0))


class ExtractWithAStartTime(unittest.TestCase):
    def test_the_start_cuts_the_front_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            x = np.zeros(10 * SR)
            x[5 * SR:5 * SR + 160] = np.random.default_rng(3).uniform(-1, 1, 160)    # a click at 5 s
            path = Path(tmp) / "a.wav"
            write_wav(path, x)
            whole = matcher._extract_audio_raw(str(path), 10)
            cut = matcher._extract_audio_raw(str(path), 4, start=4.0)
            self.assertEqual(len(whole), 10 * SR)
            self.assertEqual(len(cut), 4 * SR)
            click_whole = int(np.argmax(np.abs(whole) > 0.5))
            click_cut = int(np.argmax(np.abs(cut) > 0.5))
            self.assertAlmostEqual(click_whole / SR, 5.0, delta=0.01)
            self.assertAlmostEqual(click_cut / SR, 1.0, delta=0.01)

    def test_no_start_is_the_old_behaviour(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.wav"
            write_wav(path, np.random.default_rng(1).uniform(-1, 1, 2 * SR))
            self.assertTrue(np.array_equal(matcher._extract_audio_raw(str(path), 1),
                                           matcher._extract_audio_raw(str(path), 1, start=None)))
            self.assertTrue(np.array_equal(matcher._extract_audio_raw(str(path), 1),
                                           matcher._extract_audio_raw(str(path), 1, start=0)))


class OverAProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name).resolve()
        cls.src = root / "Recordings"
        cls.src.mkdir()
        cls.root = root
        # name, length, drift in ppm
        spec = [("A_C001", 200.0, 50.0), ("A_C002", 90.0, 0.0), ("A_C003", 200.0, 200.0),
                ("A_C004", 200.0, 0.0)]
        matches = []
        for i, (name, total, ppm) in enumerate(spec):
            cam, wav = tracks(total, 0.25, ppm, seed=7 + i)
            if name == "A_C004":                      # nothing in common in the last minute
                wav = wav.copy()
                wav[-60 * SR:] = make_track([], 60.0, noise=0.02, ambience=0.02, seed=9)[: 60 * SR]
            video = cls.src / f"{name}.wav"           # the camera's scratch audio stands in for the clip
            audio = cls.src / f"ZOOM{i:04d}.WAV"
            write_wav(video, cam)
            write_wav(audio, wav)
            matches.append(SceneMatch(index=i, video_path=str(video), audio_path=str(audio),
                                      video_duration=total, audio_duration=total + 1.0,
                                      offset=stored_offset(cam, wav), confidence=1.0))
        cls.project = Project(video_dir=str(cls.src), audio_dir=str(cls.src), matches=matches)
        cls.project_file = root / "project.json"
        cls.project.save(str(cls.project_file))
        cls.work = root / "work"
        cls.rows = clap.analyse_drift_project(cls.project, str(cls.work), report_name="drift-test",
                                              log=lambda s: None)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def by_clip(self):
        return {r["clip"]: r for r in self.rows}

    def test_every_clip_has_a_row_in_project_order(self):
        self.assertEqual([r["clip"] for r in self.rows], ["A_C001", "A_C002", "A_C003", "A_C004"])

    def test_the_results(self):
        rows = self.by_clip()
        self.assertEqual(rows["A_C001"]["status"], "measured")
        self.assertAlmostEqual(rows["A_C001"]["ppm"], 50.0, delta=15.0)
        self.assertFalse(rows["A_C001"]["exceeds"])
        self.assertEqual(rows["A_C002"]["status"], "skipped")
        self.assertEqual(rows["A_C003"]["status"], "measured")
        self.assertTrue(rows["A_C003"]["exceeds"])
        self.assertEqual(rows["A_C004"]["status"], "not_measurable")

    def test_a_file_per_measured_clip_and_the_reports_are_written(self):
        one = json.loads((self.work / "analysis" / "A_C001" / "drift.json").read_text())
        self.assertEqual(one["clip"], "A_C001")
        self.assertAlmostEqual(one["ppm"], 50.0, delta=15.0)
        for name in ("A_C001", "A_C003"):
            self.assertTrue((self.work / "analysis" / name / "drift.json").is_file())
        self.assertFalse((self.work / "analysis" / "A_C002" / "drift.json").exists())
        report = json.loads((self.work / "reports" / "drift-test.json").read_text())
        self.assertEqual([c["clip"] for c in report["clips"]], ["A_C001", "A_C002", "A_C003", "A_C004"])
        self.assertEqual(report["over_limit"], ["A_C003"])
        text = (self.work / "reports" / "drift-test.txt").read_text()
        self.assertIn("A_C003", text)

    def test_the_report_text_names_the_clips_over_the_limit_and_the_counts(self):
        text = (self.work / "reports" / "drift-test.txt").read_text()
        self.assertIn("4 clips: measured 2, skipped 1 (shorter than 120 s), not measurable 1", text)
        self.assertIn("over the limit of half a frame: A_C003", text)
        self.assertIn("shorter than 120 s", text)

    def test_the_originals_are_only_read(self):
        before = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in self.src.iterdir())
        clap.analyse_drift_project(self.project, str(self.work), report_name="drift-again",
                                   log=lambda s: None)
        after = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in self.src.iterdir())
        self.assertEqual(before, after)

    def test_a_work_folder_inside_the_originals_is_refused(self):
        import safety
        with self.assertRaises(safety.OriginalsProtectedError):
            clap.analyse_drift_project(self.project, str(self.src / "work"), log=lambda s: None)

    def test_limit_and_only_choose_the_clips(self):
        rows = clap.analyse_drift_project(self.project, str(self.root / "work2"), limit=2,
                                          report_name="r", log=lambda s: None)
        self.assertEqual([r["clip"] for r in rows], ["A_C001", "A_C002"])
        rows = clap.analyse_drift_project(self.project, str(self.root / "work3"),
                                          only=["A_C003"], report_name="r", log=lambda s: None)
        self.assertEqual([r["clip"] for r in rows], ["A_C003"])

    def test_the_frame_rate_of_the_clip_comes_from_conversion_json_when_it_is_there(self):
        work = self.root / "work4"
        work.mkdir()
        (work / config.CONVERSION_FILE).write_text(json.dumps({"clips": {
            "A_C001": {"status": "done", "fps": {"num": 25, "den": 1}}}}))
        rows = clap.analyse_drift_project(self.project, str(work), only=["A_C001", "A_C003"],
                                          report_name="r", log=lambda s: None)
        by = {r["clip"]: r for r in rows}
        self.assertAlmostEqual(by["A_C001"]["limit_s"], 0.02, places=6)
        self.assertAlmostEqual(by["A_C003"]["limit_s"], 0.5 * 1001 / 30000, places=6)


class CommandLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name).resolve()
        src = root / "Recordings"
        src.mkdir()
        cam, wav = tracks(130.0, 0.25, 50.0)
        video, audio = src / "A_C001.wav", src / "ZOOM0000.WAV"
        write_wav(video, cam)
        write_wav(audio, wav)
        project = Project(video_dir=str(src), audio_dir=str(src), matches=[SceneMatch(
            index=0, video_path=str(video), audio_path=str(audio), video_duration=130.0,
            audio_duration=131.0, offset=stored_offset(cam, wav), confidence=1.0)])
        cls.project_file = root / "project.json"
        project.save(str(cls.project_file))
        cls.work = root / "work"
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

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

    def test_analyse_drift_writes_the_report_named_by_the_option(self):
        code, text = self.run_cli(["--project", str(self.project_file), "analyse", "--drift",
                                   "--work", str(self.work), "--report-name", "drift-all"])
        self.assertEqual(code, 0, text)
        self.assertTrue((self.work / "reports" / "drift-all.txt").is_file())
        self.assertTrue((self.work / "reports" / "drift-all.json").is_file())
        self.assertIn("A_C001", text)
        self.assertIn("measured 1", text)

    def test_the_default_report_name(self):
        code, _ = self.run_cli(["--project", str(self.project_file), "analyse", "--drift",
                                "--work", str(self.root / "w2")])
        self.assertEqual(code, 0)
        self.assertTrue((self.root / "w2" / "reports" / "drift-report.txt").is_file())

    def test_the_project_file_is_not_written(self):
        before = self.project_file.read_bytes()
        self.run_cli(["--project", str(self.project_file), "analyse", "--drift",
                      "--work", str(self.root / "w3")])
        self.assertEqual(self.project_file.read_bytes(), before)

    def test_drift_with_clap_names_each_report_apart(self):
        code, _ = self.run_cli(["--project", str(self.project_file), "analyse", "--clap", "--drift",
                                "--work", str(self.root / "w4"), "--report-name", "x"])
        self.assertEqual(code, 0)
        reports = self.root / "w4" / "reports"
        self.assertTrue((reports / "x.txt").is_file())              # the clap report
        self.assertTrue((reports / "x-drift.txt").is_file())

    def test_without_anything_to_analyse_the_message_names_drift(self):
        code, text = self.run_cli(["--project", str(self.project_file), "analyse"])
        self.assertEqual(code, 2)
        self.assertIn("--drift", text)


if __name__ == "__main__":
    unittest.main()
