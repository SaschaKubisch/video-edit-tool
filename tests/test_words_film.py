"""Check 4.12: cues and titles on the film (SPECS 9.4, 9.8): the text box of a scene, adding, editing,
moving, shifting and removing cues at film time, trims that keep the cues with their picture,
titles, importing, and the SRT file. The example of SPECS 3.3 is the film."""

import unittest

import config
import edits
import textlayout
import timeline
import words
from tests.timeline_fixture import example

S1_LEN, S2_LEN, S3_LEN = 862 * 1001 / 30000, 1060 * 1001 / 30000, 620 * 1001 / 30000
S2_START = S1_LEN


def errors(data):
    return [f for f in timeline.validate(data, file_checks=False) if f.level == "error"]


class FilmCase(unittest.TestCase):
    def setUp(self):
        self._factory = words.RULES_FACTORY
        words.RULES_FACTORY = lambda data: textlayout.char_rules(42)
        self.addCleanup(setattr, words, "RULES_FACTORY", self._factory)
        self.d = example()

    def ops(self, *ops, data=None):
        notes = []
        out = edits.apply_operations(data if data is not None else self.d, list(ops), notes)
        self.notes = notes
        return out

    def cues(self, data, anchor=None, origin=None):
        return [c for c in data["subtitles"]["cues"]
                if (anchor is None or c["anchor"] == anchor) and (origin is None or c["origin"] == origin)] \
            if "subtitles" in data else []


SCENE_TEXT = ("Um, so this is the pan. It is hot enough now. We add the sausage, uh, and we wait "
              "for it to cook. Nobody talks for a while")


