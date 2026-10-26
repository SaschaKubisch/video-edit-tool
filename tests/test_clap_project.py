"""`analyse --clap` over a project: the files it writes, the flags in the report,
and that it only reads the originals."""

import contextlib
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

import clap
import main
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


class AnalyseClapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        self.src = root / "Recordings"
        self.src.mkdir()
        self.work = root / "work"
        # (clip name, clap time in the camera audio or None, offset, clap in the WAV?)
        spec = [("A_C001", 2.0, 0.276, True),     # usual offset, claps agree
                ("A_C002", 3.0, 1.28, True),      # offset outside the usual range
                ("A_C003", None, 0.2, False),     # no clap anywhere
                ("A_C004", 1.5, 0.15, False)]     # clap only in the camera audio
        matches = []
        for i, (name, t, off, wav_clap) in enumerate(spec):
            cam = make_track([t] if t is not None else [], 25.0, gain=1.0, noise=0.01, seed=i + 1)
            wav = make_track([t + off] if (t is not None and wav_clap) else [], 25.0,
                             gain=0.4, noise=0.02, seed=i + 11)
            video = self.src / f"{name}.wav"        # the camera's scratch audio stands in for the clip
            audio = self.src / f"ZOOM{i:04d}.WAV"
            write_wav(video, cam)
            write_wav(audio, wav)
            matches.append(SceneMatch(index=i, video_path=str(video), audio_path=str(audio),
                                      video_duration=25.0, audio_duration=25.0,
                                      offset=off, confidence=1.0))
        self.project = Project(video_dir=str(self.src), audio_dir=str(self.src), matches=matches)
        self.project_file = root / "project.json"
        self.project.save(str(self.project_file))
        self.quiet = contextlib.redirect_stdout(io.StringIO())

    def listing(self):
        return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in self.src.iterdir())

    def test_rows_for_every_clip_with_flags(self):
        rows = clap.analyse_project(self.project, str(self.work), log=lambda s: None)
        by = {r["clip"]: r for r in rows}
        self.assertEqual(by["A_C001"]["result"], "agree")
        self.assertAlmostEqual(by["A_C001"]["clap_s"], 2.0, delta=0.01)
        self.assertFalse(by["A_C001"]["review"])
        self.assertEqual(by["A_C002"]["result"], "agree")
        self.assertTrue(by["A_C002"]["review"])
        self.assertTrue(any("usual range" in r for r in by["A_C002"]["reasons"]))
        self.assertEqual(by["A_C003"]["result"], "none")
        self.assertIsNone(by["A_C003"]["clap_s"])
        self.assertTrue(by["A_C003"]["review"])
        self.assertEqual(by["A_C004"]["result"], "single")
        self.assertAlmostEqual(by["A_C004"]["clap_s"], 1.5, delta=0.01)

    def test_files_are_written_into_the_work_directory(self):
        clap.analyse_project(self.project, str(self.work), log=lambda s: None)
        one = json.loads((self.work / "analysis" / "A_C001" / "clap.json").read_text())
        self.assertEqual(one["result"], "agree")
        self.assertEqual(one["clip"], "A_C001")
        report = json.loads((self.work / "reports" / "clap-report.json").read_text())
        self.assertEqual([c["clip"] for c in report["clips"]],
                         ["A_C001", "A_C002", "A_C003", "A_C004"])
        text = (self.work / "reports" / "clap-report.txt").read_text()
        self.assertIn("A_C002", text)
        self.assertIn("usual range", text)
        self.assertIn("4 clips: agree 2, single 1, none 1; for review 3.", text)

    def test_limit_and_report_name(self):
        rows = clap.analyse_project(self.project, str(self.work), limit=2,
                                    report_name="clap-report-first2", log=lambda s: None)
        self.assertEqual(len(rows), 2)
        self.assertTrue((self.work / "reports" / "clap-report-first2.json").exists())
        self.assertFalse((self.work / "analysis" / "A_C003").exists())

    def test_the_originals_are_not_changed(self):
        before = self.listing()
        clap.analyse_project(self.project, str(self.work), log=lambda s: None)
        self.assertEqual(self.listing(), before)

    def test_a_work_directory_inside_the_originals_is_refused(self):
        import safety
        with self.assertRaises(safety.OriginalsProtectedError):
            clap.analyse_project(self.project, str(self.src / "work"), log=lambda s: None)
        self.assertFalse((self.src / "work").exists())

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

    def test_command_line(self):
        code, text = self.run_cli(["--project", str(self.project_file), "analyse", "--clap",
                                   "--work", str(self.work), "--limit", "3",
                                   "--report-name", "first3"])
        self.assertEqual(code, 0, text)
        self.assertIn("A_C003", text)
        self.assertIn("REVIEW", text)
        self.assertTrue((self.work / "reports" / "first3.txt").exists())

    def test_command_line_needs_a_work_directory(self):
        code, text = self.run_cli(["--project", str(self.project_file), "analyse", "--clap"])
        self.assertEqual(code, 2)
        self.assertIn("--work", text)

    def test_command_line_without_a_choice_of_analysis_is_an_error(self):
        code, text = self.run_cli(["--project", str(self.project_file), "analyse",
                                   "--work", str(self.work)])
        self.assertEqual(code, 2)
        self.assertIn("--clap", text)


if __name__ == "__main__":
    unittest.main()
