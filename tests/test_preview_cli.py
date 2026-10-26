"""The commands `preview` and `timeline summary` (SPECS 4.3, 5.5)."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

import main
import timeline
from tests import media
from tests.test_preview import PreviewFixture
from tests.timeline_fixture import example


def run_cli(argv):
    args = main.build_parser().parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args.func(args)
        except SystemExit as e:
            code = e.code or 0
    return code, out.getvalue() + err.getvalue()


class SummaryCommandTests(unittest.TestCase):
    def test_summary_prints_the_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timeline.json"
            timeline.Timeline(example()).save(str(path))
            code, text = run_cli(["timeline", "summary", "--file", str(path)])
        self.assertEqual(code, 0, text)
        self.assertIn("3 segments, 3 enabled", text)
        self.assertIn("s003", text)
        self.assertIn("pan with sausage", text)
        self.assertIn("00:01:04.13", text)

    def test_summary_of_a_missing_file_is_an_error(self):
        code, text = run_cli(["timeline", "summary", "--file", "/nonexistent/timeline.json"])
        self.assertEqual(code, 2)
        self.assertIn("not found", text)


class PreviewCommandTests(PreviewFixture):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.render_dir = Path(tmp.name) / "work"
        self.tl = self.make_tl()
        self.path = Path(tmp.name) / "timeline.json"
        self.tl.save(str(self.path))

    def test_renders_the_preview_and_prints_what_it_made(self):
        code, text = run_cli(["preview", "--file", str(self.path), "--work", str(self.render_dir),
                              "--encoder", "libx264", "--width", "320", "--no-file-checks"])
        self.assertEqual(code, 0, text)
        out = self.render_dir / "previews" / "preview.mp4"
        self.assertTrue(out.is_file())
        self.assertEqual(media.video_frames(out), 180)
        self.assertIn("180 frames", text)
        self.assertIn("preview.mp4", text)
        self.assertTrue((self.render_dir / "previews" / "preview.json").is_file())
        meta = json.loads((self.render_dir / "previews" / "preview.json").read_text())
        self.assertEqual(meta["timeline_hash"], timeline.file_hash(str(self.path)))

    def test_a_second_run_reuses_the_segments(self):
        argv = ["preview", "--file", str(self.path), "--work", str(self.render_dir),
                "--encoder", "libx264", "--width", "320", "--no-file-checks"]
        run_cli(argv)
        code, text = run_cli(argv)
        self.assertEqual(code, 0, text)
        self.assertIn("0 rendered, 3 reused", text)

    def test_range_and_name(self):
        code, text = run_cli(["preview", "--file", str(self.path), "--work", str(self.render_dir),
                              "--encoder", "libx264", "--width", "320", "--range", "1:3",
                              "--name", "part", "--no-file-checks"])
        self.assertEqual(code, 0, text)
        self.assertEqual(media.video_frames(self.render_dir / "previews" / "part.mp4"), 120)

    def test_the_file_checks_refuse_a_timeline_whose_original_is_missing(self):
        # the fixture points at /o/x.braw, which does not exist
        code, text = run_cli(["preview", "--file", str(self.path), "--work", str(self.render_dir),
                              "--encoder", "libx264"])
        self.assertEqual(code, 1)
        self.assertIn("V7", text)
        self.assertFalse((self.render_dir / "previews").exists())

    def test_an_invalid_timeline_is_refused_with_the_findings(self):
        d = copy.deepcopy(self.tl.data)
        d["segments"][0]["out"] = 5000
        self.path.write_text(json.dumps(d))
        code, text = run_cli(["preview", "--file", str(self.path), "--work", str(self.render_dir),
                              "--no-file-checks"])
        self.assertEqual(code, 1)
        self.assertIn("V9", text)
        self.assertFalse((self.render_dir / "previews").exists())

    def test_a_missing_timeline_file_and_a_missing_work_directory_are_errors(self):
        code, text = run_cli(["preview", "--file", "/nonexistent/t.json", "--work", str(self.render_dir)])
        self.assertEqual(code, 2)
        self.assertIn("not found", text)
        code, text = run_cli(["--project", str(self.path.parent / "none.json"), "preview",
                              "--file", str(self.path)])
        self.assertEqual(code, 2)
        self.assertIn("--work", text)

    def test_a_bad_range_is_an_error(self):
        code, text = run_cli(["preview", "--file", str(self.path), "--work", str(self.render_dir),
                              "--range", "banana"])
        self.assertEqual(code, 2)
        self.assertIn("--range", text)


if __name__ == "__main__":
    unittest.main()
