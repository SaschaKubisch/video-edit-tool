"""Step 6, the commands that mark scenes for the shorts: `scenes pin` and `scenes unpin` (the importance of a scene,
`priority` `must`), and `scenes crop` (where the 9:16 window sits in a scene, `crop`). Each goes through the validator,
makes one snapshot for `timeline undo`, and has --dry-run. The film itself does not change: its length stays.

The film is the example of SPECS 3.3: s001 and s002 are cut from clip A002_01011540_C002 (4096 x 1720), s003 from
A002_01060615_C083 (4096 x 2160)."""

import json
import unittest

import sceneedit
from tests.test_scene_edit import EditCase, run
from tests.timeline_fixture import example


class PinTests(EditCase):
    def test_pin_marks_the_scene_must_and_leaves_the_film_as_it_is(self):
        frames = self.frames()
        code, out, err = self.scenes("pin", "s002")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.seg("s002")["priority"], "must")
        self.assertNotIn("priority", self.seg("s001"))
        self.assertEqual(self.frames(), frames)
        self.assertIn("s002", out)
        self.assertIn("pinned", out)
        self.assertIn("snapshot 0001", out)
        self.assertEqual(self.snapshots(), 1)

    def test_several_scenes_and_undo(self):
        before = self.path.read_bytes()
        self.scenes("pin", "s001,s003")
        self.assertEqual([self.seg(s).get("priority") for s in ("s001", "s002", "s003")], ["must", None, "must"])
        code, out, err = run(["timeline", "undo", "--file", str(self.path)])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unpin_takes_the_mark_away(self):
        self.scenes("pin", "s001")
        code, out, err = self.scenes("unpin", "s001")
        self.assertEqual(code, 0, err)
        self.assertNotIn("priority", self.seg("s001"))
        self.assertIn("no longer pinned", out)

    def test_unpin_also_clears_a_drop_mark(self):
        self.data["segments"][1]["priority"] = "drop"
        self.save()
        code, out, err = self.scenes("unpin", "s002")
        self.assertEqual(code, 0, err)
        self.assertNotIn("priority", self.seg("s002"))

    def test_pinning_a_scene_that_is_marked_drop_says_so(self):
        self.data["segments"][1]["priority"] = "drop"
        self.save()
        code, out, err = self.scenes("pin", "s002")
        self.assertEqual(self.seg("s002")["priority"], "must")
        self.assertIn("drop", out)

    def test_a_scene_that_is_already_pinned_is_no_change_and_no_snapshot(self):
        self.scenes("pin", "s001")
        code, out, err = self.scenes("pin", "s001")
        self.assertEqual(code, 0, err)
        self.assertIn("already pinned", out)
        self.assertEqual(self.snapshots(), 1)

    def test_a_removed_scene_can_be_pinned_but_the_output_says_it_is_not_in_the_film(self):
        self.scenes("remove", "s002")
        code, out, err = self.scenes("pin", "s002")
        self.assertEqual(code, 0, err)
        self.assertIn("removed from the film", out)

    def test_dry_run_writes_nothing(self):
        before = self.path.read_bytes()
        code, out, err = self.scenes("pin", "s002", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.snapshots(), 0)
        self.assertIn("dry run", out)

    def test_an_unknown_scene_is_refused(self):
        before = self.path.read_bytes()
        code, out, err = self.scenes("pin", "s099")
        self.assertEqual(code, 1)
        self.assertIn("no scene s099", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_no_scene_named_is_exit_code_2(self):
        code, out, err = self.scenes("pin", "")
        self.assertEqual(code, 2)


class CropFunctionTests(unittest.TestCase):
    def test_a_position_is_written_as_x(self):
        out = sceneedit.crop(example(), "s001", x=0.3)
        self.assertEqual(next(s for s in out["segments"] if s["id"] == "s001")["crop"], {"x": 0.3})

    def test_a_pan_is_written_as_x_from_and_x_to(self):
        out = sceneedit.crop(example(), "s001", x_from=0.2, x_to=0.6)
        self.assertEqual(next(s for s in out["segments"] if s["id"] == "s001")["crop"], {"x_from": 0.2, "x_to": 0.6})

    def test_clear_takes_the_crop_away(self):
        d = sceneedit.crop(example(), "s001", x=0.3)
        out = sceneedit.crop(d, "s001", clear=True)
        self.assertNotIn("crop", next(s for s in out["segments"] if s["id"] == "s001"))

    def test_the_data_it_was_given_is_not_changed(self):
        d = example()
        sceneedit.crop(d, "s001", x=0.3)
        self.assertNotIn("crop", d["segments"][0])

    def test_values_outside_0_to_1_are_refused(self):
        for kw in ({"x": 1.2}, {"x": -0.1}, {"x_from": 0.2, "x_to": 1.5}):
            with self.assertRaises(sceneedit.SceneEditError, msg=kw):
                sceneedit.crop(example(), "s001", **kw)

    def test_x_cannot_be_mixed_with_a_pan_and_a_pan_needs_both_ends(self):
        for kw in ({"x": 0.5, "x_from": 0.1, "x_to": 0.2}, {"x_from": 0.1}, {"x_to": 0.1}, {}):
            with self.assertRaises(sceneedit.SceneEditError, msg=kw):
                sceneedit.crop(example(), "s001", **kw)

    def test_clear_cannot_come_with_a_position(self):
        with self.assertRaises(sceneedit.SceneEditError):
            sceneedit.crop(example(), "s001", x=0.4, clear=True)

    def test_an_unknown_scene_is_refused(self):
        with self.assertRaises(sceneedit.SceneEditError):
            sceneedit.crop(example(), "s099", x=0.4)


class CropCommandTests(EditCase):
    def test_x_sets_the_position_and_the_output_says_what_the_window_shows(self):
        frames = self.frames()
        code, out, err = self.scenes("crop", "s002", "--x", "0.3")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.seg("s002")["crop"], {"x": 0.3})
        self.assertEqual(self.frames(), frames)
        # s002 is cut from a 4096 x 1720 clip: the 9:16 window is 968 x 1720 pixels, 23.6 percent of the width
        self.assertIn("968 x 1720", out)
        self.assertIn("4096 x 1720", out)
        self.assertIn("23.6 percent", out)
        self.assertIn("938", out)                         # round_even((4096 - 968) * 0.3) = 938
        self.assertIn("snapshot 0001", out)
        self.assertEqual(self.snapshots(), 1)

    def test_the_window_of_a_clip_with_the_full_height_is_the_other_size(self):
        code, out, err = self.scenes("crop", "s003", "--x", "0")
        self.assertEqual(code, 0, err)
        self.assertIn("1216 x 2160", out)
        self.assertIn("29.7 percent", out)

    def test_from_and_to_make_a_pan(self):
        code, out, err = self.scenes("crop", "s001", "--from", "0.2", "--to", "0.6")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.seg("s001")["crop"], {"x_from": 0.2, "x_to": 0.6})
        self.assertIn("pan", out)

    def test_clear_returns_to_the_centre(self):
        self.scenes("crop", "s001", "--x", "0.3")
        code, out, err = self.scenes("crop", "s001", "--clear")
        self.assertEqual(code, 0, err)
        self.assertNotIn("crop", self.seg("s001"))
        self.assertIn("centre", out)

    def test_the_same_position_again_is_no_change_and_no_snapshot(self):
        self.scenes("crop", "s001", "--x", "0.3")
        code, out, err = self.scenes("crop", "s001", "--x", "0.3")
        self.assertEqual(code, 0, err)
        self.assertIn("already", out)
        self.assertEqual(self.snapshots(), 1)

    def test_undo_returns_the_file_byte_for_byte(self):
        before = self.path.read_bytes()
        self.scenes("crop", "s001", "--x", "0.3")
        run(["timeline", "undo", "--file", str(self.path)])
        self.assertEqual(self.path.read_bytes(), before)

    def test_dry_run_writes_nothing(self):
        before = self.path.read_bytes()
        code, out, err = self.scenes("crop", "s001", "--x", "0.3", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("dry run", out)
        self.assertIn("968 x 1720", out)

    def test_the_film_stays_valid_without_a_warning_about_the_crop(self):
        self.scenes("crop", "s001", "--x", "0.3")
        code, out, err = run(["timeline", "validate", "--file", str(self.path), "--no-file-checks"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("V13", out)

    def test_a_value_outside_0_to_1_is_refused(self):
        before = self.path.read_bytes()
        code, out, err = self.scenes("crop", "s001", "--x", "1.4")
        self.assertEqual(code, 1)
        self.assertIn("between 0 and 1", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_without_an_option_it_says_what_to_give(self):
        code, out, err = self.scenes("crop", "s001")
        self.assertEqual(code, 2)
        self.assertIn("--x", err)

    def test_an_unknown_scene_is_refused(self):
        code, out, err = self.scenes("crop", "s099", "--x", "0.3")
        self.assertEqual(code, 1)
        self.assertIn("no scene s099", err)


if __name__ == "__main__":
    unittest.main()
