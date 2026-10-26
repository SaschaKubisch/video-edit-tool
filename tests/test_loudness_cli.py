"""`loudness set` and `loudness show`: the owner's decision D9 written into the timeline file
(SPECS 8.4: the code has no built-in mode, the file carries it)."""

import json
import unittest

import store
import timeline
from tests.test_timeline_commands import FileCase, run


class LoudnessCommandTests(FileCase):
    def loudness(self):
        return json.loads(self.path.read_text())["audio"]["loudness"]

    def set(self, *extra):
        return run(["loudness", "set", "--file", str(self.path), "--no-file-checks", *extra])

    def test_the_decision_of_the_owner_is_the_default_of_the_mode(self):
        self.save("x")
        code, out, err = self.set("--mode", "segment_then_film")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.loudness(), {"mode": "segment_then_film", "target_lufs": -14.0,
                                           "true_peak_dbtp": -1.0, "max_gain_db": 12.0})

    def test_values_can_be_given(self):
        self.save("x")
        self.set("--mode", "film_only", "--target", "-16", "--peak", "-1.5", "--max-gain", "8")
        self.assertEqual(self.loudness(), {"mode": "film_only", "target_lufs": -16.0,
                                           "true_peak_dbtp": -1.5, "max_gain_db": 8.0})

    def test_off_keeps_only_the_mode(self):
        self.save("x")
        self.set("--mode", "segment_then_film")
        self.set("--mode", "off")
        self.assertEqual(self.loudness(), {"mode": "off"})

    def test_the_old_file_is_kept_as_a_snapshot_and_the_new_one_is_valid(self):
        self.save("x")
        self.set("--mode", "segment_then_film")
        self.assertGreaterEqual(len(store.list_snapshots(str(self.path))), 1)
        tl = timeline.Timeline.load(str(self.path))
        self.assertFalse(timeline.has_errors(tl.validate(file_checks=False)))

    def test_a_value_the_rules_refuse_changes_nothing(self):
        self.save("x")
        before = self.path.read_bytes()
        code, out, err = self.set("--mode", "segment_then_film", "--target", "-3")
        self.assertEqual(code, 1)
        self.assertIn("V17", out + err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_show_prints_the_settings(self):
        self.save("x")
        self.set("--mode", "segment_then_film")
        code, out, err = run(["loudness", "show", "--file", str(self.path)])
        self.assertEqual(code, 0)
        self.assertIn("segment_then_film", out)
        self.assertIn("-14", out)

    def test_a_missing_file_is_exit_code_2(self):
        code, out, err = self.set("--mode", "off")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
