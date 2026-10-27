"""Tests for project.py: the field work_dir (SPECS 6.9), the round trip of the file, the selection trims."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from project import Project, SceneMatch


def scene(index=0, vd=10.0, ad=10.0, offset=0.0, ts=0.0, te=0.0, **kw):
    return SceneMatch(index=index, video_path=kw.get("vp", f"/v/{index}.MOV"),
                      audio_path=kw.get("ap", f"/a/{index}.WAV"),
                      video_duration=vd, audio_duration=ad, offset=offset,
                      confidence=1.0, trim_start=ts, trim_end=te)


class WorkDirTests(unittest.TestCase):
    def test_work_dir_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            Project(video_dir="/v", audio_dir="/a", work_dir="/w").save(str(path))
            self.assertEqual(Project.load(str(path)).work_dir, "/w")

    def test_file_without_work_dir_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            path.write_text(json.dumps({"video_dir": "/v", "audio_dir": "/a",
                                        "output_dir": "", "matches": [],
                                        "selection": [], "selection_trims": {}}))
            self.assertEqual(Project.load(str(path)).work_dir, "")


class RoundTripTests(unittest.TestCase):
    def make(self):
        return Project(video_dir="/v", audio_dir="/a", matches=[scene(0), scene(1)], selection=[1, 0],
                       selection_trims={"1": {"start": 2.0, "end": 1.0}})

    def test_roundtrip_and_no_mutation(self):
        p = self.make()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "project.json")
            p.save(path)
            self.assertEqual(os.listdir(d), ["project.json"])
            q = Project.load(path)
        self.assertEqual(q.selection_trims, {"1": {"start": 2.0, "end": 1.0}})
        first = q.get_ordered_scenes()
        self.assertEqual((first[0].index, first[0].trim_start, first[0].trim_end),
                         (1, 2.0, 1.0))
        q.selection_trims = {1: {"start": 5.0}}
        second = q.get_ordered_scenes()
        self.assertEqual(second[0].trim_start, 5.0)
        self.assertEqual(q.get_match(1).trim_start, 0.0)

    def test_unknown_keys_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            m = {"index": 0, "video_path": "v", "audio_path": "a",
                 "video_duration": 1, "audio_duration": 1, "offset": 0,
                 "confidence": 1, "future_field": 3}
            with open(path, "w") as f:
                json.dump({"matches": [m]}, f)
            self.assertEqual(Project.load(path).matches[0].index, 0)

    def test_a_file_with_the_old_output_folder_still_loads(self):
        """output_dir was the folder of the audio copies (removed with D4); old files keep the key."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            with open(path, "w") as f:
                json.dump({"video_dir": "/v", "audio_dir": "/a", "output_dir": "/o", "matches": []}, f)
            p = Project.load(path)
            self.assertEqual(p.video_dir, "/v")
            p.save(path)
            self.assertNotIn("output_dir", json.load(open(path)))


if __name__ == "__main__":
    unittest.main()
