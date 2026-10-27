"""Check 5.1, the data part: the scene index that Claude Code reads to find a scene (SPECS 10.2).

The index is built from the timeline file, the transcripts that `subtitles auto` kept, the scene
texts and the cues. It is written as JSON and as markdown into the work folder. Nothing is drawn
here; the sheets are in tests/test_scene_sheets.py."""

import json
import tempfile
import unittest
from pathlib import Path

import edits
import fsutil
import sceneindex
import timeline
import transcribe
from tests.timeline_fixture import example

CLIP_A = "A002_01011540_C002"        # s001 (frames 38-900) and s002 (frames 1320-2380) of the example
CLIP_B = "A002_01060615_C083"        # s003 (frames 30-650)


def write_transcript(work, clip, phrases):
    fsutil.write_json_atomic(transcribe.transcript_path(str(work), clip), {
        "model": "ggml-fake.bin", "language": "en", "seconds": 120.0, "phrases": [
            {"start_s": a, "end_s": b, "text": text, "prob": 0.9, "activity": 0.8, "words": [],
             "dropped": dropped} for a, b, text, dropped in phrases]})


class IndexCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.work = self.dir / "work"
        self.work.mkdir()
        self.path = self.dir / "timeline-test.json"
        self.data = example()
        self.save()

    def save(self):
        timeline.Timeline(self.data).save(str(self.path))

    def tl(self):
        return timeline.Timeline.load(str(self.path))

    def build(self):
        return sceneindex.write_index(self.tl(), str(self.path), str(self.work))

    def scene(self, index, sid):
        return next(s for s in index["scenes"] if s["id"] == sid)


class RowTests(IndexCase):
    def test_one_row_per_scene_in_the_order_of_the_file(self):
        index = self.build()
        self.assertEqual([s["id"] for s in index["scenes"]], ["s001", "s002", "s003"])

    def test_film_times_come_from_the_film_order(self):
        index = self.build()
        s1, s2, s3 = (self.scene(index, i) for i in ("s001", "s002", "s003"))
        fps = 30000 / 1001
        self.assertEqual((s1["film_start_frame"], s1["length_frames"]), (0, 862))
        self.assertAlmostEqual(s1["film_start_s"], 0.0)
        self.assertAlmostEqual(s1["film_end_s"], 862 / fps, places=3)
        self.assertEqual(s2["film_start_frame"], 862)
        self.assertAlmostEqual(s2["film_start_s"], 862 / fps, places=3)
        self.assertEqual(s3["film_start_frame"], 862 + 1060)
        self.assertEqual(index["film_frames"], 862 + 1060 + 620)

    def test_the_clip_and_the_frames_and_the_source_times(self):
        s1 = self.scene(self.build(), "s001")
        self.assertEqual((s1["source"], s1["source_in"], s1["source_out"]), (CLIP_A, 38, 900))
        self.assertAlmostEqual(s1["source_start_s"], 38 / (30000 / 1001), places=3)
        self.assertAlmostEqual(s1["source_end_s"], 900 / (30000 / 1001), places=3)
        self.assertEqual(s1["label"], "stove, wide")
        self.assertEqual(s1["origin"], "assembly")
        self.assertEqual(s1["note"], "starts 0.5 s after the clap")

    def test_a_removed_scene_has_no_film_time_and_comes_last_in_the_markdown(self):
        self.data["segments"][1]["enabled"] = False
        self.save()
        index = self.build()
        s2 = self.scene(index, "s002")
        self.assertFalse(s2["enabled"])
        self.assertIsNone(s2["film_start_s"])
        self.assertIsNone(s2["film_end_s"])
        # the scenes after it start earlier, because the removed scene takes no time
        self.assertEqual(self.scene(index, "s003")["film_start_frame"], 862)
        self.assertEqual(index["scenes_in_film"], 2)
        self.assertEqual(index["scenes_removed"], 1)
        md = Path(index["paths"]["markdown"]).read_text(encoding="utf-8")
        self.assertLess(md.index("## Scenes in the film"), md.index("## Removed scenes"))
        self.assertGreater(md.index("### s002"), md.index("## Removed scenes"))
        self.assertLess(md.index("### s001"), md.index("## Removed scenes"))

    def test_the_scene_text_and_the_cues_are_in_the_row(self):
        self.data["segments"][0]["text"] = "Now the pan is hot enough."
        self.save()
        s1 = self.scene(self.build(), "s001")
        self.assertEqual(s1["text"], "Now the pan is hot enough.")
        (cue,) = s1["cues"]
        self.assertEqual((cue["id"], cue["text"], cue["origin"]), ("c001", "Now the pan is hot enough.", "auto"))
        self.assertAlmostEqual(cue["film_start_s"], 3.2)       # s001 starts the film
        self.assertAlmostEqual(cue["film_end_s"], 6.0)

    def test_the_cues_of_a_removed_scene_stay_listed_without_film_time(self):
        self.data["segments"][0]["enabled"] = False
        self.save()
        index = self.build()
        (cue,) = self.scene(index, "s001")["cues"]
        self.assertIsNone(cue["film_start_s"])
        self.assertEqual(self.scene(index, "s002")["cues"], [])

    def test_the_totals(self):
        index = self.build()
        self.assertEqual(index["scenes_in_film"], 3)
        self.assertEqual(index["scenes_removed"], 0)
        self.assertAlmostEqual(index["film_seconds"], (862 + 1060 + 620) / (30000 / 1001), places=2)
        self.assertEqual(index["timeline_hash"], timeline.file_hash(str(self.path)))
        self.assertEqual(index["timeline"], str(self.path))


