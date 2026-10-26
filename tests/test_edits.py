"""The operations the page may send (SPECS 4.4) and the summary of the timeline
(SPECS 4.3): pure functions on the timeline data."""

import copy
import unittest

import edits
import timeline
from tests.timeline_fixture import example


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.d = example()

    def apply(self, *ops):
        return edits.apply_operations(self.d, list(ops))

    def seg(self, data, sid):
        return next(s for s in data["segments"] if s["id"] == sid)

    def test_trim_in_moves_the_start_later_for_a_positive_number(self):
        out = self.apply({"op": "trim_in", "segment": "s001", "frames": 30})
        self.assertEqual(self.seg(out, "s001")["in"], 38 + 30)
        self.assertEqual(self.seg(out, "s001")["out"], 900)

    def test_trim_in_moves_the_start_earlier_for_a_negative_number(self):
        out = self.apply({"op": "trim_in", "segment": "s001", "frames": -8})
        self.assertEqual(self.seg(out, "s001")["in"], 30)

    def test_trim_out_moves_the_end(self):
        out = self.apply({"op": "trim_out", "segment": "s002", "frames": -30})
        self.assertEqual(self.seg(out, "s002")["out"], 2350)
        out = self.apply({"op": "trim_out", "segment": "s002", "frames": 5})
        self.assertEqual(self.seg(out, "s002")["out"], 2385)

    def test_set_enabled_removes_and_restores_a_segment(self):
        out = self.apply({"op": "set_enabled", "segment": "s002", "enabled": False})
        self.assertIs(self.seg(out, "s002")["enabled"], False)
        out2 = edits.apply_operations(out, [{"op": "set_enabled", "segment": "s002", "enabled": True}])
        self.assertIs(self.seg(out2, "s002")["enabled"], True)
        self.assertEqual(timeline.Timeline(out).film_length_frames(), 862 + 620)

    def test_move_puts_the_segment_at_the_index(self):
        out = self.apply({"op": "move", "segment": "s003", "to_index": 0})
        self.assertEqual([s["id"] for s in out["segments"]], ["s003", "s001", "s002"])
        out = self.apply({"op": "move", "segment": "s001", "to_index": 2})
        self.assertEqual([s["id"] for s in out["segments"]], ["s002", "s003", "s001"])

    def test_set_label(self):
        out = self.apply({"op": "set_label", "segment": "s001", "label": "stove at night"})
        self.assertEqual(self.seg(out, "s001")["label"], "stove at night")

    def test_operations_are_applied_in_order_and_the_input_is_not_changed(self):
        before = copy.deepcopy(self.d)
        out = self.apply({"op": "trim_in", "segment": "s001", "frames": 10},
                         {"op": "trim_in", "segment": "s001", "frames": 5})
        self.assertEqual(self.seg(out, "s001")["in"], 38 + 15)
        self.assertEqual(self.d, before)

    def test_segment_ids_and_other_fields_never_change(self):
        out = self.apply({"op": "move", "segment": "s003", "to_index": 0},
                         {"op": "trim_in", "segment": "s003", "frames": 1})
        self.assertEqual(sorted(s["id"] for s in out["segments"]), ["s001", "s002", "s003"])
        self.assertEqual(out["sources"], self.d["sources"])
        self.assertEqual(out["film"], self.d["film"])


