"""The `convert` subcommand of main.py: options, exit codes, and what it writes."""

import contextlib
import io
import json
import unittest
from pathlib import Path

import main
from project import Project, SceneMatch
from tests.fakes import Workspace


def run_cli(argv, decoder):
    """Run main's parser and the convert command. Returns (exit code, output)."""
    args = main.build_parser().parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args.func(args, decoder=decoder)
        except SystemExit as e:
            code = e.code or 0
    return code, out.getvalue() + err.getvalue()


class ConvertCliTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self, names=("A_C001", "A_C002", "A_C003"))
        matches = [SceneMatch(index=i, video_path=p, audio_path=str(self.ws.src / f"Z{i}.WAV"),
                              video_duration=1.0, audio_duration=1.0, offset=0.1,
                              confidence=1.0)
                   for i, (n, p) in enumerate(self.ws.refs)]
        self.project_file = self.ws.root / "project.json"
        Project(video_dir=str(self.ws.src), audio_dir=str(self.ws.src),
                matches=matches).save(str(self.project_file))
        self.project_bytes = self.project_file.read_bytes()
        self.base = ["--project", str(self.project_file), "convert",
                     "--lut", str(self.ws.lut), "--encoder", "libx264"]

    def copies(self):
        d = self.ws.work / "copies"
        return sorted(p.name for p in d.glob("*.mp4")) if d.exists() else []

    def test_converts_all_matched_clips_and_leaves_project_json_alone(self):
        code, text = run_cli(self.base + ["--work", str(self.ws.work)], self.ws.decoder)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.copies(), ["A_C001.mp4", "A_C002.mp4", "A_C003.mp4"])
        self.assertEqual(self.project_file.read_bytes(), self.project_bytes)
        self.assertIn("Converted 3", text)

    def test_work_directory_comes_from_the_project_when_not_given(self):
        proj = Project.load(str(self.project_file))
        proj.work_dir = str(self.ws.work)
        proj.save(str(self.project_file))
        code, text = run_cli(self.base, self.ws.decoder)
        self.assertEqual(code, 0, text)
        self.assertEqual(len(self.copies()), 3)

    def test_no_work_directory_anywhere_is_an_error(self):
        code, text = run_cli(self.base, self.ws.decoder)
        self.assertEqual(code, 2)
        self.assertIn("--work", text)

    def test_missing_project_file_is_an_error(self):
        self.project_file.unlink()
        code, text = run_cli(self.base + ["--work", str(self.ws.work)], self.ws.decoder)
        self.assertEqual(code, 2)
        self.assertIn("project", text.lower())

    def test_limit_and_only(self):
        code, _ = run_cli(self.base + ["--work", str(self.ws.work), "--limit", "1"],
                          self.ws.decoder)
        self.assertEqual((code, self.copies()), (0, ["A_C001.mp4"]))
        code, _ = run_cli(self.base + ["--work", str(self.ws.work), "--only", "A_C003"],
                          self.ws.decoder)
        self.assertEqual((code, self.copies()), (0, ["A_C001.mp4", "A_C003.mp4"]))

    def test_dry_run_writes_nothing_and_prints_the_estimate(self):
        code, text = run_cli(self.base + ["--work", str(self.ws.work), "--dry-run"],
                             self.ws.decoder)
        self.assertEqual(code, 0, text)
        self.assertIn("Would convert 3", text)
        self.assertFalse(self.ws.work.exists())

    def test_a_failed_clip_gives_exit_code_1(self):
        self.ws.decoder.fail_on = {self.ws.refs[1][1]}
        code, text = run_cli(self.base + ["--work", str(self.ws.work)], self.ws.decoder)
        self.assertEqual(code, 1, text)
        self.assertEqual(self.copies(), ["A_C001.mp4", "A_C003.mp4"])

    def test_work_directory_inside_the_originals_gives_exit_code_2(self):
        code, text = run_cli(self.base + ["--work", str(self.ws.src / "work")],
                             self.ws.decoder)
        self.assertEqual(code, 2)
        self.assertIn("original", text.lower())
        self.assertFalse((self.ws.src / "work").exists())

    def test_probe_prints_the_facts_as_json(self):
        path = self.ws.refs[0][1]
        code, text = run_cli(["convert", "--probe", path], self.ws.decoder)
        self.assertEqual(code, 0, text)
        data = json.loads(text[text.index("{"):])
        for key in ("width", "height", "fps_num", "fps_den", "frames",
                    "colour_science_gen", "has_sidecar", "tracks"):
            self.assertIn(key, data)
        self.assertEqual(data["frames"], 12)

    def test_test_converts_the_start_of_one_clip_into_a_test_folder(self):
        path = self.ws.refs[0][1]
        code, text = run_cli(self.base + ["--work", str(self.ws.work),
                                          "--test", path, "--seconds", "0.2"],
                             self.ws.decoder)
        self.assertEqual(code, 0, text)
        copy = self.ws.work / "test" / "copies" / "A_C001.mp4"
        self.assertTrue(copy.is_file())
        # 0.2 s at 29.97 fps is 6 frames
        self.assertIn("6 frames", text)
        self.assertFalse((self.ws.work / "conversion.json").exists())



class GracefulStopTests(unittest.TestCase):
    """`kill` (SIGTERM) must stop a long run cleanly: the partial copy removed, the
    lock released, the clip left as 'partial' so that the next run converts it again.
    A background job started by a shell ignores SIGINT, so SIGTERM is the way to stop."""

    def test_sigterm_becomes_a_keyboard_interrupt_and_the_run_can_resume(self):
        import os
        import signal
        import threading

        import convert
        ws = Workspace(self, names=("A_C001", "A_C002"))
        real = ws.decoder.write_frames
        started = threading.Event()

        def slow(path, scale, sink, max_frames=None):
            if path == ws.refs[1][1]:
                started.set()
                os.kill(os.getpid(), signal.SIGTERM)       # as `kill pid` would
            return real(path, scale, sink, max_frames)

        ws.decoder.write_frames = slow
        old = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, old)
        main.install_graceful_stop()
        conv = convert.Converter(str(ws.work), ws.decoder,
                                 convert.PreviewSettings(encoder="libx264"), str(ws.lut),
                                 [str(ws.src)], log=lambda s: None, free_bytes=lambda p: 10 ** 12)
        refs = [convert.ClipRef(n, p) for n, p in ws.refs]
        with self.assertRaises(KeyboardInterrupt):
            conv.run(refs)
        self.assertTrue(started.is_set())
        self.assertFalse((ws.work / ".convert.lock").exists())
        self.assertEqual(list(ws.work.rglob("*.partial")), [])
        data = json.loads((ws.work / "conversion.json").read_text())
        self.assertEqual(data["clips"]["A_C001"]["status"], "done")
        self.assertEqual(data["clips"]["A_C002"]["status"], "partial")
        ws.decoder.write_frames = real
        summary = conv.run(refs)
        self.assertEqual((summary.converted, summary.skipped), (1, 1))


if __name__ == "__main__":
    unittest.main()
