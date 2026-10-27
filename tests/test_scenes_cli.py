"""Check 5.1, the commands `scenes index`, `scenes sheets` and `scenes frames`, run through the command-line
parser as Claude Code runs them (SPECS 10.2, 10.4)."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import main
from tests.test_scene_sheets import SheetFixture


def run(argv):
    """Run the command line. Returns (exit code, standard output, standard error)."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args = main.build_parser().parse_args(argv)
            args.func(args)
        except SystemExit as e:
            code = e.code or 0
    return code, out.getvalue(), err.getvalue()


class ScenesCommandCase(SheetFixture):
    def setUp(self):
        super().setUp()
        self.make()

    def scenes(self, *argv, work=True):
        extra = ["--work", str(self.work)] if work else []
        return run(["scenes", argv[0], *argv[1:], "--file", str(self.path), *extra])

    def index_json(self):
        return json.loads((self.folder() / "index.json").read_text(encoding="utf-8"))


class IndexCommand(ScenesCommandCase):
    def test_it_writes_the_json_and_the_markdown_and_prints_where(self):
        code, out, err = self.scenes("index")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.folder() / "index.json").is_file())
        self.assertTrue((self.folder() / "index.md").is_file())
        self.assertIn(str(self.folder() / "index.md"), out)
        self.assertIn(str(self.folder() / "index.json"), out)
        self.assertIn("4 scenes in the film", out)
        self.assertEqual([s["id"] for s in self.index_json()["scenes"]], ["s001", "s002", "s003", "s004"])

    def test_it_says_that_the_sheets_are_not_drawn_yet(self):
        code, out, err = self.scenes("index")
        self.assertIn("scenes sheets", out)
        self.assertFalse(any(s["sheet"]["exists"] for s in self.index_json()["scenes"]))

    def test_with_sheets_it_draws_them_first_and_lists_them_as_there(self):
        code, out, err = self.scenes("index", "--sheets")
        self.assertEqual(code, 0, err)
        self.assertTrue(all(s["sheet"]["exists"] for s in self.index_json()["scenes"]))
        self.assertTrue((self.folder() / "sheets" / "s003.jpg").is_file())

    def test_the_hash_of_the_file_is_in_the_output(self):
        import timeline
        code, out, err = self.scenes("index")
        self.assertIn(timeline.file_hash(str(self.path))[:8], out)

    def test_a_missing_file_is_exit_code_2_and_a_missing_work_folder_too(self):
        code, out, err = run(["scenes", "index", "--file", str(self.dir / "none.json"), "--work", str(self.work)])
        self.assertEqual(code, 2)
        self.assertIn("not found", err)
        old = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, old)
        code, out, err = run(["scenes", "index", "--file", str(self.path)])
        self.assertEqual(code, 2)
        self.assertIn("work", err.lower())

    def test_a_work_folder_inside_the_originals_is_refused(self):
        import timeline
        tl = timeline.Timeline.load(str(self.path))
        tl.data["sources"]["A"]["audio"] = str(self.dir / "originals" / "a.wav")
        tl.save(str(self.path))
        code, out, err = run(["scenes", "index", "--file", str(self.path), "--work", str(self.dir / "originals")])
        self.assertEqual(code, 2)
        self.assertIn("original", err)
        self.assertFalse((self.dir / "originals").exists())


class SheetsCommand(ScenesCommandCase):
    def test_it_draws_every_sheet_and_the_overview_and_says_how_many(self):
        code, out, err = self.scenes("sheets")
        self.assertEqual(code, 0, err)
        self.assertIn("4 scenes", out)
        self.assertIn("4 drawn", out)
        self.assertIn("overview-01.jpg", out)
        self.assertTrue((self.folder() / "overview-01.jpg").is_file())
        self.assertTrue((self.folder() / "sheets" / "s004.jpg").is_file())

    def test_it_writes_the_index_again_so_it_lists_the_sheets(self):
        self.scenes("index")
        self.assertFalse(self.index_json()["scenes"][0]["sheet"]["exists"])
        self.scenes("sheets")
        data = self.index_json()
        self.assertTrue(all(s["sheet"]["exists"] for s in data["scenes"]))
        self.assertEqual(data["scenes"][0]["overview"], {"page": str(self.folder() / "overview-01.jpg"), "place": 1})

    def test_a_second_run_draws_none(self):
        self.scenes("sheets")
        code, out, err = self.scenes("sheets")
        self.assertEqual(code, 0, err)
        self.assertIn("0 drawn", out)
        self.assertIn("4 kept", out)

    def test_a_missing_preview_copy_is_an_error_that_names_it(self):
        import timeline
        tl = timeline.Timeline.load(str(self.path))
        tl.data["sources"]["B"]["preview_copy"] = str(self.copies / "gone.mp4")
        tl.save(str(self.path))
        code, out, err = self.scenes("sheets")
        self.assertEqual(code, 1)
        self.assertIn("gone.mp4", err)


class FramesCommand(ScenesCommandCase):
    def test_it_prints_the_path_of_each_page_one_per_line(self):
        code, out, err = self.scenes("frames", "s002", "--every", "2s")
        self.assertEqual(code, 0, err)
        paths = out.strip().splitlines()
        self.assertEqual(len(paths), 1)
        self.assertTrue(Path(paths[0]).is_file())
        self.assertIn("s002", Path(paths[0]).name)

    def test_the_step_defaults_to_one_second(self):
        code, out, err = self.scenes("frames", "s002")
        self.assertEqual(code, 0, err)
        self.assertIn("every1s", out)

    def test_from_and_to_are_film_seconds(self):
        code, out, err = self.scenes("frames", "s002", "--every", "1s", "--from", "4", "--to", "7")
        self.assertEqual(code, 0, err)
        self.assertIn("4.00-7.00", out)

    def test_an_unknown_scene_and_a_range_outside_the_scene_are_exit_code_1(self):
        code, out, err = self.scenes("frames", "s999", "--every", "1s")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("s999", err)
        code, out, err = self.scenes("frames", "s002", "--every", "1s", "--from", "0", "--to", "1")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("s002", err)

    def test_a_step_that_is_not_a_step_is_exit_code_2(self):
        code, out, err = self.scenes("frames", "s002", "--every", "fast")
        self.assertEqual(code, 2)
        self.assertIn("step", err)

    def test_too_many_stills_are_refused_and_nothing_is_drawn(self):
        code, out, err = self.scenes("frames", "s003", "--every", "0.1s")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("120", err)


if __name__ == "__main__":
    unittest.main()