class OperationErrorTests(unittest.TestCase):
    def setUp(self):
        self.d = example()

    def fails(self, op, text):
        with self.assertRaises(edits.OperationError) as cm:
            edits.apply_operations(self.d, [op] if isinstance(op, dict) else op)
        self.assertIn(text, str(cm.exception))

    def test_an_unknown_operation(self):
        self.fails({"op": "explode", "segment": "s001"}, "unknown operation")

    def test_an_operation_without_a_name(self):
        self.fails({"segment": "s001"}, "op")

    def test_an_unknown_segment(self):
        self.fails({"op": "trim_in", "segment": "s999", "frames": 1}, "s999")

    def test_frames_must_be_a_whole_number(self):
        self.fails({"op": "trim_in", "segment": "s001", "frames": 1.5}, "frames")
        self.fails({"op": "trim_in", "segment": "s001", "frames": "3"}, "frames")
        self.fails({"op": "trim_in", "segment": "s001", "frames": True}, "frames")

    def test_enabled_must_be_true_or_false(self):
        self.fails({"op": "set_enabled", "segment": "s001", "enabled": "yes"}, "enabled")

    def test_the_target_index_must_lie_inside_the_list(self):
        self.fails({"op": "move", "segment": "s001", "to_index": 3}, "to_index")
        self.fails({"op": "move", "segment": "s001", "to_index": -1}, "to_index")

    def test_the_label_must_be_text(self):
        self.fails({"op": "set_label", "segment": "s001", "label": 5}, "label")

    def test_the_operations_must_be_a_list_of_objects(self):
        with self.assertRaises(edits.OperationError):
            edits.apply_operations(self.d, "trim")
        with self.assertRaises(edits.OperationError):
            edits.apply_operations(self.d, [5])
        with self.assertRaises(edits.OperationError):
            edits.apply_operations(self.d, [])

    def test_a_failing_operation_changes_nothing(self):
        before = copy.deepcopy(self.d)
        with self.assertRaises(edits.OperationError):
            edits.apply_operations(self.d, [{"op": "trim_in", "segment": "s001", "frames": 5},
                                            {"op": "trim_in", "segment": "nope", "frames": 5}])
        self.assertEqual(self.d, before)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.tl = timeline.Timeline(example())
        self.s = edits.summary(self.tl, file_hash="9f2c1a47" + "0" * 56)

    def test_totals(self):
        self.assertEqual(self.s["segments"], 3)
        self.assertEqual(self.s["enabled"], 3)
        self.assertEqual(self.s["film_frames"], 2542)
        self.assertAlmostEqual(self.s["film_seconds"], 2542 * 1001 / 30000, places=3)
        self.assertEqual(self.s["fps"], {"num": 30000, "den": 1001})
        self.assertEqual(self.s["hash"][:8], "9f2c1a47")

    def test_rows_carry_film_times_and_source_frames(self):
        rows = self.s["rows"]
        self.assertEqual([r["id"] for r in rows], ["s001", "s002", "s003"])
        r = rows[1]
        self.assertEqual((r["in"], r["out"], r["length_frames"]), (1320, 2380, 1060))
        self.assertEqual(r["film_start_frame"], 862)
        self.assertAlmostEqual(r["film_start_s"], 862 * 1001 / 30000, places=3)
        self.assertAlmostEqual(r["length_s"], 1060 * 1001 / 30000, places=3)
        self.assertEqual(r["source"], "A002_01011540_C002")
        self.assertEqual(r["source_frames"], 2915)
        self.assertEqual(r["label"], "stove, wide, after the camera was moved")
        self.assertTrue(r["enabled"])

    def test_a_disabled_segment_has_no_film_time(self):
        d = example()
        d["segments"][1]["enabled"] = False
        s = edits.summary(timeline.Timeline(d))
        r = s["rows"][1]
        self.assertFalse(r["enabled"])
        self.assertIsNone(r["film_start_frame"])
        self.assertEqual(r["length_frames"], 1060)
        self.assertEqual(s["enabled"], 2)
        self.assertEqual(s["rows"][2]["film_start_frame"], 862)

    def test_the_text_form_is_the_table_of_section_4_3(self):
        text = edits.format_summary(self.s, name="timeline.json")
        lines = text.splitlines()
        self.assertIn("timeline.json", lines[0])
        self.assertIn("3 segments, 3 enabled", lines[0])
        self.assertIn("9f2c1a47", lines[0])
        self.assertIn("film time", lines[1])
        self.assertIn("s002", lines[3])
        self.assertIn("00:00:28.76-00:01:04.13", lines[3])
        self.assertIn("A002_01011540_C002", lines[3])
        self.assertIn("1320-2380", lines[3])
        self.assertIn("stove, wide, after the camera was moved", lines[3])


if __name__ == "__main__":
    unittest.main()