class SceneTextTests(FilmCase):
    def test_the_text_becomes_cues_across_the_whole_scene(self):
        out = self.ops({"op": "set_scene_text", "segment": "s003", "text": SCENE_TEXT})
        cues = self.cues(out, "s003")
        self.assertGreaterEqual(len(cues), 3)
        self.assertEqual({c["origin"] for c in cues}, {"text"})
        self.assertEqual(cues[0]["start_s"], 0.0)
        self.assertGreater(cues[-1]["end_s"], S3_LEN * 0.75)
        self.assertLessEqual(cues[-1]["end_s"], S3_LEN + 1e-6)
        self.assertEqual(errors(out), [])
        self.assertEqual(out["segments"][2]["text"],
                         "So this is the pan. It is hot enough now. We add the sausage, and we wait "
                         "for it to cook. Nobody talks for a while.")

    def test_other_scenes_keep_their_cues(self):
        out = self.ops({"op": "set_scene_text", "segment": "s003", "text": SCENE_TEXT})
        self.assertEqual(self.cues(out, "s001"), self.d["subtitles"]["cues"])

    def test_saving_the_same_text_again_changes_nothing(self):
        once = self.ops({"op": "set_scene_text", "segment": "s003", "text": SCENE_TEXT})
        twice = self.ops({"op": "set_scene_text", "segment": "s003", "text": SCENE_TEXT}, data=once)
        self.assertEqual(once, twice)
        self.assertTrue(any("did not change" in n for n in self.notes))

    def test_new_text_replaces_only_that_scenes_generated_cues(self):
        once = self.ops({"op": "set_scene_text", "segment": "s003", "text": SCENE_TEXT})
        twice = self.ops({"op": "set_scene_text", "segment": "s003", "text": "Just one line."}, data=once)
        self.assertEqual(len(self.cues(twice, "s003")), 1)
        self.assertEqual(self.cues(twice, "s001"), once["subtitles"]["cues"][:1])

    def test_cues_moved_by_hand_are_not_replaced_without_asking(self):
        once = self.ops({"op": "set_scene_text", "segment": "s003", "text": SCENE_TEXT})
        first = self.cues(once, "s003")[0]
        film_start = once["segments"][0]["out"]          # unused; the film time is computed below
        rows = {r["seg"]["id"]: r for r in words.film_segments(once)}
        moved = self.ops({"op": "edit_cue", "cue": first["id"],
                          "film_start_s": rows["s003"]["start_s"] + 1.0,
                          "film_end_s": rows["s003"]["start_s"] + 3.5}, data=once)
        self.assertTrue(self.cues(moved, "s003")[0].get("locked"))
        with self.assertRaises(words.NeedsConfirmation) as ctx:
            self.ops({"op": "set_scene_text", "segment": "s003", "text": "Something else."}, data=moved)
        self.assertEqual(ctx.exception.cues, [first["id"]])
        replaced = self.ops({"op": "set_scene_text", "segment": "s003", "text": "Something else.",
                             "replace_adjusted": True}, data=moved)
        self.assertEqual([c["text"] for c in self.cues(replaced, "s003")], ["Something else."])
        self.assertFalse(self.cues(replaced, "s003")[0].get("locked"))

    def test_a_cue_made_by_hand_in_the_scene_also_asks(self):
        added = self.ops({"op": "add_cue", "film_start_s": S2_START + S2_LEN + 2.0, "text": "By hand."})
        with self.assertRaises(words.NeedsConfirmation):
            self.ops({"op": "set_scene_text", "segment": "s003", "text": "New text."}, data=added)

    def test_text_that_needs_more_time_than_the_scene_has_is_reported(self):
        short = self.ops({"op": "trim_out", "segment": "s003", "frames": -530})     # 90 frames = 3 s
        long_text = " ".join(["The pan is hot enough now and we add the sausage and wait."] * 6)
        out = self.ops({"op": "set_scene_text", "segment": "s003", "text": long_text}, data=short)
        self.assertTrue(any("shorter than a person can read" in n for n in self.notes), self.notes)
        cues = self.cues(out, "s003")
        self.assertGreaterEqual(len(cues), 6)
        self.assertLessEqual(cues[-1]["end_s"], 90 * 1001 / 30000 + 1e-6)
        self.assertEqual(errors(out), [])

    def test_empty_text_removes_the_text_and_its_cues(self):
        once = self.ops({"op": "set_scene_text", "segment": "s003", "text": SCENE_TEXT})
        out = self.ops({"op": "set_scene_text", "segment": "s003", "text": " um "}, data=once)
        self.assertNotIn("text", out["segments"][2])
        self.assertEqual(self.cues(out, "s003"), [])

    def test_a_scene_that_is_switched_off_keeps_its_text_cues(self):
        once = self.ops({"op": "set_scene_text", "segment": "s003", "text": SCENE_TEXT})
        off = self.ops({"op": "set_enabled", "segment": "s003", "enabled": False}, data=once)
        self.assertEqual(self.cues(off, "s003"), self.cues(once, "s003"))
        self.assertEqual([s["id"] for s in words.cue_spans(off)], ["c001"])
        on = self.ops({"op": "set_enabled", "segment": "s003", "enabled": True}, data=off)
        self.assertEqual(len(words.cue_spans(on)), 1 + len(self.cues(once, "s003")))

    def test_unknown_scene(self):
        with self.assertRaises(words.WordsError):
            self.ops({"op": "set_scene_text", "segment": "s099", "text": "x"})