class SpeechTests(IndexCase):
    """The offset of CLIP_A is 0.276 s, so the sound of s001 (in = frame 38) starts at
    38 / 29.97 + 0.276 = 1.544 s of the WAV, and that of s002 (in = frame 1320) at 44.32 s."""

    def test_a_phrase_goes_to_the_scene_whose_sound_it_is(self):
        write_transcript(self.work, CLIP_A, [(4.0, 6.0, "Give me the knife.", ""),
                                             (60.0, 62.0, "Now the salad.", "")])
        index = self.build()
        (p1,) = self.scene(index, "s001")["speech"]
        (p2,) = self.scene(index, "s002")["speech"]
        self.assertEqual(p1["text"], "Give me the knife.")
        s0 = 38 / (30000 / 1001) + 0.276
        self.assertAlmostEqual(p1["film_start_s"], 4.0 - s0, places=2)
        self.assertAlmostEqual(p1["source_start_s"], 38 / (30000 / 1001) + 4.0 - s0, places=2)
        self.assertFalse(p1["partial"])
        self.assertEqual(p2["text"], "Now the salad.")
        self.assertEqual(self.scene(index, "s003")["speech"], [])

    def test_the_film_time_of_a_phrase_follows_the_film_order(self):
        write_transcript(self.work, CLIP_A, [(60.0, 62.0, "Now the salad.", "")])
        s0 = 1320 / (30000 / 1001) + 0.276
        index = self.build()
        (p,) = self.scene(index, "s002")["speech"]
        self.assertAlmostEqual(p["film_start_s"], 862 / (30000 / 1001) + 60.0 - s0, places=2)

    def test_a_phrase_cut_by_the_scene_is_marked_partial(self):
        write_transcript(self.work, CLIP_A, [(1.0, 2.0, "Before the scene.", "")])   # 0.456 s inside s001
        (p,) = self.scene(self.build(), "s001")["speech"]
        self.assertTrue(p["partial"])
        self.assertAlmostEqual(p["film_start_s"], 0.0)

    def test_a_phrase_with_almost_nothing_inside_is_left_out(self):
        write_transcript(self.work, CLIP_A, [(0.5, 1.6, "Barely.", "")])             # 0.056 s inside s001
        self.assertEqual(self.scene(self.build(), "s001")["speech"], [])

    def test_dropped_phrases_are_listed_apart_with_the_reason(self):
        write_transcript(self.work, CLIP_A, [(4.0, 6.0, "Thank you.", "stock phrase"),
                                             (7.0, 9.0, "Pass me the salt.", "")])
        s1 = self.scene(self.build(), "s001")
        self.assertEqual([p["text"] for p in s1["speech"]], ["Pass me the salt."])
        self.assertEqual([(p["text"], p["dropped"]) for p in s1["speech_dropped"]],
                         [("Thank you.", "stock phrase")])

    def test_a_clip_without_a_transcript_is_flagged_and_a_clip_with_one_is_not(self):
        write_transcript(self.work, CLIP_A, [])
        index = self.build()
        self.assertTrue(self.scene(index, "s001")["has_transcript"])
        self.assertFalse(self.scene(index, "s003")["has_transcript"])
        self.assertEqual(index["clips_without_transcript"], [CLIP_B])

    def test_a_scene_that_is_removed_still_lists_its_speech_with_source_time_only(self):
        self.data["segments"][1]["enabled"] = False
        self.save()
        write_transcript(self.work, CLIP_A, [(60.0, 62.0, "Now the salad.", "")])
        (p,) = self.scene(self.build(), "s002")["speech"]
        self.assertIsNone(p["film_start_s"])
        self.assertGreater(p["source_start_s"], 44.0)

    def test_a_broken_transcript_file_is_ignored(self):
        path = Path(transcribe.transcript_path(str(self.work), CLIP_A))
        path.parent.mkdir(parents=True)
        path.write_text("{ not json")
        self.assertFalse(self.scene(self.build(), "s001")["has_transcript"])


