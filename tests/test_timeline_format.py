"""Check 2.1: the example of SPECS 3.3 parses and validates, saves and loads to
the same bytes, and the derived values (SPECS 3.4) come out as stated.
Also: loading, atomic saving, versions, key order, free keys."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import timeline
from tests.timeline_fixture import example


class ExampleTests(unittest.TestCase):
    def test_the_example_of_the_specification_validates_with_file_checks_off(self):
        self.assertEqual(timeline.validate(example(), file_checks=False), [])

    def test_derived_values_of_the_example(self):
        tl = timeline.Timeline(example())
        self.assertEqual(tl.film_length_frames(), 2542)
        self.assertEqual(tl.segment_film_starts(), {"s001": 0, "s002": 862, "s003": 1922})
        self.assertEqual(tl.segment_lengths(), {"s001": 862, "s002": 1060, "s003": 620})
        self.assertEqual([s["id"] for s in tl.film_order()], ["s001", "s002", "s003"])

    def test_disabled_segments_are_not_in_the_film(self):
        d = example()
        d["segments"][1]["enabled"] = False
        tl = timeline.Timeline(d)
        self.assertEqual(tl.film_length_frames(), 862 + 620)
        self.assertEqual(tl.segment_film_starts(), {"s001": 0, "s003": 862})

    def test_film_samples_do_not_drift_over_many_segments(self):
        # 150 segments of 7 frames at 24000/1001 and 48000 Hz: the sum of the
        # segment sample lengths must equal the sample position of the film end.
        d = example()
        d["film"]["fps"] = {"num": 24000, "den": 1001}
        for s in d["sources"].values():
            s["fps"] = {"num": 24000, "den": 1001}
        d["segments"] = [{"id": f"s{i + 1:03d}", "source": "A002_01060615_C083",
                          "in": i * 3, "out": i * 3 + 7} for i in range(150)]
        d["sources"]["A002_01060615_C083"]["frames"] = 600
        tl = timeline.Timeline(d)
        lengths = [tl.segment_sample_length(s) for s in tl.film_order()]
        self.assertEqual(sum(lengths), tl.film_sample(tl.film_length_frames()))
        self.assertEqual(tl.film_sample(150 * 7), round(150 * 7 * 48000 * 1001 / 24000))

    def test_source_audio_position(self):
        tl = timeline.Timeline(example())
        seg = tl.segment("s001")      # in 38, offset 0.276 s, 29.97 fps, 48 kHz
        want = round((38 * 1001 / 30000 + 0.276) * 48000)
        self.assertEqual(tl.source_audio_start(seg), want)

    def test_drift_moves_the_audio_start(self):
        d = example()
        d["sources"]["A002_01011540_C002"]["audio_drift_ppm"] = 50
        tl = timeline.Timeline(d)
        seg = tl.segment("s002")      # in 1320
        base = (1320 * 1001 / 30000 + 0.276) * 48000
        shift = 1320 * 1001 / 30000 * 50e-6 * 48000
        self.assertEqual(tl.source_audio_start(seg), round(base + shift))


class SaveLoadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "timeline.json"

    def test_saved_file_loads_and_saves_to_the_same_bytes(self):
        timeline.Timeline(example()).save(str(self.path))
        first = self.path.read_bytes()
        again = timeline.Timeline.load(str(self.path))
        second_path = self.dir / "second.json"
        again.save(str(second_path))
        self.assertEqual(second_path.read_bytes(), first)

    def test_file_is_utf8_indented_by_two_spaces_with_a_final_newline(self):
        d = example()
        d["film"]["title"] = "Küche, früh"
        timeline.Timeline(d).save(str(self.path))
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("Küche, früh", text)
        self.assertIn('\n  "version": 1,\n  "kind": "film",', text)
        self.assertTrue(text.endswith("}\n"))

    def test_keys_are_written_in_the_order_of_the_specification(self):
        d = example()
        film = d["film"]
        d["film"] = {k: film[k] for k in reversed(list(film))}       # shuffled
        d = {k: d[k] for k in reversed(list(d))}
        timeline.Timeline(d).save(str(self.path))
        saved = json.loads(self.path.read_text(), object_pairs_hook=lambda p: p)
        self.assertEqual([k for k, _ in saved][:4], ["version", "kind", "film", "sources"])
        film_keys = [k for k, _ in dict(saved)["film"]]
        self.assertEqual(film_keys, ["title", "fps", "frame", "fit", "fade_in_s",
                                     "fade_out_s", "audio_join"])

    def test_free_keys_survive_a_round_trip(self):
        d = example()
        d["x_note"] = "for people"
        d["segments"][0]["x_why"] = {"a": 1}
        timeline.Timeline(d).save(str(self.path))
        back = timeline.Timeline.load(str(self.path)).data
        self.assertEqual(back["x_note"], "for people")
        self.assertEqual(back["segments"][0]["x_why"], {"a": 1})
        self.assertEqual(timeline.validate(back, file_checks=False), [])

    def test_save_replaces_the_file_atomically_and_leaves_no_temporary_file(self):
        self.path.write_text("old")
        timeline.Timeline(example()).save(str(self.path))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["timeline.json"])
        self.assertTrue(self.path.read_text().startswith("{"))

    def test_a_write_that_fails_halfway_leaves_the_old_file_whole(self):
        self.path.write_text("old content")
        with mock.patch("timeline.json.dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                timeline.Timeline(example()).save(str(self.path))
        self.assertEqual(self.path.read_text(), "old content")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["timeline.json"])

    def test_a_failing_rename_leaves_the_old_file_whole_and_no_temporary_file(self):
        self.path.write_text("old content")
        with mock.patch("timeline.os.replace", side_effect=OSError("no")):
            with self.assertRaises(OSError):
                timeline.Timeline(example()).save(str(self.path))
        self.assertEqual(self.path.read_text(), "old content")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["timeline.json"])

    def test_hash_is_the_sha256_of_the_file(self):
        import hashlib
        timeline.Timeline(example()).save(str(self.path))
        self.assertEqual(timeline.file_hash(str(self.path)),
                         hashlib.sha256(self.path.read_bytes()).hexdigest())


class LoadRefusalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "timeline.json"

    def test_a_higher_version_is_refused_and_names_the_remedy(self):
        d = example()
        d["version"] = 2
        self.path.write_text(json.dumps(d))
        with self.assertRaises(timeline.UnknownVersion) as ctx:
            timeline.Timeline.load(str(self.path))
        self.assertIn("update", str(ctx.exception).lower())

    def test_a_file_without_a_version_is_refused(self):
        d = example()
        del d["version"]
        self.path.write_text(json.dumps(d))
        with self.assertRaises(timeline.UnknownVersion):
            timeline.Timeline.load(str(self.path))

    def test_not_json_is_refused_with_the_position(self):
        self.path.write_text('{"version": 1,')
        with self.assertRaises(timeline.TimelineError) as ctx:
            timeline.Timeline.load(str(self.path))
        self.assertIn("line", str(ctx.exception))

    def test_a_json_list_is_refused(self):
        self.path.write_text("[]")
        with self.assertRaises(timeline.TimelineError):
            timeline.Timeline.load(str(self.path))

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(FileNotFoundError):
            timeline.Timeline.load(str(self.path))

    def test_duplicate_keys_are_reported_by_validate(self):
        text = json.dumps(example()).replace('"kind": "film"', '"kind": "film", "kind": "short"')
        self.path.write_text(text)
        tl = timeline.Timeline.load(str(self.path))
        findings = tl.validate(file_checks=False)
        self.assertTrue(any("duplicate" in f.message for f in findings), findings)


if __name__ == "__main__":
    unittest.main()