class TrimTests(FilmCase):
    def test_unmoved_text_cues_are_spread_again_over_the_new_length(self):
        once = self.ops({"op": "set_scene_text", "segment": "s003", "text": SCENE_TEXT})
        trimmed = self.ops({"op": "trim_in", "segment": "s003", "frames": 300}, data=once)
        new_len = (620 - 300) * 1001 / 30000
        cues = self.cues(trimmed, "s003")
        self.assertEqual(len(cues), len(self.cues(once, "s003")))
        self.assertEqual(cues[0]["start_s"], 0.0)
        self.assertLessEqual(cues[-1]["end_s"], new_len + 1e-6)
        self.assertGreater(cues[-1]["end_s"], new_len * 0.6)
        self.assertEqual(errors(trimmed), [])

    def test_cues_moved_by_hand_keep_their_picture_when_the_start_is_trimmed(self):
        added = self.ops({"op": "add_cue", "film_start_s": 10.0, "film_end_s": 14.0, "text": "Hold it."})
        before = self.cues(added, "s001", "manual")[0]
        trimmed = self.ops({"op": "trim_in", "segment": "s001", "frames": 60}, data=added)
        after = self.cues(trimmed, "s001", "manual")[0]
        self.assertAlmostEqual(after["start_s"], before["start_s"] - 60 * 1001 / 30000, delta=0.001)
        self.assertAlmostEqual(after["end_s"], before["end_s"] - 60 * 1001 / 30000, delta=0.001)

    def test_a_cue_with_less_than_half_a_second_left_is_dropped(self):
        out = self.ops({"op": "trim_in", "segment": "s001", "frames": 170})   # start 5.67 s: 0.33 s left
        self.assertEqual(self.cues(out, "s001"), [])
        self.assertTrue(any("removed" in n for n in self.notes))

    def test_a_cue_with_enough_left_is_kept_and_clipped(self):
        out = self.ops({"op": "trim_in", "segment": "s001", "frames": 120})   # start 4.0 s: 2.0 s left
        c = self.cues(out, "s001")[0]
        self.assertEqual(c["start_s"], 0.0)
        self.assertAlmostEqual(c["end_s"], 6.0 - 120 * 1001 / 30000, delta=0.04)      # a frame

    def test_trimming_the_end_clips_cues(self):
        out = self.ops({"op": "trim_out", "segment": "s001", "frames": -(862 - 150)})   # the scene ends at 5.0 s
        c = self.cues(out, "s001")[0]
        self.assertAlmostEqual(c["end_s"], 150 * 1001 / 30000, delta=0.001)

    def test_trimming_the_end_far_back_drops_the_cue(self):
        out = self.ops({"op": "trim_out", "segment": "s001", "frames": -(862 - 100)})   # the scene ends at 3.3 s
        self.assertEqual(self.cues(out, "s001"), [])

    def test_moving_a_scene_moves_its_cues_with_it(self):
        out = self.ops({"op": "move", "segment": "s001", "to_index": 2})
        spans = words.cue_spans(out)
        self.assertAlmostEqual(spans[0]["start_s"], S2_LEN + S3_LEN + 3.2, delta=0.001)
        self.assertEqual(errors(out), [])


