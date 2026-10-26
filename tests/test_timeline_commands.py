"""The commands `timeline validate` (its snapshot), `timeline history`, `timeline undo` and
`timeline frames` (SPECS 4.1, 4.3, 10.4), run through the command-line parser as the owner
or Claude Code runs them. The frames come from synthetic preview copies whose frames can be
told apart by their grey value (tests/media.py)."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import cv2

import main
import store
import timeline
from tests import media
from tests.test_preview import PreviewFixture, W, H
from tests.timeline_fixture import example


def run(argv):
    """Run the command line. Returns (exit code, standard output, standard error)."""
    args = main.build_parser().parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args.func(args)
        except SystemExit as e:
            code = e.code or 0
    return code, out.getvalue(), err.getvalue()


class FileCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "timeline.json"

    def save(self, label):
        d = example()
        d["segments"][0]["label"] = label
        store.save(timeline.Timeline(d), str(self.path))

    def label(self):
        return json.loads(self.path.read_text())["segments"][0]["label"]


class ValidateMakesASnapshot(FileCase):
    def validate(self, *extra):
        return run(["timeline", "validate", "--file", str(self.path), "--no-file-checks", *extra])

    def test_a_valid_file_is_snapshotted_and_the_number_is_reported_on_standard_error(self):
        self.save("v0")
        code, out, err = self.validate()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])                  # standard output is still the JSON list
        self.assertEqual(len(store.list_snapshots(str(self.path))), 1)
        self.assertIn("0001", err)

    def test_a_second_validate_of_the_same_file_makes_no_second_snapshot(self):
        self.save("v0")
        self.validate()
        code, out, err = self.validate()
        self.assertEqual(len(store.list_snapshots(str(self.path))), 1)
        self.assertEqual(err, "")

    def test_a_file_with_errors_is_not_snapshotted(self):
        d = example()
        d["segments"][0]["out"] = 99999
        timeline.Timeline(d).save(str(self.path))
        code, out, err = self.validate()
        self.assertEqual(code, 1)
        self.assertFalse((self.dir / "timeline.history").exists())

    def test_the_spec_workflow_edit_validate_undo(self):
        self.save("v0")
        self.validate()
        d = example()
        d["segments"][0]["label"] = "by hand"
        timeline.Timeline(d).save(str(self.path))               # a direct edit
        self.validate()
        code, out, err = run(["timeline", "undo", "--file", str(self.path)])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.label(), "v0")


class HistoryCommand(FileCase):
    def test_it_lists_the_versions_with_the_change(self):
        self.save("v0")
        d = example()
        d["segments"][0].update({"label": "v1", "in": 68})
        store.save(timeline.Timeline(d), str(self.path))
        code, out, err = run(["timeline", "history", "--file", str(self.path)])
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertIn("timeline.json", lines[0])
        self.assertTrue(any(l.startswith("0001") and "first version kept" in l for l in lines), out)
        self.assertTrue(any("current" in l and "in 38 -> 68" in l for l in lines), out)
        self.assertEqual(err, "")

    def test_json_is_a_list_of_rows(self):
        self.save("v0")
        self.save("v1")
        code, out, _ = run(["timeline", "history", "--file", str(self.path), "--json"])
        rows = json.loads(out)
        self.assertEqual([r["number"] for r in rows], [1, None])
        self.assertTrue(rows[-1]["current"])
        self.assertEqual(rows[-1]["hash"], timeline.file_hash(str(self.path)))

    def test_a_file_with_no_history_says_so(self):
        self.save("v0")
        code, out, _ = run(["timeline", "history", "--file", str(self.path)])
        self.assertEqual(code, 0)
        self.assertIn("no earlier version", out)

    def test_a_missing_file_is_exit_code_2(self):
        code, out, err = run(["timeline", "history", "--file", str(self.dir / "nope.json")])
        self.assertEqual(code, 2)
        self.assertIn("not found", err)


class UndoCommand(FileCase):
    def test_it_restores_the_previous_version_and_says_what_it_did(self):
        self.save("v0")
        self.save("v1")
        code, out, err = run(["timeline", "undo", "--file", str(self.path)])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.label(), "v0")
        self.assertIn("0001", out)
        self.assertIn("undone", out)                            # where the replaced version is kept
        self.assertIn("label", out)                             # what the undo changed

    def test_with_nothing_to_undo_it_changes_nothing_and_exits_1(self):
        self.save("v0")
        before = self.path.read_bytes()
        code, out, err = run(["timeline", "undo", "--file", str(self.path)])
        self.assertEqual(code, 1)
        self.assertIn("no earlier version", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_a_missing_file_is_exit_code_2(self):
        code, out, err = run(["timeline", "undo", "--file", str(self.dir / "nope.json")])
        self.assertEqual(code, 2)
        self.assertIn("not found", err)


class FramesCommand(PreviewFixture):
    """Stills at film times and at source times, from the preview copies."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tl = cls.make_tl(cls)
        cls.file = cls.work / "frames-timeline.json"
        cls.tl.save(str(cls.file))
        # the stills go to a folder of their own: the fixture's WAV files lie next to its work
        # folder, and the tool rightly refuses to write inside a folder that holds originals
        cls._stills = tempfile.TemporaryDirectory()
        cls.stills_work = Path(cls._stills.name)

    @classmethod
    def tearDownClass(cls):
        cls._stills.cleanup()
        super().tearDownClass()

    def frames(self, *extra):
        return run(["timeline", "frames", "--file", str(self.file), "--work", str(self.stills_work),
                    "--no-file-checks", *extra])

    def grey(self, path):
        img = cv2.imread(str(path))
        self.assertIsNotNone(img, path)
        return img

    def center(self, img):
        return int(img[img.shape[0] // 2, img.shape[1] // 2].mean())

    def nearest_frame(self, value, near):
        """Which source frame within 3 of `near` has the grey value closest to `value`. The
        grey values repeat every 30 frames and neighbours differ by 7, so a still that shows
        another frame than `near` (or the same value with a shift) is found."""
        return min(range(near - 3, near + 4), key=lambda i: abs(media.frame_value(i) - value))

    def test_a_still_at_a_film_time_is_the_frame_of_that_film_time(self):
        # s001 is A frames 10..69 (film frames 0..59); film frame 30 = A frame 40
        t = 30 * 1001 / 30000
        code, out, err = self.frames("--film-s", f"{t:.4f}")
        self.assertEqual(code, 0, err)
        paths = out.strip().splitlines()
        self.assertEqual(len(paths), 1)
        png = Path(paths[0])
        self.assertTrue(png.is_file())
        self.assertEqual(png.parent, self.stills_work / "stills")
        img = self.grey(png)
        self.assertEqual(img.shape[:2], (540, 960))             # the frame of the film preview, 960 wide
        value = self.center(img)
        self.assertEqual(self.nearest_frame(value, 40), 40)
        self.assertAlmostEqual(value, media.frame_value(40), delta=3)

    def test_several_times_give_several_files_in_the_order_given(self):
        # film frame 70 is in s002 (B frames 5..64 start at film frame 60): B frame 15
        t1, t2 = 30 * 1001 / 30000, 70 * 1001 / 30000
        code, out, err = self.frames("--film-s", f"{t2:.4f},{t1:.4f}")
        self.assertEqual(code, 0, err)
        paths = [Path(p) for p in out.strip().splitlines()]
        self.assertEqual(len(paths), 2)
        v2 = self.center(self.grey(paths[0]))
        v1 = self.center(self.grey(paths[1]))
        self.assertEqual(self.nearest_frame(v2, 15), 15)
        self.assertEqual(self.nearest_frame(v1, 40), 40)
        self.assertNotEqual(paths[0], paths[1])

    def test_a_letterboxed_clip_shows_its_black_bars(self):
        code, out, err = self.frames("--film-s", f"{70 * 1001 / 30000:.4f}")        # clip B
        img = self.grey(Path(out.strip()))
        h, w = img.shape[:2]
        self.assertLessEqual(int(img[2, w // 2].max()), 8)                          # the bar is black
        self.assertGreater(int(img[h // 2, w // 2].mean()), 15)

    def test_a_source_time_gives_the_frame_of_the_clip(self):
        code, out, err = self.frames("--source", "A", "--at", f"{50 * 1001 / 30000:.4f}")
        self.assertEqual(code, 0, err)
        png = Path(out.strip())
        self.assertIn("A", png.name)
        img = self.grey(png)
        value = int(img[img.shape[0] // 2, img.shape[1] // 2].mean())
        self.assertEqual(self.nearest_frame(value, 50), 50)

    def test_the_first_frame_of_the_film_and_a_time_past_the_end(self):
        code, out, err = self.frames("--film-s", "0")
        self.assertEqual(code, 0, err)
        self.assertTrue(Path(out.strip()).is_file())
        code, out, err = self.frames("--film-s", "9999")
        self.assertEqual(code, 1)
        self.assertIn("beyond the end of the film", err)
        self.assertEqual(out, "")

    def test_a_removed_segment_has_no_film_time(self):
        tl = self.make_tl()
        tl.data["segments"][0]["enabled"] = False
        path = self.work / "frames-timeline-off.json"
        tl.save(str(path))
        code, out, err = run(["timeline", "frames", "--file", str(path), "--work", str(self.stills_work),
                              "--no-file-checks", "--film-s", "0"])
        self.assertEqual(code, 0, err)
        value = self.center(self.grey(Path(out.strip())))
        # the film now starts with s002, B frame 5, whatever s001 was
        self.assertEqual(self.nearest_frame(value, 5), 5)

    def test_an_unknown_clip_and_a_time_outside_the_clip_are_errors(self):
        code, out, err = self.frames("--source", "nope", "--at", "1")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no clip", err)
        code, out, err = self.frames("--source", "A", "--at", "9999")
        self.assertEqual(code, 1)
        self.assertIn("beyond the end of the clip", err)

    def test_a_negative_time_is_an_error(self):
        code, out, err = self.frames("--film-s", "-1")
        self.assertEqual(code, 1)
        self.assertIn("negative", err)

    def test_the_options_must_name_what_to_show(self):
        code, _, err = self.frames()
        self.assertEqual(code, 2)
        self.assertIn("--film-s", err)
        code, _, err = self.frames("--film-s", "1", "--source", "A", "--at", "1")
        self.assertEqual(code, 2)
        code, _, err = self.frames("--source", "A")
        self.assertEqual(code, 2)
        self.assertIn("--at", err)
        code, _, err = self.frames("--film-s", "abc")
        self.assertEqual(code, 2)
        self.assertIn("numbers", err)

    def test_without_a_work_folder_it_is_an_error(self):
        code, _, err = run(["--project", str(self.work / "none.json"), "timeline", "frames",
                            "--file", str(self.file), "--no-file-checks", "--film-s", "1"])
        self.assertEqual(code, 2)
        self.assertIn("--work", err)

    def test_the_still_is_never_written_inside_a_folder_that_holds_an_original(self):
        d = self.make_tl()
        orig = self.work / "originals"
        orig.mkdir(exist_ok=True)
        d.data["sources"]["A"]["original_video"] = str(orig / "x.braw")
        path = self.work / "frames-timeline-orig.json"
        d.save(str(path))
        code, out, err = run(["timeline", "frames", "--file", str(path), "--work", str(orig),
                              "--no-file-checks", "--film-s", "1"])
        self.assertNotEqual(code, 0)
        self.assertFalse((orig / "stills").exists())


if __name__ == "__main__":
    unittest.main()