class FilesTests(IndexCase):
    def test_the_json_and_the_markdown_are_written_into_the_work_folder(self):
        index = self.build()
        folder = self.work / "index" / "timeline-test"
        self.assertEqual(index["paths"]["json"], str(folder / "index.json"))
        self.assertEqual(index["paths"]["markdown"], str(folder / "index.md"))
        on_disk = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        self.assertEqual([s["id"] for s in on_disk["scenes"]], ["s001", "s002", "s003"])
        self.assertTrue((folder / "index.md").is_file())

    def test_the_index_folder_is_named_after_the_timeline_file(self):
        self.assertEqual(sceneindex.index_dir("/w", "/x/timeline.json"), "/w/index/timeline")
        self.assertEqual(sceneindex.index_dir("/w", "/x/timeline-full-subs.json"), "/w/index/timeline-full-subs")

    def test_the_sheet_path_of_a_scene_is_fixed_and_listed(self):
        index = self.build()
        s1 = self.scene(index, "s001")
        self.assertEqual(s1["sheet"]["path"], str(self.work / "index" / "timeline-test" / "sheets" / "s001.jpg"))
        self.assertFalse(s1["sheet"]["exists"])

    def test_the_markdown_has_what_claude_code_needs_to_find_a_scene(self):
        write_transcript(self.work, CLIP_A, [(4.0, 6.0, "Give me the knife.", "")])
        self.data["segments"][0]["text"] = "Now the pan is hot enough."
        self.save()
        md = Path(self.build()["paths"]["markdown"]).read_text(encoding="utf-8")
        fps = 30000 / 1001
        self.assertIn(timeline.file_hash(str(self.path))[:8], md)
        self.assertIn("### s001", md)
        self.assertIn("00:00:00.00 - " + edits.clock(862 / fps), md)
        self.assertIn(CLIP_A, md)
        self.assertIn("frames 38-900", md)
        self.assertIn("Give me the knife.", md)
        self.assertIn("Now the pan is hot enough.", md)          # the scene text and the cue
        self.assertIn("sheets/s001.jpg", md)
        self.assertIn("stove, wide", md)

    def test_the_markdown_says_when_a_scene_has_no_transcript(self):
        md = Path(self.build()["paths"]["markdown"]).read_text(encoding="utf-8")
        self.assertIn("no transcript", md)

    def test_writing_the_index_again_changes_nothing_but_the_time(self):
        a = self.build()
        first = json.loads(Path(a["paths"]["json"]).read_text(encoding="utf-8"))
        b = self.build()
        second = json.loads(Path(b["paths"]["json"]).read_text(encoding="utf-8"))
        first.pop("written"), second.pop("written")
        self.assertEqual(first, second)

    def test_the_timeline_file_is_not_touched(self):
        before = self.path.read_bytes()
        self.build()
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