class CueOperationTests(FilmCase):
    def test_add_a_cue_at_film_time(self):
        out = self.ops({"op": "add_cue", "film_start_s": 30.0, "film_end_s": 33.0, "text": "Hello there."})
        new = [c for c in out["subtitles"]["cues"] if c["id"] != "c001"][0]
        self.assertEqual(new["anchor"], "s002")
        self.assertAlmostEqual(new["start_s"], 30.0 - S2_START, delta=0.04)
        self.assertAlmostEqual(new["end_s"] - new["start_s"], 3.0, delta=0.04)
        self.assertEqual((new["origin"], new["locked"], new["id"]), ("manual", True, "c002"))
        self.assertEqual(errors(out), [])

    def test_the_end_defaults_to_the_time_the_text_needs(self):
        out = self.ops({"op": "add_cue", "film_start_s": 30.0, "text": "x" * 34})      # 34 / 17 = 2 s
        new = [c for c in out["subtitles"]["cues"] if c["id"] == "c002"][0]
        self.assertAlmostEqual(new["end_s"] - new["start_s"], 2.0, delta=0.04)

    def test_a_cue_that_runs_past_its_scene_is_shortened_and_the_note_says_so(self):
        out = self.ops({"op": "add_cue", "film_start_s": S2_START - 1.0, "film_end_s": S2_START + 2.0,
                        "text": "Hello there."})
        new = [c for c in out["subtitles"]["cues"] if c["id"] == "c002"][0]
        self.assertEqual(new["anchor"], "s001")
        self.assertAlmostEqual(new["end_s"], S1_LEN, delta=0.04)
        self.assertTrue(any("shortened" in n for n in self.notes))

    def test_a_time_outside_the_film_is_refused(self):
        with self.assertRaises(words.WordsError):
            self.ops({"op": "add_cue", "film_start_s": 500.0, "text": "x"})
        with self.assertRaises(words.WordsError):
            self.ops({"op": "add_cue", "film_start_s": 5.0, "text": "  "})

    def test_a_long_text_becomes_several_cues_spread_over_the_time(self):
        text = ("The kitchen was quiet that morning and the light came in low over the table, "
                "so we made coffee first. Then we cut the bread.")
        out = self.ops({"op": "add_cue", "film_start_s": 40.0, "film_end_s": 52.0, "text": text})
        new = [c for c in out["subtitles"]["cues"] if c["id"] != "c001"]
        self.assertGreaterEqual(len(new), 2)
        self.assertEqual(errors(out), [])
        self.assertTrue(any("became" in n for n in self.notes))
        for c in new:
            self.assertLessEqual(len(c["text"].split("\n")), 2)

    def test_edit_the_text_and_the_time(self):
        out = self.ops({"op": "edit_cue", "cue": "c001", "text": "A new text.", "film_start_s": 2.0,
                        "film_end_s": 5.0})
        c = out["subtitles"]["cues"][0]
        self.assertEqual((c["text"], c["anchor"], c["locked"]), ("A new text.", "s001", True))
        self.assertAlmostEqual(c["start_s"], 2.0, delta=0.04)
        self.assertEqual(c["origin"], "auto" if self.d["subtitles"]["cues"][0].get("origin") == "auto"
                         else c["origin"])

    def test_moving_a_cue_into_another_scene_changes_its_anchor(self):
        out = self.ops({"op": "edit_cue", "cue": "c001", "film_start_s": S2_START + 5.0,
                        "film_end_s": S2_START + 8.0})
        c = out["subtitles"]["cues"][0]
        self.assertEqual(c["anchor"], "s002")
        self.assertAlmostEqual(c["start_s"], 5.0, delta=0.04)

    def test_remove_cues(self):
        out = self.ops({"op": "remove_cue", "cue": "c001"})
        self.assertEqual(out["subtitles"]["cues"], [])
        with self.assertRaises(words.WordsError):
            self.ops({"op": "remove_cue", "cue": "c099"})

    def test_shift_all_cues(self):
        out = self.ops({"op": "shift_cues", "seconds": 1.5})
        c = out["subtitles"]["cues"][0]
        self.assertAlmostEqual(c["start_s"], 4.7, delta=0.04)
        self.assertAlmostEqual(c["end_s"], 7.5, delta=0.04)

    def test_shifting_a_cue_out_of_the_film_changes_nothing(self):
        before = edits.copy.deepcopy(self.d)
        with self.assertRaises(words.WordsError):
            self.ops({"op": "shift_cues", "seconds": -10.0})
        self.assertEqual(self.d, before)

    def test_shift_only_some_cues(self):
        added = self.ops({"op": "add_cue", "film_start_s": 30.0, "film_end_s": 32.0, "text": "Two."})
        out = self.ops({"op": "shift_cues", "seconds": 1.0, "cues": ["c002"]}, data=added)
        self.assertEqual(out["subtitles"]["cues"][0], added["subtitles"]["cues"][0])
        self.assertNotEqual(out["subtitles"]["cues"][1], added["subtitles"]["cues"][1])

    def test_import_cues_in_film_seconds_and_in_scene_seconds(self):
        out = self.ops({"op": "import_cues", "replace": True, "cues": [
            {"start_s": 31.0, "end_s": 34.0, "text": "From a file."},
            {"anchor": "s003", "start_s": 2.0, "end_s": 5.0, "text": "In the third scene."}]})
        cues = out["subtitles"]["cues"]
        self.assertEqual([c["anchor"] for c in cues], ["s002", "s003"])
        self.assertEqual(errors(out), [])
        with self.assertRaises(words.WordsError):
            self.ops({"op": "import_cues", "cues": [{"end_s": 3, "text": "x"}]})

    def test_the_cues_stay_in_film_order(self):
        out = self.ops({"op": "add_cue", "film_start_s": 1.0, "film_end_s": 2.5, "text": "First."},
                       {"op": "add_cue", "film_start_s": 60.0, "film_end_s": 62.0, "text": "Last."})
        order = [c["id"] for c in out["subtitles"]["cues"]]
        self.assertEqual(order, ["c002", "c001", "c003"])

    def test_the_form_of_an_export(self):
        out = self.ops({"op": "set_export_subtitles", "export": "film-4k", "form": "burned"})
        self.assertEqual(out["exports"][0]["subtitles"], "burned")
        with self.assertRaises(words.WordsError):
            self.ops({"op": "set_export_subtitles", "export": "film-4k", "form": "hard"})
        with self.assertRaises(words.WordsError):
            self.ops({"op": "set_export_subtitles", "export": "nope", "form": "srt"})


