"""Check 5.4: the commands `scenes remove`, `scenes keep`, `scenes restore` and `scenes cut` (SPECS 10.4),
run through the command-line parser as Claude Code runs them. Each goes through the validator and makes one
snapshot, so `timeline undo` returns to the file before; each has --dry-run, which writes nothing.

The film is the example of SPECS 3.3: s001 (clip A002_01011540_C002, frames 38-900, 862 frames, film 0-28.76 s),
s002 (same clip, frames 1320-2380, 1060 frames, film 28.76-64.13 s) and s003 (clip A002_01060615_C083,
frames 30-650, 620 frames, film 64.13-84.82 s). Cue c001 is on s001 from 3.2 to 6.0 s. Title t001 is on the film."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import config
import main
import render
import store
import textlayout
import timeline
import words
from tests.timeline_fixture import example

F = 30000 / 1001
CLIP_A = "A002_01011540_C002"


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args = main.build_parser().parse_args(argv)
            args.func(args)
        except SystemExit as e:
            code = e.code or 0
    return code, out.getvalue(), err.getvalue()


class EditCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "timeline.json"
        self.data = example()
        self.data["titles"] = [{"id": "t001", "text": "Sunday kitchen", "anchor": "film", "start_s": 1.0,
                                "end_s": 5.0, "template": "center", "fade_s": 0.5}]
        self.save()
        self._factory = words.RULES_FACTORY
        words.RULES_FACTORY = lambda data: textlayout.char_rules(42)
        self.addCleanup(setattr, words, "RULES_FACTORY", self._factory)

    def save(self):
        timeline.Timeline(self.data).save(str(self.path))
        for p in store.list_snapshots(str(self.path)):
            Path(p).unlink()

    def scenes(self, *argv):
        return run(["scenes", argv[0], *argv[1:], "--file", str(self.path)])

    def subtitles(self, *argv):
        return run(["subtitles", argv[0], *argv[1:], "--file", str(self.path)])

    def now(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def seg(self, sid, data=None):
        return next(s for s in (data or self.now())["segments"] if s["id"] == sid)

    def enabled(self):
        return [s["id"] for s in self.now()["segments"] if s.get("enabled", True)]

    def snapshots(self):
        return len(store.list_snapshots(str(self.path)))

    def tl(self):
        return timeline.Timeline.load(str(self.path))

    def frames(self):
        return self.tl().film_length_frames()


class RemoveTests(EditCase):
    def test_remove_switches_the_scene_off_and_keeps_it_in_the_file(self):
        code, out, err = self.scenes("remove", "s002")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.enabled(), ["s001", "s003"])
        self.assertEqual([s["id"] for s in self.now()["segments"]], ["s001", "s002", "s003"])
        self.assertFalse(self.seg("s002")["enabled"])

    def test_it_makes_one_snapshot_and_undo_returns_the_file_byte_for_byte(self):
        before = self.path.read_bytes()
        self.scenes("remove", "s002")
        self.assertEqual(self.snapshots(), 1)
        code, out, err = run(["timeline", "undo", "--file", str(self.path)])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_several_scenes_as_a_comma_list_or_as_several_words(self):
        self.scenes("remove", "s001,s003")
        self.assertEqual(self.enabled(), ["s002"])
        self.save()
        self.scenes("remove", "s001", "s003")
        self.assertEqual(self.enabled(), ["s002"])

    def test_the_output_says_what_leaves_the_film_and_how_long_the_film_gets(self):
        code, out, err = self.scenes("remove", "s002")
        self.assertIn("s002", out)
        self.assertIn("00:00:28.76 - 00:01:04.13", out)               # where the scene was
        self.assertIn(CLIP_A, out)
        self.assertIn("00:01:24.82 -> 00:00:49.45", out)               # the film before and after
        self.assertIn("-35.4 s", out)
        self.assertIn("3 -> 2", out)                                   # scenes in the film
        self.assertIn("scenes restore", out)
        self.assertIn("timeline undo", out)
        self.assertIn("snapshot 0001", out)

    def test_a_dry_run_writes_nothing_and_says_the_same(self):
        before = self.path.read_bytes()
        code, dry, err = self.scenes("remove", "s002", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.snapshots(), 0)
        self.assertIn("dry run", dry)
        self.assertIn("nothing was written", dry)
        code, real, err = self.scenes("remove", "s002")
        for line in ("00:01:24.82 -> 00:00:49.45", "3 -> 2", "s002"):
            self.assertIn(line, dry)
            self.assertIn(line, real)
        self.assertNotIn("dry run", real)

    def test_the_cues_of_a_removed_scene_leave_the_film_and_stay_in_the_file(self):
        code, out, err = self.scenes("remove", "s001")
        self.assertIn("c001", out)
        self.assertIn("hidden with their scene", out)
        self.assertEqual([c["id"] for c in self.now()["subtitles"]["cues"]], ["c001"])

    def test_titles_that_are_placed_by_film_time_are_named(self):
        code, out, err = self.scenes("remove", "s001")
        self.assertIn("t001", out)
        self.assertIn("film time", out)
        code, out, err = self.scenes("remove", "s003", "--dry-run")          # after the title: no mention
        self.assertNotIn("t001", out)

    def test_an_unknown_scene_changes_nothing(self):
        before = self.path.read_bytes()
        code, out, err = self.scenes("remove", "s002,s999")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("s999", err)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.snapshots(), 0)

    def test_removing_every_scene_is_refused_and_names_keep(self):
        before = self.path.read_bytes()
        code, out, err = self.scenes("remove", "s001,s002,s003")
        self.assertEqual(code, 1)
        self.assertIn("at least one", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_a_scene_that_is_already_removed_is_no_change_and_no_snapshot(self):
        self.scenes("remove", "s002")
        code, out, err = self.scenes("remove", "s002")
        self.assertEqual(code, 0, err)
        self.assertIn("already removed", out)
        self.assertIn("no change", out.lower())
        self.assertEqual(self.snapshots(), 1)

    def test_the_result_validates_with_the_rules_of_the_file(self):
        self.scenes("remove", "s002")
        code, out, err = run(["timeline", "validate", "--file", str(self.path), "--no-file-checks"])
        self.assertEqual(code, 0, out)


class KeepTests(EditCase):
    def test_keep_removes_all_the_others(self):
        code, out, err = self.scenes("keep", "s002")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.enabled(), ["s002"])
        self.assertEqual(self.frames(), 1060)
        self.assertIn("s001", out)
        self.assertIn("s003", out)
        self.assertIn("3 -> 1", out)

    def test_keep_several(self):
        self.scenes("keep", "s001,s003")
        self.assertEqual(self.enabled(), ["s001", "s003"])

    def test_keep_in_a_dry_run_changes_nothing(self):
        before = self.path.read_bytes()
        code, out, err = self.scenes("keep", "s002", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("dry run", out)

    def test_a_scene_in_the_list_that_is_removed_stays_removed_and_the_output_says_so(self):
        self.scenes("remove", "s002")
        code, out, err = self.scenes("keep", "s002,s003")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.enabled(), ["s003"])
        self.assertIn("s002 is removed", out)
        self.assertIn("scenes restore s002", out)

    def test_keep_of_the_scenes_that_are_already_all_there_is_no_change(self):
        code, out, err = self.scenes("keep", "s001,s002,s003")
        self.assertEqual(code, 0, err)
        self.assertIn("no change", out.lower())
        self.assertEqual(self.snapshots(), 0)

    def test_keep_names_a_scene_that_does_not_exist(self):
        code, out, err = self.scenes("keep", "s010")
        self.assertEqual(code, 1)
        self.assertIn("s010", err)
        self.assertEqual(self.snapshots(), 0)

    def test_undo_returns_to_the_file_before(self):
        before = self.path.read_bytes()
        self.scenes("keep", "s002")
        run(["timeline", "undo", "--file", str(self.path)])
        self.assertEqual(self.path.read_bytes(), before)


class RestoreTests(EditCase):
    def test_restore_puts_a_removed_scene_back_with_its_cues(self):
        self.scenes("remove", "s001")
        code, out, err = self.scenes("restore", "s001")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.enabled(), ["s001", "s002", "s003"])
        self.assertEqual(self.frames(), 862 + 1060 + 620)
        self.assertIn("c001", out)
        self.assertIn("back in the film", out.lower())

    def test_restore_of_a_scene_that_is_in_the_film_is_no_change(self):
        code, out, err = self.scenes("restore", "s002")
        self.assertEqual(code, 0, err)
        self.assertIn("already in the film", out)
        self.assertEqual(self.snapshots(), 0)

    def test_a_restore_that_breaks_a_rule_is_refused_with_the_finding(self):
        # s001 shrinks to 0.5 s and is removed; as the first scene it would need the 1 s fade-in (V11)
        self.data["segments"][0]["out"] = self.data["segments"][0]["in"] + 15
        self.data["segments"][0]["enabled"] = False
        self.save()
        before = self.path.read_bytes()
        code, out, err = self.scenes("restore", "s001")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)[0]["rule"], "V11")
        self.assertIn("nothing was saved", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_restore_has_a_dry_run(self):
        self.scenes("remove", "s002")
        before = self.path.read_bytes()
        code, out, err = self.scenes("restore", "s002", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.snapshots(), 1)
        self.assertIn("00:00:49.45 -> 00:01:24.82", out)

    def test_restore_and_undo(self):
        self.scenes("remove", "s002")
        removed = self.path.read_bytes()
        self.scenes("restore", "s002")
        run(["timeline", "undo", "--file", str(self.path)])
        self.assertEqual(self.path.read_bytes(), removed)


class CutTests(EditCase):
    """s001 plays from film second 0 to 28.76. Cut 10 to 13 s: 90 frames at 29.97 (a = 38 + 300, b = 38 + 390)."""

    def cut(self, a, b, scene="s001", *extra):
        return self.scenes("cut", scene, "--from", str(a), "--to", str(b), *extra)

    def test_a_cut_in_the_middle_splits_the_scene_in_two(self):
        code, out, err = self.cut(10, 13)
        self.assertEqual(code, 0, err)
        s1, s4 = self.seg("s001"), self.seg("s004")
        a, b = 38 + round(10 * F), 38 + round(13 * F)
        self.assertEqual((s1["in"], s1["out"]), (38, a))
        self.assertEqual((s4["in"], s4["out"], s4["source"]), (b, 900, CLIP_A))
        order = [s["id"] for s in self.now()["segments"]]
        self.assertEqual(order, ["s001", "s004", "s002", "s003"])
        self.assertEqual(s4["origin"], "prompt")
        self.assertEqual(self.seg("s001")["origin"], "assembly")

    def test_the_film_gets_shorter_by_the_cut_moment_to_the_frame(self):
        self.cut(10, 13)
        removed = round(13 * F) - round(10 * F)
        self.assertEqual(self.frames(), 862 + 1060 + 620 - removed)

    def test_the_new_scene_starts_where_the_cut_ends_in_the_film(self):
        self.cut(10, 13)
        tl = self.tl()
        starts = tl.segment_film_starts()
        self.assertEqual(starts["s004"], round(10 * F))                 # the first part ends where the cut began
        self.assertEqual(starts["s002"], starts["s004"] + self.seg("s004")["out"] - self.seg("s004")["in"])

    def test_it_makes_one_snapshot_and_undo_returns_the_file_byte_for_byte(self):
        before = self.path.read_bytes()
        self.cut(10, 13)
        self.assertEqual(self.snapshots(), 1)
        run(["timeline", "undo", "--file", str(self.path)])
        self.assertEqual(self.path.read_bytes(), before)

    def test_a_dry_run_says_what_happens_and_writes_nothing(self):
        before = self.path.read_bytes()
        code, out, err = self.cut(10, 13, "s001", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.snapshots(), 0)
        self.assertIn("dry run", out)
        self.assertIn("s004", out)                                       # the new scene
        self.assertIn("00:01:24.82 -> 00:01:21.82", out)                 # 3 s shorter
        self.assertIn("-3.0 s", out)
        self.assertIn(f"{38 + round(10 * F)}", out)                      # the source frames of the cut

    def test_the_new_join_has_the_default_sound_crossfade(self):
        self.cut(10, 13)
        plan = render.make_plan(self.tl())
        by_id = {p.id: p for p in plan.segments}
        self.assertEqual(by_id["s004"].join_in_samples, round(0.5 * 48000))
        self.assertEqual(by_id["s004"].curve_in, "equal_power")
        self.assertEqual(by_id["s004"].join_out_samples, by_id["s002"].join_in_samples)

    def test_the_second_part_has_no_join_of_its_own_and_the_first_keeps_its_own(self):
        code, out, err = self.cut(70, 75, "s003")                       # s003 has audio_join 0.25 s, linear, and gain -2
        self.assertEqual(code, 0, err)
        first, second = self.seg("s003"), self.seg("s004")
        self.assertEqual(first["audio_join"], {"duration_s": 0.25, "curve": "linear"})
        self.assertNotIn("audio_join", second)
        self.assertEqual(second["gain_db_adjust"], -2.0)                # the level of the scene goes with both parts
        self.assertEqual(second["label"], "pan with sausage (after the cut)")

    def test_a_cue_before_the_cut_stays_and_a_cue_across_its_start_is_shortened(self):
        self.cut(5, 8)                                                   # c001 plays 3.2 to 6.0 s
        (cue,) = self.now()["subtitles"]["cues"]
        self.assertEqual((cue["id"], cue["anchor"]), ("c001", "s001"))
        self.assertAlmostEqual(cue["start_s"], 3.2, places=2)
        self.assertAlmostEqual(cue["end_s"], round(5 * F) / F, places=2)

    def test_a_cue_after_the_cut_moves_to_the_new_scene_with_its_picture(self):
        code, out, err = self.subtitles("add", "--at", "14", "--end", "16", "--text", "After the cut.")
        self.assertEqual(code, 0, err)
        self.cut(10, 13)
        cues = {c["id"]: c for c in self.now()["subtitles"]["cues"]}
        moved = cues["c002"]
        self.assertEqual(moved["anchor"], "s004")
        self.assertAlmostEqual(moved["start_s"], round(14 * F) / F - round(13 * F) / F, places=1)
        self.assertEqual(cues["c001"]["anchor"], "s001")                 # before the cut
        # the film time of the cue is where the picture is: 14 s of the old film is 11 s of the new one
        spans = {s["id"]: s for s in words.cue_spans(self.now())}
        self.assertAlmostEqual(spans["c002"]["start_s"], 14 - (round(13 * F) - round(10 * F)) / F, places=1)

    def test_a_cue_inside_the_cut_moment_is_removed_and_the_output_says_so(self):
        self.subtitles("add", "--at", "11", "--end", "12", "--text", "Gone.")
        code, out, err = self.cut(10, 13)
        self.assertEqual([c["id"] for c in self.now()["subtitles"]["cues"]], ["c001"])
        self.assertIn("c002", out)
        self.assertIn("removed", out)

    def test_a_cue_that_would_keep_less_than_half_a_second_is_removed(self):
        self.subtitles("add", "--at", "9.8", "--end", "11.5", "--text", "Almost gone.")
        self.cut(10, 13)                                                 # 0.2 s of it would be left
        self.assertEqual([c["id"] for c in self.now()["subtitles"]["cues"]], ["c001"])

    def test_the_text_of_the_scene_stays_with_the_first_part_and_its_cues_are_made_again(self):
        self.data["segments"][0]["text"] = "We put the pan on the stove. It takes a minute to get hot."
        self.data["subtitles"]["cues"] = [
            {"id": "c001", "anchor": "s001", "start_s": 0.0, "end_s": 2.5, "text": "We put the pan on the stove.",
             "origin": "text"},
            {"id": "c002", "anchor": "s001", "start_s": 2.6, "end_s": 5.0, "text": "It takes a minute to get hot.",
             "origin": "text"},
            {"id": "c003", "anchor": "s001", "start_s": 20.0, "end_s": 22.0, "text": "By hand.",
             "origin": "manual", "locked": True}]
        self.save()
        code, out, err = self.cut(10, 13)
        self.assertEqual(code, 0, err)
        s1, s4 = self.seg("s001"), self.seg("s004")
        self.assertIn("pan on the stove", s1["text"])
        self.assertNotIn("text", s4)
        a_len = round(10 * F) / F
        cues = self.now()["subtitles"]["cues"]
        generated = [c for c in cues if c["origin"] == "text"]
        self.assertTrue(generated)
        self.assertTrue(all(c["anchor"] == "s001" and c["end_s"] <= a_len + 0.01 for c in generated))
        self.assertAlmostEqual(max(c["end_s"] for c in generated), a_len, delta=0.5)   # spread across the first part
        moved = next(c for c in cues if c["text"] == "By hand.")                      # made by hand: it goes with its picture
        self.assertEqual(moved["anchor"], "s004")
        self.assertAlmostEqual(moved["start_s"], 20.0 - round(13 * F) / F, places=1)

    def test_a_cut_from_the_start_of_the_scene_trims_its_start(self):
        code, out, err = self.cut(0, 5)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.seg("s001")["in"], 38 + round(5 * F))
        self.assertEqual([s["id"] for s in self.now()["segments"]], ["s001", "s002", "s003"])

    def test_a_cut_to_the_end_of_the_scene_trims_its_end(self):
        code, out, err = self.cut(20, 28.762)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.seg("s001")["out"], 38 + round(20 * F))
        self.assertEqual([s["id"] for s in self.now()["segments"]], ["s001", "s002", "s003"])

    def test_a_cut_of_the_whole_scene_is_refused_and_names_remove(self):
        code, out, err = self.cut(0, 28.762)
        self.assertEqual(code, 1)
        self.assertIn("scenes remove s001", err)
        self.assertEqual(self.snapshots(), 0)

    def test_a_range_outside_the_scene_is_refused_with_the_span_of_the_scene(self):
        code, out, err = self.cut(25, 31)                               # s001 ends at 28.76
        self.assertEqual(code, 1)
        self.assertIn("s001", err)
        self.assertIn("28.76", err)
        code, out, err = self.cut(8, 8)
        self.assertEqual(code, 1)
        code, out, err = self.cut(9, 8)
        self.assertEqual(code, 1)
        self.assertEqual(self.snapshots(), 0)

    def test_a_removed_scene_cannot_be_cut_and_the_error_says_restore(self):
        self.scenes("remove", "s002")
        code, out, err = self.cut(1, 2, "s002")
        self.assertEqual(code, 1)
        self.assertIn("scenes restore s002", err)

    def test_an_unknown_scene_is_refused(self):
        code, out, err = self.cut(1, 2, "s999")
        self.assertEqual(code, 1)
        self.assertIn("s999", err)

    def test_a_part_too_short_for_its_joins_is_refused_by_the_validator(self):
        before = self.path.read_bytes()
        code, out, err = self.cut(0.2, 20)                               # the first part would be 0.2 s: a scene needs 0.5 s
        self.assertEqual(code, 1)
        self.assertIn("V11", out)
        self.assertIn("nothing was saved", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_the_result_validates(self):
        self.cut(10, 13)
        code, out, err = run(["timeline", "validate", "--file", str(self.path), "--no-file-checks"])
        self.assertEqual(code, 0, out)

    def test_a_second_cut_in_the_new_scene_works_and_ids_run_on(self):
        self.cut(10, 13)
        code, out, err = self.cut(14, 16, "s004")                        # film seconds of the film as it is now
        self.assertEqual(code, 0, err)
        self.assertEqual([s["id"] for s in self.now()["segments"]], ["s001", "s004", "s005", "s002", "s003"])

    def test_the_notes_name_the_moment_in_film_and_source_time(self):
        code, out, err = self.cut(10, 13)
        self.assertIn("removed moment", out)
        self.assertIn(f"frames {38 + round(10 * F)}-{38 + round(13 * F)}", out)


class OptionsTests(EditCase):
    def test_cut_needs_from_and_to(self):
        code, out, err = self.scenes("cut", "s001", "--from", "1")
        self.assertEqual(code, 2)
        self.assertIn("--to", err)

    def test_a_missing_file_is_exit_code_2(self):
        code, out, err = run(["scenes", "remove", "s001", "--file", str(self.dir / "none.json")])
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    def test_an_empty_list_of_scenes_is_refused(self):
        code, out, err = self.scenes("remove", ",")
        self.assertIn(code, (1, 2))
        self.assertEqual(self.snapshots(), 0)

    def test_the_history_names_what_a_prompt_edit_did(self):
        self.scenes("remove", "s002")
        code, out, err = run(["timeline", "history", "--file", str(self.path)])
        self.assertIn("s002", out)
        self.assertIn("removed from the film", out)


if __name__ == "__main__":
    unittest.main()