class TitleTests(FilmCase):
    def test_add_edit_remove(self):
        out = self.ops({"op": "add_title", "text": "Sunday kitchen", "start_s": 10, "length_s": 3,
                        "template": "lower_third", "size_pct": 5, "fade_s": 0.4})
        t = out["titles"][-1]
        self.assertEqual((t["id"], t["anchor"], t["start_s"], t["end_s"]), ("t002", "film", 10, 13))
        self.assertEqual((t["template"], t["size_pct"], t["fade_s"]), ("lower_third", 5, 0.4))
        self.assertEqual(errors(out), [])
        out = self.ops({"op": "edit_title", "title": "t002", "text": "Monday", "end_s": 20}, data=out)
        self.assertEqual((out["titles"][-1]["text"], out["titles"][-1]["end_s"]), ("Monday", 20))
        out = self.ops({"op": "remove_title", "title": "t002"}, data=out)
        self.assertEqual([t["id"] for t in out["titles"]], ["t001"])

    def test_a_title_on_a_scene(self):
        out = self.ops({"op": "add_title", "text": "Pan", "anchor": "s003", "start_s": 1, "end_s": 4})
        self.assertEqual(out["titles"][-1]["anchor"], "s003")
        with self.assertRaises(words.WordsError):
            self.ops({"op": "add_title", "text": "Pan", "anchor": "s099", "start_s": 1, "end_s": 4})

    def test_bad_titles_are_refused(self):
        with self.assertRaises(words.WordsError):
            self.ops({"op": "add_title", "text": " ", "start_s": 1, "end_s": 4})
        with self.assertRaises(words.WordsError):
            self.ops({"op": "add_title", "text": "x", "start_s": 4, "end_s": 1})
        with self.assertRaises(words.WordsError):
            self.ops({"op": "remove_title", "title": "t099"})

    def test_import_titles(self):
        out = self.ops({"op": "import_titles", "replace": True, "titles": [
            {"text": "One", "start_s": 0, "end_s": 3, "template": "opening"},
            {"text": "Two", "start_s": 20, "length_s": 2.5}]})
        self.assertEqual([(t["id"], t["text"]) for t in out["titles"]], [("t001", "One"), ("t002", "Two")])
        self.assertEqual(out["titles"][1]["end_s"], 22.5)
        self.assertEqual(errors(out), [])


class SrtTests(FilmCase):
    def test_time_format(self):
        self.assertEqual(words.srt_time(0), "00:00:00,000")
        self.assertEqual(words.srt_time(3661.5), "01:01:01,500")
        self.assertEqual(words.srt_time(59.9996), "00:01:00,000")

    def test_text_and_parse_round_trip(self):
        out = self.ops({"op": "add_cue", "film_start_s": 40.0, "film_end_s": 43.0, "text": "Second\nline."})
        srt = words.srt_text(words.cue_spans(out))
        self.assertTrue(srt.startswith("1\n00:00:03,200 --> 00:00:06,000\nNow the pan is hot enough.\n\n2\n"))
        parsed = words.parse_srt(srt)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[1]["text"], "Second\nline.")
        self.assertAlmostEqual(parsed[1]["start_s"], 40.0, delta=0.05)

    def test_a_cue_on_a_removed_scene_is_not_in_the_file(self):
        out = self.ops({"op": "set_enabled", "segment": "s001", "enabled": False})
        self.assertEqual(words.srt_text(words.cue_spans(out)), "")

    def test_long_lines_are_wrapped_again(self):
        spans = [{"start_s": 1.0, "end_s": 4.0, "text": "The pan is hot enough now so we can add the sausage"}]
        text = words.srt_text(spans, textlayout.char_rules(42))
        lines = text.split("\n")[2:-1]
        self.assertEqual(len([l for l in lines if l]), 2)
        self.assertTrue(all(len(l) <= 42 for l in lines))

    def test_bad_srt(self):
        with self.assertRaises(words.WordsError):
            words.parse_srt("1\nnot a time\ntext\n")


if __name__ == "__main__":
    unittest.main()
